from fastapi import APIRouter, Query, HTTPException, Body
from fastapi.responses import StreamingResponse
from typing import Optional, List
import math
import json
import csv
import io

from app.config import config
from app.models.schemas import (
    TableData, StatsResponse, FilterOption, UpdateDataRequest,
    InsertRowsRequest, DeleteRowsRequest, AddColumnRequest, RenameColumnRequest,
    RegexReplaceRequest, SplitColumnRequest, ConvertTypeRequest, ValidateRequest
)
from app.services.database import DatabaseService

router = APIRouter(prefix="/api/data", tags=["数据查询"])

try:
    import openpyxl
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False


def get_db_path(session_id: str):
    db_path = config.get_db_path(session_id)
    if config.DB_TYPE == 'mysql':
        return db_path
    if not db_path.exists():
        return None
    return db_path


def sanitize_table_name(name: str) -> str:
    """将 sheet 名转换为有效的 SQLite 表名"""
    return name.replace(" ", "_").replace("-", "_")


@router.get("/{session_id}/tables")
async def list_tables(session_id: str):
    """列出所有可用的表（sheet）"""
    db_path = get_db_path(session_id)
    if not db_path:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")

    tables = DatabaseService.list_tables(db_path)
    result = []
    for t in tables:
        headers = DatabaseService.get_headers(db_path, t)
        result.append({"name": t, "headers": headers})

    return {"tables": result}


@router.get("/{session_id}/query", response_model=TableData)
async def query_data(
    session_id: str,
    tableName: str = Query(default="data"),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=100, ge=1, le=5000),
    sortColumn: Optional[str] = Query(default=None),
    sortOrder: str = Query(default="asc", regex="^(asc|desc)$"),
    filters: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None)
):
    """
    分页查询数据，支持多列组合筛选和全局搜索
    """
    db_path = get_db_path(session_id)
    if not db_path:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")

    filter_dict = {}
    if filters:
        try:
            filter_dict = json.loads(filters)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="filters 参数格式错误，应为 JSON 字符串")

    table_name = sanitize_table_name(tableName)
    headers = DatabaseService.get_headers(db_path, table_name)
    rows, total = DatabaseService.query_data(
        db_path,
        table_name=table_name,
        headers=headers,
        page=page,
        page_size=pageSize,
        sort_column=sortColumn,
        sort_order=sortOrder,
        filters=filter_dict,
        search=search
    )

    data_rows = [list(row) for row in rows]

    return TableData(
        headers=headers,
        rows=data_rows,
        totalRows=total,
        page=page,
        pageSize=pageSize,
        totalPages=math.ceil(total / pageSize) if total > 0 else 0
    )


@router.get("/{session_id}/stats")
async def get_stats(session_id: str, tableName: str = Query(default="data")):
    """获取表统计信息"""
    db_path = get_db_path(session_id)
    if not db_path:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")

    table_name = sanitize_table_name(tableName)
    stats = DatabaseService.get_table_stats(db_path, table_name)
    return StatsResponse(sheetName=tableName, **stats)


@router.get("/{session_id}/filters/{column}")
async def get_filter_options(session_id: str, column: str, tableName: str = Query(default="data")):
    """获取列的筛选选项"""
    db_path = get_db_path(session_id)
    if not db_path:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")

    table_name = sanitize_table_name(tableName)
    options = DatabaseService.get_filter_options(db_path, table_name, column)
    return [
        FilterOption(column=column, values=[opt[0]], count=opt[1])
        for opt in options
    ]


@router.put("/{session_id}/update")
async def update_data(
    session_id: str,
    request: UpdateDataRequest
):
    """更新数据行"""
    db_path = get_db_path(session_id)
    if not db_path:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")

    table_name = sanitize_table_name(request.tableName)
    affected = DatabaseService.update_row(db_path, table_name, request.rowId, request.updates)
    return {"success": affected > 0, "affectedRows": affected}


