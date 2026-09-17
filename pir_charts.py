"""Post-Implementation Report — the charts, drawn to the sample's design.

Every figure on the Post-Implementation Report is one of these chart types.
They are here, and only here, so the deck's look is set in one place:

    coverage_donut         State + per-LGA coverage
    lga_percent_stacked    LGA coverage mix, 100% stacked
    cumulative_bar         Cumulative settlements coverage
    not_visited_stacked    Settlements per LGA, visited vs not
    missed_children_stacked Target children per LGA, reached vs missed
    planned_visited_bar    Planned vs visited vs not visited
    ward_count_stacked     Ward coverage within one LGA

The sample's Team Deployed/Devices bar is deliberately absent: team deployment
is an implementation-monitoring measure that belongs to the daily ERM analysis,
not to the final report. `stage_charts.team_deployment_chart` still draws it for
the daily deck.

Design rules taken from the sample, applied to all of them:

  * white canvas, no chart border, no right/top spines
  * horizontal grid only, light grey, behind the bars
  * the VALUE is printed on every bar — inside in white when the bar is tall
    enough to hold it, above in the bar's own colour when it is not, which is
    how the sample prints its small counts (e.g. the "1" over a one-settlement
    ward)
  * stacked bars stack BEST-covered at the bottom, so the shortfall sits on top
  * the legend for a stacked bar sits ABOVE the plot; for a donut, below it
  * a donut's hole carries the sample's "N / Visited / X% of Y Planned" summary

Every function takes the ASPECT RATIO of the slide box it will be placed in and
sizes its figure to match — see `_figsize` for why that matters more than it
sounds like it should.

Colours come from `stage_charts`, so a PIR chart and a daily-deck chart of the
same thing are the same colour. Fonts come from `pipeline_fonts`, so no figure
triggers a `findfont` lookup for a face that is not installed.
"""
from __future__ import annotations

import os
import sys
import textwrap

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline_fonts import apply_matplotlib_defaults
from stage_charts import (GREEN_DARK, GREEN_MID, GREEN_LIGHT, GREEN_PALE,
                          SALMON, BLUE, TITLE_C)
import pir_analysis as pa
from pir_analysis import COVERAGE_ORDER

apply_matplotlib_defaults()

# The sample's "No Coverage"/"Not Visited" COLUMN colour. Distinct from SALMON,
# which the sample keeps for the No Coverage SLICE/SEGMENT in donuts and stacked
# bars — the same distinction the sample makes, kept rather than tidied away so
# a reader moving between the two decks sees what they expect.
ORANGE = "#e8710a"
VISITED_GREEN = "#38761d"

COVERAGE_COLORS = {"Fully Covered": GREEN_DARK, "Partially Covered": GREEN_MID,
                   "Low Coverage": GREEN_LIGHT, "Very Low Coverage": GREEN_PALE,
                   "No Coverage": SALMON}
# Bottom-to-top stacking order: best covered at the bottom, the gap on top.
STACK_ORDER = list(reversed(COVERAGE_ORDER))
# Segment fills dark enough to carry white text.
DARK_FILLS = {GREEN_DARK, GREEN_MID, SALMON, BLUE, ORANGE, VISITED_GREEN}

GRID_C = "#d9d9d9"
DPI = 150


def _canvas(ax, title: str | None = None, title_pt: float = 15.0,
            grid: bool = True, title_pad: float = 14):
    if title:
        ax.set_title(title, fontsize=title_pt, weight="bold", color=TITLE_C,
                     pad=title_pad)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.spines["bottom"].set_color("#000000")
    if grid:
        ax.grid(axis="y", color=GRID_C, linewidth=0.8, zorder=0)
    else:
        ax.grid(False)
    ax.set_axisbelow(True)
    ax.tick_params(axis="both", length=0, labelsize=11, colors="#000000")


def _save(fig, out_png: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(out_png)), exist_ok=True)
    fig.savefig(out_png, dpi=DPI, facecolor="white")
    plt.close(fig)
    return out_png


