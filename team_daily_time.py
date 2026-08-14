"""Daily time spent tracking, measured from the raw track timestamps.

One question, answered one way: **for each team, on each tracking date, how
long was it actually tracking?**

Why this module exists
----------------------
The Time Spent Range chart used to be derived from `team_time_range`, which
totals each team's per-settlement ping counts out of the PLANNED settlement
list. Two consequences made its numbers wrong for this question:

    the population was every team code in the settlement plan, not the teams
    that actually transmitted — so a campaign with 797 reporting teams
    produced bars summing to several thousand

    duration was inferred from ping counts against the settlement list rather
    than read from the timestamps, so it could not distinguish a team tracking
    steadily for two hours from a device left switched on

This module reads the merged track export directly. Nothing about settlements,
assignment, coverage or the 08:00-15:00 field window enters into it.

How duration is measured
------------------------
Pings are sorted per (team, local date) and the gaps between consecutive pings
are summed. A gap longer than `MAX_GAP_MINUTES` is not counted: the tracker was
off, out of range, or the team had stopped, and none of that is time spent
tracking. This is why the figure is not simply last-minus-first, which would
count an hour's break as an hour of work.

A team with a single ping on a date has no gaps and so no measurable duration;
it is credited `SINGLE_PING_MINUTES` rather than zero, since one ping is
evidence of presence, not of nothing.

Local time is UTC+1, applied to the whole timestamp so a late-evening ping
belongs to the correct local date.
"""
from __future__ import annotations

import argparse
import os

import pandas as pd

# Column names in the merged track export.
TS_COL = "GPS Timestamp (UTC)"
TEAM_COL = "Team Code"
LGA_COL = "NGA LGA 2024 Label"
TS_FORMAT = "%m/%d/%Y %H:%M:%S"
LOCAL_OFFSET = pd.Timedelta(hours=1)


def _norm_place(series: pd.Series) -> pd.Series:
    """Normalise a place name for comparison across two sources.

    The same LGA appears as "Talata_Mafara" in one file and "Talata Mafara" in
    another, so names are compared with underscores collapsed, whitespace
    squeezed and case ignored. Identical to `stage3_erm_workbook.norm_lga`,
    which does the same job for the same reason.
    """
    return (series.astype(str).str.replace("_", " ", regex=False)
            .str.replace(r"\s+", " ", regex=True).str.strip().str.title())


def _find_state_col(columns) -> str | None:
    """A state column in the track export, if it carries one."""
    for col in columns:
        low = str(col).lower()
        if "state" in low and "code" not in low and "id" not in low:
            return col
    return None


def scope_from_visitation(visitation) -> dict:
    """The state scope to filter tracks by, taken from the day's visitation CSV.

    Stage 2 has already filtered that file to the campaign's state, so its LGA
    and team-code sets ARE the state — no new input is needed and the scope
    cannot drift from the rest of the pipeline.

    Returns {"state", "lgas", "teams"}, any of which may be empty.
    """
    dip = (pd.read_csv(visitation, low_memory=False)
           if isinstance(visitation, str) else visitation)

    def pick(keyword, exclude=("code", "id")):
        for col in dip.columns:
            low = str(col).lower()
            if keyword in low and not any(x in low for x in exclude):
                return col
        return None

    state_col = pick("state")
    lga_col = pick("lga")
    team_col = next((c for c in dip.columns
                     if "team" in str(c).lower() and "code" in str(c).lower()), None)

    states = (set(_norm_place(dip[state_col]).dropna()) - {"", "Nan", "None"}
              if state_col else set())
    return {
        "state": next(iter(states)) if len(states) == 1 else None,
        "lgas": (frozenset(_norm_place(dip[lga_col]).dropna()) - {"", "Nan", "None"}
                 if lga_col else frozenset()),
        "teams": (frozenset(dip[team_col].dropna().astype(str).str.strip())
                  - {""} if team_col else frozenset()),
    }

# Gaps longer than this are treated as "not tracking" and excluded from the
# total. Set above the device's normal reporting interval with room for a
# missed ping or two, but well below the length of a genuine break.
MAX_GAP_MINUTES = 15.0
# Credit for a date on which a team produced exactly one ping.
SINGLE_PING_MINUTES = 2.0

# Bands, in display order. Same boundaries the pipeline has always used, so
# these figures can be read against the workbook's other time tables.
TIME_RANGE_ORDER = ["<12 mins", "12 - 30 mins", "30 mins - 1 hr",
                    "1 - 2 hrs", ">2 hrs"]
UNDER_12 = "<12 mins"


