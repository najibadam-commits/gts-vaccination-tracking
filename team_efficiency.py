"""Team Time Efficiency — Coverage x Time cross-reference at TEAM level.

Answers the operational question the ERM actually asks: *which teams spent a
long time in the field and still covered little of their assigned area?* Time
on the ground is only useful if it converts into gridded coverage; a team at
three hours and 20% coverage is a different problem from a team at ten minutes
and 20%, and needs a different conversation.

Rewritten from the `CoveragexTime` draft. Four things in that draft made its
numbers wrong for this purpose, all fixed here:

    draft                                    this module
    ------------------------------------     ------------------------------------
    summed "GPS Points Count" straight        minutes = pings x PING_MINUTES,
    into bins labelled in MINUTES, so         the same factor stage 2 and
    every duration was halved                stage 3 already use
    flagged "1 - 2 Hrs" AND "> 2 Hrs"        flags strictly > 120 minutes
    as the long-duration risk
    grouped by LGA|Ward|Settlement, so       groups by TEAM, which is what an
    it could not name a team at all          ERM follow-up needs
    coverage = visited ROWS / total rows,    coverage = visited CELLS / total
    which is binary 0/100 when the input     CELLS, the same gridded measure
    is one row per settlement                the workbook and maps report

Why cells and not an average of percentages
-------------------------------------------
A ratio cannot be aggregated across settlements without its denominator.
Averaging per-settlement coverage lets a settlement with four grid cells pull
the mean as hard as one with four hundred. Team coverage here is

    sum(cells visited across the team's settlements)
    ------------------------------------------------
    sum(total cells across the team's settlements)

which is the same construction stage 2 uses per settlement, just one level up.
Stage 2 keeps `Grid Cells` and `Grid Cells Visited` in the visitation CSV for
exactly this reason.

Daily vs cumulative
-------------------
Both scopes are produced and must not be mixed: `Daily Cells Visited` against
today's tracks, `Grid Cells Visited` from Day 1 to date. A team can look
efficient cumulatively and idle today.
"""
from __future__ import annotations

import argparse
import os

import pandas as pd

# Same ping-to-minute factor as stage2_analysis and team_time_range. Imported
# rather than redefined so the three can never drift apart.
from team_time_range import PING_MINUTES, _find_col, _find_team_col, _join_codes

# ---- thresholds -----------------------------------------------------------
# "Long time in the field" is strictly MORE than two hours, matching the
# operational definition. 120 minutes exactly is not flagged.
LONG_FIELD_MINUTES = 120
# "Low gridded area coverage" is below 50%, the pipeline's Partially Covered
# floor and the 50% break in the stacked-bar bands.
LOW_COVERAGE = 0.50

# Efficiency quadrants, from the two thresholds above.
Q_FLAG = "Long time, low coverage"      # > 2 hrs and < 50% — the follow-up list
Q_PRODUCTIVE = "Long time, good coverage"
Q_EFFICIENT = "Short time, good coverage"
Q_LOW_EFFORT = "Short time, low coverage"
QUADRANT_ORDER = [Q_FLAG, Q_LOW_EFFORT, Q_PRODUCTIVE, Q_EFFICIENT]

# ---- stationary teams -----------------------------------------------------
# A separate and blunter question from the quadrants above: not "was the time
# productive" but "did the team move at all". A team logging a substantial
# stretch of field time whose pings never leave one or two grid cells has
# reported working while its tracks show it in effectively one location — the
# device sat somewhere. That is a data-integrity finding, not an efficiency
# one, and it is the group an ERM most needs named.
#
# Thresholds are deliberately conservative so the list stays defensible: at
# least half an hour of pings (below that, a single-cell settlement is normal),
# and no more than one grid cell touched across everything the team was
# assigned. A team with genuinely one tiny settlement can land here, which is
# why the output is worded as "flag for review", not as a finding of fact.
STATIONARY_MIN_MINUTES = 30
STATIONARY_MAX_CELLS = 1

