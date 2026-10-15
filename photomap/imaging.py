"""Open a photo once and produce everything the index needs from its pixels:
metadata, a small thumbnail, and a fingerprint used to confirm duplicates."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps

from .exif import read_metadata


def dhash(img: Image.Image, size: int = 8) -> str:
    """Difference hash: 64 bits describing the image's broad light/dark layout.

    Two copies of the same photo give the same or nearly the same hash even if
    one was re-encoded, resized or had its metadata stripped. Returned as 16 hex chars.
    """
    small = img.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS)
    px = small.load()
    bits = 0
    for y in range(size):
        for x in range(size):
            bits = (bits << 1) | (px[x, y] > px[x + 1, y])
    return f"{bits:016x}"


def hamming(a: str | None, b: str | None) -> int:
    """Number of differing bits between two dhash strings (64 if either is missing)."""
    if not a or not b:
        return 64
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def process_photo(path: Path, thumb_path: Path, thumb_size: int, fallback_tz: str) -> dict:
    """Read one photo. Never raises: problems are reported in the returned dict."""
    result = {"error": None, "thumb_error": None, "dhash": None, "thumb_ok": False}
    try:
        with Image.open(path) as img:
            result.update(read_metadata(img, fallback_tz))
            # Decode JPEGs at reduced size: much faster than decoding all 12+ megapixels.
            img.draft("RGB", (thumb_size * 2, thumb_size * 2))
            try:
                upright = ImageOps.exif_transpose(img)  # fix sideways portrait photos
                result["dhash"] = dhash(upright)
                upright.thumbnail((thumb_size, thumb_size), Image.Resampling.LANCZOS)
                if upright.mode not in ("RGB", "L"):
                    upright = upright.convert("RGB")
                thumb_path.parent.mkdir(parents=True, exist_ok=True)
                upright.save(thumb_path, "JPEG", quality=80)
                result["thumb_ok"] = True
            except Exception as e:  # metadata is still useful without a thumbnail
                result["thumb_error"] = f"{type(e).__name__}: {e}"
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
    return result
