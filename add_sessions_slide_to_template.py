"""Add the "Tracking Sessions by Team & Device" slide to the reporting template.

Why this is a script and not a hand-edited .pptx
------------------------------------------------
Stage 6 no longer fabricates slides — it fills the approved template and
nothing else. So a new slide has to exist in the TEMPLATE before the pipeline
can report on it, which means editing the file the team signed off on.

Doing that by hand in PowerPoint produces a 59 MB binary with no record of what
changed. This script makes the edit reproducible and reviewable: it takes the
approved template, clones the Team Time Efficiency slide (title placeholder,
narration box, eHA branding and master, all inherited rather than recreated),
retitles it, strips the cloned chart, and writes a NEW file.

The approved template is never modified. The output is a proposal for review;
once approved, replace the original and update `PPTX_TEMPLATE_NAME` in app.py.

    python add_sessions_slide_to_template.py \
        --template ../eHA_Revised_reporting_template.pptx \
        --output   ../eHA_Revised_reporting_template_v2.pptx

Where the slide goes and why
----------------------------
Directly after Team Time Efficiency, so the deck reads deployment -> how long
they tracked -> how efficiently -> in how many pieces and on whose device, and
only then moves on to coverage. Putting it after the coverage slides would
separate it from the three slides that answer the same question about the same
teams.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile

from pptx import Presentation

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pptx_xml_utils import duplicate_slide, rezip, safe_extract

# The title stage 6 looks for. Defined here because this script is what writes
# it into the template; stage6_pptx_report imports the same constant so the two
# can never disagree about the slide's name.
SESSIONS_SLIDE_TITLE = "Tracking Sessions by Team & Device"
DEVICE_LGA_SLIDE_TITLE = "Tracks per Device (IMEI) by LGA"
TEAM_LGA_SLIDE_TITLE = "Tracks per Team Code by LGA"

# Cloned from this slide: title + narration + one picture is the closest layout
# in the deck to "title + narration + table(s)", and cloning keeps the
# template's own fonts, background and footer rather than inventing a new look.
SOURCE_SLIDE_TITLE = "Team Time Efficiency"

# The slides to add, in order, each inserted after the one before it. Placeholder
# narration means the template opens as a finished deck rather than a set of
# blanks; stage 6 overwrites all of it with the run's actual figures.
EFFICIENCY_SLIDE_TITLE = "Team Time Efficiency"

# Each entry is (title, placeholder narration, title of the slide to clone).
# Cloning from a slide that already exists in the deck is what keeps the
# branding, master and fonts the template's own.
NEW_SLIDES = [
    # The efficiency slide is listed FIRST and cloned from Team Deployment,
    # because the superseded 24-slide template has no efficiency slide to clone
    # from — and that template is exactly the one that needs this repair. On a
    # revised template it is already present and this entry is skipped.
    (EFFICIENCY_SLIDE_TITLE,
     "Teams plotted by time in the field against grid-weighted coverage. Teams "
     "logging more than 2 hours with under 50% of their assigned grid covered "
     "are flagged, as are teams whose tracks never left a single grid cell.",
     "Team Deployed"),
    (SESSIONS_SLIDE_TITLE,
     "Tracking sessions per team and per device for the reporting day. Teams "
     "and devices are ranked with shared devices first: a device transmitting "
     "under more than one team code invalidates both teams' tracks.",
     EFFICIENCY_SLIDE_TITLE),
    (DEVICE_LGA_SLIDE_TITLE,
     "Unique devices, tracks and pings per LGA, with each device attributed to "
     "the LGA its GPS points fall inside. Devices are identified by IMEI, so "
     "repeated submissions from one handset are counted once.",
     EFFICIENCY_SLIDE_TITLE),
    (TEAM_LGA_SLIDE_TITLE,
     "Unique team codes, tracks and pings per LGA, with each team attributed "
     "to the LGA its GPS points fall inside. A team that worked more than one "
     "LGA appears in each, so the column sums above the state total.",
     EFFICIENCY_SLIDE_TITLE),
]


def _slide_index_by_title(prs, title: str) -> int | None:
    for i, slide in enumerate(prs.slides):
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text_frame.text.strip():
                if shape.text_frame.text.strip() == title:
                    return i
                break
    return None


def _slide_part_name(prs, index: int) -> str:
    return os.path.basename(str(list(prs.slides)[index].part.partname))


def _retitle(slide, title_text, narration_text):
    """Set a cloned slide's title and narration, and strip the cloned chart."""
    # The title is held as the lxml ELEMENT, not the wrapper: python-pptx builds
    # a fresh wrapper on every access, so `shape is not title` is always True
    # and the title placeholder gets overwritten with the narration text —
    # which is exactly what happened the first time this was written.
    title = slide.shapes.title
    title_el = title._element if title is not None else None
    if title is not None and title.text_frame.paragraphs[0].runs:
        para = title.text_frame.paragraphs[0]
        for extra in list(para.runs[1:]):
            extra._r.getparent().remove(extra._r)
        para.runs[0].text = title_text

    for shape in list(slide.shapes):
        # The cloned scatter chart has no equivalent on these slides — stage 6
        # puts tables in the content band instead.
        if shape.shape_type == 13:
            shape._element.getparent().remove(shape._element)
        elif (shape.has_text_frame and shape._element is not title_el
              and shape.text_frame.text.strip()):
            para = shape.text_frame.paragraphs[0]
            if para.runs:
                for extra in list(para.runs[1:]):
                    extra._r.getparent().remove(extra._r)
                para.runs[0].text = narration_text
            for extra_para in list(shape.text_frame.paragraphs[1:]):
                extra_para._p.getparent().remove(extra_para._p)


