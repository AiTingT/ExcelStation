import threading
from dataclasses import dataclass, field
from typing import Dict, Optional, List
from pathlib import Path
import logging
import uuid
import time

from app.config import config
from app.services.excelParser import ExcelParserService, FileEncryptedError
from app.services.database import DatabaseService

logger = logging.getLogger(__name__)


def _deduplicate_headers(headers: List[str]) -> List[str]:
    """对重复列名添加数字后缀，避免 SQLite 报 duplicate column name 错误。
    空列名自动命名为 Column_N。

    例如 ["个", "个", "", "个"] → ["个", "个_2", "Column_1", "个_3"]
    """
    seen: Dict[str, int] = {}
    result: List[str] = []
    col_idx = 0
    for h in headers:
        col_idx += 1
        if not h or not h.strip():
            h = f"Column_{col_idx}"
        if h in seen:
            seen[h] += 1
            result.append(f"{h}_{seen[h]}")
        else:
            seen[h] = 1
            result.append(h)
    return result


@dataclass
class SheetInfo:
    """工作表信息"""
    name: str
    headers: List[str] = field(default_factory=list)
    rowCount: int = 0


@dataclass
class ParseProgress:
    """解析进度"""
    sessionId: str
    fileName: str
    status: str
    sheets: List[SheetInfo] = field(default_factory=list)
    currentSheet: str = ""
    totalRows: int = 0
    processedRows: int = 0
    error: Optional[str] = None
    isEncrypted: bool = False
    password: Optional[str] = None
    parseTime: float = 0.0


