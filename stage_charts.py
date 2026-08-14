"""Report charts — styled after the eHA Daily Tracking Report samples.

Generates PNG charts for the daily report:
  1. team_deployment.png   – GTS Trackers bar (deployed / reported / pending)
  2. state_coverage_donut.png – daily settlement coverage donut, total in centre
  3. lga_coverage_stacked.png – LGA daily coverage 100% stacked bar
  4. lga_cumulative_counts.png – LGA cumulative counts stacked by range
  5. time_spent.png        – time spent in field bar
Palette: eHA greens + salmon (as in the sample report).
"""
import os
import textwrap

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from pipeline_fonts import apply_matplotlib_defaults

# Same font policy as the maps, applied before any figure is created, so the
# charts and the maps in one deck are set in the same face on every machine —
# and so no text object triggers a `findfont` lookup for a font that is not
# installed. See pipeline_fonts for why the stock font list is replaced.
_CHART_FONT = apply_matplotlib_defaults()

# sample-report palette
GREEN_DARK = "#1e6b30"     # Fully Covered / 80-100%
GREEN_MID = "#4f9e57"      # Partially Covered / 50-79%
GREEN_LIGHT = "#a8d5a2"    # Low Coverage / 1-49%
GREEN_PALE = "#cde8c9"     # Very Low Coverage
SALMON = "#e79a96"         # No Coverage / 0%
NO_TRACKS_C = "#a8443f"    # teams never seen at all — darker than the <12 band
BLUE = "#4285f4"           # deployment bars
TITLE_C = "#111111"

COVERAGE_ORDER = ["Fully Covered", "Partially Covered", "Low Coverage",
                  "Very Low Coverage", "No Coverage"]
COVERAGE_COLORS = {"Fully Covered": GREEN_DARK, "Partially Covered": GREEN_MID,
                   "Low Coverage": GREEN_LIGHT, "Very Low Coverage": GREEN_PALE,
                   "No Coverage": SALMON}
RANGE_ORDER = ["0", "1% - 49%", "50% - 79%", "80% - 100%"]
RANGE_COLORS = {"0": SALMON, "1% - 49%": GREEN_LIGHT, "50% - 79%": GREEN_MID,
                "80% - 100%": GREEN_DARK}
# Bottom-to-top order for the stacked LGA bars: fully covered at the bottom,
# no coverage on top. Separate from RANGE_ORDER, which stays worst-to-best and
# still drives the legend, so the key is unchanged by the stacking direction.
STACK_ORDER = list(reversed(RANGE_ORDER))


def _style_ax(ax, title):
    ax.set_title(title, fontsize=13, weight="bold", color=TITLE_C, pad=12)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", color="#dddddd", linewidth=0.7, zorder=0)
    ax.set_axisbelow(True)


def team_deployment_chart(deploy_csv: str, day: int, out_png: str, label: str | None = None) -> dict:
    d = pd.read_csv(deploy_csv)
    gt = d[d["LGA"] == "Grand Total"].iloc[0]
    vals = [int(gt["Teams Deployed"]), int(gt["Teams Reported"]), int(gt["Teams Pending"])]
    labels = ["Teams Deployed", "Teams Reported", "Teams Pending"]
    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    bars = ax.bar(labels, vals, color=BLUE, width=0.55, zorder=3)
    for b, v in zip(bars, vals):
        ax.annotate(f"{v:,}", (b.get_x() + b.get_width() / 2, b.get_height()),
                    ha="center", va="bottom", fontsize=11, weight="bold", color="#0b47a1")
    _style_ax(ax, label or f"GTS Trackers — Day {day}")
    ax.tick_params(labelsize=11)
    fig.tight_layout()
    fig.savefig(out_png, dpi=150, facecolor="white")
    plt.close(fig)
    reporting_pct = float(gt["Reporting %"]) if "Reporting %" in gt else (
        vals[1] / vals[0] if vals[0] else 0)
    return {"deployed": vals[0], "reported": vals[1], "pending": vals[2],
            "reporting_pct": reporting_pct}


def state_coverage_donut(visitation_csv: str, cum_col: str, day: int, out_png: str,
                         label: str | None = None,
                         class_col: str = "Settlement Coverage",
                         day_scope: bool = False) -> dict:
    """Settlement coverage donut.

    `class_col` selects the temporal scope: "Settlement Coverage" is cumulative
    (Day 1 to the reporting day), "Daily Settlement Coverage" is the reporting
    day alone. `cum_col` only ever scopes which settlements were planned, and
    the returned `visited` count follows whichever status column is passed in.

    `day_scope=True` narrows the settlements charted to those PLANNED for
    `day`, using the settlement list's own planning flag. The daily donut needs
    it: drawn against the whole campaign list, a normal day's work reads as a
    catastrophic 29% because the other settlements were never scheduled. The
    slide narration uses the same rule (`stage2_analysis.day_scope_mask`), so
    the two cannot disagree.
    """
    dip = pd.read_csv(visitation_csv, low_memory=False)
    if day_scope:
        from stage2_analysis import day_scope_mask
        scheduled, col = day_scope_mask(dip, day)
        dip = dip[scheduled]
        if col:
            print(f"  daily donut scoped to '{col}' ({len(dip):,} settlements)")
    planned = dip[dip[cum_col].notna()]
    if class_col not in dip.columns:
        class_col = "Settlement Coverage"
    counts = (planned[class_col].value_counts()
              .reindex(COVERAGE_ORDER).fillna(0).astype(int))
    counts = counts[counts > 0]
    total = int(counts.sum())

    fig, ax = plt.subplots(figsize=(6.4, 5.2))
    wedges, _ = ax.pie(counts.values, colors=[COVERAGE_COLORS[c] for c in counts.index],
                       startangle=90, counterclock=False,
                       wedgeprops=dict(width=0.42, edgecolor="white", linewidth=1.5))
    for w, (lab, v) in zip(wedges, counts.items()):
        ang = np.deg2rad((w.theta1 + w.theta2) / 2)
        r = 0.79
        ax.text(r * np.cos(ang), r * np.sin(ang), f"{v:,}\n({v / total:.1%})",
                ha="center", va="center", fontsize=8.5, weight="bold", color="white")
    ax.text(0, 0, f"{total:,}", ha="center", va="center", fontsize=20, weight="bold")
    # the default title follows the scope actually plotted, so a cumulative
    # donut cannot end up captioned "Daily" just because no label was passed
    default_title = (f"State Daily Settlement Coverage — Day {day} only"
                     if class_col.lower().startswith("daily")
                     else f"State Cumulative Settlement Coverage — Day 1 to Day {day}")
    title = label or default_title
    ax.set_title(title, fontsize=13, weight="bold", pad=14)
    ax.legend(wedges, counts.index, loc="lower center", bbox_to_anchor=(0.5, -0.12),
              ncol=3, fontsize=9, frameon=False)
    fig.tight_layout()
    fig.savefig(out_png, dpi=150, facecolor="white")
    plt.close(fig)

    visited = int((dip[cum_col] == "Visited").sum())
    return {"planned": total, "visited": visited,
            "pct": visited / total * 100 if total else 0}


