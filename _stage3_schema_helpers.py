"""Helpers for robust GTS track-column detection and timestamp parsing."""
from __future__ import annotations

import re
from typing import Iterable

import pandas as pd


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(name).strip().lower()).strip()


def find_track_column(columns: Iterable[str], candidates: Iterable[str], *, required: bool = True) -> str | None:
    """Return the best matching source column using normalized aliases."""
    cols = list(columns)
    normalized = {_norm(c): c for c in cols}
    aliases = [_norm(c) for c in candidates]

    # Exact normalized match first.
    for alias in aliases:
        if alias in normalized:
            return normalized[alias]

    # Then tolerate extra words such as "2024", "Label", "UTC", etc.
    for alias in aliases:
        tokens = set(alias.split())
        for col in cols:
            ct = set(_norm(col).split())
            if tokens and tokens.issubset(ct):
                return col

    # Finally allow the distinctive keyword to appear in a longer header.
    for alias in aliases:
        for col in cols:
            if alias in _norm(col):
                return col

    if required:
        raise ValueError(
            "Could not detect a required GTS track column. "
            f"Tried aliases: {list(candidates)}. Available columns: {cols}"
        )
    return None


def detect_track_columns(columns: Iterable[str]) -> dict[str, str | None]:
    """Detect the LGA, team and GPS timestamp columns used by Stage 3."""
    cols = list(columns)
    return {
        "lga": find_track_column(
            cols,
            ["NGA LGA 2024 Label", "NGA LGA Label", "LGA Label", "LGA Name", "LGA"],
        ),
        "team_code": find_track_column(
            cols,
            ["Team Code", "TeamCode", "Team ID", "Team Identifier", "Team"],
        ),
        "ts": find_track_column(
            cols,
            [
                "GPS Timestamp (UTC)",
                "GPS Timestamp",
                "GPS Time",
                "Timestamp (UTC)",
                "Timestamp",
                "Date Time",
                "Datetime",
                "DateTime",
                "Recorded At",
            ],
        ),
    }


def parse_gps_timestamp(values: pd.Series) -> pd.Series:
    """Parse common GTS timestamp formats without assuming one exact format."""
    raw = values.astype("string").str.strip()
    parsed = pd.to_datetime(raw, errors="coerce", format="mixed")

    # A few older pandas/runtime combinations do not support format='mixed'.
    if parsed.notna().sum() == 0 and len(raw):
        parsed = pd.to_datetime(raw, errors="coerce")

    return parsed