@router.get("/{session_id}/export")
async def export_data(
    session_id: str,
    tableName: str = Query(default="data"),
    format: str = Query(default="csv", regex="^(csv|xlsx)$"),
    filters: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    allSheets: bool = Query(default=False)
):
    """导出数据（支持 CSV 和 XLSX 格式，可导出当前筛选后的数据）"""
    db_path = get_db_path(session_id)
    if not db_path:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")

    filter_dict = {}
    if filters:
        try:
            filter_dict = json.loads(filters)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="filters 参数格式错误")

    if format == "xlsx" and not HAS_OPENPYXL:
        raise HTTPException(status_code=400, detail="XLSX 导出需要 openpyxl，请先安装")

    from app.services.taskManager import taskManager
    progress = taskManager.get_progress(session_id)
    sheets = progress.sheets if progress else []

    if allSheets:
        tables = DatabaseService.list_tables(db_path)
    else:
        tables = [sanitize_table_name(tableName)]

    if format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        first = True
        for t in tables:
            headers = DatabaseService.get_headers(db_path, t)
            rows, _ = DatabaseService.query_data(
                db_path, t, headers=headers, page=1, page_size=100000,
                filters=filter_dict if not allSheets else None,
                search=search if not allSheets else None
            )
            if len(tables) > 1:
                if not first:
                    writer.writerow([])
                writer.writerow([f"=== {t} ==="])
                first = False
            writer.writerow(headers)
            for row in rows:
                writer.writerow(row[1:])
        output.seek(0)
        content = output.getvalue().encode("utf-8-sig")
        filename = f"export.csv"
        media_type = "text/csv"
        return StreamingResponse(
            iter([content]),
            media_type=media_type,
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"}
        )
    else:
        import openpyxl
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        for t in tables:
            sheet_title = t[:31] if t else "Sheet1"
            ws = wb.create_sheet(title=sheet_title)
            headers = DatabaseService.get_headers(db_path, t)
            rows, _ = DatabaseService.query_data(
                db_path, t, headers=headers, page=1, page_size=100000,
                filters=filter_dict if not allSheets else None,
                search=search if not allSheets else None
            )
            ws.append(headers)
            for row in rows:
                ws.append(list(row[1:]))
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        filename = f"export.xlsx"
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"}
        )


@router.post("/{session_id}/clean/deduplicate")
async def deduplicate_data(
    session_id: str,
    tableName: str = Query(default="data"),
    columns: Optional[List[str]] = Body(default=None)
):
    """数据去重（基于指定列或所有列）"""
    db_path = get_db_path(session_id)
    if not db_path:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")

    table_name = sanitize_table_name(tableName)
    headers = DatabaseService.get_headers(db_path, table_name)

    with DatabaseService.get_connection(db_path) as conn:
        cursor = conn.cursor()
        if columns:
            if config.DB_TYPE == 'mysql':
                col_list = ", ".join([f'`{c}`' for c in columns if c in headers])
            else:
                col_list = ", ".join([f'"{c}"' for c in columns if c in headers])
            if not col_list:
                raise HTTPException(status_code=400, detail="指定的列不存在")
            if config.DB_TYPE == 'mysql':
                cursor.execute(f"""
                    DELETE FROM `{table_name}`
                    WHERE row_id NOT IN (
                        SELECT MIN(row_id) FROM `{table_name}`
                        GROUP BY {col_list}
                    )
                """)
            else:
                cursor.execute(f"""
                    DELETE FROM "{table_name}"
                    WHERE row_id NOT IN (
                        SELECT MIN(row_id) FROM "{table_name}"
                        GROUP BY {col_list}
                    )
                """)
        else:
            if config.DB_TYPE == 'mysql':
                col_list = ", ".join([f'`{h}`' for h in headers])
                cursor.execute(f"""
                    DELETE FROM `{table_name}`
                    WHERE row_id NOT IN (
                        SELECT MIN(row_id) FROM `{table_name}`
                        GROUP BY {col_list}
                    )
                """)
            else:
                col_list = ", ".join([f'"{h}"' for h in headers])
                cursor.execute(f"""
                    DELETE FROM "{table_name}"
                    WHERE row_id NOT IN (
                        SELECT MIN(row_id) FROM "{table_name}"
                        GROUP BY {col_list}
                    )
                """)
        conn.commit()
        removed = cursor.rowcount

    with DatabaseService.get_connection(db_path) as conn2:
        cursor2 = conn2.cursor()
        if config.DB_TYPE == 'mysql':
            cursor2.execute(f'SELECT COUNT(*) FROM `{table_name}`')
            remaining = list(cursor2.fetchone().values())[0]
        else:
            cursor2.execute(f'SELECT COUNT(*) FROM "{table_name}"')
            remaining = cursor2.fetchone()[0]

    return {"success": True, "removed": removed, "remaining": remaining}


