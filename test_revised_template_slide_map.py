"""Guards the slide map against eHA_Revised_reporting_template.pptx.

The failure this exists to catch is silent. Stage 6 addresses slides by
POSITION (`SLIDE_*` constants, then `slides[n]`), so pointing it at a deck
whose order differs does not raise — it writes the daily-coverage figures onto
Time Spent Analysis, the cumulative donut onto Team Time Efficiency, and the
statewide map onto Cumulative Settlements Coverage, then saves a deck that
opens cleanly and is wrong on every slide from 4 down. That is exactly what
happened while the app was still defaulting to the superseded 24-slide
"organization's reporting template.pptx".

So this test asserts on the ORDER AND TITLES of the built deck, not on whether
the build completed. Three paths, because two slides are conditional:

  * full deck        — efficiency and target-population data both available
  * no efficiency    — Team Time Efficiency deleted, everything below shifts up
  * neither          — Target Population deleted too

Run: python test_revised_template_slide_map.py
"""
import os
import sys
import tempfile

import pandas as pd
from pptx import Presentation

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stage6_pptx_report as s6

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(os.path.dirname(HERE), "eHA_Revised_reporting_template.pptx")
# The proposed template carrying the Tracking Sessions slide. Both are tested:
# an analyst running last month's approved template must still get a correct
# deck, just without that slide.
TEMPLATE_V2 = os.path.join(os.path.dirname(HERE),
                           "eHA_Revised_reporting_template_v2.pptx")
SESSIONS_TITLE = "Tracking Sessions by Team & Device"
DEVICE_LGA_TITLE = "Tracks per Device (IMEI) by LGA"
TEAM_LGA_TITLE = "Tracks per Team Code by LGA"

STATE = "Zamfara"
DAY = 1
LGAS = ["Anka", "Bakura", "Gusau"]


# --------------------------------------------------------------- fixtures
def _visitation_csv(path, with_target: bool):
    """A settlement list stage 2 could plausibly have produced.

    `set target` / `number of household` are what target_population reads; drop
    them and the Target Population slide must be deleted rather than left
    showing the template's sample numbers.
    """
    rows = []
    for i, lga in enumerate(LGAS):
        for j in range(20):
            visited = (i + j) % 3 != 0
            rows.append({
                "ogc_fid": len(rows) + 1,
                "state": STATE,
                "lga": lga,
                "wardname": f"{lga} Ward {j % 4}",
                "team_code": f"{lga[:3].upper()}-{j:03d}",
                "settlements": f"{lga} Settlement {j}",
                "latitude": 11.0 + j / 100,
                "longitude": 6.0 + i / 100,
                # stage 2 writes these two as Visited / Not Visited TEXT, not
                # flags — _coverage_stats counts the string. A numeric fixture
                # builds a deck reading "0 of 60", which looks like a slide-map
                # fault and isn't one.
                "day of activity": "Day1",
                "day_1": 1.0 if visited else None,
                "day_1_daily": "Visited" if visited else "Not Visited",
                "day_1_cumm": "Visited" if visited else "Not Visited",
                "Coverage": 80.0 if visited else 0.0,
                "Settlement Coverage": "Fully Covered" if visited else "No Coverage",
                "set population": 900 + j,
                "set target": 200 + j,
                "number of household": 100 + j,
            })
    df = pd.DataFrame(rows)
    if not with_target:
        df = df.drop(columns=["set target", "number of household", "set population"])
    df.to_csv(path, index=False)
    return path


def _deploy_csv(path):
    pd.DataFrame([
        {"LGA": lga, "Teams Deployed": 100, "Teams Reported": 70,
         "Teams Pending": 30, "Reporting %": 0.7} for lga in LGAS
    ] + [{"LGA": "Grand Total", "Teams Deployed": 300, "Teams Reported": 210,
          "Teams Pending": 90, "Reporting %": 0.7}]).to_csv(path, index=False)
    return path


def _time_csv(path):
    pd.DataFrame([
        {"Time Spent": "0 (No evidence of tracks)", "Number of Teams": 90},
        {"Time Spent": "< 1 hr", "Number of Teams": 60},
        {"Time Spent": "1 - 2 hrs", "Number of Teams": 100},
        {"Time Spent": ">2 hrs", "Number of Teams": 50},
        {"Time Spent": "Grand Total", "Number of Teams": 300},
    ]).to_csv(path, index=False)
    return path


