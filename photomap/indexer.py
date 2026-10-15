"""Scan a photo folder and bring the index up to date.

Only new or changed files are opened, so a rescan of an unchanged folder takes
seconds even at tens of thousands of photos.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import db
from .analysis import find_duplicates, find_suggestions
from .exif import PHOTO_EXTENSIONS
from .imaging import process_photo

log = logging.getLogger("photomap")


@dataclass
class Settings:
    photo_folder: Path
    data_dir: Path
    fallback_tz: str = "America/Denver"   # used when a photo has no timezone of its own
    thumb_size: int = 256                 # pixels, longest side
    suggest_window_s: float = 300         # borrow a location from a GPS photo this close in time
    workers: int = 4                      # photos read in parallel

    def __post_init__(self):
        self.photo_folder = Path(self.photo_folder)
        self.data_dir = Path(self.data_dir)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "index.sqlite"

    @property
    def thumb_dir(self) -> Path:
        return self.data_dir / "thumbs"


@dataclass
class ScanReport:
    found: int = 0
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    removed: int = 0
    unreadable: int = 0
    thumb_failures: int = 0
    duplicates: int = 0
    suggestions: int = 0
    on_map: int = 0
    needs_review: int = 0
    seconds: float = 0.0
    errors: list[tuple[str, str]] = field(default_factory=list)

    def __str__(self) -> str:
        return (
            f"Scanned {self.found:,} photos in {self.seconds:.1f}s: "
            f"{self.new:,} new, {self.changed:,} changed, {self.unchanged:,} unchanged, "
            f"{self.removed:,} removed.\n"
            f"On the map: {self.on_map:,}. Duplicates hidden: {self.duplicates:,}. "
            f"Needing review: {self.needs_review:,} ({self.suggestions:,} with a suggested location).\n"
            f"Unreadable files: {self.unreadable:,}. Thumbnail failures: {self.thumb_failures:,}."
        )


def thumb_name(rel_path: str) -> str:
    """Stable thumbnail file name for a photo path, spread over 256 sub-folders."""
    h = hashlib.sha1(rel_path.encode("utf-8")).hexdigest()[:20]
    return f"{h[:2]}/{h}.jpg"


def _is_photo(name: str) -> bool:
    if name.startswith((".", "~$")):  # hidden files, Office/Drive temp files
        return False
    return os.path.splitext(name)[1].lower() in PHOTO_EXTENSIONS


def list_photos(folder: Path) -> dict[str, tuple[int, int]]:
    """Map each photo's relative path (forward slashes) to (size, modified time ns)."""
    found = {}
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if not _is_photo(name):
                continue
            full = Path(root) / name
            try:
                st = full.stat()
            except OSError as e:  # vanished mid-scan, or permissions
                log.warning("Skipping %s: %s", full, e)
                continue
            found[full.relative_to(folder).as_posix()] = (st.st_size, st.st_mtime_ns)
    return found


