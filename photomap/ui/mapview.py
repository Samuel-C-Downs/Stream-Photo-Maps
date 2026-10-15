"""The Map screen: clustered map, filters, photos in view, and the photo viewer."""

from __future__ import annotations

import sqlite3

import dash_leaflet as dl
from dash import ALL, ClientsideFunction, Input, Output, State, ctx, dcc, html, no_update

from .. import db, mapdata
from .common import fmt, filter_controls, header, register_filters, thumb, version_store

GALLERY_LIMIT = 90     # thumbnails shown in the "photos in view" panel
ORDER_LIMIT = 5000     # photos the viewer can step through with next/previous

OSM_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'

TIME_SOURCE_NOTE = {
    "offset": "",
    "gps": " (time from GPS clock)",
    "fallback": " (time zone assumed: Denver)",
}


def js(name: str) -> dict:
    """Reference to a function in assets/photomap.js."""
    return {"variable": f"photomap.{name}"}


def tile_layer(pm):
    return dl.TileLayer(url=pm.tile_url, attribution=OSM_ATTRIBUTION, maxZoom=19)


def layout(pm):
    d = pm.data()
    return html.Div(className="pm-app", children=[
        header(pm, "map"),
        html.Div(className="pm-body", children=[
            html.Aside(className="pm-sidebar", children=[
                html.Div(id="count", className="pm-count"),
                filter_controls(d, "m"),
                _legend(pm),
            ]),
            html.Div(className="pm-map", children=_map(pm, d)),
            html.Aside(className="pm-gallery", children=[
                html.Div(className="pm-gallery-head", children=[
                    html.Div("Photos in view", className="pm-section-title"),
                    html.Div(id="gallery-sub", className="pm-gallery-sub"),
                ]),
                html.Div(id="gallery", className="pm-gallery-grid"),
            ]),
        ]),
        _viewer(),
        dcc.Store(id="all-photos", data=d.geojson()),  # every photo; filtered in the browser
        version_store("map", pm),
        dcc.Store(id="view-order", data=[]),
        dcc.Store(id="viewer-id"),
        dcc.Store(id="vt-refresh", data=0),
    ])


def _legend(pm):
    from .. import __version__

    return html.Div(className="pm-legend", children=[
        html.Div([html.Span(className="pm-dot", style={"background": "#2563eb"}), "Location from the photo"]),
        html.Div([html.Span(className="pm-dot", style={"background": "#8b5cf6"}), "Placed by hand"]),
        html.Div([html.Span(className="pm-dot", style={"background": "rgba(37,99,235,.88)",
                                                        "width": "14px", "height": "14px"}),
                  "Group of photos: click to zoom in"]),
        html.Div(f"Photo Map {__version__}", style={"marginTop": "16px"}),
    ])


def _map(pm, d):
    (south, west), (north, east) = d.bounds
    return dl.Map(
        # center/zoom are a starting point; the viewport then fits the map to the photos.
        id="map", center=[(south + north) / 2, (west + east) / 2], zoom=10,
        viewport={"bounds": d.bounds, "transition": "fitBounds"},
        maxZoom=19, trackViewport=True, style={"height": "100%"},
        children=[
            tile_layer(pm),
            dl.GeoJSON(
                id="photos",
                cluster=True, zoomToBoundsOnClick=True, spiderfyOnMaxZoom=True,
                superClusterOptions={"radius": 60, "maxZoom": 18},
                pointToLayer=js("pointToLayer"), clusterToLayer=js("clusterToLayer"),
                onEachFeature=js("onEachFeature"),
            ),
        ],
    )


