"""Nmass 反演执行、统计分级与结果落盘。"""
from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import rasterio
from affine import Affine

from ..core.config import CLASS_COLORS
from ..core.utils import new_id, now_iso, now_stamp
from . import raster as R
from .models import get_model, load_estimator

ProgressCb = Optional[Callable[[int, str, str], None]]

CLASS_DEFS = [
    ("low", "低氮", CLASS_COLORS["low"], "需追肥"),
    ("suitable", "适宜", CLASS_COLORS["suitable"], "保持现状"),
    ("high", "高氮", CLASS_COLORS["high"], "控制施肥"),
]


def _progress(cb: ProgressCb, percent: int, stage: str, message: str) -> None:
    if cb:
        cb(int(percent), stage, message)


def _sha256_file(path: Path) -> Optional[str]:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _actual_unit(model: Dict[str, Any]) -> str:
    unit = str(model.get("unit") or "").strip()
    return unit or "待确认"


def _alignment_summary(
    source_wavelengths: Sequence[float],
    target_wavelengths: Sequence[float],
) -> Dict[str, Any]:
    src = sorted({float(v) for v in source_wavelengths if np.isfinite(float(v))})
    target = sorted({float(v) for v in target_wavelengths if np.isfinite(float(v))})
    exact = interpolated = edge = 0
    for value in target:
        if not src or value < src[0] or value > src[-1]:
            edge += 1
        elif np.any(np.abs(np.asarray(src) - value) <= 1e-6):
            exact += 1
        else:
            interpolated += 1
    total = max(1, len(target))
    return {
        "source_range": [src[0], src[-1]] if src else [],
        "target_range": [target[0], target[-1]] if target else [],
        "exact_bands": int(exact),
        "interpolated_bands": int(interpolated),
        "edge_filled_bands": int(edge),
        "edge_fill_ratio": round(edge / total, 5),
        "resampled": bool(interpolated or edge),
        "method": (
            "exact"
            if not (interpolated or edge)
            else "linear_interpolation_with_nearest_edge_fill"
        ),
    }


def _pixel_area_m2(transform: Sequence[float], crs: Optional[str], height: int) -> float:
    a, b, c, d, e, f = [float(v) for v in transform[:6]]
    det = abs(a * e - b * d)
    if det <= 0:
        return 0.0
    if crs and ("4326" in crs or "longlat" in crs.lower() or "geographic" in crs.lower()):
        lat = float(f) + float(e) * height / 2.0
        m_lat = 111320.0
        m_lon = 111320.0 * max(0.05, math.cos(math.radians(lat)))
        return det * m_lat * m_lon
    return det


def _compute_vi_feature(
    name: str,
    bands: Dict[str, float],
    stack: np.ndarray,
    wavelengths: Sequence[float],
) -> np.ndarray:
    wl = [float(v) for v in wavelengths]
    if not wl or len(wl) != stack.shape[0]:
        raise ValueError("预处理结果缺少波段波长信息，无法按模型要求计算植被指数")

    def idx(target: float, tol: float = 120.0) -> int:
        i = R.nearest_band_index(wl, target, tol)
        if i is None:
            raise ValueError(
                f"模型需要 {target:g} nm 附近的波段，当前影像波段范围为 "
                f"{min(wl):g}–{max(wl):g} nm，请更换模型或调整预处理波段"
            )
        return i

    name_u = name.upper()
    nir = stack[idx(float(bands.get("nir", 840)))]
    if name_u == "NDVI":
        red = stack[idx(float(bands.get("red", 670)))]
        denom = nir + red
        return np.where(np.abs(denom) > 1e-6, (nir - red) / denom, np.nan).astype(np.float32)
    if name_u == "NDRE":
        re = stack[idx(float(bands.get("red_edge", 730)))]
        denom = nir + re
        return np.where(np.abs(denom) > 1e-6, (nir - re) / denom, np.nan).astype(np.float32)
    if name_u == "CIREDGE":
        re = stack[idx(float(bands.get("red_edge", 730)))]
        return np.where(np.abs(re) > 1e-6, nir / re - 1.0, np.nan).astype(np.float32)
    if name_u == "GNDVI":
        green = stack[idx(float(bands.get("green", 550)))]
        denom = nir + green
        return np.where(np.abs(denom) > 1e-6, (nir - green) / denom, np.nan).astype(np.float32)
    raise ValueError(f"模型使用了暂不支持的植被指数：{name}")


