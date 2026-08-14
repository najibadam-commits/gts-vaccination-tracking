"""GTS Vaccination Tracking — web app.

Run with:  streamlit run app.py
The analyst uploads the planned settlements first; the app detects the
state, LGAs and wards from it, then runs the full daily pipeline.
"""
import os
import sys
import io
import base64
import zipfile
import tempfile
import shutil

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(APP_DIR)

# ------------------------------------------------ pipeline modules
# Imported at module scope, NOT lazily inside the run handler.
#
# Streamlit's file watcher only tracks modules that are imported when the
# script is first executed. A stage imported lazily inside `if run:` is never
# registered, so it stays cached in sys.modules for the life of the server:
# edit a pipeline stage while the app is running and the app happily keeps
# calling the old code. That surfaces as a signature mismatch — the reloaded
# app.py passing a new argument to a stale function — rather than as an
# obvious "restart me", which is a genuinely confusing way to lose an hour.
#
# The cost is a slower first page load, since geopandas/matplotlib now import
# at startup instead of on the first run. That is the right trade for a tool
# whose analysis modules change regularly.
from cloud_limits import banner as cloud_banner, check_inputs, map_dpi_override
from qpt_layout import discover_templates
from stage1_merge import merge_tracks
from stage2_analysis import run_analysis
from stage3_erm_workbook import build_workbook_from_agg, load_tracks_teams
from stage4_maps import (generate_maps, implementation_map, set_dpi_override,
                         set_template_dirs)
from stage_charts import generate_charts
from stage5_report import build_report
from stage6_pptx_report import build_pptx_report
from stage7_pir_report import build_pir_report


def _find_default(filename):
    """Looks for a boundary/logo file next to the app, one level up, or in cwd."""
    for folder in (APP_DIR, BASE_DIR, os.getcwd()):
        candidate = os.path.join(folder, filename)
        if os.path.exists(candidate):
            return candidate
    return None


DEFAULT_LGA = _find_default("LGA.sqlite")
DEFAULT_WARD = _find_default("Ward.sqlite")
DEFAULT_STATE = _find_default("state.sqlite")
DEFAULT_LOGO = _find_default("eha_logo.png")


def _find_default_pptx():
    """Looks for any .pptx template file next to the app or one level up."""
    for folder in (APP_DIR, BASE_DIR, os.getcwd()):
        if not os.path.isdir(folder):
            continue
        for fn in os.listdir(folder):
            if fn.lower().endswith(".pptx") and "template" in fn.lower():
                return os.path.join(folder, fn)
    return None


DEFAULT_PPTX_TEMPLATE = _find_default_pptx()


def _find_default_pir_template():
    """Looks for the 'Post Implementation Report sample' file next to the app
    or one level up, so it's applied automatically for Post-Campaign Analysis."""
    for folder in (APP_DIR, BASE_DIR, os.getcwd()):
        if not os.path.isdir(folder):
            continue
        for fn in sorted(os.listdir(folder)):
            low = fn.lower()
            if "post implementation report" in low and "sample" in low:
                return os.path.join(folder, fn)
    return None


DEFAULT_PIR_TEMPLATE = _find_default_pir_template()


def _find_map_templates():
    """Locate the QGIS map layout templates that drive stage 4.

    Returns {role: path} for the roles found ('lga', 'statewide',
    'implementation'). Discovery is shared with the CLI runners so both agree on
    which template file plays which role, and accepts .qpt exports as well as
    whole .qgs/.qgz projects.
    """
    return discover_templates([APP_DIR, BASE_DIR, os.getcwd()])


DEFAULT_MAP_TEMPLATES = _find_map_templates()

MAP_TEMPLATE_ROLE_LABELS = {
    "lga": "LGA visitation status & coverage (side-by-side) map",
    "statewide": "Statewide cumulative visitation map",
    "implementation": "State implementation map",
}

# Backend setting, deliberately not exposed in the interface. None keeps each
# template's own printResolution (300 dpi); set an integer to render smaller.
MAP_RESOLUTION_DPI = None


def _img_b64(path):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode()
    except Exception:
        return None


LOGO_B64 = _img_b64(DEFAULT_LOGO)

st.set_page_config(page_title="GTS Vaccination Tracking", page_icon="🛰️", layout="wide")

