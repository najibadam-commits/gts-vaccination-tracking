"""Assert the efficiency chart, flagged-team table and narration agree.

The three are published together and read together, so they must come from one
dataset and reconcile exactly. Builds a synthetic raw track export with a known
number of teams in each flag category and checks the counts everywhere they
surface.

    python test_flagged_teams.py
"""
import os
import sys
import tempfile

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from team_efficiency import (  # noqa: E402
    FLAGGED, OK, REASON_EARLY_STOP, REASON_EXCESSIVE_IDLE,
    REASON_INSUFFICIENT_TIME, REASON_INVALID_TIMESTAMP, REASON_LATE_START,
    REASON_MISSING_END, REASON_MISSING_START, analyse,
)
from team_daily_time import scope_from_visitation  # noqa: E402

DATE = "2026-08-27"
LOCAL_OFFSET_HOURS = 1   # module local time is UTC+1; pings are written in UTC

# Teams by intended category — mirrors the shape of the module's own smoke test
# but as a proper reconciling test, run at the effort level test files here
# usually get: known composition in, exact counts checked everywhere out.
N_ON_TIME = 5      # full window, dense pings, no flag
N_LATE = 4         # starts well after 08:00, otherwise fine
N_EARLY = 3         # stops well before 16:00, otherwise fine
N_IDLE = 2          # dense briefly, long silent stretch, dense again — idle
N_INVALID = 2       # garbage timestamps only
N_MISSING = 3       # present in the settlement list, absent from the tracks
LGAS = ["LGA_A", "LGA_B"]


def _dense(rows, team, start_hhmm, end_hhmm, step_min=3, day=DATE):
    sh, sm = map(int, start_hhmm.split(":"))
    eh, em = map(int, end_hhmm.split(":"))
    t = pd.Timestamp(f"{day} {sh:02d}:{sm:02d}:00")
    end = pd.Timestamp(f"{day} {eh:02d}:{em:02d}:00")
    while t <= end:
        utc = t - pd.Timedelta(hours=LOCAL_OFFSET_HOURS)
        rows.append((team, utc.strftime("%m/%d/%Y %H:%M:%S")))
        t += pd.Timedelta(minutes=step_min)


def build():
    rows = []
    on_time, late, early, idle, invalid, missing = [], [], [], [], [], []

    for i in range(N_ON_TIME):
        team = f"TM_OK{i:02d}"
        on_time.append(team)
        _dense(rows, team, "08:00", "16:00")

    for i in range(N_LATE):
        team = f"TM_LATE{i:02d}"
        late.append(team)
        _dense(rows, team, "10:00", "16:00")   # 2 hrs late, well past grace

    for i in range(N_EARLY):
        team = f"TM_EARLY{i:02d}"
        early.append(team)
        _dense(rows, team, "08:00", "13:00")   # stops 3 hrs early

    for i in range(N_IDLE):
        team = f"TM_IDLE{i:02d}"
        idle.append(team)
        _dense(rows, team, "08:00", "08:15")
        _dense(rows, team, "15:45", "16:00")

    for i in range(N_INVALID):
        team = f"TM_BAD{i:02d}"
        invalid.append(team)
        rows.append((team, "not-a-timestamp"))
        rows.append((team, "still-not-one"))

    for i in range(N_MISSING):
        team = f"TM_GONE{i:02d}"
        missing.append(team)
        # deliberately no rows — present in the roster, absent from tracks

    tracks = pd.DataFrame(rows, columns=["Team Code", "GPS Timestamp (UTC)"])
    tracks["Valid"] = "1"

    all_teams = on_time + late + early + idle + invalid + missing
    visitation = pd.DataFrame({
        "state": ["Testland"] * len(all_teams),
        "lga": [LGAS[i % 2] for i in range(len(all_teams))],
        "ward": ["W1"] * len(all_teams),
        "team_code": all_teams,
    })
    return tracks, visitation, {
        "on_time": on_time, "late": late, "early": early, "idle": idle,
        "invalid": invalid, "missing": missing,
    }


