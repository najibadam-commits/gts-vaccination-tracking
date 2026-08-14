"""Team performance & Time Spent Range analysis.

Ported from `Team Time Spent Range Analysis.py`. Answers "how long did each
team actually spend in the field, and which teams need following up", broken
down State -> LGA -> Ward, with the team codes named so the finding is
actionable at the ERM.

Relationship to the existing Time Spent Analysis
------------------------------------------------
The pipeline already has a time-spent measure, in
`stage3_erm_workbook.time_spent_analysis`. The two are NOT the same and both
are kept on purpose:

    stage3_erm_workbook.time_spent_analysis   this module
    --------------------------------------    -------------------------------
    per team, from raw GPS pings              per team, from the settlement
    restricted to the 08:00-15:00 field       analysis' per-settlement ping
    window                                    counts (no time-of-day filter)
    grain: LGA -> team                        grain: State -> LGA -> Ward -> team
    "0 (No evidence of tracks)" counted       same band, counted from teams
    as deployed minus reported                whose assigned settlements carry
                                              no pings at all
    drives the headline compliance figures    drives the breakdown tables and
    quoted in the reports                     the under-12-minutes follow-up

So the headline "X% of teams spent an hour or more in the field" still comes
from the 08:00-15:00 measure, and this module supplies the detail beneath it.
Quoting one where the report means the other is the mistake to watch for, which
is why every label this module produces says "across assigned settlements".

Counting rule
-------------
A team's time-spent band is derived ONCE per reporting level, from that team's
TOTAL minutes at that level — not per settlement. This matters: a team working
six settlements has six per-settlement bands, and counting it in each one (as
the original script did) makes the bands sum to more than the number of teams,
so the state totals stop reconciling with Team Deployment.

Re-aggregating at each level keeps every team counted exactly once per level:

    state summary   sum a team's minutes across the whole state, classify once
    LGA summary     sum within the LGA, classify once
    ward summary    sum within the ward, classify once

A team working two wards therefore appears once in each of those wards, which
is what a ward-level report should say, but still only once in the LGA and
state totals.

Caveat carried over from the source data: `track_count` is the ping count
inside a settlement's extent, by any team. Attributing it to the team the DIP
assigned to that settlement assumes one team per settlement, which is the same
assumption the per-settlement `Time Spent` column already makes.
"""
from __future__ import annotations

import argparse
import os
import re

import pandas as pd

# Each GPS ping represents 2 minutes in the field — the same factor
# stage2_analysis.classify_time_spent uses, kept in one place so the bands
# cannot drift apart between the two modules.
PING_MINUTES = 2

# Band order for display.
#
# "0 (No evidence of tracks)" is a band in its own right, matching stage3's
# team-level analysis. This module used to fold zero-minute teams into
# "<12 mins" on the assumption that a team with no tracks has no settlement
# rows. That assumption is wrong: the analysis reads the PLANNED settlement
# list, so a team that never reported still has all of its assigned settlements
# there, each with track_count 0. Every such team was therefore counted as
# having "spent under 12 minutes in the field" — which is why that bar could
# read 2,806 on a campaign where only 1,022 teams reported at all, and why the
# follow-up list was mostly teams that submitted nothing rather than teams who
# turned up briefly. They are different problems and need different bands.
NO_TRACKS = "0 (No evidence of tracks)"
UNDER_12 = "<12 mins"

TIME_RANGE_ORDER = [NO_TRACKS, UNDER_12, "12 - 30 mins", "30 mins - 1 hr",
                    "1 - 2 hrs", ">2 hrs"]

# How many team codes to name in a summary row before truncating. The original
# script cut the list at 100 silently; this one appends "(+N more)" so a
# truncated cell is never mistaken for a complete one.
MAX_TEAM_CODES = 40


def classify_minutes(minutes: float) -> str:
    """Minutes in the field -> time-spent band.

    Half-open cascading bounds, identical to
    `stage2_analysis.classify_time_spent`, so a total of exactly 12 minutes
    lands in "12 - 30 mins" rather than "<12 mins" and no value falls between
    two bands.

    Zero (or missing) minutes is NOT "<12 mins" — it is `NO_TRACKS`. A team
    that submitted nothing has not been measured as spending a short time in
    the field; it has not been seen at all.
    """
    if pd.isna(minutes) or minutes <= 0:
        return NO_TRACKS
    if minutes < 12:
        return UNDER_12
    if minutes <= 30:
        return "12 - 30 mins"
    if minutes <= 60:
        return "30 mins - 1 hr"
    if minutes <= 120:
        return "1 - 2 hrs"
    return ">2 hrs"


