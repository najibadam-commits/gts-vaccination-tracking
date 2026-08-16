"""Flexible column resolution for GTS track exports.

This module makes track ingestion tolerant of schema/header changes across
tracker/export releases. It resolves fields by meaning, validates that the
resolved fields actually contain usable values, and parses mixed timestamp
formats consistently.

Canonical keys returned by ``resolve``:
    team, ts, lat, lon, state, lga, ward
"""

from __future__ import annotations

import csv
import os
import re
from typing import Iterable

import pandas as pd


def _norm(name: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name).strip().lower())


ALIASES = {
    "team": (
        "team code", "team_code", "team id", "team_id", "team",
        "teamcode", "tracker team", "team name", "teamname",
        "team identifier", "team identifier code", "teamid",
    ),
    "ts": (
        "gps timestamp (utc)", "gps timestamp", "timestamp", "datetime",
        "date time", "date_time", "track time", "track timestamp",
        "recorded at", "recorded_at", "recorded time", "recorded_time",
        "capture time", "capture_time", "event time", "event_time",
        "created at", "created_at", "time", "ts", "date",
    ),
    "lat": (
        "lat", "latitude", "y", "gps latitude", "gps_latitude",
        "gps lat", "gps_lat", "location latitude", "latitude decimal",
    ),
    "lon": (
        "lon", "long", "longitude", "x", "gps longitude", "gps_longitude",
        "gps long", "gps_long", "location longitude", "longitude decimal",
    ),
    "state": (
        "nga state label", "state label", "state name", "state",
        "state_name", "admin1", "admin 1",
    ),
    "lga": (
        "nga lga label", "lga label", "lga name", "lga",
        "local government area", "local government", "lga_name",
        "admin2", "admin 2",
    ),
    "ward": (
        "nga ward label", "ward label", "ward name", "ward",
        "ward_name", "admin3", "admin 3",
    ),
}

_ALIAS_NORM = {key: [_norm(v) for v in values] for key, values in ALIASES.items()}


def read_columns(path: str) -> list[str]:
    """Read only the column names without loading the track data."""
    ext = os.path.splitext(str(path))[1].lower()
    if ext in {".csv", ".txt"}:
        with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as fh:
            return next(csv.reader(fh), [])
    if ext == ".parquet":
        return list(pd.read_parquet(path).columns)
    import geopandas as gpd
    return list(gpd.read_file(path, rows=0).columns)


def _score(column: str, key: str) -> int:
    n = _norm(column)
    aliases = _ALIAS_NORM[key]

    if n in aliases:
        return 1000 - aliases.index(n)

    if key == "team":
        if "team" in n and any(x in n for x in ("code", "id", "name", "identifier")):
            return 600
        if "team" in n and not any(x in n for x in ("old", "status")):
            return 500

    if key == "ts":
        if any(x in n for x in (
            "timestamp", "datetime", "tracktime", "recordedat",
            "recordedtime", "capturetime", "eventtime", "createdat"
        )):
            return 500
        if n in {"date", "time"}:
            return 300

    if key == "lat" and "lat" in n and "status" not in n:
        return 400

    if key == "lon" and ("lon" in n or "long" in n) and "along" not in n:
        return 400

    if key in {"state", "lga", "ward"} and key in n and not any(
        x in n for x in ("code", "id", "old")
    ):
        return 350

    return 0


def resolve(headers: Iterable[str]) -> dict[str, str | None]:
    """Resolve semantic track fields to actual source headers."""
    headers = list(headers)
    out: dict[str, str | None] = {}

    for key in ALIASES:
        ranked = sorted(
            ((score, col) for col in headers if (score := _score(col, key)) > 0),
            key=lambda item: (-item[0], headers.index(item[1])),
        )
        out[key] = ranked[0][1] if ranked else None

    return out


