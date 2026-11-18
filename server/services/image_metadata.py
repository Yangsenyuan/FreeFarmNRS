"""统一解析 ENVI、GeoTIFF 与常见伴随文件中的影像元数据。

本模块不改写影像。所有字段都保留来源、置信度和原始值；合并时
``user`` 来源始终优先于自动解析结果。
"""
from __future__ import annotations

import copy
import json
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

METADATA_FIELDS = (
    "sensor_name",
    "sensor_type",
    "acquisition_datetime",
    "flight_date",
    "field_id",
    "image_type",
    "value_type",
    "processing_level",
    "wavelengths",
    "bandwidths",
    "wavelength_unit",
    "band_names",
)

SOURCE_PRIORITY = {
    "user": 100,
    "xmp_sidecar": 82,
    "xml_sidecar": 80,
    "json_sidecar": 76,
    "aux_xml": 72,
    "envi_header": 68,
    "geotiff_tags": 64,
    "band_tags": 58,
    "filename_rule": 32,
    "default": 5,
}

SOURCE_CONFIDENCE = {
    "user": 1.0,
    "xmp_sidecar": 0.94,
    "xml_sidecar": 0.93,
    "json_sidecar": 0.90,
    "aux_xml": 0.86,
    "envi_header": 0.88,
    "geotiff_tags": 0.84,
    "band_tags": 0.78,
    "filename_rule": 0.52,
    "default": 0.10,
}

LEGACY_FIELD_NAMES = {
    "sensor": "sensor_name",
    "instrument": "sensor_name",
    "field": "field_id",
    "field_name": "field_id",
    "date": "flight_date",
    "capture_date": "flight_date",
    "data_type": "value_type",
    "fwhm": "bandwidths",
    "band_widths": "bandwidths",
    "center_wavelengths": "wavelengths",
}

ENVI_DTYPE = {
    1: "uint8",
    2: "int16",
    3: "int32",
    4: "float32",
    5: "float64",
    6: "complex64",
    9: "complex128",
    12: "uint16",
    13: "uint32",
    14: "int64",
    15: "uint64",
}

_FIELD_ALIASES: Dict[str, Tuple[str, ...]] = {
    "sensor_name": (
        "sensor name",
        "sensor_name",
        "sensorname",
        "sensor",
        "instrument",
        "camera",
        "camera model",
        "camera_model",
        "sensor model",
        "model",
        "tifftag_model",
    ),
    "sensor_type": (
        "sensor type",
        "sensor_type",
        "instrument type",
        "camera type",
        "type",
    ),
    "acquisition_datetime": (
        "acquisition datetime",
        "acquisition_datetime",
        "acquisition date time",
        "acquisition_date_time",
        "datetime",
        "date time",
        "capture time",
        "capture_datetime",
        "datetimeoriginal",
        "tifftag_datetime",
    ),
    "flight_date": (
        "flight date",
        "flight_date",
        "acquisition date",
        "acquisition_date",
        "capture date",
        "date",
    ),
    "field_id": (
        "field id",
        "field_id",
        "field",
        "field name",
        "field_name",
        "plot id",
        "plot_id",
        "plot",
    ),
    "image_type": (
        "image type",
        "image_type",
        "imagetype",
        "spectral type",
        "spectral_type",
    ),
    "value_type": (
        "value type",
        "value_type",
        "data type",
        "data_type",
        "datatype",
        "sample type",
        "sample_format",
    ),
    "processing_level": (
        "processing level",
        "processing_level",
        "data level",
        "data_level",
        "level",
    ),
    "wavelengths": (
        "wavelength",
        "wavelengths",
        "wavelength list",
        "wavelength_list",
        "band wavelengths",
        "band_wavelengths",
        "center wavelengths",
        "centralwavelength",
        "central_wavelength",
    ),
    "bandwidths": (
        "fwhm",
        "bandwidth",
        "bandwidths",
        "band widths",
        "band_widths",
        "wavelength fwhm",
        "wavelength_fwhm",
    ),
    "wavelength_unit": (
        "wavelength units",
        "wavelength_units",
        "wavelength unit",
        "wavelength_unit",
        "wl units",
        "wl_units",
        "unit",
    ),
    "band_names": (
        "band names",
        "band_names",
        "band name",
        "band_name",
        "band descriptions",
        "band_descriptions",
    ),
}


def normalize_key(value: Any) -> str:
    """统一大小写、空格、连字符与下划线，便于跨格式查找别名。"""
    text = str(value or "").strip().casefold()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "_", text).strip("_")


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _decode_text(raw: bytes) -> Tuple[str, str]:
    """按 BOM、UTF-8、GB18030/GBK、Windows ANSI 顺序解码。"""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig"), "utf-8-sig"
    for encoding in ("utf-8", "gb18030", "gbk", "cp1252"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1"), "latin-1"


def _read_text(path: Path) -> Tuple[str, str]:
    text, encoding = _decode_text(path.read_bytes())
    return text.lstrip("\ufeff"), encoding


def _tag_lookup(tags: Dict[str, Any], aliases: Sequence[str]) -> Tuple[Optional[str], Any]:
    normalized = {normalize_key(k): (k, v) for k, v in (tags or {}).items()}
    for alias in aliases:
        found = normalized.get(normalize_key(alias))
        if found is not None:
            return str(found[0]), found[1]
    return None, None


def _split_values(value: Any) -> List[str]:
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if not _empty(v)]
    text = str(value or "").strip().strip("{}").strip()
    return [
        item.strip().strip("'\"")
        for item in re.split(r"[,，;\n\r]+", text)
        if item.strip().strip("'\"")
    ]


def parse_number_list(value: Any) -> List[float]:
    values: List[float] = []
    for token in _split_values(value):
        try:
            values.append(float(token))
        except (TypeError, ValueError):
            continue
    return values


def _normalize_wavelengths(
    values: Sequence[float], unit: Optional[str]
) -> Tuple[List[float], str]:
    unit_text = str(unit or "").strip().replace("μ", "µ").casefold()
    nums = [float(v) for v in values]
    if not nums:
        return [], "nm"
    if any(token in unit_text for token in ("um", "µm", "micron", "micrometer")):
        return [round(v * 1000.0, 6) for v in nums], "nm"
    if unit_text == "m" or unit_text.startswith("meter"):
        return [round(v * 1_000_000_000.0, 6) for v in nums], "nm"
    if "nm" in unit_text or "nanometer" in unit_text:
        return nums, "nm"
    vmax = max(abs(v) for v in nums)
    if vmax <= 5.0:
        return [round(v * 1000.0, 6) for v in nums], "nm"
    return nums, "nm"


