"""Attribute tracks to state and LGA by spatial overlay, not by name matching.

Why this module exists
----------------------
Every per-team figure in the report used to be derived by matching STRINGS —
the export's `NGA LGA 2024 Label` against the settlement list's `lga`, and the
export's `Team Code` against the settlement list's `team_code`. Both matches
fail, and both fail silently:

  * `time_spent_analysis` never scoped to the campaign state at all, so on a
    national GTS export it counted 157 LGAs across 16 states, then grouped by
    (LGA, team_code) so a team that crossed an LGA line counted twice, then
    added "no evidence of tracks" for every team code in the settlement list —
    3,067 of them, because NONE matched. Reported total: 4,213 teams. True
    figure for that campaign: 952.

  * `team_deploy_report` derived Teams Reported the same way and returned
    4,960; with a manually entered 200 deployed, Pending clipped to 0. From the
    other side, when nothing matched at all, Reported collapsed to 0 and
    Pending came back equal to Deployed.

The team-code namespaces genuinely do not overlap. The settlement list uses
`GOR-025`, `TWD-202`; the export uses `ZAM/BUG/BEL/003`, `ZM/BKM/BKM/023`. The
raw intersection is 0 of 3,067, and reducing the export code to its last two
segments recovers 11.7%. So no string rule can join them, and the fix is not a
better string rule — it is to stop asking the strings and ask the geometry.

What this does instead
----------------------
Each valid ping is a point. It is intersected with the state and LGA boundary
layers the pipeline already ships (`state.sqlite`, `LGA.sqlite`), and the team
and device are attributed to wherever their points actually fell. Checked
against a real April 2026 export: 952 teams fall inside the Zamfara polygon
against 951 carrying the export's own Zamfara label — the two agree, which is
what makes the spatial figure trustworthy rather than merely different.

Counting rules
--------------
STATE level counts each team ONCE, however many LGAs it worked or pings it
sent. This is the figure the Time Spent and Team Deployed slides quote.

LGA level counts a team in EVERY LGA it entered, by explicit choice. Field
teams do cross boundaries and an LGA table that hides that is misleading about
where work happened. The consequence is arithmetic, not a fault: the LGA column
sums to more than the state total. `meta["lga_sum_teams"]` carries that sum so
the slide can say so in its own caption rather than leaving a reader to notice
the discrepancy and distrust the deck.

Invalid fixes (`Valid = 0`) are dropped before anything is counted, and
"tracks" means sessions on the same 15-minute gap rule the rest of the pipeline
uses — see `team_sessions`. Pings are carried alongside so every figure
reconciles back to a row count in the source file.
"""
from __future__ import annotations

import argparse
import os

import pandas as pd

LOCAL_OFFSET = pd.Timedelta(hours=1)

# Boundary layers shipped with the pipeline, and the column in each that holds
# the name. Resolved by trying these in order, so a re-exported layer with a
# different spelling does not need a code change.
STATE_NAME_COLS = ("statename", "state_name", "state", "admin1name", "name")
LGA_NAME_COLS = ("lganame", "lga_name", "lga", "admin2name", "name")


def state_of_interest(visitation, states=None) -> str:
    """The campaign's state, taken from the PLANNED SETTLEMENTS.

    The state is a property of what was planned, not of what was transmitted.
    Deriving it from the settlement list means the analysis cannot be widened
    by tracks that happen to be in the export — a national GTS export carries
    sixteen states, and "the state we are reporting on" is decided by the plan.

    Raises rather than guessing when the settlement list names more than one
    state: a two-state plan is a real situation and silently picking the first
    would report one of them as if it were the campaign.
    """
    dip = (pd.read_csv(visitation, low_memory=False)
           if isinstance(visitation, str) else visitation)
    col = next((c for c in dip.columns
                if "state" in str(c).lower()
                and not any(x in str(c).lower() for x in ("code", "id"))), None)
    if col is None:
        raise ValueError(
            "The planned settlement list has no state column, so the state of "
            f"interest cannot be derived from it. It has: {list(dip.columns)[:20]}…")

    names = sorted(set(_norm(dip[col].dropna())) - {"", "Nan", "None"})
    if not names:
        raise ValueError(f"The settlement list's '{col}' column is empty.")
    if len(names) > 1:
        raise ValueError(
            f"The planned settlement list covers more than one state "
            f"({', '.join(names)}). Split the run by state, or pass the state "
            f"explicitly — this analysis reports on one state at a time.")

    name = names[0]
    if states is not None and name not in set(states["state"]):
        raise ValueError(
            f"'{name}' comes from the settlement list but is not in the state "
            f"boundary layer, so its tracks cannot be selected spatially. The "
            f"layer has: {', '.join(sorted(states['state'].unique())[:12])}…")
    return name


