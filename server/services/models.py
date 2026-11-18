"""反演模型注册、加载与自定义模型导入。"""
from __future__ import annotations

import hashlib
import shutil
import threading
from pathlib import Path
from typing import Any, Dict, List

import joblib
import numpy as np

from ..core.config import BUILTIN_MODELS_DIR, CUSTOM_MODELS_DIR
from ..core.utils import (
    new_id,
    now_iso,
    now_stamp,
    parse_number_list,
    read_json,
    safe_float,
    write_json,
)


class ModelNotFound(KeyError):
    pass


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class OnnxPredictor:
    """仅支持单输入 (N, B) float32 的 ONNX 模型。"""

    def __init__(self, path: Path):
        try:
            import onnxruntime as ort  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise ValueError(
                "当前环境未安装 onnxruntime，暂不支持 ONNX 模型；请使用 .pkl / .joblib 模型，"
                "或运行 pip install onnxruntime 后再试。"
            ) from exc
        self.session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        inputs = self.session.get_inputs()
        if len(inputs) != 1:
            raise ValueError("ONNX 模型需为单输入模型（输入形状 [N, B]）")
        self.input_name = inputs[0].name
        self.input_shape = inputs[0].shape
        self.n_features_in_ = (
            int(self.input_shape[1])
            if len(self.input_shape) == 2 and isinstance(self.input_shape[1], int)
            else None
        )

    def predict(self, X: np.ndarray) -> np.ndarray:
        X = np.asarray(X, dtype=np.float32)
        if len(self.input_shape) == 2 and self.input_shape[1] not in (None, "None"):
            if X.shape[1] != int(self.input_shape[1]):
                raise ValueError(
                    f"模型输入波段数为 {self.input_shape[1]}，当前数据为 {X.shape[1]}"
                )
        output = self.session.run(None, {self.input_name: X})[0]
        return np.asarray(output).reshape(X.shape[0], -1)[:, 0]


class AffineCalibratedPredictor:
    """Wrap a parent predictor with a few-shot affine correction."""

    def __init__(self, base_estimator: Any, slope: float, intercept: float):
        self.base_estimator = base_estimator
        self.slope = float(slope)
        self.intercept = float(intercept)
        self.n_features_in_ = getattr(base_estimator, "n_features_in_", None)

    def predict(self, X: np.ndarray) -> np.ndarray:
        base = np.asarray(self.base_estimator.predict(X), dtype=np.float64).reshape(-1)
        return self.slope * base + self.intercept


def onnx_available() -> bool:
    try:
        import onnxruntime

        return bool(onnxruntime)
    except ImportError:
        return False


def _iter_models():
    if BUILTIN_MODELS_DIR.exists():
        for path in sorted(BUILTIN_MODELS_DIR.glob("*.json")):
            meta = read_json(path)
            if isinstance(meta, dict) and meta.get("id"):
                yield meta, BUILTIN_MODELS_DIR
    if CUSTOM_MODELS_DIR.exists():
        for entry in sorted(CUSTOM_MODELS_DIR.iterdir()):
            if entry.is_dir():
                meta = read_json(entry / "meta.json")
                if isinstance(meta, dict) and meta.get("id"):
                    yield meta, entry


def _model_availability(
    meta: Dict[str, Any],
    base: Path,
    seen: set[str] | None = None,
) -> bool:
    seen = set(seen or ())
    model_id = str(meta.get("id") or "")
    if model_id and model_id in seen:
        return False
    if model_id:
        seen.add(model_id)
    filename = meta.get("file")
    if filename:
        return (base / str(filename)).is_file()
    parent_id = (
        meta.get("parent_model_id")
        or (meta.get("few_shot") or {}).get("parent_model_id")
    )
    if parent_id:
        for parent_meta, parent_base in _iter_models():
            if parent_meta.get("id") == parent_id:
                return _model_availability(parent_meta, parent_base, seen)
        return False
    return not bool(meta.get("model_kind") or meta.get("type") == "few_shot_calibrated")


def _public(meta: Dict[str, Any], base: Path) -> Dict[str, Any]:
    data = dict(meta)
    data["builtin"] = bool(meta.get("builtin"))
    data["available"] = _model_availability(meta, base)
    data.pop("_dir", None)
    return data


def list_models() -> List[Dict[str, Any]]:
    models = []
    seen = set()
    for meta, base in _iter_models():
        if meta["id"] in seen:
            continue
        seen.add(meta["id"])
        models.append(_public(meta, base))
    return models


