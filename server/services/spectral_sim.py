"""简化冠层光谱模拟器。

用于生成演示影像与示例预训练模型。光谱随氮含量变化的方向符合植被遥感常识：
氮含量升高 → 红边吸收增强、近红外反射率升高，NDRE 增大。
注意：该模拟器仅用于功能演示，不能替代 PROSAIL 等辐射传输模型。
"""
from __future__ import annotations

from typing import Sequence, Tuple

import numpy as np

ANCHOR_WL = np.array(
    [400, 450, 500, 550, 580, 620, 650, 675, 700, 715, 730, 750, 800, 850, 900],
    dtype=float,
)

# 植被光谱锚点：refl = a + n * b，其中 n = (Nmass - 15) / 20，范围约 0~1.5
_VEG_A = np.array(
    [0.035, 0.045, 0.060, 0.080, 0.060, 0.042, 0.035, 0.030, 0.060, 0.170, 0.300, 0.400, 0.440, 0.450, 0.430],
    dtype=float,
)
_VEG_B = np.array(
    [0.003, 0.004, 0.005, 0.030, 0.010, 0.000, -0.005, -0.015, -0.050, -0.090, -0.110, 0.040, 0.080, 0.100, 0.090],
    dtype=float,
)

# 裸土光谱
_SOIL = np.array(
    [0.075, 0.095, 0.115, 0.135, 0.150, 0.170, 0.185, 0.195, 0.205, 0.210, 0.215, 0.220, 0.230, 0.235, 0.240],
    dtype=float,
)


def wavelengths_default() -> np.ndarray:
    return np.arange(400, 901, 10, dtype=float)


def vegetation_anchor_spectra(wl: Sequence[float]) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """返回 (n=0 光谱, 单位氮增量光谱, 土壤光谱)，均插值到目标波长。"""
    wl = np.asarray(wl, dtype=float)
    a = np.interp(wl, ANCHOR_WL, _VEG_A)
    b = np.interp(wl, ANCHOR_WL, _VEG_B)
    soil = np.interp(wl, ANCHOR_WL, _SOIL)
    return a, b, soil


def simulate_spectra(
    nmass: np.ndarray,
    veg_fraction: np.ndarray,
    wl: Sequence[float],
    noise: float = 0.0,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """按像元模拟光谱。nmass / veg_fraction 形状相同 (H,W) 或标量数组；返回 (H,W,B)。"""
    nmass = np.asarray(nmass, dtype=np.float32)
    vf = np.asarray(veg_fraction, dtype=np.float32)
    n = np.clip((nmass - 15.0) / 20.0, 0.0, 1.5)
    a, b, soil = vegetation_anchor_spectra(wl)
    veg = a[None, None, :] + n[..., None] * b[None, None, :]
    vf3 = np.clip(vf, 0.0, 1.0)[..., None]
    refl = veg * vf3 + soil[None, None, :] * (1.0 - vf3)
    if noise > 0:
        rng = rng or np.random.default_rng(42)
        refl = refl + rng.normal(0.0, noise, size=refl.shape).astype(np.float32)
    return np.clip(refl, 0.001, 0.95).astype(np.float32)


def ndre_of_spectra(spectra: np.ndarray, wl: Sequence[float]) -> np.ndarray:
    wl = np.asarray(wl, dtype=float)
    nir_i = int(np.argmin(np.abs(wl - 840)))
    re_i = int(np.argmin(np.abs(wl - 730)))
    nir = spectra[..., nir_i]
    re = spectra[..., re_i]
    denom = nir + re
    return np.where(np.abs(denom) > 1e-6, (nir - re) / denom, np.nan)
