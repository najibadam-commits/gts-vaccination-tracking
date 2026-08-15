"""Assert one broken polygon cannot abort a whole campaign day.

Reproduces the second Streamlit Cloud failure: a settlement-extent layer
containing a degenerate ring made pyogrio raise

    GEOSException: Invalid number of points in LinearRing found 2

naming neither the file nor the feature, and the run stopped. pyogrio 0.13
defaults to on_invalid="raise"; earlier versions warned, which is why the same
layer can work on one machine and fail on another.

    python test_bad_geometry.py
"""
import os
import sys
import tempfile

import geopandas as gpd
import pandas as pd
import shapely
from shapely.geometry import box

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stage2_analysis import read_spatial  # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    ok = ok and good
    print(f"  [{'PASS' if good else 'FAIL'}] {label}: got {got}, want {want}")


def degenerate_ring_wkb() -> bytes:
    """A polygon whose outer ring has only TWO points — what the field file had.

    Built as raw WKB because shapely will not construct one: little-endian,
    type 3 (polygon), 1 ring, 2 points.
    """
    import struct
    return (struct.pack("<BI", 1, 3)          # byte order, type=polygon
            + struct.pack("<I", 1)            # one ring
            + struct.pack("<I", 2)            # two points in that ring
            + struct.pack("<dd", 8.0, 9.0)
            + struct.pack("<dd", 8.1, 9.1))


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="badgeom_")
    path = os.path.join(tmp, "voronoi.geojson")

    # Three good settlement extents written normally...
    good = gpd.GeoDataFrame(
        {"Settlement": ["Alpha", "Beta", "Gamma"],
         "LGA": ["Central"] * 3,
         "geometry": [box(8 + i, 9, 8.1 + i, 9.1) for i in range(3)]},
        crs="EPSG:4326")
    good.to_file(path, driver="GeoJSON")

    # ...then a degenerate one appended at the WKB level, which is how it
    # arrives from QGIS.
    gpkg = os.path.join(tmp, "voronoi_with_bad.gpkg")
    frame = pd.concat([
        pd.DataFrame({"Settlement": ["Alpha", "Beta", "Gamma"],
                      "LGA": ["Central"] * 3,
                      "geometry": [box(8 + i, 9, 8.1 + i, 9.1) for i in range(3)]}),
        pd.DataFrame({"Settlement": ["Broken"], "LGA": ["Central"],
                      "geometry": list(shapely.from_wkb(
                          [degenerate_ring_wkb()], on_invalid="ignore"))}),
    ], ignore_index=True)
    gdf = gpd.GeoDataFrame(frame, geometry="geometry", crs="EPSG:4326")

    print("the layer as it arrives:")
    print(f"  {len(gdf)} features, {int(gdf.geometry.isna().sum())} unreadable\n")

    try:
        gdf.to_file(gpkg, driver="GPKG")
        wrote = True
    except Exception as exc:
        print(f"  (could not write GPKG here: {exc}) — testing the reader directly\n")
        wrote = False

    print("read_spatial drops the bad feature and keeps the run alive:")
    if wrote:
        out = read_spatial(gpkg)
        check("good features survive", len(out), 3)
        check("no null geometry remains", int(out.geometry.isna().sum()), 0)
        check("the broken one is gone", "Broken" in set(out["Settlement"]), False)
    else:
        from stage2_analysis import _drop_bad_geometry
        out = _drop_bad_geometry(gdf, "voronoi.gpkg")
        check("good features survive", len(out), 3)
        check("no null geometry remains", int(out.geometry.isna().sum()), 0)
        check("the broken one is gone", "Broken" in set(out["Settlement"]), False)

    print("\na clean layer is untouched:")
    clean = read_spatial(path)
    check("all features kept", len(clean), 3)

    print("\nan entirely broken layer fails with a message that says what to do:")
    from stage2_analysis import _drop_bad_geometry
    all_bad = gpd.GeoDataFrame(
        {"Settlement": ["X", "Y"], "geometry": [None, None]}, crs="EPSG:4326")
    try:
        _drop_bad_geometry(all_bad, "voronoi.gpkg")
        check("raises", False, True)
    except ValueError as exc:
        msg = str(exc)
        check("names the file", "voronoi.gpkg" in msg, True)
        check("suggests the QGIS remedy", "Check Validity" in msg, True)

    print("\n" + ("ALL PASSED" if ok else "FAILURES ABOVE"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
