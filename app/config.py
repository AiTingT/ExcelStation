from pathlib import Path
from typing import Optional
import os
import sys


def get_app_dir() -> Path:
    """获取应用根目录，适配 PyInstaller 打包后的环境"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def get_resource_dir(subdir: str) -> Path:
    """获取资源目录，优先从 exe 同级目录找，再找内部打包目录"""
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
        external = base / subdir
        if external.exists():
            return external
        if hasattr(sys, "_MEIPASS"):
            internal = Path(sys._MEIPASS) / subdir
            if internal.exists():
                return internal
        return base
    return Path(__file__).resolve().parent.parent / subdir


class Config:
    """应用配置"""

    BASE_DIR: Path = get_app_dir()
    STATIC_DIR: Path = get_resource_dir("static")

    # 共享资源目录
    SHARED_DIR: Path = get_resource_dir("_shared")

    # 数据目录（始终在 exe 同级目录，方便用户管理数据）
    DATA_DIR: Path = BASE_DIR / "data"
    DATA_DIR.mkdir(exist_ok=True)

    # 数据库路径
    DB_PATH: Path = DATA_DIR / "workspace.db"

    # 上传配置
    UPLOAD_DIR: Path = DATA_DIR / "uploads"
    UPLOAD_DIR.mkdir(exist_ok=True)

    MAX_FILE_SIZE: int = 500 * 1024 * 1024  # 500MB

    # 分页配置
    DEFAULT_PAGE_SIZE: int = 100
    MAX_PAGE_SIZE: int = 1000

    # 支持的文件扩展名
    ALLOWED_EXTENSIONS: set = {".xlsx", ".xls", ".csv"}

    # 数据库类型配置
    DB_TYPE: str = os.environ.get("DB_TYPE", "sqlite")  # "sqlite" 或 "mysql"

    # MySQL 配置
    MYSQL_HOST: str = os.environ.get("MYSQL_HOST", "127.0.0.1")
    MYSQL_PORT: int = int(os.environ.get("MYSQL_PORT", "3306"))
    MYSQL_USER: str = os.environ.get("MYSQL_USER", "root")
    MYSQL_PASSWORD: str = os.environ.get("MYSQL_PASSWORD", "")
    MYSQL_DATABASE: str = os.environ.get("MYSQL_DATABASE", "excel_station")

    @classmethod
    def get_db_path(cls, session_id: str) -> Path:
        """获取指定会话的数据库路径"""
        session_db_dir = cls.DATA_DIR / "sessions"
        session_db_dir.mkdir(exist_ok=True)
        return session_db_dir / f"{session_id}.db"


config = Config()
