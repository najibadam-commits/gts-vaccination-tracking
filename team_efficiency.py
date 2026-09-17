"""Team Time Efficiency — actual clock time against the 08:00-16:00 operational window.

Answers the operational question the ERM asks for accountability: *did this team's
GPS-recorded activity actually cover the operational day?* Every team is measured
against the same fixed window, 08:00-16:00 local time, regardless of when it happened
to start or stop tracking.

    Team Time Efficiency = Effective Operational Duration / 480 minutes

where Effective Operational Duration is the minutes of continuous tracking (gaps over
`MAX_GAP_MINUTES` excluded, same rule `team_daily_time.py` uses) that fall INSIDE the
window — time tracked before 08:00 or after 16:00 earns nothing, and a team that starts
at 08:00 and tracks solidly to 16:00 scores 100%.

Superseded design
------------------
This module used to cross-reference ping-count-derived field minutes against
grid-weighted settlement coverage (>2 hrs & <50% coverage = flagged). That analysis
answered a different question — was time in the field productive — and worked from the
settlement-level visitation CSV, which carries no clock times at all. It has been
replaced outright rather than kept alongside, per the decision to make this module's
"Team Time Efficiency" the clock-window metric everywhere it is reported.

Why raw track pings, not the visitation CSV
--------------------------------------------
Start/End time, and therefore everything downstream of it, only exists in the raw GPS
ping stream. The visitation CSV is a settlement-level aggregate with no timestamps.
`per_team_per_date` reads the merged track export directly, the same file
`team_daily_time.py` reads and with the same gap-detection rule (`MAX_GAP_MINUTES`,
imported rather than redefined so the two can never drift apart) — this module adds the
window clipping and the flag-reason logic on top.

Two duration figures, not one
------------------------------
`Total Recorded Duration` is the team's whole day, unwindowed — exactly
`team_daily_time._durations()`. `Effective Operational Duration` is the same
gap-summing, but each gap is first clipped to `[08:00, 16:00]` before being added, so
only in-window tracking counts. Both are reported: a team can rack up hours of
Total Recorded Duration entirely outside the window and still score 0% efficiency, and
the two numbers together are what make that visible rather than just asserted.

Flags are reasons, not a score
--------------------------------
A team is "Flagged" the moment ANY rule below fires, and every rule that fires is
listed — a team can be both a late start AND under the efficiency floor, and both
reasons are kept, joined with " + ", so a reader never has to guess which problem is
being reported. Two rules distinguish "no data" from "bad data": a team absent from the
track export entirely for the date is MISSING; a team present in the export whose
timestamps could not be parsed at all is INVALID — these are different problems for a
supervisor to chase and are named differently on purpose.

Thresholds
----------
`LATE_START_GRACE_MINUTES`, `EARLY_STOP_GRACE_MINUTES`, `MIN_EFFICIENCY_PCT` and
`EXCESSIVE_IDLE_MINUTES` have no prior value anywhere in this pipeline to inherit — they
are set here to reasonable defaults and exposed as `analyse()` keyword arguments so a
campaign can tune them without editing code. `MAX_GAP_MINUTES` (the tracking-gap rule)
is NOT reconfigurable here — it is imported from `team_daily_time`, which already
established it, so the two modules' notion of "was the tracker actually running" cannot
diverge.
"""
from __future__ import annotations

import argparse
import os

import pandas as pd

from team_daily_time import MAX_GAP_MINUTES, SINGLE_PING_MINUTES

# ---- the operational window -------------------------------------------------
WINDOW_START_HOUR = 8
WINDOW_END_HOUR = 16
WINDOW_MINUTES = (WINDOW_END_HOUR - WINDOW_START_HOUR) * 60   # 480
LOCAL_OFFSET = pd.Timedelta(hours=1)   # UTC+1, same convention as team_daily_time.py

