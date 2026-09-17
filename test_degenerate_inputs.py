"""A run must not die at the charting stage because a chart has no data.

The bug this guards: `state_coverage_donut` passed its counts straight to
`ax.pie`, which raises

    ValueError: All wedge sizes are zero

on an empty series. That killed a full post-campaign run at stage 6 — minutes
after the spatial analysis it depends on had already succeeded — and the error
named none of the several quite different causes it could have had.

Both halves of the fix are asserted here: no chart raises on degenerate input,
and each cause is REPORTED with the detail needed to fix it. A chart that
silently drew an empty ring would be worse than the crash — a donut reading
"0" looks like a campaign that achieved nothing, rather than a column that
could not be read.

Run:  python test_degenerate_inputs.py
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from contextlib import redirect_stdout

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stage_charts import (COVERAGE_COLORS, lga_coverage_stacked, readable_on,
                          state_coverage_donut)

CUM_COL = "day_3_cumm"
FAILED = []


def check(condition, message):
    print(f"  {'ok  ' if condition else 'FAIL'}  {message}")
    if not condition:
        FAILED.append(message)


def visitation(**override) -> pd.DataFrame:
    n = 12
    d = pd.DataFrame({
        "lga": [f"Lga {chr(65 + i % 3)}" for i in range(n)],
        CUM_COL: ["Visited" if i % 3 else "Not Visited" for i in range(n)],
        "Settlement Coverage": ["Fully Covered", "Partially Covered",
                                "No Coverage"] * 4,
    })
    for key, value in override.items():
        d[key] = value
    return d


def draw(df, tmp, tag):
    """Both charts, returning (stats, everything printed)."""
    csv = os.path.join(tmp, f"{tag}.csv")
    df.to_csv(csv, index=False)
    log = io.StringIO()
    with redirect_stdout(log):
        stats = state_coverage_donut(csv, CUM_COL, 3,
                                     os.path.join(tmp, f"{tag}_donut.png"))
        lga_coverage_stacked(csv, CUM_COL, 3,
                             os.path.join(tmp, f"{tag}_lga.png"))
    return stats, log.getvalue()


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="degenerate_")

    print("healthy input is unaffected")
    stats, log = draw(visitation(), tmp, "ok")
    check(stats["planned"] == 12 and stats["visited"] == 8,
          f"the donut still counts correctly ({stats})")
    check("WARNING" not in log, "and warns about nothing")

    print("\nan empty cumulative status column")
    stats, log = draw(visitation(**{CUM_COL: np.nan}), tmp, "nostatus")
    check(stats["planned"] == 0, "the donut returns zero rather than raising")
    check(CUM_COL in log and "nothing counts as planned" in log,
          "and the warning names the column that is empty")
    check("stage 2" in log.lower() and "day" in log.lower(),
          "and points at the stage and the likely cause")
    check(log.count("WARNING") == 2,
          "the LGA chart reports it too rather than drawing empty bars")

    print("\ncoverage classes the pipeline does not recognise")
    stats, log = draw(visitation(**{"Settlement Coverage": "covered"}),
                      tmp, "badclass")
    check(stats["planned"] == 0, "no raise")
    check("'covered' (x12)" in log,
          "the warning quotes the values it actually found")
    check("Fully Covered" in log, "and the ones it expected")
    check(log.count("WARNING") == 2, "from both charts")

    print("\nan empty settlement list")
    stats, log = draw(visitation().iloc[0:0], tmp, "empty")
    check(stats["planned"] == 0, "no raise")
    check("state filter" in log, "the warning suggests where to look")

    print("\nin-wedge labels stay readable")
    for cls, fill in COVERAGE_COLORS.items():
        ink = readable_on(fill)
        check(ink in ("white", "#111111"), f"{cls} ({fill}) -> {ink}")
    check(readable_on(COVERAGE_COLORS["No Coverage"]) == "#111111",
          "No Coverage is pale enough to need dark text, and gets it")
    check(readable_on(COVERAGE_COLORS["Fully Covered"]) == "white",
          "Fully Covered is dark enough for white")

    print()
    if FAILED:
        print(f"{len(FAILED)} CHECK(S) FAILED")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
