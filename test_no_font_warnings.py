"""Assert the pipeline never asks matplotlib for a font that is not installed.

`findfont: Font family 'X' not found.` is logged by matplotlib's font manager,
not raised as a warning, so it is easy to ignore and easy to reintroduce. This
captures that logger while exercising every text path the map and chart stages
use — including the exact font names the QGIS templates carry — and fails if
anything is logged.

    python test_no_font_warnings.py
"""
import io
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from pipeline_fonts import (  # noqa: E402
    apply_matplotlib_defaults, installed_families, resolve_family,
)

# Every font family named anywhere in the QGIS layout templates shipped with
# the pipeline, plus the empty case for text the template does not describe.
TEMPLATE_FONTS = ["MS Shell Dlg 2", "Open Sans", "Arial", "Century Gothic",
                  "Trebuchet MS", ""]


class FindFontCatcher(logging.Handler):
    """Captures only the failure, not matplotlib's routine scoring trace.

    `findfont:` prefixes two very different things. At DEBUG, matplotlib logs
    the score of every candidate font it considers on a successful match —
    thousands of lines, entirely normal, never displayed. The failure we care
    about is logged at WARNING:

        findfont: Font family 'MS Shell Dlg 2' not found.

    so the filter is the level, not the word.
    """

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.messages = []

    def emit(self, record):
        text = record.getMessage()
        if "not found" in text.lower() or "findfont" in text.lower():
            self.messages.append(text)


def draw_everything():
    """Exercise the text paths the pipeline actually uses."""
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot([0, 1], [0, 1], label="series")
    # inherits rcParams — titles, axis labels, ticks, legend
    ax.set_title("Map Title")
    ax.set_xlabel("x label")
    ax.set_ylabel("y label")
    ax.legend()
    ax.annotate("annotation", (0.5, 0.5))
    # explicit families, as stage 4 passes them for scale bar / legend / labels
    for i, name in enumerate(TEMPLATE_FONTS):
        family = resolve_family(name)
        fig.text(0.02, 0.02 + i * 0.05, f"{name or 'default'} -> {family}",
                 family=family, fontsize=8)
    fig.legend(prop={"family": resolve_family("MS Shell Dlg 2"), "size": 8})
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=80)
    plt.close(fig)


def main() -> int:
    catcher = FindFontCatcher()
    for logger_name in ("matplotlib", "matplotlib.font_manager"):
        lg = logging.getLogger(logger_name)
        lg.setLevel(logging.WARNING)   # not DEBUG — see FindFontCatcher
        lg.addHandler(catcher)

    family = apply_matplotlib_defaults()
    print(f"resolved default family : {family}")
    print(f"font.sans-serif          : {matplotlib.rcParams['font.sans-serif']}")
    print(f"installed families       : {len(installed_families())}")
    print()

    print("template font resolution:")
    for name in TEMPLATE_FONTS:
        resolved = resolve_family(name)
        ok = resolved in installed_families()
        print(f"  [{'PASS' if ok else 'FAIL'}] {name or '(unspecified)':16} "
              f"-> {resolved}")
        if not ok:
            catcher.messages.append(f"resolve_family({name!r}) returned an "
                                    f"uninstalled family {resolved!r}")
    print()

    # importing the stages must not warn either — they set rcParams on import
    import stage_charts  # noqa: F401
    print("stage_charts imported")
    try:
        import stage4_maps  # noqa: F401
        print("stage4_maps imported")
    except ImportError as exc:
        print(f"stage4_maps not importable here ({exc}) — skipping")

    draw_everything()
    print("drew a figure using every text path")
    print()

    if catcher.messages:
        print(f"FAILED — {len(catcher.messages)} font lookup(s) missed:")
        for m in dict.fromkeys(catcher.messages):
            print(f"    {m}")
        return 1
    print("ALL PASSED — no findfont warnings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
