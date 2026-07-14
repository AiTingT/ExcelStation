from fastapi import APIRouter, UploadFile, File, HTTPException, Form, Request
from pathlib import Path
import shutil
import logging
from typing import Optional

from app.config import config
from app.services.taskManager import taskManager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/upload", tags=["文件上传"])


def _user_id(request: Request) -> str:
    """从请求头获取用户 ID"""
    return request.headers.get("X-User-Id", "")


@router.post("/")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    password: Optional[str] = Form(default=None)
):
    """上传 Excel 文件，后台异步解析所有 sheet，支持加密文件密码"""
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in config.ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式，仅支持: {', '.join(config.ALLOWED_EXTENSIONS)}"
        )

    upload_path = config.UPLOAD_DIR / f"upload_{file.filename}"
    try:
        with open(upload_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        logger.error(f"文件保存失败: {e}")
        raise HTTPException(status_code=500, detail="文件保存失败")

    uid = _user_id(request)
    session_id = taskManager.create_task(file.filename, upload_path, password, user_id=uid)
    logger.info(f"创建解析任务: {session_id}, 用户: {uid[:8]}..., 文件: {file.filename}")

    return {"sessionId": session_id, "fileName": file.filename}


@router.post("/retry-password/{session_id}")
async def retry_with_password(request: Request, session_id: str, password: str = Form(...)):
    """使用密码重试解析加密文件"""
    success = taskManager.retry_with_password(session_id, password, user_id=_user_id(request))
    if not success:
        raise HTTPException(status_code=400, detail="无法重试，请检查会话是否存在或文件是否已加密")
    return {"success": True, "message": "已开始重新解析"}


@router.get("/progress/{session_id}")
async def get_upload_progress(request: Request, session_id: str):
    """查询解析进度"""
    progress = taskManager.get_progress(session_id)
    if not progress:
        raise HTTPException(status_code=404, detail="任务不存在")
    # 隔离：用户只能查看自己的任务进度
    uid = _user_id(request)
    if uid and progress.userId != uid:
        raise HTTPException(status_code=404, detail="任务不存在")

    return {
        "sessionId": progress.sessionId,
        "fileName": progress.fileName,
        "status": progress.status,
        "currentSheet": progress.currentSheet,
        "totalRows": progress.totalRows,
        "processedRows": progress.processedRows,
        "isEncrypted": progress.isEncrypted,
        "parseTime": progress.parseTime,
        "sheets": [
            {"name": s.name, "headers": s.headers, "rowCount": s.rowCount}
            for s in progress.sheets
        ],
        "error": progress.error
    }


@router.get("/sessions")
async def list_sessions(request: Request):
    """列出当前用户的会话"""
    return {"sessions": taskManager.list_sessions(user_id=_user_id(request))}


@router.delete("/sessions/{session_id}")
async def delete_session(request: Request, session_id: str):
    """删除会话"""
    if taskManager.delete_session(session_id, user_id=_user_id(request)):
        return {"success": True, "message": "会话已删除"}
    raise HTTPException(status_code=404, detail="会话不存在")
