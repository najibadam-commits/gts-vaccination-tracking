"""Post-Implementation Report — the Settlement Coverage Example figures.

The sample deck's two "Settlement Coverage Examples" slides show four single
settlements, one per coverage class, so a stakeholder can see what "Partially
Covered" actually looks like on the ground: the settlement extent, the target-
area grid cells inside it, which of those cells the teams' tracks reached, and
the track points themselves.

In the sample those four pictures are satellite screenshots taken by hand in
QGIS — and they are from a different state entirely (Gombe), reused as generic
illustrations. This module produces the same four pictures from THIS campaign's
own data instead, so the examples are real settlements from the report's own
state. There is no satellite base: the pipeline fetches no map tiles, and a
report that fails on an offline machine is worse than one drawn on a plain
ground.

Everything is read from artifacts the pipeline already produced —
`settlement_extents_gdf` and `gridded_ta_gdf` from stage 4, stage 2's gridded
target-area output, and the merged track export — so nothing here re-derives
geometry that another stage already computed.

Selection is deterministic. For each class, the settlement charted is the one
whose grid-cell count is closest to the median for that class, ties broken by
the settlement key. Picking the median rather than the largest keeps the
examples representative rather than showing the four biggest settlements in the
state; picking it by a stated rule rather than at random means two runs on the
same data produce the same deck.
"""
from __future__ import annotations

import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline_fonts import apply_matplotlib_defaults

apply_matplotlib_defaults()

# Sample symbology.
EXTENT_EDGE = "#3fb3e8"       # settlement extent outline (cyan)
CELL_EDGE = "#ffd400"         # target-area grid cell outline (yellow)
CELL_VISITED = "#5aa02c"      # visited cell fill
CELL_VISITED_ALPHA = 0.45
TRACK_C = "#f08de0"           # track points (pink)
GROUND = "#eceee8"            # plain ground in place of satellite imagery
LABEL_C = "#ffffff"
DPI = 150

# The four example panels, in the sample's order: slide 21 carries the first
# two, slide 22 the last two. `classes` lists the coverage classes that qualify
# for the panel — "Low Coverage" pools Low and Very Low, as the sample's
# "Low Coverage" panel does.
EXAMPLE_PANELS = [
    {"key": "fully", "title": "Fully Covered", "classes": ("Fully Covered",)},
    {"key": "partially", "title": "Partially Covered",
     "classes": ("Partially Covered",)},
    {"key": "low", "title": "Low Coverage",
     "classes": ("Low Coverage", "Very Low Coverage")},
    {"key": "not_visited", "title": "Not Visited", "classes": ("No Coverage",)},
]


def _pick(candidates: pd.DataFrame, cells_by_unique: pd.Series) -> str | None:
    """The settlement key closest to the median grid-cell count."""
    if candidates.empty:
        return None
    counts = candidates["unique"].map(cells_by_unique).fillna(0)
    usable = candidates[counts > 0]
    if usable.empty:
        # No grid cells at all for this class (a not-visited settlement with no
        # target area). Still worth drawing — the extent and the absence of
        # cells IS the picture — so fall back to the first key by name.
        return sorted(candidates["unique"].astype(str))[0]
    counts = usable["unique"].map(cells_by_unique)
    target = counts.median()
    order = sorted(zip((abs(counts - target)).tolist(),
                       usable["unique"].astype(str).tolist()))
    return order[0][1]


