"""Checkpointed pipeline runner — resumable execution of the daily GTS pipeline.

Runs the same work as run_pipeline.py but in small resumable steps, saving
progress to a checkpoint folder. Invoke repeatedly until it prints DONE.
Useful in time-limited environments and crash recovery on long campaign days.

Usage (same args as run_pipeline.py, plus --ckpt):
    python checkpoint_runner.py --tracks-folder ... --settlements ... \
        --gridded-ta ... --voronoi ... --lga-boundaries ... \
        --state Zamfara --day 1 --output DayN_out --ckpt DayN_out/.ckpt
"""
import argparse
import json
import os
import pickle
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd

TIME_BUDGET = 27  # seconds of work per invocation


def load_state(ckpt):
    p = os.path.join(ckpt, "state.json")
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return {"steps": {}, "scan_chunk": 0}


def save_state(ckpt, state):
    with open(os.path.join(ckpt, "state.json"), "w") as f:
        json.dump(state, f)


def pkl_save(ckpt, name, obj):
    with open(os.path.join(ckpt, name), "wb") as f:
        pickle.dump(obj, f, protocol=4)


def pkl_load(ckpt, name):
    with open(os.path.join(ckpt, name), "rb") as f:
        return pickle.load(f)


def step_merge(args, state, t0):
    """Resumable merge: appends one source CSV at a time."""
    import csv as csvmod
    import glob
    from stage1_merge import fix_row

    out = os.path.join(args.output, "merged_tracks.csv")
    done_files = set(state.get("merged_files", []))
    csv_files = sorted(glob.glob(os.path.join(args.tracks_folder, "Tracks_*.csv")))
    if not csv_files:
        sys.exit(f"No Tracks_*.csv in {args.tracks_folder}")

    mode = "a" if done_files else "w"
    with open(out, mode, encoding="utf-8", newline="") as fout:
        writer = csvmod.writer(fout)
        wrote_header = bool(done_files)
        for csv_file in csv_files:
            base = os.path.basename(csv_file)
            if base in done_files:
                continue
            with open(csv_file, "r", encoding="utf-8", errors="ignore") as f:
                header = f.readline().rstrip("\n").split(",")
                n = len(header)
                lat_i, lon_i = header.index("Lat"), header.index("Lon")
                if not wrote_header:
                    writer.writerow(header + ["source_file"])
                    wrote_header = True
                kept = 0
                for line in f:
                    row = fix_row(line.rstrip("\n").split(","), n)
                    if row is None:
                        continue
                    try:
                        lat = float(row[lat_i]); lon = float(row[lon_i])
                    except (ValueError, IndexError):
                        continue
                    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
                        continue
                    writer.writerow(row + [base])
                    kept += 1
            done_files.add(base)
            state["merged_files"] = sorted(done_files)
            print(f"merged {base}: {kept:,} points")
            if time.time() - t0 > TIME_BUDGET:
                break

    if len(done_files) == len(csv_files):
        state["steps"]["merge"] = "done"
        return True
    print(f"merge progress: {len(done_files)}/{len(csv_files)}")
    return False


