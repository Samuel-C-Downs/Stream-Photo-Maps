"""The Tags screen: pick photos (by filter, click or shift-click) and add or remove tags in bulk.

Also renames and deletes tags. Tags are stored in the index only, never in photo files.
"""

from __future__ import annotations

from dash import Input, Output, State, ctx, dcc, html, no_update

from .. import db, mapdata
from .common import fmt, filter_controls, header, register_filters, thumb, version_store

PAGE_SIZE = 120


def layout(pm):
    d = pm.data()
    return html.Div(className="pm-app", children=[
        header(pm, "tags"),
        html.Div(className="pm-body pm-body-tags", children=[
            html.Aside(className="pm-sidebar", children=[
                html.Div(id="tg-count", className="pm-count"),
                filter_controls(d, "t"),
                html.Div(className="pm-field pm-manage", children=[
                    html.Div("Manage tags", className="pm-section-title"),
                    html.Div(id="tg-tag-list", className="pm-tag-list"),
                    dcc.Dropdown(id="tg-manage", placeholder="Choose a tag…", clearable=True),
                    html.Div(className="pm-inline", children=[
                        dcc.Input(id="tg-rename-to", className="pm-input", type="text",
                                  placeholder="New name", autoComplete="off"),
                        html.Button("Rename", id="tg-rename", className="pm-btn"),
                    ]),
                    html.Button("Delete tag…", id="tg-delete", className="pm-btn pm-btn-danger"),
                ]),
            ]),
            html.Main(className="pm-tags-main", children=[
                html.Div(className="pm-actionbar", children=[
                    html.Div(className="pm-actionbar-row", children=[
                        html.Div(id="tg-sel-text", className="pm-sel-text"),
                        html.Button("Select all matching", id="tg-select-all", className="pm-btn"),
                        html.Button("Select this page", id="tg-select-page", className="pm-btn"),
                        html.Button("Clear selection", id="tg-clear-sel", className="pm-btn"),
                    ]),
                    html.Div(className="pm-actionbar-row", children=[
                        dcc.Input(id="tg-add-input", className="pm-input", type="text", list="tg-datalist",
                                  placeholder="Tag to add, e.g. Project 1234", autoComplete="off", n_submit=0),
                        html.Datalist(id="tg-datalist"),
                        html.Button("Add to selected", id="tg-add", className="pm-btn pm-btn-primary"),
                        html.Div(className="pm-actionbar-gap"),
                        dcc.Dropdown(id="tg-remove-tag", placeholder="Tag on selected photos…",
                                     className="pm-remove-dd", clearable=True),
                        html.Button("Remove from selected", id="tg-remove", className="pm-btn"),
                    ]),
                    html.Div(id="tg-msg", className="pm-review-msg"),
                ]),
                html.Div(id="tg-grid", className="pm-tile-grid"),
                html.Div(className="pm-pager", children=[
                    html.Button("‹ Previous", id="tg-prev", className="pm-btn"),
                    html.Span(id="tg-page-text"),
                    html.Button("Next ›", id="tg-next", className="pm-btn"),
                ]),
                html.Div("Click a photo to select it; shift-click to select a range. "
                         "Only photos on the map are listed here.", className="pm-note pm-tags-help"),
            ]),
        ]),
        dcc.ConfirmDialog(id="tg-delete-confirm"),
        dcc.Store(id="tg-page", data=0),
        dcc.Store(id="tg-selected", data=[]),
        dcc.Store(id="tg-last"),
        dcc.Store(id="tg-refresh", data=0),
        version_store("tags", pm),
    ])


def _matching(pm, filters):
    """Photos matching the filters, oldest first (the same order as the map's side panel)."""
    return mapdata.view_order(pm.data(), filters or {}, None)


