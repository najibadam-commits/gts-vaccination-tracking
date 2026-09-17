# GTS Vaccination Tracking — Automated Daily Pipeline

Automates the full daily workflow: **merge tracks → visitation analysis → ERM workbook → coverage maps → draft report**.

One pipeline, two analysis types — pick at the start of the run (`--analysis-type` on the command line, or step 0️⃣ in the web app):

- **Daily ERM Analysis** (default) — today's numbers for the evening review meeting: Daily ERM Analysis workbook tab, the Daily Tracking Report `.docx`, and (optionally) your organization's PPTX report template.
- **Post-Campaign Analysis** — whole-campaign numbers for the Post-Implementation Report: a Post-Campaign Analysis workbook tab and a Post-Implementation Report (PIR) **`.pptx`**, built by filling the organization's own **"Post Implementation Report" deck** — the report the team presents to stakeholders. The pipeline picks that deck up automatically from alongside the pipeline; no manual selection is needed (pass `--pir-template`/upload a different `.pptx` in the web app's step 6️⃣ to override). Point the campaign day at the FINAL day (chaining prior days via `--prev-dip`) and, ideally, supply all campaign days' tracks so team deployment and time-spent reflect the whole campaign, not just one day. The organization PPTX template is not used for this analysis type — only the daily path builds a PPTX.

Both analysis types read the same input datasets (tracks, settlements, target areas, boundaries); only the processing/aggregation and the final report differ.

## What each stage does

| Stage | Script | Replaces |
|---|---|---|
| 1 | `stage1_merge.py` | Manually merging the multi-million-row `Tracks_*.csv` exports into one file |
| 2 | `stage2_analysis.py` | The Jupyter notebook visitation/coverage analysis (`{State}_day_{N}_settlement_visitation.csv`) |
| 3 | `stage3_erm_workbook.py` | Team Deploy Report, Time Spent Analysis (8am–3pm, **scoped to the day the tracks were transmitted**), the **Team performance / time-spent-range** breakdown (`team_time_range.py` — State/LGA/Ward tabs plus the under-12-minutes follow-up list) and the ERM workbook with charts — writes a **Daily ERM Analysis** tab or a **Post-Campaign Analysis** tab (settlement coverage, coordinates coverage, not-visited-by-LGA ranking, potentially missed children) depending on `--analysis-type` |
| 4 | `stage4_maps.py` | QGIS map production — statewide, per-LGA and state implementation maps styled after the QGIS templates. **Per-LGA maps are now two panels side by side on one page** (Visitation Status from the settlement extents, Visitation Coverage from the gridded target areas) — see "Side-by-side LGA maps" below. Statewide and implementation maps keep the standardized single-panel layout: title top-center, north arrow top-right, eHA logo bottom-right, legend outside the map (bottom-left). Ward boundaries + large bold labels on LGA maps, large LGA labels statewide, and the state implementation map zoomed in so the focal state fills most of the frame |
| 4b | `stage_charts.py` | Report charts styled after the ERM samples: GTS Trackers bar, coverage donut with centre total, LGA 100% stacked bar, cumulative range stacked bar, time-spent bar, team time-range bar, and the **estimated <5 population / household reached-vs-not-reached donuts plus per-LGA bar** (post-campaign adds a missed-children chart) |
| 5 | `stage5_report.py` | **Daily ERM Analysis only** — Daily Tracking Report draft (.docx) with charts, tables, maps and auto narration — you add photos and challenges |
| 6 | `stage6_pptx_report.py` | **Daily ERM Analysis only** — populates your **organization's own PPTX report template** with this day's numbers, charts and maps — same slides, same branding, ready to present |
| 7 | `stage7_pir_report.py` | **Post-Campaign Analysis only** — the Post-Implementation Report **deck** (.pptx), built by filling the **"Post Implementation Report" sample** slide by slide (auto-detected next to the pipeline via `find_default_pir_template`, or pass `--pir-template`). Sections follow the sample: background, state settlement coverage (donut + LGA coverage mix), cumulative settlements coverage, potentially missed children, settlements without geo-coordinates, statewide coverage map, one slide per LGA (donut + per-ward coverage bar), settlement coverage examples, data-driven challenges and recommendations, photo placeholders. The sample's **Team Deployed/Devices** slide is dropped — see "What the final report leaves out" below. Indicators come from `pir_analysis.py`, charts from `pir_charts.py`, example figures from `pir_examples.py` |

### Team deployment (both analysis types)

Enter the **total teams deployed** for the campaign (`--teams-deployed N`, or the "Total teams deployed" field in the web app). Combined with the team codes seen in the field tracking data, the pipeline derives:

- **Teams deployed** — your entered total (falls back to the count of unique team codes in the planned-settlement list if left blank)
- **Teams reported** — teams whose tracks were actually received
- **Teams pending** — deployed − reported (never negative)
- **Reporting %** — reported / deployed

These appear in the Team Deploy-Report table/chart in both workbook tabs, and in the Team Deployment section of both the Daily Tracking Report and the Post-Implementation Report.

## Web app

```bash
pip install -r requirements.txt
streamlit run app.py
```

Opens a browser page at `http://localhost:8501`. Workflow:

