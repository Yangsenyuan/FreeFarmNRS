"""示例项目生成：合成多光谱/高光谱影像，并跑通预处理与反演。"""
from __future__ import annotations

import io
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import rasterio
from PIL import Image
from rasterio.transform import from_origin
from scipy.ndimage import gaussian_filter

from ..core.store import store
from ..core.utils import now_iso
from . import raster as R
from .inference import compact_summary, run_inversion
from .models import get_model
from .preprocess import run_preprocess
from .spectral_sim import simulate_spectra

DEMO_IDS = ["demo_corn", "demo_hyperspectral"]
MULTI_WL = np.array([475, 560, 668, 717, 840], dtype=float)
MULTI_NAMES = ["蓝光", "绿光", "红光", "红边", "近红外"]
MULTI_FWHM = [20, 20, 20, 20, 40]
HYPER_WL = np.arange(400, 901, 5, dtype=float)


def _field_nmass_veg(
    height: int, width: int, seed: int, stage: int = 0, base: float = 25.5
) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:height, 0:width]
    xn = xx / max(width - 1, 1)
    yn = yy / max(height - 1, 1)

    nmass = (
        base
        + stage * 0.45
        + 3.1 * np.sin(2 * np.pi * (xn * 0.95 + 0.12))
        + 2.3 * np.cos(2 * np.pi * (yn * 1.05 - 0.2))
        + 0.7 * np.sin(2 * np.pi * (xn + yn) * 2.4)
    )
    smooth = gaussian_filter(rng.normal(0, 1, (height, width)), 26)
    smooth = smooth / (smooth.std() + 1e-6)
    nmass = np.clip(nmass + smooth * 2.3, 19.0, 32.0)

    veg = (
        0.90
        + stage * 0.015
        + 0.018 * np.sin(2 * np.pi * (xn * 1.5 - 0.35))
        + 0.010 * np.cos(2 * np.pi * yn * 1.8)
    )
    # 田间道路
    veg[np.abs(yn - 0.53) < 0.016] = 0.05
    veg[np.abs(xn - 0.28) < 0.012] = 0.05
    # 裸土斑块
    for cx, cy, r in [(0.18, 0.20, 0.055), (0.72, 0.30, 0.045), (0.84, 0.78, 0.05), (0.40, 0.82, 0.035)]:
        veg[((xn - cx) ** 2 + (yn - cy) ** 2) < r * r] = 0.08
    veg = np.clip(veg + gaussian_filter(rng.normal(0, 0.008, (height, width)), 2), 0.02, 1.0)
    return nmass.astype(np.float32), veg.astype(np.float32)


def _write_gtiff(
    path: Path,
    dn: np.ndarray,
    wavelengths: np.ndarray,
    names: List[str],
    fwhm: List[float],
    res: float = 0.1,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    height, width = dn.shape[1], dn.shape[2]
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=width,
        height=height,
        count=dn.shape[0],
        dtype="uint16",
        crs="EPSG:32650",
        transform=from_origin(500000.0, 3500000.0, res, res),
        nodata=0,
        compress="deflate",
        predictor=2,
        tiled=True,
    ) as ds:
        ds.write(dn.astype(np.uint16))
        ds.update_tags(
            wavelength=", ".join(f"{w:g}" for w in wavelengths),
            wavelength_units="nm",
            fwhm=", ".join(f"{v:g}" for v in fwhm),
            sensor="DEMO",
        )
        for i, name in enumerate(names):
            ds.set_band_description(i + 1, f"{name} {wavelengths[i]:g}nm")


