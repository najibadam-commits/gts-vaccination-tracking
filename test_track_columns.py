"""Assert track exports are read by column MEANING, not by exact name.

Reproduces the first Streamlit Cloud failure: an export whose LGA column is not
spelled `NGA LGA 2024 Label` used to make `usecols` match nothing, and the run
died later with `KeyError: 'lga'` from a groupby — naming a column the analyst
never typed. The label carries a boundary year, so it was always going to drift.

    python test_track_columns.py
"""
import os
import sys
import tempfile

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stage3_erm_workbook import load_tracks_teams  # noqa: E402
from team_daily_time import analyse  # noqa: E402
from track_columns import parse_timestamps, resolve  # noqa: E402

# The same three pings, written under five different header conventions.
LAYOUTS = {
    "pipeline default": dict(lat="Lat", lon="Lon", lga="NGA LGA 2024 Label",
                             team="Team Code", ts="GPS Timestamp (UTC)"),
    "different year":   dict(lat="Lat", lon="Lon", lga="NGA LGA 2022 Label",
                             team="Team Code", ts="GPS Timestamp (UTC)"),
    "plain names":      dict(lat="Latitude", lon="Longitude", lga="LGA",
                             team="Team", ts="Timestamp"),
    "snake case":       dict(lat="lat", lon="lon", lga="lga_name",
                             team="team_code", ts="date_time"),
    "no LGA column":    dict(lat="Lat", lon="Lon", lga=None,
                             team="Team Code", ts="GPS Timestamp (UTC)"),
}

N_TEAMS, PINGS = 4, 30


def write(path, names, ts_fmt="%m/%d/%Y %H:%M:%S"):
    rows = []
    for t in range(N_TEAMS):
        for k in range(PINGS):
            stamp = pd.Timestamp("2026-05-02 08:00:00") + pd.Timedelta(minutes=2 * k)
            row = {names["lat"]: 9.0, names["lon"]: 8.0,
                   names["team"]: f"TM{t:03d}",
                   names["ts"]: stamp.strftime(ts_fmt)}
            if names["lga"]:
                row[names["lga"]] = "Lafia"
            rows.append(row)
    pd.DataFrame(rows).to_csv(path, index=False)


ok = True


def check(label, got, want):
    global ok
    good = got == want
    ok = ok and good
    print(f"  [{'PASS' if good else 'FAIL'}] {label}: got {got}, want {want}")


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="trackcols_")

    print("every layout is read correctly:")
    for label, names in LAYOUTS.items():
        path = os.path.join(tmp, f"{label.replace(' ', '_')}.csv")
        write(path, names)
        agg = load_tracks_teams(path)
        teams = int(agg["team_code"].nunique())
        pings = int(agg["pings"].sum())
        res = analyse(path, "latest")
        good = (teams == N_TEAMS and pings == N_TEAMS * PINGS
                and res["meta"]["total_teams"] == N_TEAMS
                and res["meta"]["date"] == "2026-05-02")
        global ok
        ok = ok and good
        print(f"  [{'PASS' if good else 'FAIL'}] {label:18} "
              f"{teams} teams, {pings} pings, date {res['meta']['date']}")

    print("\na missing team column fails with a message naming the columns:")
    bad = os.path.join(tmp, "no_team.csv")
    pd.DataFrame({"Lat": [9.0], "Lon": [8.0], "Reading Time": ["x"]}).to_csv(bad, index=False)
    try:
        load_tracks_teams(bad)
        check("raises", False, True)
    except ValueError as exc:
        msg = str(exc)
        check("names the missing role", "team code" in msg, True)
        check("lists the file's actual columns", "Reading Time" in msg, True)
        check("is not a bare KeyError", "KeyError" not in type(exc).__name__, True)

    print("\ntimestamp layouts are detected, not assumed:")
    for fmt, label in (("%m/%d/%Y %H:%M:%S", "US m/d/Y"),
                       ("%Y-%m-%d %H:%M:%S", "ISO"),
                       ("%Y-%m-%dT%H:%M:%S", "ISO with T")):
        s = pd.Series([pd.Timestamp("2026-05-02 08:30:00").strftime(fmt)] * 10)
        parsed = parse_timestamps(s)
        check(f"{label:12} parses", bool(parsed.notna().all()), True)

    print("\nLGA Code is not mistaken for the LGA name:")
    r = resolve(["Lat", "Lon", "LGA Code", "NGA LGA 2024 Label", "Team Code",
                 "GPS Timestamp (UTC)"])
    check("picks the name, not the code", r["lga"], "NGA LGA 2024 Label")

    print("\n" + ("ALL PASSED" if ok else "FAILURES ABOVE"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