def wavelength_from_label(label: Optional[str]) -> Optional[float]:
    """从 ``475nm``、``B6 560nm``、``Band 8 (840 nm)`` 等文本提取中心波长。"""
    if not label:
        return None
    text = str(label).replace("μ", "µ")
    match = re.search(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(nm|µm|um)\b", text, re.I)
    if match:
        value = float(match.group(1))
        return value * 1000.0 if match.group(2).casefold() in ("um", "µm") else value
    # 去掉 B6、Band 6、波段6 等编号，再寻找合理的波长数值。
    cleaned = re.sub(r"(?i)(?:\bband\b|波段)\s*\d+", " ", text)
    cleaned = re.sub(r"(?i)\bB\s*\d+\b", " ", cleaned)
    for token in re.findall(r"(?<![\d.])(\d+(?:\.\d+)?)(?![\d.])", cleaned):
        value = float(token)
        if 300.0 <= value <= 2500.0:
            return value
    return None


def _normalize_date(value: Any) -> Tuple[Optional[str], Optional[str]]:
    if _empty(value):
        return None, None
    raw = str(value).strip()
    compact = re.fullmatch(r"(20\d{2})(\d{2})(\d{2})", raw)
    if compact:
        iso = "-".join(compact.groups())
        return iso, iso + "T00:00:00"
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            parsed = datetime.strptime(raw[:10], fmt)
            return parsed.date().isoformat(), None
        except ValueError:
            pass
    try:
        parsed_dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed_dt.date().isoformat(), raw
    except ValueError:
        return None, raw


def _normalize_value_type(value: Any) -> Any:
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit() and int(text) in ENVI_DTYPE:
        return ENVI_DTYPE[int(text)]
    return text


class _Collector:
    def __init__(self) -> None:
        self.candidates: Dict[str, Dict[str, Any]] = {}
        self.all_candidates: List[Dict[str, Any]] = []

    def add(
        self,
        field: str,
        value: Any,
        source: str,
        raw_key: Optional[str] = None,
        raw_value: Any = None,
    ) -> None:
        if field not in METADATA_FIELDS or _empty(value):
            return
        priority = SOURCE_PRIORITY.get(source, 0)
        confidence = SOURCE_CONFIDENCE.get(source, 0.5)
        record = {
            "field": field,
            "value": value,
            "source": source,
            "priority": priority,
            "confidence": confidence,
            "raw_key": raw_key,
            "raw_value": raw_value,
        }
        self.all_candidates.append(record)
        current = self.candidates.get(field)
        if current is None or priority >= int(current["priority"]):
            self.candidates[field] = record

    def add_tags(self, tags: Dict[str, Any], source: str) -> None:
        for field, aliases in _FIELD_ALIASES.items():
            raw_key, raw_value = _tag_lookup(tags, aliases)
            if raw_key is None or _empty(raw_value):
                continue
            if isinstance(raw_value, (dict, list)) and field not in (
                "wavelengths",
                "bandwidths",
                "band_names",
            ):
                continue
            value: Any = raw_value
            if field in ("wavelengths", "bandwidths"):
                value = parse_number_list(raw_value)
            elif field == "band_names":
                value = _split_values(raw_value)
            elif field == "value_type":
                value = _normalize_value_type(raw_value)
            elif field == "flight_date":
                value, _ = _normalize_date(raw_value)
            self.add(field, value, source, raw_key, raw_value)


def _find_case_insensitive(parent: Path, names: Iterable[str]) -> Optional[Path]:
    wanted = {name.casefold() for name in names}
    if not parent.exists():
        return None
    try:
        for entry in parent.iterdir():
            if entry.is_file() and entry.name.casefold() in wanted:
                return entry
    except OSError:
        return None
    return None


def find_envi_header(dataset_path: str | Path) -> Optional[Path]:
    path = Path(dataset_path)
    if path.suffix.casefold() == ".hdr" and path.exists():
        return path
    candidates = [
        path.name + ".hdr",
        path.stem + ".hdr",
        path.name + ".HDR",
        path.stem + ".HDR",
    ]
    return _find_case_insensitive(path.parent, candidates)


def _parse_envi_values(text: str) -> Tuple[Dict[str, str], Dict[str, str]]:
    lines = text.lstrip("\ufeff").splitlines()
    normalized: Dict[str, str] = {}
    raw_values: Dict[str, str] = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        if "=" not in line:
            i += 1
            continue
        raw_key, _, value = line.partition("=")
        raw_key = raw_key.strip().lstrip("\ufeff")
        value = value.strip()
        if value.startswith("{"):
            buffer = [value]
            while "}" not in value and i + 1 < len(lines):
                i += 1
                value = lines[i]
                buffer.append(value)
            value = " ".join(buffer).strip()
            value = value[1:].rsplit("}", 1)[0].strip()
        value = value.strip().strip("'\"")
        normalized[normalize_key(raw_key)] = value
        raw_values[raw_key] = value
        i += 1
    return normalized, raw_values


def read_envi_header(path: str | Path) -> Dict[str, Any]:
    """读取 ENVI 头文件并返回编码、规范化值与原始键值。"""
    header_path = find_envi_header(path)
    if header_path is None:
        return {"path": None, "encoding": None, "values": {}, "raw_values": {}, "raw_text": ""}
    text, encoding = _read_text(header_path)
    values, raw_values = _parse_envi_values(text)
    return {
        "path": str(header_path),
        "encoding": encoding,
        "values": values,
        "raw_values": raw_values,
        "raw_text": text,
    }


def _apply_envi(
    collector: _Collector,
    header: Dict[str, Any],
    band_count: int,
) -> Dict[str, Any]:
    values = header.get("values") or {}
    if not values:
        return {}
    collector.add_tags(values, "envi_header")

    raw_key, raw_names = _tag_lookup(
        values,
        ("band names", "band_names", "band name", "band descriptions", "band_descriptions"),
    )
    labels = _split_values(raw_names) if raw_key else []
    if labels:
        collector.add("band_names", labels, "envi_header", raw_key, raw_names)
        parsed = [wavelength_from_label(label) for label in labels]
        if parsed and all(value is not None for value in parsed):
            collector.add("wavelengths", [float(v) for v in parsed if v is not None], "envi_header", raw_key, raw_names)

    raw_unit_key, raw_unit = _tag_lookup(values, _FIELD_ALIASES["wavelength_unit"])
    current_wl = collector.candidates.get("wavelengths")
    current_bw = collector.candidates.get("bandwidths")
    if current_wl:
        unit = raw_unit if raw_unit_key else "nm"
        normalized, unit = _normalize_wavelengths(current_wl["value"], unit)
        current_wl["value"] = normalized
        current_wl["raw_value"] = current_wl.get("raw_value")
        collector.add("wavelength_unit", unit, "envi_header", raw_unit_key, raw_unit)
    if current_bw:
        unit = raw_unit if raw_unit_key else "nm"
        normalized, _ = _normalize_wavelengths(current_bw["value"], unit)
        current_bw["value"] = normalized

    raw_data_type_key, raw_data_type = _tag_lookup(values, _FIELD_ALIASES["value_type"])
    if not collector.candidates.get("value_type") and raw_data_type:
        collector.add(
            "value_type",
            _normalize_value_type(raw_data_type),
            "envi_header",
            raw_data_type_key,
            raw_data_type,
        )
    return {
        "path": header.get("path"),
        "encoding": header.get("encoding"),
        "values": header.get("raw_values") or {},
        "normalized_keys": values,
        "band_count": band_count,
    }


def _read_json_sidecar(path: Optional[Path]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if path is None:
        return {}, {}
    text, encoding = _read_text(path)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}, {"path": str(path), "encoding": encoding, "parse_error": "JSON 结构无效"}
    return data if isinstance(data, dict) else {"value": data}, {
        "path": str(path),
        "encoding": encoding,
    }


def _walk_json(
    value: Any, prefix: str = "", depth: int = 0
) -> Iterable[Tuple[str, Any]]:
    if depth > 6:
        return
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield path, child
            yield from _walk_json(child, path, depth + 1)
    elif isinstance(value, list):
        return


def _deep_json_value(data: Dict[str, Any], aliases: Sequence[str]) -> Tuple[Optional[str], Any]:
    wanted = {normalize_key(alias) for alias in aliases}
    for path, value in _walk_json(data):
        key = path.rsplit(".", 1)[-1]
        if normalize_key(key) in wanted and not isinstance(value, (dict, list)):
            return path, value
    return None, None


def _apply_json(collector: _Collector, data: Dict[str, Any], source: str = "json_sidecar") -> None:
    if not data:
        return
    collector.add_tags(data, source)
    for field, aliases in _FIELD_ALIASES.items():
        if collector.candidates.get(field) and collector.candidates[field]["source"] == source:
            continue
        path, value = _deep_json_value(data, aliases)
        if path is None or _empty(value):
            continue
        parsed: Any = value
        if field in ("wavelengths", "bandwidths"):
            parsed = parse_number_list(value)
        elif field == "band_names":
            parsed = _split_values(value)
        elif field == "value_type":
            parsed = _normalize_value_type(value)
        elif field == "flight_date":
            parsed, _ = _normalize_date(value)
        collector.add(field, parsed, source, path, value)

    # MicaSense/通用 JSON 常见 Bands 数组。
    bands = None
    for path, value in _walk_json(data):
        if normalize_key(path.rsplit(".", 1)[-1]) in ("bands", "band", "spectral_bands") and isinstance(value, list):
            bands = value
            break
    if isinstance(bands, list) and bands:
        wavelengths: List[float] = []
        bandwidths: List[Optional[float]] = []
        names: List[str] = []
        for band in bands:
            if not isinstance(band, dict):
                continue
            _, wl = _tag_lookup(
                band,
                ("center wavelength", "centralwavelength", "wavelength", "wavelength_nm", "centerwavelength"),
            )
            _, bw = _tag_lookup(band, ("wavelength fwhm", "fwhm", "bandwidth", "band_width"))
            _, name = _tag_lookup(band, ("band name", "band_name", "name", "label"))
            wl_values = parse_number_list(wl)
            wavelengths.append(float(wl_values[0]) if wl_values else None)  # type: ignore[arg-type]
            bw_values = parse_number_list(bw)
            bandwidths.append(float(bw_values[0]) if bw_values else None)
            names.append(str(name).strip() if name is not None else "")
        if wavelengths and all(value is not None for value in wavelengths):
            collector.add("wavelengths", wavelengths, source, "bands", bands)
        if bandwidths and any(value is not None for value in bandwidths):
            collector.add("bandwidths", bandwidths, source, "bands", bands)
        if names:
            collector.add("band_names", names, source, "bands", bands)


def _xml_local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag.rsplit(":", 1)[-1]


def _read_aux_xml(path: Optional[Path]) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Dict[str, Any]]:
    if path is None:
        return {}, [], {}
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        return {}, [], {"path": str(path), "parse_error": str(exc)}
    dataset: Dict[str, Any] = {}
    band_tags: List[Dict[str, Any]] = []
    compact = ET.tostring(root, encoding="unicode")
    for elem in root.iter():
        if _xml_local(elem.tag) != "PAMRasterBand":
            continue
        tags: Dict[str, Any] = {}
        for mdi in elem.iter():
            if _xml_local(mdi.tag) == "MDI":
                key = mdi.attrib.get("key") or mdi.attrib.get("name")
                if key:
                    tags[key] = (mdi.text or "").strip()
        if tags:
            band_tags.append(tags)
    for elem in root.iter():
        parent_is_band = False
        # ElementTree 没有 parent；仅取不属于 PAMRasterBand 的 MDI。
        if _xml_local(elem.tag) == "MDI":
            key = elem.attrib.get("key") or elem.attrib.get("name")
            if key:
                dataset[key] = (elem.text or "").strip()
    for band in band_tags:
        for key in band:
            dataset.pop(key, None)
    return dataset, band_tags, {"path": str(path), "xml": compact}


