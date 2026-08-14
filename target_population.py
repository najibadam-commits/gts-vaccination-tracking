"""Target Population & Household Coverage analysis.

Translates settlement visitation into the two indicators the planned
settlement document carries per settlement:

    Set Population        estimated under-5 children targeted
    Number of Households  estimated households

and reports, for each, how much sits in settlements that were visited
("reached") versus not visited ("not reached"), with percentages and a per-LGA
breakdown.

What "reached" means here
-------------------------
A settlement's full estimated population counts as reached if the settlement
was visited at all. This is an ESTIMATE of exposure, not a measure of children
vaccinated: it says "teams reached the settlements where this many under-5s are
estimated to live", not "this many children were vaccinated". Every label the
pipeline produces from this module says "estimated" for that reason. Partial
coverage within a visited settlement is not modelled — the settlement's own
`Coverage` fraction already carries that, and mixing the two would imply a
precision the source estimates do not have.

Relationship to `missed_children_table`
---------------------------------------
`stage3_erm_workbook.missed_children_table` already sums the target-population
field over NOT-visited settlements per LGA, for the post-campaign report. This
module is a superset: it covers both indicators, both sides of the split, the
percentages, and runs for daily as well as post-campaign. The older table is
left alone so the existing PIR section is unchanged.

Column detection
----------------
The planned settlement document is not under our control and the two fields
turn up under several names ("Set Population", "set_population", "population";
"Number of Households", "no_of_households", "households"). Both are matched
tolerantly, and either one being absent degrades to reporting the other rather
than failing — a settlement list with no household column still produces the
under-5 analysis.
"""
from __future__ import annotations

import argparse
import os

import pandas as pd

# Candidate column names per indicator, best first. Matching is done on a
# normalized name (lowercased, non-alphanumerics collapsed to underscore), so
# "Set Population", "SET-POPULATION" and "set_population" all land together.
POPULATION_CANDIDATES = (
    "set_population", "set_target", "target_population", "under_5_population",
    "u5_population", "population", "target",
)
HOUSEHOLD_CANDIDATES = (
    "number_of_households", "no_of_households", "num_households", "households",
    "household", "hh", "no_of_hh", "number_of_household",
)

# Estimated targeted households MISSED in a planned settlement — a figure the
# microplan supplies per settlement, not something this module derives. Where
# the column is absent the indicator is dropped from every output rather than
# estimated, so a deck never shows a number the source data did not contain.
MISSING_HOUSEHOLD_CANDIDATES = (
    "estimated_targeted_missing_households", "targeted_missing_households",
    "estimated_missing_households", "est_missing_households",
    "missing_households", "missed_households", "missing_household",
    "number_of_missing_households", "no_of_missing_households",
    "missing_hh", "missed_hh",
)

# Name segments that disqualify a column: a "population by day" planning field
# is not the settlement's total estimate, and a code/id column is not a count.
#
# Matched against whole underscore-separated SEGMENTS, not as substrings. A
# substring test looks equivalent and is not — "households" contains "old", so
# a substring rule silently rejected every household column there is.
EXCLUDE_SEGMENTS = frozenset({
    "day", "days", "daily", "code", "codes", "id", "ids", "uid",
    "pct", "percent", "percentage", "old", "prev", "previous",
})

# Additional segments barred from the population/household searches only. Their
# substring pass would otherwise read "estimated_targeted_missing_households" as
# the settlement's household estimate — "households" is inside it — and report a
# shortfall figure as the total. The missing-household search uses the plain
# EXCLUDE_SEGMENTS above so it can still find those columns.
SHORTFALL_SEGMENTS = frozenset({"missing", "missed", "unreached", "gap", "shortfall"})

REACHED = "Reached (Visited)"
NOT_REACHED = "Not Reached (Not Visited)"


def _norm(name: str) -> str:
    out = []
    for ch in str(name).strip().lower():
        out.append(ch if ch.isalnum() else "_")
    while "__" in (joined := "".join(out)):
        out = list(joined.replace("__", "_"))
    return "".join(out).strip("_")


def find_indicator_col(df: pd.DataFrame, candidates: tuple[str, ...],
                       exclude_segments: frozenset[str] = EXCLUDE_SEGMENTS) -> str | None:
    """First column matching one of `candidates`, exact match before substring.

    Exact matches are tried across all candidates before any substring match,
    so a list carrying both `population` and `set_population` picks the
    settlement estimate rather than whichever column happens to come first.

    `exclude_segments` is the disqualifying-segment set to apply; callers
    looking for a total pass the stricter set so a shortfall column cannot be
    mistaken for one.
    """
    normed = {col: _norm(col) for col in df.columns}
    usable = {col: n for col, n in normed.items()
              if not (set(n.split("_")) & exclude_segments)}
    for cand in candidates:
        for col, n in usable.items():
            if n == cand:
                return col
    for cand in candidates:
        for col, n in usable.items():
            if cand in n:
                return col
    return None


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").fillna(0)