def step_scan(args, state, t0):
    """One pass over merged CSV -> coord weights + (lga, team, hour) pings."""
    ckpt = args.ckpt
    merged = os.path.join(args.output, "merged_tracks.csv")
    chunksize = 400_000
    chunk_no = state.get("scan_chunk", 0)

    coord = pkl_load(ckpt, "coords.pkl") if chunk_no else None
    teams = pkl_load(ckpt, "teams.pkl") if chunk_no else None

    # Coordinate weights become `track_count`, and every per-team minutes
    # figure is derived from it — so on a daily run they must cover the
    # reporting day alone. The date is resolved once and stored in the run
    # state, so a resumed scan keeps filtering to the same day rather than
    # re-resolving against a partially-read file.
    track_date = state.get("track_date", "__unset__")
    if track_date == "__unset__":
        from stage2_analysis import latest_track_date
        track_date = (None if args.analysis_type == "post_campaign"
                      else latest_track_date(merged))
        state["track_date"] = track_date
        print(f"scan scoped to transmission date: {track_date or 'all dates'}")

    usecols = ["Lat", "Lon", "NGA LGA 2024 Label", "Team Code", "GPS Timestamp (UTC)"]
    reader = pd.read_csv(merged, usecols=usecols, chunksize=chunksize,
                         dtype={"NGA LGA 2024 Label": str, "Team Code": str,
                                "GPS Timestamp (UTC)": str},
                         skiprows=range(1, chunk_no * chunksize + 1))
    done = True
    for chunk in reader:
        if track_date:
            local_all = (pd.to_datetime(chunk["GPS Timestamp (UTC)"],
                                        format="%m/%d/%Y %H:%M:%S", errors="coerce")
                         + pd.Timedelta(hours=1))
            chunk = chunk[local_all.dt.strftime("%Y-%m-%d") == track_date]
            if not len(chunk):
                chunk_no += 1
                continue
        c = chunk.groupby(["Lon", "Lat"]).size().rename("weight").reset_index()
        coord = c if coord is None else (
            pd.concat([coord, c]).groupby(["Lon", "Lat"], as_index=False)["weight"].sum())

        t = chunk.rename(columns={"NGA LGA 2024 Label": "lga", "Team Code": "team_code",
                                  "GPS Timestamp (UTC)": "ts"}).dropna(subset=["team_code"])
        ts = pd.to_datetime(t["ts"], format="%m/%d/%Y %H:%M:%S", errors="coerce")
        local = ts + pd.Timedelta(hours=1)  # UTC -> UTC+1
        # `date` matches stage3.load_tracks_teams so time spent can be scoped to
        # the day the tracks were transmitted in both runners
        t["date"] = local.dt.strftime("%Y-%m-%d")
        t["hour"] = local.dt.hour
        keys = ["lga", "team_code", "date", "hour"]
        g = t.groupby(keys, dropna=False).size().rename("pings").reset_index()
        teams = g if teams is None else (
            pd.concat([teams, g]).groupby(keys, as_index=False,
                                          dropna=False)["pings"].sum())
        chunk_no += 1
        if time.time() - t0 > TIME_BUDGET:
            done = False
            break

    pkl_save(ckpt, "coords.pkl", coord)
    pkl_save(ckpt, "teams.pkl", teams)
    state["scan_chunk"] = chunk_no
    if done:
        state["steps"]["scan"] = "done"
        print(f"scan complete: {len(coord):,} unique coords, {len(teams):,} team-hour rows")
    else:
        print(f"scan progress: {chunk_no} chunks")
    return done


def step_prep_ta(args, state, t0):
    from stage2_analysis import read_spatial, construct_unique
    ta = read_spatial(args.gridded_ta)
    ta = construct_unique(ta, "unique")
    pkl_save(args.ckpt, "ta.pkl", ta)
    state["steps"]["prep_ta"] = "done"
    print(f"gridded TA ready: {len(ta):,} cells")
    return True


def step_prep_vor(args, state, t0):
    from stage2_analysis import read_spatial, construct_unique
    vor = read_spatial(args.voronoi)
    vor = construct_unique(vor, "unique")
    pkl_save(args.ckpt, "vor.pkl", vor)
    state["steps"]["prep_vor"] = "done"
    print(f"voronoi ready: {len(vor):,} extents")
    return True


