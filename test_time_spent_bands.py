import sys, pandas as pd, numpy as np
sys.path.insert(0, '/sessions/clever-awesome-wright/mnt/gts_pipeline')
from team_time_range import analyse, state_distribution, NO_TRACKS, UNDER_12

# 300 planned teams; only 100 have any pings at all. Of those 100, 20 are brief.
rows = []
for t in range(300):
    reported = t < 100
    brief = reported and t < 20
    for s in range(3):                      # 3 settlements each
        if not reported:
            pings = 0
        elif brief:
            pings = 1                       # 3 settlements x 1 ping x 2 min = 6 min
        else:
            pings = 60                      # 3 x 60 x 2 = 360 min
        rows.append({"State": "Testland", "LGA": f"LGA{t % 5}",
                     "Ward": f"LGA{t % 5} Ward {s % 2}",
                     "Settlement": f"S{t}-{s}",
                     "team_code": f"T{t:03d}", "track_count": pings})

df = pd.DataFrame(rows)
res = analyse(df, "Testland")
dist = state_distribution(res)
print(dist.to_string(index=False))
m = res["meta"]
print("\nmeta:", {k: m[k] for k in ("total_teams", "teams_with_tracks",
                                    "teams_no_tracks", "teams_under_12")})

ok = True
def check(lbl, got, want):
    global ok
    good = got == want; ok = ok and good
    print(f"  [{'PASS' if good else 'FAIL'}] {lbl}: got {got}, want {want}")

d = dict(zip(dist["Time Spent Range"], dist["Teams"]))
check("bands sum to team count", int(dist['Teams'].sum()), 300)
check("no-tracks teams are their own band", d[NO_TRACKS], 200)
check("under-12 counts only teams actually seen", d[UNDER_12], 20)
check("no-tracks NOT folded into under-12", d[UNDER_12] < 100, True)
check("meta total", m["total_teams"], 300)
check("meta with tracks", m["teams_with_tracks"], 100)
check("under-12 follow-up list excludes no-tracks",
      int(res["under_12_lga"]["Teams Under 12 Mins"].sum()), 20)
print("\n" + ("ALL PASSED" if ok else "FAILURES ABOVE"))
sys.exit(0 if ok else 1)
