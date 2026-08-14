"""SUPERSEDED — kept for reference only.

`team_time_range.py` is the maintained version of this analysis and is what
the pipeline calls. This script is the original standalone draft.

The hard-coded Windows path it used to carry has been replaced by command-line
arguments, so the file is portable and safe to commit:

    python "Team Time Spent Range Analysis.py" \
        --input  "output/settlement_analysis_day 8.csv" \
        --output "output/Team_TimeSpent_Range_Summary.xlsx"

Paths are resolved relative to wherever you run it from.
"""
import argparse
import os

import pandas as pd

print("===== TEAM TIME SPENT RANGE ANALYSIS =====")

# =====================================================
# FILE PATHS — supplied on the command line, not baked in
# =====================================================
_ap = argparse.ArgumentParser(description=__doc__)
_ap.add_argument("--input", default=os.environ.get("GTS_SETTLEMENT_ANALYSIS"),
                 help="settlement analysis CSV (or set GTS_SETTLEMENT_ANALYSIS)")
_ap.add_argument("--output", default="Team_TimeSpent_Range_Summary.xlsx",
                 help="where to write the summary workbook")
_args = _ap.parse_args()

if not _args.input:
    raise SystemExit(
        "No input given. Pass --input <settlement analysis CSV>, or set the "
        "GTS_SETTLEMENT_ANALYSIS environment variable.")

INPUT_FILE = _args.input
OUTPUT_FILE = _args.output

# =====================================================
# LOAD DATA
# =====================================================
df = pd.read_csv(
    INPUT_FILE,
    low_memory=False,
    encoding="latin1"
)

# =====================================================
# CLEAN COLUMNS
# =====================================================
df.columns = (
    df.columns
    .str.lower()
    .str.strip()
    .str.replace(" ", "_")
    .str.replace("-", "_")
)

# =====================================================
# CHECK REQUIRED COLUMNS
# =====================================================
required_cols = [
    "state_name",
    "lga_name",
    "ward_name",
    "team_code",
    "time_spent_range"
]

missing = [c for c in required_cols if c not in df.columns]

if missing:
    raise ValueError(
        f"Missing columns: {missing}"
    )

# =====================================================
# KEEP ONLY 12 STATES
# =====================================================
# auto-detect top 12 deployed states
top_states = (
    df["state_name"]
    .dropna()
    .astype(str)
    .str.strip()
    .str.upper()
    .value_counts()
    .head(12)
    .index
)

df["state_name"] = (
    df["state_name"]
    .astype(str)
    .str.strip()
    .str.upper()
)

df = df[
    df["state_name"].isin(top_states)
].copy()

print(f"States included: {len(top_states)}")

# =====================================================
# REMOVE DUPLICATE TEAM-LEVEL RECORDS
# (same team repeated in same ward/category)
# =====================================================
df_unique = df.drop_duplicates(
    subset=[
        "state_name",
        "lga_name",
        "ward_name",
        "team_code",
        "time_spent_range"
    ]
)

# =====================================================
# SUMMARY 1:
# UNIQUE TEAMS PER TIME RANGE
# =====================================================
team_summary = (
    df_unique.groupby(
        [
            "state_name",
            "lga_name",
            "ward_name",
            "time_spent_range"
        ]
    )["team_code"]
    .nunique()
    .reset_index(
        name="unique_teams"
    )
)

# =====================================================
# SUMMARY 2:
# TEAM LIST PER CATEGORY
# =====================================================
team_list = (
    df_unique.groupby(
        [
            "state_name",
            "lga_name",
            "ward_name",
            "time_spent_range"
        ]
    )["team_code"]
    .apply(
        lambda x: ", ".join(
            sorted(
                x.astype(str).unique()
            )[:100]
        )
    )
    .reset_index(
        name="team_codes"
    )
)

# merge
final = team_summary.merge(
    team_list,
    on=[
        "state_name",
        "lga_name",
        "ward_name",
        "time_spent_range"
    ],
    how="left"
)

# =====================================================
# STATE-LEVEL SUMMARY
# =====================================================
state_summary = (
    df_unique.groupby(
        [
            "state_name",
            "time_spent_range"
        ]
    )["team_code"]
    .nunique()
    .reset_index(
        name="unique_teams"
    )
)

# =====================================================
# LGA-LEVEL SUMMARY
# =====================================================
lga_summary = (
    df_unique.groupby(
        [
            "state_name",
            "lga_name",
            "time_spent_range"
        ]
    )["team_code"]
    .nunique()
    .reset_index(
        name="unique_teams"
    )
)

# =====================================================
# LESS THAN 12 MINS
# =====================================================
lt12 = df_unique[
    df_unique["time_spent_range"]
    .astype(str)
    .str.contains(
        "12",
        case=False,
        na=False
    )
]

lt12_summary = (
    lt12.groupby(
        [
            "state_name",
            "lga_name",
            "ward_name"
        ]
    )["team_code"]
    .nunique()
    .reset_index(
        name="teams_less_than_12mins"
    )
)

# =====================================================
# SAVE TO EXCEL
# =====================================================
with pd.ExcelWriter(
    OUTPUT_FILE,
    engine="openpyxl"
) as writer:

    final.to_excel(
        writer,
        sheet_name="Ward_Level_Summary",
        index=False
    )

    lga_summary.to_excel(
        writer,
        sheet_name="LGA_Level_Summary",
        index=False
    )

    state_summary.to_excel(
        writer,
        sheet_name="State_Level_Summary",
        index=False
    )

    lt12_summary.to_excel(
        writer,
        sheet_name="Less_Than_12mins",
        index=False
    )

print("\n✅ DONE")
print("Saved to:")
print(OUTPUT_FILE)