def register(pm):
    app = pm.app
    register_filters(pm, "t", "tags")

    @app.callback(
        Output("tg-page", "data"),
        Input("t-filters", "data"), Input("tg-prev", "n_clicks"), Input("tg-next", "n_clicks"),
        State("tg-page", "data"), prevent_initial_call=True,
    )
    def change_page(filters, _prev, _next, page):
        trigger = ctx.triggered_id
        if trigger == "t-filters":
            return 0
        if not ctx.triggered or ctx.triggered[0]["value"] in (None, 0):
            return no_update
        pages = max(1, -(-len(_matching(pm, filters)) // PAGE_SIZE))
        page = (page or 0) + (1 if trigger == "tg-next" else -1)
        return min(max(page, 0), pages - 1)

    @app.callback(
        Output("tg-grid", "children"), Output("tg-page-text", "children"),
        Output("tg-prev", "disabled"), Output("tg-next", "disabled"),
        Output("tg-sel-text", "children"), Output("tg-count", "children"),
        Input("t-filters", "data"), Input("tg-page", "data"), Input("tg-selected", "data"),
        Input("tg-refresh", "data"), Input({"type": "version", "page": "tags"}, "data"),
    )
    def show_grid(filters, page, selected, _refresh, _version):
        d = pm.data()
        matching = _matching(pm, filters)
        pages = max(1, -(-len(matching) // PAGE_SIZE))
        page = min(page or 0, pages - 1)
        chosen = set(selected or [])
        tiles = []
        for p in matching[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]:
            names = [d.tags[t] for t in p.tags]
            tiles.append(html.Div(
                className="pm-tile is-selected" if p.id in chosen else "pm-tile",
                title=f"{p.file_name}\n{mapdata.friendly_time(p.taken_local)}"
                      + (f"\nTags: {', '.join(names)}" if names else ""),
                **{"data-select-id": str(p.id)},
                children=[
                    thumb(p.id, className="pm-tile-img"),
                    html.Span("✓", className="pm-tile-check"),
                    html.Div(" · ".join(names), className="pm-tile-tags") if names else None,
                ]))
        if not tiles:
            tiles = [html.Div("No photos match these filters.", className="pm-gallery-note")]

        hidden = len(chosen - {p.id for p in matching})
        sel_text = [html.Strong(fmt(len(chosen))), " selected"]
        if hidden:
            sel_text.append(html.Span(f" ({fmt(hidden)} hidden by the filters)", className="pm-note"))
        total = len(d.points)
        count = [html.Strong(fmt(len(matching))),
                 "photos on the map" if len(matching) == total else f"of {fmt(total)} photos match the filters"]
        return (tiles, f"Page {page + 1} of {pages}", page == 0, page >= pages - 1, sel_text, count)

    @app.callback(
        Output("tg-selected", "data"), Output("tg-last", "data"),
        Input("tg-click", "data"), Input("tg-select-all", "n_clicks"),
        Input("tg-select-page", "n_clicks"), Input("tg-clear-sel", "n_clicks"),
        State("tg-selected", "data"), State("tg-last", "data"),
        State("t-filters", "data"), State("tg-page", "data"), prevent_initial_call=True,
    )
    def select(click, _all, _page, _clear, selected, last, filters, page):
        trigger = ctx.triggered_id
        if not ctx.triggered or ctx.triggered[0]["value"] in (None, 0):
            return no_update, no_update
        matching = [p.id for p in _matching(pm, filters)]
        chosen = list(selected or [])
        if trigger == "tg-clear-sel":
            return [], None
        if trigger == "tg-select-all":
            return sorted(set(chosen) | set(matching)), last
        if trigger == "tg-select-page":
            page_ids = matching[(page or 0) * PAGE_SIZE:((page or 0) + 1) * PAGE_SIZE]
            return sorted(set(chosen) | set(page_ids)), last

        photo_id = (click or {}).get("id")
        if photo_id is None:
            return no_update, no_update
        if click.get("shift") and last in matching and photo_id in matching:
            a, b = sorted((matching.index(last), matching.index(photo_id)))
            return sorted(set(chosen) | set(matching[a:b + 1])), photo_id
        if photo_id in chosen:
            chosen.remove(photo_id)
        else:
            chosen.append(photo_id)
        return chosen, photo_id

    @app.callback(
        Output("tg-remove-tag", "options"), Output("tg-remove-tag", "value"),
        Input("tg-selected", "data"), Input("tg-refresh", "data"),
    )
    def removable_tags(selected, _):
        d = pm.data()
        chosen = set(selected or [])
        counts = {}
        for p in d.points:
            if p.id in chosen:
                for t in p.tags:
                    counts[d.tags[t]] = counts.get(d.tags[t], 0) + 1
        return [{"label": f"{t} ({fmt(n)})", "value": t} for t, n in sorted(counts.items(), key=lambda x: x[0].lower())], None

    @app.callback(
        Output("tg-tag-list", "children"), Output("tg-manage", "options"), Output("tg-datalist", "children"),
        Input("tg-refresh", "data"), Input({"type": "version", "page": "tags"}, "data"),
    )
    def show_tags(_refresh, _version):
        with pm.read() as conn:
            counts = db.tag_counts(conn)
        if counts:
            listing = [html.Div(className="pm-tag-row", children=[html.Span(t), html.Span(fmt(n), className="pm-note")])
                       for t, n in counts]
        else:
            listing = [html.Div("No tags yet. Select photos and add one.", className="pm-note")]
        return (listing, [{"label": t, "value": t} for t, _ in counts],
                [html.Option(value=t) for t, _ in counts])

    @app.callback(
        Output("tg-delete-confirm", "displayed"), Output("tg-delete-confirm", "message"),
        Input("tg-delete", "n_clicks"), State("tg-manage", "value"), prevent_initial_call=True,
    )
    def confirm_delete(clicks, tag):
        if not clicks or not tag:
            return no_update, no_update
        with pm.read() as conn:
            n = dict(db.tag_counts(conn)).get(tag, 0)
        return True, f"Remove the tag “{tag}” from all {n} photos? The photos themselves are not affected."

    @app.callback(
        Output("tg-refresh", "data"), Output("tg-msg", "children"),
        Output("tg-add-input", "value"), Output("tg-rename-to", "value"), Output("tg-manage", "value"),
        Input("tg-add", "n_clicks"), Input("tg-add-input", "n_submit"), Input("tg-remove", "n_clicks"),
        Input("tg-rename", "n_clicks"), Input("tg-delete-confirm", "submit_n_clicks"),
        State("tg-add-input", "value"), State("tg-remove-tag", "value"), State("tg-selected", "data"),
        State("tg-manage", "value"), State("tg-rename-to", "value"), State("tg-refresh", "data"),
        prevent_initial_call=True,
    )
    def edit(_add, _submit, _remove, _rename, _delete, add_text, remove_tag, selected, manage, rename_to, refresh):
        trigger = ctx.triggered_id
        nothing = (no_update,) * 5
        if not ctx.triggered or ctx.triggered[0]["value"] in (None, 0):
            return nothing
        selected = selected or []
        bump = (refresh or 0) + 1
        if trigger in ("tg-add", "tg-add-input"):
            if not selected:
                return no_update, "Select some photos first.", no_update, no_update, no_update
            with pm.edit() as conn:
                tag = db.add_tag(conn, selected, add_text)
            if not tag:
                return no_update, "Type a tag to add.", no_update, no_update, no_update
            return bump, f"Added “{tag}” to {fmt(len(selected))} photos.", "", no_update, no_update
        if trigger == "tg-remove":
            if not selected or not remove_tag:
                return no_update, "Choose a tag on the selected photos to remove.", no_update, no_update, no_update
            with pm.edit() as conn:
                db.remove_tag(conn, selected, remove_tag)
            return bump, f"Removed “{remove_tag}” from the selected photos.", no_update, no_update, no_update
        if trigger == "tg-rename":
            if not manage:
                return no_update, "Choose a tag to rename.", no_update, no_update, no_update
            with pm.edit() as conn:
                new = db.rename_tag(conn, manage, rename_to)
            if not new:
                return no_update, "Type a new name for the tag.", no_update, no_update, no_update
            return bump, f"Renamed “{manage}” to “{new}”.", no_update, "", None
        if trigger == "tg-delete-confirm" and manage:
            with pm.edit() as conn:
                n = db.delete_tag(conn, manage)
            return bump, f"Deleted “{manage}” from {fmt(n)} photos.", no_update, no_update, None
        return nothing
