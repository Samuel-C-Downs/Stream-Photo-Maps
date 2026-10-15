"""The Photo Map app: Map, Review and Tags screens, plus first-run folder setup.

Start it with run() (from a notebook) or `python -m photomap` (from a terminal).
The screens live in photomap/ui/.
"""

from __future__ import annotations

import logging
import socket
import sqlite3
import subprocess
import sys
import threading
import webbrowser
from contextlib import closing, contextmanager
from pathlib import Path

from dash import ALL, Dash, Input, Output, State, dcc, html, no_update

from . import config, db, mapdata, media
from .indexer import Settings
from .scanning import ScanManager
from .ui import common, mapview, review, tagging

log = logging.getLogger("photomap")

OSM_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"

PAGES = {"/": mapview, "/review": review, "/tags": tagging}
PING_REPLY = "photo-map"
LOG_FILE = "photo-map.log"


class PhotoMapApp:
    def __init__(self, data_dir: Path = config.DEFAULT_DATA_DIR):
        self.data_dir = Path(data_dir)
        self.scans = ScanManager()
        self.edits = 0  # goes up after every change made in the app (tags, placements)
        self._cache: tuple[str | None, mapdata.MapData | None] = (None, None)
        self._lock = threading.Lock()
        self.tile_url = config.load(self.data_dir).get("tile_url") or OSM_URL

        self.app = Dash(
            __name__,
            assets_folder=str(Path(__file__).parent / "assets"),
            title="Photo Map",
            update_title=None,
            suppress_callback_exceptions=True,  # screens swap components in and out
        )
        media.register(self.app.server, self.settings)
        self.app.server.add_url_rule("/_photomap/ping", "photomap_ping", lambda: PING_REPLY)
        self.app.layout = html.Div([
            dcc.Location(id="url"),
            # written by assets/photomap.js when something is clicked
            dcc.Store(id="open-request"), dcc.Store(id="tg-click"), dcc.Store(id="rv-pick"),
            html.Div(id="page"),
        ])
        self._register_callbacks()
        for page in PAGES.values():
            page.register(self)

    # ---- data -----------------------------------------------------------------

    def settings(self) -> Settings | None:
        folder = config.load(self.data_dir).get("photo_folder")
        return Settings(folder, self.data_dir) if folder else None

    def version(self) -> str:
        """Changes whenever what the screens show could have changed: a scan finished or an edit."""
        return f"{self.scans.version}.{self.edits}"

    def data(self) -> mapdata.MapData:
        version = self.version()
        with self._lock:
            if self._cache[0] != version:
                self._cache = (version, mapdata.load(self.settings()))
            return self._cache[1]

    @contextmanager
    def read(self):
        with closing(sqlite3.connect(self.settings().db_path)) as conn:
            yield conn

    @contextmanager
    def edit(self):
        """Open the index for a change; screens refresh once it's done."""
        with closing(db.connect(self.settings().db_path)) as conn:
            yield conn
            conn.commit()
        with self._lock:
            self.edits += 1

    def start_scan(self) -> bool:
        s = self.settings()
        if s is None or not s.photo_folder.is_dir():
            return False
        return self.scans.start(s)

    def status_text(self):
        st = self.scans.status()
        if st["running"]:
            progress = f" {common.fmt(st['done'])} of {common.fmt(st['total'])}" if st["total"] else ""
            return f"Scanning photos…{progress}"
        if st["error"]:
            return html.Span(f"Last scan failed: {st['error']}", className="pm-warn")
        s = self.settings()
        if s and not s.photo_folder.is_dir():
            return html.Span(f"Photo folder not found: {s.photo_folder}", className="pm-warn")
        d = self.data()
        return [html.Strong(common.fmt(len(d.points))), " photos on the map"]

    # ---- setup page -------------------------------------------------------------

    def setup_page(self):
        current = self.settings()
        previous = config.find_previous_data(self.data_dir) if current is None else []
        return html.Div(className="pm-setup", children=[
            self._previous_card(previous[0]) if previous else None,
            html.H1("Choose your photo folder" if not previous else "Or start with a photo folder"),
            html.P("Photo Map reads the photos in this folder and all its sub-folders and puts them "
                   "on a map. Your photos are only read, never changed or moved."),
            html.Div(className="pm-setup-row", children=[
                dcc.Input(id="setup-path", className="pm-input", type="text",
                          value=str(current.photo_folder) if current else "",
                          placeholder=r"C:\Users\you\Pictures\Photos"),
                html.Button("Browse…", id="setup-browse", className="pm-btn"),
            ]),
            html.Div(id="setup-error", className="pm-error"),
            html.Div(style={"display": "flex", "gap": "12px", "alignItems": "center"}, children=[
                html.Button("Show my photos", id="setup-start", className="pm-btn pm-btn-primary"),
                dcc.Link("Back to the map", href="/", className="pm-btn pm-btn-link") if current else None,
            ]),
            html.P("Changing to a different folder rebuilds the index. Tags and locations you added "
                   "for photos in the old folder would be lost.", className="pm-note",
                   style={"marginTop": "24px"}) if current else None,
        ])

    @staticmethod
    def _previous_card(prev: config.PreviousData):
        bits = [f"{common.fmt(prev.photos)} photos indexed"]
        if prev.tags:
            bits.append(f"{common.fmt(prev.tags)} tag{'s' if prev.tags != 1 else ''}")
        if prev.placed:
            bits.append(f"{common.fmt(prev.placed)} placed by hand")
        return html.Div(className="pm-carry", children=[
            html.H1("Welcome back"),
            html.P(["Photo Map found your data from an earlier copy in ", html.B(str(prev.app_folder)), "."]),
            html.P(" · ".join(bits) + (f" · photos from {prev.photo_folder}" if prev.photo_folder else ""),
                   className="pm-note"),
            dcc.Store(id="setup-prev", data=str(prev.data_dir)),
            dcc.Loading(type="dot", children=html.Div(id="setup-import-msg", className="pm-error")),
            html.Button("Bring my photos, tags and places across", id="setup-import",
                        className="pm-btn pm-btn-primary"),
            html.P("The earlier copy is left as it is. Once everything looks right here, you can delete it.",
                   className="pm-note", style={"marginTop": "12px"}),
        ])

    # ---- callbacks ----------------------------------------------------------------

    def _register_callbacks(self):
        app = self.app

        @app.callback(Output("page", "children"), Input("url", "pathname"))
        def route(path):
            if self.settings() is None or path == "/setup":
                return self.setup_page()
            return PAGES.get(path, mapview).layout(self)

        @app.callback(
            Output("status", "children"), Output("rescan", "disabled"),
            Output("nav-review", "children"), Output("nav-review", "className"),
            Output({"type": "version", "page": ALL}, "data"),
            Input("poll", "n_intervals"), State({"type": "version", "page": ALL}, "data"),
        )
        def poll(_, shown):
            version = self.version()
            changed = [version if v != version else no_update for v in shown]
            n = self.data().needs_review
            return (self.status_text(), self.scans.status()["running"],
                    common.fmt(n) if n else "", "pm-badge" if n else "", changed)

        @app.callback(
            Output("rescan", "disabled", allow_duplicate=True),
            Input("rescan", "n_clicks"), prevent_initial_call=True,
        )
        def rescan(clicks):
            if not clicks:  # Dash also calls this when the button first appears
                return no_update
            return self.start_scan()

        @app.callback(
            Output("setup-path", "value"), Output("setup-error", "children"),
            Input("setup-browse", "n_clicks"), prevent_initial_call=True,
        )
        def browse(clicks):
            if not clicks:  # page just appeared; don't pop up the folder picker
                return no_update, no_update
            chosen, error = pick_folder()
            return (chosen or no_update), error

        @app.callback(
            Output("url", "pathname", allow_duplicate=True), Output("setup-import-msg", "children"),
            Input("setup-import", "n_clicks"), State("setup-prev", "data"), prevent_initial_call=True,
        )
        def import_previous(clicks, source):
            if not clicks or not source:
                return no_update, no_update
            try:
                config.copy_previous_data(Path(source), self.data_dir)
            except (OSError, FileNotFoundError) as e:
                log.exception("Copying earlier data failed")
                return no_update, f"Couldn't copy the earlier data: {e}"
            with self._lock:
                self.edits += 1  # screens must reload from the copied index
            self.start_scan()  # picks up anything new in the photo folder
            return "/", ""

        @app.callback(
            Output("url", "pathname"), Output("setup-error", "children", allow_duplicate=True),
            Input("setup-start", "n_clicks"), State("setup-path", "value"), prevent_initial_call=True,
        )
        def choose_folder(clicks, path):
            if not clicks:
                return no_update, no_update
            folder = Path((path or "").strip().strip('"'))
            if not str(folder) or str(folder) == "." or not folder.is_dir():
                return no_update, "That folder doesn't exist. Check the path, or use Browse…"
            if self.scans.status()["running"]:
                return no_update, "A scan is still running. Wait for it to finish, then try again."
            config.save(self.data_dir, photo_folder=str(folder.resolve()))
            self.start_scan()
            return "/", ""