def _figsize(aspect: float, n_items: int = 1, per_item: float = 0.0,
             min_w: float = 6.0, max_w: float = 13.0,
             min_h: float = 3.6, max_h: float = 7.0) -> tuple[float, float]:
    """A figure sized to the SHAPE OF THE BOX it will be placed in.

    Every chart here is contain-fitted into a fixed box on a slide. A figure
    whose aspect ratio differs from that box's is letterboxed, and a wide
    chart — a per-ward bar for an LGA with sixteen wards — letterboxed into a
    half-slide box comes out as a 6-inch by 1-inch sliver with unreadable type.
    Matching the figure's aspect to the box removes the letterboxing entirely,
    so the chart fills the space it was given.

    Width still grows with the number of categories (`per_item`), within
    bounds; the height then follows from the aspect. `aspect` is width/height of
    the destination box.
    """
    width = min(max(min_w, per_item * max(n_items, 1)), max_w)
    height = width / aspect
    if height < min_h:
        height = min_h
        width = min(max_w, height * aspect)
    elif height > max_h:
        height = max_h
        width = max(min_w, height * aspect)
    return round(width, 2), round(height, 2)


def _bar_value(ax, bar, value, fill, top, fmt="{:,.0f}", pt=13.0):
    """Print a bar's value inside it when it fits, above it when it does not.

    "Fits" is 12% of the axis height, which is about two line heights at this
    type size — below that the label starts colliding with the bar's own edge.
    """
    h = bar.get_height()
    x = bar.get_x() + bar.get_width() / 2
    text = fmt.format(value)
    if h >= top * 0.12:
        ax.text(x, h - top * 0.035, text, ha="center", va="top", fontsize=pt,
                weight="bold", color="white" if fill in DARK_FILLS else "#333333",
                zorder=4)
    else:
        ax.text(x, h + top * 0.015, text, ha="center", va="bottom", fontsize=pt,
                weight="bold", color=fill, zorder=4)


# ------------------------------------------------- slides 4 and the LGA slides
def coverage_donut(counts: dict, out_png: str, title: str | None = None,
                   min_label_pct: float = 0.04,
                   centre: tuple[str, str, str] | None = None,
                   aspect: float = 1.15) -> str:
    """Coverage-class donut with count and share on each slice.

    Classes with no settlements are dropped (the sample's per-LGA donuts show
    three or four classes, not always five) and slices thinner than
    `min_label_pct` are left unlabelled rather than overprinted — the sample
    labels only the slices with room, and a 0.4% wedge cannot hold "2 (0.4%)".

    `centre` is the sample's "492 / Visited / 97% of 509 Planned" summary, as
    (big, middle, small). The sample puts it in a separate PowerPoint text box
    dropped over the hole; it is drawn INTO the figure here instead, because a
    text box positioned by slide coordinates only lands in the hole while the
    donut keeps exactly the aspect ratio and legend height it had when those
    coordinates were chosen — and the per-LGA donuts do not (their legends run
    to three, four or five entries). Drawn in the figure, it is in the hole by
    construction.
    """
    series = (pd.Series(counts, dtype="float64")
              .reindex(COVERAGE_ORDER).fillna(0).astype(int))
    series = series[series > 0]
    total = int(series.sum())
    if total == 0:
        series = pd.Series({"No Coverage": 1})
        total = 1

    fig_w, fig_h = _figsize(aspect, 1, 0, min_w=5.4, max_w=6.2,
                            min_h=4.4, max_h=5.8)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    # Explicit axes rectangle, not tight_layout. An equal-aspect pie plus a
    # legend anchored below the axes makes tight_layout shrink the axes to fit
    # the legend, and a five-entry legend is wider than the figure — which is
    # how the first build came out with a small donut and a legend running off
    # both edges. Reserving the band here and using a FIGURE legend keeps the
    # donut at full size and the key inside the canvas.
    legend_band = 0.11 if len(series) <= 3 else 0.18
    title_band = 0.08 if title else 0.02
    ax.set_position([0.02, legend_band, 0.96, 1 - legend_band - title_band])

    wedges, _ = ax.pie(
        series.values, colors=[COVERAGE_COLORS[c] for c in series.index],
        startangle=90, counterclock=False, radius=1.0,
        wedgeprops=dict(width=0.42, edgecolor="white", linewidth=1.5))
    for w, (name, value) in zip(wedges, series.items()):
        share = value / total
        if share < min_label_pct:
            continue
        ang = np.deg2rad((w.theta1 + w.theta2) / 2)
        r = 0.79
        ax.text(r * np.cos(ang), r * np.sin(ang), f"{value:,} ({share:.0%})",
                ha="center", va="center", fontsize=8.5, weight="bold",
                color="white" if COVERAGE_COLORS[name] in DARK_FILLS else "#333333")
    if centre:
        big, middle, small = centre
        # The hole is 0.58 of the pie radius; the widest of the three lines
        # decides the type size so a long "28.6% of 8,964 Planned" shrinks to
        # fit rather than spilling onto the ring.
        widest = max(len(big), len(middle), len(small))
        small_pt = min(9.5, max(6.5, 190.0 / max(widest, 1)))
        ax.text(0, 0.16, big, ha="center", va="center",
                fontsize=small_pt * 1.85, weight="bold", color="#111111")
        ax.text(0, 0.00, middle, ha="center", va="center", fontsize=small_pt,
                weight="bold", color="#111111")
        ax.text(0, -0.15, small, ha="center", va="center", fontsize=small_pt,
                weight="bold", color="#111111")
    if title:
        ax.set_title(title, fontsize=13, weight="bold", color="#666666", pad=8)
    # At most three keys per row: five across is wider than the figure at any
    # legible type size.
    fig.legend(wedges, list(series.index), loc="lower center",
               ncol=min(3, len(series)), fontsize=9, frameon=False,
               handlelength=0.9, handleheight=0.9, borderpad=0.2,
               columnspacing=1.1, labelspacing=0.3,
               bbox_to_anchor=(0.5, 0.005))
    ax.set_aspect("equal")
    return _save(fig, out_png)


