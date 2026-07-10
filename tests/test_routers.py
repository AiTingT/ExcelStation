"""P2: Router 集成测试"""
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