def pick_folder() -> tuple[str | None, str]:
    """Open the system folder picker. Runs in its own process so it can't freeze the app."""
    code = (
        "import tkinter as tk\n"
        "from tkinter import filedialog\n"
        "root = tk.Tk(); root.withdraw(); root.attributes('-topmost', True)\n"
        "print(filedialog.askdirectory(title='Choose your photo folder') or '')\n"
    )
    try:
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"Couldn't open the folder picker ({e}). Paste the folder path instead."
    if result.returncode != 0:
        return None, "Couldn't open the folder picker. Paste the folder path instead."
    chosen = result.stdout.strip()
    return (str(Path(chosen)) if chosen else None), ""


def _free_port(start: int) -> int:
    for port in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No free port found between {start} and {start + 49}")


def already_running(port: int) -> bool:
    """True if a Photo Map is already answering on this port."""
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/_photomap/ping", timeout=1.5) as r:
            return r.read().decode(errors="ignore").strip() == PING_REPLY
    except OSError:
        return False


def _setup_logging(data_dir: Path) -> Path:
    """Messages to the window, and warnings/errors to data/photo-map.log to send when asking for help."""
    from logging.handlers import RotatingFileHandler

    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    if not log.handlers:
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(message)s"))
        log.addHandler(console)
        log.setLevel(logging.INFO)
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path = data_dir / LOG_FILE
    root = logging.getLogger()
    if not any(getattr(h, "baseFilename", None) == str(log_path) for h in root.handlers):
        file_handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        file_handler.setLevel(logging.INFO)
        root.addHandler(file_handler)
        if root.level > logging.INFO or root.level == logging.NOTSET:
            root.setLevel(logging.INFO)
    return log_path