def _split(df: pd.DataFrame, value_col: str, visited_mask: pd.Series,
           coverage: pd.Series | None = None) -> dict:
    """Totals and percentages for one indicator.

    Two different questions are answered here and they must not be confused:

    `total` / `reached` / `reached_pct`
        Settlement-level exposure. A settlement's WHOLE estimate counts as
        reached if the settlement was visited at all. This drives the donuts.

    `visited_total` / `within_reached` / `within_pct`
        Depth inside the settlements actually visited. `within_reached` weights
        each visited settlement's estimate by the fraction of its grid that was
        covered, so it answers "of the population in the settlements teams got
        to, how much did they actually work through". This is what the slide
        narrative quotes, and it is always <= `visited_total`.

    With no coverage column the weighted figures fall back to the unweighted
    ones, which makes `within_pct` 100% — honest, if uninformative.
    """
    values = _numeric(df[value_col])
    total = float(values.sum())
    reached = float(values[visited_mask].sum())
    not_reached = total - reached

    visited_total = reached
    if coverage is not None:
        frac = pd.to_numeric(coverage, errors="coerce").fillna(0).clip(0, 1)
        within_reached = float((values[visited_mask] * frac[visited_mask]).sum())
    else:
        within_reached = visited_total

    return {
        "total": int(round(total)),
        "reached": int(round(reached)),
        "not_reached": int(round(not_reached)),
        "reached_pct": (reached / total) if total else 0.0,
        "not_reached_pct": (not_reached / total) if total else 0.0,
        "visited_total": int(round(visited_total)),
        "within_reached": int(round(within_reached)),
        "within_pct": (within_reached / visited_total) if visited_total else 0.0,
        "column": value_col,
    }


def analyse_daily_and_cumulative(visitation_csv: str | pd.DataFrame, daily_col: str,
                                 cum_col: str) -> dict:
    """Both temporal scopes in one pass — daily is primary, cumulative is context.

    The reporting rule is `Daily = the reporting day only`,
    `Cumulative = Day 1 through the reporting day`, and the two must never be
    mixed in one figure. Returning them together, from one read of the
    settlement list, is what keeps callers from reaching for whichever column
    is nearest and quietly reporting cumulative numbers as daily ones.

    Returns {"daily": <analyse() result>, "cumulative": <analyse() result>}.
    `daily` is None when the settlement list predates the daily status column
    (an older run), in which case callers should present cumulative only and
    say so rather than passing it off as the day's work.
    """
    dip = (visitation_csv if isinstance(visitation_csv, pd.DataFrame)
           else pd.read_csv(visitation_csv, low_memory=False))
    cumulative = analyse(dip, cum_col)
    daily = None
    if daily_col and daily_col in dip.columns:
        # scope stays the planned settlements (cum_col not null); only the
        # visited/not determination comes from the daily column
        daily = analyse(dip, daily_col, scope_col=cum_col)
    return {"daily": daily, "cumulative": cumulative}


