"""Things that matter when a friend installs or updates: carrying data over, one instance, the launcher."""

import shutil
import threading
from pathlib import Path

from photomap import config, db
from photomap.app import PING_REPLY, PhotoMapApp, _free_port, _setup_logging, already_running
from photomap.indexer import index_folder

DENVER = dict(lat=39.7392, lon=-104.9903)
ROOT = Path(__file__).resolve().parent.parent


def make_copy(parent: Path, name: str) -> Path:
    """A fake app folder: just needs a photomap/ package folder to be recognised."""
    folder = parent / name
    (folder / "photomap").mkdir(parents=True)
    return folder


def test_finds_and_copies_an_earlier_copy(make_photo, settings, tmp_path):
    make_photo("a.jpg", seed=1, taken="2022:01:01 10:00:00", **DENVER)
    make_photo("b.jpg", seed=2, taken="2022:01:01 10:01:00")

    apps = tmp_path / "Documents"
    old = make_copy(apps, "photo-map-0.3.0")
    new = make_copy(apps, "photo-map-0.4.0")
    make_copy(apps, "unrelated-folder").joinpath("photomap").rmdir()  # not an app copy

    old_settings = type(settings)(settings.photo_folder, old / "data")
    index_folder(old_settings)
    config.save(old / "data", photo_folder=str(settings.photo_folder))
    conn = db.connect(old_settings.db_path)
    ids = [r[0] for r in conn.execute("SELECT id FROM photos ORDER BY file_name")]
    db.add_tag(conn, ids, "Project 1234")
    db.set_manual_location(conn, ids[1], 39.5, -105.0)
    conn.close()

    found = config.find_previous_data(new / "data")
    assert [f.app_folder.name for f in found] == ["photo-map-0.3.0"]
    assert (found[0].photos, found[0].tags, found[0].placed) == (2, 1, 1)

    config.copy_previous_data(found[0].data_dir, new / "data")
    pm = PhotoMapApp(new / "data")
    assert pm.settings().photo_folder == settings.photo_folder
    d = pm.data()
    assert len(d.points) == 2 and d.tags == ["Project 1234"]
    assert (old / "data" / "index.sqlite").exists()  # the old copy is left alone

    # Once this copy has its own index, nothing is offered any more.
    assert config.find_previous_data(new / "data") == []


def test_nothing_offered_without_an_earlier_copy(tmp_path):
    new = make_copy(tmp_path, "photo-map")
    assert config.find_previous_data(new / "data") == []


def test_already_running_detects_photo_map_only(tmp_path):
    from werkzeug.serving import make_server

    port = _free_port(8400)
    assert not already_running(port)  # nothing there

    pm = PhotoMapApp(tmp_path / "data")
    server = make_server("127.0.0.1", port, pm.app.server, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert pm.app.server.test_client().get("/_photomap/ping").data.decode() == PING_REPLY
        assert already_running(port)
    finally:
        server.shutdown()
        server.server_close()


def test_log_file_written(tmp_path):
    import logging

    path = _setup_logging(tmp_path / "data")
    logging.getLogger("photomap").warning("something to report")
    for h in logging.getLogger().handlers:
        h.flush()
    assert "something to report" in path.read_text(encoding="utf-8")


def test_launcher_is_windows_friendly():
    bat = (ROOT / "Start Photo Map.bat").read_bytes()
    assert b"\r\n" in bat and b"\n" not in bat.replace(b"\r\n", b"")  # CRLF only
    text = bat.decode("ascii")  # plain ASCII: no surprises with Windows code pages
    assert "uv run --frozen --no-dev python -m photomap" in text
    assert (ROOT / "uv.lock").exists() and (ROOT / ".python-version").exists()
    assert "*.bat text eol=crlf" in (ROOT / ".gitattributes").read_text()
