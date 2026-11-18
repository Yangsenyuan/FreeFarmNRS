"""集中式模型适用性预检。

这里的结果只描述当前影像、预处理状态与模型输入之间的可执行性和风险。
它不重新评价模型精度，也不把元数据缺失推断成匹配。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

EDGE_FILL_WARNING_RATIO = 0.20
SUPPORTED_INPUT_TYPES = {"spectra", "vi"}


def _number_list(value: Any) -> List[float]:
    if value is None:
        return []
    if isinstance(value, (int, float, np.integer, np.floating)):
        return [float(value)]
    if isinstance(value, str):
        import re

        parts = re.split(r"[,，;\s]+", value.strip())
        value = [part for part in parts if part]
    try:
        return [float(v) for v in value]
    except (TypeError, ValueError):
        return []


def _source_wavelengths(
    image: Dict[str, Any], processed_meta: Optional[Dict[str, Any]]
) -> Tuple[List[float], str]:
    image_wl = _number_list(image.get("wavelengths"))
    band_count = int(image.get("bands") or 0)
    if image_wl and (not band_count or len(image_wl) == band_count):
        return sorted(image_wl), "image_metadata"
    if processed_meta:
        source_wl = _number_list(processed_meta.get("source_wavelengths"))
        if source_wl:
            return sorted(source_wl), "processed_source_metadata"
        processed_wl = _number_list(processed_meta.get("wavelengths"))
        if processed_wl:
            return sorted(processed_wl), "processed_wavelengths"
    return [], "missing"


def _model_targets(model: Dict[str, Any]) -> Tuple[List[float], int, List[str], str]:
    input_cfg = model.get("input") or {}
    input_type = str(input_cfg.get("type") or "").strip()
    if input_type == "spectra":
        wavelengths = sorted(set(_number_list(input_cfg.get("wavelengths"))))
        return wavelengths, len(_number_list(input_cfg.get("wavelengths"))), [], input_type

    features = input_cfg.get("features") or []
    if not features and input_cfg.get("index"):
        features = [
            {
                "index": input_cfg.get("index"),
                "bands": input_cfg.get("bands") or {},
            }
        ]
    target_values: List[float] = []
    feature_names: List[str] = []
    for feature in features:
        feature_names.append(str(feature.get("index") or "VI"))
        for value in (feature.get("bands") or {}).values():
            nums = _number_list(value)
            if nums:
                target_values.append(nums[0])
    return sorted(set(target_values)), len(features), feature_names, input_type


def _categorize_targets(
    source: Sequence[float],
    target: Sequence[float],
    input_type: str,
    tolerance: float = 120.0,
) -> Tuple[int, int, int]:
    if not source or not target:
        return 0, 0, 0
    src = np.asarray(sorted(source), dtype=float)
    exact = 0
    interpolated = 0
    edge = 0
    for target_value in target:
        if input_type == "vi":
            if float(np.min(np.abs(src - target_value))) <= tolerance:
                exact += 1
            else:
                edge += 1
            continue
        if np.any(np.abs(src - target_value) <= 1e-6):
            exact += 1
        elif src[0] < target_value < src[-1]:
            interpolated += 1
        else:
            edge += 1
    return exact, interpolated, edge


def _norm(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "")


def _crop_match(image: Dict[str, Any], model: Dict[str, Any]) -> Tuple[str, str]:
    crops = [str(v).strip() for v in (model.get("crops") or []) if str(v).strip()]
    current = (
        image.get("project_crop")
        or image.get("crop")
        or image.get("crop_name")
        or image.get("metadata", {}).get("crop")
    )
    crop_text = str(current or "").strip()
    if not crops or not crop_text:
        return "unknown", "项目作物或模型适用作物信息不足"
    current_norm = _norm(crop_text)
    for crop in crops:
        candidate = _norm(crop)
        if candidate and (candidate in current_norm or current_norm in candidate):
            return "match", ""
    return "mismatch", f"模型适用作物为 {'、'.join(crops)}，当前项目作物为 {crop_text}"


def _sensor_match(
    image: Dict[str, Any],
    model: Dict[str, Any],
    required_bands: int,
) -> Tuple[str, str, str]:
    sensors = [str(v).strip() for v in (model.get("sensors") or []) if str(v).strip()]
    image_sensor = (
        image.get("sensor")
        or image.get("sensor_name")
        or image.get("instrument")
        or (image.get("metadata") or {}).get("sensor_name")
    )
    sensor_text = str(image_sensor or "").strip()
    image_bands = int(image.get("bands") or 0)
    available_hyperspectral = image_bands > 10
    available_multispectral = 0 < image_bands <= 10

    if not sensors or not sensor_text:
        if sensors and not sensor_text:
            generic = " ".join(_norm(v) for v in sensors)
            if "五波段" in generic and image_bands and image_bands != 5:
                return (
                    "mismatch",
                    "strong",
                    f"模型要求五波段（475/560/668/717/840 nm），当前影像为 {image_bands} 波段",
                )
        return "unknown", "none", "传感器类型信息不足，不能判定为匹配"

    sensor_norm = _norm(sensor_text)
    sensor_joined = " ".join(_norm(v) for v in sensors)
    if any(
        _norm(item) and (_norm(item) in sensor_norm or sensor_norm in _norm(item))
        for item in sensors
    ):
        return "match", "strong", ""
    if "五波段" in sensor_joined and image_bands and image_bands != 5:
        return (
            "mismatch",
            "strong",
            f"模型要求五波段，当前影像为 {image_bands} 波段",
        )
    if "高光谱" in sensor_joined and available_multispectral:
        return "mismatch", "strong", "模型面向高光谱数据，当前影像为多光谱数据"
    if "多光谱" in sensor_joined and available_multispectral:
        return "match", "medium", ""
    if "高光谱" in sensor_joined and available_hyperspectral:
        return "match", "medium", ""
    if "多光谱" in sensor_joined and available_hyperspectral:
        return "unknown", "weak", "模型面向多光谱，高光谱数据需重采样，建议确认"
    return "unknown", "weak", f"模型传感器范围（{'、'.join(sensors)}）与当前传感器（{sensor_text}）证据不足"


def _file_error(model: Dict[str, Any]) -> Optional[str]:
    if model.get("available") is False:
        return "模型文件缺失，请重新导入模型"
    filename = model.get("file")
    if not filename:
        return None
    base = model.get("_dir")
    if not base:
        return None
    path = Path(str(base)) / str(filename)
    if not path.exists() or not path.is_file():
        return f"模型文件缺失：{path.name}"
    return None


def assess_model_compatibility(
    image: Dict[str, Any],
    processed_meta: Optional[Dict[str, Any]],
    model: Dict[str, Any],
    edge_fill_warning_ratio: float = EDGE_FILL_WARNING_RATIO,
) -> Dict[str, Any]:
    """评估当前影像能否执行指定模型。

    status 为 incompatible 时禁止执行；warning 时必须由用户显式确认后以
    force=True 执行；compatible 仍可能包含可重采样等非阻断说明。
    """
    errors: List[str] = []
    warnings: List[str] = []
    notes: List[str] = []

    input_cfg = model.get("input") or {}
    input_type = str(input_cfg.get("type") or "").strip()
    if input_type not in SUPPORTED_INPUT_TYPES:
        errors.append("模型输入类型不受支持，请使用光谱机器学习或植被指数模型")

    file_error = _file_error(model)
    if file_error:
        errors.append(file_error)

    source_wl, source_origin = _source_wavelengths(image, processed_meta)
    target_wl, required_bands, features, _ = _model_targets(model)
    if input_type in SUPPORTED_INPUT_TYPES and not target_wl:
        errors.append("模型缺少完整输入波段定义")
    if input_type == "spectra" and not source_wl:
        errors.append("影像缺少波段中心波长，无法建立模型输入且不能安全执行反演")

    exact_bands, interpolated_bands, edge_filled_bands = _categorize_targets(
        source_wl, target_wl, input_type
    )
    target_count = len(target_wl)
    if target_count and input_type == "spectra":
        if not source_wl:
            exact_bands = interpolated_bands = edge_filled_bands = 0
        elif edge_filled_bands == target_count:
            errors.append(
                f"模型要求 {min(target_wl):g}–{max(target_wl):g} nm，"
                f"与影像光谱范围 {min(source_wl):g}–{max(source_wl):g} nm 完全无可用交集"
            )
    if input_type == "vi" and target_count and source_wl and exact_bands == 0:
        errors.append(
            f"当前影像无法获得模型所需的植被指数波段（{min(target_wl):g}–{max(target_wl):g} nm）"
        )

    source_range = [float(source_wl[0]), float(source_wl[-1])] if source_wl else []
    target_range = [float(min(target_wl)), float(max(target_wl))] if target_wl else []
    edge_fill_ratio = edge_filled_bands / max(1, target_count)

    crop_match, crop_message = _crop_match(image, model)
    if crop_match == "mismatch":
        warnings.append(crop_message)
    elif crop_match == "unknown":
        warnings.append(crop_message)

    sensor_match, sensor_evidence, sensor_message = _sensor_match(
        image, model, required_bands
    )
    if sensor_match == "mismatch":
        warnings.append(sensor_message)
    elif sensor_match == "unknown":
        warnings.append(sensor_message)

    if edge_fill_ratio >= max(0.0, float(edge_fill_warning_ratio)):
        warnings.append(
            f"{edge_filled_bands}/{max(1, target_count)} 个模型波段需要超出影像范围的边缘填充"
            f"（{edge_fill_ratio * 100:.1f}%），结果可能失真"
        )
    elif edge_filled_bands:
        notes.append(f"{edge_filled_bands} 个波段使用最近边缘波段填充")
    if interpolated_bands:
        notes.append(f"{interpolated_bands} 个模型波段通过线性光谱插值获得")
    if processed_meta and processed_meta.get("created_at"):
        notes.append("将使用现有预处理结果；执行时仍会再次核对兼容性")
    elif processed_meta is None:
        notes.append("影像尚未预处理；默认流程将先执行波段对齐与掩膜")

    if errors:
        status = "incompatible"
    elif warnings:
        status = "warning"
    else:
        status = "compatible"

    if status == "incompatible":
        recommendation = "请更换模型或补充/修正影像波段、模型文件后重试。"
    elif crop_match == "mismatch":
        recommendation = "作物适用性不一致；仅在确认模型迁移合理后手动强制运行。"
    elif sensor_match == "mismatch":
        recommendation = "传感器类型不一致；建议更换对应模型，确需试算时先做小区域核查。"
    elif edge_fill_ratio >= float(edge_fill_warning_ratio):
        recommendation = "边缘填充比例较高；建议调整模型训练范围或更换模型后重试。"
    elif interpolated_bands:
        recommendation = "波段可重采样；可继续执行，并在结果中核对空间分布与统计范围。"
    elif target_count and exact_bands == target_count:
        recommendation = "所需波段完整可用，可直接执行反演。"
    else:
        recommendation = "信息不足；请确认作物、传感器和波段范围后再执行。"

    if status == "compatible" and (
        crop_match == "match" or sensor_match == "match"
    ):
        card_status = "resample" if (interpolated_bands or edge_filled_bands) else "recommended"
    elif status == "compatible":
        card_status = "unknown"
    elif status == "warning":
        card_status = "unknown" if crop_match == "unknown" or sensor_match == "unknown" else "confirm"
    else:
        card_status = "incompatible"

    return {
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "notes": notes,
        "crop_match": crop_match,
        "sensor_match": sensor_match,
        "sensor_evidence": sensor_evidence,
        "required_bands": int(required_bands),
        "required_spectral_bands": int(target_count),
        "exact_bands": int(exact_bands),
        "interpolated_bands": int(interpolated_bands),
        "edge_filled_bands": int(edge_filled_bands),
        "edge_fill_ratio": round(float(edge_fill_ratio), 5),
        "source_range": source_range,
        "target_range": target_range,
        "source_origin": source_origin,
        "features": features,
        "model_input_type": input_type,
        "model_id": model.get("id"),
        "model_name": model.get("name"),
        "recommendation": recommendation,
        "card_status": card_status,
    }