# ---- thresholds (configurable — see module docstring) -----------------------
LATE_START_GRACE_MINUTES = 30
EARLY_STOP_GRACE_MINUTES = 30
MIN_EFFICIENCY_PCT = 0.50
EXCESSIVE_IDLE_MINUTES = 60

# Flag reasons — the exact wording shown in the flagged-team table, the chart and
# the workbook narration, so all three read the same vocabulary.
REASON_LATE_START = "Late start"
REASON_EARLY_STOP = "Early stop"
REASON_INSUFFICIENT_TIME = "Insufficient operational time"
REASON_EXCESSIVE_IDLE = "Excessive idle time"
REASON_MISSING_START = "Missing start time"
REASON_MISSING_END = "Missing end time"
REASON_INVALID_TIMESTAMP = "Invalid timestamp"

FLAGGED = "Flagged"
OK = "OK"

MAX_TEAM_CODES = 40


def _norm_place(series: pd.Series) -> pd.Series:
    """Normalise a place name for comparison — same rule used everywhere else."""
    return (series.astype(str).str.replace("_", " ", regex=False)
            .str.replace(r"\s+", " ", regex=True).str.strip().str.title())


def _join_codes(series: pd.Series) -> str:
    codes = series.astype(str).tolist()
    if len(codes) > MAX_TEAM_CODES:
        codes = codes[:MAX_TEAM_CODES] + [f"… +{len(series) - MAX_TEAM_CODES} more"]
    return ", ".join(codes)


def _team_lookup(dip: pd.DataFrame) -> dict:
    """team_code -> {State, LGA, Ward}, from the visitation CSV.

    Same construction as `stage3_erm_workbook.py`'s LGA backfill: dedupe on
    team_code (first-seen wins — a team is assumed to sit in one Ward for the
    campaign, the same latent assumption that backfill already makes for LGA) and
    map onto whatever the raw pings didn't already carry.
    """
    def pick(keyword, exclude=("code", "id")):
        for col in dip.columns:
            low = str(col).lower()
            if keyword in low and not any(x in low for x in exclude):
                return col
        return None

    team_col = next((c for c in dip.columns
                     if "team" in str(c).lower() and "code" in str(c).lower()), None)
    if not team_col:
        return {}
    state_col, lga_col, ward_col = pick("state"), pick("lga"), pick("ward")

    sub = dip.dropna(subset=[team_col]).drop_duplicates(subset=[team_col])
    codes = sub[team_col].astype(str).str.strip()
    lookup = {}
    for i, tc in zip(sub.index, codes):
        lookup[tc] = {
            "State": str(sub.at[i, state_col]).strip() if state_col else "",
            "LGA": (_norm_place(pd.Series([sub.at[i, lga_col]])).iloc[0]
                    if lga_col else ""),
            "Ward": str(sub.at[i, ward_col]).strip() if ward_col else "",
        }
    return lookup


def _windowed_durations(times: pd.Series, window_start: pd.Timestamp,
                        window_end: pd.Timestamp) -> tuple[float, float]:
    """(Total Recorded Duration, Effective Operational Duration) in minutes.

    Total is `team_daily_time._durations()`'s rule verbatim — gaps over
    `MAX_GAP_MINUTES` excluded, unwindowed. Effective is the same gap-summing with
    each gap first clipped to `[window_start, window_end]`, so tracking outside the
    window contributes nothing to it.
    """
    if len(times) <= 1:
        in_window = bool(len(times) and window_start <= times.iloc[0] <= window_end)
        credit = SINGLE_PING_MINUTES if len(times) else 0.0
        return credit, (credit if in_window else 0.0)

    gaps_min = times.diff().dropna().dt.total_seconds() / 60.0
    total = float(gaps_min[gaps_min <= MAX_GAP_MINUTES].sum())

    effective = 0.0
    prev = times.iloc[0]
    for i in range(1, len(times)):
        cur = times.iloc[i]
        gap_min = (cur - prev).total_seconds() / 60.0
        if gap_min <= MAX_GAP_MINUTES:
            lo, hi = max(prev, window_start), min(cur, window_end)
            if hi > lo:
                effective += (hi - lo).total_seconds() / 60.0
        prev = cur
    return total, effective