def _sample_values(path: str, columns: list[str], nrows: int = 100) -> pd.DataFrame:
    """Read a small sample only; used for schema validation."""
    ext = os.path.splitext(str(path))[1].lower()

    if ext in {".csv", ".txt"}:
        return pd.read_csv(
            path, nrows=nrows, encoding="utf-8-sig",
            encoding_errors="replace", dtype="string"
        )

    if ext == ".parquet":
        return pd.read_parquet(path, columns=columns).head(nrows)

    import geopandas as gpd
    return gpd.read_file(path, rows=nrows)[columns]


def has_usable_values(series: pd.Series) -> bool:
    """Return True when a field contains at least one meaningful value."""
    if series is None:
        return False
    s = series.astype("string").str.strip()
    return bool(s.notna().any() and (s != "").any())


def validate_resolved(
    path: str,
    resolved: dict[str, str | None],
    require_team: bool = True,
    require_timestamp: bool = True,
) -> tuple[bool, dict[str, object]]:
    """Validate resolved fields against a small sample of actual file data."""
    headers = read_columns(path)
    wanted = [resolved.get("team"), resolved.get("ts"), resolved.get("lat"), resolved.get("lon")]
    wanted = [c for c in wanted if c and c in headers]

    try:
        sample = _sample_values(path, wanted, nrows=100) if wanted else pd.DataFrame()
    except Exception as exc:
        return False, {"reason": f"sample read failed: {exc}"}

    details: dict[str, object] = {"rows_sampled": len(sample)}

    team_ok = bool(resolved.get("team")) and has_usable_values(
        sample[resolved["team"]] if resolved.get("team") in sample.columns else pd.Series(dtype="string")
    )

    ts_ok = False
    parsed_count = 0
    if resolved.get("ts") in sample.columns:
        parsed = parse_timestamps(sample[resolved["ts"]])
        parsed_count = int(parsed.notna().sum())
        ts_ok = parsed_count > 0

    details["team_ok"] = team_ok
    details["timestamp_ok"] = ts_ok
    details["timestamp_values_parsed"] = parsed_count

    ok = (team_ok or not require_team) and (ts_ok or not require_timestamp)
    return ok, details


def require(
    resolved: dict[str, str | None],
    fields: Iterable[str],
    headers: Iterable[str],
    context: str = "track analysis",
) -> None:
    missing = [f for f in fields if not resolved.get(f)]
    if not missing:
        return

    expected = {
        "team": "Team Code / Team ID / Team Name",
        "ts": "timestamp / GPS Timestamp / Date Time / Recorded At",
        "lat": "Lat / Latitude", "lon": "Lon / Longitude",
        "lga": "LGA / NGA LGA Label", "state": "State / NGA State Label",
        "ward": "Ward / NGA Ward Label",
    }
    details = "; ".join(f"{f}: {expected.get(f, f)}" for f in missing)
    raise ValueError(
        f"{context}: missing required track field(s): {details}. "
        f"Available columns: [{', '.join(map(str, headers))}]"
    )


def parse_timestamps(series: pd.Series) -> pd.Series:
    """Parse mixed tracker timestamp formats into timezone-naive UTC datetimes."""
    s = series.astype("string").str.strip()
    out = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")

    try:
        parsed = pd.to_datetime(s, errors="coerce", utc=True, format="mixed")
    except (TypeError, ValueError):
        parsed = pd.to_datetime(s, errors="coerce", utc=True)

    valid = parsed.notna()
    if valid.any():
        out.loc[valid] = parsed.loc[valid].dt.tz_convert("UTC").dt.tz_localize(None)

    remaining = ~valid
    if remaining.any():
        formats = (
            "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M",
            "%m/%d/%Y %I:%M:%S %p", "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y %H:%M", "%Y/%m/%d %H:%M:%S",
            "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
        )
        for fmt in formats:
            parsed2 = pd.to_datetime(s.loc[remaining], format=fmt, errors="coerce", utc=True)
            good = parsed2.notna()
            if good.any():
                out.loc[parsed2.index[good]] = parsed2.loc[good].dt.tz_convert("UTC").dt.tz_localize(None)
                remaining = out.isna() & s.notna()
                if not remaining.any():
                    break

    return out
