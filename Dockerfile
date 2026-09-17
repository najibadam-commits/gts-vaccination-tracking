# GTS Vaccination Tracking pipeline: container image.
#
# One image runs everywhere: a VM with docker compose (see deploy/), Render,
# or a laptop with Docker Desktop. See deploy/README.md.
#
#   docker build -t gts-pipeline .
#   docker run --rm -p 8501:8501 gts-pipeline
#
# Layout inside the image:
#   /srv/gts/app       the code (APP_DIR in app.py)
#   /srv/gts/private   working directory; mount PRIVATE templates here
#                      (e.g. "Post Implementation Report 2026.pptx"). app.py
#                      searches APP_DIR, its parent and the cwd for templates,
#                      so files mounted here are picked up with no code change,
#                      and they never have to enter the public repo.

FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/matplotlib \
    PORT=8501

# Fonts pipeline_fonts.py prefers over DejaVu, so maps and charts match the
# templates more closely: Open Sans (what the QGIS layouts ask for), Liberation
# (Arial / Times metrics), URW base35 (Century Gothic / Helvetica stand-ins),
# Carlito (Calibri metrics). The geospatial wheels bundle GDAL/GEOS/PROJ, so no
# system GIS packages are needed.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        fonts-open-sans fonts-liberation fonts-urw-base35 fonts-crosextra-carlito \
        fontconfig \
 && fc-cache -f \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 app

WORKDIR /srv/gts/app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY --chown=app:app . .

# Streamlit reads .streamlit/config.toml from the WORKING directory, which is
# /srv/gts/private, not from the app folder, so install it as the user-level
# config instead. Environment variables (STREAMLIT_SERVER_MAX_UPLOAD_SIZE, ...)
# still override it per deployment.
RUN mkdir -p /home/app/.streamlit /srv/gts/private /tmp/matplotlib \
 && cp .streamlit/config.toml /home/app/.streamlit/config.toml \
 && chown -R app:app /home/app/.streamlit /srv/gts/private /tmp/matplotlib \
 && python -c "import matplotlib.font_manager as fm; fm._load_fontmanager(try_read_cache=False)"

USER app
WORKDIR /srv/gts/private
EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8501\")}/_stcore/health', timeout=4)" || exit 1

# $PORT lets hosts that assign a port (Render) work unchanged.
CMD ["sh", "-c", "exec streamlit run /srv/gts/app/app.py --server.address=0.0.0.0 --server.port=${PORT} --server.headless=true"]
