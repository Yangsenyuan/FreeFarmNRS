"""API 输出序列化。"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..core.store import store
from ..core.utils import read_json


def image_public(pid: str, image: Dict[str, Any]) -> Dict[str, Any]:
    iid = image.get("id")
    idir = store.image_dir(pid, iid)
    data = dict(image)
    data["has_processed"] = (idir / "processed" / "meta.json").exists()
    data["has_result"] = (idir / "results" / "latest.json").exists()
    data["source_available"] = (idir / str(image.get("file") or "")).exists()
    version = str(image.get("updated_at") or image.get("imported_at") or "")
    data["thumb_url"] = (
        f"/api/projects/{pid}/images/{iid}/preview?mode=rgb&size=480&v={version}"
    )
    # 图像列表里不必携带完整 transform 等细节，但保留给查看页
    return data


def project_public(project: Dict[str, Any], include_images: bool = True) -> Dict[str, Any]:
    pid = project.get("id")
    images = project.get("images", []) or []
    inverted = sum(1 for im in images if im.get("status") == "inverted")
    preprocessed = sum(1 for im in images if im.get("status") in ("preprocessed", "inverted"))
    if not images:
        status, label = "empty", "待导入"
    elif inverted == len(images):
        status, label = "completed", "已完成"
    elif preprocessed > 0:
        status, label = "processing", "处理中"
    else:
        status, label = "imported", "待反演"

    cover_path = store.project_dir(pid) / "cover.png"
    cover_url = (
        f"/api/projects/{pid}/cover?v={project.get('updated_at') or ''}"
        if cover_path.exists()
        else None
    )
    last_image = ""
    if images:
        last_image = max(images, key=lambda im: im.get("imported_at") or "").get("name", "")
    data = {
        "id": pid,
        "name": project.get("name"),
        "description": project.get("description"),
        "crop": project.get("crop"),
        "field": project.get("field"),
        "note": project.get("note"),
        "created_at": project.get("created_at"),
        "updated_at": project.get("updated_at"),
        "image_count": len(images),
        "inverted_count": inverted,
        "preprocessed_count": preprocessed,
        "status": status,
        "status_label": label,
        "cover_url": cover_url,
        "last_image_name": last_image,
    }
    if include_images:
        data["images"] = [image_public(pid, im) for im in images]
    return data


def result_public(pid: str, iid: str, image: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    rdir = store.image_dir(pid, iid) / "results"
    summary = read_json(rdir / "latest.json")
    if not isinstance(summary, dict):
        return None
    version = str(summary.get("created_at") or "")
    data = dict(summary)
    data["unit"] = (
        summary.get("unit")
        or (summary.get("model") or {}).get("unit")
        or "待确认"
    )
    data["preview_classified_url"] = (
        f"/api/projects/{pid}/images/{iid}/result/preview?mode=classified&v={version}"
    )
    data["preview_continuous_url"] = (
        f"/api/projects/{pid}/images/{iid}/result/preview?mode=continuous&v={version}"
    )
    if image:
        data["image_name"] = image.get("name")
        data["flight_date"] = image.get("flight_date") or summary.get("flight_date")
    return data
