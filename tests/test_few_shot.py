from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient
from pypdf import PdfReader
from rasterio.warp import transform as transform_coordinates

from server.api import processing as processing_api
from server.core.store import store
from server.main import app
from server.services import few_shot as few_shot_service
from server.services import models as model_service
from server.services.inference import run_inversion
from server.services.report import export_result_pdf


def test_offset_for_small_sample_and_affine_for_larger_sample():
    offset = few_shot_service.fit_calibration([20.0, 30.0], [21.0, 31.0])
    assert offset["mode"] == "offset"
    assert offset["slope"] == 1.0
    assert offset["intercept"] == 1.0
    assert offset["leave_one_out"]["available"] is True

    predicted = np.linspace(18.0, 30.0, 8)
    measured = predicted * 1.18 - 2.0
    affine = few_shot_service.fit_calibration(predicted.tolist(), measured.tolist())
    assert affine["mode"] == "affine_shrunk"
    assert affine["sample_count"] == 8
    assert affine["after_in_sample"]["rmse"] < affine["before"]["rmse"]
    assert affine["leave_one_out"]["available"] is True
    assert affine["recommended_samples"] == 5


def test_extract_samples_rejects_invalid_points_and_pairs_predictions(tmp_path):
    result_dir = tmp_path / "results"
    result_dir.mkdir()
    values = np.arange(20, dtype=np.float32).reshape(4, 5)
    values[0, 0] = np.nan
    np.save(result_dir / "nmass_latest.npy", values)
    summary = {
        "id": "base_result",
        "unit": "mg/g",
        "model": {"id": "dnn_nmass_demo", "name": "父模型", "unit": "mg/g"},
        "files": {"npy": "nmass_latest.npy"},
        "crs": "EPSG:32650",
        "transform": [1, 0, 500000, 0, -1, 3500000],
    }
    payload = few_shot_service.extract_measured_samples(
        result_dir,
        summary,
        [
            {"sample_id": "ok1", "col": 1, "row": 1, "nmass": 8.0},
            {"sample_id": "ok2", "col": 2, "row": 1, "nmass": 10.0},
            {"sample_id": "masked", "col": 0, "row": 0, "nmass": 5.0},
            {"sample_id": "outside", "col": 99, "row": 1, "nmass": 5.0},
        ],
    )
    assert payload["sample_count"] == 2
    assert len(payload["rejected_samples"]) == 2
    assert payload["samples"][0]["base_prediction"] == 6.0
    assert payload["calibration"]["mode"] == "offset"

    lon, lat = transform_coordinates(
        "EPSG:32650", "EPSG:4326", [500002.5], [3499998.5]
    )
    lonlat = few_shot_service.extract_measured_samples(
        result_dir,
        summary,
        [{"sample_id": "gps", "lon": lon[0], "lat": lat[0], "nmass": 8.0}],
    )
    assert lonlat["samples"][0]["col"] == 2
    assert lonlat["samples"][0]["row"] == 1
    assert lonlat["samples"][0]["coordinate_source"] == "lonlat"

    try:
        few_shot_service.extract_measured_samples(
            result_dir, summary, [{"col": 1, "row": 1, "nmass": 8}], unit="g/kg"
        )
    except ValueError as exc:
        assert "单位" in str(exc)
    else:
        raise AssertionError("unit mismatch should fail")


def test_derived_model_uses_parent_without_copying_binary(tmp_path, monkeypatch):
    root = tmp_path / "models"
    root.mkdir()
    monkeypatch.setattr(few_shot_service, "CUSTOM_MODELS_DIR", root)
    monkeypatch.setattr(model_service, "CUSTOM_MODELS_DIR", root)

    parent = model_service.get_model("dnn_nmass_demo")
    calibration = {
        "unit": "mg/g",
        "samples": [{"col": 1, "row": 1, "nmass": 24.0}],
        "rejected_samples": [],
        "sample_fingerprint": "abc",
        "coordinate_system": "preprocessed_raster_col_row_zero_based",
        "calibration": few_shot_service.fit_calibration([20.0], [24.0]),
    }
    derived = few_shot_service.create_derived_model(
        parent,
        calibration,
        project_id="p",
        image_id="i",
        base_result_id="r",
    )
    saved = root / derived["id"]
    assert (saved / "meta.json").exists()
    assert not list(saved.glob("model.*"))

    listed = model_service.get_model(derived["id"])
    assert listed["available"] is True
    assert listed["model_kind"] == "few_shot_calibrated"
    estimator = model_service.load_estimator(listed)
    prediction = np.asarray(
        estimator.predict(np.linspace(0.1, 0.8, 51, dtype=np.float32)[None, :])
    )
    assert prediction.shape == (1,)
    assert np.isfinite(prediction).all()