def lga_coverage_stacked(visitation_csv: str, cum_col: str, day: int, out_png: str,
                         label: str | None = None) -> None:
    dip = pd.read_csv(visitation_csv, low_memory=False)
    lga_col = next(c for c in dip.columns if "lga" in c.lower() and "code" not in c.lower())
    planned = dip[dip[cum_col].notna()]
    tab = (planned.groupby([lga_col, "Settlement Coverage"]).size().unstack(fill_value=0)
           .reindex(columns=COVERAGE_ORDER, fill_value=0))
    pct = tab.div(tab.sum(axis=1), axis=0).fillna(0)

    fig, ax = plt.subplots(figsize=(11.5, 5.2))
    bottom = np.zeros(len(pct))
    x = np.arange(len(pct))
    for cat in COVERAGE_ORDER:
        vals = pct[cat].values
        ax.bar(x, vals, bottom=bottom, color=COVERAGE_COLORS[cat], width=0.68,
               label=cat, zorder=3)
        for xi, (v, b, n) in enumerate(zip(vals, bottom, tab[cat].values)):
            if v > 0.045:
                ax.text(xi, b + v / 2, f"{n:,}", ha="center", va="center",
                        fontsize=8, weight="bold",
                        color="white" if cat in ("Fully Covered", "Partially Covered",
                                                 "No Coverage") else "#333333")
        bottom += vals
    ax.set_xticks(x, pct.index, rotation=40, ha="right", fontsize=10)
    ax.set_ylim(0, 1)
    ax.yaxis.set_major_formatter(lambda v, p: f"{v:.0%}")
    _style_ax(ax, label or f"LGA Daily Settlement Coverage — Day {day}")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.28), ncol=5, fontsize=9,
              frameon=False)
    fig.tight_layout()
    fig.savefig(out_png, dpi=150, facecolor="white")
    plt.close(fig)


def coverage_to_range(cov) -> str:
    """Settlement coverage fraction -> the band the stacked bars group by."""
    if pd.isna(cov) or cov == 0:
        return "0"
    if cov < 0.50:
        return "1% - 49%"
    if cov < 0.80:
        return "50% - 79%"
    return "80% - 100%"


def lga_range_stacked(visitation_csv, coverage_col: str, scope_col: str, out_png: str,
                      label: str, day_scope: int | None = None) -> None:
    """Settlements per LGA, stacked by coverage band.

    THE shared chart behind both the State Daily Coverage and State Cumulative
    Coverage slides. It is one function on purpose: the two slides are meant to
    be read side by side, so identical band order, colours, labelling, legend
    and stacking are the point. **Only `coverage_col` and `scope_col` differ**
    between the two calls —

        daily       coverage_col="Daily Coverage", scope_col=f"day_{{N}}_daily"
        cumulative  coverage_col="Coverage",       scope_col=f"day_{{N}}_cumm"

    — so if this chart ever needs redesigning, both slides change together.

    Every LGA in scope is shown even when it has no coverage that day, so the
    daily and cumulative charts always have the same bars in the same order.

    `day_scope=N` additionally narrows the settlements to those PLANNED for day
    N, from the settlement list's own planning flag — passed by the daily call
    only, so that chart counts the day's settlements rather than the whole
    campaign's. The cumulative call leaves it None by design.
    """
    dip = pd.read_csv(visitation_csv, low_memory=False)
    lga_col = next(c for c in dip.columns if "lga" in c.lower() and "code" not in c.lower())
    if coverage_col not in dip.columns:
        coverage_col = "Coverage"
    if day_scope is not None:
        from stage2_analysis import day_scope_mask
        scheduled, col = day_scope_mask(dip, day_scope)
        dip = dip[scheduled]
        if col:
            print(f"  daily LGA bar scoped to '{col}' ({len(dip):,} settlements)")
    scoped = dip[dip[scope_col].notna()].copy() if scope_col in dip.columns else dip.copy()

    scoped["range"] = pd.to_numeric(scoped[coverage_col], errors="coerce").map(coverage_to_range)
    tab = (scoped.groupby([lga_col, "range"]).size().unstack(fill_value=0)
           .reindex(columns=RANGE_ORDER, fill_value=0))

    fig, ax = plt.subplots(figsize=(11.5, 5.2))
    x = np.arange(len(tab))
    bottom = np.zeros(len(tab))
    peak = tab.values.sum(axis=1).max() if len(tab) else 0
    # Stack best-covered at the BOTTOM through no-coverage at the TOP, so the
    # bars grow upward out of the good news and the gap sits on top where it
    # reads as the shortfall. RANGE_ORDER runs worst-to-best, hence reversed.
    handles = {}
    for cat in STACK_ORDER:
        vals = tab[cat].values
        bars = ax.bar(x, vals, bottom=bottom, color=RANGE_COLORS[cat], width=0.68,
                      label=cat, zorder=3)
        handles[cat] = bars
        for xi, (v, b) in enumerate(zip(vals, bottom)):
            if peak and v > peak * 0.04:
                ax.text(xi, b + v / 2, f"{int(v):,}", ha="center", va="center",
                        fontsize=8, weight="bold",
                        color="white" if cat in ("50% - 79%", "80% - 100%", "0") else "#333333")
        bottom += vals
    ax.set_xticks(x, tab.index, rotation=40, ha="right", fontsize=10)
    _style_ax(ax, label)
    # legend keeps RANGE_ORDER regardless of the stacking direction, so the
    # key reads the same as it always has
    ax.legend([handles[c] for c in RANGE_ORDER], RANGE_ORDER,
              loc="upper center", bbox_to_anchor=(0.5, -0.28), ncol=4, fontsize=9,
              frameon=False)
    fig.tight_layout()
    fig.savefig(out_png, dpi=150, facecolor="white")
    plt.close(fig)


