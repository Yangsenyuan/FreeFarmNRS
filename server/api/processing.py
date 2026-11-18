"""预处理与反演执行 API。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..core.store import store
from ..core.utils import read_json
from ..services.few_shot import create_derived_model, extract_measured_samples
from ..services.inference import compact_summary, run_inversion
from ..services.jobs import jobs
from ..services.model_compatibility import assess_model_compatibility
from ..services.models import (
    ModelNotFound,
    delete_custom_model,
    get_model,
    list_models,
)
from ..services.preprocess import PreprocessConfig, load_processed, run_preprocess
from .deps import get_image, get_project

router = APIRouter(prefix="/api/projects/{pid}/images", tags=["processing"])
batch_router = APIRouter(prefix="/api/projects", tags=["processing"])


class PreprocessRequest(BaseModel):
    config: Dict[str, Any] = Field(default_factory=dict)


class InversionRequest(BaseModel):
    model_id: str
    low: Optional[float] = None
    high: Optional[float] = None
    auto_preprocess: bool = True
    force: bool = False


class BatchInversionRequest(BaseModel):
    image_ids: List[str] = Field(min_length=1, max_length=50)
    model_id: str
    low: Optional[float] = None
    high: Optional[float] = None
    auto_preprocess: bool = True
    force: bool = False


class CompatibilityBatchRequest(BaseModel):
    image_ids: List[str] = Field(min_length=1, max_length=50)
    model_id: str


class FewShotSampleInput(BaseModel):
    sample_id: Optional[str] = None
    col: Optional[int] = Field(default=None, ge=0)
    row: Optional[int] = Field(default=None, ge=0)
    lon: Optional[float] = Field(default=None, ge=-180, le=180)
    lat: Optional[float] = Field(default=None, ge=-90, le=90)
    nmass: float = Field(gt=0)


class FewShotPreviewRequest(BaseModel):
    samples: List[FewShotSampleInput] = Field(min_length=1, max_length=200)
    unit: Optional[str] = None


class FewShotApplyRequest(FewShotPreviewRequest):
    name: Optional[str] = None
    force: bool = False


def _preprocess_summary(pmeta: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "created_at": pmeta.get("created_at"),
        "quality": pmeta.get("quality"),
        "mask": pmeta.get("mask"),
        "bands": pmeta.get("bands"),
        "wavelengths": pmeta.get("wavelengths"),
        "config": pmeta.get("config"),
        "indices": pmeta.get("indices"),
        "width": pmeta.get("width"),
        "height": pmeta.get("height"),
        "crs": pmeta.get("crs"),
    }


def _active_jobs_for(pid: str, iid: str) -> List[Dict[str, Any]]:
    active = []
    for job in jobs.list(project_id=pid, active_only=True):
        if job.get("image_id") == iid:
            active.append(job)
            continue
        extra_ids = (job.get("extra") or {}).get("image_ids") or []
        if iid in extra_ids:
            active.append(job)
    return active


def _ensure_source(pid: str, image: Dict[str, Any]) -> Path:
    source = store.image_dir(pid, image["id"]) / str(image.get("file") or "")
    if not source.exists():
        raise ValueError("影像源文件缺失，请重新导入")
    return source


def _current_result_context(pid: str, iid: str):
    image = get_image(pid, iid)
    result_dir = store.image_dir(pid, iid) / "results"
    summary = read_json(result_dir / "latest.json")
    if not isinstance(summary, dict):
        raise ValueError("当前影像尚无反演结果，请先使用模型库模型完成反演")
    model_id = (summary.get("model") or {}).get("id")
    if not model_id:
        raise ValueError("当前结果未记录所用模型，无法建立少样本校正")
    try:
        parent_model = get_model(str(model_id))
    except ModelNotFound:
        raise ValueError("当前结果所用模型已不存在，无法建立少样本校正")
    if not parent_model.get("available"):
        raise ValueError("当前结果所用模型文件不可用，无法建立少样本校正")
    return image, result_dir, summary, parent_model


def _compatibility_image(pid: str, image: Dict[str, Any]) -> Dict[str, Any]:
    project = store.get_project(pid)
    enriched = dict(image)
    enriched["project_crop"] = project.get("crop") or ""
    return enriched


def _assess_image(
    pid: str,
    image: Dict[str, Any],
    model: Dict[str, Any],
    processed_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if processed_meta is None:
        pdir = store.image_dir(pid, image["id"]) / "processed"
        processed_meta = load_processed(pdir)
    return assess_model_compatibility(
        _compatibility_image(pid, image),
        processed_meta,
        model,
    )


def _assessment_block_message(image: Dict[str, Any], assessment: Dict[str, Any]) -> str:
    details = assessment.get("errors") or assessment.get("warnings") or []
    suffix = "；".join(str(v) for v in details[:4])
    return f"影像「{image.get('name') or image.get('id')}」：{suffix}"


def _require_compatible_assessment(
    image: Dict[str, Any],
    assessment: Dict[str, Any],
    force: bool,
) -> None:
    if assessment.get("status") == "incompatible":
        raise ValueError(_assessment_block_message(image, assessment))
    if assessment.get("warnings") and not force:
        raise ValueError(
            _assessment_block_message(image, assessment)
            + "。请核对预检结果并显式确认忽略警告后重试"
        )


@router.get("/{iid}/processed")
def get_processed(pid: str, iid: str) -> Dict[str, Any]:
    get_image(pid, iid)
    pdir = store.image_dir(pid, iid) / "processed"
    pmeta = load_processed(pdir)
    if not pmeta:
        raise HTTPException(status_code=404, detail="该影像尚未完成预处理")
    data = dict(pmeta)
    version = pmeta.get("created_at") or ""
    data["preview_urls"] = {
        key: f"/api/projects/{pid}/images/{iid}/processed/preview?layer={key}&v={version}"
        for key in (pmeta.get("previews") or {})
    }
    return data


@router.get("/{iid}/processed/preview")
def processed_preview(pid: str, iid: str, layer: str = "rgb"):
    get_image(pid, iid)
    pdir = store.image_dir(pid, iid) / "processed"
    pmeta = load_processed(pdir)
    if not pmeta:
        raise HTTPException(status_code=404, detail="该影像尚未完成预处理")
    files = pmeta.get("previews") or {}
    if layer not in ("rgb", "ndvi", "mask") or layer not in files:
        raise HTTPException(status_code=404, detail="该预处理图层不存在")
    path = pdir / files[layer]
    if not path.exists():
        raise HTTPException(status_code=404, detail="预处理预览图缺失，请重新预处理")
    return FileResponse(
        path,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/{iid}/model-compatibility")
def get_model_compatibility(
    pid: str,
    iid: str,
    model_id: Optional[str] = None,
) -> Dict[str, Any]:
    image = get_image(pid, iid)
    pdir = store.image_dir(pid, iid) / "processed"
    pmeta = load_processed(pdir)
    if model_id:
        try:
            model = get_model(model_id)
        except ModelNotFound:
            raise HTTPException(status_code=404, detail="所选模型不存在")
        assessment = _assess_image(pid, image, model, pmeta)
        return {
            "image_id": iid,
            "image_name": image.get("name"),
            "model_id": model_id,
            "assessment": assessment,
        }

    assessments = []
    for model in list_models():
        try:
            model_with_dir = get_model(str(model["id"]))
        except ModelNotFound:
            model_with_dir = model
        assessment = _assess_image(pid, image, model_with_dir, pmeta)
        assessment["available"] = bool(model.get("available", True))
        assessment["demo"] = bool(model.get("demo"))
        assessments.append(assessment)
    return {
        "image_id": iid,
        "image_name": image.get("name"),
        "processed": bool(pmeta),
        "assessments": assessments,
    }


@batch_router.post("/{pid}/batch-model-compatibility")
def batch_model_compatibility(
    pid: str,
    payload: CompatibilityBatchRequest,
) -> Dict[str, Any]:
    project = get_project(pid)
    image_map = {im["id"]: im for im in project.get("images", [])}
    missing = [iid for iid in payload.image_ids if iid not in image_map]
    if missing:
        raise HTTPException(status_code=404, detail=f"影像不存在：{', '.join(missing[:3])}")
    try:
        model = get_model(payload.model_id)
    except ModelNotFound:
        raise HTTPException(status_code=404, detail="所选模型不存在")
    rows = []
    for iid in payload.image_ids:
        image = image_map[iid]
        pmeta = load_processed(store.image_dir(pid, iid) / "processed")
        assessment = _assess_image(pid, image, model, pmeta)
        rows.append(
            {
                "image_id": iid,
                "image_name": image.get("name"),
                "bands": image.get("bands"),
                "has_processed": bool(pmeta),
                "assessment": assessment,
            }
        )
    counts = {
        status: sum(1 for row in rows if row["assessment"].get("status") == status)
        for status in ("compatible", "warning", "incompatible")
    }
    return {
        "project_id": pid,
        "model_id": payload.model_id,
        "model_name": model.get("name"),
        "images": rows,
        "counts": counts,
        "can_run": counts["incompatible"] == 0,
        "requires_confirmation": counts["warning"] > 0,
    }


@router.post("/{iid}/preprocess")
def start_preprocess(pid: str, iid: str, payload: PreprocessRequest) -> Dict[str, Any]:
    image = get_image(pid, iid)
    if _active_jobs_for(pid, iid):
        raise HTTPException(status_code=409, detail="该影像有任务正在执行，请稍后再试")
    try:
        _ensure_source(pid, image)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    config = payload.config or {}
    try:
        PreprocessConfig.model_validate(config)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"预处理参数无效：{exc}")
    label = f"预处理 · {image.get('name')}"

    def worker(progress):
        current = store.get_image(pid, iid)
        source = _ensure_source(pid, current)
        store.append_log(pid, f"开始预处理「{current.get('name')}」", source="preprocess")
        pmeta = run_preprocess(
            source,
            store.image_dir(pid, iid) / "processed",
            config=config or None,
            progress=progress,
        )
        summary = _preprocess_summary(pmeta)
        store.update_image(
            pid,
            iid,
            {
                "status": "preprocessed",
                "preprocess": summary,
                "preprocess_at": pmeta.get("created_at"),
            },
        )
        level = (pmeta.get("quality") or {}).get("level", "ok")
        level_label = {"ok": "正常", "notice": "提示", "warning": "警告"}.get(level, level)
        store.append_log(
            pid,
            f"影像「{current.get('name')}」预处理完成，质量检查：{level_label}，"
            f"植被像元占比 {pmeta['mask']['ratio'] * 100:.1f}%",
            level="warning" if level == "warning" else "info",
            source="preprocess",
        )
        return {
            "image_id": iid,
            "quality": pmeta.get("quality"),
            "mask": pmeta.get("mask"),
            "bands": pmeta.get("bands"),
            "wavelengths": pmeta.get("wavelengths"),
        }

    jid = jobs.submit(
        "preprocess",
        worker,
        project_id=pid,
        image_id=iid,
        label=label,
        extra={"image_ids": [iid]},
    )
    return {"job_id": jid, "label": label}


@router.get("/{iid}/few-shot/template.csv")
def few_shot_template(pid: str, iid: str, coordinate: str = "pixel"):
    get_image(pid, iid)
    if coordinate == "lonlat":
        content = (
            "sample_id,lon,lat,nmass\n"
            "sample_1,114.17650,23.01480,23.4\n"
            "sample_2,114.17800,23.01420,25.1\n"
            "sample_3,114.17920,23.01360,21.8\n"
        )
    elif coordinate == "pixel":
        content = (
            "sample_id,col,row,nmass\n"
            "sample_1,120,80,23.4\n"
            "sample_2,240,96,25.1\n"
            "sample_3,360,144,21.8\n"
        )
    else:
        raise HTTPException(status_code=400, detail="coordinate 仅支持 pixel 或 lonlat")
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="few_shot_samples_{coordinate}.csv"',
        },
    )


@router.post("/{iid}/few-shot/preview")
def preview_few_shot(
    pid: str,
    iid: str,
    payload: FewShotPreviewRequest,
) -> Dict[str, Any]:
    try:
        image, result_dir, summary, parent_model = _current_result_context(pid, iid)
        calibration = extract_measured_samples(
            result_dir,
            summary,
            [sample.model_dump() for sample in payload.samples],
            unit=payload.unit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "image_id": iid,
        "image_name": image.get("name"),
        "base_result_id": summary.get("id"),
        "parent_model": {
            key: parent_model.get(key)
            for key in ("id", "name", "version", "algorithm", "unit", "file_sha256")
        },
        **calibration,
    }


@router.post("/{iid}/few-shot/apply")
def apply_few_shot(
    pid: str,
    iid: str,
    payload: FewShotApplyRequest,
) -> Dict[str, Any]:
    image = get_image(pid, iid)
    if _active_jobs_for(pid, iid):
        raise HTTPException(status_code=409, detail="该影像有任务正在执行，请稍后再试")
    try:
        image, result_dir, summary, parent_model = _current_result_context(pid, iid)
        calibration_result = extract_measured_samples(
            result_dir,
            summary,
            [sample.model_dump() for sample in payload.samples],
            unit=payload.unit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    pmeta = load_processed(store.image_dir(pid, iid) / "processed")
    if not pmeta:
        raise HTTPException(
            status_code=400,
            detail="当前结果对应的预处理数据缺失，请先重新预处理和基础反演",
        )
    try:
        derived = create_derived_model(
            parent_model,
            calibration_result,
            project_id=pid,
            image_id=iid,
            base_result_id=summary.get("id"),
            name=payload.name,
            result_dir=result_dir,
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"创建少样本校正模型失败：{exc}")

    assessment = _assess_image(pid, image, derived, pmeta)
    try:
        _require_compatible_assessment(image, assessment, payload.force)
    except ValueError as exc:
        try:
            delete_custom_model(derived["id"])
        except Exception:
            pass
        status_code = 400 if assessment.get("status") == "incompatible" else 409
        raise HTTPException(status_code=status_code, detail=str(exc))

    thresholds = summary.get("thresholds") or {}
    low = thresholds.get("low")
    high = thresholds.get("high")
    label = f"few-shot 微调 · {image.get('name')}"

    def worker(progress):
        result = _invert_one(
            pid,
            iid,
            derived["id"],
            None if low is None else float(low),
            None if high is None else float(high),
            False,
            payload.force,
            progress,
            prefix="[few-shot] ",
        )
        return {
            "image_id": iid,
            "model_id": derived["id"],
            "calibration": derived.get("few_shot"),
            "summary": result,
        }

    jid = jobs.submit(
        "few_shot_calibration",
        worker,
        project_id=pid,
        image_id=iid,
        label=label,
        extra={"image_ids": [iid], "parent_model_id": parent_model.get("id")},
    )
    derived_public = dict(derived)
    derived_public.pop("_dir", None)
    derived_public["available"] = True
    return {
        "job_id": jid,
        "label": label,
        "model": derived_public,
        "calibration": calibration_result,
        "preflight": assessment,
    }


def _invert_one(
    pid: str,
    iid: str,
    model_id: str,
    low: Optional[float],
    high: Optional[float],
    auto_preprocess: bool,
    force: bool,
    progress,
    prefix: str = "",
) -> Dict[str, Any]:
    image = store.get_image(pid, iid)
    source = _ensure_source(pid, image)
    pdir = store.image_dir(pid, iid) / "processed"
    pmeta = load_processed(pdir)
    try:
        model = get_model(model_id)
    except ModelNotFound:
        raise ValueError("所选模型不存在或已被删除")
    if not model.get("available"):
        raise ValueError("模型文件缺失，请重新导入模型")

    if not pmeta:
        if not auto_preprocess:
            raise ValueError("该影像尚未完成预处理，请先执行预处理或勾选自动预处理")
        progress(2, "自动预处理", f"{prefix}尚未预处理，正在自动执行默认流程…")

        def pre_progress(p, s, m):
            progress(2 + int(p * 0.26), "自动预处理", f"{prefix}{m}")

        pmeta = run_preprocess(source, pdir, config=None, progress=pre_progress)
        store.update_image(
            pid,
            iid,
            {
                "status": "preprocessed",
                "preprocess": _preprocess_summary(pmeta),
                "preprocess_at": pmeta.get("created_at"),
            },
        )
        store.append_log(pid, f"影像「{image.get('name')}」自动预处理完成", source="inversion")

    assessment = _assess_image(pid, image, model, pmeta)
    _require_compatible_assessment(image, assessment, force)
    ignored_warnings = assessment.get("warnings", []) if force else []

    progress(32, "模型准备", f"{prefix}正在加载模型「{model.get('name')}」…")
    store.append_log(
        pid,
        f"开始反演「{image.get('name')}」，模型「{model.get('name')}」",
        source="inversion",
    )

    def inv_progress(p, s, m):
        progress(32 + int(p * 0.68), s, f"{prefix}{m}")

    summary = run_inversion(
        image,
        pdir,
        model,
        store.image_dir(pid, iid) / "results",
        low=low,
        high=high,
        progress=inv_progress,
        compatibility=assessment,
        force_used=force,
        ignored_warnings=ignored_warnings,
    )
    store.update_image(
        pid,
        iid,
        {
            "status": "inverted",
            "result": compact_summary(summary),
            "result_at": summary.get("created_at"),
        },
    )
    store.append_result_summary(pid, compact_summary(summary))
    store.append_log(
        pid,
        f"反演完成「{image.get('name')}」：均值 {summary['stats']['mean']} {summary.get('unit') or '待确认'}，"
        f"变异系数 {summary['stats']['cv']}%，模型「{model.get('name')}」",
        source="inversion",
    )
    # 用最新的分级专题图更新项目封面
    preview = store.image_dir(pid, iid) / "results" / "previews" / "classified.png"
    if preview.exists():
        try:
            from ..services import raster as R
            R.save_cover_from_png(preview, store.project_dir(pid) / "cover.png")
        except Exception:
            pass
    return summary


@router.post("/{iid}/inversions")
def start_inversion(pid: str, iid: str, payload: InversionRequest) -> Dict[str, Any]:
    image = get_image(pid, iid)
    if _active_jobs_for(pid, iid):
        raise HTTPException(status_code=409, detail="该影像有任务正在执行，请稍后再试")
    try:
        model = get_model(payload.model_id)
    except ModelNotFound:
        raise HTTPException(status_code=404, detail="所选模型不存在")
    if payload.low is not None and payload.high is not None and payload.high <= payload.low:
        raise HTTPException(status_code=400, detail="高氮阈值必须大于低氮阈值")
    pmeta = load_processed(store.image_dir(pid, iid) / "processed")
    if not pmeta and not payload.auto_preprocess:
        raise HTTPException(
            status_code=400,
            detail="该影像尚未完成预处理，请先执行预处理或勾选自动预处理",
        )
    assessment = _assess_image(pid, image, model, pmeta)
    try:
        _require_compatible_assessment(image, assessment, payload.force)
    except ValueError as exc:
        status_code = 400 if assessment.get("status") == "incompatible" else 409
        raise HTTPException(status_code=status_code, detail=str(exc))
    label = f"反演 · {image.get('name')}"

    def worker(progress):
        summary = _invert_one(
            pid,
            iid,
            payload.model_id,
            payload.low,
            payload.high,
            payload.auto_preprocess,
            payload.force,
            progress,
        )
        return {
            "image_id": iid,
            "summary": summary,
        }

    jid = jobs.submit(
        "inversion",
        worker,
        project_id=pid,
        image_id=iid,
        label=label,
        extra={"image_ids": [iid]},
    )
    return {"job_id": jid, "label": label, "preflight": assessment}


@batch_router.post("/{pid}/batch-inversions")
def start_batch_inversion(pid: str, payload: BatchInversionRequest) -> Dict[str, Any]:
    project = get_project(pid)
    image_map = {im["id"]: im for im in project.get("images", [])}
    missing = [iid for iid in payload.image_ids if iid not in image_map]
    if missing:
        raise HTTPException(status_code=404, detail=f"影像不存在：{', '.join(missing[:3])}")
    conflicts = [iid for iid in payload.image_ids if _active_jobs_for(pid, iid)]
    if conflicts:
        names = [image_map[iid].get("name", iid) for iid in conflicts[:3]]
        raise HTTPException(status_code=409, detail=f"以下影像有任务正在执行：{'、'.join(names)}")
    try:
        model = get_model(payload.model_id)
    except ModelNotFound:
        raise HTTPException(status_code=404, detail="所选模型不存在")
    if payload.low is not None and payload.high is not None and payload.high <= payload.low:
        raise HTTPException(status_code=400, detail="高氮阈值必须大于低氮阈值")
    preflight_rows = []
    for iid in payload.image_ids:
        image = image_map[iid]
        pmeta = load_processed(store.image_dir(pid, iid) / "processed")
        if not pmeta and not payload.auto_preprocess:
            raise HTTPException(
                status_code=400,
                detail=f"影像「{image.get('name')}」尚未完成预处理，请先处理或勾选自动预处理",
            )
        assessment = _assess_image(pid, image, model, pmeta)
        preflight_rows.append(
            {
                "image_id": iid,
                "image_name": image.get("name"),
                "assessment": assessment,
            }
        )
    incompatible = [
        row for row in preflight_rows if row["assessment"].get("status") == "incompatible"
    ]
    if incompatible:
        raise HTTPException(
            status_code=400,
            detail="；".join(
                _assessment_block_message(image_map[row["image_id"]], row["assessment"])
                for row in incompatible[:5]
            ),
        )
    warning_rows = [
        row
        for row in preflight_rows
        if row["assessment"].get("warnings") and not payload.force
    ]
    if warning_rows:
        raise HTTPException(
            status_code=409,
            detail="逐幅预检存在需确认警告："
            + "；".join(
                _assessment_block_message(image_map[row["image_id"]], row["assessment"])
                for row in warning_rows[:5]
            ),
        )
    total = len(payload.image_ids)
    label = f"批量反演 · {total} 幅影像"

    def worker(progress):
        results = []
        errors = []
        for index, iid in enumerate(payload.image_ids):
            image = store.get_image(pid, iid)
            base = index / total * 100.0
            span = 100.0 / total

            def sub_progress(p, s, m, base=base, span=span, index=index):
                progress(
                    int(base + p * span / 100.0),
                    s,
                    f"[{index + 1}/{total}] {m}",
                )

            try:
                summary = _invert_one(
                    pid,
                    iid,
                    payload.model_id,
                    payload.low,
                    payload.high,
                    payload.auto_preprocess,
                    payload.force,
                    sub_progress,
                    prefix=f"[{index + 1}/{total}] ",
                )
                results.append(compact_summary(summary))
            except Exception as exc:  # noqa: BLE001
                errors.append(
                    {"image_id": iid, "image_name": image.get("name"), "error": str(exc)}
                )
                store.append_log(
                    pid,
                    f"批量反演失败「{image.get('name')}」：{exc}",
                    level="error",
                    source="inversion",
                )
        if not results:
            raise ValueError("批量反演未产生任何结果：" + "；".join(e["error"] for e in errors[:3]))
        return {"results": results, "errors": errors, "total": total}

    jid = jobs.submit(
        "batch_inversion",
        worker,
        project_id=pid,
        label=label,
        extra={"image_ids": payload.image_ids},
    )
    return {
        "job_id": jid,
        "label": label,
        "count": total,
        "preflight": preflight_rows,
    }
