"""生成示例项目。

运行：
    .venv\\Scripts\\python.exe -m server.tools.seed_demo [--force]
"""
from __future__ import annotations

import sys

from server.core.config import ensure_dirs
from server.services.demo import seed_demo


def _progress(percent: int, stage: str, message: str):
    print(f"[{percent:3d}%] {stage}：{message}")


if __name__ == "__main__":
    ensure_dirs()
    force = "--force" in sys.argv
    result = seed_demo(force=force, progress=_progress)
    print(result)