class ParseTaskManager:
    """解析任务管理器"""

    def __init__(self):
        self._tasks: Dict[str, ParseProgress] = {}
        self._lock = threading.Lock()

    def create_task(self, file_name: str, file_path: Path, password: Optional[str] = None) -> str:
        """创建解析任务，返回 sessionId"""
        session_id = str(uuid.uuid4())[:8]
        progress = ParseProgress(
            sessionId=session_id,
            fileName=file_name,
            status="pending",
            password=password
        )

        with self._lock:
            self._tasks[session_id] = progress

        thread = threading.Thread(
            target=self._parse_worker,
            args=(session_id, file_path),
            daemon=True
        )
        thread.start()

        return session_id

    def retry_with_password(self, session_id: str, password: str) -> bool:
        """使用密码重试解析加密文件"""
        with self._lock:
            task = self._tasks.get(session_id)
            if not task or task.status != "error" or not task.isEncrypted:
                return False
            task.status = "pending"
            task.error = None
            task.password = password

        file_path = None
        for f in config.UPLOAD_DIR.glob(f"*{session_id}*"):
            file_path = f
            break

        if not file_path or not file_path.exists():
            return False

        thread = threading.Thread(
            target=self._parse_worker,
            args=(session_id, file_path),
            daemon=True
        )
        thread.start()
        return True

    def get_progress(self, session_id: str) -> Optional[ParseProgress]:
        """获取任务进度"""
        with self._lock:
            return self._tasks.get(session_id)

    def list_sessions(self) -> List[dict]:
        """列出所有会话"""
        with self._lock:
            return [
                {
                    "sessionId": s.sessionId,
                    "fileName": s.fileName,
                    "status": s.status,
                    "totalRows": s.totalRows,
                    "isEncrypted": s.isEncrypted,
                    "parseTime": s.parseTime,
                    "sheets": [{"name": sh.name, "rowCount": sh.rowCount, "headers": sh.headers} for sh in s.sheets]
                }
                for s in self._tasks.values()
            ]

    def delete_session(self, session_id: str) -> bool:
        """删除会话及数据"""
        with self._lock:
            if session_id not in self._tasks:
                return False
            del self._tasks[session_id]

        # 清理数据库
        db_path = config.get_db_path(session_id)
        if config.DB_TYPE == 'mysql':
            DatabaseService.delete_session(session_id)
        else:
            if db_path.exists():
                db_path.unlink()

        # 清理上传文件
        for f in config.UPLOAD_DIR.glob(f"*{session_id}*"):
            f.unlink(missing_ok=True)

        return True

    def register_merged_session(self, session_id: str, file_name: str, table_name: str, headers: List[str], row_count: int) -> None:
        """注册一个合并产生的会话（不经过解析流程），使其出现在文件列表中"""
        sheet_info = SheetInfo(name=table_name, headers=headers, rowCount=row_count)
        progress = ParseProgress(
            sessionId=session_id,
            fileName=file_name,
            status="completed",
            sheets=[sheet_info],
            totalRows=row_count,
            processedRows=row_count,
            parseTime=0.0
        )
        with self._lock:
            self._tasks[session_id] = progress

    def _update_progress(self, session_id: str, **kwargs):
        """更新进度"""
        with self._lock:
            task = self._tasks.get(session_id)
            if task:
                for key, value in kwargs.items():
                    setattr(task, key, value)

    def _parse_worker(self, session_id: str, file_path: Path):
        """后台解析线程 - 解析所有 sheet"""
        db_path = config.get_db_path(session_id)
        try:
            self._update_progress(session_id, status="parsing")
            t0 = time.time()

            file_ext = file_path.suffix.lower()
            db_path.parent.mkdir(parents=True, exist_ok=True)

            if file_ext == ".csv":
                self._parse_csv(session_id, file_path, db_path)
            else:
                self._parse_excel(session_id, file_path, db_path)

            elapsed = time.time() - t0
            logger.info(f"任务 {session_id} 完成，耗时 {elapsed:.1f}s")
            self._update_progress(session_id, parseTime=elapsed)

        except FileEncryptedError as e:
            logger.warning(f"任务 {session_id} 文件已加密: {e}")
            self._update_progress(
                session_id,
                status="error",
                error=str(e.message),
                isEncrypted=True
            )

        except Exception as e:
            logger.error(f"任务 {session_id} 失败: {e}", exc_info=True)
            self._update_progress(session_id, status="error", error=str(e))

            if config.DB_TYPE == 'mysql':
                DatabaseService.delete_session(session_id)
            else:
                if db_path.exists():
                    db_path.unlink()

    def _parse_csv(self, session_id: str, file_path: Path, db_path: Path):
        """解析 CSV 文件"""
        headers, row_iter = ExcelParserService.parse_csv(file_path)
        sheet_info = SheetInfo(name="CSV", headers=headers)

        self._update_progress(
            session_id,
            sheets=[sheet_info],
            currentSheet="CSV",
            totalRows=0
        )

        with DatabaseService.get_connection(db_path) as conn:
            DatabaseService.optimize_for_insert(conn)
            DatabaseService.init_table(conn, "CSV", headers, db_path)

            chunk = []
            processed = 0
            for row in row_iter:
                chunk.append(ExcelParserService.convert_to_str_tuple(row, len(headers)))
                if len(chunk) >= 5000:
                    DatabaseService.bulk_insert(conn, "CSV", headers, chunk, db_path)
                    processed += len(chunk)
                    self._update_progress(session_id, processedRows=processed)
                    chunk = []

            if chunk:
                DatabaseService.bulk_insert(conn, "CSV", headers, chunk, db_path)
                processed += len(chunk)

            conn.commit()

        sheet_info.rowCount = processed
        self._update_progress(
            session_id,
            status="completed",
            processedRows=processed,
            totalRows=processed,
            sheets=[sheet_info]
        )

    def _parse_excel(self, session_id: str, file_path: Path, db_path: Path):
        """解析 Excel 文件 - 所有 sheet，一次性读取，性能优化"""
        password = self._tasks[session_id].password if session_id in self._tasks else None

        logger.info(f"任务 {session_id} 开始解析 Excel, 密码: {'有' if password else '无'}")

        all_sheet_data = ExcelParserService.parse_all_sheets(file_path, password)
        all_sheets = []
        total_processed = 0
        total_rows = sum(len(s["rows"]) for s in all_sheet_data)

        with DatabaseService.get_connection(db_path) as conn:
            DatabaseService.optimize_for_insert(conn)

            for sheet_data in all_sheet_data:
                sheet_name = sheet_data["name"]
                headers = sheet_data["headers"]
                data_rows = sheet_data["rows"]

                logger.info(f"任务 {session_id} 解析表: {sheet_name}, {len(data_rows)} 行")
                self._update_progress(session_id, currentSheet=sheet_name, totalRows=total_rows)

                if not data_rows:
                    logger.info(f"表 {sheet_name} 无数据，跳过")
                    continue

                data_rows = ExcelParserService.clean_rows(data_rows)
                headers, data_rows = ExcelParserService.clean_empty_columns(data_rows, headers)
                headers = _deduplicate_headers(headers)

                if not data_rows:
                    logger.info(f"表 {sheet_name} 清洗后无数据，跳过")
                    continue

                sheet_info_obj = SheetInfo(
                    name=sheet_name,
                    headers=headers,
                    rowCount=len(data_rows)
                )
                all_sheets.append(sheet_info_obj)

                table_name = sheet_name.replace(" ", "_").replace("-", "_")
                DatabaseService.init_table(conn, table_name, headers, db_path)

                BATCH_SIZE = 10000
                converted_rows = []
                for row in data_rows:
                    converted_rows.append(ExcelParserService.convert_to_str_tuple(tuple(row), len(headers)))
                    if len(converted_rows) >= BATCH_SIZE:
                        DatabaseService.bulk_insert(conn, table_name, headers, converted_rows, db_path)
                        total_processed += len(converted_rows)
                        self._update_progress(session_id, processedRows=total_processed, sheets=all_sheets.copy())
                        converted_rows = []

                if converted_rows:
                    DatabaseService.bulk_insert(conn, table_name, headers, converted_rows, db_path)
                    total_processed += len(converted_rows)
                    self._update_progress(session_id, processedRows=total_processed, sheets=all_sheets.copy())

                logger.info(f"表 {sheet_name} 完成: {len(data_rows)} 行")

            conn.commit()

        self._update_progress(
            session_id,
            status="completed",
            processedRows=total_processed,
            totalRows=total_processed,
            sheets=all_sheets
        )
        logger.info(f"任务 {session_id} 完成，共 {len(all_sheets)} 个表，{total_processed} 行")


# 全局实例
taskManager = ParseTaskManager()
