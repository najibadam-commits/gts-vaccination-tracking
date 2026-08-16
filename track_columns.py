"""Flexible column resolution for GTS track exports.

The GTS tracker/export schema has changed across releases. This module keeps
the analysis stages independent of those header changes by resolving columns
by meaning and normalising timestamps consistently.

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
    "team": ("team code", "team_code", "team id", "team_id", "team", "teamcode", "tracker team", "team name"),
    "ts": ("gps timestamp (utc)", "gps timestamp", "timestamp", "datetime", "date time", "date_time", "track time", "track timestamp", "recorded at", "recorded_at", "ts", "time"),
    "lat": ("lat", "latitude", "y", "gps latitude", "gps_latitude"),
    "lon": ("lon", "long", "longitude", "x", "gps longitude", "gps_longitude"),
    "state": ("nga state label", "state label", "state name", "state"),
    "lga": ("nga lga label", "lga label", "lga name", "lga", "local government area", "local government"),
    "ward": ("nga ward label", "ward label", "ward name", "ward"),
}
_ALIAS_NORM = {key: [_norm(v) for v in values] for key, values in ALIASES.items()}


def read_columns(path: str) -> list[str]:
    """Read only the column names without loading the track data."""
    ext = os.path.splitext(str(path))[1].lower()
    if ext in {".csv", ".txt"}:
        with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as fh:
            return next(csv.reader(fh))
    if ext == ".parquet":
        return list(pd.read_parquet(path).columns)
    import geopandas as gpd
    return list(gpd.read_file(path, rows=0).columns)


def _score(column: str, key: str) -> int:
    n = _norm(column)
    aliases = _ALIAS_NORM[key]
    if n in aliases:
        return 1000 - aliases.index(n)
    if key == "team" and "team" in n and "code" in n and "old" not in n:
        return 500
    if key == "ts" and any(x in n for x in ("timestamp", "datetime", "tracktime")):
        return 450
    if key == "lat" and "lat" in n and "status" not in n:
        return 400
    if key == "lon" and ("lon" in n or "long" in n) and "along" not in n:
        return 400
    if key in {"state", "lga", "ward"} and key in n and not any(x in n for x in ("code", "id", "old")):
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


def require(resolved: dict[str, str | None], fields: Iterable[str], headers: Iterable[str], context: str = "track analysis") -> None:
    missing = [f for f in fields if not resolved.get(f)]
    if not missing:
        return
    expected = {
        "team": "Team Code / Team ID", "ts": "timestamp / GPS Timestamp",
        "lat": "Lat / Latitude", "lon": "Lon / Longitude",
        "lga": "LGA / NGA LGA Label", "state": "State / NGA State Label",
        "ward": "Ward / NGA Ward Label",
    }
    details = "; ".join(f"{f}: {expected.get(f, f)}" for f in missing)
    raise ValueError(f"{context}: missing required track field(s): {details}. Available columns: [{', '.join(map(str, headers))}]")


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
        for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y %I:%M:%S %p", "%d/%m/%Y %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
            parsed2 = pd.to_datetime(s.loc[remaining], format=fmt, errors="coerce", utc=True)
            good = parsed2.notna()
            if good.any():
                out.loc[parsed2.index[good]] = parsed2.loc[good].dt.tz_convert("UTC").dt.tz_localize(None)
                remaining = out.isna() & s.notna()
                if not remaining.any():
                    break
    return out