def lga_cumulative_counts(visitation_csv: str, cum_col: str, day: int, out_png: str,
                          label: str | None = None) -> None:
    """Cumulative view of `lga_range_stacked` — Day 1 through the reporting day."""
    lga_range_stacked(visitation_csv, "Coverage", cum_col, out_png,
                      label or f"State Cumulative Coverage by LGA — Day 1 to Day {day}")


def lga_daily_counts(visitation_csv: str, daily_col: str, day: int, out_png: str,
                     label: str | None = None) -> None:
    """Daily view of `lga_range_stacked` — the reporting day alone."""
    lga_range_stacked(visitation_csv, "Daily Coverage", daily_col, out_png,
                      label or f"State Daily Coverage by LGA — Day {day} only")


def time_spent_chart(time_csv: str, day: int, out_png: str, label: str | None = None) -> None:
    """Time spent in field, styled after the chart in the ERM Analysis sample.

    Deliberately different from the other charts here: plain blue columns on a
    clean plot with no gridlines and no y-axis, large black axis titles, values
    printed above each column, and horizontal wrapped category labels inside a
    thin chart border. Category order follows the CSV, matching the sample.
    """
    t = pd.read_csv(time_csv)
    t = t[t["Time Spent"] != "Grand Total"]
    categories = [str(c) for c in t["Time Spent"]]
    values = [float(v) for v in t["Number of Teams"]]

    fig, ax = plt.subplots(figsize=(10.4, 5.4))
    positions = np.arange(len(categories))
    bars = ax.bar(positions, values, color=BLUE, width=0.8, zorder=3)

    # value above each column — plain weight, matching the sample
    for b, v in zip(bars, values):
        ax.annotate(f"{int(round(v))}", (b.get_x() + b.get_width() / 2, b.get_height()),
                    ha="center", va="bottom", fontsize=12, color="#000000",
                    xytext=(0, 4), textcoords="offset points")

    ax.set_title(label or f"Time Spent in the Field (8:00am – 3:00pm) — Day {day}",
                 fontsize=12.5, weight="bold", color="#000000", pad=14)
    ax.set_xlabel("Time Spent", fontsize=17, color="#000000", labelpad=10)
    ax.set_ylabel("Number of Teams", fontsize=17, color="#000000", labelpad=10)

    # no gridlines and no y-axis at all — the printed values carry the numbers
    ax.grid(False)
    ax.yaxis.set_visible(False)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.spines["bottom"].set_color("#000000")
    ax.spines["bottom"].set_linewidth(1.0)

    ax.set_xticks(positions)
    ax.set_xticklabels([textwrap.fill(c, 18) for c in categories],
                       fontsize=11, color="#000000")
    ax.tick_params(axis="x", length=0, pad=6)
    ax.set_xlim(-0.6, len(categories) - 0.4)
    ax.set_ylim(0, (max(values) if values else 1) * 1.16)

    fig.tight_layout()
    # thin border around the chart area, as in the sample
    fig.add_artist(plt.Rectangle((0.004, 0.004), 0.992, 0.992, transform=fig.transFigure,
                                 fill=False, edgecolor="#9a9a9a", linewidth=1.0))
    fig.savefig(out_png, dpi=150, facecolor="white")
    plt.close(fig)


def daily_time_spent_chart(tracks_file: str, day: int, out_png: str,
                           report_date: str | None = "latest",
                           label: str | None = None,
                           scope: dict | None = None) -> dict:
    """Teams per time-spent band for ONE tracking date, from the raw timestamps.

    The Time Spent Analysis chart. Its only input is the merged track export:
    for each team, the time between consecutive pings on that date, summed with
    long gaps excluded (see `team_daily_time`). Nothing about the settlement
    plan, assignment, coverage or a time-of-day window enters into it, so the
    bars count exactly the teams that transmitted that day and nothing else.

    `scope` restricts the rows to the campaign's state — GTS exports are
    national, so without it the bars count every state's teams. Built by
    `team_daily_time.scope_from_visitation` from the day's visitation CSV.

    Returns the analysis `meta`, or {} if it could not run — in which case no
    file is written and the caller has one fewer image.
    """
    from team_daily_time import analyse, describe, UNDER_12
    try:
        res = analyse(tracks_file, report_date, scope=scope)
    except Exception as exc:
        print(f"  daily time-spent chart skipped ({exc})")
        return {}
    dist = res["distribution"]
    if not int(dist["Teams"].sum()):
        print("  daily time-spent chart skipped (no teams transmitted)")
        return {}
    print(f"  {describe(res)}")

    categories = [str(c) for c in dist["Time Spent Range"]]
    values = [float(v) for v in dist["Teams"]]
    colors = [SALMON if c == UNDER_12 else BLUE for c in categories]

    fig, ax = plt.subplots(figsize=(10.4, 5.4))
    positions = np.arange(len(categories))
    bars = ax.bar(positions, values, color=colors, width=0.8, zorder=3)
    for b, v in zip(bars, values):
        ax.annotate(f"{int(round(v))}", (b.get_x() + b.get_width() / 2, b.get_height()),
                    ha="center", va="bottom", fontsize=12, color="#000000",
                    xytext=(0, 4), textcoords="offset points")

    date_txt = res["meta"]["date"] or f"Day {day}"
    ax.set_title(label or f"Teams by Time Spent Tracking — {date_txt}",
                 fontsize=12.5, weight="bold", color="#000000", pad=14)
    ax.set_xlabel("Time Spent Tracking", fontsize=17, color="#000000", labelpad=10)
    ax.set_ylabel("Number of Teams", fontsize=17, color="#000000", labelpad=10)

    ax.grid(False)
    ax.yaxis.set_visible(False)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.spines["bottom"].set_color("#000000")
    ax.spines["bottom"].set_linewidth(1.0)
    ax.set_xticks(positions)
    ax.set_xticklabels([textwrap.fill(c, 18) for c in categories],
                       fontsize=11, color="#000000")
    ax.tick_params(axis="x", length=0, pad=6)
    ax.set_xlim(-0.6, len(categories) - 0.4)
    ax.set_ylim(0, (max(values) if values else 1) * 1.16)

    fig.tight_layout()
    fig.add_artist(plt.Rectangle((0.004, 0.004), 0.992, 0.992, transform=fig.transFigure,
                                 fill=False, edgecolor="#9a9a9a", linewidth=1.0))
    fig.savefig(out_png, dpi=150, facecolor="white")
    plt.close(fig)
    res["meta"]["by_lga"] = res["by_lga"]
    return res["meta"]