def _team_date_row(team_code: str, date: str, times: pd.Series, meta: dict,
                   thresholds: dict) -> dict:
    """One team's figures and flags for one tracking date, from its sorted pings."""
    window_start = pd.Timestamp(f"{date} 00:00:00") + pd.Timedelta(hours=WINDOW_START_HOUR)
    window_end = pd.Timestamp(f"{date} 00:00:00") + pd.Timedelta(hours=WINDOW_END_HOUR)

    times = times.sort_values()
    start = times.iloc[0] if len(times) else pd.NaT
    end = times.iloc[-1] if len(times) else pd.NaT

    total_min, effective_min = _windowed_durations(times, window_start, window_end)
    efficiency = (effective_min / WINDOW_MINUTES) if WINDOW_MINUTES else 0.0

    lo = max(start, window_start) if pd.notna(start) else pd.NaT
    hi = min(end, window_end) if pd.notna(end) else pd.NaT
    span_in_window = (max(0.0, (hi - lo).total_seconds() / 60.0)
                      if pd.notna(lo) and pd.notna(hi) and hi > lo else 0.0)
    idle_in_window = max(0.0, span_in_window - effective_min)

    reasons = []
    missing = pd.isna(start) or pd.isna(end)
    if missing:
        reasons.append(REASON_MISSING_START)
        reasons.append(REASON_MISSING_END)
    else:
        if start > window_start + pd.Timedelta(minutes=thresholds["late_start_grace"]):
            reasons.append(REASON_LATE_START)
        if end < window_end - pd.Timedelta(minutes=thresholds["early_stop_grace"]):
            reasons.append(REASON_EARLY_STOP)
        if efficiency < thresholds["min_efficiency"]:
            reasons.append(REASON_INSUFFICIENT_TIME)
        if idle_in_window > thresholds["excessive_idle"]:
            reasons.append(REASON_EXCESSIVE_IDLE)

    attrib = meta.get("lookup", {}).get(team_code, {})
    return {
        "State": meta.get("state_by_team", {}).get(team_code) or attrib.get("State", ""),
        "LGA": meta.get("lga_by_team", {}).get(team_code) or attrib.get("LGA", ""),
        "Ward": meta.get("ward_by_team", {}).get(team_code) or attrib.get("Ward", ""),
        "Team": team_code,
        "Team Date": date,
        "Start Time": start.strftime("%H:%M") if pd.notna(start) else "",
        "End Time": end.strftime("%H:%M") if pd.notna(end) else "",
        "Total Recorded Duration (min)": round(total_min, 1),
        "Effective Operational Duration (min)": round(effective_min, 1),
        "Team Time Efficiency (%)": round(efficiency * 100, 1),
        "Pings": int(len(times)),
        "Flag/Status": FLAGGED if reasons else OK,
        "Reason for Flagging": " + ".join(reasons),
    }


