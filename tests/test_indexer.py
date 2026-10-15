import os
import sqlite3

from PIL import Image

from photomap import db
from photomap.indexer import index_folder

from conftest import picture

DENVER = dict(lat=39.7392, lon=-104.9903)
BOULDER = dict(lat=40.0150, lon=-105.2705)


def rows(settings, sql="SELECT * FROM photos"):
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    try:
        return {r["file_name"] if "file_name" in r.keys() else i: r
                for i, r in enumerate(conn.execute(sql))}
    finally:
        conn.close()


# --- basic indexing -------------------------------------------------------

def test_fresh_index(make_photo, settings):
    make_photo("a.jpg", seed=1, taken="2026:07:04 18:30:00", offset="-06:00", **DENVER)
    make_photo("trips/b.jpg", seed=2, taken="2026:07:05 10:00:00", offset="-06:00", **BOULDER)
    make_photo("no_gps.jpg", seed=3, taken="2026:03:03 15:00:00")
    (make_photo.folder / ".hidden.jpg").write_bytes(b"x")
    (make_photo.folder / "notes.txt").write_text("not a photo")

    report = index_folder(settings)

    assert (report.found, report.new, report.on_map, report.needs_review) == (3, 3, 2, 1)
    photos = rows(settings)
    assert photos["b.jpg"]["folder"] == "trips"
    assert photos["a.jpg"]["lat"] == __import__("pytest").approx(39.7392, abs=1e-6)
    for p in photos.values():
        assert (settings.thumb_dir / p["thumb_file"]).exists()


def test_rescan_reads_only_new_or_changed_files(make_photo, settings):
    make_photo("a.jpg", seed=1, taken="2026:07:04 18:30:00", **DENVER)
    make_photo("b.jpg", seed=2, taken="2026:07:04 19:30:00", **DENVER)
    index_folder(settings)

    again = index_folder(settings)
    assert (again.new, again.changed, again.unchanged) == (0, 0, 2)

    make_photo("c.jpg", seed=3, taken="2026:07:04 20:00:00", **DENVER)
    path_b = make_photo("b.jpg", seed=2, taken="2026:07:04 19:30:00", **BOULDER)
    os.utime(path_b, ns=(path_b.stat().st_atime_ns, path_b.stat().st_mtime_ns + 10**9))
    third = index_folder(settings)
    assert (third.new, third.changed, third.unchanged) == (1, 1, 1)
    assert rows(settings)["b.jpg"]["lat"] > 40  # re-read picked up the new GPS


def test_deleted_file_removes_row_and_thumbnail(make_photo, settings):
    gone = make_photo("gone.jpg", seed=1, taken="2026:07:04 18:30:00", **DENVER)
    make_photo("kept.jpg", seed=2, taken="2026:07:04 19:30:00", **DENVER)
    index_folder(settings)
    thumb = settings.thumb_dir / rows(settings)["gone.jpg"]["thumb_file"]

    gone.unlink()
    report = index_folder(settings)
    assert report.removed == 1 and "gone.jpg" not in rows(settings)
    assert not thumb.exists()


def test_manual_location_survives_file_change(make_photo, settings):
    path = make_photo("x.jpg", seed=1, taken="2026:03:03 15:00:00")
    index_folder(settings)
    conn = db.connect(settings.db_path)
    db.set_manual_location(conn, rows(settings)["x.jpg"]["id"], 39.5, -105.0)
    conn.close()

    make_photo("x.jpg", seed=1, taken="2026:03:03 15:00:01")
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 10**9))
    report = index_folder(settings)
    assert report.changed == 1
    assert rows(settings)["x.jpg"]["manual_lat"] == 39.5
    assert report.on_map == 1 and report.needs_review == 0


def test_unreadable_file_reported_once_and_not_retried(make_photo, settings):
    make_photo("ok.jpg", seed=1, taken="2026:07:04 18:30:00", **DENVER)
    (make_photo.folder / "corrupt.jpg").write_bytes(b"not a jpeg")
    report = index_folder(settings)
    assert report.unreadable == 1 and report.errors[0][0] == "corrupt.jpg"
    assert report.on_map == 1 and report.needs_review == 0
    assert index_folder(settings).changed == 0


def test_formats_heic_png_and_portrait_orientation(make_photo, settings):
    make_photo("phone.heic", seed=1, fmt="HEIF", taken="2026:05:01 08:00:00", offset="-06:00", **DENVER)
    make_photo("screenshot.png", seed=2, fmt="PNG")
    # Stored landscape with orientation 6: displays as portrait.
    make_photo("portrait.jpg", seed=3, size=(640, 480), orientation=6,
               taken="2026:05:01 09:00:00", **DENVER)
    report = index_folder(settings)
    assert report.unreadable == 0 and report.thumb_failures == 0
    p = rows(settings)
    assert p["phone.heic"]["lat"] is not None
    assert p["screenshot.png"]["time_source"] == "none"
    assert (p["portrait.jpg"]["width"], p["portrait.jpg"]["height"]) == (480, 640)
    with Image.open(settings.thumb_dir / p["portrait.jpg"]["thumb_file"]) as t:
        assert t.height > t.width


