"""系统信息与示例项目 API。"""
from __future__ import annotations

import platform
import sys
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Dict

from fastapi import APIRouter
from pydantic import BaseModel

from ..core.config import APP_NAME, APP_NAME_EN, APP_VERSION, DATA_DIR
from ..core.store import store
from ..services.demo import DEMO_IDS, demo_projects_exist, seed_demo
from ..services.jobs import jobs
from ..services.models import list_models, onnx_available
from ..services.preprocess import default_config

router = APIRouter(prefix="/api/system", tags=["system"])


class DemoRequest(BaseModel):
    force: bool = False


def _pkg_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "未安装"


@router.get("/info")
def system_info() -> Dict[str, Any]:
    projects = store.list_projects()
    image_count = sum(len(p.get("images") or []) for p in projects)
    result_count = sum(
        1
        for p in projects
        for im in (p.get("images") or [])
        if im.get("status") == "inverted"
    )
    return {
        "app_name": APP_NAME,
        "app_name_en": APP_NAME_EN,
        "version": APP_VERSION,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "data_dir": str(DATA_DIR),
        "project_count": len(projects),
        "image_count": image_count,
        "result_count": result_count,
        "model_count": len(list_models()),
        "onnx_available": onnx_available(),
        "demo_exists": demo_projects_exist(),
        "demo_ids": DEMO_IDS,
        "packages": {
            "rasterio": _pkg_version("rasterio"),
            "numpy": _pkg_version("numpy"),
            "scipy": _pkg_version("scipy"),
            "scikit-learn": _pkg_version("scikit-learn"),
            "reportlab": _pkg_version("reportlab"),
        },
    }


@router.get("/preprocess-defaults")
def preprocess_defaults() -> Dict[str, Any]:
    return default_config()


@router.get("/demo")
def demo_status() -> Dict[str, Any]:
    return {"exists": demo_projects_exist(), "project_ids": DEMO_IDS}


@router.post("/demo")
def create_demo(payload: DemoRequest) -> Dict[str, Any]:
    def worker(progress):
        return seed_demo(force=payload.force, progress=progress)

    jid = jobs.submit(
        "demo",
        worker,
        label="生成示例项目",
        extra={"force": payload.force},
    )
    return {"job_id": jid, "label": "生成示例项目"}
