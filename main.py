#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
表格工作台 - 启动入口
"""

import sys
import os
import webbrowser


def main():
    import uvicorn

    is_frozen = getattr(sys, "frozen", False)
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "127.0.0.1")

    print("=" * 50)
    print("  表格工作台")
    print("=" * 50)
    print(f"  访问地址: http://{host}:{port}")
    print(f"  API 文档: http://{host}:{port}/docs")
    print("=" * 50)
    print("  启动后将自动打开浏览器...")
    print("  按 Ctrl+C 停止服务")
    print("=" * 50)
    print()

    import threading
    def open_browser():
        import time
        time.sleep(1.5)
        try:
            webbrowser.open(f"http://{host}:{port}")
        except Exception:
            pass

    if is_frozen:
        threading.Thread(target=open_browser, daemon=True).start()

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=not is_frozen,
        log_level="info"
    )


if __name__ == "__main__":
    main()
