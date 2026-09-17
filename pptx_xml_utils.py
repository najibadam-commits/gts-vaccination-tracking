"""Self-contained PPTX slide duplication/deletion helpers.

Ships with the pipeline (no dependency on any external tool). Used to
expand or shrink the number of "LGA Coverage Map" slides in the
organization's report template to match the campaign's actual LGA count,
before handing the deck to python-pptx for content edits.

OOXML rule of thumb: a .pptx is a zip of XML parts. A slide is registered
in three places — ppt/slides/slideN.xml itself, an Override entry in
[Content_Types].xml, and a relationship + <p:sldId> entry in
ppt/presentation.xml (.rels + the slide XML). All three must stay in sync.
"""
import re
import shutil
import stat
import zipfile
from pathlib import Path


def safe_extract(zip_path, dest_dir):
    dest_dir = Path(dest_dir).resolve()
    with zipfile.ZipFile(zip_path) as zf:
        for m in zf.infolist():
            if stat.S_ISLNK(m.external_attr >> 16):
                raise ValueError(f"symlink archive entry not allowed: {m.filename!r}")
            target = (dest_dir / m.filename).resolve()
            if not str(target).startswith(str(dest_dir)):
                raise ValueError(f"unsafe archive path: {m.filename!r}")
        zf.extractall(dest_dir)
    return dest_dir


def rezip(src_dir, out_path):
    src_dir = Path(src_dir)
    out_path = Path(out_path)
    if out_path.exists():
        out_path.unlink()
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(src_dir.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(src_dir).as_posix())


def prune_unused_media(pptx_path, out_path=None) -> tuple[int, int]:
    """Drop image parts that no shape references any more. Returns (dropped, bytes).

    Why this is needed
    ------------------
    Deleting a picture SHAPE through python-pptx removes it from the slide XML
    but leaves the slide's relationship to the image, and the image itself, in
    the package. A report built by filling a template therefore ships every
    chart, map and photograph of the run the template was exported from — inert,
    unreachable, and the overwhelming majority of the file. The
    "Post Implementation Report 2026" deck is 30 MB, of which about 25 MB is
    exactly that; a generated report inherits all of it.

    What it does
    ------------
    For every part with a `.rels`, the part's own XML is scanned for the
    relationship ids it actually uses (`r:embed`, `r:link`, `r:id`). A
    relationship is removed only when BOTH its id is unused in that XML AND its
    target is a media file — so slide-to-layout, layout-to-master and
    slide-to-notes relationships, which live only in the .rels and are never
    named in the XML, are untouched. Media no surviving relationship points at
    is then dropped from the package.

    Rewrites `pptx_path` in place unless `out_path` is given.
    """
    src = Path(pptx_path)
    dest = Path(out_path) if out_path else src.with_suffix(".pruned.pptx")

    rel_re = re.compile(r'<Relationship\b[^>]*/>')
    id_re = re.compile(r'\bId="([^"]+)"')
    target_re = re.compile(r'\bTarget="([^"]+)"')
    used_re = re.compile(r'r:(?:embed|link|id)="(rId\d+)"')

    with zipfile.ZipFile(src) as zin:
        infos = zin.infolist()
        names = zin.namelist()
        data = {n: zin.read(n) for n in names}

    rewritten: dict[str, bytes] = {}
    kept_media: set[str] = set()
    for name in names:
        if not (name.endswith(".rels") and "/_rels/" in name):
            continue
        owner = name.replace("/_rels/", "/").removesuffix(".rels")
        owner_xml = data.get(owner, b"").decode("utf-8", "ignore")
        used_ids = set(used_re.findall(owner_xml))
        text = data[name].decode("utf-8")
        out_parts, changed = [], False
        pos = 0
        for m in rel_re.finditer(text):
            out_parts.append(text[pos:m.start()])
            pos = m.end()
            rel = m.group(0)
            target = target_re.search(rel)
            rid = id_re.search(rel)
            is_media = bool(target and "media/" in target.group(1))
            if is_media and rid and rid.group(1) not in used_ids:
                changed = True          # orphaned image relationship — drop it
                continue
            if is_media:
                # resolve "../media/imageN.png" against the owner's folder
                kept_media.add(target.group(1).split("/")[-1])
            out_parts.append(rel)
        out_parts.append(text[pos:])
        if changed:
            rewritten[name] = "".join(out_parts).encode("utf-8")

    dropped, freed = 0, 0
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in infos:
            name = info.filename
            if name.startswith("ppt/media/") and name.split("/")[-1] not in kept_media:
                dropped += 1
                freed += len(data[name])
                continue
            zout.writestr(info, rewritten.get(name, data[name]))

    if out_path is None:
        dest.replace(src)
    return dropped, freed