# --- duplicates -------------------------------------------------------------

def test_copy_without_gps_is_hidden_behind_copy_with_gps(make_photo, settings):
    img = picture(7)
    make_photo("20220103_100503.jpg", img=img, taken="2022:01:03 10:05:03", offset="-07:00", **DENVER)
    make_photo("20220103_100503(1).jpg", img=img, quality=75, taken="2022:01:03 10:05:03", offset="-07:00")
    report = index_folder(settings)
    p = rows(settings)
    assert report.duplicates == 1 and report.needs_review == 0
    assert p["20220103_100503(1).jpg"]["duplicate_of"] == p["20220103_100503.jpg"]["id"]


def test_keeps_the_gps_copy_even_when_it_is_the_numbered_one(make_photo, settings):
    img = picture(8)
    make_photo("20220805_151225.jpg", img=img, taken="2022:08:05 15:12:25")
    make_photo("20220805_151225(1).jpg", img=img, taken="2022:08:05 15:12:25", **DENVER)
    index_folder(settings)
    p = rows(settings)
    assert p["20220805_151225.jpg"]["duplicate_of"] == p["20220805_151225(1).jpg"]["id"]


def test_burst_shots_in_the_same_second_are_not_merged(make_photo, settings):
    for i in range(3):
        make_photo(f"burst_{i}.jpg", seed=100 + i, taken="2022:08:05 15:12:25", **DENVER)
    report = index_folder(settings)
    assert report.duplicates == 0 and report.on_map == 3


def test_stripped_copies_are_merged(make_photo, settings):
    original = picture(9)
    make_photo("dated.jpg", img=original, taken="2022:01:01 12:00:00", **DENVER)
    original.save(make_photo.folder / "downloaded.jpg", "JPEG", quality=90)  # no EXIF at all
    shared = picture(10)
    shared.save(make_photo.folder / "4909fa04-.jpg", "JPEG", quality=90)
    shared.save(make_photo.folder / "4909fa04-(1).jpg", "JPEG", quality=80)
    report = index_folder(settings)
    p = rows(settings)
    assert report.duplicates == 2
    assert p["downloaded.jpg"]["duplicate_of"] == p["dated.jpg"]["id"]
    assert p["4909fa04-(1).jpg"]["duplicate_of"] == p["4909fa04-.jpg"]["id"]


def test_blank_images_are_not_treated_as_duplicates(make_photo, settings):
    black = Image.new("RGB", (640, 480), "black")
    make_photo("black1.jpg", img=black)
    make_photo("black2.jpg", img=black)
    assert index_folder(settings).duplicates == 0


# --- suggested locations ------------------------------------------------------

def test_suggestion_from_gps_photo_minutes_apart(make_photo, settings):
    make_photo("gps.jpg", seed=1, taken="2022:03:01 08:40:00", offset="-07:00", **BOULDER)
    make_photo("near.jpg", seed=2, taken="2022:03:01 08:42:30", offset="-07:00")
    make_photo("far.jpg", seed=3, taken="2022:03:01 09:40:00", offset="-07:00")
    report = index_folder(settings)
    p = rows(settings)
    assert report.needs_review == 2 and report.suggestions == 1
    assert p["near.jpg"]["suggested_lat"] == p["gps.jpg"]["lat"]
    assert p["near.jpg"]["suggested_gap_s"] == 150
    assert p["far.jpg"]["suggested_lat"] is None


def test_accepting_and_skipping_reviews(make_photo, settings):
    make_photo("gps.jpg", seed=1, taken="2022:03:01 08:40:00", **BOULDER)
    make_photo("near.jpg", seed=2, taken="2022:03:01 08:41:00")
    make_photo("junk.png", seed=3, fmt="PNG")
    index_folder(settings)
    p = rows(settings)
    conn = db.connect(settings.db_path)
    assert db.accept_suggestion(conn, p["near.jpg"]["id"])
    assert not db.accept_suggestion(conn, p["junk.png"]["id"])
    db.skip_review(conn, p["junk.png"]["id"])
    conn.close()

    report = index_folder(settings)  # a rescan must not undo the review
    assert (report.on_map, report.needs_review) == (2, 0)
    points = rows(settings, "SELECT file_name, location_source FROM map_points")
    assert points["near.jpg"]["location_source"] == "manual"