def _track_points(path: str, extents):
    """Track pings inside the settlement extents, read memory-safely.

    A spatial file is small enough to read whole. A merged-tracks CSV is not —
    it is the pipeline's largest artifact — so it is streamed in chunks and
    filtered to the extents' overall bounding box before anything is built into
    geometry. Column names are resolved through `track_columns`, the same
    resolver stage 2 uses, so an export that renames its latitude column is
    handled here exactly as it is everywhere else.
    """
    import geopandas as gpd
    import pandas as _pd
    from stage4_maps import read_spatial

    if str(path).lower().endswith((".parquet", ".gpkg", ".geojson", ".sqlite")):
        return read_spatial(path)

    from track_columns import resolve, read_columns, valid_mask
    cols = resolve(read_columns(path))
    lat_col, lon_col = cols.get("lat"), cols.get("lon")
    if not lat_col or not lon_col:
        print("  coverage examples: the track export has no latitude/longitude "
              "columns — track points omitted")
        return None

    minx, miny, maxx, maxy = extents.total_bounds
    pad = 0.01
    keep = []
    usecols = [c for c in (lat_col, lon_col, cols.get("valid")) if c]
    for chunk in _pd.read_csv(path, usecols=usecols, chunksize=500_000,
                              low_memory=False):
        lat = _pd.to_numeric(chunk[lat_col], errors="coerce")
        lon = _pd.to_numeric(chunk[lon_col], errors="coerce")
        mask = (lat.between(miny - pad, maxy + pad)
                & lon.between(minx - pad, maxx + pad)
                & ~((lat == 0) & (lon == 0)))
        if cols.get("valid"):
            mask &= valid_mask(chunk[cols["valid"]])
        if mask.any():
            keep.append(_pd.DataFrame({"lat": lat[mask], "lon": lon[mask]}))
    if not keep:
        return None
    pts = _pd.concat(keep, ignore_index=True).drop_duplicates()
    return gpd.GeoDataFrame(pts, geometry=gpd.points_from_xy(pts["lon"], pts["lat"]),
                            crs=getattr(extents, "crs", "EPSG:4326"))


def _render(out_png: str, extent, cells, tracks, meta: dict) -> str:
    fig, ax = plt.subplots(figsize=(6.2, 5.6))
    ax.set_facecolor(GROUND)
    fig.patch.set_facecolor("white")

    if cells is not None and len(cells):
        visited = cells[cells["status"] == "Visited"]
        if len(visited):
            visited.plot(ax=ax, facecolor=CELL_VISITED, edgecolor=CELL_EDGE,
                         linewidth=1.6, alpha=CELL_VISITED_ALPHA, zorder=2)
        rest = cells[cells["status"] != "Visited"]
        if len(rest):
            rest.plot(ax=ax, facecolor="none", edgecolor=CELL_EDGE,
                      linewidth=1.6, zorder=2)
    if extent is not None and len(extent):
        extent.boundary.plot(ax=ax, color=EXTENT_EDGE, linewidth=2.4, zorder=3)
    if tracks is not None and len(tracks):
        tracks.plot(ax=ax, color=TRACK_C, markersize=9, linewidth=0, zorder=4)

    # A margin around the drawn geometry. Without it the extent fills the frame
    # edge to edge, the identity callout runs off the right side and the
    # coverage banner prints over the settlement.
    if extent is not None and len(extent):
        minx, miny, maxx, maxy = extent.total_bounds
        pad_x = max((maxx - minx) * 0.16, 1e-4)
        pad_y = max((maxy - miny) * 0.10, 1e-4)
        ax.set_xlim(minx - pad_x, maxx + pad_x)
        ax.set_ylim(miny - pad_y, maxy + pad_y)

    # settlement identity, placed at the extent centroid like the sample's
    # label callout — flipped to the left of the dot when the centroid sits in
    # the right half, so the text stays inside the frame either way
    if extent is not None and len(extent):
        c = extent.geometry.union_all().centroid
        ax.plot([c.x], [c.y], marker="o", color=EXTENT_EDGE, markersize=6,
                zorder=5)
        label = "\n".join([meta["settlement"], f"Ward: {meta['ward']}",
                           f"LGA: {meta['lga']}", meta["state"]])
        x0, x1 = ax.get_xlim()
        right_half = (c.x - x0) / max(x1 - x0, 1e-9) > 0.55
        ax.annotate(label, (c.x, c.y),
                    xytext=(-9 if right_half else 9, 2),
                    textcoords="offset points", fontsize=9.5, weight="bold",
                    color=LABEL_C, va="center",
                    ha="right" if right_half else "left", zorder=6,
                    path_effects=[pe.withStroke(linewidth=2.6, foreground="#333333")])

    # the rotated coverage banner down the left edge, as in the sample
    ax.text(0.018, 0.5, f"COVERAGE%: {meta['coverage_pct']:.1f} "
                        f"({meta['class'].upper()})",
            transform=ax.transAxes, rotation=90, ha="left", va="center",
            fontsize=11, weight="bold", color="#0b6ea8", zorder=6,
            path_effects=[pe.withStroke(linewidth=3.0, foreground="#a9e0f7")])

    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    for side in ax.spines.values():
        side.set_color("#333333")
        side.set_linewidth(1.2)
    fig.tight_layout(pad=0.4)
    os.makedirs(os.path.dirname(os.path.abspath(out_png)), exist_ok=True)
    fig.savefig(out_png, dpi=DPI, facecolor="white")
    plt.close(fig)
    return out_png


