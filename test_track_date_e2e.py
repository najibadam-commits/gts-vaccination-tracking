"""End-to-end check that stage 2 scopes a day's tracks to one transmission date.

Builds a tiny but real spatial dataset — settlement extents, gridded target
areas and a two-day track export — and runs `run_analysis` over it twice: once
scoped to the latest date (the daily default) and once unscoped (post-campaign).
The assertion is that `track_count`, which every per-team minutes figure is
derived from, reflects only the reporting day in the first case.
"""
import os
import sys

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point, box

sys.path.insert(0, "/sessions/clever-awesome-wright/mnt/gts_pipeline")
from stage2_analysis import run_analysis  # noqa: E402

OUT = "/tmp/tdtest"
os.makedirs(OUT, exist_ok=True)

# Three settlements, 0.02deg apart, each a 0.01deg square extent split into a
# 2x2 grid of target-area cells.
SETTS = [("Alpha", 8.10, 9.10), ("Beta", 8.20, 9.10), ("Gamma", 8.30, 9.10)]
SIZE = 0.01

extents, grids, rows = [], [], []
for name, lon, lat in SETTS:
    extents.append({"State": "Testland", "LGA": "Central", "Ward": "Ward 1",
                    "Settlement": name,
                    "geometry": box(lon, lat, lon + SIZE, lat + SIZE)})
    for i in range(2):
        for j in range(2):
            grids.append({
                "State": "Testland", "LGA": "Central", "Ward": "Ward 1",
                "Settlement": name,
                "geometry": box(lon + i * SIZE / 2, lat + j * SIZE / 2,
                                lon + (i + 1) * SIZE / 2, lat + (j + 1) * SIZE / 2)})
    rows.append({"State": "Testland", "LGA": "Central", "Ward": "Ward 1",
                 "Settlement": name, "team_code": f"T-{name[:1]}",
                 "latitude": lat + SIZE / 2, "longitude": lon + SIZE / 2,
                 "day1": "Yes", "day2": "Yes"})

vor_path = os.path.join(OUT, "voronoi.geojson")
ta_path = os.path.join(OUT, "gridded.geojson")
gpd.GeoDataFrame(extents, crs="EPSG:4326").to_file(vor_path, driver="GeoJSON")
gpd.GeoDataFrame(grids, crs="EPSG:4326").to_file(ta_path, driver="GeoJSON")

sett_path = os.path.join(OUT, "settlements.csv")
pd.DataFrame(rows).to_csv(sett_path, index=False)

# Tracks. Day 1 is a heavy day in Alpha and Beta; day 2 is a light day in Beta
# and Gamma. A single 23:45 UTC ping spills onto a third local date and must be
# ignored rather than treated as the reporting day.
track_rows = []


def pings(settlement_lon, settlement_lat, day, n, team, hour=9):
    for k in range(n):
        track_rows.append({
            "Lat": settlement_lat + SIZE / 4 + (k % 3) * 0.0005,
            "Lon": settlement_lon + SIZE / 4 + (k % 2) * 0.0005,
            "NGA LGA 2024 Label": "Central", "Team Code": team,
            "GPS Timestamp (UTC)": f"05/0{day}/2026 {hour:02d}:{k % 60:02d}:00"})


pings(8.10, 9.10, 1, 200, "T-A")     # Alpha, day 1 only
pings(8.20, 9.10, 1, 150, "T-B")     # Beta, day 1
pings(8.20, 9.10, 2, 40, "T-B")      # Beta, day 2
pings(8.30, 9.10, 2, 60, "T-G")      # Gamma, day 2 only
track_rows.append({"Lat": 9.105, "Lon": 8.105, "NGA LGA 2024 Label": "Central",
                   "Team Code": "T-A", "GPS Timestamp (UTC)": "05/02/2026 23:45:00"})

tracks_path = os.path.join(OUT, "merged_tracks.csv")
pd.DataFrame(track_rows).to_csv(tracks_path, index=False)
print(f"track export: {len(track_rows)} pings across 3 local dates\n")


def track_counts(result):
    df = pd.read_csv(result["visitation_csv"])
    return dict(zip(df["Settlement"], df["track_count"].astype(int)))


print("=" * 60)
print("DAILY run (track_date='latest')")
print("=" * 60)
daily = run_analysis(sett_path, tracks_path, ta_path, vor_path, "Testland", 2,
                     False, os.path.join(OUT, "daily"), track_date="latest")
d_counts = track_counts(daily)
print("\nresolved date :", daily["track_date"])
print("track_count   :", d_counts)

print("\n" + "=" * 60)
print("POST-CAMPAIGN run (track_date=None)")
print("=" * 60)
whole = run_analysis(sett_path, tracks_path, ta_path, vor_path, "Testland", 2,
                     False, os.path.join(OUT, "whole"), track_date=None)
w_counts = track_counts(whole)
print("\ntrack_count   :", w_counts)

print("\n" + "=" * 60)
print("ASSERTIONS")
print("=" * 60)
ok = True


def check(label, got, want):
    global ok
    good = got == want
    ok = ok and good
    print(f"  [{'PASS' if good else 'FAIL'}] {label}: got {got}, want {want}")


check("reporting date ignores the 23:45 spill", daily["track_date"], "2026-05-02")
check("Alpha (day 1 only) has no day-2 time", d_counts["Alpha"], 0)
check("Beta counts day 2 pings only", d_counts["Beta"], 40)
check("Gamma counts day 2 pings only", d_counts["Gamma"], 60)
check("unscoped Alpha keeps day 1 pings", w_counts["Alpha"], 201)
check("unscoped Beta spans both days", w_counts["Beta"], 190)

daily_df = pd.read_csv(daily["visitation_csv"])
alpha = daily_df[daily_df["Settlement"] == "Alpha"].iloc[0]
check("Alpha still cumulatively visited on track evidence",
      alpha["day_2_cumm"], "Visited")
check("Alpha not counted as visited TODAY", alpha["day_2_daily"], "Not Visited")

print("\n" + ("ALL PASSED" if ok else "FAILURES ABOVE"))
sys.exit(0 if ok else 1)
