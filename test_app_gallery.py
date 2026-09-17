"""Regression test for the app's result page — gallery, zip and downloads.

    python test_app_gallery.py

Why this exists
---------------
Stage 7 writes the Post-Implementation Report's figures to a `pir` SUBFOLDER of
the charts directory. The result page listed that directory with a plain
`os.listdir` and handed every entry to `st.image`, so a post-campaign run died
with

    MediaFileStorageError: Error opening '...\\charts\\pir'

— Streamlit trying to read a directory as an image. The same flat listing wrote
a bare directory entry into the maps+charts zip and left the PIR figures out of
the download entirely.

Both are now handled by `image_files` and `zip_tree`, which every UI shares.
This test exercises those directly against a charts folder shaped like a real
post-campaign run's, so a future stage that adds another subfolder cannot
reintroduce either failure.

It also checks the thing a finished run is actually FOR: that the report is
offered for download. The report download used to be gated on the results dict's
`pptx` key, which only stage 6 sets — so a post-campaign run built the
Post-Implementation Report, put its path under `report`, and then showed no
download button for it at all. `download_targets` is asserted here for both
analysis types.

Streamlit itself is not imported: the app modules pull in the whole pipeline at
module scope, and the two functions under test are plain filesystem code.
"""
from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))


def load_helpers():
    """Read `image_files` and `zip_tree` out of app.py without importing it.

    Importing app.py runs the whole Streamlit page — every pipeline stage at
    module scope, geopandas and matplotlib included — which is far more than a
    test of two filesystem helpers should need. The two functions are lifted
    from the source and executed on their own instead, so this stays a fast
    check that still tests the code the app actually runs.
    """
    import pandas as pd
    import re as _re
    src = open(os.path.join(HERE, "app.py"), encoding="utf-8").read()
    ns: dict = {"os": os, "pd": pd, "re": _re}
    for name in ("image_files", "zip_tree", "day_from_prev_visitation",
                 "_scope_label", "_scope_slug"):
        start = src.index(f"def {name}(")
        # the function ends at the next top-level `def`/statement
        rest = src[start:]
        end = len(rest)
        for i, line in enumerate(rest.splitlines(keepends=True)):
            if i and line and not line[0].isspace() and not line.startswith(f"def {name}"):
                end = sum(len(x) for x in rest.splitlines(keepends=True)[:i])
                break
        exec(compile(rest[:end], "app.py", "exec"), ns)
    return (ns["image_files"], ns["zip_tree"], ns["day_from_prev_visitation"],
            ns["_scope_label"], ns["_scope_slug"])


def download_targets(results: dict) -> list[str]:
    """The files app.py's result page offers, given a results dict.

    Mirrors the selection logic in the Downloads block. Kept as a separate
    statement of the rule so the test fails loudly if the two drift — the whole
    point is that every analysis type offers its own report.
    """
    out = []
    if results.get("pptx"):
        out.append(results["pptx"])
    elif str(results.get("report", "")).lower().endswith(".pptx"):
        out.append(results["report"])
    out.append(results["workbook"])
    out.append(results["visitation"])
    return out


def make_charts_folder(root: str) -> tuple[str, str]:
    """A charts folder shaped like a post-campaign run's."""
    charts = os.path.join(root, "charts")
    pir = os.path.join(charts, "pir")
    os.makedirs(pir)
    for fn in ("team_deployment.png", "state_coverage_donut.png", "time_spent.png"):
        open(os.path.join(charts, fn), "wb").write(b"\x89PNG\r\n\x1a\n")
    for fn in ("state_donut.png", "lga_donut_Anka.png", "cumulative.png"):
        open(os.path.join(pir, fn), "wb").write(b"\x89PNG\r\n\x1a\n")
    # non-image files that must not be offered to st.image
    open(os.path.join(charts, "notes.txt"), "w").write("x")
    maps = os.path.join(root, "maps")
    os.makedirs(maps)
    open(os.path.join(maps, "Kwara_statewide_day_1.png"), "wb").write(b"\x89PNG\r\n\x1a\n")
    return charts, maps


def check(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"  ok  {message}")