def get_model(model_id: str) -> Dict[str, Any]:
    for meta, base in _iter_models():
        if meta.get("id") == model_id:
            data = dict(meta)
            data["_dir"] = str(base)
            data["available"] = _model_availability(meta, base)
            return data
    raise ModelNotFound(model_id)


def load_estimator(model: Dict[str, Any]):
    few_shot = model.get("few_shot") or {}
    if model.get("model_kind") == "few_shot_calibrated" or few_shot:
        parent_id = model.get("parent_model_id") or few_shot.get("parent_model_id")
        if not parent_id:
            raise ValueError("派生模型缺少父模型 ID")
        try:
            parent = get_model(str(parent_id))
        except ModelNotFound as exc:
            raise ValueError(f"派生模型的父模型不存在：{parent_id}") from exc
        if not parent.get("available"):
            raise FileNotFoundError(f"派生模型的父模型文件缺失：{parent_id}")
        return AffineCalibratedPredictor(
            load_estimator(parent),
            float(few_shot.get("slope", 1.0)),
            float(few_shot.get("intercept", 0.0)),
        )
    path = Path(model["_dir"]) / str(model.get("file", ""))
    if not path.exists():
        raise FileNotFoundError(f"模型文件缺失：{path.name}")
    if path.suffix.lower() == ".onnx":
        return OnnxPredictor(path)
    estimator = joblib.load(path)
    if not hasattr(estimator, "predict"):
        raise ValueError("模型文件不包含 predict 方法，无法用于反演")
    return estimator


def validate_model_wavelengths(wavelengths: List[float]) -> None:
    if len(wavelengths) < 2:
        raise ValueError("请填写模型训练波段中心波长（至少 2 个，逗号分隔）")
    if len(wavelengths) > 400:
        raise ValueError("训练波长数量过多（>400）")
    if min(wavelengths) < 100 or max(wavelengths) > 20000:
        raise ValueError("训练波长应处于 100–20000 nm 的合理范围")
    if any(b <= a for a, b in zip(wavelengths, wavelengths[1:])):
        raise ValueError("训练波长必须严格递增且不得重复，请按模型真实输入顺序填写")