def _apply_aux(
    collector: _Collector,
    dataset: Dict[str, Any],
    bands: List[Dict[str, Any]],
) -> None:
    collector.add_tags(dataset, "aux_xml")
    for band in bands:
        collector.add_tags(band, "aux_xml")
    wavelengths: List[Optional[float]] = []
    bandwidths: List[Optional[float]] = []
    for band in bands:
        _, wl = _tag_lookup(
            band,
            ("wavelength", "center wavelength", "centralwavelength", "wavelength_nm"),
        )
        values = parse_number_list(wl)
        wavelengths.append(float(values[0]) if values else None)
        _, bw = _tag_lookup(band, ("fwhm", "bandwidth", "wavelength fwhm"))
        values = parse_number_list(bw)
        bandwidths.append(float(values[0]) if values else None)
    if wavelengths and all(value is not None for value in wavelengths):
        collector.add("wavelengths", wavelengths, "aux_xml", "PAMRasterBand/MDI[key=wavelength]", bands)
    if bandwidths and any(value is not None for value in bandwidths):
        collector.add("bandwidths", bandwidths, "aux_xml", "PAMRasterBand/MDI[key=fwhm]", bands)


def _read_xmp(path: Optional[Path]) -> Tuple[List[Tuple[str, str, str]], Dict[str, Any]]:
    if path is None:
        return [], {}
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        return [], {"path": str(path), "parse_error": str(exc)}
    values: List[Tuple[str, str, str]] = []
    raw: Dict[str, str] = {}
    for elem in root.iter():
        for attr_name, attr_value in elem.attrib.items():
            name = _xml_local(attr_name)
            text = str(attr_value or "").strip()
            if text and normalize_key(name) not in ("about", "parseType", "lang"):
                values.append((name, text, str(elem.attrib)))
                raw[name] = text
        if len(elem):
            continue
        text = (elem.text or "").strip()
        if not text:
            continue
        name = _xml_local(elem.tag)
        values.append((name, text, str(elem.attrib)))
        raw[name] = text
    return values, {"path": str(path), "values": raw}


