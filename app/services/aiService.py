from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
import json
import logging

logger = logging.getLogger(__name__)


def strip_markdown_code_block(content: str) -> str:
    """去除 LLM 返回内容中的 markdown 代码块包裹"""
    content = content.strip()
    if content.startswith("```json"):
        content = content[7:]
    elif content.startswith("```"):
        content = content[3:]
    if content.endswith("```"):
        content = content[:-3]
    return content.strip()


def humanize_ai_error(e: Exception) -> str:
    """把 AI 调用相关的异常翻译成普通办公人员能看懂的中文提示"""
    import urllib.error
    import socket
    import json as _json

    # HTTP 错误（带状态码，最常见）
    if isinstance(e, urllib.error.HTTPError):
        code = e.code
        if code == 401:
            return "API Key 无效或已过期，请到右上角设置中重新填写"
        if code == 403:
            return "API Key 没有访问权限，请检查账号或更换 Key"
        if code == 404:
            return "请求的模型或地址不存在，请检查模型名称和 Base URL"
        if code == 429:
            return "请求过于频繁或额度已用完，请稍后再试"
        if code >= 500:
            return f"AI 服务暂时不可用（{code}），请稍后重试"
        return f"AI 服务返回错误（{code}），请检查配置"
    # 连接错误（含超时）
    if isinstance(e, urllib.error.URLError):
        reason = e.reason
        if isinstance(reason, socket.timeout) or "timed out" in str(reason).lower():
            return "连接 AI 服务超时，请检查网络后重试"
        return "无法连接到 AI 服务，请检查网络或 Base URL 是否正确"
    if isinstance(e, (socket.timeout, TimeoutError)):
        return "连接 AI 服务超时，请检查网络后重试"
    # AI 返回内容不是合法 JSON
    if isinstance(e, _json.JSONDecodeError):
        return "AI 返回内容格式异常，请重新提问或换个问法"
    # 兜底：返回原始信息，但去掉可能包含的 Key 等敏感前缀
    msg = str(e).strip()
    if not msg:
        return "AI 处理失败，请重试"
    return msg


@dataclass
class AIConfig:
    """AI 配置"""
    provider: str = "deepseek"
    apiKey: str = ""
    baseUrl: str = ""
    model: str = ""


class AIProvider:
    """AI Provider 基类"""

    def __init__(self, config: AIConfig):
        self.config = config

    def chat(self, messages: List[Dict[str, str]], temperature: float = 0.3) -> str:
        """发送聊天请求，返回文本内容"""
        raise NotImplementedError


class DeepSeekProvider(AIProvider):
    """DeepSeek Provider"""

    def chat(self, messages, temperature=0.3):
        import urllib.request
        base = self.config.baseUrl or "https://api.deepseek.com"
        if base.endswith("/"):
            base = base[:-1]
        url = f"{base}/v1/chat/completions"
        data = json.dumps({
            "model": self.config.model or "deepseek-chat",
            "messages": messages,
            "temperature": temperature
        }).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {self.config.apiKey}")
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"]


class OpenAIProvider(AIProvider):
    """OpenAI / 兼容 OpenAI 协议的 Provider"""

    def chat(self, messages, temperature=0.3):
        import urllib.request
        url = self.config.baseUrl or "https://api.openai.com/v1/chat/completions"
        data = json.dumps({
            "model": self.config.model or "gpt-4o-mini",
            "messages": messages,
            "temperature": temperature
        }).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {self.config.apiKey}")
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"]


class OllamaProvider(AIProvider):
    """Ollama 本地 Provider"""

    def chat(self, messages, temperature=0.3):
        import urllib.request
        base = self.config.baseUrl or "http://localhost:11434"
        url = f"{base}/api/chat"
        data = json.dumps({
            "model": self.config.model or "qwen2.5:7b",
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature}
        }).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result["message"]["content"]


class QwenProvider(AIProvider):
    """通义千问 Provider"""

    def chat(self, messages, temperature=0.3):
        import urllib.request
        url = self.config.baseUrl or "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
        data = json.dumps({
            "model": self.config.model or "qwen-plus",
            "messages": messages,
            "temperature": temperature
        }).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {self.config.apiKey}")
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"]


PROVIDER_MAP = {
    "deepseek": DeepSeekProvider,
    "openai": OpenAIProvider,
    "ollama": OllamaProvider,
    "qwen": QwenProvider,
    "通义": QwenProvider,
}