def step_join(args, state, t0):
    import geopandas as gpd
    from stage2_analysis import (find_and_update_visited_grids, generate_ta_cumulative_summary,
                                 calculate_coverage)
    ckpt = args.ckpt
    coord = pkl_load(ckpt, "coords.pkl")
    ta = pkl_load(ckpt, "ta.pkl")
    vor = pkl_load(ckpt, "vor.pkl")

    pts = gpd.GeoDataFrame(coord, geometry=gpd.points_from_xy(coord["Lon"], coord["Lat"]),
                           crs="EPSG:4326")
    if ta.crs is not None and pts.crs != ta.crs:
        pts = pts.to_crs(ta.crs)

    # clip to voronoi extents & per-settlement ping counts
    hit = pts.sjoin(vor[["unique", "geometry"]], how="inner", predicate="within")
    clipped = hit.drop_duplicates(subset=["Lon", "Lat"])
    print(f"{len(clipped):,} unique points within settlement extents")
    track_counts = (hit.groupby("unique")["weight"].sum().reset_index()
                    .rename(columns={"weight": "track_count"}))

    # carry over previous day's grid visitation
    if args.prev_ta and os.path.exists(args.prev_ta):
        prev = pd.read_pickle(args.prev_ta) if args.prev_ta.endswith(".pkl") else None
        if prev is None:
            from stage2_analysis import read_spatial
            prev = read_spatial(args.prev_ta)
        if "rowid" not in ta.columns:
            ta["rowid"] = ta.index + 1
        ta = ta.merge(prev[["rowid", "visitation"]].drop_duplicates("rowid"),
                      on="rowid", how="left")

    ta_updated = find_and_update_visited_grids(clipped[["geometry"]], ta)
    summary = calculate_coverage(generate_ta_cumulative_summary(ta_updated, "unique"))
    # today-only per-settlement coverage, kept separate from the cumulative
    # summary above so daily reporting never reads prior days' work
    from stage2_analysis import generate_ta_daily_summary
    daily_summary = generate_ta_daily_summary(ta_updated, "unique")
    summary = (summary.merge(track_counts, on="unique", how="left")
               .fillna({"track_count": 0}).set_index("unique"))

    pkl_save(ckpt, "summary.pkl", summary)
    pkl_save(ckpt, "daily_summary.pkl", daily_summary)
    pkl_save(ckpt, "ta_updated.pkl", ta_updated)
    state["steps"]["join"] = "done"
    n_vis = int((summary["visitation"] == "Visited").sum())
    print(f"join complete: {n_vis:,} of {len(summary):,} settlements visited")
    return True


