"""Unit tests for the tracking-session analysis.

Sessionising is the kind of logic that looks obviously right and is quietly
off by one, so every case here is a hand-built ping sequence with an answer
worked out by hand rather than read back from the code.

Run: python test_team_sessions.py
"""
from __future__ import annotations

import os
import sys
import tempfile

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import team_sessions as ts

FAILURES: list[str] = []


def check(name, got, want):
    if got == want:
        print(f"ok    {name}")
    else:
        print(f"FAIL  {name}: got {got!r}, expected {want!r}")
        FAILURES.append(name)


def _ts(*minutes_from_start):
    base = pd.Timestamp("2026-04-18 08:00:00")
    return pd.Series([base + pd.Timedelta(minutes=m) for m in minutes_from_start])


# ------------------------------------------------------------ _sessions
def test_sessions_counting():
    gap = ts.SESSION_GAP_MINUTES

    check("no pings is no sessions", ts._sessions(_ts()), 0)
    # One ping is evidence a transmission began. Counting it as zero would make
    # a team that reported once indistinguishable from one that never reported.
    check("single ping is one session", ts._sessions(_ts(0)), 1)
    check("steady 2-min pings are one session",
          ts._sessions(_ts(0, 2, 4, 6, 8, 10)), 1)
    # Exactly at the boundary is still the same session — the rule is
    # "longer than", so the boundary value itself must not split.
    check("gap exactly at the boundary does not split",
          ts._sessions(_ts(0, gap)), 1)
    check("gap just over the boundary splits",
          ts._sessions(_ts(0, gap + 0.5)), 2)
    check("two clear breaks make three sessions",
          ts._sessions(_ts(0, 2, 4, 90, 92, 200)), 3)
    # Order must not matter: chunked reads can deliver pings out of sequence.
    check("unsorted input gives the same answer",
          ts._sessions(_ts(200, 2, 92, 0, 90, 4)), 3)


# ------------------------------------------------- end-to-end on a CSV
def _tracks_csv(path):
    """Three teams on one date, with sessions chosen to be countable by eye.

      ZAM-01  steady pings, 1 session, own device
      ZAM-02  five separate bursts -> 5 sessions, so FRAGMENTED (>= 4)
      ZAM-03  two bursts -> 2 sessions, and shares ZAM-02's device
      ZAM-04  every ping flagged Valid = 0 -> must vanish entirely

    The Valid column is written as a STRING, which is how it arrives in a real
    merged export and the reason `valid_mask` does not compare it to 0.
    """
    rows = []

    def add(team, device, minutes, valid="1", lga="Anka"):
        base = pd.Timestamp("2026-04-18 07:00:00")   # 08:00 local (UTC+1)
        for m in minutes:
            rows.append({
                "NGA State 2024 Label": "Zamfara",
                "NGA LGA 2024 Label": lga,
                "Team Code": team,
                "IMEI": device,
                "Valid": valid,
                "GPS Timestamp (UTC)": (base + pd.Timedelta(minutes=m)
                                        ).strftime("%m/%d/%Y %H:%M:%S"),
                "Lat": 11.9, "Lon": 6.0,
            })

    add("ZAM-01", "device-a", [0, 2, 4, 6, 8, 10])
    add("ZAM-02", "device-b", [0, 2, 60, 62, 120, 180, 240, 242])
    add("ZAM-03", "device-b", [300, 302, 400], lga="Bakura")
    add("ZAM-04", "device-c", [0, 2, 4], valid="0")

    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def test_end_to_end():
    with tempfile.TemporaryDirectory() as tmp:
        res = ts.analyse(_tracks_csv(os.path.join(tmp, "merged_tracks.csv")),
                         "latest")
        per_team = res["per_team"].set_index("team_code")
        m = res["meta"]

        check("invalid-only team is excluded",
              "ZAM-04" in per_team.index, False)
        check("teams counted", m["total_teams"], 3)
        check("steady team is one session",
              int(per_team.loc["ZAM-01", "Sessions"]), 1)
        check("bursty team is five sessions",
              int(per_team.loc["ZAM-02", "Sessions"]), 5)
        check("two-burst team is two sessions",
              int(per_team.loc["ZAM-03", "Sessions"]), 2)
        check("fragmented flag follows the threshold",
              bool(per_team.loc["ZAM-02", "Fragmented"]), True)
        check("non-fragmented team is not flagged",
              bool(per_team.loc["ZAM-03", "Fragmented"]), False)
        check("fragmented count", m["fragmented_teams"], 1)
        check("single-session count", m["single_session_teams"], 1)

        # Device sharing: device-b carried ZAM-02 and ZAM-03 on the same date.
        # This is the finding the per-device view exists for.
        check("shared devices detected", m["shared_devices"], 1)
        check("devices counted", m["total_devices"], 2)
        per_device = res["per_device"].set_index("device")
        check("shared device names both teams",
              int(per_device.loc["device-b", "Teams"]), 2)

        # by_lga feeds the slide's table, so its shape is part of the contract.
        by_lga = res["by_lga"].set_index("LGA")
        check("by_lga covers both LGAs", sorted(by_lga.index.tolist()),
              ["Anka", "Bakura"])
        check("by_lga counts fragmented teams",
              int(by_lga.loc["Anka", "Fragmented Teams"]), 1)


def test_no_device_column():
    """An export without an IMEI column must still produce team sessions."""
    with tempfile.TemporaryDirectory() as tmp:
        path = _tracks_csv(os.path.join(tmp, "merged_tracks.csv"))
        df = pd.read_csv(path).drop(columns=["IMEI"])
        df.to_csv(path, index=False)
        res = ts.analyse(path, "latest")
        check("teams still counted without a device column",
              res["meta"]["total_teams"], 3)
        check("device figures reported as absent",
              res["meta"]["has_devices"], False)
        check("shared-device count is zero, not an error",
              res["meta"]["shared_devices"], 0)


def test_sessions_match_time_spent_segmentation():
    """Sessions and minutes must come from ONE segmentation.

    `team_daily_time` sums the gaps at or below MAX_GAP_MINUTES; this module
    starts a new session above the same value. So for any team, the number of
    gaps EXCLUDED from its minutes must be exactly its session count minus one.
    If the two constants ever drift apart, this fails.
    """
    import team_daily_time as tdt
    check("session boundary is the time-spent gap rule",
          ts.SESSION_GAP_MINUTES, tdt.MAX_GAP_MINUTES)

    times = _ts(0, 2, 4, 60, 62, 200)
    gaps = times.sort_values().diff().dropna().dt.total_seconds() / 60.0
    excluded = int((gaps > tdt.MAX_GAP_MINUTES).sum())
    check("excluded gaps == sessions - 1", ts._sessions(times) - 1, excluded)


def main():
    test_sessions_counting()
    test_end_to_end()
    test_no_device_column()
    test_sessions_match_time_spent_segmentation()
    print()
    if FAILURES:
        print(f"FAILED — {len(FAILURES)} check(s): {', '.join(FAILURES)}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
