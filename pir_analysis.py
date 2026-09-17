"""Post-Implementation Report — the indicators behind every figure in the deck.

One module, one job: turn stage 2's settlement visitation CSV into the exact
numbers the "Post Implementation Report 2026" sample presents, so the charts,
the tables and the narration on a slide are all read off the SAME computation
and cannot disagree with one another.

Nothing here draws anything and nothing here touches a .pptx. `pir_charts.py`
renders these numbers, `stage7_pir_report.py` places them on slides.

The reconciliation rule
-----------------------
The sample's own arithmetic is the specification:

    planned settlements  =  Fully + Partially + Low + Very Low + No Coverage
    visited settlements  =  planned − No Coverage

i.e. on the deck a settlement is "visited" exactly when its coverage class is
something other than "No Coverage", and the donut's five slices add up to the
planned total. Stage 2 writes two things that can disagree about that:

    `Settlement Coverage`   the grid-cell coverage class
    `day_{N}_cumm`          "Visited"/"Not Visited" from evidence of tracks

They disagree for a settlement that has tracks but no polygon in the gridded
target-area layer — pings, therefore reached, but no measurable coverage, so it
classifies as "No Coverage". Left alone, the deck would say 492 visited in the
narration and 475 in the donut.

`reconciled_classes()` resolves it the same way `stage_charts
.cumulative_coverage_stats` already does for the daily deck: reached-but-not-
measurable is moved into **Low Coverage**, the honest bucket. Every PIR figure
is then derived from that one Series, so the identity above holds on every
slide, at state, LGA and ward level.

Daily vs cumulative
-------------------
The PIR is a whole-campaign report and every indicator here is CUMULATIVE —
Day 1 through the final day — taken from the cumulative columns (`Coverage`,
`Settlement Coverage`, `day_{N}_cumm`). The daily columns are deliberately not
read. Stage 7 states "Cumulative" in the chart titles for the same reason.
"""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stage2_analysis import find_col
from stage3_erm_workbook import norm_lga
from target_population import find_indicator_col, EXCLUDE_SEGMENTS

# Worst-to-best is how the legends read; best-to-worst is how the bars stack.
COVERAGE_ORDER = ["Fully Covered", "Partially Covered", "Low Coverage",
                  "Very Low Coverage", "No Coverage"]
NO_COVERAGE = "No Coverage"
REACHED_FALLBACK_CLASS = "Low Coverage"

# Potentially-missed CHILDREN, so the under-5 target is preferred over the
# settlement's whole population. `target_population.POPULATION_CANDIDATES` puts
# `set_population` first because that module reports population coverage; here
# the indicator is children, so the order is reversed.
#
# Matching goes through `find_indicator_col`, which normalizes the name — the
# production settlement lists carry "set target" with a SPACE, which an exact
# `set_target` comparison misses entirely.
TARGET_CHILDREN_CANDIDATES = (
    "set_target", "target_population", "under_5_population", "u5_population",
    "target", "set_population", "population",
)


# --------------------------------------------------------------- column finding
def lga_column(dip: pd.DataFrame) -> str:
    col = find_col(dip, "lga")
    if col is None:
        raise KeyError("no LGA column in the settlement visitation file")
    return col


def ward_column(dip: pd.DataFrame) -> str | None:
    return find_col(dip, "ward")


def settlement_column(dip: pd.DataFrame) -> str | None:
    return find_col(dip, "settlement")


def team_column(dip: pd.DataFrame) -> str | None:
    return next((c for c in dip.columns
                 if "team" in c.lower() and "code" in c.lower()), None)


def target_children_column(dip: pd.DataFrame) -> str | None:
    return find_indicator_col(dip, TARGET_CHILDREN_CANDIDATES, EXCLUDE_SEGMENTS)


def geo_mask(dip: pd.DataFrame) -> pd.Series:
    """Settlements carrying usable geo-coordinates.

    (0, 0) is treated as missing: it is the null island the GTS export uses for
    a device that never got a fix, not a settlement in the Gulf of Guinea.
    """
    lat, lon = find_col(dip, "latitude"), find_col(dip, "longitude")
    if not lat or not lon:
        return pd.Series(False, index=dip.index)
    la = pd.to_numeric(dip[lat], errors="coerce")
    lo = pd.to_numeric(dip[lon], errors="coerce")
    return la.notna() & lo.notna() & ~((la == 0) & (lo == 0))


