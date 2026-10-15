/* Browser-side helpers for the photo map.
 * Functions under window.photomap are referenced from Python as {"variable": "photomap.<name>"}.
 */

function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, function (c) {
        return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c];
    });
}

/* Filter rules: must match passes_filter() in mapdata.py. */
function photomapPasses(p, f) {
    if (p.d === null || p.d === undefined) {
        if (f.undated === false) return false;
    } else {
        if (f.start !== null && f.start !== undefined && p.d < f.start) return false;
        if (f.end !== null && f.end !== undefined && p.d > f.end) return false;
    }
    if (f.folders && f.folders.length && f.folders.indexOf(p.f) === -1) return false;
    if (f.devices && f.devices.length && f.devices.indexOf(p.v) === -1) return false;
    if (f.tags && f.tags.length) {  /* any chosen tag; -1 matches photos without tags */
        const tags = p.t || [];
        const hit = tags.some(function (t) { return f.tags.indexOf(t) !== -1; })
            || (f.tags.indexOf(-1) !== -1 && tags.length === 0);
        if (!hit) return false;
    }
    return true;
}

/* Runs in the browser whenever the filters change: the map gets only matching photos,
 * so clusters are built from (and count) just those. */
window.dash_clientside = Object.assign({}, window.dash_clientside, {
    photomap: {
        applyFilter: function (all, filters) {
            if (!all) return window.dash_clientside.no_update;
            const f = filters || {};
            return {
                type: "FeatureCollection",
                features: all.features.filter(function (ft) { return photomapPasses(ft.properties, f); }),
            };
        },
    },
});

window.photomap = {
    pointToLayer: function (feature, latlng) {
        const manual = feature.properties.m === 1;
        return L.circleMarker(latlng, {
            radius: 7, weight: 2, color: "#ffffff", opacity: 1,
            fillColor: manual ? "#8b5cf6" : "#2563eb", fillOpacity: 0.95,
        });
    },

    clusterToLayer: function (feature, latlng) {
        const n = feature.properties.point_count;
        const size = n < 10 ? 32 : n < 100 ? 38 : n < 1000 ? 46 : 54;
        const icon = L.divIcon({
            html: "<span>" + feature.properties.point_count_abbreviated + "</span>",
            className: "pm-cluster",
            iconSize: L.point(size, size),
        });
        return L.marker(latlng, {icon: icon});
    },

    onEachFeature: function (feature, layer) {
        const p = feature.properties;
        if (p.cluster) return;
        const more = p.n > 1
            ? '<div class="pm-popup-more">' + p.n + " photos at this spot · zoom in or see the list</div>"
            : "";
        layer.bindPopup(
            '<div class="pm-popup">' +
            '<img class="pm-popup-img" src="/photo/thumb/' + p.id + '" data-photo-id="' + p.id + '" alt="">' +
            '<div class="pm-popup-when">' + p.w + "</div>" +
            '<div class="pm-popup-dev">' + escapeHtml(p.dev) + (p.m === 1 ? " · placed by hand" : "") + "</div>" +
            (p.tg ? '<div class="pm-popup-tags">' + escapeHtml(p.tg) + "</div>" : "") +
            more +
            '<button class="pm-btn pm-btn-primary pm-popup-open" data-photo-id="' + p.id + '">View photo</button>' +
            "</div>",
            {minWidth: 220, maxWidth: 260}
        );
    },
};

/* Tags page: clicking a tile selects it (shift-click selects a range). */
document.addEventListener("click", function (e) {
    const el = e.target.closest("[data-select-id]");
    if (!el || !window.dash_clientside || !window.dash_clientside.set_props) return;
    e.preventDefault();
    window.dash_clientside.set_props("tg-click", {
        data: {id: parseInt(el.getAttribute("data-select-id"), 10), shift: e.shiftKey, t: Date.now()},
    });
});

/* Review page: clicking a photo in the queue makes it the current one. */
document.addEventListener("click", function (e) {
    const el = e.target.closest("[data-review-id]");
    if (!el || !window.dash_clientside || !window.dash_clientside.set_props) return;
    e.preventDefault();
    window.dash_clientside.set_props("rv-pick", {
        data: {id: parseInt(el.getAttribute("data-review-id"), 10), t: Date.now()},
    });
});

/* Anything with data-photo-id (popup image or button, gallery thumbnail) opens the viewer. */
document.addEventListener("click", function (e) {
    const el = e.target.closest("[data-photo-id]");
    if (!el || !window.dash_clientside || !window.dash_clientside.set_props) return;
    e.preventDefault();
    window.dash_clientside.set_props("open-request", {
        data: {id: parseInt(el.getAttribute("data-photo-id"), 10), t: Date.now()},
    });
});

/* Keyboard in the viewer: left/right arrows to move, Escape to close (not while typing a tag). */
document.addEventListener("keydown", function (e) {
    const viewer = document.getElementById("viewer");
    if (!viewer || viewer.style.display === "none") return;
    if (e.target && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA")) return;
    const ids = {ArrowLeft: "viewer-prev", ArrowRight: "viewer-next", Escape: "viewer-close"};
    const target = ids[e.key] && document.getElementById(ids[e.key]);
    if (target && !target.disabled) {
        e.preventDefault();
        target.click();
    }
});
