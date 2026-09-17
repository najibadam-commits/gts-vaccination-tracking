"""Tracking sessions per team and per device, from the raw track timestamps.

One question: **in how many separate sittings did each team transmit on the
reporting date, and on how many devices?**

Why sessions and not ping counts
--------------------------------
"Number of tracks" is ambiguous in a GTS export, because `Track ID` is issued
PER PING, not per track — a million-row export carries very nearly a million
distinct Track IDs. Counting them measures data volume, which is a function of
how long the device was on and how often it reported, and says nothing an
analyst can act on.

A session is the useful unit: a run of consecutive pings with no gap long
enough to mean the device stopped. One session is a team that switched the
tracker on, worked, and switched it off. Six sessions on the same date is a
device that was repeatedly killed and restarted, or that kept losing its fix —
either way the day's track is fragmentary and the coverage derived from it
understates the work.

The gap rule is NOT a new threshold
-----------------------------------
`team_daily_time` already defines "not tracking" as a gap longer than
`MAX_GAP_MINUTES`, and excludes such gaps from time spent. This module imports
that same constant and uses it as the session boundary, which makes the two
analyses two views of one segmentation:

    minutes tracked  =  the total length of a team's sessions
    sessions         =  how many pieces that total arrived in

They cannot contradict each other, and there is one number to revise if the
device's reporting interval ever changes.

Checked against a real April 2026 Zamfara export, valid fixes only: the gap
between consecutive pings runs 2.0 minutes at the median and 5.6 at the 95th
percentile, so the 15-minute boundary sits well clear of normal reporting and
will not cut a working team's track in half. The 99th percentile is 17.8
minutes — that top 1% of gaps is precisely the switched-off / lost-signal tail
this is meant to catch.

Invalid fixes are excluded
--------------------------
Rows the export flags `Valid = 0` are dropped before sessions are cut, so a
burst of rejected fixes cannot manufacture a session or bridge a real break.
Roughly 21% of rows in a recent export carried the flag. Note that the flag
arrives as a STRING in some chunk files and an integer in others — see
`track_columns.valid_mask`, which is why this module does not compare it to 0
directly.

Devices
-------
Sessions are cut per team and, separately, per device, and the two are crossed
to surface the pairings that should not exist: one team transmitting from
several devices on one day, and — the more serious of the two — one device
transmitting under several team codes, which means the tracker was passed
around and neither team's track is trustworthy.

Local time is UTC+1, applied before the date is taken, so a late-evening ping
belongs to the correct local date.
"""
from __future__ import annotations

import argparse
import os

import pandas as pd

from team_daily_time import (LOCAL_OFFSET, MAX_GAP_MINUTES, _norm_place,
                             scope_from_visitation)

# The session boundary IS the "not tracking" gap — see the module docstring.
SESSION_GAP_MINUTES = MAX_GAP_MINUTES

# A team whose day arrived in this many pieces or more is called fragmented.
#
# Set from the same export the gap rule was checked on (1,063 Zamfara teams,
# valid fixes only): median 2 sessions, 75th percentile 3, 90th percentile 4.
# So 4 flags the top ~14% — a follow-up list of roughly 150 teams, which a
# supervisor can act on. Dropping it to 3 flags 28% and the list stops being a
# list; raising it to 5 flags 7% and misses the middle of the tail.
FRAGMENTED_SESSIONS = 4


def _sessions(times: pd.Series) -> int:
    """How many sessions one team's (or device's) pings on one date fall into.

    A session starts at the first ping and at every ping that follows a gap
    longer than the boundary, so a single ping is one session, not zero — it is
    evidence of a transmission that began, which is what a session counts.
    """
    ordered = times.sort_values()
    if len(ordered) <= 1:
        return int(len(ordered))
    gaps = ordered.diff().dropna().dt.total_seconds() / 60.0
    return int(1 + (gaps > SESSION_GAP_MINUTES).sum())