def team_time_range_chart(visitation_csv, day: int, out_png: str,
                          state: str | None = None, label: str | None = None) -> dict:
    """Teams per time-spent band, from the team performance analysis.

    Styled to match `time_spent_chart` so the two read as the same family, but
    it is a different measure — each team's TOTAL minutes across the
    settlements assigned to it, with no 08:00-15:00 window. The title says so,
    because the two charts are otherwise easy to confuse.

    Bars below 12 minutes are drawn in the not-visited red: that band is the
    follow-up list, and it should be the thing the eye lands on.

    Returns the analysis' `meta` dict (team counts, how minutes were derived),
    or {} if the analysis could not run — in which case no chart is written and
    the caller simply has one fewer image.
    """
    from team_time_range import analyse, state_distribution, NO_TRACKS, UNDER_12
    try:
        result = analyse(visitation_csv, state)
    except Exception as exc:
        print(f"  team time-range chart skipped ({exc})")
        return {}

    dist = state_distribution(result)
    categories = [str(c) for c in dist["Time Spent Range"]]
    values = [float(v) for v in dist["Teams"]]
    # Two follow-up bands, two different problems, so two colours: teams that
    # were never seen at all (darker), and teams seen only briefly (salmon).
    # Folding them into one bar is what made "under 12 minutes" read as an
    # implausible share of the campaign.
    colors = [NO_TRACKS_C if c == NO_TRACKS else SALMON if c == UNDER_12 else BLUE
              for c in categories]

    fig, ax = plt.subplots(figsize=(10.4, 5.4))
    positions = np.arange(len(categories))
    bars = ax.bar(positions, values, color=colors, width=0.8, zorder=3)

    for b, v in zip(bars, values):
        ax.annotate(f"{int(round(v))}", (b.get_x() + b.get_width() / 2, b.get_height()),
                    ha="center", va="bottom", fontsize=12, color="#000000",
                    xytext=(0, 4), textcoords="offset points")

    ax.set_title(label or f"Teams by Time Spent Range, across assigned settlements "
                          f"— Day {day}",
                 fontsize=12.5, weight="bold", color="#000000", pad=14)
    ax.set_xlabel("Time Spent Range", fontsize=17, color="#000000", labelpad=10)
    ax.set_ylabel("Number of Teams", fontsize=17, color="#000000", labelpad=10)

    ax.grid(False)
    ax.yaxis.set_visible(False)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.spines["bottom"].set_color("#000000")
    ax.spines["bottom"].set_linewidth(1.0)

    ax.set_xticks(positions)
    ax.set_xticklabels([textwrap.fill(c, 18) for c in categories],
                       fontsize=11, color="#000000")
    ax.tick_params(axis="x", length=0, pad=6)
    ax.set_xlim(-0.6, len(categories) - 0.4)
    ax.set_ylim(0, (max(values) if values else 1) * 1.16)

    fig.tight_layout()
    fig.add_artist(plt.Rectangle((0.004, 0.004), 0.992, 0.992, transform=fig.transFigure,
                                 fill=False, edgecolor="#9a9a9a", linewidth=1.0))
    fig.savefig(out_png, dpi=150, facecolor="white")
    plt.close(fig)
    return result["meta"]


def reached_donut(split: dict, out_png: str, title: str, centre_label: str = "") -> None:
    """Reached vs not-reached donut for one estimated indicator.

    Deliberately the same construction as `state_coverage_donut` — same wedge
    width, same white separators, same centre total, same legend placement — so
    the new slide looks native to the deck rather than bolted on. Only the
    palette differs: two categories instead of five coverage classes.
    """
    values = [split["reached"], split["not_reached"]]
    labels = [f"Reached ({split['reached_pct']:.1%})",
              f"Not Reached ({split['not_reached_pct']:.1%})"]
    colors = [GREEN_DARK, SALMON]
    total = split["total"]

    # a zero total would make pie() raise; show an empty ring instead
    drawable = [v for v in values if v > 0]
    fig, ax = plt.subplots(figsize=(5.6, 5.2))
    if not drawable:
        ax.pie([1], colors=["#e6e6e6"], startangle=90,
               wedgeprops=dict(width=0.42, edgecolor="white", linewidth=1.5))
        wedges = []
    else:
        wedges, _ = ax.pie(values, colors=colors, startangle=90, counterclock=False,
                           wedgeprops=dict(width=0.42, edgecolor="white", linewidth=1.5))
    # In-wedge labels only where the slice can hold them. A thin "not reached"
    # sliver — which is the good news case — would otherwise print its label
    # across the ring edge. The legend carries the percentage either way, and
    # the callout below picks up whatever is dropped here.
    MIN_LABELLED_SHARE = 0.08
    outside = []
    for w, v in zip(wedges, values):
        if v <= 0:
            continue
        share = v / total if total else 0
        ang = np.deg2rad((w.theta1 + w.theta2) / 2)
        if share >= MIN_LABELLED_SHARE:
            ax.text(0.79 * np.cos(ang), 0.79 * np.sin(ang), f"{v:,}\n({share:.1%})",
                    ha="center", va="center", fontsize=9, weight="bold", color="white")
        else:
            outside.append((ang, v, share))
    for ang, v, share in outside:
        x, y = 1.16 * np.cos(ang), 1.16 * np.sin(ang)
        ax.annotate(f"{v:,} ({share:.1%})", xy=(np.cos(ang), np.sin(ang)), xytext=(x, y),
                    ha="left" if x >= 0 else "right", va="center", fontsize=8.5,
                    weight="bold", color=SALMON,
                    arrowprops=dict(arrowstyle="-", color=SALMON, linewidth=0.9))
    ax.set_xlim(-1.45, 1.45)
    ax.text(0, 0.06, f"{total:,}", ha="center", va="center", fontsize=19, weight="bold")
    if centre_label:
        ax.text(0, -0.16, centre_label, ha="center", va="center", fontsize=8.5,
                color="#555555")
    ax.set_title(title, fontsize=12.5, weight="bold", color=TITLE_C, pad=14)
    if wedges:
        ax.legend(wedges, labels, loc="lower center", bbox_to_anchor=(0.5, -0.10),
                  ncol=2, fontsize=9, frameon=False)
    fig.tight_layout()
    fig.savefig(out_png, dpi=150, facecolor="white")
    plt.close(fig)