def _find_col(df: pd.DataFrame, keyword: str, exclude=("code", "old", "_id")) -> str | None:
    """First column whose name contains `keyword`, ignoring id/code variants.

    The source settlement list is not under our control — the same field turns
    up as `lga`, `LGA Name`, `lga_name` — so columns are matched rather than
    named. `team_code` is the one place the exclusion has to be relaxed, since
    the team column legitimately ends in "code".
    """
    for col in df.columns:
        low = col.lower().strip()
        if keyword in low and not any(re.search(rf"(?:{e})", low) for e in exclude):
            return col
    return None


def _find_team_col(df: pd.DataFrame) -> str | None:
    """The team identifier column, preferring an explicit team-code column."""
    for col in df.columns:
        low = col.lower().strip()
        if "team" in low and "code" in low:
            return col
    return _find_col(df, "team", exclude=("old", "_id"))


def _find_minutes_source(df: pd.DataFrame) -> str | None:
    """The per-settlement ping-count column minutes are derived from.

    `track_count_y` is accepted as a last resort: a visitation CSV produced
    before the day-2 column collision was fixed in stage 2 has the current
    day's counts under that name (`_x` holds the previous day's). Reading it
    keeps an already-generated CSV usable rather than silently dropping to the
    coarser fallback.
    """
    for name in ("track_count", "track count", "trackcount", "pings", "track_count_y"):
        for col in df.columns:
            if col.lower().strip() == name:
                return col
    return None


def _join_codes(codes) -> str:
    ordered = sorted({str(c).strip() for c in codes if pd.notna(c) and str(c).strip()})
    if len(ordered) <= MAX_TEAM_CODES:
        return ", ".join(ordered)
    shown = ", ".join(ordered[:MAX_TEAM_CODES])
    return f"{shown} (+{len(ordered) - MAX_TEAM_CODES} more)"


def _summarise(per_team: pd.DataFrame, group_cols: list[str],
               with_codes: bool = False) -> pd.DataFrame:
    """Unique teams per band at one reporting level, in band order.

    Bands with no teams are kept as explicit zeros so every level shows the
    same five rows — a missing band reads as "not measured", a zero reads as
    "nobody", and only the second is true.
    """
    if not len(per_team):
        cols = group_cols + ["Time Spent Range", "Teams"] + (["Team Codes"] if with_codes else [])
        return pd.DataFrame(columns=cols)

    counts = (per_team.groupby(group_cols + ["Time Spent Range"], dropna=False)["Team"]
              .nunique().reset_index(name="Teams"))
    # every (group, band) combination, so zero-team bands survive
    keys = per_team[group_cols].drop_duplicates()
    bands = pd.DataFrame({"Time Spent Range": TIME_RANGE_ORDER})
    full = keys.merge(bands, how="cross").merge(
        counts, on=group_cols + ["Time Spent Range"], how="left")
    full["Teams"] = full["Teams"].fillna(0).astype(int)

    if with_codes:
        codes = (per_team.groupby(group_cols + ["Time Spent Range"], dropna=False)["Team"]
                 .apply(_join_codes).reset_index(name="Team Codes"))
        full = full.merge(codes, on=group_cols + ["Time Spent Range"], how="left")
        full["Team Codes"] = full["Team Codes"].fillna("")

    full["_order"] = full["Time Spent Range"].map(
        {b: i for i, b in enumerate(TIME_RANGE_ORDER)})
    full = (full.sort_values(group_cols + ["_order"])
            .drop(columns="_order").reset_index(drop=True))
    return full


def _per_team_at(rows: pd.DataFrame, group_cols: list[str], minutes_col: str | None,
                 fallback_band_col: str | None) -> pd.DataFrame:
    """Total each team's time at one level, then classify it once.

    `minutes_col` is the per-settlement ping count. If the settlement analysis
    carries no ping counts, each team falls back to the band covering most of
    its settlements — less precise, but it keeps the analysis usable on an
    older visitation CSV instead of failing outright.
    """
    keys = group_cols + ["Team"]
    if minutes_col is not None:
        agg = rows.groupby(keys, dropna=False).agg(
            Settlements=("Team", "size"),
            Pings=(minutes_col, "sum")).reset_index()
        agg["Minutes"] = agg["Pings"] * PING_MINUTES
        agg["Time Spent Range"] = agg["Minutes"].apply(classify_minutes)
        return agg.drop(columns="Pings")

    agg = rows.groupby(keys, dropna=False).agg(
        Settlements=("Team", "size")).reset_index()
    mode = (rows.dropna(subset=[fallback_band_col])
            .groupby(keys, dropna=False)[fallback_band_col]
            .agg(lambda s: s.value_counts().index[0]).reset_index(name="Time Spent Range"))
    agg = agg.merge(mode, on=keys, how="left")
    # no band anywhere for this team means nothing was recorded for it, which is
    # NO_TRACKS — not a short visit
    agg["Time Spent Range"] = agg["Time Spent Range"].fillna(NO_TRACKS)
    agg["Minutes"] = pd.NA
    return agg