# ------------------------------------------------------------- the core series
def planned_mask(dip: pd.DataFrame, cum_col: str) -> pd.Series:
    """The settlements the campaign PLANNED — the denominator everywhere.

    Stage 2 writes the cumulative status column for every planned settlement,
    so a non-null value is the planning flag. A settlement list that predates
    that column falls back to every row, which is what the sample means by
    "total settlements with and without geo-coordinates planned".
    """
    if cum_col in dip.columns:
        mask = dip[cum_col].notna()
        if bool(mask.any()):
            return mask
    return pd.Series(True, index=dip.index)


def reconciled_classes(dip: pd.DataFrame, cum_col: str,
                       class_col: str = "Settlement Coverage") -> pd.Series:
    """Coverage class per settlement, reconciled against evidence of tracks.

    See the module docstring. Returns a Series aligned to `dip.index` whose
    values are always one of COVERAGE_ORDER, so `!= "No Coverage"` is THE
    definition of visited for the whole report.
    """
    if class_col not in dip.columns:
        # No grid-cell classification at all (a settlement list run without the
        # gridded target areas). Fall back to the binary status so the deck
        # still builds: reached settlements read as Low Coverage, the same
        # bucket reached-but-unmeasured lands in above.
        visited = _visited_flag(dip, cum_col)
        return pd.Series([REACHED_FALLBACK_CLASS if v else NO_COVERAGE
                          for v in visited], index=dip.index)

    classes = dip[class_col].astype(str).str.strip()
    classes = classes.where(classes.isin(COVERAGE_ORDER), NO_COVERAGE)
    visited = _visited_flag(dip, cum_col)
    # reached, but the grid could not measure it -> Low Coverage
    classes = classes.mask(visited & classes.eq(NO_COVERAGE),
                           REACHED_FALLBACK_CLASS)
    return classes


def _visited_flag(dip: pd.DataFrame, cum_col: str) -> pd.Series:
    if cum_col in dip.columns:
        return dip[cum_col].astype(str).str.strip().str.casefold().eq("visited")
    if "Track Evidence" in dip.columns:
        return dip["Track Evidence"].astype(str).str.strip().str.casefold().eq("yes")
    return pd.Series(False, index=dip.index)


def _counts(classes: pd.Series) -> pd.Series:
    return (classes.value_counts().reindex(COVERAGE_ORDER).fillna(0).astype(int))


def prepare(dip: pd.DataFrame, cum_col: str) -> pd.DataFrame:
    """The planned settlements with the report's derived columns attached.

    Every function below takes this frame, so the reconciliation and the LGA
    name normalization happen exactly once per run.
    """
    out = dip[planned_mask(dip, cum_col)].copy()
    out["_class"] = reconciled_classes(out, cum_col)
    out["_visited"] = out["_class"].ne(NO_COVERAGE)
    out["_lga"] = norm_lga(out[lga_column(out)])
    ward = ward_column(out)
    out["_ward"] = (out[ward].astype(str).str.strip().replace({"": "Unknown",
                                                               "nan": "Unknown"})
                    if ward else "Unknown")
    out["_has_geo"] = geo_mask(out)
    return out


# --------------------------------------------------------------- state summary
def state_summary(prepared: pd.DataFrame) -> dict:
    """Every whole-state figure the deck quotes, from one pass."""
    counts = _counts(prepared["_class"])
    planned = int(len(prepared))
    visited = int(prepared["_visited"].sum())
    team_col = team_column(prepared)
    return {
        "planned": planned,
        "visited": visited,
        "not_visited": planned - visited,
        "pct": visited / planned * 100 if planned else 0.0,
        "classes": counts.to_dict(),
        "n_lga": int(prepared["_lga"].nunique()),
        "n_ward": int(prepared.groupby("_lga")["_ward"].nunique().sum()),
        "n_team": int(prepared[team_col].dropna().nunique()) if team_col else 0,
        "n_geo": int(prepared["_has_geo"].sum()),
        "n_no_geo": int((~prepared["_has_geo"]).sum()),
        "lgas": sorted(prepared["_lga"].unique().tolist()),
    }


