from __future__ import annotations

import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
from scipy.signal import savgol_filter

from server.services.spectral_sim import simulate_spectra, wavelengths_default
from server.tools import train_demo_models as training


MODEL_DIR = Path(__file__).resolve().parents[1] / "server" / "builtin_models"
WL51 = wavelengths_default()
WL5 = np.array([475.0, 560.0, 668.0, 717.0, 840.0], dtype=float)


def _load(model_id: str):
    meta = json.loads((MODEL_DIR / f"{model_id}.json").read_text(encoding="utf-8"))
    model = joblib.load(MODEL_DIR / meta["file"])
    return meta, model


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assert_common_metadata(meta: dict, expected_bands: int):
    required = {
        "id",
        "name",
        "version",
        "algorithm",
        "builtin",
        "demo",
        "synthetic",
        "unit",
        "training_data",
        "input",
        "thresholds",
        "dataset",
        "structure",
        "metrics",
        "r2",
        "rmse",
        "metric_type",
        "limitations",
        "file_sha256",
    }
    assert required.issubset(meta)
    assert meta["demo"] is True
    assert meta["synthetic"] is True
    assert "合成" in meta["training_data"]
    assert "不是田间实测数据" in meta["training_data"]
    assert meta["dataset"]["synthetic"] is True
    assert meta["dataset"]["split_method"].startswith("80% train / 10% validation / 10%")
    assert meta["metrics"]["metric_type"] == "independent_test"
    assert meta["metric_type"] == "independent_test"
    assert len(meta["input"]["wavelengths"]) == expected_bands
    wavelengths = np.asarray(meta["input"]["wavelengths"], dtype=float)
    assert np.all(np.diff(wavelengths) > 0)
    assert 0.0 <= float(meta["r2"]) <= 1.0
    assert float(meta["rmse"]) > 0.0
    assert meta["file_sha256"] == _sha256(MODEL_DIR / meta["file"])
    assert meta["file_size_bytes"] == (MODEL_DIR / meta["file"]).stat().st_size


def test_dnn_nmass_demo_is_real_51_band_model_and_metrics_are_reproducible():
    meta, model = _load("dnn_nmass_demo")
    _assert_common_metadata(meta, 51)
    wavelengths = np.asarray(meta["input"]["wavelengths"], dtype=float)
    assert wavelengths[0] == 400.0
    assert wavelengths[-1] == 900.0
    assert np.allclose(np.diff(wavelengths), 10.0)

    X, y = training.make_dataset(n=7200, seed=3101)
    (
        _,
        _,
        X_test,
        _,
        _,
        y_test,
    ) = training.split_train_validation_test(X, y, 3101)
    prediction = np.asarray(model.predict(X_test), dtype=float).reshape(-1)
    assert prediction.shape == y_test.shape
    assert np.isfinite(prediction).all()
    recomputed = training.metrics(y_test, prediction)
    assert recomputed == meta["test_metrics"]
    assert meta["metrics"]["test"] == meta["test_metrics"]
    assert 12.0 <= float(prediction.mean()) <= 38.0


def test_dnn_multispectral_demo_is_real_five_band_model():
    meta, model = _load("dnn_multispectral_demo")
    _assert_common_metadata(meta, 5)
    assert meta["input"]["wavelengths"] == WL5.tolist()

    rng = np.random.default_rng(3102)
    n = 7200
    y = rng.uniform(15.0, 35.0, n).astype(np.float32)
    veg = rng.uniform(0.45, 1.0, n).astype(np.float32)
    X = simulate_spectra(y, veg, WL5, noise=0.004, rng=rng).reshape(n, len(WL5))
    X = savgol_filter(X, 5, 2, axis=1).astype(np.float32)
    _, _, X_test, _, _, y_test = training.split_train_validation_test(X, y, 3102)
    prediction = np.asarray(model.predict(X_test), dtype=float).reshape(-1)
    assert prediction.shape == y_test.shape
    assert np.isfinite(prediction).all()
    assert training.metrics(y_test, prediction) == meta["test_metrics"]


def test_dnn_models_are_listed_as_available_builtin_models():
    from server.services.models import get_model, load_estimator

    for model_id, bands in (
        ("dnn_nmass_demo", 51),
        ("dnn_multispectral_demo", 5),
    ):
        selected = get_model(model_id)
        assert selected["available"] is True
        assert selected["builtin"] is True
        estimator = load_estimator(selected)
        sample = np.full((3, bands), 0.12, dtype=np.float32)
        output = np.asarray(estimator.predict(sample)).reshape(-1)
        assert output.shape == (3,)
        assert np.isfinite(output).all()
