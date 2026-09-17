"""The ONE rule for whether a planned settlement carries usable coordinates.

Two stages of this pipeline count "settlements with geo-coordinates": the ERM
workbook's Post-Campaign Analysis tab and the Post-Implementation Report's
completeness slides. They used to decide it separately — the workbook asked
only whether the two cells were non-null, the report also rejected blanks,
text, (0, 0) and out-of-range values — so the same campaign could be reported
as having 8,231 mappable settlements on a slide and 8,290 in the workbook, with
nothing to tell a reader which was right.

One rule, in one place, imported by both. It lives in its own module rather
than in either of them because `pir_analysis` already imports from
`stage3_erm_workbook`, so the workbook cannot import it back, and because the
rule needs nothing but pandas — putting it in `stage2_analysis` would drag
geopandas into the workbook stage for the sake of ten lines.

The rule
--------
A settlement counts as having usable coordinates when a latitude AND a
longitude column exist; both values parse as numbers, so a blank cell, a null,
a placeholder like "N/A" and any other text do not count; and both fall inside
the valid range for their axis, so a transposed or corrupted pair — a latitude
of 601, a longitude typed into the latitude column — is rejected rather than
plotted into the sea. (0, 0) is treated as missing: it is the null island the
GTS export writes for a device that never got a fix, not a settlement in the
Gulf of Guinea.

What this module never does
---------------------------
It never looks at visit status, tracks, coverage or anything else about
implementation, and nothing here filters rows. Coordinate availability is a
property of the settlement LIST and must be decided independently of what the
teams did; the mask LABELS rows so both callers can count with and without
against the same planned denominator. A settlement with no coordinates stays
in every planned total in the report.
"""
from __future__ import annotations

import re

import pandas as pd

# Values that are present in the file but mean "no coordinate". Compared
# case-folded after stripping, so " N/A " and "n/a" are the same thing.
BLANKS = frozenset({"", "nan", "none", "null", "n/a", "na", "#n/a", "-", "--",
                    "nil", "unknown"})
LAT_RANGE = (-90.0, 90.0)
LON_RANGE = (-180.0, 180.0)


def find_coordinate_columns(df: pd.DataFrame) -> tuple[str | None, str | None]:
    """The latitude and longitude columns, or (None, None).

    Same matching rule as `stage2_analysis.find_col` — first column whose name
    contains the keyword, skipping code/id/old columns — restated here so this
    module stays free of the geopandas import chain.
    """
    def pick(keyword: str) -> str | None:
        for col in df.columns:
            low = str(col).lower()
            if keyword in low and not re.search(r"(?:code|old|id\b|_id)", low):
                return col
        return None
    return pick("latitude"), pick("longitude")


def clean_coordinate(col: pd.Series) -> pd.Series:
    """One coordinate column as numbers, with everything unusable as NaN.

    Goes through `astype(str).str.strip()` because production settlement lists
    arrive from Excel with coordinates as text, sometimes padded with spaces and
    sometimes empty — and an empty string is not NaN to `pd.to_numeric` until it
    is stripped first.
    """
    text = col.astype(str).str.strip()
    return pd.to_numeric(text.mask(text.str.casefold().isin(BLANKS)),
                         errors="coerce")


def usable_coordinates(df: pd.DataFrame) -> pd.Series:
    """Boolean mask, aligned to `df.index`: does this row have usable coords?"""
    lat, lon = find_coordinate_columns(df)
    if not lat or not lon:
        return pd.Series(False, index=df.index)
    la, lo = clean_coordinate(df[lat]), clean_coordinate(df[lon])
    return (la.notna() & lo.notna()
            & la.between(*LAT_RANGE) & lo.between(*LON_RANGE)
            & ~((la == 0) & (lo == 0)))
