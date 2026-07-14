"""P2: Router 集成测试"""
import sqlite3
from fastapi.testclient import TestClient


class TestSanitizeTableName:
    def test_spaces(self):
        from app.routers.data import sanitize_table_name
        assert sanitize_table_name("Sheet 1") == "Sheet_1"

    def test_dashes(self):
        from app.routers.data import sanitize_table_name
        assert sanitize_table_name("data-2024") == "data_2024"

    def test_normal(self):
        from app.routers.data import sanitize_table_name
        assert sanitize_table_name("data") == "data"


class TestAIRouter:
    def test_get_config(self):
        from app.main import app
        with TestClient(app) as client:
            resp = client.get("/api/ai/config")
            assert resp.status_code == 200
            data = resp.json()
            assert "provider" in data
            # apiKey 应该是脱敏的或为空
            if data["apiKey"]:
                assert "..." in data["apiKey"] or data["apiKey"] == ""

    def test_update_config_preserves_masked_key(self):
        """验证保存脱敏 apiKey 时保留原值"""
        from app.main import app
        from app.routers.ai import load_ai_config, save_ai_config
        from app.services.aiService import AIConfig

        # 先保存一个真实 key
        save_ai_config(AIConfig(provider="deepseek", apiKey="sk-real-key-12345",
                                baseUrl="", model="deepseek-chat"))

        with TestClient(app) as client:
            # 用脱敏值保存（模拟前端行为）
            resp = client.post("/api/ai/config", json={
                "provider": "deepseek", "apiKey": "sk-real...",
                "baseUrl": "", "model": "deepseek-chat"
            })
            assert resp.status_code == 200

            # 内部应该还是完整的 key
            cfg = load_ai_config()
            assert cfg.apiKey == "sk-real-key-12345"

    def test_update_config_preserves_empty_key(self):
        """验证保存空 apiKey 时保留原值"""
        from app.main import app
        from app.routers.ai import load_ai_config, save_ai_config
        from app.services.aiService import AIConfig

        save_ai_config(AIConfig(provider="deepseek", apiKey="sk-real-key-99999",
                                baseUrl="", model="deepseek-chat"))

        with TestClient(app) as client:
            resp = client.post("/api/ai/config", json={
                "provider": "deepseek", "apiKey": "",
                "baseUrl": "", "model": "deepseek-chat"
            })
            assert resp.status_code == 200
            cfg = load_ai_config()
            assert cfg.apiKey == "sk-real-key-99999"

    def test_update_config_allows_new_key(self):
        """验证传入新 apiKey 时正常替换"""
        from app.main import app
        from app.routers.ai import load_ai_config, save_ai_config
        from app.services.aiService import AIConfig

        save_ai_config(AIConfig(provider="deepseek", apiKey="old-key",
                                baseUrl="", model="deepseek-chat"))

        with TestClient(app) as client:
            resp = client.post("/api/ai/config", json={
                "provider": "deepseek", "apiKey": "brand-new-key",
                "baseUrl": "", "model": "deepseek-chat"
            })
            assert resp.status_code == 200
            cfg = load_ai_config()
            assert cfg.apiKey == "brand-new-key"


class TestCompareRouter:
    def _create_order_db(self, db_path, rows):
        conn = sqlite3.connect(str(db_path))
        conn.execute('''CREATE TABLE "orders" (
            row_id INTEGER PRIMARY KEY AUTOINCREMENT,
            "订单编号" TEXT,
            "期望申报日期" TEXT,
            "状态" TEXT
        )''')
        conn.executemany(
            'INSERT INTO "orders" ("订单编号", "期望申报日期", "状态") VALUES (?, ?, ?)',
            rows
        )
        conn.commit()
        conn.close()

    def test_compare_returns_selected_column_changes_and_presence_rows(self, tmp_path, monkeypatch):
        from app.main import app
        from app.routers import data as data_router

        db_a = tmp_path / "a.db"
        db_b = tmp_path / "b.db"
        self._create_order_db(db_a, [
            ("O001", "2026-06-01", "待审核"),
            ("O002", "2026-06-02", "待审核"),
        ])
        self._create_order_db(db_b, [
            ("O001", "2026-06-03", "已审核"),
            ("O003", "2026-06-04", "新增"),
        ])

        monkeypatch.setattr(data_router.config, "DB_TYPE", "sqlite")
        monkeypatch.setattr(
            data_router,
            "get_db_path",
            lambda session_id: db_a if session_id == "A" else (db_b if session_id == "B" else None)
        )

        with TestClient(app) as client:
            resp = client.post("/api/data/compare", json={
                "sessionA": "A",
                "tableA": "orders",
                "sessionB": "B",
                "tableB": "orders",
                "keyColumn": "订单编号",
                "compareColumns": ["期望申报日期"]
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["headersA"] == ["订单编号", "期望申报日期", "状态"]
        assert data["headersB"] == ["订单编号", "期望申报日期", "状态"]
        assert data["compareColumns"] == ["期望申报日期"]
        assert data["stats"]["onlyA"] == 1
        assert data["stats"]["onlyB"] == 1
        assert data["stats"]["both"] == 1
        assert data["stats"]["changed"] == 1
        assert data["stats"]["changedCells"] == 1
        assert data["onlyARows"][0]["订单编号"] == "O002"
        assert data["onlyBRows"][0]["订单编号"] == "O003"
        assert data["changedCells"] == [{
            "key": "O001",
            "column": "期望申报日期",
            "a": "2026-06-01",
            "b": "2026-06-03",
        }]
        assert data["changedRows"][0]["changes"] == {
            "期望申报日期": {"a": "2026-06-01", "b": "2026-06-03"}
        }
