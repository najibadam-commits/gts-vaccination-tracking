"""Stage 1 — flexible, memory-safe GTS track merge.

Input tracker exports are not assumed to have one fixed schema. Column names
are resolved through ``track_columns`` and each file is mapped into a shared
output schema. Original attributes are preserved; canonical aliases needed by
the downstream pipeline are added when the source uses different names.
"""
from __future__ import annotations

import argparse
import csv
import glob
import os
import sys

from track_columns import read_columns, resolve

CANONICAL = {"team": "Team Code", "ts": "GPS Timestamp (UTC)", "lat": "Lat", "lon": "Lon"}


def _read_header(path: str) -> list[str]:
    return read_columns(path)


def _repair_row(row: list[str], expected_cols: int, team_idx: int | None) -> list[str] | None:
    if len(row) == expected_cols:
        return row
    if len(row) > expected_cols and team_idx is not None:
        overflow = len(row) - expected_cols
        end = team_idx + overflow + 1
        if end <= len(row):
            return row[:team_idx] + [",".join(row[team_idx:end])] + row[end:]
    return None


def _parse_coordinate(value: str, lower: float, upper: float) -> float | None:
    try:
        x = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return x if lower <= x <= upper else None


def merge_tracks(input_folder: str, output_folder: str | None = None,
                 file_pattern: str = "Tracks_*.csv", write_geojson: bool = False) -> str:
    """Stream compatible CSV track exports into one merged CSV.

    Different files may use different Team/timestamp/latitude/longitude
    headers. The union of source attributes is retained and canonical aliases
    ``Team Code``, ``GPS Timestamp (UTC)``, ``Lat`` and ``Lon`` are appended
    when needed.
    """
    output_folder = output_folder or input_folder
    os.makedirs(output_folder, exist_ok=True)
    csv_files = sorted(glob.glob(os.path.join(input_folder, file_pattern)))
    if not csv_files:
        sys.exit(f"No files matching {file_pattern} in {input_folder}")

    file_meta = []
    union_headers: list[str] = []
    seen = set()
    for csv_file in csv_files:
        header = _read_header(csv_file)
        if not header:
            continue
        cols = resolve(header)
        if not cols.get("team") or not cols.get("ts"):
            print(f"  WARNING: {os.path.basename(csv_file)} has no recognised Team Code/Team ID or timestamp column; file skipped")
            continue
        for h in header:
            if h not in seen:
                seen.add(h)
                union_headers.append(h)
        team_idx = header.index(cols["team"]) if cols.get("team") in header else None
        file_meta.append((csv_file, header, cols, team_idx))

    if not file_meta:
        raise ValueError("No input track file contains a usable team and timestamp field")

    output_headers = list(union_headers)
    for canonical in CANONICAL.values():
        if canonical not in output_headers:
            output_headers.append(canonical)
    if "source_file" not in output_headers:
        output_headers.append("source_file")

    out_csv = os.path.join(output_folder, "merged_tracks.csv")
    gj_path = os.path.join(output_folder, "tracks.geojson")
    gj = None
    first_feature = True
    total = 0

    with open(out_csv, "w", encoding="utf-8", newline="") as fout:
        writer = csv.DictWriter(fout, fieldnames=output_headers, extrasaction="ignore")
        writer.writeheader()
        if write_geojson:
            import json
            gj = open(gj_path, "w", encoding="utf-8")
            gj.write('{"type":"FeatureCollection","name":"tracks","crs":{"type":"name","properties":{"name":"urn:ogc:def:crs:OGC:1.3:CRS84"}},"features":[\n')

        print(f"Merging {len(file_meta)} compatible track file(s)...")
        for fi, (csv_file, header, cols, team_idx) in enumerate(file_meta, 1):
            kept = skipped = 0
            header_pos = {name: i for i, name in enumerate(header)}
            with open(csv_file, "r", encoding="utf-8-sig", errors="replace", newline="") as fh:
                reader = csv.reader(fh)
                next(reader, None)
                for raw in reader:
                    row = _repair_row(raw, len(header), team_idx)
                    if row is None:
                        skipped += 1
                        continue
                    record = {name: row[i] for name, i in header_pos.items()}
                    lat_col, lon_col = cols.get("lat"), cols.get("lon")
                    lat = record.get(lat_col, "") if lat_col else ""
                    lon = record.get(lon_col, "") if lon_col else ""
                    if lat_col and lon_col:
                        lat_num = _parse_coordinate(lat, -90, 90)
                        lon_num = _parse_coordinate(lon, -180, 180)
                        if lat_num is None or lon_num is None or (lat_num == 0 and lon_num == 0):
                            skipped += 1
                            continue
                    for key, canonical in CANONICAL.items():
                        source_col = cols.get(key)
                        value = record.get(source_col, "") if source_col else ""
                        if not record.get(canonical):
                            record[canonical] = value
                    src = os.path.basename(csv_file)
                    record["source_file"] = src
                    writer.writerow(record)
                    kept += 1
                    total += 1
                    if gj is not None and lat and lon:
                        try:
                            lat_f, lon_f = float(lat), float(lon)
                            props = {k: v for k, v in record.items() if k not in {"Lat", "Lon"}}
                            feat = {"type": "Feature", "properties": props, "geometry": {"type": "Point", "coordinates": [lon_f, lat_f]}}
                            gj.write(("" if first_feature else ",\n") + json.dumps(feat))
                            first_feature = False
                        except (TypeError, ValueError):
                            pass
            print(f"  [{fi}/{len(file_meta)}] {os.path.basename(csv_file)}: {kept:,} points kept" + (f", {skipped:,} skipped" if skipped else ""))
        if gj is not None:
            gj.write("\n]}\n")
            gj.close()
            print(f"Saved {gj_path}")
    print(f"Saved {out_csv} ({total:,} points)")

    # Integrated NGA administrative enrichment. Existing non-empty labels are
    # preserved; missing State/LGA/Ward values are spatially derived. If the
    # boundary layer is unavailable, the clean merged CSV remains usable and
    # Stage 3 can fall back to team-code/LGA matching.
    try:
        from enrich_tracks_nga import enrich_track_file, has_usable_lga
        if not has_usable_lga(out_csv):
            enriched = enrich_track_file(out_csv, output_folder)
            print(f"Administrative enrichment complete: {enriched}")
            return enriched
        print("Existing LGA field detected; NGA enrichment skipped.")
    except Exception as exc:
        print(f"Administrative enrichment skipped ({exc}); using merged track CSV.")
    return out_csv


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Merge flexible GTS track CSVs")
    ap.add_argument("input_folder")
    ap.add_argument("-o", "--output", default=None)
    ap.add_argument("--geojson", action="store_true")
    ap.add_argument("--pattern", default="Tracks_*.csv")
    args = ap.parse_args()
    merge_tracks(args.input_folder, args.output, file_pattern=args.pattern, write_geojson=args.geojson)