def run(photo_folder: str | Path | None = None, data_dir: str | Path = config.DEFAULT_DATA_DIR,
        port: int = 8050, open_browser: bool = True) -> None:
    """Start Photo Map and open it in the browser. Blocks until stopped (Ctrl+C, or Stop in Jupyter).

    photo_folder: optional; otherwise the app asks for one (and remembers it).
    If Photo Map is already running, this just opens it in the browser.
    """
    from werkzeug.serving import make_server

    from . import __version__

    if already_running(port):
        url = f"http://127.0.0.1:{port}/"
        print(f"Photo Map is already running. Opening {url}")
        if open_browser:
            webbrowser.open(url)
        return

    data_dir = Path(data_dir)
    log_path = _setup_logging(data_dir)
    log.info("Photo Map %s starting (log file: %s)", __version__, log_path)

    if photo_folder is not None:
        folder = Path(photo_folder)
        if not folder.is_dir():
            raise FileNotFoundError(f"Photo folder not found: {folder}")
        config.save(data_dir, photo_folder=str(folder.resolve()))

    pm = PhotoMapApp(data_dir)
    if pm.settings():
        pm.start_scan()  # index on start; the screens show the existing index meanwhile

    port = _free_port(port)
    url = f"http://127.0.0.1:{port}/"
    server = make_server("127.0.0.1", port, pm.app.server, threaded=True)  # this computer only
    print(f"\nPhoto Map is running at {url}\n"
          "Keep this window open while you use the map. To quit, close this window "
          "(or press Ctrl+C; in Jupyter, the Stop button).\n", flush=True)
    if open_browser:
        threading.Timer(1.0, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Photo Map stopped.")
    finally:
        server.server_close()