def _predict_full(
    model: Dict[str, Any],
    stack: np.ndarray,
    wavelengths: Sequence[float],
    mask: np.ndarray,
    progress: ProgressCb = None,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """对整幅预处理影像预测 Nmass，返回 (结果数组, 附加信息)。"""
    n_bands, height, width = stack.shape
    input_cfg = model.get("input") or {}
    input_type = input_cfg.get("type")
    out = np.full((height, width), np.nan, dtype=np.float32)
    info: Dict[str, Any] = {"input_type": input_type}

    chunk_rows = max(8, min(128, int(2_000_000 / max(width, 1))))
    started = time.time()

    if input_type == "vi":
        features = input_cfg.get("features")
        if not features and input_cfg.get("index"):
            features = [
                {
                    "index": input_cfg.get("index"),
                    "bands": input_cfg.get("bands", {}),
                }
            ]
        if not features:
            raise ValueError("植被指数模型缺少指数定义")
        coef = [float(v) for v in (input_cfg.get("coef") or [])]
        intercept = float(input_cfg.get("intercept", 0.0))
        if len(coef) != len(features):
            raise ValueError("模型系数数量与指数数量不一致")
        feature_stack = []
        for feat in features:
            feature_stack.append(
                _compute_vi_feature(
                    str(feat.get("index")), feat.get("bands") or {}, stack, wavelengths
                )
            )
        info["features"] = [f.get("index") for f in features]
        for y0 in range(0, height, chunk_rows):
            y1 = min(height, y0 + chunk_rows)
            block = np.zeros((y1 - y0, width), dtype=np.float32)
            for i, farr in enumerate(feature_stack):
                block += farr[y0:y1] * coef[i]
            block += intercept
            out[y0:y1] = np.clip(block, 0.0, 60.0)
            pct = 10 + int(78 * y1 / max(height, 1))
            _progress(progress, pct, "逐块反演", f"正在反演第 {y1}/{height} 行…")
    elif input_type == "spectra":
        target_wl = [float(v) for v in (input_cfg.get("wavelengths") or [])]
        if len(target_wl) < 2:
            raise ValueError("模型缺少训练波段波长信息")
        src_wl = [float(v) for v in wavelengths]
        if len(src_wl) == len(target_wl) and np.allclose(src_wl, target_wl, atol=1e-3):
            aligned = stack
            info["alignment"] = {
                "resampled": False,
                "bands": len(target_wl),
                "source_band_count": len(src_wl),
                "exact_bands": len(target_wl),
                "interpolated_bands": 0,
                "edge_filled_bands": 0,
                "edge_fill_ratio": 0.0,
                "edge_filled": False,
                "source_range": [min(src_wl), max(src_wl)] if src_wl else [],
                "target_range": [min(target_wl), max(target_wl)] if target_wl else [],
                "method": "exact",
            }
        else:
            aligned, align_info = R.resample_stack(stack, src_wl, target_wl)
            info["alignment"] = {
                "resampled": bool(align_info.get("resampled", True)),
                "bands": len(target_wl),
                **align_info,
            }
        estimator = load_estimator(model)
        info["estimator"] = type(estimator).__name__
        n_features = getattr(estimator, "n_features_in_", None)
        if n_features is not None and int(n_features) != aligned.shape[0]:
            raise ValueError(
                f"模型期望 {int(n_features)} 个波段输入，当前数据为 {aligned.shape[0]} 个波段"
            )
        for y0 in range(0, height, chunk_rows):
            y1 = min(height, y0 + chunk_rows)
            block_mask = mask[y0:y1]
            if block_mask.any():
                X = aligned[:, y0:y1, :].reshape(aligned.shape[0], -1).T
                X = X[block_mask.ravel()]
                X = np.nan_to_num(X, nan=0.0)
                pred = np.asarray(estimator.predict(X)).reshape(-1)
                block = np.full((y1 - y0, width), np.nan, dtype=np.float32)
                block[block_mask] = np.clip(pred, 0.0, 60.0)
                out[y0:y1] = block
            pct = 10 + int(78 * y1 / max(height, 1))
            _progress(progress, pct, "逐块反演", f"正在反演第 {y1}/{height} 行…")
    else:
        raise ValueError("暂不支持该模型输入类型，请使用植被指数或光谱机器学习模型")

    out[~mask] = np.nan
    info["duration_sec"] = round(time.time() - started, 2)
    return out, info


def _class_stats(values: np.ndarray, low: float, high: float, pixel_area: float) -> List[Dict[str, Any]]:
    total = int(values.size)
    results = []
    for key, name, color, action in CLASS_DEFS:
        if key == "low":
            sel = values < low
        elif key == "suitable":
            sel = (values >= low) & (values <= high)
        else:
            sel = values > high
        count = int(sel.sum())
        if key == "low":
            rmin, rmax = None, low
        elif key == "suitable":
            rmin, rmax = low, high
        else:
            rmin, rmax = high, None
        results.append(
            {
                "key": key,
                "name": name,
                "color": color,
                "action": action,
                "count": count,
                "ratio": round(count / total, 5) if total else 0.0,
                "area_ha": round(count * pixel_area / 10000.0, 4),
                "area_mu": round(count * pixel_area / 666.6667, 2),
                "range": {"min": rmin, "max": rmax},
            }
        )
    return results


def _class_array(values: np.ndarray, low: float, high: float) -> np.ndarray:
    classes = np.zeros(values.shape, dtype=np.uint8)
    classes[(values >= low) & (values <= high)] = 1
    classes[values > high] = 2
    return classes


def _save_previews(
    out: np.ndarray,
    mask: np.ndarray,
    low: float,
    high: float,
    out_dir: Path,
) -> Tuple[Dict[str, str], Dict[str, float]]:
    preview_dir = out_dir / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    height, width = out.shape
    step = max(1, int(math.ceil(max(height, width) / 1400.0)))
    vals = out[::step, ::step]
    m = mask[::step, ::step]
    finite = np.isfinite(vals) & m
    if not finite.any():
        raise ValueError("反演结果没有有效像元，无法生成专题图")

    p2, p98 = np.percentile(vals[finite], [2, 98])
    display_min, display_max = float(p2), float(p98)
    if display_max <= display_min:
        display_min, display_max = float(low - 2), float(high + 2)

    rgba = np.zeros(vals.shape + (4,), dtype=np.uint8)
    class_idx = _class_array(np.nan_to_num(vals, nan=-999), low, high)
    for i, (_, _, color, _) in enumerate(CLASS_DEFS):
        sel = finite & (class_idx == i)
        rgba[sel] = (*R._hex_to_rgb(color), 255)  # noqa: SLF001
    # 掩膜区使用深灰绿，避免在深色画布上形成纯黑空洞
    rgba[~finite] = (28, 39, 32, 255)

    cont = R.colorize(
        vals,
        mask=finite,
        cmap="nmass",
        vmin=display_min,
        vmax=display_max,
    )
    # 背景保持深色，突出有效区域
    cont[..., 0][~finite] = 18
    cont[..., 1][~finite] = 26
    cont[..., 2][~finite] = 22
    cont[..., 3][~finite] = 255

    R.save_rgba_png(rgba, preview_dir / "classified.png")
    R.save_rgba_png(cont, preview_dir / "continuous.png")
    return (
        {
            "classified": "previews/classified.png",
            "continuous": "previews/continuous.png",
        },
        {"min": display_min, "max": display_max},
    )


def run_inversion(
    image_meta: Dict[str, Any],
    processed_dir: str | Path,
    model: Dict[str, Any],
    out_dir: str | Path,
    low: Optional[float] = None,
    high: Optional[float] = None,
    progress: ProgressCb = None,
    compatibility: Optional[Dict[str, Any]] = None,
    force_used: bool = False,
    ignored_warnings: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """执行反演并保存结果，返回结果摘要（含相对文件路径）。"""
    processed_dir = Path(processed_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()

    meta_path = processed_dir / "meta.json"
    from ..core.utils import read_json

    pmeta = read_json(meta_path)
    if not isinstance(pmeta, dict):
        raise ValueError("尚未完成数据预处理，请先执行预处理")

    thresholds = model.get("thresholds") or {}
    default_low = thresholds.get("low")
    default_high = thresholds.get("high")
    low = float(low if low is not None else (default_low if default_low is not None else 22.0))
    high = float(high if high is not None else (default_high if default_high is not None else 28.0))
    unit = _actual_unit(model)
    if high <= low:
        raise ValueError("高氮阈值必须大于低氮阈值")

    _progress(progress, 4, "加载数据", "正在加载预处理结果…")
    from .preprocess import load_stack
    stack, mask = load_stack(processed_dir)
    if stack.ndim != 3 or mask.shape != stack.shape[1:]:
        raise ValueError("预处理结果格式异常，请重新预处理")
    wavelengths = [float(v) for v in (pmeta.get("wavelengths") or [])]
    if stack.shape[0] != len(wavelengths):
        raise ValueError("预处理结果波段数与波长信息不一致，请重新预处理")
    if not mask.any():
        raise ValueError("植被掩膜内没有有效像元，无法反演")

    _progress(progress, 8, "模型准备", f"正在加载模型「{model.get('name')}」…")
    few_shot = model.get("few_shot") or {}
    prediction_model = model
    calibration_chain: List[Dict[str, Any]] = []
    resolved_ids = set()
    while prediction_model.get("few_shot"):
        current_calibration = dict(prediction_model.get("few_shot") or {})
        parent_id = (
            prediction_model.get("parent_model_id")
            or current_calibration.get("parent_model_id")
        )
        if not parent_id or str(parent_id) in resolved_ids:
            raise ValueError("少样本派生模型的父模型链无效或存在循环引用")
        resolved_ids.add(str(parent_id))
        try:
            parent_model = get_model(str(parent_id))
        except Exception as exc:
            raise ValueError(f"派生模型的父模型不可用：{parent_id}") from exc
        if not parent_model.get("available"):
            raise ValueError(f"派生模型的父模型文件缺失：{parent_id}")
        calibration_chain.append(current_calibration)
        prediction_model = parent_model
    out, pred_info = _predict_full(prediction_model, stack, wavelengths, mask, progress)
    for current_calibration in reversed(calibration_chain):
        slope = float(current_calibration.get("slope", 1.0))
        intercept = float(current_calibration.get("intercept", 0.0))
        finite_prediction = np.isfinite(out)
        out[finite_prediction] = np.clip(
            slope * out[finite_prediction] + intercept, 0.0, 60.0
        )
    if calibration_chain:
        pred_info["few_shot_calibration"] = {
            "parent_model_id": model.get("parent_model_id"),
            "mode": few_shot.get("mode"),
            "slope": few_shot.get("slope"),
            "intercept": few_shot.get("intercept"),
            "sample_count": few_shot.get("sample_count"),
            "unit": few_shot.get("unit"),
        }
        pred_info["few_shot_calibration_chain"] = [
            {
                "parent_model_id": item.get("parent_model_id"),
                "mode": item.get("mode"),
                "slope": item.get("slope"),
                "intercept": item.get("intercept"),
                "sample_count": item.get("sample_count"),
            }
            for item in reversed(calibration_chain)
        ]

    _progress(progress, 90, "统计分级", "正在统计氮素分布与分级面积…")
    finite = np.isfinite(out) & mask
    values = out[finite]
    if values.size == 0:
        raise ValueError("反演结果没有有效像元")
    transform = pmeta.get("transform") or [1, 0, 0, 0, -1, 0]
    pixel_area = _pixel_area_m2(transform, pmeta.get("crs"), out.shape[0])
    total_pixels = int(out.size)
    valid_count = int(values.size)

    mean = float(np.mean(values))
    std = float(np.std(values))
    stats = {
        "count": valid_count,
        "total_count": total_pixels,
        "valid_ratio": round(valid_count / max(total_pixels, 1), 5),
        "mean": round(mean, 3),
        "std": round(std, 3),
        "cv": round(std / mean * 100.0, 2) if mean else None,
        "min": round(float(np.min(values)), 3),
        "max": round(float(np.max(values)), 3),
        "p5": round(float(np.percentile(values, 5)), 3),
        "p25": round(float(np.percentile(values, 25)), 3),
        "p50": round(float(np.percentile(values, 50)), 3),
        "p75": round(float(np.percentile(values, 75)), 3),
        "p95": round(float(np.percentile(values, 95)), 3),
    }

    class_stats = _class_stats(values, low, high, pixel_area)

    hmin, hmax = float(np.min(values)), float(np.max(values))
    if hmax <= hmin:
        hmax = hmin + 1.0
    counts, edges = np.histogram(values, bins=30, range=(hmin, hmax))
    histogram = {
        "counts": [int(c) for c in counts],
        "edges": [round(float(e), 3) for e in edges],
        "mean": round(mean, 3),
        "thresholds": [low, high],
    }

    _progress(progress, 94, "保存结果", "正在写出 GeoTIFF 与专题图…")
    stamp = now_stamp()
    tif_name = f"nmass_{stamp}.tif"
    tif_path = out_dir / tif_name
    transform_affine = Affine(*[float(v) for v in transform[:6]])
    profile = {
        "driver": "GTiff",
        "height": out.shape[0],
        "width": out.shape[1],
        "count": 1,
        "dtype": "float32",
        "nodata": float("nan"),
        "transform": transform_affine,
        "compress": "deflate",
        "predictor": 3,
    }
    if pmeta.get("crs"):
        profile["crs"] = pmeta["crs"]
    model_dir = Path(str(model.get("_dir") or ""))
    model_file = model_dir / str(model.get("file") or "") if model.get("file") else None
    file_sha256 = str(model.get("file_sha256") or "").strip()
    if not file_sha256 and model_file is not None:
        file_sha256 = _sha256_file(model_file) or ""
    training_wavelengths = [
        float(v) for v in ((model.get("input") or {}).get("wavelengths") or [])
    ]
    processed_alignment = (pred_info.get("alignment") or {})
    source_band_range = [
        float(v) for v in (pmeta.get("source_wavelengths") or [])
    ]
    processed_band_range = [float(v) for v in wavelengths]
    preprocessing_trace = {
        "processed_at": pmeta.get("created_at"),
        "config": pmeta.get("config") or {},
        "config_sha256": _json_sha256(pmeta.get("config") or {}),
        "quality_level": (pmeta.get("quality") or {}).get("level"),
        "mask_ratio": (pmeta.get("mask") or {}).get("ratio"),
        "source_band_count": pmeta.get("source_bands"),
        "source_wavelengths": source_band_range,
        "processed_wavelengths": processed_band_range,
        "source_range": [min(source_band_range), max(source_band_range)] if source_band_range else [],
        "processed_range": [min(processed_band_range), max(processed_band_range)] if processed_band_range else [],
        "preprocess_alignment": pmeta.get("alignment") or {},
        "removed_bands": pmeta.get("removed_bands") or [],
        "smoothing_applied": bool(pmeta.get("smoothing_applied")),
        "pipeline": (
            "反射率转换 → 坏波段剔除 → Savitzky-Golay 平滑 → 波段对齐 → "
            "植被/云掩膜 → 植被指数 → 质量检查"
        ),
    }
    full_source_wavelengths = source_band_range or processed_band_range
    trace_alignment = _alignment_summary(
        full_source_wavelengths, training_wavelengths or processed_band_range
    )
    trace_alignment["prediction_alignment"] = processed_alignment
    trace = {
        "model": {
            "id": model.get("id"),
            "name": model.get("name"),
            "version": model.get("version"),
            "algorithm": model.get("algorithm"),
            "model_kind": model.get("model_kind"),
            "parent_model_id": model.get("parent_model_id"),
            "parent_model_name": model.get("parent_model_name"),
            "parent_file_sha256": model.get("parent_file_sha256"),
            "few_shot": few_shot or None,
            "file": model.get("file"),
            "file_sha256": file_sha256,
            "training_wavelengths": training_wavelengths,
            "input_type": (model.get("input") or {}).get("type"),
            "synthetic": bool((model.get("dataset") or {}).get("synthetic", model.get("demo"))),
        },
        "alignment": trace_alignment,
        "preprocessing": preprocessing_trace,
        "compatibility": compatibility or {},
        "force_used": bool(force_used),
        "ignored_warnings": [str(v) for v in (ignored_warnings or [])],
        "unit": unit,
        "threshold_unit": unit,
    }
    with rasterio.open(tif_path, "w", **profile) as dst:
        dst.write(out.astype(np.float32), 1)
        dst.update_tags(
            MODEL=str(model.get("name") or ""),
            MODEL_ID=str(model.get("id") or ""),
            MODEL_VERSION=str(model.get("version") or ""),
            MODEL_SHA256=file_sha256,
            NITROGEN_UNIT=unit,
            LOW_THRESHOLD=str(low),
            HIGH_THRESHOLD=str(high),
            SOFTWARE="FreeFarmNRS V1.0",
        )

    np.save(out_dir / "nmass_latest.npy", out.astype(np.float32))
    previews, display_range = _save_previews(out, mask, low, high, out_dir)

    published_model = {
        k: model.get(k)
        for k in (
            "id",
            "name",
            "algorithm",
            "type",
            "crops",
            "sensors",
            "r2",
            "rmse",
            "unit",
            "training_data",
            "version",
            "demo",
            "file_sha256",
            "metric_type",
            "limitations",
            "dataset",
            "structure",
            "model_kind",
            "parent_model_id",
            "parent_model_name",
            "parent_file_sha256",
            "few_shot",
        )
        if k in model
    }
    published_model["file_sha256"] = file_sha256

    summary = {
        "id": new_id("res", 4),
        "image_id": image_meta.get("id"),
        "image_name": image_meta.get("name"),
        "flight_date": image_meta.get("flight_date") or "",
        "model": published_model,
        "created_at": now_iso(),
        "thresholds": {"low": low, "high": high, "unit": unit},
        "classes": [
            {
                "key": key,
                "name": name,
                "color": color,
                "action": action,
                "min": None if key == "low" else (high if key == "high" else low),
                "max": None if key == "high" else (low if key == "low" else high),
            }
            for key, name, color, action in CLASS_DEFS
        ],
        "stats": stats,
        "class_stats": class_stats,
        "histogram": histogram,
        "display_range": display_range,
        "area": {
            "pixel_area_m2": round(pixel_area, 6),
            "valid_ha": round(valid_count * pixel_area / 10000.0, 4),
            "valid_mu": round(valid_count * pixel_area / 666.6667, 2),
            "total_ha": round(total_pixels * pixel_area / 10000.0, 4),
            "total_mu": round(total_pixels * pixel_area / 666.6667, 2),
        },
        "width": int(out.shape[1]),
        "height": int(out.shape[0]),
        "crs": pmeta.get("crs"),
        "transform": transform,
        "wavelengths": wavelengths,
        "prediction": pred_info,
        "trace": trace,
        "files": {
            "tif": tif_name,
            "npy": "nmass_latest.npy",
            **previews,
        },
        "duration_sec": round(time.time() - started, 2),
        "unit": unit,
    }
    from ..core.utils import write_json

    write_json(out_dir / "latest.json", summary)
    _progress(progress, 99, "完成", "反演结果已保存")
    return summary


def compact_summary(summary: Dict[str, Any]) -> Dict[str, Any]:
    """用于项目级历史记录（覆盖式保存 + 保留统计摘要）。"""
    return {
        "id": summary.get("id"),
        "image_id": summary.get("image_id"),
        "image_name": summary.get("image_name"),
        "flight_date": summary.get("flight_date"),
        "created_at": summary.get("created_at"),
        "unit": summary.get("unit"),
        "model": {
            "id": (summary.get("model") or {}).get("id"),
            "name": (summary.get("model") or {}).get("name"),
            "version": (summary.get("model") or {}).get("version"),
            "file_sha256": (summary.get("model") or {}).get("file_sha256"),
            "metric_type": (summary.get("model") or {}).get("metric_type"),
            "model_kind": (summary.get("model") or {}).get("model_kind"),
            "parent_model_id": (summary.get("model") or {}).get("parent_model_id"),
            "few_shot": {
                key: ((summary.get("model") or {}).get("few_shot") or {}).get(key)
                for key in (
                    "parent_model_id",
                    "mode",
                    "slope",
                    "intercept",
                    "sample_count",
                    "unit",
                    "sample_fingerprint",
                    "after_in_sample",
                    "leave_one_out",
                )
                if key in ((summary.get("model") or {}).get("few_shot") or {})
            }
            or None,
        },
        "thresholds": summary.get("thresholds"),
        "stats": summary.get("stats"),
        "class_stats": summary.get("class_stats"),
        "area": summary.get("area"),
        "trace": {
            "alignment": (summary.get("trace") or {}).get("alignment"),
            "force_used": (summary.get("trace") or {}).get("force_used"),
            "ignored_warnings": (summary.get("trace") or {}).get("ignored_warnings"),
            "preprocessing": {
                "config_sha256": (
                    ((summary.get("trace") or {}).get("preprocessing") or {}).get("config_sha256")
                ),
                "quality_level": (
                    ((summary.get("trace") or {}).get("preprocessing") or {}).get("quality_level")
                ),
            },
        },
    }
