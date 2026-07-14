from fastapi import APIRouter, Query, HTTPException, Body, Request
from fastapi.responses import StreamingResponse
from typing import Optional, List
import math
import json
import csv
import io
import uuid
from datetime import datetime

from app.config import config
from app.models.schemas import (
    TableData, StatsResponse, FilterOption, UpdateDataRequest,
    InsertRowsRequest, DeleteRowsRequest, AddColumnRequest, RenameColumnRequest,
    RegexReplaceRequest, SplitColumnRequest, ConvertTypeRequest, ValidateRequest,
    CleanPreviewRequest
)
from pydantic import BaseModel
from app.services.database import DatabaseService
from app.services.taskManager import taskManager

router = APIRouter(prefix="/api/data", tags=["数据查询"])


def _user_id(req: Request) -> str:
    """从请求头获取用户 ID"""
    return req.headers.get("X-User-Id", "")


def _check_session_ownership(session_id: str, user_id: str):
    """校验 session 归属，不匹配则抛 404"""
    if not user_id:
        return
    progress = taskManager.get_progress(session_id)
    if progress and progress.userId and progress.userId != user_id:
        raise HTTPException(status_code=404, detail="会话不存在")

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


def _is_number(v) -> bool:
    try:
        float(str(v).strip())
        return True
    except (ValueError, TypeError):
        return False


def _is_date(v) -> bool:
    import re
    s = str(v).strip()
    if not s:
        return False
    patterns = [
        r'^\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?$',
        r'^\d{1,2}[-/]\d{1,2}[-/]\d{4}$',
        r'^\d{8}$',
    ]
    return any(re.match(p, s) for p in patterns)


def _parse_date_val(v):
    """尝试解析日期值，返回 datetime.date 或 None"""
    import re
    from datetime import datetime
    s = str(v).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%Y年%m月%d日"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    m = re.match(r'^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$', s)
    if m:
        try:
            return datetime(int(m[1]), int(m[2]), int(m[3])).date()
        except ValueError:
            pass
    return None


class ReconcileRequest(BaseModel):
    sessionA: str
    tableA: str
    sessionB: str
    tableB: str
    keyColumns: List[str]
    amountColumn: Optional[str] = None
    amountTolerance: float = 0.01
    dateColumn: Optional[str] = None
    dateToleranceDays: int = 0


class MergeSource(BaseModel):
    sessionId: str
    tableName: str


class MergeRequest(BaseModel):
    sources: List[MergeSource]
    targetTableName: str = "合并数据"
    mergeMode: str = "union"  # union(并集) | intersect(交集)
    addSource: bool = True


@router.get("/{session_id}/tables")
async def list_tables(req: Request, session_id: str):
    """列出所有可用的表（sheet）"""
    _check_session_ownership(session_id, _user_id(req))
    db_path = get_db_path(session_id)
    if not db_path:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")

    tables = DatabaseService.list_tables(db_path)
    result = []
    for t in tables:
        if t.startswith("_snapshot_"):
            continue  # 隐藏清洗快照表
        headers = DatabaseService.get_headers(db_path, t)
        result.append({"name": t, "headers": headers})

    return {"tables": result}


@router.get("/{session_id}/query", response_model=TableData)
async def query_data(
    req: Request,
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
    _check_session_ownership(session_id, _user_id(req))
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
async def get_stats(req: Request, session_id: str, tableName: str = Query(default="data")):
    """获取表统计信息"""
    _check_session_ownership(session_id, _user_id(req))
    db_path = get_db_path(session_id)
    if not db_path:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")

    table_name = sanitize_table_name(tableName)
    stats = DatabaseService.get_table_stats(db_path, table_name)
    return StatsResponse(sheetName=tableName, **stats)


@router.get("/{session_id}/filters/{column}")
async def get_filter_options(req: Request, session_id: str, column: str, tableName: str = Query(default="data")):
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
    req: Request,
    session_id: str,
    request: UpdateDataRequest
):
    """更新数据行"""
    _check_session_ownership(session_id, _user_id(req))
    db_path = get_db_path(session_id)
    if not db_path:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")

    table_name = sanitize_table_name(request.tableName)
    affected = DatabaseService.update_row(db_path, table_name, request.rowId, request.updates)
    return {"success": affected > 0, "affectedRows": affected}