0. Choose the **analysis type** — Daily ERM Analysis or Post-Campaign Analysis. This only changes the processing/outputs further down; every other input step is identical for both.
1. Upload the **planned settlements** CSV — the app auto-detects the state, LGAs and wards and shows them for confirmation (a dropdown appears if the file covers several states).
2. Upload the day's **track exports** (multiple files at once) — for Post-Campaign Analysis, upload all campaign days' tracks here if you have them, so team deployment and time-spent reflect the whole campaign.
3. Upload the **gridded** and **voronoi** target areas.
4. **Daily ERM Analysis** — set the campaign day (attach the previous day's visitation CSV for day 2+, tick mop-up if applicable) and enter the **total teams deployed**, which drives Teams Reported/Pending/Reporting % throughout; leave at 0 to fall back to the settlement list's own team-code count. **Post-Campaign Analysis** — neither is asked for (see "What the final report leaves out"); the step becomes a single optional upload for the previous run's visitation CSV, needed only if you do not have every campaign day's tracks.
5. Optionally upload a **State PHC logo** — since the campaign runs in a different state each time, this is separate from the fixed organization logo and is placed on the title page/slide.
6. **Daily ERM Analysis** — optionally point to your **organization's .pptx report template**. **Post-Campaign Analysis** — the "Post Implementation Report" deck found beside the app is shown and applied automatically. Either way, type the **campaign title** (e.g. "Polio SIA April 2026") — this appears on the report's title page/slide.
7. Click **Run Analysis**, then download the report (the Daily Tracking Report draft `.docx`, or the Post-Implementation Report `.pptx` for Post-Campaign Analysis), the PPTX report in your org template (daily only, if provided), the ERM workbook, visitation CSV, and a zip of all maps + charts; preview any map or chart in the page.

### Editing pipeline code while the app is running

The apps import every pipeline stage at **module scope**, not lazily inside the
run handler. Streamlit's file watcher only tracks modules that are imported
when the script is first executed, so a lazily-imported stage stays cached in
`sys.modules` for the life of the server: edit `stage_charts.py` while the app
is running and the app keeps calling the old code. That surfaces as a puzzling
signature mismatch — the reloaded `app.py` passing a new argument to a stale
function — rather than an obvious "restart me".

With module-scope imports, saving any pipeline file makes Streamlit offer
**Rerun** and pick up the change. The cost is a slower first page load, since
geopandas and matplotlib now import at startup instead of on the first run.

Restarting still fixes anything odd, and `__pycache__` never needs clearing —
Python compares each source file's timestamp against its `.pyc` and recompiles
on its own.

### Alternative interface (optional)

A redesigned tabbed interface is available at `app_modern_ui.py` (`streamlit run app_modern_ui.py`). It reads the same inputs and calls the same pipeline stages — only the presentation differs. It depends on `gts_theme.py`; neither file is used by `app.py`.

Boundary layers (`LGA.sqlite`, `Ward.sqlite`, `state.sqlite`), `eha_logo.png`, and any `*template*.pptx` file are read automatically from the folder above the app. Upload size is configured up to 4 GB in `.streamlit/config.toml`. To let colleagues on your office network use it, run `streamlit run app.py --server.address 0.0.0.0` and share your machine's IP.

## Daily routine (in Cowork)

1. Drop the day's `Tracks_*.csv` exports into a folder (e.g. `Day2_tracks/`).
2. Tell Claude: *"Run the GTS pipeline for Zamfara day 2, tracks in Day2_tracks"*.
3. Collect outputs from the day's output folder.

## Running on your own PC (one command)

```bash
python gts_pipeline/run_pipeline.py ^
    --tracks-folder "Day1_tracks" ^
    --settlements settlement.csv ^
    --gridded-ta gridded_ta.sqlite ^
    --voronoi voronoi_ta.sqlite ^
    --lga-boundaries LGA.sqlite ^
    --wards Ward.sqlite ^
    --states state.sqlite ^
    --state Zamfara --day 1 ^
    --output Day1_outputs --logo eha_logo.png
```

For **day 2 onward** (cumulative visitation), add:

```bash
    --prev-dip Day1_outputs/Zamfara_day_1_settlement_visitation.csv
```

For **mop-up days**, add `--mopup`. To also export a GeoJSON of merged tracks for QGIS, add `--geojson`.

To also build the report in your **organization's PPTX template**, add:

```bash
    --pptx-template ../eHA_Revised_reporting_template.pptx --campaign-name "Polio SIA April 2026"
```

`--campaign-name` is shown on both the .docx title page and the PPTX title slide. Add `--state-logo state_phc_logo.png` to place the campaign's State PHC logo on the title page/slide (separate from `--logo`, the fixed organization logo used on maps and elsewhere).

To run the **Post-Campaign Analysis** instead of the Daily ERM Analysis, add `--analysis-type post_campaign` and `--teams-deployed N`:

```bash
    --analysis-type post_campaign --teams-deployed 240
```

Point `--day`/`--prev-dip` at the campaign's FINAL day (chained through prior days as usual) and, ideally, pass all campaign days' tracks in `--tracks-folder`. This builds a Post-Campaign Analysis workbook tab and a Post-Implementation Report `.pptx` instead of the daily workbook tab, `.docx` report and PPTX — `--pptx-template` is ignored under `post_campaign`. `--teams-deployed` still feeds the workbook's Team Deploy-Report tab under `post_campaign`, but nothing in the PIR reads it. `--teams-deployed` also works with `--analysis-type daily` (the default), where it overrides the settlement-list-derived total for that single day.

The PIR's template is picked up automatically — the pipeline looks next to the script for a `.pptx` named like **"Post Implementation Report ..."** and fills it, no flag needed; where several are present the newest wins, so a "... 2027.pptx" supersedes "... 2026.pptx" with no code change. Pass `--pir-template path/to/deck.pptx` to point at a different one. It **must be a .pptx**: stage 7 builds the report by filling that deck, so a `.pdf` or `.docx` is rejected rather than silently ignored.

Requirements: `pip install pandas geopandas pyogrio xlsxwriter python-docx python-pptx pillow matplotlib`
(optional: `pyarrow` for faster intermediate files).

`checkpoint_runner.py` is a resumable version of the same pipeline and accepts the same `--analysis-type`/`--teams-deployed` flags, plus `--ckpt` for a checkpoint folder. If a run is interrupted at any point, re-running continues where it stopped. Two arguments exist only in `run_pipeline.py`: `--geojson` (also export merged tracks as GeoJSON) and `--skip-merge` (reuse an existing `merged_tracks.csv`) — `checkpoint_runner.py` has its own resumable merge step instead and doesn't need either.

## Inputs

- **Tracks** — GTS export chunks (`Tracks_0.csv`, `Tracks_1.csv`, …). Rows with broken Team Code commas, invalid or (0,0) coordinates are repaired/removed automatically.
- **Settlements (DIP)** — planned settlement list. If it covers several states it is filtered to `--state`. A `unique` key column is used if present; otherwise one is built from State_LGA_Ward_Settlement (matching works best when both DIP and TA carry the same precomputed `unique` column, as in your production files).
- **Gridded TA** and **Voronoi TA** — sqlite/gpkg/geojson accepted.
- **LGA boundaries** — used for maps; filtered to the campaign LGAs automatically.

## Outputs

Common to both analysis types, per day:

- `merged_tracks.csv` — all cleaned track points in one file
- `{State}_day_{N}_settlement_visitation.csv` — visitation, coverage %, coverage class, time-spent class per settlement
- `visited_day_{N}.geojson` / `not_visited_day_{N}.geojson` — map-ready layers (also load directly in QGIS)
- `maps/`, `charts/` — statewide + per-LGA coverage PNGs and report charts
- `team_deploy_day_{N}.csv` / `time_spent_day_{N}.csv` — Team Deploy-Report and Time Spent Analysis data behind the workbook/report tables and charts
- `flagged_teams_day_{N}.csv` — every team flagged for supervisory follow-up against the 08:00-16:00 operational window (State, LGA, Ward, Team, Team Date, Start/End Time, Total Recorded/Effective Operational Duration, Team Time Efficiency %, Flag/Status, Reason for Flagging), same rows as the workbook's **Flagged Teams** tab

**Daily ERM Analysis** (`--analysis-type daily`, the default):

- `{State}_Day_{N}_Vaccination_Tracking_ERM_Analysis.xlsx` — Team Deploy-Report, Time Spent Analysis, the Team Range (State/LGA/Ward) and Teams Under 12 Mins tabs, the Tracking Sessions tabs (`Tracking Sessions`, `Session Data (Team)`, `Session Data (Device)`), the Target Pop & HH Coverage tab, and the Daily ERM Analysis tab, with native Excel charts
- `charts/team_time_range.png` — teams per time-spent band, used on the Time Spent Analysis slide
- `charts/target_children_donut.png`, `charts/target_households_donut.png`, `charts/target_coverage_by_lga.png` — used on the Target Population and Household Coverage Analysis slide
- `{State}_Day_{N}_Daily_Tracking_Report_DRAFT.docx` — report draft with tables and all maps embedded
- `{State}_Day_{N}_Report.pptx` — same report in your organization's PPTX template, if `--pptx-template` was given

**Post-Campaign Analysis** (`--analysis-type post_campaign`):

- `{State}_PostCampaign_ERM_Analysis.xlsx` — Team Deploy-Report, Time Spent Analysis and Post-Campaign Analysis tabs (adds settlements with/without geo-coordinates, not-visited-by-LGA ranking, potentially missed children)
- `{State}_Post_Implementation_Report.pptx` — the Post-Implementation Report deck, built by filling the **"Post Implementation Report"** sample (`--pir-template` to override; the web app auto-detects and shows the file it found in step 6️⃣)
- `charts/pir/` — every figure on that deck: the deployment bar, the state coverage donut and LGA coverage mix, the cumulative coverage bar, the not-visited and missed-children bars, the geo-coordinate coverage bar, one donut and one per-ward bar per LGA, and the four settlement coverage examples
- `charts/missed_children.png` — additional chart used only in this report type
- No PPTX is produced for this analysis type.

## Organization PPTX template

`stage6_pptx_report.py` fills in the eHealth Africa "GTS Tracking Report" deck (title, background, team
deployment, time spent, daily & cumulative coverage, statewide + per-LGA coverage maps) with this day's
numbers and images, matching the template's own layout and branding. The template itself has no dedicated
Time Spent Analysis slide, so one is created at build time by duplicating the Team Deployment slide's
layout (title, narrative, one chart) and inserting it right after — filled with the time-spent bar chart
and a compliance narrative (teams that spent 1hr+ in the field vs. teams with no evidence of tracks). The
template's two separate cumulative-coverage slides (donut, then LGA breakdown bar) are merged into one
"Cumulative Coverage" slide with both charts side by side, using the same two-chart layout as the State
Daily Coverage slide — one less slide to click through, easier to compare the two views at a glance. It
automatically adds or removes LGA map slides to match the campaign's actual LGA count, and clears the
challenges/photos slides to blank placeholders for you to fill in by hand. The supervision photo slide is
retitled **"ERM & Supportive Supervision Pictures"** — matched on the template's own wording rather than a
slide number, since that number moves with the campaign's LGA count. A **Target Population and
Household Coverage Analysis** slide is inserted after the cumulative-coverage slides (see that section
above). The day-over-day settlement comparison table (template slide 7) isn't auto-filled yet and is
dropped from the generated deck — let me know if you'd like that added next.

The template is **`eHA_Revised_reporting_template.pptx`** — the deck the team approved — and stage 6 now
follows it exactly: every slide it fills already exists in the template, in the template's order. Nothing is
inserted or merged at build time. Deck order: title, background, team deployment, time spent, **team time
efficiency**, state daily coverage, state cumulative coverage, cumulative settlements coverage, **target
population & household coverage**, statewide map, one slide per LGA, challenges, photos, thank-you.

Two slides are conditional: **Team Time Efficiency** is deleted when there is no usable merged track export
(no clock start/end times to measure against the operational window) and no `team_efficiency.png` already
rendered; **Target Population Coverage Analysis** is deleted when the settlement list carries neither the
population estimates it needs.

Slides are addressed by POSITION, so a template whose order differs does not raise an error — it produces a
deck that opens cleanly and is wrong from slide 4 down. That is what happened while the app was still
picking up the superseded `organization's reporting template.pptx`. If the template is revised again,
re-verify the `SLIDE_*` constants at the top of `stage6_pptx_report.py` against the new file BEFORE the next
campaign, and run `python test_revised_template_slide_map.py`, which asserts the built deck's slide order and
titles.

## Post-Implementation Report

The PIR is the whole-campaign report the team presents to stakeholders. It is
produced by **filling the organization's own "Post Implementation Report" deck**
rather than by generating a document from scratch, so what comes out of the
pipeline is the format the audience already knows. Three modules do the work,
and they are separate on purpose — the reporting logic can be revised without
touching the daily path:

| Module | Job |
|---|---|
| `pir_analysis.py` | every indicator, computed once. No drawing, no .pptx |
| `pir_charts.py` | the eight chart types the sample uses, and nothing else |
| `pir_examples.py` | the four Settlement Coverage Example figures |
| `stage7_pir_report.py` | fills the template deck and places the results |

### The identity every figure is derived from

The sample's own arithmetic is the specification:

```
planned settlements = Fully + Partially + Low + Very Low + No Coverage
visited settlements = planned − No Coverage
```

so on the deck a settlement is "visited" exactly when its coverage class is
anything other than "No Coverage", and the donut's slices add up to the planned
total. Stage 2 writes two things that can disagree about that: the grid-cell
class in `Settlement Coverage`, and `Visited`/`Not Visited` in `day_{N}_cumm`.
They part company for a settlement that has tracks but no polygon in the gridded
target-area layer — pings, therefore reached, but no measurable coverage, so it
classifies as "No Coverage".

`pir_analysis.reconciled_classes` resolves it the way `stage_charts
.cumulative_coverage_stats` already does for the daily deck: reached-but-not-
measurable moves into **Low Coverage**. Every PIR figure comes off that one
reconciled Series, at state, LGA and ward level, so the donut, the bars and the
narration cannot disagree. `test_pir_report.py` asserts the identity directly.

Everything is **cumulative** — Day 1 through the final day. The daily columns
are not read. Point `--day`/`--prev-dip` at the campaign's final day.

### What the final report leaves out

**Team deployment and the campaign day are implementation measures, not
reporting ones.** Teams deployed, teams reported, teams pending and reporting %
tell the LGA team who to chase *today* — that is the job of the Daily ERM
Analysis, and stage 6's deck and the workbook's Team Deploy-Report tab keep them
in full. In a report written after the campaign has ended there is nobody left
to chase, and the figure adds a number the reader cannot act on. So:

- the sample's **Team Deployed/Devices** slide is removed from the generated
  deck, not filled;
- no team count appears anywhere else in it (the Background bullet is gone, and
  the Challenges narrative no longer opens on teams that returned no tracks);
- the app's **Post-Campaign** path stops asking for the campaign day, the mop-up
  flag and the total teams deployed. Stage 2 still needs a day number internally
  — it names its cumulative column `day_{N}_cumm` and chains runs on it — so the
  app reads that number back off the previous run's visitation CSV
  (`day_from_prev_visitation`) instead of asking. No prior CSV means day 1,
  which is what a single whole-campaign run should be.

The regression test asserts the slide is gone AND that no deployment wording
survives anywhere in the deck, since a slide can be removed while its numbers
leak into a narration.

Field time is treated differently and is kept: teams that transmitted no usable
track points at all is a **tracking-quality** finding, and a final report should
say when part of the campaign could not be verified.

### Every LGA chart is the same chart

Each LGA- and ward-level figure in the report is a **stacked composition**:
categories in one order, best class at the bottom, the shortfall on top, counts
printed inside each segment, counts too small to fit lifted above the whole bar
in the segment's own colour, legend above the plot. `pir_charts._stacked` is the
only implementation; the callers differ only in categories, palette and y scale.

Two were previously plain single-series columns — settlements not visited per
LGA, and potentially missed children per LGA. They are stacked against their own
whole now, which is both consistent and more honest: 817 not-visited settlements
reads very differently in an LGA of 900 than in one of 1,000, and a plain column
could not show that. **The reported figures are unchanged** — the not-visited and
missed-children counts are the same numbers, still labelled on their own segment,
and the slide's "N =" totals still come from `not_visited_by_lga` and
`missed_children_by_lga`. What is added is the part they came out of.

The stack also ran **upside down** against the sample until this pass — No
Coverage at the bottom, Fully Covered on top — while the legend read correctly,
which is exactly why it survived review. `test_pir_report.py` now reads the bar
bottoms back out of a render and asserts the direction.

### The implementation map

Styled to the reference sheet. The change worth calling out is that **the
shading marks what is NOT in the campaign**: implementing LGAs are plain white,
and the LGAs left out are hatched pink, so the gap is what stands out. This
module used to do the reverse — shading the implementing LGAs and leaving the
excluded ones white, with the legend labelling the pink "Implementing LGAs" — so
a reader scanning for coverage saw the campaign as the exception rather than the
rule.

Also from the reference: adjoining states are labelled in Title Case, bold and
**underlined** (the underline is what distinguishes a state from an LGA at a
glance — matplotlib has no underline property, so `_underline_labels` measures
the rendered text and draws a rule under it); LGA names are dark olive; the
adjoining states are one flat grey with their internal divisions undrawn, and
the translucent wash that used to sit over them is gone; and a thin rule frames
the drawn area.

On the **per-LGA** maps the focal LGA is now named as well as outlined, at
1.65x the surrounding LGAs' label size and in black — the heavy outline alone
says "something here is different", not "THIS one".

Legend wording is matched by role, and "Non-Implementing" is tested before
"Implementing" — it contains it, so the looser test would otherwise claim the
shaded entry and give it the white swatch.

### Where the generated deck deliberately differs from the sample

Four places, each because following the sample exactly would produce something
either wrong or unbuildable:

- **Coverage classes are consistent across every chart.** The sample's state-level
  LGA bar carries a four-entry legend that colours "Very Low Coverage" salmon,
  while its donut and its ward-level bars use salmon for "No Coverage" — the
  same colour meaning two different things two charts apart. The generated deck
  uses the five classes and one palette everywhere.
- **The device-mix bars need input.** The sample charts Total Planned Teams
  against eHA Phones and Vaccine Buddy. The GTS export records a device
  identifier but not a device *type*, so that split cannot be derived. Supply it
  with `--eha-phones`/`--vaccine-buddy` (or the app's fields); without it the
  slide charts teams reported against teams pending and says on the slide that
  it has done so.
- **The coverage examples are real settlements from this campaign.** The
  sample's four example screenshots are hand-made in QGIS over satellite
  imagery, and are from a different state entirely (Gombe) — generic
  illustrations. `pir_examples.py` picks one real settlement per coverage class
  from the current run instead and draws its extent, its target-area grid, the
  cells the tracks reached and the pings themselves. There is no satellite base:
  the pipeline fetches no map tiles, and a report that fails on an offline
  machine is worse than one drawn on a plain ground. Selection is deterministic
  (the settlement closest to the median cell count for its class), so two runs
  on the same data produce the same deck.
- **Challenges and Recommendation are a data-driven draft, not prose.** The
  sample's challenges are field-operations findings — trackers returned late,
  weak network, devices not deployed in hard-to-reach wards — which no dataset
  here records. What the data does show is *where tracking is absent*: teams
  that returned nothing, wards with no tracks at all, settlements never reached,
  settlements with no coordinates to track. Each item states the observation and
  leaves the operational cause to the analyst; bracketed prompts mark what still
  needs a human.

### Layout

Slides are located by **title**, never by slide number — twice: once on the
unpacked package for the structural edits, and again on the loaded deck for the
content edits. The second pass is not redundant. python-pptx renumbers slide
parts when it opens a package, so a part-filename map built before the load
points at the wrong slides afterwards; that is precisely how an early build came
out with eleven LGA slides still carrying the sample's own Akwanga chart.

Each content slide is cleared of the sample's pictures and text and rebuilt from
the `BOX_*` constants at the top of `stage7_pir_report.py`, rather than reusing
the sample's own shape positions (which were nudged by hand per slide — its
twelve LGA slides have twelve slightly different donut boxes). Charts are
contain-fitted into those boxes and **sized to each box's aspect ratio**, so a
sixteen-ward bar chart fills its half-slide instead of being letterboxed into an
unreadable sliver. `_overlap_report` runs at the end of every build and prints
any colliding pair; the regression test asserts there are none.

The LGA block grows and shrinks with the campaign: the template's LGA slides are
reduced to one, which is then duplicated once per implementing LGA.

### File size

The sample deck is 30 MB, and about 25 MB of that is the charts, maps and
photographs of the run it was exported from. Stage 7 replaces every one of them,
but deleting a picture SHAPE leaves the image itself in the package — so without
intervention the generated report would inherit all 25 MB, unreachable and
carried in every copy anyone emails. `pptx_xml_utils.prune_unused_media` runs at
the end of each build and drops the orphans; the run log says how many and how
much. Branding referenced from a master, layout or theme is never touched.

### No day anywhere in the final report

The campaign day exists on the post-campaign path for exactly one reason: stage 2
names its cumulative column `day_{N}_cumm` and chains a run onto the previous one
by that number. That is an internal key. It should not reach anything a reader
sees, and it was reaching four of them:

| Where | Daily run | Post-campaign run |
|---|---|---|
| Output folder | `Kwara_Day1/` | `Kwara_PostCampaign/` |
| Visitation CSV | `Kwara_day_1_settlement_visitation.csv` | `Kwara_postcampaign_settlement_visitation.csv` |
| Maps, charts, geojson, gridded TA | `..._day_1.png` | `..._postcampaign.png` |
| **Map titles, baked into the PNG** | `— Day 1` | `— {campaign name}` |

That last row is the one that mattered most and the one a text check on the deck
could never catch: the statewide coverage map is embedded on the report's Coverage
Map slide, and its title is drawn into the image. A whole-campaign report was
showing a map captioned "Day 1".

`stage2_analysis.output_tag(day, analysis_type)` is the single source of the
filename fragment, and `generate_maps(..., tag=, title_scope=)` carries it into
stage 4. **Both default to the day**, so the daily ERM path is byte-for-byte
unchanged — verified by `test_revised_template_slide_map.py`. Column names are
deliberately untouched.

### Downloading the report

The result page offers the report FIRST, then the workbook, the visitation CSV
and the maps+charts zip. Which file is "the report" depends on the analysis
type, and that is worth stating because getting it wrong is silent:

| Analysis type | Report offered | Written by |
|---|---|---|
| Daily ERM Analysis | `{State}_Day_{N}_Report.pptx` | stage 6, from the org template |
| Post-Campaign Analysis | `{State}_Post_Implementation_Report.pptx` | stage 7, from the PIR template |

The daily `.docx` draft from stage 5 is still written to the output folder but
deliberately not offered — the branded deck is what goes out.

The download button used to be gated on the results dict's `pptx` key, which
only stage 6 sets. Stage 7 puts the PIR under `report`, so a finished
post-campaign run built the deck and then showed no way to download it.
`test_app_gallery.py` now asserts that both analysis types offer a presentable
deck.

A post-campaign run is also labelled by SCOPE rather than by day throughout the
result page — "Whole campaign" instead of "Day 1", and
`{State}_PostCampaign_maps_charts.zip` instead of `{State}_Day1_...`. The day
number on that path exists only so stage 2 can name its cumulative column; it
means nothing to a reader.

### The charts folder has a subfolder now

Stage 7 writes the PIR's figures to `charts/pir/`. The app's result page used to
list the charts directory flat and hand every entry to `st.image`, which meant a
post-campaign run ended in

```
MediaFileStorageError: Error opening '...\charts\pir'
```

— Streamlit trying to read a directory as an image. The same flat listing also
wrote a bare directory entry into the maps+charts zip and left the PIR figures
out of the download. Both UIs now share `image_files()` and `zip_tree()`, which
filter to real image files and walk subfolders; the PIR figures get their own
browsable section under the Charts tab rather than being stacked with the daily
charts, since a post-campaign run produces two per LGA. `test_app_gallery.py`
covers both.

Anything else that adds a subfolder under `charts/` or `maps/` is now safe.

### Testing it

```bash
python test_pir_report.py                       # finds the deck beside the pipeline
python test_pir_report.py --template deck.pptx  # or point at one
```

It checks the section order, that one LGA slide exists per LGA, that **none of
the sample's own content survives** (no "Nasarawa", no "Akwanga", none of its
figures), that the coverage identity holds at state and LGA level, that nothing
overlaps, and that changing the input data changes the deck.

