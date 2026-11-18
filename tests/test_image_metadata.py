from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from server.services.image_metadata import (
    image_fields_from_metadata,
    parse_image_metadata,
    read_envi_header,
    reparse_image_metadata,
    wavelength_from_label,
)

FIXTURES = Path(__file__).parent / "fixtures" / "metadata"
PLANET_DIR = Path(
    r"E:\Postgraduate_2\experiment\planetscope"
    r"\20251024PS_psscene_analytic_8b_sr_udm2\PSScene"
)
PLANET_DATASET = PLANET_DIR / "20251011_032408_70_24da_3B_AnalyticMS_SR_8b_clip.tif"


def _write_tiff(path: Path, count: int = 3, tags=None, descriptions=None):
    profile = {
        "driver": "GTiff",
        "height": 4,
        "width": 5,
        "count": count,
        "dtype": "float32",
        "crs": "EPSG:32650",
        "transform": from_origin(500000, 3500000, 10, 10),
    }
    with rasterio.open(path, "w", **profile) as ds:
        ds.write(np.ones((count, 4, 5), dtype=np.float32))
        if tags:
            ds.update_tags(**tags)
        if descriptions:
            for index, value in enumerate(descriptions, start=1):
                ds.set_band_description(index, value)


def test_label_wavelength_avoids_band_numbers():
    assert wavelength_from_label("B6 560nm") == 560
    assert wavelength_from_label("Band 8 (840 nm)") == 840
    assert wavelength_from_label("Band 6") is None


def test_standard_envi_header_and_raster(tmp_path):
    dataset = tmp_path / "scene.dat"
    profile = {
        "driver": "ENVI",
        "height": 3,
        "width": 4,
        "count": 3,
        "dtype": "uint16",
        "crs": "EPSG:32650",
        "transform": from_origin(500000, 3500000, 10, 10),
    }
    with rasterio.open(dataset, "w", **profile) as ds:
        ds.write(np.arange(36, dtype=np.uint16).reshape(3, 3, 4))
    (tmp_path / "scene.dat.hdr").write_text(
        "\n".join(
            [
                "ENVI",
                "samples = 4",
                "lines = 3",
                "bands = 3",
                "data type = 12",
                "sensor type = UAV Multispectral",
                "acquisition date = 2026-06-10",
                "field id = FIELD-A",
                "wavelength units = nm",
                "coordinate system string = {EPSG:32650}",
                "wavelength = {475, 560, 668}",
                "fwhm = {20, 20, 30}",
                "band names = {Blue 475nm, Green 560 nm, Red 668nm}",
                "data level = L2A",
            ]
        ),
        encoding="utf-8",
    )
    header = read_envi_header(dataset)
    assert header["encoding"] == "utf-8"
    result = parse_image_metadata(dataset)
    assert result["raster"]["width"] == 4
    assert result["raster"]["height"] == 3
    assert result["raster"]["bands"] == 3
    assert result["raster"]["crs"] == "EPSG:32650"
    assert result["wavelengths"] == [475.0, 560.0, 668.0]
    assert result["bandwidths"] == [20.0, 20.0, 30.0]
    assert result["flight_date"] == "2026-06-10"
    assert result["field_id"] == "FIELD-A"
    assert result["value_type"] == "uint16"
    assert result["processing_level"] == "L2A"
    assert result["metadata_sources"]["wavelengths"] == "envi_header"
    assert result["raw_metadata"]["envi_header"]["values"]["band names"].startswith("Blue")


@pytest.mark.parametrize(
    ("encoding", "bom"),
    [("utf-8-sig", True), ("gbk", False)],
)
def test_envi_bom_and_gbk(tmp_path, encoding, bom):
    dataset = tmp_path / f"encoded_{encoding}.dat"
    dataset.write_bytes(b"placeholder")
    text = "\n".join(
        [
            "ENVI",
            "bands = 2",
            "sensor type = 国产高光谱相机",
            "band names = {B1 560nm, B2 670nm}",
            "acquisition date = 20260614",
        ]
    )
    data = text.encode(encoding)
    if not bom and encoding == "utf-8-sig":
        data = text.encode("utf-8")
    (tmp_path / (dataset.name + ".hdr")).write_bytes(data)
    header = read_envi_header(dataset)
    expected_encoding = "utf-8-sig" if bom else "gb18030"
    assert header["encoding"].startswith(expected_encoding[:5])
    result = parse_image_metadata(dataset)
    assert result["sensor_type"] == "国产高光谱相机"
    assert result["wavelengths"] == [560.0, 670.0]
    assert result["flight_date"] == "2026-06-14"


