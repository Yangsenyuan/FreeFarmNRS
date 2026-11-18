"""结果导出：PNG 专题图与 PDF 反演报告。"""
from __future__ import annotations

import io
import math
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from ..core.config import APP_NAME, APP_VERSION, CLASS_COLORS

# 中文字体：Windows 本地与 Linux 容器（Render/Docker）通用
FONT_CANDIDATES = [
    *([Path(os.environ["NMASS_FONT"])] if os.environ.get("NMASS_FONT") else []),
    Path("C:/Windows/Fonts/simhei.ttf"),
    Path("C:/Windows/Fonts/msyh.ttc"),
    Path("C:/Windows/Fonts/simsun.ttc"),
    Path("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"),
    Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
]


def _result_unit(summary: Dict[str, Any]) -> str:
    """返回本次结果的实际单位；缺失时明确标记待确认，不冒充 mg/g。"""
    unit = str(summary.get("unit") or (summary.get("model") or {}).get("unit") or "").strip()
    return unit or "待确认"


def _fmt_value(value: Any, unit: str) -> str:
    return f"{value} {unit}" if value not in (None, "") else "—"


def _date_label(image_meta: Dict[str, Any]) -> str:
    text = " ".join(
        str(image_meta.get(key) or "")
        for key in ("sensor", "sensor_name", "image_type", "format")
    ).casefold()
    return "采集日期" if any(token in text for token in ("satellite", "卫星", "planetscope", "sentinel", "landsat")) else "飞行日期"


def _font_path() -> Optional[Path]:
    for p in FONT_CANDIDATES:
        if p.exists():
            return p
    return None


# ---------------------------------------------------------------- matplotlib 字体

def _setup_matplotlib():
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import rcParams
    import matplotlib.font_manager as fm

    path = _font_path()
    if path:
        try:
            fm.fontManager.addfont(str(path))
            name = fm.FontProperties(fname=str(path)).get_name()
        except Exception:
            name = "SimHei"
        rcParams["font.sans-serif"] = [name, "Microsoft YaHei", "SimHei", "DejaVu Sans"]
    else:
        rcParams["font.sans-serif"] = ["DejaVu Sans"]
    rcParams["axes.unicode_minus"] = False
    return matplotlib


def _load_map_array(summary: Dict[str, Any], result_dir: Path, max_dim: int = 1800) -> Tuple[np.ndarray, int]:
    npy_name = (summary.get("files") or {}).get("npy") or "nmass_latest.npy"
    arr = np.load(result_dir / npy_name)
    low = float(summary["thresholds"]["low"])
    high = float(summary["thresholds"]["high"])
    step = max(1, int(math.ceil(max(arr.shape) / float(max_dim))))
    values = arr[::step, ::step]
    valid = np.isfinite(values)
    classes = np.zeros(values.shape, dtype=np.uint8)
    classes[(values >= low) & (values <= high)] = 1
    classes[values > high] = 2

    # 掩膜区用浅灰色，避免被误认为数据空洞
    rgb = np.ones(values.shape + (3,), dtype=np.float32) * np.array([0.900, 0.918, 0.895], dtype=np.float32)
    for i, (key, _, color, _) in enumerate(
        [
            ("low", "低氮", CLASS_COLORS["low"], ""),
            ("suitable", "适宜", CLASS_COLORS["suitable"], ""),
            ("high", "高氮", CLASS_COLORS["high"], ""),
        ]
    ):
        c = color.lstrip("#")
        rgb[valid & (classes == i)] = np.array(
            [int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)], dtype=np.float32
        ) / 255.0
    return rgb, step


