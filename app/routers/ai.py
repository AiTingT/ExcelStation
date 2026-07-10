from fastapi import APIRouter, HTTPException, Body
from typing import Optional
from pydantic import BaseModel
import json
from pathlib import Path

from app.config import config
from app.services.aiService import AIConfig, create_provider, NL2SQLService, SmartChartService
from app.models.schemas import NL2SQLRequest

router = APIRouter(prefix="/api/ai", tags=["AI 功能"])


class AIConfigUpdate(BaseModel):
    provider: str = "deepseek"
    apiKey: str = ""
    baseUrl: str = ""
    model: str = ""


_config_cache: Optional[AIConfig] = None


def load_ai_config() -> AIConfig:
    """加载 AI 配置"""
    global _config_cache
    if _config_cache:
        return _config_cache
    config_path = config.DATA_DIR / "ai_config.json"
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            _config_cache = AIConfig(**data)
            return _config_cache
        except Exception:
            pass
    _config_cache = AIConfig()
    return _config_cache


def save_ai_config(cfg: AIConfig) -> None:
    """保存 AI 配置"""
    global _config_cache
    _config_cache = cfg
    config_path = config.DATA_DIR / "ai_config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(cfg.__dict__, f, ensure_ascii=False, indent=2)


@router.get("/config")
async def get_ai_config():
    """获取 AI 配置"""
    cfg = load_ai_config()
    return {
        "provider": cfg.provider,
        "apiKey": cfg.apiKey[:6] + "..." if cfg.apiKey else "",
        "baseUrl": cfg.baseUrl,
        "model": cfg.model
    }


@router.post("/config")
async def update_ai_config(data: AIConfigUpdate):
    """更新 AI 配置"""
    api_key = data.apiKey
    # 如果传入的是脱敏值（包含 "..."）或为空，保留原值
    if "..." in api_key or not api_key:
        old_cfg = load_ai_config()
        api_key = old_cfg.apiKey

    cfg = AIConfig(
        provider=data.provider,
        apiKey=api_key,
        baseUrl=data.baseUrl,
        model=data.model
    )
    save_ai_config(cfg)
    return {"success": True, "message": "配置已保存"}


@router.post("/test")
async def test_ai_connection():
    """测试 AI 连接"""
    cfg = load_ai_config()
    if not cfg.apiKey and cfg.provider != "ollama":
        raise HTTPException(status_code=400, detail="请先配置 API Key")
    try:
        provider = create_provider(cfg)
        result = provider.chat([{"role": "user", "content": "你好，请回复'连接成功'"}])
        return {"success": True, "message": "连接成功", "reply": result}
    except Exception as e:
        return {"success": False, "message": f"连接失败: {str(e)}"}


@router.post("/nl2sql/{session_id}")
async def nl2sql_query(
    session_id: str,
    request: NL2SQLRequest
):
    """自然语言转 SQL 查询（支持多表 + 多轮对话）"""
    from app.services.database import DatabaseService

    db_path = config.get_db_path(session_id)
    if config.DB_TYPE == 'mysql':
        if not db_path:
            raise HTTPException(status_code=404, detail="会话不存在或已过期")
    else:
        if not db_path or not db_path.exists():
            raise HTTPException(status_code=404, detail="会话不存在或已过期")

    cfg = load_ai_config()
    if not cfg.apiKey and cfg.provider != "ollama":
        raise HTTPException(status_code=400, detail="请先在设置中配置 AI API Key")

    from app.routers.data import sanitize_table_name

    if request.tableName:
        table_names = [sanitize_table_name(request.tableName)]
    else:
        table_names = DatabaseService.list_tables(db_path)

    tables_info = []
    for tn in table_names:
        headers = DatabaseService.get_headers(db_path, tn)
        sample_rows, _ = DatabaseService.query_data(
            db_path, tn, headers=headers, page=1, page_size=5
        )
        sample_data = [list(row) for row in sample_rows]
        tables_info.append({
            "name": tn,
            "columns": headers,
            "sample_data": sample_data
        })

    try:
        provider = create_provider(cfg)
        result = NL2SQLService.generate_sql(
            provider, tables_info, request.question, request.history,
            db_type=config.DB_TYPE
        )

        if not result.get("success"):
            return {"success": False, "error": result.get("error", "生成失败")}

        sql = result.get("sql", "")
        is_valid, err = validate_sql(sql)
        if not is_valid:
            return {"success": False, "error": err}

        rows = DatabaseService.execute_sql(db_path, sql)
        columns = rows[0] if rows else []
        data_rows = rows[1] if len(rows) > 1 else []

        # 限制返回行数
        MAX_RESULT_ROWS = 1000
        if len(data_rows) > MAX_RESULT_ROWS:
            data_rows = data_rows[:MAX_RESULT_ROWS]

        return {
            "success": True,
            "sql": sql,
            "explanation": result.get("explanation", ""),
            "columns": columns,
            "rows": [list(r) for r in data_rows]
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


def validate_sql(sql: str) -> tuple:
    """校验 SQL 安全性。返回 (is_valid, error_msg)"""
    import re
    sql_stripped = sql.strip()

    # 必须 SELECT
    if not sql_stripped.upper().startswith("SELECT"):
        return False, "只允许 SELECT 查询"

    # 危险关键字黑名单（用单词边界匹配，避免误杀如 SELECTED）
    dangerous = ["DROP", "DELETE", "INSERT", "UPDATE", "ALTER", "CREATE",
                 "TRUNCATE", "EXEC", "EXECUTE"]
    sql_upper = sql_stripped.upper()
    for kw in dangerous:
        if re.search(rf'\b{kw}\b', sql_upper):
            return False, f"不允许包含 {kw} 操作"

    # 禁止分号（防止 SQL 注入）
    if ";" in sql_stripped.rstrip(";"):
        return False, "SQL 中不允许包含分号"

    return True, ""


@router.post("/smart-chart/{session_id}")
async def smart_chart_suggest(
    session_id: str,
    tableName: str = Body(default="data"),
    userRequest: str = Body(default="")
):
    """智能图表推荐"""
    from app.services.database import DatabaseService

    db_path = config.get_db_path(session_id)
    if config.DB_TYPE == 'mysql':
        if not db_path:
            raise HTTPException(status_code=404, detail="会话不存在或已过期")
    else:
        if not db_path or not db_path.exists():
            raise HTTPException(status_code=404, detail="会话不存在或已过期")

    cfg = load_ai_config()
    if not cfg.apiKey and cfg.provider != "ollama":
        raise HTTPException(status_code=400, detail="请先在设置中配置 AI API Key")

    from app.routers.data import sanitize_table_name
    table_name = sanitize_table_name(tableName)
    headers = DatabaseService.get_headers(db_path, table_name)

    sample_rows, _ = DatabaseService.query_data(
        db_path, table_name, headers=headers, page=1, page_size=10
    )
    sample_data = [list(row) for row in sample_rows]

    try:
        provider = create_provider(cfg)
        result = SmartChartService.suggest_chart(provider, headers, sample_data, userRequest)
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}