@router.post("/{session_id}/clean/fill-empty")
async def fill_empty_values(
    session_id: str,
    tableName: str = Query(default="data"),
    fillColumn: str = Body(...),
    fillValue: str = Body(...)
):
    """填充空值（指定列）"""
    db_path = get_db_path(session_id)
    if not db_path:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")

    table_name = sanitize_table_name(tableName)
    headers = DatabaseService.get_headers(db_path, table_name)

    if fillColumn not in headers:
        raise HTTPException(status_code=400, detail="指定的列不存在")

    with DatabaseService.get_connection(db_path) as conn:
        cursor = conn.cursor()
        if config.DB_TYPE == 'mysql':
            cursor.execute(f"""
                UPDATE `{table_name}`
                SET `{fillColumn}` = %s
                WHERE `{fillColumn}` IS NULL OR `{fillColumn}` = ''
            """, [fillValue])
        else:
            cursor.execute(f"""
                UPDATE "{table_name}"
                SET "{fillColumn}" = ?
                WHERE "{fillColumn}" IS NULL OR "{fillColumn}" = ''
            """, [fillValue])
        conn.commit()
        updated = cursor.rowcount

    return {"success": True, "updated": updated, "column": fillColumn, "value": fillValue}


@router.get("/{session_id}/pivot")
async def pivot_table(
    session_id: str,
    tableName: str = Query(default="data"),
    rowField: str = Query(...),
    valueField: str = Query(...),
    aggFunc: str = Query(default="sum", regex="^(sum|count|avg|min|max)$"),
    colField: Optional[str] = Query(default=None)
):
    """透视表接口"""
    db_path = get_db_path(session_id)
    if not db_path:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")

    table_name = sanitize_table_name(tableName)
    headers = DatabaseService.get_headers(db_path, table_name)

    if rowField not in headers:
        raise HTTPException(status_code=400, detail="行字段不存在")
    if valueField not in headers:
        raise HTTPException(status_code=400, detail="值字段不存在")

    with DatabaseService.get_connection(db_path) as conn:
        cursor = conn.cursor()

        if config.DB_TYPE == 'mysql':
            agg_sql = {
                "sum": f'SUM(CAST(`{valueField}` AS DECIMAL(20,4)))',
                "count": f'COUNT(*)',
                "avg": f'AVG(CAST(`{valueField}` AS DECIMAL(20,4)))',
                "min": f'MIN(CAST(`{valueField}` AS DECIMAL(20,4)))',
                "max": f'MAX(CAST(`{valueField}` AS DECIMAL(20,4)))'
            }[aggFunc]
        else:
            agg_sql = {
                "sum": f'SUM(CAST("{valueField}" AS REAL))',
                "count": f'COUNT(*)',
                "avg": f'AVG(CAST("{valueField}" AS REAL))',
                "min": f'MIN(CAST("{valueField}" AS REAL))',
                "max": f'MAX(CAST("{valueField}" AS REAL))'
            }[aggFunc]

        if colField and colField in headers and colField != rowField:
            if config.DB_TYPE == 'mysql':
                cursor.execute(f'SELECT DISTINCT `{colField}` FROM `{table_name}` WHERE `{colField}` IS NOT NULL AND `{colField}` != "" ORDER BY `{colField}`')
                col_values = [list(row.values())[0] for row in cursor.fetchall()][:50]
            else:
                cursor.execute(f'SELECT DISTINCT "{colField}" FROM "{table_name}" WHERE "{colField}" IS NOT NULL AND "{colField}" != "" ORDER BY "{colField}"')
                col_values = [row[0] for row in cursor.fetchall()][:50]

            agg_cols = []
            params = []
            for cv in col_values:
                if config.DB_TYPE == 'mysql':
                    if aggFunc == "count":
                        agg_cols.append(f'SUM(CASE WHEN `{colField}` = %s THEN 1 ELSE 0 END) AS `{cv}`')
                    elif aggFunc == "sum":
                        agg_cols.append(f'SUM(CASE WHEN `{colField}` = %s THEN CAST(`{valueField}` AS DECIMAL(20,4)) ELSE 0 END) AS `{cv}`')
                    elif aggFunc == "avg":
                        agg_cols.append(f'AVG(CASE WHEN `{colField}` = %s THEN CAST(`{valueField}` AS DECIMAL(20,4)) ELSE NULL END) AS `{cv}`')
                    elif aggFunc == "min":
                        agg_cols.append(f'MIN(CASE WHEN `{colField}` = %s THEN CAST(`{valueField}` AS DECIMAL(20,4)) ELSE NULL END) AS `{cv}`')
                    elif aggFunc == "max":
                        agg_cols.append(f'MAX(CASE WHEN `{colField}` = %s THEN CAST(`{valueField}` AS DECIMAL(20,4)) ELSE NULL END) AS `{cv}`')
                else:
                    if aggFunc == "count":
                        agg_cols.append(f'SUM(CASE WHEN "{colField}" = ? THEN 1 ELSE 0 END) AS "{cv}"')
                    elif aggFunc == "sum":
                        agg_cols.append(f'SUM(CASE WHEN "{colField}" = ? THEN CAST("{valueField}" AS REAL) ELSE 0 END) AS "{cv}"')
                    elif aggFunc == "avg":
                        agg_cols.append(f'AVG(CASE WHEN "{colField}" = ? THEN CAST("{valueField}" AS REAL) ELSE NULL END) AS "{cv}"')
                    elif aggFunc == "min":
                        agg_cols.append(f'MIN(CASE WHEN "{colField}" = ? THEN CAST("{valueField}" AS REAL) ELSE NULL END) AS "{cv}"')
                    elif aggFunc == "max":
                        agg_cols.append(f'MAX(CASE WHEN "{colField}" = ? THEN CAST("{valueField}" AS REAL) ELSE NULL END) AS "{cv}"')
                params.append(cv)

            if config.DB_TYPE == 'mysql':
                cursor.execute(f"""
                    SELECT `{rowField}`, {', '.join(agg_cols)}
                    FROM `{table_name}`
                    WHERE `{rowField}` IS NOT NULL AND `{rowField}` != ''
                    GROUP BY `{rowField}`
                    ORDER BY `{rowField}`
                    LIMIT 200
                """, params)
            else:
                cursor.execute(f"""
                    SELECT "{rowField}", {', '.join(agg_cols)}
                    FROM "{table_name}"
                    WHERE "{rowField}" IS NOT NULL AND "{rowField}" != ''
                    GROUP BY "{rowField}"
                    ORDER BY "{rowField}"
                    LIMIT 200
                """, params)

            rows = cursor.fetchall()
            if config.DB_TYPE == 'mysql':
                result_rows = []
                for row in rows:
                    vals = list(row.values())
                    result_rows.append([vals[0]] + [vals[i + 1] if vals[i + 1] is not None else 0 for i in range(len(col_values))])
            else:
                result_rows = [
                    [row[0]] + [row[i + 1] if row[i + 1] is not None else 0 for i in range(len(col_values))]
                    for row in rows
                ]
            return {
                "rowField": rowField,
                "colField": colField,
                "valueField": valueField,
                "aggFunc": aggFunc,
                "columns": [rowField] + [str(cv) for cv in col_values],
                "rows": [list(r) for r in result_rows]
            }
        else:
            if config.DB_TYPE == 'mysql':
                cursor.execute(f"""
                    SELECT `{rowField}`, {agg_sql} as val
                    FROM `{table_name}`
                    WHERE `{rowField}` IS NOT NULL AND `{rowField}` != ''
                    GROUP BY `{rowField}`
                    ORDER BY val DESC
                    LIMIT 200
                """)
            else:
                cursor.execute(f"""
                    SELECT "{rowField}", {agg_sql} as val
                    FROM "{table_name}"
                    WHERE "{rowField}" IS NOT NULL AND "{rowField}" != ''
                    GROUP BY "{rowField}"
                    ORDER BY val DESC
                    LIMIT 200
                """)
            rows = cursor.fetchall()
            if config.DB_TYPE == 'mysql':
                result_rows = [list(row.values()) for row in rows]
            else:
                result_rows = [list(row) for row in rows]
            return {
                "rowField": rowField,
                "valueField": valueField,
                "aggFunc": aggFunc,
                "columns": [rowField, f"{aggFunc}_{valueField}"],
                "rows": result_rows
            }


