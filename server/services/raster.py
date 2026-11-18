"""栅格影像读取、元数据解析、预览渲染与光谱提取。"""
from __future__ import annotations

import math
import re
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling
from rasterio.windows import Window

from ..core.utils import parse_number_list

# ---------------------------------------------------------------- 元数据解析

_WL_KEYS = ("wavelength", "wavelengths", "wavelength_list", "band_wavelengths")
_UNIT_KEYS = ("wavelength_units", "wavelength units", "wavelength_unit", "wl_units")
_FWHM_KEYS = ("fwhm", "bandwidth", "bandwidths", "band_widths", "band widths")


def _lookup(tags: Dict[str, str], keys: Sequence[str]) -> Optional[str]:
    if not tags:
        return None
    low = {str(k).lower(): v for k, v in tags.items()}
    for key in keys:
        if key.lower() in low:
            return low[key.lower()]
    return None


def _read_envi_header(path: Path) -> Dict[str, str]:
    """直接解析 ENVI .hdr，作为 rasterio 标签的后备来源。"""
    candidates = []
    for suffix in (".hdr", ".HDR"):
        candidates.append(path.with_suffix(suffix))
        candidates.append(path.with_name(path.name + suffix))
    # 有些文件为 .dat.hdr
    for cand in candidates:
        if cand.exists() and cand != path:
            try:
                text = cand.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            return _parse_hdr_text(text)
    return {}


def _parse_hdr_text(text: str) -> Dict[str, str]:
    lines = text.splitlines()
    result: Dict[str, str] = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        if "=" not in line:
            i += 1
            continue
        key, _, value = line.partition("=")
        key = key.strip().lower()
        value = value.strip()
        if value.startswith("{"):
            # 多行花括号块
            buf = [value]
            while "}" not in value and i + 1 < len(lines):
                i += 1
                value = lines[i]
                buf.append(value)
            value = " ".join(buf)
            value = value.strip("{} \t")
        result[key] = value
        i += 1
    return result


def _normalize_wavelength_unit(values: List[float], unit: Optional[str]) -> Tuple[List[float], str]:
    unit = (unit or "").strip()
    lowered = unit.lower().replace("μ", "µ")
    if not values:
        return values, unit or "nm"
    if any(token in lowered for token in ("um", "µm", "micron", "micrometer")):
        return [v * 1000.0 for v in values], "nm"
    if "nm" in lowered:
        return values, "nm"
    if "m" == lowered:
        return [v * 1e9 for v in values], "nm"
    # 无单位时按数值范围推断
    vmax = max(abs(v) for v in values)
    if vmax <= 5.0:
        return [v * 1000.0 for v in values], "nm"
    if vmax <= 3000.0:
        return values, "nm"
    return values, unit or "nm"


def _desc_wavelength(desc: Optional[str]) -> Optional[float]:
    if not desc:
        return None
    low = desc.lower()
    nums = re.findall(r"-?\d+(?:\.\d+)?", desc)
    if not nums:
        return None
    value = float(nums[0])
    if "nm" in low:
        return value
    if "um" in low or "µm" in low or "μ" in desc:
        return value * 1000.0
    if "band" in low or "波段" in desc or "b" == low[:1]:
        return None
    if 300 <= value <= 2500:
        return value
    if 0.3 <= value <= 2.5:
        return value * 1000.0
    return None


