"""Pieces shared by several screens: header, filter sidebar, option lists."""

from __future__ import annotations

from datetime import date

from dash import Input, Output, dcc, html, no_update

from .. import mapdata

EMPTY_FILTERS = {"start": None, "end": None, "undated": True, "folders": [], "devices": [], "tags": []}


def fmt(n: int) -> str:
    return f"{n:,}"


def version_store(page: str, pm):
    """Each page holds the data version it's showing; the poll updates it after scans and edits."""
    return dcc.Store(id={"type": "version", "page": page}, data=pm.version())


# ---- header -------------------------------------------------------------------------

def header(pm, active: str):
    d = pm.data()

    def link(label, href, key, extra=None):
        cls = "pm-nav-link is-active" if key == active else "pm-nav-link"
        return dcc.Link([label, extra] if extra is not None else label, href=href, className=cls)

    badge = html.Span(fmt(d.needs_review) if d.needs_review else "", id="nav-review",
                      className="pm-badge" if d.needs_review else "")
    return html.Header(className="pm-header", children=[
        html.Div("Photo Map", className="pm-title"),
        html.Nav(className="pm-nav", children=[
            link("Map", "/", "map"),
            link("Review", "/review", "review", badge),
            link("Tags", "/tags", "tags"),
        ]),
        html.Div(pm.status_text(), id="status", className="pm-status"),
        html.Div(className="pm-header-actions", children=[
            html.Button("Re-scan folder", id="rescan", className="pm-btn"),
            dcc.Link("Change folder", href="/setup", className="pm-btn"),
        ]),
        dcc.Interval(id="poll", interval=1000),
    ])


# ---- filter sidebar ------------------------------------------------------------------

def folder_options(d: mapdata.MapData):
    counts = [0] * len(d.folders)
    for p in d.points:
        counts[p.folder] += 1
    return [{"label": f"{mapdata.folder_label(f)} ({fmt(c)})", "value": i}
            for i, (f, c) in enumerate(zip(d.folders, counts))]


def device_options(d: mapdata.MapData):
    counts = [0] * len(d.devices)
    for p in d.points:
        counts[p.device] += 1
    return [{"label": f"{name} ({fmt(c)})", "value": i} for i, (name, c) in enumerate(zip(d.devices, counts))]


def tag_options(d: mapdata.MapData):
    untagged = sum(1 for p in d.points if not p.tags)
    options = [{"label": f"{name} ({fmt(c)})", "value": i}
               for i, (name, c) in enumerate(zip(d.tags, d.tag_counts()))]
    return options + [{"label": f"No tags ({fmt(untagged)})", "value": mapdata.NO_TAGS}]


def _day_range(d):
    first = mapdata.from_day_number(d.first_day) if d.first_day is not None else None
    last = mapdata.from_day_number(d.last_day) if d.last_day is not None else None
    return first, last


def filter_controls(d: mapdata.MapData, prefix: str):
    """Date, folder, camera and tag filters. Writes the filter dict to Store '<prefix>-filters'."""
    first, last = _day_range(d)

    def field(title, *children):
        return html.Div(className="pm-field", children=[html.Div(title, className="pm-section-title"), *children])

    return html.Div([
        dcc.Store(id=f"{prefix}-filters", data=EMPTY_FILTERS),
        field("Date taken",
              dcc.DatePickerRange(
                  id=f"{prefix}-dates", min_date_allowed=first, max_date_allowed=last,
                  initial_visible_month=last, display_format="MMM D, YYYY",
                  start_date_placeholder_text="From", end_date_placeholder_text="To",
                  clearable=True, minimum_nights=0),
              dcc.Checklist(id=f"{prefix}-undated", className="pm-check", value=["y"],
                            options=[{"label": " Include photos with no date", "value": "y"}])),
        field("Tags", dcc.Dropdown(id=f"{prefix}-tags", options=tag_options(d), multi=True,
                                   placeholder="Any tags")),
        field("Folder", dcc.Dropdown(id=f"{prefix}-folders", options=folder_options(d), multi=True,
                                     placeholder="All folders")),
        field("Phone or camera", dcc.Dropdown(id=f"{prefix}-devices", options=device_options(d), multi=True,
                                              placeholder="All cameras")),
        html.Div(className="pm-field", children=[
            html.Button("Clear filters", id=f"{prefix}-clear", className="pm-btn")]),
    ])


def register_filters(pm, prefix: str, page: str):
    """Callbacks that turn the controls into a filter dict, clear them, and refresh their options."""
    app = pm.app

    @app.callback(
        Output(f"{prefix}-filters", "data"),
        Input(f"{prefix}-dates", "start_date"), Input(f"{prefix}-dates", "end_date"),
        Input(f"{prefix}-undated", "value"), Input(f"{prefix}-folders", "value"),
        Input(f"{prefix}-devices", "value"), Input(f"{prefix}-tags", "value"),
    )
    def set_filters(start, end, undated, folders, devices, tags):
        def day(iso):
            return mapdata.day_number(date.fromisoformat(iso[:10])) if iso else None
        return {"start": day(start), "end": day(end), "undated": bool(undated),
                "folders": folders or [], "devices": devices or [], "tags": tags or []}

    @app.callback(
        Output(f"{prefix}-dates", "start_date"), Output(f"{prefix}-dates", "end_date"),
        Output(f"{prefix}-undated", "value"), Output(f"{prefix}-folders", "value"),
        Output(f"{prefix}-devices", "value"), Output(f"{prefix}-tags", "value"),
        Input(f"{prefix}-clear", "n_clicks"), prevent_initial_call=True,
    )
    def clear_filters(clicks):
        if not clicks:  # Dash also calls this when the button first appears
            return (no_update,) * 6
        return None, None, ["y"], [], [], []

    @app.callback(
        Output(f"{prefix}-folders", "options"), Output(f"{prefix}-devices", "options"),
        Output(f"{prefix}-tags", "options"),
        Output(f"{prefix}-dates", "min_date_allowed"), Output(f"{prefix}-dates", "max_date_allowed"),
        Input({"type": "version", "page": page}, "data"), prevent_initial_call=True,
    )
    def refresh_options(_):
        d = pm.data()
        first, last = _day_range(d)
        return folder_options(d), device_options(d), tag_options(d), first, last


def thumb(photo_id: int, className="pm-thumb", **attrs):
    return html.Img(src=f"/photo/thumb/{photo_id}", className=className, **attrs)
