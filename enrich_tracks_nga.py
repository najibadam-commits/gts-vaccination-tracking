"""Flexible NGA administrative enrichment for GTS tracks.

Adds NGA State/LGA/Ward labels to tracks while preserving source attributes.
Existing non-empty labels are preserved; missing labels are filled by spatial
joins against the State.sqlite, LGA.sqlite and Ward.sqlite boundary layers
already shipped with this repository.
"""
from __future__ import annotations

import os
from pathlib import Path

import geopandas as gpd
import pandas as pd

from track_columns import read_columns, resolve

NGA_STATE_LABEL_COL = "NGA State Label"
NGA_LGA_LABEL_COL = "NGA LGA Label"
NGA_WARD_LABEL_COL = "NGA Ward Label"
LABEL_COLUMNS = [NGA_STATE_LABEL_COL, NGA_LGA_LABEL_COL, NGA_WARD_LABEL_COL]
WARD_LABEL_OVERRIDE_COL = "NGA Ward 2024 Label"
WARD_LABEL_OVERRIDES = {"Yantumaki B", "Yusufari"}
_BOUNDARY_FILES = {"state": "state.sqlite", "lga": "LGA.sqlite", "ward": "Ward.sqlite"}


def _find_repo_file(filename: str) -> str | None:
    here = Path(__file__).resolve().parent
    for folder in (here, here.parent, Path.cwd()):
        p = folder / filename
        if p.exists():
            return str(p)
    return None


def _spatial_layer(path: str) -> str | None:
    try:
        import pyogrio
        layers = pyogrio.list_layers(path)
        return str(layers[0][0]) if len(layers) else None
    except Exception:
        return None


def _label_column(gdf: gpd.GeoDataFrame, level: str) -> str:
    preferred = {
        "state": ["NGA State Label", "State Name", "State", "NAME_1", "name"],
        "lga": ["NGA LGA Label", "LGA Name", "LGA", "NAME_2", "name"],
        "ward": ["NGA Ward Label", "Ward Name", "Ward", "NAME_3", "name"],
    }[level]
    for candidate in preferred:
        if candidate in gdf.columns:
            return candidate
    for col in gdf.columns:
        low = str(col).lower()
        if level in low and ("name" in low or "label" in low) and "code" not in low:
            return col
    for col in gdf.columns:
        if col != gdf.geometry.name and pd.api.types.is_object_dtype(gdf[col]):
            return col
    raise ValueError(f"No label/name column found in {level} boundary: {list(gdf.columns)}")


def _load_boundary(level: str) -> gpd.GeoDataFrame:
    path = _find_repo_file(_BOUNDARY_FILES[level])
    if not path:
        raise FileNotFoundError(f"Required {level} boundary '{_BOUNDARY_FILES[level]}' was not found beside the app.")
    layer = _spatial_layer(path)
    gdf = gpd.read_file(path, layer=layer) if layer else gpd.read_file(path)
    if gdf.empty or gdf.geometry.isna().all():
        raise ValueError(f"{level} boundary has no usable geometry")
    gdf = gdf.set_crs("EPSG:4326") if gdf.crs is None else gdf.to_crs("EPSG:4326")
    label = _label_column(gdf, level)
    return gpd.GeoDataFrame({"label": gdf[label].astype(str).str.strip()}, geometry=gdf.geometry, crs="EPSG:4326")


def load_boundaries() -> dict[str, gpd.GeoDataFrame]:
    return {level: _load_boundary(level) for level in ("state", "lga", "ward")}


