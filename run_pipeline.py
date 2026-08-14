"""GTS Vaccination Tracking — one-command daily pipeline.

Runs:  merge tracks -> visitation analysis -> ERM workbook -> maps -> report draft

Example:
    python run_pipeline.py \
        --tracks-folder "merging tracks" \
        --settlements Analysis/settlement.csv \
        --gridded-ta Analysis/gridded_ta.sqlite \
        --voronoi Analysis/voronoi_ta.sqlite \
        --lga-boundaries LGA.sqlite \
        --state Zamfara --day 1 \
        --output "Day1_outputs"

For day 2+, add:  --prev-dip Day1_outputs/Zamfara_day_1_settlement_visitation.csv
                  --prev-ta  Day1_outputs/gridded_ta_day_1.parquet
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stage1_merge import merge_tracks
from stage2_analysis import run_analysis
from stage3_erm_workbook import build_workbook_from_agg, load_tracks_teams
from stage4_maps import generate_maps
from stage5_report import build_report

import pandas as pd


def main():
    ap = argparse.ArgumentParser(description="GTS daily tracking pipeline")
    ap.add_argument("--tracks-folder", required=True, help="Folder with the day's Tracks_*.csv exports")
    ap.add_argument("--settlements", required=True, help="Settlement/DIP CSV")
    ap.add_argument("--gridded-ta", required=True)
    ap.add_argument("--voronoi", required=True)
    ap.add_argument("--lga-boundaries", required=True)
    ap.add_argument("--wards", default=None, help="Ward boundary file for LGA maps")
    ap.add_argument("--states", default=None, help="State boundary file for the implementation map")
    ap.add_argument("--state", required=True)
    ap.add_argument("--day", type=int, required=True)
    ap.add_argument("--mopup", action="store_true")
    ap.add_argument("--output", required=True, help="Output folder for the day")
    ap.add_argument("--prev-dip", default=None, help="Previous day's visitation CSV")
    ap.add_argument("--prev-ta", default=None, help="Previous day's gridded TA parquet")
    ap.add_argument("--logo", default=None)
    ap.add_argument("--map-template-dir", default=None,
                    help="Directory holding the QGIS .qpt map layout templates that drive "
                    "stage 4 (page size, legend, scale bar, north arrow, logo placement). "
                    "Defaults to $GTS_MAP_TEMPLATE_DIR, then the folder holding these "
                    "scripts. Roles are matched by filename: 'lga'+'coverage'/'visitation', "
                    "'state'+'cumulative', and 'implementation'.")
    ap.add_argument("--map-dpi", type=int, default=None,
                    help="Override the map templates' print resolution (300 dpi). A3 at "
                    "300 dpi is ~3500x5000 px per map; lower this to shrink the maps "
                    "folder and the reports that embed them.")
    ap.add_argument("--geojson", action="store_true", help="Also export merged tracks as GeoJSON")
    ap.add_argument("--skip-merge", action="store_true", help="Reuse existing tracks.parquet in output")
    ap.add_argument("--pptx-template", default=None,
                    help="Organization's .pptx report template — if given, also builds the "
                    "report in that template alongside the .docx")
    ap.add_argument("--campaign-name", default="Vaccination Tracking Report",
                    help="Used on the report title page/slide, e.g. 'Polio SIA April 2026'")
    ap.add_argument("--state-logo", default=None,
                    help="State PHC logo (title page/slide) — separate from --logo, "
                    "since the campaign may run in a different state each time")
    ap.add_argument("--analysis-type", choices=["daily", "post_campaign"], default="daily",
                    help="'daily' (default) builds the Daily ERM Analysis workbook tab and the "
                    "daily .docx/.pptx report. 'post_campaign' builds a Post-Campaign Analysis "
                    "workbook tab instead and a Post-Implementation Report (PIR) .docx — pass the "
                    "FINAL day's --day/--prev-dip chain and, ideally, all campaign days' tracks "
                    "in --tracks-folder so team deployment and time-spent reflect the whole campaign.")
    ap.add_argument("--teams-deployed", type=int, default=None,
                    help="Manually-entered total teams deployed for the campaign/reporting day — "
                    "overrides the settlement-list-derived count and drives Teams "
                    "Reported/Pending/Reporting %% throughout the workbook and report.")
    ap.add_argument("--pir-template", default=None,
                    help="Post Implementation Report sample/template — auto-detected next to "
                    "this script if not given; used as the reporting template when "
                    "--analysis-type post_campaign builds the PIR .docx.")
    args = ap.parse_args()

    os.makedirs(args.output, exist_ok=True)

    # Stage 1 — merge
    print("\n========== STAGE 1: MERGE TRACKS ==========")
    tracks_path = os.path.join(args.output, "merged_tracks.csv")
    if args.skip_merge and os.path.exists(tracks_path):
        print(f"Reusing {tracks_path}")
    else:
        tracks_path = merge_tracks(args.tracks_folder, args.output, write_geojson=args.geojson)

    # Stage 2 — analysis
    print("\n========== STAGE 2: VISITATION ANALYSIS ==========")
    # Daily runs count only the latest transmission date, so a merged export
    # spanning several days cannot report the campaign to date as one day's
    # work. Post-campaign deliberately spans the whole period.
    result = run_analysis(
        args.settlements, tracks_path, args.gridded_ta, args.voronoi,
        args.state, args.day, args.mopup, args.output,
        prev_ta_file=args.prev_ta, prev_dip_file=args.prev_dip,
        track_date=None if args.analysis_type == "post_campaign" else "latest")
    cum_col = result["cum_col"]
    daily_col = result.get("daily_col")
    visitation_csv = result["visitation_csv"]
    track_date = result.get("track_date")

    # Stage 3 — ERM workbook
    print("\n========== STAGE 3: ERM WORKBOOK ==========")
    is_pc = args.analysis_type == "post_campaign"
    if is_pc:
        # whole-campaign artifact — no single "Day_N" in the filename
        workbook_path = os.path.join(
            args.output, f"{args.state}_PostCampaign_ERM_Analysis.xlsx")
    else:
        workbook_path = os.path.join(
            args.output, f"{args.state}_Day_{args.day}_Vaccination_Tracking_ERM_Analysis.xlsx")

    # single pass over merged_tracks.csv, shared by the workbook and the
    # deploy/time-spent CSVs used by the report stages (avoids scanning a
    # multi-million-row file twice)
    dip = pd.read_csv(visitation_csv, low_memory=False)
    teams = load_tracks_teams(tracks_path)
    deploy_csv = os.path.join(args.output, f"team_deploy_day_{args.day}.csv")
    time_csv = os.path.join(args.output, f"time_spent_day_{args.day}.csv")
    flagged_csv = os.path.join(args.output, f"flagged_teams_day_{args.day}.csv")
    build_workbook_from_agg(visitation_csv, teams, cum_col, args.day, args.state, workbook_path,
                            deploy_csv=deploy_csv, time_csv=time_csv,
                            flagged_csv=flagged_csv,
                            analysis_type=args.analysis_type, teams_deployed_total=args.teams_deployed)

    # Stage 4 — charts + maps
    print("\n========== STAGE 4: CHARTS & COVERAGE MAPS ==========")
    from stage_charts import generate_charts
    charts_folder = os.path.join(args.output, "charts")
    generate_charts(visitation_csv, cum_col, deploy_csv, time_csv, args.day, charts_folder,
                    analysis_type=args.analysis_type, daily_col=daily_col,
                    track_date=track_date, tracks_file=tracks_path)
    maps_folder = os.path.join(args.output, "maps")
    if args.map_template_dir or args.map_dpi:
        from stage4_maps import set_dpi_override, set_template_dirs
        if args.map_template_dir:
            set_template_dirs(args.map_template_dir)
        set_dpi_override(args.map_dpi)
    if args.states:
        from stage4_maps import implementation_map
        lga_col = next(c for c in dip.columns if "lga" in c.lower() and "code" not in c.lower())
        # normalize underscore/space variants (e.g. "Talata_Mafara" vs "Talata Mafara")
        # so the LGA count baked into the map title matches the actual map slides
        _norm = (dip[lga_col].dropna().astype(str).str.replace("_", " ", regex=False)
                .str.replace(r"\s+", " ", regex=True).str.strip().str.title())
        impl_lgas = sorted(_norm.unique())
        os.makedirs(maps_folder, exist_ok=True)
        implementation_map(args.states, args.lga_boundaries, args.state, impl_lgas,
                           os.path.join(maps_folder, f"{args.state}_implementation_map.png"),
                           logo=args.logo,
                           title=f"{args.state} State GTS Implementation Map — {len(impl_lgas)} LGAs")
    # The settlement extents and stage 2's per-cell gridded TA are what the
    # side-by-side LGA maps are built from; without them stage 4 falls back to
    # the single-panel settlement-point map on its own.
    generate_maps(visitation_csv, cum_col, args.lga_boundaries, args.state,
                  args.day, maps_folder, args.logo, args.wards,
                  voronoi_file=args.voronoi,
                  gridded_ta_file=result.get("ta_parquet"),
                  state_logo=args.state_logo)

    if args.analysis_type == "post_campaign":
        # Stage 7 — Post-Implementation Report (PIR), whole-campaign view
        print("\n========== STAGE 7: POST-IMPLEMENTATION REPORT (PIR) ==========")
        from stage7_pir_report import build_pir_report
        report_path = os.path.join(args.output, f"{args.state}_Post_Implementation_Report.docx")
        build_pir_report(args.state, visitation_csv, cum_col, deploy_csv, time_csv,
                         maps_folder, report_path, args.logo,
                         campaign_name=args.campaign_name, charts_folder=charts_folder,
                         state_logo=args.state_logo, template_path=args.pir_template)
    else:
        # Stage 5 — report draft
        print("\n========== STAGE 5: REPORT DRAFT ==========")
        report_path = os.path.join(
            args.output, f"{args.state}_Day_{args.day}_Daily_Tracking_Report_DRAFT.docx")
        build_report(args.state, args.day, visitation_csv, cum_col, deploy_csv,
                     time_csv, maps_folder, report_path, args.logo,
                     campaign_name=args.campaign_name, charts_folder=charts_folder,
                     state_logo=args.state_logo)

        # Stage 6 — report draft in the organization's PPTX template (optional, daily only)
        if args.pptx_template:
            print("\n========== STAGE 6: PPTX REPORT (ORG TEMPLATE) ==========")
            from stage6_pptx_report import build_pptx_report
            pptx_path = os.path.join(
                args.output, f"{args.state}_Day_{args.day}_Report.pptx")
            build_pptx_report(args.pptx_template, args.state, args.day, visitation_csv,
                              cum_col, deploy_csv, time_csv, maps_folder, charts_folder,
                              pptx_path, campaign_name=args.campaign_name,
                              state_logo_path=args.state_logo,
                              tracks_file=tracks_path)

    print("\n========== PIPELINE COMPLETE ==========")
    print(f"All outputs in: {args.output}")


if __name__ == "__main__":
    main()
