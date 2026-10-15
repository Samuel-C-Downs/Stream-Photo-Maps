"""The Review screen: give a location to photos that have none, or leave them off the map.

Locations chosen here are stored in the index only; photo files are never changed.
"""

from __future__ import annotations

import dash_leaflet as dl
from dash import ALL, Input, Output, State, ctx, dcc, html, no_update

from .. import db, mapdata
from .common import fmt, header, version_store
from .mapview import tile_layer

QUEUE_LIMIT = 400  # rows shown in the queue list


def layout(pm):
    queue = mapdata.review_queue(pm.settings())
    first = queue[0].id if queue else None
    return html.Div(className="pm-app", children=[
        header(pm, "review"),
        html.Div(className="pm-review", children=[
            html.Aside(className="pm-queue", children=[
                html.Div(className="pm-queue-head", children=[
                    html.Div("Photos without a location", className="pm-section-title"),
                    html.Div(id="rv-queue-sub", className="pm-gallery-sub"),
                    html.Button(id="rv-accept-all", className="pm-btn", style={"marginTop": "10px"}),
                ]),
                html.Div(id="rv-list", className="pm-queue-list"),
            ]),
            html.Section(className="pm-review-photo", children=[
                html.Div(className="pm-review-stage", children=[
                    html.Img(id="rv-img", className="pm-review-img", alt=""),
                ]),
                html.Div(id="rv-info", className="pm-review-info"),
                html.Div(id="rv-sugg", className="pm-review-sugg"),
                html.Div(className="pm-review-actions", children=[
                    html.Button("Accept suggested spot", id="rv-accept", className="pm-btn pm-btn-primary"),
                    html.Button("Place at pin", id="rv-place", className="pm-btn pm-btn-primary"),
                    html.Button("Skip: leave off the map", id="rv-skip", className="pm-btn"),
                    html.Button("Undo", id="rv-undo", className="pm-btn pm-btn-link", disabled=True),
                ]),
                html.Div(id="rv-msg", className="pm-review-msg"),
            ]),
            html.Section(className="pm-review-map", children=[
                dl.Map(id="rv-map", center=[39.7, -105.0], zoom=10, maxZoom=19,
                       style={"height": "100%"}, children=[
                           tile_layer(pm),
                           dl.LayerGroup(id="rv-context"),
                           dl.LayerGroup(id="rv-suggestion"),
                           dl.LayerGroup(id="rv-pin"),
                       ]),
                html.Div(className="pm-map-hint", children=[
                    "Click the map to drop a pin, or click a ",
                    html.Span(className="pm-dot", style={"background": "#94a3b8"}),
                    "photo from the same day to use its spot.",
                ]),
            ]),
            html.Div(id="rv-empty", className="pm-review-empty", style={"display": "none"}, children=[
                html.H2("Every photo has a location"),
                html.P("Nothing left to review. New photos without GPS will show up here after a scan."),
                dcc.Link("Back to the map", href="/", className="pm-btn pm-btn-primary"),
            ]),
        ]),
        dcc.ConfirmDialog(id="rv-confirm"),
        dcc.Store(id="rv-current", data=first),
        dcc.Store(id="rv-pin-at"),
        dcc.Store(id="rv-last"),
        dcc.Store(id="rv-refresh", data=0),
        version_store("review", pm),
    ])


def _next_after(old_ids: list[int], current: int | None, new_ids: list[int]) -> int | None:
    """After the current photo leaves the queue, move to the one that took its place."""
    if not new_ids:
        return None
    if current in new_ids:
        return current
    i = old_ids.index(current) if current in old_ids else 0
    for candidate in old_ids[i + 1:]:
        if candidate in new_ids:
            return candidate
    return new_ids[-1]