def _write_envi(
    path: Path,
    dn: np.ndarray,
    wavelengths: np.ndarray,
    fwhm: float,
    res: float = 0.1,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    height, width = dn.shape[1], dn.shape[2]
    with rasterio.open(
        path,
        "w",
        driver="ENVI",
        width=width,
        height=height,
        count=dn.shape[0],
        dtype="uint16",
        crs="EPSG:32650",
        transform=from_origin(500100.0, 3500100.0, res, res),
        interleave="bsq",
    ) as ds:
        ds.write(dn.astype(np.uint16))
        ds.update_tags(
            wavelength=", ".join(f"{w:g}" for w in wavelengths),
            wavelength_units="nm",
            fwhm=", ".join(f"{fwhm:g}" for _ in wavelengths),
            sensor="DEMO",
        )
        for i, w in enumerate(wavelengths):
            ds.set_band_description(i + 1, f"{w:g}nm")


def _save_cover(project_dir: Path, image_path: Path, out_name: str = "cover.png") -> None:
    try:
        png, _ = R.render_image_preview(image_path, mode="rgb", max_size=720)
        img = Image.open(io.BytesIO(png)).convert("RGBA")
        bg = Image.new("RGBA", img.size, (18, 30, 24, 255))
        bg.alpha_composite(img)
        bg.convert("RGB").save(project_dir / out_name, "PNG", optimize=True)
    except Exception:
        pass


def _make_multispectral_image(
    image_dir: Path, name: str, stage: int, seed: int
) -> Path:
    height, width = 600, 800
    nmass, veg = _field_nmass_veg(height, width, seed, stage=stage)
    rng = np.random.default_rng(seed + 100 + stage)
    refl = simulate_spectra(nmass, veg, MULTI_WL, noise=0.004, rng=rng)
    refl = gaussian_filter(refl, sigma=(0.8, 0.8, 0))
    dn = np.clip(refl * 10000.0, 1, 65535).astype(np.uint16)
    dn = np.transpose(dn, (2, 0, 1))
    path = image_dir / "source" / name
    _write_gtiff(path, dn, MULTI_WL, MULTI_NAMES, MULTI_FWHM)
    return path


def _make_hyperspectral_image(image_dir: Path, name: str) -> Path:
    height, width = 360, 480
    nmass, veg = _field_nmass_veg(height, width, seed=2026, stage=1, base=25.0)
    rng = np.random.default_rng(88)
    refl = simulate_spectra(nmass, veg, HYPER_WL, noise=0.0035, rng=rng)
    refl = gaussian_filter(refl, sigma=(0.8, 0.8, 0))
    dn = np.clip(refl * 10000.0, 1, 65535).astype(np.uint16)
    dn = np.transpose(dn, (2, 0, 1))
    path = image_dir / "source" / name
    _write_envi(path, dn, HYPER_WL, 5.0)
    return path


def _register_image(
    pid: str,
    image_dir: Path,
    source_path: Path,
    name: str,
    sensor: str,
    flight_date: str,
    field: str,
    fmt: str,
    note: str,
    iid: Optional[str] = None,
) -> Dict[str, Any]:
    meta = R.raster_meta(source_path)
    image = {
        "id": iid or image_dir.name,
        "name": name,
        "file": str(source_path.relative_to(image_dir)).replace("\\", "/"),
        "format": fmt,
        "sensor": sensor,
        "flight_date": flight_date,
        "field": field,
        "note": note,
        "imported_at": now_iso(),
        "status": "imported",
        "file_size": source_path.stat().st_size,
        **meta,
    }
    return store.add_image(pid, image)


def _process_and_invert(
    pid: str,
    image: Dict[str, Any],
    model_id: str,
    progress: Optional[Callable[[int, str, str], None]] = None,
) -> None:
    image_dir = store.image_dir(pid, image["id"])
    source_path = image_dir / image["file"]
    processed_dir = image_dir / "processed"
    results_dir = image_dir / "results"

    pmeta = run_preprocess(
        source_path,
        processed_dir,
        config=None,
        progress=(lambda p, s, m: progress(int(p * 0.6), s, m)) if progress else None,
    )
    store.update_image(
        pid,
        image["id"],
        {
            "status": "preprocessed",
            "preprocess": {
                "created_at": pmeta["created_at"],
                "quality": pmeta["quality"],
                "mask": pmeta["mask"],
                "bands": pmeta["bands"],
                "wavelengths": pmeta["wavelengths"],
                "config": pmeta["config"],
                "indices": pmeta["indices"],
            },
        },
    )
    store.append_log(
        pid,
        f"影像「{image['name']}」预处理完成，有效像元占比 {pmeta['mask']['ratio'] * 100:.1f}%",
        source="preprocess",
    )

    model = get_model(model_id)
    summary = run_inversion(
        image,
        processed_dir,
        model,
        results_dir,
        low=None,
        high=None,
        progress=(lambda p, s, m: progress(60 + int(p * 0.4), s, m)) if progress else None,
    )
    store.update_image(
        pid,
        image["id"],
        {
            "status": "inverted",
            "result": compact_summary(summary),
            "result_at": summary["created_at"],
        },
    )
    store.append_result_summary(pid, compact_summary(summary))
    store.append_log(
        pid,
        f"影像「{image['name']}」反演完成，均值 {summary['stats']['mean']} {summary.get('unit') or '待确认'}，模型「{model.get('name')}」",
        source="inversion",
    )
    preview = results_dir / "previews" / "classified.png"
    if preview.exists():
        try:
            R.save_cover_from_png(preview, store.project_dir(pid) / "cover.png")
        except Exception:
            pass


def _create_corn_project(progress=None) -> str:
    pid = "demo_corn"
    store.create_project(
        "夏玉米氮素监测示范",
        description="三期无人机多光谱影像，展示从数据导入到氮素分级专题图的完整流程。",
        crop="夏玉米",
        field="A区",
        note="演示项目：影像由平台内置光谱模拟器生成，仅用于功能展示。",
        project_id=pid,
    )
    dates = ["2026-06-10", "2026-07-02", "2026-07-15"]
    names = ["田块A_20260610_多光谱.tif", "田块A_20260702_多光谱.tif", "田块A_20260715_多光谱.tif"]
    images = []
    for stage, (date, name) in enumerate(zip(dates, names)):
        iid = f"demo_corn_img{stage + 1}"
        image_dir = store.image_dir(pid, iid)
        image_dir.mkdir(parents=True, exist_ok=True)
        source = _make_multispectral_image(image_dir, name, stage, seed=3100 + stage)
        image = _register_image(
            pid,
            image_dir,
            source,
            name,
            sensor="MicaSense RedEdge-P（示例）",
            flight_date=date,
            field="A区",
            fmt="GeoTIFF",
            note="演示数据：由平台光谱模拟器生成。",
            iid=iid,
        )
        images.append(image)
        if progress:
            progress(5 + stage * 8, "生成示例影像", f"已生成 {name}")
    _save_cover(store.project_dir(pid), store.image_dir(pid, images[0]["id"]) / images[0]["file"])
    store.append_log(pid, "示例项目已创建，开始预处理与反演演示", source="demo")

    for i, image in enumerate(images):
        _process_and_invert(
            pid,
            image,
            "rf_multispectral_demo",
            progress=(lambda p, s, m, i=i: progress(30 + i * 20 + int(p * 0.2), s, m)) if progress else None,
        )
    if progress:
        progress(92, "示例项目", "夏玉米示例项目已完成")
    return pid


def _create_hyperspectral_project(progress=None) -> str:
    pid = "demo_hyperspectral"
    store.create_project(
        "高光谱试验田",
        description="机载高光谱影像（ENVI 格式，400–900 nm / 5 nm），用于展示光谱曲线与 PLSR 反演。",
        crop="春玉米",
        field="B区",
        note="演示项目：影像由平台内置光谱模拟器生成，仅用于功能展示。",
        project_id=pid,
    )
    name = "试验田B_20260618_高光谱.dat"
    iid = "demo_hyper_img1"
    image_dir = store.image_dir(pid, iid)
    image_dir.mkdir(parents=True, exist_ok=True)
    source = _make_hyperspectral_image(image_dir, name)
    if progress:
        progress(4, "生成示例影像", "高光谱影像已生成")
    image = _register_image(
        pid,
        image_dir,
        source,
        name,
        sensor="Headwall Nano-Hyperspec（示例）",
        flight_date="2026-06-18",
        field="B区",
        fmt="ENVI",
        note="演示数据：ENVI 格式，101 个波段。",
        iid=iid,
    )
    _save_cover(store.project_dir(pid), source)
    store.append_log(pid, "高光谱示例项目已创建，开始预处理与 PLSR 反演", source="demo")
    _process_and_invert(
        pid,
        image,
        "plsr_nmass_demo",
        progress=(lambda p, s, m: progress(10 + int(p * 0.8), s, m)) if progress else None,
    )
    if progress:
        progress(95, "示例项目", "高光谱示例项目已完成")
    return pid


def demo_projects_exist() -> bool:
    return all((store.project_dir(pid) / "project.json").exists() for pid in DEMO_IDS)


def seed_demo(force: bool = False, progress=None) -> Dict[str, Any]:
    started = time.time()
    if force:
        for pid in DEMO_IDS:
            try:
                store.delete_project(pid)
            except Exception:
                pass
    if demo_projects_exist():
        return {
            "created": False,
            "project_ids": DEMO_IDS,
            "message": "示例项目已存在",
            "elapsed": round(time.time() - started, 1),
        }
    created = []
    if not (store.project_dir(DEMO_IDS[0]) / "project.json").exists():
        created.append(_create_corn_project(progress))
    if not (store.project_dir(DEMO_IDS[1]) / "project.json").exists():
        created.append(_create_hyperspectral_project(progress))
    if progress:
        progress(100, "完成", "示例项目创建完成")
    return {
        "created": True,
        "project_ids": created,
        "message": "示例项目创建完成",
        "elapsed": round(time.time() - started, 1),
    }
