"""P1: DatabaseService 集成测试（使用临时 SQLite）"""
import pytest
from unittest.mock import patch
from app.services.database import DatabaseService


class TestDatabaseServiceWithSQLite:

    @patch("app.services.database.config")
    def test_get_headers(self, mock_config, temp_db):
        mock_config.DB_TYPE = "sqlite"
        headers = DatabaseService.get_headers(str(temp_db), "test_data")
        assert headers == ["name", "age", "email"]

    @patch("app.services.database.config")
    def test_query_data(self, mock_config, temp_db):
        mock_config.DB_TYPE = "sqlite"
        rows, total = DatabaseService.query_data(
            str(temp_db), "test_data", headers=["name", "age", "email"],
            page=1, page_size=10
        )
        assert total == 3
        assert len(rows) == 3

    @patch("app.services.database.config")
    def test_update_row(self, mock_config, temp_db):
        mock_config.DB_TYPE = "sqlite"
        affected = DatabaseService.update_row(str(temp_db), "test_data", 1, {"name": "Alice2"})
        assert affected == 1
        # 验证更新后的值
        rows, _ = DatabaseService.query_data(
            str(temp_db), "test_data", headers=["name", "age", "email"],
            page=1, page_size=10
        )
        # rows[0] 是第一行，[1] 是 name 列（[0] 是 row_id）
        assert rows[0][1] == "Alice2"

    @patch("app.services.database.config")
    def test_insert_rows(self, mock_config, temp_db):
        mock_config.DB_TYPE = "sqlite"
        headers = ["name", "age", "email"]
        count = DatabaseService.insert_rows(
            str(temp_db), "test_data", headers,
            [{"name": "Dave", "age": "40", "email": "d@e.com"}]
        )
        assert count == 1
        rows, total = DatabaseService.query_data(
            str(temp_db), "test_data", headers=headers, page=1, page_size=10
        )
        assert total == 4

    @patch("app.services.database.config")
    def test_delete_rows(self, mock_config, temp_db):
        mock_config.DB_TYPE = "sqlite"
        count = DatabaseService.delete_rows(str(temp_db), "test_data", [1])
        assert count == 1
        rows, total = DatabaseService.query_data(
            str(temp_db), "test_data", headers=["name", "age", "email"],
            page=1, page_size=10
        )
        assert total == 2

    @patch("app.services.database.config")
    def test_add_column(self, mock_config, temp_db):
        mock_config.DB_TYPE = "sqlite"
        col = DatabaseService.add_column(str(temp_db), "test_data", "phone", "N/A")
        assert col == "phone"
        headers = DatabaseService.get_headers(str(temp_db), "test_data")
        assert "phone" in headers

    @patch("app.services.database.config")
    def test_drop_column(self, mock_config, temp_db):
        mock_config.DB_TYPE = "sqlite"
        DatabaseService.drop_column(str(temp_db), "test_data", "email")
        headers = DatabaseService.get_headers(str(temp_db), "test_data")
        assert "email" not in headers

    @patch("app.services.database.config")
    def test_rename_column(self, mock_config, temp_db):
        mock_config.DB_TYPE = "sqlite"
        DatabaseService.rename_column(str(temp_db), "test_data", "name", "full_name")
        headers = DatabaseService.get_headers(str(temp_db), "test_data")
        assert "full_name" in headers
        assert "name" not in headers


class TestSanitizeHeader:
    def test_normal(self):
        assert DatabaseService._sanitize_header("hello") == "hello"

    def test_quotes(self):
        assert DatabaseService._sanitize_header('"hello"') == "hello"

    def test_single_quotes(self):
        assert DatabaseService._sanitize_header("'hello'") == "hello"

    def test_empty(self):
        assert DatabaseService._sanitize_header("") == ""

    def test_control_chars(self):
        result = DatabaseService._sanitize_header("hello\x00world")
        assert "\x00" not in result

    def test_mixed_quotes(self):
        assert DatabaseService._sanitize_header('`"hello"`') == "hello"