# Wording used in the follow-up table's Flag/Reason column, and on the chart's
# legend, so a reader moving between the two sees the same rule stated the same
# way. A team meeting both rules gets both, joined with " + ".
REASON_LONG_LOW = f"> {LONG_FIELD_MINUTES // 60} hrs & < {LOW_COVERAGE:.0%} coverage"
REASON_STATIONARY = f"<= {STATIONARY_MAX_CELLS} grid cell"

MAX_TEAM_CODES = 40


def quadrant(minutes: float, coverage: float) -> str:
    """Place a team on the time x coverage grid."""
    long_field = pd.notna(minutes) and minutes > LONG_FIELD_MINUTES
    low_cov = pd.isna(coverage) or coverage < LOW_COVERAGE
    if long_field:
        return Q_FLAG if low_cov else Q_PRODUCTIVE
    return Q_LOW_EFFORT if low_cov else Q_EFFICIENT


def _cell_cols(df: pd.DataFrame, scope: str) -> tuple[str | None, str | None]:
    """(visited-cells column, total-cells column) for the requested scope."""
    total = "Grid Cells" if "Grid Cells" in df.columns else None
    visited = ("Daily Cells Visited" if scope == "daily" else "Grid Cells Visited")
    return (visited if visited in df.columns else None), total