def test_geotiff_dataset_and_band_tags(tmp_path):
    path = tmp_path / "tagged.tif"
    _write_tiff(
        path,
        count=3,
        tags={
            "sensor": "DEMO",
            "acquisition_date": "2026-06-10",
            "field_id": "P-12",
        },
        descriptions=["B1 475nm", "B2 560nm", "B3 668nm"],
    )
    with rasterio.open(path, "r+") as ds:
        ds.update_tags(1, FWHM="20")
        ds.update_tags(2, FWHM="20")
        ds.update_tags(3, FWHM="30")
    result = parse_image_metadata(path)
    assert result["sensor_name"] == "DEMO"
    assert result["metadata_sources"]["sensor_name"] == "geotiff_tags"
    assert result["flight_date"] == "2026-06-10"
    assert result["field_id"] == "P-12"
    assert result["wavelengths"] == [475.0, 560.0, 668.0]
    assert result["metadata_sources"]["wavelengths"] == "band_tags"
    assert result["bandwidths"] == [20.0, 20.0, 30.0]


def test_aux_xml_real_structure(tmp_path):
    path = tmp_path / "aux_scene.tif"
    _write_tiff(path, count=3, tags={"sensor": "TIFF-SENSOR"})
    shutil.copyfile(FIXTURES / "metadata.aux.xml", tmp_path / "aux_scene.tif.aux.xml")
    result = parse_image_metadata(path)
    assert result["sensor_name"] == "AUX-SENSOR"
    assert result["flight_date"] == "2026-06-11"
    assert result["field_id"] == "AUX-FIELD"
    assert result["processing_level"] == "L2A"
    assert result["wavelengths"] == [475.0, 560.0, 668.0]
    assert result["metadata_sources"]["wavelengths"] == "aux_xml"
    assert "PAMDataset" in result["raw_metadata"]["aux_xml"]["xml"]


def test_json_sidecar_and_bands(tmp_path):
    path = tmp_path / "json_scene.tif"
    _write_tiff(path, count=5)
    shutil.copyfile(FIXTURES / "metadata.json", tmp_path / "json_scene.json")
    result = parse_image_metadata(path)
    assert result["sensor_name"] == "RedEdge-P"
    assert result["sensor_type"] == "multispectral"
    assert result["flight_date"] == "2026-06-13"
    assert result["field_id"] == "JSON-FIELD-07"
    assert result["wavelengths"] == [475.0, 560.0, 668.0, 717.0, 842.0]
    assert result["bandwidths"] == [32.0, 27.0, 16.0, 12.0, 57.0]
    assert result["metadata_sources"]["wavelengths"] == "json_sidecar"
    assert result["raw_metadata"]["json_sidecar"]["document"]["schema"] == "farm-survey-metadata-v1"


def test_micasense_xmp_real_structure(tmp_path):
    path = tmp_path / "micasense.tif"
    _write_tiff(path, count=1)
    shutil.copyfile(FIXTURES / "micasense.xmp", tmp_path / "micasense.tif.xmp")
    result = parse_image_metadata(path)
    assert result["sensor_name"] == "Altum-PT"
    assert result["sensor_type"] == "Multispectral"
    assert result["acquisition_datetime"] == "2026-06-12T10:15:00+08:00"
    assert result["flight_date"] == "2026-06-12"
    assert result["field_id"] == "FIELD-A03"
    assert result["wavelengths"] == [560.0]
    assert result["bandwidths"] == [20.0]
    assert result["metadata_sources"]["sensor_name"] == "xmp_sidecar"
    assert result["raw_metadata"]["xmp_sidecar"]["values"]["Model"] == "Altum-PT"


