# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path

block_cipher = None

base_dir = Path(SPECPATH).resolve()

hidden_imports = [
    # FastAPI 核心与依赖
    'fastapi',
    'fastapi.staticfiles',
    'fastapi.responses',
    'fastapi.middleware',
    'fastapi.middleware.cors',
    'starlette',
    'starlette.staticfiles',
    'starlette.responses',
    'starlette.routing',
    'pydantic',
    'python_multipart',
    'multipart',
    # Uvicorn 完整启动链
    'uvicorn',
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.loops.asyncio',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.http.h11_impl',
    'uvicorn.protocols.http.httptools_impl',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.protocols.websockets.wsproto_impl',
    'uvicorn.protocols.websockets.websockets_impl',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',
    'anyio',
    'anyio._backends',
    'anyio._backends._asyncio',
    'h11',
    'httptools',
    # Excel 解析（含可选：加密/xls/MySQL）
    'python_calamine',
    'openpyxl',
    'msoffcrypto',
    'msoffcrypto.office',
    'msoffcrypto.format',
    'msoffcrypto.format.ooxml',
    'xlrd',
    'polars',
    'psutil',
    'pymysql',
    # 应用模块
    'app',
    'app.main',
    'app.config',
    'app.models',
    'app.models.schemas',
    'app.routers',
    'app.routers.upload',
    'app.routers.data',
    'app.routers.ai',
    'app.routers.system',
    'app.routers.database',
    'app.services',
    'app.services.excelParser',
    'app.services.database',
    'app.services.taskManager',
    'app.services.aiService',
]

a = Analysis(
    ['main.py'],
    pathex=[str(base_dir)],
    binaries=[],
    datas=[
        (str(base_dir / 'static'), 'static'),
        (str(base_dir / '_shared'), '_shared'),
    ],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='ExcelStation',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
