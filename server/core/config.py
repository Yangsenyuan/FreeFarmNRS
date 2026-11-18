"""全局配置与路径定义。"""
from __future__ import annotations

import os
from pathlib import Path

# 项目根目录（CSC_Nmass）
BASE_DIR = Path(__file__).resolve().parents[2]

# 可通过环境变量覆盖数据目录，便于安装到其他磁盘
DATA_DIR = Path(os.environ.get("NMASS_DATA_DIR", str(BASE_DIR / "data"))).resolve()
PROJECTS_DIR = DATA_DIR / "projects"
CUSTOM_MODELS_DIR = DATA_DIR / "models"
CACHE_DIR = DATA_DIR / "cache"

WEB_DIR = BASE_DIR / "web"
BUILTIN_MODELS_DIR = BASE_DIR / "server" / "builtin_models"

APP_NAME = "FreeFarmNRS"
APP_NAME_EN = "FreeFarmNRS"
APP_VERSION = "1.0.0"

# 本地默认仅监听 127.0.0.1；云平台（如 Render）会注入 PORT，此时需监听 0.0.0.0
HOST = os.environ.get("NMASS_HOST") or ("0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")
PORT = int(os.environ.get("NMASS_PORT") or os.environ.get("PORT") or "8765")

# 单幅影像载入内存的上限（像元×波段），超过时给出友好提示
MAX_LOAD_VALUES = 500_000_000

# 结果分级默认阈值（mg/g）
DEFAULT_LOW_THRESHOLD = 22.0
DEFAULT_HIGH_THRESHOLD = 28.0

CLASS_COLORS = {
    "low": "#D55E00",
    "suitable": "#009E73",
    "high": "#0072B2",
}


def ensure_dirs() -> None:
    for d in (DATA_DIR, PROJECTS_DIR, CUSTOM_MODELS_DIR, CACHE_DIR, BUILTIN_MODELS_DIR):
        d.mkdir(parents=True, exist_ok=True)
