"""数据库配置路由"""
from fastapi import APIRouter
from pydantic import BaseModel
import json
import logging

from app.services.database import DatabaseService
from app.config import config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/db", tags=["database"])

_DB_CONFIG_FILENAME = "db_config.json"


class DBConfig(BaseModel):
    db_type: str = "sqlite"
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = ""
    mysql_database: str = "excel_station"


def _db_config_path():
    return config.DATA_DIR / _DB_CONFIG_FILENAME


def load_db_config() -> dict:
    """从 data/db_config.json 加载持久化的数据库配置并应用到 config 对象。

    优先级：持久化文件 > 环境变量 > 默认值。
    文件不存在或解析失败时，静默回退到当前 config（由环境变量/默认值初始化）。
    """
    path = _db_config_path()
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        # 只接受已知字段，防止脏数据污染 config
        allowed = {"db_type", "mysql_host", "mysql_port", "mysql_user",
                   "mysql_password", "mysql_database"}
        filtered = {k: v for k, v in data.items() if k in allowed}

        if "db_type" in filtered and filtered["db_type"] in ("sqlite", "mysql"):
            config.DB_TYPE = filtered["db_type"]
        if "mysql_host" in filtered and isinstance(filtered["mysql_host"], str):
            config.MYSQL_HOST = filtered["mysql_host"]
        if "mysql_port" in filtered:
            try:
                config.MYSQL_PORT = int(filtered["mysql_port"])
            except (TypeError, ValueError):
                pass
        if "mysql_user" in filtered and isinstance(filtered["mysql_user"], str):
            config.MYSQL_USER = filtered["mysql_user"]
        if "mysql_password" in filtered and isinstance(filtered["mysql_password"], str):
            config.MYSQL_PASSWORD = filtered["mysql_password"]
        if "mysql_database" in filtered and isinstance(filtered["mysql_database"], str):
            config.MYSQL_DATABASE = filtered["mysql_database"]

        logger.info(f"已加载持久化数据库配置: db_type={config.DB_TYPE}, "
                    f"mysql_host={config.MYSQL_HOST}, mysql_database={config.MYSQL_DATABASE}")
        return filtered
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"加载 db_config.json 失败: {e}")
        return {}


def save_db_config() -> None:
    """将当前内存中的数据库配置持久化到 data/db_config.json。"""
    path = _db_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "db_type": config.DB_TYPE,
        "mysql_host": config.MYSQL_HOST,
        "mysql_port": config.MYSQL_PORT,
        "mysql_user": config.MYSQL_USER,
        "mysql_password": config.MYSQL_PASSWORD,
        "mysql_database": config.MYSQL_DATABASE,
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"数据库配置已持久化到 {path}")
    except OSError as e:
        logger.error(f"保存 db_config.json 失败: {e}")


@router.get("/config")
async def get_db_config():
    """获取当前数据库配置"""
    return {
        "db_type": config.DB_TYPE,
        "mysql_host": config.MYSQL_HOST,
        "mysql_port": config.MYSQL_PORT,
        "mysql_user": config.MYSQL_USER,
        "mysql_password": config.MYSQL_PASSWORD,
        "mysql_database": config.MYSQL_DATABASE
    }


@router.post("/config")
async def update_db_config(config_data: DBConfig):
    """更新数据库配置，并持久化到 data/db_config.json"""
    DatabaseService.update_db_config(
        db_type=config_data.db_type,
        mysql_host=config_data.mysql_host,
        mysql_port=config_data.mysql_port,
        mysql_user=config_data.mysql_user,
        mysql_password=config_data.mysql_password,
        mysql_database=config_data.mysql_database
    )
    save_db_config()
    return {"success": True, "message": "配置已更新并保存"}


@router.post("/test-mysql")
async def test_mysql_connection(config_data: DBConfig):
    """测试 MySQL 连接，返回详细错误信息"""
    result = DatabaseService.test_mysql_connection(
        host=config_data.mysql_host,
        port=config_data.mysql_port,
        user=config_data.mysql_user,
        password=config_data.mysql_password,
        database=config_data.mysql_database
    )
    return result