def _scale_bar(ax, summary: Dict[str, Any], width_px: int):
    area = summary.get("area") or {}
    pixel_area = float(area.get("pixel_area_m2") or 0)
    if pixel_area <= 0:
        return
    pixel_size = math.sqrt(pixel_area)
    raw = width_px * pixel_size / 4.0
    if raw <= 0:
        return
    exp = math.floor(math.log10(raw))
    base = 10 ** exp
    for m in (1, 2, 5, 10):
        if raw <= m * base:
            bar_m = m * base
            break
    else:
        bar_m = 10 * base
    bar_px = bar_m / pixel_size
    x0 = width_px * 0.05
    y0 = summary["height"] * 0.95
    # 使用下采样坐标
    step = max(1, int(math.ceil(max(summary["width"], summary["height"]) / 1800.0)))
    x0 /= step
    y0 /= step
    bar_px /= step
    ax.plot([x0, x0 + bar_px], [y0, y0], color="#1c2a21", lw=3, solid_capstyle="butt")
    ax.plot([x0, x0], [y0 - 8, y0 + 8], color="#1c2a21", lw=1.2)
    ax.plot([x0 + bar_px, x0 + bar_px], [y0 - 8, y0 + 8], color="#1c2a21", lw=1.2)
    ax.text(
        x0 + bar_px / 2,
        y0 - 26,
        f"{bar_m:g} m",
        ha="center",
        va="bottom",
        fontsize=9,
        color="#1c2a21",
        bbox=dict(facecolor="white", edgecolor="none", alpha=0.85, pad=1.5),
    )


def _north_arrow(ax, height_px: int, width_px: int):
    x = width_px * 0.94
    y = height_px * 0.90
    ax.annotate(
        "N",
        xy=(x, y),
        xytext=(x, y + height_px * 0.08),
        ha="center",
        va="center",
        fontsize=12,
        color="#1c2a21",
        arrowprops=dict(arrowstyle="-|>", color="#1c2a21", lw=1.6),
        bbox=dict(facecolor="white", edgecolor="#1c2a21", boxstyle="circle,pad=0.25"),
    )


def _map_figure(summary: Dict[str, Any], result_dir: Path, include_header: bool = True):
    _setup_matplotlib()
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    rgb, step = _load_map_array(summary, result_dir)
    height, width = rgb.shape[:2]

    fig = plt.figure(figsize=(11.5, 8.2), dpi=180, facecolor="#f6f8f4")
    if include_header:
        ax_head = fig.add_axes([0.04, 0.90, 0.92, 0.075])
        ax_head.set_xlim(0, 1)
        ax_head.set_ylim(0, 1)
        ax_head.axis("off")
        ax_head.text(0.0, 0.72, "氮素含量（Nmass）反演专题图", fontsize=20, fontweight="bold", color="#1b3a25", transform=ax_head.transAxes)
        ax_head.text(
            0.0,
            0.20,
            f"{summary.get('image_name', '')}　|　反演模型：{(summary.get('model') or {}).get('name', '')}",
            fontsize=10.5,
            color="#5b6f61",
            transform=ax_head.transAxes,
        )
        if bool((summary.get("model") or {}).get("demo")):
            ax_head.text(
                0.84,
                0.20,
                "合成示例 · 仅用于流程演示",
                fontsize=8.8,
                color="#B45309",
                ha="right",
                transform=ax_head.transAxes,
                bbox=dict(facecolor="#FFF4E5", edgecolor="#E8C797", boxstyle="round,pad=0.25"),
            )
        ax_map = fig.add_axes([0.04, 0.10, 0.68, 0.76])
        ax_side = fig.add_axes([0.745, 0.10, 0.235, 0.76])
    else:
        ax_map = fig.add_axes([0.02, 0.06, 0.74, 0.90])
        ax_side = fig.add_axes([0.78, 0.06, 0.20, 0.90])

    ax_map.imshow(rgb, interpolation="nearest")
    ax_map.set_axis_off()
    ax_map.add_patch(
        Rectangle((0, 0), 1, 1, transform=ax_map.transAxes, fill=False, edgecolor="#b9c6b8", lw=1.0)
    )
    low = summary["thresholds"]["low"]
    high = summary["thresholds"]["high"]
    unit = _result_unit(summary)
    _scale_bar(ax_map, summary, width)
    _north_arrow(ax_map, height, width)

    ax_side.set_xlim(0, 1)
    ax_side.set_ylim(0, 1)
    ax_side.axis("off")
    ax_side.text(0, 0.985, "等级图例", fontsize=13, fontweight="bold", color="#1b3a25", va="top", transform=ax_side.transAxes)
    items = [
        ("低氮", f"< {low:g}", CLASS_COLORS["low"], "需追肥"),
        ("适宜", f"{low:g} – {high:g}", CLASS_COLORS["suitable"], "保持现状"),
        ("高氮", f"> {high:g}", CLASS_COLORS["high"], "控制施肥"),
        ("掩膜", "非植被/无效", "#e6eae4", "不参与统计"),
    ]
    for i, (name, rng, color, action) in enumerate(items):
        y = 0.915 - i * 0.082
        ax_side.add_patch(
            Rectangle((0.0, y - 0.030), 0.13, 0.043, facecolor=color, transform=ax_side.transAxes, edgecolor="#c3cec2", lw=0.5)
        )
        ax_side.text(0.17, y - 0.008, name, fontsize=10.5, fontweight="bold", color="#1c2a21", va="center", transform=ax_side.transAxes)
        ax_side.text(0.17, y - 0.040, f"{rng} {unit} · {action}" if name != "掩膜" else "非植被 / 无效像元", fontsize=7.8, color="#607267", va="center", transform=ax_side.transAxes)

    stats = summary.get("stats") or {}
    area = summary.get("area") or {}
    ax_side.text(0, 0.575, "统计摘要", fontsize=13, fontweight="bold", color="#1b3a25", va="top", transform=ax_side.transAxes)
    rows = [
        ("均值", f"{stats.get('mean'):.2f} {unit}" if stats.get("mean") is not None else "—"),
        ("标准差", f"{stats.get('std'):.2f}" if stats.get("std") is not None else "—"),
        ("变异系数", f"{stats.get('cv'):.1f}%" if stats.get("cv") is not None else "—"),
        ("有效面积", f"{area.get('valid_mu', 0):.2f} 亩"),
        ("有效像元", f"{(stats.get('valid_ratio') or 0) * 100:.1f}%"),
    ]
    for i, (k, v) in enumerate(rows):
        y = 0.515 - i * 0.070
        ax_side.text(0.0, y, k, fontsize=9.2, color="#6b7d70", va="center", transform=ax_side.transAxes)
        ax_side.text(0.98, y, v, fontsize=10.0, color="#1c2a21", ha="right", va="center", fontweight="bold", transform=ax_side.transAxes)
        ax_side.plot([0, 1], [y - 0.032, y - 0.032], color="#dbe5da", lw=0.8, transform=ax_side.transAxes)

    ax_side.text(
        0,
        0.03,
        "由 FreeFarmNRS 生成",
        fontsize=8,
        color="#8a998f",
        style="italic",
        transform=ax_side.transAxes,
    )
    return fig