## Daily vs cumulative — the rule

**Daily = the reporting day only. Cumulative = Day 1 through the reporting
day.** The two are never mixed in one figure, and where both appear together
the wording says which is which.

Stage 2 writes a column for each, per settlement:

| Column | Scope | Written from |
|---|---|---|
| `day_{N}_daily` | that day alone | this run's tracks only (`cumm` on the grid) |
| `Daily Coverage` | that day alone | today's visited cells / total cells |
| `Daily Settlement Coverage` | that day alone | `Daily Coverage` classified |
| `day_{N}_cumm` | Day 1 → day N | today's tracks plus every prior day carried forward |
| `Coverage` | Day 1 → day N | max(today, previous) |
| `Settlement Coverage` | Day 1 → day N | `Coverage` classified |
| `Track Evidence` | Day 1 → day N | Yes once tracks have been seen in the settlement on any day |

### The reporting day is a transmission date, not a folder

Stage 2 resolves the day's tracks to ONE local transmission date and counts
only those pings (`--analysis-type daily`, the default). This is what makes
`track_count` — and therefore every per-team minutes figure derived from it,
including the Time Spent Range chart on the Time Spent Analysis slide —
describe a single day. Handed a merged export covering several days, an
unscoped run reports the campaign to date as one day's field time.

