"""API 公共依赖。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from fastapi import HTTPException

from ..core.store import ImageNotFound, ProjectNotFound, store


def get_project(pid: str) -> Dict[str, Any]:
    try:
        return store.get_project(pid)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail="项目不存在或已被删除")


def get_image(pid: str, iid: str) -> Dict[str, Any]:
    try:
        return store.get_image(pid, iid)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail="项目不存在或已被删除")
    except ImageNotFound:
        raise HTTPException(status_code=404, detail="影像不存在或已被删除")


def image_source_path(pid: str, image: Dict[str, Any]) -> Path:
    path = store.image_dir(pid, image["id"]) / str(image.get("file") or "")
    if not path.exists():
        raise HTTPException(status_code=404, detail="影像源文件缺失，请重新导入")
    return path


def processed_dir(pid: str, iid: str) -> Path:
    return store.image_dir(pid, iid) / "processed"


def results_dir(pid: str, iid: str) -> Path:
    return store.image_dir(pid, iid) / "results"