def per_team_per_date(tracks_file: str, visitation_csv: str | pd.DataFrame,
                      scope: dict | None = None, **thresholds) -> pd.DataFrame:
    """Team Time Efficiency for every team, on every tracking date in the export.

    `visitation_csv` supplies State/LGA/Ward for teams whose own pings don't carry
    them (via `_team_lookup`, the same backfill pattern
    `stage3_erm_workbook.py` uses for LGA) and the roster of teams expected to be
    reporting, so a team absent from the tracks ENTIRELY on the reporting date still
    gets a row — flagged MISSING rather than silently absent.

    `scope` restricts the tracks to one campaign's state/LGAs/teams — pass
    `team_daily_time.scope_from_visitation(visitation_csv)`; without it the figures
    span every state in a national export.
    """
    from team_daily_time import scope_from_visitation
    from track_columns import parse_timestamps, read_columns, require, resolve, valid_mask

    dip = (visitation_csv if isinstance(visitation_csv, pd.DataFrame)
          else pd.read_csv(visitation_csv, low_memory=False))
    scope = scope or scope_from_visitation(dip)
    lookup = _team_lookup(dip)
    want_state = scope.get("state")
    want_lgas = scope.get("lgas") or frozenset()
    want_teams = scope.get("teams") or frozenset()

    header = read_columns(tracks_file)
    cols = resolve(header)
    require(cols, ("team", "ts"), header, "Team Time Efficiency")
    ts_col, team_col = cols["ts"], cols["team"]
    lga_col, ward_col, state_col = cols.get("lga"), cols.get("ward"), cols.get("state")
    valid_col = cols["valid"]

    keep = {c for c in (ts_col, team_col, lga_col, ward_col, state_col, valid_col) if c}

    parts = []
    raw_teams_seen: set[str] = set()
    parsed_teams_seen: set[str] = set()
    for chunk in pd.read_csv(tracks_file, usecols=lambda c: c in keep,
                             chunksize=500_000, dtype=str):
        chunk = chunk.dropna(subset=[team_col])
        chunk[team_col] = chunk[team_col].astype(str).str.strip()

        if valid_col and valid_col in chunk.columns:
            chunk = chunk[valid_mask(chunk[valid_col])]

        if want_state and state_col and state_col in chunk.columns:
            chunk = chunk[_norm_place(chunk[state_col]) == want_state]
        elif want_lgas and lga_col and lga_col in chunk.columns:
            chunk = chunk[_norm_place(chunk[lga_col]).isin(want_lgas)]
        elif want_teams:
            chunk = chunk[chunk[team_col].isin(want_teams)]
        if not len(chunk):
            continue

        raw_teams_seen |= set(chunk[team_col].unique())
        ts = parse_timestamps(chunk[ts_col])
        local = ts + LOCAL_OFFSET
        ok = local.notna()
        parsed_teams_seen |= set(chunk.loc[ok, team_col].unique())

        part = pd.DataFrame({
            "team_code": chunk[team_col],
            "state": (chunk[state_col].astype(str).str.strip()
                     if state_col and state_col in chunk.columns else ""),
            "lga": (chunk[lga_col].astype(str).str.strip()
                   if lga_col and lga_col in chunk.columns else ""),
            "ward": (chunk[ward_col].astype(str).str.strip()
                    if ward_col and ward_col in chunk.columns else ""),
            "_ts": local,
        })
        parts.append(part[ok])

    allp = (pd.concat(parts, ignore_index=True) if parts else
           pd.DataFrame(columns=["team_code", "state", "lga", "ward", "_ts"]))
    allp["date"] = allp["_ts"].dt.strftime("%Y-%m-%d") if len(allp) else ""

    # First-seen non-blank state/lga/ward per team, straight off its own pings —
    # used ahead of the visitation-CSV lookup in `_team_date_row`.
    def _first_nonblank(s: pd.Series) -> dict:
        s = s[s.astype(str).str.strip() != ""]
        return s.groupby(level=0).first().to_dict() if len(s) else {}

    by_team = allp.set_index("team_code") if len(allp) else allp
    state_by_team = _first_nonblank(by_team["state"]) if len(allp) else {}
    lga_by_team = _first_nonblank(by_team["lga"].map(
        lambda v: _norm_place(pd.Series([v])).iloc[0])) if len(allp) else {}
    ward_by_team = _first_nonblank(by_team["ward"]) if len(allp) else {}
    meta = {"lookup": lookup, "state_by_team": state_by_team,
           "lga_by_team": lga_by_team, "ward_by_team": ward_by_team}

    th = {
        "late_start_grace": thresholds.get("late_start_grace", LATE_START_GRACE_MINUTES),
        "early_stop_grace": thresholds.get("early_stop_grace", EARLY_STOP_GRACE_MINUTES),
        "min_efficiency": thresholds.get("min_efficiency", MIN_EFFICIENCY_PCT),
        "excessive_idle": thresholds.get("excessive_idle", EXCESSIVE_IDLE_MINUTES),
    }

    rows = []
    if len(allp):
        for (team_code, date), grp in allp.groupby(["team_code", "date"]):
            rows.append(_team_date_row(team_code, date, grp["_ts"], meta, th))

    # A team present in the export SOMEWHERE but whose timestamps never parsed is
    # INVALID — a data-quality problem, not an absence, so it is kept file-level
    # rather than tied to one date (a team's bad-timestamp pings aren't attributable
    # to a particular date if the timestamp itself couldn't be read). Teams that are
    # simply MISSING on the reporting date are resolved by `analyse()` instead, which
    # knows which date is being reported and can tell "absent today" from "absent
    # from the whole export" — this function alone cannot, since it accumulates
    # `raw_teams_seen` across every date in the file.
    invalid = sorted(raw_teams_seen - parsed_teams_seen)

    out = pd.DataFrame(rows)
    out.attrs["expected_teams"] = sorted(want_teams or set(lookup.keys()))
    out.attrs["invalid_teams"] = invalid
    out.attrs["meta"] = meta
    out.attrs["thresholds"] = th
    return out