def target_coverage_lga_bar(by_lga, out_png: str, label: str | None = None) -> bool:
    """Estimated <5 children reached vs not reached per LGA, horizontal stacked.

    Horizontal because LGA names are long and this chart shares a slide with
    two donuts, so it gets a tall narrow slot. Falls back to the household
    columns when the settlement list carried no population field. Returns False
    if neither is available, in which case no file is written.
    """
    if by_lga is None or not len(by_lga):
        return False
    if "<5 Children Reached" in by_lga.columns:
        reached_col, not_col = "<5 Children Reached", "<5 Children Not Reached"
        what = "Estimated <5 Children"
    elif "Households Reached" in by_lga.columns:
        reached_col, not_col = "Households Reached", "Households Not Reached"
        what = "Estimated Households"
    else:
        return False

    d = by_lga.sort_values(reached_col, ascending=True)
    y = np.arange(len(d))
    fig, ax = plt.subplots(figsize=(7.2, max(4.2, 0.52 * len(d) + 1.4)))
    ax.barh(y, d[reached_col], color=GREEN_DARK, label="Reached", zorder=3)
    ax.barh(y, d[not_col], left=d[reached_col], color=SALMON,
            label="Not Reached", zorder=3)

    totals = d[reached_col] + d[not_col]
    for i, (r, t) in enumerate(zip(d[reached_col], totals)):
        if t > 0:
            ax.text(t, i, f"  {r / t:.0%}", va="center", ha="left",
                    fontsize=9, weight="bold", color="#333333")

    ax.set_yticks(y, d["LGA"], fontsize=10)
    ax.set_xlabel(what, fontsize=10, color="#333333")
    ax.set_title(label or f"{what}: Reached vs Not Reached by LGA",
                 fontsize=12.5, weight="bold", color=TITLE_C, pad=12)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="x", color="#dddddd", linewidth=0.7, zorder=0)
    ax.set_axisbelow(True)
    ax.set_xlim(0, float(totals.max()) * 1.16 if len(totals) else 1)
    ax.legend(loc="lower right", fontsize=9, frameon=False)
    fig.tight_layout()
    fig.savefig(out_png, dpi=150, facecolor="white")
    plt.close(fig)
    return True


def target_population_charts(visitation_csv, cum_col: str, output_folder: str,
                             label_suffix: str = "", daily_col: str | None = None) -> dict:
    """Target Population & Household Coverage charts, daily and cumulative.

    The DAILY charts are the primary ones — they carry no `_cum` suffix and are
    what the slide leads with. The cumulative set is written alongside as
    context. Chart titles say which scope they are, because two donuts showing
    different numbers for the same indicator is otherwise just confusing.

    Returns {"daily": ..., "cumulative": ...} so the workbook and the slide
    reuse the analysis rather than recomputing it. Returns {} when the
    settlement list carries neither estimate.
    """
    from target_population import analyse_daily_and_cumulative, describe
    try:
        both = analyse_daily_and_cumulative(visitation_csv, daily_col, cum_col)
    except Exception as exc:
        print(f"  target population analysis skipped ({exc})")
        return {}

    day_label = f" — {label_suffix}" if label_suffix else ""
    scopes = [
        ("daily", both["daily"], "", f"{day_label} (this day only)"),
        ("cumulative", both["cumulative"], "_cum", f"{day_label} (Day 1 to date)"),
    ]
    for name, result, suffix, title_tail in scopes:
        if result is None:
            print(f"  target population {name}: not available")
            continue
        print(f"  target population {name}: {describe(result)}")
        if result["children"]:
            reached_donut(result["children"],
                          os.path.join(output_folder, f"target_children_donut{suffix}.png"),
                          f"Estimated <5 Target Population{title_tail}",
                          centre_label="estimated <5 children")
        if result["households"]:
            reached_donut(result["households"],
                          os.path.join(output_folder, f"target_households_donut{suffix}.png"),
                          f"Estimated Households{title_tail}",
                          centre_label="estimated households")
        # Short title: this chart sits in a narrow third of the slide, and the
        # long form ran past the figure's own width and was cut off mid-word.
        scope_word = "this day" if name == "daily" else "Day 1 to date"
        target_coverage_lga_bar(
            result["by_lga"],
            os.path.join(output_folder, f"target_coverage_by_lga{suffix}.png"),
            label=f"Reached vs Not Reached by LGA ({scope_word})")
    return both