def _stacked(tab: pd.DataFrame, out_png: str, title: str | None,
             percent: bool, figsize, label_wrap: int,
             order: list[str] | None = None,
             colors: dict[str, str] | None = None) -> str:
    """THE stacked bar. Every LGA- and ward-level chart in the report is one.

    One function, because the point is that they are all the same chart: same
    stacking direction (best at the bottom, the gap on top), same in-segment
    counts, same rule for a segment too thin to hold its number, same legend
    above the plot in the same order. Only the categories, the palette and the
    y scale differ, and those are arguments.

    `order` runs BEST FIRST — Fully Covered, then down to No Coverage — and the
    bars are stacked in that order from the bottom up, so the shortfall always
    ends on top. That is how the sample's charts read, and it is what makes a
    row of bars comparable: every bar grows out of the good news, and the pink
    cap is the gap.

    (This used to stack `reversed(order)`, putting No Coverage at the BOTTOM —
    inverted against the sample, and against this module's own description of
    itself. The legend order was right, which is why it went unnoticed.)
    """
    order = order or COVERAGE_ORDER
    colors = colors or COVERAGE_COLORS
    stack_order = list(order)
    tab = tab.reindex(columns=order, fill_value=0)
    present = [c for c in order if int(tab[c].sum()) > 0]
    totals = tab.sum(axis=1)
    plotted = tab.div(totals.replace(0, np.nan), axis=0).fillna(0) if percent else tab

    fig, ax = plt.subplots(figsize=figsize)
    # A count printed inside a segment must FIT the segment. "97,957" at 9.5pt
    # is wider than a bar when fourteen of them share a nine-inch figure, and it
    # then runs across its neighbours. Sized from the actual bar width and the
    # longest number that has to sit in one.
    bar_w_in = (figsize[0] * 0.62) / max(len(tab), 1)
    longest = max((len(f"{int(v):,}") for v in tab.to_numpy().ravel()), default=3)
    seg_pt = max(6.5, min(9.5, bar_w_in * 72.0 / (longest * 0.62)))
    x = np.arange(len(tab))
    bottom = np.zeros(len(tab))
    totals = plotted.sum(axis=1).to_numpy(dtype=float)
    peak = float(totals.max() or 1)
    handles = {}
    # Counts too small to sit inside their own segment, collected per bar and
    # placed together ABOVE the whole bar afterwards — which is how the sample
    # shows a count of one or two.
    #
    # Above the WHOLE bar, not just above its own segment. Now that the stack
    # runs best-at-the-bottom, the thin classes are the ones at the BOTTOM, and
    # a label nudged up from there lands inside the next segment and collides
    # with that segment's own centred number. Lifting them clear of the bar is
    # the only placement that cannot collide with anything.
    outside: dict[int, list[tuple[int, str]]] = {}
    for cat in stack_order:
        vals = plotted[cat].values.astype(float)
        counts = tab[cat].values.astype(int)
        handles[cat] = ax.bar(x, vals, bottom=bottom, color=colors[cat],
                              width=0.62, label=cat, zorder=3)
        for i, (v, b, n) in enumerate(zip(vals, bottom, counts)):
            if n == 0:
                continue
            if v >= peak * 0.055:
                ax.text(i, b + v / 2, f"{n:,}", ha="center", va="center",
                        fontsize=seg_pt, weight="bold",
                        color="white" if colors[cat] in DARK_FILLS
                        else "#333333", zorder=4)
            else:
                outside.setdefault(i, []).append((int(n), colors[cat]))
        bottom += vals

    # one line height, in data units, at this chart's type size
    step = peak * 0.036
    headroom = 0.0
    for i, items in outside.items():
        y = totals[i] + step * 0.5
        # innermost class nearest the bar, so the order up the page matches the
        # order up the stack
        for n, colour in items:
            ax.text(i, y, f"{n:,}", ha="center", va="bottom", fontsize=seg_pt,
                    weight="bold", color=colour, zorder=4)
            y += step
        headroom = max(headroom, y)
    ax.set_xticks(x)
    # Horizontal labels while they fit; rotated once there are enough
    # categories that a wrapped name would run into its neighbour.
    rotate = len(tab) > 8
    ax.set_xticklabels(
        [textwrap.fill(str(i), label_wrap if not rotate else 18) for i in tab.index],
        fontsize=10 if not rotate else 9.5,
        rotation=30 if rotate else 0, ha="right" if rotate else "center")
    if percent:
        ax.set_ylim(0, max(1.15, headroom * 1.04))
        ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
        ax.yaxis.set_major_formatter(lambda v, p: f"{v:.0%}")
    else:
        ax.set_ylim(0, max(peak * 1.14, headroom * 1.04))
    # The legend sits in the band directly above the plot, so a titled chart
    # needs its title lifted clear of it — otherwise the two print on top of
    # each other, which is exactly what happened the first time these two
    # charts were given titles.
    _canvas(ax, title, title_pt=14.0, title_pad=32 if title else 14)
    # legend above the plot, best-to-worst left-to-right, only classes present
    ax.legend([handles[c] for c in present], present, loc="lower center",
              bbox_to_anchor=(0.5, 1.005), ncol=len(present), fontsize=10,
              frameon=False, handlelength=1.0, handleheight=1.0,
              columnspacing=1.4, borderpad=0.2)
    fig.tight_layout()
    return _save(fig, out_png)


