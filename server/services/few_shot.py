"""Few-shot post-hoc calibration for existing Nmass models.

The parent model remains unchanged.  A small measured sample set is used to
learn an offset or a conservatively shrunk affine correction.  The correction
is stored as a derived model with no duplicated binary model file.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
from affine import Affine
from rasterio.warp import transform as transform_coordinates

from ..core.config import CUSTOM_MODELS_DIR
from ..core.utils import new_id, now_iso, now_stamp, read_json, write_json


MIN_AFFINE_SAMPLES = 3
RECOMMENDED_SAMPLES = 5
MAX_SAMPLES = 200


def _json_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _round(value: Optional[float], digits: int = 6) -> Optional[float]:
    return None if value is None or not np.isfinite(float(value)) else round(float(value), digits)


def _metric_summary(measured: Sequence[float], predicted: Sequence[float]) -> Dict[str, Any]:
    y = np.asarray(measured, dtype=float)
    p = np.asarray(predicted, dtype=float)
    finite = np.isfinite(y) & np.isfinite(p)
    if not finite.any():
        return {
            "count": 0,
            "mae": None,
            "rmse": None,
            "bias": None,
            "r2": None,
        }
    y = y[finite]
    p = p[finite]
    residual = p - y
    mae = float(np.mean(np.abs(residual)))
    rmse = float(np.sqrt(np.mean(residual**2)))
    bias = float(np.mean(residual))
    if len(y) < 2:
        r2 = None
    else:
        total = float(np.sum((y - np.mean(y)) ** 2))
        r2 = None if total <= 1e-12 else float(1.0 - np.sum(residual**2) / total)
    return {
        "count": int(len(y)),
        "mae": _round(mae),
        "rmse": _round(rmse),
        "bias": _round(bias),
        "r2": _round(r2),
    }


def _fit_from_arrays(predicted: Sequence[float], measured: Sequence[float]) -> Dict[str, Any]:
    pred = np.asarray(predicted, dtype=float)
    truth = np.asarray(measured, dtype=float)
    n = int(len(pred))
    if n == 0 or len(truth) != n:
        raise ValueError("校正样点不能为空，且预测值与实测值数量必须一致")
    if not (np.isfinite(pred).all() and np.isfinite(truth).all()):
        raise ValueError("校正样点包含 NaN 或 Inf")

    variance = float(np.var(pred))
    mode = "offset"
    raw_slope: Optional[float] = None
    shrink: Optional[float] = None
    clamped = False

    if n < MIN_AFFINE_SAMPLES or variance <= 1e-10:
        slope = 1.0
        intercept = float(np.mean(truth - pred))
    else:
        raw_slope, raw_intercept = np.polyfit(pred, truth, 1)
        raw_slope = float(raw_slope)
        shrink = n / (n + (4.0 if n < RECOMMENDED_SAMPLES else 2.0))
        slope = 1.0 + (raw_slope - 1.0) * shrink
        clipped_slope = min(4.0, max(0.25, float(slope)))
        clamped = abs(clipped_slope - slope) > 1e-9
        slope = clipped_slope
        intercept = float(np.mean(truth - slope * pred))
        mode = "affine_shrunk"

    calibrated = slope * pred + intercept
    return {
        "mode": mode,
        "formula": "calibrated = slope * base_prediction + intercept",
        "slope": _round(slope),
        "intercept": _round(intercept),
        "sample_count": n,
        "raw_slope": _round(raw_slope),
        "shrink_factor": _round(shrink),
        "slope_clamped": bool(clamped),
        "before": _metric_summary(truth, pred),
        "after_in_sample": _metric_summary(truth, calibrated),
    }


def _leave_one_out_metrics(
    predicted: Sequence[float],
    measured: Sequence[float],
) -> Dict[str, Any]:
    pred = np.asarray(predicted, dtype=float)
    truth = np.asarray(measured, dtype=float)
    n = len(pred)
    if n < 2:
        return {
            "available": False,
            "reason": "至少 2 个有效样点才能计算留一误差",
            "mae": None,
            "rmse": None,
            "bias": None,
            "r2": None,
        }
    held_out = []
    for index in range(n):
        train_mask = np.arange(n) != index
        fit = _fit_from_arrays(pred[train_mask], truth[train_mask])
        held_out.append(fit["slope"] * pred[index] + fit["intercept"])
    metrics = _metric_summary(truth, held_out)
    return {"available": True, **metrics}


def fit_calibration(
    predicted: Sequence[float],
    measured: Sequence[float],
) -> Dict[str, Any]:
    """Fit and evaluate a conservative few-shot correction."""
    calibration = _fit_from_arrays(predicted, measured)
    calibration["leave_one_out"] = _leave_one_out_metrics(predicted, measured)
    warnings: List[str] = []
    n = int(calibration["sample_count"])
    if n < MIN_AFFINE_SAMPLES:
        warnings.append(
            f"当前只有 {n} 个有效样点，仅执行整体偏置校正；建议至少 5 个样点。"
        )
    elif n < RECOMMENDED_SAMPLES:
        warnings.append(
            f"当前有 {n} 个有效样点，已采用收缩线性校正；建议增加到至少 5 个样点。"
        )
    if calibration.get("slope_clamped"):
        warnings.append("原始拟合斜率超出保守范围，已限制在 0.25–4.0。")
    calibration["warnings"] = warnings
    calibration["enough_for_affine"] = n >= MIN_AFFINE_SAMPLES
    calibration["recommended_samples"] = RECOMMENDED_SAMPLES
    return calibration


def _coerce_sample(
    raw: Dict[str, Any],
    index: int,
    crs: Optional[str],
    transform: Optional[Sequence[float]],
) -> Tuple[Dict[str, Any], Optional[str]]:
    sample_id = str(raw.get("sample_id") or f"sample_{index + 1}")
    try:
        measured = float(raw["nmass"])
    except (KeyError, TypeError, ValueError):
        return {}, f"{sample_id}：nmass 必须为数值"
    coordinate_source = "pixel"
    original_coordinates: Dict[str, Any] = {}
    if raw.get("col") is not None and raw.get("row") is not None:
        try:
            col = int(raw["col"])
            row = int(raw["row"])
        except (TypeError, ValueError):
            return {}, f"{sample_id}：col、row 必须为整数"
        original_coordinates = {"col": col, "row": row}
    elif raw.get("lon") is not None and raw.get("lat") is not None:
        coordinate_source = "lonlat"
        if not crs or not transform or len(transform) < 6:
            return {}, f"{sample_id}：结果缺少坐标系或仿射变换，无法转换经纬度"
        try:
            lon = float(raw["lon"])
            lat = float(raw["lat"])
            projected_x, projected_y = transform_coordinates(
                "EPSG:4326", str(crs), [lon], [lat]
            )
            col_float, row_float = ~Affine(*[float(v) for v in transform[:6]]) @ (
                float(projected_x[0]),
                float(projected_y[0]),
            )
            col = int(math.floor(col_float))
            row = int(math.floor(row_float))
        except Exception:
            return {}, f"{sample_id}：经纬度无法转换为当前结果行列号"
        original_coordinates = {"lon": lon, "lat": lat}
    else:
        return {}, f"{sample_id}：请提供 col、row，或提供 lon、lat"
    if col < 0 or row < 0:
        return {}, f"{sample_id}：转换后的 col、row 必须为非负整数"
    if not np.isfinite(measured) or measured <= 0:
        return {}, f"{sample_id}：实测 Nmass 必须为有限正数"
    return {
        "sample_id": sample_id,
        "col": col,
        "row": row,
        "nmass": measured,
        "coordinate_source": coordinate_source,
        **original_coordinates,
    }, None


def extract_measured_samples(
    result_dir: str | Path,
    summary: Dict[str, Any],
    samples: Iterable[Dict[str, Any]],
    unit: Optional[str] = None,
) -> Dict[str, Any]:
    """Pair measured points with the current result raster."""
    result_dir = Path(result_dir)
    file_info = summary.get("files") or {}
    npy_name = file_info.get("npy") or "nmass_latest.npy"
    npy_path = result_dir / str(npy_name)
    if not npy_path.exists():
        raise ValueError("当前结果栅格缺失，请先重新反演")
    result_unit = str(summary.get("unit") or (summary.get("model") or {}).get("unit") or "待确认")
    if result_unit in ("", "待确认"):
        raise ValueError("当前结果缺少明确实测单位，不能进行少样本校正")
    requested_unit = str(unit or result_unit).strip()
    if requested_unit != result_unit:
        raise ValueError(
            f"实测数据单位“{requested_unit}”与当前结果单位“{result_unit}”不一致，"
            "请先统一为同一单位"
        )

    array = np.load(str(npy_path), mmap_mode="r")
    height, width = int(array.shape[0]), int(array.shape[1])
    accepted: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    seen: set[Tuple[int, int]] = set()
    raw_samples = list(samples)
    if not raw_samples:
        raise ValueError("请至少提供一个实测样点")
    if len(raw_samples) > MAX_SAMPLES:
        raise ValueError(f"样点数量过多，一次最多 {MAX_SAMPLES} 个")
    crs = summary.get("crs")
    transform = summary.get("transform")

    for index, raw in enumerate(raw_samples):
        sample, error = _coerce_sample(
            raw if isinstance(raw, dict) else {},
            index,
            crs=str(crs) if crs else None,
            transform=transform,
        )
        if error:
            rejected.append({"sample_id": f"sample_{index + 1}", "error": error})
            continue
        col, row = int(sample["col"]), int(sample["row"])
        if col >= width or row >= height:
            rejected.append(
                {
                    "sample_id": sample["sample_id"],
                    "error": f"坐标 ({col}, {row}) 超出当前结果范围 {width}×{height}",
                }
            )
            continue
        if (col, row) in seen:
            rejected.append(
                {
                    "sample_id": sample["sample_id"],
                    "error": f"坐标 ({col}, {row}) 重复",
                }
            )
            continue
        seen.add((col, row))
        base = float(array[row, col])
        if not np.isfinite(base):
            rejected.append(
                {
                    "sample_id": sample["sample_id"],
                    "error": f"坐标 ({col}, {row}) 位于无效像元或植被掩膜外",
                }
            )
            continue
        accepted.append(
            {
                **sample,
                "base_prediction": _round(base),
                "residual": _round(sample["nmass"] - base),
            }
        )

    if not accepted:
        details = "；".join(item["error"] for item in rejected[:5])
        raise ValueError("没有可与当前结果匹配的有效样点" + (f"：{details}" if details else ""))

    fp_payload = [
        [s["sample_id"], s["col"], s["row"], round(float(s["nmass"]), 8)]
        for s in accepted
    ]
    calibration = fit_calibration(
        [float(s["base_prediction"]) for s in accepted],
        [float(s["nmass"]) for s in accepted],
    )
    if rejected:
        calibration["warnings"].insert(
            0, f"有 {len(rejected)} 个样点未使用，请检查坐标、像元有效性或重复项。"
        )

    for sample in accepted:
        sample["calibrated_prediction"] = _round(
            calibration["slope"] * sample["base_prediction"] + calibration["intercept"]
        )
        sample["calibrated_residual"] = _round(sample["nmass"] - sample["calibrated_prediction"])

    return {
        "unit": result_unit,
        "parent_model": summary.get("model") or {},
        "samples": accepted,
        "rejected_samples": rejected,
        "sample_count": len(accepted),
        "sample_fingerprint": _json_sha256(fp_payload),
        "coordinate_system": "preprocessed_raster_col_row_zero_based",
        "calibration": calibration,
    }


def create_derived_model(
    parent_model: Dict[str, Any],
    calibration_result: Dict[str, Any],
    *,
    project_id: str,
    image_id: str,
    base_result_id: Optional[str] = None,
    name: Optional[str] = None,
    result_dir: Optional[str | Path] = None,
) -> Dict[str, Any]:
    """Persist a derived few-shot model without copying parent weights."""
    parent_id = str(parent_model.get("id") or "").strip()
    if not parent_id:
        raise ValueError("父模型缺少 ID")
    calibration = dict(calibration_result.get("calibration") or {})
    if not calibration:
        raise ValueError("校正结果为空")

    model_id = f"fewshot_{now_stamp()}_{new_id('fs', 2).split('_')[-1]}"
    dest = CUSTOM_MODELS_DIR / model_id
    dest.mkdir(parents=True, exist_ok=False)
    unit = str(calibration_result.get("unit") or parent_model.get("unit") or "待确认")
    loo = calibration.get("leave_one_out") or {}
    metric_type = (
        "few_shot_leave_one_out"
        if loo.get("available")
        else "few_shot_insufficient_samples"
    )
    metric_source = loo if loo.get("available") else {}
    model_name = str(name or "").strip() or f"{parent_model.get('name') or parent_id} · 实测校正"

    parent_input = copy.deepcopy(parent_model.get("input") or {})
    thresholds = copy.deepcopy(parent_model.get("thresholds") or {})
    thresholds["unit"] = unit
    meta = {
        "id": model_id,
        "name": model_name,
        "type": parent_model.get("type") or "ml",
        "model_kind": "few_shot_calibrated",
        "algorithm": f"{parent_model.get('algorithm') or '模型'} + 少样本线性后校准",
        "builtin": False,
        "demo": False,
        "synthetic": bool(parent_model.get("synthetic") or parent_model.get("demo")),
        "version": "1.0",
        "unit": unit,
        "unit_confirmed": unit not in ("", "待确认"),
        "crops": list(parent_model.get("crops") or []),
        "sensors": list(parent_model.get("sensors") or []),
        "training_data": (
            f"{parent_model.get('training_data') or '父模型训练数据'}；"
            f"随后使用 {calibration.get('sample_count')} 个用户实测样点进行后校准"
        ),
        "r2": metric_source.get("r2"),
        "rmse": metric_source.get("rmse"),
        "mae": metric_source.get("mae"),
        "bias": metric_source.get("bias"),
        "metric_type": metric_type,
        "description": (
            f"由父模型“{parent_model.get('name') or parent_id}”派生的少样本校正式模型。"
            f"当前校正为 {calibration.get('mode')}，使用 {calibration.get('sample_count')} 个实测样点。"
            "父模型权重未修改；该模型只在父模型预测上应用偏置/斜率校正。"
        ),
        "input": parent_input,
        "thresholds": thresholds,
        "parent_model_id": parent_id,
        "parent_model_name": parent_model.get("name"),
        "parent_file_sha256": parent_model.get("file_sha256"),
        "few_shot": {
            **calibration,
            "parent_model_id": parent_id,
            "parent_model_name": parent_model.get("name"),
            "parent_file_sha256": parent_model.get("file_sha256"),
            "project_id": project_id,
            "image_id": image_id,
            "base_result_id": base_result_id,
            "unit": unit,
            "coordinate_system": calibration_result.get("coordinate_system"),
            "sample_fingerprint": calibration_result.get("sample_fingerprint"),
            "samples": calibration_result.get("samples") or [],
            "rejected_samples": calibration_result.get("rejected_samples") or [],
            "created_at": now_iso(),
        },
        "tags": ["few-shot", "实测微调", "派生模型"],
        "created_at": now_iso(),
    }
    try:
        if result_dir is not None:
            snapshot = snapshot_base_result(result_dir, model_id)
            meta["base_result_snapshot"] = snapshot
            meta["few_shot"]["base_result_snapshot"] = snapshot
        write_json(dest / "meta.json", meta)
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return meta


def snapshot_base_result(
    result_dir: str | Path,
    calibration_id: str,
) -> Dict[str, Any]:
    """Copy the current result artifacts before a calibrated inversion."""
    result_dir = Path(result_dir)
    summary = read_json(result_dir / "latest.json")
    if not isinstance(summary, dict):
        raise ValueError("当前结果摘要不存在，无法建立校正基线")
    snapshot_dir = result_dir / "calibration_sources" / calibration_id
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    copied: List[str] = []
    candidates = [result_dir / "latest.json", result_dir / "nmass_latest.npy"]
    file_info = summary.get("files") or {}
    if file_info.get("tif"):
        candidates.append(result_dir / str(file_info["tif"]))
    preview_dir = result_dir / "previews"
    if preview_dir.exists():
        candidates.extend(sorted(preview_dir.glob("*.png")))
    for source in candidates:
        if not source.exists() or not source.is_file():
            continue
        relative = source.relative_to(result_dir)
        target = snapshot_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(relative.as_posix())
    return {
        "snapshot_dir": f"calibration_sources/{calibration_id}",
        "copied_files": copied,
        "base_result_id": summary.get("id"),
        "base_model": summary.get("model") or {},
    }