def step_dip(args, state, t0):
    import geopandas as gpd
    from stage2_analysis import (find_col, construct_unique, set_cumulative_visitation,
                                 detect_day_column, get_activity_day_col,
                                 classify_coverage, classify_time_spent)
    ckpt = args.ckpt
    summary = pkl_load(ckpt, "summary.pkl")

    dip_source = args.prev_dip if args.prev_dip else args.settlements
    dip = pd.read_csv(dip_source, low_memory=False)
    state_col = find_col(dip, "state")
    if state_col and dip[state_col].nunique() > 1:
        dip = dip[dip[state_col].astype(str).str.strip().str.title()
                  == args.state.title()].copy()
        print(f"Filtered DIP to {args.state}: {len(dip):,} settlements")
    dip = construct_unique(dip, "unique")

    if "Coverage" in dip.columns:
        dip = dip.rename(columns={"Coverage": "Prev_Coverage"})
    else:
        dip["Prev_Coverage"] = np.nan

    # `Track Evidence` is cumulative, so the previous day's value has to survive
    # into today's calculation rather than being recomputed from today's tracks
    # alone — held aside here and folded back in below, as run_analysis does.
    if "Track Evidence" in dip.columns:
        dip["Prev_Track_Evidence"] = (dip["Track Evidence"].astype(str)
                                      .str.strip().str.lower() == "yes")
        dip = dip.drop(columns=["Track Evidence"])
    else:
        dip["Prev_Track_Evidence"] = False

    # See the matching comment in stage2_analysis.run_analysis: from day 2 the
    # DIP is the previous day's visitation CSV and already carries these, which
    # would make pandas suffix both sides and blank out Time Spent.
    stale = [c for c in ("track_count", "visitation", "Daily Coverage",
                         "daily_visitation", "Daily Settlement Coverage",
                         "Grid Cells Visited", "Grid Cells", "Daily Cells Visited")
             if c in dip.columns]
    if stale:
        print(f"  dropping previous day's {', '.join(stale)} — recomputed below")
        dip = dip.drop(columns=stale)

    get_activity_day_col(dip, args.day)
    cell_counts = summary.rename(columns={"Visited": "Grid Cells Visited",
                                          "Total": "Grid Cells"})
    merge_cols = [c for c in ("visitation", "Coverage", "track_count",
                              "Grid Cells Visited", "Grid Cells")
                  if c in cell_counts.columns]
    dip = dip.merge(cell_counts[merge_cols], left_on="unique",
                    right_index=True, how="left")
    dip = dip.merge(pkl_load(ckpt, "daily_summary.pkl"), left_on="unique",
                    right_index=True, how="left")
    both = dip[["Coverage", "Prev_Coverage"]].astype(float)
    dip["Coverage"] = both.max(axis=1)

    # Cumulative evidence of tracks — see stage2_analysis.run_analysis for why
    # both sources are needed and why the rule is applied here rather than by
    # each consumer.
    def _positive(col):
        return (pd.to_numeric(dip[col], errors="coerce").fillna(0) > 0
                if col in dip.columns else pd.Series(False, index=dip.index))

    cum_evidence = (_positive("Grid Cells Visited") | _positive("track_count")
                    | dip["Prev_Track_Evidence"].fillna(False).astype(bool))
    dip["Track Evidence"] = np.where(cum_evidence, "Yes", "No")

    dip = set_cumulative_visitation(dip, args.day)
    cum_col = f"day_{args.day}_cumm"
    dip[cum_col] = dip[cum_col].replace({"Not Yet Visited": "Not Visited"})
    n_by_evidence = int((cum_evidence & (dip[cum_col] != "Visited")).sum())
    dip.loc[cum_evidence & dip[cum_col].notna(), cum_col] = "Visited"
    if n_by_evidence:
        print(f"  {n_by_evidence:,} settlements counted as cumulatively visited "
              f"on track evidence alone")

    # DAILY status alongside the cumulative one — see stage2_analysis
    daily_col = f"day_{args.day}_daily"
    dip[daily_col] = dip["daily_visitation"].where(dip[cum_col].notna())
    dip[daily_col] = dip[daily_col].fillna(
        pd.Series("Not Visited", index=dip.index).where(dip[cum_col].notna()))

    dip["Settlement Coverage"] = dip.apply(classify_coverage, axis=1)
    dip["Daily Settlement Coverage"] = dip.apply(
        lambda r: classify_coverage({"Coverage": r.get("Daily Coverage")}), axis=1)
    dip["Time Spent"] = dip.apply(classify_time_spent, axis=1)
    dip.drop(columns=["Prev_Coverage", "Prev_Track_Evidence", "visitation",
                      "daily_visitation", "is_cumm",
                      "Total", "Visited", "Not Yet Visited"],
             inplace=True, errors="ignore")

    out_csv = os.path.join(args.output, f"{args.state}_day_{args.day}_settlement_visitation.csv")
    dip.to_csv(out_csv, index=False)
    print(f"Saved {out_csv}")

    # map-ready layers
    lat = find_col(dip, "latitude")
    lon = find_col(dip, "longitude")
    if lat and lon:
        pts = dip.copy()
        pts[lat] = pd.to_numeric(pts[lat], errors="coerce")
        pts[lon] = pd.to_numeric(pts[lon], errors="coerce")
        pts = pts.dropna(subset=[lat, lon])
        g = gpd.GeoDataFrame(pts, geometry=gpd.points_from_xy(pts[lon], pts[lat]), crs="EPSG:4326")
        for status, name in [("Visited", "visited"), ("Not Visited", "not_visited")]:
            sub = g[g[cum_col] == status]
            if len(sub):
                sub.to_file(os.path.join(args.output, f"{name}_day_{args.day}.geojson"),
                            driver="GeoJSON")

    # persist updated TA for next day's cumulative run
    pkl_save(ckpt, "ta_final.pkl", pkl_load(ckpt, "ta_updated.pkl"))
    state["steps"]["dip"] = "done"
    return True