def classify_minutes(minutes: float) -> str:
    """Minutes tracked -> band. Half-open bounds, so no value falls between two."""
    if pd.isna(minutes):
        return UNDER_12
    if minutes < 12:
        return UNDER_12
    if minutes <= 30:
        return "12 - 30 mins"
    if minutes <= 60:
        return "30 mins - 1 hr"
    if minutes <= 120:
        return "1 - 2 hrs"
    return ">2 hrs"


def _durations(group: pd.Series) -> float:
    """Minutes tracked for one team on one date, from its ping timestamps."""
    times = group.sort_values()
    if len(times) <= 1:
        return SINGLE_PING_MINUTES
    gaps = times.diff().dropna().dt.total_seconds() / 60.0
    return float(gaps[gaps <= MAX_GAP_MINUTES].sum())


def per_team_per_date(tracks_file: str, chunksize: int = 500_000,
                      scope: dict | None = None) -> pd.DataFrame:
    """Minutes tracked by every team on every date in the export.

    Returns columns: date, team_code, lga, Pings, Minutes, Time Spent Range.
    One row per team per date — durations are never combined across dates.

    `scope` restricts the rows to ONE STATE. GTS exports are national, so
    without it a campaign in one state has its time-spent figures computed over
    every state's teams. Built by `scope_from_visitation` from the day's
    visitation CSV, which stage 2 has already filtered to the campaign's state.
    Three routes are tried, in descending order of reliability:

        1. the export's own state column, where it has one
        2. the state's LGA names — the same rule stage 3 uses on this file
        3. the state's team codes, as a last resort

    Timestamps have to be seen together to measure a gap, so the file is read
    in chunks but the timestamps themselves are held per team/date rather than
    aggregated on the fly. Only a handful of columns are read, which keeps this
    affordable on a multi-million-row export.
    """
    scope = scope or {}
    want_state = scope.get("state")
    want_lgas = scope.get("lgas") or frozenset()
    want_teams = scope.get("teams") or frozenset()

    keep = {TS_COL, TEAM_COL, LGA_COL}
    header = pd.read_csv(tracks_file, nrows=0)
    state_col = _find_state_col(header.columns) if want_state else None
    if state_col:
        keep.add(state_col)

    route = None
    if state_col:
        route = f"state column '{state_col}' == {want_state}"
    elif want_lgas:
        route = f"{len(want_lgas)} LGA name(s) from the settlement list"
    elif want_teams:
        route = f"{len(want_teams)} team code(s) from the settlement list"
    if route:
        print(f"  time spent scoped to the campaign state by {route}")

    parts = []
    seen = kept = 0
    for chunk in pd.read_csv(tracks_file, usecols=lambda c: c in keep,
                             chunksize=chunksize, dtype=str):
        if TS_COL not in chunk.columns or TEAM_COL not in chunk.columns:
            raise ValueError(
                f"Track export needs '{TS_COL}' and '{TEAM_COL}' to measure "
                f"daily tracking time; it has {list(chunk.columns)}")
        chunk = chunk.dropna(subset=[TEAM_COL])
        seen += len(chunk)

        if state_col and state_col in chunk.columns:
            chunk = chunk[_norm_place(chunk[state_col]) == want_state]
        elif want_lgas and LGA_COL in chunk.columns:
            chunk = chunk[_norm_place(chunk[LGA_COL]).isin(want_lgas)]
        elif want_teams:
            chunk = chunk[chunk[TEAM_COL].astype(str).str.strip().isin(want_teams)]
        kept += len(chunk)
        if not len(chunk):
            continue

        ts = pd.to_datetime(chunk[TS_COL], format=TS_FORMAT, errors="coerce")
        local = ts + LOCAL_OFFSET
        part = pd.DataFrame({
            "date": local.dt.strftime("%Y-%m-%d"),
            "team_code": chunk[TEAM_COL].astype(str).str.strip(),
            "lga": (chunk[LGA_COL].astype(str).str.strip()
                    if LGA_COL in chunk.columns else ""),
            "_ts": local,
        })
        parts.append(part.dropna(subset=["_ts"]))

    if route:
        print(f"  {kept:,} of {seen:,} pings are in the campaign state")
        if not kept and seen:
            print("  WARNING: the state filter matched no pings — check that "
                  "the track export covers this state, and that its LGA names "
                  "match the settlement list's")

    if not parts:
        return pd.DataFrame(columns=["date", "team_code", "lga", "Pings",
                                     "Minutes", "Time Spent Range"])

    allp = pd.concat(parts, ignore_index=True)
    allp = allp[allp["team_code"] != ""]

    grouped = allp.groupby(["date", "team_code"], dropna=False)
    out = grouped.agg(Pings=("_ts", "size"),
                      lga=("lga", "first")).reset_index()
    out["Minutes"] = grouped["_ts"].apply(_durations).values
    out["Minutes"] = out["Minutes"].round(1)
    out["Time Spent Range"] = out["Minutes"].apply(classify_minutes)
    return out[["date", "team_code", "lga", "Pings", "Minutes",
                "Time Spent Range"]]


