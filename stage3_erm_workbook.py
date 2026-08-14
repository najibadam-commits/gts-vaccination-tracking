"""Stage 3 — Daily ERM Analysis workbook.

Builds an Excel workbook from the Stage 2 visitation CSV and the day's raw
tracks, with the three daily analyses:
  1. Team Deploy Report  – teams deployed vs teams that submitted tracks, per LGA
  2. Time Spent Analysis – field time (08:00–15:00) per team, classified
  3. Daily ERM Analysis  – state & LGA settlement coverage tables + charts
Charts are native Excel charts (xlsxwriter) so they can be copied into slides.
"""
import argparse
import os

import numpy as np
import pandas as pd

COVERAGE_ORDER = ["Fully Covered", "Partially Covered", "Low Coverage",
                  "Very Low Coverage", "No Coverage"]
TIME_ORDER = ["0 (No evidence of tracks)", "<12 mins", "12 - 30 mins",
              "30 mins - 1 hr", "1 - 2 hrs", ">2 hrs"]
# Field window for team compliance, local time (UTC+1). Widened from a
# 14:00 close to 15:00 so teams with a late takeoff are still credited for
# the time they spend in the field.
FIELD_START, FIELD_END = 8, 15  # 08:00 - 15:00 local


def norm_lga(s: pd.Series) -> pd.Series:
    """Normalizes underscore/space LGA-name variants (e.g. "Talata_Mafara" vs
    "Talata Mafara") so they aren't double-counted as separate LGAs."""
    return (s.astype(str).str.replace("_", " ", regex=False)
            .str.replace(r"\s+", " ", regex=True).str.strip().str.title())


def load_tracks_teams(tracks_file: str, chunksize: int = 500_000) -> pd.DataFrame:
    """Chunk-aggregates merged tracks into (lga, team_code, date, hour) ping counts.

    Memory-safe for millions of rows. Dates and hours are local (UTC+1).

    `date` is carried so time-spent can be scoped to the day the tracks were
    transmitted. Without it, a merged export covering several days aggregates
    every day's field time into one figure and the "daily" analysis silently
    reports the campaign to date. The shift to local time is applied to the
    whole timestamp, not just the hour, so a 23:30 UTC ping moves to the next
    local date instead of wrapping to hour 0 on the wrong day.
    """
    cols = {"NGA LGA 2024 Label", "Team Code", "GPS Timestamp (UTC)"}
    parts = []
    for chunk in pd.read_csv(tracks_file, usecols=lambda c: c in cols,
                             chunksize=chunksize, dtype=str):
        chunk = chunk.rename(columns={"NGA LGA 2024 Label": "lga",
                                      "Team Code": "team_code",
                                      "GPS Timestamp (UTC)": "ts"})
        chunk = chunk.dropna(subset=["team_code"])
        ts = pd.to_datetime(chunk["ts"], format="%m/%d/%Y %H:%M:%S", errors="coerce")
        local = ts + pd.Timedelta(hours=1)  # UTC -> UTC+1 local
        chunk["date"] = local.dt.strftime("%Y-%m-%d")
        chunk["hour"] = local.dt.hour
        parts.append(chunk.groupby(["lga", "team_code", "date", "hour"], dropna=False)
                     .size().rename("pings"))
    agg = (pd.concat(parts).groupby(["lga", "team_code", "date", "hour"], dropna=False)
           .sum().reset_index())
    return agg


def latest_track_date(tracks_teams: pd.DataFrame) -> str | None:
    """The most recent local date of real fieldwork in the track aggregate.

    Weighted by pings and subject to the same minimum-share rule stage 2 uses,
    so a trickle of overnight pings on the following date cannot be mistaken for
    a reporting day. The threshold is imported rather than restated so the two
    stages cannot drift apart; stage 2 pulls in geopandas, so the import is done
    here rather than at module scope, keeping this module runnable on its own.

    Returns None for an aggregate produced before `date` was carried, so every
    caller degrades to the old whole-file behaviour rather than failing.
    """
    if "date" not in tracks_teams.columns:
        return None
    try:
        from stage2_analysis import MIN_DATE_PING_SHARE as share
    except Exception:
        share = 0.05
    work = tracks_teams.copy()
    work["date"] = work["date"].astype(str).str.strip()
    work = work[~work["date"].str.lower().isin(["", "nan", "nat", "none"])]
    if not len(work):
        return None
    by_date = work.groupby("date")["pings"].sum().sort_index()
    substantive = by_date[by_date >= by_date.max() * share]
    return str(substantive.index[-1] if len(substantive) else by_date.index[-1])