def test_filename_rule(tmp_path):
    path = tmp_path / "田块A_20260610_多光谱.tif"
    _write_tiff(path, count=3)
    result = parse_image_metadata(path)
    assert result["field_id"] == "田块A"
    assert result["flight_date"] == "2026-06-10"
    assert result["image_type"] == "多光谱"
    assert result["metadata_sources"]["field_id"] == "filename_rule"
    assert result["metadata_confidence"]["field_id"] < result["metadata_confidence"]["value_type"]


def test_user_values_override_and_survive_reparse(tmp_path):
    path = tmp_path / "田块A_20260610_多光谱.tif"
    _write_tiff(
        path,
        count=3,
        tags={"sensor": "HEADER-SENSOR", "acquisition_date": "2026-06-10"},
        descriptions=["B1 475nm", "B2 560nm", "B3 668nm"],
    )
    user = {
        "sensor": "USER-CAMERA",
        "flight_date": "2026-06-20",
        "field": "USER-FIELD",
    }
    first = parse_image_metadata(path, user_metadata=user)
    assert first["sensor_name"] == "USER-CAMERA"
    assert first["metadata_sources"]["sensor_name"] == "user"
    assert first["flight_date"] == "2026-06-20"
    assert first["field_id"] == "USER-FIELD"

    second = reparse_image_metadata(image_fields_from_metadata(first), parse_image_metadata(path))
    assert second["merged"]["sensor_name"] == "USER-CAMERA"
    assert second["merged"]["flight_date"] == "2026-06-20"
    assert second["merged"]["field_id"] == "USER-FIELD"
    assert set(second["preserved_user_fields"]) >= {"sensor_name", "flight_date", "field_id"}
    assert second["conflicts"]


def test_old_project_without_sources_is_conservative(tmp_path):
    path = tmp_path / "new_parse.tif"
    _write_tiff(path, count=3, tags={"sensor": "AUTO-NEW", "field_id": "AUTO-FIELD"})
    incoming = parse_image_metadata(path)
    existing = {
        "sensor": "OLD-MANUAL",
        "field": "OLD-FIELD",
        "flight_date": "2026-05-01",
        "wavelengths": [475, 560, 668],
        "bandwidths": [20, 20, 30],
    }
    merged = reparse_image_metadata(existing, incoming)
    assert merged["merged"]["sensor_name"] == "OLD-MANUAL"
    assert merged["merged"]["field_id"] == "OLD-FIELD"
    assert merged["merged"]["flight_date"] == "2026-05-01"


@pytest.mark.skipif(not PLANET_DATASET.exists(), reason="用户本地 PlanetScope 验收数据不存在")
def test_real_planetscope_stac_xml_and_cloud_conflict():
    result = parse_image_metadata(PLANET_DATASET)
    assert result["sensor_name"] == "PlanetScope"
    assert result["instrument"] == "PSB.SD"
    assert result["scene_id"] == "20251011_032408_70_24da"
    assert result["acquisition_datetime"] == "2025-10-11T03:24:08Z"
    assert result["flight_date"] == "2025-10-11"
    assert result["wavelengths"] == [442.0, 490.0, 531.0, 565.0, 610.0, 665.0, 705.0, 865.0]
    assert result["bandwidths"] == [21.0, 50.0, 36.0, 36.0, 20.0, 30.0, 16.0, 40.0]
    assert result["value_type"] == "surface_reflectance"
    assert result["processing_level"] == "L3B"
    assert result["reflectance_scale"] == 0.0001
    assert result["epsg"] == 32650
    assert result["metadata_sources"]["sensor_name"] == "xml_sidecar"
    assert result["metadata_sources"]["wavelengths"] == "json_sidecar"
    assert result["field_id"] == ""
    conflict = result["metadata_conflicts"][0]
    values = sorted(item["value"] for item in conflict["values"])
    assert values == [0.0, 0.0, 31.27]
    assert "人工确认" in conflict["message"]
    assert result["raw_metadata"]["xml_sidecar"]