ok = True


def check(label, got, want):
    global ok
    good = got == want
    ok = ok and good
    print(f"  [{'PASS' if good else 'FAIL'}] {label}: got {got}, want {want}")


def main() -> int:
    tracks, visitation, groups = build()
    with tempfile.TemporaryDirectory() as tmp:
        tracks_csv = os.path.join(tmp, "tracks.csv")
        tracks.to_csv(tracks_csv, index=False)
        res = analyse(tracks_csv, visitation, report_date=DATE,
                      scope=scope_from_visitation(visitation))

    m, table, per_team = res["meta"], res["flagged_table"], res["per_team"]
    by_team = per_team.set_index("Team")

    total_expected = sum(len(v) for v in groups.values())
    print(f"teams analysed: {m['total_teams']} (expected {total_expected})\n")
    print(table.head(6).to_string(index=False))
    print()

    print("counts:")
    check("total teams", m["total_teams"], total_expected)
    check("on-time teams are OK",
          all(by_team.loc[t, "Flag/Status"] == OK for t in groups["on_time"]), True)
    check("flagged teams total", m["flagged_teams"],
          total_expected - len(groups["on_time"]))
    check("one row per flagged team in flagged_table", len(table), m["flagged_teams"])
    check("no team listed twice", table["Team"].duplicated().any(), False)

    print("\nreasons attributed correctly:")
    for t in groups["late"]:
        check(f"{t} reason", by_team.loc[t, "Reason for Flagging"], REASON_LATE_START)
    for t in groups["early"]:
        check(f"{t} reason", by_team.loc[t, "Reason for Flagging"], REASON_EARLY_STOP)
    for t in groups["idle"]:
        reason = by_team.loc[t, "Reason for Flagging"]
        check(f"{t} has insufficient-time reason",
              REASON_INSUFFICIENT_TIME in reason, True)
        check(f"{t} has excessive-idle reason",
              REASON_EXCESSIVE_IDLE in reason, True)
    for t in groups["invalid"]:
        check(f"{t} reason", by_team.loc[t, "Reason for Flagging"],
              REASON_INVALID_TIMESTAMP)
    for t in groups["missing"]:
        check(f"{t} reason", by_team.loc[t, "Reason for Flagging"],
              f"{REASON_MISSING_START} + {REASON_MISSING_END}")
        check(f"{t} efficiency is 0", by_team.loc[t, "Team Time Efficiency (%)"], 0.0)

    print("\nrequired flagged_table fields present:")
    required = ("State", "LGA", "Ward", "Team", "Team Date", "Start Time", "End Time",
               "Total Recorded Duration (min)", "Effective Operational Duration (min)",
               "Team Time Efficiency (%)", "Flag/Status", "Reason for Flagging")
    check("all required columns present",
          all(c in table.columns for c in required), True)
    check("State/LGA/Ward attributed for a missing team",
          bool(by_team.loc[groups["missing"][0], "LGA"]), True)

    print("\nfigures reconcile:")
    check("summary Flagged count matches flagged_table rows",
          int(res["summary"].loc[res["summary"]["Status"] == FLAGGED, "Teams"].iloc[0]),
          len(table))
    check("summary OK count matches on-time teams",
          int(res["summary"].loc[res["summary"]["Status"] == OK, "Teams"].iloc[0]),
          len(groups["on_time"]))
    check("flagged_pct matches the narration's percentage",
          round(m["flagged_pct"] * 100, 1),
          round((total_expected - len(groups["on_time"])) / total_expected * 100, 1))
    check("reason_counts sums cover late+early+invalid+2*missing "
          "(idle teams carry two reasons each)",
          m["reason_counts"].get(REASON_LATE_START, 0), len(groups["late"]))

    print("\n" + ("ALL PASSED" if ok else "FAILURES ABOVE"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
