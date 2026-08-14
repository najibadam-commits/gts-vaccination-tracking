"""Stage 1 — Merge GTS track CSV exports into a single file (memory-safe streaming).

Reads all Tracks_*.csv chunks, repairs rows where stray commas appear inside
the Team Code field, validates coordinates, and streams out:
  - merged_tracks.csv      (single clean file — main analysis input)
  - tracks.geojson         (optional, for QGIS) via --geojson

Handles millions of rows on small-memory machines: nothing is held in RAM.
"""
import argparse
import csv
import glob
import json
import os
import sys

EXPECTED_COLS = 23
TEAM_CODE_IDX = 12  # 0-based index of Team Code column in GTS export
LAT_IDX_NAME, LON_IDX_NAME = "Lat", "Lon"


def fix_row(row: list[str], expected_cols: int) -> list[str] | None:
    if len(row) == expected_cols:
        return row
    if len(row) > expected_cols:
        overflow = len(row) - expected_cols
        row[TEAM_CODE_IDX] = ",".join(row[TEAM_CODE_IDX:TEAM_CODE_IDX + overflow + 1])
        return row[:TEAM_CODE_IDX + 1] + row[TEAM_CODE_IDX + overflow + 1:]
    return None  # short rows dropped


def merge_tracks(input_folder: str, output_folder: str | None = None,
                 file_pattern: str = "Tracks_*.csv",
                 write_geojson: bool = False) -> str:
    """Streams all track chunks into one clean CSV. Returns its path."""
    output_folder = output_folder or input_folder
    os.makedirs(output_folder, exist_ok=True)

    csv_files = sorted(glob.glob(os.path.join(input_folder, file_pattern)))
    if not csv_files:
        sys.exit(f"No files matching {file_pattern} in {input_folder}")

    out_csv = os.path.join(output_folder, "merged_tracks.csv")
    gj_path = os.path.join(output_folder, "tracks.geojson")
    gj = None

    header_out = None
    lat_i = lon_i = None
    total = 0

    print(f"Merging {len(csv_files)} files...")
    with open(out_csv, "w", encoding="utf-8", newline="") as fout:
        writer = csv.writer(fout)
        if write_geojson:
            gj = open(gj_path, "w", encoding="utf-8")
            gj.write('{"type":"FeatureCollection","name":"tracks",'
                     '"crs":{"type":"name","properties":{"name":"urn:ogc:def:crs:OGC:1.3:CRS84"}},'
                     '"features":[\n')
        first_feature = True

        for fi, csv_file in enumerate(csv_files, 1):
            kept = 0
            with open(csv_file, "r", encoding="utf-8", errors="ignore") as f:
                header = f.readline().rstrip("\n").split(",")
                n = len(header)
                if header_out is None:
                    header_out = header + ["source_file"]
                    lat_i = header.index(LAT_IDX_NAME)
                    lon_i = header.index(LON_IDX_NAME)
                    writer.writerow(header_out)

                src = os.path.basename(csv_file)
                for line in f:
                    row = fix_row(line.rstrip("\n").split(","), n)
                    if row is None:
                        continue
                    try:
                        lat = float(row[lat_i]); lon = float(row[lon_i])
                    except (ValueError, IndexError):
                        continue
                    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
                        continue
                    writer.writerow(row + [src])
                    kept += 1
                    if gj is not None:
                        props = {k: v for k, v in zip(header, row)}
                        props["source_file"] = src
                        feat = {"type": "Feature", "properties": props,
                                "geometry": {"type": "Point", "coordinates": [lon, lat]}}
                        gj.write(("" if first_feature else ",\n") + json.dumps(feat))
                        first_feature = False
            total += kept
            print(f"  [{fi}/{len(csv_files)}] {os.path.basename(csv_file)}: {kept:,} points kept")

        if gj is not None:
            gj.write("\n]}\n")
            gj.close()
            print(f"Saved {gj_path}")

    print(f"Saved {out_csv} ({total:,} points)")
    return out_csv


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Merge GTS track CSVs into one file")
    ap.add_argument("input_folder", help="Folder containing Tracks_*.csv files")
    ap.add_argument("-o", "--output", default=None, help="Output folder (default: input folder)")
    ap.add_argument("--geojson", action="store_true", help="Also write tracks.geojson for QGIS")
    ap.add_argument("--pattern", default="Tracks_*.csv")
    args = ap.parse_args()
    merge_tracks(args.input_folder, args.output, file_pattern=args.pattern,
                 write_geojson=args.geojson)
