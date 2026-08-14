# GTS pipeline — static review notes (2026-07-28)

Shell sandbox was unavailable, so this is a code review, not a run. Signatures in
both runners match the stage modules — nothing fails on arity in either mode.

## A. run_pipeline.py vs checkpoint_runner.py divergences

**A1. `--geojson` and `--skip-merge` exist only in run_pipeline.py.**
Passing either to `checkpoint_runner.py` is an argparse error, and checkpoint runs
never emit the merged-tracks GeoJSON. README line 76 says "same arguments" — no
longer true.

**A2. `--mopup` is accepted by checkpoint_runner but never used.**
`step_dip`/`step_join` ignore it. Separately, `stage2_analysis.run_analysis`
lines 333–336: the `is_mop_up` branch and the `else` branch do the same
replacement, so the flag is a no-op there too. Mop-up runs currently behave
identically to normal runs in *both* paths. Confirm intent before documenting.

**A3. State-filter robustness differs.**
`run_analysis` L281: `dip[state_col].str.strip()`
`step_dip` L225: `dip[state_col].astype(str).str.strip()`
A multi-state settlement list with a non-object state column crashes
run_pipeline but not checkpoint_runner.

**A4. Track-column tolerance differs.**
`step_scan` L113 uses a hard `usecols` list; `stage3.load_tracks_teams` L37 uses
`usecols=lambda c: c in cols`. A track export missing one of those columns gives
a ValueError at read time in checkpoint mode, and a later KeyError in
run_pipeline mode — different failure points on identical input.

## B. run_pipeline.py scans merged_tracks.csv twice

`build_workbook()` (stage3 L205) calls `load_tracks_teams(tracks_path)`
internally; run_pipeline L101 then calls it again to build the deploy/time CSVs.
That's a second full pass over a multi-million-row file.

checkpoint_runner already avoids this — `step_erm` passes `deploy_csv=`/`time_csv=`
so `build_workbook_from_agg` writes them from the agg it already has (stage3
L253–256). Fix: in run_pipeline, call `load_tracks_teams` once and go straight to
`build_workbook_from_agg(..., deploy_csv=..., time_csv=...)`.

## C. stage2_analysis correctness

**C1. `classify_coverage` (L195) — 0.4% coverage reports as "Fully Covered".**
Raw coverage in (0, 0.005): `is_empty()` is False, `round(c, 2)` -> 0.0, no range
matches, and the function falls through to `return "Fully Covered"` (L205).
Fix: make the fallthrough conditional on `coverage >= 0.70`.

**C2. `classify_time_spent` (L208) — gaps in the range table.**
Ranges `(1,12), (13,30), (31,60), (61,120)` are inclusive both ends, so 12–13,
30–31 and 60–61 are uncovered. Masked today only because `mins = track_count * 2`
is always even. Change the ping-to-minute factor and rows silently jump to
">2 hrs". Also `mins == 12` is labelled "<12 mins".

**C3. Dead code, L291–292:** `for layer in ("ta", "voronoi"): pass`

## D. checkpoint_runner crash recovery

**D1. `step_merge` can duplicate rows on non-graceful resume.**
Rows are appended as each source file is read, but the file is added to
`done_files` only after it completes (L90). A kill mid-file — as opposed to the
graceful `TIME_BUDGET` break — leaves partial rows on disk and re-appends the
whole file on resume, inflating `track_count` and therefore coverage. Safer:
write per-source temp files and concat on completion, or checkpoint the byte
offset.

## E. README gaps (post-campaign work is undocumented)

Nothing in the current README covers the post-campaign path:

- `--analysis-type daily|post_campaign` — not mentioned
- `--teams-deployed N` — not mentioned
- `stage7_pir_report.py` — missing from the stage table (§"What each stage does")
- Missing from §Outputs:
  - `{State}_Post_Implementation_Report.docx`
  - `{State}_Day_{N}_PostCampaign_ERM_Analysis.xlsx`
  - `charts/missed_children.png`
- `--pptx-template` is silently ignored under `post_campaign` in both runners
  (run_pipeline L150, `step_pptx_report` L415) — worth stating explicitly
- Line 76's "same arguments" claim needs the A1 caveat

Cosmetic: the post-campaign workbook filename still embeds `Day_{N}`
(run_pipeline L94–95) for what is a whole-campaign artifact.