def create_provider(config: AIConfig) -> AIProvider:
    """根据配置创建 Provider"""
    provider_cls = PROVIDER_MAP.get(config.provider.lower())
    if not provider_cls:
        provider_cls = OpenAIProvider
    return provider_cls(config)


class NL2SQLService:
    """自然语言转 SQL 服务"""

    @staticmethod
    def generate_sql(
        provider: AIProvider,
        tables: List[Dict[str, Any]],
        question: str,
        history: List[Dict[str, str]] = None,
        db_type: str = "sqlite"
    ) -> Dict[str, Any]:
        """根据自然语言问题生成 SQL（支持多表 + 多轮对话）"""
        table_desc = ""
        sample_desc = ""

        for table in tables:
            table_name = table.get("name", "")
            columns = table.get("columns", [])
            sample_data = table.get("sample_data", [])

            table_desc += f"\n表名：{table_name}\n列名：\n"
            table_desc += "\n".join([f"- {col}" for col in columns])

            if sample_data:
                sample_desc += f"\n\n{table_name} 示例数据（前3行）：\n"
                for row in sample_data[:3]:
                    sample_desc += f"- {row}\n"

        # 根据数据库类型选择标识符引用符和类型转换语法
        if db_type == "mysql":
            quote_char = "`"
            cast_expr = "CAST(`列名` AS DECIMAL)"
            db_name = "MySQL"
        else:
            quote_char = '"'
            cast_expr = 'CAST("列名" AS REAL)'
            db_name = "SQLite"

        # System message 包含表结构和规则
        system_content = f"""你是一个 SQL 专家。请根据用户的问题，生成 {db_name} 查询语句。

可用表：
{table_desc}{sample_desc}

规则：
1. 只能 SELECT 查询，不能修改数据
2. 列名和表名必须用 {quote_char} 包裹，如 {quote_char}列名{quote_char}
3. 数值比较时用 {cast_expr} 转换
4. 字符串用单引号
5. 如果需要查询多个表，请使用 JOIN 或 UNION ALL
6. 只返回 JSON，不要 markdown 代码块
7. 返回格式必须是 {{"sql": "...", "isQuery": true, "explanation": "..."}}"""

        messages = [{"role": "system", "content": system_content}]

        # 加入历史对话（最近 20 条，避免 token 过长）
        if history:
            for msg in history[-20:]:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": content})

        # 当前用户问题
        messages.append({"role": "user", "content": question})

        try:
            content = provider.chat(messages)
            content = strip_markdown_code_block(content)
            result = json.loads(content)
            return {
                "success": True,
                "sql": result.get("sql", ""),
                "isQuery": result.get("isQuery", True),
                "explanation": result.get("explanation", "")
            }
        except Exception as e:
            logger.error(f"NL2SQL 生成失败: {e}")
            return {"success": False, "error": humanize_ai_error(e), "sql": ""}


class SmartChartService:
    """智能图表生成服务"""

    @staticmethod
    def suggest_chart(
        provider: AIProvider,
        columns: List[str],
        sample_data: List[List[str]],
        user_request: str = ""
    ) -> Dict[str, Any]:
        """根据数据智能推荐图表类型"""
        column_desc = "\n".join([f"- {col}" for col in columns])
        sample_desc = ""
        if sample_data:
            sample_desc = "\n\n示例数据（前3行）：\n"
            for row in sample_data[:3]:
                sample_desc += f"- {row}\n"

        req_desc = user_request if user_request else "请根据数据特点推荐最合适的图表"

        prompt = f"""你是一个数据可视化专家。请根据数据特点推荐最合适的 ECharts 图表。

列名：
{column_desc}{sample_desc}

用户需求：{req_desc}

请直接返回 JSON 格式：
{{
  "chartType": "bar",
  "title": "图表标题",
  "xAxis": "x轴列名",
  "yAxis": "y轴列名",
  "seriesType": "bar",
  "explanation": "为什么推荐这个图表"
}}

支持的 chartType：bar, line, pie, scatter

只返回 JSON，不要 markdown 代码块。
"""
        messages = [{"role": "user", "content": prompt}]
        try:
            content = provider.chat(messages)
            content = strip_markdown_code_block(content)
            result = json.loads(content)
            return {"success": True, **result}
        except Exception as e:
            logger.error(f"智能图表推荐失败: {e}")
            return {"success": False, "error": humanize_ai_error(e)}
