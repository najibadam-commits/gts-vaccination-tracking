"""Resolve the columns of a GTS track export, whatever the exporter called them.

Why this exists
---------------
Four places in the pipeline read the merged track export, and each hard-coded
the column names of one particular export:

    "Lat", "Lon", "NGA LGA 2024 Label", "Team Code", "GPS Timestamp (UTC)"

`pd.read_csv(usecols=lambda c: c in cols)` silently keeps NOTHING when those
names do not match, the subsequent rename is a no-op, and the failure surfaces
much later as

    KeyError: 'lga'

from a `groupby`, naming a column the analyst never typed and cannot find in
their file. That is what broke the first cloud run: the export's LGA column was
not spelled `NGA LGA 2024 Label`.

The label carries a YEAR — `NGA LGA 2024 Label` — so it changes with each
boundary release and was always going to drift. Matching on meaning rather than
on an exact string fixes the whole class.

`require()` produces the error the old code should have: which role could not
be filled, and every column the file actually has.
"""
from __future__ import annotations

import pandas as pd

# Role -> (exact names tried first, then substring rules).
#
# Exact matches are tried across every role before any substring match, so a
# file carrying both "Lat" and "Latitude_rounded" picks the plain one. The
# substring rules use ALL/ANY term groups rather than a single keyword: "lga"
# alone would match "LGA Code", and "time" alone would match "Time Zone".
_RULES: dict[str, tuple[tuple[str, ...], tuple[tuple[tuple[str, ...], tuple[str, ...]], ...]]] = {
    "lat":  (("lat", "latitude", "y"),
             ((("lat",), ()),)),
    "lon":  (("lon", "lng", "long", "longitude", "x"),
             ((("lon",), ()), (("lng",), ()))),
    "team": (("team code", "team_code", "teamcode", "team"),
             ((("team", "code"), ()), (("team",), ()))),
    "lga":  (("nga lga 2024 label", "lga", "lga name", "lga_name"),
             ((("lga",), ("code", "id", "pcode")),)),
    "ts":   (("gps timestamp (utc)", "gps timestamp", "timestamp", "datetime",
              "date time", "time"),
             ((("timestamp",), ()), (("gps", "time"), ()), (("date", "time"), ()))),
    "state": (("nga state 2024 label", "state", "state name", "state_name"),
              ((("state",), ("code", "id", "pcode")),)),
}

# Timestamp layouts seen in GTS exports, tried in order. The first is the
# format the pipeline has always assumed; the rest cover the common exporter
# variants. Anything else falls through to pandas' own parser.
_TS_FORMATS = (
    "%m/%d/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%m/%d/%Y %H:%M",
    "%Y/%m/%d %H:%M:%S",
)


def _norm(name) -> str:
    return " ".join(str(name).strip().lower().replace("_", " ").split())


def resolve(columns) -> dict[str, str | None]:
    """Map each role to the column that fills it, or None.

    Never raises — a caller that can work without a role (LGA, say) simply
    finds None there. Use `require` for the roles you cannot do without.
    """
    cols = list(columns)
    normed = {c: _norm(c) for c in cols}
    found: dict[str, str | None] = {}
    taken: set[str] = set()

    for role, (exact, _) in _RULES.items():
        for want in exact:
            hit = next((c for c in cols
                        if normed[c] == want and c not in taken), None)
            if hit is not None:
                found[role] = hit
                taken.add(hit)
                break

    for role, (_, patterns) in _RULES.items():
        if found.get(role):
            continue
        for all_terms, none_terms in patterns:
            hit = next((c for c in cols
                        if c not in taken
                        and all(t in normed[c] for t in all_terms)
                        and not any(t in normed[c] for t in none_terms)), None)
            if hit is not None:
                found[role] = hit
                taken.add(hit)
                break
        found.setdefault(role, None)

    return {role: found.get(role) for role in _RULES}


def require(resolved: dict[str, str | None], roles, columns, what: str) -> None:
    """Raise a message an analyst can act on, if a needed role is unfilled."""
    missing = [r for r in roles if not resolved.get(r)]
    if not missing:
        return
    labels = {"lat": "latitude", "lon": "longitude", "team": "team code",
              "lga": "LGA name", "ts": "GPS timestamp", "state": "state name"}
    raise ValueError(
        f"{what} needs a {', '.join(labels.get(r, r) for r in missing)} column "
        f"in the track export, and none of its columns look like one.\n"
        f"The file has: {', '.join(str(c) for c in columns)}\n"
        f"Rename the relevant column, or re-export the tracks with the "
        f"standard GTS fields.")


def read_columns(tracks_file: str) -> list[str]:
    """The export's header, read without loading the file."""
    return list(pd.read_csv(tracks_file, nrows=0).columns)


def parse_timestamps(series: pd.Series) -> pd.Series:
    """Parse GTS timestamps, trying the known layouts before inference.

    The pipeline previously hard-coded `%m/%d/%Y %H:%M:%S` with
    `errors="coerce"`, so an export in any other layout produced a column of
    NaT — no error, just every date silently missing, which would have made the
    day-scoping quietly select nothing.

    A format is accepted only if it parses most of the sample; otherwise the
    next is tried, and pandas' own parser is the last resort.
    """
    sample = series.dropna().head(200)
    if not len(sample):
        return pd.to_datetime(series, errors="coerce")

    for fmt in _TS_FORMATS:
        parsed = pd.to_datetime(sample, format=fmt, errors="coerce")
        if parsed.notna().mean() >= 0.9:
            return pd.to_datetime(series, format=fmt, errors="coerce")

    return pd.to_datetime(series, errors="coerce", dayfirst=False)


def describe(resolved: dict[str, str | None]) -> str:
    """One line naming the columns chosen, for the run log."""
    return " · ".join(f"{role}={name}" for role, name in resolved.items() if name)