def _read_pings(tracks_file: str, chunksize: int, scope: dict) -> pd.DataFrame:
    """Every usable ping, as date / team / device / lga / timestamp.

    Scoped to one state by the same three routes `team_daily_time` uses, in the
    same order, so both modules always describe the same population.
    """
    from track_columns import (parse_timestamps, read_columns, require,
                               resolve as resolve_cols, valid_mask)

    header = read_columns(tracks_file)
    cols = resolve_cols(header)
    require(cols, ("team", "ts"), header, "Tracking sessions")
    ts_col, team_col = cols["ts"], cols["team"]
    lga_col, dev_col, valid_col = cols["lga"], cols["device"], cols["valid"]

    want_state = scope.get("state")
    want_lgas = scope.get("lgas") or frozenset()
    want_teams = scope.get("teams") or frozenset()
    state_col = cols["state"] if want_state else None

    if not dev_col:
        print("  no device/IMEI column in the export — per-device session "
              "figures will be omitted from the slide")
    if not valid_col:
        print("  no Valid column in the export — every ping is counted")

    keep = {c for c in (ts_col, team_col, lga_col, dev_col, valid_col, state_col) if c}

    parts = []
    seen = kept = dropped_invalid = 0
    for chunk in pd.read_csv(tracks_file, usecols=lambda c: c in keep,
                             chunksize=chunksize, dtype=str):
        chunk = chunk.dropna(subset=[team_col])
        seen += len(chunk)

        if valid_col and valid_col in chunk.columns:
            ok = valid_mask(chunk[valid_col])
            dropped_invalid += int((~ok).sum())
            chunk = chunk[ok]

        if state_col and state_col in chunk.columns:
            chunk = chunk[_norm_place(chunk[state_col]) == want_state]
        elif want_lgas and lga_col and lga_col in chunk.columns:
            chunk = chunk[_norm_place(chunk[lga_col]).isin(want_lgas)]
        elif want_teams:
            chunk = chunk[chunk[team_col].astype(str).str.strip().isin(want_teams)]
        kept += len(chunk)
        if not len(chunk):
            continue

        local = parse_timestamps(chunk[ts_col]) + LOCAL_OFFSET
        parts.append(pd.DataFrame({
            "date": local.dt.strftime("%Y-%m-%d"),
            "team_code": chunk[team_col].astype(str).str.strip(),
            "device": (chunk[dev_col].astype(str).str.strip()
                       if dev_col and dev_col in chunk.columns else ""),
            "lga": (chunk[lga_col].astype(str).str.strip()
                    if lga_col and lga_col in chunk.columns else ""),
            "_ts": local,
        }).dropna(subset=["_ts"]))

    if dropped_invalid:
        print(f"  {dropped_invalid:,} of {seen:,} pings dropped as Valid = 0 "
              f"before sessions were cut")
    print(f"  {kept:,} of {seen:,} pings are in scope for session analysis")

    if not parts:
        return pd.DataFrame(columns=["date", "team_code", "device", "lga", "_ts"])

    allp = pd.concat(parts, ignore_index=True)
    allp = allp[allp["team_code"] != ""]

    lga_by_team = scope.get("lga_by_team") or {}
    if lga_by_team and not allp["lga"].astype(str).str.strip().any():
        allp["lga"] = allp["team_code"].map(lga_by_team).fillna("")
    return allp


def per_team_per_date(tracks_file: str, chunksize: int = 500_000,
                      scope: dict | None = None) -> pd.DataFrame:
    """Sessions, devices and pings for every team on every date in the export.

    Columns: date, team_code, lga, Pings, Sessions, Devices, Fragmented.
    """
    allp = _read_pings(tracks_file, chunksize, scope or {})
    if not len(allp):
        return pd.DataFrame(columns=["date", "team_code", "lga", "Pings",
                                     "Sessions", "Devices", "Fragmented"])

    grouped = allp.groupby(["date", "team_code"], dropna=False)
    out = grouped.agg(Pings=("_ts", "size"),
                      lga=("lga", "first"),
                      Devices=("device", lambda s: int(s[s != ""].nunique()))
                      ).reset_index()
    out["Sessions"] = grouped["_ts"].apply(_sessions).values
    out["Fragmented"] = out["Sessions"] >= FRAGMENTED_SESSIONS
    return out[["date", "team_code", "lga", "Pings", "Sessions", "Devices",
                "Fragmented"]]


def per_device_per_date(tracks_file: str, chunksize: int = 500_000,
                        scope: dict | None = None,
                        pings: pd.DataFrame | None = None) -> pd.DataFrame:
    """Sessions and team codes for every device on every date.

    `pings` lets a caller that has already read the file reuse it rather than
    scanning a multi-million-row export twice.

    Columns: date, device, Pings, Sessions, Teams.
    """
    allp = _read_pings(tracks_file, chunksize, scope or {}) if pings is None else pings
    allp = allp[allp["device"].astype(str).str.strip() != ""] if len(allp) else allp
    if not len(allp):
        return pd.DataFrame(columns=["date", "device", "Pings", "Sessions", "Teams"])

    grouped = allp.groupby(["date", "device"], dropna=False)
    out = grouped.agg(Pings=("_ts", "size"),
                      Teams=("team_code", "nunique")).reset_index()
    out["Sessions"] = grouped["_ts"].apply(_sessions).values
    return out[["date", "device", "Pings", "Sessions", "Teams"]]


