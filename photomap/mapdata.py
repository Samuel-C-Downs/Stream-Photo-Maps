"""Turns the index into what the pages need: points, GeoJSON, filter options, review queue.

Filtering happens in the browser (assets/photomap.js) for speed. The same rules are
repeated in passes_filter() so the server can count and list the photos in view.
Keep the two in step (tests/test_app.py checks they agree).
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime

from .indexer import Settings

_EPOCH = date(1970, 1, 1)
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_SEP = "\x1f"  # separates tags in GROUP_CONCAT; can't appear in a typed tag

NO_TAGS = -1  # filter value meaning "photos without any tag"


def day_number(d: date) -> int:
    return (d - _EPOCH).days


def from_day_number(n: int) -> date:
    return date.fromordinal(_EPOCH.toordinal() + n)


def friendly_time(iso: str | None) -> str:
    """'2026-07-04T18:30:00' -> 'Jul 4, 2026, 6:30 PM' (works on Windows too)."""
    if not iso:
        return "Date unknown"
    t = datetime.fromisoformat(iso)
    hour = t.hour % 12 or 12
    return f"{_MONTHS[t.month - 1]} {t.day}, {t.year}, {hour}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"


def friendly_gap(seconds: float | None) -> str:
    if seconds is None:
        return ""
    if seconds < 60:
        return f"{seconds:.0f} seconds"
    minutes = seconds / 60
    return f"{minutes:.0f} minute{'s' if round(minutes) != 1 else ''}"


def device_name(make: str | None, model: str | None) -> str:
    make, model = (make or "").strip(), (model or "").strip()
    if make and model.lower().startswith(make.lower()):
        make = ""  # "Apple iPhone 11" rather than "Apple Apple iPhone 11"
    return " ".join(p for p in (make, model) if p) or "Unknown camera"


def folder_label(folder: str) -> str:
    return folder or "(top folder)"


@dataclass
class Point:
    id: int
    lat: float
    lon: float
    manual: bool
    folder: int        # index into MapData.folders
    device: int        # index into MapData.devices
    day: int | None    # day_number of the local capture date
    taken_local: str | None
    taken_utc: str | None
    file_name: str
    tags: list[int] = field(default_factory=list)  # indexes into MapData.tags


@dataclass
class MapData:
    points: list[Point]
    folders: list[str]
    devices: list[str]
    first_day: int | None
    last_day: int | None
    needs_review: int
    tags: list[str] = field(default_factory=list)

    @property
    def bounds(self):
        return bounds_of(self.points)

    def tag_counts(self) -> list[int]:
        counts = [0] * len(self.tags)
        for p in self.points:
            for t in p.tags:
                counts[t] += 1
        return counts

    def geojson(self) -> dict:
        """Features carry short property names to keep the page light at 50,000 photos."""
        here = Counter((round(p.lat, 4), round(p.lon, 4)) for p in self.points)
        features = []
        for p in self.points:
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [p.lon, p.lat]},
                "properties": {
                    "id": p.id, "d": p.day, "f": p.folder, "v": p.device, "t": p.tags,
                    "m": 1 if p.manual else 0,
                    "w": friendly_time(p.taken_local),
                    "dev": self.devices[p.device],
                    "tg": " · ".join(self.tags[t] for t in p.tags),
                    "n": here[(round(p.lat, 4), round(p.lon, 4))],
                },
            })
        return {"type": "FeatureCollection", "features": features}


def bounds_of(points, pad: float = 0.0):
    if not points:
        return [[39.4, -105.3], [40.1, -104.6]]  # Denver area until photos are indexed
    lats = [p.lat for p in points]
    lons = [p.lon for p in points]
    return [[min(lats) - pad, min(lons) - pad], [max(lats) + pad, max(lons) + pad]]


def _split_tags(text: str | None) -> list[str]:
    return [t for t in (text or "").split(_SEP) if t]


def load(settings: Settings | None) -> MapData:
    if settings is None or not settings.db_path.exists():
        return MapData([], [], [], None, None, 0)
    with sqlite3.connect(settings.db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT m.id, m.lat, m.lon, m.location_source, m.folder, m.make, m.model, "
            f"m.taken_local, m.taken_utc, m.file_name, GROUP_CONCAT(t.tag, '{_SEP}') AS tags "
            "FROM map_points m LEFT JOIN tags t ON t.photo_id = m.id GROUP BY m.id"
        ).fetchall()
        needs_review = conn.execute("SELECT COUNT(*) FROM review_queue").fetchone()[0]

    folders = sorted({r["folder"] for r in rows})
    devices = sorted({device_name(r["make"], r["model"]) for r in rows}, key=str.lower)
    tags = sorted({t for r in rows for t in _split_tags(r["tags"])}, key=str.lower)
    folder_ix = {f: i for i, f in enumerate(folders)}
    device_ix = {d: i for i, d in enumerate(devices)}
    tag_ix = {t: i for i, t in enumerate(tags)}

    points = []
    for r in rows:
        day = None
        if r["taken_local"]:
            day = day_number(datetime.fromisoformat(r["taken_local"]).date())
        points.append(Point(
            id=r["id"], lat=r["lat"], lon=r["lon"], manual=r["location_source"] == "manual",
            folder=folder_ix[r["folder"]], device=device_ix[device_name(r["make"], r["model"])],
            day=day, taken_local=r["taken_local"], taken_utc=r["taken_utc"], file_name=r["file_name"],
            tags=sorted(tag_ix[t] for t in _split_tags(r["tags"])),
        ))
    days = [p.day for p in points if p.day is not None]
    return MapData(points, folders, devices, min(days, default=None), max(days, default=None),
                   needs_review, tags)


def passes_filter(p: Point, f: dict) -> bool:
    """Same rules as photomapPasses() in assets/photomap.js."""
    if p.day is None:
        if not f.get("undated", True):
            return False
    else:
        if f.get("start") is not None and p.day < f["start"]:
            return False
        if f.get("end") is not None and p.day > f["end"]:
            return False
    if f.get("folders") and p.folder not in f["folders"]:
        return False
    if f.get("devices") and p.device not in f["devices"]:
        return False
    wanted = f.get("tags")
    if wanted:  # any of the chosen tags; NO_TAGS matches untagged photos
        if not (any(t in wanted for t in p.tags) or (NO_TAGS in wanted and not p.tags)):
            return False
    return True


def in_bounds(p: Point, bounds) -> bool:
    if not bounds:
        return True
    (south, west), (north, east) = bounds
    return south <= p.lat <= north and west <= p.lon <= east


def view_order(data: MapData, filters: dict, bounds) -> list[Point]:
    """Photos currently visible on the map: by capture time, then undated ones, then by id."""
    visible = [p for p in data.points if passes_filter(p, filters) and in_bounds(p, bounds)]
    visible.sort(key=lambda p: (p.taken_utc is None, p.taken_utc or "", p.id))
    return visible


# ---- review queue -------------------------------------------------------------------

@dataclass
class ReviewItem:
    id: int
    file_name: str
    folder: str
    device: str
    taken_local: str | None
    suggested_lat: float | None
    suggested_lon: float | None
    suggested_from: int | None
    suggested_gap_s: float | None

    @property
    def has_suggestion(self) -> bool:
        return self.suggested_lat is not None


def review_queue(settings: Settings | None) -> list[ReviewItem]:
    """Photos waiting for a location, oldest first (undated ones last)."""
    if settings is None or not settings.db_path.exists():
        return []
    with sqlite3.connect(settings.db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT q.id, p.file_name, p.folder, p.make, p.model, q.taken_local, q.suggested_lat, "
            "q.suggested_lon, q.suggested_from, q.suggested_gap_s, p.taken_utc "
            "FROM review_queue q JOIN photos p ON p.id = q.id "
            "ORDER BY p.taken_utc IS NULL, p.taken_utc, p.file_name"
        ).fetchall()
    return [ReviewItem(r["id"], r["file_name"], r["folder"], device_name(r["make"], r["model"]),
                       r["taken_local"], r["suggested_lat"], r["suggested_lon"],
                       r["suggested_from"], r["suggested_gap_s"]) for r in rows]


def same_day(data: MapData, taken_local: str | None) -> list[Point]:
    """Photos on the map taken on the same calendar day: likely nearby places."""
    if not taken_local:
        return []
    day = day_number(datetime.fromisoformat(taken_local).date())
    points = [p for p in data.points if p.day == day]
    points.sort(key=lambda p: p.taken_local or "")
    return points
