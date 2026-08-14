"""GTS Vaccination Tracking — web app (redesigned tabbed interface).

Run with:  streamlit run app_modern_ui.py

This is the July 2026 UI redesign, kept alongside the original interface. The
shipped entry point is `app.py`; swap the two filenames to make this the default.

The analyst uploads the planned settlements first; the app detects the state,
LGAs and wards from it, then runs the full daily pipeline.

Layout
------
Inputs are grouped into four tabs that follow the order of the work —
Input Data → Campaign → Geospatial → Report. The sidebar is a persistent
control panel: analysis mode, a live readiness meter over the required inputs,
and the run control. Pipeline progress and results render in an output region
directly beneath the tabs so they are visible no matter which tab is open.

All presentation lives in ``gts_theme.py``. This module keeps the pipeline
wiring only.
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

import gts_theme as ui

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

st.set_page_config(
    page_title="GTS Vaccination Tracking",
    page_icon="🛰️",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ------------------------------------------------ small rendering helpers

def html(markup):
    """Shorthand for emitting one of gts_theme's HTML primitives."""
    st.markdown(markup, unsafe_allow_html=True)


def _wide(fn, *args, **kwargs):
    """Call a Streamlit element full-width across API versions.

    Streamlit replaced ``use_container_width=True`` with ``width="stretch"``.
    Trying the modern spelling first keeps the app free of deprecation banners
    on current releases while still running on older installs.
    """
    try:
        return fn(*args, width="stretch", **kwargs)
    except TypeError:
        return fn(*args, use_container_width=True, **kwargs)


def _choice(label, options, default, key, help=None):
    """Segmented control where available, radio buttons otherwise.

    Segmented controls allow deselection and return None in that case; the
    caller always gets a usable value by falling back to `default`.
    """
    if hasattr(st, "segmented_control"):
        picked = st.segmented_control(label, options, default=default, key=key,
                                      help=help, label_visibility="collapsed")
        return picked if picked is not None else default
    return st.radio(label, options, index=options.index(default), key=key,
                    horizontal=True, help=help, label_visibility="collapsed")


def _button(label, glyph=None, **kwargs):
    """Full-width button, degrading gracefully if `icon` isn't supported."""
    if glyph:
        try:
            return _wide(st.button, label, icon=glyph, **kwargs)
        except TypeError:
            pass
    return _wide(st.button, label, **kwargs)


def _mime_for(path):
    """Data-URI mime type for an inline image preview."""
    ext = os.path.splitext(path or "")[1].lower()
    return "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png"


# ------------------------------------------------ theme
# The Appearance toggle lives at the bottom of the sidebar, but the stylesheet
# has to go out first. Reading the widget's key straight from session state does
# that: it is absent on the very first run (so we fall through to light, which
# is also the widget's default) and holds the user's pick on every rerun after.
# Deliberately read with .get() rather than pre-seeding the key — assigning to a
# widget's key before the widget exists makes Streamlit warn about it.
ACTIVE_THEME = "dark" if st.session_state.get("gts_theme") == "Dark" else "light"
html(ui.build_css(ACTIVE_THEME))


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


# ================================================ sidebar — mode selection
# Split across two blocks: the analysis mode has to be resolved before the tabs
# render (it decides which reporting card is shown), while the readiness meter
# has to come after them (it reads the uploaded files). Streamlit appends to the
# sidebar in code order, so the panel still reads top-to-bottom.
with st.sidebar:
    html(ui.brand_mark("GTS Pipeline", "Vaccination Tracking", LOGO_B64))
    st.divider()

    html(ui.eyebrow("Analysis mode", "compass"))
    analysis_choice = _choice(
        "What do you want to run?",
        ["Daily ERM Analysis", "Post-Campaign Analysis"],
        default="Daily ERM Analysis",
        key="analysis_choice",
        help="Daily ERM Analysis: today's numbers for the evening review meeting. "
        "Post-Campaign Analysis: whole-campaign numbers for the Post-Implementation Report (PIR) — "
        "same inputs, just point Campaign Day at the final day (with all prior days chained via "
        "the previous-day visitation CSV) and, ideally, upload all campaign days' tracks.")
    analysis_type = "post_campaign" if analysis_choice == "Post-Campaign Analysis" else "daily"

    if analysis_type == "post_campaign":
        st.caption("Whole-campaign totals → Post-Implementation Report (.docx).")
    else:
        st.caption("Single-day totals → Daily Tracking Report + branded deck.")

    st.divider()