def test_derived_model_is_unavailable_when_parent_is_missing(tmp_path, monkeypatch):
    root = tmp_path / "models"
    entry = root / "orphan"
    entry.mkdir(parents=True)
    (entry / "meta.json").write_text(
        json.dumps(
            {
                "id": "orphan",
                "name": "孤立派生模型",
                "model_kind": "few_shot_calibrated",
                "parent_model_id": "not_found",
                "input": {"type": "spectra", "wavelengths": [400, 410]},
                "few_shot": {
                    "parent_model_id": "not_found",
                    "slope": 1.0,
                    "intercept": 0.0,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(model_service, "CUSTOM_MODELS_DIR", root)
    model = model_service.get_model("orphan")
    assert model["available"] is False
    try:
        model_service.load_estimator(model)
    except (ValueError, FileNotFoundError):
        pass
    else:
        raise AssertionError("missing parent should make derived estimator unavailable")


def test_derived_inference_applies_affine_to_base_prediction(tmp_path, monkeypatch):
    model_root = tmp_path / "models"
    model_root.mkdir()
    monkeypatch.setattr(few_shot_service, "CUSTOM_MODELS_DIR", model_root)
    monkeypatch.setattr(model_service, "CUSTOM_MODELS_DIR", model_root)
    processed = tmp_path / "processed"
    processed.mkdir()
    wavelengths = np.arange(400, 901, 10, dtype=float)
    spectrum = np.linspace(0.18, 0.58, len(wavelengths), dtype=np.float32)
    stack = np.repeat(spectrum[:, None, None], 4, axis=1)
    stack = np.repeat(stack, 5, axis=2)
    np.savez_compressed(processed / "stack.npz", stack=stack.astype(np.float16))
    np.save(processed / "mask.npy", np.ones((4, 5), dtype=bool))
    (processed / "meta.json").write_text(
        json.dumps(
            {
                "created_at": "2026-11-18T10:00:00+08:00",
                "width": 5,
                "height": 4,
                "bands": 51,
                "wavelengths": wavelengths.tolist(),
                "source_wavelengths": wavelengths.tolist(),
                "transform": [1, 0, 0, 0, -1, 0],
                "crs": None,
                "config": {},
                "quality": {"level": "ok"},
                "mask": {"ratio": 1.0},
            }
        ),
        encoding="utf-8",
    )
    image = {"id": "tiny", "name": "tiny.tif", "flight_date": "2026-11-18"}
    base = run_inversion(
        image,
        processed,
        model_service.get_model("dnn_nmass_demo"),
        tmp_path / "base",
    )
    calibration = {
        "unit": "mg/g",
        "samples": [{"col": 0, "row": 0, "nmass": 24.0}],
        "rejected_samples": [],
        "sample_fingerprint": "chain",
        "coordinate_system": "preprocessed_raster_col_row_zero_based",
        "calibration": few_shot_service.fit_calibration([20.0], [24.0]),
    }
    derived = few_shot_service.create_derived_model(
        model_service.get_model("dnn_nmass_demo"),
        calibration,
        project_id="p",
        image_id="i",
        base_result_id=base["id"],
    )
    corrected = run_inversion(
        image,
        processed,
        model_service.get_model(derived["id"]),
        tmp_path / "corrected",
    )
    assert corrected["model"]["model_kind"] == "few_shot_calibrated"
    assert corrected["model"]["parent_model_id"] == "dnn_nmass_demo"
    assert corrected["trace"]["model"]["few_shot"]["sample_count"] == 1
    base_values = np.load(tmp_path / "base" / "nmass_latest.npy")
    corrected_values = np.load(tmp_path / "corrected" / "nmass_latest.npy")
    fit = calibration["calibration"]
    expected = np.clip(fit["slope"] * base_values + fit["intercept"], 0.0, 60.0)
    assert np.allclose(corrected_values, expected, equal_nan=True)
    pdf_path = export_result_pdf(
        corrected,
        {"name": "few-shot测试项目", "field": "T1"},
        {"sensor": "高光谱", "flight_date": "2026-11-18"},
        tmp_path / "corrected",
        tmp_path / "corrected" / "report.pdf",
    )
    text = "\n".join(page.extract_text() or "" for page in PdfReader(str(pdf_path)).pages)
    assert "Few-shot" in text
    assert "留一" in text


def _write_result(pid: str, iid: str):
    image_dir = store.image_dir(pid, iid)
    result_dir = image_dir / "results"
    processed_dir = image_dir / "processed"
    result_dir.mkdir(parents=True)
    processed_dir.mkdir(parents=True)
    values = np.arange(24, dtype=np.float32).reshape(4, 6) + 10.0
    np.save(result_dir / "nmass_latest.npy", values)
    (result_dir / "latest.json").write_text(
        json.dumps(
            {
                "id": "base_result",
                "unit": "mg/g",
                "model": {
                    "id": "dnn_nmass_demo",
                    "name": "全谱 51 波段 Nmass-DNN 示例模型",
                    "unit": "mg/g",
                },
                "thresholds": {"low": 22.0, "high": 28.0, "unit": "mg/g"},
                "files": {"npy": "nmass_latest.npy"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    np.savez_compressed(
        processed_dir / "stack.npz",
        stack=np.zeros((51, 4, 6), dtype=np.float16),
    )
    np.save(processed_dir / "mask.npy", np.ones((4, 6), dtype=bool))
    (processed_dir / "meta.json").write_text(
        json.dumps(
            {
                "created_at": "2026-11-18T10:00:00+08:00",
                "width": 6,
                "height": 4,
                "bands": 51,
                "wavelengths": list(range(400, 901, 10)),
                "source_wavelengths": list(range(400, 901, 10)),
            }
        ),
        encoding="utf-8",
    )


def test_preview_and_apply_few_shot_api(tmp_path, monkeypatch):
    project_root = tmp_path / "projects"
    model_root = tmp_path / "models"
    project_root.mkdir()
    model_root.mkdir()
    monkeypatch.setattr(store, "root", project_root)
    monkeypatch.setattr(few_shot_service, "CUSTOM_MODELS_DIR", model_root)
    monkeypatch.setattr(model_service, "CUSTOM_MODELS_DIR", model_root)
    project = store.create_project("few-shot测试", crop="玉米", project_id="few_api")
    store.add_image(
        project["id"],
        {
            "id": "few_img",
            "name": "few.tif",
            "file": "source/few.tif",
            "bands": 51,
            "wavelengths": list(range(400, 901, 10)),
            "sensor": "高光谱",
            "status": "inverted",
        },
    )
    _write_result(project["id"], "few_img")
    client = TestClient(app)
    template = client.get("/api/projects/few_api/images/few_img/few-shot/template.csv")
    assert template.status_code == 200
    assert "sample_id,col,row,nmass" in template.text
    lonlat_template = client.get(
        "/api/projects/few_api/images/few_img/few-shot/template.csv?coordinate=lonlat"
    )
    assert lonlat_template.status_code == 200
    assert "sample_id,lon,lat,nmass" in lonlat_template.text
    samples = [
        {"sample_id": "s1", "col": 1, "row": 1, "nmass": 24.0},
        {"sample_id": "s2", "col": 2, "row": 2, "nmass": 28.0},
        {"sample_id": "s3", "col": 3, "row": 3, "nmass": 31.0},
    ]
    preview = client.post(
        "/api/projects/few_api/images/few_img/few-shot/preview",
        json={"samples": samples, "unit": "mg/g"},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["sample_count"] == 3
    assert preview.json()["calibration"]["mode"] == "affine_shrunk"

    def fake_invert_one(*args, **kwargs):
        return {"id": "calibrated_result", "image_id": "few_img", "model": {"id": "derived"}}

    monkeypatch.setattr(processing_api, "_invert_one", fake_invert_one)
    applied = client.post(
        "/api/projects/few_api/images/few_img/few-shot/apply",
        json={"samples": samples, "unit": "mg/g", "name": "测试实测校正模型"},
    )
    assert applied.status_code == 200, applied.text
    data = applied.json()
    assert data["model"]["model_kind"] == "few_shot_calibrated"
    assert data["calibration"]["sample_count"] == 3
    job_id = data["job_id"]
    for _ in range(40):
        status = client.get(f"/api/jobs/{job_id}").json()
        if status["status"] in ("completed", "failed"):
            break
        time.sleep(0.05)
    assert status["status"] == "completed", status
    snapshot = (
        store.image_dir("few_api", "few_img")
        / "results"
        / "calibration_sources"
        / data["model"]["id"]
        / "nmass_latest.npy"
    )
    assert snapshot.exists()


def test_apply_few_shot_runs_real_inversion(tmp_path, monkeypatch):
    project_root = tmp_path / "projects"
    model_root = tmp_path / "models"
    project_root.mkdir()
    model_root.mkdir()
    monkeypatch.setattr(store, "root", project_root)
    monkeypatch.setattr(few_shot_service, "CUSTOM_MODELS_DIR", model_root)
    monkeypatch.setattr(model_service, "CUSTOM_MODELS_DIR", model_root)
    project = store.create_project("few-shot实跑", crop="玉米", project_id="few_real")
    wavelengths = list(range(400, 901, 10))
    store.add_image(
        project["id"],
        {
            "id": "real_img",
            "name": "real.tif",
            "file": "source/real.tif",
            "bands": 51,
            "wavelengths": wavelengths,
            "sensor": "高光谱",
            "flight_date": "2026-11-18",
            "status": "imported",
        },
    )
    image_dir = store.image_dir(project["id"], "real_img")
    source_dir = image_dir / "source"
    processed_dir = image_dir / "processed"
    results_dir = image_dir / "results"
    source_dir.mkdir(parents=True)
    processed_dir.mkdir(parents=True)
    (source_dir / "real.tif").write_bytes(b"placeholder")
    spectrum = np.linspace(0.2, 0.6, 51, dtype=np.float32)
    stack = np.repeat(spectrum[:, None, None], 6, axis=1)
    stack = np.repeat(stack, 8, axis=2)
    np.savez_compressed(processed_dir / "stack.npz", stack=stack.astype(np.float16))
    np.save(processed_dir / "mask.npy", np.ones((6, 8), dtype=bool))
    (processed_dir / "meta.json").write_text(
        json.dumps(
            {
                "created_at": "2026-11-18T10:00:00+08:00",
                "width": 8,
                "height": 6,
                "bands": 51,
                "wavelengths": wavelengths,
                "source_wavelengths": wavelengths,
                "source_bands": 51,
                "transform": [1, 0, 0, 0, -1, 0],
                "crs": None,
                "config": {},
                "quality": {"level": "ok"},
                "mask": {"ratio": 1.0},
                "previews": {},
            }
        ),
        encoding="utf-8",
    )
    image = store.get_image(project["id"], "real_img")
    base = run_inversion(
        image,
        processed_dir,
        model_service.get_model("dnn_nmass_demo"),
        results_dir,
    )
    assert base["stats"]["count"] == 48
    samples = [
        {"sample_id": f"s{i}", "col": i, "row": 1, "nmass": 20.0 + i * 0.5}
        for i in range(5)
    ]
    client = TestClient(app)
    applied = client.post(
        "/api/projects/few_real/images/real_img/few-shot/apply",
        json={"samples": samples, "name": "实跑校正模型", "unit": "mg/g"},
    )
    assert applied.status_code == 200, applied.text
    job_id = applied.json()["job_id"]
    status = None
    for _ in range(100):
        status = client.get(f"/api/jobs/{job_id}").json()
        if status["status"] in ("completed", "failed"):
            break
        time.sleep(0.05)
    assert status and status["status"] == "completed", status
    current = store.get_image(project["id"], "real_img")
    assert current["result"]["model"]["model_kind"] == "few_shot_calibrated"
    latest = json.loads((results_dir / "latest.json").read_text(encoding="utf-8"))
    assert latest["trace"]["model"]["few_shot"]["sample_count"] == 5
    snapshot = (
        results_dir
        / "calibration_sources"
        / applied.json()["model"]["id"]
        / "nmass_latest.npy"
    )
    assert snapshot.exists()
