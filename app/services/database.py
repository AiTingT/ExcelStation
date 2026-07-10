from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from contextlib import contextmanager
import logging
import sqlite3
import threading
from app.config import config

try:
    import pymysql
    HAS_PYMYSQL = True
except ImportError:
    HAS_PYMYSQL = False

logger = logging.getLogger(__name__)

_thread_local = threading.local()


class DatabaseService:
    """数据库服务 - 支持 SQLite 和 MySQL 双模式"""

    @staticmethod
    def _get_mysql_db_name(session_id: str) -> str:
        return config.MYSQL_DATABASE

    @staticmethod
    def _get_full_table_name(session_id: str, table_name: str) -> str:
        if config.DB_TYPE == 'mysql':
            return f'{session_id}_{table_name}'
        return table_name

    @staticmethod
    def _extract_session_id(db_path):
        session_id = str(db_path).replace('.db', '')
        if '/' in session_id:
            session_id = session_id.split('/')[-1]
        if '\\' in session_id:
            session_id = session_id.split('\\')[-1]
        return session_id

    @staticmethod
    def _create_mysql_db(db_name: str):
        conn = pymysql.connect(
            host=config.MYSQL_HOST,
            port=config.MYSQL_PORT,
            user=config.MYSQL_USER,
            password=config.MYSQL_PASSWORD,
            charset='utf8mb4'
        )
        try:
            with conn.cursor() as cursor:
                cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _get_cached_connection(db_name: str):
        if not hasattr(_thread_local, 'connections'):
            _thread_local.connections = {}
        if db_name not in _thread_local.connections:
            return None
        conn = _thread_local.connections[db_name]
        try:
            conn.ping(reconnect=True)
            return conn
        except Exception:
            if db_name in _thread_local.connections:
                del _thread_local.connections[db_name]
            return None

    @staticmethod
    def _cache_connection(db_name: str, conn):
        if not hasattr(_thread_local, 'connections'):
            _thread_local.connections = {}
        _thread_local.connections[db_name] = conn

    @staticmethod
    def _close_cached_connection(db_name: str):
        if hasattr(_thread_local, 'connections') and db_name in _thread_local.connections:
            try:
                _thread_local.connections[db_name].close()
            except Exception:
                pass
            del _thread_local.connections[db_name]

    @staticmethod
    @contextmanager
    def get_connection(db_path_or_session):
        if config.DB_TYPE == 'mysql':
            if not HAS_PYMYSQL:
                raise RuntimeError("MySQL 模式需要安装 pymysql，请执行: pip install pymysql")
            session_id = str(db_path_or_session).replace('.db', '')
            if '/' in session_id:
                session_id = session_id.split('/')[-1]
            if '\\' in session_id:
                session_id = session_id.split('\\')[-1]
            db_name = DatabaseService._get_mysql_db_name(session_id)
            
            conn = DatabaseService._get_cached_connection(db_name)
            if conn is None:
                try:
                    conn = pymysql.connect(
                        host=config.MYSQL_HOST,
                        port=config.MYSQL_PORT,
                        user=config.MYSQL_USER,
                        password=config.MYSQL_PASSWORD,
                        database=db_name,
                        charset='utf8mb4',
                        cursorclass=pymysql.cursors.DictCursor,
                        autocommit=False
                    )
                    DatabaseService._cache_connection(db_name, conn)
                except pymysql.err.OperationalError as e:
                    if 'Unknown database' in str(e):
                        DatabaseService._create_mysql_db(db_name)
                        conn = pymysql.connect(
                            host=config.MYSQL_HOST,
                            port=config.MYSQL_PORT,
                            user=config.MYSQL_USER,
                            password=config.MYSQL_PASSWORD,
                            database=db_name,
                            charset='utf8mb4',
                            cursorclass=pymysql.cursors.DictCursor,
                            autocommit=False
                        )
                        DatabaseService._cache_connection(db_name, conn)
                    else:
                        raise
            try:
                yield conn
            except Exception:
                raise
        else:
            db_path = Path(db_path_or_session) if not isinstance(db_path_or_session, Path) else db_path_or_session
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
            finally:
                conn.close()

    @staticmethod
    def _sanitize_header(h) -> str:
        """清洗列名：去除引号、控制字符等可能导致 SQL 语法错误的字符"""
        s = str(h).strip()
        if not s:
            return ""
        # 移除双引号、单引号、反引号，以及控制字符
        s = s.replace('"', '').replace("'", '').replace('`', '')
        s = ''.join(c for c in s if c.isprintable())
        return s

    @staticmethod
    def init_table(conn, table_name, headers, db_path=None):
        # 清洗列名：空列名 → Column_N，特殊字符 → 移除
        sanitized_headers = []
        for i, h in enumerate(headers):
            cleaned = DatabaseService._sanitize_header(h)
            if not cleaned:
                cleaned = f"Column_{i+1}"
            sanitized_headers.append(cleaned)
        headers = sanitized_headers

        if config.DB_TYPE == 'mysql' and db_path:
            session_id = DatabaseService._extract_session_id(db_path)
            table_name = DatabaseService._get_full_table_name(session_id, table_name)
        if config.DB_TYPE == 'mysql':
            cursor = conn.cursor()
            columns_def = ', '.join([f'`{h}` TEXT' for h in headers])
            cursor.execute(f"""CREATE TABLE IF NOT EXISTS `{table_name}` (
                row_id INTEGER PRIMARY KEY AUTO_INCREMENT,
                {columns_def}
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
            conn.commit()
        else:
            cursor = conn.cursor()
            columns_def = ', '.join([f'"{h}" TEXT' for h in headers])
            cursor.execute(f"""CREATE TABLE IF NOT EXISTS "{table_name}" (
                row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                {columns_def}
            )""")
            conn.commit()

    @staticmethod
    def bulk_insert(conn, table_name, headers, rows, db_path=None):
        # 同样清洗列名
        sanitized_headers = [DatabaseService._sanitize_header(h) or f"Column_{i+1}" for i, h in enumerate(headers)]
        headers = sanitized_headers

        if config.DB_TYPE == 'mysql' and db_path:
            session_id = DatabaseService._extract_session_id(db_path)
            table_name = DatabaseService._get_full_table_name(session_id, table_name)
        if config.DB_TYPE == 'mysql':
            cursor = conn.cursor()
            placeholders = ', '.join(['%s' for _ in headers])
            columns = ', '.join([f'`{h}`' for h in headers])
            
            max_rows_per_query = 500
            for i in range(0, len(rows), max_rows_per_query):
                chunk = rows[i:i + max_rows_per_query]
                values_template = ', '.join([f'({placeholders})' for _ in chunk])
                all_values = []
                for row in chunk:
                    all_values.extend(row)
                cursor.execute(f'INSERT INTO `{table_name}` ({columns}) VALUES {values_template}', all_values)
        else:
            cursor = conn.cursor()
            placeholders = ', '.join(['?' for _ in headers])
            columns = ', '.join([f'"{h}"' for h in headers])
            cursor.executemany(f'INSERT INTO "{table_name}" ({columns}) VALUES ({placeholders})', rows)

    @staticmethod
    def optimize_for_insert(conn):
        if config.DB_TYPE == 'mysql':
            cursor = conn.cursor()
            cursor.execute('SET autocommit = 0')
            cursor.execute('SET unique_checks = 0')
            cursor.execute('SET foreign_key_checks = 0')
        else:
            cursor = conn.cursor()
            cursor.execute('PRAGMA journal_mode = MEMORY')
            cursor.execute('PRAGMA synchronous = OFF')
            cursor.execute('PRAGMA cache_size = -200000')
            cursor.execute('PRAGMA temp_store = MEMORY')
            cursor.execute('PRAGMA mmap_size = 1073741824')

    @staticmethod
    def query_data(db_path, table_name='data', headers=None, page=1, page_size=100, sort_column=None, sort_order='asc', filters=None, search=None):
        session_id = DatabaseService._extract_session_id(db_path)
        full_table_name = DatabaseService._get_full_table_name(session_id, table_name)
        
        with DatabaseService.get_connection(db_path) as conn:
            cursor = conn.cursor()
            if headers is None:
                headers = DatabaseService.get_headers(db_path, table_name)
            where_clauses = []
            params = []
            if filters:
                for col, val in filters.items():
                    if val is not None and str(val).strip() != '':
                        if '|||' in str(val):
                            values = str(val).split('|||')
                            if config.DB_TYPE == 'mysql':
                                ph = ', '.join(['%s' for _ in values])
                                where_clauses.append(f'`{col}` IN ({ph})')
                            else:
                                ph = ', '.join(['?' for _ in values])
                                where_clauses.append(f'"{col}" IN ({ph})')
                            params.extend(values)
                        else:
                            like_p = f'%{val}%'
                            if config.DB_TYPE == 'mysql':
                                where_clauses.append(f'`{col}` LIKE %s')
                            else:
                                where_clauses.append(f'"{col}" LIKE ?')
                            params.append(like_p)
            if search and search.strip():
                search_conditions = []
                for col in headers:
                    like_p = f'%{search}%'
                    if config.DB_TYPE == 'mysql':
                        search_conditions.append(f'`{col}` LIKE %s')
                    else:
                        search_conditions.append(f'"{col}" LIKE ?')
                    params.append(like_p)
                if search_conditions:
                    where_clauses.append('(' + ' OR '.join(search_conditions) + ')')
            where_sql = ' WHERE ' + ' AND '.join(where_clauses) if where_clauses else ''
            if config.DB_TYPE == 'mysql':
                cursor.execute(f'SELECT COUNT(*) FROM `{full_table_name}`{where_sql}', params)
            else:
                cursor.execute(f'SELECT COUNT(*) FROM "{full_table_name}"{where_sql}', params)
            total_row = cursor.fetchone()
            if config.DB_TYPE == 'mysql':
                total = list(total_row.values())[0]
            else:
                total = total_row[0]
            order_sql = ''
            if sort_column and (sort_column in headers or sort_column == 'row_id'):
                if config.DB_TYPE == 'mysql':
                    order_sql = f' ORDER BY `{sort_column}` {sort_order.upper()}'
                else:
                    order_sql = f' ORDER BY "{sort_column}" {sort_order.upper()}'
            offset = (page - 1) * page_size
            if config.DB_TYPE == 'mysql':
                cols_sql = ', '.join([f'`{h}`' for h in headers])
                cursor.execute(f'SELECT row_id, {cols_sql} FROM `{full_table_name}`{where_sql}{order_sql} LIMIT %s OFFSET %s', params + [page_size, offset])
            else:
                cols_sql = ', '.join([f'"{h}"' for h in headers])
                cursor.execute(f'SELECT row_id, {cols_sql} FROM "{full_table_name}"{where_sql}{order_sql} LIMIT ? OFFSET ?', params + [page_size, offset])
            rows = cursor.fetchall()
            if config.DB_TYPE == 'mysql':
                return [tuple([row['row_id']] + [row[h] for h in headers]) for row in rows], total
            return [tuple([row[0]] + list(row[1:])) for row in rows], total

    @staticmethod
    def get_headers(db_path, table_name='data'):
        session_id = DatabaseService._extract_session_id(db_path)
        full_table_name = DatabaseService._get_full_table_name(session_id, table_name)
        
        with DatabaseService.get_connection(db_path) as conn:
            cursor = conn.cursor()
            if config.DB_TYPE == 'mysql':
                cursor.execute(f'SHOW COLUMNS FROM `{full_table_name}`')
                columns = [row['Field'] for row in cursor.fetchall() if row['Field'] != 'row_id']
            else:
                cursor.execute(f'SELECT name FROM pragma_table_info("{full_table_name}")')
                columns = [row[0] for row in cursor.fetchall() if row[0] != 'row_id']
            return columns

    @staticmethod
    def get_table_stats(db_path, table_name='data'):
        session_id = DatabaseService._extract_session_id(db_path)
        full_table_name = DatabaseService._get_full_table_name(session_id, table_name)
        
        with DatabaseService.get_connection(db_path) as conn:
            cursor = conn.cursor()
            if config.DB_TYPE == 'mysql':
                cursor.execute(f'SELECT COUNT(*) as cnt FROM `{full_table_name}`')
            else:
                cursor.execute(f'SELECT COUNT(*) FROM "{full_table_name}"')
            row_count_result = cursor.fetchone()
            if config.DB_TYPE == 'mysql':
                row_count = row_count_result['cnt']
            else:
                row_count = row_count_result[0]
            if config.DB_TYPE == 'mysql':
                cursor.execute(f"SELECT COUNT(*) as cnt FROM INFORMATION_SCHEMA.COLUMNS WHERE table_schema = DATABASE() AND table_name = '{full_table_name}'")
            else:
                cursor.execute(f'SELECT COUNT(*) FROM pragma_table_info("{full_table_name}")')
            col_count_result = cursor.fetchone()
            if config.DB_TYPE == 'mysql':
                column_count = list(col_count_result.values())[0]
            else:
                column_count = col_count_result[0] if isinstance(col_count_result, (list, tuple, dict)) else col_count_result
            headers = DatabaseService.get_headers(db_path, table_name)
            numeric_stats = {}
            for col in headers[:20]:
                try:
                    if config.DB_TYPE == 'mysql':
                        cursor.execute(f'SELECT AVG(CAST(`{col}` AS DECIMAL)), SUM(CAST(`{col}` AS DECIMAL)), MIN(CAST(`{col}` AS DECIMAL)), MAX(CAST(`{col}` AS DECIMAL)) FROM `{full_table_name}` WHERE `{col}` IS NOT NULL AND `{col}` != ''')
                    else:
                        cursor.execute(f'SELECT AVG(CAST("{col}" AS REAL)), SUM(CAST("{col}" AS REAL)), MIN(CAST("{col}" AS REAL)), MAX(CAST("{col}" AS REAL)) FROM "{full_table_name}" WHERE "{col}" IS NOT NULL AND "{col}" != ""')
                    result = cursor.fetchone()
                    if result:
                        if config.DB_TYPE == 'mysql':
                            vals = list(result.values())
                        else:
                            vals = result
                        if vals[0] is not None:
                            numeric_stats[col] = {'avg': round(vals[0], 2) if vals[0] else 0, 'sum': round(vals[1], 2) if vals[1] else 0, 'min': round(vals[2], 2) if vals[2] else 0, 'max': round(vals[3], 2) if vals[3] else 0}
                except Exception:
                    pass
            return {'rowCount': row_count, 'columnCount': column_count, 'numericColumns': list(numeric_stats.keys()), 'stats': numeric_stats}

    @staticmethod
    def get_filter_options(db_path, table_name, column, limit=100):
        session_id = DatabaseService._extract_session_id(db_path)
        full_table_name = DatabaseService._get_full_table_name(session_id, table_name)
        
        with DatabaseService.get_connection(db_path) as conn:
            cursor = conn.cursor()
            if config.DB_TYPE == 'mysql':
                cursor.execute(f'SELECT `{column}`, COUNT(*) as cnt FROM `{full_table_name}` WHERE `{column}` IS NOT NULL AND `{column}` != '' GROUP BY `{column}` ORDER BY cnt DESC LIMIT %s', [limit])
                return [(row[column], row['cnt']) for row in cursor.fetchall()]
            else:
                cursor.execute(f'SELECT "{column}", COUNT(*) as cnt FROM "{full_table_name}" WHERE "{column}" IS NOT NULL AND "{column}" != "" GROUP BY "{column}" ORDER BY cnt DESC LIMIT ?', [limit])
                return [(row[0], row[1]) for row in cursor.fetchall()]

    @staticmethod
    def list_tables(db_path):
        session_id = DatabaseService._extract_session_id(db_path)
        prefix = f'{session_id}_'
        
        with DatabaseService.get_connection(db_path) as conn:
            cursor = conn.cursor()
            if config.DB_TYPE == 'mysql':
                cursor.execute('SHOW TABLES')
                all_tables = [list(row.values())[0] for row in cursor.fetchall()]
                return [t[len(prefix):] for t in all_tables if t.startswith(prefix)]
            else:
                cursor.execute('SELECT name FROM sqlite_master WHERE type="table" AND name NOT LIKE "sqlite_%"')
                return [row[0] for row in cursor.fetchall()]

    @staticmethod
    def update_row(db_path, table_name, row_id, updates):
        session_id = DatabaseService._extract_session_id(db_path)
        full_table_name = DatabaseService._get_full_table_name(session_id, table_name)
        
        with DatabaseService.get_connection(db_path) as conn:
            cursor = conn.cursor()
            set_clauses = []
            params = []
            for col, val in updates.items():
                if config.DB_TYPE == 'mysql':
                    set_clauses.append(f'`{col}` = %s')
                else:
                    set_clauses.append(f'"{col}" = ?')
                params.append(val)
            params.append(row_id)
            
            if config.DB_TYPE == 'mysql':
                cursor.execute(f'UPDATE `{full_table_name}` SET {", ".join(set_clauses)} WHERE row_id = %s', params)
            else:
                cursor.execute(f'UPDATE "{full_table_name}" SET {", ".join(set_clauses)} WHERE row_id = ?', params)
            conn.commit()
            return 1

    @staticmethod
    def execute_sql(db_path, sql):
        with DatabaseService.get_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(sql)
            if config.DB_TYPE == 'mysql':
                columns = [desc[0] for desc in cursor.description] if cursor.description else []
                rows = cursor.fetchall()
                return columns, [tuple(row[col] for col in columns) for row in rows]
            else:
                columns = [desc[0] for desc in cursor.description] if cursor.description else []
                rows = cursor.fetchall()
                return columns, [tuple(row) for row in rows]

    @staticmethod
    def delete_session(session_id):
        if config.DB_TYPE == 'mysql':
            db_name = DatabaseService._get_mysql_db_name(session_id)
            DatabaseService._close_cached_connection(db_name)
            try:
                conn = pymysql.connect(host=config.MYSQL_HOST, port=config.MYSQL_PORT, user=config.MYSQL_USER, password=config.MYSQL_PASSWORD, database=db_name, charset='utf8mb4')
                with conn.cursor() as cursor:
                    cursor.execute('SHOW TABLES')
                    tables = [list(row.values())[0] for row in cursor.fetchall()]
                    prefix = f'{session_id}_'
                    for table in tables:
                        if table.startswith(prefix):
                            cursor.execute(f'DROP TABLE IF EXISTS `{table}`')
                conn.commit()
                conn.close()
                return True
            except Exception as e:
                logger.error(f'删除 MySQL 会话失败: {e}')
                return False
        else:
            db_path = config.get_db_path(session_id)
            if db_path.exists():
                db_path.unlink()
                return True
            return False

    @staticmethod
    def test_mysql_connection(host, port, user, password, database):
        if not HAS_PYMYSQL:
            return {"success": False, "error_type": "missing_driver", "message": "未安装 pymysql 驱动，请执行: pip install pymysql"}
        try:
            conn = pymysql.connect(host=host, port=port, user=user, password=password, database=database, charset='utf8mb4', connect_timeout=5)
            conn.close()
            return {"success": True}
        except pymysql.err.OperationalError as e:
            code = e.args[0] if e.args else 0
            msg = str(e.args[1]) if len(e.args) > 1 else str(e)
            if code in (2003, 2002):
                return {"success": False, "error_type": "network", "message": f"无法连接到服务器 {host}:{port}，请检查主机地址和端口是否正确，以及 MySQL 服务是否启动"}
            elif code in (1045, 1044):
                return {"success": False, "error_type": "auth", "message": f"认证失败：用户名或密码错误（用户: {user}）"}
            elif code == 1049:
                return {"success": False, "error_type": "database", "message": f"数据库 '{database}' 不存在，请先创建该数据库"}
            elif code == 1129:
                return {"success": False, "error_type": "blocked", "message": "该主机被 MySQL 屏蔽（连接失败次数过多），请稍后再试或联系管理员"}
            else:
                return {"success": False, "error_type": "mysql", "message": f"MySQL 错误 [{code}]: {msg}"}
        except pymysql.err.InternalError as e:
            code = e.args[0] if e.args else 0
            msg = str(e.args[1]) if len(e.args) > 1 else str(e)
            return {"success": False, "error_type": "mysql", "message": f"MySQL 内部错误 [{code}]: {msg}"}
        except Exception as e:
            import socket
            if isinstance(e, (socket.timeout, TimeoutError)):
                return {"success": False, "error_type": "timeout", "message": f"连接超时：无法在5秒内连接到 {host}:{port}，请检查网络或防火墙设置"}
            return {"success": False, "error_type": "unknown", "message": f"未知错误: {type(e).__name__}: {e}"}

    @staticmethod
    def update_db_config(db_type, mysql_host=None, mysql_port=None, mysql_user=None, mysql_password=None, mysql_database=None):
        config.DB_TYPE = db_type
        if mysql_host:
            config.MYSQL_HOST = mysql_host
        if mysql_port:
            config.MYSQL_PORT = mysql_port
        if mysql_user:
            config.MYSQL_USER = mysql_user
        if mysql_password:
            config.MYSQL_PASSWORD = mysql_password
        if mysql_database:
            config.MYSQL_DATABASE = mysql_database

    @staticmethod
    def insert_rows(db_path, table_name, headers, rows_data: List[Dict]):
        """插入多行数据。rows_data 为 [{col: val, ...}, ...]"""
        session_id = DatabaseService._extract_session_id(db_path)
        full_table_name = DatabaseService._get_full_table_name(session_id, table_name)
        cols = [DatabaseService._sanitize_header(h) for h in headers]

        with DatabaseService.get_connection(db_path) as conn:
            cursor = conn.cursor()
            if config.DB_TYPE == 'mysql':
                columns_sql = ', '.join([f'`{c}`' for c in cols])
                placeholders = ', '.join(['%s' for _ in cols])
                sql = f'INSERT INTO `{full_table_name}` ({columns_sql}) VALUES ({placeholders})'
                for row_dict in rows_data:
                    values = [row_dict.get(c, "") for c in cols]
                    cursor.execute(sql, values)
            else:
                columns_sql = ', '.join([f'"{c}"' for c in cols])
                placeholders = ', '.join(['?' for _ in cols])
                sql = f'INSERT INTO "{full_table_name}" ({columns_sql}) VALUES ({placeholders})'
                for row_dict in rows_data:
                    values = [row_dict.get(c, "") for c in cols]
                    cursor.execute(sql, values)
            conn.commit()
            return len(rows_data)

    @staticmethod
    def delete_rows(db_path, table_name, row_ids: List[int]):
        """按 row_id 删除多行"""
        session_id = DatabaseService._extract_session_id(db_path)
        full_table_name = DatabaseService._get_full_table_name(session_id, table_name)

        with DatabaseService.get_connection(db_path) as conn:
            cursor = conn.cursor()
            if config.DB_TYPE == 'mysql':
                placeholders = ', '.join(['%s' for _ in row_ids])
                cursor.execute(
                    f'DELETE FROM `{full_table_name}` WHERE row_id IN ({placeholders})',
                    list(row_ids)
                )
            else:
                placeholders = ', '.join(['?' for _ in row_ids])
                cursor.execute(
                    f'DELETE FROM "{full_table_name}" WHERE row_id IN ({placeholders})',
                    list(row_ids)
                )
            conn.commit()
            return cursor.rowcount

    @staticmethod
    def add_column(db_path, table_name, column_name, default_value=""):
        """新增列（带默认值）"""
        session_id = DatabaseService._extract_session_id(db_path)
        full_table_name = DatabaseService._get_full_table_name(session_id, table_name)
        sanitized = DatabaseService._sanitize_header(column_name)
        if not sanitized:
            raise ValueError("列名不能为空")

        with DatabaseService.get_connection(db_path) as conn:
            cursor = conn.cursor()
            # SQLite 不支持参数化 DEFAULT，需要内联
            safe_default = default_value.replace("'", "''")
            if config.DB_TYPE == 'mysql':
                cursor.execute(
                    f'ALTER TABLE `{full_table_name}` ADD COLUMN `{sanitized}` TEXT DEFAULT %s',
                    [default_value]
                )
            else:
                cursor.execute(
                    f"ALTER TABLE \"{full_table_name}\" ADD COLUMN \"{sanitized}\" TEXT DEFAULT '{safe_default}'"
                )
            conn.commit()
        return sanitized

    @staticmethod
    def drop_column(db_path, table_name, column_name):
        """删除列。SQLite 3.35.0+ 支持 DROP COLUMN，旧版本用重建表回退"""
        session_id = DatabaseService._extract_session_id(db_path)
        full_table_name = DatabaseService._get_full_table_name(session_id, table_name)
        sanitized = DatabaseService._sanitize_header(column_name)

        with DatabaseService.get_connection(db_path) as conn:
            cursor = conn.cursor()
            if config.DB_TYPE == 'mysql':
                cursor.execute(f'ALTER TABLE `{full_table_name}` DROP COLUMN `{sanitized}`')
            else:
                try:
                    cursor.execute(f'ALTER TABLE "{full_table_name}" DROP COLUMN "{sanitized}"')
                except sqlite3.OperationalError as e:
                    if "no such column" in str(e).lower() or "unable to" in str(e).lower():
                        _drop_column_rebuild(conn, full_table_name, sanitized)
                    else:
                        raise
            conn.commit()
        return sanitized

    @staticmethod
    def rename_column(db_path, table_name, old_name, new_name):
        """重命名列"""
        session_id = DatabaseService._extract_session_id(db_path)
        full_table_name = DatabaseService._get_full_table_name(session_id, table_name)
        sanitized_old = DatabaseService._sanitize_header(old_name)
        sanitized_new = DatabaseService._sanitize_header(new_name)
        if not sanitized_new:
            raise ValueError("新列名不能为空")

        with DatabaseService.get_connection(db_path) as conn:
            cursor = conn.cursor()
            if config.DB_TYPE == 'mysql':
                # MySQL 8.0+ 支持 RENAME COLUMN，5.7 需要 CHANGE COLUMN
                try:
                    cursor.execute(
                        f'ALTER TABLE `{full_table_name}` RENAME COLUMN `{sanitized_old}` TO `{sanitized_new}`'
                    )
                except Exception:
                    cursor.execute(
                        f'ALTER TABLE `{full_table_name}` CHANGE COLUMN `{sanitized_old}` `{sanitized_new}` TEXT'
                    )
            else:
                cursor.execute(
                    f'ALTER TABLE "{full_table_name}" RENAME COLUMN "{sanitized_old}" TO "{sanitized_new}"'
                )
            conn.commit()
        return sanitized_new

    @staticmethod
    def regex_replace(db_path, table_name, column, pattern, replacement):
        """正则替换。SQLite 不原生支持 REGEXP REPLACE，用 Python 逐行处理"""
        import re
        session_id = DatabaseService._extract_session_id(db_path)
        full_table_name = DatabaseService._get_full_table_name(session_id, table_name)

        compiled = re.compile(pattern)
        with DatabaseService.get_connection(db_path) as conn:
            cursor = conn.cursor()
            if config.DB_TYPE == 'mysql':
                cursor.execute(f'SELECT row_id, `{column}` FROM `{full_table_name}`')
            else:
                cursor.execute(f'SELECT row_id, "{column}" FROM "{full_table_name}"')
            rows = cursor.fetchall()

            updated = 0
            for row in rows:
                rid = row[0]
                val = row[1]
                if val is None:
                    continue
                str_val = str(val)
                new_val = compiled.sub(replacement, str_val)
                if new_val != str_val:
                    if config.DB_TYPE == 'mysql':
                        cursor.execute(
                            f'UPDATE `{full_table_name}` SET `{column}` = %s WHERE row_id = %s',
                            [new_val, rid]
                        )
                    else:
                        cursor.execute(
                            f'UPDATE "{full_table_name}" SET "{column}" = ? WHERE row_id = ?',
                            [new_val, rid]
                        )
                    updated += 1
            conn.commit()
        return updated

    @staticmethod
    def split_column(db_path, table_name, source_column, delimiter, max_splits=0):
        """按分隔符拆列。新增 column_1, column_2, ... 列"""
        session_id = DatabaseService._extract_session_id(db_path)
        full_table_name = DatabaseService._get_full_table_name(session_id, table_name)

        with DatabaseService.get_connection(db_path) as conn:
            cursor = conn.cursor()
            # 先取所有值，确定拆分后最大列数
            if config.DB_TYPE == 'mysql':
                cursor.execute(f'SELECT `{source_column}` FROM `{full_table_name}`')
            else:
                cursor.execute(f'SELECT "{source_column}" FROM "{full_table_name}"')
            all_vals = [r[0] for r in cursor.fetchall()]

            max_parts = 0
            for v in all_vals:
                if v:
                    parts = v.split(delimiter, max_splits) if max_splits > 0 else v.split(delimiter)
                    max_parts = max(max_parts, len(parts))

            if max_parts <= 1:
                return {"newColumns": [], "affectedRows": 0}

            # 新增列
            new_col_names = []
            for i in range(1, max_parts + 1):
                col_name = f"{source_column}_{i}"
                new_col_names.append(col_name)
                if config.DB_TYPE == 'mysql':
                    cursor.execute(
                        f'ALTER TABLE `{full_table_name}` ADD COLUMN `{col_name}` TEXT DEFAULT \'\''
                    )
                else:
                    cursor.execute(
                        f'ALTER TABLE "{full_table_name}" ADD COLUMN "{col_name}" TEXT DEFAULT \'\''
                    )

            # 回填数据
            if config.DB_TYPE == 'mysql':
                cursor.execute(f'SELECT row_id, `{source_column}` FROM `{full_table_name}`')
            else:
                cursor.execute(f'SELECT row_id, "{source_column}" FROM "{full_table_name}"')
            rows = cursor.fetchall()

            updated = 0
            for row in rows:
                rid = row[0]
                val = row[1]
                if not val:
                    continue
                parts = val.split(delimiter, max_splits) if max_splits > 0 else val.split(delimiter)
                set_clauses = []
                params = []
                for i, col_name in enumerate(new_col_names):
                    v = parts[i].strip() if i < len(parts) else ""
                    if config.DB_TYPE == 'mysql':
                        set_clauses.append(f'`{col_name}` = %s')
                    else:
                        set_clauses.append(f'"{col_name}" = ?')
                    params.append(v)
                params.append(rid)
                if config.DB_TYPE == 'mysql':
                    cursor.execute(
                        f'UPDATE `{full_table_name}` SET {", ".join(set_clauses)} WHERE row_id = %s',
                        params
                    )
                else:
                    cursor.execute(
                        f'UPDATE "{full_table_name}" SET {", ".join(set_clauses)} WHERE row_id = ?',
                        params
                    )
                updated += 1

            conn.commit()
        return {"newColumns": new_col_names, "affectedRows": updated}

    @staticmethod
    def convert_type(db_path, table_name, column, target_type, date_format=""):
        """数据类型转换（逐行处理）"""
        session_id = DatabaseService._extract_session_id(db_path)
        full_table_name = DatabaseService._get_full_table_name(session_id, table_name)

        with DatabaseService.get_connection(db_path) as conn:
            cursor = conn.cursor()
            if config.DB_TYPE == 'mysql':
                cursor.execute(
                    f'SELECT row_id, `{column}` FROM `{full_table_name}` '
                    f'WHERE `{column}` IS NOT NULL AND `{column}` != ""'
                )
            else:
                cursor.execute(
                    f'SELECT row_id, "{column}" FROM "{full_table_name}" '
                    f'WHERE "{column}" IS NOT NULL AND "{column}" != ""'
                )
            rows = cursor.fetchall()

            updated = 0
            errors = []
            for row in rows:
                rid = row[0]
                val = row[1]
                try:
                    if target_type == "number":
                        new_val = str(float(val))
                    elif target_type == "date":
                        from datetime import datetime
                        parsed = None
                        for fmt in ["%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y", "%Y%m%d",
                                    "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"]:
                            try:
                                parsed = datetime.strptime(str(val).strip(), fmt)
                                break
                            except ValueError:
                                continue
                        if not parsed:
                            errors.append(f"行 {rid}: 无法解析日期 '{val}'")
                            continue
                        out_fmt = date_format or "%Y-%m-%d"
                        new_val = parsed.strftime(out_fmt)
                    elif target_type == "string":
                        new_val = str(val)
                    else:
                        continue

                    if config.DB_TYPE == 'mysql':
                        cursor.execute(
                            f'UPDATE `{full_table_name}` SET `{column}` = %s WHERE row_id = %s',
                            [new_val, rid]
                        )
                    else:
                        cursor.execute(
                            f'UPDATE "{full_table_name}" SET "{column}" = ? WHERE row_id = ?',
                            [new_val, rid]
                        )
                    updated += 1
                except (ValueError, TypeError) as e:
                    errors.append(f"行 {rid}: {str(e)}")

            conn.commit()
        return {"updated": updated, "errors": errors[:20]}


def _drop_column_rebuild(conn, table_name, column_name):
    """SQLite 旧版本不支持 DROP COLUMN 时的回退方案：重建表"""
    cursor = conn.cursor()
    cursor.execute(f'PRAGMA table_info("{table_name}")')
    all_cols = [r[1] for r in cursor.fetchall()]
    keep_cols = [c for c in all_cols if c != column_name]

    if len(keep_cols) == len(all_cols):
        return  # 列不存在，无需操作

    tmp_name = f"{table_name}_tmp_drop_col"
    cols_sql = ', '.join([f'"{c}"' for c in keep_cols])
    data_cols = [c for c in keep_cols if c != "row_id"]

    cursor.execute(f'ALTER TABLE "{table_name}" RENAME TO "{tmp_name}"')
    data_cols_def = ', '.join([f'"{c}" TEXT' for c in data_cols])
    cursor.execute(f'''
        CREATE TABLE "{table_name}" (
            row_id INTEGER PRIMARY KEY AUTOINCREMENT,
            {data_cols_def}
        )
    ''')
    cursor.execute(f'INSERT INTO "{table_name}" ({cols_sql}) SELECT {cols_sql} FROM "{tmp_name}"')
    cursor.execute(f'DROP TABLE "{tmp_name}"')