def parse_band_metadata(ds: rasterio.DatasetReader, path: Optional[Path] = None) -> Dict[str, Any]:
    """解析波段中心波长、带宽、名称。缺失时返回空列表。"""
    count = ds.count
    ds_tags = {str(k): str(v) for k, v in (ds.tags() or {}).items()}
    envi_tags = _read_envi_header(Path(path or ds.name)) if path else {}
    merged = dict(envi_tags)
    merged.update({k.lower(): v for k, v in ds_tags.items()})

    wavelengths: List[float] = []
    raw_wl = _lookup(merged, _WL_KEYS)
    if raw_wl:
        wavelengths = parse_number_list(raw_wl)

    unit = _lookup(merged, _UNIT_KEYS)

    if len(wavelengths) != count:
        # 尝试从波段描述解析
        desc_values = [_desc_wavelength(d) for d in (ds.descriptions or [])]
        if desc_values and all(v is not None for v in desc_values):
            wavelengths = [float(v) for v in desc_values]
            unit = "nm"
        else:
            wavelengths = []

    if wavelengths:
        wavelengths, unit = _normalize_wavelength_unit(wavelengths, unit)
        # 合理性检查：波长应大致递增且处于 100–20000 nm
        if len(wavelengths) == count and min(wavelengths) > 100 and max(wavelengths) < 20000:
            pass
        else:
            wavelengths = []

    bandwidths: List[Optional[float]] = []
    raw_bw = _lookup(merged, _FWHM_KEYS)
    if raw_bw:
        bw = parse_number_list(raw_bw)
        if len(bw) == count:
            bw, _ = _normalize_wavelength_unit(bw, unit)
            bandwidths = [round(v, 3) for v in bw]
    if not bandwidths:
        for i in range(count):
            band_tags = {str(k).lower(): str(v) for k, v in (ds.tags(i + 1) or {}).items()}
            bw = band_tags.get("fwhm") or band_tags.get("bandwidth")
            if bw:
                vals = parse_number_list(bw)
                bandwidths.append(round(vals[0], 3) if vals else None)
            else:
                bandwidths.append(None)
    if len(bandwidths) != count:
        bandwidths = [None] * count

    descriptions = list(ds.descriptions or [])
    if len(descriptions) != count:
        descriptions = [None] * count

    band_names = []
    raw_names = merged.get("band names") or merged.get("band_names")
    if raw_names:
        names = [n.strip() for n in re.split(r"[,，\n]", str(raw_names)) if n.strip()]
        if len(names) == count:
            band_names = names
    if not band_names:
        band_names = [f"Band {i + 1}" for i in range(count)]

    return {
        "wavelengths": [round(float(v), 3) for v in wavelengths] if wavelengths else [],
        "wavelength_unit": unit or "nm",
        "bandwidths": bandwidths,
        "band_names": band_names,
        "descriptions": descriptions,
    }


