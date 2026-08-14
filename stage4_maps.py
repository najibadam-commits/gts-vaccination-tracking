"""Stage 4 — Daily visitation maps, rendered from the QGIS layout templates.

Layout is not hardcoded here. `qpt_layout.py` parses the QGIS print layout and
this module renders from the resulting `LayoutSpec`, so editing a template in
QGIS and dropping it back in changes the generated maps with no code change.
`.qpt`, `.qgs` and `.qgz` are all accepted. Templates are discovered by
filename:

    role             matches filenames containing        template shipped
    ---------------  ----------------------------------  ---------------------------
    lga              'lga' + 'coverage'|'visitation'     UPDATED LGA GTS Coverage
                                                         Map Template Atlas SBSs.qgz
    statewide        'state' + 'cumulative'|'cummulative' state cummulative map.qpt
    implementation   'implementation'                    Implementation map.qpt

Side-by-side LGA maps
---------------------
The LGA template is a two-map A0 landscape page and each LGA is rendered as one
image holding both panels:

    left   Visitation Status   — settlement extents (voronoi), filled by whether
                                 the settlement was visited at all
    right  Visitation Coverage — gridded target-area cells, filled by whether
                                 each cell was visited, with the settlement
                                 extents outlined over them for context

Both panels share one extent so features line up across the page, and each
carries its own title, legend(s) and north arrow taken from the template.

This needs polygon inputs the earlier point maps did not: the voronoi settlement
extents (a pipeline input) and the per-cell gridded TA that stage 2 writes as
`gridded_ta_day_{N}.parquet`. When either is missing — or when the discovered
LGA template has only one map frame — this module falls back to the previous
single-panel settlement-point map, so an older template or a partial run still
produces maps.

What comes from the template: page size and orientation, print resolution, map
frame rects, legend positions/fonts/entry labels/columns, scale bar position and
units, north arrow positions and colours, logo positions, atlas margin, and the
per-panel title labels.

What does NOT come from the template: layer symbology. A print layout carries
placement only — the visited/not-visited fills and the LGA/ward outline styling
live in the project's layer definitions, so they remain constants below. The
values here are read off the supplied project's own layer styles.

If a template for a role is missing or unparseable, that role falls back to the
previous built-in layout (A3 landscape, no scale bar) and a note is printed, so
a bad template never takes a run down.
"""
import argparse
import os
import re
import textwrap

import matplotlib
matplotlib.use("Agg")
import matplotlib.patheffects
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Polygon, Rectangle
import pandas as pd
import geopandas as gpd

from pipeline_fonts import apply_matplotlib_defaults, resolve_family
from qpt_layout import LayoutSpec, Rect, choose_scalebar_units, load_templates

# Point matplotlib's defaults at a font that exists here, before any figure is
# created. This covers every text object that does not carry an explicit family
# — axis and tick labels, figure titles, legend text — so the policy holds for
# text the QGIS templates never described.
_MAP_FONT = apply_matplotlib_defaults()

# --------------------------------------------------------------------------- #
# Layer symbology — NOT carried by a print layout (see module docstring).
# --------------------------------------------------------------------------- #
VISITED_COLOR = "#1a9641"
NOT_VISITED_COLOR = "#c43c39"      # template not_visited red
BOUNDARY_COLOR = "#887f6c"         # template LGA outline
LGA_FILL = "#f2efe9"
WARD_COLOR = "#65406d"             # template ward outline (purple)
STATE_OUTLINE = "#a48227"
IMPLEMENTING_FILL = "#f3e3e3"      # implementing LGAs (template pink)
FOCAL_FILL = "#ffffff"
ADJOINING_FILL = "#d9d9d9"

# --------------------------------------------------------------------------- #
# Side-by-side LGA panels — colours taken from the LGA project's layer styles.
# --------------------------------------------------------------------------- #
EXTENT_VISITED_FILL = "#33a02c"        # settlement extent, visited
EXTENT_NOT_VISITED_FILL = "#ea9999"    # settlement extent, not visited
GRID_VISITED_FILL = "#33a02c"          # gridded TA cell, visited
GRID_NOT_VISITED_FILL = "#ea9999"      # gridded TA cell, not visited
# On the coverage panel the extents are drawn as outlines over the cells
EXTENT_VISITED_OUTLINE = "#33a02c"
EXTENT_NOT_VISITED_OUTLINE = "#fb9a99"
# The focal (Atlas) LGA is the ONLY one lightened: white fill, heavy black
# outline. Every other LGA on the page — whether or not it is part of the
# campaign — gets the identical flat grey, so nothing but the target LGA is
# visually emphasised. The grey also masks any settlement or grid geometry
# spilling past the focal boundary, which is why it is drawn above the data.
DUAL_FOCAL_FILL = "#ffffff"
DUAL_FOCAL_OUTLINE = "#000000"
DUAL_ADJOINING_FILL = "#d0d0ce"
DUAL_ADJOINING_OUTLINE = "#000000"
DUAL_ADJOINING_LABEL = "#4a4a4a"       # adjoining LGA names, for geographic context
DUAL_WARD_OUTLINE = "#000000"          # ward boundaries, dotted black

# Padding around the focal LGA, as a fraction of its extent. The LGA template's
# own atlas margin is 0.02, which crops to the LGA itself and leaves no
# surrounding context — deliberately overridden here so the adjoining LGAs and
# their labels are actually on the page.
LGA_CONTEXT_MARGIN = 0.14

# An adjoining LGA is labelled only if this much of the drawn view is actually
# filled by it; below that the label has nowhere to sit without colliding.
MIN_ADJOINING_LABEL_AREA = 0.006
# Stroke width for outline-only legend swatches. The swatch is much larger than
# the feature it stands for, so the map's hairline reads as an empty box here.
LEGEND_OUTLINE_WIDTH = 2.2

# Legend compression on the side-by-side LGA pages. The template's own legend
# geometry is generous — 10mm box spacing at A0 — which leaves the legends
# taking more of the page than the information in them warrants. These scale
# the padding down and nudge the block lower, without touching the entry
# wording, order, colours or font sizes: readability is unchanged, only the
# whitespace around it. Set LEGEND_COMPRESSION to 1.0 for the template's own
# spacing.
LEGEND_COMPRESSION = 0.45          # multiplies the template's box padding
LEGEND_LABEL_SPACING = 0.28        # vertical gap between entries, font-size units
LEGEND_COLUMN_SPACING = 1.0        # horizontal gap between columns
LEGEND_HANDLE_TEXT_PAD = 0.5       # gap between a swatch and its label
# Shift down the page, in mm. Positive moves the legend away from the map.
LEGEND_SHIFT_DOWN_MM = 10.0

# Draw order on the side-by-side panels. Adjoining LGAs sit *above* the data so
# they mask anything spilling past the focal LGA, exactly as the project does.
Z_FOCAL_FILL = 1       # white base under the focal LGA — below the data, not over it
Z_DATA = 2
Z_EXTENT_OUTLINE = 3
Z_ADJOINING = 4
Z_WARD = 5
Z_FOCAL = 6
Z_LABEL = 7

# --------------------------------------------------------------------------- #
# Two pieces of information the templates do not carry.
#
# None of the three templates contains a label item (QGIS type 65641), so a
# strict reading of "copy the template exactly" would leave every map untitled,
# and the legend has title="" so the coverage percentage would disappear too.
# Both are kept by default because dropping them removes information the report
# relies on. Set either to False for a byte-faithful rendering of the template.
# --------------------------------------------------------------------------- #
DRAW_FALLBACK_TITLE = True
DRAW_LEGEND_COVERAGE_SUBTITLE = True

# Padding around the implementing LGAs on the state implementation map, as a
# fraction of their extent. Smaller = more zoomed in; this is the knob to turn
# if the implementing LGAs should fill more or less of the frame. At 0.08 a band
# of adjoining territory stays visible for context.
IMPLEMENTATION_ZOOM_MARGIN = 0.08

# Maps rendered from the built-in layout (see LEGACY_LAYOUT_ROLES) borrow this
# role's north arrow so every map in the report carries the same one. The
# built-in page and the templates share the A3 aspect ratio, so the arrow lands
# at the same relative position and size on both.
NORTH_ARROW_REFERENCE_ROLE = "statewide"
# Used only if that reference template is unavailable — the statewide template's
# arrow rect and colour expressed as figure fractions.
FALLBACK_NORTH_ARROW_RECT = [0.9503, 0.9230, 0.0497, 0.0648]
FALLBACK_NORTH_ARROW_COLOR = "#6c6c6c"