def _name_col(gdf, candidates, what):
    lower = {str(c).lower(): c for c in gdf.columns}
    for cand in candidates:
        if cand in lower:
            return lower[cand]
    raise ValueError(
        f"Could not find the {what} name column in the boundary layer. "
        f"Tried {candidates}; the layer has {list(gdf.columns)}.")


def _norm(series):
    return (series.astype(str).str.replace("_", " ", regex=False)
            .str.replace(r"\s+", " ", regex=True).str.strip().str.title())


def load_boundaries(state_path: str | None, lga_path: str | None):
    """Read the two boundary layers, keeping only name + geometry."""
    import geopandas as gpd

    states = lgas = None
    if state_path and os.path.exists(state_path):
        g = gpd.read_file(state_path)
        col = _name_col(g, STATE_NAME_COLS, "state")
        states = g[[col, "geometry"]].rename(columns={col: "state"})
        states["state"] = _norm(states["state"])
        states = states.to_crs("EPSG:4326") if states.crs else states.set_crs("EPSG:4326")
    if lga_path and os.path.exists(lga_path):
        g = gpd.read_file(lga_path)
        col = _name_col(g, LGA_NAME_COLS, "LGA")
        lgas = g[[col, "geometry"]].rename(columns={col: "lga"})
        lgas["lga"] = _norm(lgas["lga"])
        lgas = lgas.to_crs("EPSG:4326") if lgas.crs else lgas.set_crs("EPSG:4326")
    return states, lgas


