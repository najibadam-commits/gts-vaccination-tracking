"""One font policy for every matplotlib figure the pipeline draws.

Why this exists
---------------
The QGIS layout templates carry the font names of the machine they were
authored on:

    MS Shell Dlg 2   Qt's Windows UI alias — not a real font file anywhere
    Open Sans        a Google font, installed on the author's PC
    Arial            Windows/macOS only
    Century Gothic   Microsoft Office only
    Trebuchet MS     Windows/macOS only

Stage 4 passed those names straight to matplotlib with a fallback appended,
e.g. `family=["MS Shell Dlg 2", "DejaVu Sans"]`. Matplotlib tries each name in
order and logs

    findfont: Font family 'MS Shell Dlg 2' not found.

for every name it cannot find, on **every text object it draws** — so a single
map produced dozens of identical warnings. Appending a fallback made the
substitution deterministic but did nothing about the search that precedes it.

The fix is to never hand matplotlib a name it cannot resolve. `resolve_family`
maps a requested name to a font that is actually installed, checked once
against the font manager, so no lookup can fail at draw time.

The pipeline therefore renders identically on any machine, and needs no fonts
installed beyond the DejaVu family matplotlib ships with.
"""
from __future__ import annotations

from functools import lru_cache

import matplotlib

# Preference order for a generic sans-serif. Every entry is checked against the
# fonts actually installed; the last is matplotlib's own bundled font, which is
# present by definition, so this list can never be exhausted.
PREFERRED_SANS = (
    "Open Sans",        # what the templates ask for — used when it IS installed
    "Liberation Sans",  # metric-compatible with Arial, ships with most Linux
    "Nimbus Sans",
    "Arial",
    "Helvetica",
    "DejaVu Sans",      # matplotlib's default, always available
)

# Substitutes for specific template fonts, tried before the generic list above
# so a distinctive face is matched to something close rather than flattened.
FONT_ALIASES = {
    "century gothic": ("URW Gothic", "Questrial", "Futura"),
    "trebuchet ms": ("Liberation Sans", "Verdana", "DejaVu Sans"),
    "arial": ("Liberation Sans", "Nimbus Sans", "Helvetica"),
    "helvetica": ("Nimbus Sans", "Liberation Sans", "Arial"),
    "calibri": ("Carlito", "Liberation Sans"),
    "times new roman": ("Liberation Serif", "Nimbus Roman", "DejaVu Serif"),
    # Qt UI aliases, not real font files on any platform
    "ms shell dlg 2": (),
    "ms shell dlg": (),
    "sans-serif": (),
}

# Guaranteed present: matplotlib ships it, so this is the end of every chain.
LAST_RESORT = "DejaVu Sans"

_reported: set[str] = set()


@lru_cache(maxsize=1)
def installed_families() -> frozenset[str]:
    """Family names matplotlib can actually load on this machine."""
    from matplotlib import font_manager
    return frozenset(f.name for f in font_manager.fontManager.ttflist)


@lru_cache(maxsize=256)
def resolve_family(requested: str | None) -> str:
    """A font family that is installed here, closest to `requested`.

    Never returns a name matplotlib cannot find, which is what stops the
    `findfont` warnings at source. An empty or unknown request yields the
    preferred generic sans.
    """
    installed = installed_families()
    name = (requested or "").strip()
    chain = []
    if name:
        chain.append(name)
        chain.extend(FONT_ALIASES.get(name.lower(), ()))
    chain.extend(PREFERRED_SANS)

    for candidate in chain:
        if candidate in installed:
            if name and candidate.lower() != name.lower() and name not in _reported:
                _reported.add(name)
                print(f"  font: '{name}' is not installed — using "
                      f"'{candidate}' instead")
            return candidate

    if name and name not in _reported:
        _reported.add(name)
        print(f"  font: '{name}' is not installed — using '{LAST_RESORT}'")
    return LAST_RESORT


def default_family() -> str:
    """The family used wherever a template does not name one."""
    return resolve_family(None)


def apply_matplotlib_defaults() -> str:
    """Point matplotlib's own defaults at an installed font.

    Covers every text object that does NOT carry an explicit family — axis
    labels, tick labels, figure titles, legend text — so the policy holds for
    text the template never described. Returns the family chosen.

    `font.sans-serif` is set to exactly the resolved family plus the bundled
    last resort: matplotlib's stock list names a dozen fonts that are mostly
    absent on Linux, and it walks that list on every miss.
    """
    family = default_family()
    matplotlib.rcParams["font.family"] = "sans-serif"
    matplotlib.rcParams["font.sans-serif"] = (
        [family] if family == LAST_RESORT else [family, LAST_RESORT])
    # A glyph the chosen face lacks would otherwise warn in the same way.
    matplotlib.rcParams["axes.unicode_minus"] = False
    return family


if __name__ == "__main__":
    chosen = apply_matplotlib_defaults()
    print(f"default family: {chosen}")
    print(f"{len(installed_families())} families installed")
    for name in ("MS Shell Dlg 2", "Open Sans", "Arial", "Century Gothic",
                 "Trebuchet MS", ""):
        print(f"  {name or '(unspecified)':16} -> {resolve_family(name)}")
