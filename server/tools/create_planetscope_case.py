"""建立用户提供的 PlanetScope 本地展示项目。

原始数据只复制到该展示项目的 source 目录，不进入测试夹具或公开示例。
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from typing import Dict, Iterable

from server.core.config import ensure_dirs
from server.core.store import ProjectNotFound, store
from server.core.utils import now_iso
from server.services import raster as R

PROJECT_ID = "planet_hengyu_case"
IMAGE_ID = "planet_hengyu_img1"
LOCATION = "广东省惠州市惠城区恒福菜场"
DEFAULT_SOURCE_DIR = Path(
    r"E:\Postgraduate_2\experiment\planetscope"
    r"\20251024PS_psscene_analytic_8b_sr_udm2\PSScene"
)
SCENE_ID = "20251011_032408_70_24da"
DATASET_NAME = f"{SCENE_ID}_3B_AnalyticMS_SR_8b_clip.tif"
SIDECARS = (
    f"{SCENE_ID}.json",
    f"{SCENE_ID}_metadata.json",
    f"{SCENE_ID}_3B_AnalyticMS_8b_metadata_clip.xml",
)


def _copy_files(source_dir: Path, target_dir: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    names: Iterable[str] = (DATASET_NAME, *SIDECARS)
    for name in names:
        source = source_dir / name
        if not source.exists():
            raise FileNotFoundError(f"用户提供的 PlanetScope 文件不存在：{source}")
        shutil.copy2(source, target_dir / name)
    return target_dir / DATASET_NAME


def create_case(source_dir: Path = DEFAULT_SOURCE_DIR) -> Dict[str, object]:
    ensure_dirs()
    try:
        project = store.get_project(PROJECT_ID)
        created = False
    except ProjectNotFound:
        project = store.create_project(
            name="惠州恒福菜场 PlanetScope 监测案例",
            description=(
                "用户提供的 PlanetScope PSScene 8 波段地表反射率裁剪影像。"
                "仅用于本地平台展示；内置模型结果仅用于流程演示，不代表真实田间标定精度。"
            ),
            crop="蔬菜",
            field=LOCATION,
            note="用户提供的 PlanetScope PSScene 地表反射率数据",
            project_id=PROJECT_ID,
        )
        created = True

    project = store.update_project(
        PROJECT_ID,
        {
            "name": "惠州恒福菜场 PlanetScope 监测案例",
            "crop": "蔬菜",
            "field": LOCATION,
            "description": project.get("description")
            or (
                "用户提供的 PlanetScope PSScene 8 波段地表反射率裁剪影像。"
                "仅用于本地平台展示；内置模型结果仅用于流程演示，不代表真实田间标定精度。"
            ),
        },
    )
    if any(image.get("id") == IMAGE_ID for image in project.get("images", [])):
        existing = store.get_image(PROJECT_ID, IMAGE_ID)
        dataset_path = store.image_dir(PROJECT_ID, IMAGE_ID) / str(existing.get("file") or "")
        if dataset_path.exists():
            refreshed = R.raster_meta(dataset_path, user_metadata={"field_id": LOCATION})
            sources = dict(existing.get("metadata_sources") or {})
            fields = dict(existing.get("metadata_fields") or {})
            fields["flight_date"] = refreshed.get("metadata_fields", {}).get("flight_date") or fields.get("flight_date")
            fields["field_id"] = {
                "value": LOCATION,
                "source": "user",
                "confidence": 1.0,
                "raw_key": "user:location",
                "raw_value": LOCATION,
            }
            sources.update(
                {
                    "flight_date": refreshed.get("metadata_sources", {}).get("flight_date", ""),
                    "field_id": "user",
                }
            )
            metadata = dict(existing.get("metadata") or {})
            metadata.update(
                {
                    "flight_date": refreshed.get("flight_date") or "2025-10-11",
                    "field_id": LOCATION,
                    "metadata_sources": sources,
                    "metadata_fields": fields,
                }
            )
            existing = store.update_image(
                PROJECT_ID,
                IMAGE_ID,
                {
                    "flight_date": refreshed.get("flight_date") or "2025-10-11",
                    "field": LOCATION,
                    "field_id": LOCATION,
                    "metadata": metadata,
                    "metadata_fields": fields,
                    "metadata_sources": sources,
                },
            )
        return {"project": project, "image": existing, "created": False, "reused": True}

    image_dir = store.image_dir(PROJECT_ID, IMAGE_ID)
    dataset_path = _copy_files(Path(source_dir), image_dir / "source")
    meta = R.raster_meta(
        dataset_path,
        user_metadata={
            "field_id": LOCATION,
        },
    )
    image = {
        "id": IMAGE_ID,
        "name": dataset_path.name,
        "file": str(dataset_path.relative_to(image_dir)).replace("\\", "/"),
        "format": "GeoTIFF",
        "sensor": meta.get("sensor") or "PlanetScope",
        "sensor_name": meta.get("sensor_name") or "PlanetScope",
        "sensor_type": meta.get("sensor_type") or "satellite",
        "flight_date": meta.get("flight_date") or "2025-10-11",
        "field": meta.get("field") or LOCATION,
        "field_id": meta.get("field_id") or LOCATION,
        "note": "用户提供的 PlanetScope PSScene 地表反射率数据",
        "imported_at": now_iso(),
        "status": "imported",
        "file_size": dataset_path.stat().st_size,
        "data_source": "user_provided_local_planetscope",
        "scene_id": SCENE_ID,
        **meta,
    }
    # 用户指定地点高于自动解析结果。
    image["field"] = LOCATION
    image["field_id"] = LOCATION
    image.setdefault("metadata_sources", {})
    image["metadata_sources"]["field_id"] = "user"
    image["metadata_fields"] = dict(image.get("metadata_fields") or {})
    image["metadata_fields"]["field_id"] = {
        "value": LOCATION,
        "source": "user",
        "confidence": 1.0,
        "raw_key": "user:location",
        "raw_value": LOCATION,
    }
    metadata = dict(image.get("metadata") or {})
    metadata["field_id"] = LOCATION
    metadata["metadata_sources"] = image["metadata_sources"]
    metadata["metadata_fields"] = image["metadata_fields"]
    image["metadata"] = metadata
    store.add_image(PROJECT_ID, image)
    store.append_log(
        PROJECT_ID,
        f"导入用户提供的 PlanetScope 案例影像「{dataset_path.name}」",
        source="import",
    )
    return {
        "project": store.get_project(PROJECT_ID),
        "image": image,
        "created": created,
        "reused": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", default=str(DEFAULT_SOURCE_DIR))
    args = parser.parse_args()
    result = create_case(Path(args.source_dir))
    print(
        f"项目 {PROJECT_ID}；影像 {result['image']['id']}；"
        f"新建项目={result['created']}；复用={result['reused']}"
    )
    print(f"源数据已复制到本地展示项目：{Path(args.source_dir)} -> data/projects/{PROJECT_ID}")


if __name__ == "__main__":
    main()