def build_examples(prepared: pd.DataFrame, output_folder: str,
                   voronoi_file: str | None, gridded_ta_file: str | None,
                   visitation_csv: str, cum_col: str,
                   tracks_file: str | None = None,
                   state_name: str = "") -> dict:
    """One PNG per coverage class. Returns {panel key: png path}.

    Missing inputs are not an error: without the settlement extents there is
    nothing to draw and an empty dict comes back, which stage 7 reads as "leave
    the example slides as editable placeholders". Producing a wrong picture is
    worse than producing none.
    """
    from stage4_maps import settlement_extents_gdf, gridded_ta_gdf

    extents = settlement_extents_gdf(voronoi_file, visitation_csv, cum_col)
    if extents is None or not len(extents):
        print("  coverage examples skipped — no settlement extents available")
        return {}
    grids = gridded_ta_gdf(gridded_ta_file)

    if "unique" not in prepared.columns:
        print("  coverage examples skipped — the settlement list has no "
              "`unique` key to join the extents on")
        return {}

    cells_by_unique = pd.Series(dtype=float)
    if grids is not None and "unique" in grids.columns:
        cells_by_unique = grids.groupby("unique").size()

    # Track points, if a track export was supplied. Only the pings inside the
    # four chosen settlements are wanted, so a CSV export is read in chunks and
    # filtered to their bounding boxes — a campaign's merged tracks run to
    # millions of rows and loading them whole to draw four thumbnails would
    # cost more than the rest of the report put together.
    tracks = None
    if tracks_file and os.path.exists(tracks_file):
        try:
            tracks = _track_points(tracks_file, extents)
        except Exception as exc:
            print(f"  coverage examples: track points unavailable ({exc})")

    coverage = pd.to_numeric(prepared.get("Coverage"), errors="coerce")
    settlement_col = next((c for c in prepared.columns
                           if c.lower().startswith("settlement")), None)

    out: dict[str, str] = {}
    for panel in EXAMPLE_PANELS:
        pool = prepared[prepared["_class"].isin(panel["classes"])]
        key = _pick(pool, cells_by_unique)
        if key is None:
            print(f"  coverage example '{panel['title']}': no settlement in "
                  f"this class — panel skipped")
            continue
        row = pool[pool["unique"].astype(str) == key].iloc[0]
        extent = extents[extents["unique"].astype(str) == key]
        if not len(extent):
            print(f"  coverage example '{panel['title']}': no extent polygon "
                  f"for {key} — panel skipped")
            continue
        cells = (grids[grids["unique"].astype(str) == key]
                 if grids is not None and "unique" in grids.columns else None)
        pts = None
        if tracks is not None and len(tracks):
            try:
                pts = tracks[tracks.geometry.within(extent.geometry.union_all())]
            except Exception:
                pts = None
        cov = coverage.get(row.name)
        meta = {
            "settlement": str(row.get(settlement_col, key)) if settlement_col else str(key),
            "ward": str(row.get("_ward", "")),
            "lga": str(row.get("_lga", "")),
            "state": state_name,
            "class": panel["title"],
            "coverage_pct": (float(cov) * 100 if pd.notna(cov) else 0.0),
        }
        png = os.path.join(output_folder, f"coverage_example_{panel['key']}.png")
        out[panel["key"]] = _render(png, extent, cells, pts, meta)
        print(f"  coverage example '{panel['title']}': {meta['settlement']} "
              f"({meta['lga']}) at {meta['coverage_pct']:.1f}%")
    return out