def latest_date(per_team: pd.DataFrame) -> str | None:
    """The most recent date present, or None for an empty frame."""
    if not len(per_team):
        return None
    return str(per_team["date"].max())


def analyse(tracks_file: str, report_date: str | None = "latest",
            scope: dict | None = None) -> dict:
    """Daily time spent for one tracking date, in one state.

    `report_date` is "latest" (the default), an explicit "YYYY-MM-DD", or None
    to return every date's rows without selecting one.

    `scope` restricts the rows to the campaign's state — see
    `per_team_per_date`. Pass `scope_from_visitation(visitation_csv)`; without
    it the figures are computed over every state in the export.

    Returns:
        per_team      one row per team for the selected date
        distribution  Time Spent Range / Teams, in band order
        by_lga        teams per band per LGA
        all_dates     every team-date row, so other dates can be inspected
        meta          the date used, team counts and the rules applied
    """
    all_rows = per_team_per_date(tracks_file, scope=scope)
    chosen = (latest_date(all_rows) if report_date == "latest" else report_date)

    per_team = (all_rows[all_rows["date"] == chosen] if chosen
                else all_rows).reset_index(drop=True)

    counts = (per_team.groupby("Time Spent Range")["team_code"].nunique()
              .reindex(TIME_RANGE_ORDER).fillna(0).astype(int))
    distribution = counts.reset_index(name="Teams")

    by_lga = pd.DataFrame()
    if len(per_team):
        by_lga = (per_team.groupby(["lga", "Time Spent Range"])["team_code"]
                  .nunique().unstack(fill_value=0)
                  .reindex(columns=TIME_RANGE_ORDER, fill_value=0)
                  .reset_index().rename(columns={"lga": "LGA"}))

    n_teams = int(per_team["team_code"].nunique())
    n_u12 = int(counts.get(UNDER_12, 0))
    dates = sorted(all_rows["date"].dropna().unique().tolist())

    print(f"  daily time spent: {n_teams:,} teams transmitted on {chosen} "
          f"({len(dates)} date(s) in the export) — bands sum to "
          f"{int(distribution['Teams'].sum()):,}")

    return {
        "per_team": per_team,
        "distribution": distribution,
        "by_lga": by_lga,
        "all_dates": all_rows,
        "meta": {
            "date": chosen,
            "dates_in_export": dates,
            "total_teams": n_teams,
            "teams_under_12": n_u12,
            "under_12_pct": (n_u12 / n_teams) if n_teams else 0.0,
            "max_gap_minutes": MAX_GAP_MINUTES,
            "state": (scope or {}).get("state"),
            "state_scoped": bool(scope and (scope.get("state")
                                            or scope.get("lgas")
                                            or scope.get("teams"))),
            "median_minutes": (float(per_team["Minutes"].median())
                               if len(per_team) else 0.0),
        },
    }


def describe(result: dict) -> str:
    m = result["meta"]
    return (f"{m['total_teams']:,} teams tracked on {m['date']} · median "
            f"{m['median_minutes']:.0f} min · {m['teams_under_12']:,} under 12 "
            f"min ({m['under_12_pct']:.1%}) · gaps over "
            f"{m['max_gap_minutes']:g} min excluded")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Daily time spent tracking, per team, from track timestamps")
    ap.add_argument("--tracks", required=True, help="merged_tracks.csv")
    ap.add_argument("--date", default="latest",
                    help='"latest" (default), a YYYY-MM-DD date, or "all"')
    ap.add_argument("--visitation-csv", default=None,
                    help="the day's visitation CSV — restricts the analysis to "
                         "that campaign's state (strongly recommended, since "
                         "GTS track exports are national)")
    ap.add_argument("--output", default="Daily_Time_Spent.xlsx")
    a = ap.parse_args()

    scope = scope_from_visitation(a.visitation_csv) if a.visitation_csv else None
    res = analyse(a.tracks, None if a.date == "all" else a.date, scope=scope)
    print(describe(res))
    print()
    print(res["distribution"].to_string(index=False))
    with pd.ExcelWriter(a.output, engine="xlsxwriter") as xl:
        res["distribution"].to_excel(xl, sheet_name="Distribution", index=False)
        res["per_team"].to_excel(xl, sheet_name="Per Team", index=False)
        if len(res["by_lga"]):
            res["by_lga"].to_excel(xl, sheet_name="By LGA", index=False)
        res["all_dates"].to_excel(xl, sheet_name="All Dates", index=False)
    print(f"\nSaved {os.path.abspath(a.output)}")
