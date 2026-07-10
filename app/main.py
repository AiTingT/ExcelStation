from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path
import logging

from app.config import config
from app.routers import upload, data, ai, system, database

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# 创建 FastAPI 应用
app = FastAPI(
    title="Excel 轻量工作站",
    description="超大 Excel 文件的本地化 AI 处理平台",
    version="1.0.0"
)

# 挂载静态文件目录
app.mount("/_shared", StaticFiles(directory=str(config.SHARED_DIR)), name="shared")
app.mount("/toolbox", StaticFiles(directory=str(config.STATIC_DIR / "toolbox")), name="toolbox")

# 注册路由
app.include_router(upload.router)
app.include_router(data.router)
app.include_router(ai.router)
app.include_router(system.router)
app.include_router(database.router)


@app.on_event("startup")
async def _load_persisted_db_config():
    """启动时从 data/db_config.json 恢复数据库配置（优先级高于环境变量默认值）"""
    database.load_db_config()


@app.get("/")
async def root():
    """返回前端页面"""
    index_path = config.STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {"message": "Excel 轻量工作站 API", "docs": "/docs"}


@app.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "ok", "message": "Excel 轻量工作站运行中"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        log_level="info"
    )