def attribute_pings(tracks_file: str, states=None, lgas=None,
                    state_name: str | None = None,
                    chunksize: int = 500_000) -> pd.DataFrame:
    """Every valid ping, with the state and LGA its COORDINATES fall in.

    Returns columns: team_code, device, state, lga, _ts.

    Read and joined in chunks: a national export runs to millions of rows and
    the point layer for all of them at once does not need to exist. Restricting
    to `state_name` inside the loop is what keeps the working set small.
    """
    import geopandas as gpd

    from track_columns import (parse_timestamps, read_columns, require,
                               resolve as resolve_cols, valid_mask)

    header = read_columns(tracks_file)
    cols = resolve_cols(header)
    require(cols, ("team", "ts", "lat", "lon"), header, "Spatial track attribution")
    ts_c, team_c = cols["ts"], cols["team"]
    lat_c, lon_c, dev_c, valid_c = cols["lat"], cols["lon"], cols["device"], cols["valid"]

    want = {c for c in (ts_c, team_c, lat_c, lon_c, dev_c, valid_c) if c}
    if state_name:
        state_name = _norm(pd.Series([state_name])).iloc[0]

    scope_poly = None
    if states is not None and state_name:
        scope_poly = states[states["state"] == state_name]
        if not len(scope_poly):
            raise ValueError(
                f"'{state_name}' is not in the state boundary layer. It has: "
                f"{', '.join(sorted(states['state'].unique())[:12])}…")

    parts = []
    attribute_pings._assigned = 0
    seen = dropped_invalid = no_fix = no_identifier = 0
    for chunk in pd.read_csv(tracks_file, usecols=lambda c: c in want,
                             chunksize=chunksize, dtype=str):
        seen += len(chunk)
        # A row is usable if it carries EITHER identifier. Dropping every row
        # without a team code — which is what this used to do — silently threw
        # away 36,928 pings from 212 devices that were transmitting with no
        # team code assigned. They cannot be counted as teams, but they are
        # real devices in the field and belong in the device figures.
        has_team = chunk[team_c].notna() & (chunk[team_c].astype(str).str.strip() != "")
        has_dev = (chunk[dev_c].notna() & (chunk[dev_c].astype(str).str.strip() != "")
                   if dev_c and dev_c in chunk.columns
                   else pd.Series(False, index=chunk.index))
        usable = has_team | has_dev
        no_identifier += int((~usable).sum())
        chunk = chunk[usable]

        if valid_c and valid_c in chunk.columns:
            ok = valid_mask(chunk[valid_c])
            dropped_invalid += int((~ok).sum())
            chunk = chunk[ok]

        lat = pd.to_numeric(chunk[lat_c], errors="coerce")
        lon = pd.to_numeric(chunk[lon_c], errors="coerce")
        good = lat.notna() & lon.notna() & lat.between(-90, 90) & lon.between(-180, 180)
        no_fix += int((~good).sum())
        chunk, lat, lon = chunk[good], lat[good], lon[good]
        if not len(chunk):
            continue

        pts = gpd.GeoDataFrame(
            pd.DataFrame({
                "team_code": chunk[team_c].astype(str).str.strip(),
                "device": (chunk[dev_c].astype(str).str.strip()
                           if dev_c and dev_c in chunk.columns else ""),
                "_ts": parse_timestamps(chunk[ts_c]) + LOCAL_OFFSET,
            }),
            geometry=gpd.points_from_xy(lon, lat), crs="EPSG:4326")

        if states is not None:
            # Joined against EVERY state, then narrowed — not against the focal
            # polygon alone. Joining only to the focal state makes "tracks
            # assigned to a state" and "tracks in the state of interest" the
            # same number, so the validation cannot show how much of the export
            # the overlay actually placed. The spatial index makes 37 polygons
            # cost essentially the same as one.
            pts = gpd.sjoin(pts, states, how="left", predicate="within")
            pts = pts.drop(columns=[c for c in ("index_right",) if c in pts.columns])
            assigned_here = int(pts["state"].notna().sum())
            attribute_pings._assigned = getattr(attribute_pings, "_assigned", 0) + assigned_here
            if scope_poly is not None:
                pts = pts[pts["state"] == state_name]
        else:
            pts["state"] = state_name or ""

        if not len(pts):
            continue

        if lgas is not None:
            pts = gpd.sjoin(pts, lgas, how="left", predicate="within")
            pts = pts.drop(columns=[c for c in ("index_right",) if c in pts.columns])
        else:
            pts["lga"] = ""

        parts.append(pd.DataFrame(pts.drop(columns="geometry")))

    attribute_pings.last_counts = {
        "rows_read": seen,
        "assigned_to_a_state": attribute_pings._assigned,
        "dropped_invalid": dropped_invalid,
        "dropped_no_fix": no_fix,
        "dropped_no_identifier": no_identifier,
        "valid_read": seen - dropped_invalid - no_fix - no_identifier,
    }
    if dropped_invalid:
        print(f"  {dropped_invalid:,} of {seen:,} pings dropped as Valid = 0")
    if no_fix:
        print(f"  {no_fix:,} pings dropped for missing or out-of-range coordinates")
    if no_identifier:
        print(f"  {no_identifier:,} pings dropped with neither a team code nor "
              f"a device id")

    if not parts:
        return pd.DataFrame(columns=["team_code", "device", "state", "lga", "_ts"])

    out = pd.concat(parts, ignore_index=True).dropna(subset=["_ts"])
    out["lga"] = out["lga"].fillna("").astype(str)
    out["team_code"] = out["team_code"].fillna("").replace("nan", "")
    out["device"] = out["device"].fillna("").replace("nan", "")
    if state_name:
        print(f"  spatial attribution: {len(out):,} valid pings fall inside "
              f"{state_name}, from {out['team_code'].nunique():,} team codes "
              f"and {out['device'].nunique():,} devices")
    return out


