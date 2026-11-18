from __future__ import annotations

from pathlib import Path

import numpy as np
from pypdf import PdfReader

from server.services.report import export_result_pdf, export_result_png


def _summary(result_dir: Path) -> dict:
    values = np.linspace(18.0, 34.0, 20 * 24, dtype=np.float32).reshape(20, 24)
    np.save(result_dir / "nmass_latest.npy", values)
    return {
        "id": "res_test",
        "image_id": "img_test",
        "image_name": "20251011 PlanetScope test",
        "flight_date": "2025-10-11",
        "created_at": "2026-11-17T12:00:00+08:00",
        "model": {
            "id": "test_model",
            "name": "非标准单位测试模型",
            "algorithm": "PLSR",
            "version": "2.1",
            "crops": ["蔬菜"],
            "sensors": ["PlanetScope"],
            "training_data": "独立测试数据",
            "r2": 0.81,
            "rmse": 2.3,
            "metric_type": "independent_test",
            "file_sha256": "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
        },
        "unit": "g/kg",
        "thresholds": {"low": 220.0, "high": 280.0, "unit": "g/kg"},
        "stats": {
            "count": 480,
            "total_count": 480,
            "valid_ratio": 0.8,
            "mean": 260.0,
            "std": 12.0,
            "cv": 4.6,
            "min": 180.0,
            "max": 340.0,
            "p5": 185.0,
            "p25": 220.0,
            "p50": 260.0,
            "p75": 280.0,
            "p95": 330.0,
        },
        "class_stats": [
            {
                "key": "low",
                "name": "低氮",
                "ratio": 0.25,
                "area_mu": 1.2,
                "action": "需追肥",
                "range": {"min": None, "max": 220.0},
            },
            {
                "key": "suitable",
                "name": "适宜",
                "ratio": 0.5,
                "area_mu": 2.4,
                "action": "保持现状",
                "range": {"min": 220.0, "max": 280.0},
            },
            {
                "key": "high",
                "name": "高氮",
                "ratio": 0.25,
                "area_mu": 1.2,
                "action": "控制施肥",
                "range": {"min": 280.0, "max": None},
            },
        ],
        "classes": [],
        "histogram": {
            "edges": [180 + index * 10 for index in range(17)],
            "counts": [20] * 16,
            "mean": 260.0,
            "thresholds": [220.0, 280.0],
        },
        "area": {
            "pixel_area_m2": 9.0,
            "valid_mu": 4.8,
            "valid_ha": 0.32,
            "total_mu": 6.0,
            "total_ha": 0.4,
        },
        "width": 24,
        "height": 20,
        "crs": "EPSG:32650",
        "transform": [3, 0, 209559, 0, -3, 2548575],
        "files": {"tif": "result.tif", "npy": "nmass_latest.npy"},
        "trace": {
            "model": {
                "training_wavelengths": list(range(400, 901, 10)),
                "file_sha256": "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
                "synthetic": True,
            },
            "alignment": {
                "source_range": [442.0, 865.0],
                "target_range": [400.0, 900.0],
                "exact_bands": 2,
                "interpolated_bands": 40,
                "edge_filled_bands": 9,
                "edge_fill_ratio": 0.17647,
            },
            "preprocessing": {
                "config_sha256": "preprocess-sha-123456",
                "quality_level": "notice",
                "mask_ratio": 0.73,
                "pipeline": "反射率转换 → 平滑 → 波段对齐 → 掩膜",
            },
            "force_used": True,
            "ignored_warnings": ["流程演示确认"],
        },
    }


def test_pdf_and_png_use_actual_unit_and_trace(tmp_path):
    summary = _summary(tmp_path)
    project = {"name": "PlanetScope 案例", "field": "恒福菜场"}
    image = {
        "name": summary["image_name"],
        "sensor": "PlanetScope",
        "sensor_name": "PlanetScope",
        "image_type": "卫星多光谱",
        "flight_date": "2025-10-11",
        "field": "恒福菜场",
    }
    pdf = export_result_pdf(summary, project, image, tmp_path, tmp_path / "report.pdf")
    png = export_result_png(summary, tmp_path, tmp_path / "map.png")
    assert pdf.exists() and pdf.stat().st_size > 1000
    assert png.exists() and png.stat().st_size > 1000
    text = "\n".join(page.extract_text() or "" for page in PdfReader(str(pdf)).pages)
    assert "g/kg" in text
    assert "abcdef1234567890" in text
    assert "采集日期" in text
    assert "preprocess-sha-123456" in text
    assert "强制忽略警告：是" in text