def analyse(tracks_file: str, report_date: str | None = "latest",
            scope: dict | None = None, chunksize: int = 500_000) -> dict:
    """Session analysis for one tracking date, in one state.

    `report_date` is "latest" (the default), an explicit "YYYY-MM-DD", or None
    for every date. `scope` should be `scope_from_visitation(visitation_csv)`;
    without it the figures cover every state in the export, which for a national
    GTS export is never what the campaign wants.

    Returns:
        per_team     one row per team for the selected date
        per_device   one row per device for the selected date
        by_lga       LGA / Teams / Median Sessions / Fragmented Teams
        all_dates    every team-date row
        meta         the date used, medians, fragmentation and sharing counts
    """
    scope = scope or {}
    pings = _read_pings(tracks_file, chunksize, scope)

    # The export is read ONCE and both views are built from that frame. Reading
    # it per view would double the cost of the most expensive input in the
    # pipeline, on a file that is routinely several million rows.
    team_rows = _summarise_teams(pings) if len(pings) else pd.DataFrame(
        columns=["date", "team_code", "lga", "IMEI", "Pings", "Sessions",
                 "Devices", "Fragmented"])
    device_rows = per_device_per_date(tracks_file, chunksize, scope, pings=pings)

    chosen = (str(team_rows["date"].max()) if report_date == "latest" and len(team_rows)
              else report_date)
    per_team = (team_rows[team_rows["date"] == chosen] if chosen else team_rows
                ).reset_index(drop=True)
    per_device = (device_rows[device_rows["date"] == chosen] if chosen else device_rows
                  ).reset_index(drop=True)

    by_lga = pd.DataFrame()
    if len(per_team):
        by_lga = (per_team.groupby("lga")
                  .agg(Teams=("team_code", "nunique"),
                       **{"Median Sessions": ("Sessions", "median"),
                          "Fragmented Teams": ("Fragmented", "sum")})
                  .reset_index().rename(columns={"lga": "LGA"}))
        by_lga["Median Sessions"] = by_lga["Median Sessions"].round(0).astype(int)
        by_lga["Fragmented Teams"] = by_lga["Fragmented Teams"].astype(int)
        by_lga = by_lga.sort_values(["Fragmented Teams", "Teams"],
                                    ascending=False).reset_index(drop=True)

    n_teams = int(per_team["team_code"].nunique()) if len(per_team) else 0
    n_frag = int(per_team["Fragmented"].sum()) if len(per_team) else 0
    multi_device = int((per_team["Devices"] > 1).sum()) if len(per_team) else 0
    shared_devices = int((per_device["Teams"] > 1).sum()) if len(per_device) else 0

    meta = {
        "date": chosen,
        "total_teams": n_teams,
        "total_devices": int(per_device["device"].nunique()) if len(per_device) else 0,
        "median_sessions": float(per_team["Sessions"].median()) if len(per_team) else 0.0,
        "max_sessions": int(per_team["Sessions"].max()) if len(per_team) else 0,
        "single_session_teams": int((per_team["Sessions"] == 1).sum()) if len(per_team) else 0,
        "fragmented_teams": n_frag,
        "fragmented_pct": (n_frag / n_teams) if n_teams else 0.0,
        "fragmented_threshold": FRAGMENTED_SESSIONS,
        "multi_device_teams": multi_device,
        "shared_devices": shared_devices,
        "session_gap_minutes": SESSION_GAP_MINUTES,
        "state": scope.get("state"),
        "has_devices": bool(len(per_device)),
    }
    meta["single_session_pct"] = (meta["single_session_teams"] / n_teams) if n_teams else 0.0

    print(f"  sessions: {n_teams:,} teams on {chosen} — median "
          f"{meta['median_sessions']:.0f} session(s), {n_frag:,} fragmented "
          f"(>= {FRAGMENTED_SESSIONS}), {shared_devices:,} shared device(s)")

    return {"per_team": per_team, "per_device": per_device, "by_lga": by_lga,
            "all_dates": team_rows, "meta": meta,
            "team_table": team_table(per_team),
            "device_table": device_table(per_device, per_team)}