def analyse(visitation_csv: str | pd.DataFrame, scope: str = "cumulative",
            state_name: str | None = None) -> dict:
    """Team-level Coverage x Time for one temporal scope.

    `scope` is "daily" (the reporting day alone) or "cumulative" (Day 1 to
    date). Returns:

        per_team   one row per team — settlements, minutes, cells, coverage,
                   time band, quadrant, and whether it is flagged
        flagged    the follow-up list: > 2 hrs AND < 50% coverage, worst first
        matrix     quadrant counts by LGA, for the workbook
        summary    one row per quadrant with team counts and shares
        meta       thresholds, totals and how minutes were derived

    Raises ValueError only when the settlement analysis carries neither cell
    counts nor a ping count — without one of those there is no metric to build.
    """
    df = (visitation_csv if isinstance(visitation_csv, pd.DataFrame)
          else pd.read_csv(visitation_csv, low_memory=False))
    df = df.copy()

    lga_col = _find_col(df, "lga")
    team_col = _find_team_col(df)
    if team_col is None or lga_col is None:
        raise ValueError(
            f"Team efficiency needs team and LGA columns; the settlement "
            f"analysis has {list(df.columns)[:20]}…")

    visited_col, total_col = _cell_cols(df, scope)
    ping_col = next((c for c in ("track_count", "pings") if c in df.columns), None)
    if ping_col is None:
        raise ValueError(
            "Team efficiency needs a `track_count` column to derive field "
            "minutes. Re-run stage 2 to produce it.")
    if visited_col is None or total_col is None:
        raise ValueError(
            "Team efficiency needs `Grid Cells` and `Grid Cells Visited` "
            "(or `Daily Cells Visited`) to weight coverage by grid area. "
            "Re-run stage 2 — older visitation CSVs predate these columns.")

    work = pd.DataFrame({
        "LGA": df[lga_col].astype(str).str.replace("_", " ", regex=False)
        .str.replace(r"\s+", " ", regex=True).str.strip().str.title(),
        "Team": df[team_col].astype(str).str.strip(),
        "_pings": pd.to_numeric(df[ping_col], errors="coerce").fillna(0),
        "_visited": pd.to_numeric(df[visited_col], errors="coerce").fillna(0),
        "_cells": pd.to_numeric(df[total_col], errors="coerce").fillna(0),
    })
    work = work[work["Team"].notna() & ~work["Team"].isin(["", "nan", "None"])]

    per_team = (work.groupby(["LGA", "Team"], dropna=False)
                .agg(Settlements=("Team", "size"),
                     Pings=("_pings", "sum"),
                     **{"Cells Visited": ("_visited", "sum"),
                        "Grid Cells": ("_cells", "sum")})
                .reset_index())
    per_team["Minutes"] = (per_team["Pings"] * PING_MINUTES).round().astype(int)
    per_team["Hours"] = (per_team["Minutes"] / 60).round(2)
    per_team["Coverage"] = (per_team["Cells Visited"] /
                            per_team["Grid Cells"]).where(per_team["Grid Cells"] > 0)
    per_team["Quadrant"] = [quadrant(m, c) for m, c
                            in zip(per_team["Minutes"], per_team["Coverage"])]
    per_team["Flagged"] = per_team["Quadrant"] == Q_FLAG
    for c in ("Cells Visited", "Grid Cells"):
        per_team[c] = per_team[c].round().astype(int)

    # Reported working, but the tracks never left one place.
    per_team["Stationary"] = ((per_team["Minutes"] >= STATIONARY_MIN_MINUTES) &
                              (per_team["Cells Visited"] <= STATIONARY_MAX_CELLS))
    stationary = (per_team[per_team["Stationary"]]
                  .sort_values(["Minutes"], ascending=False)
                  .reset_index(drop=True))

    flagged = (per_team[per_team["Flagged"]]
               .sort_values(["Coverage", "Minutes"], ascending=[True, False])
               .reset_index(drop=True))

    # The follow-up list an LGA supervisor actually works from: EVERY flagged
    # team, both reasons together, in the five fields needed to act. A team can
    # meet both rules, so the reason is combined rather than the team listed
    # twice — the row count is then the number of teams to follow up, which is
    # what the narration quotes.
    flagged_any = per_team[per_team["Flagged"] | per_team["Stationary"]].copy()
    reasons = []
    for is_long_low, is_stationary in zip(flagged_any["Flagged"],
                                          flagged_any["Stationary"]):
        parts = []
        if is_long_low:
            parts.append(REASON_LONG_LOW)
        if is_stationary:
            parts.append(REASON_STATIONARY)
        reasons.append(" + ".join(parts))
    flagged_any["Flag/Reason"] = reasons

    flagged_table = pd.DataFrame({
        "Team": flagged_any["Team"].astype(str),
        "LGA": flagged_any["LGA"].astype(str),
        "Time Spent (hrs)": flagged_any["Hours"],
        "% Grid Covered": (flagged_any["Coverage"].fillna(0) * 100).round(1),
        "Flag/Reason": flagged_any["Flag/Reason"],
        # kept alongside so the percentage can be audited back to its inputs
        "Cells Visited": flagged_any["Cells Visited"],
        "Grid Cells": flagged_any["Grid Cells"],
        "Settlements": flagged_any["Settlements"],
    }).sort_values(["Flag/Reason", "% Grid Covered", "Time Spent (hrs)"],
                   ascending=[True, True, False]).reset_index(drop=True)

    matrix = pd.DataFrame()
    if len(per_team):
        matrix = (per_team.groupby(["LGA", "Quadrant"]).size().unstack(fill_value=0)
                  .reindex(columns=QUADRANT_ORDER, fill_value=0)
                  .sort_values(Q_FLAG, ascending=False).reset_index())

    n = len(per_team)
    summary = pd.DataFrame({
        "Quadrant": QUADRANT_ORDER,
        "Definition": [
            f"> {LONG_FIELD_MINUTES // 60} hrs in field, < {LOW_COVERAGE:.0%} grid coverage",
            f"≤ {LONG_FIELD_MINUTES // 60} hrs, < {LOW_COVERAGE:.0%} coverage",
            f"> {LONG_FIELD_MINUTES // 60} hrs, ≥ {LOW_COVERAGE:.0%} coverage",
            f"≤ {LONG_FIELD_MINUTES // 60} hrs, ≥ {LOW_COVERAGE:.0%} coverage"],
        "Teams": [int((per_team["Quadrant"] == q).sum()) for q in QUADRANT_ORDER],
    })
    summary["Share"] = (summary["Teams"] / n) if n else 0.0

    return {
        "per_team": per_team, "flagged": flagged, "matrix": matrix,
        "summary": summary, "stationary": stationary,
        # every flagged team, both reasons, ready to publish
        "flagged_table": flagged_table,
        "meta": {
            "scope": scope,
            "long_field_minutes": LONG_FIELD_MINUTES,
            "low_coverage": LOW_COVERAGE,
            "ping_minutes": PING_MINUTES,
            "coverage_basis": f"{visited_col} / {total_col} (grid-cell weighted)",
            "total_teams": n,
            "flagged_teams": int(len(flagged)),
            "flagged_pct": (len(flagged) / n) if n else 0.0,
            "flagged_codes": _join_codes(flagged["Team"]) if len(flagged) else "",
            "stationary_min_minutes": STATIONARY_MIN_MINUTES,
            "stationary_max_cells": STATIONARY_MAX_CELLS,
            "stationary_teams": int(len(stationary)),
            "stationary_pct": (len(stationary) / n) if n else 0.0,
            "stationary_codes": (_join_codes(stationary["Team"])
                                 if len(stationary) else ""),
            "stationary_median_hours": (float(stationary["Hours"].median())
                                        if len(stationary) else 0.0),
            # teams meeting EITHER rule — the row count of `flagged_table`
            "flagged_any": int(len(flagged_table)),
            # The two rules overlap: a team can be both over 2 hours with low
            # coverage AND stationary. These make the split explicit so the
            # narration, the chart legend and the table can all reconcile
            # instead of quoting three different totals.
            "flagged_both": int((per_team["Flagged"] & per_team["Stationary"]).sum()),
            "flagged_only": int((per_team["Flagged"] & ~per_team["Stationary"]).sum()),
            "stationary_only": int((per_team["Stationary"] & ~per_team["Flagged"]).sum()),
            "stationary_only_pct": (
                int((per_team["Stationary"] & ~per_team["Flagged"]).sum()) / n
                if n else 0.0),
        },
    }