# On a hosted runner, say so and say what is capped — see cloud_limits.
_cloud_note = cloud_banner()
if _cloud_note:
    st.info(_cloud_note, icon="☁️")

# ------------------------------------------------ theme / styling
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}
footer {visibility: hidden;}

/* page background */
[data-testid="stAppViewContainer"] > .main {
    background-color: #F4F7FB;
}

/* title banner */
.gts-banner {
    display: flex;
    align-items: center;
    gap: 18px;
    background: linear-gradient(135deg, #1F4E79 0%, #2E75B6 100%);
    padding: 22px 28px;
    border-radius: 14px;
    margin-bottom: 24px;
    box-shadow: 0 4px 14px rgba(31, 78, 121, 0.25);
}
.gts-banner img { height: 44px; }
.gts-banner-title { color: #fff; font-size: 25px; font-weight: 700; letter-spacing: -0.3px; margin: 0; }
.gts-banner-subtitle { color: #CFE3F5; font-size: 13.5px; margin: 3px 0 0; }

/* step headers inside cards */
.gts-step-header { display: flex; align-items: center; gap: 12px; margin-bottom: 4px; }
.gts-step-badge {
    display: flex; align-items: center; justify-content: center;
    width: 34px; height: 34px; min-width: 34px;
    border-radius: 50%;
    background: #1F4E79; color: #fff; font-size: 16px; line-height: 1;
}
.gts-step-title { font-size: 16.5px; font-weight: 600; color: #1A2433; margin: 0; }
.gts-step-subtitle { font-size: 13px; color: #64748B; margin-top: 1px; }

/* result banner */
.gts-result-banner {
    display: flex; align-items: center; gap: 12px;
    background: linear-gradient(135deg, #1E7A46 0%, #26A65B 100%);
    padding: 16px 22px; border-radius: 12px; margin-bottom: 16px;
}
.gts-result-title { color: #fff; font-size: 19px; font-weight: 700; margin: 0; }
.gts-result-subtitle { color: #D6F2E2; font-size: 13px; margin: 2px 0 0; }

/* card containers */
div[data-testid="stVerticalBlockBorderWrapper"] {
    border-radius: 12px !important;
    border: 1px solid #E3E8EF !important;
    background: #FFFFFF;
    box-shadow: 0 1px 3px rgba(16, 24, 40, 0.05);
    margin-bottom: 18px;
    padding: 6px 4px;
}

/* buttons */
.stButton > button, .stDownloadButton > button {
    border-radius: 8px;
    font-weight: 600;
}

/* file uploader */
[data-testid="stFileUploaderDropzone"] {
    border-radius: 10px;
    border: 1.5px dashed #B9C6D6;
    background: #FAFCFE;
}

/* metrics */
div[data-testid="stMetric"] {
    background: #FFFFFF;
    border: 1px solid #E3E8EF;
    border-radius: 10px;
    padding: 10px 14px;
}

/* sidebar */
section[data-testid="stSidebar"] {
    background: #10233B;
}
section[data-testid="stSidebar"] * { color: #E7EEF6 !important; }
section[data-testid="stSidebar"] .stButton > button {
    width: 100%;
    background: #1F4E79;
    border: 1px solid #2E75B6;
}
section[data-testid="stSidebar"] hr { border-color: rgba(255,255,255,0.12); }
</style>
""", unsafe_allow_html=True)


def step_card(icon, title, subtitle=None):
    """Renders a styled step header with an icon that represents the stage's
    function. Call inside `with st.container(border=True):`.

    Built as a single unindented line (no embedded newlines/leading spaces) —
    Streamlit's markdown renderer treats 4+ space indented lines as a code
    block, which was previously causing raw tags like `</div>` to show up
    as literal text instead of being rendered as HTML."""
    sub_html = f'<div class="gts-step-subtitle">{subtitle}</div>' if subtitle else ""
    st.markdown(
        f'<div class="gts-step-header"><span class="gts-step-badge">{icon}</span>'
        f'<div><p class="gts-step-title">{title}</p>{sub_html}</div></div>',
        unsafe_allow_html=True,
    )


banner_logo = f'<img src="data:image/png;base64,{LOGO_B64}" />' if LOGO_B64 else ""
st.markdown(
    f'<div class="gts-banner">{banner_logo}'
    f'<div><p class="gts-banner-title">GTS Vaccination Tracking</p>'
    f'<p class="gts-banner-subtitle">Merge tracks → visitation analysis → ERM workbook → maps → report draft</p>'
    f'</div></div>',
    unsafe_allow_html=True,
)


def save_upload(uploaded, folder, name=None):
    path = os.path.join(folder, name or uploaded.name)
    with open(path, "wb") as f:
        f.write(uploaded.getbuffer())
    return path


def find_col(df, keyword):
    import re
    for col in df.columns:
        low = col.lower()
        if keyword in low and not re.search(r"(?:code|old|id\b|_id)", low):
            return col
    return None


if "workdir" not in st.session_state:
    st.session_state.workdir = tempfile.mkdtemp(prefix="gts_")
WORK = st.session_state.workdir

# ------------------------------------------------ 1. analysis type
with st.container(border=True):
    step_card("🧭", "Analysis Type")
    analysis_choice = st.radio(
        "What do you want to run?",
        ["Daily ERM Analysis", "Post-Campaign Analysis"],
        horizontal=True, label_visibility="collapsed",
        help="Daily ERM Analysis: today's numbers for the evening review meeting. "
        "Post-Campaign Analysis: whole-campaign numbers for the Post-Implementation Report (PIR) — "
        "same inputs, just point Campaign Day at the final day (with all prior days chained via "
        "the previous-day visitation CSV) and, ideally, upload all campaign days' tracks in step 3.")
    analysis_type = "post_campaign" if analysis_choice == "Post-Campaign Analysis" else "daily"

# ------------------------------------------------ 2. planned settlements
with st.container(border=True):
    step_card("📥", "Planned Settlements (DIP)")
    sett_file = st.file_uploader("Upload the planned settlements CSV", type=["csv"],
                                 help="The app detects the state, LGAs and wards from this file")

    state_name, dip = None, None
    if sett_file:
        dip = pd.read_csv(sett_file, low_memory=False)
        state_col = find_col(dip, "state")
        lga_col = find_col(dip, "lga")
        ward_col = find_col(dip, "ward")
        if not state_col:
            st.error("Could not find a state column in this file.")
        else:
            states = sorted(dip[state_col].dropna().astype(str).str.strip().str.title().unique())
            state_name = states[0] if len(states) == 1 else st.selectbox(
                "Multiple states found — select the state to analyse", states)
            sub = dip[dip[state_col].astype(str).str.strip().str.title() == state_name]
            n_lga = sub[lga_col].astype(str).str.strip().str.title().nunique() if lga_col else 0
            n_ward = (sub.groupby(sub[lga_col].astype(str).str.strip().str.title())[ward_col]
                      .nunique().sum() if ward_col and lga_col else 0)
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("State", state_name)
            c2.metric("LGAs", n_lga)
            c3.metric("Wards", int(n_ward))
            c4.metric("Planned settlements", f"{len(sub):,}")
            with st.expander("LGAs detected"):
                st.write(", ".join(sorted(sub[lga_col].dropna().astype(str).str.strip()
                                          .str.title().unique())) if lga_col else "—")
            sett_path = save_upload(sett_file, WORK, "settlements.csv")

# ------------------------------------------------ 3. tracks
with st.container(border=True):
    step_card("🛰️", "GTS Tracks")
    tracks_files = st.file_uploader("Upload the day's track exports (Tracks_0.csv, Tracks_1.csv, …)",
                                    type=["csv"], accept_multiple_files=True)
    if tracks_files:
        st.success(f"{len(tracks_files)} track file(s) uploaded")

# ------------------------------------------------ 4. target areas
with st.container(border=True):
    step_card("🎯", "Target Areas")
    c1, c2 = st.columns(2)
    with c1:
        ta_file = st.file_uploader("Gridded target area", type=["sqlite", "gpkg", "geojson"])
    with c2:
        vor_file = st.file_uploader("Voronoi target area (settlement extents)",
                                    type=["sqlite", "gpkg", "geojson"])

# ------------------------------------------------ 5. campaign day
with st.container(border=True):
    step_card("📅", "Campaign Day")
    c1, c2, c3 = st.columns(3)
    day = c1.number_input("Day of campaign", min_value=1, max_value=14, value=1)
    mopup = c2.checkbox("Mop-up day")
    prev_file = c3.file_uploader("Previous day's visitation CSV (for day 2+)", type=["csv"])

    teams_deployed_in = st.number_input(
        "Total teams deployed (optional — overrides the count derived from the planned-"
        "settlement list's team codes)",
        min_value=0, value=0, step=1,
        help="Leave at 0 to use the settlement list's own team-code count. When set, this drives "
        "Teams Reported (from the tracks received), Teams Pending (Deployed − Reported), and "
        "Reporting % throughout the workbook and report.")
    teams_deployed = int(teams_deployed_in) if teams_deployed_in else None

# ------------------------------------------------ 6. boundary layers
with st.container(border=True):
    step_card("🗺️", "Boundary Layers",
             "Used for the maps (LGA/ward boundaries, state implementation map). "
             "Auto-detected next to the app if present — otherwise upload them below.")

    def boundary_picker(label, default_path, key):
        """Upload control only — the auto-detection status stays in the backend.

        An auto-detected file is still used silently; uploading one overrides it.
        Only when nothing is detected AND nothing is uploaded does the user need
        to be told, since the run cannot proceed without it.
        """
        up = st.file_uploader(f"Upload {label}", type=["sqlite", "gpkg", "geojson"], key=key)
        if up:
            return save_upload(up, WORK)
        if not default_path:
            st.warning(f"{label}: please upload this file")
        return default_path

    c1, c2, c3 = st.columns(3)
    with c1:
        lga_boundary_path = boundary_picker("LGA boundaries", DEFAULT_LGA, "lga_b")
    with c2:
        ward_boundary_path = boundary_picker("Ward boundaries", DEFAULT_WARD, "ward_b")
    with c3:
        state_boundary_path = boundary_picker("State boundaries", DEFAULT_STATE, "state_b")

    logo_path = DEFAULT_LOGO
    if not DEFAULT_LOGO:
        logo_up = st.file_uploader("Logo (optional, PNG)", type=["png"], key="logo_up")
        logo_path = save_upload(logo_up, WORK) if logo_up else None

    st.markdown("**State PHC Logo**")
    st.caption("Campaigns run in different states — upload the relevant State Primary "
              "Healthcare (PHC) logo to place on the report's title page/slide.")
    state_logo_up = st.file_uploader("State PHC logo (optional, PNG or JPEG)",
                                     type=["png", "jpg", "jpeg"], key="state_logo_up")
    state_logo_path = save_upload(state_logo_up, WORK) if state_logo_up else None

# ------------------------------------------------ 6b. map layout templates
map_template_dir = None
map_dpi = None
with st.container(border=True):
    step_card("🗺️", "Map Layout Templates",
             "The maps are drawn from your QGIS print layouts — page size and "
             "orientation, legend, scale bar, north arrow and logo placement all come "
             "from them. Drop a layout (.qpt) or a whole project (.qgz) in beside the "
             "app to change the maps; no code change needed. A layout with two map "
             "frames produces the side-by-side LGA pages.")
    # Template detection, per-role status and the resolution setting are backend
    # concerns — they run exactly as before, just without surfacing anything.
    # MAP_RESOLUTION_DPI is the backend value; None keeps each template's own
    # printResolution (300 dpi).
    map_dpi = MAP_RESOLUTION_DPI

    qpt_ups = st.file_uploader(
        "Upload map layout templates (.qpt, .qgz or .qgs)", type=["qpt", "qgz", "qgs"],
        accept_multiple_files=True, key="qpt_up")
    if qpt_ups:
        map_template_dir = os.path.join(WORK, "map_templates")
        os.makedirs(map_template_dir, exist_ok=True)
        for up in qpt_ups:
            with open(os.path.join(map_template_dir, up.name), "wb") as fh:
                fh.write(up.getbuffer())

# ------------------------------------------------ 7. reporting template
pptx_template_path = None
pir_template_path = None
with st.container(border=True):
    if analysis_type == "daily":
        step_card("📄", "Organization Report Template (optional)",
                 "The final output report has been generated from the embedded custom eHA "
                 "organizational branded reporting template.")
        # The embedded template is applied silently; an upload overrides it.
        pptx_up = st.file_uploader("Upload .pptx template", type=["pptx"], key="pptx_up")
        pptx_template_path = save_upload(pptx_up, WORK) if pptx_up else DEFAULT_PPTX_TEMPLATE
    else:
        step_card("📄", "Reporting Template",
                 "The Post-Implementation Report (PIR) is built in the style of the organization's "
                 "Post Implementation Report sample — applied automatically, no action needed.")
        pir_template_path = DEFAULT_PIR_TEMPLATE
        if DEFAULT_PIR_TEMPLATE:
            st.success(f"Reporting template: found `{os.path.basename(DEFAULT_PIR_TEMPLATE)}` — "
                       "applied automatically.")
            use_other_pir = st.checkbox("Use a different PIR sample/template", key="chk_pir")
            if use_other_pir:
                pir_up = st.file_uploader("Upload PIR sample/template", type=["pdf", "docx"], key="pir_up")
                pir_template_path = save_upload(pir_up, WORK) if pir_up else DEFAULT_PIR_TEMPLATE
        else:
            st.warning("No Post Implementation Report sample found next to the app — the PIR will "
                      "use the built-in default layout.")
            pir_up = st.file_uploader("Upload PIR sample/template (optional)", type=["pdf", "docx"],
                                      key="pir_up")
            pir_template_path = save_upload(pir_up, WORK) if pir_up else None

    campaign_name = st.text_input("Campaign title (shown on the report's title page/slide)",
                                  value="Vaccination Tracking Report")

# ------------------------------------------------ sidebar control panel
with st.sidebar:
    if LOGO_B64:
        st.markdown(f'<img src="data:image/png;base64,{LOGO_B64}" style="height:32px;margin-bottom:4px;" />',
                   unsafe_allow_html=True)
    st.markdown("### GTS Pipeline")
    st.caption("Vaccination Tracking Automation")
    st.divider()

    st.markdown("**Setup Progress**")
    checklist = [
        ("Planned settlements", bool(sett_file), True),
        ("Track files", bool(tracks_files), True),
        ("Gridded target area", bool(ta_file), True),
        ("Voronoi target area", bool(vor_file), True),
        ("LGA boundaries", bool(lga_boundary_path), True),
        ("Ward boundaries", bool(ward_boundary_path), False),
        ("State boundaries", bool(state_boundary_path), False),
    ]
    for label, done, required in checklist:
        if done:
            icon = "✅"
        elif required:
            icon = "🔲"
        else:
            icon = "▫️"
        tag = "" if required else " _(optional)_"
        st.markdown(f"{icon} {label}{tag}")

    missing = [label for label, done, required in checklist if required and not done]
    ready = not missing

    # Flag a run that is unlikely to survive the runner's memory BEFORE it
    # starts, with the reason, rather than after twenty minutes of merging.
    for _warning in check_inputs(
            tracks_bytes=sum(getattr(f, "size", 0) or 0 for f in (tracks_files or [])),
            settlements_bytes=getattr(sett_file, "size", 0) or 0 if sett_file else 0,
            n_track_files=len(tracks_files or [])):
        st.warning(_warning, icon="⚠️")

    st.divider()
    run = st.button("🚀 Run Analysis", type="primary", disabled=not ready,
                    use_container_width=True,
                    help=None if ready else f"Missing: {', '.join(missing)}")
    if missing:
        st.warning(f"Still needed:\n\n" + "\n".join(f"- {m}" for m in missing))

    st.divider()
    if st.button("↺ Start Over", use_container_width=True):
        for k in ("workdir", "results"):
            st.session_state.pop(k, None)
        st.rerun()

    st.divider()
    st.caption("eHealth Africa · GTS Automation")

if run:
    out_dir = os.path.join(WORK, f"{state_name}_Day{day}")
    os.makedirs(out_dir, exist_ok=True)
    tracks_dir = os.path.join(WORK, "tracks_in")
    os.makedirs(tracks_dir, exist_ok=True)
    for tf in tracks_files:
        save_upload(tf, tracks_dir)
    ta_path = save_upload(ta_file, WORK)
    vor_path = save_upload(vor_file, WORK)
    prev_path = save_upload(prev_file, WORK, "prev_dip.csv") if prev_file else None


    progress = st.progress(0, "Starting…")
    log = st.status("Running pipeline", expanded=True)

    try:
        with log:
            st.write("**Stage 1** — merging tracks…")
            merged = merge_tracks(tracks_dir, out_dir)
            progress.progress(20, "Tracks merged")

            st.write("**Stage 2** — settlement visitation analysis…")
            result = run_analysis(sett_path, merged, ta_path, vor_path, state_name,
                                  int(day), mopup, out_dir, prev_dip_file=prev_path,
                                  track_date=None if analysis_type == "post_campaign"
                                             else "latest")
            cum_col = result["cum_col"]
            visitation_csv = result["visitation_csv"]
            progress.progress(50, "Analysis complete")

            st.write("**Stage 3** — ERM workbook…")
            teams = load_tracks_teams(merged)
            is_pc = analysis_type == "post_campaign"
            wb_path = os.path.join(out_dir, f"{state_name}_PostCampaign_ERM_Analysis.xlsx") if is_pc \
                else os.path.join(out_dir, f"{state_name}_Day_{day}_Vaccination_Tracking_ERM_Analysis.xlsx")
            deploy_csv = os.path.join(out_dir, f"team_deploy_day_{day}.csv")
            time_csv = os.path.join(out_dir, f"time_spent_day_{day}.csv")
            build_workbook_from_agg(visitation_csv, teams, cum_col, int(day), state_name,
                                    wb_path, deploy_csv=deploy_csv, time_csv=time_csv,
                                    flagged_csv=os.path.join(out_dir, f"flagged_teams_day_{day}.csv"),
                                    analysis_type=analysis_type,
                                    teams_deployed_total=teams_deployed)
            progress.progress(65, "Workbook ready")

            st.write("**Stage 4** — charts & maps…")
            charts_dir = os.path.join(out_dir, "charts")
            generate_charts(visitation_csv, cum_col, deploy_csv, time_csv, int(day), charts_dir,
                            analysis_type=analysis_type,
                            daily_col=result.get("daily_col"),
                            track_date=result.get("track_date"),
                            tracks_file=merged)
            maps_dir = os.path.join(out_dir, "maps")
            os.makedirs(maps_dir, exist_ok=True)
            vis = pd.read_csv(visitation_csv, low_memory=False)
            lga_col2 = find_col(vis, "lga")
            impl_lgas = sorted(vis[lga_col2].dropna().astype(str).str.strip().str.title().unique())
            # uploaded layouts take precedence over the ones beside the app
            set_template_dirs([map_template_dir] if map_template_dir else
                              [APP_DIR, BASE_DIR, os.getcwd()])
            # A hosted runner cannot hold a 300 dpi A0 raster; the cap is
            # applied here so an explicit --map-dpi still wins locally.
            set_dpi_override(map_dpi_override() or map_dpi)
            if state_boundary_path:
                implementation_map(state_boundary_path, lga_boundary_path, state_name, impl_lgas,
                                   os.path.join(maps_dir, f"{state_name}_implementation_map.png"),
                                   logo=logo_path,
                                   title=f"{state_name} State GTS Implementation Map — {len(impl_lgas)} LGAs")
            else:
                st.warning("Skipped implementation map — no state boundary file provided.")
            # vor_path and stage 2's gridded TA are what the side-by-side LGA
            # maps are drawn from; stage 4 falls back to the single-panel
            # settlement-point map on its own if either is unavailable
            generate_maps(visitation_csv, cum_col, lga_boundary_path, state_name, int(day),
                          maps_dir, logo_path, ward_boundary_path,
                          voronoi_file=vor_path,
                          gridded_ta_file=result.get("ta_parquet"),
                          state_logo=state_logo_path)
            progress.progress(85, "Maps ready")

            pptx_path = None
            if analysis_type == "post_campaign":
                st.write("**Stage 7** — Post-Implementation Report (PIR)…")
                report_path = os.path.join(out_dir, f"{state_name}_Post_Implementation_Report.docx")
                build_pir_report(state_name, visitation_csv, cum_col, deploy_csv, time_csv,
                                 maps_dir, report_path, logo_path,
                                 campaign_name=campaign_name, charts_folder=charts_dir,
                                 state_logo=state_logo_path, template_path=pir_template_path)
                report_label = "📄 Post-Implementation Report (.docx)"
            else:
                st.write("**Stage 5** — report draft…")
                report_path = os.path.join(out_dir, f"{state_name}_Day_{day}_Daily_Tracking_Report_DRAFT.docx")
                build_report(state_name, int(day), visitation_csv, cum_col, deploy_csv,
                             time_csv, maps_dir, report_path, logo_path,
                             campaign_name=campaign_name, charts_folder=charts_dir,
                             state_logo=state_logo_path)
                report_label = "📄 Report draft (.docx)"
                if pptx_template_path:
                    st.write("**Stage 6** — report in your organization's PPTX template…")
                    pptx_path = os.path.join(out_dir, f"{state_name}_Day_{day}_Report.pptx")
                    build_pptx_report(pptx_template_path, state_name, int(day), visitation_csv,
                                      cum_col, deploy_csv, time_csv, maps_dir, charts_dir,
                                      pptx_path, campaign_name=campaign_name,
                                      state_logo_path=state_logo_path,
                                      tracks_file=merged)
            progress.progress(100, "Done")
        log.update(label="Pipeline complete ✅", state="complete")
        st.session_state.results = {
            "out_dir": out_dir, "report": report_path, "report_label": report_label,
            "workbook": wb_path, "visitation": visitation_csv, "maps": maps_dir,
            "charts": charts_dir, "state": state_name, "day": int(day), "pptx": pptx_path,
            "cum_col": cum_col,
        }
    except Exception as e:
        log.update(label="Pipeline failed ❌", state="error")
        st.exception(e)

# ------------------------------------------------ results
if "results" in st.session_state:
    r = st.session_state.results

    st.markdown(
        f'<div class="gts-result-banner"><div>'
        f'<p class="gts-result-title">📦 Results — {r["state"]} · Day {r["day"]}</p>'
        f'<p class="gts-result-subtitle">Pipeline complete — download the report, workbook and '
        f'supporting files below</p></div></div>',
        unsafe_allow_html=True,
    )

    with st.container(border=True):
        try:
            vis_df = pd.read_csv(r["visitation"], low_memory=False)
            cum_col = r.get("cum_col")
            if cum_col and cum_col in vis_df.columns:
                visited = int((vis_df[cum_col] == "Visited").sum())
                total = len(vis_df)
                pct = visited / total * 100 if total else 0
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("State", r["state"])
                m2.metric("Campaign Day", r["day"])
                m3.metric("Settlements Visited", f"{visited:,}")
                m4.metric("Coverage", f"{pct:.1f}%")
        except Exception:
            pass

        st.markdown("⬇️ **Downloads**")
        # The .docx draft is still produced by stage 5 but is no longer offered
        # here — the branded .pptx is the report that goes out.
        cols = st.columns(4 if r.get("pptx") else 3)
        with open(r["workbook"], "rb") as f:
            cols[0].download_button("📊 ERM workbook (.xlsx)", f.read(),
                                    os.path.basename(r["workbook"]), use_container_width=True)
        with open(r["visitation"], "rb") as f:
            cols[1].download_button("🗂️ Visitation CSV", f.read(),
                                    os.path.basename(r["visitation"]), use_container_width=True)

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for folder in (r["maps"], r["charts"]):
                for fn in os.listdir(folder):
                    z.write(os.path.join(folder, fn),
                            os.path.join(os.path.basename(folder), fn))
        cols[2].download_button("🗺️ Maps + charts (.zip)", buf.getvalue(),
                                f"{r['state']}_Day{r['day']}_maps_charts.zip", use_container_width=True)

        if r.get("pptx"):
            with open(r["pptx"], "rb") as f:
                cols[3].download_button("📽️ Report (.pptx, org template)", f.read(),
                                        os.path.basename(r["pptx"]), use_container_width=True)

        tab1, tab2 = st.tabs(["🗺️ Maps", "📊 Charts"])
        with tab1:
            pngs = sorted(os.listdir(r["maps"]))
            # default the preview to the State Visitation Coverage map rather
            # than whichever LGA happens to sort first
            statewide_name = f"{r['state']}_statewide_day_{r['day']}.png"
            default_idx = pngs.index(statewide_name) if statewide_name in pngs else 0
            sel = st.selectbox("Preview map", pngs, index=default_idx)
            st.image(os.path.join(r["maps"], sel), use_container_width=True)
        with tab2:
            for fn in sorted(os.listdir(r["charts"])):
                st.image(os.path.join(r["charts"], fn), use_container_width=True)