def analyse(visitation_csv: str | pd.DataFrame, status_col: str,
            scope_col: str | None = None) -> dict:
    """Estimated under-5 and household coverage from a day's visitation CSV.

    `status_col` decides reached vs not reached — pass `day_{N}_daily` for the
    reporting day alone or `day_{N}_cumm` for Day 1 to date. `scope_col`
    decides which settlements are in the denominator at all, and defaults to
    `status_col`; the daily call passes the cumulative column so that the
    denominator stays the full planned list and the day's reach is measured
    against it rather than against itself.

    Returns:
        children    split dict, or None when the settlement list has no
                    population column
        households  split dict, or None when it has no household column
        by_lga      per-LGA DataFrame with both indicators, reached and total,
                    ranked by the largest not-reached under-5 estimate
        summary     tidy DataFrame for the workbook tab
        settlements planned / visited / not-visited settlement counts
    Raises ValueError only if NEITHER indicator column is present.
    """
    dip = (visitation_csv if isinstance(visitation_csv, pd.DataFrame)
           else pd.read_csv(visitation_csv, low_memory=False))
    dip = dip.copy()
    if status_col not in dip.columns:
        raise ValueError(f"'{status_col}' not in the settlement analysis "
                         f"({list(dip.columns)[:15]}…)")
    scope_col = scope_col if scope_col and scope_col in dip.columns else status_col

    # Totals first, with shortfall-style names barred, then the shortfall
    # column itself. Nothing is derived here: no missing-households column in
    # the settlement list means the indicator is simply absent downstream.
    totals_exclude = EXCLUDE_SEGMENTS | SHORTFALL_SEGMENTS
    pop_col = find_indicator_col(dip, POPULATION_CANDIDATES, totals_exclude)
    hh_col = find_indicator_col(dip, HOUSEHOLD_CANDIDATES, totals_exclude)
    missing_hh_col = find_indicator_col(dip, MISSING_HOUSEHOLD_CANDIDATES)
    if pop_col is None and hh_col is None:
        raise ValueError(
            "The planned settlement document has neither an estimated "
            "population column (Set Population) nor a household column "
            "(Number of Households); nothing to analyse.")

    # Only settlements that were actually planned — a null status means the
    # settlement was not in scope, and counting its population would inflate
    # the denominator.
    planned = dip[dip[scope_col].notna()].copy()
    visited_mask = planned[status_col].astype(str).str.strip().str.title() == "Visited"

    lga_col = next((c for c in planned.columns
                    if "lga" in c.lower() and "code" not in c.lower()), None)

    # Coverage fraction matching the scope: the day's own coverage for the
    # daily call, the cumulative one otherwise. Reading the wrong one here
    # would weight today's reach by every previous day's work.
    cov_col = ("Daily Coverage" if "daily" in status_col.lower() else "Coverage")
    coverage = planned[cov_col] if cov_col in planned.columns else None

    children = _split(planned, pop_col, visited_mask, coverage) if pop_col else None
    households = _split(planned, hh_col, visited_mask, coverage) if hh_col else None

    # Estimated targeted missing households — a supplied per-settlement figure,
    # so it is summed, not split into reached/not-reached. What matters
    # operationally is how much of it sits in settlements teams never got to:
    # that share is still entirely outstanding, while the rest sits in
    # settlements a team did reach and may already have been worked through.
    missing_households = None
    if missing_hh_col:
        mvals = _numeric(planned[missing_hh_col])
        total_missing = float(mvals.sum())
        in_visited = float(mvals[visited_mask].sum())
        missing_households = {
            "total": int(round(total_missing)),
            "in_visited": int(round(in_visited)),
            "in_not_visited": int(round(total_missing - in_visited)),
            "in_not_visited_pct": ((total_missing - in_visited) / total_missing
                                   if total_missing else 0.0),
            "settlements_with_missing": int((mvals > 0).sum()),
            "column": missing_hh_col,
        }

    # ---- per-LGA breakdown
    by_lga = pd.DataFrame()
    if lga_col is not None:
        work = pd.DataFrame({
            "LGA": planned[lga_col].astype(str).str.replace("_", " ", regex=False)
            .str.replace(r"\s+", " ", regex=True).str.strip().str.title(),
            "_visited": visited_mask.values,
        })
        if pop_col:
            work["_pop"] = _numeric(planned[pop_col]).values
        if hh_col:
            work["_hh"] = _numeric(planned[hh_col]).values
        if missing_hh_col:
            work["_miss"] = _numeric(planned[missing_hh_col]).values

        rows = {"Settlements": work.groupby("LGA").size()}
        if pop_col:
            rows["Estimated <5 Children"] = work.groupby("LGA")["_pop"].sum()
            rows["<5 Children Reached"] = (work[work["_visited"]]
                                           .groupby("LGA")["_pop"].sum())
        if hh_col:
            rows["Estimated Households"] = work.groupby("LGA")["_hh"].sum()
            rows["Households Reached"] = (work[work["_visited"]]
                                          .groupby("LGA")["_hh"].sum())
        if missing_hh_col:
            rows["Est. Targeted Missing Households"] = work.groupby("LGA")["_miss"].sum()
            rows["Missing HH in Unvisited Settlements"] = (
                work[~work["_visited"]].groupby("LGA")["_miss"].sum())
        by_lga = pd.DataFrame(rows).fillna(0)

        if pop_col:
            by_lga["<5 Children Not Reached"] = (
                by_lga["Estimated <5 Children"] - by_lga["<5 Children Reached"])
            by_lga["<5 Reached %"] = (by_lga["<5 Children Reached"] /
                                      by_lga["Estimated <5 Children"]).fillna(0)
        if hh_col:
            by_lga["Households Not Reached"] = (
                by_lga["Estimated Households"] - by_lga["Households Reached"])
            by_lga["Households Reached %"] = (by_lga["Households Reached"] /
                                              by_lga["Estimated Households"]).fillna(0)

        sort_on = ("<5 Children Not Reached" if pop_col else "Households Not Reached")
        by_lga = by_lga.sort_values(sort_on, ascending=False).reset_index()
        for c in by_lga.columns:
            if c != "LGA" and "%" not in c:
                by_lga[c] = by_lga[c].round().astype(int)

    # ---- tidy summary for the workbook
    summary_rows = []
    for name, split in (("Estimated <5 Target Population", children),
                        ("Estimated Households", households)):
        if split is None:
            continue
        summary_rows.append({
            "Indicator": name,
            "Estimated Total": split["total"],
            REACHED: split["reached"],
            NOT_REACHED: split["not_reached"],
            "Reached %": split["reached_pct"],
            "Not Reached %": split["not_reached_pct"],
        })
    if missing_households:
        # Row shaped to the same columns so the workbook tab stays one table.
        # "Reached" has no meaning for a shortfall figure, so the split shown
        # is where the shortfall sits: unvisited settlements vs visited ones.
        summary_rows.append({
            "Indicator": "Est. Targeted Missing Households",
            "Estimated Total": missing_households["total"],
            REACHED: missing_households["in_visited"],
            NOT_REACHED: missing_households["in_not_visited"],
            "Reached %": (1 - missing_households["in_not_visited_pct"]),
            "Not Reached %": missing_households["in_not_visited_pct"],
        })
    summary = pd.DataFrame(summary_rows)

    n_planned = int(len(planned))
    n_visited = int(visited_mask.sum())
    return {
        "children": children,
        "households": households,
        "missing_households": missing_households,
        "by_lga": by_lga,
        "summary": summary,
        "settlements": {
            "planned": n_planned, "visited": n_visited,
            "not_visited": n_planned - n_visited,
            "pct": (n_visited / n_planned) if n_planned else 0.0,
        },
        "meta": {
            "population_column": pop_col,
            "household_column": hh_col,
            "missing_household_column": missing_hh_col,
            "status_column": status_col,
            "scope_column": scope_col,
        },
    }