def _tracks_csv(path):
    """A merged track export matching the settlement fixture's teams and LGAs.

    Session structure is deliberate: every fourth team transmits in five
    separate bursts, so the Time Spent slide has a non-zero fragmented count to
    report and the table's sessions column has something to show. Written with
    the GTS column names so `track_columns` has to resolve them.
    """
    base = pd.Timestamp("2026-04-18 07:00:00")
    rows = []
    for i, lga in enumerate(LGAS):
        for j in range(20):
            bursty = j % 4 == 0
            offsets = ([0, 2, 60, 62, 120, 180, 240] if bursty
                       else [0, 2, 4, 6, 8, 10])
            for m in offsets:
                rows.append({
                    "NGA State 2024 Label": STATE,
                    "NGA LGA 2024 Label": lga,
                    "Team Code": f"{lga[:3].upper()}-{j:03d}",
                    "IMEI": f"device-{i}-{j}",
                    "Valid": "1",
                    "GPS Timestamp (UTC)": (base + pd.Timedelta(minutes=m)
                                            ).strftime("%m/%d/%Y %H:%M:%S"),
                    "Lat": 11.0 + j / 100, "Lon": 6.0 + i / 100,
                })
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _maps_folder(path):
    """Map PNGs. Only the FILENAMES drive slide creation — one LGA slide per
    non-statewide, non-implementation png — so 1x1 images are enough."""
    os.makedirs(path, exist_ok=True)
    from PIL import Image
    for name in ([f"{STATE}_implementation_map.png",
                  f"{STATE}_statewide_day_{DAY}.png"]
                 + [f"{STATE}_{lga}_day_{DAY}.png" for lga in LGAS]):
        Image.new("RGB", (8, 8), "white").save(os.path.join(path, name))
    return path


def _charts_folder(path, with_efficiency_png=True):
    """`with_efficiency_png` models the difference between "the efficiency
    analysis could not be recomputed" and "there is nothing to show at all".
    Stage 6 keeps the slide on EITHER the recomputed figures or the rendered
    chart, so the chart's presence is a test dimension in its own right."""
    os.makedirs(path, exist_ok=True)
    from PIL import Image
    names = ["team_deployment.png", "daily_time_spent.png",
             "state_coverage_donut.png"]
    if with_efficiency_png:
        names.append("team_efficiency.png")
    for name in tuple(names) + ("state_coverage_donut.png",
                 "state_coverage_donut_daily.png",
                 "lga_coverage_stacked_daily.png", "lga_cumulative_counts.png",
                 "state_cumulative_coverage.png", "target_children_donut.png",
                 "target_households_donut.png", "target_coverage_by_lga.png"):
        Image.new("RGB", (8, 8), "white").save(os.path.join(path, name))
    return path


class _FakeEfficiency:
    """Stands in for team_efficiency.analyse, which needs a real merged track
    export with parseable GPS timestamps the fixture doesn't carry.
    `available=False` simulates the real "could not recompute" case."""

    def __init__(self, available):
        self.available = available

    def __call__(self, *_a, **_kw):
        if not self.available:
            raise ValueError("no usable GPS timestamps in the track export")
        return {
            "per_team": pd.DataFrame({"Team": ["A", "B"]}),
            "meta": {"flagged_teams": 223, "total_teams": 3407,
                     "flagged_pct": 0.065, "avg_efficiency_pct": 61.4,
                     "window": "08:00-16:00", "window_minutes": 480,
                     "reason_counts": {"Late start": 120, "Early stop": 80,
                                      "Insufficient operational time": 23}},
        }


# ------------------------------------------------------------------ helper
def _titles(pptx_path):
    out = []
    for slide in Presentation(pptx_path).slides:
        text = ""
        for sh in slide.shapes:
            if sh.has_text_frame and sh.text_frame.text.strip():
                text = sh.text_frame.text.strip().split("\n")[0]
                break
        out.append(text)
    return out


def _slim_copy(src, tmp, tag):
    """A slimmed working copy of a template, built once per run.

    The real templates are ~59 MB, almost all of it the embedded maps and
    charts stage 6 replaces anyway, and safe_extract + rezip of that is the
    slowest step in a build. Slimming first takes the suite from minutes to
    seconds. Branding is kept, so the copy differs from the original only in
    bytes stage 6 overwrites — which is precisely what makes it a valid
    substitute for a test that asserts slide order and content.
    """
    from slim_template import slim
    out = os.path.join(tmp, f"template_{tag}.pptx")
    slim(src, out)
    return out


def _build(tmp, efficiency, target, template, tag="v1", eff_chart=True):
    import team_efficiency
    original = team_efficiency.analyse
    team_efficiency.analyse = _FakeEfficiency(efficiency)
    try:
        out = os.path.join(tmp, f"out_{tag}_{efficiency}_{target}_{eff_chart}.pptx")
        return s6.build_pptx_report(
            template, STATE, DAY,
            _visitation_csv(os.path.join(tmp, f"vis_{target}.csv"), target),
            f"day_{DAY}_cumm",
            _deploy_csv(os.path.join(tmp, "deploy.csv")),
            _time_csv(os.path.join(tmp, "time.csv")),
            _maps_folder(os.path.join(tmp, "maps")),
            _charts_folder(os.path.join(tmp, f"charts_{eff_chart}"), eff_chart),
            out,
            tracks_file=_tracks_csv(os.path.join(tmp, "merged_tracks.csv")))
    finally:
        team_efficiency.analyse = original