The date is chosen by ping count, not by taking the maximum: a date must carry
at least **5%** of the busiest date's pings to count as a day of fieldwork
(`MIN_DATE_PING_SHARE`). Trackers left switched on overnight, and devices with a
skewed clock, put a thin trickle of pings on the following date; taking the
maximum blindly would scope the whole analysis to a handful of pings and report
near-zero coverage for a day that went fine. The run log prints the ping count
per date and names any trailing date it ignored.

Local time is UTC+1, and the offset is applied to the whole timestamp rather
than the hour alone, so a 23:30 UTC ping belongs to the next local date instead
of wrapping to hour 0 on the wrong day.

**Cumulative figures are not narrowed by this.** When the export spans several
dates, stage 2 makes a second pass over all of them purely to record which
settlements have evidence of tracks anywhere in the period. Daily figures
narrow to the reporting date; cumulative coverage does not. This is what keeps
the common case honest — a later day re-run with the whole campaign's tracks in
the folder and no previous day's CSV to carry forward.

`--analysis-type post_campaign` passes `track_date=None` and spans everything,
which is the point of that mode.

### Both sides of the daily ratio are day-scoped

The daily figure is **settlements visited today ÷ settlements planned for
today**. The denominator comes from the settlement list's own planning flag
(`day1`, `day2`, …) via `stage2_analysis.day_scope_mask` — the pipeline's own
`day_{N}` activity column is deliberately ignored, since it is written "Yes"
for every row and would select the whole list.

A settlement is in the day's denominator **only when its day flag reads "Yes"
or "Y"** (case-insensitive). This is an inclusion test: an earlier version
excluded a handful of known falsy strings instead, so any other value — a date,
a team code, a stray character — silently counted as planned and inflated the
denominator with settlements nobody was sent to.

**Cumulative coverage is never day-scoped.** It runs against every planned
settlement in the list, whichever day each was scheduled for. Measured against the whole
campaign list instead, a normal day reads as a catastrophic one: the teams were
never sent to the other settlements.

The same helper scopes the **daily donut and the daily LGA bar**, so the chart
and the sentence above it can never quote different denominators. Where the
settlement list carries no flag for that day, the full planned list is used and
the run log says so.

### Cumulative counts evidence of tracks, whatever day was planned

A planned settlement counts as reached cumulatively **once tracks have been
seen in it at any point in the period**, regardless of which day it was
scheduled for. Stage 2 applies this once, at the source: `Track Evidence` is
carried forward from the previous day's CSV, ORed with today's evidence from
either `Grid Cells Visited > 0` (the gridded layer recorded a visit) or
`track_count > 0` (pings fell inside the settlement extent), and `day_{N}_cumm`
is set to `Visited` wherever it is Yes.

The second source matters: not every planned settlement has a polygon in the
gridded layer, and without it a settlement teams demonstrably worked reads as
never visited for the rest of the campaign. Because the rule is applied in
stage 2, every consumer — slides, charts, workbook, maps — agrees on which
settlements have been reached instead of each re-deriving it.

This matters because from Day 2 the pipeline feeds the previous day's
visitation CSV back in as the settlement list. Before these columns existed the
"daily" figures scoped the *denominator* to settlements planned that day but
read the *visited status* from the cumulative column — so a settlement covered
on Day 1 counted as visited again on Day 2, and daily coverage only ever went
up. `stage6_pptx_report._daily_coverage_stats` now reads `day_{N}_daily`, and
warns loudly if it is missing rather than silently falling back.

In the deck:

- **Slide 5 — State Daily Coverage** — the reporting day alone
- **Slide 6 — State Cumulative Coverage** — Day 1 through the reporting day
- Both use the same donut and the same stacked bar (`lga_range_stacked`), with
  identical bands, colours, ordering, labelling and legend. **Only the data
  scope differs**, so the two slides can be read against each other. They are
  one function on purpose — redesigning one alone is not possible.

The stacked bars run **fully covered at the bottom → no coverage at the top**
(`STACK_ORDER`, the reverse of `RANGE_ORDER`), so the bar grows upward out of
the good news and the shortfall sits on top. The **legend keeps `RANGE_ORDER`**
regardless, so the key reads the same as it always has.

## Target Population & Household Coverage

`target_population.py` turns settlement visitation into the two estimates the
planned settlement document carries per settlement:

| Field in the planned settlement document | What it is |
|---|---|
| **Set Population** | estimated under-5 children targeted in that settlement |
| **Number of Households** | estimated households in that settlement |
| **Estimated Targeted Missing Households** | estimated targeted households recorded as missed in that settlement — **optional** |

For the first two it reports the estimated total, how much sits in **visited
(reached)** settlements versus **not-visited (not reached)** ones, and the
percentage each way — plus a per-LGA breakdown.

### Estimated targeted missing households

A **supplied** per-settlement figure, never derived. Where the settlement list
carries a column for it — `Estimated Targeted Missing Households`,
`missing_households`, `missed_households`, `est_missing_hh` and similar all
match — the indicator appears in the narration, the per-LGA table and the
workbook summary. **Where the column is absent the indicator is simply left
out**: nothing is estimated or back-calculated to fill the gap.

Being a shortfall rather than a total, it is not split into reached and not
reached. What is reported is where the shortfall sits: `in_not_visited` is the
part in settlements teams have not reached at all, which is still entirely
outstanding, against `in_visited`, which sits in settlements a team did reach
and may already have been worked through.

Detection order matters and is handled explicitly: the population and household
searches bar `missing`, `missed`, `unreached`, `gap` and `shortfall` as name
segments, because a substring match on "households" would otherwise read
`Estimated Targeted Missing Households` as the settlement's household estimate
and report a shortfall figure as the total.

**The primary analysis is daily**, following the rule above: the slide and the
workbook lead with the reporting day's reach, then state the cumulative
position from Day 1 to date as context. `analyse_daily_and_cumulative()`
returns both from one read so a caller cannot reach for the wrong column. The
daily call keeps the full planned list as its denominator, so the day's reach
is measured against the campaign rather than against itself.

**What "reached" means.** A settlement's full estimate counts as reached if the
settlement was visited at all. This is an estimate of *exposure*, not of
children vaccinated: "teams reached the settlements where this many under-5s
are estimated to live". Partial coverage inside a visited settlement is not
modelled — the settlement's own `Coverage` fraction already carries that, and
combining the two would imply a precision the source estimates don't have.
Every label the pipeline produces says "estimated" for this reason.

**Column detection is tolerant**, because the planned settlement document isn't
under our control. `Set Population`, `set_population`, `set_target`,
`population` and `target` all match for the first indicator;
`Number of Households`, `no_of_households`, `households` and `hh` for the
second. Planning columns like `population by day` are excluded. **Either field
being absent degrades gracefully** — a settlement list with no household column
still produces the under-5 analysis, and the slide re-lays itself for two
panels instead of three. Only when *neither* is present is the section skipped
entirely.

Outputs:

- ERM workbook tab **`Target Pop & HH Coverage`** — totals, reached/not-reached,
  percentages, a 100% stacked chart and the per-LGA table
- `charts/target_children_donut.png`, `charts/target_households_donut.png`,
  `charts/target_coverage_by_lga.png`
- a new PPTX slide (below)

Run standalone:

```bash
python gts_pipeline/target_population.py \
    --visitation-csv Day1_outputs/Zamfara_day_1_settlement_visitation.csv \
    --cum-col day_1_cumm
```

### "Target Population Coverage Analysis" slide

Inserted **after Cumulative Settlements Coverage and before the maps**, so the
deck reads: settlements covered → what that means in children and households →
where. It is cloned from the template's own Team Deployment layout, so the
title style, background, accent bar and branding are the template's, not a new
design. The content band holds the two estimate donuts and the per-LGA
reached/not-reached bar.

The narrative states the **reporting day first**, in the form:

> On Day 2, teams visited settlements with an estimated target population of
> 63,119, of which 46,322 were reached, representing 73.4% coverage.

`visited_total` is the estimated population of the settlements visited that
day; `within_reached` weights each of those by the fraction of its grid
actually covered, so it answers "of the population teams got to, how much did
they work through". A **separate** sentence then gives the cumulative position
from Day 1 to date. The two are never combined into one figure.

Because this slide is inserted, **everything from the statewide map onward
shifts down one index** — handled by `map_offset` in `stage6_pptx_report.py`.
When the settlement list carries neither estimate the slide is not created and
nothing shifts.

**Long titles are auto-fitted.** The template's master sets titles at 28pt,
which wraps this slide's title (and the per-LGA map titles) onto a second line
where it collides with the accent bar. `_set_title` steps the font down just
enough to keep one line, so the wording is preserved and short titles are
untouched. The estimate is deliberately conservative: the deck is a Google
Slides export and its title font is often substituted with a wider one.

## Team Time Efficiency (08:00-16:00 operational window)

`team_efficiency.py` measures each team's **actual GPS-recorded clock time**
against a fixed operational window, 08:00-16:00 local time:

> **Team Time Efficiency = Effective Operational Duration ÷ 480 minutes**

Effective Operational Duration is the minutes of continuous tracking (gaps over
`MAX_GAP_MINUTES` = 15 min excluded — the same rule `team_daily_time.py` uses)
that fall INSIDE the window; time tracked before 08:00 or after 16:00 earns
nothing. A team that starts at 08:00 and tracks solidly to 16:00 scores 100%.

### Two duration figures, not one