def _apply_xmp(
    collector: _Collector, values: List[Tuple[str, str, str]]
) -> None:
    def first(*names: str) -> Tuple[Optional[str], Optional[str]]:
        wanted = {normalize_key(name) for name in names}
        for name, text, _ in values:
            if normalize_key(name) in wanted:
                return name, text
        return None, None

    key, text = first("Model", "CameraModel", "Instrument")
    if key and text:
        collector.add("sensor_name", text, "xmp_sidecar", key, text)
    else:
        key, text = first("Creator", "RigName")
        if key and text:
            collector.add("sensor_name", text, "xmp_sidecar", key, text)
    key, text = first("DateTimeOriginal", "DateTimeDigitized", "CreateDate", "CaptureTime")
    if key and text:
        collector.add("acquisition_datetime", text, "xmp_sidecar", key, text)
        flight_date, _ = _normalize_date(text)
        collector.add("flight_date", flight_date, "xmp_sidecar", key, text)
    key, text = first("FieldId", "FieldID", "FieldName", "PlotId")
    if key and text:
        collector.add("field_id", text, "xmp_sidecar", key, text)
    key, text = first("SensorType", "ImageType")
    if key and text:
        collector.add("sensor_type", text, "xmp_sidecar", key, text)
    key, text = first("CentralWavelength", "CenterWavelength", "Wavelength")
    if key and text:
        nums = parse_number_list(text)
        if nums:
            collector.add("wavelengths", [nums[0]], "xmp_sidecar", key, text)
    key, text = first("WavelengthFWHM", "FWHM", "Bandwidth")
    if key and text:
        nums = parse_number_list(text)
        if nums:
            collector.add("bandwidths", [nums[0]], "xmp_sidecar", key, text)
    key, text = first("BandName")
    if key and text:
        collector.add("band_names", [text], "xmp_sidecar", key, text)
        wavelength = wavelength_from_label(text)
        if wavelength is not None and not collector.candidates.get("wavelengths"):
            collector.add("wavelengths", [wavelength], "xmp_sidecar", key, text)


def _canonical_utc(value: Any) -> Optional[str]:
    if _empty(value):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(
        microsecond=0
    ).isoformat().replace("+00:00", "Z")


def _all_xml_text(root: ET.Element, name: str) -> List[str]:
    wanted = normalize_key(name)
    return [
        (elem.text or "").strip()
        for elem in root.iter()
        if normalize_key(_xml_local(elem.tag)) == wanted and (elem.text or "").strip()
    ]


def _xml_first(root: ET.Element, name: str) -> Optional[str]:
    values = _all_xml_text(root, name)
    return values[0] if values else None