@router.get("/{session_id}/export")
async def export_data(
    req: Request,
    session_id: str,
    tableName: str = Query(default="data"),
    format: str = Query(default="csv", regex="^(csv|xlsx)$"),
    filters: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    allSheets: bool = Query(default=False)
):
    """导出数据（支持 CSV 和 XLSX 格式，可导出当前筛选后的数据）"""
    _check_session_ownership(session_id, _user_id(req))
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
    req: Request,
    session_id: str,
    tableName: str = Query(default="data"),
    columns: Optional[List[str]] = Body(default=None)
):
    """数据去重（基于指定列或所有列）"""
    _check_session_ownership(session_id, _user_id(req))
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
    req: Request,
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
    req: Request,
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
        "changedRows": diff_details
    }


# ==================== CRUD：行/列操作 ====================

@router.post("/{session_id}/rows")
async def insert_rows(req: Request, session_id: str, request: InsertRowsRequest):
    """插入单行/多行"""
    _check_session_ownership(session_id, _user_id(req))
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
async def delete_rows(req: Request, session_id: str, request: DeleteRowsRequest):
    """按 row_id 删除单行/多行"""
    _check_session_ownership(session_id, _user_id(req))
    db_path = get_db_path(session_id)
    if not db_path:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")

    table_name = sanitize_table_name(request.tableName)

    if not request.rowIds:
        raise HTTPException(status_code=400, detail="rowIds 不能为空")

    count = DatabaseService.delete_rows(db_path, table_name, request.rowIds)
    return {"success": True, "affectedRows": count}


@router.post("/{session_id}/columns")
async def add_column(req: Request, session_id: str, request: AddColumnRequest):
    """新增列"""
    _check_session_ownership(session_id, _user_id(req))
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
async def drop_column(req: Request, session_id: str, column: str, tableName: str = Query(default="data")):
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
async def rename_column(req: Request, session_id: str, column: str, request: RenameColumnRequest):
    """重命名列"""
    _check_session_ownership(session_id, _user_id(req))
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
async def regex_replace(req: Request, session_id: str, request: RegexReplaceRequest):
    """正则替换"""
    _check_session_ownership(session_id, _user_id(req))
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
async def split_column(req: Request, session_id: str, request: SplitColumnRequest):
    """按分隔符拆列"""
    _check_session_ownership(session_id, _user_id(req))
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
async def convert_type(req: Request, session_id: str, request: ConvertTypeRequest):
    """数据类型转换"""
    _check_session_ownership(session_id, _user_id(req))
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


