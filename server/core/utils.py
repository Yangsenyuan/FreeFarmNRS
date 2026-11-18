"""通用工具函数。"""
from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, List, Optional


def now_iso() -> str:
    """本地时区的 ISO 时间字符串（秒精度）。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def new_id(prefix: str, nbytes: int = 4) -> str:
    return f"{prefix}_{secrets.token_hex(nbytes)}"


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, OSError):
        return default


def write_json(path: Path, data: Any) -> None:
    """原子写入 JSON，避免中断导致文件损坏。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            try:
                os.remove(tmp_name)
            except OSError:
                pass


_ILLEGAL_FS = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def sanitize_filename(name: str, fallback: str = "file") -> str:
    name = (name or "").strip().replace("\x00", "")
    name = _ILLEGAL_FS.sub("_", name).strip(" .")
    if not name:
        name = fallback
    # 限制长度，保留扩展名
    if len(name) > 120:
        stem, dot, ext = name.rpartition(".")
        if dot and len(ext) <= 10:
            name = stem[: 120 - len(ext) - 1] + "." + ext
        else:
            name = name[:120]
    return name


def parse_number_list(value: Any) -> List[float]:
    """从 '400, 410, 420' / '400 410 420' / '[400,410]' 中解析数值列表。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        out = []
        for v in value:
            try:
                out.append(float(v))
            except (TypeError, ValueError):
                continue
        return out
    text = str(value)
    text = text.replace("，", ",").replace(";", ",").replace("；", ",")
    nums = re.findall(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", text)
    return [float(n) for n in nums]


def mean_or_none(values: Iterable[float]) -> Optional[float]:
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def format_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024.0
    return f"{size:.1f} TB"
