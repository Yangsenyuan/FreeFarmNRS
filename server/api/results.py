"""反演结果查询与导出 API。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..core.store import store
from ..core.utils import now_stamp, read_json
from ..services import raster as R
from ..services.report import export_result_pdf, export_result_png
from .deps import get_image, get_project
from .serializers import result_public

image_router = APIRouter(prefix="/api/projects/{pid}/images", tags=["results"])
project_router = APIRouter(prefix="/api/projects", tags=["results"])


def _latest_summary(pid: str, iid: str) -> Dict[str, Any]:
    path = store.image_dir(pid, iid) / "results" / "latest.json"
    summary = read_json(path)
    if not isinstance(summary, dict):
        raise HTTPException(status_code=404, detail="该影像尚无反演结果")
    return summary


@image_router.get("/{iid}/result")
def get_result(pid: str, iid: str) -> Dict[str, Any]:
    image = get_image(pid, iid)
    summary = result_public(pid, iid, image)
    if not summary:
        raise HTTPException(status_code=404, detail="该影像尚无反演结果")
    return summary


@image_router.get("/{iid}/result/preview")
def result_preview(pid: str, iid: str, mode: str = "classified"):
    get_image(pid, iid)
    summary = _latest_summary(pid, iid)
    if mode not in ("classified", "continuous"):
        raise HTTPException(status_code=400, detail="不支持的预览模式")
    rel = (summary.get("files") or {}).get(mode)
    if not rel:
        raise HTTPException(status_code=404, detail="结果预览图不存在")
    path = store.image_dir(pid, iid) / "results" / rel
    if not path.exists():
        raise HTTPException(status_code=404, detail="结果预览图缺失")
    return FileResponse(
        path,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@image_router.get("/{iid}/result/value")
def result_value(pid: str, iid: str, x: int, y: int) -> Dict[str, Any]:
    get_image(pid, iid)
    summary = _latest_summary(pid, iid)
    npy = (summary.get("files") or {}).get("npy") or "nmass_latest.npy"
    npy_path = store.image_dir(pid, iid) / "results" / npy
    if not npy_path.exists():
        raise HTTPException(status_code=404, detail="结果栅格文件缺失")
    thresholds = summary.get("thresholds") or {}
    low = thresholds.get("low")
    high = thresholds.get("high")
    try:
        value = R.read_result_pixel(
            npy_path,
            row=y,
            col=x,
            low=float(low if low is not None else 22.0),
            high=float(high if high is not None else 28.0),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    for item in summary.get("classes") or []:
        if item.get("key") == value.get("class"):
            value["class_name"] = item.get("name")
            value["color"] = item.get("color")
            break
    value["unit"] = (
        summary.get("unit")
        or (summary.get("model") or {}).get("unit")
        or "待确认"
    )
    return value


def _export(pid: str, iid: str, kind: str) -> FileResponse:
    image = get_image(pid, iid)
    summary = _latest_summary(pid, iid)
    project = get_project(pid)
    rdir = store.image_dir(pid, iid) / "results"
    exports = rdir / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    stem = Path(image.get("name") or "result").stem
    safe_stem = "".join(ch for ch in stem if ch not in '\\/:*?"<>|')[:60] or "result"
    stamp = now_stamp()
    if kind == "png":
        path = exports / f"Nmass_map_{stamp}.png"
        export_result_png(summary, rdir, path)
        download_name = f"Nmass专题图_{safe_stem}.png"
        media = "image/png"
    else:
        path = exports / f"Nmass_report_{stamp}.pdf"
        export_result_pdf(summary, project, image, rdir, path)
        download_name = f"氮素反演报告_{safe_stem}.pdf"
        media = "application/pdf"
    store.append_log(pid, f"导出结果（{kind.upper()}）「{image.get('name')}」", source="result")
    return FileResponse(path, media_type=media, filename=download_name)


@image_router.get("/{iid}/result/export/png")
def export_png(pid: str, iid: str):
    try:
        return _export(pid, iid, "png")
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"专题图导出失败：{exc}")


@image_router.get("/{iid}/result/export/pdf")
def export_pdf(pid: str, iid: str):
    try:
        return _export(pid, iid, "pdf")
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"PDF 报告生成失败：{exc}")


@project_router.get("/{pid}/results")
def list_project_results(pid: str) -> Dict[str, Any]:
    project = get_project(pid)
    items: List[Dict[str, Any]] = []
    for image in project.get("images", []):
        summary = result_public(pid, image["id"], image)
        if summary:
            items.append(summary)
    items.sort(key=lambda s: (s.get("flight_date") or "", s.get("created_at") or ""))
    history = store.read_results_history(pid)
    history = history[::-1]
    return {"results": items, "history": history, "project_id": pid}