@router.post("/{session_id}/clean/preview")
async def clean_preview(req: Request, session_id: str, request: CleanPreviewRequest):
    """清洗预览（dry-run）：返回将影响的行数与样本，不修改数据"""
    _check_session_ownership(session_id, _user_id(req))
    db_path = get_db_path(session_id)
    if not db_path:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")
    table_name = sanitize_table_name(request.tableName)
    headers = DatabaseService.get_headers(db_path, table_name)
    op = request.operation

    def q(ident):
        return f'`{ident}`' if config.DB_TYPE == 'mysql' else f'"{ident}"'

    def fetch_count(sql, params=None):
        with DatabaseService.get_connection(db_path) as conn:
            cur = conn.cursor()
            cur.execute(sql, params or [])
            row = cur.fetchone()
            if config.DB_TYPE == 'mysql':
                return list(row.values())[0]
            return row[0]

    # 总行数
    total = fetch_count(f'SELECT COUNT(*) FROM {q(table_name)}')

    samples = []
    affected = 0
    note = ""

    if op == "deduplicate":
        cols = request.columns or headers
        col_list = ", ".join(q(c) for c in cols if c in headers)
        if not col_list:
            raise HTTPException(status_code=400, detail="指定的列不存在")
        affected = fetch_count(
            f'SELECT COUNT(*) FROM {q(table_name)} WHERE row_id NOT IN '
            f'(SELECT MIN(row_id) FROM {q(table_name)} GROUP BY {col_list})'
        )
        with DatabaseService.get_connection(db_path) as conn:
            cur = conn.cursor()
            cur.execute(
                f'SELECT {col_list} FROM {q(table_name)} WHERE row_id NOT IN '
                f'(SELECT MIN(row_id) FROM {q(table_name)} GROUP BY {col_list}) LIMIT 5'
            )
            for r in cur.fetchall():
                samples.append({"before": " | ".join("" if x is None else str(x) for x in r), "after": "（删除）"})
        note = "保留每组重复行的第一条，删除其余重复行"

    elif op == "fill-empty":
        col = request.fillColumn
        if col not in headers:
            raise HTTPException(status_code=400, detail="指定的列不存在")
        affected = fetch_count(
            f'SELECT COUNT(*) FROM {q(table_name)} WHERE {q(col)} IS NULL OR {q(col)} = ""'
        )
        with DatabaseService.get_connection(db_path) as conn:
            cur = conn.cursor()
            cur.execute(
                f'SELECT {q(col)} FROM {q(table_name)} WHERE {q(col)} IS NULL OR {q(col)} = "" LIMIT 5'
            )
            for r in cur.fetchall():
                samples.append({"before": "(空)" if r[0] is None or r[0] == "" else str(r[0]), "after": request.fillValue})
        note = f"将列「{col}」中的空值填充为「{request.fillValue or '(空)'}」"

    elif op == "regex-replace":
        import re as _re
        col = request.column
        if col not in headers:
            raise HTTPException(status_code=400, detail=f"列 '{col}' 不存在")
        try:
            compiled = _re.compile(request.pattern)
        except _re.error as e:
            raise HTTPException(status_code=400, detail=f"正则表达式语法错误: {e}")
        with DatabaseService.get_connection(db_path) as conn:
            cur = conn.cursor()
            cur.execute(f'SELECT {q(col)} FROM {q(table_name)}')
            for r in cur.fetchall():
                val = r[0]
                if val is None:
                    continue
                str_val = str(val)
                new_val = compiled.sub(request.replacement, str_val)
                if new_val != str_val:
                    affected += 1
                    if len(samples) < 5:
                        samples.append({"before": str_val, "after": new_val})
        note = f"将列「{col}」中匹配 /{request.pattern}/ 的内容替换为「{request.replacement}」"

    elif op == "split-column":
        col = request.sourceColumn
        if col not in headers:
            raise HTTPException(status_code=400, detail=f"列 '{col}' 不存在")
        delim = request.delimiter
        if not delim:
            raise HTTPException(status_code=400, detail="分隔符不能为空")
        max_parts = 0
        with DatabaseService.get_connection(db_path) as conn:
            cur = conn.cursor()
            cur.execute(f'SELECT {q(col)} FROM {q(table_name)}')
            for r in cur.fetchall():
                val = r[0]
                if not val:
                    continue
                parts = str(val).split(delim, request.maxSplits) if request.maxSplits > 0 else str(val).split(delim)
                max_parts = max(max_parts, len(parts))
                if len(parts) > 1:
                    affected += 1
                    if len(samples) < 5:
                        samples.append({"before": str(val), "after": " | ".join(p.strip() for p in parts)})
        if max_parts > 1:
            new_cols = ", ".join(f"{col}_{i}" for i in range(1, max_parts + 1))
            note = f"将列「{col}」按「{delim}」拆分，新增 {max_parts} 列（{new_cols}）"
        else:
            note = f"未发现包含分隔符「{delim}」的值，不会新增列"

    elif op == "convert-type":
        col = request.column
        if col not in headers:
            raise HTTPException(status_code=400, detail=f"列 '{col}' 不存在")
        tt = request.targetType
        if tt not in ("string", "number", "date"):
            raise HTTPException(status_code=400, detail="targetType 必须是 string/number/date")
        from datetime import datetime as _dt
        with DatabaseService.get_connection(db_path) as conn:
            cur = conn.cursor()
            cur.execute(
                f'SELECT row_id, {q(col)} FROM {q(table_name)} '
                f'WHERE {q(col)} IS NOT NULL AND {q(col)} != ""'
            )
            rows = cur.fetchall()
        fail = 0
        for row in rows:
            rid, val = row[0], row[1]
            try:
                if tt == "number":
                    new_val = str(float(val))
                elif tt == "date":
                    parsed = None
                    for fmt in ["%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y", "%Y%m%d",
                                "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"]:
                        try:
                            parsed = _dt.strptime(str(val).strip(), fmt)
                            break
                        except ValueError:
                            continue
                    if not parsed:
                        fail += 1
                        if len(samples) < 5:
                            samples.append({"before": str(val), "after": "❌ 无法解析"})
                        continue
                    out_fmt = request.dateFormat or "%Y-%m-%d"
                    new_val = parsed.strftime(out_fmt)
                else:
                    new_val = str(val)
                affected += 1
                if len(samples) < 5:
                    samples.append({"before": str(val), "after": new_val})
            except (ValueError, TypeError):
                fail += 1
                if len(samples) < 5:
                    samples.append({"before": str(val), "after": "❌ 转换失败"})
        type_name = {"string": "文本", "number": "数字", "date": "日期"}[tt]
        note = f"将列「{col}」转为{type_name}"
        if fail:
            note += f"，其中 {fail} 条无法转换将保持原值"

    else:
        raise HTTPException(status_code=400, detail=f"不支持的操作: {op}")

    return {"success": True, "affectedCount": affected, "totalRows": total, "samples": samples, "note": note}