def analyse(visitation_csv: str | pd.DataFrame, state_name: str | None = None) -> dict:
    """Run the team performance / time-spent-range analysis.

    Returns a dict of DataFrames:
        per_team_ward  one row per team per ward — settlements, minutes, band
        ward_summary   teams per band per ward, with the team codes named
        lga_summary    teams per band per LGA
        state_summary  teams per band for the state (the headline distribution)
        under_12       teams under 12 minutes, by ward, with their codes
        under_12_lga   the same rolled up to LGA, ranked worst first
    plus `meta` describing how minutes were derived and how many teams there are.

    Every returned frame is safe to be empty; callers should not assume rows.
    """
    df = (visitation_csv if isinstance(visitation_csv, pd.DataFrame)
          else pd.read_csv(visitation_csv, low_memory=False))
    df = df.copy()

    state_col = _find_col(df, "state")
    lga_col = _find_col(df, "lga")
    ward_col = _find_col(df, "ward")
    team_col = _find_team_col(df)

    missing = [n for n, c in (("lga", lga_col), ("ward", ward_col), ("team", team_col))
               if c is None]
    if missing:
        raise ValueError(
            f"Team time-range analysis needs {missing} column(s); the settlement "
            f"analysis has {list(df.columns)[:20]}…")

    minutes_col = _find_minutes_source(df)
    band_col = next((c for c in df.columns if c.lower().strip() == "time spent"), None)
    if minutes_col is None and band_col is None:
        raise ValueError(
            "Team time-range analysis needs either a `track_count` column (to "
            "total each team's minutes) or a `Time Spent` column (to fall back "
            "to each team's most common band); the settlement analysis has "
            "neither.")

    work = pd.DataFrame({
        "State": (df[state_col] if state_col else pd.Series(state_name or "", index=df.index))
        .astype(str).str.strip().str.title(),
        "LGA": df[lga_col].astype(str).str.replace("_", " ", regex=False)
        .str.replace(r"\s+", " ", regex=True).str.strip().str.title(),
        "Ward": df[ward_col].astype(str).str.replace("_", " ", regex=False)
        .str.replace(r"\s+", " ", regex=True).str.strip().str.title(),
        "Team": df[team_col].astype(str).str.strip(),
    })
    if minutes_col is not None:
        work[minutes_col] = pd.to_numeric(df[minutes_col], errors="coerce").fillna(0)
    if band_col is not None:
        work[band_col] = df[band_col]

    # Rows with no team assigned cannot be attributed to anyone. The original
    # script's top-12-states filter is deliberately not carried over: this
    # pipeline is already filtered to one state upstream.
    work = work[work["Team"].notna() & ~work["Team"].isin(["", "nan", "None"])]
    if state_name:
        sel = work[work["State"].str.title() == str(state_name).title()]
        if len(sel):
            work = sel

    per_team_ward = _per_team_at(work, ["State", "LGA", "Ward"], minutes_col, band_col)
    per_team_lga = _per_team_at(work, ["State", "LGA"], minutes_col, band_col)
    per_team_state = _per_team_at(work, ["State"], minutes_col, band_col)

    ward_summary = _summarise(per_team_ward, ["State", "LGA", "Ward"], with_codes=True)
    lga_summary = _summarise(per_team_lga, ["State", "LGA"])
    state_summary = _summarise(per_team_state, ["State"])

    # Exact band match, not a substring test: "12" also appears in "12 - 30
    # mins", which is what made the original script's sheet overstate.
    u12 = per_team_ward[per_team_ward["Time Spent Range"] == UNDER_12]
    if len(u12):
        under_12 = (u12.groupby(["State", "LGA", "Ward"], dropna=False)
                    .agg(**{"Teams Under 12 Mins": ("Team", "nunique"),
                            "Team Codes": ("Team", _join_codes)})
                    .reset_index()
                    .sort_values("Teams Under 12 Mins", ascending=False)
                    .reset_index(drop=True))
    else:
        under_12 = pd.DataFrame(
            columns=["State", "LGA", "Ward", "Teams Under 12 Mins", "Team Codes"])

    # LGA-level roll-up, recomputed from `per_team_lga` rather than summed from
    # the ward table: a team working two wards is under 12 minutes in each of
    # them, so summing the ward counts would double-count it. At LGA level the
    # team's minutes are totalled across its wards and classified once.
    u12_lga = per_team_lga[per_team_lga["Time Spent Range"] == UNDER_12]
    if len(u12_lga):
        under_12_lga = (u12_lga.groupby(["State", "LGA"], dropna=False)
                        .agg(**{"Teams Under 12 Mins": ("Team", "nunique"),
                                "Team Codes": ("Team", _join_codes)})
                        .reset_index()
                        .sort_values("Teams Under 12 Mins", ascending=False)
                        .reset_index(drop=True))
    else:
        under_12_lga = pd.DataFrame(
            columns=["State", "LGA", "Teams Under 12 Mins", "Team Codes"])

    total_teams = int(per_team_state["Team"].nunique()) if len(per_team_state) else 0
    n_under_12 = int(per_team_state.loc[
        per_team_state["Time Spent Range"] == UNDER_12, "Team"].nunique()) \
        if len(per_team_state) else 0
    n_no_tracks = int(per_team_state.loc[
        per_team_state["Time Spent Range"] == NO_TRACKS, "Team"].nunique()) \
        if len(per_team_state) else 0
    n_reported = total_teams - n_no_tracks

    # Reconciliation, printed so the figures on the slide can be checked
    # against the deployment table without opening the workbook. The bands are
    # mutually exclusive and cover every team, so they must sum to the number
    # of team codes in the planned settlement list.
    band_total = int(state_summary["Teams"].sum()) if len(state_summary) else 0
    print(f"  time-spent bands: {band_total:,} team-band rows across "
          f"{total_teams:,} team codes in the settlement list — "
          f"{n_reported:,} with tracks, {n_no_tracks:,} with none, "
          f"{n_under_12:,} under 12 mins")
    if band_total != total_teams:
        print(f"  WARNING: bands sum to {band_total:,} but there are "
              f"{total_teams:,} teams — a team is being counted in two bands")

    return {
        "per_team_ward": per_team_ward,
        "ward_summary": ward_summary,
        "lga_summary": lga_summary,
        "state_summary": state_summary,
        "under_12": under_12,
        "under_12_lga": under_12_lga,
        "meta": {
            "minutes_from": minutes_col or f"fallback: most common `{band_col}` band",
            "exact_minutes": minutes_col is not None,
            "total_teams": total_teams,
            "teams_under_12": n_under_12,
            "under_12_pct": (n_under_12 / total_teams) if total_teams else 0.0,
            # teams in the planned list whose assigned settlements carry no
            # pings at all — counted separately from the under-12 follow-up
            "teams_no_tracks": n_no_tracks,
            "no_tracks_pct": (n_no_tracks / total_teams) if total_teams else 0.0,
            "teams_with_tracks": n_reported,
        },
    }


