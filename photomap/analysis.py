"""Cross-photo checks recomputed on every scan: duplicates and suggested locations."""

from __future__ import annotations

import bisect
import re
from collections import defaultdict
from datetime import datetime

from .imaging import hamming

# A re-encoded copy of the same photo usually lands within a few bits;
# different photos land around 32. These limits leave a wide gap between the two.
DATED_DUP_MAX_BITS = 10      # also requires same capture time, camera and size
UNDATED_DUP_MAX_BITS = 4     # only size to go on, so be stricter

_COPY_MARKER = re.compile(r"\(\d+\)|[-_ ]copy\b", re.IGNORECASE)


def _canonical_rank(row) -> tuple:
    """Lower sorts first: the copy to keep when several files are the same photo."""
    return (
        row["lat"] is None,                       # keep the copy that has GPS
        row["taken_local"] is None,               # then one with a capture date
        bool(_COPY_MARKER.search(row["file_name"])),  # then not "photo(1).jpg"
        len(row["file_name"]),
        row["id"],
    )


def find_duplicates(rows) -> dict[int, int]:
    """Return {duplicate_id: kept_id} for every photo that repeats another.

    rows: readable photos with id, file_name, taken_local, make, model,
    width, height, dhash, lat.
    """
    dated = defaultdict(list)
    undated_by_size = defaultdict(list)
    by_hash = defaultdict(list)
    for r in rows:
        # Blank or near-uniform images (black frames, plain screenshots) all hash
        # to almost nothing, so their fingerprints can't tell them apart.
        if not r["dhash"] or bin(int(r["dhash"], 16)).count("1") <= 2:
            continue
        by_hash[(r["dhash"], r["width"], r["height"])].append(r)
        if r["taken_local"] and (r["make"] or r["model"]):
            dated[(r["taken_local"], r["make"], r["model"], r["width"], r["height"])].append(r)
        elif not r["taken_local"]:
            undated_by_size[(r["width"], r["height"])].append(r)

    # Union-find over "is the same photo as" links.
    parent = {}

    def find(i):
        parent.setdefault(i, i)
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def link(a, b):
        parent[find(a)] = find(b)

    # 1. Same capture time, camera and size, and the pixels match.
    #    Burst shots share a second but differ in content, so the fingerprint keeps them apart.
    for group in dated.values():
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if hamming(a["dhash"], b["dhash"]) <= DATED_DUP_MAX_BITS:
                    link(a["id"], b["id"])

    # 2. Undated copies (metadata stripped) that match another undated photo closely.
    for group in undated_by_size.values():
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if hamming(a["dhash"], b["dhash"]) <= UNDATED_DUP_MAX_BITS:
                    link(a["id"], b["id"])

    # 3. An undated copy with exactly the same fingerprint and size as a dated original.
    #    Only the undated copies are linked; two dated photos are never merged here.
    for group in by_hash.values():
        dated_members = [r for r in group if r["taken_local"]]
        undated_members = [r for r in group if not r["taken_local"]]
        if dated_members and undated_members:
            for r in undated_members:
                link(r["id"], dated_members[0]["id"])

    clusters = defaultdict(list)
    by_id = {r["id"]: r for r in rows}
    for i in parent:
        clusters[find(i)].append(by_id[i])

    result = {}
    for members in clusters.values():
        if len(members) < 2:
            continue
        keep = min(members, key=_canonical_rank)
        for m in members:
            if m["id"] != keep["id"]:
                result[m["id"]] = keep["id"]
    return result


def _ts(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return None


def find_suggestions(rows, window_s: float) -> dict[int, tuple[float, float, int, float]]:
    """Suggest a location for each photo without one, from the photo with GPS
    taken closest in time (within window_s seconds).

    rows: readable, non-duplicate photos with id, lat, lon, manual_lat, taken_utc.
    Returns {photo_id: (lat, lon, source_photo_id, gap_seconds)}.
    """
    sources = sorted(
        (t, r["id"], r["lat"], r["lon"])
        for r in rows
        if r["lat"] is not None and (t := _ts(r["taken_utc"])) is not None
    )
    times = [s[0] for s in sources]
    result = {}
    for r in rows:
        if r["lat"] is not None or r["manual_lat"] is not None:
            continue
        t = _ts(r["taken_utc"])
        if t is None or not sources:
            continue
        i = bisect.bisect_left(times, t)
        best = None
        for j in (i - 1, i):
            if 0 <= j < len(sources):
                gap = abs(sources[j][0] - t)
                if best is None or gap < best[0]:
                    best = (gap, sources[j])
        gap, (_, src_id, lat, lon) = best
        if gap <= window_s:
            result[r["id"]] = (lat, lon, src_id, gap)
    return result