# ------------------------------------------------------------------- cases
def _expected(efficiency, target, sessions=True, eff_chart=True):
    titles = [
        f"{STATE} State",
        "Background",
        "Team Deployed/Team Reported",
        f"Time Spent Analysis - Day {DAY}",
    ]
    # Kept on the recomputed figures OR the rendered chart — losing a slide
    # whose chart is already finished is the fault this models.
    if efficiency or eff_chart:
        titles.append("Team Time Efficiency")
    # These three appear whenever the analysis has data, whatever the template
    # contains: stage 6 creates them when the template has not caught up, so a
    # report is never missing an analysis because a .pptx is a version behind.
    if sessions:
        titles += [SESSIONS_TITLE, DEVICE_LGA_TITLE, TEAM_LGA_TITLE]
    titles += [
        f"State Daily Coverage - Day {DAY}",
        f"State Cumulative Coverage - Day 1 to Day {DAY}",
        "Cumulative Settlements Coverage",
    ]
    if target:
        titles.append("Target Population Coverage Analysis")
    titles.append(f"State Coverage Map - Day {DAY}")
    titles += [f"{lga} LGA Visitation Status & Coverage - Day {DAY}" for lga in LGAS]
    titles += ["Challenges", "Deployment Pictures",
               "ERM & Supportive Supervision Pictures", "Thank you!"]
    return titles


def run_case(tmp, efficiency, target, template, tag="v1", eff_chart=True):
    label = (f"[{tag}] efficiency={efficiency}, chart={eff_chart}, "
             f"target={target}")
    built = _build(tmp, efficiency, target, template, tag, eff_chart)
    got = _titles(built)
    want = _expected(efficiency, target, sessions=True, eff_chart=eff_chart)

    failures = []
    if len(got) != len(want):
        failures.append(f"slide count {len(got)}, expected {len(want)}")
    for i, (g, w) in enumerate(zip(got, want), start=1):
        if g != w:
            failures.append(f"slide {i}: {g!r} != expected {w!r}")

    # A deck that still carries the template's sample figures is the exact
    # symptom of a matcher that failed to find its box, so check the numbers
    # moved rather than only that the titles are right.
    sample_leftovers = [t for t in _all_text(built)
                        if "9,064" in t or "Nasarawa" in t or "Day 2" in t]
    if sample_leftovers:
        failures.append(f"template sample text survived: {sample_leftovers[:2]}")

    failures += _check_time_spent_slide(built)
    failures += _check_sessions_slide(
        built, 4 + (1 if (efficiency or eff_chart) else 0))
    if efficiency or eff_chart:
        failures += _check_efficiency_slide(built, efficiency)

    if failures:
        print(f"FAIL  {label}")
        for f in failures:
            print(f"        {f}")
        return False
    print(f"ok    {label}  ({len(got)} slides)")
    return True


def _check_time_spent_slide(pptx_path):
    """Slide 4 must carry the session analysis, in the narration and the table.

    Asserted here rather than in test_team_sessions.py because the risk is not
    the arithmetic — that is covered there — but the wiring: a narration that
    silently keeps the template's sample sentence, or a table that never gained
    its third column, both produce a deck that looks finished and reports
    nothing new.
    """
    slide = list(Presentation(pptx_path).slides)[3]
    problems = []

    narration = ""
    for sh in slide.shapes:
        if sh.has_text_frame and "session" in sh.text_frame.text.lower():
            narration = sh.text_frame.text
            break
    if not narration:
        problems.append("slide 4 narration says nothing about sessions")

    tables = [sh.table for sh in slide.shapes if getattr(sh, "has_table", False)]
    if not tables:
        problems.append("slide 4 has no follow-up table")
    else:
        # LGA first, then that LGA's own metrics. The table is the follow-up
        # list, so every column has to be LGA-specific — a state total repeated
        # down the rows would look right and mean nothing.
        headers = [c.text.strip() for c in tables[0].rows[0].cells]
        if headers[0].lower() != "lga":
            problems.append(f"slide 4 table does not lead with LGA: {headers}")
        if not any("sessions" in h.lower() for h in headers[1:]):
            problems.append(f"slide 4 table has no sessions column: {headers}")
        if not any("12" in h for h in headers[1:]):
            problems.append(f"slide 4 table has no under-12 column: {headers}")
        if len(headers) < 3:
            problems.append(f"slide 4 table has {len(headers)} columns, expected 3+")
        # Every data row must name an LGA — the narration below the table
        # refers to "top LGAs", so a blank first cell breaks that reference.
        for row in list(tables[0].rows)[1:]:
            first = row.cells[0].text.strip()
            if not first:
                problems.append("slide 4 table has a row with no LGA name")
                break
        # Exactly one table: the template ships the previous run's, and a build
        # that fails to clear it would leave two stacked on each other.
        if len(tables) != 1:
            problems.append(f"slide 4 has {len(tables)} tables, expected 1")
    return problems