def _viewer():
    return html.Div(id="viewer", className="pm-viewer", style={"display": "none"}, children=[
        html.Div(className="pm-viewer-stage", children=[
            html.Img(id="viewer-img", className="pm-viewer-img", alt="Loading photo…"),
            html.Button("‹", id="viewer-prev", className="pm-viewer-nav", title="Previous (←)"),
            html.Button("›", id="viewer-next", className="pm-viewer-nav", title="Next (→)"),
            html.Button("×", id="viewer-close", className="pm-viewer-close", title="Close (Esc)"),
        ]),
        html.Div(className="pm-viewer-bar", children=[
            html.Div(className="pm-viewer-info", children=[
                html.Div(id="viewer-meta", className="pm-viewer-meta"),
                html.Div(className="pm-viewer-tags", children=[
                    html.Div(id="viewer-tag-chips", className="pm-chips"),
                    dcc.Input(id="viewer-tag-input", className="pm-tag-input", type="text",
                              placeholder="Add a tag, press Enter", list="viewer-tag-list",
                              autoComplete="off", n_submit=0),
                    html.Datalist(id="viewer-tag-list"),
                ]),
            ]),
            html.Div(id="viewer-pos", className="pm-viewer-pos"),
            html.A("Open original ↗", id="viewer-original", target="_blank"),
        ]),
    ])


def _photo_meta(pm, photo_id: int):
    s = pm.settings()
    with sqlite3.connect(s.db_path) as conn:
        conn.row_factory = sqlite3.Row
        r = conn.execute(
            "SELECT file_name, folder, make, model, taken_local, time_source, "
            "lat, lon, manual_lat, manual_lon FROM photos WHERE id = ?", (photo_id,)
        ).fetchone()
    if r is None:
        return html.B("Photo not found")
    if r["manual_lat"] is not None:
        where = f"{r['manual_lat']:.5f}, {r['manual_lon']:.5f} (placed by hand)"
    elif r["lat"] is not None:
        where = f"{r['lat']:.5f}, {r['lon']:.5f}"
    else:
        where = "No location"
    when = mapdata.friendly_time(r["taken_local"]) + TIME_SOURCE_NOTE.get(r["time_source"], "")
    return [
        html.B(r["file_name"]),
        html.Span(when),
        html.Span(mapdata.device_name(r["make"], r["model"])),
        html.Span(mapdata.folder_label(r["folder"])),
        html.Span(where),
    ]