# --------------------------------------------------------- report tables
# Both tables are ranked SHARED DEVICES FIRST. A slide or report page shows the
# top of a list and nothing else, so the ranking decides what gets seen — and a
# device transmitting under two team codes invalidates both teams' tracks,
# which outranks a team that merely had a choppy day.
def team_table(per_team: pd.DataFrame) -> pd.DataFrame:
    """Team Code / IMEI / Tracks / Pings, worst first.

    "Tracks" is the session count — see the module docstring for why the
    export's own `Track ID` is not what anyone means by a track.
    """
    if not len(per_team):
        return pd.DataFrame(columns=["Team Code", "IMEI", "Tracks", "Pings"])
    out = per_team.copy()
    out["_multi"] = out["Devices"] > 1
    out = out.sort_values(["_multi", "Sessions", "Pings"],
                          ascending=[False, False, False])
    return (out.rename(columns={"team_code": "Team Code", "Sessions": "Tracks"})
            [["Team Code", "IMEI", "Tracks", "Pings"]].reset_index(drop=True))


def device_table(per_device: pd.DataFrame,
                 per_team: pd.DataFrame | None = None) -> pd.DataFrame:
    """IMEI / Teams / Tracks / Pings, shared devices first."""
    if not len(per_device):
        return pd.DataFrame(columns=["IMEI", "Teams", "Tracks", "Pings"])
    out = per_device.sort_values(["Teams", "Sessions", "Pings"],
                                 ascending=[False, False, False])
    return (out.rename(columns={"device": "IMEI", "Sessions": "Tracks"})
            [["IMEI", "Teams", "Tracks", "Pings"]].reset_index(drop=True))


def _device_list(s: pd.Series) -> str:
    """The IMEIs a team transmitted from, as one cell.

    Listed rather than counted because the report has to name the device: a
    count tells a supervisor that something is wrong, the IMEI tells them which
    handset to go and look at. Sorted so the same team reads the same way on
    two consecutive days.
    """
    return ", ".join(sorted(set(s[s != ""].astype(str))))


def _summarise_teams(pings: pd.DataFrame) -> pd.DataFrame:
    """Per-team rows from an already-read ping frame — see `per_team_per_date`."""
    grouped = pings.groupby(["date", "team_code"], dropna=False)
    out = grouped.agg(Pings=("_ts", "size"),
                      lga=("lga", "first"),
                      Devices=("device", lambda s: int(s[s != ""].nunique())),
                      IMEI=("device", _device_list),
                      ).reset_index()
    out["Sessions"] = grouped["_ts"].apply(_sessions).values
    out["Fragmented"] = out["Sessions"] >= FRAGMENTED_SESSIONS
    return out[["date", "team_code", "lga", "IMEI", "Pings", "Sessions",
                "Devices", "Fragmented"]]


def describe(result: dict) -> str:
    m = result["meta"]
    parts = [f"{m['total_teams']:,} teams on {m['date']}",
             f"median {m['median_sessions']:.0f} session(s)",
             f"{m['fragmented_teams']:,} fragmented "
             f"({m['fragmented_pct']:.1%}, >= {m['fragmented_threshold']})"]
    if m["has_devices"]:
        parts.append(f"{m['total_devices']:,} devices")
        parts.append(f"{m['shared_devices']:,} used by more than one team")
    parts.append(f"gap boundary {m['session_gap_minutes']:g} min")
    return " · ".join(parts)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Tracking sessions per team and per device, from track timestamps")
    ap.add_argument("--tracks", required=True, help="merged_tracks.csv")
    ap.add_argument("--date", default="latest",
                    help='"latest" (default), a YYYY-MM-DD date, or "all"')
    ap.add_argument("--visitation-csv", default=None,
                    help="the day's visitation CSV — restricts the analysis to "
                         "that campaign's state (strongly recommended, since "
                         "GTS track exports are national)")
    ap.add_argument("--output", default="Tracking_Sessions.xlsx")
    a = ap.parse_args()

    scope = scope_from_visitation(a.visitation_csv) if a.visitation_csv else None
    res = analyse(a.tracks, None if a.date == "all" else a.date, scope=scope)
    print(describe(res))
    print()
    if len(res["by_lga"]):
        print(res["by_lga"].to_string(index=False))
    with pd.ExcelWriter(a.output, engine="xlsxwriter") as xl:
        res["per_team"].to_excel(xl, sheet_name="Per Team", index=False)
        if len(res["per_device"]):
            res["per_device"].to_excel(xl, sheet_name="Per Device", index=False)
        if len(res["by_lga"]):
            res["by_lga"].to_excel(xl, sheet_name="By LGA", index=False)
        res["all_dates"].to_excel(xl, sheet_name="All Dates", index=False)
    print(f"\nSaved {os.path.abspath(a.output)}")