def _placeholder_row(team_code: str, date: str, meta: dict, reason: str) -> dict:
    attrib = meta.get("lookup", {}).get(team_code, {})
    return {
        "State": meta.get("state_by_team", {}).get(team_code) or attrib.get("State", ""),
        "LGA": meta.get("lga_by_team", {}).get(team_code) or attrib.get("LGA", ""),
        "Ward": meta.get("ward_by_team", {}).get(team_code) or attrib.get("Ward", ""),
        "Team": team_code, "Team Date": date, "Start Time": "", "End Time": "",
        "Total Recorded Duration (min)": 0.0, "Effective Operational Duration (min)": 0.0,
        "Team Time Efficiency (%)": 0.0, "Pings": 0,
        "Flag/Status": FLAGGED, "Reason for Flagging": reason,
    }


def analyse(tracks_file: str, visitation_csv: str | pd.DataFrame,
           report_date: str | None = "latest", scope: dict | None = None,
           **thresholds) -> dict:
    """Team Time Efficiency for one reporting date (default: the latest in the export).

    `report_date=None` returns every date's rows without selecting one — used for
    `analysis_type="post_campaign"`, matching the branch `time_spent_analysis()`
    already uses one function up in `stage3_erm_workbook.py`.

    Returns:
        per_team       one row per team for the reporting date (incl. MISSING/
                       INVALID placeholder rows for teams absent from the export)
        flagged_table  every flagged team, worst efficiency first
        summary        counts by Flag/Status and by each reason
        all_dates      every team-date row actually seen in the export
        meta           thresholds used, totals, flagged codes
    """
    all_rows = per_team_per_date(tracks_file, visitation_csv, scope=scope, **thresholds)
    meta_attrs = all_rows.attrs.get("meta", {})
    th = all_rows.attrs.get("thresholds", {})
    expected = set(all_rows.attrs.get("expected_teams", []))
    invalid = set(all_rows.attrs.get("invalid_teams", []))

    chosen = report_date
    if chosen == "latest":
        chosen = (str(all_rows["Team Date"].max())
                 if len(all_rows) and "Team Date" in all_rows.columns else None)
        if not chosen or chosen == "nan":
            chosen = None

    per_team = (all_rows[all_rows["Team Date"] == chosen] if chosen and len(all_rows)
               else all_rows).reset_index(drop=True)

    if chosen:
        # MISSING is resolved here, not in `per_team_per_date`, because only this
        # function knows which single date is being reported: a team seen on Day 1
        # but not on today's `chosen` date must still be flagged missing for today,
        # which a file-wide "ever seen" check cannot tell apart from being present.
        present_today = set(per_team["Team"]) if len(per_team) else set()
        absent = sorted(expected - present_today - invalid)
        invalid_today = sorted(invalid - present_today)
        placeholders = [_placeholder_row(tc, chosen, meta_attrs, REASON_MISSING_START
                                         + " + " + REASON_MISSING_END) for tc in absent]
        placeholders += [_placeholder_row(tc, chosen, meta_attrs, REASON_INVALID_TIMESTAMP)
                         for tc in invalid_today]
        if placeholders:
            per_team = pd.concat([per_team, pd.DataFrame(placeholders)],
                                 ignore_index=True)

    flagged_table = (per_team[per_team["Flag/Status"] == FLAGGED]
                     .sort_values(["Team Time Efficiency (%)"])
                     .reset_index(drop=True))

    n = len(per_team)
    reason_counts = {}
    if n:
        for reasons in per_team["Reason for Flagging"]:
            for r in [x for x in str(reasons).split(" + ") if x]:
                reason_counts[r] = reason_counts.get(r, 0) + 1
    summary = pd.DataFrame({
        "Status": [OK, FLAGGED],
        "Teams": [int((per_team["Flag/Status"] == OK).sum()) if n else 0,
                 int(len(flagged_table))],
    })
    summary["Share"] = (summary["Teams"] / n) if n else 0.0
    reason_summary = pd.DataFrame(
        {"Reason": list(reason_counts.keys()), "Teams": list(reason_counts.values())}
    ).sort_values("Teams", ascending=False).reset_index(drop=True)

    return {
        "per_team": per_team,
        "flagged_table": flagged_table,
        "summary": summary,
        "reason_summary": reason_summary,
        "all_dates": all_rows,
        "meta": {
            "window": f"{WINDOW_START_HOUR:02d}:00-{WINDOW_END_HOUR:02d}:00",
            "window_minutes": WINDOW_MINUTES,
            "date": chosen,
            "total_teams": n,
            "flagged_teams": int(len(flagged_table)),
            "flagged_pct": (len(flagged_table) / n) if n else 0.0,
            "flagged_codes": _join_codes(flagged_table["Team"]) if len(flagged_table) else "",
            "avg_efficiency_pct": (float(per_team["Team Time Efficiency (%)"].mean())
                                   if n else 0.0),
            "reason_counts": reason_counts,
            "thresholds": th,
        },
    }


