"""QGIS print-layout parser (.qpt, .qgs and .qgz).

Reads a QGIS layout template and returns a `LayoutSpec` describing the page and
the items on it, in millimetres, plus helpers to convert those millimetres into
matplotlib figure fractions. Stage 4 renders from that spec, so editing a
template in QGIS and re-exporting it changes the generated maps with no code
change.

Accepted files
--------------
- `.qpt` — a layout exported on its own (Layout Manager -> Export as Template)
- `.qgs` — an uncompressed project file
- `.qgz` — a zipped project; the `.qgs` inside it is read directly, so a project
  saved straight out of QGIS can be dropped in without exporting anything

A project may hold several layouts (the older templates here each carry a
Landscape and a Portrait variant). `parse_layout` picks the richest one — most
map frames first, then most layout items — because that is the layout the
project was actually built around.

Multi-map layouts
-----------------
A layout may contain more than one map frame, which is how a side-by-side
comparison page is built in QGIS. `LayoutSpec.maps` holds every frame ordered
left-to-right, and the legends, scale bars, north arrows and labels are
assigned to whichever frame they sit under on the page (`panel_of`,
`legends_for`, `label_for`).

Geometry, not the `map_uuid` attribute, decides that assignment. QGIS points a
new legend at whichever map was selected when it was created, so on a two-map
page it is common for every legend to carry the *first* map's uuid even though
they clearly belong under different frames. Where the item sits on the page is
the reliable signal.

Single-map layouts are unaffected: `spec.legend`, `spec.map_rect`,
`spec.scalebar` and `spec.north_arrow` still return the first (only) item.

Scope — what a layout does and does not carry
---------------------------------------------
A layout is the *print layout*: page size and orientation, and the position,
size and styling of the layout items (map frame, legend, scale bar, north
arrow, pictures, labels). That is what this module parses.

It does NOT carry layer symbology. The visited/not-visited fill colours and the
LGA/ward outline styling live in the project's layer definitions, not in the
layout, so those remain constants in stage4_maps.py.

QGIS layout item type codes
---------------------------
    65638  page
    65639  map frame
    65640  picture (north arrow is a picture with an SVG north-arrow file)
    65641  label
    65642  legend
    65646  scale bar

Coordinate systems
------------------
QGIS layout coordinates are millimetres from the *top-left* of the page, y
increasing downward. Matplotlib figure fractions run from the *bottom-left*,
y increasing upward. `LayoutSpec.rect_to_fig()` does that flip.
"""
from __future__ import annotations

import math
import os
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field

MM_PER_INCH = 25.4

# QGIS layout item type codes
ITEM_PAGE = "65638"
ITEM_MAP = "65639"
ITEM_PICTURE = "65640"
ITEM_LABEL = "65641"
ITEM_LEGEND = "65642"
ITEM_SCALEBAR = "65646"


# --------------------------------------------------------------------------- #
# small attribute parsers
# --------------------------------------------------------------------------- #

def _parse_measure_pair(value: str | None) -> tuple[float, float]:
    """'297,420,mm' or '1.23,384.5,mm' -> (297.0, 420.0).

    QGIS appends a unit suffix; templates written by this project are always in
    mm (the <Layout units="mm"> attribute). A missing or malformed value yields
    (0, 0) rather than raising, so a partially-hand-edited template still loads.
    """
    if not value:
        return 0.0, 0.0
    parts = [p.strip() for p in str(value).split(",")]
    nums: list[float] = []
    for p in parts:
        try:
            nums.append(float(p))
        except ValueError:
            continue  # the trailing 'mm'
        if len(nums) == 2:
            break
    while len(nums) < 2:
        nums.append(0.0)
    return nums[0], nums[1]


