"""Diagnose — and repair — a deck that is missing Team Time Efficiency.

Two jobs, and you almost always want both:

    python fix_efficiency_slide.py --deck Zamfara_Day_1_Report.pptx \\
                                   --charts Day1_out/charts

  1. It says WHY the slide is not in that deck. There are only four reasons and
     it names the one that applies, rather than leaving you to guess:

       - the deck was built from the superseded 24-slide template, which has
         no Team Time Efficiency slide at all
       - the slide exists but its chart is missing
       - team_efficiency.png was never rendered, so stage 4's analysis did not
         run either
       - the code that builds the deck is an older copy still cached by
         Streamlit (it reports the stage 6 version it can see)

  2. With `--repair`, it INSERTS the slide into the existing deck — cloning the
     Team Deployment slide for its layout, so the branding, master and fonts
     are the deck's own — and drops `team_efficiency.png` onto it. The deck you
     already generated becomes the deck you needed, without re-running the
     pipeline.

Repair writes to a new file by default; the input deck is not modified.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile

from pptx import Presentation
from pptx.util import Emu, Pt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pptx_xml_utils import duplicate_slide, rezip, safe_extract

TITLE = "Team Time Efficiency"
CHART_NAME = "team_efficiency.png"
CLONE_FROM = "Team Deployed"

# Same band the pipeline uses, so a repaired slide sits where a generated one
# would. Imported rather than restated where possible.
try:
    from stage6_pptx_report import (CONTENT_BOTTOM_IN, EFF_CHART_LEFT_IN,
                                    EFF_CHART_WIDTH_IN, EFF_METHOD_NARRATION,
                                    NARR_TOP_IN)
    _HAVE_STAGE6 = True
except Exception:                                    # pragma: no cover
    CONTENT_BOTTOM_IN, EFF_CHART_LEFT_IN, EFF_CHART_WIDTH_IN = 5.18, 0.30, 9.40
    NARR_TOP_IN = 1.08
    EFF_METHOD_NARRATION = (
        "Team Time Efficiency measures each team's actual GPS start and end "
        "time against the fixed 08:00-16:00 operational window. A team is "
        "flagged for a late start, an early stop, insufficient operational "
        "time, excessive idle time, or a missing or invalid timestamp.")
    _HAVE_STAGE6 = False

EFF_CONTENT_TOP_IN = 2.05


def _titles(prs):
    out = []
    for slide in prs.slides:
        text = ""
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text_frame.text.strip():
                text = shape.text_frame.text.strip().split("\n")[0]
                break
        out.append(text)
    return out


def _index_of(prs, prefix):
    low = prefix.lower()
    for i, title in enumerate(_titles(prs)):
        if title.lower().startswith(low):
            return i
    return None


def diagnose(deck_path, charts_dir=None):
    prs = Presentation(deck_path)
    titles = _titles(prs)
    have_slide = _index_of(prs, TITLE)
    chart = os.path.join(charts_dir, CHART_NAME) if charts_dir else None
    have_chart = bool(chart and os.path.exists(chart))

    print(f"deck            {deck_path}")
    print(f"slides          {len(titles)}")
    print(f"efficiency slide{'':<1} "
          f"{'present at slide ' + str(have_slide + 1) if have_slide is not None else 'ABSENT'}")
    if charts_dir:
        print(f"{CHART_NAME:<16} {'found' if have_chart else 'NOT FOUND'} in {charts_dir}")
    print(f"stage6 loaded   {'yes' if _HAVE_STAGE6 else 'no (running standalone)'}")
    if _HAVE_STAGE6:
        import stage6_pptx_report as s6
        print(f"stage6 file     {s6.__file__}")
        print(f"stage6 knows the slide-by-title fix: "
              f"{'yes' if hasattr(s6, '_resolve_slide_map') else 'NO — this is an old copy'}")

    print()
    if have_slide is not None:
        print("DIAGNOSIS: the slide IS in this deck. If you cannot see it, check")
        print("  you are opening the file the run actually wrote — the app names")
        print("  it {State}_Day_{N}_Report.pptx in the output folder.")
        return 0

    deploy_i = _index_of(prs, CLONE_FROM)
    if charts_dir and not have_chart:
        print(f"DIAGNOSIS: {CHART_NAME} is not in the charts folder, so stage 4's")
        print("  efficiency analysis did not run. Team Time Efficiency needs the")
        print("  raw merged track export (clock start/end times) — check the run")
        print("  actually had `--skip-merge` off, or a merged_tracks.csv present.")
    elif len(titles) <= 25 and _index_of(prs, "Tracking Sessions") is None:
        print("DIAGNOSIS: this deck was built from the superseded 24-slide")
        print("  template, which has no Team Time Efficiency slide. Point the app")
        print("  at eHA_Revised_reporting_template.pptx, or repair this deck below.")
    else:
        print("DIAGNOSIS: the template has the slide but this deck does not, which")
        print("  means the build dropped it. If the run log has no line starting")
        print("  'Team Time Efficiency:', the code that produced this deck predates")
        print("  the fix — restart Streamlit so it reloads the pipeline modules.")

    if deploy_i is None:
        print("\n  NOTE: no 'Team Deployed' slide either, so --repair cannot clone a")
        print("  layout from this deck.")
    else:
        print(f"\n  Repairable: --repair will insert it after slide {deploy_i + 1}.")
    return 1


def _narration_for(tracks_file, visitation_csv):
    """This run's actual figures, worded exactly as stage 6 words them.

    Falls back to the methodology sentence when the analysis cannot run — a
    slide that states the method is honest, a slide quoting numbers that were
    not measured is not.
    """
    if not tracks_file or not visitation_csv:
        return EFF_METHOD_NARRATION, False
    try:
        from team_efficiency import analyse
        from team_daily_time import scope_from_visitation
        import pandas as pd
        dip = pd.read_csv(visitation_csv, low_memory=False)
        m = analyse(tracks_file, dip, report_date="latest",
                   scope=scope_from_visitation(dip))["meta"]
    except Exception as exc:
        print(f"  could not recompute the figures ({exc}) — using the "
              f"methodology narration instead")
        return EFF_METHOD_NARRATION, False

    text = (f"{m['flagged_teams']:,} of {m['total_teams']:,} teams "
            f"({m['flagged_pct']:.1%}) were flagged against the {m['window']} "
            f"operational window — average Team Time Efficiency was "
            f"{m['avg_efficiency_pct']:.1f}%.")
    reason_counts = m.get("reason_counts") or {}
    if reason_counts:
        top = sorted(reason_counts.items(), key=lambda kv: kv[1], reverse=True)
        lead_reason, lead_n = top[0]
        text += f" The leading reason was {lead_reason.lower()} ({lead_n:,} teams)"
        others = top[1:]
        if others:
            MAX_SECONDARY_REASONS = 2
            shown = ", ".join(f"{r.lower()} ({n:,})"
                              for r, n in others[:MAX_SECONDARY_REASONS])
            extra = len(others) - MAX_SECONDARY_REASONS
            tail = f", and {extra} other reason{'s' if extra != 1 else ''}" \
                if extra > 0 else ""
            text += f", followed by {shown}{tail}."
        else:
            text += "."
    else:
        text += " No team was flagged for this reporting date."
    return text, True


def repair(deck_path, charts_dir, out_path, tracks_file=None, visitation_csv=None):
    prs = Presentation(deck_path)
    if _index_of(prs, TITLE) is not None:
        print(f"'{TITLE}' is already in {deck_path} — nothing to repair")
        if os.path.abspath(deck_path) != os.path.abspath(out_path):
            shutil.copy2(deck_path, out_path)
        return out_path

    deploy_i = _index_of(prs, CLONE_FROM)
    if deploy_i is None:
        raise SystemExit(
            f"Cannot repair: no slide titled like '{CLONE_FROM}' to clone a "
            f"layout from, so a new slide would not carry the deck's branding.")
    chart = os.path.join(charts_dir, CHART_NAME)
    if not os.path.exists(chart):
        raise SystemExit(f"Cannot repair: {chart} does not exist.")

    narration, real = _narration_for(tracks_file, visitation_csv)
    print("  narration: " + ("this run's figures" if real else "methodology only"))

    source = os.path.basename(str(list(prs.slides)[deploy_i].part.partname))
    workdir = tempfile.mkdtemp(prefix="gts_fix_")
    try:
        unpacked = os.path.join(workdir, "unpacked")
        safe_extract(deck_path, unpacked)
        duplicate_slide(unpacked, source, after=source)
        packed = os.path.join(workdir, "packed.pptx")
        rezip(unpacked, packed)

        prs2 = Presentation(packed)
        slide = list(prs2.slides)[deploy_i + 1]

        # Title. Held as the lxml element because python-pptx hands out a new
        # wrapper on every access, so `is not title` never holds.
        title = slide.shapes.title
        title_el = title._element if title is not None else None
        if title is not None and title.text_frame.paragraphs[0].runs:
            para = title.text_frame.paragraphs[0]
            for extra in list(para.runs[1:]):
                extra._r.getparent().remove(extra._r)
            para.runs[0].text = TITLE

        # Narration: the methodology, not invented numbers. The per-team counts
        # live in the workbook's Team Time Efficiency tab.
        for shape in list(slide.shapes):
            if shape.shape_type == 13:
                shape._element.getparent().remove(shape._element)
            elif (shape.has_text_frame and shape._element is not title_el
                  and shape.text_frame.text.strip()):
                para = shape.text_frame.paragraphs[0]
                if para.runs:
                    for extra in list(para.runs[1:]):
                        extra._r.getparent().remove(extra._r)
                    para.runs[0].text = narration
                    para.runs[0].font.size = Pt(11 if real else 12)
                for extra_para in list(shape.text_frame.paragraphs[1:]):
                    extra_para._p.getparent().remove(extra_para._p)

        # The chart, contain-fitted into the content band so it cannot cross
        # the footer.
        from PIL import Image
        with Image.open(chart) as im:
            iw, ih = im.size
        box_l, box_t = EFF_CHART_LEFT_IN, EFF_CONTENT_TOP_IN
        box_w = EFF_CHART_WIDTH_IN
        box_h = max(1.2, CONTENT_BOTTOM_IN - EFF_CONTENT_TOP_IN)
        scale = min(box_w / iw, box_h / ih)
        w, h = iw * scale, ih * scale
        slide.shapes.add_picture(
            chart, Emu(int((box_l + (box_w - w) / 2) * 914400)),
            Emu(int((box_t + (box_h - h) / 2) * 914400)),
            Emu(int(w * 914400)), Emu(int(h * 914400)))

        prs2.save(out_path)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print(f"inserted '{TITLE}' as slide {deploy_i + 2}")
    print(f"saved {out_path}")
    for i, title_text in enumerate(_titles(Presentation(out_path))[:8], 1):
        mark = "  <-- new" if title_text == TITLE else ""
        print(f"  {i:3d}  {title_text}{mark}")
    return out_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--deck", required=True, help="the generated .pptx report")
    ap.add_argument("--charts", default=None,
                    help="the run's charts folder (holds team_efficiency.png)")
    ap.add_argument("--visitation", default=None,
                    help="the run's {State}_day_{N}_settlement_visitation.csv — "
                         "together with --tracks, supplies the real figures for "
                         "the narration; without either the slide states the "
                         "methodology instead")
    ap.add_argument("--tracks", default=None,
                    help="the run's merged_tracks.csv — Team Time Efficiency "
                         "needs raw GPS timestamps, which only exist here")
    ap.add_argument("--repair", action="store_true",
                    help="insert the slide into the deck")
    ap.add_argument("--output", default=None,
                    help="where to write the repaired deck "
                         "(default: <deck>_with_efficiency.pptx)")
    a = ap.parse_args()

    if not os.path.exists(a.deck):
        raise SystemExit(f"Deck not found: {a.deck}")

    status = diagnose(a.deck, a.charts)
    if a.repair:
        if not a.charts:
            raise SystemExit("--repair needs --charts, to find team_efficiency.png")
        print()
        repair(a.deck, a.charts,
               a.output or a.deck.replace(".pptx", "_with_efficiency.pptx"),
               tracks_file=a.tracks, visitation_csv=a.visitation)
    sys.exit(0 if not a.repair else 0)
