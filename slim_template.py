"""Shrink a reporting template by replacing its embedded slide images.

The problem
-----------
`eHA_Revised_reporting_template.pptx` is 59 MB, and 54 MB of that is 27 PNGs —
the maps and charts from the run the template was exported from. Stage 6
REPLACES every one of them on every build, so those bytes are carried purely to
make the template look finished when opened. They cost:

  * the repo — 59 MB is over GitHub's 50 MB warning threshold, and the deployed
    cloud app needs the template inside the repo to find it at all
  * every build — safe_extract + rezip of a 59 MB package is the slowest step
    in stage 6, and the slide-map tests build the deck five times

Branding is NOT touched
-----------------------
Media referenced from a slide MASTER, LAYOUT or theme is left exactly as it is:
that is the eHA logo, the footer bar and the title-slide artwork, which stage 6
never replaces and which the deck would visibly lose. Only media referenced
*solely* from slide XML is shrunk — the maps and charts. On the current
template that is 27 parts and 54.2 MB, against 4.5 MB of branding kept intact.

Each shrunk image is replaced by a 2x2 pixel file of the same format, so every
relationship, content type and shape geometry stays valid. Opening the slimmed
template shows the same layout with blank picture boxes, which is what a
template arguably ought to look like anyway.

    python slim_template.py --template ../eHA_Revised_reporting_template.pptx \
                            --output   ../eHA_Revised_reporting_template_slim.pptx

Verify afterwards with `python test_revised_template_slide_map.py`, which
asserts the built deck's slide order and content — a slimmed template that
still passes it is interchangeable with the original as far as the report is
concerned.
"""
from __future__ import annotations

import argparse
import collections
import io
import os
import re
import shutil
import zipfile


def _placeholder(ext: str) -> bytes:
    """A 2x2 image of the requested format, as bytes."""
    from PIL import Image
    buf = io.BytesIO()
    fmt = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG"}.get(ext.lower())
    if fmt is None:
        return b""
    Image.new("RGB", (2, 2), "white").save(buf, format=fmt)
    return buf.getvalue()


def _media_usage(zf: zipfile.ZipFile) -> dict[str, set[str]]:
    """media filename -> the kinds of part that reference it.

    Read from the .rels files rather than the slide XML, because that is where
    the target path actually lives; the slide only carries a relationship id.
    """
    usage: dict[str, set[str]] = collections.defaultdict(set)
    for name in zf.namelist():
        if "_rels" not in name or not name.endswith(".rels"):
            continue
        text = zf.read(name).decode("utf-8", "ignore")
        for target in re.findall(r'Target="([^"]*media/[^"]+)"', text):
            kind = ("slide" if "/slides/_rels" in name else
                    "layout" if "/slideLayouts/" in name else
                    "master" if "/slideMasters/" in name else
                    "notes" if "/notesSlides/" in name else "other")
            usage[target.split("/")[-1]].add(kind)
    return usage


def slim(template_path: str, output_path: str,
         keep_branding: bool = True) -> str:
    with zipfile.ZipFile(template_path) as zin:
        usage = _media_usage(zin)
        infos = zin.infolist()

        shrink: set[str] = set()
        for info in infos:
            if not info.filename.startswith("ppt/media/"):
                continue
            base = info.filename.split("/")[-1]
            kinds = usage.get(base, set())
            # Unreferenced media is shrunk too — it is dead weight by
            # definition. Media used anywhere outside a slide is branding.
            if keep_branding and (kinds - {"slide", "notes"}):
                continue
            shrink.add(info.filename)

        before = sum(i.file_size for i in infos)
        saved = 0
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zout:
            for info in infos:
                data = zin.read(info.filename)
                if info.filename in shrink:
                    ext = info.filename.rsplit(".", 1)[-1]
                    small = _placeholder(ext)
                    if small:
                        saved += len(data) - len(small)
                        data = small
                zout.writestr(info, data)

    after = os.path.getsize(output_path)
    print(f"{os.path.basename(template_path)}: {len(shrink)} slide image(s) "
          f"replaced, {saved / 1e6:.1f} MB of image data removed")
    print(f"  {before / 1e6:.1f} MB uncompressed -> {after / 1e6:.1f} MB on disk")
    if keep_branding:
        kept = sum(1 for b, k in usage.items() if k - {"slide", "notes"})
        print(f"  {kept} branding image(s) kept (masters, layouts, theme)")
    return output_path


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    base = os.path.dirname(here)
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--template",
                    default=os.path.join(base, "eHA_Revised_reporting_template.pptx"))
    ap.add_argument("--output", default=None,
                    help="default: <template>_slim.pptx")
    ap.add_argument("--all", action="store_true",
                    help="also shrink branding images (masters/layouts/theme). "
                         "Use for test fixtures only — the real deck loses its "
                         "logo and footer artwork.")
    a = ap.parse_args()
    out = a.output or a.template.replace(".pptx", "_slim.pptx")
    if not os.path.exists(a.template):
        raise SystemExit(f"Template not found: {a.template}")
    slim(a.template, out, keep_branding=not a.all)