def step_erm(args, state, t0):
    from stage3_erm_workbook import build_workbook_from_agg
    ckpt = args.ckpt
    teams = pkl_load(ckpt, "teams.pkl")
    out_csv = os.path.join(args.output, f"{args.state}_day_{args.day}_settlement_visitation.csv")
    is_pc = args.analysis_type == "post_campaign"
    wb = os.path.join(args.output, f"{args.state}_PostCampaign_ERM_Analysis.xlsx") if is_pc \
        else os.path.join(args.output, f"{args.state}_Day_{args.day}_Vaccination_Tracking_ERM_Analysis.xlsx")
    build_workbook_from_agg(out_csv, teams, f"day_{args.day}_cumm", args.day, args.state, wb,
                            deploy_csv=os.path.join(args.output, f"team_deploy_day_{args.day}.csv"),
                            time_csv=os.path.join(args.output, f"time_spent_day_{args.day}.csv"),
                            flagged_csv=os.path.join(args.output, f"flagged_teams_day_{args.day}.csv"),
                            analysis_type=args.analysis_type,
                            teams_deployed_total=args.teams_deployed)
    state["steps"]["erm"] = "done"
    return True


def step_charts(args, state, t0):
    from stage_charts import generate_charts
    charts_dir = os.path.join(args.output, "charts")
    out_csv = os.path.join(args.output, f"{args.state}_day_{args.day}_settlement_visitation.csv")
    generate_charts(out_csv, f"day_{args.day}_cumm",
                    os.path.join(args.output, f"team_deploy_day_{args.day}.csv"),
                    os.path.join(args.output, f"time_spent_day_{args.day}.csv"),
                    args.day, charts_dir, analysis_type=args.analysis_type,
                    daily_col=f"day_{args.day}_daily",
                    track_date=state.get("track_date"),
                    tracks_file=os.path.join(args.output, "merged_tracks.csv"))
    state["steps"]["charts"] = "done"
    return True


def _dual_map_inputs(args, lga_spec):
    """Load the polygon layers the side-by-side LGA maps need.

    Returns (extents, grids) or (None, None) when the layout is single-map or
    either layer is unavailable — stage 4's own fallback rules, applied here so
    the checkpoint runner behaves identically to run_pipeline.

    The gridded TA comes from this run's checkpoint rather than a file on disk,
    because that is where the checkpoint runner keeps it.
    """
    from stage4_maps import settlement_extents_gdf, gridded_ta_gdf, _status_from_visitation
    if lga_spec is None or not lga_spec.is_multi_map:
        return None, None

    out_csv = os.path.join(args.output, f"{args.state}_day_{args.day}_settlement_visitation.csv")
    extents = settlement_extents_gdf(getattr(args, "voronoi", None), out_csv,
                                     f"day_{args.day}_cumm")
    grids = None
    try:
        ta = pkl_load(args.ckpt, "ta_final.pkl")
        col = next((c for c in ("visitation", "VIS_STAT", "cumm") if c in ta.columns), None)
        if col is not None:
            grids = ta.copy()
            grids["status"] = _status_from_visitation(grids[col])
            if "rowid" in grids.columns:
                grids = grids.sort_values("status").drop_duplicates("rowid", keep="first")
            print(f"  gridded TA: {len(grids):,} cells "
                  f"({int((grids['status'] == 'Visited').sum()):,} visited)")
    except Exception as exc:
        print(f"  gridded TA not available from the checkpoint ({exc})")
    if grids is None:
        # a resumed run whose checkpoint predates this step can still use the
        # file stage 2 writes, if one is lying next to the outputs
        for name in (f"gridded_ta_day_{args.day}.parquet", f"gridded_ta_day_{args.day}.gpkg"):
            grids = gridded_ta_gdf(os.path.join(args.output, name))
            if grids is not None:
                break

    if extents is None or grids is None:
        print("  LGA maps fall back to the single-panel settlement-point layout")
        return None, None
    return extents, grids