@router.post("/compare")
async def compare_files(
    sessionA: str = Body(...),
    sessionB: str = Body(...),
    tableA: str = Body(default="data"),
    tableB: str = Body(default="data"),
    keyColumn: str = Body(...),
    compareColumns: Optional[List[str]] = Body(default=None)
):
    """
    多文件对比分析
    基于指定的关键列对比两个表的数据，找出仅在A、仅在B、两者共有的记录
    """
    db_path_a = get_db_path(sessionA)
    db_path_b = get_db_path(sessionB)
    if not db_path_a:
        raise HTTPException(status_code=404, detail=f"会话 {sessionA} 不存在或已过期")
    if not db_path_b:
        raise HTTPException(status_code=404, detail=f"会话 {sessionB} 不存在或已过期")

    table_name_a = sanitize_table_name(tableA)
    table_name_b = sanitize_table_name(tableB)

    headers_a = DatabaseService.get_headers(db_path_a, table_name_a)
    headers_b = DatabaseService.get_headers(db_path_b, table_name_b)

    if keyColumn not in headers_a:
        raise HTTPException(status_code=400, detail=f"关键列 {keyColumn} 在表 A 中不存在")
    if keyColumn not in headers_b:
        raise HTTPException(status_code=400, detail=f"关键列 {keyColumn} 在表 B 中不存在")

    cols_to_compare = compareColumns if compareColumns else list(set(headers_a) & set(headers_b) - {keyColumn})

    def build_query(db_path, table_name, headers, key_col):
        all_cols = [key_col] + [c for c in headers if c != key_col]
        if config.DB_TYPE == 'mysql':
            columns_sql = ", ".join([f'`{c}`' for c in all_cols])
        else:
            columns_sql = ", ".join([f'"{c}"' for c in all_cols])
        with DatabaseService.get_connection(db_path) as conn:
            cursor = conn.cursor()
            if config.DB_TYPE == 'mysql':
                cursor.execute(f'SELECT {columns_sql} FROM `{table_name}`')
            else:
                cursor.execute(f'SELECT {columns_sql} FROM "{table_name}"')
            rows = cursor.fetchall()
            if config.DB_TYPE == 'mysql':
                return all_cols, [dict(row) for row in rows]
            else:
                return all_cols, [dict(zip(all_cols, row)) for row in rows]

    cols_a, data_a = build_query(db_path_a, table_name_a, headers_a, keyColumn)
    cols_b, data_b = build_query(db_path_b, table_name_b, headers_b, keyColumn)

    map_a = {row[keyColumn]: row for row in data_a}
    map_b = {row[keyColumn]: row for row in data_b}

    keys_a = set(map_a.keys())
    keys_b = set(map_b.keys())

    only_a_keys = keys_a - keys_b
    only_b_keys = keys_b - keys_a
    both_keys = keys_a & keys_b

    diff_details = []
    for key in both_keys:
        row_a = map_a[key]
        row_b = map_b[key]
        changes = {}
        for col in cols_to_compare:
            if col in row_a and col in row_b:
                if str(row_a[col] or "") != str(row_b[col] or ""):
                    changes[col] = {"a": row_a[col], "b": row_b[col]}
        if changes:
            diff_details.append({
                "key": key,
                "changes": changes
            })

    def sample_rows(keys, data_map, limit=100):
        result = []
        for k in list(keys)[:limit]:
            result.append(data_map[k])
        return result

    return {
        "keyColumn": keyColumn,
        "compareColumns": cols_to_compare,
        "stats": {
            "onlyA": len(only_a_keys),
            "onlyB": len(only_b_keys),
            "both": len(both_keys),
            "changed": len(diff_details)
        },
        "sampleOnlyA": sample_rows(only_a_keys, map_a),
        "sampleOnlyB": sample_rows(only_b_keys, map_b),
        "sampleBoth": sample_rows(both_keys, map_a),
        "sampleChanged": diff_details[:100]
    }


