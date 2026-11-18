"""模型库 API。"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ..core.config import CUSTOM_MODELS_DIR
from ..core.utils import new_id
from ..services.models import (
    ModelNotFound,
    delete_custom_model,
    get_model,
    import_custom_model,
    list_models,
    onnx_available,
)

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("")
def get_models() -> Dict[str, Any]:
    return {"models": list_models(), "onnx_available": onnx_available()}


@router.get("/{model_id}")
def get_model_detail(model_id: str) -> Dict[str, Any]:
    try:
        model = get_model(model_id)
    except ModelNotFound:
        raise HTTPException(status_code=404, detail="模型不存在")
    model.pop("_dir", None)
    return model


@router.post("/import")
def import_model(
    file: UploadFile = File(...),
    name: str = Form(...),
    wavelengths: str = Form(...),
    algorithm: str = Form("机器学习"),
    crops: str = Form(""),
    sensors: str = Form(""),
    training_data: str = Form("用户导入模型"),
    r2: Optional[float] = Form(None),
    rmse: Optional[float] = Form(None),
    unit: str = Form(""),
    description: str = Form("用户导入的自定义反演模型。"),
    version: str = Form("1.0"),
    low: Optional[float] = Form(None),
    high: Optional[float] = Form(None),
    trusted_source: bool = Form(False),
    metric_type: str = Form("user_reported"),
) -> Dict[str, Any]:
    filename = file.filename or "model.pkl"
    suffix = Path(filename).suffix.lower()
    if suffix not in (".pkl", ".joblib", ".onnx"):
        raise HTTPException(status_code=400, detail="仅支持 .pkl / .joblib / .onnx 模型文件")
    if suffix == ".onnx" and not onnx_available():
        raise HTTPException(
            status_code=400,
            detail="当前环境未安装 onnxruntime，暂不支持 ONNX 模型；请使用 .pkl / .joblib",
        )
    incoming = CUSTOM_MODELS_DIR / "_incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    tmp = incoming / f"{new_id('upload', 4)}{suffix}"
    try:
        with open(tmp, "wb") as out:
            shutil.copyfileobj(file.file, out)
        if tmp.stat().st_size == 0:
            raise ValueError("模型文件为空")
        model = import_custom_model(
            tmp,
            filename,
            {
                "name": name,
                "wavelengths": wavelengths,
                "algorithm": algorithm,
                "crops": crops,
                "sensors": sensors,
                "training_data": training_data,
                "r2": r2,
                "rmse": rmse,
                "unit": unit,
                "description": description,
                "version": version,
                "low": low,
                "high": high,
                "trusted_source": trusted_source,
                "metric_type": metric_type,
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        try:
            file.file.close()
        except Exception:
            pass
    return model


@router.delete("/{model_id}")
def remove_model(model_id: str) -> Dict[str, Any]:
    try:
        delete_custom_model(model_id)
    except ModelNotFound:
        raise HTTPException(status_code=404, detail="模型不存在")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}