def step_maps(args, state, t0):
    from stage4_maps import (load_boundaries, load_wards, settlements_gdf,
                             render_map_figure, render_dual_lga_figure, safe_name,
                             set_template_dirs, set_dpi_override, template_for,
                             _figure_dpi, _subset_for_lga)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import geopandas as gpd

    if getattr(args, "map_template_dir", None):
        set_template_dirs(args.map_template_dir)
    set_dpi_override(getattr(args, "map_dpi", None))
    statewide_spec = template_for("statewide")
    lga_spec = template_for("lga")

    maps_dir = os.path.join(args.output, "maps")
    os.makedirs(maps_dir, exist_ok=True)
    done_maps = set(state.get("maps_done", []))

    out_csv = os.path.join(args.output, f"{args.state}_day_{args.day}_settlement_visitation.csv")
    points = settlements_gdf(out_csv, f"day_{args.day}_cumm")
    _pl = next((c for c in points.columns if "lga" in c.lower() and "code" not in c.lower()), None)
    boundaries = load_boundaries(args.lga_boundaries, args.state,
                                 points[_pl].dropna().unique().tolist() if _pl else None)
    wards = load_wards(args.wards, args.state) if args.wards else None
    lga_name_col = next((c for c in boundaries.columns
                         if "lga" in c.lower() and "code" not in c.lower()), None) or \
        next((c for c in boundaries.columns if c.lower() in ("name", "lganame")), boundaries.columns[0])
    pts_lga_col = _pl
    ward_name_col = ward_lga_col = None
    if wards is not None:
        ward_name_col = next((c for c in wards.columns if "ward" in c.lower()
                              and "code" not in c.lower()), None)
        ward_lga_col = next((c for c in wards.columns if c.lower() == "lga"), None)

    jobs = ["__impl__", "__statewide__"] + sorted(map(str, boundaries[lga_name_col].dropna().unique()))

    # Side-by-side inputs are only worth loading if an LGA map is still pending.
    extents = grids = None
    if any(j not in done_maps for j in jobs[2:]):
        extents, grids = _dual_map_inputs(args, lga_spec)
    dual = extents is not None and grids is not None
    # the single-panel renderer cannot use a two-map page (see stage4_maps)
    single_panel_spec = None if (lga_spec is not None and lga_spec.is_multi_map) else lga_spec
    lga_dpi = _figure_dpi(lga_spec if dual else single_panel_spec)

    finished = True
    for job in jobs:
        if job in done_maps:
            continue
        if job == "__impl__":
            if args.states:
                from stage4_maps import implementation_map
                if _pl:
                    _norm = (points[_pl].dropna().astype(str).str.replace("_", " ", regex=False)
                            .str.replace(r"\s+", " ", regex=True).str.strip().str.title())
                    impl_lgas = sorted(_norm.unique())
                else:
                    impl_lgas = []
                implementation_map(
                    args.states, args.lga_boundaries, args.state, impl_lgas,
                    os.path.join(maps_dir, f"{args.state}_implementation_map.png"),
                    logo=args.logo,
                    title=f"{args.state} State GTS Implementation Map — {len(impl_lgas)} LGAs")
            done_maps.add(job)
            state["maps_done"] = sorted(done_maps)
            if time.time() - t0 > TIME_BUDGET:
                break
            continue
        if job == "__statewide__":
            fig = render_map_figure(
                boundaries, points,
                f"{args.state} State Settlement Visitation Coverage — Day {args.day}",
                lga_label_col=lga_name_col, lga_label_size=15, logo=args.logo,
                role="statewide", spec=statewide_spec)
            p = os.path.join(maps_dir, f"{args.state}_statewide_day_{args.day}.png")
            dpi = _figure_dpi(statewide_spec)
        else:
            b = boundaries[boundaries[lga_name_col].astype(str) == job]
            w = None
            if wards is not None and ward_lga_col:
                w = wards[wards[ward_lga_col].astype(str).str.strip().str.title()
                          == job.strip().title()]
                if not len(w):
                    w = gpd.clip(wards, b)
            elif wards is not None:
                w = gpd.clip(wards, b)
            if dual:
                fig = render_dual_lga_figure(
                    lga_spec, job, focal=b,
                    extents=_subset_for_lga(extents, job, b),
                    grids=_subset_for_lga(grids, job, b),
                    adjoining=boundaries[boundaries[lga_name_col].astype(str) != job],
                    wards=w, ward_label_col=ward_name_col, logo=args.logo,
                    state_logo=getattr(args, "state_logo", None),
                    title_context=job.replace("_", " "))
            else:
                pts = points[points[pts_lga_col].astype(str).str.strip().str.title()
                             == job.strip().title()] if pts_lga_col else gpd.clip(points, b)
                if not len(pts):
                    pts = gpd.clip(points, b)
                fig = render_map_figure(b, pts,
                                        f"{job} LGA Settlement Visitation — Day {args.day}",
                                        wards=w, ward_label_col=ward_name_col, logo=args.logo,
                                        is_lga=True, role="lga", spec=single_panel_spec)
            p = os.path.join(maps_dir, f"{args.state}_{safe_name(job)}_day_{args.day}.png")
            dpi = lga_dpi
        fig.savefig(p, dpi=dpi, facecolor="white")
        plt.close(fig)
        done_maps.add(job)
        state["maps_done"] = sorted(done_maps)
        if time.time() - t0 > TIME_BUDGET:
            finished = job == jobs[-1]
            break

    if finished and all(j in done_maps for j in jobs):
        state["steps"]["maps"] = "done"
        print(f"maps complete: {len(jobs)} images")
        return True
    print(f"maps progress: {len(done_maps)}/{len(jobs)}")
    return False