def lga_percent_stacked(tab: pd.DataFrame, out_png: str,
                        title: str | None = None,
                        aspect: float = 1.61) -> str:
    """Coverage mix per LGA as 100% stacked columns (slide 4, right)."""
    return _stacked(tab, out_png, title, percent=True,
                    figsize=_figsize(aspect, len(tab), 0.80, min_w=7.5,
                                     max_w=11.5, min_h=4.2),
                    label_wrap=11)


def ward_count_stacked(tab: pd.DataFrame, out_png: str,
                       title: str | None = None,
                       aspect: float = 1.62) -> str:
    """Settlements per ward, stacked by coverage class (per-LGA slides, right).

    Counts, not percentages: at ward level the sample shows how MANY
    settlements each ward holds, so a one-settlement ward does not read as
    equal in weight to a sixteen-settlement one.
    """
    return _stacked(tab, out_png, title, percent=False,
                    figsize=_figsize(aspect, len(tab), 0.82, min_w=7.5,
                                     max_w=11.5, min_h=4.2),
                    label_wrap=10)


# ------------------------------------------------------------------- slide 5
CUM_BAR_COLORS = {
    "Planned Settlements": BLUE,
    "Fully Covered": GREEN_DARK,
    "Partially Covered": GREEN_MID,
    "Low Coverage": GREEN_LIGHT,
    "No Coverage": ORANGE,
}