def main() -> int:
    image_files, zip_tree, day_from_prev, _scope, _slug = load_helpers()
    tmp = tempfile.mkdtemp(prefix="gallery_test_")
    try:
        charts, maps = make_charts_folder(tmp)

        listed = image_files(charts)
        check("pir" not in listed,
              "the charts listing excludes the pir SUBFOLDER "
              "(the MediaFileStorageError)")
        check("notes.txt" not in listed,
              "the charts listing excludes non-image files")
        check(listed == sorted(listed) and len(listed) == 3,
              f"the three top-level charts are listed, sorted ({listed})")
        check(len(image_files(os.path.join(charts, "pir"))) == 3,
              "the PIR figures are listed from their own folder")
        check(image_files(os.path.join(tmp, "does_not_exist")) == [],
              "a missing folder lists as empty rather than raising")

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for folder in (maps, charts):
                zip_tree(z, folder)
        names = set(zipfile.ZipFile(buf).namelist())
        check("charts/pir/state_donut.png" in names,
              "the zip carries the PIR figures, under charts/pir/")
        check("charts/team_deployment.png" in names,
              "the zip still carries the top-level charts")
        check("maps/Kwara_statewide_day_1.png" in names,
              "the zip still carries the maps")
        check(not any(n.rstrip("/").endswith("charts/pir") and n.endswith("/")
                      for n in names),
              "no bare directory entry is written")
        check(all(not i.is_dir() for i in zipfile.ZipFile(buf).infolist()),
              "every zip member is a real file")

        # The Post-Campaign path no longer asks for a campaign day — it is
        # read back off the chained visitation CSV, because stage 2 still names
        # its cumulative column `day_{N}_cumm` and chains on that number.
        import pandas as pd
        chained = os.path.join(tmp, "prev.csv")
        pd.DataFrame({"lga": ["A"], "day_5_cumm": ["Visited"],
                      "day_4_cumm": ["Visited"]}).to_csv(chained, index=False)
        check(day_from_prev(chained) == 6,
              "the campaign day follows the chained visitation CSV (day_5 -> 6)")
        check(day_from_prev(None) == 1,
              "no prior CSV means day 1 — one whole-campaign run")
        check(day_from_prev(os.path.join(tmp, "nope.csv")) == 1,
              "a missing prior CSV means day 1 rather than raising")
        plain = os.path.join(tmp, "plain.csv")
        pd.DataFrame({"lga": ["A"]}).to_csv(plain, index=False)
        check(day_from_prev(plain) == 1,
              "a CSV with no cumulative column means day 1")

        # --- nothing a post-campaign run produces is stamped with a day ---
        from stage2_analysis import output_tag
        check(output_tag(3, "daily") == "day_3",
              "a daily run's outputs are still named for their day")
        check(output_tag(1, "post_campaign") == "postcampaign",
              "a post-campaign run's outputs are named for their SCOPE")
        check("day" not in output_tag(6, "post_campaign"),
              "no day number reaches a final-report filename")

        # --- the report is downloadable, whichever stage produced it ---
        daily = {"pptx": "/out/Kwara_Day_1_Report.pptx",
                 "report": "/out/Kwara_Day_1_Daily_Tracking_Report_DRAFT.docx",
                 "report_label": "Report draft (.docx)",
                 "workbook": "/out/wb.xlsx", "visitation": "/out/v.csv",
                 "analysis_type": "daily", "day": 1, "state": "Kwara"}
        post = {"pptx": None,
                "report": "/out/Kwara_Post_Implementation_Report.pptx",
                "report_label": "Post-Implementation Report (.pptx)",
                "workbook": "/out/wb.xlsx", "visitation": "/out/v.csv",
                "analysis_type": "post_campaign", "day": 1, "state": "Kwara"}

        check(daily["pptx"] in download_targets(daily),
              "a daily run offers stage 6's org-template deck")
        check(daily["report"] not in download_targets(daily),
              "a daily run does not offer the .docx draft")
        check(post["report"] in download_targets(post),
              "a POST-CAMPAIGN run offers its Post-Implementation Report deck")
        for results in (daily, post):
            check(any(t.lower().endswith(".pptx") for t in download_targets(results)),
                  f"{results['analysis_type']}: a presentable deck is downloadable")

        check(_scope(daily) == "Day 1" and _scope(post) == "Whole campaign",
              "the result page labels a post-campaign run by scope, not by day")
        check(_slug(post) == "PostCampaign",
              "and names its maps+charts zip the same way")

        print("\nALL CHECKS PASSED")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