def step_report(args, state, t0):
    if args.analysis_type == "post_campaign":
        state["steps"]["report"] = "done"
        return True
    from stage5_report import build_report
    out_csv = os.path.join(args.output, f"{args.state}_day_{args.day}_settlement_visitation.csv")
    build_report(args.state, args.day, out_csv, f"day_{args.day}_cumm",
                 os.path.join(args.output, f"team_deploy_day_{args.day}.csv"),
                 os.path.join(args.output, f"time_spent_day_{args.day}.csv"),
                 os.path.join(args.output, "maps"),
                 os.path.join(args.output,
                              f"{args.state}_Day_{args.day}_Daily_Tracking_Report_DRAFT.docx"),
                 args.logo, campaign_name=args.campaign_name,
                 charts_folder=os.path.join(args.output, "charts"),
                 state_logo=args.state_logo)
    state["steps"]["report"] = "done"
    return True


def step_pptx_report(args, state, t0):
    if not args.pptx_template or args.analysis_type == "post_campaign":
        state["steps"]["pptx_report"] = "done"
        return True
    from stage6_pptx_report import build_pptx_report
    out_csv = os.path.join(args.output, f"{args.state}_day_{args.day}_settlement_visitation.csv")
    build_pptx_report(
        args.pptx_template, args.state, args.day, out_csv, f"day_{args.day}_cumm",
        os.path.join(args.output, f"team_deploy_day_{args.day}.csv"),
        os.path.join(args.output, f"time_spent_day_{args.day}.csv"),
        os.path.join(args.output, "maps"), os.path.join(args.output, "charts"),
        os.path.join(args.output, f"{args.state}_Day_{args.day}_Report.pptx"),
        campaign_name=args.campaign_name, state_logo_path=args.state_logo,
        tracks_file=os.path.join(args.output, "merged_tracks.csv"))
    state["steps"]["pptx_report"] = "done"
    return True