def scope_tracks_to_date(tracks_teams: pd.DataFrame,
                         report_date: str | None) -> tuple[pd.DataFrame, str | None]:
    """Restrict the track aggregate to one transmission date.

    `report_date="latest"` resolves to the most recent date in the file — the
    day being reported on. `None` leaves the aggregate untouched, which is what
    the post-campaign analysis wants. Returns the frame and the date actually
    applied, so callers can label the output honestly.
    """
    if report_date is None or "date" not in tracks_teams.columns:
        return tracks_teams, None
    resolved = latest_track_date(tracks_teams) if report_date == "latest" else report_date
    if resolved is None:
        return tracks_teams, None
    return tracks_teams[tracks_teams["date"].astype(str) == str(resolved)].copy(), resolved


def team_deploy_report(dip: pd.DataFrame, tracks_teams: pd.DataFrame,
                       teams_deployed_total: int | None = None) -> pd.DataFrame:
    """Teams deployed (from DIP team codes) vs teams reported (submitted tracks), per LGA.

    `teams_deployed_total`, if given, is the analyst's manually-entered total teams
    deployed for the campaign/reporting day — it overrides the Grand Total "Teams
    Deployed" figure (which otherwise comes from counting unique team codes in the
    planned-settlement list, a figure that can drift from the true deployment count).
    Per-LGA rows are left as derived from the settlement list, since the manual
    figure is a single state-wide number. A "Reporting %" column (Teams Reported /
    Teams Deployed) is always added.
    """
    lga_col = next(c for c in dip.columns if "lga" in c.lower() and "code" not in c.lower())
    team_col = next((c for c in dip.columns if "team" in c.lower() and "code" in c.lower()), None)

    deployed = (dip.dropna(subset=[team_col]).groupby(lga_col)[team_col].nunique()
                if team_col else pd.Series(dtype=int))
    reported = tracks_teams.groupby("lga")["team_code"].nunique()

    report = pd.DataFrame({"Teams Deployed": deployed, "Teams Reported": reported}).fillna(0).astype(int)
    report["Teams Pending"] = (report["Teams Deployed"] - report["Teams Reported"]).clip(lower=0)
    report.index.name = "LGA"
    report.loc["Grand Total"] = report.sum()

    if teams_deployed_total is not None:
        gt_reported = int(report.loc["Grand Total", "Teams Reported"])
        report.loc["Grand Total", "Teams Deployed"] = int(teams_deployed_total)
        report.loc["Grand Total", "Teams Pending"] = max(int(teams_deployed_total) - gt_reported, 0)

    denom = report["Teams Deployed"].replace(0, np.nan)
    report["Reporting %"] = (report["Teams Reported"] / denom).fillna(0).round(3)
    return report.reset_index()


def coordinates_table(dip: pd.DataFrame) -> pd.DataFrame:
    """Settlements with vs without geo-coordinates, per LGA (planned-settlement list)."""
    lga_col = next(c for c in dip.columns if "lga" in c.lower() and "code" not in c.lower())
    lat_col = next((c for c in dip.columns if "latitude" in c.lower()), None)
    lon_col = next((c for c in dip.columns if "longitude" in c.lower()), None)
    has_geo = (dip[[lat_col, lon_col]].notna().all(axis=1)
              if lat_col and lon_col else pd.Series(False, index=dip.index))

    tbl = pd.DataFrame({"LGA": norm_lga(dip[lga_col]), "has_geo": has_geo})
    out = tbl.groupby("LGA")["has_geo"].sum().rename("With Coordinates").astype(int)
    total = tbl.groupby("LGA").size().rename("Total")
    out = pd.concat([out, total], axis=1)
    out["Without Coordinates"] = (out["Total"] - out["With Coordinates"]).astype(int)
    out = out.drop(columns="Total").reset_index().sort_values("LGA")
    out.loc[len(out)] = ["Grand Total", int(out["With Coordinates"].sum()),
                         int(out["Without Coordinates"].sum())]
    return out.reset_index(drop=True)


