"""SQLite index: one row per photo file.

Columns fall into three groups:
- read from the file (refreshed whenever the file changes),
- computed across photos on every scan (duplicates, suggested locations),
- entered by the user (manual location, review status, tags), never touched by a rescan.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

# Columns refreshed from the file on every (re)read. Order matters for upserts.
FILE_COLUMNS = [
    "file_name", "folder", "format", "file_size", "mtime_ns",
    "width", "height", "orientation", "make", "model",
    "lat", "lon", "altitude_m", "taken_local", "taken_utc", "time_source",
    "dhash", "thumb_file", "thumb_ok", "error", "thumb_error", "indexed_at",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS photos (
    id              INTEGER PRIMARY KEY,
    rel_path        TEXT NOT NULL UNIQUE,   -- path inside the photo folder, forward slashes
    file_name       TEXT NOT NULL,
    folder          TEXT NOT NULL,          -- sub-folder inside the photo folder ('' = top level)
    format          TEXT,
    file_size       INTEGER NOT NULL,
    mtime_ns        INTEGER NOT NULL,
    width           INTEGER,                -- as displayed (EXIF rotation applied)
    height          INTEGER,
    orientation     INTEGER,
    make            TEXT,
    model           TEXT,
    lat             REAL,                   -- from the photo's own GPS data
    lon             REAL,
    altitude_m      REAL,
    taken_local     TEXT,                   -- wall-clock time on the phone, ISO 8601
    taken_utc       TEXT,                   -- ISO 8601 with +00:00
    time_source     TEXT,                   -- offset | gps | fallback | none
    dhash           TEXT,                   -- image fingerprint for duplicate checks
    thumb_file      TEXT,                   -- relative to the thumbnails folder
    thumb_ok        INTEGER NOT NULL DEFAULT 0,
    error           TEXT,                   -- set when the file could not be opened
    thumb_error     TEXT,
    indexed_at      TEXT NOT NULL,

    -- computed on every scan
    duplicate_of    INTEGER REFERENCES photos(id) ON DELETE SET NULL,
    suggested_lat   REAL,
    suggested_lon   REAL,
    suggested_from  INTEGER REFERENCES photos(id) ON DELETE SET NULL,
    suggested_gap_s REAL,

    -- entered by the user; a rescan never changes these
    manual_lat      REAL,
    manual_lon      REAL,
    review_status   TEXT                    -- NULL = not reviewed, 'placed', 'skipped'
);

CREATE INDEX IF NOT EXISTS idx_photos_taken ON photos(taken_utc);
CREATE INDEX IF NOT EXISTS idx_photos_latlon ON photos(lat, lon);

CREATE TABLE IF NOT EXISTS tags (
    photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    tag      TEXT NOT NULL,
    PRIMARY KEY (photo_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag);

-- Every photo that belongs on the map, with where its location came from.
CREATE VIEW IF NOT EXISTS map_points AS
SELECT id, rel_path, file_name, folder, make, model, taken_local, taken_utc,
       thumb_file, width, height,
       COALESCE(manual_lat, lat) AS lat,
       COALESCE(manual_lon, lon) AS lon,
       CASE WHEN manual_lat IS NOT NULL THEN 'manual' ELSE 'gps' END AS location_source
FROM photos
WHERE error IS NULL AND duplicate_of IS NULL
  AND COALESCE(manual_lat, lat) IS NOT NULL;

-- Photos that need a person to place them (or skip them).
CREATE VIEW IF NOT EXISTS review_queue AS
SELECT id, rel_path, file_name, make, model, taken_local, thumb_file,
       suggested_lat, suggested_lon, suggested_from, suggested_gap_s
FROM photos
WHERE error IS NULL AND duplicate_of IS NULL
  AND lat IS NULL AND manual_lat IS NULL
  AND review_status IS NULL;
"""