def cumulative_bar_counts(summary: dict) -> dict:
    """The five bars on the Cumulative Settlements Coverage slide.

    "Low Coverage" here is the sample's collapsed bucket — Low **and** Very Low
    together — because that chart shows four outcomes against the plan, not the
    donut's five classes. Collapsing rather than dropping is what keeps the four
    outcome bars summing to Planned.
    """
    c = summary["classes"]
    low = int(c.get("Low Coverage", 0)) + int(c.get("Very Low Coverage", 0))
    return {
        "Planned Settlements": summary["planned"],
        "Fully Covered": int(c.get("Fully Covered", 0)),
        "Partially Covered": int(c.get("Partially Covered", 0)),
        "Low Coverage": low,
        "No Coverage": int(c.get(NO_COVERAGE, 0)),
    }


# ------------------------------------------------------------------ breakdowns
def lga_class_table(prepared: pd.DataFrame) -> pd.DataFrame:
    """Settlements per LGA per coverage class — index LGA, columns COVERAGE_ORDER."""
    tab = (prepared.groupby(["_lga", "_class"]).size().unstack(fill_value=0)
           .reindex(columns=COVERAGE_ORDER, fill_value=0))
    return tab.sort_index()


def ward_class_table(prepared: pd.DataFrame, lga: str) -> pd.DataFrame:
    """Settlements per ward per coverage class, for one LGA."""
    sub = prepared[prepared["_lga"] == lga]
    tab = (sub.groupby(["_ward", "_class"]).size().unstack(fill_value=0)
           .reindex(columns=COVERAGE_ORDER, fill_value=0))
    return tab.sort_index()


def lga_summary(prepared: pd.DataFrame, lga: str) -> dict:
    """The per-LGA slide's own figures — same identity as the state slide."""
    sub = prepared[prepared["_lga"] == lga]
    counts = _counts(sub["_class"])
    planned = int(len(sub))
    visited = int(sub["_visited"].sum())
    return {
        "lga": lga, "planned": planned, "visited": visited,
        "not_visited": planned - visited,
        "pct": visited / planned * 100 if planned else 0.0,
        "classes": counts.to_dict(),
        "n_ward": int(sub["_ward"].nunique()),
    }


def not_visited_by_lga(prepared: pd.DataFrame) -> pd.Series:
    """Not-visited settlements per LGA, LGAs with none dropped, ranked by name.

    The sample charts only the LGAs that actually have a gap (4 of 12), which is
    the point of the slide — a bar of zeros for every other LGA buries it.
    """
    s = (prepared[~prepared["_visited"]].groupby("_lga").size()
         .astype(int).sort_index())
    return s[s > 0]


def missed_children_by_lga(prepared: pd.DataFrame) -> pd.Series:
    """Target children in not-visited settlements, per LGA.

    Empty Series when the settlement list carries no target/population field —
    stage 7 then drops the slide rather than charting zeros, because a zero here
    means "not measured", not "no children missed".
    """
    col = target_children_column(prepared)
    if not col:
        return pd.Series(dtype=int)
    nv = prepared[~prepared["_visited"]].copy()
    if nv.empty:
        return pd.Series(dtype=int)
    nv[col] = pd.to_numeric(nv[col], errors="coerce").fillna(0)
    s = nv.groupby("_lga")[col].sum().round().astype(int).sort_index()
    return s[s > 0]


def coordinates_split(prepared: pd.DataFrame) -> pd.DataFrame:
    """Settlements with vs without geo-coordinates per LGA, plus a Grand Total.

    The GTS can only track a settlement it has a coordinate for; the ones
    without are visited by the same teams but are invisible to the tracker,
    which is exactly what the sample's "No Geocoordinate Settlements" slide is
    about.
    """
    grp = prepared.groupby("_lga")["_has_geo"]
    out = pd.DataFrame({
        "LGA": grp.sum().index,
        "With Coordinates": grp.sum().astype(int).values,
        "Total Planned": grp.size().astype(int).values,
    })
    out["Without Coordinates"] = out["Total Planned"] - out["With Coordinates"]
    out = out[["LGA", "With Coordinates", "Without Coordinates", "Total Planned"]]
    out.loc[len(out)] = ["Grand Total", int(out["With Coordinates"].sum()),
                         int(out["Without Coordinates"].sum()),
                         int(out["Total Planned"].sum())]
    return out.reset_index(drop=True)



