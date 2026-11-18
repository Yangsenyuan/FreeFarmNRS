"""本地一键启动入口：启动服务并自动打开浏览器。"""
from __future__ import annotations

import threading
import webbrowser
from urllib.error import URLError
from urllib.request import urlopen

import uvicorn

from server.core.config import APP_NAME, HOST, PORT, ensure_dirs


def _already_running() -> bool:
    try:
        with urlopen(f"http://{HOST}:{PORT}/api/health", timeout=1.2) as resp:
            return resp.status == 200
    except (URLError, OSError, ValueError):
        return False


def _open_browser() -> None:
    webbrowser.open(f"http://{HOST}:{PORT}/")


def main() -> None:
    ensure_dirs()
    if _already_running():
        print(f"{APP_NAME} 已在运行，正在打开浏览器…")
        _open_browser()
        return
    print("=" * 52)
    print(f"  {APP_NAME} V1.0")
    print(f"  访问地址：http://{HOST}:{PORT}/")
    print("  数据仅在本机处理，关闭本窗口即可停止服务")
    print("=" * 52)
    threading.Timer(1.6, _open_browser).start()
    uvicorn.run("server.main:app", host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