def step_pir_report(args, state, t0):
    if args.analysis_type != "post_campaign":
        state["steps"]["pir_report"] = "done"
        return True
    from stage7_pir_report import build_pir_report
    out_csv = os.path.join(args.output, f"{args.state}_day_{args.day}_settlement_visitation.csv")
    build_pir_report(
        args.state, out_csv, f"day_{args.day}_cumm",
        os.path.join(args.output, f"team_deploy_day_{args.day}.csv"),
        os.path.join(args.output, f"time_spent_day_{args.day}.csv"),
        os.path.join(args.output, "maps"),
        os.path.join(args.output, f"{args.state}_Post_Implementation_Report.docx"),
        args.logo, campaign_name=args.campaign_name,
        charts_folder=os.path.join(args.output, "charts"), state_logo=args.state_logo,
        template_path=args.pir_template)
    state["steps"]["pir_report"] = "done"
    return True


STEPS = [("merge", step_merge), ("scan", step_scan), ("prep_ta", step_prep_ta),
         ("prep_vor", step_prep_vor), ("join", step_join), ("dip", step_dip),
         ("erm", step_erm), ("charts", step_charts), ("maps", step_maps),
         ("report", step_report), ("pptx_report", step_pptx_report),
         ("pir_report", step_pir_report)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tracks-folder", required=True)
    ap.add_argument("--settlements", required=True)
    ap.add_argument("--gridded-ta", required=True)
    ap.add_argument("--voronoi", required=True)
    ap.add_argument("--lga-boundaries", required=True)
    ap.add_argument("--wards", default=None, help="Ward boundary file for LGA maps")
    ap.add_argument("--states", default=None, help="State boundary file for the implementation map")
    ap.add_argument("--state", required=True)
    ap.add_argument("--day", type=int, required=True)
    ap.add_argument("--mopup", action="store_true")
    ap.add_argument("--output", required=True)
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--prev-dip", default=None)
    ap.add_argument("--prev-ta", default=None)
    ap.add_argument("--logo", default=None)
    ap.add_argument("--map-template-dir", default=None,
                    help="Directory holding the QGIS .qpt map layout templates that drive "
                    "stage 4 (page size, legend, scale bar, north arrow, logo placement). "
                    "Defaults to $GTS_MAP_TEMPLATE_DIR, then the folder holding these scripts.")
    ap.add_argument("--map-dpi", type=int, default=None,
                    help="Override the map templates' print resolution (300 dpi) to shrink "
                    "the rendered PNGs.")
    ap.add_argument("--pptx-template", default=None,
                    help="Organization's .pptx report template — if given, also builds the "
                    "report in that template alongside the .docx")
    ap.add_argument("--campaign-name", default="Vaccination Tracking Report")
    ap.add_argument("--state-logo", default=None,
                    help="State PHC logo (title page/slide) — separate from --logo")
    ap.add_argument("--analysis-type", choices=["daily", "post_campaign"], default="daily",
                    help="'daily' builds the Daily ERM Analysis tab + daily .docx/.pptx report. "
                    "'post_campaign' builds a Post-Campaign Analysis tab + a Post-Implementation "
                    "Report (PIR) .docx instead.")
    ap.add_argument("--teams-deployed", type=int, default=None,
                    help="Manually-entered total teams deployed — overrides the settlement-list-"
                    "derived count and drives Teams Reported/Pending/Reporting %% throughout.")
    ap.add_argument("--pir-template", default=None,
                    help="Post Implementation Report sample/template — auto-detected next to "
                    "this script if not given; used as the reporting template when "
                    "--analysis-type post_campaign builds the PIR .docx.")
    args = ap.parse_args()

    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.ckpt, exist_ok=True)
    state = load_state(args.ckpt)
    t0 = time.time()

    for name, fn in STEPS:
        if state["steps"].get(name) == "done":
            continue
        print(f">>> step: {name}", flush=True)
        finished = fn(args, state, t0)
        save_state(args.ckpt, state)
        if not finished or time.time() - t0 > TIME_BUDGET:
            print("CONTINUE", flush=True)
            return
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
