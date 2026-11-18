"""数据预处理流水线。

流程：反射率转换 → 坏波段剔除 → 光谱平滑 → 波段对齐 → 云/植被掩膜 → 植被指数 → 质量检查。
所有步骤参数可见可调，默认参数保证可以一键跑通。
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import rasterio
from pydantic import BaseModel, Field, model_validator
from rasterio.windows import Window
from scipy.signal import savgol_filter

from ..core.config import MAX_LOAD_VALUES
from ..core.utils import now_iso
from . import raster as R

ProgressCb = Optional[Callable[[int, str, str], None]]


class ReflectanceConfig(BaseModel):
    enabled: bool = True
    method: str = "scale"  # scale | none | panel
    scale: float = 10000.0
    coefficient: float = 1.0
    offset: float = 0.0

    @model_validator(mode="after")
    def _check_scale(self):
        if self.method == "scale" and self.scale == 0:
            raise ValueError("反射率比例因子不能为 0")
        if self.method not in ("scale", "none", "panel"):
            raise ValueError("未知的反射率转换方式")
        return self


class SmoothingConfig(BaseModel):
    enabled: bool = True
    window: int = 5
    polyorder: int = 2


class BadBandsConfig(BaseModel):
    enabled: bool = False
    ranges: List[List[float]] = Field(default_factory=lambda: [[1340, 1450], [1800, 1960]])


class AlignConfig(BaseModel):
    enabled: bool = True
    start: float = 400.0
    end: float = 900.0
    step: float = 10.0
    extrapolate: bool = True


class MaskConfig(BaseModel):
    enabled: bool = True
    ndvi_threshold: float = 0.35
    nir_wavelength: float = 840.0
    red_wavelength: float = 670.0


class CloudConfig(BaseModel):
    enabled: bool = False
    blue_threshold: float = 0.30


class CropConfig(BaseModel):
    enabled: bool = False
    x0: int = 0
    y0: int = 0
    width: int = 0
    height: int = 0


class PreprocessConfig(BaseModel):
    reflectance: ReflectanceConfig = Field(default_factory=ReflectanceConfig)
    smoothing: SmoothingConfig = Field(default_factory=SmoothingConfig)
    bad_bands: BadBandsConfig = Field(default_factory=BadBandsConfig)
    alignment: AlignConfig = Field(default_factory=AlignConfig)
    mask: MaskConfig = Field(default_factory=MaskConfig)
    cloud: CloudConfig = Field(default_factory=CloudConfig)
    crop: CropConfig = Field(default_factory=CropConfig)
    indices: List[str] = Field(default_factory=lambda: ["NDVI", "NDRE", "CIredge"])

    @model_validator(mode="after")
    def _check_pipeline(self):
        if self.smoothing.enabled:
            if self.smoothing.window < 3:
                raise ValueError("Savitzky-Golay 平滑窗口至少为 3")
            if self.smoothing.polyorder < 1 or self.smoothing.polyorder >= self.smoothing.window:
                raise ValueError("平滑多项式阶数必须小于窗口长度")
        if self.smoothing.window % 2 == 0:
            self.smoothing.window += 1
        if self.alignment.enabled:
            if self.alignment.end <= self.alignment.start or self.alignment.step <= 0:
                raise ValueError("波段对齐范围或步长不合理")
            steps = (self.alignment.end - self.alignment.start) / self.alignment.step + 1
            if steps > 400:
                raise ValueError("波段对齐目标波段过多（>400），请增大步长")
        for rng in self.bad_bands.ranges:
            if len(rng) != 2 or rng[1] < rng[0]:
                raise ValueError("坏波段范围格式不正确")
        return self


def default_config() -> Dict[str, Any]:
    return PreprocessConfig().model_dump()


def _progress(cb: ProgressCb, percent: int, stage: str, message: str) -> None:
    if cb:
        cb(int(percent), stage, message)


def _pct(values: np.ndarray, q: float) -> Optional[float]:
    if values.size == 0:
        return None
    v = float(np.nanpercentile(values, q))
    return v if math.isfinite(v) else None


def compute_index(
    name: str,
    data: np.ndarray,
    wavelengths: Sequence[float],
    tolerance: float = 70.0,
) -> Tuple[Optional[np.ndarray], Optional[Dict[str, Any]], Optional[str]]:
    """计算单个植被指数。data: (B,H,W)。返回 (array, info, reason)。"""
    if not wavelengths:
        return None, None, "缺少波段中心波长信息"
    wl = [float(v) for v in wavelengths]

    def band(target: float) -> Optional[int]:
        return R.nearest_band_index(wl, target, tolerance)

    name_upper = name.upper()
    if name_upper == "NDVI":
        nir_i, red_i = band(840), band(670)
        if nir_i is None or red_i is None:
            return None, None, "缺少近红外或红光波段"
        nir, red = data[nir_i], data[red_i]
        denom = nir + red
        arr = np.where(np.abs(denom) > 1e-6, (nir - red) / denom, np.nan)
        info = {"nir": 840, "red": 670, "nir_band": nir_i + 1, "red_band": red_i + 1}
        label = "归一化植被指数"
    elif name_upper == "NDRE":
        nir_i, re_i = band(840), band(730)
        if nir_i is None or re_i is None:
            return None, None, "缺少近红外或红边波段"
        nir, re = data[nir_i], data[re_i]
        denom = nir + re
        arr = np.where(np.abs(denom) > 1e-6, (nir - re) / denom, np.nan)
        info = {"nir": 840, "red_edge": 730, "nir_band": nir_i + 1, "red_edge_band": re_i + 1}
        label = "红边归一化差值指数"
    elif name_upper == "CIREDGE":
        nir_i, re_i = band(840), band(730)
        if nir_i is None or re_i is None:
            return None, None, "缺少近红外或红边波段"
        nir, re = data[nir_i], data[re_i]
        arr = np.where(np.abs(re) > 1e-6, nir / re - 1.0, np.nan)
        info = {"nir": 840, "red_edge": 730, "nir_band": nir_i + 1, "red_edge_band": re_i + 1}
        label = "红边叶绿素指数"
    elif name_upper == "GNDVI":
        nir_i, green_i = band(840), band(550)
        if nir_i is None or green_i is None:
            return None, None, "缺少近红外或绿光波段"
        nir, green = data[nir_i], data[green_i]
        denom = nir + green
        arr = np.where(np.abs(denom) > 1e-6, (nir - green) / denom, np.nan)
        info = {"nir": 840, "green": 550, "nir_band": nir_i + 1, "green_band": green_i + 1}
        label = "绿光归一化植被指数"
    else:
        return None, None, f"不支持的植被指数：{name}"

    return arr.astype(np.float32), {"label": label, **info}, None


def _save_previews(
    out_dir: Path,
    stack: np.ndarray,
    wavelengths: Sequence[float],
    mask: np.ndarray,
    ndvi: Optional[np.ndarray],
) -> Dict[str, str]:
    preview_dir = out_dir / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    previews: Dict[str, str] = {}
    rgb_idx = R.pick_rgb_bands(wavelengths, stack.shape[0])
    rgba = R.stretch_rgb(stack[rgb_idx], mask=None, pmin=1, pmax=99)
    R.save_rgba_png(rgba, preview_dir / "rgb.png")
    previews["rgb"] = "previews/rgb.png"

    if ndvi is not None:
        rgba_ndvi = R.colorize(ndvi, cmap="ndvi", vmin=-0.1, vmax=0.9)
        R.save_rgba_png(rgba_ndvi, preview_dir / "ndvi.png")
        previews["ndvi"] = "previews/ndvi.png"

    mask_rgba = np.zeros(mask.shape + (4,), dtype=np.uint8)
    mask_rgba[mask] = (46, 125, 50, 200)
    mask_rgba[~mask] = (120, 120, 120, 40)
    R.save_rgba_png(mask_rgba, preview_dir / "mask.png")
    previews["mask"] = "previews/mask.png"
    return previews


def run_preprocess(
    source_path: str | Path,
    out_dir: str | Path,
    config: Optional[Dict[str, Any]] = None,
    progress: ProgressCb = None,
) -> Dict[str, Any]:
    """执行完整预处理并落盘，返回处理元数据。"""
    source_path = Path(source_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        cfg = PreprocessConfig.model_validate(config or {})
    except Exception as exc:
        raise ValueError(f"预处理参数无效：{exc}") from exc

    quality_items: List[Dict[str, str]] = []

    def add_check(key: str, title: str, status: str, message: str):
        quality_items.append(
            {"key": key, "title": title, "status": status, "message": message}
        )

    _progress(progress, 6, "读取影像", "正在读取影像数据…")
    with rasterio.open(source_path) as ds:
        full_meta = R.raster_meta(source_path)
        src_wl = [float(v) for v in full_meta.get("wavelengths") or []]

        crop = cfg.crop
        if crop.enabled and crop.width > 0 and crop.height > 0:
            x0 = max(0, min(crop.x0, ds.width - 1))
            y0 = max(0, min(crop.y0, ds.height - 1))
            w = max(1, min(crop.width, ds.width - x0))
            h = max(1, min(crop.height, ds.height - y0))
            window = Window(x0, y0, w, h)
            transform = ds.window_transform(window)
        else:
            window = Window(0, 0, ds.width, ds.height)
            x0, y0, w, h = 0, 0, ds.width, ds.height
            transform = ds.transform

        n_values = int(ds.count) * int(w) * int(h)
        if n_values > MAX_LOAD_VALUES:
            raise ValueError(
                f"影像数据量过大（约 {n_values / 1e6:.0f} 百万像元值），"
                "当前版本建议先进行感兴趣区裁剪后再处理。"
            )
        data = ds.read(window=window).astype(np.float32)
        if ds.nodata is not None:
            data = np.where(data == np.float32(ds.nodata), np.nan, data)
        crs = ds.crs.to_string() if ds.crs else None

    if data.ndim != 3 or data.shape[0] == 0:
        raise ValueError("影像波段数据为空，无法处理")
    if not src_wl or len(src_wl) != data.shape[0]:
        src_wl = []

    raw_finite = data[np.isfinite(data)]
    raw_min = float(np.nanmin(raw_finite)) if raw_finite.size else None
    raw_max = float(np.nanmax(raw_finite)) if raw_finite.size else None

    # 1. 反射率转换
    _progress(progress, 14, "反射率转换", "正在转换地表反射率…")
    if cfg.reflectance.enabled and cfg.reflectance.method != "none":
        if cfg.reflectance.method == "scale":
            data = data / float(cfg.reflectance.scale)
            conv_note = f"按比例因子 {cfg.reflectance.scale:g} 将 DN 值转换为反射率"
        else:
            data = data * float(cfg.reflectance.coefficient) + float(cfg.reflectance.offset)
            conv_note = (
                f"按定标系数 {cfg.reflectance.coefficient:g}"
                f"（偏移 {cfg.reflectance.offset:g}）转换为反射率"
            )
    else:
        conv_note = "未执行反射率转换，直接使用原始数值"

    finite_after = data[np.isfinite(data)]
    p1 = _pct(finite_after, 1)
    p50 = _pct(finite_after, 50)
    p99 = _pct(finite_after, 99)
    outside_ratio = 0.0
    if finite_after.size:
        outside = np.logical_or(finite_after < -0.05, finite_after > 1.2)
        outside_ratio = float(np.mean(outside))
    range_msg = (
        f"反射率 1%–99% 分位：{p1:.3f}–{p99:.3f}，中位数 {p50:.3f}" if p1 is not None else "数值范围未知"
    )
    if cfg.reflectance.enabled and cfg.reflectance.method == "none" and p50 is not None and p50 > 1.5:
        add_check("reflectance", "反射率转换", "warning", range_msg + "；数值疑似仍为 DN 值，请确认反射率转换参数")
    elif cfg.reflectance.enabled and cfg.reflectance.method == "scale" and p99 is not None and p99 < 0.02:
        add_check("reflectance", "反射率转换", "warning", range_msg + "；数值偏小，数据可能已是 0–1 反射率，建议将转换方式设为“不转换”")
    elif outside_ratio > 0.10:
        add_check("reflectance", "反射率转换", "warning", range_msg + f"；{outside_ratio * 100:.1f}% 像元超出合理反射率范围")
    elif outside_ratio > 0.02:
        add_check("reflectance", "反射率转换", "notice", range_msg + f"；{outside_ratio * 100:.1f}% 像元超出合理反射率范围")
    else:
        add_check("reflectance", "反射率转换", "ok", conv_note + "。" + range_msg)

    # 2. 坏波段剔除
    _progress(progress, 22, "坏波段剔除", "正在剔除坏波段…")
    removed_bands: List[int] = []
    if cfg.bad_bands.enabled and src_wl and cfg.bad_bands.ranges:
        keep = []
        for i, wl in enumerate(src_wl):
            bad = any(lo <= wl <= hi for lo, hi in cfg.bad_bands.ranges)
            if bad:
                removed_bands.append(i + 1)
            else:
                keep.append(i)
        if len(keep) >= 2:
            data = data[keep]
            src_wl = [src_wl[i] for i in keep]
            add_check("bad_bands", "坏波段剔除", "ok", f"已剔除 {len(removed_bands)} 个坏波段（波段号：{removed_bands}）")
        else:
            add_check("bad_bands", "坏波段剔除", "notice", "剔除后有效波段不足 2 个，已自动跳过坏波段剔除")
    else:
        add_check("bad_bands", "坏波段剔除", "ok", "未启用坏波段剔除，保留全部波段")

    # 3. 光谱平滑
    _progress(progress, 36, "光谱平滑", "正在执行 Savitzky-Golay 平滑…")
    smoothing_applied = False
    if cfg.smoothing.enabled:
        n_bands = data.shape[0]
        window = cfg.smoothing.window
        if window > n_bands:
            window = n_bands if n_bands % 2 == 1 else n_bands - 1
        if window >= 3 and cfg.smoothing.polyorder < window:
            data = savgol_filter(
                data, window_length=window, polyorder=cfg.smoothing.polyorder, axis=0
            ).astype(np.float32)
            smoothing_applied = True
            add_check(
                "smoothing",
                "光谱平滑",
                "ok",
                f"Savitzky-Golay 平滑完成（窗口 {window}，阶数 {cfg.smoothing.polyorder}）",
            )
        else:
            add_check("smoothing", "光谱平滑", "notice", "波段数不足，已自动跳过光谱平滑")
    else:
        add_check("smoothing", "光谱平滑", "ok", "未启用光谱平滑")

    # 4. 波段对齐
    _progress(progress, 54, "波段对齐", "正在将波段重采样至模型标准波长…")
    alignment_info: Dict[str, Any] = {}
    src_wl_before = list(src_wl)
    if cfg.alignment.enabled:
        if not src_wl:
            add_check("alignment", "波段对齐", "warning", "缺少波段中心波长信息，已跳过波段对齐；建议补充波长后重新处理")
        else:
            target = np.arange(
                cfg.alignment.start,
                cfg.alignment.end + cfg.alignment.step * 0.5,
                cfg.alignment.step,
            ).round(3)
            data, alignment_info = R.resample_stack(data, src_wl, target)
            src_wl = [float(v) for v in target]
            if alignment_info.get("edge_filled"):
                add_check(
                    "alignment",
                    "波段对齐",
                    "notice",
                    f"已对齐到 {len(target)} 个标准波段（{cfg.alignment.start:g}–{cfg.alignment.end:g} nm）；"
                    "目标波长超出源影像范围的两端采用最近邻填充",
                )
            else:
                add_check(
                    "alignment",
                    "波段对齐",
                    "ok",
                    f"已对齐到 {len(target)} 个标准波段（{cfg.alignment.start:g}–{cfg.alignment.end:g} nm，步长 {cfg.alignment.step:g} nm）",
                )
    else:
        add_check("alignment", "波段对齐", "ok", "未启用波段对齐，保留原始波段")

    # 5. 云/阴影与植被掩膜
    _progress(progress, 70, "植被掩膜", "正在计算植被掩膜…")
    wl_now = src_wl
    total_pixels = int(data.shape[1] * data.shape[2])
    finite_all = np.isfinite(data)
    if wl_now:
        nir_i = R.nearest_band_index(wl_now, cfg.mask.nir_wavelength, 120)
        red_i = R.nearest_band_index(wl_now, cfg.mask.red_wavelength, 120)
    else:
        nir_i = red_i = None
    if nir_i is None or red_i is None:
        nir_i = data.shape[0] - 1
        red_i = max(0, data.shape[0] - 3)

    nir = data[nir_i]
    red = data[red_i]
    denom = nir + red
    ndvi_full = np.where(np.abs(denom) > 1e-6, (nir - red) / denom, np.nan).astype(np.float32)

    if cfg.mask.enabled:
        valid = np.isfinite(ndvi_full) & (ndvi_full >= cfg.mask.ndvi_threshold)
    else:
        valid = np.isfinite(nir) & np.isfinite(red)
    valid &= finite_all.all(axis=0)

    cloud_ratio = 0.0
    if cfg.cloud.enabled:
        if wl_now:
            blue_i = R.nearest_band_index(wl_now, 480, 100)
        else:
            blue_i = 0
        if blue_i is not None:
            blue = data[blue_i]
            clouds = np.isfinite(blue) & (blue > cfg.cloud.blue_threshold)
            cloud_ratio = float(clouds.sum() / max(total_pixels, 1))
            valid &= ~clouds
            add_check(
                "cloud",
                "云/阴影检测",
                "ok" if cloud_ratio < 0.2 else "warning",
                f"按蓝光阈值 {cfg.cloud.blue_threshold:g} 检测到 {cloud_ratio * 100:.1f}% 像元疑似云覆盖",
            )
        else:
            add_check("cloud", "云/阴影检测", "notice", "缺少蓝光波段，已跳过云检测")
    else:
        add_check("cloud", "云/阴影检测", "ok", "未启用云/阴影检测")

    valid_pixels = int(valid.sum())
    mask_ratio = valid_pixels / max(total_pixels, 1)
    if cfg.mask.enabled:
        if mask_ratio < 0.10:
            add_check("mask", "植被掩膜", "warning", f"有效植被像元仅 {mask_ratio * 100:.1f}%，请检查 NDVI 阈值或影像质量")
        elif mask_ratio < 0.30:
            add_check("mask", "植被掩膜", "notice", f"有效植被像元占比 {mask_ratio * 100:.1f}%，可能包含较多裸土")
        else:
            add_check("mask", "植被掩膜", "ok", f"植被像元占比 {mask_ratio * 100:.1f}%（NDVI ≥ {cfg.mask.ndvi_threshold:g}）")
    else:
        add_check("mask", "植被掩膜", "ok", "未启用植被掩膜，全部有效像元参与反演")

    # 6. 植被指数
    _progress(progress, 84, "植被指数", "正在计算植被指数…")
    indices_meta: Dict[str, Any] = {}
    index_arrays: Dict[str, np.ndarray] = {}
    safe_mask = valid
    for name in cfg.indices or []:
        arr, info, reason = compute_index(name, data, wl_now)
        key = name.upper()
        if arr is None:
            add_check(f"index_{key}", f"{key} 指数", "notice", reason or "无法计算")
            continue
        arr = arr.astype(np.float32)
        index_arrays[key] = arr
        vals = arr[safe_mask & np.isfinite(arr)]
        if vals.size:
            indices_meta[key] = {
                "label": info.get("label", key),
                "min": round(float(np.min(vals)), 5),
                "max": round(float(np.max(vals)), 5),
                "mean": round(float(np.mean(vals)), 5),
                "std": round(float(np.std(vals)), 5),
                **{k: v for k, v in info.items() if k != "label"},
            }
        else:
            indices_meta[key] = {"label": info.get("label", key), **info, "empty": True}

    # 7. 保存结果
    _progress(progress, 94, "保存结果", "正在保存预处理结果…")
    # 光谱栈以 float16 压缩保存：反射率精度足够，体积约为 float32 的 1/4
    np.savez_compressed(out_dir / "stack.npz", stack=data.astype(np.float16))
    np.save(out_dir / "mask.npy", valid.astype(bool))
    indices_dir = out_dir / "indices"
    indices_dir.mkdir(exist_ok=True)
    for key, arr in index_arrays.items():
        np.save(indices_dir / f"{key}.npy", arr.astype(np.float32))

    previews = _save_previews(out_dir, data, wl_now, valid, index_arrays.get("NDVI"))

    levels = [item["status"] for item in quality_items]
    overall = "ok"
    if "warning" in levels:
        overall = "warning"
    elif "notice" in levels:
        overall = "notice"

    transform_list = list(transform)[:6]
    meta = {
        "created_at": now_iso(),
        "width": int(data.shape[2]),
        "height": int(data.shape[1]),
        "bands": int(data.shape[0]),
        "shape": [int(data.shape[0]), int(data.shape[1]), int(data.shape[2])],
        "crs": crs,
        "transform": [float(v) for v in transform_list],
        "res_x": abs(float(transform.a)),
        "res_y": abs(float(transform.e)),
        "wavelengths": [round(float(v), 3) for v in wl_now],
        "wavelength_unit": "nm",
        "source_wavelengths": [round(float(v), 3) for v in src_wl_before],
        "source_bands": int(full_meta.get("bands", 0)),
        "source_width": int(full_meta.get("width", 0)),
        "source_height": int(full_meta.get("height", 0)),
        "crop": {
            "enabled": bool(cfg.crop.enabled),
            "x0": int(x0),
            "y0": int(y0),
            "width": int(w),
            "height": int(h),
        },
        "config": cfg.model_dump(),
        "quality": {"level": overall, "items": quality_items},
        "mask": {
            "enabled": bool(cfg.mask.enabled),
            "ndvi_threshold": float(cfg.mask.ndvi_threshold),
            "ratio": round(mask_ratio, 5),
            "valid_pixels": valid_pixels,
            "total_pixels": total_pixels,
            "cloud_ratio": round(cloud_ratio, 5),
        },
        "indices": indices_meta,
        "removed_bands": removed_bands,
        "alignment": alignment_info,
        "smoothing_applied": smoothing_applied,
        "raw_range": {"min": raw_min, "max": raw_max},
        "reflectance_range": {
            "p1": p1,
            "p50": p50,
            "p99": p99,
            "outside_ratio": round(outside_ratio, 5),
        },
        "previews": previews,
    }
    from ..core.utils import write_json

    write_json(out_dir / "meta.json", meta)
    _progress(progress, 100, "完成", "预处理完成")
    return meta


def load_processed(out_dir: str | Path) -> Optional[Dict[str, Any]]:
    from ..core.utils import read_json

    out_dir = Path(out_dir)
    meta = read_json(out_dir / "meta.json")
    if not isinstance(meta, dict):
        return None
    if not ((out_dir / "stack.npz").exists() or (out_dir / "stack.npy").exists()):
        return None
    if not (out_dir / "mask.npy").exists():
        return None
    return meta


def load_stack(out_dir: str | Path) -> Tuple[np.ndarray, np.ndarray]:
    """读取预处理光谱栈（兼容旧的 float32 .npy 与压缩 .npz）。"""
    out_dir = Path(out_dir)
    npz = out_dir / "stack.npz"
    npy = out_dir / "stack.npy"
    if npz.exists():
        with np.load(npz) as data:
            stack = data["stack"].astype(np.float32)
    elif npy.exists():
        stack = np.load(npy).astype(np.float32)
    else:
        raise ValueError("预处理光谱栈文件缺失，请重新执行预处理")
    mask_path = out_dir / "mask.npy"
    if not mask_path.exists():
        raise ValueError("植被掩膜文件缺失，请重新执行预处理")
    mask = np.load(mask_path).astype(bool)
    return stack, mask
