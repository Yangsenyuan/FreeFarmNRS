"""项目管理 API。"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..core.store import store
from ..services import raster as R
from ..services.jobs import jobs
from .deps import get_project, image_source_path
from .serializers import project_public

router = APIRouter(prefix="/api/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = ""
    crop: str = ""
    field: str = ""
    note: str = ""


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    description: Optional[str] = None
    crop: Optional[str] = None
    field: Optional[str] = None
    note: Optional[str] = None


@router.get("")
def list_projects() -> Dict[str, Any]:
    projects = [project_public(p, include_images=False) for p in store.list_projects()]
    return {"projects": projects}


@router.post("")
def create_project(payload: ProjectCreate) -> Dict[str, Any]:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="项目名称不能为空")
    try:
        project = store.create_project(
            name=name,
            description=payload.description.strip(),
            crop=payload.crop.strip(),
            field=payload.field.strip(),
            note=payload.note.strip(),
        )
    except FileExistsError:
        raise HTTPException(status_code=409, detail="项目已存在")
    return project_public(project)


@router.get("/{pid}")
def get_project_detail(pid: str) -> Dict[str, Any]:
    project = get_project(pid)
    return project_public(project)


@router.patch("/{pid}")
def update_project(pid: str, payload: ProjectUpdate) -> Dict[str, Any]:
    get_project(pid)
    patch = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    if "name" in patch:
        patch["name"] = patch["name"].strip()
        if not patch["name"]:
            raise HTTPException(status_code=400, detail="项目名称不能为空")
    project = store.update_project(pid, patch)
    store.append_log(pid, "项目信息已更新", source="project")
    return project_public(project)


@router.delete("/{pid}")
def delete_project(pid: str) -> Dict[str, Any]:
    get_project(pid)
    active = jobs.list(project_id=pid, active_only=True)
    if active:
        raise HTTPException(status_code=409, detail="该项目有正在执行的任务，请等待任务完成后再删除")
    store.delete_project(pid)
    return {"ok": True}


@router.get("/{pid}/logs")
def get_logs(pid: str, limit: int = Query(200, ge=1, le=1000)) -> Dict[str, Any]:
    get_project(pid)
    return {"logs": store.read_logs(pid, limit=limit)}


@router.get("/{pid}/cover")
def get_cover(pid: str):
    project = get_project(pid)
    cover = store.project_dir(pid) / "cover.png"
    images = project.get("images") or []
    if not cover.exists() and images:
        try:
            source = image_source_path(pid, images[0])
            R.save_cover_image(source, cover)
        except Exception:
            raise HTTPException(status_code=404, detail="暂无项目封面")
    if not cover.exists():
        raise HTTPException(status_code=404, detail="暂无项目封面")
    return FileResponse(
        cover,
        media_type="image/png",
        headers={"Cache-Control": "no-cache"},
    )
