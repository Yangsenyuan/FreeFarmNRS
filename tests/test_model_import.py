from pathlib import Path

import joblib
import numpy as np
import pytest
from sklearn.linear_model import LinearRegression

from server.services import models as model_service


def _model_file(tmp_path: Path) -> Path:
    x = np.linspace(0.1, 0.8, 51, dtype=float)
    X = np.stack([x, x + 0.01, x - 0.02])
    y = X.mean(axis=1) * 20.0
    estimator = LinearRegression().fit(X, y)
    path = tmp_path / "input.joblib"
    joblib.dump(estimator, path)
    return path


def _form(wavelengths=None, **overrides):
    values = {
        "name": "自定义测试模型",
        "wavelengths": wavelengths or list(range(400, 901, 10)),
        "algorithm": "LinearRegression",
        "unit": "g/kg",
        "crops": "玉米",
        "sensors": "高光谱",
        "low": 20,
        "high": 27,
        "trusted_source": True,
    }
    values.update(overrides)
    return values


def test_import_runs_trial_and_records_hash(tmp_path, monkeypatch):
    root = tmp_path / "custom_models"
    root.mkdir()
    monkeypatch.setattr(model_service, "CUSTOM_MODELS_DIR", root)
    imported = model_service.import_custom_model(
        _model_file(tmp_path), "trusted.joblib", _form()
    )
    assert imported["unit"] == "g/kg"
    assert imported["unit_confirmed"] is True
    assert len(imported["file_sha256"]) == 64
    trial = imported["validation"]["trial_inference"]
    assert trial["ok"] is True
    assert trial["samples"] == 3
    saved = root / imported["id"]
    assert (saved / "model.joblib").exists()
    assert (saved / "meta.json").exists()


def test_untrusted_pickle_is_rejected(tmp_path, monkeypatch):
    root = tmp_path / "custom_models"
    root.mkdir()
    monkeypatch.setattr(model_service, "CUSTOM_MODELS_DIR", root)
    with pytest.raises(ValueError, match="可信来源"):
        model_service.import_custom_model(
            _model_file(tmp_path),
            "untrusted.joblib",
            _form(trusted_source=False),
        )


def test_wavelengths_must_be_strictly_increasing(tmp_path, monkeypatch):
    root = tmp_path / "custom_models"
    root.mkdir()
    monkeypatch.setattr(model_service, "CUSTOM_MODELS_DIR", root)
    bad = [400.0, 410.0, 410.0, 420.0]
    with pytest.raises(ValueError, match="严格递增"):
        model_service.import_custom_model(
            _model_file(tmp_path), "bad_wavelengths.joblib", _form(bad)
        )


def test_threshold_order_is_validated(tmp_path, monkeypatch):
    root = tmp_path / "custom_models"
    root.mkdir()
    monkeypatch.setattr(model_service, "CUSTOM_MODELS_DIR", root)
    with pytest.raises(ValueError, match="高氮"):
        model_service.import_custom_model(
            _model_file(tmp_path),
            "bad_threshold.joblib",
            _form(low=30, high=20),
        )


class _BadOutputModel:
    n_features_in_ = 51

    def predict(self, X):
        return np.full((len(X), 2), np.nan)


def test_trial_rejects_invalid_output_contract():
    with pytest.raises(ValueError, match="输出数量"):
        model_service.trial_predict(_BadOutputModel(), 51)


def test_missing_unit_is_explicitly_pending(tmp_path, monkeypatch):
    root = tmp_path / "custom_models"
    root.mkdir()
    monkeypatch.setattr(model_service, "CUSTOM_MODELS_DIR", root)
    imported = model_service.import_custom_model(
        _model_file(tmp_path), "unknown_unit.joblib", _form(unit="")
    )
    assert imported["unit"] == "待确认"
    assert imported["unit_confirmed"] is False