def export_result_png(summary: Dict[str, Any], result_dir: str | Path, out_path: str | Path) -> Path:
    result_dir = Path(result_dir)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig = _map_figure(summary, result_dir, include_header=True)
    fig.savefig(out_path, dpi=200, facecolor=fig.get_facecolor())
    import matplotlib.pyplot as plt

    plt.close(fig)
    return out_path


def _histogram_bytes(summary: Dict[str, Any]) -> io.BytesIO:
    _setup_matplotlib()
    import matplotlib.pyplot as plt

    hist = summary.get("histogram") or {}
    edges = hist.get("edges") or []
    counts = hist.get("counts") or []
    fig, ax = plt.subplots(figsize=(8.2, 3.9), dpi=170, facecolor="#ffffff")
    if len(edges) == len(counts) + 1 and counts:
        centers = [(edges[i] + edges[i + 1]) / 2 for i in range(len(counts))]
        width = (edges[-1] - edges[0]) / max(len(counts), 1) * 0.86
        ax.bar(centers, counts, width=width, color="#3f8f5a", edgecolor="white", linewidth=0.4)
    mean = hist.get("mean")
    if mean is not None:
        ax.axvline(mean, color="#f2a93b", linestyle="--", linewidth=1.8, label=f"均值 {mean:.2f}")
    for i, t in enumerate(hist.get("thresholds") or []):
        ax.axvline(t, color="#7b8b80", linestyle=":", linewidth=1.3, label="分级阈值" if i == 0 else None)
    unit = _result_unit(summary)
    ax.set_xlabel(f"氮素含量 Nmass（{unit}）", fontsize=10, color="#33463a")
    ax.set_ylabel("像元数", fontsize=10, color="#33463a")
    ax.tick_params(colors="#5b6f61", labelsize=8.5)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color("#c9d6cc")
    ax.grid(axis="y", color="#e6eee7", linewidth=0.8)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, fontsize=8.5, loc="upper right")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=170, facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return buf


