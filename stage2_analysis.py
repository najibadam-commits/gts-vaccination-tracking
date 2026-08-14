"""Stage 2 — Settlement visitation & coverage analysis.

Ported from the original analysis_script.ipynb. Inputs:
  - settlements (DIP/planned list) CSV
  - merged tracks (parquet or geojson from Stage 1)
  - gridded target area (sqlite/gpkg/geojson)
  - voronoi target area / settlement extent (sqlite/gpkg/geojson)

Outputs (into the day's output folder):
  - {State}_day_{N}_settlement_visitation.csv
  - visited / not-visited settlement point layers (geojson) for mapping
  - updated gridded TA with visitation status (parquet, reused next day for cumulative logic)
"""
import argparse
import os
import re
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd

# ----------------------------------------------------------------- helpers

def is_empty(value) -> bool:
    if pd.isna(value):
        return True
    if value == 0 or value == "":
        return True
    return False


def standardize_column_names(data: pd.DataFrame, columns=None) -> pd.DataFrame:
    columns = columns if columns is not None else data.columns
    col_map = {c: c.strip().replace(" ", "_").replace("\n", "").strip().lower() for c in columns}
    data.rename(columns=col_map, inplace=True)
    return data


def _norm(text) -> str:
    return re.sub(r"\s+", " ", str(text)).strip().title()


def find_col(data: pd.DataFrame, keyword: str) -> str | None:
    """Finds a column containing the keyword, excluding code/id/old columns."""
    for col in data.columns:
        low = col.lower()
        if keyword in low and not re.search(r"(?:code|old|id\b|_id)", low):
            return col
    return None


def construct_unique(data: pd.DataFrame, unique_col: str = "unique") -> pd.DataFrame:
    """Builds a unique settlement key: State_Lga_Ward_Settlement."""
    state_col = find_col(data, "state")
    lga_col = find_col(data, "lga")
    ward_col = find_col(data, "ward")
    sett_col = find_col(data, "settlement")
    missing = [n for n, c in
               [("state", state_col), ("lga", lga_col), ("ward", ward_col), ("settlement", sett_col)] if c is None]
    if missing:
        raise ValueError(f"Could not detect admin columns: {missing} in {list(data.columns)[:15]}")

    if unique_col in data.columns:
        return data

    parts = []
    for c in (state_col, lga_col, ward_col, sett_col):
        s = (data[c].astype(str).str.replace(r"\s+", " ", regex=True)
             .str.strip().str.title())
        s = s.mask(data[c].isna())
        parts.append(s)
    combined = parts[0].str.cat(parts[1:], sep="_")  # NaN if any part NaN
    data.insert(0, unique_col, combined)
    return data


def detect_day_column(day: int, data: pd.DataFrame, not_found: str = "raise") -> str | None:
    for col in data.columns:
        if "cumm" in col.lower():
            continue
        match = re.search(r"\d+", col)
        if match and int(match.group()) == day and "day" in col.lower():
            return col
    # fallback: original behaviour (first numeric match)
    for col in data.columns:
        match = re.search(r"\d+", col)
        if match and int(match.group()) == day:
            return col
    if not_found == "ignore":
        return None
    raise AttributeError(f"No column matches day {day}")


# ------------------------------------------------------- core spatial logic