def trial_predict(
    estimator: Any,
    feature_count: int,
    sample_count: int = 3,
) -> Dict[str, Any]:
    """用确定性合成光谱执行一次导入试推理，验证输出契约。"""
    if feature_count < 2:
        raise ValueError("模型输入波段数至少为 2")
    base = np.linspace(0.18, 0.62, feature_count, dtype=np.float32)
    offsets = np.array([0.0, 0.015, -0.012], dtype=np.float32)[:sample_count]
    X = np.clip(base[None, :] + offsets[:, None], 0.0, 1.0).astype(np.float32)
    try:
        raw = estimator.predict(X)
    except Exception as exc:
        raise ValueError(f"模型试推理失败：{exc}") from exc
    try:
        output = np.asarray(raw, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("模型输出无法转换为数值序列") from exc
    if output.ndim == 0 or output.size != sample_count:
        raise ValueError(
            f"模型试推理输出数量异常：输入 {sample_count} 条，输出形状 {output.shape}；"
            "应可转换为 (N,)"
        )
    flattened = output.reshape(-1)
    if not np.isfinite(flattened).all():
        raise ValueError("模型试推理输出包含 NaN 或 Inf")
    return {
        "ok": True,
        "samples": int(sample_count),
        "feature_count": int(feature_count),
        "output_shape": [int(v) for v in output.shape],
        "values": [round(float(v), 6) for v in flattened],
        "min": round(float(np.min(flattened)), 6),
        "max": round(float(np.max(flattened)), 6),
    }


_model_lock = threading.Lock()


def import_custom_model(
    file_path: str | Path,
    original_name: str,
    form: Dict[str, Any],
) -> Dict[str, Any]:
    """导入 .pkl/.joblib/.onnx 模型。form 需包含名称、波长、算法等信息。"""
    file_path = Path(file_path)
    ext = Path(original_name).suffix.lower()
    if ext not in (".pkl", ".joblib", ".onnx"):
        raise ValueError("仅支持 .pkl / .joblib / .onnx 模型文件")
    if ext in (".pkl", ".joblib") and not bool(form.get("trusted_source")):
        raise ValueError("请确认仅导入来自可信来源的 .pkl / .joblib 模型")
    name = str(form.get("name") or "").strip()
    if not name:
        raise ValueError("请填写模型名称")
    wavelengths = parse_number_list(form.get("wavelengths"))
    validate_model_wavelengths(wavelengths)
    raw_unit = str(form.get("unit") or "").strip()
    unit = raw_unit or "待确认"
    low = safe_float(form.get("low"), 22.0)
    high = safe_float(form.get("high"), 28.0)
    if low is None or high is None or high <= low:
        raise ValueError("高氮分级阈值必须大于低氮阈值")

    with _model_lock:
        mid = f"custom_{now_stamp()}_{new_id('m', 2).split('_')[-1]}"
        dest = CUSTOM_MODELS_DIR / mid
        dest.mkdir(parents=True, exist_ok=True)
        filename = "model" + ext
        target = dest / filename
        shutil.move(str(file_path), target)

        try:
            if ext == ".onnx":
                estimator = OnnxPredictor(target)
            else:
                estimator = joblib.load(target)
                if not hasattr(estimator, "predict"):
                    raise ValueError("模型文件不包含 predict 方法")
            n_features = getattr(estimator, "n_features_in_", None)
            if n_features is not None and int(n_features) != len(wavelengths):
                raise ValueError(
                    f"模型期望 {int(n_features)} 个波段输入，但填写的中心波长有 {len(wavelengths)} 个，请核对"
                )
            trial = trial_predict(estimator, len(wavelengths))
        except Exception:
            shutil.rmtree(dest, ignore_errors=True)
            raise

        digest = file_sha256(target)
        meta = {
            "id": mid,
            "name": name,
            "type": "ml",
            "algorithm": str(form.get("algorithm") or ("ONNX" if ext == ".onnx" else "机器学习")),
            "builtin": False,
            "demo": False,
            "version": str(form.get("version") or "1.0"),
            "unit": unit,
            "unit_confirmed": bool(raw_unit),
            "crops": [s.strip() for s in str(form.get("crops") or "").replace("，", ",").split(",") if s.strip()],
            "sensors": [s.strip() for s in str(form.get("sensors") or "").replace("，", ",").split(",") if s.strip()],
            "training_data": str(form.get("training_data") or "用户导入模型"),
            "r2": safe_float(form.get("r2")),
            "rmse": safe_float(form.get("rmse")),
            "metric_type": str(form.get("metric_type") or "user_reported") if (form.get("r2") is not None or form.get("rmse") is not None) else "not_provided",
            "description": str(form.get("description") or "用户导入的自定义反演模型。"),
            "input": {"type": "spectra", "wavelengths": wavelengths},
            "thresholds": {
                "low": float(low),
                "high": float(high),
                "unit": unit,
            },
            "file": filename,
            "file_type": ext.lstrip("."),
            "file_sha256": digest,
            "file_size": int(target.stat().st_size),
            "trusted_source_confirmed": bool(form.get("trusted_source")) or ext == ".onnx",
            "validation": {
                "wavelengths_validated": True,
                "trial_inference": trial,
            },
            "created_at": now_iso(),
            "tags": ["自定义模型"],
        }
        write_json(dest / "meta.json", meta)
        return _public(meta, dest)


def delete_custom_model(model_id: str) -> None:
    model = get_model(model_id)
    if model.get("builtin"):
        raise ValueError("内置模型不可删除")
    if CUSTOM_MODELS_DIR.exists():
        for entry in CUSTOM_MODELS_DIR.iterdir():
            if not entry.is_dir():
                continue
            child = read_json(entry / "meta.json")
            parent_id = (
                (child or {}).get("parent_model_id")
                or ((child or {}).get("few_shot") or {}).get("parent_model_id")
            )
            if parent_id == model_id:
                raise ValueError(
                    f"模型已被派生模型“{(child or {}).get('name') or entry.name}”引用，"
                    "请先删除派生模型"
                )
    dest = CUSTOM_MODELS_DIR / model_id
    if dest.exists() and dest.parent == CUSTOM_MODELS_DIR:
        shutil.rmtree(dest)


def model_summary_for_report(model: Dict[str, Any]) -> Dict[str, Any]:
    keys = (
        "id",
        "name",
        "algorithm",
        "crops",
        "sensors",
        "r2",
        "rmse",
        "metric_type",
        "unit",
        "training_data",
        "version",
        "file_sha256",
        "model_kind",
        "parent_model_id",
        "parent_model_name",
        "few_shot",
    )
    return {k: model.get(k) for k in keys if k in model}