def _read_planet_xml(path: Optional[Path]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if path is None:
        return {}, {}
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        return {}, {"path": str(path), "parse_error": str(exc)}
    values = {
        "platform_short_names": _all_xml_text(root, "shortName"),
        "acquisition_datetime": _xml_first(root, "acquisitionDateTime")
        or _xml_first(root, "acquisitionDate"),
        "product_type": _xml_first(root, "productType"),
        "pixel_format": _xml_first(root, "pixelFormat"),
        "epsg": _xml_first(root, "epsgCode"),
        "row_gsd": _xml_first(root, "rowGsd"),
        "column_gsd": _xml_first(root, "columnGsd"),
        "num_rows": _xml_first(root, "numRows"),
        "num_columns": _xml_first(root, "numColumns"),
        "num_bands": _xml_first(root, "numBands"),
        "cloud_cover_percentage": _xml_first(root, "cloudCoverPercentage"),
        "sun_azimuth": _xml_first(root, "illuminationAzimuthAngle"),
        "sun_elevation": _xml_first(root, "illuminationElevationAngle"),
        "view_azimuth": _xml_first(root, "azimuthAngle"),
        "off_nadir": _xml_first(root, "spaceCraftViewAngle"),
        "atmospheric_correction_applied": _xml_first(
            root, "atmosphericCorrectionApplied"
        ),
    }
    values["instrument"] = (
        values["platform_short_names"][1]
        if len(values["platform_short_names"]) > 1
        else None
    )
    values["sensor_name"] = values["platform_short_names"][0] if values["platform_short_names"] else None
    return values, {
        "path": str(path),
        "values": values,
        "xml": ET.tostring(root, encoding="unicode"),
    }


def _apply_planet_xml(
    collector: _Collector, values: Dict[str, Any]
) -> None:
    if not values:
        return
    sensor = values.get("sensor_name")
    if sensor:
        collector.add("sensor_name", sensor, "xml_sidecar", "eop:platform/eop:shortName", sensor)
        if "planet" in str(sensor).casefold():
            collector.add("sensor_type", "卫星光学传感器", "xml_sidecar", "eop:platform", sensor)
            collector.add("image_type", "卫星多光谱", "xml_sidecar", "eop:instrument", values.get("instrument"))
    acquired = _canonical_utc(values.get("acquisition_datetime"))
    if acquired:
        collector.add(
            "acquisition_datetime",
            acquired,
            "xml_sidecar",
            "ps:acquisitionDateTime",
            values.get("acquisition_datetime"),
        )
        flight_date, _ = _normalize_date(acquired)
        collector.add("flight_date", flight_date, "xml_sidecar", "ps:acquisitionDateTime", acquired)
    product_type = values.get("product_type")
    if product_type:
        collector.add("processing_level", product_type, "xml_sidecar", "eop:productType", product_type)
        collector.add("value_type", "surface_reflectance", "xml_sidecar", "eop:productType", product_type)


def _planet_asset_metadata(
    data: Dict[str, Any], dataset_path: Path
) -> Dict[str, Any]:
    assets = data.get("assets") or {}
    selected = None
    for asset in assets.values():
        if not isinstance(asset, dict):
            continue
        href = str(asset.get("href") or "")
        if Path(href).name.casefold() == dataset_path.name.casefold():
            selected = asset
            break
    if selected is None:
        for asset in assets.values():
            if not isinstance(asset, dict):
                continue
            roles = [str(v).casefold() for v in (asset.get("roles") or [])]
            bands = asset.get("eo:bands") or []
            if bands and ("data" in roles or "reflectance" in roles):
                selected = asset
                break
    if selected is None:
        return {}
    eo_bands = selected.get("eo:bands") or []
    raster_bands = selected.get("raster:bands") or []
    wavelengths: List[float] = []
    bandwidths: List[float] = []
    band_names: List[str] = []
    scales: List[float] = []
    for band in eo_bands:
        if not isinstance(band, dict):
            continue
        center = band.get("center_wavelength", band.get("center_wavelength_nm"))
        fwhm = band.get("full_width_half_max", band.get("fwhm"))
        try:
            center_value = float(center)
            wavelengths.append(center_value * 1000.0 if center_value <= 10.0 else center_value)
        except (TypeError, ValueError):
            pass
        try:
            width_value = float(fwhm)
            bandwidths.append(width_value * 1000.0 if width_value <= 10.0 else width_value)
        except (TypeError, ValueError):
            pass
        band_names.append(str(band.get("name") or band.get("common_name") or ""))
    for band in raster_bands:
        if isinstance(band, dict) and band.get("scale") is not None:
            try:
                scales.append(float(band["scale"]))
            except (TypeError, ValueError):
                pass
    return {
        "asset.href": selected.get("href"),
        "asset.type": selected.get("pl:asset_type"),
        "roles": selected.get("roles") or [],
        "wavelengths": wavelengths,
        "bandwidths": bandwidths,
        "band_names": band_names,
        "reflectance_scale": scales[0] if scales else None,
        "reflectance_scales": scales,
        "epsg": selected.get("proj:epsg"),
        "bbox": selected.get("proj:bbox"),
        "shape": selected.get("proj:shape"),
        "transform": selected.get("proj:transform"),
        "data_type": (raster_bands[0] or {}).get("data_type") if raster_bands else None,
        "nodata": (raster_bands[0] or {}).get("nodata") if raster_bands else None,
    }


def _apply_planet_json(
    collector: _Collector,
    data: Dict[str, Any],
    dataset_path: Path,
) -> Dict[str, Any]:
    properties = data.get("properties") or {}
    constellation = str(properties.get("constellation") or "").casefold()
    provider = str(properties.get("provider") or "").casefold()
    item_type = str(properties.get("pl:item_type") or properties.get("item_type") or "")
    is_planet = (
        "planetscope" in constellation
        or "planetscope" in provider
        or item_type.upper() == "PSSCENE"
        or "analyticms_sr_8b" in dataset_path.name.casefold()
    )
    if not is_planet:
        return {}
    instrument = properties.get("instruments") or properties.get("instrument")
    if isinstance(instrument, list):
        instrument = instrument[0] if instrument else None
    collector.add(
        "sensor_name",
        "PlanetScope",
        "json_sidecar",
        "properties.constellation",
        properties.get("constellation"),
    )
    collector.add("sensor_type", "卫星光学传感器", "json_sidecar", "properties.constellation", constellation)
    collector.add("image_type", "卫星多光谱", "json_sidecar", "properties.instruments", instrument)
    acquired_raw = properties.get("datetime") or properties.get("acquired")
    acquired = _canonical_utc(acquired_raw)
    if acquired:
        collector.add("acquisition_datetime", acquired, "json_sidecar", "properties.datetime", acquired_raw)
        flight_date, _ = _normalize_date(acquired)
        collector.add("flight_date", flight_date, "json_sidecar", "properties.datetime", acquired_raw)
    asset = _planet_asset_metadata(data, dataset_path)
    if asset.get("wavelengths"):
        collector.add("wavelengths", asset["wavelengths"], "json_sidecar", "assets.eo:bands", asset)
    if asset.get("bandwidths") and len(asset["bandwidths"]) == len(asset.get("wavelengths") or []):
        collector.add("bandwidths", asset["bandwidths"], "json_sidecar", "assets.eo:bands", asset)
    if asset.get("band_names"):
        collector.add("band_names", asset["band_names"], "json_sidecar", "assets.eo:bands", asset)
    if asset.get("data_type"):
        collector.add("value_type", asset["data_type"], "json_sidecar", "assets.raster:bands.data_type", asset["data_type"])
    if "reflectance" in [str(v).casefold() for v in asset.get("roles") or []]:
        collector.add("value_type", "surface_reflectance", "json_sidecar", "assets.roles", asset.get("roles"))
    return {
        "is_planet": True,
        "scene_id": data.get("id"),
        "instrument": instrument,
        "platform": properties.get("platform"),
        "constellation": properties.get("constellation"),
        "cloud_cover_json": properties.get("eo:cloud_cover", properties.get("cloud_cover")),
        "bbox": data.get("bbox"),
        "geometry": data.get("geometry"),
        "asset": asset,
        "properties": properties,
    }


def _planet_conflicts(observations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cloud_values = []
    seen = set()
    for item in observations:
        value = item.get("value")
        if value is None:
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        key = (item.get("source"), item.get("path"), value)
        if key in seen:
            continue
        seen.add(key)
        cloud_values.append({**item, "value": value})
    if len({item["value"] for item in cloud_values}) <= 1:
        return []
    text = "；".join(
        f"{item['source']} {item['path']}={item['value']:g}%" for item in cloud_values
    )
    return [
        {
            "field": "cloud_cover",
            "values": cloud_values,
            "message": f"同一 Scene 的云量来源值不一致：{text}。请人工确认最终采用值。",
        }
    ]


def _planet_scene_prefix(stem: str) -> Optional[str]:
    match = re.match(r"^(20\d{2}\d{4}_\d{6}_\d+_[0-9a-z]+)", stem, re.I)
    return match.group(1) if match else None


def _find_sidecars(dataset_path: Path) -> Dict[str, Any]:
    name = dataset_path.name
    stem = dataset_path.stem
    aux_names = (name + ".aux.xml", name + ".AUX.XML", stem + ".aux.xml")
    json_names = (
        name + ".json",
        name + ".JSON",
        stem + ".json",
        stem + ".metadata.json",
        stem + "_metadata.json",
    )
    xmp_names = (
        name + ".xmp",
        name + ".XMP",
        stem + ".xmp",
        stem + "_metadata.xmp",
    )
    aux = _find_case_insensitive(dataset_path.parent, aux_names)
    json_path = _find_case_insensitive(dataset_path.parent, json_names)
    xmp = _find_case_insensitive(dataset_path.parent, xmp_names)
    json_files: List[Path] = []
    xml_files: List[Path] = []
    scene_prefix = _planet_scene_prefix(stem)
    if scene_prefix:
        try:
            entries = sorted(dataset_path.parent.iterdir())
        except OSError:
            entries = []
        main_json = dataset_path.parent / f"{scene_prefix}.json"
        if main_json.exists():
            json_files.append(main_json)
        if json_path and json_path not in json_files:
            json_files.append(json_path)
        for entry in entries:
            if not entry.is_file() or entry in json_files:
                continue
            lower = entry.name.casefold()
            if (
                lower.endswith(".json")
                and entry.stem.casefold().startswith(scene_prefix.casefold())
            ):
                json_files.append(entry)
        for entry in entries:
            if not entry.is_file():
                continue
            lower = entry.name.casefold()
            if (
                lower.endswith(".xml")
                and not lower.endswith(".aux.xml")
                and entry.stem.casefold().startswith(scene_prefix.casefold())
                and "metadata" in lower
            ):
                xml_files.append(entry)
    elif json_path:
        json_files.append(json_path)
    return {
        "aux_xml": aux,
        "json_sidecar": json_files[0] if json_files else None,
        "json_sidecars": json_files,
        "xml_sidecar": xml_files[0] if xml_files else None,
        "xml_sidecars": xml_files,
        "xmp_sidecar": xmp,
        "planet_scene_prefix": scene_prefix,
    }


def infer_from_filename(path: str | Path) -> Dict[str, Any]:
    """从文件名推断田块、日期和影像类型，不推断具体品牌。"""
    name = Path(path).name
    stem = re.sub(r"(?i)\.(?:tif|tiff|dat|img|bil|bsq)$", "", name)
    date_match = re.search(r"(?<!\d)(20\d{2})([-_]?)(\d{2})\2(\d{2})(?!\d)", stem)
    result: Dict[str, Any] = {}
    if date_match:
        iso = f"{date_match.group(1)}-{date_match.group(3)}-{date_match.group(4)}"
        result["flight_date"] = iso
        prefix = stem[: date_match.start()].rstrip("_- ")
        if prefix:
            result["field_id"] = prefix
    type_match = re.search(r"(多光谱|高光谱|multispectral|hyperspectral)", stem, re.I)
    if type_match:
        label = type_match.group(1).casefold()
        result["image_type"] = "高光谱" if label in ("高光谱", "hyperspectral") else "多光谱"
    return result


def _apply_user(collector: _Collector, user_metadata: Optional[Dict[str, Any]]) -> None:
    if not isinstance(user_metadata, dict):
        return
    payload = user_metadata
    for container in ("metadata_fields", "metadata"):
        nested = payload.get(container)
        if isinstance(nested, dict):
            payload = {**payload, **nested}
            break
    for key, value in payload.items():
        field = LEGACY_FIELD_NAMES.get(normalize_key(key), normalize_key(key))
        if field not in METADATA_FIELDS:
            continue
        raw_value = value
        if isinstance(value, dict) and "value" in value:
            raw_value = value.get("value")
        collector.add(
            field,
            raw_value,
            "user",
            f"user:{key}",
            raw_value,
        )


def _finalize(
    collector: _Collector,
    raw_metadata: Dict[str, Any],
    raster_info: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    fields: Dict[str, Dict[str, Any]] = {}
    sources: Dict[str, str] = {}
    confidence: Dict[str, float] = {}
    result: Dict[str, Any] = {}
    for field in METADATA_FIELDS:
        candidate = collector.candidates.get(field)
        if candidate:
            fields[field] = {
                "value": candidate["value"],
                "source": candidate["source"],
                "confidence": candidate["confidence"],
                "raw_key": candidate.get("raw_key"),
                "raw_value": candidate.get("raw_value"),
            }
            result[field] = candidate["value"]
            sources[field] = candidate["source"]
            confidence[field] = candidate["confidence"]
        else:
            default: Any = [] if field in ("wavelengths", "bandwidths", "band_names") else ""
            fields[field] = {
                "value": default,
                "source": None,
                "confidence": 0.0,
                "raw_key": None,
                "raw_value": None,
            }
            result[field] = default

    wavelengths = result.get("wavelengths") or []
    bandwidths = result.get("bandwidths") or []
    if wavelengths:
        result["wavelength_unit"] = result.get("wavelength_unit") or "nm"
    if bandwidths and result.get("wavelength_unit") == "nm":
        bandwidths, _ = _normalize_wavelengths(bandwidths, "nm")
        result["bandwidths"] = bandwidths
        fields["bandwidths"]["value"] = bandwidths

    raster_info = raster_info or {}
    if not result.get("value_type") and raster_info.get("dtype"):
        result["value_type"] = raster_info["dtype"]
        fields["value_type"] = {
            "value": raster_info["dtype"],
            "source": "geotiff_tags",
            "confidence": SOURCE_CONFIDENCE["geotiff_tags"],
            "raw_key": "dtype",
            "raw_value": raster_info["dtype"],
        }
        sources["value_type"] = "geotiff_tags"
        confidence["value_type"] = SOURCE_CONFIDENCE["geotiff_tags"]

    result.update(
        {
            "metadata_sources": sources,
            "metadata_confidence": confidence,
            "metadata_fields": fields,
            "metadata_candidates": collector.all_candidates,
            "raw_metadata": raw_metadata,
            "raster": raster_info,
            "metadata_warnings": [],
        }
    )
    if wavelengths:
        if min(wavelengths) < 100 or max(wavelengths) > 20000:
            result["metadata_warnings"].append("解析到的中心波长超出 100–20000 nm，已保留原始值但需人工确认")
    return result


def _geotiff_info(
    path: Path, collector: _Collector
) -> Tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]], List[str]]:
    try:
        import rasterio

        with rasterio.open(path) as ds:
            tags = {str(k): str(v) for k, v in (ds.tags() or {}).items()}
            band_tags = [
                {str(k): str(v) for k, v in (ds.tags(i + 1) or {}).items()}
                for i in range(ds.count)
            ]
            descriptions = list(ds.descriptions or [])
            if len(descriptions) != ds.count:
                descriptions = [None] * ds.count
            raster_info = {
                "width": int(ds.width),
                "height": int(ds.height),
                "bands": int(ds.count),
                "dtype": str(ds.dtypes[0]) if ds.count else None,
                "driver": ds.driver,
                "crs": ds.crs.to_string() if ds.crs else None,
                "transform": [float(v) for v in list(ds.transform)[:6]],
                "descriptions": descriptions,
            }
    except Exception as exc:  # noqa: BLE001
        return {}, {}, [], [f"影像头读取失败：{exc}"]

    collector.add_tags(tags, "geotiff_tags")
    if raster_info.get("dtype"):
        collector.add("value_type", raster_info["dtype"], "geotiff_tags", "dtype", raster_info["dtype"])

    names: List[str] = []
    wave_values: List[Optional[float]] = []
    bw_values: List[Optional[float]] = []
    for index, band in enumerate(band_tags):
        collector.add_tags(band, "band_tags")
        _, band_name = _tag_lookup(band, ("band name", "band_name", "name"))
        label = str(band_name or descriptions[index] or "").strip()
        names.append(label or f"Band {index + 1}")
        _, raw_wl = _tag_lookup(
            band,
            ("wavelength", "center wavelength", "centralwavelength", "wavelength_nm"),
        )
        numbers = parse_number_list(raw_wl)
        wave_values.append(
            float(numbers[0]) if numbers else wavelength_from_label(label)
        )
        _, raw_bw = _tag_lookup(band, ("fwhm", "bandwidth", "wavelength fwhm"))
        numbers = parse_number_list(raw_bw)
        bw_values.append(float(numbers[0]) if numbers else None)

    if wave_values and all(value is not None for value in wave_values):
        collector.add("wavelengths", wave_values, "band_tags", "band descriptions/tags", descriptions)
    if bw_values and any(value is not None for value in bw_values):
        collector.add("bandwidths", bw_values, "band_tags", "per-band FWHM", bw_values)
    if names:
        collector.add("band_names", names, "band_tags", "descriptions", descriptions)
    return tags, band_tags, descriptions, [] if raster_info else ["无法读取影像尺寸"]


