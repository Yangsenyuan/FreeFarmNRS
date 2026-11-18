from __future__ import annotations

import json

import numpy as np
import rasterio
from fastapi.testclient import TestClient
from rasterio.transform import from_origin

from server.core.store import store
from server.main import app


def _tiff_bytes() -> bytes:
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "scene.tif"
        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            height=8,
            width=9,
            count=3,
            dtype="float32",
            crs="EPSG:32650",
            transform=from_origin(500000, 3500000, 3, 3),
        ) as ds:
            ds.write(np.ones((3, 8, 9), dtype=np.float32))
            for index, label in enumerate(("Blue 475nm", "Green 560nm", "Red 668nm"), start=1):
                ds.set_band_description(index, label)
        return path.read_bytes()


def test_upload_sidecar_and_reparse_preserve_user(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setattr(store, "root", root)
    store.create_project("侧车测试", crop="玉米", project_id="metadata_api")
    client = TestClient(app)
    sidecar = {
        "sensor": "SIDECAR-SENSOR",
        "field_id": "SIDECAR-FIELD",
        "flight_date": "2026-06-10",
        "value_type": "surface_reflectance",
    }
    response = client.post(
        "/api/projects/metadata_api/images",
        files=[
            ("files", ("scene.tif", _tiff_bytes(), "image/tiff")),
            (
                "files",
                (
                    "scene.json",
                    json.dumps(sidecar).encode("utf-8"),
                    "application/json",
                ),
            ),
        ],
        data={"sensor": "", "flight_date": "", "field": "", "note": ""},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert not payload["errors"]
    image = payload["imported"][0]
    assert image["sensor"] == "SIDECAR-SENSOR"
    assert image["field_id"] == "SIDECAR-FIELD"
    assert image["flight_date"] == "2026-06-10"
    assert image["metadata_sources"]["sensor_name"] == "json_sidecar"

    image_id = image["id"]
    patch = client.patch(
        f"/api/projects/metadata_api/images/{image_id}",
        json={"sensor": "USER-SENSOR"},
    )
    assert patch.status_code == 200
    assert patch.json()["metadata_sources"]["sensor_name"] == "user"

    reparse = client.post(
        f"/api/projects/metadata_api/images/{image_id}/reparse-metadata?apply=true"
    )
    assert reparse.status_code == 200, reparse.text
    body = reparse.json()
    assert "sensor_name" in body["preserved_user_fields"]
    assert body["image"]["sensor"] == "USER-SENSOR"
    assert body["image"]["metadata_sources"]["sensor_name"] == "user"