def describe(result: dict) -> str:
    m = result["meta"]
    return (f"{m['total_teams']:,} teams · {m['flagged_teams']:,} flagged "
            f"({m['flagged_pct']:.1%}) against the {m['window']} window · "
            f"avg efficiency {m['avg_efficiency_pct']:.1f}%")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Team Time Efficiency — clock time vs. the 08:00-16:00 window")
    ap.add_argument("--tracks", required=True, help="merged_tracks.csv")
    ap.add_argument("--visitation-csv", required=True,
                    help="{State}_day_{N}_settlement_visitation.csv from stage 2")
    ap.add_argument("--date", default="latest",
                    help='"latest" (default), a YYYY-MM-DD date, or "all"')
    ap.add_argument("--output", default="Team_Time_Efficiency.xlsx")
    a = ap.parse_args()

    from team_daily_time import scope_from_visitation
    dip = pd.read_csv(a.visitation_csv, low_memory=False)
    res = analyse(a.tracks, dip, None if a.date == "all" else a.date,
                 scope=scope_from_visitation(dip))
    print(describe(res))
    print()
    print(res["summary"].to_string(index=False))
    if len(res["flagged_table"]):
        print(f"\nFlagged teams ({len(res['flagged_table'])}):")
        print(res["flagged_table"].head(25).to_string(index=False))
    with pd.ExcelWriter(a.output, engine="xlsxwriter") as xl:
        res["summary"].to_excel(xl, sheet_name="Summary", index=False)
        res["reason_summary"].to_excel(xl, sheet_name="Reasons", index=False)
        res["flagged_table"].to_excel(xl, sheet_name="Flagged Teams", index=False)
        res["per_team"].to_excel(xl, sheet_name="All Teams", index=False)
    print(f"\nSaved {os.path.abspath(a.output)}")