# ==================== CRUD：行/列操作 ====================

@router.post("/{session_id}/rows")
async def insert_rows(session_id: str, request: InsertRowsRequest):
    """插入单行/多行"""
    db_path = get_db_path(session_id)
    if not db_path:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")

    table_name = sanitize_table_name(request.tableName)
    headers = DatabaseService.get_headers(db_path, table_name)

    # 验证列名
    for row_dict in request.rows:
        for col in row_dict:
            if col not in headers:
                raise HTTPException(status_code=400, detail=f"列 '{col}' 不存在")

    if not request.rows:
        raise HTTPException(status_code=400, detail="rows 不能为空")

    count = DatabaseService.insert_rows(db_path, table_name, headers, request.rows)
    return {"success": True, "affectedRows": count}


@router.delete("/{session_id}/rows")
async def delete_rows(session_id: str, request: DeleteRowsRequest):
    """按 row_id 删除单行/多行"""
    db_path = get_db_path(session_id)
    if not db_path:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")

    table_name = sanitize_table_name(request.tableName)

    if not request.rowIds:
        raise HTTPException(status_code=400, detail="rowIds 不能为空")

    count = DatabaseService.delete_rows(db_path, table_name, request.rowIds)
    return {"success": True, "affectedRows": count}


@router.post("/{session_id}/columns")
async def add_column(session_id: str, request: AddColumnRequest):
    """新增列"""
    db_path = get_db_path(session_id)
    if not db_path:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")

    table_name = sanitize_table_name(request.tableName)
    headers = DatabaseService.get_headers(db_path, table_name)

    if request.columnName in headers:
        raise HTTPException(status_code=400, detail=f"列 '{request.columnName}' 已存在")

    col = DatabaseService.add_column(db_path, table_name, request.columnName, request.defaultValue)
    return {"success": True, "columnName": col}