# ---------------------------------------------- LGA splits for stacked charts
# Every LGA-level chart in the report is a stacked bar showing a whole split
# into parts, so these return the WHOLE as well as the part being reported. The
# headline figures — settlements not visited, children potentially missed — are
# unchanged; they are simply shown against the total they came out of, which is
# what makes one LGA's gap comparable to another's.
VISITED_LABEL = "Visited"
NOT_VISITED_LABEL = "Not Visited"
REACHED_CHILDREN_LABEL = "In Visited Settlements"
MISSED_CHILDREN_LABEL = "Potentially Missed"


def visitation_split_by_lga(prepared: pd.DataFrame,
                            only_with_gaps: bool = True) -> pd.DataFrame:
    """Planned settlements per LGA, split Visited / Not Visited.

    `only_with_gaps` keeps the slide's focus: an LGA that reached everything
    adds a full-height bar of one colour and pushes the LGAs with a gap into a
    narrower column each. The Grand Total is on the slide either way.
    """
    tab = (prepared.groupby("_lga")["_visited"]
           .agg(**{VISITED_LABEL: "sum", "_planned": "size"}))
    tab[NOT_VISITED_LABEL] = tab["_planned"] - tab[VISITED_LABEL]
    tab = tab[[VISITED_LABEL, NOT_VISITED_LABEL]].astype(int).sort_index()
    if only_with_gaps:
        tab = tab[tab[NOT_VISITED_LABEL] > 0]
    return tab


def children_split_by_lga(prepared: pd.DataFrame,
                          only_with_gaps: bool = True) -> pd.DataFrame:
    """Target children per LGA, split by whether their settlement was reached.

    Empty when the settlement list carries no target/population field — a zero
    there would mean "not measured", not "no children".
    """
    col = target_children_column(prepared)
    if not col:
        return pd.DataFrame(columns=[REACHED_CHILDREN_LABEL, MISSED_CHILDREN_LABEL])
    df = prepared.copy()
    df["_target"] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    grouped = df.groupby("_lga")
    tab = pd.DataFrame({
        REACHED_CHILDREN_LABEL: grouped.apply(
            lambda g: g.loc[g["_visited"], "_target"].sum(), include_groups=False),
        MISSED_CHILDREN_LABEL: grouped.apply(
            lambda g: g.loc[~g["_visited"], "_target"].sum(), include_groups=False),
    }).round().astype(int).sort_index()
    if only_with_gaps:
        tab = tab[tab[MISSED_CHILDREN_LABEL] > 0]
    return tab


# ------------------------------------------------------------ field-time
def time_spent_summary(time_csv: str | None) -> dict:
    """Whole-campaign field-time compliance, for the Challenges narrative.

    "Compliant" is the pipeline's existing definition — a team whose tracks put
    it in the field for an hour or more (`stage3_erm_workbook.time_spent_analysis`
    bands "1 - 2 hrs" and ">2 hrs").
    """
    out = {"total": 0, "compliant": 0, "compliant_pct": 0.0, "no_tracks": 0}
    if not (time_csv and os.path.exists(time_csv)):
        return out
    t = pd.read_csv(time_csv)
    gt = t[t["Time Spent"] == "Grand Total"]
    out["total"] = int(gt.iloc[0]["Number of Teams"]) if len(gt) else 0
    out["compliant"] = int(t.loc[t["Time Spent"].isin(["1 - 2 hrs", ">2 hrs"]),
                                 "Number of Teams"].sum())
    nt = t[t["Time Spent"] == "0 (No evidence of tracks)"]
    out["no_tracks"] = int(nt.iloc[0]["Number of Teams"]) if len(nt) else 0
    out["compliant_pct"] = out["compliant"] / out["total"] if out["total"] else 0.0
    return out


def wards_without_tracks(prepared: pd.DataFrame) -> pd.DataFrame:
    """Wards where NOT ONE planned settlement was reached, with their LGA.

    The strongest signal in the data for the sample's "trackers not deployed in
    these wards" challenge, and the only one of its challenges the tracking data
    can actually observe.
    """
    g = prepared.groupby(["_lga", "_ward"])["_visited"].agg(["sum", "size"])
    dark = g[g["sum"] == 0].reset_index()
    dark.columns = ["LGA", "Ward", "Visited", "Planned Settlements"]
    return (dark[["LGA", "Ward", "Planned Settlements"]]
            .sort_values(["LGA", "Ward"]).reset_index(drop=True))