# Fallback geometry, used only when no template is available for a role.
_FALLBACK_FIGSIZE = (14.85, 10.5)          # A3 landscape, inches
_FALLBACK_MAP_AXES = [0.01, 0.085, 0.98, 0.80]
_FALLBACK_DPI = 150

# Roles that ignore a discovered *single-map* template and use the built-in
# layout above. The old A3-portrait 'LGA coverage map.qpt' is left in the folder
# but not applied, because the original landscape layout is preferred for the
# single-panel maps.
#
# A *multi-map* template always wins, whatever this set says: a side-by-side
# layout is not something the built-in layout can stand in for, so supplying one
# is taken as an explicit request to use it.
LEGACY_LAYOUT_ROLES: set[str] = {"lga"}

# Sentinel for "work the template out from the role". It exists so that an
# explicit spec=None can mean "use the built-in layout, do not go looking" —
# which is what the side-by-side fallback path needs, since the LGA template it
# would rediscover is the two-map page the single-panel renderer cannot use.
AUTO_TEMPLATE = object()

_TEMPLATE_CACHE: dict[str, LayoutSpec] | None = None
_TEMPLATE_DIRS: list[str] = []
_WARNED_ROLES: set[str] = set()
_DPI_OVERRIDE: int | None = None


def set_dpi_override(dpi: int | None) -> None:
    """Override the template's printResolution for raster output.

    The templates specify 300 dpi, which on A3 portrait is a 3508x4961 PNG —
    roughly eight times the pixel count of the previous 140 dpi output. Across
    every LGA that inflates the maps folder and anything embedding them. Set a
    lower value here (or via --map-dpi) to trade fidelity for file size without
    editing the templates.
    """
    global _DPI_OVERRIDE
    _DPI_OVERRIDE = int(dpi) if dpi else None


# --------------------------------------------------------------------------- #
# template plumbing
# --------------------------------------------------------------------------- #

def set_template_dirs(dirs: list[str] | str | None) -> None:
    """Point template discovery at one or more directories and clear the cache.

    Call before generating maps to override the templates that sit next to the
    code (the web app uses this for uploaded templates).
    """
    global _TEMPLATE_DIRS, _TEMPLATE_CACHE
    if dirs is None:
        _TEMPLATE_DIRS = []
    elif isinstance(dirs, str):
        _TEMPLATE_DIRS = [dirs]
    else:
        _TEMPLATE_DIRS = list(dirs)
    _TEMPLATE_CACHE = None
    _WARNED_ROLES.clear()


def templates(quiet: bool = False) -> dict[str, LayoutSpec]:
    """Parsed templates by role, loaded once per process."""
    global _TEMPLATE_CACHE
    if _TEMPLATE_CACHE is None:
        if not quiet:
            print("Stage 4 — loading map layout templates:")
            report_font()
        _TEMPLATE_CACHE = load_templates(_TEMPLATE_DIRS or None, quiet=quiet)
        if not _TEMPLATE_CACHE and not quiet:
            print("  none found — using built-in fallback layout")
    return _TEMPLATE_CACHE


def template_for(role: str) -> LayoutSpec | None:
    spec = templates().get(role)
    # A multi-map layout overrides the legacy opt-out — see LEGACY_LAYOUT_ROLES.
    if role in LEGACY_LAYOUT_ROLES and (spec is None or not spec.is_multi_map):
        if role not in _WARNED_ROLES:
            _WARNED_ROLES.add(role)
            if spec is None:
                print(f"  '{role}' maps use the original built-in layout "
                      f"(LEGACY_LAYOUT_ROLES) — no template found for this role")
            else:
                print(f"  '{role}' maps use the original built-in layout "
                      f"(LEGACY_LAYOUT_ROLES) — the single-map "
                      f"{os.path.basename(spec.source_path)} is ignored")
        return None
    if spec is None and role not in _WARNED_ROLES:
        _WARNED_ROLES.add(role)
        print(f"  no '{role}' template found — falling back to the built-in layout")
    return spec


def _font_family(name: str) -> str:
    """Template font, resolved to one that is installed on this machine.

    Returns a SINGLE family name, never a fallback list. Passing matplotlib a
    list beginning with an absent font — which is what this used to do — makes
    it search for that font and log

        findfont: Font family 'MS Shell Dlg 2' not found.

    once per text object, before falling through to the fallback. Resolving up
    front means no lookup can miss, so the warnings never arise.

    See `pipeline_fonts` for the substitution table and why the templates name
    fonts that do not exist here.
    """
    return resolve_family(name)


def report_font() -> None:
    """Name the font the maps will be drawn in, once, in the stage-4 log."""
    print(f"  map font: {_MAP_FONT} "
          f"(template fonts are substituted if not installed)")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def safe_name(name: str) -> str:
    return str(name).replace("/", "-").replace("\\", "-").replace(" ", "_")


def _wrap(name: str, width: int = 12) -> str:
    return "\n".join(textwrap.wrap(str(name), width)) or str(name)


def _pick_layer(path: str) -> str | None:
    import pyogrio
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Boundary file not found: {path}\n"
            "Check the --lga-boundaries / --wards / --states path, or in the web app "
            "upload the file directly in the 'Boundary Layers' section.")
    names = [l[0] for l in pyogrio.list_layers(path)]
    skip = {"data_licenses", "sql_statements_log", "elementarygeometries", "knn2"}
    data_layers = [n for n in names if n.lower() not in skip]
    return data_layers[0] if data_layers else names[0]


def read_boundary_file(path: str) -> gpd.GeoDataFrame:
    if path.endswith(".sqlite"):
        return gpd.read_file(path, layer=_pick_layer(path))
    return gpd.read_file(path)


def load_boundaries(lga_file: str, state_name: str, lga_names: list[str] | None = None) -> gpd.GeoDataFrame:
    lga = read_boundary_file(lga_file)
    state_col = next((c for c in lga.columns if "state" in c.lower() and "code" not in c.lower()), None)
    if state_col:
        sel = lga[lga[state_col].astype(str).str.strip().str.title() == state_name.title()]
        if len(sel):
            lga = sel
    if lga_names:
        name_col = next((c for c in lga.columns if "lga" in c.lower() and "code" not in c.lower()), None)
        if name_col:
            wanted = {str(n).strip().title() for n in lga_names}
            sel = lga[lga[name_col].astype(str).str.strip().str.title().isin(wanted)]
            if len(sel):
                lga = sel
    return lga


def load_wards(ward_file: str, state_name: str) -> gpd.GeoDataFrame | None:
    if not ward_file or not os.path.exists(ward_file):
        return None
    wards = read_boundary_file(ward_file)
    state_col = next((c for c in wards.columns if c.lower() == "state"), None)
    if state_col:
        sel = wards[wards[state_col].astype(str).str.strip().str.title() == state_name.title()]
        if len(sel):
            wards = sel
    return wards


def settlements_gdf(visitation_csv: str, cum_col: str) -> gpd.GeoDataFrame:
    df = pd.read_csv(visitation_csv, low_memory=False)
    lat = next(c for c in df.columns if "latitude" in c.lower())
    lon = next(c for c in df.columns if "longitude" in c.lower())
    df[lat] = pd.to_numeric(df[lat], errors="coerce")
    df[lon] = pd.to_numeric(df[lon], errors="coerce")
    df = df.dropna(subset=[lat, lon])
    gdf = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df[lon], df[lat]), crs="EPSG:4326")
    gdf["status"] = gdf[cum_col].map(lambda v: "Visited" if v == "Visited" else "Not Visited")
    return gdf


# --------------------------------------------------------------------------- #
# polygon inputs for the side-by-side panels
# --------------------------------------------------------------------------- #

def read_spatial(path: str) -> gpd.GeoDataFrame:
    """Read any of the spatial formats the pipeline passes around."""
    path = str(path)
    if path.endswith(".parquet"):
        return gpd.read_parquet(path)
    if path.endswith(".sqlite"):
        return gpd.read_file(path, layer=_pick_layer(path))
    return gpd.read_file(path)


