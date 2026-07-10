import pytest
import tempfile
import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock


@pytest.fixture
def temp_db():
    """创建临时 SQLite 数据库，包含 test_data 表和 3 行样本数据"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    conn = sqlite3.connect(str(db_path))
    conn.execute('''CREATE TABLE "test_data" (
        row_id INTEGER PRIMARY KEY AUTOINCREMENT,
        "name" TEXT,
        "age" TEXT,
        "email" TEXT
    )''')
    conn.execute('''INSERT INTO "test_data" ("name", "age", "email") VALUES
        ('Alice', '30', 'alice@example.com'),
        ('Bob', '25', 'bob@test.com'),
        ('Charlie', '35', '')
    ''')
    conn.commit()
    conn.close()

    yield db_path

    db_path.unlink(missing_ok=True)


@pytest.fixture
def mock_config():
    """Mock config 为 SQLite 模式"""
    with patch("app.services.database.config") as mock:
        mock.DB_TYPE = "sqlite"
        yield mock
