"""Assert the efficiency chart, flagged-team table and narration agree.

The three are published together and read together, so they must come from one
dataset and reconcile exactly. Builds a settlement analysis with a known number
of teams in each category and checks the counts everywhere they surface.

    python test_flagged_teams.py
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from team_efficiency import (  # noqa: E402
    analyse, REASON_LONG_LOW, REASON_STATIONARY,
)

# Teams by intended category. Each works 3 settlements of 20 grid cells.
N_LONG_LOW = 25     # > 2 hrs, coverage well under 50%, several cells touched
N_STATIONARY = 7    # >= 30 min of pings, exactly one cell touched
N_BOTH = 4          # meets both rules
N_CLEAN = 40        # short time, good coverage
CELLS = 20
SETTLEMENTS = 3


def build() -> pd.DataFrame:
    rows = []
    for i in range(N_LONG_LOW + N_STATIONARY + N_BOTH + N_CLEAN):
        if i < N_LONG_LOW:
            pings, visited = 200, 4          # 400 min, 4/60 cells = 6.7%
        elif i < N_LONG_LOW + N_STATIONARY:
            pings, visited = 40, 1           # 80 min, 1 cell -> stationary only
        elif i < N_LONG_LOW + N_STATIONARY + N_BOTH:
            pings, visited = 200, 1          # 400 min AND 1 cell -> both
        else:
            pings, visited = 20, 18          # 40 min, 18/60 = 30%... clean below
        for s in range(SETTLEMENTS):
            rows.append({
                "State": "Testland", "LGA": f"LGA{i % 4}", "Ward": "W1",
                "Settlement": f"S{i}-{s}", "team_code": f"TM{i:03d}",
                "track_count": pings if s == 0 else 0,
                "Grid Cells": CELLS,
                "Grid Cells Visited": visited if s == 0 else (
                    CELLS if i >= N_LONG_LOW + N_STATIONARY + N_BOTH else 0),
            })
    return pd.DataFrame(rows)


ok = True


def check(label, got, want):
    global ok
    good = got == want
    ok = ok and good
    print(f"  [{'PASS' if good else 'FAIL'}] {label}: got {got}, want {want}")


def main() -> int:
    res = analyse(build(), "cumulative")
    m, table, per_team = res["meta"], res["flagged_table"], res["per_team"]

    print(f"teams analysed: {m['total_teams']}\n")
    print(table.head(6).to_string(index=False))
    print()

    print("categories:")
    check("long-time / low-coverage", m["flagged_teams"], N_LONG_LOW + N_BOTH)
    check("stationary", m["stationary_teams"], N_STATIONARY + N_BOTH)
    check("flagged teams in total (both rules, counted once)",
          m["flagged_any"], N_LONG_LOW + N_STATIONARY + N_BOTH)

    print("\ntable:")
    check("one row per flagged team", len(table), m["flagged_any"])
    check("no team listed twice", table["Team"].duplicated().any(), False)
    check("required fields present",
          all(c in table.columns for c in
              ("Team", "LGA", "Time Spent (hrs)", "% Grid Covered", "Flag/Reason")),
          True)
    both_rows = table["Flag/Reason"].str.contains(" \\+ ", regex=True).sum()
    check("teams meeting both rules carry both reasons", int(both_rows), N_BOTH)
    check("long-low reason count",
          int((table["Flag/Reason"] == REASON_LONG_LOW).sum()), N_LONG_LOW)
    check("stationary-only reason count",
          int((table["Flag/Reason"] == REASON_STATIONARY).sum()), N_STATIONARY)

    print("\nfigures reconcile:")
    check("every table row is flagged in per_team",
          set(table["Team"]) <= set(per_team.loc[
              per_team["Flagged"] | per_team["Stationary"], "Team"]), True)
    check("% Grid Covered matches cells visited / grid cells",
          bool(np.allclose(table["% Grid Covered"],
                           (table["Cells Visited"] / table["Grid Cells"] * 100).round(1))),
          True)
    check("flagged share matches the narration's percentage",
          round(m["flagged_pct"] * 100, 1),
          round((N_LONG_LOW + N_BOTH) / m["total_teams"] * 100, 1))
    check("stationary median hours is a real median",
          m["stationary_median_hours"] > 0, True)

    print("\nthe two rules overlap — the split is stated, not implied:")
    check("teams meeting both", m["flagged_both"], N_BOTH)
    check("long-low only", m["flagged_only"], N_LONG_LOW)
    check("stationary only ('a further N' in the narration)",
          m["stationary_only"], N_STATIONARY)
    check("the three disjoint groups sum to the flagged total",
          m["flagged_only"] + m["stationary_only"] + m["flagged_both"],
          m["flagged_any"])
    check("narration's 'a further' does not double-count",
          m["stationary_only"] + m["flagged_teams"], m["flagged_any"])

    print("\nchart series (drawn once each, summing to the team count):")
    stationary = per_team["Stationary"]
    flagged_only = (per_team["Quadrant"] == "Long time, low coverage") & ~stationary
    within = ~flagged_only & ~stationary
    check("three series sum to all teams",
          int(stationary.sum() + flagged_only.sum() + within.sum()),
          m["total_teams"])
    check("stationary drawn as stationary, not double-counted",
          int(stationary.sum()), N_STATIONARY + N_BOTH)

    print("\n" + ("ALL PASSED" if ok else "FAILURES ABOVE"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