`Total Recorded Duration` is the team's whole day, unwindowed. `Effective
Operational Duration` is the same gap-summing, clipped to the window. A team
can log hours of Total Recorded Duration entirely outside the window and still
score 0% efficiency — reporting both numbers is what makes that visible rather
than just asserted.

### Why raw track pings, not the visitation CSV

Start/End time — and therefore everything downstream of it — only exists in
the raw GPS ping stream. The settlement-level visitation CSV carries no
timestamps at all, so this module reads the merged track export directly (the
same file `team_daily_time.py` reads), not the settlement analysis.

### Flags — reasons, not a score

A team is **Flagged** the moment ANY rule below fires, and EVERY rule that
fires is listed, joined with ` + `, so a reader never has to guess which
problem is being reported:

| Reason | Rule |
|---|---|
| Late start | Start Time later than 08:00 + 30 min grace |
| Early stop | End Time earlier than 16:00 − 30 min grace |
| Insufficient operational time | Team Time Efficiency under 50% |
| Excessive idle time | over 60 min of idle time inside the team's own working span |
| Missing start time / Missing end time | team expected (per the settlement list roster) but produced zero pings on the reporting date |
| Invalid timestamp | team present in the export, but none of its timestamps could be parsed |

Missing and Invalid are deliberately distinct: one is "no data at all", the
other is "data present but corrupt" — different problems for a supervisor to
chase.

Thresholds (`LATE_START_GRACE_MINUTES`, `EARLY_STOP_GRACE_MINUTES`,
`MIN_EFFICIENCY_PCT`, `EXCESSIVE_IDLE_MINUTES`) are module constants, also
exposed as `analyse()` keyword arguments — no established value existed for
any of them anywhere in this pipeline before this module, so they are
deliberately easy to retune per campaign without editing code. `MAX_GAP_MINUTES`
is NOT reconfigurable here — it is imported from `team_daily_time.py`, so the
two modules' notion of "was the tracker actually running" cannot diverge.

### The flagged-team follow-up list

Every flagged team, every reason that applies, in the fields a supervisor needs
to act — `State`, `LGA`, `Ward`, `Team`, `Team Date`, `Start Time`, `End Time`,
`Total Recorded Duration (min)`, `Effective Operational Duration (min)`,
`Team Time Efficiency (%)`, `Flag/Status`, `Reason for Flagging`.

- workbook tab **`Flagged Teams`**
- `flagged_teams_day_{N}.csv`, so the list can go to LGA supervisors without
  circulating the whole workbook

All of it comes from one `team_efficiency.analyse` call on the merged track
export, so the chart, table, CSV and narration cannot disagree.
`test_flagged_teams.py` asserts that they don't.

### Superseded design

This module used to cross-reference ping-count-derived field minutes against
grid-weighted settlement coverage (flagged at over 2 hrs with under 50%
coverage, or "stationary" — no more than one grid cell touched). That answered
a different question — was time in the field productive — and worked from the
settlement-level visitation CSV, which carries no clock times at all. It has
been replaced outright by the clock-window metric above, per the decision to
make this module's "Team Time Efficiency" the 08:00-16:00 metric everywhere
it is reported, rather than keeping both analyses alongside each other.

### Why the slide went missing, and why it now can't

The analysis was never broken. It was lost twice, for two different reasons:

1. **The superseded template had no slide for it.** The app was defaulting to
   the 24-slide `organization's reporting template.pptx`, which has no Team Time
   Efficiency slide at all. Fixed by pointing the app at the approved template,
   where it is slide 5.
2. **Stage 6 recomputed it, and deleted the slide when the recompute failed.**
   `team_efficiency.analyse(visitation_csv, "cumulative")` is called TWICE per
   run — once by stage 4 to draw `team_efficiency.png`, and again by stage 6 for
   the narration. The slide was dropped purely on the second call's result, so a
   settlement CSV that satisfied stage 4 but not stage 6, or any transient
   failure, removed a slide whose chart was sitting finished in the charts
   folder.

The slide is now kept whenever there is **either** a recomputed figure **or** a
rendered chart:

| Figures | Chart | Result |
|---|---|---|
| yes | yes | slide with this run's numbers in the narration |
| no | yes | slide **kept**, chart shown, narration states the methodology |
| no | no | slide dropped, with the reason logged |

The middle row matters: it never quotes numbers that were not measured, and it
never leaves the template's own sample figures under this run's chart. The
methodology text names the same window and flag reasons the chart is built on,
so the slide stays true. `test_revised_template_slide_map.py` asserts all
three rows, including that the sample deck's `3,407` never appears.

Outputs — workbook tabs `Team Time Efficiency`, `Flagged Teams`,
`Efficiency by LGA`, `Team Efficiency Data`; chart `charts/team_efficiency.png`;
and the **Team Time Efficiency** slide, inserted after Time Spent Analysis.

Run standalone:

```bash
python gts_pipeline/team_efficiency.py \
    --tracks Day3_outputs/merged_tracks.csv \
    --visitation-csv Day3_outputs/Zamfara_day_3_settlement_visitation.csv \
    --date latest
```

Thresholds (`WINDOW_START_HOUR`/`WINDOW_END_HOUR`, `LATE_START_GRACE_MINUTES`,
`EARLY_STOP_GRACE_MINUTES`, `MIN_EFFICIENCY_PCT`, `EXCESSIVE_IDLE_MINUTES`) are
at the top of the module.

## Team performance & time-spent range

`team_time_range.py` answers "how long did each team actually spend, and which
teams need following up", broken down **State → LGA → Ward** with the team codes
named. It runs inside stage 3 and needs no new inputs.

**There are now two time-spent measures, and they are not the same.** Both are
kept on purpose:

| | Time Spent Analysis (existing) | Team time-spent range (new) |
|---|---|---|
| Unit | per team | per team |
| Source | raw GPS pings, **08:00–15:00 only** | the settlement analysis' own per-settlement ping counts, no time-of-day filter |
| Grain | LGA → team | State → LGA → **Ward** → team, with team codes |
| Extra band | `0 (No evidence of tracks)` — deployed but never seen | — |
| Used for | the headline compliance figures the reports quote | the breakdown tabs and the under-12-minutes follow-up |

The headline "X% of teams spent an hour or more in the field" still comes from
the 08:00–15:00 measure. Everything the new analysis produces is labelled
"across assigned settlements" so the two are not confused in an ERM.

**Counting rule — a team is counted once per level.** A team's band is derived
from its *total* minutes at that level, not per settlement. Re-aggregating at
each level means the State tab's bands sum to the actual team count and
reconcile with Team Deployment. A team working two wards appears once in each
of those wards (which is what a ward report should say) but only once in the
LGA and State totals — so its ward band and its LGA band can legitimately
differ.

One consequence worth knowing: because minutes are totalled, **far fewer teams
land in `<12 mins` than a per-settlement view suggests.** A team with five
settlements at four minutes each totals twenty minutes and lands in
`12 - 30 mins`. That is the intended reading — the band describes the team's
day, not its weakest settlement.

Outputs (all inside the existing ERM workbook):

| Tab | Contents |
|---|---|
| `Team Range (State)` | teams per band for the state, with a column chart — the headline distribution |
| `Team Range (LGA)` | teams per band per LGA |
| `Team Range (Ward)` | teams per band per ward, **with the team codes listed** |
| `Teams Under 12 Mins` | wards ranked by how many teams fell under 12 minutes, with their codes |
| `Team Range Data` | one row per team per ward — settlements, total minutes, band |

`charts/team_time_range.png` is also written, for the PPTX slide below.

Run it on its own against any day's visitation CSV:

```bash
python gts_pipeline/team_time_range.py \
    --visitation-csv Day1_outputs/Zamfara_day_1_settlement_visitation.csv \
    --state Zamfara --output Team_TimeSpent_Range_Summary.xlsx
```

