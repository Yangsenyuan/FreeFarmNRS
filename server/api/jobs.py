"""后台任务查询 API。"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query

from ..services.jobs import jobs

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("")
def list_jobs(
    project_id: Optional[str] = None,
    active_only: bool = Query(False),
) -> Dict[str, Any]:
    return {"jobs": jobs.list(project_id=project_id, active_only=active_only)}


@router.get("/{jid}")
def get_job(jid: str) -> Dict[str, Any]:
    job = jobs.get(jid)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return job