def _map_bytes(summary: Dict[str, Any], result_dir: Path) -> io.BytesIO:
    fig = _map_figure(summary, result_dir, include_header=True)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=170, facecolor=fig.get_facecolor())
    import matplotlib.pyplot as plt

    plt.close(fig)
    buf.seek(0)
    return buf


# ---------------------------------------------------------------- PDF

def _pdf_styles():
    from reportlab.lib.colors import HexColor
    from reportlab.lib.styles import ParagraphStyle

    font = "SimHei"
    return {
        "title": ParagraphStyle("title", fontName=font, fontSize=18, leading=24, textColor=HexColor("#ffffff")),
        "subtitle": ParagraphStyle("subtitle", fontName=font, fontSize=10, leading=14, textColor=HexColor("#dcebd9")),
        "h2": ParagraphStyle("h2", fontName=font, fontSize=13, leading=18, textColor=HexColor("#1b5e2a"), spaceBefore=10, spaceAfter=6),
        "body": ParagraphStyle("body", fontName=font, fontSize=9.5, leading=15, textColor=HexColor("#263a2d")),
        "small": ParagraphStyle("small", fontName=font, fontSize=8, leading=11, textColor=HexColor("#6b7d70")),
        "cell": ParagraphStyle("cell", fontName=font, fontSize=8.8, leading=12, textColor=HexColor("#263a2d")),
        "cell_b": ParagraphStyle("cell_b", fontName=font, fontSize=8.8, leading=12, textColor=HexColor("#ffffff")),
        "label": ParagraphStyle("label", fontName=font, fontSize=8.8, leading=12, textColor=HexColor("#1b5e2a")),
    }


