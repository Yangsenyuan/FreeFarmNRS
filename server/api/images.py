"""影像导入、浏览、光谱查询 API。"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..core.store import store
from ..core.utils import new_id, now_iso, parse_number_list, sanitize_filename
from ..services import raster as R
from ..services import image_metadata as IM
from ..services.jobs import jobs
from .deps import get_image, get_project, image_source_path
from .serializers import image_public, project_public

router = APIRouter(prefix="/api/projects/{pid}/images", tags=["images"])

TIFF_EXTS = {".tif", ".tiff"}
ENVI_DATA_EXTS = {".dat", ".img", ".bil", ".bsq"}
ENVI_HDR_EXTS = {".hdr"}
SIDECAR_EXTS = {".json", ".xml", ".xmp"}


def _clear_preview_cache(pid: str, iid: str) -> None:
    cache = store.image_dir(pid, iid) / "preview_cache"
    if cache.exists():
        shutil.rmtree(cache, ignore_errors=True)


def _clear_cover(pid: str) -> None:
    cover = store.project_dir(pid) / "cover.png"
    if cover.exists():
        try:
            cover.unlink()
        except OSError:
            pass


def _generate_cover(pid: str) -> None:
    project = store.get_project(pid)
    images = project.get("images") or []
    cover = store.project_dir(pid) / "cover.png"
    if not images:
        _clear_cover(pid)
        return
    preferred = next((im for im in images if im.get("status") == "inverted"), images[0])
    idir = store.image_dir(pid, preferred["id"])
    result_preview = idir / "results" / "previews" / "classified.png"
    try:
        if result_preview.exists():
            R.save_cover_from_png(result_preview, cover)
            return
        source = idir / str(preferred.get("file") or "")
        if source.exists():
            R.save_cover_image(source, cover)
    except Exception:
        pass


def _sidecar_matches(dataset_name: str, sidecar_name: str) -> bool:
    dataset = Path(dataset_name).name.casefold()
    sidecar = Path(sidecar_name).name.casefold()
    dataset_stem = Path(dataset_name).stem.casefold()
    sidecar_stem = Path(sidecar_name).stem.casefold()
    if sidecar_stem == dataset or sidecar_stem == dataset_stem:
        return True
    if sidecar.startswith(dataset + ".") or sidecar.startswith(dataset_stem + "."):
        return True
    dataset_parts = dataset_stem.split("_")
    sidecar_parts = sidecar_stem.split("_")
    # PlanetScope 侧车与数据共用 YYYYMMDD_HHMMSS_xx_xxxx 前缀。
    if len(dataset_parts) >= 4 and len(sidecar_parts) >= 4:
        return dataset_parts[:4] == sidecar_parts[:4]
    return False


class ImageUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    sensor: Optional[str] = None
    sensor_name: Optional[str] = None
    sensor_type: Optional[str] = None
    flight_date: Optional[str] = None
    acquisition_datetime: Optional[str] = None
    field: Optional[str] = None
    field_id: Optional[str] = None
    image_type: Optional[str] = None
    value_type: Optional[str] = None
    processing_level: Optional[str] = None
    note: Optional[str] = None
    wavelengths: Optional[List[float]] = None
    bandwidths: Optional[List[float]] = None
    wavelength_unit: Optional[str] = None


class PointIn(BaseModel):
    x: int
    y: int
    label: str = ""


class RegionIn(BaseModel):
    x: int
    y: int
    w: int = 5
    h: int = 5
    shape: str = "box"
    label: str = ""


class SpectraRequest(BaseModel):
    points: List[PointIn] = Field(default_factory=list)
    regions: List[RegionIn] = Field(default_factory=list)


@router.get("")
def list_images(pid: str) -> Dict[str, Any]:
    project = get_project(pid)
    return {"images": [image_public(pid, im) for im in project.get("images", [])]}


@router.post("")
def upload_images(
    pid: str,
    files: List[UploadFile] = File(...),
    sensor: str = Form(""),
    flight_date: str = Form(""),
    field: str = Form(""),
    note: str = Form(""),
) -> Dict[str, Any]:
    project = get_project(pid)
    if not files:
        raise HTTPException(status_code=400, detail="未选择任何文件")

    errors: List[Dict[str, str]] = []
    groups: List[Dict[str, Any]] = []

    # 先按文件名分组：ENVI 需要 .hdr 与数据文件配对
    hdr_map: Dict[str, List[UploadFile]] = {}
    hdr_names: Dict[str, str] = {}
    indexed: List[Any] = []
    for idx, uf in enumerate(files):
        raw_name = uf.filename or f"file_{idx}"
        name = sanitize_filename(Path(raw_name).name, fallback=f"file_{idx}")
        ext = Path(name).suffix.lower()
        indexed.append((idx, name, ext, uf))
        if ext in ENVI_HDR_EXTS:
            stem = Path(name).stem.lower()
            hdr_map.setdefault(stem, []).append(uf)
            hdr_names[stem] = name

    used_hdr_ids = set()
    for idx, name, ext, uf in indexed:
        if ext in ENVI_HDR_EXTS:
            continue
        if ext in TIFF_EXTS:
            groups.append({"dataset_name": name, "fmt": "GeoTIFF", "files": [(idx, name, uf)]})
        elif ext in ENVI_DATA_EXTS:
            stem = Path(name).stem.lower()
            hdrs = hdr_map.get(stem, [])
            if not hdrs:
                errors.append(
                    {
                        "file": name,
                        "message": "缺少 ENVI 头文件（.hdr），请将 .hdr 与数据文件一起选择后重新导入",
                    }
                )
                continue
            # 只取第一个匹配的 hdr
            hdr_uf = hdrs[0]
            used_hdr_ids.add(id(hdr_uf))
            groups.append(
                {
                    "dataset_name": name,
                    "fmt": "ENVI",
                    "files": [(idx, name, uf), (-1, hdr_names.get(stem, stem + ".hdr"), hdr_uf)],
                }
            )
        elif ext in SIDECAR_EXTS:
            # 伴随文件在影像组建立后按文件名匹配，不单独成为数据集。
            continue
        else:
            errors.append(
                {
                    "file": name,
                    "message": "不支持的文件格式；当前支持 GeoTIFF（.tif/.tiff）与 ENVI（.hdr + .dat/.img）",
                }
            )

    sidecars = [
        (idx, name, uf)
        for idx, name, ext, uf in indexed
        if ext in SIDECAR_EXTS
    ]
    used_sidecars = set()
    for group in groups:
        dataset_name = group["dataset_name"]
        for idx, sidecar_name, sidecar_uf in sidecars:
            if id(sidecar_uf) in used_sidecars:
                continue
            if _sidecar_matches(dataset_name, sidecar_name):
                group["files"].append((idx, sidecar_name, sidecar_uf))
                used_sidecars.add(id(sidecar_uf))
    for _idx, sidecar_name, _uf in sidecars:
        if id(_uf) not in used_sidecars:
            errors.append(
                {
                    "file": sidecar_name,
                    "message": "伴随元数据文件未匹配到同名/同场景影像，已跳过",
                }
            )

    for stem, hdrs in hdr_map.items():
        for hdr_uf in hdrs:
            if id(hdr_uf) not in used_hdr_ids:
                errors.append(
                    {
                        "file": hdr_names.get(stem, f"{stem}.hdr"),
                        "message": "未找到配对的 ENVI 数据文件（.dat/.img），该头文件已跳过",
                    }
                )

    imported = []
    for group in groups:
        name = group["dataset_name"]
        iid = new_id("img", 5)
        image_dir = store.image_dir(pid, iid)
        source_dir = image_dir / "source"
        source_dir.mkdir(parents=True, exist_ok=True)
        saved_paths: Dict[str, Path] = {}
        try:
            for idx, fname, uf in group["files"]:
                safe = sanitize_filename(Path(fname).name, fallback=f"file_{idx}")
                target = source_dir / safe
                # 避免组内重名
                if target.exists():
                    target = source_dir / f"{target.stem}_{idx}{target.suffix}"
                with open(target, "wb") as out:
                    shutil.copyfileobj(uf.file, out)
                saved_paths[safe] = target
                try:
                    uf.file.close()
                except Exception:
                    pass
            dataset_name = group["dataset_name"]
            dataset_path = saved_paths.get(dataset_name)
            if dataset_path is None or not dataset_path.exists():
                raise ValueError("数据文件保存失败")
            if dataset_path.stat().st_size == 0:
                raise ValueError("文件内容为空，可能上传不完整")
            try:
                user_metadata = {
                    "sensor_name": sensor.strip(),
                    "flight_date": flight_date.strip(),
                    "field_id": field.strip(),
                }
                user_metadata = {k: v for k, v in user_metadata.items() if v}
                if note.strip():
                    user_metadata["note"] = note.strip()
                meta = R.raster_meta(dataset_path, user_metadata=user_metadata)
            except Exception as exc:
                raise ValueError(f"无法读取影像，文件可能损坏或格式不受支持（{exc}）") from exc
            if not meta.get("width") or not meta.get("height") or not meta.get("bands"):
                raise ValueError("影像尺寸或波段数为 0，无法导入")
            rel_file = str(dataset_path.relative_to(image_dir)).replace("\\", "/")
            image = {
                "id": iid,
                "name": name,
                "file": rel_file,
                "format": group["fmt"],
                "sensor": meta.get("sensor") or "",
                "sensor_name": meta.get("sensor_name") or meta.get("sensor") or "",
                "flight_date": meta.get("flight_date") or "",
                "field": meta.get("field") or "",
                "field_id": meta.get("field_id") or meta.get("field") or "",
                "note": note.strip(),
                "imported_at": now_iso(),
                "status": "imported",
                "file_size": dataset_path.stat().st_size,
                **meta,
            }
            store.add_image(pid, image)
            store.append_log(
                pid,
                f"导入影像「{name}」：{meta['width']}×{meta['height']}，{meta['bands']} 个波段",
                source="import",
            )
            imported.append(image_public(pid, image))
        except Exception as exc:  # noqa: BLE001
            shutil.rmtree(image_dir, ignore_errors=True)
            errors.append({"file": name, "message": str(exc)})

    if imported:
        _generate_cover(pid)
    project = store.get_project(pid)
    return {
        "imported": imported,
        "errors": errors,
        "project": project_public(project),
    }


@router.get("/{iid}")
def get_image_detail(pid: str, iid: str) -> Dict[str, Any]:
    image = get_image(pid, iid)
    return image_public(pid, image)


def _mark_user_metadata(image: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    """把编辑过的元数据字段标记为 user，并同步统一结果结构。"""
    field_map = {
        "sensor": "sensor_name",
        "sensor_name": "sensor_name",
        "sensor_type": "sensor_type",
        "flight_date": "flight_date",
        "acquisition_datetime": "acquisition_datetime",
        "field": "field_id",
        "field_id": "field_id",
        "image_type": "image_type",
        "value_type": "value_type",
        "processing_level": "processing_level",
        "wavelengths": "wavelengths",
        "bandwidths": "bandwidths",
        "wavelength_unit": "wavelength_unit",
    }
    meta = dict(image.get("metadata") or {})
    fields = dict(meta.get("metadata_fields") or image.get("metadata_fields") or {})
    sources = dict(meta.get("metadata_sources") or image.get("metadata_sources") or {})
    confidence = dict(
        meta.get("metadata_confidence") or image.get("metadata_confidence") or {}
    )
    for key, field in field_map.items():
        if key not in patch:
            continue
        value = patch.get(key)
        fields[field] = {
            "value": value,
            "source": "user",
            "confidence": 1.0,
            "raw_key": f"manual:{key}",
            "raw_value": value,
        }
        sources[field] = "user"
        confidence[field] = 1.0
        meta[field] = value
    meta["metadata_fields"] = fields
    meta["metadata_sources"] = sources
    meta["metadata_confidence"] = confidence
    patch["metadata"] = meta
    patch["metadata_fields"] = fields
    patch["metadata_sources"] = sources
    patch["metadata_confidence"] = confidence
    return patch


@router.patch("/{iid}")
def update_image(pid: str, iid: str, payload: ImageUpdate) -> Dict[str, Any]:
    image = get_image(pid, iid)
    patch = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    if "name" in patch:
        patch["name"] = patch["name"].strip()
        if not patch["name"]:
            raise HTTPException(status_code=400, detail="影像名称不能为空")
    if "wavelengths" in patch:
        wl = [float(v) for v in (patch["wavelengths"] or [])]
        if wl:
            if len(wl) != int(image.get("bands") or 0):
                raise HTTPException(
                    status_code=400,
                    detail=f"中心波长数量（{len(wl)}）与影像波段数（{image.get('bands')}）不一致",
                )
            if min(wl) < 100 or max(wl) > 20000:
                raise HTTPException(status_code=400, detail="中心波长应在 100–20000 nm 之间")
        patch["wavelengths"] = wl
        patch["wavelength_unit"] = payload.wavelength_unit or "nm"
        _clear_preview_cache(pid, iid)
        _clear_cover(pid)
    if "bandwidths" in patch:
        bw = [float(v) for v in (patch["bandwidths"] or [])]
        if bw and len(bw) != int(image.get("bands") or 0):
            raise HTTPException(
                status_code=400,
                detail=f"带宽数量（{len(bw)}）与影像波段数（{image.get('bands')}）不一致",
            )
        patch["bandwidths"] = bw
    patch = _mark_user_metadata(image, patch)
    if "sensor" in patch:
        patch["sensor_name"] = patch["sensor"]
    elif "sensor_name" in patch:
        patch["sensor"] = patch["sensor_name"]
    if "field" in patch:
        patch["field_id"] = patch["field"]
    elif "field_id" in patch:
        patch["field"] = patch["field_id"]
    updated = store.update_image(pid, iid, patch)
    store.append_log(pid, f"更新影像「{updated.get('name')}」元数据", source="import")
    return image_public(pid, updated)


@router.post("/{iid}/reparse-metadata")
def reparse_metadata(pid: str, iid: str, apply: bool = False) -> Dict[str, Any]:
    image = get_image(pid, iid)
    source = image_source_path(pid, image)
    if not source.exists():
        raise HTTPException(status_code=404, detail="影像源文件缺失，无法重新解析")
    try:
        incoming = IM.parse_image_metadata(source)
        compared = IM.reparse_image_metadata(image, incoming)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"重新解析失败：{exc}")
    response = {
        "apply": apply,
        "current": compared["current"],
        "incoming": compared["incoming"],
        "changes": compared["changes"],
        "conflicts": compared["conflicts"],
        "preserved_user_fields": compared["preserved_user_fields"],
        "auto_updated_fields": compared["auto_updated_fields"],
        "metadata_warnings": [
            *((incoming.get("metadata_warnings") or [])),
            *((incoming.get("metadata_conflicts") or [])),
        ],
    }
    if apply:
        fields = IM.image_fields_from_metadata(compared["merged"])
        patch = {
            **fields,
            "metadata": compared["merged"],
            "metadata_fields": compared["merged"].get("metadata_fields") or {},
            "metadata_sources": compared["merged"].get("metadata_sources") or {},
            "metadata_confidence": compared["merged"].get("metadata_confidence") or {},
            "metadata_warnings": compared["merged"].get("metadata_warnings") or [],
            "metadata_conflicts": compared["merged"].get("metadata_conflicts") or [],
        }
        updated = store.update_image(pid, iid, patch)
        _clear_preview_cache(pid, iid)
        _clear_cover(pid)
        _generate_cover(pid)
        store.append_log(
            pid,
            f"重新解析影像「{updated.get('name')}」元数据；保留用户字段 "
            f"{len(compared['preserved_user_fields'])} 个",
            source="import",
        )
        response["image"] = image_public(pid, updated)
    return response


@router.delete("/{iid}")
def delete_image(pid: str, iid: str) -> Dict[str, Any]:
    get_image(pid, iid)
    active = [j for j in jobs.list(project_id=pid, active_only=True) if j.get("image_id") == iid]
    if active:
        raise HTTPException(status_code=409, detail="该影像有正在执行的任务，暂不能删除")
    store.remove_image(pid, iid)
    _clear_cover(pid)
    _generate_cover(pid)
    return {"ok": True}


@router.get("/{iid}/preview")
def image_preview(
    pid: str,
    iid: str,
    mode: str = Query("rgb", pattern="^(rgb|gray|pseudo|ndvi)$"),
    bands: Optional[str] = None,
    band: int = 1,
    size: int = Query(1400, ge=200, le=2400),
    cmap: str = "greens",
    pmin: float = Query(2, ge=0, le=100),
    pmax: float = Query(98, ge=0, le=100),
    gamma: float = Query(1.0, ge=0.2, le=5.0),
    stretch: str = Query("percent", pattern="^(percent|fixed|std)$"),
    vmin: Optional[float] = None,
    vmax: Optional[float] = None,
):
    image = get_image(pid, iid)
    source = image_source_path(pid, image)
    if pmin >= pmax:
        raise HTTPException(status_code=400, detail="拉伸百分位参数不正确")

    wl = image.get("wavelengths") or []
    band_count = int(image.get("bands") or 0)
    if mode == "ndvi":
        cache_key_data = {"mode": mode, "size": size, "v": image.get("updated_at")}
    else:
        idx: List[int] = []
        if bands:
            vals = [int(v) for v in parse_number_list(bands)]
            idx = [v for v in vals if 1 <= v <= band_count]
            if len(idx) != len(vals):
                raise HTTPException(status_code=400, detail="波段号超出影像范围")
        if mode == "rgb":
            if len(idx) < 1:
                idx = [i + 1 for i in R.pick_rgb_bands(wl, band_count)]
            elif len(idx) == 1:
                idx = idx * 3
            elif len(idx) == 2:
                idx = idx + [idx[-1]]
        else:
            if not idx:
                idx = [min(max(band, 1), band_count)]
            idx = idx[:1]
        cache_key_data = {
            "mode": mode,
            "bands": idx,
            "size": size,
            "cmap": cmap,
            "pmin": pmin,
            "pmax": pmax,
            "gamma": gamma,
            "stretch": stretch,
            "vmin": vmin,
            "vmax": vmax,
            "v": image.get("updated_at"),
        }

    key = hashlib.md5(
        json.dumps(cache_key_data, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:20]
    cache_dir = store.image_dir(pid, iid) / "preview_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"{key}.png"
    if not cached.exists():
        try:
            if mode == "ndvi":
                png, _ = R.render_ndvi_preview(source, max_size=size)
            else:
                png, _ = R.render_image_preview(
                    source,
                    mode=mode,
                    bands=idx,
                    max_size=size,
                    cmap=cmap,
                    pmin=pmin,
                    pmax=pmax,
                    gamma=gamma,
                    vmin=vmin,
                    vmax=vmax,
                    stretch=stretch,
                )
            cached.write_bytes(png)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"影像预览生成失败：{exc}")
    return FileResponse(
        cached,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


def _spectra_wavelengths(image: Dict[str, Any]) -> List[float]:
    wl = image.get("wavelengths") or []
    return [float(v) for v in wl] if len(wl) == int(image.get("bands") or 0) else []


@router.get("/{iid}/spectrum")
def point_spectrum(pid: str, iid: str, x: int, y: int) -> Dict[str, Any]:
    image = get_image(pid, iid)
    source = image_source_path(pid, image)
    try:
        result = R.read_point_spectrum(source, row=y, col=x, wavelengths=_spectra_wavelengths(image))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    result["label"] = f"像素 ({x}, {y})"
    result["unit"] = "nm" if _spectra_wavelengths(image) else "band"
    return result


@router.post("/{iid}/spectra")
def batch_spectra(pid: str, iid: str, payload: SpectraRequest) -> Dict[str, Any]:
    image = get_image(pid, iid)
    source = image_source_path(pid, image)
    wl = _spectra_wavelengths(image)
    if len(payload.points) + len(payload.regions) > 20:
        raise HTTPException(status_code=400, detail="一次最多查询 20 条光谱")
    spectra = []
    try:
        for i, p in enumerate(payload.points):
            item = R.read_point_spectrum(source, row=p.y, col=p.x, wavelengths=wl)
            item["label"] = p.label or f"P{i + 1} ({p.x}, {p.y})"
            item["x"], item["y"] = p.x, p.y
            spectra.append(item)
        for i, r in enumerate(payload.regions):
            item = R.read_region_spectrum(
                source,
                row=r.y,
                col=r.x,
                win_w=min(max(r.w, 1), 200),
                win_h=min(max(r.h, 1), 200),
                shape=r.shape if r.shape in ("box", "circle") else "box",
                wavelengths=wl,
            )
            item["label"] = r.label or f"区域{i + 1} ({r.x}, {r.y})"
            item["x"], item["y"] = r.x, r.y
            spectra.append(item)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "spectra": spectra,
        "unit": "nm" if wl else "band",
        "wavelength_unit": "nm" if wl else "band",
    }
