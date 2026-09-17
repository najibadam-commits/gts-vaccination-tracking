"""Stage 7 — Post-Implementation Report (PIR), in the stakeholder deck format.

Builds the whole-campaign Post-Implementation GTS Tracking Report as a
PowerPoint deck, populating **"Post Implementation Report 2026.pptx"** — the
report the team presents to stakeholders — as its template.

That sample is the specification for this module. Its structure, in order:

     1  title                        state, campaign, month
     2  Background                   LGA/ward/settlement counts + state
                                     implementation map
     3  State Settlement Coverage    coverage donut + LGA coverage mix
     4  Cumulative Settlements       planned vs depth of coverage achieved
     5  Potentially Missed Children  not-visited settlements + missed children
     6  No Geocoordinate Settlements planned vs visited vs not visited
     7  Coverage Map                 statewide coverage map
     8+ {LGA} LGA Settlements        per-LGA donut + per-ward coverage bar
        Settlement Coverage Examples one real settlement per coverage class
        Challenges                   what the tracking data shows went wrong
        Recommendation               what to do about it
        Deployment / ERM Pictures    photo placeholders for the analyst
        Thank you!

The sample's **Team Deployed/Devices** slide is deliberately dropped, and no
team-deployment figure appears anywhere in the deck. Teams deployed, teams
reported and teams pending are implementation-monitoring measures: they tell the
LGA team who to chase TODAY, which is what the Daily ERM Analysis exists for. In
a report written after the campaign has ended there is nobody left to chase, and
the figure adds a number the reader cannot act on. The daily deck (stage 6) and
the ERM workbook's Team Deploy-Report tab keep it in full. The same reasoning
retires the campaign-day inputs from this path: the PIR is whole-campaign by
construction, so which day is which is stage 2's business, not the report's.

Everything on those slides is recomputed from the current run. No figure, name
or count is carried over from the sample: the deck it produces for Zamfara is a
Zamfara deck, and re-running against different inputs moves every number. The
only thing taken from the sample is the DESIGN — its slide order, its chart
types, its palette, its wording conventions.

How it works, and why
---------------------
The same two-phase approach stage 6 uses for the daily deck, for the same
reason: python-pptx cannot add or remove slides, so the structural work
(matching the LGA slide count to this campaign's LGAs) is done on the unpacked
package, and the content is filled afterwards through python-pptx.

Slides are located by TITLE, not by slide number. The sample is a Google Slides
export and a re-export renumbers its parts; a positional map would then write
the missed-children charts onto the coverage map without erroring. See
`_resolve_parts` and `_resolve_in_presentation`.

Every content slide is laid out from the constants below rather than from the
sample's own shape positions. The sample's boxes were nudged by hand per slide —
its twelve LGA slides have twelve slightly different donut boxes — which reads
as untidy across a generated deck and cannot be relied on for charts whose
aspect ratio depends on this campaign's LGA and ward counts. The template's own
picture and text shapes are removed and replaced at fixed, checked coordinates,
and `_overlap_report` asserts at build time that nothing collides.

Analytical scope
----------------
Whole campaign, cumulative — Day 1 through the final day. Pass the FINAL day's
`cum_col` (`day_{N}_cumm`), chained through prior days with `--prev-dip`, and
ideally all campaign days' tracks, so coverage and field time describe the
campaign rather than its last day. `pir_analysis` carries the indicator
definitions; this module only places them.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from datetime import date

import pandas as pd
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.util import Pt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pptx_xml_utils import (safe_extract, rezip, duplicate_slide, delete_slide,
                            clean_unused_slides, prune_unused_media)
from stage6_pptx_report import (_in, _fit_picture, _set_title, _delete_shape,
                                _overlap_report, _same_shape, _slide_text)

import pir_analysis as pa
import pir_charts as pc

# ----------------------------------------------------------------- geometry
SLIDE_W_IN, SLIDE_H_IN = 10.0, 5.625
# The master's footer band starts here; nothing may cross it.
CONTENT_BOTTOM_IN = 5.25

# One box per slide role, in inches (left, top, width, height). Charts are
# contain-fitted inside these, never stretched, so a wide ward bar and a square
# donut both sit centred in their half of the slide.
#
# The master paints a footer band — the eHealth Africa URL and copyright line —
# across the bottom of every slide from about 5.18in down. `_overlap_report`
# cannot see it: it lives on the MASTER, not on the slide, so nothing this
# module places is ever reported as colliding with it. Every box below
# therefore ends above FOOTNOTE_TOP by hand, and the footnote itself sits in the
# strip between the content and the band.
FOOTNOTE_TOP_IN = 4.80

BOX_BACKGROUND_MAP = (0.10, 1.16, 4.08, 3.60)
BOX_BACKGROUND_TEXT = (4.34, 1.14, 5.40, 3.62)
BOX_STATE_DONUT = (0.08, 1.26, 4.10, 3.48)
BOX_STATE_LGA_BAR = (4.28, 1.26, 5.62, 3.48)
BOX_CUM_NARRATIVE = (0.18, 1.10, 9.64, 0.62)
BOX_CUM_CHART = (1.30, 1.78, 7.40, 3.24)
# The N-labels sit clear of the layout's own accent underline, which runs to
# about 1.25in on this slide's layout.
BOX_MISSED_N_LEFT = (0.28, 1.32, 2.20, 0.32)
BOX_MISSED_N_RIGHT = (5.20, 1.32, 2.20, 0.32)
BOX_MISSED_LEFT = (0.10, 1.66, 4.80, 3.06)
BOX_MISSED_RIGHT = (5.02, 1.66, 4.86, 3.06)
BOX_GEO_CHART = (0.10, 1.46, 4.52, 3.26)
BOX_GEO_TEXT = (4.74, 1.12, 5.10, 3.60)
BOX_STATE_MAP = (1.52, 0.86, 6.96, 4.24)
BOX_LGA_DONUT = (0.10, 1.20, 3.86, 3.54)
BOX_LGA_WARD_BAR = (4.06, 1.20, 5.84, 3.54)
BOX_EXAMPLE_LEFT = (0.34, 0.98, 4.42, 4.04)
BOX_EXAMPLE_RIGHT = (5.12, 0.98, 4.42, 4.04)
BOX_EXAMPLE_LABEL_LEFT = (0.34, 0.60, 4.42, 0.30)
BOX_EXAMPLE_LABEL_RIGHT = (5.12, 0.60, 4.42, 0.30)
BOX_PROSE = (0.32, 1.00, 9.36, 4.02)
BOX_PHOTO_CAPTION = (0.40, 1.08, 9.20, 0.30)
BOX_PHOTOS = (0.40, 1.60, 9.20, 3.10)
BOX_FOOTNOTE = (0.10, FOOTNOTE_TOP_IN, 9.60, 0.34)

FOOTNOTE_PT = 8.5
FOOTNOTE_COLOR = RGBColor(0x55, 0x55, 0x55)

# Prose slides (Challenges, Recommendation) — the sample's bold-heading +
# paragraph rhythm, at the sizes it uses.
PROSE_HEADING_PT = 11.0
PROSE_BODY_PT = 10.0

# Titles the sample gives its slides. Used both to LOCATE a slide in whatever
# template is supplied and to re-title it for this campaign.
T_BACKGROUND = "Background"
# Kept only so the slide can be LOCATED and removed — see `_resolve_parts` and
# the structural edits. Nothing writes to it.
T_DEPLOY = "Team Deployed/Devices"
T_STATE_COVERAGE = "State Settlement Coverage"
T_CUMULATIVE = "Cumulative Settlements Coverage"
T_LGA = "LGA Settlements Coverage"
T_COVERAGE_MAP = "Coverage Map"
T_EXAMPLES = "Settlement Coverage Examples"
T_EXAMPLES_CONT = "Settlement Coverage Examples Cont’d"
T_CHALLENGES = "Challenges"
T_RECOMMENDATION = "Recommendation"
T_DEPLOY_PICS = "Deployment Pictures"
T_ERM_PICS = "ERM Pictures"
T_NO_GEO = "Settlements Without Geo-Coordinates"

# Photo slides the analyst fills in by hand. The sample's own campaign photos
# are stripped: a Zamfara report must not ship with Nasarawa's photographs.
PHOTO_SLIDES = {
    T_DEPLOY_PICS: ("eHealth staff distributing the trackers during the morning "
                    "take-off at the implementing LGAs",
                    "[Insert tracker deployment photographs here.]"),
    T_ERM_PICS: ("eHealth staff participating in the evening review meetings in "
                 "the implementing LGAs",
                 "[Insert evening review meeting photographs here.]"),
}


# ------------------------------------------------------------ template lookup
def find_default_pir_template(*search_dirs: str) -> str | None:
    """Locate the Post Implementation Report deck to use as the template.

    Prefers a file named like the report the team presents ("Post Implementation
    Report ..."), newest first when several are present, so a "... 2027.pptx"
    supersedes "... 2026.pptx" without anyone editing this file.

    Only .pptx is accepted now. The previous stage 7 wrote a Word document and
    treated the sample as a reference to be *named* in the file's properties; a
    .pdf or .docx sample satisfied it. This one FILLS the sample, so anything
    that is not a deck cannot be used and is better rejected loudly than
    silently ignored.
    """
    hits: list[tuple[float, str]] = []
    for folder in search_dirs:
        if not folder or not os.path.isdir(folder):
            continue
        for fn in os.listdir(folder):
            low = fn.lower()
            if not low.endswith(".pptx") or low.startswith("~$"):
                continue
            if "post implementation report" in low or "post-implementation" in low:
                path = os.path.join(folder, fn)
                try:
                    hits.append((os.path.getmtime(path), path))
                except OSError:
                    continue
    if not hits:
        return None
    hits.sort(key=lambda t: (t[0], len(t[1])), reverse=True)
    return hits[0][1]


# Role -> the text that identifies its slide, as (needles, exclusions). One
# table drives BOTH resolution passes below, so the structural phase and the
# content phase can never disagree about which slide is which.
SLIDE_ROLES = (
    ("background", ("background",), ()),
    ("deploy", ("team deployed",), ()),
    ("state_coverage", ("state settlement coverage",), ()),
    ("cumulative", ("cumulative settlements coverage",), ()),
    ("missed", ("potentially missed children",), ()),
    ("no_geo", ("geocoordinate",), ()),
    ("coverage_map", ("coverage map",), (T_LGA, T_EXAMPLES)),
    ("examples", ("settlement coverage examples",), ("cont",)),
    ("examples_cont", ("settlement coverage examples", "cont"), ()),
    ("challenges", ("challenges",), ()),
    ("recommendation", ("recommendation",), ()),
    ("deploy_pics", ("deployment pictures",), ()),
    ("erm_pics", ("erm pictures",), ()),
    ("thanks", ("thank you",), ()),
)


def _is_lga_slide(text: str) -> bool:
    return T_LGA.lower() in text and T_EXAMPLES.lower() not in text


def _assign_roles(keys: list, texts: dict) -> dict:
    """Role -> key, matching each slide's lowercased text against SLIDE_ROLES.

    `keys` are in presentation order and `texts` maps key -> that slide's text.
    Slides are located by TITLE, never by slide number: the sample is a Google
    Slides export and a re-export renumbers its parts, at which point a
    positional map writes the missed-children charts onto the coverage map
    while producing a deck that opens perfectly cleanly.
    """
    out: dict = {}
    for role, needles, exclude in SLIDE_ROLES:
        out[role] = None
        for k in keys:
            t = texts[k]
            if any(x.lower() in t for x in exclude):
                continue
            if all(x.lower() in t for x in needles):
                out[role] = k
                break
    out["title"] = keys[0] if keys else None
    out["lga_slides"] = [k for k in keys if _is_lga_slide(texts[k])]
    return out


def _resolve_parts(unpacked: str) -> dict:
    """Role -> slide PART FILENAME, for the structural (unpacked) phase."""
    slides_dir = os.path.join(unpacked, "ppt", "slides")
    names = sorted((f for f in os.listdir(slides_dir) if f.endswith(".xml")),
                   key=lambda n: int("".join(c for c in n if c.isdigit()) or 0))
    return _assign_roles(names, {n: _slide_text(unpacked, n).lower()
                                 for n in names})


def _slide_full_text(slide) -> str:
    parts = []
    for sh in slide.shapes:
        if sh.has_text_frame and sh.text_frame.text:
            parts.append(sh.text_frame.text)
    return " \n".join(parts).lower()


def _resolve_in_presentation(prs) -> dict:
    """Role -> SLIDE OBJECT, for the content phase.

    Resolved a second time, on the loaded deck, rather than carried over from
    `_resolve_parts`. python-pptx RENUMBERS slide parts when it opens a
    package: after the structural phase duplicates a slide as `slide40.xml`,
    the loaded presentation calls that same slide `slide29.xml`, and a
    part-filename map built before the load silently points at the wrong
    slides — which is exactly how eleven LGA slides once came out still
    carrying the sample's own Akwanga chart. Matching the loaded deck's own
    text has no such failure mode.

    Must be called BEFORE any slide is filled: filling rewrites the very text
    this matches on.
    """
    slides = list(prs.slides)
    texts = {i: _slide_full_text(s) for i, s in enumerate(slides)}
    roles = _assign_roles(list(range(len(slides))), texts)
    resolved = {r: (slides[i] if i is not None else None)
                for r, i in roles.items() if r != "lga_slides"}
    resolved["lga_slides"] = [slides[i] for i in roles["lga_slides"]]
    return resolved


# ------------------------------------------------------------ shape utilities
def _strip_content(slide, keep_pictures_above_in: float = 0.0):
    """Clear a slide of the sample's own content, leaving title and branding.

    Removes every picture and every loose text box except the title
    placeholder, so this module's shapes are placed onto a clean slide rather
    than on top of the previous campaign's charts, captions and footnotes.

    `keep_pictures_above_in` protects a branding image pinned to the top of the
    slide — the sample's missed-children slide carries the eHealth Africa logo
    at 0.02in — which must survive while the two charts beneath it are replaced.
    A picture is kept only when it sits ENTIRELY above the cutoff, so a chart
    that merely starts high is still swept.
    """
    title = slide.shapes.title
    cutoff = int(_in(keep_pictures_above_in))
    for sh in list(slide.shapes):
        if _same_shape(sh, title):
            continue
        if sh.shape_type == 13:
            try:
                if keep_pictures_above_in and (int(sh.top) + int(sh.height)) <= cutoff:
                    continue
            except (TypeError, ValueError):
                pass
            _delete_shape(sh)
        elif getattr(sh, "has_table", False):
            _delete_shape(sh)
        elif sh.has_text_frame and sh.text_frame.text.strip():
            _delete_shape(sh)


def _textbox(slide, box_in, segments, align=PP_ALIGN.LEFT,
             anchor=MSO_ANCHOR.TOP, wrap: bool = True):
    """Write rich text into a FIXED box.

    `segments` is a list of (text, style); style may carry `size`, `bold`,
    `color`, `bullet`, `align` and `space_before`. One paragraph per segment;
    an empty text makes a blank spacer paragraph.

    The box is pinned and autofit switched OFF. The template's own text boxes
    grow downwards to fit their content, which is how a sample's background
    bullets end up over the map as soon as a campaign has longer LGA names.
    `_fit_prose_pt` shrinks the type instead.
    """
    left, top, width, height = box_in
    tb = slide.shapes.add_textbox(_in(left), _in(top), _in(width), _in(height))
    tf = tb.text_frame
    tf.word_wrap = wrap
    try:
        tf.auto_size = MSO_AUTO_SIZE.NONE
        tf.vertical_anchor = anchor
    except Exception:
        pass
    first = True
    for text, style in segments:
        para = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        para.alignment = style.get("align", align)
        if style.get("space_before"):
            try:
                para.space_before = Pt(style["space_before"])
            except Exception:
                pass
        if not text:
            continue
        run = para.add_run()
        run.text = ("•  " + text) if style.get("bullet") else text
        run.font.size = Pt(style.get("size", 12))
        run.font.bold = bool(style.get("bold"))
        if style.get("color") is not None:
            run.font.color.rgb = style["color"]
    return tb


def _fit_prose_pt(segments, width_in: float, height_in: float, base: float,
                  minimum: float = 7.5, char_w_ratio: float = 0.58,
                  line_h_ratio: float = 1.34) -> float:
    """Largest scale from 1.0 down at which `segments` fit in the box.

    A character-count estimate, not a text metric: python-pptx cannot measure
    rendered text, and this deck is a Google Slides export whose fonts are
    substituted at render time anyway. The ratios are deliberately pessimistic —
    over-estimating shrinks the type slightly, under-estimating runs the
    background bullets off the right edge and a challenges list over the footer
    band. `char_w_ratio` was raised from a measured 0.52 to 0.58 after a real
    build showed exactly that: the substituted face is wider than the one the
    estimate was taken from.

    Returns a MULTIPLIER applied to every segment's own size, so the relative
    weight of headings against body text survives the shrink.
    """
    def lines_at(pt: float, text: str) -> int:
        per_line = max(1, int(width_in * 72.0 / (pt * char_w_ratio)))
        return max(1, -(-len(text) // per_line))

    scale = 1.0
    floor = minimum / base
    while scale > floor:
        used = 0.0
        for text, style in segments:
            pt = style.get("size", base) * scale
            used += lines_at(pt, text) * pt * line_h_ratio / 72.0
            used += style.get("space_before", 0) * scale / 72.0
        if used <= height_in:
            return scale
        scale -= 0.04
    return floor


def _scaled(segments, scale: float):
    return [(t, {**s, "size": s.get("size", 12) * scale,
                 "space_before": s.get("space_before", 0) * scale})
            for t, s in segments]


def _fitted_textbox(slide, box_in, segments, base_pt: float, **kw):
    scale = _fit_prose_pt(segments, box_in[2] - 0.20, box_in[3], base_pt)
    return _textbox(slide, box_in, _scaled(segments, scale), **kw)


def _footnote(slide, text: str):
    _textbox(slide, BOX_FOOTNOTE,
             [(text, {"size": FOOTNOTE_PT, "bold": True,
                      "color": FOOTNOTE_COLOR})])


def _picture(slide, box_in, png: str | None):
    if not png or not os.path.exists(png):
        return None
    left, top, width, height = box_in
    return _fit_picture(slide, png,
                        (_in(left), _in(top), _in(width), _in(height)))


def _missing(slide, box_in, text: str):
    """Stand-in for a figure that could not be produced.

    An empty half-slide reads as a rendering failure; a sentence saying what is
    absent and why is a finding the analyst can act on.
    """
    _textbox(slide, box_in,
             [(text, {"size": 11, "bold": True, "color": FOOTNOTE_COLOR})],
             align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)


def _safe(name: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in str(name))


def _find_map(maps_folder: str | None, keyword: str) -> str | None:
    if not maps_folder or not os.path.isdir(maps_folder):
        return None
    hits = sorted(f for f in os.listdir(maps_folder)
                  if f.lower().endswith(".png") and keyword in f.lower())
    return os.path.join(maps_folder, hits[0]) if hits else None


# The title slide's own placeholder. Its master sets 40pt, which suits the
# template's own two-line title and puts this pipeline's longer one — state,
# report name and campaign — across the layout's accent bar. The size is fitted
# to the box instead, down to a floor that still reads as a cover.
LANDING_TITLE_BASE_PT = 40.0
LANDING_TITLE_MIN_PT = 24.0
LANDING_TITLE_CHAR_W_RATIO = 0.52
LANDING_TITLE_LINE_H_RATIO = 1.22
# The layout paints an accent bar across the lower part of the title
# placeholder. A three-line title runs straight through it, so the size is
# stepped down until the title fits on two — which is also simply how a cover
# reads best.
LANDING_TITLE_MAX_LINES = 2


def _landing_title(shape, text: str) -> float:
    """Write the cover title, shrunk to the lines its placeholder can hold."""
    _set_placeholder_text(shape, text)
    try:
        width_in = int(shape.width) / 914400.0 - 0.2
        height_in = int(shape.height) / 914400.0
    except (TypeError, ValueError):
        width_in, height_in = 8.3, 2.2
    pt = LANDING_TITLE_BASE_PT
    while pt > LANDING_TITLE_MIN_PT:
        per_line = max(1, int(width_in * 72.0 / (pt * LANDING_TITLE_CHAR_W_RATIO)))
        lines = max(1, -(-len(text) // per_line))
        if (lines <= LANDING_TITLE_MAX_LINES
                and lines * pt * LANDING_TITLE_LINE_H_RATIO / 72.0 <= height_in):
            break
        pt -= 1.0
    for para in shape.text_frame.paragraphs:
        for run in para.runs:
            run.font.size = Pt(pt)
    return pt


def _title(slide, text: str):
    """Set a slide's title, clearing whatever the sample left in it.

    `stage6_pptx_report._set_title` rewrites the first run of the first
    paragraph and shrinks the font to keep the title on one line, which is what
    is wanted — but it leaves any FURTHER paragraphs in place. Several of this
    sample's titles carry a trailing empty paragraph, which then renders as a
    blank second line pushing the real title up under the slide's accent bar.
    Cleared first, then handed over.
    """
    title = slide.shapes.title
    if title is None:
        return
    for para in list(title.text_frame.paragraphs[1:]):
        para._p.getparent().remove(para._p)
    _set_title(slide, text)


def _set_placeholder_text(shape, text: str):
    """Replace a placeholder's text, keeping its first run's own formatting."""
    tf = shape.text_frame
    paras = tf.paragraphs
    for p in list(paras[1:]):
        p._p.getparent().remove(p._p)
    para = paras[0]
    if para.runs:
        for extra in list(para.runs[1:]):
            extra._r.getparent().remove(extra._r)
        run = para.runs[0]
    else:
        run = para.add_run()
    run.text = text


# ------------------------------------------------------------------ narrative
def _challenges(summary: dict, timing: dict, dark_wards: pd.DataFrame,
                not_visited: pd.Series, state: str) -> list[tuple[str, dict]]:
    """A Challenges draft written from what the tracking data can observe.

    The sample's challenges are field-operations findings — trackers returned
    late, weak network, devices not deployed in hard-to-reach wards — which no
    dataset in this pipeline records. What the data DOES show is where tracking
    is absent, and that absence is the evidence behind those findings. Each item
    below therefore states the observation and leaves the operational cause to
    the analyst; bracketed prompts mark what still needs a human.
    """
    head = lambda t: (t, {"size": PROSE_HEADING_PT, "bold": True,
                          "space_before": 6})
    body = lambda t: (t, {"size": PROSE_BODY_PT})
    out: list[tuple[str, dict]] = []

    if timing.get("no_tracks"):
        out += [head("Devices Reporting No Field Time"),
                body(f"{timing['no_tracks']:,} teams transmitted no usable track "
                     f"points within the 8:00am–3:00pm window across the "
                     f"campaign. Where a team was known to be in the field this "
                     f"points to a tracker that was switched off, out of "
                     f"battery, or unable to acquire a fix, rather than to work "
                     f"not done.")]
    if len(dark_wards):
        listed = ", ".join(f"{r.Ward} ({r.LGA})"
                           for r in dark_wards.head(6).itertuples())
        more = (f", and {len(dark_wards) - 6} other ward(s)"
                if len(dark_wards) > 6 else "")
        out += [head("Wards With No Tracking Coverage At All"),
                body(f"{len(dark_wards):,} ward(s) recorded no evidence of tracks "
                     f"in any planned settlement: {listed}{more}. [Check whether "
                     f"these are the hard-to-reach or fixed-post wards where "
                     f"trackers were not deployed.]")]
    if len(not_visited):
        worst = not_visited.sort_values(ascending=False)
        listed = ", ".join(f"{lga} ({int(n):,})" for lga, n in worst.head(5).items())
        out += [head("Planned Settlements Not Reached"),
                body(f"{int(not_visited.sum()):,} planned settlements across "
                     f"{len(not_visited)} LGA(s) show no evidence of a team "
                     f"visit, concentrated in {listed}.")]
    if summary["n_no_geo"]:
        out += [head("Settlements That Could Not Be Tracked"),
                body(f"{summary['n_no_geo']:,} of the {summary['planned']:,} "
                     f"planned settlements carry no geo-coordinates and were "
                     f"therefore never loaded to the GTS. Teams were expected to "
                     f"visit them, but their coverage cannot be measured from "
                     f"tracking data.")]
    if not out:
        out = [head("No Tracking Gaps Detected"),
               body(f"Every deployed team returned tracks and every planned "
                    f"settlement in {state} shows evidence of a visit. [Add any "
                    f"operational challenges the field teams reported.]")]
    return out


def _recommendations(summary: dict, timing: dict, dark_wards: pd.DataFrame,
                     not_visited: pd.Series) -> list[tuple[str, dict]]:
    """Recommendations, each tied to a challenge the data actually raised."""
    head = lambda t: (t, {"size": PROSE_HEADING_PT, "bold": True,
                          "space_before": 6})
    body = lambda t: (t, {"size": PROSE_BODY_PT})
    out: list[tuple[str, dict]] = []
    if timing.get("no_tracks"):
        out += [head("Strengthen Tracker Deployment and Retrieval"),
                body("Establish decentralised tracker collection points in the "
                     "LGAs with the largest reporting gap, and reconcile device "
                     "hand-over and return against the team list each day, so a "
                     "missing tracker is known on the day rather than at the end "
                     "of the campaign.")]
    if len(dark_wards):
        out += [head("Plan Earlier for Hard-to-Reach Wards"),
                body("Run a pre-campaign logistics assessment for the wards that "
                     "returned no tracks, deploy trackers and personnel to them "
                     "ahead of day one, and budget the transport and "
                     "accommodation those wards need rather than treating them "
                     "as ordinary wards.")]
    if len(not_visited):
        out += [head("Follow Up the Unreached Settlements"),
                body("Schedule the settlements with no evidence of a visit into "
                     "the mop-up round, prioritised by the estimated target "
                     "children they hold, and re-verify their coverage from "
                     "tracks rather than from a verbal report.")]
    if summary["n_no_geo"]:
        out += [head("Close the Geo-Coordinate Gap"),
                body(f"Capture coordinates for the {summary['n_no_geo']:,} "
                     f"planned settlements that have none, through the field "
                     f"teams during implementation, so the next campaign can "
                     f"measure their coverage instead of assuming it.")]
    out += [head("Sustain Field-Time Compliance"),
            body("Continue the daily evening review of time-spent and coverage "
                 "figures with the LGA teams. The measure is only useful while "
                 "it is fed back to the people it describes, on the day.")]
    return out


def _fill_examples(slide, title_text: str, panels):
    """Two coverage-example panels, labelled, on one slide."""
    if slide is None:
        return
    _title(slide, title_text)
    _strip_content(slide)
    for (label, png), box, label_box in zip(
            panels, (BOX_EXAMPLE_LEFT, BOX_EXAMPLE_RIGHT),
            (BOX_EXAMPLE_LABEL_LEFT, BOX_EXAMPLE_LABEL_RIGHT)):
        _textbox(slide, label_box,
                 [(label, {"size": 14, "bold": True, "align": PP_ALIGN.CENTER})],
                 align=PP_ALIGN.CENTER)
        if png and os.path.exists(png):
            _picture(slide, box, png)
        else:
            _missing(slide, box,
                     f"[{label} example — paste a screenshot of a "
                     f"{label.lower()} settlement here.]")


# ------------------------------------------------------------------- the build
def build_pir_report(state: str, visitation_csv: str, cum_col: str,
                     time_csv: str | None,
                     maps_folder: str | None, output_file: str,
                     logo: str | None = None,
                     campaign_name: str = "Vaccination Tracking Report",
                     charts_folder: str | None = None,
                     state_logo: str | None = None,
                     template_path: str | None = None,
                     voronoi_file: str | None = None,
                     gridded_ta_file: str | None = None,
                     tracks_file: str | None = None) -> str:
    """Build the Post-Implementation Report deck. Returns the output path.

    `template_path` is the sample deck to fill; auto-detected beside this script
    and in the folder above it when not given.

    There is no team-deployment argument: that slide is removed from the deck
    (see the module docstring). `time_csv` is still read, but only for the
    Challenges narrative — teams that transmitted no usable field time is a
    tracking-quality finding a final report should carry.

    `voronoi_file`, `gridded_ta_file` and `tracks_file` drive the Settlement
    Coverage Example slides. Without them those two slides are left as editable
    placeholders rather than dropped, because the analyst may still want to
    paste QGIS screenshots in.

    Anything this build decides — a slide left as a placeholder, an indicator
    that could not be computed — is appended to `build_pir_report.notes` as well
    as printed, so the web app can put it in front of the analyst instead of
    burying it in stdout they never see.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    template_path = template_path or find_default_pir_template(
        here, os.path.dirname(here), os.getcwd())
    if not template_path or not os.path.exists(template_path):
        raise FileNotFoundError(
            "No Post Implementation Report .pptx template found. Keep "
            "'Post Implementation Report 2026.pptx' beside the pipeline, or "
            "pass --pir-template.")
    if not str(template_path).lower().endswith(".pptx"):
        raise ValueError(
            f"The PIR template must be a .pptx deck; got "
            f"{os.path.basename(template_path)}. The report is built by filling "
            f"the sample deck, so a .pdf or .docx cannot be used as one.")
    print(f"PIR template: {template_path}")

    notes: list[str] = []
    build_pir_report.notes = notes

    charts_folder = charts_folder or os.path.join(
        os.path.dirname(os.path.abspath(output_file)) or ".", "charts")
    pir_charts_dir = os.path.join(charts_folder, "pir")
    os.makedirs(pir_charts_dir, exist_ok=True)

    # ---------------------------------------------------------- indicators
    dip = pd.read_csv(visitation_csv, low_memory=False)
    prepared = pa.prepare(dip, cum_col)
    summary = pa.state_summary(prepared)
    lga_tab = pa.lga_class_table(prepared)
    lgas = list(lga_tab.index)
    not_visited = pa.not_visited_by_lga(prepared)
    missed = pa.missed_children_by_lga(prepared)
    # The same two indicators as stacked compositions — every LGA-level chart in
    # the deck is a stacked bar, so these are too. The headline counts above are
    # unchanged and still drive the slide's "N =" totals.
    visitation_split = pa.visitation_split_by_lga(prepared)
    children_split = pa.children_split_by_lga(prepared)
    coords = pa.coordinates_split(prepared)
    timing = pa.time_spent_summary(time_csv)
    dark_wards = pa.wards_without_tracks(prepared)

    print(f"  {summary['planned']:,} planned settlements, "
          f"{summary['visited']:,} visited ({summary['pct']:.1f}%), "
          f"{summary['n_lga']} LGAs, {summary['n_ward']} wards, "
          f"{summary['n_no_geo']:,} without geo-coordinates")

    # ------------------------------------------------------------- charts
    def ch(name: str) -> str:
        return os.path.join(pir_charts_dir, name)

    pc.coverage_donut(
        summary["classes"], ch("state_donut.png"),
        centre=(f"{summary['visited']:,}", "Visited",
                f"{summary['pct']:.1f}% of {summary['planned']:,} Planned"))
    pc.lga_percent_stacked(lga_tab, ch("lga_coverage_mix.png"))
    pc.cumulative_bar(pa.cumulative_bar_counts(summary), ch("cumulative.png"))
    pc.planned_visited_bar(summary["planned"], summary["visited"],
                           summary["not_visited"], ch("geo_coverage.png"))
    if len(visitation_split):
        pc.not_visited_stacked(visitation_split, ch("not_visited.png"))
    if len(children_split):
        pc.missed_children_stacked(children_split, ch("missed_children.png"))
    for lga in lgas:
        st = pa.lga_summary(prepared, lga)
        pc.coverage_donut(
            st["classes"], ch(f"lga_donut_{_safe(lga)}.png"),
            centre=(f"{st['visited']:,}", "Visited",
                    f"{st['pct']:.1f}% of {st['planned']:,} Planned"))
        pc.ward_count_stacked(pa.ward_class_table(prepared, lga),
                              ch(f"lga_wards_{_safe(lga)}.png"))
    print(f"  PIR charts written to {pir_charts_dir}")

    examples: dict = {}
    try:
        import pir_examples
        examples = pir_examples.build_examples(
            prepared, pir_charts_dir, voronoi_file, gridded_ta_file,
            visitation_csv, cum_col, tracks_file, state_name=state)
    except Exception as exc:
        print(f"  coverage example figures unavailable ({exc})")
    if not examples:
        notes.append("The Settlement Coverage Example slides were left as "
                     "placeholders — the settlement extents and gridded "
                     "target areas were not available to this stage.")

    state_map = _find_map(maps_folder, "statewide")
    impl_map = _find_map(maps_folder, "implementation")
    if state_map is None:
        notes.append("No statewide coverage map was found in the maps folder — "
                     "the Coverage Map slide is a placeholder.")

    # ------------------------------------------------- structural edits
    workdir = tempfile.mkdtemp(prefix="gts_pir_")
    unpacked = os.path.join(workdir, "unpacked")
    safe_extract(template_path, unpacked)
    smap = _resolve_parts(unpacked)
    absent = [k for k, v in smap.items() if v is None and k != "lga_slides"]
    if absent:
        print(f"  template has no slide for: {', '.join(absent)} — "
              f"those sections are skipped")
        notes.append("The template is missing slides for: " + ", ".join(absent))

    # Team Deployed/Devices is removed, not filled — see the module docstring.
    if smap.get("deploy"):
        delete_slide(unpacked, smap["deploy"])
        print("  Team Deployed/Devices slide removed — team deployment is a "
              "daily-implementation measure, not a final-report one")

    template_lga_slides = smap["lga_slides"]
    if template_lga_slides and lgas:
        # Reduce the template's hand-made LGA slides to one, then duplicate it
        # once per LGA actually implementing — so a 6-LGA campaign gets 6
        # slides and a 20-LGA campaign gets 20, from the same template.
        keep = template_lga_slides[0]
        for extra in template_lga_slides[1:]:
            delete_slide(unpacked, extra)
        prev = keep
        for _ in range(len(lgas) - 1):
            prev = duplicate_slide(unpacked, keep, after=prev)
        print(f"  LGA slides: template had {len(template_lga_slides)}, "
              f"deck has {len(lgas)} — one per implementing LGA")
    elif template_lga_slides:
        for extra in template_lga_slides:
            delete_slide(unpacked, extra)

    clean_unused_slides(unpacked)
    intermediate = os.path.join(workdir, "intermediate.pptx")
    rezip(unpacked, intermediate)

    # ---------------------------------------------------- content edits
    prs = Presentation(intermediate)
    slides = list(prs.slides)
    # Resolved AGAIN, against the loaded deck — see `_resolve_in_presentation`.
    # Done once here, before any slide is written, because filling a slide
    # rewrites the text the matching reads.
    role_slides = _resolve_in_presentation(prs)
    lga_slides = role_slides["lga_slides"]
    if len(lga_slides) != len(lgas):
        print(f"  WARNING: {len(lga_slides)} LGA slide(s) in the deck for "
              f"{len(lgas)} LGA(s) — the extra ones are left untouched")
        notes.append(f"The deck has {len(lga_slides)} LGA slides for "
                     f"{len(lgas)} implementing LGAs.")

    def slide_for(role: str):
        return role_slides.get(role)

    today = date.today()

    # ---- title slide
    s = slides[0]
    if s.shapes.title is not None:
        _landing_title(
            s.shapes.title,
            f"{state} Post-Implementation GTS Tracking Report - {campaign_name}")
    for ph in s.placeholders:
        if ph.placeholder_format.idx == 1:
            _set_placeholder_text(ph, today.strftime("%B, %Y"))
    if state_logo and os.path.exists(state_logo):
        _picture(s, (8.30, 0.16, 1.20, 0.92), state_logo)

    # ---- Background
    s = slide_for("background")
    if s is not None:
        _title(s, T_BACKGROUND)
        _strip_content(s)
        if impl_map:
            _picture(s, BOX_BACKGROUND_MAP, impl_map)
        else:
            _missing(s, BOX_BACKGROUND_MAP,
                     "[State implementation map — re-run stage 4 with the "
                     "state boundary layer to generate it.]")
        bullets = [
            (f"eHealth Africa supported the {state} {campaign_name} with "
             f"vaccination tracking across the following:", {"size": 14}),
            ("", {"size": 6}),
            (f"{summary['n_lga']:,} LGAs", {"size": 14, "bullet": True}),
            (f"{summary['n_ward']:,} Wards", {"size": 14, "bullet": True}),
            (f"{summary['n_geo']:,} settlements with geo-coordinates were "
             f"tracked", {"size": 14, "bullet": True}),
            (f"{summary['planned']:,} settlements planned in total, with and "
             f"without geo-coordinates", {"size": 14, "bullet": True}),
        ]
        _fitted_textbox(s, BOX_BACKGROUND_TEXT, bullets, base_pt=14,
                        anchor=MSO_ANCHOR.MIDDLE)

    # ---- State Settlement Coverage
    s = slide_for("state_coverage")
    if s is not None:
        _title(s, T_STATE_COVERAGE)
        _strip_content(s)
        _picture(s, BOX_STATE_DONUT, ch("state_donut.png"))
        _picture(s, BOX_STATE_LGA_BAR, ch("lga_coverage_mix.png"))
        _footnote(s, "*Coverage class is measured from the gridded target-area "
                     "cells the teams' tracks intersected, cumulative across the "
                     "campaign.")

    # ---- Cumulative Settlements Coverage
    s = slide_for("cumulative")
    if s is not None:
        _title(s, T_CUMULATIVE)
        _strip_content(s)
        c = summary["classes"]
        low = int(c.get("Low Coverage", 0)) + int(c.get("Very Low Coverage", 0))
        _fitted_textbox(s, BOX_CUM_NARRATIVE, [(
            f"{summary['visited']:,} ({summary['pct']:.0f}%) of the "
            f"{summary['planned']:,} cumulative settlements planned were visited "
            f"across the {summary['n_lga']} LGAs. "
            f"{int(c.get('Fully Covered', 0)):,} were fully covered, "
            f"{int(c.get('Partially Covered', 0)):,} partially covered and "
            f"{low:,} reached at low coverage.",
            {"size": 14, "bold": True})], base_pt=14)
        _picture(s, BOX_CUM_CHART, ch("cumulative.png"))

    # ---- Potentially Missed Children
    s = slide_for("missed")
    if s is not None:
        _title(s, f"{state} - Potentially Missed Children by LGA")
        _strip_content(s, keep_pictures_above_in=0.75)
        if len(visitation_split):
            _textbox(s, BOX_MISSED_N_LEFT,
                     [(f"N = {int(not_visited.sum()):,} not visited",
                       {"size": 13, "bold": True})])
            _picture(s, BOX_MISSED_LEFT, ch("not_visited.png"))
        else:
            _missing(s, BOX_MISSED_LEFT,
                     "Every planned settlement shows evidence of a visit — "
                     "there are no not-visited settlements to chart.")
        if len(children_split):
            _textbox(s, BOX_MISSED_N_RIGHT,
                     [(f"N = {int(missed.sum()):,} potentially missed",
                       {"size": 13, "bold": True})])
            _picture(s, BOX_MISSED_RIGHT, ch("missed_children.png"))
            _footnote(s, f"*Population data is from the {state} State settlement "
                         f"list; children in settlements with no evidence of a "
                         f"visit count as potentially missed.")
        else:
            _missing(s, BOX_MISSED_RIGHT,
                     "[No target-population field in the settlement list — "
                     "potentially missed children could not be estimated.]")
            notes.append("Potentially missed children could not be estimated: "
                         "the settlement list carries no target or population "
                         "column.")

    # ---- settlements without geo-coordinates
    s = slide_for("no_geo")
    if s is not None:
        _title(s, T_NO_GEO)
        _strip_content(s)
        _picture(s, BOX_GEO_CHART, ch("geo_coverage.png"))
        gt = coords[coords["LGA"] == "Grand Total"].iloc[0]
        bullets = [
            (f"{summary['planned']:,} planned settlements were uploaded for "
             f"implementation.", {"size": 13, "bold": True, "bullet": True}),
            (f"{int(gt['With Coordinates']):,} of them carry geo-coordinates and "
             f"were loaded to the GTS for tracking.",
             {"size": 13, "bold": True, "bullet": True}),
            (f"{int(gt['Without Coordinates']):,} without geo-coordinates were "
             f"not tracked on the GTS, though the vaccination teams carrying "
             f"trackers were expected to visit every planned settlement.",
             {"size": 13, "bold": True, "bullet": True}),
            (f"Cumulative coverage across all {summary['planned']:,} planned "
             f"settlements: {summary['visited']:,} visited, "
             f"{summary['not_visited']:,} not visited.",
             {"size": 13, "bold": True, "bullet": True}),
        ]
        _fitted_textbox(s, BOX_GEO_TEXT, bullets, base_pt=13,
                        anchor=MSO_ANCHOR.MIDDLE)
        _footnote(s, "*A settlement with no coordinates cannot be measured from "
                     "tracking data; its visitation is not asserted.")

    # ---- statewide coverage map
    s = slide_for("coverage_map")
    if s is not None:
        _title(s, T_COVERAGE_MAP)
        _strip_content(s)
        if state_map:
            _picture(s, BOX_STATE_MAP, state_map)
        else:
            _missing(s, BOX_STATE_MAP,
                     "[Statewide coverage map — none found in the maps "
                     "folder.]")

    # ---- one slide per LGA
    for lga, s in zip(lgas, lga_slides):
        _title(s, f"{lga} LGA Settlements Coverage")
        _strip_content(s)
        _picture(s, BOX_LGA_DONUT, ch(f"lga_donut_{_safe(lga)}.png"))
        _picture(s, BOX_LGA_WARD_BAR, ch(f"lga_wards_{_safe(lga)}.png"))

    # ---- settlement coverage examples
    _fill_examples(slide_for("examples"), T_EXAMPLES,
                   [("Fully Covered", examples.get("fully")),
                    ("Partially Covered", examples.get("partially"))])
    _fill_examples(slide_for("examples_cont"), T_EXAMPLES_CONT,
                   [("Low Coverage", examples.get("low")),
                    ("Not Visited", examples.get("not_visited"))])

    # ---- challenges & recommendations
    s = slide_for("challenges")
    if s is not None:
        _title(s, T_CHALLENGES)
        _strip_content(s)
        _fitted_textbox(s, BOX_PROSE,
                        _challenges(summary, timing, dark_wards,
                                    not_visited, state),
                        base_pt=PROSE_HEADING_PT)
    s = slide_for("recommendation")
    if s is not None:
        _title(s, T_RECOMMENDATION)
        _strip_content(s)
        _fitted_textbox(s, BOX_PROSE,
                        _recommendations(summary, timing, dark_wards,
                                         not_visited),
                        base_pt=PROSE_HEADING_PT)

    # ---- photo slides — cleared for the analyst
    for role, slide_title in (("deploy_pics", T_DEPLOY_PICS),
                              ("erm_pics", T_ERM_PICS)):
        s = slide_for(role)
        if s is None:
            continue
        caption, prompt = PHOTO_SLIDES[slide_title]
        _title(s, slide_title)
        _strip_content(s)
        _textbox(s, BOX_PHOTO_CAPTION,
                 [(caption, {"size": 12, "bold": True,
                             "align": PP_ALIGN.CENTER})],
                 align=PP_ALIGN.CENTER)
        _missing(s, BOX_PHOTOS, prompt)

    # ------------------------------------------------------------ checks
    # Slide 1 is skipped: its overlapping decorative shapes are the template's
    # own design and not this module's to change.
    overlaps = _overlap_report(prs, skip_slides=(1,))
    for issue in overlaps:
        print(f"  LAYOUT: {issue}")
    if overlaps:
        notes.append(f"{len(overlaps)} overlapping shape pair(s) were reported "
                     f"at build time — see the run log.")

    prs.save(output_file)

    # Every content picture on the template's slides was replaced, but deleting
    # a picture SHAPE leaves the image itself in the package. Without this the
    # report ships the sample's own 25 MB of charts, maps and photographs —
    # unreachable, but carried in every copy anyone emails.
    try:
        dropped, freed = prune_unused_media(output_file)
        if dropped:
            print(f"  removed {dropped} unreferenced image(s) inherited from "
                  f"the template ({freed / 1e6:.1f} MB)")
    except Exception as exc:
        print(f"  could not prune unused template images ({exc}) — the deck is "
              f"correct but larger than it needs to be")

    size_mb = os.path.getsize(output_file) / 1e6
    print(f"Saved {output_file} — {len(slides)} slides, {size_mb:.1f} MB")
    return output_file


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Build the Post-Implementation Report (PIR) deck")
    ap.add_argument("--state", required=True)
    ap.add_argument("--visitation-csv", required=True)
    ap.add_argument("--cum-col", required=True)
    ap.add_argument("--time-csv", default=None,
                    help="Time Spent Analysis CSV — read only for the "
                         "Challenges narrative")
    ap.add_argument("--maps-folder", default=None)
    ap.add_argument("--charts-folder", default=None)
    ap.add_argument("--output", required=True)
    ap.add_argument("--logo", default=None)
    ap.add_argument("--state-logo", default=None)
    ap.add_argument("--campaign-name", default="Vaccination Tracking Report")
    ap.add_argument("--template", default=None,
                    help="Post Implementation Report .pptx to fill — "
                         "auto-detected beside this script if not given")
    ap.add_argument("--voronoi", default=None)
    ap.add_argument("--gridded-ta", default=None)
    ap.add_argument("--tracks-file", default=None,
                    help="Merged track export (CSV or spatial) — used only for "
                         "the track points on the coverage-example figures")
    a = ap.parse_args()
    build_pir_report(
        a.state, a.visitation_csv, a.cum_col, a.time_csv,
        a.maps_folder, a.output, a.logo, campaign_name=a.campaign_name,
        charts_folder=a.charts_folder, state_logo=a.state_logo,
        template_path=a.template,
        voronoi_file=a.voronoi, gridded_ta_file=a.gridded_ta,
        tracks_file=a.tracks_file)