def _ensure_unique_key(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Make sure the frame carries the `unique` settlement key.

    Production files precompute it; anything else gets it built the same way
    stage 2 does, so the two sides of the join always agree.
    """
    if "unique" in gdf.columns:
        return gdf
    from stage2_analysis import construct_unique
    return construct_unique(gdf, "unique")


def _status_from_visitation(series: pd.Series) -> pd.Series:
    """'Visited' / anything else -> the two statuses the panels colour by.

    Stage 2 writes "Not Yet Visited" on the gridded TA and "Not Visited" on the
    settlement list; both mean the same thing to a map.
    """
    return series.astype(str).str.strip().str.title().map(
        lambda v: "Visited" if v == "Visited" else "Not Visited")


def settlement_extents_gdf(voronoi_file: str, visitation_csv: str,
                           cum_col: str) -> gpd.GeoDataFrame | None:
    """Settlement extent polygons carrying this day's visitation status.

    The extents themselves have no visitation status — it comes from the day's
    visitation CSV, joined on the `unique` settlement key. Returns None if the
    file cannot be read or the join matches nothing, so the caller can fall
    back to the single-panel point map.
    """
    if not voronoi_file or not os.path.exists(voronoi_file):
        return None
    try:
        extents = read_spatial(voronoi_file)
    except Exception as exc:
        print(f"  settlement extents not usable ({exc}) — "
              f"falling back to the single-panel LGA map")
        return None

    try:
        extents = _ensure_unique_key(extents)
        dip = pd.read_csv(visitation_csv, low_memory=False)
        dip = _ensure_unique_key(dip)
        if cum_col not in dip.columns:
            print(f"  '{cum_col}' missing from the visitation CSV — "
                  f"falling back to the single-panel LGA map")
            return None
        status = (dip[["unique", cum_col]].dropna(subset=["unique"])
                  .drop_duplicates("unique").set_index("unique")[cum_col])
        extents = extents.copy()
        extents["status"] = _status_from_visitation(
            extents["unique"].map(status).fillna("Not Visited"))
        matched = int(extents["unique"].isin(status.index).sum())
        if not matched:
            print("  no settlement extent matched the visitation CSV on `unique` — "
                  "falling back to the single-panel LGA map")
            return None
        print(f"  settlement extents: {matched:,} of {len(extents):,} matched the day's status")
        return extents
    except Exception as exc:
        print(f"  settlement extents not usable ({exc}) — "
              f"falling back to the single-panel LGA map")
        return None


def gridded_ta_gdf(gridded_ta_file: str) -> gpd.GeoDataFrame | None:
    """Gridded target-area cells carrying their visited flag.

    This is stage 2's `gridded_ta_day_{N}` output, which already holds one row
    per cell with a `visitation` column. Returns None when unavailable.
    """
    if not gridded_ta_file or not os.path.exists(gridded_ta_file):
        return None
    try:
        grids = read_spatial(gridded_ta_file)
    except Exception as exc:
        print(f"  gridded TA not usable ({exc}) — "
              f"falling back to the single-panel LGA map")
        return None
    col = next((c for c in ("visitation", "VIS_STAT", "vis_stat", "cumm")
                if c in grids.columns), None)
    if col is None:
        print("  gridded TA has no visitation column — "
              "falling back to the single-panel LGA map")
        return None
    grids = grids.copy()
    grids["status"] = _status_from_visitation(grids[col])
    # a cell may appear once per intersecting track point; one row per cell is
    # all a map needs, and it keeps the draw cheap on a full-state grid
    if "rowid" in grids.columns:
        grids = grids.sort_values("status").drop_duplicates("rowid", keep="first")
    print(f"  gridded TA: {len(grids):,} cells "
          f"({int((grids['status'] == 'Visited').sum()):,} visited)")
    return grids


def _subset_for_lga(gdf: gpd.GeoDataFrame | None, lga_name: str,
                    boundary: gpd.GeoDataFrame) -> gpd.GeoDataFrame | None:
    """Rows belonging to one LGA — by attribute where possible, else spatially.

    The attribute path is far cheaper on a state-wide grid; the spatial clip is
    the safety net for files that carry no usable LGA column.
    """
    if gdf is None or not len(gdf):
        return gdf
    col = next((c for c in gdf.columns
                if "lga" in c.lower() and "code" not in c.lower()), None)
    if col is not None:
        want = lga_name.strip().replace("_", " ").title()
        norm = (gdf[col].astype(str).str.replace("_", " ", regex=False)
                .str.replace(r"\s+", " ", regex=True).str.strip().str.title())
        sel = gdf[norm == want]
        if len(sel):
            return sel
    try:
        return gpd.clip(gdf, boundary)
    except Exception:
        return gdf.iloc[0:0]


# --------------------------------------------------------------------------- #
# template-driven drawing primitives
# --------------------------------------------------------------------------- #

def _new_figure(spec: LayoutSpec | None):
    """Create the page figure and the map axes, per template if available."""
    if spec is None:
        fig = plt.figure(figsize=_FALLBACK_FIGSIZE)
        return fig, fig.add_axes(_FALLBACK_MAP_AXES)
    fig = plt.figure(figsize=spec.figsize)
    return fig, fig.add_axes(spec.rect_to_fig(spec.map_rect))


def _figure_dpi(spec: LayoutSpec | None) -> int:
    if _DPI_OVERRIDE:
        return _DPI_OVERRIDE
    return spec.dpi if spec is not None else _FALLBACK_DPI


def _data_box(fig, ax) -> tuple[float, float, float, float]:
    """The area the map actually fills, as (x0, y0, x1, y1) figure fractions.

    The map frame runs full-bleed, but geopandas sets an equal aspect ratio, so
    the drawn extent is letterboxed inside that frame and centred: a tall extent
    on a landscape page leaves wide blank margins left and right. Layout items
    positioned from page coordinates land in those margins, visually detached
    from the map. This computes the real drawn region so they can be placed
    against it instead.
    """
    p = ax.get_position()
    try:
        x0, x1 = ax.get_xlim()
        y0, y1 = ax.get_ylim()
        dx, dy = abs(x1 - x0), abs(y1 - y0)
        fig_w, fig_h = fig.get_size_inches()
        box_w, box_h = p.width * fig_w, p.height * fig_h
        if dx <= 0 or dy <= 0 or box_w <= 0 or box_h <= 0:
            return p.x0, p.y0, p.x1, p.y1
        # equal aspect: one data unit is the same length on both axes
        scale = min(box_w / dx, box_h / dy)
        used_w, used_h = (dx * scale) / fig_w, (dy * scale) / fig_h
        # anchor 'C' — matplotlib centres the used area within the axes box
        cx, cy = p.x0 + p.width / 2, p.y0 + p.height / 2
        return (cx - used_w / 2, cy - used_h / 2, cx + used_w / 2, cy + used_h / 2)
    except Exception:
        return p.x0, p.y0, p.x1, p.y1


def _reanchor(spec: LayoutSpec, rect, box: tuple[float, float, float, float] | None):
    """Re-anchor a template rect from the page edges to the drawn map's edges.

    The item keeps its template size and its gap from whichever page corner it
    sits nearest, but that gap is now measured from the map area rather than the
    paper. With `box` None this is just the plain page placement.
    """
    left_page = rect.x / spec.page_width
    bottom_page = 1.0 - (rect.y + rect.h) / spec.page_height
    w = spec.mm_to_figw(rect.w)
    h = spec.mm_to_figh(rect.h)
    if box is None:
        return [left_page, bottom_page, w, h]

    x0, y0, x1, y1 = box
    if left_page + w / 2 <= 0.5:
        left = x0 + left_page
    else:
        left = x1 - w - (1.0 - (left_page + w))
    if bottom_page + h / 2 <= 0.5:
        bottom = y0 + bottom_page
    else:
        bottom = y1 - h - (1.0 - (bottom_page + h))
    # never push an item off the page
    left = min(max(left, 0.0), max(0.0, 1.0 - w))
    bottom = min(max(bottom, 0.0), max(0.0, 1.0 - h))
    return [left, bottom, w, h]


def _is_pale(hex_color: str) -> bool:
    """True for a fill too light to read against the page's white background."""
    try:
        r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    except (ValueError, IndexError):
        return False
    return (0.299 * r + 0.587 * g + 0.114 * b) > 235


def _arrow_glyph(fig, rect_frac: list[float], color: str, font_size: float = 13,
                 stroke: str | None = None, stroke_width: float = 0.2):
    """Draw the north arrow glyph into a figure-fraction rectangle.

    One implementation for every map so the arrow is identical throughout the
    report, whether the map came from a template or the built-in layout.

    QGIS both fills and strokes the arrow SVG. Templates that fill it grey read
    fine from the fill alone, but the LGA template fills it white and carries
    the shape entirely in the stroke — so a white fill gets the stroke widened
    to stay visible, and the 'N' takes the stroke colour rather than vanishing
    into the page.
    """
    stroke = stroke or color
    pale = _is_pale(color)
    nax = fig.add_axes(rect_frac, zorder=20)
    nax.set_xlim(0, 1)
    nax.set_ylim(0, 1)
    nax.axis("off")
    nax.patch.set_alpha(0)
    # arrow occupies the lower ~78% of the frame, 'N' sits above it
    tip_y, base_y = 0.80, 0.06
    body = [(0.5, tip_y), (0.86, base_y), (0.5, 0.26), (0.14, base_y)]
    nax.add_patch(Polygon(body, closed=True, facecolor=color, edgecolor=stroke,
                          linewidth=max(stroke_width * 6, 1.4) if pale else 0.6))
    nax.text(0.5, 0.93, "N", ha="center", va="center", fontsize=font_size,
             weight="bold", color=stroke if pale else color)
    return nax


def _draw_north_arrow(fig, spec: LayoutSpec | None, ax=None, box=None):
    """North arrow at the template's rect, in the template's fill colour.

    The templates reference QGIS's bundled `arrows/NorthArrow_02.svg`, which is
    not available to matplotlib, so an equivalent arrow glyph is drawn into the
    same rectangle at the same colour. Position, size and colour are faithful;
    the glyph outline is an approximation.

    Maps without a template (the LGA coverage maps) borrow the reference
    template's arrow rather than drawing a different one, so the arrow is
    consistent across the report.
    """
    if spec is None or spec.north_arrow is None:
        ref = templates(quiet=True).get(NORTH_ARROW_REFERENCE_ROLE)
        if ref is not None and ref.north_arrow is not None:
            _arrow_glyph(fig, ref.rect_to_fig(ref.north_arrow.rect),
                         ref.north_arrow.fill_color)
        else:
            _arrow_glyph(fig, FALLBACK_NORTH_ARROW_RECT, FALLBACK_NORTH_ARROW_COLOR)
        return

    na = spec.north_arrow
    _arrow_glyph(fig, _reanchor(spec, na.drawn_rect, box), na.fill_color,
                 stroke=na.stroke_color, stroke_width=na.stroke_width)


def _draw_logo(fig, spec: LayoutSpec | None, logo: str | None, box=None):
    """Organization logo at the template's picture rect.

    The templates point at an eHA logo somewhere on the author's machine, a path
    that does not exist here, so the logo file passed in by the pipeline is used
    instead — the template supplies only the placement.
    """
    if not logo or not os.path.exists(logo):
        return
    if spec is not None and spec.logo is not None:
        placement = _reanchor(spec, spec.logo.rect, box)
    else:
        placement = [0.905, 0.012, 0.075, 0.075]
    _place_image(fig, logo, placement, anchor="SE")


def _draw_state_logo(fig, spec: LayoutSpec | None, state_logo: str | None, box=None):
    """Campaign State PHC logo, if the layout has a second picture slot.

    The campaign runs in a different state each time, so the template's own
    file (a fixed state's logo) is ignored and only its placement is used.
    Silently does nothing when the layout has one picture slot or no state logo
    was supplied — neither is an error.
    """
    if not state_logo or not os.path.exists(state_logo):
        return
    if spec is None or spec.state_logo is None:
        return
    _place_image(fig, state_logo, _reanchor(spec, spec.state_logo.rect, box), anchor="NW")


def _place_image(fig, path: str, placement: list[float], anchor: str = "SE"):
    try:
        img = plt.imread(path)
    except Exception:
        return
    ax = fig.add_axes(placement, anchor=anchor, zorder=10)
    ax.imshow(img)
    ax.axis("off")
    ax.patch.set_alpha(0)


def _draw_scalebar(fig, ax, spec: LayoutSpec | None, box=None):
    """Single-box scale bar at the layout's (first) scale-bar rect."""
    if spec is None or spec.scalebar is None:
        return
    _draw_scalebar_item(fig, ax, spec, spec.scalebar, box=box)


def _draw_scalebar_item(fig, ax, spec: LayoutSpec, sb, box=None):
    """Single-box scale bar at a specific scale-bar item's rect.

    Call after the axes' x/y limits are final — the bar length is derived from
    the rendered extent so the bar is metrically correct for this map, not for
    whichever atlas feature was on screen when the template was saved.
    """
    try:
        x0, x1 = ax.get_xlim()
        y0, y1 = ax.get_ylim()
    except Exception:
        return
    lon_span = abs(x1 - x0)
    if lon_span <= 0:
        return
    pos = ax.get_position()
    km_per_segment, bar_mm = choose_scalebar_units(
        spec, sb, pos.width, lon_span, (y0 + y1) / 2.0)
    if bar_mm <= 0:
        return

    segments = max(1, sb.segments)
    seg_w = spec.mm_to_figw(bar_mm / segments)
    bar_h = spec.mm_to_figh(sb.height)
    left, bottom, _, _ = _reanchor(spec, sb.rect, box)
    # lift the bar off the page edge by the box content spacing, and leave the
    # label row above it inside the item rect
    bottom += spec.mm_to_figh(1.0)

    for i in range(segments):
        fig.add_artist(Rectangle(
            (left + i * seg_w, bottom), seg_w, bar_h,
            transform=fig.transFigure, zorder=21,
            facecolor=sb.fill_color if i % 2 == 0 else sb.fill_color2,
            edgecolor=sb.stroke_color, linewidth=sb.line_width * 2))

    label_y = bottom + bar_h + spec.mm_to_figh(sb.height * 0.45)
    fams = _font_family(sb.font_family)
    for i in range(segments + 1):
        value = km_per_segment * i
        text = f"{value:g}" if i < segments else f"{value:g} {sb.unit_label}"
        fig.text(left + i * seg_w, label_y, text, ha="center", va="bottom",
                 fontsize=sb.font_size * 0.72, color=sb.stroke_color,
                 family=fams, zorder=21)


# label text -> the artist role it stands for, so template legend entries drive
# both the order and the wording of the legend
def _legend_role(label: str) -> str | None:
    l = label.strip().lower()
    # 'TA Gridded Extents' is a categorized layer: one template entry standing
    # for both cell colours, so it gets its own role and is expanded at draw
    # time. Tested first because its wording also contains no 'visit'.
    if "grid" in l:
        return "grid"
    if "visit" in l and ("not" in l or "yet" in l):
        return "not_visited"
    if "visit" in l:
        return "visited"
    if "implementing" in l or "implement" in l:
        return "implementing"
    if "adjoining" in l and "lga" in l:
        return "adjoining_lga"
    if "adjoining" in l and "state" in l:
        return "adjoining_state"
    if "focal" in l:
        return "focal"
    if "ward" in l:
        # 'ward' is the accurate role; 'implementing' is kept as an alias
        # because the older single-map templates route ward entries through it
        return "ward"
    return None


def _draw_legend(fig, spec: LayoutSpec | None, handles_by_role: dict[str, object],
                 fallback_handles: list, subtitle: str = "",
                 fallback_ncol: int = 2, label_suffixes: dict[str, str] | None = None,
                 box=None, legend_spec=None,
                 expand_roles: dict[str, list[tuple[object, str]]] | None = None,
                 compress: bool = False, shift_down_mm: float = 0.0):
    """Legend at the template's rect, entries in the template's order and wording.

    `handles_by_role` maps a role name (see `_legend_role`) to a matplotlib
    handle. Template entries whose role has no handle on this map are skipped,
    so the LGA template's 'Adjoining LGA' row simply does not appear on a map
    that draws no adjoining LGAs.

    `label_suffixes` appends per-role text to the template's own wording — used
    to keep the settlement counts the report quotes, which the template has no
    way to express.

    `expand_roles` turns one template entry into several rows, for entries that
    stand for a categorized layer: the gridded-TA row carries a colour per
    category, which a single handle cannot express.

    `legend_spec` renders a specific legend rather than the layout's first one —
    a side-by-side page has one per panel, sometimes more.

    Font sizes need no page-size correction: QGIS layout points and matplotlib
    points are both physical points on the page, so the template's 40pt on A0
    and 12pt on A3 both come out at the size QGIS shows.
    """
    lg = legend_spec if legend_spec is not None else (spec.legend if spec else None)
    if spec is None or lg is None:
        fig.legend(handles=fallback_handles, loc="lower left", bbox_to_anchor=(0.012, 0.005),
                   ncol=fallback_ncol, fontsize=12, frameon=True, edgecolor="#bbbbbb",
                   title=subtitle or None, title_fontsize=12)
        return

    suffixes = label_suffixes or {}
    expand = expand_roles or {}
    handles = []
    for label in lg.labels:
        role = _legend_role(label)
        if role and role in expand:
            for handle, text in expand[role]:
                handle.set_label(text)
                handles.append(handle)
            continue
        handle = handles_by_role.get(role) if role else None
        if handle is None:
            continue
        handle.set_label(f"{label}{suffixes.get(role, '')}")
        handles.append(handle)
    if not handles:
        handles = fallback_handles
    if not handles:
        return

    title = lg.title or (subtitle if DRAW_LEGEND_COVERAGE_SUBTITLE else "")
    rect = lg.rect
    if shift_down_mm:
        # QGIS y grows downward, so adding moves the block down the page
        rect = Rect(rect.x, rect.y + shift_down_mm, rect.w, rect.h)
    left, bottom, _, _ = _reanchor(spec, rect, box)
    pad_scale = LEGEND_COMPRESSION if compress else 1.0
    extra = ({"labelspacing": LEGEND_LABEL_SPACING,
              "columnspacing": LEGEND_COLUMN_SPACING,
              "handletextpad": LEGEND_HANDLE_TEXT_PAD} if compress else {})
    legend = fig.legend(
        handles=handles, loc="lower left", bbox_to_anchor=(left, bottom),
        bbox_transform=fig.transFigure, ncol=max(1, lg.columns),
        title=title or None, title_fontsize=lg.title_font_size * 0.7,
        frameon=bool(lg.frame or lg.background),
        borderpad=lg.box_space * 0.35 * pad_scale,
        handlelength=max(1.0, lg.symbol_width * 0.28),
        prop={"family": _font_family(lg.font_family),
              "size": lg.label_font_size * 0.8},
        **extra)
    frame = legend.get_frame()
    frame.set_facecolor("white")
    frame.set_edgecolor("#444444" if lg.frame else "none")
    legend.set_zorder(22)


_EXPRESSION_RE = re.compile(r"\[%.*?%\]", re.DOTALL)


def resolve_label_text(text: str, feature_name: str) -> str:
    """Substitute a layout label's atlas expressions with the feature's name.

    QGIS labels interpolate expressions between `[%` and `%]` — `[% @atlas_pagename %]`
    and field references like `[%"lganame"%]` are both used in these templates,
    and both stand for the name of the feature the page is being rendered for.
    Anything else between the markers is replaced the same way rather than
    printed raw, because a stray expression on a finished map is worse than a
    slightly wrong noun.
    """
    if not text:
        return text
    return _EXPRESSION_RE.sub(lambda _: feature_name or "", text).strip()


def _draw_label_item(fig, spec: LayoutSpec, lb, feature_name: str):
    """Draw one template label item at its own rect."""
    left, bottom = spec.point_to_fig(lb.rect.x, lb.rect.y + lb.rect.h)
    ha = {0: "left", 1: "center", 2: "right"}.get(lb.h_align, "center")
    x = left + (spec.mm_to_figw(lb.rect.w) / 2 if ha == "center"
                else spec.mm_to_figw(lb.rect.w) if ha == "right" else 0)
    fig.text(x, bottom + spec.mm_to_figh(lb.rect.h) * 0.5,
             resolve_label_text(lb.text, feature_name),
             ha=ha, va="center", fontsize=lb.font_size, color=lb.color,
             family=_font_family(lb.font_family), weight="bold", zorder=23)


def _draw_title(fig, spec: LayoutSpec | None, title: str, default_size: float = 17):
    """Title from the template's label items, or a fallback top-centre title.

    The single-map templates contain no label item, so for those this draws the
    fallback. Add a label to a template in QGIS and its text, position, font
    size and colour take over — use `[% @atlas_pagename %]` or a field
    reference in the label to get the per-feature name.
    """
    if spec is not None and spec.labels:
        for lb in spec.labels:
            _draw_label_item(fig, spec, lb, title)
        return
    if not DRAW_FALLBACK_TITLE or not title:
        return
    # The templates run the map frame full-bleed to the page edge, so a fallback
    # title sits over the map itself — the white stroke keeps it readable.
    fig.text(0.5, 0.985 if spec is not None else 0.965, title, ha="center", va="top",
             fontsize=default_size, weight="bold", color="#1a1a1a", zorder=23,
             path_effects=[matplotlib.patheffects.withStroke(linewidth=3.5,
                                                             foreground="white")])


def _set_extent(ax, gdf: gpd.GeoDataFrame, margin: float):
    minx, miny, maxx, maxy = gdf.total_bounds
    mx, my = (maxx - minx) * margin, (maxy - miny) * margin
    ax.set_xlim(minx - mx, maxx + mx)
    ax.set_ylim(miny - my, maxy + my)


# --------------------------------------------------------------------------- #
# renderers
# --------------------------------------------------------------------------- #

def render_map_figure(boundaries: gpd.GeoDataFrame, points: gpd.GeoDataFrame, title: str,
                      lga_label_col: str | None = None, lga_label_size: float = 14,
                      wards: gpd.GeoDataFrame | None = None, ward_label_col: str | None = None,
                      logo: str | None = None, is_lga: bool = False,
                      role: str | None = None, spec=AUTO_TEMPLATE):
    """Single-panel coverage map for a state or one LGA, laid out from a template.

    `role` selects the template ('lga' or 'statewide'); it defaults from
    `is_lga` so existing callers need no change. Pass `spec` to supply an
    already-parsed layout and bypass discovery, or `spec=None` to force the
    built-in layout.
    """
    if spec is AUTO_TEMPLATE:
        spec = template_for(role or ("lga" if is_lga else "statewide"))
    fig, ax = _new_figure(spec)

    boundaries.plot(ax=ax, facecolor=LGA_FILL, edgecolor=BOUNDARY_COLOR, linewidth=1.0)

    ward_handle = None
    if wards is not None and len(wards):
        wards.plot(ax=ax, facecolor="none", edgecolor=WARD_COLOR, linewidth=0.6,
                   linestyle=(0, (4, 2)), alpha=0.9)
        ward_handle = Patch(facecolor="none", edgecolor=WARD_COLOR, linewidth=0.9,
                            linestyle=(0, (4, 2)))
        if ward_label_col:
            for _, row in wards.iterrows():
                pt = row.geometry.representative_point()
                ax.annotate(_wrap(row[ward_label_col], 10), (pt.x, pt.y), ha="center",
                            va="center", fontsize=12, weight="bold", color=WARD_COLOR,
                            style="italic",
                            path_effects=[matplotlib.patheffects.withStroke(
                                linewidth=2.2, foreground="white")])

    nv = points[points["status"] == "Not Visited"]
    v = points[points["status"] == "Visited"]
    if len(nv):
        nv.plot(ax=ax, color=NOT_VISITED_COLOR, markersize=9, alpha=0.85,
                edgecolor="#8c2b29", linewidth=0.2)
    if len(v):
        v.plot(ax=ax, color=VISITED_COLOR, markersize=9, alpha=0.9,
               edgecolor="#0e5c26", linewidth=0.2)

    if lga_label_col:
        for _, row in boundaries.iterrows():
            pt = row.geometry.representative_point()
            ax.annotate(_wrap(row[lga_label_col], 12), (pt.x, pt.y), ha="center", va="center",
                        fontsize=lga_label_size, color="#1a1a1a", weight="bold",
                        path_effects=[matplotlib.patheffects.withStroke(linewidth=2.5,
                                                                       foreground="white")])

    ax.set_axis_off()
    _set_extent(ax, boundaries, spec.atlas_margin if spec is not None else 0.03)

    total = len(points)
    pct = (len(v) / total * 100) if total else 0
    visited_handle = Line2D([0], [0], marker="o", color="w", markerfacecolor=VISITED_COLOR,
                            markersize=11, label=f"Visited ({len(v):,})")
    not_visited_handle = Line2D([0], [0], marker="o", color="w",
                                markerfacecolor=NOT_VISITED_COLOR, markersize=11,
                                label=f"Not Visited ({len(nv):,})")
    handles_by_role = {
        "visited": Line2D([0], [0], marker="o", color="w", markerfacecolor=VISITED_COLOR,
                          markersize=11),
        "not_visited": Line2D([0], [0], marker="o", color="w",
                              markerfacecolor=NOT_VISITED_COLOR, markersize=11),
        "adjoining_lga": Patch(facecolor=LGA_FILL, edgecolor=BOUNDARY_COLOR),
    }
    if ward_handle is not None:
        # 'implementing' is the legacy alias these templates route wards through
        handles_by_role["ward"] = ward_handle
        handles_by_role["implementing"] = ward_handle

    # counts are appended to the template's own wording so the map keeps the
    # per-status totals the report quotes
    _draw_legend(fig, spec, handles_by_role,
                 fallback_handles=[visited_handle, not_visited_handle],
                 subtitle=f"Settlement Visitation — Coverage {pct:.1f}%",
                 label_suffixes={"visited": f" ({len(v):,})",
                                 "not_visited": f" ({len(nv):,})"})
    _draw_title(fig, spec, title, default_size=17)
    _draw_north_arrow(fig, spec, ax)
    _draw_scalebar(fig, ax, spec)
    _draw_logo(fig, spec, logo)
    fig.set_dpi(_figure_dpi(spec))
    return fig


# --------------------------------------------------------------------------- #
# side-by-side LGA panels
# --------------------------------------------------------------------------- #

# What each panel of a two-map LGA page shows. Decided from the panel's own
# title and legend wording so the template stays in charge; if a template says
# nothing either way, left is status and right is coverage, which is the
# reading order of the supplied layout.
PANEL_STATUS = "status"
PANEL_COVERAGE = "coverage"


def panel_kinds(spec: LayoutSpec) -> list[str]:
    """Classify each map frame as the status panel or the coverage panel."""
    kinds: list[str] = []
    for m in spec.maps:
        label = spec.label_for(m.panel)
        text = (label.text if label else "").lower()
        legend_text = " ".join(
            " ".join(lg.labels) for lg in spec.legends_for(m.panel)).lower()
        if "coverage" in text or "grid" in legend_text:
            kinds.append(PANEL_COVERAGE)
        elif "status" in text or "visitation" in text:
            kinds.append(PANEL_STATUS)
        else:
            kinds.append(PANEL_STATUS if m.panel == 0 else PANEL_COVERAGE)
    # a template that named both panels the same still needs one of each
    if len(kinds) >= 2 and len(set(kinds)) == 1:
        kinds[0], kinds[-1] = PANEL_STATUS, PANEL_COVERAGE
    return kinds


def _label_adjoining(ax, adjoining, label_col, font_size):
    """Name each adjoining LGA inside the part of it that is actually on screen.

    The label is placed in the LGA's intersection with the current view, not at
    its true centroid: on a map zoomed to one LGA most neighbours are only
    partly visible and their centroids sit off the page, so centroid placement
    drops exactly the labels the reader needs.
    """
    if adjoining is None or not len(adjoining) or not label_col:
        return
    from shapely.geometry import box as _bbox
    view = ax.viewLim
    view_poly = _bbox(view.x0, view.y0, view.x1, view.y1)
    min_area = view_poly.area * MIN_ADJOINING_LABEL_AREA
    for _, row in adjoining.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        try:
            visible = geom.intersection(view_poly)
        except Exception:
            continue
        if visible.is_empty or visible.area < min_area:
            continue
        pt = visible.representative_point()
        ax.annotate(_wrap(row[label_col], 12), (pt.x, pt.y), ha="center", va="center",
                    fontsize=font_size, weight="bold", color=DUAL_ADJOINING_LABEL,
                    zorder=Z_LABEL,
                    path_effects=[matplotlib.patheffects.withStroke(
                        linewidth=2.6, foreground="white")])


def _draw_panel_context(ax, focal, adjoining, wards, ward_label_col, ward_label_size):
    """The boundary furniture both panels share, in the project's draw order.

    Every non-focal LGA is drawn identically — the campaign's other LGAs get no
    different treatment from LGAs outside the campaign, so the only LGA the eye
    picks out is the focal one.
    """
    if adjoining is not None and len(adjoining):
        adjoining.plot(ax=ax, facecolor=DUAL_ADJOINING_FILL,
                       edgecolor=DUAL_ADJOINING_OUTLINE, linewidth=0.8,
                       zorder=Z_ADJOINING)
    if wards is not None and len(wards):
        wards.plot(ax=ax, facecolor="none", edgecolor=DUAL_WARD_OUTLINE,
                   linewidth=0.9, linestyle=(0, (1, 2)), zorder=Z_WARD)
    if focal is not None and len(focal):
        focal.plot(ax=ax, facecolor="none", edgecolor=DUAL_FOCAL_OUTLINE,
                   linewidth=2.4, zorder=Z_FOCAL)
    if wards is not None and len(wards) and ward_label_col:
        for _, row in wards.iterrows():
            if row.geometry is None or row.geometry.is_empty:
                continue
            pt = row.geometry.representative_point()
            ax.annotate(_wrap(row[ward_label_col], 10), (pt.x, pt.y), ha="center",
                        va="center", fontsize=ward_label_size, weight="bold",
                        color="#1a1a1a", zorder=Z_LABEL,
                        path_effects=[matplotlib.patheffects.withStroke(
                            linewidth=3.0, foreground="white")])


def _plot_by_status(ax, gdf, visited_style: dict, not_visited_style: dict, zorder: float):
    """Plot a polygon layer split by the `status` column."""
    if gdf is None or not len(gdf):
        return 0, 0
    v = gdf[gdf["status"] == "Visited"]
    nv = gdf[gdf["status"] != "Visited"]
    if len(nv):
        nv.plot(ax=ax, zorder=zorder, **not_visited_style)
    if len(v):
        v.plot(ax=ax, zorder=zorder, **visited_style)
    return len(v), len(nv)


def render_dual_lga_figure(spec: LayoutSpec, lga_name: str,
                           focal: gpd.GeoDataFrame,
                           extents: gpd.GeoDataFrame | None,
                           grids: gpd.GeoDataFrame | None,
                           adjoining: gpd.GeoDataFrame | None = None,
                           wards: gpd.GeoDataFrame | None = None,
                           ward_label_col: str | None = None,
                           logo: str | None = None,
                           state_logo: str | None = None,
                           title_context: str | None = None,
                           adjoining_label_col: str | None = None):
    """One page holding both LGA panels, laid out from a two-map template.

    Panel contents:
      status    settlement extents filled by whether the settlement was visited
      coverage  gridded TA cells filled by whether the cell was visited, with
                the settlement extents outlined over them

    Both panels are set to the same extent — the focal LGA plus the template's
    atlas margin — so a feature sits at the same place on the page in both, which
    is the whole point of putting them side by side.
    """
    fig = plt.figure(figsize=spec.figsize)
    kinds = panel_kinds(spec)
    # Text drawn by this module (rather than positioned by the template) has to
    # scale with the page: a 10pt ward label is fine on A3 and invisible on A0.
    # These fractions put ward labels a little below the template's 40pt legend
    # text, which reads correctly against it on the supplied A0 layout.
    ward_label_size = max(9.0, spec.page_height * 0.032)
    glyph_font_size = max(11.0, spec.page_height * 0.030)
    # adjoining names sit a little below ward names — present for orientation,
    # not competing with the focal LGA's own detail
    adjoining_label_size = max(8.0, spec.page_height * 0.026)

    for frame, kind in zip(spec.maps, kinds):
        ax = fig.add_axes(spec.rect_to_fig(frame.rect))
        ax.set_axis_off()

        visited_n = not_visited_n = 0
        if kind == PANEL_COVERAGE:
            visited_n, not_visited_n = _plot_by_status(
                ax, grids,
                {"facecolor": GRID_VISITED_FILL, "edgecolor": GRID_VISITED_FILL,
                 "linewidth": 0.05},
                {"facecolor": GRID_NOT_VISITED_FILL, "edgecolor": GRID_NOT_VISITED_FILL,
                 "linewidth": 0.05},
                Z_DATA)
            # extents as outlines over the cells, for settlement context
            _plot_by_status(
                ax, extents,
                {"facecolor": "none", "edgecolor": EXTENT_VISITED_OUTLINE,
                 "linewidth": 0.5},
                {"facecolor": "none", "edgecolor": EXTENT_NOT_VISITED_OUTLINE,
                 "linewidth": 0.4},
                Z_EXTENT_OUTLINE)
        else:
            visited_n, not_visited_n = _plot_by_status(
                ax, extents,
                {"facecolor": EXTENT_VISITED_FILL, "edgecolor": EXTENT_VISITED_FILL,
                 "linewidth": 0.3},
                {"facecolor": EXTENT_NOT_VISITED_FILL,
                 "edgecolor": EXTENT_NOT_VISITED_FILL, "linewidth": 0.3},
                Z_DATA)

        if focal is not None and len(focal):
            focal.plot(ax=ax, facecolor=DUAL_FOCAL_FILL, edgecolor="none",
                       zorder=Z_FOCAL_FILL)
        _draw_panel_context(ax, focal, adjoining, wards, ward_label_col, ward_label_size)
        # LGA_CONTEXT_MARGIN, not the template's atlas margin — the surrounding
        # LGAs have to be on the page before they can be labelled
        _set_extent(ax, focal, LGA_CONTEXT_MARGIN)
        _label_adjoining(ax, adjoining, adjoining_label_col, adjoining_label_size)

        # ---- panel furniture, all from the template's own items
        label = spec.label_for(frame.panel)
        if label is not None:
            _draw_label_item(fig, spec, label, title_context or lga_name)

        # Legend swatches are drawn far larger than the features they stand for,
        # so outline-only entries need a heavier stroke here than on the map or
        # they read as empty boxes at this page size.
        if kind == PANEL_COVERAGE:
            handles_by_role = {
                "visited": Patch(facecolor="none", edgecolor=EXTENT_VISITED_OUTLINE,
                                 linewidth=LEGEND_OUTLINE_WIDTH),
                "not_visited": Patch(facecolor="none",
                                     edgecolor=EXTENT_NOT_VISITED_OUTLINE,
                                     linewidth=LEGEND_OUTLINE_WIDTH),
            }
            expand_roles = {"grid": [
                (Patch(facecolor=GRID_VISITED_FILL, edgecolor=GRID_VISITED_FILL),
                 f"Visited ({visited_n:,} cells)"),
                (Patch(facecolor=GRID_NOT_VISITED_FILL, edgecolor=GRID_NOT_VISITED_FILL),
                 f"Not Visited ({not_visited_n:,} cells)"),
            ]}
            suffixes = {}
        else:
            handles_by_role = {
                "visited": Patch(facecolor=EXTENT_VISITED_FILL,
                                 edgecolor=EXTENT_VISITED_FILL),
                "not_visited": Patch(facecolor=EXTENT_NOT_VISITED_FILL,
                                     edgecolor=EXTENT_NOT_VISITED_FILL),
            }
            expand_roles = {}
            suffixes = {"visited": f" ({visited_n:,})",
                        "not_visited": f" ({not_visited_n:,})"}

        handles_by_role.update({
            "ward": Patch(facecolor="none", edgecolor=DUAL_WARD_OUTLINE,
                          linewidth=LEGEND_OUTLINE_WIDTH, linestyle=(0, (2, 1.5))),
            "focal": Patch(facecolor="none", edgecolor=DUAL_FOCAL_OUTLINE,
                           linewidth=LEGEND_OUTLINE_WIDTH + 1.0),
            "adjoining_lga": Patch(facecolor=DUAL_ADJOINING_FILL,
                                   edgecolor=DUAL_ADJOINING_OUTLINE,
                                   linewidth=LEGEND_OUTLINE_WIDTH * 0.5),
        })
        handles_by_role["implementing"] = handles_by_role["ward"]

        for legend_spec in spec.legends_for(frame.panel):
            _draw_legend(fig, spec, dict(handles_by_role), fallback_handles=[],
                         legend_spec=legend_spec, expand_roles=expand_roles,
                         label_suffixes=suffixes, compress=True,
                         shift_down_mm=LEGEND_SHIFT_DOWN_MM)

        arrow = spec.north_arrow_for(frame.panel)
        if arrow is not None:
            # drawn_rect, not rect: the item box is taller than the arrow QGIS
            # actually fits inside it, and the full box stretches it to a sliver
            _arrow_glyph(fig, spec.rect_to_fig(arrow.drawn_rect), arrow.fill_color,
                         font_size=glyph_font_size, stroke=arrow.stroke_color,
                         stroke_width=arrow.stroke_width)
        bar = spec.scalebar_for(frame.panel)
        if bar is not None and bar.panel == frame.panel:
            _draw_scalebar_item(fig, ax, spec, bar)

    _draw_logo(fig, spec, logo)
    _draw_state_logo(fig, spec, state_logo)
    fig.set_dpi(_figure_dpi(spec))
    return fig


def implementation_map(state_file: str, lga_file: str, state_name: str,
                       implementing_lgas: list[str], out_png: str,
                       logo: str | None = None, title: str | None = None,
                       spec=AUTO_TEMPLATE) -> str:
    """State implementation map: implementing LGAs shaded, adjoining states grey."""
    if spec is AUTO_TEMPLATE:
        spec = template_for("implementation")

    states = read_boundary_file(state_file)
    st_col = next(c for c in states.columns if "state" in c.lower() and "code" not in c.lower())
    focal = states[states[st_col].astype(str).str.strip().str.title() == state_name.title()]
    lga = load_boundaries(lga_file, state_name, implementing_lgas)
    lga_name_col = next((c for c in lga.columns if "lga" in c.lower()
                         and "code" not in c.lower()), lga.columns[0])
    wanted = {str(n).strip().title() for n in implementing_lgas}
    lga_names_t = lga[lga_name_col].astype(str).str.strip().str.title()
    impl = lga[lga_names_t.isin(wanted)] if wanted else lga

    fig, ax = _new_figure(spec)

    states.plot(ax=ax, facecolor=ADJOINING_FILL, edgecolor="#999999", linewidth=0.7)
    focal.plot(ax=ax, facecolor=FOCAL_FILL, edgecolor="#555555", linewidth=1.4)
    lga.plot(ax=ax, facecolor=FOCAL_FILL, edgecolor="#8a8a8a", linewidth=0.7)
    if len(impl):
        impl.plot(ax=ax, facecolor=IMPLEMENTING_FILL, edgecolor="#8a8a8a", linewidth=0.7)

    for _, row in lga.iterrows():
        pt = row.geometry.representative_point()
        ax.annotate(_wrap(row[lga_name_col], 12), (pt.x, pt.y), ha="center", va="center",
                    fontsize=11, weight="bold", color="#222222",
                    path_effects=[matplotlib.patheffects.withStroke(linewidth=2,
                                                                    foreground="white")])

    # Zoom to the implementing LGAs rather than the whole state so they fill the
    # frame. The margin still admits a band of adjoining territory for context;
    # fall back to the focal state when nothing is flagged as implementing.
    _set_extent(ax, impl if len(impl) else focal, IMPLEMENTATION_ZOOM_MARGIN)
    view = ax.viewLim

    # Label each adjoining state inside the sliver of it that is actually on
    # screen. Its true centroid is usually outside the zoomed view, so the
    # previous centroid test dropped most of these labels entirely — the tighter
    # the zoom, the more it dropped.
    from shapely.geometry import box as _bbox
    view_poly = _bbox(view.x0, view.y0, view.x1, view.y1)
    min_visible_area = view_poly.area * 0.004
    for _, row in states.iterrows():
        nm = str(row[st_col]).strip().title()
        if nm == state_name.title():
            continue
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        try:
            visible = geom.intersection(view_poly)
        except Exception:
            continue
        if visible.is_empty or visible.area < min_visible_area:
            continue
        pt = visible.representative_point()
        # adjoining names sit on flat grey fill, so they need weight and a halo
        # to stay legible — the previous light grey italic washed out
        ax.annotate(nm.upper(), (pt.x, pt.y), ha="center", va="center",
                    fontsize=13, color="#3f3f3f", weight="bold", style="italic",
                    zorder=6,
                    path_effects=[matplotlib.patheffects.withStroke(
                        linewidth=3.0, foreground="white")])
    ax.set_axis_off()
    # place the legend, north arrow, scale bar and logo against the drawn map
    # area instead of the paper edge, overlaying the adjoining states
    frame_box = _data_box(fig, ax)

    handles_by_role = {
        "implementing": Patch(facecolor=IMPLEMENTING_FILL, edgecolor="#8a8a8a"),
        "focal": Patch(facecolor=FOCAL_FILL, edgecolor="#555555"),
        "adjoining_state": Patch(facecolor=ADJOINING_FILL, edgecolor="#999999"),
        "adjoining_lga": Patch(facecolor=FOCAL_FILL, edgecolor="#8a8a8a"),
    }
    fallback = [Patch(facecolor=IMPLEMENTING_FILL, edgecolor="#8a8a8a", label="Implementing LGAs"),
                Patch(facecolor=FOCAL_FILL, edgecolor="#555555", label="Focal State"),
                Patch(facecolor=ADJOINING_FILL, edgecolor="#999999", label="Adjoining State")]
    _draw_legend(fig, spec, handles_by_role, fallback_handles=fallback,
                 subtitle="Legend", fallback_ncol=3, box=frame_box)
    _draw_title(fig, spec, title or f"{state_name} State GTS Implementation Map",
                default_size=19)
    _draw_north_arrow(fig, spec, ax, box=frame_box)
    _draw_scalebar(fig, ax, spec, box=frame_box)
    _draw_logo(fig, spec, logo, box=frame_box)

    fig.savefig(out_png, dpi=_figure_dpi(spec), facecolor="white")
    plt.close(fig)
    print(f"Saved {out_png}")
    return out_png


def generate_maps(visitation_csv: str, cum_col: str, lga_file: str, state_name: str,
                  day: int, output_folder: str, logo: str | None = None,
                  ward_file: str | None = None,
                  template_dirs: list[str] | None = None,
                  dpi: int | None = None,
                  voronoi_file: str | None = None,
                  gridded_ta_file: str | None = None,
                  state_logo: str | None = None) -> list[str]:
    """Statewide map plus one map per LGA.

    The LGA maps are the side-by-side status/coverage pages when a two-map
    template is available *and* both polygon inputs were supplied; otherwise
    they are the previous single-panel settlement-point maps. Either way the
    filenames are unchanged (`{State}_{LGA}_day_{N}.png`), so the report and
    PPTX stages keep matching them the same way.
    """
    if template_dirs:
        set_template_dirs(template_dirs)
    if dpi:
        set_dpi_override(dpi)
    os.makedirs(output_folder, exist_ok=True)
    statewide_spec = template_for("statewide")
    lga_spec = template_for("lga")

    points = settlements_gdf(visitation_csv, cum_col)
    pts_lga = next((c for c in points.columns if "lga" in c.lower() and "code" not in c.lower()), None)
    boundaries = load_boundaries(lga_file, state_name,
                                 points[pts_lga].dropna().unique().tolist() if pts_lga else None)
    # Every LGA in the state, campaign or not — the per-LGA maps need the real
    # neighbours for context, and `boundaries` above is filtered to the campaign
    # LGAs, which would leave a target LGA on the edge of the campaign area
    # floating with nothing around it.
    context_lgas = load_boundaries(lga_file, state_name)
    wards = load_wards(ward_file, state_name)
    lga_name_col = next((c for c in boundaries.columns
                         if "lga" in c.lower() and "code" not in c.lower()), None) or \
        next((c for c in boundaries.columns if c.lower() in ("name", "lganame")), boundaries.columns[0])
    context_name_col = lga_name_col if lga_name_col in context_lgas.columns else next(
        (c for c in context_lgas.columns if "lga" in c.lower() and "code" not in c.lower()), None)
    ward_name_col = None
    ward_lga_col = None
    if wards is not None:
        ward_name_col = next((c for c in wards.columns if "ward" in c.lower()
                              and "code" not in c.lower()), None)
        ward_lga_col = next((c for c in wards.columns if c.lower() == "lga"), None)

    # Side-by-side LGA pages need a two-map template and both polygon layers.
    # Anything missing degrades to the single-panel point map rather than
    # failing the run — a template swap should never cost you the day's maps.
    extents = grids = None
    dual = lga_spec is not None and lga_spec.is_multi_map
    if dual:
        print(f"Stage 4 — LGA maps: side-by-side layout "
              f"({os.path.basename(lga_spec.source_path)})")
        extents = settlement_extents_gdf(voronoi_file, visitation_csv, cum_col)
        grids = gridded_ta_gdf(gridded_ta_file)
        if extents is None or grids is None:
            missing = []
            if extents is None:
                missing.append("settlement extents (--voronoi)")
            if grids is None:
                missing.append("gridded TA (stage 2's gridded_ta_day_N file)")
            print(f"  missing {' and '.join(missing)} — LGA maps fall back to the "
                  f"single-panel settlement-point layout")
            dual = False

    # The single-panel renderer cannot use a two-map page — it would draw one
    # map into the left frame and leave the right half of an A0 sheet blank. So
    # on the fallback path, drop back to the built-in layout as well.
    single_panel_spec = None if (lga_spec is not None and lga_spec.is_multi_map) else lga_spec
    lga_output_spec = lga_spec if dual else single_panel_spec

    outputs = []
    fig = render_map_figure(boundaries, points,
                            f"{state_name} State Settlement Visitation Coverage — Day {day}",
                            lga_label_col=lga_name_col, lga_label_size=15, logo=logo,
                            role="statewide", spec=statewide_spec)
    p = os.path.join(output_folder, f"{state_name}_statewide_day_{day}.png")
    fig.savefig(p, dpi=_figure_dpi(statewide_spec), facecolor="white")
    plt.close(fig)
    outputs.append(p)
    print(f"Saved {p}")

    for lga_name in sorted(map(str, boundaries[lga_name_col].dropna().unique())):
        b = boundaries[boundaries[lga_name_col].astype(str) == lga_name]
        w = None
        if wards is not None and ward_lga_col:
            w = wards[wards[ward_lga_col].astype(str).str.strip().str.title()
                      == lga_name.strip().title()]
            if not len(w):
                w = gpd.clip(wards, b)
        elif wards is not None:
            w = gpd.clip(wards, b)

        if dual:
            # Every LGA except the focal one, campaign or not, drawn identically
            # and labelled. They are painted over the data as well, which masks
            # anything spilling past the focal boundary.
            others = context_lgas[context_lgas[context_name_col].astype(str) != lga_name] \
                if context_name_col else None
            fig = render_dual_lga_figure(
                lga_spec, lga_name, focal=b,
                extents=_subset_for_lga(extents, lga_name, b),
                grids=_subset_for_lga(grids, lga_name, b),
                adjoining=others, wards=w, ward_label_col=ward_name_col,
                adjoining_label_col=context_name_col,
                logo=logo, state_logo=state_logo,
                title_context=lga_name.replace("_", " "))
        else:
            pts = points[points[pts_lga].astype(str).str.strip().str.title()
                         == lga_name.strip().title()] if pts_lga else gpd.clip(points, b)
            if not len(pts):
                pts = gpd.clip(points, b)
            fig = render_map_figure(b, pts, f"{lga_name} LGA Settlement Visitation — Day {day}",
                                    wards=w, ward_label_col=ward_name_col, logo=logo,
                                    is_lga=True, role="lga", spec=single_panel_spec)
        p = os.path.join(output_folder, f"{state_name}_{safe_name(lga_name)}_day_{day}.png")
        fig.savefig(p, dpi=_figure_dpi(lga_output_spec), facecolor="white")
        plt.close(fig)
        outputs.append(p)
        print(f"Saved {p}")
    return outputs


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Generate daily coverage maps")
    ap.add_argument("--visitation-csv", required=True)
    ap.add_argument("--cum-col", required=True)
    ap.add_argument("--lga-boundaries", required=True)
    ap.add_argument("--wards", default=None, help="Ward boundary file for LGA maps")
    ap.add_argument("--state", required=True)
    ap.add_argument("--day", type=int, required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--logo", default=None)
    ap.add_argument("--map-template-dir", default=None,
                    help="Directory holding the .qpt map layout templates "
                         "(default: alongside this script, or $GTS_MAP_TEMPLATE_DIR)")
    ap.add_argument("--map-dpi", type=int, default=None,
                    help="Override the template's print resolution (300 dpi) for the "
                         "rendered PNGs — lower it to shrink the output files")
    ap.add_argument("--voronoi", default=None,
                    help="Settlement extent (voronoi) layer. Required, with "
                         "--gridded-ta, for the side-by-side LGA maps")
    ap.add_argument("--gridded-ta", default=None,
                    help="Stage 2's gridded_ta_day_{N} file (per-cell visitation). "
                         "Required, with --voronoi, for the side-by-side LGA maps")
    ap.add_argument("--state-logo", default=None,
                    help="Campaign State PHC logo, placed in the layout's second "
                         "picture slot if it has one")
    a = ap.parse_args()
    generate_maps(a.visitation_csv, a.cum_col, a.lga_boundaries, a.state, a.day,
                  a.output, a.logo, a.wards,
                  template_dirs=[a.map_template_dir] if a.map_template_dir else None,
                  dpi=a.map_dpi, voronoi_file=a.voronoi,
                  gridded_ta_file=a.gridded_ta, state_logo=a.state_logo)