# ================================================ header
mode_pill = ui.pill(
    "Post-Campaign" if analysis_type == "post_campaign" else "Daily ERM",
    kind="brand", glyph="compass")
status_pill = (ui.pill("Run complete", "ok", "check-circle")
               if "results" in st.session_state
               else ui.pill("Awaiting inputs", "neutral", "clock"))

html(ui.app_bar(
    "GTS Vaccination Tracking",
    "Merge tracks → visitation analysis → ERM workbook → coverage maps → report",
    logo_b64=LOGO_B64,
    meta=[mode_pill, status_pill],
))


# ================================================ input workspace
tab_data, tab_campaign, tab_geo, tab_report = st.tabs([
    "1 · Input Data",
    "2 · Campaign",
    "3 · Geospatial",
    "4 · Report",
])

# ------------------------------------------------ tab 1 — input data
with tab_data:
    # --- planned settlements (DIP)
    sett_path = None
    with st.container(border=True):
        html(ui.section(
            "Planned Settlements (DIP)",
            "The state, LGAs and wards for the whole run are detected from this file. "
            "Upload it first — everything downstream is filtered to what it contains.",
            glyph="database", badge=ui.pill("Required", "brand")))

        sett_file = st.file_uploader("Upload the planned settlements CSV", type=["csv"],
                                     key="sett_up",
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

                html(ui.stat_grid([
                    ui.stat("State", state_name, accent=True),
                    ui.stat("LGAs", f"{n_lga}"),
                    ui.stat("Wards", f"{int(n_ward)}"),
                    ui.stat("Planned settlements", f"{len(sub):,}"),
                ], columns=4))

                with st.expander(f"LGAs detected ({n_lga})"):
                    st.write(", ".join(sorted(sub[lga_col].dropna().astype(str).str.strip()
                                              .str.title().unique())) if lga_col else "—")
                sett_path = save_upload(sett_file, WORK, "settlements.csv")

    # --- GTS tracks
    with st.container(border=True):
        html(ui.section(
            "GTS Tracks",
            "The day's raw GPS exports. For a post-campaign run, upload every day's tracks "
            "so team deployment and time-spent cover the whole campaign.",
            glyph="satellite", badge=ui.pill("Required", "brand")))

        tracks_files = st.file_uploader(
            "Upload the day's track exports (Tracks_0.csv, Tracks_1.csv, …)",
            type=["csv"], accept_multiple_files=True, key="tracks_up")
        if tracks_files:
            total_mb = sum(getattr(f, "size", 0) for f in tracks_files) / (1024 * 1024)
            html(ui.stat_grid([
                ui.stat("Track files", f"{len(tracks_files)}", accent=True),
                ui.stat("Combined size", f"{total_mb:,.0f} MB" if total_mb else "—",
                        sub="Merged in stage 1"),
            ], columns=2))

    # --- target areas
    with st.container(border=True):
        html(ui.section(
            "Target Areas",
            "Gridded cells drive the visited/not-visited test; Voronoi polygons define "
            "each settlement's extent.",
            glyph="target", badge=ui.pill("Required", "brand")))

        c1, c2 = st.columns(2)
        with c1:
            st.caption("Gridded target area")
            ta_file = st.file_uploader("Gridded target area", type=["sqlite", "gpkg", "geojson"],
                                       key="ta_up", label_visibility="collapsed")
        with c2:
            st.caption("Voronoi target area (settlement extents)")
            vor_file = st.file_uploader("Voronoi target area (settlement extents)",
                                        type=["sqlite", "gpkg", "geojson"],
                                        key="vor_up", label_visibility="collapsed")

# ------------------------------------------------ tab 2 — campaign
with tab_campaign:
    with st.container(border=True):
        html(ui.section(
            "Campaign Day",
            "Which day of the campaign this run covers. For day 2 onward, attach the "
            "previous day's visitation CSV so cumulative coverage carries forward.",
            glyph="calendar"))

        c1, c2 = st.columns([1, 1])
        with c1:
            day = st.number_input("Day of campaign", min_value=1, max_value=14, value=1,
                                  key="day_in")
        with c2:
            st.caption("Day type")
            mopup = st.checkbox("Mop-up day", key="mopup_in",
                                help="Marks this run as a mop-up day.")

        st.caption("Previous day's visitation CSV — required from day 2 onward")
        prev_file = st.file_uploader("Previous day's visitation CSV (for day 2+)", type=["csv"],
                                     key="prev_up", label_visibility="collapsed")
        if int(day) > 1 and not prev_file:
            st.warning("Day 2 or later without a previous-day CSV — cumulative coverage will "
                       "restart from zero for this run.")

    with st.container(border=True):
        html(ui.section(
            "Team Deployment",
            "Sets the denominator for Teams Reported, Teams Pending and Reporting % "
            "throughout the workbook and report.",
            glyph="users"))

        teams_deployed_in = st.number_input(
            "Total teams deployed (optional — overrides the count derived from the planned-"
            "settlement list's team codes)",
            min_value=0, value=0, step=1, key="teams_in",
            help="Leave at 0 to use the settlement list's own team-code count. When set, this drives "
            "Teams Reported (from the tracks received), Teams Pending (Deployed − Reported), and "
            "Reporting % throughout the workbook and report.")
        teams_deployed = int(teams_deployed_in) if teams_deployed_in else None

        if teams_deployed:
            html(ui.pill(f"Denominator: {teams_deployed} teams deployed", "info", "users"))
        else:
            html(ui.pill("Falling back to the settlement list's team-code count", "neutral", "info"))

# ------------------------------------------------ tab 3 — geospatial
with tab_geo:
    with st.container(border=True):
        html(ui.section(
            "Boundary Layers",
            "Used for the maps (LGA/ward boundaries, state implementation map). "
            "Auto-detected next to the app if present — otherwise upload them below.",
            glyph="layers"))

        def boundary_picker(label, default_path, key):
            """Upload control only — the auto-detection status stays in the backend.

            An auto-detected file is still used silently; uploading one overrides it.
            Only when nothing is detected AND nothing is uploaded does the user need
            to be told, since the run cannot proceed without it.
            """
            st.caption(label)
            up = st.file_uploader(f"Upload {label}", type=["sqlite", "gpkg", "geojson"],
                                  key=key, label_visibility="collapsed")
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
            st.markdown('<hr class="gts-divider" />', unsafe_allow_html=True)
            st.caption("Organization logo (optional, PNG) — placed on every map")
            logo_up = st.file_uploader("Logo (optional, PNG)", type=["png"], key="logo_up",
                                       label_visibility="collapsed")
            logo_path = save_upload(logo_up, WORK) if logo_up else None

    # --- map layout templates
    map_template_dir = None
    map_dpi = None
    with st.container(border=True):
        html(ui.section(
            "Map Layout Templates",
            "The maps are drawn from your QGIS print layouts — page size and "
            "orientation, legend, scale bar, north arrow and logo placement all come "
            "from them. Drop a layout (.qpt) or a whole project (.qgz) in beside the "
            "app to change the maps; no code change needed. A layout with two map "
            "frames produces the side-by-side LGA pages.",
            glyph="map", badge=ui.pill("Optional", "neutral")))

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
            html(ui.pill(f"{len(qpt_ups)} uploaded layout(s) will override the bundled templates",
                         "info", "check"))

# ------------------------------------------------ tab 4 — report
pptx_template_path = None
pir_template_path = None
with tab_report:
    with st.container(border=True):
        if analysis_type == "daily":
            html(ui.section(
                "Organization Report Template",
                "The final output report is generated from the embedded custom eHA "
                "organizational branded reporting template. Upload a .pptx here only to "
                "override it.",
                glyph="file", badge=ui.pill("Optional", "neutral")))
            # The embedded template is applied silently; an upload overrides it.
            pptx_up = st.file_uploader("Upload .pptx template", type=["pptx"], key="pptx_up")
            pptx_template_path = save_upload(pptx_up, WORK) if pptx_up else DEFAULT_PPTX_TEMPLATE
        else:
            html(ui.section(
                "Reporting Template",
                "The Post-Implementation Report (PIR) is built in the style of the organization's "
                "Post Implementation Report sample — applied automatically, no action needed.",
                glyph="file"))
            pir_template_path = DEFAULT_PIR_TEMPLATE
            if DEFAULT_PIR_TEMPLATE:
                st.success(f"Reporting template: found `{os.path.basename(DEFAULT_PIR_TEMPLATE)}` — "
                           "applied automatically.")
                use_other_pir = st.checkbox("Use a different PIR sample/template", key="chk_pir")
                if use_other_pir:
                    pir_up = st.file_uploader("Upload PIR sample/template", type=["pdf", "docx"],
                                              key="pir_up")
                    pir_template_path = save_upload(pir_up, WORK) if pir_up else DEFAULT_PIR_TEMPLATE
            else:
                st.warning("No Post Implementation Report sample found next to the app — the PIR will "
                           "use the built-in default layout.")
                pir_up = st.file_uploader("Upload PIR sample/template (optional)",
                                          type=["pdf", "docx"], key="pir_up")
                pir_template_path = save_upload(pir_up, WORK) if pir_up else None

    with st.container(border=True):
        html(ui.section(
            "Title & Branding",
            "Shown on the report's title page and, for daily runs, the deck's title slide.",
            glyph="sparkle"))

        campaign_name = st.text_input("Campaign title (shown on the report's title page/slide)",
                                      value="Vaccination Tracking Report", key="campaign_in")

        st.markdown('<hr class="gts-divider" />', unsafe_allow_html=True)
        st.caption("State PHC logo (optional, PNG or JPEG) — campaigns run in different states, "
                   "so this is separate from the fixed organization logo.")
        state_logo_up = st.file_uploader("State PHC logo (optional, PNG or JPEG)",
                                         type=["png", "jpg", "jpeg"], key="state_logo_up",
                                         label_visibility="collapsed")
        state_logo_path = save_upload(state_logo_up, WORK) if state_logo_up else None

        preview = _img_b64(state_logo_path) if state_logo_path else None
        if preview:
            html(f'<img src="data:{_mime_for(state_logo_path)};base64,{preview}" '
                 'style="height:52px;margin-top:8px;border-radius:8px;" alt="State PHC logo" />')


# ================================================ sidebar — readiness & run
with st.sidebar:
    checklist = [
        ("Planned settlements", bool(sett_path), True),
        ("Track files", bool(tracks_files), True),
        ("Gridded target area", bool(ta_file), True),
        ("Voronoi target area", bool(vor_file), True),
        ("LGA boundaries", bool(lga_boundary_path), True),
        ("Ward boundaries", bool(ward_boundary_path), False),
        ("State boundaries", bool(state_boundary_path), False),
    ]
    missing = [label for label, done, required in checklist if required and not done]
    ready = not missing

    required_total = sum(1 for _, _, req in checklist if req)
    required_done = sum(1 for _, done, req in checklist if req and done)

    html(ui.eyebrow("Input readiness", "check"))
    html(ui.readiness_meter(required_done, required_total, ready))
    html(ui.checklist(checklist))

    st.divider()

    run = _button("Run Analysis", glyph=":material/play_arrow:", type="primary",
                  disabled=not ready,
                  help=None if ready else f"Missing: {', '.join(missing)}")
    if missing:
        st.caption("Still needed: " + ", ".join(missing))

    if _button("Start Over", glyph=":material/restart_alt:", key="reset_btn"):
        for k in ("workdir", "results"):
            st.session_state.pop(k, None)
        st.rerun()

    st.divider()
    html(ui.eyebrow("Appearance", "settings"))
    _choice("Appearance", ["Light", "Dark"], default="Light", key="gts_theme")

    st.divider()
    st.caption("eHealth Africa · GTS Automation")


# ================================================ output region
# Rendered below the tabs rather than inside one, so progress and results are
# visible regardless of which tab the analyst happens to have open.
output_zone = st.container()

if run:
    with output_zone:
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
                set_dpi_override(map_dpi)
                if state_boundary_path:
                    implementation_map(state_boundary_path, lga_boundary_path, state_name, impl_lgas,
                                       os.path.join(maps_dir, f"{state_name}_implementation_map.png"),
                                       logo=logo_path,
                                       title=f"{state_name} State GTS Implementation Map — {len(impl_lgas)} LGAs")
                else:
                    st.warning("Skipped implementation map — no state boundary file provided.")
                # vor_path and stage 2's gridded TA are what the side-by-side
                # LGA maps are drawn from; stage 4 falls back to the
                # single-panel settlement-point map if either is unavailable
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
                    report_label = "Post-Implementation Report (.docx)"
                else:
                    st.write("**Stage 5** — report draft…")
                    report_path = os.path.join(out_dir, f"{state_name}_Day_{day}_Daily_Tracking_Report_DRAFT.docx")
                    build_report(state_name, int(day), visitation_csv, cum_col, deploy_csv,
                                 time_csv, maps_dir, report_path, logo_path,
                                 campaign_name=campaign_name, charts_folder=charts_dir,
                                 state_logo=state_logo_path)
                    report_label = "Report draft (.docx)"
                    if pptx_template_path:
                        st.write("**Stage 6** — report in your organization's PPTX template…")
                        pptx_path = os.path.join(out_dir, f"{state_name}_Day_{day}_Report.pptx")
                        build_pptx_report(pptx_template_path, state_name, int(day), visitation_csv,
                                          cum_col, deploy_csv, time_csv, maps_dir, charts_dir,
                                          pptx_path, campaign_name=campaign_name,
                                          state_logo_path=state_logo_path,
                                          tracks_file=merged)
                progress.progress(100, "Done")
            log.update(label="Pipeline complete", state="complete")
            st.session_state.results = {
                "out_dir": out_dir, "report": report_path, "report_label": report_label,
                "workbook": wb_path, "visitation": visitation_csv, "maps": maps_dir,
                "charts": charts_dir, "state": state_name, "day": int(day), "pptx": pptx_path,
                "cum_col": cum_col, "analysis_type": analysis_type,
            }
        except Exception as e:
            log.update(label="Pipeline failed", state="error")
            st.exception(e)


# ================================================ results
with output_zone:
    if "results" in st.session_state:
        r = st.session_state.results
        is_pc = r.get("analysis_type") == "post_campaign"

        html(ui.result_banner(
            f'Results — {r["state"]} · Day {r["day"]}',
            "Pipeline complete. Download the report, workbook and supporting files below."))

        with st.container(border=True):
            # --- headline numbers
            try:
                vis_df = pd.read_csv(r["visitation"], low_memory=False)
                cum_col = r.get("cum_col")
                if cum_col and cum_col in vis_df.columns:
                    visited = int((vis_df[cum_col] == "Visited").sum())
                    total = len(vis_df)
                    pct = visited / total * 100 if total else 0
                    html(ui.stat_grid([
                        ui.stat("State", r["state"]),
                        ui.stat("Campaign day", str(r["day"])),
                        ui.stat("Settlements visited", f"{visited:,}",
                                sub=f"of {total:,} planned"),
                        ui.stat("Coverage", f"{pct:.1f}%", accent=True),
                    ], columns=4))
            except Exception:
                pass

            st.markdown('<hr class="gts-divider" />', unsafe_allow_html=True)
            html(ui.section("Downloads", "Everything this run produced.", glyph="download"))

            # Daily runs: the .docx draft from stage 5 is still written to the
            # output folder but deliberately not offered here — the branded .pptx
            # is the report that goes out. Post-campaign runs build no deck, so
            # their PIR .docx is the deliverable and is offered directly.
            downloads = [("ERM workbook (.xlsx)", r["workbook"])]
            if is_pc:
                downloads.append((r.get("report_label", "Report (.docx)"), r["report"]))
            elif r.get("pptx"):
                downloads.append(("Report (.pptx, org template)", r["pptx"]))
            downloads.append(("Visitation CSV", r["visitation"]))

            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for folder in (r["maps"], r["charts"]):
                    for fn in os.listdir(folder):
                        z.write(os.path.join(folder, fn),
                                os.path.join(os.path.basename(folder), fn))

            cols = st.columns(len(downloads) + 1)
            for col, (label, path) in zip(cols, downloads):
                with open(path, "rb") as f:
                    _wide(col.download_button, label, f.read(), os.path.basename(path))
            _wide(cols[-1].download_button, "Maps + charts (.zip)", buf.getvalue(),
                  f"{r['state']}_Day{r['day']}_maps_charts.zip")

            st.markdown('<hr class="gts-divider" />', unsafe_allow_html=True)

            # --- previews
            tab_maps, tab_charts = st.tabs(["Maps", "Charts"])
            with tab_maps:
                pngs = sorted(os.listdir(r["maps"]))
                # default the preview to the State Visitation Coverage map rather
                # than whichever LGA happens to sort first
                statewide_name = f"{r['state']}_statewide_day_{r['day']}.png"
                default_idx = pngs.index(statewide_name) if statewide_name in pngs else 0
                sel = st.selectbox("Preview map", pngs, index=default_idx)
                _wide(st.image, os.path.join(r["maps"], sel))
            with tab_charts:
                chart_files = sorted(os.listdir(r["charts"]))
                for i in range(0, len(chart_files), 2):
                    pair = chart_files[i:i + 2]
                    ccols = st.columns(len(pair))
                    for ccol, fn in zip(ccols, pair):
                        with ccol:
                            _wide(st.image, os.path.join(r["charts"], fn),
                                  caption=os.path.splitext(fn)[0].replace("_", " ").title())
    elif not run:
        html(ui.empty_state(
            "No results yet",
            "Complete the required inputs, then choose Run Analysis in the sidebar. "
            "Pipeline progress and every generated file will appear here.",
            glyph="package"))