def describe(result: dict) -> str:
    """One-line run-log summary of what was found and from which columns."""
    meta, bits = result["meta"], []
    if result["children"]:
        c = result["children"]
        bits.append(f"<5 children {c['reached']:,}/{c['total']:,} reached "
                    f"({c['reached_pct']:.1%}) from `{meta['population_column']}`")
    else:
        bits.append("no estimated population column found")
    if result["households"]:
        h = result["households"]
        bits.append(f"households {h['reached']:,}/{h['total']:,} reached "
                    f"({h['reached_pct']:.1%}) from `{meta['household_column']}`")
    else:
        bits.append("no household column found")
    if result.get("missing_households"):
        m = result["missing_households"]
        bits.append(f"est. targeted missing households {m['total']:,} "
                    f"({m['in_not_visited']:,} in unvisited settlements) from "
                    f"`{meta['missing_household_column']}`")
    else:
        bits.append("no estimated-missing-households column — indicator omitted")
    return "; ".join(bits)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Estimated under-5 target population and household coverage "
                    "from a day's settlement visitation CSV")
    ap.add_argument("--visitation-csv", required=True)
    ap.add_argument("--cum-col", required=True,
                    help="Cumulative visitation column (Day 1 to date), e.g. day_3_cumm")
    ap.add_argument("--daily-col", default=None,
                    help="Daily visitation column (that day only), e.g. day_3_daily. "
                         "Defaults to the cum-col's day, if that column exists.")
    ap.add_argument("--output", default="Target_Population_Coverage.xlsx")
    a = ap.parse_args()

    daily_col = a.daily_col or a.cum_col.replace("_cumm", "_daily")
    both = analyse_daily_and_cumulative(a.visitation_csv, daily_col, a.cum_col)
    if both["daily"]:
        print(f"DAILY      ({daily_col}): {describe(both['daily'])}")
    else:
        print(f"DAILY      ({daily_col}): column not present — cumulative only")
    print(f"CUMULATIVE ({a.cum_col}): {describe(both['cumulative'])}")

    with pd.ExcelWriter(a.output, engine="xlsxwriter") as xl:
        for name, res in (("Daily", both["daily"]), ("Cumulative", both["cumulative"])):
            if res is None:
                continue
            res["summary"].to_excel(xl, sheet_name=f"{name}_Summary", index=False)
            if len(res["by_lga"]):
                res["by_lga"].to_excel(xl, sheet_name=f"{name}_By_LGA", index=False)
    print(f"Saved {os.path.abspath(a.output)}")