def export_result_pdf(
    summary: Dict[str, Any],
    project: Dict[str, Any],
    image_meta: Dict[str, Any],
    result_dir: str | Path,
    out_path: str | Path,
) -> Path:
    result_dir = Path(result_dir)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (
        Image,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    font_file = _font_path()
    if font_file is None:
        raise ValueError("系统中未找到中文字体，无法生成 PDF 报告")
    try:
        pdfmetrics.registerFont(TTFont("SimHei", str(font_file)))
    except Exception:
        # TTC 等格式的回退
        pdfmetrics.registerFont(TTFont("SimHei", "C:/Windows/Fonts/simhei.ttf"))

    styles = _pdf_styles()
    dark_green = colors.HexColor("#1b5e2a")
    light_green = colors.HexColor("#eaf4e8")
    line_gray = colors.HexColor("#dbe5da")
    text_gray = colors.HexColor("#5b6f61")

    def P(text: Any, style="cell"):
        return Paragraph(str(text), styles[style])

    def cell_table(data, col_widths, header=True, highlight_rows=None):
        t = Table(data, colWidths=col_widths, hAlign="LEFT")
        style = [
            ("FONTNAME", (0, 0), (-1, -1), "SimHei"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.8),
            ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#263a2d")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("LINEBELOW", (0, 0), (-1, -1), 0.4, line_gray),
        ]
        if header:
            style += [
                ("BACKGROUND", (0, 0), (-1, 0), dark_green),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "SimHei"),
            ]
        if highlight_rows:
            for r in highlight_rows:
                style.append(("BACKGROUND", (0, r), (-1, r), light_green))
        t.setStyle(TableStyle(style))
        return t

    stats = summary.get("stats") or {}
    area = summary.get("area") or {}
    model = summary.get("model") or {}
    thresholds = summary.get("thresholds") or {}
    unit = _result_unit(summary)
    trace = summary.get("trace") or {}
    model_trace = trace.get("model") or {}
    alignment_trace = trace.get("alignment") or {}
    preprocess_trace = trace.get("preprocessing") or {}
    created = summary.get("created_at") or ""
    try:
        created_dt = datetime.fromisoformat(created).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        created_dt = created

    story: List[Any] = []

    # 封面式页眉
    header = Table(
        [
            [Paragraph(f"{APP_NAME}", styles["title"])],
            [Paragraph("氮素含量反演报告 · Nmass Inversion Report", styles["subtitle"])],
        ],
        colWidths=[174 * mm],
    )
    header.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), dark_green),
                ("LEFTPADDING", (0, 0), (-1, -1), 14),
                ("RIGHTPADDING", (0, 0), (-1, -1), 14),
                ("TOPPADDING", (0, 0), (-1, 0), 14),
                ("BOTTOMPADDING", (0, -1), (-1, -1), 12),
                ("TOPPADDING", (0, 1), (-1, 1), 2),
            ]
        )
    )
    story.append(header)
    story.append(Spacer(1, 8))

    story.append(
        Paragraph(
            f"项目：{project.get('name', '')}　|　影像：{summary.get('image_name', '')}　|　生成时间：{created_dt}",
            styles["small"],
        )
    )
    story.append(Spacer(1, 8))

    info_rows = [
        [P("项目名称", "label"), P(project.get("name", "—")), P("田块编号", "label"), P(image_meta.get("field") or project.get("field") or "—")],
        [P("影像文件", "label"), P(summary.get("image_name", "—")), P("传感器", "label"), P(image_meta.get("sensor") or "—")],
        [P(_date_label(image_meta), "label"), P(image_meta.get("flight_date") or "—"), P("反演模型", "label"), P(model.get("name", "—"))],
        [P("反演时间", "label"), P(created_dt), P("结果文件", "label"), P((summary.get("files") or {}).get("tif", "—"))],
    ]
    # 表头行其实是信息行：第一列和第二列、第三列和第四列分别深色标签
    info_table = Table(info_rows, colWidths=[24 * mm, 61 * mm, 24 * mm, 65 * mm], hAlign="LEFT")
    style_cmds = [
        ("FONTNAME", (0, 0), (-1, -1), "SimHei"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.8),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, line_gray),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f2f7f0")),
        ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#f2f7f0")),
        ("TEXTCOLOR", (0, 0), (0, -1), dark_green),
        ("TEXTCOLOR", (2, 0), (2, -1), dark_green),
    ]
    info_table.setStyle(TableStyle(style_cmds))
    story.append(info_table)

    story.append(Paragraph("一、反演概况", styles["h2"]))
    stat_rows = [
        [P("统计指标", "cell_b"), P("数值", "cell_b"), P("统计指标", "cell_b"), P("数值", "cell_b")],
        [P("均值"), P(f"{stats.get('mean', '—')} {unit}"), P("标准差"), P(f"{stats.get('std', '—')} {unit}")],
        [P("变异系数"), P(f"{stats.get('cv', '—')}%"), P("中位数"), P(f"{stats.get('p50', '—')} {unit}")],
        [P("最小值"), P(f"{stats.get('min', '—')} {unit}"), P("最大值"), P(f"{stats.get('max', '—')} {unit}")],
        [P("P5 / P95"), P(f"{stats.get('p5', '—')} / {stats.get('p95', '—')} {unit}"), P("有效像元占比"), P(f"{(stats.get('valid_ratio') or 0) * 100:.1f}%")],
        [P("有效面积"), P(f"{area.get('valid_mu', 0):.2f} 亩（{area.get('valid_ha', 0):.3f} 公顷）"), P("像元大小"), P(f"{math.sqrt(area.get('pixel_area_m2') or 0):.3f} m")],
    ]
    story.append(cell_table(stat_rows, [30 * mm, 57 * mm, 30 * mm, 57 * mm], highlight_rows=[1, 3, 5]))
    story.append(Spacer(1, 6))
    story.append(
        Paragraph(
            f"结论提示：田块氮素均值 {stats.get('mean', '—')} {unit}，变异系数 {stats.get('cv', '—')}%；"
            f"低氮区建议优先追肥，高氮区注意控制氮肥投入。分级阈值可结合当地农艺标准调整。",
            styles["body"],
        )
    )

    # 首页管理建议摘要
    story.append(Spacer(1, 6))
    story.append(Paragraph("管理建议", styles["h2"]))
    advise_rows = [[P("等级", "cell_b"), P("面积占比", "cell_b"), P("管理建议", "cell_b")]]
    advise_styles = [
        ("BACKGROUND", (0, 0), (-1, 0), dark_green),
    ]
    tint_map = {"low": colors.HexColor("#fbeee6"), "suitable": colors.HexColor("#eaf6ef"), "high": colors.HexColor("#eaf0f8")}
    for idx, c in enumerate(summary.get("class_stats") or [], start=1):
        advise_rows.append(
            [
                P(c.get("name")),
                P(f"{(c.get('ratio') or 0) * 100:.1f}%"),
                P(c.get("action") or "—"),
            ]
        )
        advise_styles.append(("BACKGROUND", (0, idx), (-1, idx), tint_map.get(c.get("key"), colors.white)))
    advise_table = Table(advise_rows, colWidths=[34 * mm, 34 * mm, 106 * mm], hAlign="LEFT")
    advise_table.setStyle(
        TableStyle(
            advise_styles
            + [
                ("FONTNAME", (0, 0), (-1, -1), "SimHei"),
                ("FONTSIZE", (0, 0), (-1, -1), 8.8),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("LINEBELOW", (0, 0), (-1, -1), 0.4, line_gray),
            ]
        )
    )
    story.append(advise_table)

    story.append(PageBreak())
    story.append(Paragraph("二、氮素分级专题图", styles["h2"]))
    map_img = _map_bytes(summary, result_dir)
    story.append(Image(map_img, width=174 * mm, height=124 * mm))
    story.append(Spacer(1, 4))
    story.append(
        Paragraph(
            f"图 1　氮素含量反演专题图（颜色图例：低氮 &lt;{thresholds.get('low')}，适宜 {thresholds.get('low')}–{thresholds.get('high')}，高氮 &gt;{thresholds.get('high')} {unit}）",
            styles["small"],
        )
    )

    story.append(Paragraph("三、氮素分级面积统计", styles["h2"]))
    class_rows = [[P("等级", "cell_b"), P(f"Nmass 范围（{unit}）", "cell_b"), P("面积（亩）", "cell_b"), P("面积占比", "cell_b"), P("管理建议", "cell_b")]]
    for c in summary.get("class_stats") or []:
        rng = c.get("range") or {}
        if c.get("key") == "low":
            range_text = f"< {rng.get('max')}"
        elif c.get("key") == "high":
            range_text = f"> {rng.get('min')}"
        else:
            range_text = f"{rng.get('min')} – {rng.get('max')}"
        class_rows.append(
            [
                P(c.get("name")),
                P(range_text),
                P(f"{c.get('area_mu', 0):.2f}"),
                P(f"{(c.get('ratio') or 0) * 100:.1f}%"),
                P(c.get("action") or "—"),
            ]
        )
    story.append(cell_table(class_rows, [24 * mm, 42 * mm, 28 * mm, 28 * mm, 52 * mm]))

    story.append(PageBreak())
    story.append(Paragraph("四、氮素分布直方图", styles["h2"]))
    hist_img = _histogram_bytes(summary)
    story.append(Image(hist_img, width=168 * mm, height=80 * mm))
    story.append(Spacer(1, 4))
    story.append(Paragraph("图 2　反演结果像元分布直方图（虚线为均值与分级阈值）。", styles["small"]))

    story.append(Paragraph("五、模型与参数信息", styles["h2"]))
    train_wl = model_trace.get("training_wavelengths") or []
    train_range = (
        f"{min(train_wl):g}–{max(train_wl):g} nm（{len(train_wl)} 波段）"
        if train_wl
        else "未记录"
    )
    source_range = alignment_trace.get("source_range") or []
    target_range = alignment_trace.get("target_range") or []
    range_text = (
        f"影像 {source_range[0]:g}–{source_range[1]:g} nm；模型 {target_range[0]:g}–{target_range[1]:g} nm"
        if len(source_range) == 2 and len(target_range) == 2
        else "未记录"
    )
    fill_text = (
        f"精确 {alignment_trace.get('exact_bands', 0)}，插值 {alignment_trace.get('interpolated_bands', 0)}，"
        f"边缘填充 {alignment_trace.get('edge_filled_bands', 0)}；"
        f"填充比例 {(alignment_trace.get('edge_fill_ratio') or 0) * 100:.1f}%"
    )
    sha = model_trace.get("file_sha256") or model.get("file_sha256") or "未记录"
    force_text = "是" if trace.get("force_used") else "否"
    ignored = trace.get("ignored_warnings") or []
    few_shot = model.get("few_shot") or model_trace.get("few_shot") or {}
    if model.get("r2") is not None and model.get("rmse") is not None:
        metric_text = (
            f"R2 = {model.get('r2')}，RMSE = {model.get('rmse')} {unit}"
            f"（{model.get('metric_type') or '元数据指标'}）"
        )
    elif few_shot:
        metric_text = "Few-shot 有效样点不足，未形成留一验证指标；样本内校正不作为独立精度声明"
    else:
        metric_text = "未提供与该案例对应的独立精度指标；该模型仅用于流程演示"
    param_rows = [
        [P("项目", "cell_b"), P("内容", "cell_b")],
        [P("模型名称"), P(model.get("name") or "—")],
        [P("算法类型"), P(model.get("algorithm") or "—")],
        [P("模型版本 / 哈希"), P(f"{model.get('version') or '—'} / {str(sha)[:16]}")],
        [P("训练波长"), P(train_range)],
        [P("影像与模型波段"), P(range_text)],
        [P("重采样与边缘填充"), P(fill_text)],
        [P("适用作物 / 传感器"), P(f"{(', '.join(model.get('crops') or [])) or '—'} / {(', '.join(model.get('sensors') or [])) or '—'}")],
        [P("训练数据"), P(model.get("training_data") or "—")],
        [P("精度指标"), P(metric_text)],
        [P("分级阈值"), P(f"低氮 < {thresholds.get('low')} {unit}；适宜 {thresholds.get('low')}–{thresholds.get('high')} {unit}；高氮 > {thresholds.get('high')} {unit}")],
        [P("兼容性确认"), P(f"强制忽略警告：{force_text}；被忽略警告：{'；'.join(ignored) if ignored else '无'}")],
        [P("预处理摘要"), P(f"配置哈希：{preprocess_trace.get('config_sha256') or '—'}；质量等级：{preprocess_trace.get('quality_level') or '—'}；植被像元占比：{preprocess_trace.get('mask_ratio')}")],
        [P("质量控制"), P(preprocess_trace.get("pipeline") or "无效像元不参与统计。")],
    ]
    if few_shot:
        loo = few_shot.get("leave_one_out") or {}
        metric_detail = (
            f"留一 MAE {loo.get('mae')}，RMSE {loo.get('rmse')} {unit}，R2 {loo.get('r2')}"
            if loo.get("available") and loo.get("rmse") is not None
            else "有效样点不足，未形成留一验证指标"
        )
        param_rows.extend(
            [
                [
                    P("Few-shot 父模型"),
                    P(
                        f"{model.get('parent_model_name') or few_shot.get('parent_model_name') or '—'}"
                        f"（{model.get('parent_model_id') or few_shot.get('parent_model_id') or '—'}）"
                    ),
                ],
                [
                    P("Few-shot 校正"),
                    P(
                        f"模式 {few_shot.get('mode') or '—'}；样点 {few_shot.get('sample_count') or '—'}；"
                        f"斜率 {few_shot.get('slope')}；截距 {few_shot.get('intercept')} {unit}；"
                        f"样点指纹 {str(few_shot.get('sample_fingerprint') or '—')[:16]}"
                    ),
                ],
                [P("Few-shot 留一验证"), P(metric_detail)],
            ]
        )
    story.append(cell_table(param_rows, [36 * mm, 138 * mm]))

    story.append(Spacer(1, 10))
    story.append(
        Paragraph(
            f"说明：本报告由 {APP_NAME} V{APP_VERSION} 本地生成，数据仅在本机处理，不上传外网。"
            "内置示例模型基于合成数据训练，仅用于功能演示；正式科研与生产应用请导入基于实测数据训练的模型。",
            styles["small"],
        )
    )

    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(line_gray)
        canvas.setLineWidth(0.5)
        canvas.line(18 * mm, 14 * mm, 192 * mm, 14 * mm)
        canvas.setFont("SimHei", 7.5)
        canvas.setFillColor(text_gray)
        canvas.drawString(18 * mm, 9 * mm, f"{APP_NAME} V{APP_VERSION}")
        canvas.drawRightString(192 * mm, 9 * mm, f"第 {doc.page} 页")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=15 * mm,
        bottomMargin=18 * mm,
        title="氮素反演报告",
        author=APP_NAME,
    )
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return out_path


def result_dir_for(image_dir: str | Path) -> Path:
    return Path(image_dir) / "results"
