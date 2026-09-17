"""Answer one question: will Team Time Efficiency be in the next report?

Runs the exact calls stage 6 makes, on your files, in about five seconds —
without running the pipeline. It reports the decision and the reason for it.

    python check_efficiency.py --visitation Day1_out\\Zamfara_day_1_settlement_visitation.csv --tracks Day1_out\\merged_tracks.csv
    python check_efficiency.py --visitation ... --tracks ... --charts Day1_out\\charts

Stage 6 keeps the slide when EITHER the figures recompute OR the chart exists,
so both are checked separately. Whichever fails, this names it.
"""
from __future__ import annotations

import argparse
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--visitation", required=True,
                    help="{State}_day_{N}_settlement_visitation.csv")
    ap.add_argument("--tracks", required=True,
                    help="merged_tracks.csv — Team Time Efficiency needs raw GPS "
                         "timestamps, which only exist in the track export")
    ap.add_argument("--date", default="latest",
                    help='reporting date to check, "latest" (default) or YYYY-MM-DD')
    ap.add_argument("--charts", default=None, help="the run's charts folder")
    a = ap.parse_args()

    if not os.path.exists(a.visitation):
        raise SystemExit(f"Not found: {a.visitation}")
    if not os.path.exists(a.tracks):
        raise SystemExit(f"Not found: {a.tracks}")

    print("=" * 68)
    print("1. THE INPUTS")
    df = pd.read_csv(a.visitation, low_memory=False)
    print(f"   settlement CSV: {len(df):,} rows, {len(df.columns)} columns")
    team_col = next((c for c in df.columns
                     if "team" in str(c).lower() and "code" in str(c).lower()), None)
    lga_col = next((c for c in df.columns if "lga" in str(c).lower()
                    and "code" not in str(c).lower()), None)
    print(f"   team column            {team_col!r}")
    print(f"   LGA column             {lga_col!r}")

    from track_columns import read_columns, resolve
    header = read_columns(a.tracks)
    cols = resolve(header)
    print(f"   track export: {len(header)} columns")
    for role in ("team", "ts", "lga", "ward", "state", "valid"):
        print(f"   track {role:6s} column     {cols.get(role)!r}")
    if not cols.get("team") or not cols.get("ts"):
        print("   MISSING a team-code or timestamp column — Team Time "
              "Efficiency cannot be computed from this export at all.")

    print()
    print("2. THE RECOMPUTE — team_efficiency.analyse(tracks, visitation, date)")
    eff = None
    try:
        from team_efficiency import analyse
        from team_daily_time import scope_from_visitation
        eff = analyse(a.tracks, df, report_date=a.date, scope=scope_from_visitation(df))
        n = len(eff["per_team"])
        print(f"   SUCCEEDED — {n:,} team rows for {eff['meta']['date']}")
        if n == 0:
            # stage 6 treats an empty result as no analysis at all, so this is
            # a failure even though nothing was raised.
            print("   ...but ZERO teams, which stage 6 treats as no analysis.")
            eff = None
        else:
            m = eff["meta"]
            print(f"   {m['flagged_teams']:,} of {m['total_teams']:,} teams "
                  f"flagged ({m['flagged_pct']:.1%}) — avg efficiency "
                  f"{m['avg_efficiency_pct']:.1f}%")
    except Exception as exc:
        print(f"   FAILED — {type(exc).__name__}: {exc}")

    print()
    print("3. THE CHART — team_efficiency.png")
    chart_ok = False
    if a.charts:
        p = os.path.join(a.charts, "team_efficiency.png")
        chart_ok = os.path.exists(p)
        print(f"   {'FOUND' if chart_ok else 'NOT FOUND'}: {p}")
        if chart_ok:
            print(f"   {os.path.getsize(p):,} bytes")
    else:
        print("   not checked — pass --charts to include it")

    print()
    print("4. WHAT STAGE 6 WILL DO")
    keep = eff is not None or chart_ok
    print(f"   figures={'yes' if eff is not None else 'no'}, "
          f"chart={'yes' if chart_ok else 'no'} "
          f"-> slide {'INCLUDED' if keep else 'DROPPED'}")
    print()
    if keep and eff is not None:
        print("   The slide will be in the report, with this run's figures.")
        print("   If your last run did not have it, the app was running an")
        print("   older copy of stage6_pptx_report.py — stop Streamlit")
        print("   completely (Ctrl+C) and start it again. Streamlit reloads")
        print("   app.py on change but can keep imported modules cached.")
    elif keep:
        print("   The slide will be in the report, showing the chart with a")
        print("   methodology narration rather than this run's numbers.")
    else:
        print("   The slide will be dropped. Fix step 2 or step 3 above —")
        print("   the deck cannot show an analysis that does not exist.")
    print("=" * 68)
    return 0 if keep else 1


if __name__ == "__main__":
    sys.exit(main())