def _sessions_by(pings: pd.DataFrame, keys: list[str]) -> pd.Series:
    """Session count per group, on the pipeline's shared gap rule."""
    from team_sessions import _sessions
    return pings.groupby(keys, dropna=False)["_ts"].apply(_sessions)


def analyse(tracks_file: str, state_name: str,
            state_path: str | None = None, lga_path: str | None = None,
            report_date: str | None = "latest",
            chunksize: int = 500_000) -> dict:
    """State-scoped team/device attribution, plus the two per-LGA tables.

    Returns:
        pings        the attributed ping frame, for callers wanting the detail
        by_lga_team  LGA / Teams / Tracks / Pings, keyed on Team Code
        by_lga_dev   LGA / Devices / Tracks / Pings, keyed on IMEI
        meta         state totals, counted once per team however many LGAs
    """
    states, lgas = load_boundaries(state_path, lga_path)
    pings = attribute_pings(tracks_file, states, lgas, state_name, chunksize)

    if not len(pings):
        empty = pd.DataFrame(columns=["LGA", "Teams", "Tracks", "Pings"])
        return {"pings": pings, "by_lga_team": empty, "by_lga_dev": empty,
                "meta": {"state": state_name, "date": None, "total_teams": 0,
                         "total_devices": 0, "total_pings": 0, "total_tracks": 0,
                         "lga_sum_teams": 0, "lga_sum_devices": 0,
                         "multi_lga_teams": 0, "n_lgas": 0}}

    pings["date"] = pings["_ts"].dt.strftime("%Y-%m-%d")
    dates_available = sorted(pings["date"].dropna().unique().tolist())
    in_state_all_days = int(len(pings))

    # THE DAY FILTER. Applied to the spatially-attributed pings, so the
    # population is "this state, this day" and can never carry a previous
    # day's teams forward. "latest" resolves to the most recent date that has
    # tracks INSIDE the state of interest — not the export's last date, which
    # on a national file may belong to another state entirely.
    chosen = (str(pings["date"].max()) if report_date == "latest" else report_date)
    if chosen:
        if chosen not in dates_available:
            print(f"  WARNING: no track inside {state_name} on {chosen}. "
                  f"Dates with tracks in this state: "
                  f"{', '.join(dates_available) or '(none)'}")
        pings = pings[pings["date"] == chosen]
        print(f"  day filter: {len(pings):,} of {in_state_all_days:,} in-state "
              f"pings are from {chosen} "
              f"({len(dates_available)} date(s) available in this state)")

    # --- state level: every team counted ONCE, whatever it crossed.
    # Teams and devices are counted over DIFFERENT row sets: a ping with a
    # device but no team code is a real device transmitting and belongs in the
    # device figure, but there is no team to attribute it to.
    teamed = pings[pings["team_code"] != ""]
    deviced = pings[pings["device"] != ""]
    total_teams = int(teamed["team_code"].nunique())
    total_devices = int(deviced["device"].nunique())
    state_tracks = int(_sessions_by(teamed, ["team_code"]).sum()) if len(teamed) else 0
    unassigned_devices = int(
        deviced[deviced["team_code"] == ""]["device"].nunique())
    unassigned_pings = int((pings["team_code"] == "").sum())

    # --- LGA level: a team is counted in EVERY LGA it entered (see module
    # docstring). Blank LGA means the point fell outside every polygon in the
    # layer — kept and labelled rather than silently dropped, because a large
    # "Unattributed" row is a boundary-layer problem worth seeing.
    named = pings.copy()
    named["lga"] = named["lga"].replace("", "Unattributed")
    named_team = named[named["team_code"] != ""]

    by_lga_team = (named_team.groupby("lga")
                   .agg(Teams=("team_code", "nunique"), Pings=("_ts", "size"))
                   .reset_index().rename(columns={"lga": "LGA"}))
    tracks_t = _sessions_by(named_team, ["lga", "team_code"]).groupby(level=0).sum()
    by_lga_team["Tracks"] = by_lga_team["LGA"].map(tracks_t).fillna(0).astype(int)
    by_lga_team = by_lga_team[["LGA", "Teams", "Tracks", "Pings"]].sort_values(
        "Teams", ascending=False).reset_index(drop=True)

    dev = named[named["device"] != ""]
    if len(dev):
        by_lga_dev = (dev.groupby("lga")
                      .agg(Devices=("device", "nunique"), Pings=("_ts", "size"))
                      .reset_index().rename(columns={"lga": "LGA"}))
        tracks_d = _sessions_by(dev, ["lga", "device"]).groupby(level=0).sum()
        by_lga_dev["Tracks"] = by_lga_dev["LGA"].map(tracks_d).fillna(0).astype(int)
        by_lga_dev = by_lga_dev[["LGA", "Devices", "Tracks", "Pings"]].sort_values(
            "Devices", ascending=False).reset_index(drop=True)
    else:
        by_lga_dev = pd.DataFrame(columns=["LGA", "Devices", "Tracks", "Pings"])

    per_team_lgas = named_team.groupby("team_code")["lga"].nunique()

    counts = getattr(attribute_pings, "last_counts", {})
    meta = {
        "state": state_name,
        "date": chosen,
        "dates_available_in_state": dates_available,
        # Step counts for the validation table — each one a stage of
        # day -> track -> overlay -> state of interest -> unique team.
        "pings_read": counts.get("rows_read", 0),
        "pings_valid": counts.get("valid_read", 0),
        "pings_in_a_state": counts.get("assigned_to_a_state", 0),
        "pings_in_state_all_days": in_state_all_days,
        "total_teams": total_teams,
        "total_devices": total_devices,
        "total_pings": int(len(pings)),
        "total_tracks": state_tracks,
        "n_lgas": int(named["lga"].nunique()),
        # The LGA columns sum higher than the state totals because a team that
        # worked two LGAs appears in both. Carried so the slide states it.
        "lga_sum_teams": int(by_lga_team["Teams"].sum()),
        "lga_sum_devices": int(by_lga_dev["Devices"].sum()) if len(by_lga_dev) else 0,
        "multi_lga_teams": int((per_team_lgas > 1).sum()),
        "unattributed_pings": int((named["lga"] == "Unattributed").sum()),
        # Devices transmitting with no team code assigned. Counted as devices,
        # excluded from team figures, and reported so the gap between the two
        # totals is explained rather than noticed.
        "unassigned_devices": unassigned_devices,
        "unassigned_pings": unassigned_pings,
    }

    print(f"  {state_name}: {total_teams:,} unique teams, "
          f"{total_devices:,} unique devices, {state_tracks:,} tracks, "
          f"{meta['total_pings']:,} pings across {meta['n_lgas']} LGAs "
          f"({meta['multi_lga_teams']:,} teams crossed an LGA boundary)")

    result = {"pings": pings, "by_lga_team": by_lga_team,
              "by_lga_dev": by_lga_dev, "meta": meta}
    # Time spent is computed HERE, from the pings this function has already
    # filtered to the day and the state, so it cannot be run against a wider
    # population by a caller that forgets to scope it.
    result["time_spent"] = time_spent(pings, unit="team")
    result["time_spent_device"] = time_spent(pings, unit="device")
    ts = result["time_spent"]["meta"]
    print(f"  time spent ({state_name}, {chosen}): {ts['total']:,} teams, "
          f"median {ts['median_minutes']:.0f} min, {ts['under_12']:,} under 12 "
          f"({ts['under_12_pct']:.1%})")
    return result