def analyse_daily_and_cumulative(visitation_csv) -> dict:
    """Both scopes from one read, so a caller cannot mix them up."""
    df = (visitation_csv if isinstance(visitation_csv, pd.DataFrame)
          else pd.read_csv(visitation_csv, low_memory=False))
    out = {"daily": None, "cumulative": analyse(df, "cumulative")}
    try:
        out["daily"] = analyse(df, "daily")
    except Exception:
        pass    # older CSVs have no daily cell counts; cumulative still stands
    return out


def describe(result: dict) -> str:
    m = result["meta"]
    return (f"{m['total_teams']:,} teams · {m['flagged_teams']:,} flagged "
            f"({m['flagged_pct']:.1%}) > {m['long_field_minutes']} min with "
            f"< {m['low_coverage']:.0%} coverage · {m['stationary_teams']:,} "
            f"stationary (>= {m['stationary_min_minutes']} min, "
            f"<= {m['stationary_max_cells']} grid cell) · {m['coverage_basis']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Team Time Efficiency — coverage x time cross-reference by team")
    ap.add_argument("--visitation-csv", required=True,
                    help="{State}_day_{N}_settlement_visitation.csv from stage 2")
    ap.add_argument("--scope", choices=["daily", "cumulative"], default="cumulative")
    ap.add_argument("--output", default="Team_Time_Efficiency.xlsx")
    a = ap.parse_args()

    res = analyse(a.visitation_csv, a.scope)
    print(describe(res))
    print()
    print(res["summary"].to_string(index=False))
    if len(res["flagged"]):
        print(f"\nFlagged teams ({len(res['flagged'])}):")
        print(res["flagged"][["LGA", "Team", "Settlements", "Hours",
                              "Cells Visited", "Grid Cells", "Coverage"]]
              .head(25).to_string(index=False))
    with pd.ExcelWriter(a.output, engine="xlsxwriter") as xl:
        res["summary"].to_excel(xl, sheet_name="Summary", index=False)
        res["flagged_table"].to_excel(xl, sheet_name="Flagged Teams", index=False)
        if len(res["stationary"]):
            res["stationary"].to_excel(xl, sheet_name="Stationary Teams", index=False)
        if len(res["matrix"]):
            res["matrix"].to_excel(xl, sheet_name="Quadrants by LGA", index=False)
        res["per_team"].to_excel(xl, sheet_name="All Teams", index=False)
    print(f"\nSaved {os.path.abspath(a.output)}")