@router.post("/{session_id}/clean/validate")
async def validate_data(req: Request, session_id: str, request: ValidateRequest):
    """数据校验"""
    _check_session_ownership(session_id, _user_id(req))
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


@router.post("/merge")
async def merge_tables(request: MergeRequest):
    """合并多个会话的表为一张新表，可选追加「来源文件名」「导入时间」列"""
    if not request.sources or len(request.sources) < 2:
        raise HTTPException(status_code=400, detail="至少选择 2 个表进行合并")

    # 1. 收集每个源的列结构和全量数据
    sources_data = []
    for src in request.sources:
        db_path = get_db_path(src.sessionId)
        if not db_path:
            raise HTTPException(status_code=404, detail=f"会话 {src.sessionId} 不存在或已过期")
        try:
            headers = DatabaseService.get_headers(db_path, src.tableName)
        except Exception:
            raise HTTPException(status_code=400, detail=f"表 {src.tableName} 不存在")
        if not headers:
            continue
        cols_sql = ', '.join([f'"{h}"' for h in headers])
        _, rows = DatabaseService.execute_sql(db_path, f'SELECT {cols_sql} FROM "{src.tableName}"')
        progress = taskManager.get_progress(src.sessionId)
        file_name = progress.fileName if progress else src.tableName
        sources_data.append({"headers": headers, "rows": rows, "fileName": file_name})

    if not sources_data:
        raise HTTPException(status_code=400, detail="没有可合并的数据")

    # 2. 确定合并后的列
    if request.mergeMode == "intersect":
        common = set(sources_data[0]["headers"])
        for s in sources_data[1:]:
            common &= set(s["headers"])
        merged_headers = [h for h in sources_data[0]["headers"] if h in common]
    else:  # union
        merged_headers = []
        for s in sources_data:
            for h in s["headers"]:
                if h not in merged_headers:
                    merged_headers.append(h)

    if not merged_headers:
        raise HTTPException(status_code=400, detail="合并后没有有效列")

    if request.addSource:
        if "来源文件名" not in merged_headers:
            merged_headers.append("来源文件名")
        if "导入时间" not in merged_headers:
            merged_headers.append("导入时间")

    # 3. 创建新会话并写入合并数据
    new_session_id = str(uuid.uuid4())[:8]
    new_db_path = config.get_db_path(new_session_id)
    new_db_path.parent.mkdir(parents=True, exist_ok=True)
    target_table = sanitize_table_name(request.targetTableName) or "合并数据"
    import_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    total_rows = 0
    with DatabaseService.get_connection(new_db_path) as conn:
        DatabaseService.optimize_for_insert(conn)
        DatabaseService.init_table(conn, target_table, merged_headers, new_db_path)
        for s in sources_data:
            insert_rows = []
            for row in s["rows"]:
                row_dict = dict(zip(s["headers"], row))
                new_row = []
                for h in merged_headers:
                    if h == "来源文件名":
                        new_row.append(s["fileName"])
                    elif h == "导入时间":
                        new_row.append(import_time)
                    else:
                        v = row_dict.get(h, "")
                        new_row.append("" if v is None else str(v))
                insert_rows.append(tuple(new_row))
            if insert_rows:
                DatabaseService.bulk_insert(conn, target_table, merged_headers, insert_rows, new_db_path)
                total_rows += len(insert_rows)
        conn.commit()

    # 4. 注册到 taskManager 使其出现在文件列表
    taskManager.register_merged_session(
        new_session_id,
        f"合并({len(sources_data)}表)",
        target_table,
        merged_headers,
        total_rows
    )

    return {
        "sessionId": new_session_id,
        "tableName": target_table,
        "rowCount": total_rows,
        "headers": merged_headers
    }