def register(pm):
    app = pm.app

    @app.callback(
        Output("rv-list", "children"), Output("rv-queue-sub", "children"),
        Output("rv-accept-all", "children"), Output("rv-accept-all", "disabled"),
        Output("rv-empty", "style"),
        Input("rv-current", "data"), Input("rv-refresh", "data"),
        Input({"type": "version", "page": "review"}, "data"),
    )
    def show_queue(current, _refresh, _version):
        queue = mapdata.review_queue(pm.settings())
        suggested = sum(1 for q in queue if q.has_suggestion)
        rows = []
        for q in queue[:QUEUE_LIMIT]:
            rows.append(html.Div(
                className="pm-queue-item is-current" if q.id == current else "pm-queue-item",
                **{"data-review-id": str(q.id)},
                children=[
                    html.Img(src=f"/photo/thumb/{q.id}", className="pm-queue-thumb"),
                    html.Div(className="pm-queue-text", children=[
                        html.Div(q.file_name, className="pm-queue-name"),
                        html.Div(mapdata.friendly_time(q.taken_local), className="pm-queue-when"),
                        html.Span("Suggested spot", className="pm-tag-badge") if q.has_suggestion else None,
                    ]),
                ]))
        if len(queue) > QUEUE_LIMIT:
            rows.append(html.Div(f"…and {fmt(len(queue) - QUEUE_LIMIT)} more", className="pm-gallery-note"))
        sub = (f"{fmt(len(queue))} to review · {fmt(suggested)} with a suggested spot"
               if queue else "All done")
        if suggested == 1:
            accept_all = "Accept the 1 suggested spot"
        else:
            accept_all = f"Accept all {fmt(suggested)} suggested spots" if suggested else "No suggested spots"
        empty = {"display": "flex"} if not queue else {"display": "none"}
        return rows, sub, accept_all, suggested == 0, empty

    @app.callback(
        Output("rv-img", "src"), Output("rv-info", "children"), Output("rv-sugg", "children"),
        Output("rv-accept", "disabled"), Output("rv-skip", "disabled"),
        Output("rv-context", "children"), Output("rv-suggestion", "children"), Output("rv-map", "viewport"),
        Input("rv-current", "data"), Input("rv-refresh", "data"),
    )
    def show_current(current, _):
        queue = {q.id: q for q in mapdata.review_queue(pm.settings())}
        item = queue.get(current)
        if item is None:
            return None, None, None, True, True, [], [], no_update
        d = pm.data()
        info = [html.B(item.file_name), html.Span(mapdata.friendly_time(item.taken_local)),
                html.Span(item.device), html.Span(mapdata.folder_label(item.folder))]

        day = mapdata.same_day(d, item.taken_local)
        context = [
            dl.CircleMarker(
                id={"type": "rv-ctx", "id": p.id}, center=[p.lat, p.lon], radius=7, weight=2,
                color="#ffffff", fillColor="#94a3b8", fillOpacity=0.95,
                children=dl.Tooltip(f"{mapdata.friendly_time(p.taken_local)} · {p.file_name}"),
            )
            for p in day[:300]
        ]

        if item.has_suggestion:
            source = next((p for p in day if p.id == item.suggested_from), None)
            sugg = [
                html.Img(src=f"/photo/thumb/{item.suggested_from}", className="pm-sugg-thumb"),
                html.Div([
                    html.B("Suggested spot (orange on the map)"),
                    html.Div(f"Taken {mapdata.friendly_gap(item.suggested_gap_s)} apart from "
                             f"{source.file_name if source else 'a photo with GPS'}, "
                             "which has a location."),
                ]),
            ]
            marker = [dl.CircleMarker(center=[item.suggested_lat, item.suggested_lon], radius=10, weight=3,
                                      color="#ffffff", fillColor="#ea580c", fillOpacity=1,
                                      children=dl.Tooltip("Suggested spot"))]
            viewport = {"center": [item.suggested_lat, item.suggested_lon], "zoom": 16, "transition": "flyTo"}
        else:
            sugg = html.Div("No suggested spot: no photo with GPS was taken within 5 minutes of this one.",
                            className="pm-note")
            marker = []
            area = day if day else d.points
            viewport = {"bounds": mapdata.bounds_of(area, pad=0.003), "transition": "flyToBounds"}
        return (f"/photo/preview/{item.id}", info, sugg, not item.has_suggestion, False,
                context, marker, viewport)

    @app.callback(
        Output("rv-pin-at", "data"),
        Input("rv-map", "n_clicks"), Input({"type": "rv-ctx", "id": ALL}, "n_clicks"), Input("rv-current", "data"),
        State("rv-map", "clickData"), prevent_initial_call=True,
    )
    def drop_pin(_map_clicks, _ctx_clicks, _current, click):
        trigger = ctx.triggered_id
        if trigger == "rv-current":
            return None  # new photo: clear the pin
        if not ctx.triggered or ctx.triggered[0]["value"] in (None, 0):
            return no_update
        if trigger == "rv-map":
            latlng = (click or {}).get("latlng")
            if isinstance(latlng, dict):
                return [latlng["lat"], latlng["lng"]]
            return list(latlng) if latlng else no_update
        point = next((p for p in pm.data().points if p.id == trigger["id"]), None)
        return [point.lat, point.lon] if point else no_update

    @app.callback(
        Output("rv-pin", "children"), Output("rv-place", "disabled"),
        Input("rv-pin-at", "data"),
    )
    def show_pin(at):
        if not at:
            return [], True
        return [dl.CircleMarker(center=at, radius=10, weight=3, color="#ffffff", fillColor="#8b5cf6",
                                fillOpacity=1, children=dl.Tooltip("Your pin"))], False

    @app.callback(
        Output("rv-confirm", "displayed"), Output("rv-confirm", "message"),
        Input("rv-accept-all", "n_clicks"), prevent_initial_call=True,
    )
    def confirm_accept_all(clicks):
        if not clicks:
            return no_update, no_update
        n = sum(1 for q in mapdata.review_queue(pm.settings()) if q.has_suggestion)
        return True, (f"Put all {n} photos with a suggested spot on the map at that spot? "
                      "You can still change any of them later.")

    @app.callback(
        Output("rv-current", "data"), Output("rv-last", "data"), Output("rv-refresh", "data"),
        Output("rv-msg", "children"), Output("rv-undo", "disabled"),
        Input("rv-accept", "n_clicks"), Input("rv-place", "n_clicks"), Input("rv-skip", "n_clicks"),
        Input("rv-undo", "n_clicks"), Input("rv-confirm", "submit_n_clicks"), Input("rv-pick", "data"),
        State("rv-current", "data"), State("rv-pin-at", "data"), State("rv-last", "data"),
        State("rv-refresh", "data"), prevent_initial_call=True,
    )
    def act(_a, _p, _s, _u, _all, pick, current, pin, last, refresh):
        trigger = ctx.triggered_id
        if not ctx.triggered or ctx.triggered[0]["value"] in (None, 0):
            return (no_update,) * 5
        if trigger == "rv-pick":
            return (pick or {}).get("id", no_update), no_update, no_update, "", no_update

        before = [q.id for q in mapdata.review_queue(pm.settings())]
        names = {q.id: q.file_name for q in mapdata.review_queue(pm.settings())}
        with pm.edit() as conn:
            if trigger == "rv-accept" and current is not None:
                db.accept_suggestion(conn, current)
                msg, last = f"{names.get(current, 'Photo')} placed at the suggested spot.", current
            elif trigger == "rv-place" and current is not None and pin:
                db.set_manual_location(conn, current, pin[0], pin[1])
                msg, last = f"{names.get(current, 'Photo')} placed at your pin.", current
            elif trigger == "rv-skip" and current is not None:
                db.skip_review(conn, current)
                msg, last = f"{names.get(current, 'Photo')} left off the map.", current
            elif trigger == "rv-undo" and last is not None:
                db.reset_review(conn, last)
                return last, None, (refresh or 0) + 1, "Undone.", True
            elif trigger == "rv-confirm":
                n = db.accept_all_suggestions(conn)
                msg, last = f"Placed {fmt(n)} photos at their suggested spots.", None
            else:
                return (no_update,) * 5
        after = [q.id for q in mapdata.review_queue(pm.settings())]
        return _next_after(before, current, after), last, (refresh or 0) + 1, msg, last is None
