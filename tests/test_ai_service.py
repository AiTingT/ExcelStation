"""P0: 纯函数测试 — AI 服务相关"""
from app.services.aiService import (
    create_provider, AIConfig, NL2SQLService, SmartChartService,
)


class TestCreateProvider:
    """测试 Provider 工厂函数"""

    def test_deepseek(self):
        cfg = AIConfig(provider="deepseek", apiKey="test-key")
        p = create_provider(cfg)
        assert p.__class__.__name__ == "DeepSeekProvider"

    def test_openai(self):
        cfg = AIConfig(provider="openai", apiKey="test-key")
        p = create_provider(cfg)
        assert p.__class__.__name__ == "OpenAIProvider"

    def test_ollama(self):
        cfg = AIConfig(provider="ollama")
        p = create_provider(cfg)
        assert p.__class__.__name__ == "OllamaProvider"

    def test_qwen(self):
        cfg = AIConfig(provider="qwen", apiKey="key")
        p = create_provider(cfg)
        assert p.__class__.__name__ == "QwenProvider"

    def test_unknown_fallback_to_openai(self):
        cfg = AIConfig(provider="unknown_provider", apiKey="key")
        p = create_provider(cfg)
        assert p.__class__.__name__ == "OpenAIProvider"


class TestNL2SQLGenerateSQL:
    """用 MockProvider 测试 NL2SQL 的 prompt 拼接和 JSON 解析"""

    def test_success(self):
        class MockProvider:
            def chat(self, messages, temperature=0.3):
                return '{"sql": "SELECT * FROM test", "isQuery": true, "explanation": "查询全部"}'

        tables = [{"name": "test", "columns": ["id", "name"], "sample_data": [["1", "Alice"]]}]
        result = NL2SQLService.generate_sql(MockProvider(), tables, "查询所有")
        assert result["success"] is True
        assert result["sql"] == "SELECT * FROM test"
        assert result["explanation"] == "查询全部"

    def test_with_markdown_code_block(self):
        class MockProvider:
            def chat(self, messages, temperature=0.3):
                return '```json\n{"sql": "SELECT 1", "isQuery": true, "explanation": "test"}\n```'

        tables = [{"name": "test", "columns": [], "sample_data": []}]
        result = NL2SQLService.generate_sql(MockProvider(), tables, "test")
        assert result["success"] is True
        assert result["sql"] == "SELECT 1"

    def test_parse_error(self):
        class MockProvider:
            def chat(self, messages, temperature=0.3):
                return "这不是 JSON"

        tables = [{"name": "test", "columns": [], "sample_data": []}]
        result = NL2SQLService.generate_sql(MockProvider(), tables, "test")
        assert result["success"] is False

    def test_with_history(self):
        """测试多轮对话历史被传入 messages"""
        received_messages = []

        class MockProvider:
            def chat(self, messages, temperature=0.3):
                received_messages.extend(messages)
                return '{"sql": "SELECT * FROM test LIMIT 10", "isQuery": true, "explanation": "limit"}'

        tables = [{"name": "test", "columns": ["id"], "sample_data": []}]
        history = [{"role": "user", "content": "查全部"}, {"role": "assistant", "content": "好的"}]
        result = NL2SQLService.generate_sql(MockProvider(), tables, "只看前10条", history)
        assert result["success"] is True
        # 验证 messages 包含 history
        roles = [m["role"] for m in received_messages]
        assert "user" in roles
        assert "assistant" in roles


class TestSmartChartSuggest:
    def test_success(self):
        class MockProvider:
            def chat(self, messages, temperature=0.3):
                return '{"chartType": "bar", "title": "Test", "xAxis": "A", "yAxis": "B", "seriesType": "bar", "explanation": "ok"}'

        result = SmartChartService.suggest_chart(MockProvider(), ["A", "B"], [["1", "2"]])
        assert result["success"] is True
        assert result["chartType"] == "bar"

    def test_parse_error(self):
        class MockProvider:
            def chat(self, messages, temperature=0.3):
                return "invalid"

        result = SmartChartService.suggest_chart(MockProvider(), ["A"], [["1"]])
        assert result["success"] is False
