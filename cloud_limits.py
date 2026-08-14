"""Guard rails for running the pipeline on a small hosted runner.

Streamlit Community Cloud gives an app roughly **1 GB of RAM** and an ephemeral
disk. This pipeline was built for a workstation: it merges multi-million-row
track exports, holds them in pandas, runs a geopandas spatial join against the
gridded target areas, and renders A0/A3 map pages at 300 dpi (~3,500 x 5,000 px
each, one per LGA). Any one of those can exhaust a free-tier runner.

Nothing here changes the analysis. It does three things:

    detect   whether we are on a hosted runner at all
    warn     before a run whose inputs look too big to finish, with the
             specific reason, instead of letting it die mid-stage with a
             MemoryError the analyst cannot interpret
    trim     map DPI, which is the single largest memory saving available and
             the only one that costs nothing but print resolution

Locally none of it applies: `is_hosted()` is False and the caps are ignored.
"""
from __future__ import annotations

import os

# Community Cloud sets this; it is the documented way to tell you are hosted.
# The others cover the common alternatives so a move to another host does not
# silently lose the guard rails.
HOSTED_ENV_MARKERS = (
    "STREAMLIT_SHARING_MODE",       # Community Cloud
    "STREAMLIT_SERVER_HEADLESS_HOSTED",
    "DYNO",                         # Heroku
    "K_SERVICE",                    # Cloud Run
    "WEBSITE_INSTANCE_ID",          # Azure App Service
)

# Set GTS_FORCE_LOCAL=1 to run without caps on a big self-hosted machine that
# happens to set one of the markers above.
FORCE_LOCAL_ENV = "GTS_FORCE_LOCAL"

# ---- thresholds ------------------------------------------------------------
# Total upload size beyond which a free-tier run is unlikely to complete. The
# merged file, the DataFrame and the coordinate aggregation all coexist in RAM,
# so peak usage is several times the input size.
MAX_TRACKS_MB_HOSTED = 250
# Settlement lists are small, but a national list joined against a gridded TA
# is not. Warn rather than block: the join is chunked and may still fit.
MAX_SETTLEMENTS_MB_HOSTED = 40
# Map resolution. 300 dpi is print quality and what the QGIS templates specify;
# 120 dpi is legible on screen and in a slide, at roughly one sixteenth of the
# pixels — the difference between a map that renders and one that does not.
MAP_DPI_HOSTED = 120


def is_hosted() -> bool:
    """True when running on a hosted runner rather than a workstation."""
    if os.environ.get(FORCE_LOCAL_ENV, "").strip().lower() in ("1", "true", "yes"):
        return False
    return any(os.environ.get(k) for k in HOSTED_ENV_MARKERS)


def map_dpi_override() -> int | None:
    """Map DPI to force, or None to leave the templates' own 300 dpi alone."""
    return MAP_DPI_HOSTED if is_hosted() else None


def _mb(size_bytes: float) -> float:
    return size_bytes / (1024 * 1024)


def check_inputs(tracks_bytes: float = 0, settlements_bytes: float = 0,
                 n_track_files: int = 0) -> list[str]:
    """Warnings to show before a run. Empty list means nothing to flag.

    Advisory only — the analyst decides whether to proceed. A run that is going
    to fail is better flagged in advance, with the reason and the remedy, than
    discovered twenty minutes in.
    """
    if not is_hosted():
        return []

    warnings = []
    tracks_mb = _mb(tracks_bytes)
    setts_mb = _mb(settlements_bytes)

    if tracks_mb > MAX_TRACKS_MB_HOSTED:
        warnings.append(
            f"**Track upload is {tracks_mb:,.0f} MB.** This app has about 1 GB "
            f"of memory in total, and merging uses several times the input "
            f"size, so runs over ~{MAX_TRACKS_MB_HOSTED} MB usually stop with "
            f"an out-of-memory error. Either split the campaign day into "
            f"fewer LGAs, or run the pipeline locally for a full state.")

    if setts_mb > MAX_SETTLEMENTS_MB_HOSTED:
        warnings.append(
            f"**Settlement list is {setts_mb:,.0f} MB.** If this is a national "
            f"list, filter it to the campaign state before uploading — the "
            f"spatial join holds the whole list in memory.")

    if n_track_files > 40:
        warnings.append(
            f"**{n_track_files} track files.** Each is opened in turn during "
            f"the merge; a large batch is slow on a shared runner and may hit "
            f"the request timeout. Consider merging them locally first and "
            f"uploading the single merged file.")

    return warnings


def banner() -> str | None:
    """One-line notice describing the active caps, or None when running local."""
    if not is_hosted():
        return None
    return (f"Running on a hosted runner: maps render at {MAP_DPI_HOSTED} dpi "
            f"instead of 300, and uploads are capped. For full-resolution "
            f"output on a whole state, run the pipeline locally — see "
            f"DEPLOYMENT.md.")
