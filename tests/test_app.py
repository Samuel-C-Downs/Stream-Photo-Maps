import json
import shutil
import subprocess
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from photomap import config, db, mapdata
from photomap.app import PhotoMapApp
from photomap.indexer import index_folder

DENVER = dict(lat=39.7392, lon=-104.9903)
JS_FILE = Path(__file__).resolve().parent.parent / "photomap" / "assets" / "photomap.js"


def point(**kw):
    base = dict(id=1, lat=39.7, lon=-105.0, manual=False, folder=0, device=0, day=19000,
                taken_local="2022-01-08T10:00:00", taken_utc="2022-01-08T17:00:00+00:00", file_name="a.jpg")
    return mapdata.Point(**(base | kw))


FILTER_CASES = [
    ({}, dict(), True),
    ({"start": 19001}, dict(), False),
    ({"end": 18999}, dict(), False),
    ({"start": 19000, "end": 19000}, dict(), True),
    ({"undated": False}, dict(day=None), False),
    ({"undated": True, "start": 19001}, dict(day=None), True),  # undated photos ignore the date range
    ({"folders": [1]}, dict(), False),
    ({"folders": [0, 1]}, dict(), True),
    ({"devices": [2]}, dict(device=2), True),
    ({"devices": [2]}, dict(), False),
    ({"folders": [], "devices": []}, dict(), True),  # empty selection = everything
    ({"tags": [1]}, dict(tags=[0, 1]), True),
    ({"tags": [2]}, dict(tags=[0, 1]), False),
    ({"tags": [mapdata.NO_TAGS]}, dict(tags=[]), True),
    ({"tags": [mapdata.NO_TAGS]}, dict(tags=[0]), False),
    ({"tags": [mapdata.NO_TAGS, 0]}, dict(tags=[0]), True),
]


@pytest.mark.parametrize("filters, overrides, expected", FILTER_CASES)
def test_filter_rules(filters, overrides, expected):
    assert mapdata.passes_filter(point(**overrides), filters) is expected


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_browser_filter_matches_python_filter():
    """The map filters in JavaScript; the side panel counts in Python. They must agree."""
    cases = []
    for filters, overrides, _ in FILTER_CASES:
        p = point(**overrides)
        cases.append({"f": filters, "p": {"d": p.day, "f": p.folder, "v": p.device, "t": p.tags}})
    script = (
        "global.window = {}; global.document = {addEventListener(){}};\n"
        + JS_FILE.read_text(encoding="utf-8")
        + f"\nconst cases = {json.dumps(cases)};\n"
        "console.log(JSON.stringify(cases.map(c => photomapPasses(c.p, c.f))));"
    )
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout
    js_results = json.loads(out)
    py_results = [mapdata.passes_filter(point(**o), f) for f, o, _ in FILTER_CASES]
    assert js_results == py_results


def test_view_order_is_bounds_then_time_then_undated():
    d = mapdata.MapData(
        points=[
            point(id=1, taken_utc="2022-03-01T00:00:00+00:00"),
            point(id=2, taken_utc="2022-01-01T00:00:00+00:00"),
            point(id=3, taken_utc=None, day=None),
            point(id=4, lat=45.0),  # outside the view
        ],
        folders=[""], devices=["x"], first_day=None, last_day=None, needs_review=0,
    )
    bounds = [[39.0, -106.0], [40.0, -104.0]]
    assert [p.id for p in mapdata.view_order(d, {}, bounds)] == [2, 1, 3]
    assert [p.id for p in mapdata.view_order(d, {}, None)] == [2, 4, 1, 3]


def test_labels():
    assert mapdata.friendly_time("2026-07-04T18:30:00") == "Jul 4, 2026, 6:30 PM"
    assert mapdata.friendly_time("2026-07-04T00:05:00") == "Jul 4, 2026, 12:05 AM"
    assert mapdata.friendly_time(None) == "Date unknown"
    assert mapdata.device_name("samsung", "SM-G996U") == "samsung SM-G996U"
    assert mapdata.device_name("Apple", "Apple iPhone 11") == "Apple iPhone 11"
    assert mapdata.device_name(None, None) == "Unknown camera"


# --- the running app -----------------------------------------------------------

@pytest.fixture
def client(make_photo, settings):
    make_photo("portrait.jpg", seed=1, size=(640, 480), orientation=6,
               taken="2022:05:01 09:00:00", **DENVER)
    make_photo("phone.heic", seed=2, fmt="HEIF", taken="2022:05:01 10:00:00", **DENVER)
    index_folder(settings)
    config.save(settings.data_dir, photo_folder=str(settings.photo_folder))
    pm = PhotoMapApp(settings.data_dir)
    pm.app.server.testing = True
    client = pm.app.server.test_client()
    client.pm = pm
    return client


def _ids(pm):
    return {p.file_name: p.id for p in pm.data().points}


def test_thumbnail_preview_and_original_routes(client):
    ids = _ids(client.pm)
    assert client.get(f"/photo/thumb/{ids['portrait.jpg']}").status_code == 200

    r = client.get(f"/photo/preview/{ids['portrait.jpg']}")
    assert r.status_code == 200
    with Image.open(BytesIO(r.data)) as img:
        assert img.height > img.width  # rotation applied

    r = client.get(f"/photo/original/{ids['phone.heic']}")
    assert r.status_code == 200 and "attachment" in r.headers["Content-Disposition"]
    r = client.get(f"/photo/original/{ids['portrait.jpg']}")
    assert "attachment" not in r.headers.get("Content-Disposition", "")


def test_unknown_photo_is_404(client):
    assert client.get("/photo/thumb/99999").status_code == 404
    assert client.get("/photo/preview/99999").status_code == 404


def test_pages_build(client, tmp_path):
    from photomap.app import PAGES

    assert client.get("/").status_code == 200
    for page in PAGES.values():
        page.layout(client.pm)
    client.pm.setup_page()
    empty = PhotoMapApp(tmp_path / "empty")  # first run: no folder chosen, no index yet
    assert empty.settings() is None
    empty.setup_page()


def test_edits_refresh_the_map_data(client):
    pm = client.pm
    before = pm.version()
    photo = pm.data().points[0]
    with pm.edit() as conn:
        db.add_tag(conn, [photo.id], "Project 1234")
    assert pm.version() != before
    d = pm.data()
    assert d.tags == ["Project 1234"]
    assert next(p for p in d.points if p.id == photo.id).tags == [0]