def index_folder(settings: Settings, progress: Callable[[int, int], None] | None = None) -> ScanReport:
    """Bring the index in line with the photo folder and return what changed.

    progress(done, total) is called as photos are read, if given.
    """
    start = time.perf_counter()
    if not settings.photo_folder.is_dir():
        raise FileNotFoundError(f"Photo folder not found: {settings.photo_folder}")

    report = ScanReport()
    conn = db.connect(settings.db_path)
    try:
        on_disk = list_photos(settings.photo_folder)
        report.found = len(on_disk)

        indexed = {
            r["rel_path"]: r
            for r in conn.execute("SELECT id, rel_path, file_size, mtime_ns, thumb_file FROM photos")
        }

        # Files that disappeared: drop their rows and thumbnails.
        for rel_path in indexed.keys() - on_disk.keys():
            row = indexed[rel_path]
            conn.execute("DELETE FROM photos WHERE id = ?", (row["id"],))
            if row["thumb_file"]:
                (settings.thumb_dir / row["thumb_file"]).unlink(missing_ok=True)
            report.removed += 1

        todo = []
        for rel_path, (size, mtime) in on_disk.items():
            old = indexed.get(rel_path)
            if old is None:
                report.new += 1
                todo.append(rel_path)
            elif old["file_size"] != size or old["mtime_ns"] != mtime:
                report.changed += 1
                todo.append(rel_path)
            else:
                report.unchanged += 1
        conn.commit()

        _read_photos(conn, settings, todo, on_disk, report, progress)
        _recompute(conn, settings, report)

        report.on_map = conn.execute("SELECT COUNT(*) FROM map_points").fetchone()[0]
        report.needs_review = conn.execute("SELECT COUNT(*) FROM review_queue").fetchone()[0]
        report.unreadable = conn.execute(
            "SELECT COUNT(*) FROM photos WHERE error IS NOT NULL").fetchone()[0]
        report.thumb_failures = conn.execute(
            "SELECT COUNT(*) FROM photos WHERE error IS NULL AND thumb_ok = 0").fetchone()[0]
        report.errors = [
            (r["rel_path"], r["error"])
            for r in conn.execute("SELECT rel_path, error FROM photos WHERE error IS NOT NULL")
        ]
    finally:
        conn.close()

    report.seconds = time.perf_counter() - start
    log.info("%s", report)
    return report


def _read_photos(conn, settings, todo, on_disk, report, progress, batch=200):
    """Open each new/changed photo (in parallel) and write its row."""
    total = len(todo)
    if not total:
        return

    def work(rel_path):
        thumb = thumb_name(rel_path)
        info = process_photo(
            settings.photo_folder / rel_path,
            settings.thumb_dir / thumb,
            settings.thumb_size,
            settings.fallback_tz,
        )
        return rel_path, thumb, info

    done = 0
    with ThreadPoolExecutor(max_workers=max(1, settings.workers)) as pool:
        futures = [pool.submit(work, p) for p in todo]
        for fut in as_completed(futures):
            rel_path, thumb, info = fut.result()
            size, mtime = on_disk[rel_path]
            folder, _, name = rel_path.rpartition("/")
            info.update(
                file_name=name,
                folder=folder,
                format=os.path.splitext(name)[1].lower(),
                file_size=size,
                mtime_ns=mtime,
                thumb_file=thumb if info["thumb_ok"] else None,
                thumb_ok=int(info["thumb_ok"]),
                indexed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )
            db.upsert_photo(conn, rel_path, info)
            if info["error"]:
                log.warning("Could not read %s: %s", rel_path, info["error"])
            done += 1
            if done % batch == 0:
                conn.commit()  # an interrupted scan keeps what it has done
            if progress:
                progress(done, total)
    conn.commit()


def _recompute(conn, settings, report):
    """Refresh duplicate links and suggested locations across the whole index."""
    rows = conn.execute(
        "SELECT id, file_name, taken_local, taken_utc, make, model, width, height, dhash, "
        "lat, lon, manual_lat FROM photos WHERE error IS NULL"
    ).fetchall()

    dups = find_duplicates(rows)
    conn.execute("UPDATE photos SET duplicate_of = NULL")
    conn.executemany("UPDATE photos SET duplicate_of = ? WHERE id = ?",
                     [(keep, dup) for dup, keep in dups.items()])
    report.duplicates = len(dups)

    unique = [r for r in rows if r["id"] not in dups]
    sugg = find_suggestions(unique, settings.suggest_window_s)
    conn.execute("UPDATE photos SET suggested_lat = NULL, suggested_lon = NULL, "
                 "suggested_from = NULL, suggested_gap_s = NULL")
    conn.executemany(
        "UPDATE photos SET suggested_lat = ?, suggested_lon = ?, suggested_from = ?, "
        "suggested_gap_s = ? WHERE id = ?",
        [(lat, lon, src, gap, pid) for pid, (lat, lon, src, gap) in sugg.items()],
    )
    report.suggestions = conn.execute(
        "SELECT COUNT(*) FROM review_queue WHERE suggested_lat IS NOT NULL").fetchone()[0]
    conn.commit()