Minutes come from the visitation CSV's `track_count` column (`minutes = pings ×
2`, the same factor stage 2 uses). An older CSV without it falls back to each
team's most common per-settlement band, and says so in the run log.

### Time Spent Analysis slide

Slide 4 of the approved template. Two-part: the team time-range distribution on
the left (with the `<12 mins` bar in red) and the under-12-minutes **LGAs** on
the right, as a real PowerPoint table so the figures stay selectable. The
narrative above carries both measures, each labelled.

The table names the top LGAs by teams under twelve minutes, adds each LGA's
**median tracking sessions** as a third column, and closes with a "+N more
LGAs" row followed by an italic caption. Ward-level detail stays in the
workbook's `Teams Under 12 Mins` tab. When no team falls below twelve minutes
the table is replaced by a line saying so, rather than leaving an empty column.

The slide's narration is limited to two findings — minutes and sessions. The
device-sharing count goes in the table caption instead, because the narration
band is fixed at 0.75in and the type is shrunk to fit: at campaign scale a
third sentence drops it from 10pt to 9.5, against a 9pt floor.

### "Tracking Sessions by Team & Device" slide

Two tables side by side, both ranked **shared devices first**:

| Table | Columns | Ranked by |
|---|---|---|
| Teams | Team Code, IMEI, Tracks, Pings | on >1 device, then tracks, then pings |
| Devices | IMEI, Teams, Tracks, Pings | team codes carried, then tracks, then pings |

"Tracks" is the session count. The ranking is the design, not a detail: a slide
shows the top of a list and nothing else, and a device transmitting under two
team codes invalidates both teams' tracks — that outranks a team that merely
had a choppy day. Full lists are in the workbook tabs the captions name.

A team on several devices would produce a 50-character IMEI cell and wrap the
row, so the slide shows the first IMEI in full — that is what identifies the
handset to go and look at — followed by `+N`. The workbook carries the
complete list.

**This slide is not in the approved template.** It lives in
`eHA_Revised_reporting_template_v2.pptx`, produced by
`add_sessions_slide_to_template.py`, which clones the Team Time Efficiency
slide so the branding, master and fonts are the template's own. Stage 6 finds
the slide by its title, so:

- v2 template + session data — slide is filled, everything below shifts down one
- v2 template, no session data — slide is deleted, like the other conditionals
- approved template — slide is absent, stage 6 says so and the rest of the deck
  is unaffected

That last case matters: an analyst still running last month's template must get
a correct deck, not a broken one, because the pipeline gained a slide.

### Template size — `slim_template.py`

The templates are ~59 MB, and 54 MB of that is 27 embedded PNGs: the maps and
charts from the run each was exported from. Stage 6 replaces every one of them
on every build, so those bytes buy nothing at run time and cost a lot —
GitHub's 50 MB warning threshold, a slow `safe_extract`/`rezip` on every build,
and a test suite that took minutes.

```bash
python slim_template.py --template ../eHA_Revised_reporting_template.pptx
```

59 MB -> 5.4 MB. Media referenced from a slide **master, layout or theme** is
left untouched — that is the eHA logo, footer bar and title artwork, which
stage 6 never replaces. Only slide-referenced images are shrunk, to 2x2 pixels
of the same format, so every relationship and shape geometry stays valid.
`test_revised_template_slide_map.py` slims its working copies for this reason
and passes identically against them, which is the evidence that a slimmed
template and the original are interchangeable as far as the report is concerned.

### Map legibility

**Settlement points.** Both single-panel coverage maps drew at `markersize=9` —
a ~3pt dot, visible in the PNG and effectively invisible projected, which is
where these are read. Sized per role now, because a statewide map plots every
settlement in the state and an LGA map a few hundred:

| Map | Was | Now |
|---|---|---|
| State coverage | 9 | `SETTLEMENT_POINT_SIZE["statewide"]` = 26 |
| LGA coverage (single panel) | 9 | `SETTLEMENT_POINT_SIZE["lga"]` = 55 |

Kept semi-transparent with a darker hairline edge, so overlapping points read
as a cluster rather than a blob and LGA/ward boundaries stay visible
underneath. Labels are annotated after the points and carry white halos, so
they always sit on top.

The **side-by-side LGA pages** plot settlement *extents* — true-area polygons —
not points, so the equivalent lever there is outline weight: `EXTENT_EDGE_WIDTH`
0.3 → 1.1 and `EXTENT_OUTLINE_EDGE_WIDTH` → 1.3. Same fill, same palette, same
position; the polygon simply occupies more visual space.

### Implementation map

Four faults, one of them the reason the others were invisible:

- **`LGA.sqlite` has no state NAME column** — only `statecode`, which
  `load_boundaries`' own "code" exclusion rejects. So its state filter silently
  did nothing and the layer was all 774 LGAs in Nigeria. That was masked while
  the implementing-LGA name filter narrowed it afterwards. The focal state's
  LGAs are now selected **spatially** — representative point inside the state
  polygon — which works whatever the layer calls its columns.
- **Only implementing LGAs were labelled**, because the same filter was applied
  before labelling. All LGAs in the focal state are labelled now.
- **Zoomed to the implementing LGAs**, which cropped part of the state off the
  page whenever a campaign did not cover all of it. Zooms to the focal state.
- **LGA labels at a flat 11pt.** Now 17pt (≤16 LGAs) or 13pt, stepped down by
  the square root of each LGA's area share with an 11pt floor — Zamfara's
  north-east corner packs Kaura Namoda, Birnin Magaji/Kiyaw and Gusau into a
  fraction of the page and collided at one flat size.

Everything outside the focal state is washed with `OUTSIDE_MASK_FILL` at 0.72
alpha, drawn above the fills and below every label: the state stands out, and
neighbouring states keep enough shape to orient by.

### Time Spent table — LGA-specific

Every column is that LGA's own figure, not a state total repeated down the rows:

| Column | Source |
|---|---|
| LGA | spatial attribution — the LGA the team's GPS points fall inside |
| Teams | teams that transmitted from inside that LGA |
| <12 min | of those, how many tracked under twelve minutes |
| Median sessions | median separate tracking sessions per team, in that LGA |

All three metrics are grouped through **one** team → LGA mapping. They used to
come from two sources that spell the same place differently — the export says
`Birnin Magaji-Kiyaw`, the boundary layer says `Birnin Magaji/Kiyaw` — so a
table built from one and a column merged from the other silently dropped that
row. The caption below stays italic and its device-sharing count comes from the
same run as the table.

### Time Spent — the hierarchy

    selected day -> track -> state boundary overlay -> state of interest
    -> unique team/device -> time spent

Applied in that order, in `track_geo.py`, and nothing downstream can widen the
population because nothing downstream re-reads the export.

**State of interest comes from the planned settlements**, via
`state_of_interest()` — not from the caller's argument and never from a state
field in the track data. The state is a property of what was *planned*. A
settlement list naming two states raises rather than picking one.

**The overlay is spatial, always.** Each valid ping is a point intersected with
`state.sqlite`. `test_day_state_scoping.py` writes every track row with a
deliberately wrong state field and still gets the right answer — a field-based
filter would return nothing at all.

**The day filter is applied to the attributed pings**, so a previous day's
teams cannot carry forward. `"latest"` resolves to the most recent date with
tracks *inside the state of interest*, not the export's last date, which on a
national file may belong to another state's campaign.

**Minutes are measured from those pings only.** This is the part that changed:
`team_daily_time.analyse` takes a scope of team CODES and then measures each
team from every ping it sent that day, including pings outside the state. That
gave the right population with potentially wrong durations. `track_geo.time_spent`
measures from the attributed pings, and imports `MAX_GAP_MINUTES`,
`SINGLE_PING_MINUTES` and `classify_minutes` from `team_daily_time` rather than
restating them — the measurement is unchanged, its input is corrected.

#### Validation

`track_geo.validate()` writes the funnel and three assertions onto the
workbook's Validation tab. It has to narrow at every step:

```
Rows read from the export             400,000
Tracks before spatial overlay         310,664   valid fixes, usable coords, an identifier
Tracks assigned to a state            309,480   inside any state polygon
Tracks within the state of interest   120,016   inside Zamfara, all days
Tracks on the selected day             79,945   day AND state — the analysis population
Unique teams in the state that day        755
Unique devices in the state that day      749
Teams in the Time Spent Analysis          755
No duplicate team counting                 OK
No cross-day contamination                 OK
No out-of-state tracks                     OK
```

Joining against **all 37 states** and then narrowing is deliberate: joining
only to the focal polygon makes "assigned to a state" and "within the state of
interest" the same number, and the validation stops being able to show how much
of the export the overlay actually placed.

### Spatial attribution — `track_geo.py`

Every per-team figure used to be derived by matching STRINGS: the export's
`NGA LGA 2024 Label` against the settlement list's `lga`, and its `Team Code`
against the settlement list's `team_code`. Both matches fail, and both failed
silently.

**The team-code namespaces do not overlap.** The settlement list uses
`GOR-025`, `TWD-202`; the export uses `ZAM/BUG/BEL/003`, `ZM/BKM/BKM/023`. Raw
intersection: **0 of 3,067**. Reducing the export code to its last two segments
recovers 11.7%. No string rule can join them.

What that broke, measured on a real April 2026 Zamfara campaign:

| Figure | Was | Is | Why it was wrong |
|---|---|---|---|
| Time Spent, teams | 4,213 | 689 | no state scope (157 LGAs, 16 states), grouped by (LGA, team) so boundary-crossers counted twice, and "no evidence of tracks" = every settlement-list team because none matched |
| Teams Reported | 4,960 | 755 | same national aggregate, per-LGA `nunique` summed into the Grand Total |
| Teams Pending | 0 (or = Deployed) | Deployed − Reported | clipped at 0 against an inflated Reported; equal to Deployed when nothing matched |

`track_geo.py` stops asking the strings and asks the geometry: each valid ping
is a point, intersected with `state.sqlite` and `LGA.sqlite`, and the team and
device are attributed to where their points actually fell. It cross-checks —
952 teams inside the Zamfara polygon against 951 carrying the export's own
Zamfara label — which is what makes the spatial figure trustworthy rather than
merely different. It runs in about a second per 300k points.

**Counting rules.** State level counts each team once, however many LGAs it
worked. LGA level counts a team in **every LGA it entered**, by choice: field
teams do cross boundaries and an LGA table that hides that misdescribes where
work happened. So the LGA column sums above the state total, by exactly the
number of boundary-crossers, and each slide says so.

**Devices with no team code.** 36,928 pings from 212 devices transmit with a
valid IMEI and a blank Team Code. They are counted as devices, excluded from
team figures, and reported on the Validation tab — which is why the device and
team totals differ.

The resulting in-state team-code set is passed to `team_daily_time` and
`team_sessions` as their scope, so all three analyses describe the same
population without re-reading the export.

### Validation tab

Every headline figure, with how it was derived, reconciled against the source
export. Two inequalities are deliberate and labelled as such: the per-LGA
columns sum above the state totals (boundary-crossers), and Teams Reported is
not a subset of Teams Deployed (the namespaces don't overlap). A run is
checkable rather than trustworthy-by-assertion:

```
Valid GPS points in state          79,945
Unique team codes in state            755
Unique devices (IMEI) in state        749
Tracks (sessions) in state          1,683
Sum of per-LGA team counts            781   = 755 + 26 boundary-crossers
Devices with no team code               1
Teams Deployed / Reported / Pending  3,067 / 755 / 2,312
```

### Slides are resolved by title, not by file number

`stage6_pptx_report.py` used to address slides by part-file name
(`slide11.xml`). **python-pptx renumbers every slide part when it saves**, so
the moment a slide is inserted into the template and the file is written back,
the numbering is renormalised and every constant after the insertion point
addresses a different slide. Adding three slides moved `slide11.xml` from
"first LGA map" to "Cumulative Settlements Coverage".

`_resolve_slide_map()` now resolves each role by matching the slide's title,
falling back to the positional constant with a warning. The `SLIDE_*` constants
are fallbacks, not the primary route, and the failure class is gone.

### Tracking sessions — how many sittings the day arrived in

`team_sessions.py` answers: **in how many separate sittings did each team
transmit, and on how many devices?**

"Number of tracks" is ambiguous in a GTS export, because `Track ID` is issued
PER PING — a million-row export carries very nearly a million distinct Track
IDs, so counting them measures data volume and nothing an analyst can act on. A
session is the useful unit: a run of consecutive pings with no gap long enough
to mean the device stopped.

**The gap rule is not a new threshold.** `team_daily_time` already defines "not
tracking" as a gap longer than `MAX_GAP_MINUTES` (15) and excludes such gaps
from time spent. `team_sessions` imports that same constant as its session
boundary, which makes the two analyses two views of one segmentation:

    minutes tracked  =  the total length of a team's sessions
    sessions         =  how many pieces that total arrived in

They cannot contradict each other, and `test_team_sessions.py` asserts the two
constants have not drifted apart. Checked on a real April 2026 Zamfara export
(valid fixes only): consecutive pings arrive 2.0 minutes apart at the median
and 5.6 at the 95th percentile, so 15 minutes sits well clear of normal
reporting; the 99th percentile is 17.8 minutes, which is the switched-off tail
this is meant to catch.

**Rows flagged `Valid = 0` are excluded** before sessions are cut, so a burst
of rejected fixes cannot manufacture a session or bridge a real break. That was
about 23% of rows in a recent export. Note the flag arrives as a *string* in
some chunk files and an integer in others — `track_columns.valid_mask` handles
both, which is why nothing in the pipeline compares it to `0` directly.

**Fragmented** means `FRAGMENTED_SESSIONS` (4) or more sessions on the day. Set
from the same export: median 2 sessions, 75th percentile 3, 90th percentile 4,
so 4 flags the top ~14% — a follow-up list a supervisor can act on. Three flags
28% and stops being a list.

**Devices.** Sessions are cut per team and, separately, per device, then
crossed: one team on several devices in a day, and — the more serious — one
device transmitting under several team codes, which means the tracker was
passed around and neither team's track can be attributed with confidence.

Outputs — workbook tabs `Tracking Sessions` (per-LGA summary and a fragmented
teams chart), `Session Data (Team)` and `Session Data (Device)`, plus the
sessions column and narration clause on the Time Spent slide. Runnable alone:

```bash
python team_sessions.py --tracks merged_tracks.csv --visitation-csv Day1_out/..._settlement_visitation.csv
```

### Time Spent Analysis is measured from the track timestamps

`team_daily_time.py` answers one question and nothing else: **for each team, on
each tracking date, how long was it actually tracking?** Its only input is the
merged track export.

| | rule |
|---|---|
| Population | the teams that **transmitted on that date**. Teams that sent nothing are absent; Team Deployment already reports them |
| Duration | the gaps between consecutive pings, summed, with any gap over `MAX_GAP_MINUTES` (15) excluded |
| Scope | one date. Durations are never combined across dates |
| Single ping | credited `SINGLE_PING_MINUTES` (2), since one ping is evidence of presence |

Gap-summing rather than last-minus-first: a tracker switched off over lunch, or
out of network for an hour, would otherwise have that hour counted as tracking.
Gap-summing rather than pings x interval: that infers duration instead of
reading it, and cannot tell steady work from a device left on.

This replaced two measures that used to feed the same slide — the
settlement-derived range analysis and the 08:00-15:00 field window. Both
counted a different population from the bars beside them, which is how a
campaign with 797 reporting teams produced an under-12-minutes bar of over
2,000. **The bars now sum to the teams that transmitted**, and the run log
prints that reconciliation on every run. `test_daily_time_spent.py` asserts the
duration rule against known timestamps.

The assigned-settlement view (`team_time_range.py`) is still produced for the
workbook tabs — it answers a different question — but it no longer drives the
slide.

#### Scoped to the campaign's state

**GTS track exports are national.** Unscoped, a campaign in one state has its
time-spent figures computed over every state's teams — the bars count teams
that were never part of this campaign.

The scope comes from the day's visitation CSV, which stage 2 has already
filtered to the campaign's state, so no new input is needed and the scope
cannot drift from the rest of the pipeline
(`team_daily_time.scope_from_visitation`). Three routes are tried, in
descending order of reliability:

1. the export's **own state column**, where it has one
2. the state's **LGA names** — the same rule stage 3 has always used on this
   file, with underscore/spacing variants normalised so `Talata_Mafara` matches
   `Talata Mafara`
3. the state's **team codes**, as a last resort

The run log names the route used and how many pings survived it, and warns if
the filter matches nothing — which would mean the export does not cover this
state, or its LGA names disagree with the settlement list's.

`test_state_scoped_time.py` builds a three-state export and asserts all three
routes return the campaign state's teams and only those.

### Teams that never reported are their own band

`team_time_range` reads the **planned settlement list**, so a team that never
reported still appears there with all of its assigned settlements, each with
`track_count` 0. Those zero-minute teams used to be classified `<12 mins`,
which made that bar count teams who submitted nothing alongside teams who
turned up briefly — on one campaign it read **2,806 against 1,022 reporting
teams**, and the under-12 follow-up list was mostly teams that were never seen.

Zero minutes is now `0 (No evidence of tracks)`, a band of its own, drawn in a
darker red than the `<12 mins` band beside it. They are different problems and
need different conversations: one team has to be found, the other has to be
asked what it did with its day.

The bands are mutually exclusive and cover every team, so they **must** sum to
the number of team codes in the settlement list. The run log prints that
reconciliation on every run — `N team-band rows across N team codes … X with
tracks, Y with none, Z under 12 mins` — and warns loudly if the two disagree.
`test_time_spent_bands.py` asserts it.

**The 08:00–15:00 measure is scoped to one transmission date.** `load_tracks_teams`
carries a local `date` alongside the hour, and `time_spent_analysis` keeps only
the latest date present, so a merged export covering several days cannot report
the campaign to date as one day's field time. The no-tracks count comes from the
same scoped frame: a team that reported yesterday but not today is a team with
no evidence of tracks today, which is the finding the daily review is looking
for. The post-campaign analysis passes `report_date=None` and spans the whole
period on purpose.

The LGA figures are recomputed from the per-team-per-LGA totals rather than
summed from the ward table: a team working two wards is under twelve minutes in
each, so summing ward counts would count it twice.

Fitting the LGA rows needed them compressed. PowerPoint and LibreOffice both
treat a row height as a **minimum** and grow it to fit the text plus the cell's
margins, so setting the height alone does nothing — the cell margins are cut
right down, which is what actually lets the rows shrink. Columns are given
**explicit widths**: left to distribute evenly, PowerPoint gives a long LGA
name the same room as a two-character hours figure, the name wraps, the row
grows, and a table sized for N rows silently renders taller than its box. The
caption is then positioned from the *rendered* row height rather than the
shape's reported height, which is only what was requested; using the latter
printed the caption across the table's last rows.

## Fonts — no `findfont` warnings, no machine-specific installs

The QGIS layout templates carry the font names of the machine they were
authored on:

| Template asks for | What it is |
|---|---|
| `MS Shell Dlg 2` | a Qt Windows UI alias — not a real font file on any platform |
| `Open Sans` | a Google font, installed on the author's PC |
| `Arial`, `Trebuchet MS` | Windows/macOS only |
| `Century Gothic` | Microsoft Office only |

Stage 4 used to pass those names straight to matplotlib with a fallback
appended, e.g. `family=["MS Shell Dlg 2", "DejaVu Sans"]`. Matplotlib tries
each name in order and logs

```
findfont: Font family 'MS Shell Dlg 2' not found.
```

for every name it cannot find, **on every text object it draws** — so one map
produced dozens of identical lines. Appending a fallback made the substitution
deterministic but did nothing about the search that precedes it.

`pipeline_fonts.py` now resolves every requested family against the fonts
actually installed, once, and returns only a name matplotlib can load — so no
lookup can miss. It also replaces matplotlib's stock `font.sans-serif` list
(a dozen names, mostly absent on Linux) with the one resolved family plus the
bundled `DejaVu Sans`, which covers text that carries no explicit family:
titles, axis and tick labels, legend text, annotations.

Resolution order is the requested name, then a small alias table for that
specific face, then a generic preference list ending in `DejaVu Sans` —
matplotlib's own bundled font, present by definition, so the chain can never be
exhausted. On this environment everything lands on **Liberation Sans**
(metric-compatible with Arial), and `Century Gothic` on **URW Gothic**. Where
`Open Sans` *is* installed, it is used, so nothing is lost on the authoring
machine.

Both `stage4_maps` and `stage_charts` apply the policy at import, before any
figure exists, so the maps and charts in one deck are set in the same face on
every machine. The chosen font is named once in the stage-4 log.

`test_no_font_warnings.py` captures matplotlib's font-manager logger at
WARNING while exercising every text path — including the exact names the
templates carry — and fails if anything is logged. Note that `findfont:` also
prefixes matplotlib's routine per-candidate scoring at DEBUG; that is normal
and is not what the test looks for.

## Slide layout — how overlap is prevented

Every generated slide places its narration in a **fixed band** between the
title and the content, and fits the font to that band (`_narration` /
`_fit_font_pt`, 14pt down to 9pt). The template's own narration box is 10in
wide at 18pt with no height limit, which is precisely what let a two-sentence
narration grow downwards into the charts. Pinning the band and shrinking the
type removes the failure mode, rather than tuning each slide's wording and
hoping it still fits next campaign.

The measurement is a character-count estimate, not a text metric — python-pptx
cannot measure rendered text, and the deck is a Google Slides export whose font
is usually substituted at render time anyway. The ratios are set pessimistically,
so the estimate errs towards a slightly smaller narration rather than one that
reaches into the chart. A narration that hits the 9pt floor is the signal to
shorten the wording; the box is never widened into the charts.

Three fixed bounds keep the rest honest:

| Constant | Value | Why |
|---|---|---|
| `NARR_TOP_IN` | 1.08in | clears the title placeholder, whose box runs to 1.06in |
| `CONTENT_TOP_IN` | 1.95in | common top for charts and tables, so the deck reads as one layout |
| `EFF_CONTENT_TOP_IN` | 2.05in | Team Time Efficiency's narration can run to two findings and needs a deeper band; its narration is also capped at 12pt so it is compact by construction |
| `CONTENT_BOTTOM_IN` | 5.18in | the master's footer band starts at 5.25in; anything crossing it prints over the URL and copyright line |

The glyph-width and line-height ratios are set for the **substituted** font, not
the template's own. The deck is a Google Slides export; the substitute picked at
render time is wider and more generously leaded, and an estimate tuned to the
original under-counts the lines — which is how the efficiency narration reached
its table on a real run despite fitting in the check.

### Landing page

The title slide carries exactly three lines:

```
{State} State                          bold
GTS Tracking Report                    bold
Evening Review Meeting Presentation
```

Sized as a hierarchy (28 / 40 / 18pt) rather than three equal lines: the middle
line is the report's name and takes the weight, the first names the campaign's
state and the third is a subtitle. At one size the third line alone would wrap.
The frame is centred vertically so the block sits balanced rather than hanging
off the bottom edge as the template's single line was anchored to.

**The title shape is found by role, not by its wording.** `_find_landing_title`
tries the slide's title placeholder first, then any shape mentioning a tracking
report, then the largest-typed text shape in the upper two-thirds. Matching on
the text alone — as it did at first — works on the sample template and silently
does nothing on an org template worded differently: no error, no warning, the
old title simply stays. Whichever route succeeds is printed to the run log, and
a run that finds no title shape says so loudly.

To check a template without running the pipeline:

```bash
python gts_pipeline/inspect_template_title.py "your template.pptx" --state Kano
```

It lists every shape on slide 1, names the one stage 6 would rewrite, and with
`--write` saves a preview file with the title applied.

**The campaign name is not on this slide** — it is not one of the three agreed
lines. `--campaign-name` still appears on the .docx title page from stage 5.

After the deck is assembled, `_overlap_report` walks every slide and names any
pair of shapes whose boxes intersect by more than 0.06 sq in, printing them to
the run log. It is a **check, not a fixer** — geometry here is explicit, so an
overlap means a constant is wrong or a table rendered taller than planned, and
both are worth seeing in the run log rather than in the meeting. The title
placeholder is excluded (its box is 0.92in tall for text that renders at about
half that, so it "overlaps" everything below it while looking perfectly clean),
as are the title and background slides, whose overlapping decorative shapes are
the template's own design.

### Two rules that keep the efficiency slide clear

**The narration is found by role, not by its wording.** Five slides are cloned
from the Team Deployment layout, and each one used to locate its narration by
searching for the literal phrase *"were deployed"* — the sample template's
deployment sentence. On a template worded differently that matches nothing:
`_narration` is never called, so the box keeps the template's own geometry
(full width, 18pt, no height limit) while the chart and table are placed at the
pipeline's coordinates, and the two collide. Nothing was logged, so it looked
exactly like the layout fix had not been applied. `_find_narration` now falls
back to the largest text shape above the content band and says so in the run
log.

**The content is placed below where the narration actually ends**, not at a
fixed constant. `_narration` returns its estimated bottom, and the efficiency
slide puts its chart and table at `max(EFF_CONTENT_TOP_IN, bottom + 0.22in)`.
A narration that needs an extra line now pushes the content down instead of
colliding with it, whatever the wording or the substituted font turns out to
be. The run log prints when this happens.

The **Team Time Efficiency** chart is two panels, not one, because the two
things a reader needs are different shapes: the left panel is the state-wide
efficiency distribution across the same four coverage-style bands the rest of
the deck already uses; the right panel names the worst-performing FLAGGED
teams individually, each bar labelled with its efficiency and reason inline —
a list to act on, capped at `MAX_LISTED_TEAMS` (18) since a campaign can run
hundreds of teams, too many for one bar each.

## Side-by-side LGA maps

Each LGA is rendered as **one page holding two panels**, driven by
`UPDATED LGA GTS Coverage Map Template Atlas SBSs.qgz` (A0 landscape, 1189×841mm, 300 dpi):

| | Left panel | Right panel |
|---|---|---|
| Title | Visitation **Status** Map of {LGA} LGA | Visitation **Coverage** Map of {LGA} LGA |
| Shows | Settlement extents (the voronoi/TA layer), filled by whether the settlement was visited at all | Gridded target-area cells, filled by whether each cell was visited, with the settlement extents outlined over them |
| Answers | *Which settlements did teams reach?* | *How thoroughly did they cover the ones they reached?* |

**Only the Atlas/target LGA is highlighted.** It is drawn with a white fill and
a heavy black outline; **every** other LGA on the page gets the identical flat
grey, whether or not it is part of the campaign, so nothing but the target LGA
is emphasised. The grey is drawn over the data as well, which masks anything
spilling past the focal boundary. Adjoining LGAs are **labelled** with their
names, placed inside the part of each that is actually on screen rather than at
its true centroid — on a map zoomed to one LGA most neighbours are only partly
visible and centroid placement drops exactly the labels the reader needs.

The legends are **compressed** relative to the template's own geometry — its
10mm box padding at A0 gives them more of the page than their content warrants.
`LEGEND_COMPRESSION`, `LEGEND_LABEL_SPACING` and the related constants in
`stage4_maps.py` scale the padding and the gaps between entries down, and
`LEGEND_SHIFT_DOWN_MM` nudges the block clear of the map area. Entry wording,
order, colours and font sizes are untouched — only the whitespace around them.
Set `LEGEND_COMPRESSION = 1.0` to restore the template's own spacing.

Context comes from the **whole state's** LGA layer, not just the campaign LGAs:
a target LGA on the edge of the campaign area would otherwise sit surrounded by
blank page. `LGA_CONTEXT_MARGIN` (0.14) overrides the template's own atlas
margin of 0.02, which crops to the LGA itself and leaves no room for
neighbours or their labels.

Both panels are locked to the same extent, so a settlement sits in the same place on both and
the two views can be compared directly. Each panel carries its own title, legend(s) and north
arrow from the template; the State PHC logo goes top-left and the eHA logo bottom-right.

**This needs two polygon inputs the older point maps did not**: the settlement extents
(`--voronoi`, already a pipeline input) and the per-cell gridded TA that stage 2 writes as
`gridded_ta_day_{N}.parquet`. Both runners and the web app pass these automatically. If either
is missing, or if the LGA template found has only one map frame, stage 4 prints a note and
falls back to the previous single-panel settlement-point map — a template swap or a partial run
never costs you the day's maps. Filenames are unchanged either way
(`{State}_{LGA}_day_{N}.png`), so the report and PPTX stages pick them up as before.

In the reports:

- **PPTX** — one slide per LGA as before, titled "{LGA} LGA Visitation Status & Coverage". The
  slide's map box is 1.414:1, the A-series page ratio, so the A0 page fills it without
  letterboxing.
- **Word (.docx)** — the map pages move into a **landscape section**, giving each map 9.2in
  instead of the 6.3in a portrait page allows. At portrait width each panel lands around 3in
  and the settlement detail stops being legible. The report returns to portrait after the maps.

## Map elements and layout templates

Map layout is driven by the QGIS print layouts that sit beside these scripts — page size and
orientation, print resolution, map frames, legend position/fonts/entry wording, scale bar,
north arrow, logo placement and the panel title labels all come from them. Drop in a layout and
no code change is needed. `qpt_layout.py` parses them, `stage4_maps.py` renders from the result.

Three file types are accepted:

- **`.qpt`** — a layout exported on its own (Project → Layout Manager → Export as Template)
- **`.qgz`** — a whole zipped project, straight out of QGIS, no export step needed
- **`.qgs`** — an uncompressed project

Templates are matched to a role by filename:

| Role | Filename must contain | Shipped template | Page |
|---|---|---|---|
| `lga` | `lga` + (`coverage` or `visitation`) | `UPDATED LGA GTS Coverage Map Template Atlas SBSs.qgz` | A0 landscape (1189×841mm), **2 map frames** |
| `statewide` | `state` + (`cumulative`/`cummulative`) | `state cummulative map.qpt` | A3 landscape (420×297mm) |
| `implementation` | `implementation` | `Implementation map.qpt` | A3 landscape (420×297mm) |

When several files match one role, **the layout with the most map frames wins** — dropping a
new two-map project next to an old single-map template is all it takes to switch to
side-by-side. Ties keep discovery order, so roles with a single template are unaffected. The
run log prints which file was chosen and which were passed over.

A project holding several layouts (the older `.qgz` templates each carry a Landscape and a
Portrait variant) yields its richest layout by the same rule.

`LEGACY_LAYOUT_ROLES` at the top of `stage4_maps.py` still makes the `lga` role ignore
*single-map* templates in favour of the original built-in landscape layout — that is why
`LGA coverage map.qpt` (A3 portrait) sits in the folder unused. A **multi-map** template
overrides that opt-out, since the built-in layout cannot stand in for a side-by-side page.

On the **state implementation map**, the view is zoomed to the implementing LGAs
(`IMPLEMENTATION_ZOOM_MARGIN`) so they fill the frame, adjoining-state names are drawn bold
with a white halo for legibility, and the legend, north arrow, scale bar and logo are anchored
to the drawn map area rather than the paper edge, overlaying the adjoining states.

Override the search directory with `--map-template-dir`, the `GTS_MAP_TEMPLATE_DIR`
environment variable, or the web app's "Map Layout Templates" section. A missing or
unparseable template falls back to the previous built-in layout for that role and prints a
note, so a bad template never takes a run down.

Every map carries a north arrow, a scale bar and the eHA logo at the template's positions, and
a legend whose entries use the template's own wording and order (settlement counts are appended
to the visited/not-visited rows). Ward boundaries and labels on per-LGA maps use a larger, bold,
white-haloed font for readability. The implementation map is zoomed out slightly beyond the
focal state for adjoining-state context.

Two caveats worth knowing:

- **Symbology is not template-driven.** A print layout carries placement only; the
  visited/not-visited fill colours and the LGA/ward outline styling live in the project's
  *layer* definitions, which are not parsed. They remain constants at the top of
  `stage4_maps.py` — the side-by-side ones (`EXTENT_*`, `GRID_*`, `DUAL_*`) were read off the
  supplied project's own layer styles, so changing a colour in QGIS means changing it there
  too.
- **Titles come from label items where the template has them.** The LGA template carries a
  label per panel and those drive the panel titles, including the `[%"lganame"%]` expression,
  which is substituted with the LGA being drawn. The statewide and implementation templates
  have no label item, so `stage4_maps.py` draws a fallback title at the top instead. Add a
  label to those layouts in QGIS (using `[% @atlas_pagename %]` for the per-feature name) and
  it takes over. Set `DRAW_FALLBACK_TITLE = False` for untitled maps that match the templates
  exactly.

The templates specify 300 dpi, which on A3 is roughly 3500×5000 pixels per map. Use
`--map-dpi` (or the web app's resolution slider) to lower it if the maps folder and the reports
embedding them get unwieldy.

## Analysis rules (ported from your notebook)

- A settlement grid cell is **Visited** if any GPS point intersects it; settlement **Coverage** = visited cells / total cells.
- Coverage classes: 70–100% Fully Covered, 50–69% Partially Covered, 30–49% Low, 1–29% Very Low, 0% No Coverage.
- **Time spent** = ping count × 2 minutes; field window for team compliance is 08:00–15:00 (UTC+1).
- Cumulative day logic (`day_N_cumm`) carries previous days' visitation forward via `--prev-dip`.

## Known limitations

- `--mopup` is accepted by both runners but currently has no effect on the final `day_N_cumm`
  values — "Not Yet Visited" is normalized to "Not Visited" the same way on mop-up and normal
  days. If mop-up days are meant to be treated differently downstream, flag it and this can be
  changed.
- `checkpoint_runner.py`'s resumable merge step commits progress after each whole source file
  completes; a hard kill mid-file (as opposed to the normal time-budget pause between files)
  can leave a partially-written file's rows out until the next resume re-reads it from
  scratch — safe, but that one file's work isn't preserved across the crash.