def state_distribution(result: dict) -> pd.DataFrame:
    """The headline band distribution as `Time Spent Range` / `Teams`.

    Collapsed across states (there is only ever one here) so charts and slides
    have a simple two-column frame to read.
    """
    s = result["state_summary"]
    if not len(s):
        return pd.DataFrame({"Time Spent Range": TIME_RANGE_ORDER,
                             "Teams": [0] * len(TIME_RANGE_ORDER)})
    out = (s.groupby("Time Spent Range", dropna=False)["Teams"].sum()
           .reindex(TIME_RANGE_ORDER).fillna(0).astype(int).reset_index())
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Team performance & time-spent-range analysis over a day's "
                    "settlement visitation CSV")
    ap.add_argument("--visitation-csv", required=True,
                    help="{State}_day_{N}_settlement_visitation.csv from stage 2")
    ap.add_argument("--state", default=None, help="Filter to one state")
    ap.add_argument("--output", default="Team_TimeSpent_Range_Summary.xlsx",
                    help="Workbook to write the four summaries to")
    a = ap.parse_args()

    res = analyse(a.visitation_csv, a.state)
    meta = res["meta"]
    print(f"Teams: {meta['total_teams']:,} | under 12 mins: {meta['teams_under_12']:,} "
          f"({meta['under_12_pct']:.1%}) | minutes from: {meta['minutes_from']}")
    with pd.ExcelWriter(a.output, engine="xlsxwriter") as xl:
        res["ward_summary"].to_excel(xl, sheet_name="Ward_Level_Summary", index=False)
        res["lga_summary"].to_excel(xl, sheet_name="LGA_Level_Summary", index=False)
        res["state_summary"].to_excel(xl, sheet_name="State_Level_Summary", index=False)
        res["under_12"].to_excel(xl, sheet_name="Less_Than_12mins", index=False)
        res["per_team_ward"].to_excel(xl, sheet_name="Team_Level_Data", index=False)
    print(f"Saved {os.path.abspath(a.output)}")