def missed_children_table(dip: pd.DataFrame, cum_col: str) -> pd.DataFrame:
    """Target children in settlements NOT visited, per LGA — ranked descending.

    Uses the settlement list's target-population field (`set_target`, falling back
    to `set_population`) summed over settlements where `cum_col` != "Visited"."""
    lga_col = next(c for c in dip.columns if "lga" in c.lower() and "code" not in c.lower())
    target_col = next((c for c in dip.columns
                       if c.lower() in ("set_target", "target", "set_population")), None)
    if not target_col:
        return pd.DataFrame(columns=["LGA", "Missed Children"])

    nv = dip[dip[cum_col] != "Visited"].copy()
    nv[lga_col] = norm_lga(nv[lga_col])
    nv[target_col] = pd.to_numeric(nv[target_col], errors="coerce").fillna(0)
    out = (nv.groupby(lga_col)[target_col].sum().round().astype(int)
           .sort_values(ascending=False).reset_index())
    out.columns = ["LGA", "Missed Children"]
    out.loc[len(out)] = ["Grand Total", int(out["Missed Children"].sum())]
    return out


def not_visited_ranked_table(dip: pd.DataFrame, cum_col: str) -> pd.DataFrame:
    """Not-visited settlement counts per LGA, ranked descending (worst LGAs first)."""
    lga_col = next(c for c in dip.columns if "lga" in c.lower() and "code" not in c.lower())
    nv = dip[dip[cum_col] != "Visited"].copy()
    nv[lga_col] = norm_lga(nv[lga_col])
    out = nv.groupby(lga_col).size().sort_values(ascending=False).reset_index()
    out.columns = ["LGA", "Not Visited"]
    out.loc[len(out)] = ["Grand Total", int(out["Not Visited"].sum())]
    return out