def _read_track(path: str) -> gpd.GeoDataFrame:
    if Path(path).suffix.lower() == ".csv":
        df = pd.read_csv(path, low_memory=False)
        cols = resolve(df.columns)
        if not cols.get("lat") or not cols.get("lon"):
            raise ValueError(f"Track CSV has no usable latitude/longitude fields: {list(df.columns)}")
        lat = pd.to_numeric(df[cols["lat"]], errors="coerce")
        lon = pd.to_numeric(df[cols["lon"]], errors="coerce")
        valid = lat.notna() & lon.notna() & lat.between(-90, 90) & lon.between(-180, 180) & ~((lat == 0) & (lon == 0))
        if not bool(valid.any()):
            raise ValueError("Track CSV contains no valid coordinates")
        return gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(lon, lat, crs="EPSG:4326"), crs="EPSG:4326")
    gdf = gpd.read_parquet(path) if Path(path).suffix.lower() == ".parquet" else gpd.read_file(path)
    return gdf.set_crs("EPSG:4326") if gdf.crs is None else gdf


def _join_boundaries(tracks: gpd.GeoDataFrame, boundaries: dict[str, gpd.GeoDataFrame]) -> gpd.GeoDataFrame:
    out = tracks.copy()
    tracks_4326 = tracks.to_crs("EPSG:4326") if tracks.crs and tracks.crs.to_string() != "EPSG:4326" else tracks
    valid = tracks_4326.loc[tracks_4326.geometry.notna() & ~tracks_4326.geometry.is_empty]
    for level, label_col in (("state", NGA_STATE_LABEL_COL), ("lga", NGA_LGA_LABEL_COL), ("ward", NGA_WARD_LABEL_COL)):
        if valid.empty:
            out[label_col] = out.get(label_col, "")
            continue
        joined = gpd.sjoin(valid[["geometry"]], boundaries[level], how="left", predicate="intersects")
        joined = joined[~joined.index.duplicated(keep="first")]
        spatial = joined["label"].fillna("").astype(str).str.strip()
        if label_col in out.columns:
            existing = out[label_col].fillna("").astype(str).str.strip()
            out.loc[valid.index, label_col] = existing.loc[valid.index].where(existing.loc[valid.index] != "", spatial)
        else:
            out[label_col] = ""
            out.loc[valid.index, label_col] = spatial
        out[label_col] = out[label_col].fillna("").astype(str)
    if WARD_LABEL_OVERRIDE_COL in out.columns:
        src = out[WARD_LABEL_OVERRIDE_COL].astype(str).str.strip()
        hit = src.isin(WARD_LABEL_OVERRIDES)
        out.loc[hit, NGA_WARD_LABEL_COL] = src.loc[hit]
    return out


def enrich_track_file(path: str, output_dir: str | None = None) -> str:
    """Enrich one track file and return the generated path."""
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    out_dir = output_dir or os.path.join(os.path.dirname(os.path.abspath(path)), "enriched_output")
    os.makedirs(out_dir, exist_ok=True)
    enriched = _join_boundaries(_read_track(path), load_boundaries())
    ext = Path(path).suffix.lower()
    stem = Path(path).stem
    if ext == ".csv":
        out = os.path.join(out_dir, f"{stem}_enriched.csv")
        enriched.drop(columns=["geometry"]).to_csv(out, index=False, encoding="utf-8")
    elif ext == ".parquet":
        out = os.path.join(out_dir, f"{stem}_enriched.parquet")
        enriched.to_parquet(out, index=False)
    else:
        drivers = {".gpkg": "GPKG", ".shp": "ESRI Shapefile", ".geojson": "GeoJSON", ".json": "GeoJSON"}
        if ext not in drivers:
            raise ValueError(f"Unsupported track output format: {ext}")
        out = os.path.join(out_dir, f"{stem}_enriched{ext}")
        enriched.to_file(out, driver=drivers[ext])
    return out


def has_usable_lga(path: str) -> bool:
    try:
        cols = resolve(read_columns(path))
        lga = cols.get("lga")
        if not lga:
            return False
        ext = Path(path).suffix.lower()
        if ext == ".csv":
            sample = pd.read_csv(path, usecols=[lga], nrows=5000, dtype=str)
        elif ext == ".parquet":
            sample = pd.read_parquet(path, columns=[lga])
        else:
            sample = gpd.read_file(path, columns=[lga])
        values = sample[lga].fillna("").astype(str).str.strip().str.lower()
        return bool((~values.isin({"", "nan", "none", "null"})).any())
    except Exception:
        return False