def parse_image_metadata(
    dataset_path: str | Path,
    user_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """解析单个影像及其伴随文件，返回统一元数据结果。

    ``user_metadata`` 支持统一字段名以及旧接口的 ``sensor``/``field`` 等别名。
    """
    path = Path(dataset_path)
    collector = _Collector()
    raw_metadata: Dict[str, Any] = {
        "dataset": str(path),
        "envi_header": {},
        "geotiff_tags": {},
        "band_tags": [],
        "aux_xml": {},
        "json_sidecar": {},
        "xml_sidecar": {},
        "xmp_sidecar": {},
        "filename_rule": {},
    }
    warnings: List[str] = []

    raster_info: Dict[str, Any] = {}
    band_count = 0
    if path.exists():
        tags, band_tags, _descriptions, raster_warnings = _geotiff_info(path, collector)
        raw_metadata["geotiff_tags"] = tags
        raw_metadata["band_tags"] = band_tags
        warnings.extend(raster_warnings)
        if path.exists():
            try:
                import rasterio

                with rasterio.open(path) as ds:
                    raster_info = {
                        "width": int(ds.width),
                        "height": int(ds.height),
                        "bands": int(ds.count),
                        "dtype": str(ds.dtypes[0]) if ds.count else None,
                        "driver": ds.driver,
                        "crs": ds.crs.to_string() if ds.crs else None,
                        "transform": [float(v) for v in list(ds.transform)[:6]],
                    }
                    band_count = int(ds.count)
            except Exception:
                pass

    header = read_envi_header(path)
    if header.get("path"):
        raw_metadata["envi_header"] = _apply_envi(collector, header, band_count)
        header_values = header.get("values") or {}
        band_count = band_count or int(header_values.get("bands", 0) or 0)
        if not raster_info.get("crs"):
            crs_text = (
                header_values.get("coordinate_system_string")
                or header_values.get("projection_info")
                or ""
            )
            if crs_text:
                raster_info["crs"] = str(crs_text).strip("{} ")

    sidecars = _find_sidecars(path) if path.exists() else {
        "aux_xml": None,
        "json_sidecar": None,
        "json_sidecars": [],
        "xml_sidecar": None,
        "xml_sidecars": [],
        "xmp_sidecar": None,
    }
    raw_metadata["sidecars"] = {
        key: (
            [str(item) for item in value]
            if isinstance(value, list)
            else (str(value) if value else None)
        )
        for key, value in sidecars.items()
    }

    dataset_aux, band_aux, raw_aux = _read_aux_xml(sidecars["aux_xml"])
    raw_metadata["aux_xml"] = raw_aux
    _apply_aux(collector, dataset_aux, band_aux)

    json_documents: List[Dict[str, Any]] = []
    json_raw_entries: List[Dict[str, Any]] = []
    for sidecar_path in sidecars.get("json_sidecars") or []:
        json_data, raw_json = _read_json_sidecar(sidecar_path)
        json_raw_entries.append({**raw_json, "document": json_data})
        if json_data:
            json_documents.append(json_data)
            _apply_json(collector, json_data)
    raw_metadata["json_sidecar"] = (
        json_raw_entries[0]
        if len(json_raw_entries) == 1
        else {"documents": json_raw_entries}
    )

    planet_docs: List[Dict[str, Any]] = []
    cloud_observations: List[Dict[str, Any]] = []
    for sidecar_path, json_data in zip(
        sidecars.get("json_sidecars") or [], json_documents
    ):
        planet = _apply_planet_json(collector, json_data, path)
        if planet:
            planet["source_path"] = str(sidecar_path)
            planet_docs.append(planet)
            if planet.get("cloud_cover_json") is not None:
                cloud_observations.append(
                    {
                        "source": "json_sidecar",
                        "path": f"{sidecar_path.name}:properties.cloud_cover",
                        "value": planet["cloud_cover_json"],
                    }
                )

    planet_xml_values: Dict[str, Any] = {}
    xml_raw_entries: List[Dict[str, Any]] = []
    for sidecar_path in sidecars.get("xml_sidecars") or []:
        xml_values, raw_xml = _read_planet_xml(sidecar_path)
        if raw_xml:
            xml_raw_entries.append(raw_xml)
        if xml_values:
            planet_xml_values = xml_values
            _apply_planet_xml(collector, xml_values)
            if xml_values.get("cloud_cover_percentage") is not None:
                cloud_observations.append(
                    {
                        "source": "xml_sidecar",
                        "path": f"{sidecar_path.name}:cloudCoverPercentage",
                        "value": xml_values["cloud_cover_percentage"],
                    }
                )
    raw_metadata["xml_sidecar"] = (
        xml_raw_entries[0]
        if len(xml_raw_entries) == 1
        else {"documents": xml_raw_entries}
    )

    xmp_values, raw_xmp = _read_xmp(sidecars["xmp_sidecar"])
    raw_metadata["xmp_sidecar"] = raw_xmp
    _apply_xmp(collector, xmp_values)

    filename_values = infer_from_filename(path)
    raw_metadata["filename_rule"] = filename_values
    for field, value in filename_values.items():
        collector.add(field, value, "filename_rule", "filename", path.name)

    acquisition = collector.candidates.get("acquisition_datetime")
    if acquisition and not collector.candidates.get("flight_date"):
        flight_date, _ = _normalize_date(acquisition.get("value"))
        collector.add(
            "flight_date",
            flight_date,
            acquisition["source"],
            acquisition.get("raw_key"),
            acquisition.get("raw_value"),
        )

    _apply_user(collector, user_metadata)

    # 对来自不同来源的波长/带宽统一换算为 nm。
    for field in ("wavelengths", "bandwidths"):
        candidate = collector.candidates.get(field)
        if not candidate:
            continue
        unit_candidate = collector.candidates.get("wavelength_unit")
        unit = unit_candidate["value"] if unit_candidate else None
        normalized, used_unit = _normalize_wavelengths(candidate["value"], unit)
        candidate["value"] = normalized
        if not unit_candidate:
            collector.add(
                "wavelength_unit",
                used_unit,
                candidate.get("source") or ("envi_header" if header.get("path") else "geotiff_tags"),
            )

    result = _finalize(collector, raw_metadata, raster_info)
    planet_asset = next(
        (
            item.get("asset")
            for item in planet_docs
            if item.get("asset") and item["asset"].get("wavelengths")
        ),
        {},
    )
    planet_info = {
        "scene_id": next((item.get("scene_id") for item in planet_docs if item.get("scene_id")), None),
        "sensor_name": result.get("sensor_name") or planet_xml_values.get("sensor_name"),
        "instrument": next((item.get("instrument") for item in planet_docs if item.get("instrument")), None)
        or planet_xml_values.get("instrument"),
        "platform": next((item.get("platform") for item in planet_docs if item.get("platform")), None),
        "constellation": next((item.get("constellation") for item in planet_docs if item.get("constellation")), None),
        "acquisition_datetime_utc": result.get("acquisition_datetime"),
        "cloud_observations": cloud_observations,
        "xml": planet_xml_values,
        "asset": planet_asset,
        "bbox": next((item.get("bbox") for item in planet_docs if item.get("bbox")), None),
        "geometry": next((item.get("geometry") for item in planet_docs if item.get("geometry")), None),
    }
    result["planet_scope"] = planet_info if planet_docs or planet_xml_values else None
    result["scene_id"] = planet_info["scene_id"]
    result["instrument"] = planet_info["instrument"]
    result["reflectance_scale"] = planet_asset.get("reflectance_scale")
    result["epsg"] = planet_asset.get("epsg")
    result["projected_bbox"] = planet_asset.get("bbox")
    result["projected_shape"] = planet_asset.get("shape")
    result["projected_transform"] = planet_asset.get("transform")
    result["cloud_observations"] = cloud_observations
    result["metadata_conflicts"] = _planet_conflicts(cloud_observations)
    for conflict in result["metadata_conflicts"]:
        result["metadata_warnings"].append(conflict["message"])
    result["metadata_warnings"].extend(warnings)
    if not result.get("metadata_sources"):
        result["metadata_warnings"].append("未从影像头或伴随文件识别到结构化字段")
    return result


def image_fields_from_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """生成兼容当前项目影像 JSON 的扁平字段。"""
    return {
        "sensor": metadata.get("sensor_name") or "",
        "sensor_name": metadata.get("sensor_name") or "",
        "instrument": metadata.get("instrument") or "",
        "sensor_type": metadata.get("sensor_type") or "",
        "acquisition_datetime": metadata.get("acquisition_datetime") or "",
        "flight_date": metadata.get("flight_date") or "",
        "field": metadata.get("field_id") or "",
        "field_id": metadata.get("field_id") or "",
        "image_type": metadata.get("image_type") or "",
        "value_type": metadata.get("value_type") or "",
        "processing_level": metadata.get("processing_level") or "",
        "scene_id": metadata.get("scene_id") or "",
        "reflectance_scale": metadata.get("reflectance_scale"),
        "epsg": metadata.get("epsg"),
        "cloud_observations": metadata.get("cloud_observations") or [],
        "metadata_conflicts": metadata.get("metadata_conflicts") or [],
        "metadata_warnings": metadata.get("metadata_warnings") or [],
        "wavelengths": list(metadata.get("wavelengths") or []),
        "bandwidths": list(metadata.get("bandwidths") or []),
        "wavelength_unit": metadata.get("wavelength_unit") or "nm",
        "band_names": list(metadata.get("band_names") or []),
        "planet_scope": metadata.get("planet_scope"),
        "projected_bbox": metadata.get("projected_bbox"),
        "projected_shape": metadata.get("projected_shape"),
        "projected_transform": metadata.get("projected_transform"),
        "metadata": metadata,
        "metadata_sources": metadata.get("metadata_sources") or {},
        "metadata_confidence": metadata.get("metadata_confidence") or {},
        "raw_metadata": metadata.get("raw_metadata") or {},
    }


def _existing_user_values(existing_image: Dict[str, Any]) -> Dict[str, Any]:
    sources = existing_image.get("metadata_sources") or {}
    fields = existing_image.get("metadata_fields") or (
        (existing_image.get("metadata") or {}).get("metadata_fields") or {}
    )
    values: Dict[str, Any] = {}
    for field in METADATA_FIELDS:
        legacy = {
            "sensor_name": "sensor",
            "field_id": "field",
        }.get(field, field)
        value = (
            fields.get(field, {}).get("value")
            if isinstance(fields.get(field), dict)
            else existing_image.get(field, existing_image.get(legacy))
        )
        source = sources.get(field)
        if source is None and isinstance(fields.get(field), dict):
            source = fields[field].get("source")
        # 旧项目没有来源信息；保守地保留已有非空元数据，避免静默覆盖人工值。
        if source is None and not _empty(value):
            source = "user"
        if source == "user" and not _empty(value):
            values[field] = copy.deepcopy(value)
    return values


def reparse_image_metadata(
    existing_image: Dict[str, Any],
    new_parse: Dict[str, Any],
) -> Dict[str, Any]:
    """比较重解析结果并合并，用户字段保持不变。"""
    user_values = _existing_user_values(existing_image)
    existing_fields = existing_image.get("metadata_fields") or (
        (existing_image.get("metadata") or {}).get("metadata_fields") or {}
    )

    def old_value(field: str) -> Any:
        if field in existing_fields and isinstance(existing_fields[field], dict):
            return existing_fields[field].get("value")
        legacy = {"sensor_name": "sensor", "field_id": "field"}.get(field, field)
        return existing_image.get(field, existing_image.get(legacy))

    merged = copy.deepcopy(new_parse)
    merged.setdefault("metadata_fields", {})
    merged.setdefault("metadata_sources", {})
    merged.setdefault("metadata_confidence", {})
    for field, value in user_values.items():
        merged[field] = value
        merged["metadata_fields"][field] = {
            "value": value,
            "source": "user",
            "confidence": 1.0,
            "raw_key": "existing_user_value",
            "raw_value": value,
        }
        merged["metadata_sources"][field] = "user"
        merged["metadata_confidence"][field] = 1.0
    merged["metadata_warnings"] = list(merged.get("metadata_warnings") or [])
    if user_values:
        merged["metadata_warnings"].append(
            "重新解析时保留了用户已修改字段：" + "、".join(sorted(user_values))
        )

    changes: List[Dict[str, Any]] = []
    conflicts: List[Dict[str, Any]] = []
    for field in METADATA_FIELDS:
        previous = old_value(field)
        incoming = new_parse.get(field)
        final = merged.get(field)
        if previous != incoming:
            item = {
                "field": field,
                "old": previous,
                "incoming": incoming,
                "final": final,
                "preserved_user_value": field in user_values,
            }
            changes.append(item)
            if field in user_values:
                conflicts.append(item)

    return {
        "current": {field: old_value(field) for field in METADATA_FIELDS},
        "incoming": {field: new_parse.get(field) for field in METADATA_FIELDS},
        "merged": merged,
        "changes": changes,
        "conflicts": conflicts,
        "preserved_user_fields": sorted(user_values),
        "auto_updated_fields": [
            item["field"]
            for item in changes
            if not item["preserved_user_value"]
        ],
    }