def find_and_update_visited_grids(tracks: gpd.GeoDataFrame, target_area: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Flags grid cells intersecting any track point."""
    if "rowid" not in target_area.columns:
        target_area = target_area.copy()
        target_area["rowid"] = target_area.index + 1

    ta_columns = target_area.columns.tolist() + ["index_tracks"]
    for extra in ("visitation", "cumm"):
        if extra not in ta_columns:
            ta_columns += [extra]

    joined = target_area.sjoin(tracks[["geometry"]], how="left", predicate="intersects",
                               rsuffix="tracks").reset_index()
    # `cumm` is TODAY only — it looks at this run's tracks and nothing else.
    # `visitation` below folds in prior days. Both are returned: the daily
    # reporting figures must never be read off the cumulative one.
    joined["cumm"] = np.where(joined["index_tracks"].notna(), "Visited", "Not Visited")

    # prior visitation (from a previous day's saved TA) is respected
    if "visitation" not in joined.columns:
        joined["visitation"] = np.nan
    joined["visitation"] = np.where(
        (joined["visitation"] == "Visited") | (joined["cumm"] == "Visited"),
        "Visited", "Not Yet Visited")
    return joined.loc[:, [c for c in ta_columns if c in joined.columns]]


def generate_ta_cumulative_summary(target_area: gpd.GeoDataFrame, unique_col: str) -> pd.DataFrame:
    target_area = target_area.drop_duplicates(subset=["rowid", "visitation"])
    cum = (target_area.groupby([unique_col, "visitation"]).size()
           .reset_index(name="count")
           .pivot_table(values="count", columns="visitation", index=unique_col))
    for col in ("Not Yet Visited", "Visited"):
        if col not in cum.columns:
            cum[col] = 0
    cum = cum.fillna(0)
    cum["Total"] = cum["Not Yet Visited"] + cum["Visited"]
    return cum


def calculate_coverage(summary: pd.DataFrame) -> pd.DataFrame:
    summary["visitation"] = np.where(summary["Visited"] >= 1, "Visited", "Not Yet Visited")
    summary["Coverage"] = summary["Visited"] / summary["Total"]
    return summary


def generate_ta_daily_summary(target_area: gpd.GeoDataFrame, unique_col: str) -> pd.DataFrame:
    """Per-settlement coverage for THIS DAY ONLY.

    The mirror of `generate_ta_cumulative_summary`, but pivoting on `cumm`
    (today's tracks) instead of `visitation` (today's plus every previous
    day's). Without this the reporting has no way to say what happened on a
    given day: a settlement covered on Day 1 stays "Visited" in the cumulative
    column for the rest of the campaign, so a Day 3 "daily" figure computed
    from it silently reports Day 1's work as Day 3's.

    Returns a frame indexed by settlement with `Daily Coverage` (visited cells
    / total cells today) and `daily_visitation`.
    """
    ta = target_area.drop_duplicates(subset=["rowid", "cumm"])
    tab = (ta.groupby([unique_col, "cumm"]).size()
           .reset_index(name="count")
           .pivot_table(values="count", columns="cumm", index=unique_col))
    for col in ("Visited", "Not Visited"):
        if col not in tab.columns:
            tab[col] = 0
    tab = tab.fillna(0)
    total = tab["Not Visited"] + tab["Visited"]
    out = pd.DataFrame({
        "Daily Coverage": (tab["Visited"] / total).where(total > 0),
        "Daily Cells Visited": tab["Visited"].astype(int),
        "daily_visitation": np.where(tab["Visited"] >= 1, "Visited", "Not Visited"),
    }, index=tab.index)
    return out


def count_tracks_within_settlement_extent(extent: gpd.GeoDataFrame, tracks: gpd.GeoDataFrame,
                                          unique_col: str) -> pd.DataFrame:
    joined = extent.sjoin(tracks[["geometry", "weight"]], how="left",
                          predicate="intersects", rsuffix="tracks")
    return (joined.groupby(unique_col, dropna=True)["weight"].sum()
            .reset_index().rename(columns={"weight": "track_count"}))


# -------------------------------------------------------------- DIP update

def update_visitation_row(row, prev_day_cumm):
    prev_status = row.get(prev_day_cumm) if prev_day_cumm else None
    current = row.get("visitation")
    stats = [prev_status, current]
    if any(s == "Visited" for s in stats):
        return "Visited"
    if all(pd.isna(s) for s in stats):
        return None
    return "Not Yet Visited"


def set_cumulative_visitation(dip: pd.DataFrame, day: int) -> pd.DataFrame:
    prev_cumm = f"day_{day - 1}_cumm" if day > 1 else None
    dip["visitation"] = dip.apply(update_visitation_row, args=(prev_cumm,), axis=1)

    day_cols = [detect_day_column(i, dip) for i in range(1, day + 1)]
    dip = standardize_column_names(dip, day_cols)
    day_cols = [detect_day_column(i, dip) for i in range(1, day + 1)]
    query = " | ".join([f"(`{c}`.notnull())" for c in day_cols])
    dip["is_cumm"] = dip.eval(query, engine="python")
    dip[f"day_{day}_cumm"] = dip.apply(
        lambda r: r["visitation"] if (r["is_cumm"] is True or bool(r["is_cumm"])) or pd.notna(r["visitation"])
        else np.nan, axis=1)
    return dip


def get_activity_day_col(dip: pd.DataFrame, day: int) -> str:
    """Returns the per-day activity/status column, creating `day_{day}` if needed.

    Source settlement lists often already carry legacy planning-flag columns
    named like `day1`/`day2` (Y/blank = "was this settlement scheduled that
    day", unrelated to GPS-derived visitation). detect_day_column() matches
    on digits alone and can't tell those apart from our own `day_{N}`
    convention, so only our own exact naming is trusted here — anything
    else is treated as a naming collision and a fresh column is created.
    """
    own_col = f"day_{day}"
    if own_col in dip.columns:
        return own_col
    col = detect_day_column(day, dip, "ignore")
    if col and col == own_col:
        return col
    dip[own_col] = "Yes"
    return own_col


def find_day_scope_column(dip: pd.DataFrame, day: int) -> str | None:
    """The settlement list's own "planned for day N" flag column, if any.

    Source settlement lists carry planning flags named like `day1`/`day2`
    (Y/blank = "was this settlement scheduled that day"). This module's OWN
    `day_{N}` columns are excluded: `get_activity_day_col` writes "Yes" into
    every row when the source has no flag of its own, so they say nothing about
    scheduling and using one as a scope would select the whole list.

    Single source of truth for the daily scope — the daily coverage figures on
    the slides and the daily charts both read it, so the narration and the
    chart beside it cannot end up describing different denominators.
    """
    for col in dip.columns:
        low = col.lower()
        if "cumm" in low or "daily" in low or re.fullmatch(r"day_\d+", col):
            continue
        m = re.search(r"\d+", col)
        if m and int(m.group()) == day and low.startswith("day"):
            return col
    return None


# Values in a planning-flag column that mean "scheduled that day". An
# INCLUSION list, not an exclusion list: only "Yes"/"Y" put a settlement in the
# day's denominator. Excluding a handful of known falsy strings instead meant
# any other value — a date, a team code, a stray character — silently counted
# as planned, which inflated the daily denominator with settlements nobody was
# sent to.
_SCHEDULED = frozenset({"yes", "y"})


def day_scope_mask(dip: pd.DataFrame, day: int) -> tuple[pd.Series, str | None]:
    """Boolean mask of the settlements planned for `day`, and the column used.

    A settlement is in scope only when its day-`N` flag reads "Yes" or "Y"
    (case-insensitive). Everything else is out.

    Returns an all-True mask and None when the settlement list has no usable
    flag for that day, so callers degrade to the full planned list rather than
    silently charting nothing.
    """
    col = find_day_scope_column(dip, day)
    if col is None:
        return pd.Series(True, index=dip.index), None
    mask = (dip[col].astype(str).str.strip().str.lower().isin(_SCHEDULED))
    if not bool(mask.any()):
        print(f"  '{col}' has no Yes/Y values for day {day} — daily coverage "
              f"falls back to the full planned list")
        return pd.Series(True, index=dip.index), None
    return mask, col


def classify_coverage(row) -> str:
    coverage = row["Coverage"]
    if is_empty(coverage):
        return "No Coverage"
    coverage = round(coverage, 2)
    # cascading (not table-lookup) so any coverage > 0 that rounds below 0.01
    # still lands in "Very Low Coverage" instead of falling through to
    # "Fully Covered" — "Fully Covered" only fires at/above 0.70.
    if coverage >= 0.70:
        return "Fully Covered"
    if coverage >= 0.50:
        return "Partially Covered"
    if coverage >= 0.30:
        return "Low Coverage"
    return "Very Low Coverage"


def classify_time_spent(row):
    tracks = row.get("track_count")
    if pd.isna(tracks) or tracks == 0:
        return np.nan
    mins = tracks * 2  # each track ping = 2 minutes
    # half-open cascading bounds — no gaps regardless of the ping-to-minute
    # factor, and mins == 12 correctly lands in "12 - 30 mins" not "<12 mins"
    if mins < 12:
        return "<12 mins"
    if mins <= 30:
        return "12 - 30 mins"
    if mins <= 60:
        return "30 mins - 1 hr"
    if mins <= 120:
        return "1 - 2 hrs"
    return ">2 hrs"


# ---------------------------------------------------------------- pipeline

# Timestamp handling for raw track exports. Local time is UTC+1; the offset is
# applied to the whole timestamp, not just the hour, so a 23:30 UTC ping moves
# to the next LOCAL date instead of wrapping to hour 0 on the wrong day.
TRACK_TIMESTAMP_COL = "GPS Timestamp (UTC)"
TRACK_TIMESTAMP_FORMAT = "%m/%d/%Y %H:%M:%S"
LOCAL_UTC_OFFSET = pd.Timedelta(hours=1)


def _local_dates(series: pd.Series) -> pd.Series:
    ts = pd.to_datetime(series, format=TRACK_TIMESTAMP_FORMAT, errors="coerce")
    return (ts + LOCAL_UTC_OFFSET).dt.strftime("%Y-%m-%d")


# A reporting day must carry at least this share of the busiest day's pings to
# be treated as a real day of fieldwork. Trackers left switched on overnight,
# and devices with a skewed clock, put a thin trickle of pings on the following
# date; taking the maximum date blindly would then scope the whole analysis to a
# handful of pings and report near-zero coverage for a day that went fine.
MIN_DATE_PING_SHARE = 0.05


def track_date_counts(path: str | Path, chunksize: int = 1_000_000) -> pd.Series:
    """Ping counts per LOCAL date in a raw track export, oldest date first.

    Reads the timestamp column alone, in chunks, so the pass stays cheap on a
    multi-million-row merged file. Empty Series when the file carries no usable
    timestamp column.
    """
    path = str(path)
    if path.endswith((".parquet", ".gpkg", ".geojson", ".sqlite")):
        return pd.Series(dtype="int64")
    counts: dict[str, int] = {}
    try:
        reader = pd.read_csv(path, usecols=lambda c: c == TRACK_TIMESTAMP_COL,
                             chunksize=chunksize, dtype=str)
        for chunk in reader:
            if TRACK_TIMESTAMP_COL not in chunk.columns:
                return pd.Series(dtype="int64")
            for day, n in _local_dates(chunk[TRACK_TIMESTAMP_COL]).value_counts().items():
                counts[day] = counts.get(day, 0) + int(n)
    except (ValueError, KeyError):
        return pd.Series(dtype="int64")
    return pd.Series(counts, dtype="int64").sort_index()


def latest_track_date(path: str | Path, chunksize: int = 1_000_000) -> str | None:
    """The most recent LOCAL date of real fieldwork in a raw track export.

    "Real" means carrying at least `MIN_DATE_PING_SHARE` of the busiest date's
    pings — see that constant for why the plain maximum is the wrong answer.
    Returns None when the file has no usable timestamps, which is the signal to
    fall back to using every row.
    """
    counts = track_date_counts(path, chunksize)
    if not len(counts):
        return None
    threshold = counts.max() * MIN_DATE_PING_SHARE
    substantive = counts[counts >= threshold]
    chosen = str(substantive.index[-1]) if len(substantive) else str(counts.index[-1])

    if len(counts) > 1:
        shown = ", ".join(f"{d} ({n:,})" for d, n in counts.items())
        print(f"  pings by transmission date — {shown}")
        ignored = [d for d in counts.index if str(d) > chosen]
        if ignored:
            print(f"  ignoring trailing date(s) {', '.join(map(str, ignored))} — "
                  f"under {MIN_DATE_PING_SHARE:.0%} of the busiest day's pings, "
                  f"most likely trackers left on overnight")
    return chosen


def load_track_points(path: str | Path, chunksize: int = 500_000,
                      track_date: str | None = None) -> gpd.GeoDataFrame:
    """Loads track points memory-safely as unique coordinates with a ping-count weight.

    Identical coordinates are collapsed into one point carrying `weight` =
    number of pings, which preserves track_count/time-spent math while using
    a fraction of the memory.

    `track_date` ("YYYY-MM-DD", local) keeps only pings transmitted on that
    date. This is what makes `track_count` — and therefore every per-team
    minutes figure derived from it — describe ONE day. A merged export covering
    several days otherwise produces a track_count spanning all of them, and the
    Time Spent Range chart reads as the campaign to date rather than the
    reporting day. Pass None to use every row, which is what the post-campaign
    analysis wants.
    """
    path = str(path)
    if path.endswith((".parquet", ".gpkg", ".geojson", ".sqlite")):
        if track_date:
            print(f"  note: {os.path.basename(path)} carries no timestamps — "
                  f"cannot scope tracks to {track_date}")
        gdf = read_spatial(path)
        pts = pd.DataFrame({"Lon": gdf.geometry.x, "Lat": gdf.geometry.y})
    else:
        wanted = ["Lat", "Lon"] + ([TRACK_TIMESTAMP_COL] if track_date else [])
        chunks = []
        kept = dropped = 0
        for chunk in pd.read_csv(path, usecols=lambda c: c in set(wanted),
                                 chunksize=chunksize):
            if track_date:
                if TRACK_TIMESTAMP_COL not in chunk.columns:
                    raise ValueError(
                        f"Track export has no '{TRACK_TIMESTAMP_COL}' column, so "
                        f"pings cannot be scoped to {track_date}.")
                on_day = _local_dates(chunk[TRACK_TIMESTAMP_COL]) == track_date
                dropped += int((~on_day).sum())
                chunk = chunk[on_day]
                kept += len(chunk)
                if not len(chunk):
                    continue
            chunks.append(chunk.groupby(["Lon", "Lat"]).size().rename("weight"))
        if track_date:
            print(f"  tracks scoped to {track_date}: {kept:,} pings kept, "
                  f"{dropped:,} from other dates dropped")
        if not chunks:
            raise ValueError(
                f"No track points remain after scoping to {track_date}. Check "
                f"that the export covers the reporting day.")
        agg = pd.concat(chunks).groupby(["Lon", "Lat"]).sum().reset_index()
        gdf = gpd.GeoDataFrame(agg, geometry=gpd.points_from_xy(agg["Lon"], agg["Lat"]),
                               crs="EPSG:4326")
        print(f"  {int(gdf['weight'].sum()):,} pings -> {len(gdf):,} unique points")
        return gdf
    agg = pts.groupby(["Lon", "Lat"]).size().rename("weight").reset_index()
    out = gpd.GeoDataFrame(agg, geometry=gpd.points_from_xy(agg["Lon"], agg["Lat"]),
                           crs=gdf.crs or "EPSG:4326")
    print(f"  {int(out['weight'].sum()):,} pings -> {len(out):,} unique points")
    return out


def read_spatial(path: str | Path) -> gpd.GeoDataFrame:
    path = str(path)
    if path.endswith(".parquet"):
        return gpd.read_parquet(path)
    if path.endswith(".sqlite"):
        # spatialite files may hold several tables; pick the data layer
        import pyogrio
        layers = pyogrio.list_layers(path)
        names = [l[0] for l in layers]
        skip = {"data_licenses", "sql_statements_log", "elementarygeometries", "knn2"}
        data_layers = [n for n in names if n.lower() not in skip]
        return gpd.read_file(path, layer=data_layers[0] if data_layers else names[0])
    return gpd.read_file(path)


def run_analysis(settlement_file: str, tracks_file: str, gridded_ta_file: str,
                 voronoi_file: str, state_name: str, day: int, is_mop_up: bool,
                 output_folder: str, prev_ta_file: str | None = None,
                 prev_dip_file: str | None = None,
                 track_date: str | None = "latest") -> dict:
    """Per-settlement visitation for one reporting day.

    `track_date` decides which pings count as "today":

        "latest"      the most recent transmission date in the export (default)
        "YYYY-MM-DD"  that date exactly
        None          every row, whatever date it carries

    The default matters. `track_count` — and therefore every per-team minutes
    figure derived from it, including the Time Spent Range chart — is built
    from the pings this call sees. Handed a merged export covering several
    days, an unscoped run reports the campaign to date as one day's work. The
    post-campaign analysis passes None because spanning the period is the point.
    """
    os.makedirs(output_folder, exist_ok=True)

    resolved_date = None
    if track_date == "latest":
        resolved_date = latest_track_date(tracks_file)
        if resolved_date:
            print(f"Latest transmission date in the track export: {resolved_date}")
        else:
            print("No usable timestamps in the track export — using every ping")
    elif track_date:
        resolved_date = track_date

    print("Reading datasets...")
    ta = read_spatial(gridded_ta_file)
    voronoi = read_spatial(voronoi_file)
    tracks = load_track_points(tracks_file, track_date=resolved_date)
    dip_source = prev_dip_file if prev_dip_file else settlement_file
    dip = pd.read_csv(dip_source, low_memory=False)

    # filter DIP to state if multi-state master list
    state_col = find_col(dip, "state")
    if state_col and dip[state_col].nunique() > 1:
        dip = dip[dip[state_col].astype(str).str.strip().str.title()
                 == state_name.title()].copy()
        print(f"Filtered DIP to {state_name}: {len(dip):,} settlements")

    print("Preparing unique keys...")
    dip = construct_unique(dip, "unique")
    ta = construct_unique(ta, "unique")
    voronoi = construct_unique(voronoi, "unique")

    if tracks.crs is None:
        tracks = tracks.set_crs("EPSG:4326")
    if ta.crs is not None and tracks.crs != ta.crs:
        tracks = tracks.to_crs(ta.crs)

    # carry over prior visitation for cumulative analysis
    if prev_ta_file and os.path.exists(prev_ta_file):
        print("Loading previous day's TA visitation...")
        prev_ta = read_spatial(prev_ta_file)
        if "rowid" not in ta.columns:
            ta["rowid"] = ta.index + 1
        ta = ta.merge(prev_ta[["rowid", "visitation"]].drop_duplicates("rowid"), on="rowid", how="left")

    print("Clipping tracks to settlement extents...")
    tracks_clip = gpd.clip(tracks, voronoi)
    print(f"  {len(tracks_clip):,} of {len(tracks):,} points within extents")

    print("Flagging visited grids...")
    ta_updated = find_and_update_visited_grids(tracks_clip, ta)

    print("Summarising coverage...")
    summary = calculate_coverage(generate_ta_cumulative_summary(ta_updated, "unique"))
    daily_summary = generate_ta_daily_summary(ta_updated, "unique")
    track_counts = count_tracks_within_settlement_extent(voronoi, tracks_clip, "unique")
    summary = summary.merge(track_counts, on="unique", how="left").fillna({"track_count": 0}).set_index("unique")

    # ---- evidence of tracks across the WHOLE export ----------------------
    # `track_count` above is the reporting day alone, which is what every daily
    # figure needs. Cumulative coverage asks a different question — has this
    # settlement ever been reached — and answering it from the scoped tracks
    # would throw away the earlier days sitting in the very same file. That
    # matters for the common case of an analyst re-running a later day with the
    # whole campaign's tracks in the folder and no previous day's CSV to carry
    # forward: the daily figures must narrow, the cumulative ones must not.
    #
    # Only worth a second pass when the export actually spans several dates.
    period_evidence = None
    if resolved_date and len(track_date_counts(tracks_file)) > 1:
        print("Scanning the full export for cumulative evidence of tracks...")
        all_tracks = load_track_points(tracks_file, track_date=None)
        if all_tracks.crs is None:
            all_tracks = all_tracks.set_crs("EPSG:4326")
        if ta.crs is not None and all_tracks.crs != ta.crs:
            all_tracks = all_tracks.to_crs(ta.crs)
        all_counts = count_tracks_within_settlement_extent(
            voronoi, gpd.clip(all_tracks, voronoi), "unique")
        period_evidence = (all_counts.set_index("unique")["track_count"]
                           .fillna(0) > 0)
        print(f"  {int(period_evidence.sum()):,} settlements have tracks "
              f"somewhere in the export")

    print("Updating DIP...")
    if "Coverage" in dip.columns:
        dip = dip.rename(columns={"Coverage": "Prev_Coverage"})
    else:
        dip["Prev_Coverage"] = np.nan

    # From day 2 the DIP *is* the previous day's visitation CSV, so it already
    # carries `track_count` and `visitation`. Merging on top of them would have
    # pandas suffix both sides to `track_count_x`/`track_count_y`, leaving no
    # plain `track_count` at all — and `classify_time_spent` reads that name, so
    # the whole Time Spent column silently came out blank from day 2 onward.
    # `Coverage` is protected by the rename above; these need the same care.
    # The previous day's values are superseded by today's, so they are dropped
    # rather than kept under another name.
    # `Track Evidence` is the one exception: it is a CUMULATIVE flag, so the
    # previous day's value has to survive into today's calculation rather than
    # being recomputed from today's tracks alone. Held aside under a private
    # name and folded back in below, the same way `Coverage` is.
    if "Track Evidence" in dip.columns:
        dip["Prev_Track_Evidence"] = (dip["Track Evidence"].astype(str)
                                      .str.strip().str.lower() == "yes")
        dip = dip.drop(columns=["Track Evidence"])
    else:
        dip["Prev_Track_Evidence"] = False

    stale = [c for c in ("track_count", "visitation", "Daily Coverage",
                         "daily_visitation", "Daily Settlement Coverage",
                         "Grid Cells Visited", "Grid Cells", "Daily Cells Visited")
             if c in dip.columns]
    if stale:
        print(f"  dropping previous day's {', '.join(stale)} — recomputed below")
        dip = dip.drop(columns=stale)

    get_activity_day_col(dip, day)
    cell_counts = summary.rename(columns={"Visited": "Grid Cells Visited",
                                          "Total": "Grid Cells"})
    dip = dip.merge(
        cell_counts[["visitation", "Coverage", "track_count",
                     "Grid Cells Visited", "Grid Cells"]],
        left_on="unique", right_index=True, how="left")
    dip = dip.merge(daily_summary, left_on="unique", right_index=True, how="left")

    # final coverage = max(today, previous)
    dip["Coverage"] = dip.apply(
        lambda r: np.nanmax([v for v in [r["Coverage"], r["Prev_Coverage"]] if pd.notna(v)])
        if pd.notna(r["Coverage"]) or pd.notna(r["Prev_Coverage"]) else np.nan, axis=1)

    # ---- cumulative evidence of tracks -----------------------------------
    # A settlement counts as reached cumulatively once tracks have been seen in
    # it at ANY point in the reporting period, whichever day it was planned
    # for. Two independent sources of evidence, ORed with the flag carried from
    # yesterday:
    #   Grid Cells Visited > 0   the gridded target area recorded a visit
    #   track_count > 0          pings fell inside the settlement's extent
    # The second matters because not every planned settlement has a polygon in
    # the gridded layer; without it, a settlement teams demonstrably worked
    # reads as never visited for the rest of the campaign.
    today_evidence = (
        pd.to_numeric(dip.get("Grid Cells Visited"), errors="coerce").fillna(0) > 0
        if "Grid Cells Visited" in dip.columns
        else pd.Series(False, index=dip.index))
    today_evidence = today_evidence | (
        pd.to_numeric(dip.get("track_count"), errors="coerce").fillna(0) > 0
        if "track_count" in dip.columns
        else pd.Series(False, index=dip.index))
    cum_evidence = today_evidence | dip["Prev_Track_Evidence"].fillna(False).astype(bool)
    if period_evidence is not None:
        # earlier days present in this same export, which the day scope removed
        # from `track_count` but which are still evidence the settlement was
        # reached at some point in the period
        cum_evidence = cum_evidence | (dip["unique"].map(period_evidence)
                                       .fillna(False).astype(bool))
    dip["Track Evidence"] = np.where(cum_evidence, "Yes", "No")

    dip = set_cumulative_visitation(dip, day)
    day_col = detect_day_column(day, dip)
    cum_col = f"day_{day}_cumm"
    if is_mop_up:
        dip.loc[dip[cum_col] == "Not Yet Visited", cum_col] = "Not Visited"
    else:
        dip[cum_col] = dip[cum_col].replace({"Not Yet Visited": "Not Visited"})
    # Evidence of tracks is authoritative for the CUMULATIVE status, so every
    # downstream consumer — slides, charts, workbook, maps — agrees on which
    # settlements have been reached instead of each applying its own rule.
    n_by_evidence = int((cum_evidence & (dip[cum_col] != "Visited")).sum())
    dip.loc[cum_evidence & dip[cum_col].notna(), cum_col] = "Visited"
    if n_by_evidence:
        print(f"  {n_by_evidence:,} settlements counted as cumulatively visited "
              f"on track evidence alone")

    # DAILY status column, alongside the cumulative one. `cum_col` carries every
    # day up to and including today; `daily_col` is today alone. Reporting picks
    # whichever it means — see generate_ta_daily_summary.
    daily_col = f"day_{day}_daily"
    dip[daily_col] = dip["daily_visitation"].where(dip[cum_col].notna())
    dip[daily_col] = dip[daily_col].fillna(
        pd.Series("Not Visited", index=dip.index).where(dip[cum_col].notna()))

    print("Classifying results...")
    dip["Settlement Coverage"] = dip.apply(classify_coverage, axis=1)
    dip["Daily Settlement Coverage"] = dip.apply(
        lambda r: classify_coverage({"Coverage": r.get("Daily Coverage")}), axis=1)
    dip["Time Spent"] = dip.apply(classify_time_spent, axis=1)
    dip.drop(columns=["Prev_Coverage", "Prev_Track_Evidence", "visitation",
                      "daily_visitation", "is_cumm",
                      "Total", "Visited", "Not Yet Visited"],
             inplace=True, errors="ignore")
    for c in ("Grid Cells", "Grid Cells Visited", "Daily Cells Visited"):
        if c in dip.columns:
            dip[c] = pd.to_numeric(dip[c], errors="coerce").fillna(0).astype(int)

    # ------------------------------------------------------------- outputs
    out_csv = os.path.join(output_folder, f"{state_name}_day_{day}_settlement_visitation.csv")
    dip.to_csv(out_csv, index=False)
    print(f"Saved {out_csv}")

    try:
        import pyarrow  # noqa: F401
        ta_out = os.path.join(output_folder, f"gridded_ta_day_{day}.parquet")
        gpd.GeoDataFrame(ta_updated, geometry="geometry", crs=ta.crs).to_parquet(ta_out)
    except ImportError:
        ta_out = os.path.join(output_folder, f"gridded_ta_day_{day}.gpkg")
        gpd.GeoDataFrame(ta_updated, geometry="geometry", crs=ta.crs).to_file(ta_out, driver="GPKG")

    # map-ready point layers
    lat_col = find_col(dip, "latitude") or "latitude"
    lon_col = find_col(dip, "longitude") or "longitude"
    map_layers = {}
    if lat_col in dip.columns and lon_col in dip.columns:
        pts = dip.dropna(subset=[lat_col, lon_col]).copy()
        pts[lat_col] = pd.to_numeric(pts[lat_col], errors="coerce")
        pts[lon_col] = pd.to_numeric(pts[lon_col], errors="coerce")
        pts = pts.dropna(subset=[lat_col, lon_col])
        gpts = gpd.GeoDataFrame(pts, geometry=gpd.points_from_xy(pts[lon_col], pts[lat_col]),
                                crs="EPSG:4326")
        for status, name in [("Visited", "visited"), ("Not Visited", "not_visited")]:
            sub = gpts[gpts[cum_col] == status]
            p = os.path.join(output_folder, f"{name}_day_{day}.geojson")
            if len(sub):
                sub.to_file(p, driver="GeoJSON")
                map_layers[name] = p
                print(f"Saved {p} ({len(sub):,})")

    return {"visitation_csv": out_csv, "ta_parquet": ta_out, "day_col": day_col,
            "cum_col": cum_col, "daily_col": daily_col, "map_layers": map_layers,
            "track_date": resolved_date}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="GTS settlement visitation analysis")
    ap.add_argument("--settlements", required=True)
    ap.add_argument("--tracks", required=True)
    ap.add_argument("--gridded-ta", required=True)
    ap.add_argument("--voronoi", required=True)
    ap.add_argument("--state", required=True)
    ap.add_argument("--day", type=int, required=True)
    ap.add_argument("--mopup", action="store_true")
    ap.add_argument("--output", required=True)
    ap.add_argument("--prev-ta", default=None, help="Previous day's gridded_ta parquet for cumulative")
    ap.add_argument("--prev-dip", default=None, help="Previous day's visitation CSV for cumulative")
    args = ap.parse_args()
    run_analysis(args.settlements, args.tracks, args.gridded_ta, args.voronoi,
                 args.state, args.day, args.mopup, args.output, args.prev_ta, args.prev_dip)
