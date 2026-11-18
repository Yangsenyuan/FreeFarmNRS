from pathlib import Path

from server.services.model_compatibility import assess_model_compatibility


def _model(tmp_path: Path, wavelengths, *, sensor="高光谱", crops=None):
    path = tmp_path / "model.pkl"
    path.write_bytes(b"test-model")
    return {
        "id": "test_model",
        "name": "测试模型",
        "available": True,
        "_dir": str(tmp_path),
        "file": path.name,
        "crops": crops if crops is not None else ["玉米"],
        "sensors": [sensor],
        "input": {"type": "spectra", "wavelengths": wavelengths},
    }


def _image(wavelengths, *, sensor="高光谱", crop="夏玉米"):
    return {
        "id": "image",
        "bands": len(wavelengths),
        "wavelengths": wavelengths,
        "sensor": sensor,
        "project_crop": crop,
    }


def test_exact_band_match_is_compatible(tmp_path):
    wavelengths = [float(v) for v in range(400, 901, 10)]
    result = assess_model_compatibility(
        _image(wavelengths), None, _model(tmp_path, wavelengths)
    )
    assert result["status"] == "compatible"
    assert result["crop_match"] == "match"
    assert result["sensor_match"] == "match"
    assert result["required_bands"] == 51
    assert result["exact_bands"] == 51
    assert result["interpolated_bands"] == 0
    assert result["edge_filled_bands"] == 0


def test_inside_range_requires_resampling(tmp_path):
    source = [float(v) for v in range(400, 901, 20)]
    target = [float(v) for v in range(400, 901, 10)]
    result = assess_model_compatibility(
        _image(source), None, _model(tmp_path, target)
    )
    assert result["status"] == "compatible"
    assert result["exact_bands"] == 26
    assert result["interpolated_bands"] == 25
    assert result["edge_filled_bands"] == 0
    assert result["card_status"] == "resample"


def test_high_edge_fill_is_warning(tmp_path):
    source = [float(v) for v in range(500, 801, 10)]
    target = [float(v) for v in range(400, 901, 10)]
    result = assess_model_compatibility(
        _image(source), None, _model(tmp_path, target)
    )
    assert result["status"] == "warning"
    assert result["edge_filled_bands"] == 20
    assert result["edge_fill_ratio"] >= 0.2
    assert any("边缘填充" in warning for warning in result["warnings"])


def test_disjoint_range_is_incompatible(tmp_path):
    source = [450.0, 500.0, 550.0]
    target = [2000.0, 2010.0, 2020.0]
    result = assess_model_compatibility(
        _image(source), None, _model(tmp_path, target)
    )
    assert result["status"] == "incompatible"
    assert result["errors"]


def test_missing_model_file_is_incompatible(tmp_path):
    target = [400.0, 410.0, 420.0]
    model = _model(tmp_path, target)
    (tmp_path / "model.pkl").unlink()
    result = assess_model_compatibility(_image(target), None, model)
    assert result["status"] == "incompatible"
    assert any("模型文件缺失" in error for error in result["errors"])


def test_crop_mismatch_requires_confirmation(tmp_path):
    target = [400.0, 410.0, 420.0]
    result = assess_model_compatibility(
        _image(target, crop="小麦"), None, _model(tmp_path, target, crops=["玉米"])
    )
    assert result["status"] == "warning"
    assert result["crop_match"] == "mismatch"


def test_missing_metadata_remains_unknown(tmp_path):
    target = [400.0, 410.0, 420.0]
    result = assess_model_compatibility(
        _image(target, sensor="", crop=""), None, _model(tmp_path, target)
    )
    assert result["crop_match"] == "unknown"
    assert result["sensor_match"] == "unknown"
    assert result["status"] == "warning"


def test_vi_model_can_assess_required_bands_without_file():
    model = {
        "id": "vi",
        "name": "VI",
        "available": True,
        "crops": ["玉米"],
        "sensors": ["多光谱"],
        "input": {
            "type": "vi",
            "features": [
                {"index": "NDRE", "bands": {"nir": 840, "red_edge": 730}},
                {"index": "NDVI", "bands": {"nir": 840, "red": 670}},
            ],
        },
    }
    image = _image([550.0, 670.0, 730.0, 840.0], sensor="多光谱")
    result = assess_model_compatibility(image, None, model)
    assert result["status"] == "compatible"
    assert result["required_bands"] == 2
    assert result["required_spectral_bands"] == 3
    assert result["exact_bands"] == 3