def time_spent_analysis(tracks_teams: pd.DataFrame, dip: pd.DataFrame,
                        report_date: str | None = "latest"
                        ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Counts 2-min pings per team within 08:00-15:00 and classifies time spent.

    Scoped to ONE transmission date by default (`report_date="latest"`), so the
    figures describe the day the tracks were received and never carry field
    time forward from earlier days. Pass `report_date=None` for the whole-period
    view the post-campaign analysis needs.

    The no-tracks count is taken from the SAME scoped frame: a team that
    reported yesterday but not today is a team with no evidence of tracks
    today, which is the finding the daily review is looking for.
    """
    tracks_teams, scoped_to = scope_tracks_to_date(tracks_teams, report_date)
    if scoped_to:
        print(f"  time spent scoped to tracks transmitted on {scoped_to}")
    field = tracks_teams[(tracks_teams["hour"] >= FIELD_START) &
                         (tracks_teams["hour"] < FIELD_END)]
    per_team = field.groupby(["lga", "team_code"])["pings"].sum().reset_index()
    per_team["minutes"] = per_team["pings"] * 2

    def classify(mins):
        if mins <= 0:
            return "0 (No evidence of tracks)"
        if mins < 12:
            return "<12 mins"
        if mins <= 30:
            return "12 - 30 mins"
        if mins <= 60:
            return "30 mins - 1 hr"
        if mins <= 120:
            return "1 - 2 hrs"
        return ">2 hrs"

    per_team["Time Spent"] = per_team["minutes"].apply(classify)

    # teams deployed but with no tracks at all
    team_col = next((c for c in dip.columns if "team" in c.lower() and "code" in c.lower()), None)
    n_no_tracks = 0
    if team_col:
        deployed_teams = set(dip[team_col].dropna().astype(str))
        reported_teams = set(tracks_teams["team_code"].astype(str))
        n_no_tracks = len(deployed_teams - reported_teams)

    summary = per_team.groupby("Time Spent").size().reindex(TIME_ORDER).fillna(0).astype(int)
    summary.loc["0 (No evidence of tracks)"] = n_no_tracks
    summary = summary.reset_index(name="Number of Teams")
    total = summary["Number of Teams"].sum()
    summary.loc[len(summary)] = ["Grand Total", total]
    return summary, per_team


def coverage_tables(dip: pd.DataFrame, cum_col: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """State totals, LGA coverage-class table, and LGA visited/not-visited table."""
    lga_col = next(c for c in dip.columns if "lga" in c.lower() and "code" not in c.lower())
    cov = dip.copy()
    cov["Settlement Coverage"] = cov["Settlement Coverage"].replace({"No Coverage": "No Coverage"})

    lga_cov = (cov.groupby([lga_col, "Settlement Coverage"]).size().unstack(fill_value=0)
               .reindex(columns=COVERAGE_ORDER, fill_value=0))
    lga_cov.index.name = "LGA"
    lga_cov.loc["Grand Total"] = lga_cov.sum()

    state_cov = lga_cov.loc[["Grand Total"]].T.rename(columns={"Grand Total": "Settlements"})

    visit = (cov.groupby([lga_col, cum_col]).size().unstack(fill_value=0))
    for c in ("Visited", "Not Visited"):
        if c not in visit.columns:
            visit[c] = 0
    visit = visit[["Visited", "Not Visited"]]
    visit["Total"] = visit.sum(axis=1)
    visit["% Visited"] = (visit["Visited"] / visit["Total"]).round(3)
    visit.index.name = "LGA"
    gt = visit.sum(numeric_only=True)
    gt["% Visited"] = round(gt["Visited"] / gt["Total"], 3) if gt["Total"] else 0
    visit.loc["Grand Total"] = gt
    return state_cov.reset_index(), lga_cov.reset_index(), visit.reset_index()


def build_workbook(visitation_csv: str, tracks_file: str, cum_col: str, day: int,
                   state: str, output_file: str, analysis_type: str = "daily",
                   teams_deployed_total: int | None = None) -> str:
    tracks_teams = load_tracks_teams(tracks_file)
    return build_workbook_from_agg(visitation_csv, tracks_teams, cum_col, day, state, output_file,
                                   analysis_type=analysis_type,
                                   teams_deployed_total=teams_deployed_total)


def build_workbook_from_agg(visitation_csv: str, tracks_teams: pd.DataFrame, cum_col: str,
                            day: int, state: str, output_file: str,
                            deploy_csv: str | None = None, time_csv: str | None = None,
                            flagged_csv: str | None = None,
                            analysis_type: str = "daily",
                            teams_deployed_total: int | None = None) -> str:
    """`analysis_type="daily"` (default) writes a "Daily ERM Analysis" sheet scoped
    to this reporting day — unchanged behaviour. `analysis_type="post_campaign"`
    writes a "Post-Campaign Analysis" sheet instead: pass the FINAL day's `cum_col`
    (the settlement list's cumulative status already carries prior days forward)
    and it adds campaign-level tables the daily view doesn't need — settlements
    with/without geo-coordinates, a not-visited-by-LGA ranking, and potentially
    missed children by LGA. Team Deploy-Report is shared by both modes and
    reflects whatever tracks were supplied. Time Spent Analysis is NOT: the
    daily mode scopes it to the latest transmission date in the export, so a
    multi-day merge cannot report the campaign to date as one day's field time,
    while post-campaign mode spans the whole period on purpose.
    """
    dip = pd.read_csv(visitation_csv, low_memory=False)
    if cum_col not in dip.columns:
        cum_col = f"day_{day}_cumm"

    # national exports include other states' teams — keep only this state's LGAs
    def norm_lga(s):
        return (s.astype(str).str.replace("_", " ", regex=False)
                .str.replace(r"\s+", " ", regex=True).str.strip().str.title())

    lga_col = next(c for c in dip.columns if "lga" in c.lower() and "code" not in c.lower())
    dip[lga_col] = norm_lga(dip[lga_col]).where(dip[lga_col].notna())
    state_lgas = set(dip[lga_col].dropna())
    tracks_teams = tracks_teams.copy()
    tracks_teams["lga"] = norm_lga(tracks_teams["lga"])
    tracks_teams = tracks_teams[tracks_teams["lga"].isin(state_lgas)]

    deploy = team_deploy_report(dip, tracks_teams, teams_deployed_total=teams_deployed_total)
    # Daily analysis reports the day the tracks were transmitted, so time spent
    # is scoped to the latest date in the export. The post-campaign analysis
    # deliberately spans the whole period and passes None.
    time_summary, per_team = time_spent_analysis(
        tracks_teams, dip,
        report_date=None if analysis_type == "post_campaign" else "latest")

    # Team performance / time-spent-range breakdown. A second, finer view of
    # time spent (State -> LGA -> Ward -> team, from the settlement analysis'
    # own ping counts) that sits alongside the 08:00-15:00 team measure above
    # rather than replacing it — see team_time_range's module docstring for why
    # both exist. Never fatal: an older visitation CSV missing the columns it
    # needs costs the extra tabs, not the workbook.
    ttr = None
    try:
        from team_time_range import analyse as analyse_team_time_range
        ttr = analyse_team_time_range(dip, state)
        print(f"Team time-range analysis: {ttr['meta']['total_teams']:,} teams, "
              f"{ttr['meta']['teams_under_12']:,} under 12 mins "
              f"({ttr['meta']['under_12_pct']:.1%}) — minutes from "
              f"{ttr['meta']['minutes_from']}")
    except Exception as exc:
        print(f"Team time-range analysis skipped ({exc})")

    # Estimated <5 target population and household coverage, from the planned
    # settlement document's own per-settlement estimates. Optional in the same
    # way: a settlement list carrying neither field costs this tab only.
    tpc = None
    try:
        from target_population import analyse_daily_and_cumulative, describe
        daily_col = f"day_{day}_daily"
        tpc = analyse_daily_and_cumulative(dip, daily_col, cum_col)
        if tpc["daily"]:
            print(f"Target population DAILY ({daily_col}): {describe(tpc['daily'])}")
        print(f"Target population CUMULATIVE ({cum_col}): {describe(tpc['cumulative'])}")
    except Exception as exc:
        print(f"Target population analysis skipped ({exc})")

    # Team Time Efficiency — field minutes against grid-weighted coverage, and
    # the >2hr / <50% follow-up list. Optional like the others: an older
    # visitation CSV without the grid-cell columns costs these tabs only.
    teff = None
    try:
        from team_efficiency import analyse as analyse_team_efficiency, describe as _te_desc
        teff = analyse_team_efficiency(dip, "cumulative")
        print(f"Team time efficiency: {_te_desc(teff)}")
    except Exception as exc:
        print(f"Team efficiency analysis skipped ({exc})")
    state_cov, lga_cov, visit = coverage_tables(dip, cum_col)
    is_pc = analysis_type == "post_campaign"
    sheet_name = "Post-Campaign Analysis" if is_pc else "Daily ERM Analysis"
    label = "Post-Campaign" if is_pc else f"Day {day}"
    if is_pc:
        coords_tbl = coordinates_table(dip)
        ranked_nv = not_visited_ranked_table(dip, cum_col)
        missed_tbl = missed_children_table(dip, cum_col)
    if deploy_csv:
        deploy.to_csv(deploy_csv, index=False)
    # The flagged-team follow-up list as a standalone file, so it can be sent
    # to LGA supervisors without circulating the whole workbook. Same rows as
    # the workbook's "Flagged Teams" tab, from the same analysis.
    if flagged_csv and teff is not None and "flagged_table" in teff:
        teff["flagged_table"].to_csv(flagged_csv, index=False)
        print(f"  flagged teams -> {flagged_csv} "
              f"({len(teff['flagged_table']):,} rows)")
    if time_csv:
        time_summary.to_csv(time_csv, index=False)

    with pd.ExcelWriter(output_file, engine="xlsxwriter") as xl:
        wb = xl.book
        hdr = wb.add_format({"bold": True, "bg_color": "#4472C4", "font_color": "white",
                             "border": 1})
        title_fmt = wb.add_format({"bold": True, "font_size": 14})
        pct = wb.add_format({"num_format": "0.0%"})

        def write_table(ws_name, df, startrow, title):
            df.to_excel(xl, sheet_name=ws_name, startrow=startrow + 1, index=False)
            ws = xl.sheets[ws_name]
            ws.write(startrow - 1 if startrow else 0, 0, title, title_fmt)
            for c, col in enumerate(df.columns):
                ws.write(startrow + 1, c, col, hdr)
                ws.set_column(c, c, max(14, len(str(col)) + 2))
            return ws

        # --- Team Deploy Report
        ws = write_table("Team Deploy-Report", deploy, 2, f"Team Deployment — {label}")
        chart = wb.add_chart({"type": "column"})
        n = len(deploy) - 1
        for i, series in enumerate(["Teams Deployed", "Teams Reported", "Teams Pending"]):
            chart.add_series({
                "name": series,
                "categories": ["Team Deploy-Report", 4, 0, 3 + n, 0],
                "values": ["Team Deploy-Report", 4, 1 + i, 3 + n, 1 + i],
            })
        chart.set_title({"name": f"Team Deployment vs Reported — {label}"})
        chart.set_size({"width": 720, "height": 380})
        ws.insert_chart(len(deploy) + 6, 0, chart)

        # --- Time Spent Analysis
        ws = write_table("Time Spent Analysis", time_summary, 2, f"Time Spent in Field (8am–3pm) — {label}")
        chart = wb.add_chart({"type": "column"})
        chart.add_series({
            "name": "Number of Teams",
            "categories": ["Time Spent Analysis", 4, 0, 3 + len(time_summary) - 1, 0],
            "values": ["Time Spent Analysis", 4, 1, 3 + len(time_summary) - 1, 1],
            "data_labels": {"value": True},
        })
        chart.set_title({"name": f"Time Spent Analysis — {label}"})
        chart.set_legend({"none": True})
        chart.set_size({"width": 720, "height": 380})
        ws.insert_chart(len(time_summary) + 6, 0, chart)
        per_team.to_excel(xl, sheet_name="Time Spent Data", index=False)

        # --- Team performance / time-spent range (State / LGA / Ward)
        if ttr is not None:
            state_dist = ttr["state_summary"].drop(columns=["State"], errors="ignore")
            ws = write_table("Team Range (State)", state_dist, 2,
                             f"Teams by Time Spent Range, across assigned settlements — {label}")
            if len(state_dist):
                chart = wb.add_chart({"type": "column"})
                chart.add_series({
                    "name": "Teams",
                    "categories": ["Team Range (State)", 4, 0, 3 + len(state_dist) - 1, 0],
                    "values": ["Team Range (State)", 4, 1, 3 + len(state_dist) - 1, 1],
                    "data_labels": {"value": True},
                })
                chart.set_title({"name": f"Teams by Time Spent Range — {label}"})
                chart.set_legend({"none": True})
                chart.set_size({"width": 720, "height": 380})
                ws.insert_chart(len(state_dist) + 6, 0, chart)

            write_table("Team Range (LGA)", ttr["lga_summary"], 2,
                        f"Teams by Time Spent Range per LGA — {label}")
            write_table("Team Range (Ward)", ttr["ward_summary"], 2,
                        f"Teams by Time Spent Range per Ward — {label}")

            u12 = ttr["under_12"]
            ws = write_table("Teams Under 12 Mins", u12, 2,
                             f"Teams spending under 12 minutes across their assigned "
                             f"settlements — {label}")
            if not len(u12):
                ws.write(4, 0, "No team fell below 12 minutes.")
            # the team-code column carries long lists — give it room
            if len(u12.columns):
                ws.set_column(len(u12.columns) - 1, len(u12.columns) - 1, 60)

            ttr["per_team_ward"].to_excel(xl, sheet_name="Team Range Data", index=False)

        # --- Team Time Efficiency
        if teff is not None and len(teff["per_team"]):
            m = teff["meta"]
            sheet = "Team Time Efficiency"
            ws = write_table(sheet, teff["summary"], 2,
                             f"Team Time Efficiency — {label}")
            n = len(teff["summary"])
            for c, col in enumerate(teff["summary"].columns):
                if col == "Share":
                    ws.set_column(c, c, 12, pct)
            note = n + 5
            ws.write(note, 0,
                     f"Flag = more than {m['long_field_minutes']} minutes in the field "
                     f"AND under {m['low_coverage']:.0%} gridded area coverage. "
                     f"Minutes = track pings x {m['ping_minutes']}. "
                     f"Coverage = {m['coverage_basis']}.")
            ws.write(note + 1, 0,
                     f"{m['flagged_teams']:,} of {m['total_teams']:,} teams flagged "
                     f"({m['flagged_pct']:.1%}).")

            # EVERY flagged team, both reasons in one list — the sheet an LGA
            # supervisor works from. A team meeting both rules appears once
            # with a combined reason, so the row count is the number of teams
            # to follow up, which is what the slide narration quotes.
            fl = teff["flagged_table"]
            ws2 = write_table("Flagged Teams", fl, 2,
                              f"Teams flagged for supervisory follow-up — {label}")
            if not len(fl):
                ws2.write(4, 0, "No team met either flag criterion.")
            else:
                widths = {"Team": 16, "LGA": 18, "Time Spent (hrs)": 16,
                          "% Grid Covered": 15, "Flag/Reason": 42,
                          "Cells Visited": 13, "Grid Cells": 12,
                          "Settlements": 12}
                for c, col in enumerate(fl.columns):
                    ws2.set_column(c, c, widths.get(col, 14))
                ws2.write(len(fl) + 4, 0,
                          f"{m['flagged_any']:,} teams flagged in total: "
                          f"{m['flagged_teams']:,} over "
                          f"{m['long_field_minutes'] // 60} hrs with under "
                          f"{m['low_coverage']:.0%} coverage, "
                          f"{m['stationary_teams']:,} stationary "
                          f"(<= {m['stationary_max_cells']} grid cell). "
                          f"A team meeting both is listed once.")
            if len(teff["matrix"]):
                write_table("Efficiency by LGA", teff["matrix"], 2,
                            f"Teams per efficiency quadrant, by LGA — {label}")
            teff["per_team"].to_excel(xl, sheet_name="Team Efficiency Data", index=False)

        # --- Target Population & Household Coverage
        # Daily first, cumulative underneath as context. Never merged: daily is
        # the reporting day alone, cumulative is Day 1 to date, and a single
        # combined figure would be meaningless.
        if tpc is not None:
            sheet = "Target Pop & HH Coverage"
            blocks = []
            if tpc["daily"] is not None and len(tpc["daily"]["summary"]):
                blocks.append((f"DAILY — {label} only", tpc["daily"], ""))
            if len(tpc["cumulative"]["summary"]):
                blocks.append((f"CUMULATIVE — Day 1 to {label}", tpc["cumulative"], "_cum"))

            row = 2
            for heading, res, _suffix in blocks:
                summary = res["summary"]
                n = len(summary)
                ws = write_table(sheet, summary, row, heading)
                for c, col in enumerate(summary.columns):
                    if "%" in str(col):
                        ws.set_column(c, c, 16, pct)
                s = res["settlements"]
                note_row = row + n + 3
                ws.write(note_row, 0,
                         f"Based on {s['visited']:,} of {s['planned']:,} planned "
                         f"settlements visited ({s['pct']:.1%}). A settlement's full "
                         f"estimate counts as reached if the settlement was visited — "
                         f"an estimate of exposure, not of children vaccinated.")

                chart = wb.add_chart({"type": "column", "subtype": "percent_stacked"})
                for i, series in enumerate(["Reached (Visited)", "Not Reached (Not Visited)"]):
                    if series not in summary.columns:
                        continue
                    col_i = list(summary.columns).index(series)
                    chart.add_series({
                        "name": series,
                        "categories": [sheet, row + 2, 0, row + 1 + n, 0],
                        "values": [sheet, row + 2, col_i, row + 1 + n, col_i],
                        "fill": {"color": "#1e6b30" if i == 0 else "#e79a96"},
                    })
                chart.set_title({"name": heading})
                chart.set_size({"width": 560, "height": 300})
                ws.insert_chart(note_row + 2, 0, chart)

                by_lga_start = note_row + 18
                if len(res["by_lga"]):
                    res["by_lga"].to_excel(xl, sheet_name=sheet,
                                           startrow=by_lga_start + 1, index=False)
                    ws.write(by_lga_start - 1, 0, f"By LGA — {heading}", title_fmt)
                    for c, col in enumerate(res["by_lga"].columns):
                        ws.write(by_lga_start + 1, c, col, hdr)
                        ws.set_column(c, c, max(14, len(str(col)) + 2),
                                      pct if "%" in str(col) else None)
                    row = by_lga_start + len(res["by_lga"]) + 5
                else:
                    row = by_lga_start + 3

        # --- Daily / Post-Campaign ERM Analysis
        cov_title = (f"State Cumulative Settlement Coverage — {label}" if is_pc
                    else f"State Daily Settlement Coverage — {label}")
        ws = write_table(sheet_name, state_cov, 2, cov_title)
        pie = wb.add_chart({"type": "pie"})
        pie.add_series({
            "name": f"Settlement Coverage {label}",
            "categories": [sheet_name, 4, 0, 3 + len(state_cov), 0],
            "values": [sheet_name, 4, 1, 3 + len(state_cov), 1],
            "data_labels": {"percentage": True},
            "points": [{"fill": {"color": c}} for c in
                       ["#2E7D32", "#7CB342", "#FDD835", "#FB8C00", "#C62828"]],
        })
        pie.set_title({"name": f"State Settlement Coverage — {label}"})
        pie.set_size({"width": 480, "height": 320})
        ws.insert_chart(1, 4, pie)

        r0 = len(state_cov) + 8
        lga_cov.to_excel(xl, sheet_name=sheet_name, startrow=r0 + 1, index=False)
        ws.write(r0, 0, "LGA Settlements Visitation Coverage", title_fmt)
        for c, col in enumerate(lga_cov.columns):
            ws.write(r0 + 1, c, col, hdr)
        bar = wb.add_chart({"type": "column", "subtype": "stacked"})
        n = len(lga_cov) - 1
        colors = ["#2E7D32", "#7CB342", "#FDD835", "#FB8C00", "#C62828"]
        for i, series in enumerate(COVERAGE_ORDER):
            bar.add_series({
                "name": series,
                "categories": [sheet_name, r0 + 2, 0, r0 + 1 + n, 0],
                "values": [sheet_name, r0 + 2, 1 + i, r0 + 1 + n, 1 + i],
                "fill": {"color": colors[i]},
            })
        bar.set_title({"name": f"LGA Settlement Coverage — {label}"})
        bar.set_size({"width": 780, "height": 400})
        ws.insert_chart(r0 + n + 5, 0, bar)

        r1 = r0 + n + 27
        visit.to_excel(xl, sheet_name=sheet_name, startrow=r1 + 1, index=False)
        ws.write(r1, 0, "LGA Visitation (Visited vs Not Visited)", title_fmt)
        for c, col in enumerate(visit.columns):
            ws.write(r1 + 1, c, col, hdr)
        ws.set_column(4, 4, 12, pct)

        if is_pc:
            r2 = r1 + len(visit) + 8
            coords_tbl.to_excel(xl, sheet_name=sheet_name, startrow=r2 + 1, index=False)
            ws.write(r2, 0, "Settlements With vs Without Geo-Coordinates", title_fmt)
            for c, col in enumerate(coords_tbl.columns):
                ws.write(r2 + 1, c, col, hdr)

            r3 = r2 + len(coords_tbl) + 4
            ranked_nv.to_excel(xl, sheet_name=sheet_name, startrow=r3 + 1, index=False)
            ws.write(r3, 0, "Not Visited Settlements by LGA (ranked)", title_fmt)
            for c, col in enumerate(ranked_nv.columns):
                ws.write(r3 + 1, c, col, hdr)

            r4 = r3 + len(ranked_nv) + 4
            missed_tbl.to_excel(xl, sheet_name=sheet_name, startrow=r4 + 1, index=False)
            ws.write(r4, 0, "Potentially Missed Children by LGA", title_fmt)
            for c, col in enumerate(missed_tbl.columns):
                ws.write(r4 + 1, c, col, hdr)
            if len(missed_tbl) > 1:
                n_mc = len(missed_tbl) - 1
                mc_chart = wb.add_chart({"type": "bar"})
                mc_chart.add_series({
                    "name": "Missed Children",
                    "categories": [sheet_name, r4 + 2, 0, r4 + 1 + n_mc, 0],
                    "values": [sheet_name, r4 + 2, 1, r4 + 1 + n_mc, 1],
                    "fill": {"color": "#C62828"},
                })
                mc_chart.set_title({"name": "Potentially Missed Children by LGA"})
                mc_chart.set_size({"width": 780, "height": 420})
                ws.insert_chart(r4 + n_mc + 5, 0, mc_chart)

    print(f"Saved {output_file}")
    return output_file


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Generate Daily ERM Analysis workbook")
    ap.add_argument("--visitation-csv", required=True)
    ap.add_argument("--tracks", required=True, help="merged tracks parquet/csv")
    ap.add_argument("--day", type=int, required=True)
    ap.add_argument("--state", required=True)
    ap.add_argument("--cum-col", default=None)
    ap.add_argument("--output", required=True)
    ap.add_argument("--analysis-type", choices=["daily", "post_campaign"], default="daily")
    ap.add_argument("--teams-deployed", type=int, default=None,
                    help="Manually-entered total teams deployed (overrides the DIP-derived count)")
    a = ap.parse_args()
    build_workbook(a.visitation_csv, a.tracks, a.cum_col or f"day_{a.day}_cumm",
                   a.day, a.state, a.output, analysis_type=a.analysis_type,
                   teams_deployed_total=a.teams_deployed)
