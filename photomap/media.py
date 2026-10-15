"""Serves thumbnails, large previews and original photos to the browser by photo id.

Only files recorded in the index can be served; there's no way to request an
arbitrary path.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path
from typing import Callable

from flask import abort, send_file
from PIL import Image, ImageOps

from .indexer import Settings

PREVIEW_SIZE = 1600
_BROWSER_FORMATS = {".jpg", ".jpeg", ".png", ".webp"}
_preview_lock = threading.Lock()


def _photo(settings: Settings, photo_id: int):
    with sqlite3.connect(settings.db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT rel_path, thumb_file, mtime_ns FROM photos WHERE id = ? AND error IS NULL",
            (photo_id,),
        ).fetchone()
    if row is None:
        abort(404)
    return row


def _original_path(settings: Settings, rel_path: str) -> Path:
    root = settings.photo_folder.resolve()
    path = (root / rel_path).resolve()
    if root not in path.parents or not path.is_file():
        abort(404)
    return path


def make_preview(source: Path, target: Path, size: int = PREVIEW_SIZE) -> None:
    """Write a browser-friendly JPEG no larger than size x size, upright."""
    with Image.open(source) as img:
        img.draft("RGB", (size, size))
        img = ImageOps.exif_transpose(img)
        img.thumbnail((size, size), Image.Resampling.LANCZOS)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        img.save(tmp, "JPEG", quality=85)
        os.replace(tmp, target)


def register(server, get_settings: Callable[[], Settings | None]) -> None:
    def settings_or_404() -> Settings:
        s = get_settings()
        if s is None:
            abort(404)
        return s

    @server.route("/photo/thumb/<int:photo_id>")
    def photo_thumb(photo_id):
        s = settings_or_404()
        row = _photo(s, photo_id)
        if not row["thumb_file"]:
            abort(404)
        return send_file(s.thumb_dir / row["thumb_file"], mimetype="image/jpeg", max_age=86400)

    @server.route("/photo/preview/<int:photo_id>")
    def photo_preview(photo_id):
        """Large preview, made the first time a photo is opened and reused after that."""
        s = settings_or_404()
        row = _photo(s, photo_id)
        target = s.data_dir / "previews" / (row["thumb_file"] or f"x/{photo_id}.jpg")
        stale = not target.exists() or target.stat().st_mtime_ns < row["mtime_ns"]
        if stale:
            source = _original_path(s, row["rel_path"])
            with _preview_lock:
                try:
                    make_preview(source, target)
                except Exception:
                    abort(500)
        return send_file(target, mimetype="image/jpeg", max_age=3600)

    @server.route("/photo/original/<int:photo_id>")
    def photo_original(photo_id):
        s = settings_or_404()
        row = _photo(s, photo_id)
        path = _original_path(s, row["rel_path"])
        # HEIC and TIFF won't display in most browsers, so offer them as downloads.
        return send_file(path, as_attachment=path.suffix.lower() not in _BROWSER_FORMATS,
                         download_name=path.name)
