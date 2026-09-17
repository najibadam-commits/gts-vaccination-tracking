"""Regression test for the Post-Implementation Report deck (stage 7).

Run it after any change to `stage7_pir_report`, `pir_analysis`, `pir_charts` or
`pir_examples`, and after the template deck is revised:

    python test_pir_report.py                       # finds the deck beside the pipeline
    python test_pir_report.py --template path.pptx  # or point at one

What it asserts, and why each one is here:

1. **Structure** — the deck comes out in the sample's section order, with one
   LGA slide per implementing LGA. A template re-export renumbers its slide
   parts, and a build that resolves slides positionally then writes every chart
   onto the wrong slide while producing a deck that opens perfectly cleanly.
   Only a section-by-section check catches that.

2. **Nothing of the sample survives** — no "Nasarawa", no "Akwanga", none of
   its figures. The deck is built by filling the sample, so a slide the build
   misses keeps the sample's own content and ships last campaign's numbers
   under this campaign's title. That is the single worst failure this module
   can have, so it is tested directly.

3. **The coverage identity holds** — planned = the five classes summed, and
   visited = planned − No Coverage, at state and LGA level. Every figure on the
   deck is derived from that identity; if it breaks, the donut and the
   narration disagree.

4. **The report is dynamic** — the same build against changed data produces
   changed figures. This is the acceptance criterion that separates a report
   generator from a copy of the sample.

5. **Nothing overlaps** — the build's own layout check finds no colliding
   shapes.

6. **Every LGA-level chart is the same chart** — a stacked composition, with
   the best class at the bottom and the shortfall on top. Two of them used to be
   plain single-series columns, which made a reader re-learn the page halfway
   through; and the stack ran upside down against the sample, which is the kind
   of thing that survives review because the legend still reads correctly.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pir_analysis as pa
from stage7_pir_report import (build_pir_report, find_default_pir_template,
                               _overlap_report, T_LGA)

# The sample HAS a Team Deployed/Devices slide; the generated report must not.
# See the stage 7 module docstring for why.

# Strings from the sample deck that must not survive a build. Its state, one of
# its LGAs, and its headline figures.
SAMPLE_LEAKS = ("nasarawa", "akwanga", "kokona", "509", "492", "982",
                "ibra campaign - june")

CUM_COL = "day_3_cumm"


def synthetic_visitation(n_lga: int = 4, wards_per_lga: int = 3,
                         settlements_per_ward: int = 5,
                         visited_share: float = 0.7,
                         state: str = "Testland") -> pd.DataFrame:
    """A settlement visitation frame shaped like stage 2's output.

    Deterministic: the same arguments always give the same frame, so a failure
    is reproducible. `visited_share` is the knob the dynamism check turns.
    """
    rows = []
    i = 0
    for l in range(n_lga):
        lga = f"Lga {chr(ord('A') + l)}"
        for w in range(wards_per_lga):
            ward = f"{lga} Ward {w + 1}"
            for s in range(settlements_per_ward):
                i += 1
                reached = ((i * 7919) % 100) / 100.0 < visited_share
                # spread the reached settlements across the coverage classes
                coverage = 0.0
                if reached:
                    coverage = [0.85, 0.60, 0.35, 0.10][i % 4]
                rows.append({
                    "unique": f"{state}_{lga}_{ward}_Settlement {i}",
                    "state": state,
                    "lga": lga,
                    "wardname": ward,
                    "settlements": f"Settlement {i}",
                    "team_code": f"T-{(i % 9) + 1:03d}",
                    "latitude": 11.0 + i * 0.001 if i % 11 else None,
                    "longitude": 6.0 + i * 0.001 if i % 11 else None,
                    "set population": 100 + (i % 40),
                    "set target": 20 + (i % 9),
                    "Coverage": coverage,
                    CUM_COL: "Visited" if reached else "Not Visited",
                    "Settlement Coverage": _classify(coverage),
                })
    return pd.DataFrame(rows)


def _classify(coverage: float) -> str:
    """Stage 2's `classify_coverage` thresholds, restated so the fixture does
    not depend on importing geopandas just to build a DataFrame."""
    if not coverage:
        return "No Coverage"
    if coverage >= 0.70:
        return "Fully Covered"
    if coverage >= 0.50:
        return "Partially Covered"
    if coverage >= 0.30:
        return "Low Coverage"
    return "Very Low Coverage"


def synthetic_geometry(prepared: pd.DataFrame, folder: str) -> dict:
    """Settlement extents, gridded target areas and track points to match.

    A square extent per settlement holding a 3x3 grid, with as many cells
    flagged Visited as that settlement's `Coverage` implies, and a scatter of
    pings inside those cells. Enough to exercise the coverage-example figures
    end to end without shipping a multi-gigabyte fixture.
    """
    import geopandas as gpd
    from shapely.geometry import Polygon

    os.makedirs(folder, exist_ok=True)
    extents, cells, pings = [], [], []
    for k, (key, row) in enumerate(prepared.set_index("unique").iterrows()):
        x0, y0 = 6.0 + k * 0.05, 11.0 + k * 0.05
        extents.append({"unique": key,
                        "geometry": Polygon([(x0, y0), (x0 + .03, y0),
                                             (x0 + .03, y0 + .03), (x0, y0 + .03)])})
        coverage = float(row["Coverage"] or 0)
        visited_cells = int(round(coverage * 9))
        for c in range(9):
            cx, cy = x0 + (c % 3) * 0.01, y0 + (c // 3) * 0.01
            cells.append({"unique": key,
                          "visitation": "Visited" if c < visited_cells else "Not Visited",
                          "geometry": Polygon([(cx, cy), (cx + .009, cy),
                                               (cx + .009, cy + .009), (cx, cy + .009)])})
        for c in range(visited_cells * 3):
            pings.append({"Latitude": y0 + 0.001 + (c % 9) * 0.003,
                          "Longitude": x0 + 0.001 + (c % 7) * 0.004})

    paths = {"voronoi": os.path.join(folder, "extents.gpkg"),
             "gridded": os.path.join(folder, "grid.gpkg"),
             "tracks": os.path.join(folder, "tracks.csv")}
    gpd.GeoDataFrame(extents, crs="EPSG:4326").to_file(paths["voronoi"], driver="GPKG")
    gpd.GeoDataFrame(cells, crs="EPSG:4326").to_file(paths["gridded"], driver="GPKG")
    pd.DataFrame(pings).to_csv(paths["tracks"], index=False)
    return paths


def _slide_texts(prs) -> list[str]:
    out = []
    for s in prs.slides:
        parts = [sh.text_frame.text for sh in s.shapes
                 if sh.has_text_frame and sh.text_frame.text.strip()]
        out.append("\n".join(parts))
    return out


def _titles(prs) -> list[str]:
    return [(s.shapes.title.text_frame.text.strip()
             if s.shapes.title is not None else "") for s in prs.slides]


def check(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"  ok  {message}")


def build(tmp: str, template: str, dip: pd.DataFrame, tag: str,
          geo: dict | None = None) -> tuple:
    from pptx import Presentation
    csv_path = os.path.join(tmp, f"visitation_{tag}.csv")
    dip.to_csv(csv_path, index=False)
    out = os.path.join(tmp, f"deck_{tag}.pptx")
    geo = geo or {}
    build_pir_report("Testland", csv_path, CUM_COL, None, None, out,
                     campaign_name="Test Campaign",
                     charts_folder=os.path.join(tmp, f"charts_{tag}"),
                     template_path=template,
                     voronoi_file=geo.get("voronoi"),
                     gridded_ta_file=geo.get("gridded"),
                     tracks_file=geo.get("tracks"))
    return Presentation(out), out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--template", default=None,
                    help="PIR .pptx template; auto-detected beside the pipeline "
                         "if not given")
    ap.add_argument("--keep", action="store_true",
                    help="keep the built decks for inspection")
    a = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    template = a.template or find_default_pir_template(here, os.path.dirname(here),
                                                       os.getcwd())
    if not template:
        print("No PIR .pptx template found — pass --template. SKIPPED.")
        return 0
    print(f"template: {template}\n")

    tmp = tempfile.mkdtemp(prefix="pir_test_")
    try:
        n_lga = 4
        dip = synthetic_visitation(n_lga=n_lga)
        prepared = pa.prepare(dip, CUM_COL)
        summary = pa.state_summary(prepared)

        print("indicators")
        classes_sum = sum(summary["classes"].values())
        check(classes_sum == summary["planned"],
              f"the five coverage classes sum to the planned total "
              f"({classes_sum} == {summary['planned']})")
        check(summary["visited"] == summary["planned"]
              - summary["classes"]["No Coverage"],
              "visited == planned - No Coverage")
        for lga in pa.lga_class_table(prepared).index:
            st = pa.lga_summary(prepared, lga)
            check(sum(st["classes"].values()) == st["planned"]
                  and st["visited"] == st["planned"] - st["classes"]["No Coverage"],
                  f"the same identity holds for {lga}")
        # a settlement with tracks but no measurable grid coverage must be
        # reclassified, never left contradicting the status column
        contradictions = prepared[prepared["_visited"]
                                  & prepared["_class"].eq("No Coverage")]
        check(len(contradictions) == 0,
              "no settlement is both visited and No Coverage after reconciliation")

        print("\nstructure")
        prs, path = build(tmp, template, dip, "base")
        titles = _titles(prs)
        lga_slides = [i for i, t in enumerate(titles)
                      if T_LGA.lower() in t.lower()]
        check(len(lga_slides) == n_lga,
              f"one LGA slide per implementing LGA ({len(lga_slides)} == {n_lga})")
        check(lga_slides == list(range(lga_slides[0], lga_slides[0] + n_lga)),
              "the LGA slides are contiguous, in one block")

        expected_order = ["background", "state settlement coverage",
                          "cumulative settlements coverage",
                          "potentially missed children", "geo-coordinates",
                          "coverage map"]
        found_at = []
        for needle in expected_order:
            hits = [i for i, t in enumerate(titles) if needle in t.lower()]
            check(bool(hits), f"the deck has a '{needle}' slide")
            found_at.append(hits[0])
        check(found_at == sorted(found_at),
              "those sections appear in the sample's order")
        check(all(i < lga_slides[0] for i in found_at),
              "every state-level section precedes the LGA block")
        # Team deployment is an implementation-monitoring measure; the final
        # report must not carry it, in any form.
        deck_text = "\n".join(_slide_texts(prs)).lower()
        check(not any(t.lower().startswith("team deployed") for t in titles),
              "the Team Deployed/Devices slide is gone")
        # The banned list is deployment METRICS, not the words "vaccination
        # teams" — the no-geo-coordinate slide legitimately says teams carrying
        # trackers were expected to visit every planned settlement, which is an
        # explanation, not a count anyone is being asked to act on.
        for phrase in ("teams deployed", "teams reported", "teams pending",
                       "gts tracker", "vaccine buddy", "eha phones",
                       "reporting %"):
            check(phrase not in deck_text,
                  f"no team-deployment figure survives ({phrase!r})")
        tail = [t.lower() for t in titles[lga_slides[-1] + 1:]]
        for needle in ("settlement coverage examples", "challenges",
                       "recommendation", "deployment pictures", "erm pictures"):
            check(any(needle in t for t in tail),
                  f"'{needle}' follows the LGA block")

        print("\nnothing of the sample survives")
        blob = "\n".join(_slide_texts(prs)).lower()
        for leak in SAMPLE_LEAKS:
            check(leak not in blob, f"the sample's {leak!r} is gone")
        check("testland" in blob, "the deck names the campaign's own state")
        pictures = sum(1 for s in prs.slides for sh in s.shapes
                       if sh.shape_type == 13)
        check(pictures >= 2 * n_lga,
              f"every LGA slide carries its two charts ({pictures} pictures total)")

        print("\nLGA charts are all the same stacked chart")
        import pir_charts as pc
        import numpy as _np
        from matplotlib import pyplot as _plt
        split = pa.visitation_split_by_lga(prepared)
        check(list(split.columns) == [pa.VISITED_LABEL, pa.NOT_VISITED_LABEL],
              "settlements per LGA come back as a two-part split, not one series")
        check((split.sum(axis=1) > 0).all(),
              "every LGA in the split has a whole to stack against")
        kids = pa.children_split_by_lga(prepared)
        check(list(kids.columns) == [pa.REACHED_CHILDREN_LABEL,
                                     pa.MISSED_CHILDREN_LABEL],
              "target children per LGA likewise")
        # the reported headline figures are untouched by the restyle
        not_visited = pa.not_visited_by_lga(prepared)
        missed = pa.missed_children_by_lga(prepared)
        check(int(split[pa.NOT_VISITED_LABEL].sum()) == int(not_visited.sum()),
              "the stacked chart's not-visited total equals the reported one")
        check(int(kids[pa.MISSED_CHILDREN_LABEL].sum()) == int(missed.sum()),
              "the stacked chart's missed-children total equals the reported one")

        # Stacking direction, checked on what is actually DRAWN. The order rule
        # is one line of code, but getting it wrong is invisible in review — the
        # legend still reads correctly and only the picture is upside down — so
        # it is worth reading the bar bottoms back out of the render.
        drawn = []
        real_subplots = pc.plt.subplots

        def spy_subplots(*a, **k):
            fig, ax = real_subplots(*a, **k)
            real_bar = ax.bar

            def spy_bar(x, height, bottom=None, **kw):
                base = _np.zeros(len(x)) if bottom is None else _np.asarray(bottom)
                drawn.append((kw.get("label"), float(_np.max(base))))
                return real_bar(x, height, bottom=bottom, **kw)

            ax.bar = spy_bar
            return fig, ax

        pc.plt.subplots = spy_subplots
        try:
            pc._stacked(pa.lga_class_table(prepared), os.path.join(tmp, "_order.png"),
                        None, False, (6, 4), 10)
        finally:
            pc.plt.subplots = real_subplots
        order_drawn = [label for label, _ in drawn]
        check(order_drawn[0] == "Fully Covered",
              "the best-covered class is drawn FIRST, i.e. at the bottom")
        check(order_drawn[-1] == "No Coverage",
              "and No Coverage is drawn last, i.e. on top")
        bases = dict(drawn)
        check(bases["Fully Covered"] == 0.0,
              "the best-covered class starts from zero")
        check(bases["No Coverage"] > 0.0,
              "No Coverage starts above everything else")

        print("\nlayout")
        check(not _overlap_report(prs, skip_slides=(1,)),
              "no two shapes collide")

        print("\ncoverage examples")
        try:
            geo = synthetic_geometry(prepared, os.path.join(tmp, "geo"))
        except ImportError as exc:
            print(f"  -- skipped, geopandas unavailable ({exc})")
            geo = None
        if geo:
            import pir_examples
            panels = pir_examples.build_examples(
                prepared, os.path.join(tmp, "examples"), geo["voronoi"],
                geo["gridded"], os.path.join(tmp, "visitation_base.csv"),
                CUM_COL, geo["tracks"], state_name="Testland")
            check(set(panels) == {"fully", "partially", "low", "not_visited"},
                  "one example figure per coverage class")
            check(all(os.path.getsize(p) > 5000 for p in panels.values()),
                  "each example figure has real content")
            prs_geo, _ = build(tmp, template, dip, "geo", geo=geo)
            titles_geo = _titles(prs_geo)
            ex_slides = [i for i, t in enumerate(titles_geo)
                         if "settlement coverage examples" in t.lower()]
            check(len(ex_slides) == 2, "both example slides are present")
            for i in ex_slides:
                pics = [sh for sh in prs_geo.slides[i].shapes if sh.shape_type == 13]
                check(len(pics) == 2,
                      f"slide {i + 1} carries its two example figures")
            check(not _overlap_report(prs_geo, skip_slides=(1,)),
                  "the deck with example figures still has no collisions")

        print("\ndynamic")
        other = synthetic_visitation(n_lga=n_lga, visited_share=0.3)
        prs2, path2 = build(tmp, template, other, "changed")
        blob2 = "\n".join(_slide_texts(prs2)).lower()
        check(blob != blob2,
              "changing the input data changes the deck's figures")
        s2 = pa.state_summary(pa.prepare(other, CUM_COL))
        check(s2["visited"] < summary["visited"],
              f"fewer settlements reached in the input -> fewer on the deck "
              f"({s2['visited']} < {summary['visited']})")
        n_lga_5 = 6
        prs3, _ = build(tmp, template, synthetic_visitation(n_lga=n_lga_5), "six")
        check(sum(1 for t in _titles(prs3) if T_LGA.lower() in t.lower()) == n_lga_5,
              f"a {n_lga_5}-LGA campaign gets {n_lga_5} LGA slides")

        print("\nALL CHECKS PASSED")
        if a.keep:
            print(f"decks kept in {tmp}")
        return 0
    finally:
        if not a.keep:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