def _index_by_title_prefix(prs, prefix: str) -> int | None:
    """First slide whose leading text starts with `prefix`, case-insensitively.

    A prefix rather than an exact match, because the slide being cloned from
    may carry a campaign day in its title ("Team Deployed/Team Reported" is
    stable, but "Time Spent Analysis - Day 2" is not).
    """
    low = prefix.lower()
    for i, slide in enumerate(prs.slides):
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text_frame.text.strip():
                if shape.text_frame.text.strip().lower().startswith(low):
                    return i
                break
    return None


def add_sessions_slide(template_path: str, output_path: str) -> str:
    prs = Presentation(template_path)

    missing = [entry for entry in NEW_SLIDES
               if _index_by_title_prefix(prs, entry[0]) is None]
    if not missing:
        print(f"every new slide is already in {template_path} — nothing to do")
        if os.path.abspath(template_path) != os.path.abspath(output_path):
            shutil.copy2(template_path, output_path)
        return output_path

    workdir = tempfile.mkdtemp(prefix="gts_tmpl_")
    try:
        # One slide per pass, each pass a complete unpack -> duplicate ->
        # repack -> retitle -> save round trip on its own file. Sequential
        # because a later slide may clone one an earlier pass just created:
        # the sessions slides clone Team Time Efficiency, which the first pass
        # adds when the template is the old 24-slide deck that lacks it.
        current = template_path
        # The slide each new one is inserted AFTER. It advances to the slide
        # just added, so the block comes out in NEW_SLIDES order; inserting
        # every clone directly after its shared source reversed them.
        after_title = None
        for step, (title_text, narration_text, source_title) in enumerate(missing):
            prs_now = Presentation(current)
            source_i = _index_by_title_prefix(prs_now, source_title)
            if source_i is None:
                print(f"  SKIPPED '{title_text}' — no slide titled like "
                      f"'{source_title}' to clone its layout from")
                continue
            anchor_i = (_index_by_title_prefix(prs_now, after_title)
                        if after_title else None)
            if anchor_i is None:
                anchor_i = source_i
            source = _slide_part_name(prs_now, source_i)
            anchor = _slide_part_name(prs_now, anchor_i)

            unpacked = os.path.join(workdir, f"unpacked_{step}")
            safe_extract(current, unpacked)
            duplicate_slide(unpacked, source, after=anchor)
            packed = os.path.join(workdir, f"step_{step}.pptx")
            rezip(unpacked, packed)

            prs_after = Presentation(packed)
            _retitle(list(prs_after.slides)[anchor_i + 1], title_text, narration_text)
            current = os.path.join(workdir, f"done_{step}.pptx")
            prs_after.save(current)
            after_title = title_text
            print(f"  inserted '{title_text}' as slide {anchor_i + 2} "
                  f"(cloned from '{source_title}')")

        shutil.copy2(current, output_path)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    # Print the resulting deck order. This IS the review artefact: the slide
    # map stage 6 depends on is positional, so the order below is what has to
    # be checked before the template is adopted.
    out = Presentation(output_path)
    print(f"\n{output_path}")
    for i, s in enumerate(out.slides, 1):
        text = next((sh.text_frame.text.strip().split("\n")[0]
                     for sh in s.shapes
                     if sh.has_text_frame and sh.text_frame.text.strip()), "")
        marker = "  <-- new" if text in {s[0] for s in NEW_SLIDES} else ""
        print(f"  {i:3d}  {text}{marker}")
    return output_path


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    base = os.path.dirname(here)
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--template",
                    default=os.path.join(base, "eHA_Revised_reporting_template.pptx"),
                    help="the approved template (never modified)")
    ap.add_argument("--output",
                    default=os.path.join(base, "eHA_Revised_reporting_template_v2.pptx"),
                    help="where to write the proposed template")
    a = ap.parse_args()
    if not os.path.exists(a.template):
        raise SystemExit(f"Template not found: {a.template}")
    add_sessions_slide(a.template, a.output)