@router.post("/{session_id}/snapshot/{table_name}")
async def create_snapshot(req: Request, session_id: str, table_name: str):
    """保存当前表快照，供清洗操作撤销使用"""
    db_path = get_db_path(session_id)
    if not db_path:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")
    snap = f"_snapshot_{table_name}"
    try:
        with DatabaseService.get_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(f'DROP TABLE IF EXISTS "{snap}"')
            cursor.execute(f'CREATE TABLE "{snap}" AS SELECT * FROM "{table_name}"')
            conn.commit()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"快照失败: {e}")
    return {"success": True, "message": "已保存快照"}


@router.post("/{session_id}/undo/{table_name}")
async def undo_table(req: Request, session_id: str, table_name: str):
    """撤销到上次快照（回退最近一次清洗操作）"""
    _check_session_ownership(session_id, _user_id(req))
    db_path = get_db_path(session_id)
    if not db_path:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")
    snap = f"_snapshot_{table_name}"
    tables = DatabaseService.list_tables(db_path)
    if snap not in tables:
        raise HTTPException(status_code=404, detail="没有可撤销的操作")
    headers = DatabaseService.get_headers(db_path, table_name)
    cols = ', '.join([f'"{h}"' for h in headers])
    try:
        with DatabaseService.get_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(f'DELETE FROM "{table_name}"')
            cursor.execute(f'INSERT INTO "{table_name}" ({cols}) SELECT {cols} FROM "{snap}"')
            conn.commit()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"撤销失败: {e}")
    return {"success": True, "message": "已撤销上一步"}


@router.get("/{session_id}/quality-check/{table_name}")
async def quality_check(req: Request, session_id: str, table_name: str):
    """一键数据质量体检：扫描空值、重复值、负数、日期格式错误等"""
    _check_session_ownership(session_id, _user_id(req))
    from collections import Counter
    db_path = get_db_path(session_id)
    if not db_path:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")
    headers = DatabaseService.get_headers(db_path, table_name)
    cols_sql = ', '.join([f'"{h}"' for h in headers])
    _, rows = DatabaseService.execute_sql(db_path, f'SELECT {cols_sql} FROM "{table_name}"')
    total = len(rows)

    columns_report = []
    total_issues = 0
    for i, h in enumerate(headers):
        col_vals = [str(row[i]).strip() if row[i] is not None else "" for row in rows]
        empty_count = sum(1 for v in col_vals if v == "")
        non_empty = [v for v in col_vals if v != ""]

        is_number = bool(non_empty) and sum(1 for v in non_empty if _is_number(v)) / len(non_empty) >= 0.7
        is_date = (not is_number) and bool(non_empty) and sum(1 for v in non_empty if _is_date(v)) / len(non_empty) >= 0.7

        counter = Counter(non_empty)
        duplicates = {k: c for k, c in counter.items() if c > 1}
        dup_count = sum(c - 1 for c in duplicates.values())
        top_dups = sorted(duplicates.items(), key=lambda x: -x[1])[:3]

        issues = []
        if empty_count > 0:
            issues.append({"type": "empty", "count": empty_count, "detail": f"{empty_count} 个空值"})
        if dup_count > 0:
            detail = "、".join([f"「{k}」{c}次" for k, c in top_dups])
            issues.append({"type": "duplicate", "count": dup_count, "detail": f"{dup_count} 行重复，如 {detail}"})
        if is_number:
            neg_count = sum(1 for v in non_empty if _is_number(v) and float(v) < 0)
            if neg_count > 0:
                issues.append({"type": "negative", "count": neg_count, "detail": f"{neg_count} 个负数"})
        if is_date:
            bad_date = sum(1 for v in non_empty if not _is_date(v))
            if bad_date > 0:
                issues.append({"type": "bad_date", "count": bad_date, "detail": f"{bad_date} 个日期格式错误"})

        col_type = "number" if is_number else ("date" if is_date else "text")
        level = "ok"
        if any(it["type"] in ("negative", "bad_date") for it in issues):
            level = "error"
        elif issues:
            level = "warning"

        total_issues += len(issues)
        columns_report.append({
            "name": h,
            "type": col_type,
            "emptyCount": empty_count,
            "emptyRate": round(empty_count / total, 2) if total else 0,
            "duplicateCount": dup_count,
            "issues": issues,
            "level": level
        })

    return {
        "totalRows": total,
        "columns": columns_report,
        "summary": {
            "totalColumns": len(headers),
            "healthyColumns": sum(1 for c in columns_report if c["level"] == "ok"),
            "warningColumns": sum(1 for c in columns_report if c["level"] == "warning"),
            "errorColumns": sum(1 for c in columns_report if c["level"] == "error"),
            "totalIssues": total_issues
        }
    }