def _parse_float(value: str | None, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _parse_int(value: str | None, default: int = 0) -> int:
    try:
        return int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes")


def _parse_color(value: str | None, default: str = "#000000") -> str:
    """'138,138,138,255,hsv:0,0,0.54,1' -> '#8a8a8a'.

    QGIS colour strings lead with r,g,b,a as 0-255 integers and then repeat the
    colour in a named space. Only the leading triple is used.
    """
    if not value:
        return default
    nums = re.findall(r"-?\d+", str(value).split(":")[0])
    if len(nums) < 3:
        return default
    r, g, b = (max(0, min(255, int(n))) for n in nums[:3])
    return f"#{r:02x}{g:02x}{b:02x}"


# --------------------------------------------------------------------------- #
# specs
# --------------------------------------------------------------------------- #

@dataclass
class Rect:
    """A layout item rectangle in mm, measured from the page's top-left."""
    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0

    @property
    def is_empty(self) -> bool:
        return self.w <= 0 or self.h <= 0

    @property
    def center_x(self) -> float:
        return self.x + self.w / 2

    @property
    def center_y(self) -> float:
        return self.y + self.h / 2


@dataclass
class MapFrameSpec:
    """One map frame on the page.

    A layout with two of these is a side-by-side comparison page: `panel`
    numbers them left-to-right so callers can talk about "the left panel"
    without re-deriving it from the rects.
    """
    rect: Rect
    uuid: str = ""
    item_id: str = ""
    panel: int = 0
    extent: tuple[float, float, float, float] | None = None
    atlas_margin: float = 0.03


@dataclass
class LegendSpec:
    rect: Rect
    title: str = ""
    columns: int = 1
    symbol_width: float = 7.0
    symbol_height: float = 4.0
    box_space: float = 2.0
    frame: bool = False
    background: bool = True
    font_family: str = ""
    # font sizes by QGIS legend style name: title / group / subgroup / symbol /
    # symbolLabel. 'symbolLabel' is the per-entry text size.
    font_sizes: dict[str, float] = field(default_factory=dict)
    # Legend entry labels in template order, taken from each layer's
    # 'legend/title-label' override where present, else the layer name.
    labels: list[str] = field(default_factory=list)
    # The map frame QGIS has this legend linked to. Recorded for completeness
    # but NOT used for panel assignment — see the module docstring.
    map_uuid: str = ""
    # Filled in by `LayoutSpec.assign_panels()`: index into `LayoutSpec.maps`.
    panel: int = 0

    @property
    def label_font_size(self) -> float:
        return self.font_sizes.get("symbolLabel") or self.font_sizes.get("symbol") or 12.0

    @property
    def title_font_size(self) -> float:
        return self.font_sizes.get("title") or 16.0


@dataclass
class ScaleBarSpec:
    rect: Rect
    style: str = "Single Box"
    segments: int = 2
    units_per_segment: float = 5.0
    unit_label: str = "km"
    unit_type: str = "km"
    height: float = 3.0
    line_width: float = 0.3
    min_bar_width: float = 50.0
    max_bar_width: float = 150.0
    segment_millimeters: float = 0.0
    font_size: float = 12.0
    font_family: str = ""
    fill_color: str = "#000000"
    fill_color2: str = "#ffffff"
    stroke_color: str = "#000000"
    map_uuid: str = ""
    panel: int = 0


@dataclass
class NorthArrowSpec:
    rect: Rect
    fill_color: str = "#8a8a8a"
    # QGIS strokes the arrow SVG as well as filling it. The older templates fill
    # it grey, so the stroke barely matters; the LGA template fills it *white*
    # and relies entirely on the stroke, which is invisible if only the fill is
    # honoured. Both are carried so either style renders.
    stroke_color: str = "#000000"
    stroke_width: float = 0.2
    svg_file: str = ""
    rotation: float = 0.0
    map_uuid: str = ""
    panel: int = 0
    # QGIS fits the SVG inside the item rect preserving its aspect and centres
    # it, so the item rect is often taller than the arrow actually drawn.
    # Rendering into the full rect stretches the arrow into a sliver.
    picture_width: float = 0.0
    picture_height: float = 0.0

    @property
    def drawn_rect(self) -> Rect:
        """The rect the arrow really occupies — the picture, centred in the item."""
        w = self.picture_width or self.rect.w
        h = self.picture_height or self.rect.h
        if w <= 0 or h <= 0 or self.rect.is_empty:
            return self.rect
        scale = min(self.rect.w / w, self.rect.h / h)
        dw, dh = w * scale, h * scale
        return Rect(self.rect.x + (self.rect.w - dw) / 2,
                    self.rect.y + (self.rect.h - dh) / 2, dw, dh)


@dataclass
class PictureSpec:
    rect: Rect
    file: str = ""
    picture_width: float = 0.0
    picture_height: float = 0.0


@dataclass
class LabelSpec:
    rect: Rect
    text: str = ""
    font_size: float = 14.0
    color: str = "#000000"
    font_family: str = ""
    h_align: int = 1
    v_align: int = 1
    panel: int = 0


@dataclass
class LayoutSpec:
    """A parsed QGIS print layout.

    Items are stored as lists so a multi-map layout is representable. The
    singular `map_rect` / `legend` / `scalebar` / `north_arrow` properties
    return the first of each, which is what every single-map caller wants and
    keeps the pre-existing API working unchanged.
    """
    name: str = ""
    source_path: str = ""
    page_width: float = 420.0          # mm
    page_height: float = 297.0         # mm
    dpi: int = 300
    maps: list[MapFrameSpec] = field(default_factory=list)
    legends: list[LegendSpec] = field(default_factory=list)
    scalebars: list[ScaleBarSpec] = field(default_factory=list)
    north_arrows: list[NorthArrowSpec] = field(default_factory=list)
    pictures: list[PictureSpec] = field(default_factory=list)
    labels: list[LabelSpec] = field(default_factory=list)
    atlas_orientation: str = ""        # from the atlas print_orientation filter

    # ---------------- single-item views (backwards compatible) ---------------- #

    @property
    def map_rect(self) -> Rect:
        return self.maps[0].rect if self.maps else Rect(0, 0, self.page_width, self.page_height)

    @property
    def legend(self) -> LegendSpec | None:
        return self.legends[0] if self.legends else None

    @property
    def scalebar(self) -> ScaleBarSpec | None:
        return self.scalebars[0] if self.scalebars else None

    @property
    def north_arrow(self) -> NorthArrowSpec | None:
        return self.north_arrows[0] if self.north_arrows else None

    @property
    def atlas_margin(self) -> float:
        return self.maps[0].atlas_margin if self.maps else 0.03

    @property
    def extent(self) -> tuple[float, float, float, float] | None:
        return self.maps[0].extent if self.maps else None

    @property
    def panel_count(self) -> int:
        return len(self.maps)

    @property
    def is_multi_map(self) -> bool:
        """True for a side-by-side layout — two or more map frames on the page."""
        return len(self.maps) > 1

    # ---------------- panel association ---------------- #

    def assign_panels(self) -> None:
        """Attach every ancillary item to the map frame it sits under.

        Called once at the end of parsing. An item belongs to the frame whose
        horizontal span contains the item's centre; failing that, to the frame
        whose centre is nearest. Deliberately geometric — see the module
        docstring on why `map_uuid` cannot be trusted here.
        """
        if len(self.maps) < 2:
            for group in (self.legends, self.scalebars, self.north_arrows, self.labels):
                for item in group:
                    item.panel = 0
            return
        for group in (self.legends, self.scalebars, self.north_arrows, self.labels):
            for item in group:
                item.panel = self.panel_of(item.rect)

    def panel_of(self, rect: Rect) -> int:
        """Index of the map frame that `rect` sits under."""
        if not self.maps:
            return 0
        cx = rect.center_x
        for m in self.maps:
            if m.rect.x <= cx <= m.rect.x + m.rect.w:
                return m.panel
        return min(self.maps, key=lambda m: abs(m.rect.center_x - cx)).panel

    def legends_for(self, panel: int) -> list[LegendSpec]:
        """Every legend under a panel — a panel may carry more than one."""
        return [l for l in self.legends if l.panel == panel]

    def north_arrow_for(self, panel: int) -> NorthArrowSpec | None:
        for a in self.north_arrows:
            if a.panel == panel:
                return a
        return self.north_arrows[0] if self.north_arrows else None

    def scalebar_for(self, panel: int) -> ScaleBarSpec | None:
        for s in self.scalebars:
            if s.panel == panel:
                return s
        # a single bar under one panel still measures both, since the panels
        # share an extent — fall back to it rather than dropping the bar
        return self.scalebars[0] if self.scalebars else None

    def label_for(self, panel: int) -> LabelSpec | None:
        for lb in self.labels:
            if lb.panel == panel:
                return lb
        return None

    # ---------------- geometry helpers ---------------- #

    @property
    def is_portrait(self) -> bool:
        return self.page_height > self.page_width

    @property
    def figsize(self) -> tuple[float, float]:
        """Page size in inches, for `plt.figure(figsize=...)`."""
        return (self.page_width / MM_PER_INCH, self.page_height / MM_PER_INCH)

    def rect_to_fig(self, rect: Rect) -> list[float]:
        """mm rect (top-left origin) -> [left, bottom, width, height] figure fractions."""
        left = rect.x / self.page_width
        width = rect.w / self.page_width
        height = rect.h / self.page_height
        bottom = 1.0 - (rect.y + rect.h) / self.page_height
        return [left, bottom, width, height]

    def point_to_fig(self, x_mm: float, y_mm: float) -> tuple[float, float]:
        """A single mm point (top-left origin) -> figure-fraction (x, y)."""
        return x_mm / self.page_width, 1.0 - (y_mm / self.page_height)

    def mm_to_figw(self, mm: float) -> float:
        return mm / self.page_width

    def mm_to_figh(self, mm: float) -> float:
        return mm / self.page_height

    @property
    def logo(self) -> PictureSpec | None:
        """The organization logo slot.

        A layout may carry two picture items — the org logo and a partner/state
        logo. The org logo is the one named for it, else the lowest on the page
        (these layouts put it in a bottom corner). With a single picture this is
        that picture, which is what the older single-logo templates expect.
        """
        if not self.pictures:
            return None
        named = [p for p in self.pictures if "eha" in os.path.basename(p.file).lower()]
        if named:
            return named[0]
        if len(self.pictures) == 1:
            return self.pictures[0]
        return max(self.pictures, key=lambda p: p.rect.y)

    @property
    def state_logo(self) -> PictureSpec | None:
        """The second picture slot, for the campaign's State PHC logo.

        The state changes every campaign, so the template's own file path is
        meaningless here — only the placement is used, and only when the caller
        actually supplies a state logo. None when the layout has just the one
        picture slot.
        """
        if len(self.pictures) < 2:
            return None
        org = self.logo
        rest = [p for p in self.pictures if p is not org]
        return min(rest, key=lambda p: p.rect.y) if rest else None


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #

def _rect_of(item: ET.Element) -> Rect:
    x, y = _parse_measure_pair(item.get("positionOnPage") or item.get("position"))
    w, h = _parse_measure_pair(item.get("size"))
    return Rect(x, y, w, h)


def _legend_font_sizes(item: ET.Element) -> tuple[dict[str, float], str]:
    """Pull font size per legend style name from <styles><style><text-style>."""
    sizes: dict[str, float] = {}
    family = ""
    styles = item.find("styles")
    if styles is None:
        return sizes, family
    for style in styles.findall("style"):
        name = style.get("name") or ""
        ts = style.find("text-style")
        if ts is None or not name:
            continue
        sizes[name] = _parse_float(ts.get("fontSize"), 12.0)
        family = family or (ts.get("fontFamily") or "")
    return sizes, family


def _legend_labels(item: ET.Element) -> list[str]:
    """Legend entry labels in template order.

    Each <layer-tree-layer> may carry a 'legend/title-label' custom property
    that overrides the layer name in the legend; QGIS writes the override only
    when the user renamed the entry, so fall back to the layer name.
    """
    labels: list[str] = []
    group = item.find("layer-tree-group")
    if group is None:
        return labels
    for layer in group.iter("layer-tree-layer"):
        label = None
        for opt in layer.iter("Option"):
            if opt.get("name") == "legend/title-label":
                label = opt.get("value")
                break
        labels.append((label or layer.get("name") or "").strip())
    return [l for l in labels if l]


def _parse_scalebar(item: ET.Element) -> ScaleBarSpec:
    ts = item.find("text-style")
    sb = ScaleBarSpec(
        rect=_rect_of(item),
        style=item.get("style") or "Single Box",
        segments=_parse_int(item.get("numSegments"), 2),
        units_per_segment=_parse_float(item.get("numUnitsPerSegment"), 5.0),
        unit_label=(item.get("unitLabel") or "km").strip(),
        unit_type=(item.get("unitType") or "km").strip(),
        height=_parse_float(item.get("height"), 3.0),
        line_width=_parse_float(item.get("outlineWidth"), 0.3),
        min_bar_width=_parse_float(item.get("minBarWidth"), 50.0),
        max_bar_width=_parse_float(item.get("maxBarWidth"), 150.0),
        segment_millimeters=_parse_float(item.get("segmentMillimeters"), 0.0),
        font_size=_parse_float(ts.get("fontSize"), 12.0) if ts is not None else 12.0,
        font_family=(ts.get("fontFamily") or "") if ts is not None else "",
    )
    for tag, attr in (("fillColor", "fill_color"), ("fillColor2", "fill_color2"),
                      ("strokeColor", "stroke_color")):
        el = item.find(tag)
        if el is not None:
            r = _parse_int(el.get("red"), 0)
            g = _parse_int(el.get("green"), 0)
            b = _parse_int(el.get("blue"), 0)
            setattr(sb, attr, f"#{r:02x}{g:02x}{b:02x}")
    return sb


def _is_north_arrow(item: ET.Element) -> bool:
    ident = (item.get("id") or "").lower()
    fname = (item.get("file") or "").lower()
    return "north" in ident or "northarrow" in fname.replace(" ", "") or "arrows/" in fname


def _parse_label(item: ET.Element) -> LabelSpec:
    # QGIS stores label text either as a labelText attribute or as the item's
    # text content, depending on version.
    text = item.get("labelText")
    if text is None:
        el = item.find("LabelText")
        text = el.text if el is not None and el.text else ""
    ts = item.find("text-style")
    if ts is None:
        style = item.find("LabelFont")
        size = _parse_float(style.get("size") if style is not None else None, 14.0)
        color, family = "#000000", ""
    else:
        size = _parse_float(ts.get("fontSize"), 14.0)
        color = _parse_color(ts.get("textColor"), "#000000")
        family = ts.get("fontFamily") or ""
    return LabelSpec(
        rect=_rect_of(item),
        text=(text or "").strip(),
        font_size=size,
        color=color,
        font_family=family,
        h_align=_parse_int(item.get("halign"), 1),
        v_align=_parse_int(item.get("valign"), 1),
    )


def _read_layout_roots(path: str) -> list[ET.Element]:
    """Every <Layout> element in a .qpt, .qgs or .qgz, in document order."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Layout template not found: {path}")

    if path.lower().endswith(".qgz"):
        try:
            with zipfile.ZipFile(path) as z:
                names = [n for n in z.namelist() if n.lower().endswith(".qgs")]
                if not names:
                    raise ValueError(
                        f"{os.path.basename(path)} is a .qgz but holds no .qgs "
                        f"project file")
                root = ET.fromstring(z.read(names[0]))
        except zipfile.BadZipFile as exc:
            raise ValueError(
                f"{os.path.basename(path)} has a .qgz extension but is not a zip "
                f"archive ({exc})") from exc
        except ET.ParseError as exc:
            raise ValueError(
                f"The project inside {os.path.basename(path)} is not readable as "
                f"XML ({exc})") from exc
    else:
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as exc:
            raise ValueError(
                f"{os.path.basename(path)} is not readable as XML. A .qpt/.qgs is "
                f"plain XML; a zipped project must have a .qgz extension to be "
                f"unzipped automatically. ({exc})") from exc

    if root.tag == "Layout":
        return [root]
    layouts = root.findall(".//Layout")
    if not layouts:
        raise ValueError(f"No <Layout> element in {os.path.basename(path)}")
    return layouts


def _layout_richness(layout: ET.Element) -> tuple[int, int]:
    """Sort key for picking between the layouts in one project.

    Most map frames wins — a two-map comparison page is the layout the project
    was built around, not an incidental single-map variant. Total item count
    breaks ties, which for the older Landscape/Portrait pairs just keeps the
    first one.
    """
    items = [i for i in layout.iter("LayoutItem")]
    maps = [i for i in items if (i.get("type") or "") == ITEM_MAP]
    return len(maps), len(items)


def _parse_layout_element(layout: ET.Element, path: str) -> LayoutSpec:
    """Build a LayoutSpec from one <Layout> element."""
    spec = LayoutSpec(
        name=layout.get("name") or "",
        source_path=path,
        dpi=_parse_int(layout.get("printResolution"), 300),
    )

    for item in layout.iter("LayoutItem"):
        itype = item.get("type") or ""

        if itype == ITEM_PAGE:
            w, h = _parse_measure_pair(item.get("size"))
            if w > 0 and h > 0:
                spec.page_width, spec.page_height = w, h

        elif itype == ITEM_MAP:
            frame = MapFrameSpec(
                rect=_rect_of(item),
                uuid=item.get("uuid") or "",
                item_id=item.get("id") or "",
            )
            ext = item.find("Extent")
            if ext is not None:
                frame.extent = (
                    _parse_float(ext.get("xmin")), _parse_float(ext.get("ymin")),
                    _parse_float(ext.get("xmax")), _parse_float(ext.get("ymax")),
                )
            atlas_map = item.find("AtlasMap")
            if atlas_map is not None:
                frame.atlas_margin = _parse_float(atlas_map.get("margin"), 0.03)
            spec.maps.append(frame)

        elif itype == ITEM_PICTURE:
            rect = _rect_of(item)
            if _is_north_arrow(item):
                spec.north_arrows.append(NorthArrowSpec(
                    rect=rect,
                    fill_color=_parse_color(item.get("svgFillColor"), "#8a8a8a"),
                    stroke_color=_parse_color(item.get("svgBorderColor"), "#000000"),
                    stroke_width=_parse_float(item.get("svgBorderWidth"), 0.2),
                    svg_file=item.get("file") or "",
                    rotation=_parse_float(item.get("pictureRotation"), 0.0),
                    map_uuid=item.get("mapUuid") or "",
                    picture_width=_parse_float(item.get("pictureWidth"), rect.w),
                    picture_height=_parse_float(item.get("pictureHeight"), rect.h),
                ))
            else:
                spec.pictures.append(PictureSpec(
                    rect=rect,
                    file=item.get("file") or "",
                    picture_width=_parse_float(item.get("pictureWidth"), rect.w),
                    picture_height=_parse_float(item.get("pictureHeight"), rect.h),
                ))

        elif itype == ITEM_LEGEND:
            sizes, family = _legend_font_sizes(item)
            spec.legends.append(LegendSpec(
                rect=_rect_of(item),
                title=(item.get("title") or "").strip(),
                columns=_parse_int(item.get("columnCount"), 1),
                symbol_width=_parse_float(item.get("symbolWidth"), 7.0),
                symbol_height=_parse_float(item.get("symbolHeight"), 4.0),
                box_space=_parse_float(item.get("boxSpace"), 2.0),
                frame=_parse_bool(item.get("frame"), False),
                background=_parse_bool(item.get("background"), True),
                font_family=family,
                font_sizes=sizes,
                labels=_legend_labels(item),
                map_uuid=item.get("map_uuid") or "",
            ))

        elif itype == ITEM_SCALEBAR:
            sb = _parse_scalebar(item)
            sb.map_uuid = item.get("mapUuid") or ""
            spec.scalebars.append(sb)

        elif itype == ITEM_LABEL:
            label = _parse_label(item)
            if label.text:
                spec.labels.append(label)

    atlas = layout.find("Atlas")
    if atlas is not None:
        m = re.search(r"print_orientation\W+=\W+'([^']+)'", atlas.get("featureFilter") or "")
        if m:
            spec.atlas_orientation = m.group(1)

    # A page item is optional in some exports; fall back to the map frame size
    # so the figure is never zero-sized.
    if spec.page_width <= 0 or spec.page_height <= 0:
        spec.page_width = spec.map_rect.w or 420.0
        spec.page_height = spec.map_rect.h or 297.0
    if not spec.maps or spec.maps[0].rect.is_empty:
        spec.maps = [MapFrameSpec(rect=Rect(0, 0, spec.page_width, spec.page_height))]

    # left-to-right panel order, then attach the ancillary items to their panel
    spec.maps.sort(key=lambda m: m.rect.x)
    for i, m in enumerate(spec.maps):
        m.panel = i
    spec.assign_panels()
    return spec


def parse_layout(path: str) -> LayoutSpec:
    """Parse a QGIS .qpt, .qgs or .qgz into a LayoutSpec.

    A project holding several layouts yields the richest one (see
    `_layout_richness`). Raises FileNotFoundError if the path does not exist and
    ValueError if the file cannot be read as a QGIS layout.
    """
    layouts = _read_layout_roots(path)
    best = max(layouts, key=_layout_richness)
    return _parse_layout_element(best, path)


# The old name, kept so existing callers and scripts keep working. It now
# accepts .qgs and .qgz too, which is a superset of what it used to take.
parse_qpt = parse_layout


# --------------------------------------------------------------------------- #
# template discovery
# --------------------------------------------------------------------------- #

# role -> (required keyword groups). A filename matches a role when it contains
# at least one keyword from every group, case-insensitively.
_ROLE_PATTERNS: dict[str, list[list[str]]] = {
    "implementation": [["implementation", "implement"]],
    "statewide": [["state", "statewide"], ["cummulative", "cumulative", "cumm", "cum"]],
    "lga": [["lga"], ["coverage", "visitation"]],
}

# Roles are tested in this order so that the more specific patterns win: an
# "Implementation map" filename also contains "state" in some naming schemes.
_ROLE_ORDER = ("implementation", "statewide", "lga")


def classify_template(filename: str) -> str | None:
    """Return 'lga' | 'statewide' | 'implementation' for a template filename."""
    stem = os.path.splitext(os.path.basename(filename))[0].lower()
    for role in _ROLE_ORDER:
        groups = _ROLE_PATTERNS[role]
        if all(any(kw in stem for kw in group) for group in groups):
            return role
    return None


# Layout files this module can read. `.qpt` first so that where a role has both
# an exported template and a full project of equal richness, the purpose-built
# template still wins.
TEMPLATE_EXTENSIONS = (".qpt", ".qgz", ".qgs")


def discover_template_candidates(search_dirs: list[str] | None = None) -> dict[str, list[str]]:
    """Find every layout file per role, best-guess order.

    Search order: `$GTS_MAP_TEMPLATE_DIR`, any explicitly passed directories,
    the directory holding this module, then the current working directory, so a
    template dir passed on the command line comes before the copies sitting
    next to the code. Within a directory, `.qpt` files come before projects.

    All candidates for a role are returned rather than just the first, because
    which one to use depends on what is inside it — see `load_templates`.
    """
    dirs: list[str] = []
    env_dir = os.environ.get("GTS_MAP_TEMPLATE_DIR")
    if env_dir:
        dirs.append(env_dir)
    dirs.extend(search_dirs or [])
    dirs.append(os.path.dirname(os.path.abspath(__file__)))
    dirs.append(os.getcwd())

    found: dict[str, list[str]] = {}
    seen: set[str] = set()
    for d in dirs:
        if not d:
            continue
        d = os.path.abspath(d)
        if d in seen or not os.path.isdir(d):
            continue
        seen.add(d)
        try:
            entries = os.listdir(d)
        except OSError:
            continue
        entries.sort(key=lambda fn: (TEMPLATE_EXTENSIONS.index(os.path.splitext(fn)[1].lower())
                                     if os.path.splitext(fn)[1].lower() in TEMPLATE_EXTENSIONS
                                     else len(TEMPLATE_EXTENSIONS), fn.lower()))
        for fn in entries:
            if not fn.lower().endswith(TEMPLATE_EXTENSIONS):
                continue
            role = classify_template(fn)
            if role:
                found.setdefault(role, []).append(os.path.join(d, fn))
    return found


def discover_templates(search_dirs: list[str] | None = None) -> dict[str, str]:
    """One layout file per role — the first candidate found.

    Kept for callers that only want the filename. `load_templates` does the
    richer selection, so prefer that when the parsed layout is what you need.
    """
    return {role: paths[0]
            for role, paths in discover_template_candidates(search_dirs).items() if paths}


def load_templates(search_dirs: list[str] | None = None,
                   quiet: bool = False) -> dict[str, LayoutSpec]:
    """Discover and parse templates by role, skipping any that fail to parse.

    Where a role has several candidate files, the one with the most map frames
    wins: a side-by-side comparison layout is a deliberate upgrade over a
    single-map one for the same role, and dropping a new two-map project next
    to the old single-map template should be all it takes to switch. Ties keep
    discovery order, so nothing changes for roles with one template.

    A broken or missing template is reported and skipped rather than raising:
    stage 4 falls back to its built-in layout for that role so a bad template
    never takes the whole pipeline down mid-run.
    """
    specs: dict[str, LayoutSpec] = {}
    for role, paths in discover_template_candidates(search_dirs).items():
        parsed: list[LayoutSpec] = []
        for path in paths:
            try:
                parsed.append(parse_layout(path))
            except (ValueError, FileNotFoundError, ET.ParseError) as exc:
                if not quiet:
                    print(f"  template [{role}] SKIPPED {os.path.basename(path)} — {exc}")
        if not parsed:
            continue
        # max() keeps the first of equal keys, i.e. discovery order on a tie
        best = max(parsed, key=lambda s: s.panel_count)
        specs[role] = best
        if not quiet:
            orient = "portrait" if best.is_portrait else "landscape"
            panels = f", {best.panel_count} map frames" if best.is_multi_map else ""
            print(f"  template [{role}] {os.path.basename(best.source_path)} — "
                  f"{best.page_width:g}x{best.page_height:g}mm {orient}, "
                  f"{best.dpi}dpi{panels}")
            for other in parsed:
                if other is not best:
                    print(f"      (not used: {os.path.basename(other.source_path)}, "
                          f"{other.panel_count} map frame"
                          f"{'s' if other.panel_count != 1 else ''})")
    return specs


# --------------------------------------------------------------------------- #
# scale-bar sizing
# --------------------------------------------------------------------------- #

def km_per_degree_lon(latitude_deg: float) -> float:
    """Kilometres per degree of longitude at a given latitude (WGS84 approx)."""
    return 111.320 * math.cos(math.radians(max(-89.9, min(89.9, latitude_deg))))


def choose_scalebar_units(spec: LayoutSpec, sb: ScaleBarSpec,
                          axes_width_frac: float, lon_span_deg: float,
                          mid_latitude_deg: float) -> tuple[float, float]:
    """Pick the km-per-segment and resulting on-page bar width in mm.

    The template's `numUnitsPerSegment` is honoured whenever the bar it produces
    lands inside the template's own [minBarWidth, maxBarWidth] envelope. It
    often will not: these layouts are atlas-driven, so the stored value suits
    the one feature that happened to be previewed when the template was saved,
    and every LGA has a different extent. When the template value would give an
    absurd bar, a 1/2/5 x 10^n value is chosen so the bar stays close to the
    template's own bar width.

    Returns (km_per_segment, total_bar_width_mm).
    """
    # QGIS's minBarWidth only applies to its "fit to segment width" mode, so it
    # is too strict a floor here: honouring the template's own units-per-segment
    # matters more than bar length. Only a bar too small to label, or wider than
    # the template's own ceiling, triggers the nice-number fallback.
    MIN_USABLE_BAR_MM = 15.0
    if axes_width_frac <= 0 or lon_span_deg <= 0:
        return sb.units_per_segment, 0.0

    km_span = lon_span_deg * km_per_degree_lon(mid_latitude_deg)
    # mm of page occupied by the map's full longitude span
    map_width_mm = axes_width_frac * spec.page_width
    if map_width_mm <= 0:
        return sb.units_per_segment, 0.0
    km_per_mm = km_span / map_width_mm

    segments = max(1, sb.segments)
    bar_mm = (sb.units_per_segment * segments) / km_per_mm if km_per_mm else 0.0
    if MIN_USABLE_BAR_MM <= bar_mm <= sb.max_bar_width:
        return sb.units_per_segment, bar_mm

    target_mm = sb.rect.w if sb.rect.w > 0 else (sb.min_bar_width + sb.max_bar_width) / 2
    target_mm = max(MIN_USABLE_BAR_MM, min(sb.max_bar_width, target_mm))
    target_km_per_segment = (target_mm * km_per_mm) / segments
    if target_km_per_segment <= 0:
        return sb.units_per_segment, bar_mm

    exponent = math.floor(math.log10(target_km_per_segment))
    base = 10.0 ** exponent
    for mult in (1, 2, 5, 10):
        nice = mult * base
        if nice >= target_km_per_segment:
            break
    return nice, (nice * segments) / km_per_mm