def team_efficiency_chart(visitation_csv, out_png: str, scope: str = "cumulative",
                          label: str | None = None) -> dict:
    """Team Time Efficiency — field minutes against gridded coverage.

    A scatter rather than a bar: the question is the *relationship* between two
    continuous measures, and the follow-up group is defined by a corner of that
    plane, not by a rank. Threshold lines at 2 hours and 50% divide the plot
    into the four quadrants; the top-right shading marks the flagged corner so
    the eye lands there first.

    Returns the analysis `meta`, or {} if it could not run (in which case no
    file is written and the caller simply has one fewer image).
    """
    from team_efficiency import (analyse, describe, LONG_FIELD_MINUTES,
                                 LOW_COVERAGE, Q_FLAG, STATIONARY_MAX_CELLS)
    # How many flagged codes to name in the gutter. The slide's follow-up table
    # has been removed, so this list is now the only place a reader sees team
    # codes — and the chart has the full slide width, so more of them fit
    # legibly. Twelve is what the plot height holds at 9pt without crowding.
    MAX_LABELLED_TEAMS = 12
    try:
        res = analyse(visitation_csv, scope)
    except Exception as exc:
        print(f"  team efficiency chart skipped ({exc})")
        return {}
    pt = res["per_team"].dropna(subset=["Coverage"])
    if not len(pt):
        return {}
    print(f"  team efficiency ({scope}): {describe(res)}")

    hours = pt["Minutes"] / 60.0
    cov = pt["Coverage"] * 100
    # Two flagged categories, drawn distinctly. A team can meet both rules; it
    # is drawn as stationary, the sharper finding, so every team appears once
    # and the three series sum to the team count.
    stationary = pt["Stationary"] if "Stationary" in pt.columns else pd.Series(
        False, index=pt.index)
    flagged = (pt["Quadrant"] == Q_FLAG) & ~stationary
    within = ~flagged & ~stationary
    x_max = max(3.5, float(hours.max()) * 1.10)
    thr_h = LONG_FIELD_MINUTES / 60.0
    thr_c = LOW_COVERAGE * 100

    # A gutter is reserved on the right for the flagged team codes. They used to
    # be annotated in place with fixed offsets, but flagged teams cluster in one
    # corner by definition, so the labels landed on each other, on the points
    # they name and on the legend. Stacking them outside the data area keeps
    # every code readable and leaves the plot unobstructed.
    plot_max = x_max
    gutter = plot_max * 0.22
    x_max = plot_max + gutter

    fig, ax = plt.subplots(figsize=(13.2, 4.4))
    # shade the flagged corner: beyond the time threshold, below the coverage one
    ax.add_patch(plt.Rectangle((thr_h, 0), plot_max - thr_h, thr_c,
                               facecolor=SALMON, alpha=0.16, zorder=0))
    # visually separate the label gutter from the plot
    ax.add_patch(plt.Rectangle((plot_max, 0), gutter, 104,
                               facecolor="white", edgecolor="none", zorder=1.5))
    ax.axvline(plot_max, color="#dddddd", linewidth=0.8, zorder=1.6)
    ax.axvline(thr_h, color="#8c2b29", linewidth=1.2, linestyle=(0, (5, 3)), zorder=2)
    ax.axhline(thr_c, color="#8c2b29", linewidth=1.2, linestyle=(0, (5, 3)), zorder=2)

    ax.scatter(hours[within], cov[within], s=44, color=GREEN_MID, marker="o",
               edgecolor="white", linewidth=0.7, zorder=3,
               label=f"Within thresholds ({int(within.sum()):,})")
    ax.scatter(hours[flagged], cov[flagged], s=78, color=SALMON, marker="o",
               edgecolor="#8c2b29", linewidth=0.9, zorder=4,
               label=f">{thr_h:g} hrs & <{thr_c:g}% coverage "
                     f"({int(flagged.sum()):,})")
    # Diamonds, darker, larger — a different shape as well as a different
    # colour, because these sit inside the same corner as the salmon points
    # and colour alone would not separate them.
    ax.scatter(hours[stationary], cov[stationary], s=104, color=NO_TRACKS_C,
               marker="D", edgecolor="#5d2422", linewidth=0.9, zorder=5,
               label=f"Stationary — <={STATIONARY_MAX_CELLS} grid cell "
                     f"({int(stationary.sum()):,})")
    # Name the worst offenders only; labelling every point turns to mush. The
    # codes are stacked at evenly spaced heights in the right-hand gutter with
    # a thin leader line back to their point, so no two can collide and none
    # sits over the data.
    # Stationary teams lead the gutter — fewer of them and the sharper finding
    # — then the worst of the long-time/low-coverage group fills what is left.
    lead = pt[stationary].nsmallest(MAX_LABELLED_TEAMS, "Coverage")
    fill = pt[flagged].nsmallest(max(0, MAX_LABELLED_TEAMS - len(lead)), "Coverage")
    worst = pd.concat([lead, fill]) if len(fill) else lead
    stationary_codes = set(lead["Team"].astype(str))
    if len(worst):
        label_x = plot_max + gutter * 0.12
        n = len(worst)
        # top-down, inside the shaded band's vertical range where possible
        slots = np.linspace(min(96, 8 + 11 * n), 8, n) if n > 1 else np.array([50.0])
        for (_, r), y in zip(worst.iterrows(), slots):
            px, py = r["Minutes"] / 60.0, r["Coverage"] * 100
            is_stat = str(r["Team"]) in stationary_codes
            colour = NO_TRACKS_C if is_stat else "#8c2b29"
            ax.annotate("", xy=(px, py), xytext=(label_x, y),
                        arrowprops=dict(arrowstyle="-", color=colour, alpha=0.55,
                                        linewidth=0.7, shrinkA=2, shrinkB=3),
                        zorder=4)
            ax.text(label_x, y, f"{r['Team']}{' *' if is_stat else ''}",
                    fontsize=9, weight="bold", color=colour,
                    ha="left", va="center", zorder=6)
        ax.text(plot_max + gutter * 0.12, 101,
                "Flagged teams   * stationary", fontsize=9,
                style="italic", color="#8c2b29", ha="left", va="center", zorder=5)

    ax.set_xlim(0, x_max); ax.set_ylim(0, 104)
    ax.set_xlabel("Time in the field (hours)", fontsize=11, color="#333333")
    ax.set_ylabel("Gridded area coverage (%)", fontsize=11, color="#333333")
    # tick labels stop at the plot area — the gutter is not part of the scale
    ax.set_xticks([t for t in ax.get_xticks() if 0 <= t <= plot_max])
    scope_txt = "Day 1 to date" if scope == "cumulative" else "reporting day only"
    _style_ax(ax, label or f"Team Time Efficiency — {scope_txt}")
    # The two rules overlap, so say by how much rather than leaving a reader to
    # wonder why the legend's counts and the narration's differ.
    meta = res["meta"]
    note = (f"{meta['flagged_any']:,} teams flagged in total"
            + (f" — {meta['flagged_both']:,} meet both rules and are shown as "
               f"stationary" if meta.get("flagged_both") else ""))
    ax.text(0.5, 1.005, note, transform=ax.transAxes, ha="center", va="bottom",
            fontsize=9, style="italic", color="#555555")
    ax.grid(axis="both", color="#e6e6e6", linewidth=0.7, zorder=0)
    ax.set_axisbelow(True)
    # Legend below the plot: "lower right" sat squarely in the flagged corner,
    # the one region of this chart the reader must be able to see, and above
    # the axes it collided with the title. Under the x-axis label there is
    # nothing to collide with at any data density.
    ax.legend(loc="upper center", bbox_to_anchor=(0.42, -0.13), ncol=3,
              fontsize=9, frameon=False, borderaxespad=0)
    ax.text(thr_h + (plot_max - thr_h) / 2, thr_c / 2,
            "LONG TIME,\nLOW COVERAGE", ha="center", va="center", fontsize=10,
            weight="bold", color="#8c2b29", alpha=0.45, zorder=1)
    fig.tight_layout()
    fig.savefig(out_png, dpi=150, facecolor="white")
    plt.close(fig)
    return res["meta"]


