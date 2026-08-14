import sys, pandas as pd
sys.path.insert(0, '/sessions/clever-awesome-wright/mnt/gts_pipeline')
from team_daily_time import analyse, per_team_per_date, MAX_GAP_MINUTES

rows = []
def ping(team, day, hh, mm):
    rows.append({"NGA LGA 2024 Label": "Lafia", "Team Code": team,
                 "GPS Timestamp (UTC)": f"05/0{day}/2026 {hh:02d}:{mm:02d}:00"})

# T1 day2: steady 2-min pings 09:00-11:00 -> 120 min
for k in range(61):
    ping("T1", 2, 8 + (k*2)//60, (k*2) % 60)
# T2 day2: 3 pings then a 3-hour gap then 3 pings -> gap excluded
for m in (0, 2, 4):  ping("T2", 2, 9, m)
for m in (0, 2, 4):  ping("T2", 2, 13, m)
# T3 day2: single ping
ping("T3", 2, 10, 0)
# T4 day1 ONLY - must not appear on day 2
for k in range(30): ping("T4", 1, 9, k)

pd.DataFrame(rows).to_csv("dt.csv", index=False)
allr = per_team_per_date("dt.csv")
print(allr.to_string(index=False)); print()

res = analyse("dt.csv")
print(res["distribution"].to_string(index=False))
m = res["meta"]; print("\nmeta:", {k: m[k] for k in ("date","total_teams","dates_in_export")})

ok = True
def check(l, got, want):
    global ok; good = got == want; ok = ok and good
    print(f"  [{'PASS' if good else 'FAIL'}] {l}: got {got}, want {want}")

d2 = allr[allr["date"] == "2026-05-02"].set_index("team_code")
check("reporting date is the latest", m["date"], "2026-05-02")
check("steady tracker measured across its span", d2.loc["T1","Minutes"], 120.0)
check("3-hour gap excluded (2 gaps x 2 min x 2 blocks)", d2.loc["T2","Minutes"], 8.0)
check("single ping credited, not zero", d2.loc["T3","Minutes"], 2.0)
check("day-1-only team absent from day 2", "T4" in d2.index, False)
check("population = teams that transmitted", m["total_teams"], 3)
check("bands sum to that population", int(res["distribution"]["Teams"].sum()), 3)
print("\n" + ("ALL PASSED" if ok else "FAILURES ABOVE"))
sys.exit(0 if ok else 1)
