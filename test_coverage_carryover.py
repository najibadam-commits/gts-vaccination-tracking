"""Cumulative coverage must survive a previous DIP whose Coverage is TEXT.

The bug this guards: `run_analysis` combined today's coverage with the previous
day's through a per-row `np.nanmax` over a two-element list. When one of the
two values was a string — which is what arrives whenever the file chosen as the
previous DIP carries coverage CLASS names, or fractions Excel reformatted as
text — numpy typed the pair as strings and raised

    UFuncTypeError: ufunc 'maximum' did not contain a loop with signature
    matching types (dtype('<U12'), dtype('<U12')) -> None

several minutes into a run, naming neither the column nor the file. The same
trap sat in `checkpoint_runner.step_dip` as `.astype(float)`.

What is asserted here is BOTH halves of the fix: the run no longer dies, and it
does not paper over the loss either — a column that could not be read has to be
reported, because silently coercing it to NaN understates cumulative coverage
across the whole report.

Run:  python test_coverage_carryover.py
"""
from __future__ import annotations

import io
import os
import sys
from contextlib import redirect_stdout

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stage2_analysis import coerce_coverage

FAILED = []


def check(condition, message):
    print(f"  {'ok  ' if condition else 'FAIL'}  {message}")
    if not condition:
        FAILED.append(message)


def combine(today, prev):
    """The production rule, captured once so the test cannot drift from it."""
    out = io.StringIO()
    with redirect_stdout(out):
        result = np.fmax(coerce_coverage(pd.Series(today), "Coverage"),
                         coerce_coverage(pd.Series(prev), "Prev_Coverage"))
    return result, out.getvalue()


def main() -> int:
    print("the crash")
    # The exact shape from the field: class names where fractions belong.
    result, log = combine([0.85, np.nan, 0.40, np.nan, 0.10],
                          ["Low Coverage", "No Coverage", 0.62, np.nan, "0.55"])
    check(True, "a text previous coverage no longer raises UFuncTypeError")

    print("\nthe rule is unchanged")
    check(result.iloc[0] == 0.85, "today wins when it is the larger (0.85)")
    check(result.iloc[2] == 0.62, "the previous day wins when IT is larger (0.62)")
    check(result.iloc[4] == 0.55,
          "a fraction stored as text is still read as a number ('0.55')")
    check(bool(np.isnan(result.iloc[1])),
          "unreadable previous + no coverage today = no coverage")
    check(bool(np.isnan(result.iloc[3])), "both missing stays missing")

    clean_today = [0.1, 0.9, np.nan, 0.4]
    clean_prev = [0.5, 0.2, 0.3, np.nan]
    clean, clean_log = combine(clean_today, clean_prev)
    expected = [0.5, 0.9, 0.3, 0.4]
    check(list(clean) == expected,
          f"clean numeric input is untouched ({expected})")
    check(clean_log == "", "and says nothing, because nothing was lost")

    print("\nthe loss is reported, never silent")
    check("Low Coverage" in log and "Prev_Coverage" in log,
          "the warning names the column and the values it could not read")
    check("2 of 4" in log,
          "and how many of the present values were dropped")
    _, all_bad = combine([0.2, np.nan, 0.9],
                         ["Low Coverage", "No Coverage", "Fully Covered"])
    check("EVERY value" in all_bad,
          "a wholly unusable column gets its own, louder message")
    check("visitation CSV" in all_bad and "class names" in all_bad,
          "which says what to check about the file that was chosen")

    print("\nboth call sites are fixed")
    here = os.path.dirname(os.path.abspath(__file__))
    for name in ("stage2_analysis.py", "checkpoint_runner.py"):
        text = io.open(os.path.join(here, name), encoding="utf-8").read()
        check("coerce_coverage" in text, f"{name} coerces before comparing")
        check("np.nanmax([v for v" not in text and
              'dip[["Coverage", "Prev_Coverage"]].astype(float)' not in text,
              f"{name} no longer holds the raising form")

    print()
    if FAILED:
        print(f"{len(FAILED)} CHECK(S) FAILED")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