@router.post("/reconcile")
async def reconcile_tables(request: ReconcileRequest):
    """增强对账：多列匹配 + 金额/日期容差，分类差异（仅A有/仅B有/金额不符/日期不符）"""
    dbA = get_db_path(request.sessionA)
    dbB = get_db_path(request.sessionB)
    if not dbA or not dbB:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")
    try:
        headersA = DatabaseService.get_headers(dbA, request.tableA)
        headersB = DatabaseService.get_headers(dbB, request.tableB)
    except Exception:
        raise HTTPException(status_code=400, detail="表不存在")
    for k in request.keyColumns:
        if k not in headersA or k not in headersB:
            raise HTTPException(status_code=400, detail=f"匹配列 {k} 在两表中不存在")
    colsA = ', '.join([f'"{h}"' for h in headersA])
    colsB = ', '.join([f'"{h}"' for h in headersB])
    _, rowsA = DatabaseService.execute_sql(dbA, f'SELECT {colsA} FROM "{request.tableA}"')
    _, rowsB = DatabaseService.execute_sql(dbB, f'SELECT {colsB} FROM "{request.tableB}"')

    idxA = [headersA.index(k) for k in request.keyColumns]
    idxB = [headersB.index(k) for k in request.keyColumns]

    def make_key(row, idx):
        return "|".join(str(row[i]).strip() if row[i] is not None else "" for i in idx)

    dictA = {make_key(r, idxA): r for r in rowsA}
    dictB = {make_key(r, idxB): r for r in rowsB}

    onlyA, onlyB, matched = [], [], []
    amountMismatch, dateMismatch = [], []
    for k, ra in dictA.items():
        if k in dictB:
            rb = dictB[k]
            matched.append(k)
            if request.amountColumn and request.amountColumn in headersA and request.amountColumn in headersB:
                ai = headersA.index(request.amountColumn)
                bi = headersB.index(request.amountColumn)
                try:
                    aAmt = float(str(ra[ai]).strip() or 0)
                    bAmt = float(str(rb[bi]).strip() or 0)
                    if abs(aAmt - bAmt) > request.amountTolerance:
                        amountMismatch.append({"key": k, "a": str(ra[ai]), "b": str(rb[bi]), "diff": round(aAmt - bAmt, 2)})
                except (ValueError, TypeError):
                    pass
            if request.dateColumn and request.dateColumn in headersA and request.dateColumn in headersB:
                ai = headersA.index(request.dateColumn)
                bi = headersB.index(request.dateColumn)
                da = _parse_date_val(ra[ai])
                db_ = _parse_date_val(rb[bi])
                if da and db_:
                    diff_days = abs((da - db_).days)
                    if diff_days > request.dateToleranceDays:
                        dateMismatch.append({"key": k, "a": str(ra[ai]), "b": str(rb[bi]), "diffDays": diff_days})
        else:
            onlyA.append(k)
    for k in dictB:
        if k not in dictA:
            onlyB.append(k)

    return {
        "stats": {
            "onlyA": len(onlyA),
            "onlyB": len(onlyB),
            "matched": len(matched),
            "amountMismatch": len(amountMismatch),
            "dateMismatch": len(dateMismatch),
        },
        "samples": {
            "onlyA": onlyA[:50],
            "onlyB": onlyB[:50],
            "amountMismatch": amountMismatch[:50],
            "dateMismatch": dateMismatch[:50],
        }
    }

    return (True, "")