def missed_children_chart(missed_csv_or_df, out_png: str,
                          title: str = "Potentially Missed Children by LGA") -> int:
    """Horizontal bar of potentially-missed target children per LGA (Not Visited
    settlements), ranked descending. Accepts a CSV path or an already-built
    DataFrame (LGA, Missed Children) such as `missed_children_table()`'s output."""
    if isinstance(missed_csv_or_df, str):
        m = pd.read_csv(missed_csv_or_df)
    else:
        m = missed_csv_or_df.copy()
    m = m[m["LGA"] != "Grand Total"].sort_values("Missed Children", ascending=True)

    fig, ax = plt.subplots(figsize=(9.5, max(4.4, 0.42 * len(m))))
    bars = ax.barh(m["LGA"], m["Missed Children"], color=SALMON, zorder=3)
    for b, v in zip(bars, m["Missed Children"]):
        ax.annotate(f"{int(v):,}", (b.get_width(), b.get_y() + b.get_height() / 2),
                    ha="left", va="center", fontsize=9, weight="bold", color="#8c2b29",
                    xytext=(4, 0), textcoords="offset points")
    ax.set_title(title, fontsize=13, weight="bold", color=TITLE_C, pad=12)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="x", color="#dddddd", linewidth=0.7, zorder=0)
    ax.set_axisbelow(True)
    ax.tick_params(labelsize=10)
    fig.tight_layout()
    fig.savefig(out_png, dpi=150, facecolor="white")
    plt.close(fig)
    return int(m["Missed Children"].sum())


# ---- state cumulative settlement coverage --------------------------------- #
# Bars, in order, with their palette colour and the colour of the value label
# when it is printed inside the bar.
CUM_COVERAGE_BARS = [
    ("Planned Settlement", BLUE, "white"),
    ("Fully Covered", GREEN_DARK, "white"),
    ("Partially Covered", GREEN_MID, "white"),
    ("Low Coverage", GREEN_LIGHT, "white"),
    ("Not Yet Visited", SALMON, "#111111"),
]


def cumulative_coverage_stats(visitation, cum_col: str) -> dict:
    """Counts behind the State Cumulative Settlement Coverage chart.

    Accepts a path or an already-loaded DataFrame, so callers that have the
    settlement list open (stage 6) do not re-read it.

    'Not Yet Visited' is settlements the cumulative status column says have NOT
    been reached, so the chart carries the same rule as every other cumulative
    figure in the deck: a planned settlement with evidence of tracks on any day
    of the period counts as reached, whichever day it was planned for.

    Reconciliation: the three covered classes are grid-coverage measurements
    and a settlement can be reached without one — no polygon in the gridded
    layer, so pings but no measurable coverage. Any such surplus is put in
    'Low Coverage', the honest bucket for reached-but-barely-measured, which
    keeps the four outcome bars summing to the planned total as they do in the
    sample chart.
    """
    dip = (pd.read_csv(visitation, low_memory=False)
           if isinstance(visitation, str) else visitation)
    planned = dip[dip[cum_col].notna()]
    total = int(len(planned))
    classes = planned["Settlement Coverage"].value_counts()
    fully = int(classes.get("Fully Covered", 0))
    partial = int(classes.get("Partially Covered", 0))
    low = int(classes.get("Low Coverage", 0))
    not_yet = int((planned[cum_col] != "Visited").sum())
    low += max(0, (total - not_yet) - (fully + partial + low))
    not_yet = max(0, total - fully - partial - low)
    pct = (lambda n: n / total if total else 0)
    return {"planned": total, "fully": fully, "partial": partial, "low": low,
            "not_yet": not_yet, "covered": fully + partial + low,
            "fully_pct": pct(fully), "partial_pct": pct(partial),
            "low_pct": pct(low), "not_yet_pct": pct(not_yet),
            "covered_pct": pct(fully + partial + low)}