def time_spent(pings: pd.DataFrame, unit: str = "team") -> dict:
    """Time spent in the field, from the day's in-state tracks and nothing else.

    The order of operations is the point:

        selected day -> track -> state boundary overlay -> state of interest
        -> unique team/device -> time spent

    `pings` must already be the output of `analyse`, which applies the first
    four steps. This applies the last two. Nothing here re-reads the export and
    nothing here can widen the population: a team appears if and only if it has
    at least one valid ping inside the state of interest on the selected day.

    Why this is not `team_daily_time.analyse`
    -----------------------------------------
    That function takes a scope of team CODES and then measures each team's
    minutes from every ping it sent that day — including pings outside the
    state. Passing it a spatially-derived team list therefore gave the right
    POPULATION but could still include out-of-state field time in the
    durations. Measuring from the attributed pings closes that gap.

    The measurement itself is unchanged and deliberately imported rather than
    restated: the same `MAX_GAP_MINUTES` gap rule, the same
    `SINGLE_PING_MINUTES` credit and the same `classify_minutes` bands the
    report has always used, so the methodology is preserved exactly while its
    input is corrected.
    """
    from team_daily_time import (MAX_GAP_MINUTES, SINGLE_PING_MINUTES,
                                 TIME_RANGE_ORDER, UNDER_12, classify_minutes)

    key = "device" if unit == "device" else "team_code"
    work = pings[pings[key] != ""] if len(pings) else pings
    if not len(work):
        return {"per_team": pd.DataFrame(columns=[key, "lga", "Pings",
                                                  "Minutes", "Time Spent Range"]),
                "distribution": pd.DataFrame(columns=["Time Spent Range", "Teams"]),
                "meta": {"unit": unit, "total": 0, "median_minutes": 0.0,
                         "under_12": 0, "under_12_pct": 0.0,
                         "max_gap_minutes": MAX_GAP_MINUTES}}

    def _minutes(times: pd.Series) -> float:
        ordered = times.sort_values()
        if len(ordered) <= 1:
            return SINGLE_PING_MINUTES
        gaps = ordered.diff().dropna().dt.total_seconds() / 60.0
        return float(gaps[gaps <= MAX_GAP_MINUTES].sum())

    grouped = work.groupby(key, dropna=False)
    per = grouped.agg(Pings=("_ts", "size"),
                      lga=("lga", lambda s: s.value_counts().idxmax())).reset_index()
    per["Minutes"] = grouped["_ts"].apply(_minutes).round(1).values
    per["Time Spent Range"] = per["Minutes"].apply(classify_minutes)

    counts = (per.groupby("Time Spent Range")[key].nunique()
              .reindex(TIME_RANGE_ORDER).fillna(0).astype(int))
    distribution = counts.reset_index(name="Teams")

    # Reconciliation, raised rather than logged (ported from
    # Time_Spent_Analysis_state_specific.py). Every band is a count of unique
    # teams for one day, so the bands must sum to the number of unique teams —
    # no more, no less. If they ever exceed it a team has been counted twice,
    # which is the single failure mode that made this analysis report 4,213
    # teams, and it is better to stop the run than to publish the deck.
    band_total = int(distribution["Teams"].sum())
    unique_units = int(work[key].nunique())
    if band_total != unique_units:
        raise AssertionError(
            f"Time-spent reconciliation failed: bands sum to {band_total:,} "
            f"but there are {unique_units:,} unique {unit}s on this day. "
            f"A difference means a {unit} has been counted in more than one "
            f"band, or dropped from all of them.")

    total = int(per[key].nunique())
    n_u12 = int(counts.get(UNDER_12, 0))
    meta = {
        "unit": unit,
        "total": total,
        "median_minutes": float(per["Minutes"].median()),
        "under_12": n_u12,
        "under_12_pct": (n_u12 / total) if total else 0.0,
        "max_gap_minutes": MAX_GAP_MINUTES,
    }
    return {"per_team": per, "distribution": distribution, "meta": meta}