def _get_next_slide_number(slides_dir: Path) -> int:
    existing = [int(m.group(1)) for f in slides_dir.glob("slide*.xml")
                if (m := re.match(r"slide(\d+)\.xml", f.name))]
    return max(existing) + 1 if existing else 1


def _find_slide_relationship(pres_rels: str, slide_name: str):
    for m in re.finditer(r"<Relationship\b[^>]*>", pres_rels):
        element = m.group(0)
        if re.search(rf'Target="(?:/ppt/)?slides/{re.escape(slide_name)}"', element):
            idm = re.search(r'\bId="([^"]+)"', element)
            if idm:
                return idm.group(1)
    return None


def _rid_for_slide(unpacked_dir: Path, slide_name: str) -> str:
    rels_path = unpacked_dir / "ppt" / "_rels" / "presentation.xml.rels"
    rid = _find_slide_relationship(rels_path.read_text(encoding="utf-8"), slide_name)
    if not rid:
        raise ValueError(f"{slide_name} has no relationship in presentation.xml.rels")
    return rid


def duplicate_slide(unpacked_dir, source: str, after: str | None = None) -> str:
    """Duplicates ppt/slides/<source> (e.g. 'slide9.xml'), registers it, and
    inserts it into <p:sldIdLst> right after `after` (or at the end).
    Returns the new slide's filename (e.g. 'slide26.xml')."""
    unpacked_dir = Path(unpacked_dir)
    slides_dir = unpacked_dir / "ppt" / "slides"
    rels_dir = slides_dir / "_rels"
    source_path = slides_dir / source
    if not source_path.exists():
        raise FileNotFoundError(source_path)

    dest = f"slide{_get_next_slide_number(slides_dir)}.xml"
    shutil.copy2(source_path, slides_dir / dest)

    source_rels = rels_dir / f"{source}.rels"
    if source_rels.exists():
        dest_rels_text = source_rels.read_text(encoding="utf-8")
        # drop any notesSlide relationship — speaker notes should not be shared
        dest_rels_text = re.sub(
            r'<Relationship\b(?:(?!/>).)*?/relationships/notesSlide"(?:(?!/>).)*?/>',
            "", dest_rels_text)
        (rels_dir / f"{dest}.rels").write_text(dest_rels_text, encoding="utf-8")

    # 1. [Content_Types].xml
    ct_path = unpacked_dir / "[Content_Types].xml"
    ct = ct_path.read_text(encoding="utf-8")
    override = (f'<Override PartName="/ppt/slides/{dest}" '
               'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>')
    if f'PartName="/ppt/slides/{dest}"' not in ct:
        ct_path.write_text(ct.replace("</Types>", f"  {override}\n</Types>"), encoding="utf-8")

    # 2. presentation.xml.rels
    pres_rels_path = unpacked_dir / "ppt" / "_rels" / "presentation.xml.rels"
    pres_rels = pres_rels_path.read_text(encoding="utf-8")
    pres_xml_path = unpacked_dir / "ppt" / "presentation.xml"
    pres_xml = pres_xml_path.read_text(encoding="utf-8")
    used_rids = {int(n) for n in re.findall(r'\bId="rId(\d+)"', pres_rels)}
    used_rids |= {int(n) for n in re.findall(r'\br:id="rId(\d+)"', pres_xml)}
    new_rid = f"rId{max(used_rids) + 1 if used_rids else 1}"
    new_rel = (f'<Relationship Id="{new_rid}" '
              'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" '
              f'Target="slides/{dest}"/>')
    pres_rels_path.write_text(pres_rels.replace("</Relationships>", f"  {new_rel}\n</Relationships>"),
                              encoding="utf-8")

    # 3. <p:sldIdLst> in presentation.xml
    used_ids = {int(m) for m in re.findall(r'<p:sldId[^>]*\bid="(\d+)"', pres_xml)}
    new_id = max((i for i in used_ids if i >= 256), default=255) + 1
    entry = f'<p:sldId id="{new_id}" r:id="{new_rid}"/>'

    if after:
        after_rid = _rid_for_slide(unpacked_dir, after)
        open_tag = re.search(rf'<p:sldId\b[^>]*r:id="{re.escape(after_rid)}"[^>]*/>', pres_xml)
        if not open_tag:
            raise ValueError(f"{after} not found in <p:sldIdLst>")
        end = open_tag.end()
        pres_xml = pres_xml[:end] + entry + pres_xml[end:]
    else:
        pres_xml = pres_xml.replace("</p:sldIdLst>", f"{entry}</p:sldIdLst>", 1)
    pres_xml_path.write_text(pres_xml, encoding="utf-8")

    return dest