def state_cumulative_coverage_chart(visitation_csv: str, cum_col: str, day: int,
                                    out_png: str, label: str | None = None) -> dict:
    """Planned settlements against their cumulative coverage class."""
    st = cumulative_coverage_stats(visitation_csv, cum_col)
    total, fully = st["planned"], st["fully"]
    partial, low, not_yet = st["partial"], st["low"], st["not_yet"]

    values = [total, fully, partial, low, not_yet]
    names = [b[0] for b in CUM_COVERAGE_BARS]
    colors = [b[1] for b in CUM_COVERAGE_BARS]

    fig, ax = plt.subplots(figsize=(8.6, 5.3))
    x = np.arange(len(names))
    bars = ax.bar(x, values, color=colors, width=0.55, zorder=3)

    top = (max(values) if values else 1) * 1.15
    ax.set_ylim(0, top)
    for bar, value, (name, fill, inside_color) in zip(bars, values, CUM_COVERAGE_BARS):
        # tall enough to hold the label inside; otherwise print it above, in a
        # dark tone so it stays readable against the white background
        if bar.get_height() >= top * 0.12:
            ax.text(bar.get_x() + bar.get_width() / 2,
                    bar.get_height() - top * 0.035, f"{value:,}",
                    ha="center", va="top", fontsize=12.5, weight="bold",
                    color=inside_color, zorder=4)
        else:
            ax.text(bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + top * 0.015, f"{value:,}",
                    ha="center", va="bottom", fontsize=12.5, weight="bold",
                    color=GREEN_DARK if fill in (GREEN_DARK, GREEN_MID, GREEN_LIGHT)
                    else "#111111", zorder=4)

    ax.set_title(label or f"State Cumulative Settlement Coverage — Day {day}",
                 fontsize=17, weight="bold", color="#000000", pad=16)
    ax.set_xticks(x)
    ax.set_xticklabels([textwrap.fill(n, 12) for n in names], fontsize=11.5,
                       color="#000000")
    ax.tick_params(axis="x", length=0, pad=6)
    ax.tick_params(axis="y", labelsize=11, length=0, colors="#000000")
    ax.grid(axis="y", color="#d9d9d9", linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.spines["bottom"].set_color("#000000")
    fig.tight_layout()
    fig.savefig(out_png, dpi=150, facecolor="white")
    plt.close(fig)
    return st


def generate_charts(visitation_csv: str, cum_col: str, deploy_csv: str, time_csv: str,
                    day: int, output_folder: str, analysis_type: str = "daily",
                    daily_col: str | None = None,
                    track_date: str | None = None,
                    tracks_file: str | None = None) -> dict:
    """`analysis_type="post_campaign"` relabels the charts (e.g. "Day N" ->
    "Post-Campaign (Cumulative)") and additionally generates
    missed_children.png from the settlement list's target-population field.

    `track_date` is the transmission date stage 2 scoped the day's tracks to.
    It is printed on the time-spent charts, so a reader can see at a glance
    which day's field time is being shown rather than having to trust it.

    `tracks_file` is the merged track export. The Time Spent Analysis chart is
    measured from it directly — see `daily_time_spent_chart`. Without it that
    one chart cannot be produced; everything else is unaffected."""
    os.makedirs(output_folder, exist_ok=True)
    is_pc = analysis_type == "post_campaign"
    lbl = (lambda base: f"{base} — Post-Campaign (Cumulative)") if is_pc else (lambda base: None)
    daily_col = daily_col or f"day_{day}_daily"
    stats = {}
    stats["deploy"] = team_deployment_chart(
        deploy_csv, day, os.path.join(output_folder, "team_deployment.png"),
        label=lbl("GTS Trackers"))
    # cumulative donut — Day 1 through the reporting day (slide 6)
    stats["coverage"] = state_coverage_donut(
        visitation_csv, cum_col, day, os.path.join(output_folder, "state_coverage_donut.png"),
        label=lbl("Settlement Coverage"))
    # daily donut — the reporting day alone (slide 5)
    stats["coverage_daily"] = state_coverage_donut(
        visitation_csv, cum_col, day,
        os.path.join(output_folder, "state_coverage_donut_daily.png"),
        label=lbl("Daily Settlement Coverage"), class_col="Daily Settlement Coverage")
    lga_coverage_stacked(visitation_csv, cum_col, day,
                         os.path.join(output_folder, "lga_coverage_stacked.png"),
                         label=lbl("LGA Settlement Coverage"))
    # The two stacked bars behind slides 5 and 6 — same function, same design,
    # different temporal scope. Kept adjacent so they cannot drift apart.
    lga_cumulative_counts(visitation_csv, cum_col, day,
                          os.path.join(output_folder, "lga_cumulative_counts.png"),
                          label=lbl("LGA Cumulative Settlements Coverage"))
    lga_daily_counts(visitation_csv, daily_col, day,
                     os.path.join(output_folder, "lga_daily_counts.png"),
                     label=lbl("LGA Daily Settlements Coverage"))
    on_date = f" on {track_date}" if track_date else ""
    time_spent_chart(time_csv, day, os.path.join(output_folder, "time_spent.png"),
                     label=lbl("Time Spent in the Field (8:00am – 3:00pm)")
                     or f"Time Spent in the Field (8:00am – 3:00pm) — Day {day}{on_date}")
    # Time Spent Analysis — measured from the raw track timestamps, per team,
    # for one tracking date. This is what the slide uses.
    stats["daily_time_spent"] = {}
    if tracks_file and os.path.exists(tracks_file):
        # The visitation CSV is already filtered to the campaign's state, so it
        # is the scope — no extra input, and it cannot drift from the rest of
        # the pipeline.
        from team_daily_time import scope_from_visitation
        stats["daily_time_spent"] = daily_time_spent_chart(
            tracks_file, day,
            os.path.join(output_folder, "daily_time_spent.png"),
            report_date=track_date or "latest",
            label=lbl("Teams by Time Spent Tracking"),
            scope=scope_from_visitation(visitation_csv))
    else:
        print("  daily time-spent chart skipped (no merged track export given)")
    # The assigned-settlement view is kept for the workbook tabs and the
    # under-12 follow-up list; it no longer drives the slide.
    stats["team_time_range"] = team_time_range_chart(
        visitation_csv, day, os.path.join(output_folder, "team_time_range.png"),
        label=lbl("Teams by Time Spent Range, across assigned settlements")
        or f"Teams by Time Spent Range, across assigned settlements "
           f"— Day {day}{on_date}")
    stats["team_efficiency"] = team_efficiency_chart(
        visitation_csv, os.path.join(output_folder, "team_efficiency.png"),
        scope="cumulative")
    stats["target_population"] = target_population_charts(
        visitation_csv, cum_col, output_folder,
        label_suffix="Post-Campaign" if is_pc else f"Day {day}", daily_col=daily_col)
    stats["cumulative_coverage"] = state_cumulative_coverage_chart(
        visitation_csv, cum_col, day,
        os.path.join(output_folder, "state_cumulative_coverage.png"),
        label=lbl("State Cumulative Settlement Coverage"))
    if is_pc:
        from stage3_erm_workbook import missed_children_table
        dip = pd.read_csv(visitation_csv, low_memory=False)
        missed = missed_children_table(dip, cum_col)
        if len(missed) > 1:
            stats["missed_children"] = missed_children_chart(
                missed, os.path.join(output_folder, "missed_children.png"))
    print(f"Charts saved to {output_folder}")
    return stats
