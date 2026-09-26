"""Reading FITS/XISF headers and turning them into canonical fields
(SPEC §4.2 keywords, §6.2 ingest steps 2-6)."""
from __future__ import annotations

import re
import struct
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from altair.config import AltairConfig, Rig

FITS_SUFFIXES = {".fits", ".fit", ".fts"}
XISF_SUFFIXES = {".xisf"}
IMAGE_TYPES = {
    "LIGHT": "light", "LIGHT FRAME": "light", "DARK": "dark", "DARK FRAME": "dark", "FLAT": "flat", "FLAT FRAME": "flat",
    "BIAS": "bias", "BIAS FRAME": "bias", "OFFSET": "bias", "DARKFLAT": "darkflat", "DARK FLAT": "darkflat", "FLATDARK": "darkflat",
}


def read_header(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in FITS_SUFFIXES:
        from astropy.io import fits

        header = fits.getheader(path)
        return {k: header[k] for k in header if k and k not in ("COMMENT", "HISTORY")}
    if suffix in XISF_SUFFIXES:
        return _xisf_header(path)
    raise ValueError(f"unsupported file type {suffix}")


def _xisf_header(path: Path) -> dict[str, Any]:
    """FITSKeyword elements (and geometry) from an XISF file's XML header."""
    with path.open("rb") as handle:
        if handle.read(8) != b"XISF0100":
            raise ValueError(f"{path} is not an XISF file")
        (length,) = struct.unpack("<I", handle.read(4))
        handle.read(4)
        xml = handle.read(length)
    root = ET.fromstring(xml)
    out: dict[str, Any] = {}
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag == "FITSKeyword":
            out[element.get("name", "")] = _coerce(element.get("value", ""))
        elif tag == "Image" and "geometry" in element.attrib and "NAXIS1" not in out:
            dims = element.get("geometry", "").split(":")
            if len(dims) >= 2:
                out["NAXIS1"], out["NAXIS2"] = int(dims[0]), int(dims[1])
    return out


def _coerce(value: str) -> Any:
    value = value.strip()
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1].strip()
    for cast in (int, float):
        try:
            return cast(value)
        except ValueError:
            pass
    return {"T": True, "F": False}.get(value, value)


def _first(header: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        if key in header and header[key] not in (None, ""):
            return header[key]
    return None


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sexagesimal(value: Any, hours: bool) -> float | None:
    number = _num(value)
    if number is not None:
        return number
    if not isinstance(value, str):
        return None
    parts = re.split(r"[:\s]+", value.strip())
    try:
        sign = -1 if parts[0].startswith("-") else 1
        d, m, s = (abs(float(parts[0])), float(parts[1]), float(parts[2]) if len(parts) > 2 else 0.0)
    except (ValueError, IndexError):
        return None
    degrees = sign * (d + m / 60 + s / 3600)
    return degrees * 15 if hours else degrees


def canonical(value: str | None, table: dict[str, str]) -> str | None:
    """Local alias tables: regex -> canonical name (SPEC §5 `aliases`)."""
    if value is None:
        return None
    for pattern, name in table.items():
        if re.search(pattern, str(value), re.IGNORECASE):
            return name
    return str(value).strip()


def parse_date_obs(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def night_of(date_obs: datetime, tz: str, rollover: str = "12:00") -> date:
    """The observing night: local time minus the rollover (noon -> NINA's $$DATEMINUS12$$)."""
    hours, minutes = (int(x) for x in rollover.split(":"))
    local = date_obs.astimezone(ZoneInfo(tz))
    return (local - timedelta(hours=hours, minutes=minutes)).date()


def normalize(header: dict[str, Any], config: AltairConfig, rig: Rig | None = None, file_name: str = "") -> dict[str, Any]:
    """Canonical fields from a raw header (SPEC §6.2 steps 2, 4, 5, 6)."""
    hm = config.header_mapping
    aliases = config.aliases
    raw_type = str(_first(header, hm["image_type"]) or "").strip().upper()
    date_obs = parse_date_obs(_first(header, hm["date_obs"]))
    xbin, ybin = _first(header, hm["xbinning"]), _first(header, hm["ybinning"])
    fields: dict[str, Any] = {
        "image_type": IMAGE_TYPES.get(raw_type),
        "telescope": canonical(_first(header, hm["telescope"]), aliases.get("telescope", {})),
        "camera": canonical(_first(header, hm["camera"]), aliases.get("camera", {})),
        "raw_filter": _first(header, hm["filter"]),
        "target": _first(header, hm["target"]),
        "exposure": _num(_first(header, hm["exposure"])),
        "gain": _num(_first(header, hm["gain"])),
        "offset": _num(_first(header, hm["offset"])),
        "sensor_temp": _num(_first(header, hm["sensor_temp"])),
        "binning": f"{int(xbin)}x{int(ybin or xbin)}" if _num(xbin) else None,
        "readout_mode": _first(header, hm["readout_mode"]),
        "bayer_pattern": _first(header, hm["bayer_pattern"]),
        "focal_length": _num(_first(header, hm["focal_length"])),
        "date_obs": date_obs,
        "ra_deg": _sexagesimal(header.get("RA"), hours=False) if _num(header.get("RA")) is not None else _sexagesimal(header.get("OBJCTRA"), hours=True),
        "dec_deg": _sexagesimal(_first(header, hm["dec"]), hours=False),
        "width": _num(_first(header, hm["width"])),
        "height": _num(_first(header, hm["height"])),
        "rotation_deg": _num(_first(header, hm["rotation"])),
    }
    fields["filter"] = canonical(fields["raw_filter"], aliases.get("filter", {})) if fields["raw_filter"] else (rig.default_filter if rig else None)
    fields["night"] = night_of(date_obs, config.site.timezone, config.site.session_rollover_local) if date_obs else None
    fields["rotator_pos"], fields["rotator_units"] = _rotator(header, rig, file_name)
    return fields


def _rotator(header: dict[str, Any], rig: Rig | None, file_name: str) -> tuple[float | None, str | None]:
    if not rig or not rig.rotator.present:
        return None, None
    if rig.rotator.source == "filename" and rig.rotator.filename_regex:
        match = re.search(rig.rotator.filename_regex, file_name)
        return (_num(match.group("pos")) if match else None), rig.rotator.units
    return _num(_first(header, rig.rotator.keywords)), rig.rotator.units