def delete_slide(unpacked_dir, slide_filename: str) -> None:
    """Removes a slide's <p:sldId> entry, its presentation.xml.rels
    relationship, and its [Content_Types].xml Override. Leaves the slide's
    own XML/media files in the package (inert, harmless) — call
    clean_unused() afterwards if you want them purged too."""
    unpacked_dir = Path(unpacked_dir)
    rid = _rid_for_slide(unpacked_dir, slide_filename)

    pres_xml_path = unpacked_dir / "ppt" / "presentation.xml"
    pres_xml = pres_xml_path.read_text(encoding="utf-8")
    pres_xml = re.sub(rf'<p:sldId\b[^>]*r:id="{re.escape(rid)}"[^>]*/>', "", pres_xml)
    pres_xml_path.write_text(pres_xml, encoding="utf-8")

    pres_rels_path = unpacked_dir / "ppt" / "_rels" / "presentation.xml.rels"
    pres_rels = pres_rels_path.read_text(encoding="utf-8")
    pres_rels = re.sub(rf'<Relationship\b[^>]*Id="{re.escape(rid)}"[^>]*/>', "", pres_rels)
    pres_rels_path.write_text(pres_rels, encoding="utf-8")

    ct_path = unpacked_dir / "[Content_Types].xml"
    ct = ct_path.read_text(encoding="utf-8")
    ct = re.sub(rf'<Override PartName="/ppt/slides/{re.escape(slide_filename)}"[^>]*/>', "", ct)
    ct_path.write_text(ct, encoding="utf-8")


def clean_unused_slides(unpacked_dir) -> None:
    """Deletes ppt/slides/slideN.xml (+ .rels) files no longer referenced by
    any relationship in presentation.xml.rels, and any ppt/notesSlides/*.xml
    left orphaned as a result (plus their [Content_Types].xml Overrides).
    Media files are left in place — unreferenced images are inert and
    harmless in an OOXML zip."""
    unpacked_dir = Path(unpacked_dir)
    slides_dir = unpacked_dir / "ppt" / "slides"
    notes_dir = unpacked_dir / "ppt" / "notesSlides"
    pres_rels = (unpacked_dir / "ppt" / "_rels" / "presentation.xml.rels").read_text(encoding="utf-8")
    referenced = set(re.findall(r'Target="(?:\.\./)?slides/(slide\d+\.xml)"', pres_rels))

    for f in slides_dir.glob("slide*.xml"):
        if f.name not in referenced:
            f.unlink()
            rels_f = slides_dir / "_rels" / f"{f.name}.rels"
            if rels_f.exists():
                rels_f.unlink()

    if not notes_dir.exists():
        return
    still_used_notes = set()
    for rels_f in (slides_dir / "_rels").glob("slide*.xml.rels"):
        still_used_notes |= set(re.findall(r'Target="\.\./notesSlides/(notesSlide\d+\.xml)"',
                                           rels_f.read_text(encoding="utf-8")))
    ct_path = unpacked_dir / "[Content_Types].xml"
    ct = ct_path.read_text(encoding="utf-8")
    changed = False
    for f in notes_dir.glob("notesSlide*.xml"):
        if f.name not in still_used_notes:
            f.unlink()
            rels_f = notes_dir / "_rels" / f"{f.name}.rels"
            if rels_f.exists():
                rels_f.unlink()
            new_ct = re.sub(rf'<Override PartName="/ppt/notesSlides/{re.escape(f.name)}"[^>]*/>', "", ct)
            if new_ct != ct:
                ct = new_ct
                changed = True
    if changed:
        ct_path.write_text(ct, encoding="utf-8")