@router.delete("/{session_id}/columns/{column}")
async def drop_column(session_id: str, column: str, tableName: str = Query(default="data")):
    """删除列"""
    db_path = get_db_path(session_id)
    if not db_path:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")

    table_name = sanitize_table_name(tableName)
    headers = DatabaseService.get_headers(db_path, table_name)

    if column not in headers:
        raise HTTPException(status_code=400, detail=f"列 '{column}' 不存在")

    DatabaseService.drop_column(db_path, table_name, column)
    return {"success": True}


@router.put("/{session_id}/columns/{column}")
async def rename_column(session_id: str, column: str, request: RenameColumnRequest):
    """重命名列"""
    db_path = get_db_path(session_id)
    if not db_path:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")

    table_name = sanitize_table_name(request.tableName)
    headers = DatabaseService.get_headers(db_path, table_name)

    if column not in headers:
        raise HTTPException(status_code=400, detail=f"列 '{column}' 不存在")
    if request.newName in headers:
        raise HTTPException(status_code=400, detail=f"列 '{request.newName}' 已存在")

    new_name = DatabaseService.rename_column(db_path, table_name, column, request.newName)
    return {"success": True, "newName": new_name}


# ==================== 数据清洗增强 ====================

@router.post("/{session_id}/clean/regex-replace")
async def regex_replace(session_id: str, request: RegexReplaceRequest):
    """正则替换"""
    db_path = get_db_path(session_id)
    if not db_path:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")

    table_name = sanitize_table_name(request.tableName)
    headers = DatabaseService.get_headers(db_path, table_name)

    if request.column not in headers:
        raise HTTPException(status_code=400, detail=f"列 '{request.column}' 不存在")

    import re
    try:
        re.compile(request.pattern)
    except re.error as e:
        raise HTTPException(status_code=400, detail=f"正则表达式语法错误: {e}")

    updated = DatabaseService.regex_replace(
        db_path, table_name, request.column, request.pattern, request.replacement
    )
    return {"success": True, "updated": updated}


