import pytest

from photomap import db, mapdata
from photomap.indexer import index_folder
from photomap.ui.review import _next_after

DENVER = dict(lat=39.7392, lon=-104.9903)


@pytest.fixture
def conn(make_photo, settings):
    for i in range(4):
        make_photo(f"p{i}.jpg", seed=i, taken=f"2022:03:01 08:4{i}:00", **DENVER)
    index_folder(settings)
    c = db.connect(settings.db_path)
    yield c
    c.close()


def ids(conn):
    return [r[0] for r in conn.execute("SELECT id FROM photos ORDER BY file_name")]


def test_normalize_tag():
    assert db.normalize_tag("  Project   1234 ") == "Project 1234"
    assert db.normalize_tag("   ") is None
    assert len(db.normalize_tag("x" * 200)) == db.MAX_TAG_LENGTH


def test_add_tag_reuses_existing_spelling(conn):
    a, b, *_ = ids(conn)
    assert db.add_tag(conn, [a], "Project 12") == "Project 12"
    assert db.add_tag(conn, [b], "project 12") == "Project 12"
    db.add_tag(conn, [a], "Project 12")  # adding twice is harmless
    assert db.tag_counts(conn) == [("Project 12", 2)]
    assert db.add_tag(conn, [a], "  ") is None


def test_remove_rename_merge_delete(conn):
    a, b, c, _ = ids(conn)
    db.add_tag(conn, [a, b], "Smith job")
    db.add_tag(conn, [c], "Smith Residence")
    db.remove_tag(conn, [b], "Smith job")
    assert db.tags_for(conn, b) == []

    assert db.rename_tag(conn, "Smith job", "smith residence") == "Smith Residence"  # merges
    assert db.tag_counts(conn) == [("Smith Residence", 2)]
    assert db.rename_tag(conn, "Smith Residence", "SMITH RESIDENCE") == "SMITH RESIDENCE"  # case change
    assert db.tag_counts(conn) == [("SMITH RESIDENCE", 2)]

    assert db.delete_tag(conn, "SMITH RESIDENCE") == 2
    assert db.tag_counts(conn) == []


def test_tags_survive_rescan_and_file_change(conn, make_photo, settings):
    import os

    a = ids(conn)[0]
    db.add_tag(conn, [a], "Keep me")
    path = make_photo("p0.jpg", seed=0, taken="2022:03:01 08:40:05", **DENVER)
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 10**9))
    assert index_folder(settings).changed == 1
    assert db.tags_for(conn, a) == ["Keep me"]


def test_tags_on_the_map(conn, settings):
    a, b, *_ = ids(conn)
    db.add_tag(conn, [a], "Zeta")
    db.add_tag(conn, [a, b], "alpha")
    d = mapdata.load(settings)
    assert d.tags == ["alpha", "Zeta"]  # sorted ignoring case
    by_id = {p.id: p for p in d.points}
    assert by_id[a].tags == [0, 1] and by_id[b].tags == [0]
    assert d.tag_counts() == [2, 1]
    feature = next(f for f in d.geojson()["features"] if f["properties"]["id"] == a)
    assert feature["properties"]["tg"] == "alpha · Zeta"


def test_review_undo_and_accept_all(make_photo, settings):
    make_photo("gps.jpg", seed=1, taken="2022:03:01 08:40:00", **DENVER)
    make_photo("near1.jpg", seed=2, taken="2022:03:01 08:41:00")
    make_photo("near2.jpg", seed=3, taken="2022:03:01 08:43:00")
    make_photo("far.jpg", seed=4, taken="2022:03:01 12:00:00")
    index_folder(settings)
    queue = mapdata.review_queue(settings)
    assert [q.file_name for q in queue] == ["near1.jpg", "near2.jpg", "far.jpg"]
    assert [q.has_suggestion for q in queue] == [True, True, False]

    conn = db.connect(settings.db_path)
    db.skip_review(conn, queue[2].id)
    db.reset_review(conn, queue[2].id)  # undo
    assert len(mapdata.review_queue(settings)) == 3
    assert db.accept_all_suggestions(conn) == 2
    assert [q.file_name for q in mapdata.review_queue(settings)] == ["far.jpg"]
    conn.close()


def test_same_day_context(conn, settings):
    d = mapdata.load(settings)
    assert len(mapdata.same_day(d, "2022-03-01T23:59:00")) == 4
    assert mapdata.same_day(d, "2022-03-02T00:00:00") == []
    assert mapdata.same_day(d, None) == []


@pytest.mark.parametrize("old, current, new, expected", [
    ([1, 2, 3], 2, [1, 3], 3),     # next one takes its place
    ([1, 2, 3], 3, [1, 2], 2),     # last one: step back
    ([1, 2, 3], 2, [1, 2, 3], 2),  # still there (e.g. nothing changed)
    ([1], 1, [], None),            # queue finished
    ([1, 2, 3], 2, [3], 3),        # several placed at once
])
def test_next_photo_after_an_action(old, current, new, expected):
    assert _next_after(old, current, new) == expected
