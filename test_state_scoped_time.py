"""Assert the Time Spent Analysis counts only the campaign's state.

GTS track exports are national. Builds one covering three states and checks
that each of the three scoping routes — the export's own state column, the
state's LGA names, and its team codes — yields the campaign state's teams and
nothing else.

    python test_state_scoped_time.py
"""
import os
import sys
import tempfile

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from team_daily_time import analyse, scope_from_visitation  # noqa: E402

DATE = "05/02/2026"

# Three states. Only Nasarawa is the campaign; the other two must not count.
STATES = {
    "Nasarawa": {"lgas": ["Lafia", "Akwanga", "Toto"], "teams": 40},
    "Kano":     {"lgas": ["Dala", "Fagge"],            "teams": 55},
    "Zamfara":  {"lgas": ["Gusau", "Talata_Mafara"],   "teams": 30},
}
CAMPAIGN_STATE = "Nasarawa"
EXPECTED_TEAMS = STATES[CAMPAIGN_STATE]["teams"]


def build_tracks(path, with_state_column: bool):
    rows = []
    for state, cfg in STATES.items():
        for t in range(cfg["teams"]):
            lga = cfg["lgas"][t % len(cfg["lgas"])]
            team = f"{state[:3].upper()}/{t:03d}"
            for k in range(30):                      # 30 pings, 2 min apart
                mins = 8 * 60 + k * 2
                row = {"Lat": 9.0, "Lon": 8.0,
                       "NGA LGA 2024 Label": lga, "Team Code": team,
                       "GPS Timestamp (UTC)":
                           f"{DATE} {mins // 60:02d}:{mins % 60:02d}:00"}
                if with_state_column:
                    row["NGA State 2024 Label"] = state
                rows.append(row)
    pd.DataFrame(rows).to_csv(path, index=False)
    return len(rows)


def build_visitation(path, include_state_col=True, include_lga=True,
                     include_team=True):
    """A stage-2 style visitation CSV, already filtered to the campaign state."""
    cfg = STATES[CAMPAIGN_STATE]
    rows = []
    for t in range(cfg["teams"]):
        row = {"Settlement": f"S{t}"}
        if include_state_col:
            row["State"] = CAMPAIGN_STATE
        if include_lga:
            # underscore variant on purpose — the normaliser must still match
            row["LGA"] = cfg["lgas"][t % len(cfg["lgas"])].replace(" ", "_")
        if include_team:
            row["team_code"] = f"{CAMPAIGN_STATE[:3].upper()}/{t:03d}"
        rows.append(row)
    pd.DataFrame(rows).to_csv(path, index=False)


ok = True


def check(label, got, want):
    global ok
    good = got == want
    ok = ok and good
    print(f"  [{'PASS' if good else 'FAIL'}] {label}: got {got}, want {want}")


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="state_scope_")
    national = os.path.join(tmp, "merged_tracks.csv")
    no_state_col = os.path.join(tmp, "merged_no_state.csv")
    total = build_tracks(national, with_state_column=True)
    build_tracks(no_state_col, with_state_column=False)
    all_teams = sum(c["teams"] for c in STATES.values())
    print(f"national export: {total:,} pings, {all_teams} teams "
          f"across {len(STATES)} states\n")

    print("unscoped (the old behaviour) — counts every state:")
    base = analyse(national, "latest")
    check("counts all states' teams", base["meta"]["total_teams"], all_teams)

    print("\nroute 1 — the export's own state column:")
    vis = os.path.join(tmp, "vis_full.csv")
    build_visitation(vis)
    r1 = analyse(national, "latest", scope=scope_from_visitation(vis))
    check("counts only the campaign state", r1["meta"]["total_teams"], EXPECTED_TEAMS)
    check("meta records the state", r1["meta"]["state"], CAMPAIGN_STATE)
    check("flagged as scoped", r1["meta"]["state_scoped"], True)

    print("\nroute 2 — LGA names, when the export has no state column:")
    r2 = analyse(no_state_col, "latest", scope=scope_from_visitation(vis))
    check("counts only the campaign state", r2["meta"]["total_teams"], EXPECTED_TEAMS)
    lgas = set(r2["per_team"]["lga"])
    check("no other state's LGA present",
          lgas <= set(STATES[CAMPAIGN_STATE]["lgas"]), True)

    print("\nroute 3 — team codes, when there is no state or LGA column:")
    vis_teams = os.path.join(tmp, "vis_teams.csv")
    build_visitation(vis_teams, include_state_col=False, include_lga=False)
    r3 = analyse(no_state_col, "latest", scope=scope_from_visitation(vis_teams))
    check("counts only the campaign state", r3["meta"]["total_teams"], EXPECTED_TEAMS)

    print("\nevery route agrees:")
    check("route 1 == route 2", r1["meta"]["total_teams"], r2["meta"]["total_teams"])
    check("route 2 == route 3", r2["meta"]["total_teams"], r3["meta"]["total_teams"])
    check("bands sum to the state's teams",
          int(r1["distribution"]["Teams"].sum()), EXPECTED_TEAMS)

    print("\n" + ("ALL PASSED" if ok else "FAILURES ABOVE"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
