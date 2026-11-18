from __future__ import annotations

import joblib
import numpy as np
from fastapi.testclient import TestClient
from sklearn.linear_model import LinearRegression

from server.api import models as models_api
from server.main import app
from server.services import models as model_service


def _joblib_bytes() -> bytes:
    import tempfile
    from pathlib import Path

    X = np.array([[0.1, 0.2, 0.3], [0.2, 0.3, 0.4], [0.4, 0.5, 0.6]])
    y = np.array([20.0, 24.0, 29.0])
    estimator = LinearRegression().fit(X, y)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "model.joblib"
        joblib.dump(estimator, path)
        return path.read_bytes()


def _fields(trusted: bool = True):
    return {
        "name": "API 导入测试模型",
        "wavelengths": "500, 600, 700",
        "algorithm": "LinearRegression",
        "crops": "玉米",
        "sensors": "高光谱",
        "unit": "g/kg",
        "low": "200",
        "high": "280",
        "trusted_source": "true" if trusted else "false",
    }


def test_api_import_runs_trial_and_returns_hash(tmp_path, monkeypatch):
    root = tmp_path / "models"
    root.mkdir()
    monkeypatch.setattr(models_api, "CUSTOM_MODELS_DIR", root)
    monkeypatch.setattr(model_service, "CUSTOM_MODELS_DIR", root)
    client = TestClient(app)
    response = client.post(
        "/api/models/import",
        data=_fields(),
        files={"file": ("trusted.joblib", _joblib_bytes(), "application/octet-stream")},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["unit"] == "g/kg"
    assert payload["thresholds"]["low"] == 200
    assert payload["thresholds"]["high"] == 280
    assert len(payload["file_sha256"]) == 64
    assert payload["validation"]["trial_inference"]["ok"] is True


def test_api_import_rejects_untrusted_pickle(tmp_path, monkeypatch):
    root = tmp_path / "models"
    root.mkdir()
    monkeypatch.setattr(models_api, "CUSTOM_MODELS_DIR", root)
    monkeypatch.setattr(model_service, "CUSTOM_MODELS_DIR", root)
    client = TestClient(app)
    response = client.post(
        "/api/models/import",
        data=_fields(False),
        files={"file": ("untrusted.joblib", _joblib_bytes(), "application/octet-stream")},
    )
    assert response.status_code == 400
    assert "可信来源" in response.json()["detail"]