def raster_meta(
    path: str | Path,
    user_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    path = Path(path)
    with rasterio.open(path) as ds:
        band_info = parse_band_metadata(ds, path)
        from .image_metadata import image_fields_from_metadata, parse_image_metadata

        unified = parse_image_metadata(path, user_metadata=user_metadata)
        unified_fields = image_fields_from_metadata(unified)
        for key in ("wavelengths", "bandwidths", "band_names"):
            if unified_fields.get(key):
                band_info[key] = unified_fields[key]
        if unified.get("wavelength_unit"):
            band_info["wavelength_unit"] = unified["wavelength_unit"]
        transform = list(ds.transform)[:6]
        meta: Dict[str, Any] = {
            "width": int(ds.width),
            "height": int(ds.height),
            "bands": int(ds.count),
            "dtype": str(ds.dtypes[0]) if ds.count else "unknown",
            "driver": ds.driver,
            "crs": ds.crs.to_string() if ds.crs else None,
            "transform": [float(v) for v in transform],
            "nodata": float(ds.nodata) if ds.nodata is not None else None,
            "res_x": abs(float(ds.transform.a)),
            "res_y": abs(float(ds.transform.e)),
            "bounds": [float(v) for v in ds.bounds] if ds.bounds else None,
            **band_info,
            **{
                key: value
                for key, value in unified_fields.items()
                if key not in ("wavelengths", "bandwidths", "band_names", "wavelength_unit")
            },
            "metadata": unified,
            "metadata_sources": unified.get("metadata_sources") or {},
            "metadata_confidence": unified.get("metadata_confidence") or {},
            "metadata_fields": unified.get("metadata_fields") or {},
            "metadata_warnings": unified.get("metadata_warnings") or [],
            "metadata_conflicts": unified.get("metadata_conflicts") or [],
            "raw_metadata": unified.get("raw_metadata") or {},
        }
        if ds.crs:
            try:
                meta["crs_wkt"] = ds.crs.to_wkt()
            except Exception:
                meta["crs_wkt"] = None
        return meta


# ---------------------------------------------------------------- 波段选择

def nearest_band_index(wavelengths: Sequence[float], target: float, tolerance: float = 70.0) -> Optional[int]:
    """返回 0 基波段索引；超出容差返回 None。"""
    if not wavelengths:
        return None
    arr = np.asarray(wavelengths, dtype=float)
    idx = int(np.argmin(np.abs(arr - target)))
    if abs(arr[idx] - target) > tolerance:
        return None
    return idx


def index_or_none(wavelengths: Sequence[float], target: float, tolerance: float = 70.0) -> Optional[int]:
    return nearest_band_index(wavelengths, target, tolerance)


def pick_rgb_bands(wavelengths: Sequence[float], bands: int) -> List[int]:
    """返回 0 基 [R, G, B] 波段索引。"""
    if wavelengths:
        r = nearest_band_index(wavelengths, 660, 200)
        g = nearest_band_index(wavelengths, 550, 200)
        b = nearest_band_index(wavelengths, 460, 200)
        if r is not None and g is not None and b is not None:
            return [r, g, b]
    if bands >= 3:
        return [2, 1, 0]
    if bands == 2:
        return [1, 1, 0]
    return [0, 0, 0]


def pick_false_color_bands(wavelengths: Sequence[float], bands: int) -> List[int]:
    if wavelengths:
        nir = nearest_band_index(wavelengths, 840, 200)
        red = nearest_band_index(wavelengths, 670, 200)
        green = nearest_band_index(wavelengths, 550, 200)
        if nir is not None and red is not None and green is not None:
            return [nir, red, green]
    if bands >= 4:
        return [bands - 1, 2, 1]
    if bands >= 3:
        return [bands - 1, 0, 1]
    return [0, 0, 0]


def pick_nir_red_bands(wavelengths: Sequence[float], bands: int) -> Tuple[int, int]:
    nir = nearest_band_index(wavelengths, 840, 120) if wavelengths else None
    red = nearest_band_index(wavelengths, 670, 120) if wavelengths else None
    if nir is not None and red is not None and nir != red:
        return nir, red
    if bands >= 3:
        return bands - 1, max(0, bands - 3)
    if bands == 2:
        return 1, 0
    return 0, 0


# ---------------------------------------------------------------- 数组重采样

def resample_stack(
    data: np.ndarray, src_wl: Sequence[float], target_wl: Sequence[float]
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """沿光谱维线性插值。data: (B, H, W) -> (B', H, W)。

    目标波长超出源范围时采用最近边缘波段填充，并在 info 中标记。
    """
    data = np.asarray(data, dtype=np.float32)
    src = np.asarray(src_wl, dtype=float)
    target = np.asarray(target_wl, dtype=float)
    if data.shape[0] != len(src):
        raise ValueError("波段数与波长数量不一致")
    if len(src) == 0 or len(target) == 0:
        raise ValueError("缺少波段波长信息，无法重采样")

    order = np.argsort(src)
    src = src[order]
    data = data[order]
    # 去除重复波长
    uniq_mask = np.ones(len(src), dtype=bool)
    if len(src) > 1:
        uniq_mask[1:] = np.diff(src) > 1e-6
    src = src[uniq_mask]
    data = data[uniq_mask]

    out = np.empty((len(target),) + data.shape[1:], dtype=np.float32)
    exact_bands = 0
    interpolated_bands = 0
    edge_filled_bands = 0
    for k, t in enumerate(target):
        if len(src) == 1:
            out[k] = data[0]
            if abs(t - src[0]) <= 1e-6:
                exact_bands += 1
            else:
                edge_filled_bands += 1
            continue
        if t <= src[0]:
            out[k] = data[0]
            if abs(t - src[0]) <= 1e-6:
                exact_bands += 1
            else:
                edge_filled_bands += 1
        elif t >= src[-1]:
            out[k] = data[-1]
            if abs(t - src[-1]) <= 1e-6:
                exact_bands += 1
            else:
                edge_filled_bands += 1
        else:
            exact = np.any(np.abs(src - t) <= 1e-6)
            if exact:
                exact_bands += 1
            else:
                interpolated_bands += 1
            j = int(np.searchsorted(src, t, side="left"))
            j = max(1, min(j, len(src) - 1))
            denom = src[j] - src[j - 1]
            w1 = 0.0 if abs(denom) < 1e-9 else float((t - src[j - 1]) / denom)
            out[k] = data[j - 1] * (1.0 - w1) + data[j] * w1

    total_target = max(1, len(target))
    return out, {
        "source_range": [float(src[0]), float(src[-1])],
        "target_range": [float(min(target)), float(max(target))],
        "edge_filled": edge_filled_bands > 0,
        "exact_bands": int(exact_bands),
        "interpolated_bands": int(interpolated_bands),
        "edge_filled_bands": int(edge_filled_bands),
        "edge_fill_ratio": round(edge_filled_bands / total_target, 5),
        "resampled": bool(interpolated_bands or edge_filled_bands),
        "source_band_count": int(len(src)),
        "bands": int(len(target)),
        "method": "linear_interpolation_with_nearest_edge_fill",
    }


# ---------------------------------------------------------------- 颜色工具

_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")


def _hex_to_rgb(color: str) -> Tuple[int, int, int]:
    m = _HEX.match(color.strip())
    if not m:
        return (0, 0, 0)
    v = m.group(1)
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


COLOR_STOPS: Dict[str, List[str]] = {
    "greens": ["#f7fcf5", "#c7e9c0", "#74c476", "#31a354", "#006d2c", "#00441b"],
    "viridis": ["#440154", "#414487", "#2a788e", "#22a884", "#7ad151", "#fde725"],
    "terrain": ["#333399", "#00a0a0", "#80c060", "#e0d080", "#a06030", "#ffffff"],
    "ndvi": ["#8c510a", "#d8b365", "#f6e8c3", "#c7eae5", "#5ab4ac", "#01665e"],
    "nmass": ["#fff7bc", "#d9f0a3", "#78c679", "#31a354", "#006d2c", "#00441b"],
    "oranges": ["#fff5eb", "#fdd0a2", "#fd8d3c", "#d94801", "#7f2704"],
    "blues": ["#f7fbff", "#c6dbef", "#6baed6", "#2171b5", "#08306b"],
    "gray": ["#000000", "#ffffff"],
}


def gradient_lut(stops: Sequence[str], n: int = 256) -> np.ndarray:
    stops = list(stops) if stops else list(COLOR_STOPS["nmass"])
    if len(stops) == 1:
        stops = [stops[0], stops[0]]
    positions = np.linspace(0.0, 1.0, len(stops))
    colors = np.array([_hex_to_rgb(c) for c in stops], dtype=np.float64)
    xs = np.linspace(0.0, 1.0, n)
    lut = np.empty((n, 3), dtype=np.uint8)
    for ch in range(3):
        lut[:, ch] = np.interp(xs, positions, colors[:, ch]).round().astype(np.uint8)
    return lut


_LUT_CACHE: Dict[str, np.ndarray] = {}


def lut_for(cmap: str = "nmass", stops: Optional[Sequence[str]] = None) -> np.ndarray:
    key = f"custom:{'|'.join(stops)}" if stops else cmap
    if key not in _LUT_CACHE:
        _LUT_CACHE[key] = gradient_lut(stops or COLOR_STOPS.get(cmap, COLOR_STOPS["nmass"]))
    return _LUT_CACHE[key]


def colorize(
    values: np.ndarray,
    mask: Optional[np.ndarray] = None,
    cmap: str = "nmass",
    vmin: Optional[float] = None,
    vmax: Optional[float] = None,
    stops: Optional[Sequence[str]] = None,
    pmin: float = 2.0,
    pmax: float = 98.0,
) -> np.ndarray:
    """float 数组 -> RGBA uint8。无效像元 alpha=0。"""
    values = np.asarray(values, dtype=np.float32)
    finite = np.isfinite(values)
    if mask is not None:
        finite &= np.asarray(mask, dtype=bool)
    if not finite.any():
        return np.zeros(values.shape + (4,), dtype=np.uint8)
    vals = values[finite]
    if vmin is None or vmax is None:
        lo, hi = np.percentile(vals, [pmin, pmax])
        vmin = float(lo) if vmin is None else vmin
        vmax = float(hi) if vmax is None else vmax
    if vmax <= vmin:
        vmax = vmin + 1e-6
    norm = np.clip((values - vmin) / (vmax - vmin), 0.0, 1.0)
    norm = np.nan_to_num(norm, nan=0.0, posinf=1.0, neginf=0.0)
    idx = (norm * 255).astype(np.uint8)
    rgba = np.zeros(values.shape + (4,), dtype=np.uint8)
    lut = lut_for(cmap, stops)
    rgba[..., :3] = lut[idx]
    rgba[..., 3] = finite.astype(np.uint8) * 255
    return rgba


def stretch_rgb(
    bands: np.ndarray,
    mask: Optional[np.ndarray] = None,
    pmin: float = 2.0,
    pmax: float = 98.0,
    gamma: float = 1.0,
    vmin: Optional[float] = None,
    vmax: Optional[float] = None,
) -> np.ndarray:
    """(3,H,W) 或多通道 -> RGBA。逐通道百分位拉伸。"""
    arr = np.asarray(bands, dtype=np.float32)
    if arr.ndim == 2:
        arr = arr[None, ...]
    if arr.shape[0] == 1:
        arr = np.repeat(arr, 3, axis=0)
    elif arr.shape[0] == 2:
        arr = np.concatenate([arr, arr[:1]], axis=0)
    arr = arr[:3]
    valid = np.isfinite(arr).all(axis=0)
    if mask is not None:
        valid &= np.asarray(mask, dtype=bool)

    rgb = np.zeros((3,) + arr.shape[1:], dtype=np.float32)
    for i in range(3):
        ch = arr[i]
        finite = np.isfinite(ch)
        if not finite.any():
            continue
        if vmin is not None and vmax is not None:
            lo, hi = float(vmin), float(vmax)
        else:
            lo, hi = np.percentile(ch[finite], [pmin, pmax])
            lo, hi = float(lo), float(hi)
        if hi <= lo:
            hi = lo + 1e-6
        n = np.clip((ch - lo) / (hi - lo), 0.0, 1.0)
        n = np.nan_to_num(n, nan=0.0, posinf=1.0, neginf=0.0)
        if gamma and gamma != 1.0:
            n = np.power(n, 1.0 / max(gamma, 1e-3))
        rgb[i] = n
    rgba = np.zeros((arr.shape[1], arr.shape[2], 4), dtype=np.uint8)
    rgba[..., :3] = (np.transpose(rgb, (1, 2, 0)) * 255).round().astype(np.uint8)
    rgba[..., 3] = valid.astype(np.uint8) * 255
    return rgba


def rgba_to_png_bytes(rgba: np.ndarray) -> bytes:
    img = Image.fromarray(rgba, mode="RGBA")
    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def save_rgba_png(rgba: np.ndarray, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, mode="RGBA").save(path, format="PNG", optimize=True)
    return path


# ---------------------------------------------------------------- 影像预览

def _read_decimated(
    ds: rasterio.DatasetReader, indexes: Sequence[int], max_size: int
) -> Tuple[np.ndarray, int, int]:
    scale = min(1.0, float(max_size) / float(max(ds.width, ds.height)))
    out_w = max(1, int(round(ds.width * scale)))
    out_h = max(1, int(round(ds.height * scale)))
    data = ds.read(
        indexes=list(indexes),
        out_shape=(len(indexes), out_h, out_w),
        resampling=Resampling.bilinear,
    ).astype(np.float32)
    if ds.nodata is not None:
        data = np.where(data == np.float32(ds.nodata), np.nan, data)
    return data, out_w, out_h


def render_image_preview(
    path: str | Path,
    mode: str = "rgb",
    bands: Optional[Sequence[int]] = None,
    max_size: int = 1400,
    cmap: str = "greens",
    pmin: float = 2.0,
    pmax: float = 98.0,
    gamma: float = 1.0,
    vmin: Optional[float] = None,
    vmax: Optional[float] = None,
    stretch: str = "percent",
) -> Tuple[bytes, Dict[str, Any]]:
    """渲染原始影像预览。bands 为 1 基波段号。"""
    path = Path(path)
    with rasterio.open(path) as ds:
        meta = raster_meta(path)
        if mode == "rgb":
            idx = list(bands or pick_rgb_bands(meta["wavelengths"], meta["bands"]))
            idx = [int(i) for i in idx if 1 <= int(i) <= ds.count] or [1]
            arr, out_w, out_h = _read_decimated(ds, idx, max_size)
            if stretch == "std":
                mask = np.isfinite(arr).all(axis=0)
                pmin = pmax = None  # 使用均值±2σ
                rgba = _std_stretch(arr, mask, gamma)
            else:
                rgba = stretch_rgb(
                    arr, pmin=pmin or 2, pmax=pmax or 98, gamma=gamma,
                    vmin=vmin if stretch == "fixed" else None,
                    vmax=vmax if stretch == "fixed" else None,
                )
        else:
            band = int((bands or [1])[0])
            band = min(max(band, 1), ds.count)
            arr, out_w, out_h = _read_decimated(ds, [band], max_size)
            data = arr[0]
            if stretch == "fixed" and vmin is not None and vmax is not None:
                rgba = colorize(data, cmap=cmap, vmin=vmin, vmax=vmax)
            elif stretch == "std" and np.isfinite(data).any():
                mean, std = float(np.nanmean(data)), float(np.nanstd(data))
                rgba = colorize(data, cmap=cmap, vmin=mean - 2 * std, vmax=mean + 2 * std)
            else:
                rgba = colorize(data, cmap=cmap, vmin=vmin, vmax=vmax, pmin=pmin or 2, pmax=pmax or 98)
        return rgba_to_png_bytes(rgba), {"width": out_w, "height": out_h, "source_width": ds.width, "source_height": ds.height}


def _std_stretch(arr: np.ndarray, mask: np.ndarray, gamma: float = 1.0) -> np.ndarray:
    arr = np.asarray(arr, dtype=np.float32)
    if arr.shape[0] == 1:
        arr = np.repeat(arr, 3, axis=0)
    elif arr.shape[0] == 2:
        arr = np.concatenate([arr, arr[:1]], axis=0)
    rgb = np.zeros(arr[:3].shape, dtype=np.float32)
    for i in range(min(3, arr.shape[0])):
        ch = arr[i]
        finite = np.isfinite(ch)
        if not finite.any():
            continue
        mean = float(np.mean(ch[finite]))
        std = float(np.std(ch[finite])) or 1.0
        n = np.clip((ch - (mean - 2 * std)) / (4 * std), 0, 1)
        if gamma != 1.0:
            n = np.power(n, 1.0 / max(gamma, 1e-3))
        rgb[i] = n
    rgba = np.zeros((arr.shape[1], arr.shape[2], 4), dtype=np.uint8)
    rgba[..., :3] = (np.transpose(rgb, (1, 2, 0)) * 255).round().astype(np.uint8)
    rgba[..., 3] = mask.astype(np.uint8) * 255
    return rgba


def save_cover_image(
    source_path: str | Path,
    out_path: str | Path,
    size: int = 720,
    background: Tuple[int, int, int] = (18, 30, 24),
) -> Path:
    """生成项目封面：真彩色缩略图，叠加深色背景以适配卡片。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    png, _ = render_image_preview(source_path, mode="rgb", max_size=size)
    img = Image.open(BytesIO(png)).convert("RGBA")
    bg = Image.new("RGBA", img.size, (*background, 255))
    bg.alpha_composite(img)
    bg.convert("RGB").save(out_path, "PNG", optimize=True)
    return out_path


def save_cover_from_png(
    source_png: str | Path,
    out_path: str | Path,
    size: int = 900,
    background: Tuple[int, int, int] = (18, 30, 24),
) -> Path:
    """用结果专题图生成项目封面。"""
    source_png = Path(source_png)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.open(source_png).convert("RGBA")
    img.thumbnail((size, size), Image.LANCZOS)
    bg = Image.new("RGBA", img.size, (*background, 255))
    bg.alpha_composite(img)
    bg.convert("RGB").save(out_path, "PNG", optimize=True)
    return out_path


def render_ndvi_preview(path: str | Path, max_size: int = 1400) -> Tuple[bytes, Dict[str, Any]]:
    path = Path(path)
    meta = raster_meta(path)
    nir_i, red_i = pick_nir_red_bands(meta["wavelengths"], meta["bands"])
    with rasterio.open(path) as ds:
        arr, out_w, out_h = _read_decimated(ds, [nir_i + 1, red_i + 1], max_size)
    nir, red = arr[0], arr[1]
    denom = nir + red
    ndvi = np.where(np.abs(denom) > 1e-6, (nir - red) / denom, np.nan)
    rgba = colorize(ndvi, cmap="ndvi", vmin=-0.1, vmax=0.9)
    return rgba_to_png_bytes(rgba), {
        "width": out_w,
        "height": out_h,
        "source_width": meta["width"],
        "source_height": meta["height"],
        "bands": [nir_i + 1, red_i + 1],
    }


# ---------------------------------------------------------------- 光谱提取

def read_point_spectrum(
    path: str | Path, row: int, col: int, wavelengths: Optional[Sequence[float]] = None
) -> Dict[str, Any]:
    path = Path(path)
    with rasterio.open(path) as ds:
        if row < 0 or col < 0 or row >= ds.height or col >= ds.width:
            raise ValueError("点击位置超出影像范围")
        data = ds.read(window=Window(col, row, 1, 1)).astype(np.float32).ravel()
        if ds.nodata is not None:
            data = np.where(data == ds.nodata, np.nan, data)
    wl = list(wavelengths or [])
    return {
        "kind": "point",
        "row": int(row),
        "col": int(col),
        "wavelengths": [float(v) for v in wl] if len(wl) == len(data) else list(range(1, len(data) + 1)),
        "values": [None if not np.isfinite(v) else round(float(v), 5) for v in data],
    }


def read_region_spectrum(
    path: str | Path,
    row: int,
    col: int,
    win_w: int,
    win_h: int,
    shape: str = "box",
    wavelengths: Optional[Sequence[float]] = None,
) -> Dict[str, Any]:
    path = Path(path)
    with rasterio.open(path) as ds:
        row0 = max(0, min(row - win_h // 2, ds.height - 1))
        col0 = max(0, min(col - win_w // 2, ds.width - 1))
        w = max(1, min(win_w, ds.width - col0))
        h = max(1, min(win_h, ds.height - row0))
        data = ds.read(window=Window(col0, row0, w, h)).astype(np.float32)
        if ds.nodata is not None:
            data = np.where(data == ds.nodata, np.nan, data)
        valid = np.isfinite(data).all(axis=0)
        if shape == "circle":
            yy, xx = np.mgrid[0:h, 0:w]
            cy, cx = (h - 1) / 2.0, (w - 1) / 2.0
            ry, rx = max(h / 2.0, 0.5), max(w / 2.0, 0.5)
            circle = ((yy - cy) / ry) ** 2 + ((xx - cx) / rx) ** 2 <= 1.0
            valid &= circle
        count = int(valid.sum())
        if count == 0:
            raise ValueError("所选区域内没有有效像元")
        vals = data[:, valid]
        mean = np.nanmean(vals, axis=1)
        std = np.nanstd(vals, axis=1)
    wl = list(wavelengths or [])
    return {
        "kind": "region",
        "row": int(row),
        "col": int(col),
        "width": int(w),
        "height": int(h),
        "count": count,
        "wavelengths": [float(v) for v in wl] if len(wl) == len(mean) else list(range(1, len(mean) + 1)),
        "values": [None if not np.isfinite(v) else round(float(v), 5) for v in mean],
        "std": [None if not np.isfinite(v) else round(float(v), 5) for v in std],
    }


def read_result_pixel(
    npy_path: str | Path, row: int, col: int, low: float, high: float
) -> Dict[str, Any]:
    arr = np.load(str(npy_path), mmap_mode="r")
    if row < 0 or col < 0 or row >= arr.shape[0] or col >= arr.shape[1]:
        raise ValueError("点击位置超出结果范围")
    value = float(arr[row, col])
    if not math.isfinite(value):
        return {"value": None, "class": None}
    if value < low:
        cls = "low"
    elif value > high:
        cls = "high"
    else:
        cls = "suitable"
    return {"value": round(value, 3), "class": cls}