def connect(db_path: Path) -> sqlite3.Connection:
    """Open (and if needed create) the index database."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")  # lets the app read while a scan writes
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"The index at {db_path} was made by a newer version of the app. "
            "Update the app, or delete the data folder to rebuild the index."
        )
    conn.executescript(SCHEMA)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return conn


def upsert_photo(conn: sqlite3.Connection, rel_path: str, values: dict) -> None:
    """Insert a photo, or refresh its file-derived columns if it is already indexed."""
    cols = ["rel_path"] + FILE_COLUMNS
    placeholders = ", ".join("?" for _ in cols)
    updates = ", ".join(f"{c} = excluded.{c}" for c in FILE_COLUMNS)
    conn.execute(
        f"INSERT INTO photos ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(rel_path) DO UPDATE SET {updates}",
        [rel_path] + [values.get(c) for c in FILE_COLUMNS],
    )


def set_manual_location(conn: sqlite3.Connection, photo_id: int, lat: float, lon: float) -> None:
    """Place a photo by hand (stored in the index only, never in the photo file)."""
    conn.execute(
        "UPDATE photos SET manual_lat = ?, manual_lon = ?, review_status = 'placed' WHERE id = ?",
        (lat, lon, photo_id),
    )
    conn.commit()


def accept_suggestion(conn: sqlite3.Connection, photo_id: int) -> bool:
    """Confirm a suggested location. Returns False if the photo has no suggestion."""
    row = conn.execute(
        "SELECT suggested_lat, suggested_lon FROM photos WHERE id = ?", (photo_id,)
    ).fetchone()
    if not row or row["suggested_lat"] is None:
        return False
    set_manual_location(conn, photo_id, row["suggested_lat"], row["suggested_lon"])
    return True


def skip_review(conn: sqlite3.Connection, photo_id: int) -> None:
    """Leave a photo off the map without placing it."""
    conn.execute("UPDATE photos SET review_status = 'skipped' WHERE id = ?", (photo_id,))
    conn.commit()


def reset_review(conn: sqlite3.Connection, photo_id: int) -> None:
    """Undo a placement or skip: the photo goes back into the review queue."""
    conn.execute(
        "UPDATE photos SET manual_lat = NULL, manual_lon = NULL, review_status = NULL WHERE id = ?",
        (photo_id,),
    )
    conn.commit()


def accept_all_suggestions(conn: sqlite3.Connection) -> int:
    """Place every photo in the review queue that has a suggested location. Returns how many."""
    cur = conn.execute(
        "UPDATE photos SET manual_lat = suggested_lat, manual_lon = suggested_lon, review_status = 'placed' "
        "WHERE id IN (SELECT id FROM review_queue WHERE suggested_lat IS NOT NULL)"
    )
    conn.commit()
    return cur.rowcount


# ---- tags ---------------------------------------------------------------------

MAX_TAG_LENGTH = 60


def normalize_tag(text: str | None) -> str | None:
    """Trim and collapse spaces; None if nothing is left."""
    tag = " ".join((text or "").split())[:MAX_TAG_LENGTH]
    return tag or None


def _existing_spelling(conn: sqlite3.Connection, tag: str) -> str:
    """Reuse an existing tag's capitalisation, so 'project 12' joins 'Project 12'."""
    row = conn.execute("SELECT tag FROM tags WHERE lower(tag) = lower(?) LIMIT 1", (tag,)).fetchone()
    return row[0] if row else tag


def add_tag(conn: sqlite3.Connection, photo_ids, text: str) -> str | None:
    """Tag photos. Returns the tag as stored, or None if the text was blank."""
    tag = normalize_tag(text)
    if tag is None:
        return None
    tag = _existing_spelling(conn, tag)
    conn.executemany("INSERT OR IGNORE INTO tags (photo_id, tag) VALUES (?, ?)",
                     [(int(i), tag) for i in photo_ids])
    conn.commit()
    return tag


def remove_tag(conn: sqlite3.Connection, photo_ids, tag: str) -> None:
    conn.executemany("DELETE FROM tags WHERE photo_id = ? AND tag = ?", [(int(i), tag) for i in photo_ids])
    conn.commit()


def rename_tag(conn: sqlite3.Connection, old: str, new_text: str) -> str | None:
    """Rename a tag everywhere. Renaming onto an existing tag merges the two."""
    new = normalize_tag(new_text)
    if new is None or new == old:
        return None
    if new.lower() != old.lower():
        new = _existing_spelling(conn, new)
    conn.execute("INSERT OR IGNORE INTO tags (photo_id, tag) SELECT photo_id, ? FROM tags WHERE tag = ?", (new, old))
    conn.execute("DELETE FROM tags WHERE tag = ?", (old,))
    conn.commit()
    return new


def delete_tag(conn: sqlite3.Connection, tag: str) -> int:
    cur = conn.execute("DELETE FROM tags WHERE tag = ?", (tag,))
    conn.commit()
    return cur.rowcount


def tag_counts(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    rows = conn.execute("SELECT tag, COUNT(*) FROM tags GROUP BY tag ORDER BY lower(tag)").fetchall()
    return [(r[0], r[1]) for r in rows]


def tags_for(conn: sqlite3.Connection, photo_id: int) -> list[str]:
    rows = conn.execute("SELECT tag FROM tags WHERE photo_id = ? ORDER BY lower(tag)", (photo_id,)).fetchall()
    return [r[0] for r in rows]