def validate(result: dict) -> pd.DataFrame:
    """The reconciliation the analysis has to survive before it is published.

    Each row is a count taken at one step of the hierarchy, so a reader can
    follow a figure from the raw export to the slide. The three `OK/FAIL` rows
    are assertions, not counts: they are the failure modes this rewrite exists
    to rule out.
    """
    m = result["meta"]
    ts = result.get("time_spent", {}).get("meta", {})
    pings = result.get("pings")

    dup_free = True
    cross_day = False
    out_of_state = False
    if pings is not None and len(pings):
        teamed = pings[pings["team_code"] != ""]
        # One row per team in the per-team table is what "no duplicate
        # counting" means operationally.
        per_team = result.get("time_spent", {}).get("per_team")
        if per_team is not None and len(per_team) and "team_code" in per_team:
            dup_free = int(per_team["team_code"].nunique()) == len(per_team)
        cross_day = pings["date"].nunique() > 1 if "date" in pings else False
        out_of_state = ("state" in pings
                        and pings["state"].nunique(dropna=True) > 1)

    rows = [
        ("Selected day", m["date"], "the one date every figure below is built from"),
        ("State of interest", m["state"], "from the planned settlement list"),
        ("Rows read from the export", m.get("pings_read", "—"),
         "every row in merged_tracks.csv"),
        ("Tracks before spatial overlay", m.get("pings_valid", "—"),
         "valid fixes with usable coordinates and an identifier"),
        ("Tracks assigned to a state", m.get("pings_in_a_state", "—"),
         "pings falling inside any state polygon"),
        ("Tracks within the state of interest", m.get("pings_in_state_all_days", "—"),
         "inside the state polygon, all days"),
        ("Tracks on the selected day", m["total_pings"],
         "the analysis population — day AND state"),
        ("Unique teams in the state that day", m["total_teams"],
         "distinct Team Code, counted once"),
        ("Unique devices in the state that day", m["total_devices"],
         "distinct IMEI, counted once"),
        ("Teams in the Time Spent Analysis", ts.get("total", 0),
         "teams with measurable field time on the day"),
        ("No duplicate team counting", "OK" if dup_free else "FAIL",
         "one row per team in the per-team table"),
        ("No cross-day contamination", "OK" if not cross_day else "FAIL",
         "every ping carries the selected date"),
        ("No out-of-state tracks", "OK" if not out_of_state else "FAIL",
         "every ping intersects the state of interest"),
    ]
    return pd.DataFrame(rows, columns=["Check", "Value", "How it is derived"])


