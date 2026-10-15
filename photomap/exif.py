"""Read location, capture time and camera details from a photo's EXIF data.

Nothing here writes to photos; everything is read-only.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from PIL import Image

try:  # HEIC support (some Android phones save HEIC in "high efficiency" mode)
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:  # pragma: no cover - pillow-heif is a declared dependency
    pass

PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".heic", ".heif", ".png", ".webp", ".tif", ".tiff"}

# EXIF tag numbers
GPS_IFD, EXIF_IFD = 0x8825, 0x8769
MAKE, MODEL, ORIENTATION = 271, 272, 274
DATETIME_ORIGINAL, SUBSEC_ORIGINAL, OFFSET_TIME_ORIGINAL = 36867, 37521, 36881
GPS_LAT_REF, GPS_LAT, GPS_LON_REF, GPS_LON = 1, 2, 3, 4
GPS_ALT_REF, GPS_ALT, GPS_TIME, GPS_DATE = 5, 6, 7, 29

# Orientation values 5-8 mean the stored image is rotated 90 degrees.
_ROTATED = {5, 6, 7, 8}


def _text(value) -> str | None:
    """EXIF strings sometimes arrive as bytes or padded with nulls."""
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("ascii", "ignore")
    value = str(value).strip("\x00 ").strip()
    return value or None


def dms_to_decimal(dms, ref) -> float:
    """(degrees, minutes, seconds) plus N/S/E/W -> signed decimal degrees."""
    d, m, s = (float(x) for x in dms)
    value = d + m / 60 + s / 3600
    return -value if _text(ref) in ("S", "W") else value


def read_gps(gps: dict) -> tuple[float | None, float | None, float | None]:
    """Return (lat, lon, altitude_m), or Nones when the GPS block is missing or invalid."""
    try:
        lat = dms_to_decimal(gps[GPS_LAT], gps.get(GPS_LAT_REF))
        lon = dms_to_decimal(gps[GPS_LON], gps.get(GPS_LON_REF))
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None, None, None
    if lat != lat or lon != lon:  # NaN from a zero denominator
        return None, None, None
    if (lat == 0 and lon == 0) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None, None, None
    alt = None
    if GPS_ALT in gps:
        try:
            alt = float(gps[GPS_ALT])
            if gps.get(GPS_ALT_REF) in (1, b"\x01"):  # below sea level
                alt = -alt
            if alt != alt or alt == 0:  # NaN, or a phone writing 0 for "unknown"
                alt = None
        except (TypeError, ValueError, ZeroDivisionError):
            alt = None
    return lat, lon, alt


def _parse_offset(text) -> timezone | None:
    """'-06:00' -> timezone(timedelta(hours=-6))."""
    text = _text(text)
    if not text or len(text) < 6 or text[0] not in "+-":
        return None
    sign = -1 if text[0] == "-" else 1
    return timezone(sign * timedelta(hours=int(text[1:3]), minutes=int(text[4:6])))


def read_time(exif_sub: dict, gps: dict, fallback_tz: str):
    """Return (taken_local, taken_utc, time_source).

    taken_local is the naive wall-clock time the phone showed.
    Trust order for the timezone: OffsetTimeOriginal, then the GPS UTC
    timestamp, then fallback_tz. time_source records which one was used.
    """
    raw = _text(exif_sub.get(DATETIME_ORIGINAL))
    try:
        local = datetime.strptime(raw, "%Y:%m:%d %H:%M:%S")
    except (TypeError, ValueError):
        return None, None, "none"

    subsec = _text(exif_sub.get(SUBSEC_ORIGINAL))
    if subsec and subsec.isdigit():
        local = local.replace(microsecond=int(subsec[:6].ljust(6, "0")))

    try:
        offset = _parse_offset(exif_sub.get(OFFSET_TIME_ORIGINAL))
    except ValueError:
        offset = None
    if offset:
        return local, local.replace(tzinfo=offset).astimezone(timezone.utc), "offset"

    try:
        h, m, s = (float(x) for x in gps[GPS_TIME])
        day = datetime.strptime(_text(gps.get(GPS_DATE)), "%Y:%m:%d").replace(tzinfo=timezone.utc)
        return local, day + timedelta(hours=h, minutes=m, seconds=s), "gps"
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        pass

    utc = local.replace(tzinfo=ZoneInfo(fallback_tz)).astimezone(timezone.utc)
    return local, utc, "fallback"


def read_metadata(img: Image.Image, fallback_tz: str) -> dict:
    """Pull the fields the index stores from an already-open image."""
    exif = img.getexif()
    gps = exif.get_ifd(GPS_IFD)
    sub = exif.get_ifd(EXIF_IFD)

    width, height = img.size
    orientation = exif.get(ORIENTATION) or 1
    if orientation in _ROTATED:  # store the size as the photo is displayed
        width, height = height, width

    lat, lon, alt = read_gps(gps)
    taken_local, taken_utc, time_source = read_time(sub, gps, fallback_tz)
    return {
        "width": width,
        "height": height,
        "orientation": orientation,
        "make": _text(exif.get(MAKE)),
        "model": _text(exif.get(MODEL)),
        "lat": lat,
        "lon": lon,
        "altitude_m": alt,
        "taken_local": taken_local.isoformat() if taken_local else None,
        "taken_utc": taken_utc.isoformat() if taken_utc else None,
        "time_source": time_source,
    }
