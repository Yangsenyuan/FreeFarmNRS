from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.core.config import PROJECTS_DIR
from server.services.inference import run_inversion
from server.services.models import get_model


HYPER_PROCESSED = PROJECTS_DIR / "demo_hyperspectral" / "images" / "demo_hyper_img1" / "processed"
HYPER_PROJECT = PROJECTS_DIR / "demo_hyperspectral" / "project.json"


@pytest.mark.skipif(
    not HYPER_PROCESSED.exists() or not HYPER_PROJECT.exists(),
    reason="高光谱示例预处理数据不存在",
)
def test_result_summary_records_hash_alignment_and_preprocess(tmp_path):
    project = json.loads(HYPER_PROJECT.read_text(encoding="utf-8"))
    image = project["images"][0]
    summary = run_inversion(
        image,
        HYPER_PROCESSED,
        get_model("dnn_nmass_demo"),
        tmp_path,
        force_used=True,
        ignored_warnings=["测试确认"],
    )
    assert summary["model"]["name"] == "全谱 51 波段 Nmass-DNN 示例模型"
    assert len(summary["model"]["file_sha256"]) == 64
    assert len(summary["trace"]["model"]["training_wavelengths"]) == 51
    assert summary["trace"]["alignment"]["exact_bands"] == 51
    assert len(summary["trace"]["preprocessing"]["config_sha256"]) == 64
    assert summary["trace"]["force_used"] is True
    assert summary["trace"]["ignored_warnings"] == ["测试确认"]
    latest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    assert latest["trace"]["model"]["file_sha256"] == summary["model"]["file_sha256"]
