"""Guards the Time Spent hierarchy: day -> overlay -> state -> team -> minutes.

Every check here is a way the analysis could silently widen its own population.
They are asserted rather than inspected because all of them produce a deck that
opens cleanly and reports the wrong campaign:

  * cross-day contamination — yesterday's teams counted as today's
  * out-of-state tracks — a national export's other campaigns counted as ours
  * duplicate team counting — one team counted once per LGA or per burst
  * state taken from a data field rather than from the geometry

The fixture is built around a real Nigerian state boundary so the overlay is a
genuine spatial test, not a stub.

Run: python test_day_state_scoping.py
"""
from __future__ import annotations

import os
import sys
import tempfile

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import track_geo as tg

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_LAYER = os.path.join(HERE, "state.sqlite")
LGA_LAYER = os.path.join(HERE, "LGA.sqlite")

# Inside Zamfara, and inside Katsina — the neighbouring state, which stands in
# for "another campaign in the same national export".
IN_STATE = (11.90, 6.20)      # lat, lon — Gusau area
OUT_OF_STATE = (12.60, 7.60)  # Katsina

DAY_A, DAY_B = "2026-04-18", "2026-04-19"

FAILURES: list[str] = []


def check(name, got, want):
    if got == want:
        print(f"ok    {name}")
    else:
        print(f"FAIL  {name}: got {got!r}, expected {want!r}")
        FAILURES.append(name)


def _tracks(path):
    """Four teams, laid out so every rule has something to catch.

    ZAM-DAY-A   in state, day A only          -> counted for day A
    ZAM-DAY-B   in state, day B only          -> must NOT appear on day A
    ZAM-BOTH    in state, both days           -> appears on each, once
    KAT-DAY-A   OUT of state, day A           -> never appears
    ZAM-MULTI   in state, day A, many bursts  -> one team, not one per burst
    """
    rows = []

    def add(team, day, lat, lon, minutes):
        base = pd.Timestamp(f"{day} 07:00:00")   # 08:00 local
        for m in minutes:
            rows.append({
                "NGA State 2024 Label": "SomethingWrong",   # deliberately wrong
                "NGA LGA 2024 Label": "AlsoWrong",
                "Team Code": team, "IMEI": f"dev-{team}", "Valid": "1",
                "GPS Timestamp (UTC)": (base + pd.Timedelta(minutes=m)
                                        ).strftime("%m/%d/%Y %H:%M:%S"),
                "Lat": lat, "Lon": lon,
            })

    steady = [0, 2, 4, 6, 8, 10, 12, 14]
    add("ZAM-DAY-A", DAY_A, *IN_STATE, steady)
    add("ZAM-DAY-B", DAY_B, *IN_STATE, steady)
    add("ZAM-BOTH", DAY_A, *IN_STATE, steady)
    add("ZAM-BOTH", DAY_B, *IN_STATE, steady)
    add("KAT-DAY-A", DAY_A, *OUT_OF_STATE, steady)
    # many separate bursts on one day — one team, however fragmented
    add("ZAM-MULTI", DAY_A, *IN_STATE, [0, 2, 60, 62, 120, 180, 240, 300])
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _settlements(path, state="Zamfara"):
    """The planned settlement list the state of interest is derived from."""
    pd.DataFrame([{"state": state, "lga": "Gusau", "team_code": "GUS-001",
                   "settlements": "S1", "latitude": IN_STATE[0],
                   "longitude": IN_STATE[1]}]).to_csv(path, index=False)
    return path


def main():
    if not os.path.exists(STATE_LAYER):
        print(f"SKIP — {STATE_LAYER} not found")
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        tracks = _tracks(os.path.join(tmp, "merged_tracks.csv"))
        dip = _settlements(os.path.join(tmp, "vis.csv"))

        states, _ = tg.load_boundaries(STATE_LAYER, None)

        # --- state of interest comes from the PLAN, not from the tracks.
        # Every track row carries "SomethingWrong" in its state field, so a
        # field-based filter would return nothing at all.
        check("state of interest from planned settlements",
              tg.state_of_interest(dip, states), "Zamfara")

        # --- day A
        a = tg.analyse(tracks, "Zamfara", STATE_LAYER, LGA_LAYER, report_date=DAY_A)
        teams_a = set(a["pings"]["team_code"])
        check("day A teams", sorted(teams_a),
              ["ZAM-BOTH", "ZAM-DAY-A", "ZAM-MULTI"])
        check("day A excludes the next day's team", "ZAM-DAY-B" in teams_a, False)
        check("day A excludes the out-of-state team", "KAT-DAY-A" in teams_a, False)
        check("day A ping dates are all day A",
              sorted(a["pings"]["date"].unique()), [DAY_A])

        # --- day B: a different population, recalculated for that day alone
        b = tg.analyse(tracks, "Zamfara", STATE_LAYER, LGA_LAYER, report_date=DAY_B)
        teams_b = set(b["pings"]["team_code"])
        check("day B teams", sorted(teams_b), ["ZAM-BOTH", "ZAM-DAY-B"])
        check("day B excludes the previous day's team", "ZAM-DAY-A" in teams_b, False)
        check("day B is not cumulative", len(teams_b) < len(teams_a), True)

        # --- unique counting
        ts_a = a["time_spent"]
        check("time spent counts each team once",
              len(ts_a["per_team"]), ts_a["per_team"]["team_code"].nunique())
        check("the fragmented team is one team, not one per burst",
              int((ts_a["per_team"]["team_code"] == "ZAM-MULTI").sum()), 1)
        check("time spent total == unique teams that day",
              ts_a["meta"]["total"], len(teams_a))

        # --- minutes come only from in-state, in-day pings. ZAM-BOTH sent the
        # same 14-minute run on both days; its day A minutes must not include
        # day B's, which a cumulative measure would add.
        both = ts_a["per_team"].set_index("team_code").loc["ZAM-BOTH", "Minutes"]
        check("ZAM-BOTH's day A minutes exclude day B", float(both), 14.0)

        # --- device view is scoped identically
        check("device time spent counts each device once",
              len(a["time_spent_device"]["per_team"]),
              a["time_spent_device"]["per_team"]["device"].nunique())

        # --- the validation table's own assertions
        v = tg.validate(a).set_index("Check")["Value"].to_dict()
        for row in ("No duplicate team counting", "No cross-day contamination",
                    "No out-of-state tracks"):
            check(f"validation: {row}", v[row], "OK")
        check("validation reports the selected day", v["Selected day"], DAY_A)
        check("validation reports the state of interest",
              v["State of interest"], "Zamfara")
        # The funnel has to narrow at every step, never widen.
        check("funnel narrows: in-state day <= in-state all days",
              int(v["Tracks on the selected day"])
              <= int(v["Tracks within the state of interest"]), True)
        check("funnel narrows: in-state <= assigned to a state",
              int(v["Tracks within the state of interest"])
              <= int(v["Tracks assigned to a state"]), True)

        # --- a settlement list for a different state must move the analysis
        other = tg.analyse(tracks, "Katsina", STATE_LAYER, LGA_LAYER,
                           report_date=DAY_A)
        check("Katsina sees only the Katsina team",
              sorted(set(other["pings"]["team_code"])), ["KAT-DAY-A"])

    print()
    if FAILURES:
        print(f"FAILED — {len(FAILURES)} check(s): {', '.join(FAILURES)}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
