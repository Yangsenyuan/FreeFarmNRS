from __future__ import annotations

from fastapi.testclient import TestClient

from server.core.store import store
from server.main import app


def _setup_project(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setattr(store, "root", root)
    project = store.create_project(
        "兼容性测试",
        crop="夏玉米",
        field="TEST-A",
        project_id="compat_test",
    )
    exact_wl = [float(v) for v in range(400, 901, 10)]
    store.add_image(
        project["id"],
        {
            "id": "compat_exact",
            "name": "exact.tif",
            "file": "source/exact.tif",
            "bands": 51,
            "wavelengths": exact_wl,
            "sensor": "高光谱",
            "flight_date": "2026-06-10",
            "status": "imported",
        },
    )
    store.add_image(
        project["id"],
        {
            "id": "compat_disjoint",
            "name": "disjoint.tif",
            "file": "source/disjoint.tif",
            "bands": 3,
            "wavelengths": [2000.0, 2010.0, 2020.0],
            "sensor": "高光谱",
            "flight_date": "2026-06-11",
            "status": "imported",
        },
    )
    return project


def test_single_and_batch_model_compatibility_tables(tmp_path, monkeypatch):
    _setup_project(tmp_path, monkeypatch)
    client = TestClient(app)

    response = client.get(
        "/api/projects/compat_test/images/compat_exact/model-compatibility"
    )
    assert response.status_code == 200
    payload = response.json()
    by_id = {item["model_id"]: item for item in payload["assessments"]}
    assert by_id["dnn_nmass_demo"]["status"] == "compatible"
    assert by_id["dnn_nmass_demo"]["exact_bands"] == 51
    assert by_id["rf_multispectral_demo"]["status"] == "warning"

    batch = client.post(
        "/api/projects/compat_test/batch-model-compatibility",
        json={
            "image_ids": ["compat_exact", "compat_disjoint"],
            "model_id": "dnn_nmass_demo",
        },
    )
    assert batch.status_code == 200
    rows = batch.json()["images"]
    assert rows[0]["assessment"]["status"] == "compatible"
    assert rows[1]["assessment"]["status"] == "incompatible"
    assert batch.json()["can_run"] is False


def test_server_rejects_warning_without_force(tmp_path, monkeypatch):
    _setup_project(tmp_path, monkeypatch)
    client = TestClient(app)
    response = client.post(
        "/api/projects/compat_test/images/compat_exact/inversions",
        json={
            "model_id": "rf_multispectral_demo",
            "auto_preprocess": True,
            "force": False,
        },
    )
    assert response.status_code == 409
    assert "显式确认" in response.json()["detail"]


def test_server_rejects_batch_warning_without_force(tmp_path, monkeypatch):
    _setup_project(tmp_path, monkeypatch)
    client = TestClient(app)
    response = client.post(
        "/api/projects/compat_test/batch-inversions",
        json={
            "image_ids": ["compat_exact"],
            "model_id": "rf_multispectral_demo",
            "auto_preprocess": True,
            "force": False,
        },
    )
    assert response.status_code == 409
    assert "逐幅预检" in response.json()["detail"]


def test_server_rejects_incompatible_image(tmp_path, monkeypatch):
    _setup_project(tmp_path, monkeypatch)
    client = TestClient(app)
    response = client.post(
        "/api/projects/compat_test/images/compat_disjoint/inversions",
        json={
            "model_id": "dnn_nmass_demo",
            "auto_preprocess": True,
            "force": True,
        },
    )
    assert response.status_code == 400
    assert "完全无可用交集" in response.json()["detail"]
