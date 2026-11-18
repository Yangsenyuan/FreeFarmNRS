"""用示例多光谱/高光谱影像实际验证新增 DNN 模型。

运行：
    .venv\\Scripts\\python.exe -m server.tools.verify_dnn_models

本脚本只读取现有示例项目的预处理结果，并把本次验收结果写入系统临时目录，
不会覆盖 data/projects 中的用户项目或既有反演结果。
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from server.core.config import PROJECTS_DIR
from server.services.inference import run_inversion
from server.services.models import get_model


CASES = [
    ("demo_corn", "demo_corn_img1", "dnn_multispectral_demo"),
    ("demo_corn", "demo_corn_img1", "rf_multispectral_demo"),
    ("demo_hyperspectral", "demo_hyper_img1", "dnn_nmass_demo"),
    ("demo_hyperspectral", "demo_hyper_img1", "plsr_nmass_demo"),
]


def _image_meta(project_id: str, image_id: str) -> dict:
    project = json.loads(
        (PROJECTS_DIR / project_id / "project.json").read_text(encoding="utf-8")
    )
    for image in project.get("images", []):
        if image.get("id") == image_id:
            return image
    raise FileNotFoundError(f"{project_id}/{image_id}")


def main() -> None:
    rows = []
    with tempfile.TemporaryDirectory(prefix="nmass-dnn-verify-") as tmp:
        root = Path(tmp)
        for project_id, image_id, model_id in CASES:
            image = _image_meta(project_id, image_id)
            processed = PROJECTS_DIR / project_id / "images" / image_id / "processed"
            if not (processed / "meta.json").exists():
                raise FileNotFoundError(f"示例预处理结果不存在：{processed}")
            out_dir = root / f"{image_id}-{model_id}"
            summary = run_inversion(
                image,
                processed,
                get_model(model_id),
                out_dir,
            )
            rows.append(
                {
                    "model_id": model_id,
                    "model_name": summary["model"]["name"],
                    "image_id": image_id,
                    "bands": image.get("bands"),
                    "mean": summary["stats"]["mean"],
                    "std": summary["stats"]["std"],
                    "unit": summary["unit"],
                    "class_ratio": {
                        item["key"]: item["ratio"] for item in summary["class_stats"]
                    },
                    "alignment": summary["trace"]["alignment"],
                    "file_sha256": summary["trace"]["model"]["file_sha256"],
                }
            )
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
