"""App settings saved between runs (data/settings.json), and carrying data across versions.

The data folder lives inside the app folder. A new version unzipped into a new folder
starts empty, so find_previous_data() looks for earlier copies next to it and
copy_previous_data() brings their index, thumbnails, tags and placements across.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_FILE = "settings.json"


def load(data_dir: Path) -> dict:
    try:
        return json.loads((Path(data_dir) / _FILE).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save(data_dir: Path, **values) -> dict:
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    settings = load(data_dir) | values
    (data_dir / _FILE).write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return settings


@dataclass
class PreviousData:
    app_folder: Path      # e.g. ...\Downloads\photo-map-0.3.0
    data_dir: Path        # its data folder
    photo_folder: str | None
    photos: int
    tags: int
    placed: int


def _summarize(data_dir: Path) -> PreviousData | None:
    index = data_dir / "index.sqlite"
    if not index.is_file():
        return None
    try:
        conn = sqlite3.connect(index)  # only reads
        try:
            photos = conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
            tags = conn.execute("SELECT COUNT(DISTINCT tag) FROM tags").fetchone()[0]
            placed = conn.execute("SELECT COUNT(*) FROM photos WHERE manual_lat IS NOT NULL").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    return PreviousData(data_dir.parent, data_dir, load(data_dir).get("photo_folder"), photos, tags, placed)


def find_previous_data(data_dir: Path = DEFAULT_DATA_DIR) -> list[PreviousData]:
    """Earlier Photo Map folders next to this one that hold an index, newest first.

    Only offered when this copy has no index of its own yet.
    """
    data_dir = Path(data_dir)
    if (data_dir / "index.sqlite").exists():
        return []
    here = data_dir.parent.resolve()
    found = []
    try:
        siblings = [p for p in here.parent.iterdir() if p.is_dir() and p.resolve() != here]
    except OSError:
        return []
    for folder in siblings:
        if not (folder / "photomap").is_dir():  # only other copies of this app
            continue
        summary = _summarize(folder / "data")
        if summary:
            found.append(summary)
    found.sort(key=lambda s: (s.data_dir / "index.sqlite").stat().st_mtime, reverse=True)
    return found


def copy_previous_data(source: Path, data_dir: Path = DEFAULT_DATA_DIR) -> None:
    """Copy an earlier data folder into this one (index, thumbnails, previews, settings).

    The old copy is left as it is, so nothing is lost if something goes wrong.
    """
    source, data_dir = Path(source), Path(data_dir)
    if not (source / "index.sqlite").is_file():
        raise FileNotFoundError(f"No index found in {source}")
    shutil.copytree(source, data_dir, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("*.log", "*.log.*", "*.tmp"))