def cumulative_bar(counts: dict, out_png: str,
                   title: str = "State Cumulative Settlement Coverage",
                   aspect: float = 2.24) -> str:
    """Planned settlements against the depth of coverage actually achieved."""
    names = [n for n in CUM_BAR_COLORS if n in counts]
    values = [int(counts[n]) for n in names]
    fig, ax = plt.subplots(figsize=_figsize(aspect, len(names), 1.9,
                                            min_w=8.0, max_w=10.5, min_h=3.6))
    top = max(values + [1]) * 1.16
    ax.set_ylim(0, top)
    drawn = ax.bar(np.arange(len(names)), values, width=0.5,
                   color=[CUM_BAR_COLORS[n] for n in names], zorder=3)
    for b, v, n in zip(drawn, values, names):
        _bar_value(ax, b, v, CUM_BAR_COLORS[n], top)
    ax.set_xticks(np.arange(len(names)))
    ax.set_xticklabels([textwrap.fill(n, 12) for n in names], fontsize=12)
    _canvas(ax, title, title_pt=17.0)
    fig.tight_layout()
    return _save(fig, out_png)


# --------------------------------------------------- not visited / missed
# Both of these used to be plain single-series columns — the only charts in the
# report that were not stacked. They are stacked against their own whole now,
# for two reasons. Consistency: every other LGA- and ward-level chart is a
# stacked composition, and one chart that is not makes a reader re-learn the
# page. And meaning: 817 not-visited settlements reads very differently in an
# LGA of 900 than in one of 1,000, and the plain column could not show that.
#
# The reported figures are UNCHANGED — the not-visited count and the missed
# children count are the same numbers, still labelled on their own segment, and
# the slide's "N =" totals still come from `not_visited_by_lga` and
# `missed_children_by_lga`. What is added is the part they came out of.
VISITATION_SPLIT_COLORS = {
    pa.VISITED_LABEL: GREEN_MID,
    pa.NOT_VISITED_LABEL: SALMON,
}
CHILDREN_SPLIT_COLORS = {
    pa.REACHED_CHILDREN_LABEL: GREEN_MID,
    pa.MISSED_CHILDREN_LABEL: BLUE,
}


def not_visited_stacked(tab: pd.DataFrame, out_png: str,
                        title: str = "Settlement Visitation by LGA",
                        aspect: float = 1.57) -> str:
    """Planned settlements per LGA, stacked Visited / Not Visited."""
    return _stacked(tab, out_png, title, percent=False,
                    figsize=_figsize(aspect, len(tab), 0.95, min_w=5.5,
                                     max_w=9.5, min_h=3.8),
                    label_wrap=11,
                    order=[pa.VISITED_LABEL, pa.NOT_VISITED_LABEL],
                    colors=VISITATION_SPLIT_COLORS)


def missed_children_stacked(tab: pd.DataFrame, out_png: str,
                            title: str = "Target Children by LGA",
                            aspect: float = 1.57) -> str:
    """Target children per LGA, stacked by whether their settlement was reached."""
    return _stacked(tab, out_png, title, percent=False,
                    figsize=_figsize(aspect, len(tab), 0.95, min_w=5.5,
                                     max_w=9.5, min_h=3.8),
                    label_wrap=11,
                    order=[pa.REACHED_CHILDREN_LABEL, pa.MISSED_CHILDREN_LABEL],
                    colors=CHILDREN_SPLIT_COLORS)


# ------------------------------------------------------------------- slide 7
def planned_visited_bar(planned: int, visited: int, not_visited: int,
                        out_png: str, title: str | None = None,
                        aspect: float = 1.37) -> str:
    """Planned / Visited / Not Visited, the sample's three-column summary."""
    names = ["Planned", "Visited", "Not Visited"]
    values = [int(planned), int(visited), int(not_visited)]
    colors = [BLUE, VISITED_GREEN, ORANGE]
    fig, ax = plt.subplots(figsize=_figsize(aspect, 3, 2.2, min_w=6.0,
                                            max_w=7.0, min_h=4.2))
    top = max(values + [1]) * 1.16
    ax.set_ylim(0, top)
    drawn = ax.bar(np.arange(3), values, color=colors, width=0.44, zorder=3)
    for b, v, c in zip(drawn, values, colors):
        _bar_value(ax, b, v, c, top)
    ax.set_xticks(np.arange(3))
    ax.set_xticklabels(names, fontsize=13, weight="bold")
    _canvas(ax, title, title_pt=16.0)
    fig.tight_layout()
    return _save(fig, out_png)