def register(pm):
    app = pm.app
    register_filters(pm, "m", "map")

    # Filtering runs in the browser (assets/photomap.js), before clustering.
    app.clientside_callback(
        ClientsideFunction(namespace="photomap", function_name="applyFilter"),
        Output("photos", "data"), Input("all-photos", "data"), Input("m-filters", "data"),
    )

    @app.callback(
        Output("all-photos", "data"), Output("map", "viewport"),
        Input({"type": "version", "page": "map"}, "data"), State("all-photos", "data"),
        prevent_initial_call=True,
    )
    def reload_data(_, shown):
        d = pm.data()
        # Re-fit the map only when it was empty (first scan); otherwise keep the user's view.
        was_empty = not (shown or {}).get("features")
        viewport = {"bounds": d.bounds, "transition": "fitBounds"} if was_empty and d.points else no_update
        return d.geojson(), viewport

    @app.callback(
        Output("gallery", "children"), Output("gallery-sub", "children"),
        Output("count", "children"), Output("view-order", "data"),
        Input("map", "bounds"), Input("m-filters", "data"), Input({"type": "version", "page": "map"}, "data"),
    )
    def photos_in_view(bounds, filters, _):
        d = pm.data()
        filters = filters or {}
        matching = sum(1 for p in d.points if mapdata.passes_filter(p, filters))
        visible = mapdata.view_order(d, filters, bounds)

        if matching == len(d.points):
            label = "photo on the map" if matching == 1 else "photos on the map"
        else:
            label = f"of {fmt(len(d.points))} photos match the filters"
        count = [html.Strong(fmt(matching)), label]

        thumbs = [thumb(p.id, title=mapdata.friendly_time(p.taken_local), **{"data-photo-id": str(p.id)})
                  for p in visible[:GALLERY_LIMIT]]
        if not visible:
            thumbs = [html.Div("No photos here. Zoom out or change the filters.", className="pm-gallery-note")]
        elif len(visible) > GALLERY_LIMIT:
            thumbs.append(html.Div(f"Showing the first {GALLERY_LIMIT}. Zoom in to see the rest.",
                                   className="pm-gallery-note"))
        sub = f"{fmt(len(visible))} photo{'s' if len(visible) != 1 else ''}, oldest first"
        return thumbs, sub, count, [p.id for p in visible[:ORDER_LIMIT]]

    @app.callback(
        Output("viewer-id", "data"),
        Input("open-request", "data"), Input("viewer-prev", "n_clicks"),
        Input("viewer-next", "n_clicks"), Input("viewer-close", "n_clicks"),
        State("viewer-id", "data"), State("view-order", "data"),
        prevent_initial_call=True,
    )
    def move_viewer(request, _prev, _next, _close, current, order):
        trigger = ctx.triggered_id
        if not ctx.triggered or ctx.triggered[0]["value"] in (None, 0):
            return no_update  # a button appearing, not a click
        if trigger == "open-request":
            return request["id"] if request else no_update
        if trigger == "viewer-close":
            return None
        if current is None or not order or current not in order:
            return no_update
        i = order.index(current) + (1 if trigger == "viewer-next" else -1)
        return order[i] if 0 <= i < len(order) else no_update

    @app.callback(
        Output("viewer", "style"), Output("viewer-img", "src"), Output("viewer-meta", "children"),
        Output("viewer-pos", "children"), Output("viewer-prev", "disabled"),
        Output("viewer-next", "disabled"), Output("viewer-original", "href"),
        Input("viewer-id", "data"), State("view-order", "data"),
    )
    def show_viewer(photo_id, order):
        if photo_id is None:
            return {"display": "none"}, None, None, None, True, True, None
        order = order or []
        if photo_id in order:
            i = order.index(photo_id)
            pos, has_prev, has_next = f"{fmt(i + 1)} of {fmt(len(order))} in view", i > 0, i < len(order) - 1
        else:
            pos, has_prev, has_next = "", False, False
        return ({"display": "grid"}, f"/photo/preview/{photo_id}", _photo_meta(pm, photo_id), pos,
                not has_prev, not has_next, f"/photo/original/{photo_id}")

    # ---- tags in the viewer ----

    @app.callback(
        Output("viewer-tag-chips", "children"), Output("viewer-tag-list", "children"),
        Input("viewer-id", "data"), Input("vt-refresh", "data"),
    )
    def show_viewer_tags(photo_id, _):
        if photo_id is None:
            return [], no_update
        with pm.read() as conn:
            tags = db.tags_for(conn, photo_id)
            all_tags = [t for t, _ in db.tag_counts(conn)]
        chips = [html.Span(className="pm-chip", children=[
            t, html.Button("×", id={"type": "vt-remove", "tag": t}, className="pm-chip-x", title=f"Remove “{t}”")])
            for t in tags]
        return chips, [html.Option(value=t) for t in all_tags if t not in tags]

    @app.callback(
        Output("vt-refresh", "data"), Output("viewer-tag-input", "value"),
        Input("viewer-tag-input", "n_submit"), Input({"type": "vt-remove", "tag": ALL}, "n_clicks"),
        State("viewer-tag-input", "value"), State("viewer-id", "data"), State("vt-refresh", "data"),
        prevent_initial_call=True,
    )
    def edit_viewer_tags(_submit, _removes, text, photo_id, refresh):
        if photo_id is None or not ctx.triggered or ctx.triggered[0]["value"] in (None, 0):
            return no_update, no_update
        trigger = ctx.triggered_id
        with pm.edit() as conn:
            if trigger == "viewer-tag-input":
                if not db.add_tag(conn, [photo_id], text):
                    return no_update, ""
            else:
                db.remove_tag(conn, [photo_id], trigger["tag"])
        return (refresh or 0) + 1, ""