def _all_text(pptx_path):
    out = []
    for slide in Presentation(pptx_path).slides:
        for sh in slide.shapes:
            if sh.has_text_frame and sh.text_frame.text.strip():
                out.append(sh.text_frame.text.strip())
    return out


# (template, tag, cases). The v1 template has no Tracking Sessions slide, so
# it also covers the "analyst is still on last month's template" path.
# (efficiency figures, target estimates, efficiency chart on disk)
CASES = ((True, True, True),      # everything present
         (False, True, True),     # recompute fails, chart exists -> slide KEPT
         (False, False, False))   # nothing to show -> slide dropped


def main():
    if not os.path.exists(TEMPLATE):
        print(f"SKIP — template not found at {TEMPLATE}")
        return 0
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        v1 = _slim_copy(TEMPLATE, tmp, "v1")
        for efficiency, target, chart in CASES:
            ok = run_case(tmp, efficiency, target, v1, "v1", chart) and ok
        if os.path.exists(TEMPLATE_V2):
            v2 = _slim_copy(TEMPLATE_V2, tmp, "v2")
            for efficiency, target, chart in CASES:
                ok = run_case(tmp, efficiency, target, v2, "v2", chart) and ok
        else:
            print(f"skip  [v2] template not found at {TEMPLATE_V2}")
    print("\nPASS" if ok else "\nFAILED")
    return 0 if ok else 1


def _check_efficiency_slide(pptx_path, had_figures):
    """The efficiency slide must carry this run's chart, and must never show
    the previous campaign's numbers when its figures could not be recomputed."""
    slide = list(Presentation(pptx_path).slides)[4]
    problems = []
    title = slide.shapes.title
    if title is None or title.text_frame.text.strip() != "Team Time Efficiency":
        problems.append("slide 5 is not Team Time Efficiency")
        return problems
    if not any(sh.shape_type == 13 for sh in slide.shapes):
        problems.append("efficiency slide has no chart")
    text = " ".join(sh.text_frame.text for sh in slide.shapes if sh.has_text_frame)
    if not had_figures:
        # 3,407 is the sample deck's own figure; quoting it under this run's
        # chart is the failure this guards.
        if "3,407" in text or "223 of" in text:
            problems.append("efficiency slide shows the template's sample figures")
        if "08:00-16:00 operational window" not in text:
            problems.append("efficiency slide lost its methodology narration")
    return problems


def _check_sessions_slide(pptx_path, index):
    """The Tracking Sessions slide must carry BOTH tables, filled.

    Checked separately from the titles because the slide can be present and
    correctly titled while its tables never got written — which is the failure
    mode that looks fine in a slide-order dump.
    """
    slide = list(Presentation(pptx_path).slides)[index]
    problems = []

    title = slide.shapes.title
    if title is None or title.text_frame.text.strip() != SESSIONS_TITLE:
        problems.append(f"slide {index + 1} is not the sessions slide")
        return problems

    tables = [sh.table for sh in slide.shapes if getattr(sh, "has_table", False)]
    if len(tables) != 2:
        problems.append(f"sessions slide has {len(tables)} tables, expected 2")
        return problems

    team_hdr = [c.text.strip() for c in tables[0].rows[0].cells]
    dev_hdr = [c.text.strip() for c in tables[1].rows[0].cells]
    if team_hdr != ["Team Code", "IMEI", "Tracks", "Pings"]:
        problems.append(f"team table header is {team_hdr}")
    if dev_hdr != ["IMEI", "Teams", "Tracks", "Pings"]:
        problems.append(f"device table header is {dev_hdr}")

    # Placeholder narration from the template must have been replaced.
    for sh in slide.shapes:
        if sh.has_text_frame and "ranked with shared devices first" in sh.text_frame.text:
            problems.append("sessions slide still shows the template's "
                            "placeholder narration")

    # An IMEI cell wide enough to wrap is the layout fault this column's
    # shortening exists to prevent.
    for row in list(tables[0].rows)[1:]:
        cell = row.cells[1].text.strip()
        if len(cell) > 24:
            problems.append(f"team table IMEI cell not shortened: {cell!r}")
            break
    return problems

if __name__ == "__main__":
    sys.exit(main())