@router.post("/{session_id}/clean/split-column")
async def split_column(session_id: str, request: SplitColumnRequest):
    """按分隔符拆列"""
    db_path = get_db_path(session_id)
    if not db_path:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")

    table_name = sanitize_table_name(request.tableName)
    headers = DatabaseService.get_headers(db_path, table_name)

    if request.sourceColumn not in headers:
        raise HTTPException(status_code=400, detail=f"列 '{request.sourceColumn}' 不存在")

    if not request.delimiter:
        raise HTTPException(status_code=400, detail="分隔符不能为空")

    result = DatabaseService.split_column(
        db_path, table_name, request.sourceColumn,
        request.delimiter, request.maxSplits
    )
    return {"success": True, **result}


@router.post("/{session_id}/clean/convert-type")
async def convert_type(session_id: str, request: ConvertTypeRequest):
    """数据类型转换"""
    db_path = get_db_path(session_id)
    if not db_path:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")

    table_name = sanitize_table_name(request.tableName)
    headers = DatabaseService.get_headers(db_path, table_name)

    if request.column not in headers:
        raise HTTPException(status_code=400, detail=f"列 '{request.column}' 不存在")

    if request.targetType not in ("string", "number", "date"):
        raise HTTPException(status_code=400, detail="targetType 必须是 string/number/date")

    result = DatabaseService.convert_type(
        db_path, table_name, request.column,
        request.targetType, request.dateFormat
    )
    return {"success": True, **result}


@router.post("/{session_id}/clean/validate")
async def validate_data(session_id: str, request: ValidateRequest):
    """数据校验"""
    db_path = get_db_path(session_id)
    if not db_path:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")

    table_name = sanitize_table_name(request.tableName)
    headers = DatabaseService.get_headers(db_path, table_name)

    for rule in request.rules:
        if rule.column not in headers:
            raise HTTPException(status_code=400, detail=f"列 '{rule.column}' 不存在")
        if rule.ruleType not in ("email", "phone", "range", "regex", "not_empty"):
            raise HTTPException(status_code=400, detail=f"不支持的规则类型: {rule.ruleType}")

    # 获取所有数据（限制 10 万行）
    rows, total = DatabaseService.query_data(
        db_path, table_name, headers=headers, page=1, page_size=100000
    )

    import re
    invalid_rows = []

    for row in rows:
        row_id = row[0]
        col_values = dict(zip(headers, row[1:]))
        for rule in request.rules:
            val = col_values.get(rule.column, "")
            is_valid, msg = _check_rule(val, rule)
            if not is_valid:
                invalid_rows.append({
                    "rowId": row_id,
                    "column": rule.column,
                    "value": str(val or ""),
                    "ruleType": rule.ruleType,
                    "message": msg
                })

    return {
        "success": True,
        "totalRows": total,
        "invalidCount": len(invalid_rows),
        "invalidRows": invalid_rows[:500]
    }


def _check_rule(val, rule) -> tuple:
    """检查单条校验规则。返回 (is_valid, message)"""
    import re
    val_str = str(val or "").strip()

    if rule.ruleType == "not_empty":
        return (bool(val_str), "值不能为空")

    if rule.ruleType == "email":
        pattern = r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$'
        if val_str and not re.match(pattern, val_str):
            return (False, "不是有效的邮箱格式")
        return (True, "")

    if rule.ruleType == "phone":
        pattern = r'^1[3-9]\d{9}$'
        if val_str and not re.match(pattern, val_str):
            return (False, "不是有效的手机号格式")
        return (True, "")

    if rule.ruleType == "range":
        try:
            num = float(val_str)
            min_val = rule.params.get("min")
            max_val = rule.params.get("max")
            if min_val is not None and num < float(min_val):
                return (False, f"值小于最小值 {min_val}")
            if max_val is not None and num > float(max_val):
                return (False, f"值大于最大值 {max_val}")
        except ValueError:
            return (False, "不是有效的数值")
        return (True, "")

    if rule.ruleType == "regex":
        pattern = rule.params.get("pattern", "")
        if val_str and not re.match(pattern, val_str):
            return (False, f"不匹配规则: {pattern}")
        return (True, "")

    return (True, "")