def reported_team_codes(result: dict) -> set:
    """Team codes with at least one valid track inside the campaign state.

    This is Teams Reported. It cannot be derived by joining to the settlement
    list's team codes — see the module docstring — so it is derived from where
    the tracks actually are, deduplicated by team code.
    """
    pings = result.get("pings")
    if pings is None or not len(pings):
        return set()
    return set(pings["team_code"].astype(str).str.strip()) - {""}


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(
        description="Attribute tracks to state and LGA by spatial overlay")
    ap.add_argument("--tracks", required=True, help="merged_tracks.csv")
    ap.add_argument("--state", required=True)
    ap.add_argument("--state-boundaries", default=os.path.join(here, "state.sqlite"))
    ap.add_argument("--lga-boundaries", default=os.path.join(here, "LGA.sqlite"))
    ap.add_argument("--date", default="latest")
    ap.add_argument("--output", default="Track_Attribution.xlsx")
    a = ap.parse_args()

    res = analyse(a.tracks, a.state, a.state_boundaries, a.lga_boundaries,
                  None if a.date == "all" else a.date)
    m = res["meta"]
    print()
    print(res["by_lga_team"].to_string(index=False))
    print()
    print(res["by_lga_dev"].to_string(index=False))
    with pd.ExcelWriter(a.output, engine="xlsxwriter") as xl:
        res["by_lga_team"].to_excel(xl, sheet_name="Tracks by LGA (Team)", index=False)
        res["by_lga_dev"].to_excel(xl, sheet_name="Tracks by LGA (Device)", index=False)
        pd.DataFrame([m]).T.to_excel(xl, sheet_name="Validation")
    print(f"\nSaved {os.path.abspath(a.output)}")
