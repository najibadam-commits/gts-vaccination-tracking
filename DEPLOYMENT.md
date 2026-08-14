# Deploying the GTS pipeline

Everything needed to put this repo on GitHub and run it on Streamlit Community
Cloud, plus an honest account of what the free tier can and cannot do with it.

---

## 1. Why the deploy failed

> *The app's code is not connected to a remote GitHub repository.*

This is **not** a Streamlit configuration problem, a wrong remote URL, or an
unpushed branch. It is simpler than any of those:

```
$ git status
fatal: not a git repository
```

There is no Git repository in `gts_pipeline/` at all — no `.git` folder, no
commits, no remote. Streamlit Community Cloud deploys *from GitHub*; it has no
way to see a folder on your PC. Section 4 creates the repository from scratch.

If you had previously run `git init` and were only missing the remote, the fix
would be section 4 step 4 alone (`git remote add origin …`). You have not, so
run the whole of section 4.

---

## 2. What the app is

| | |
|---|---|
| **Entry point** | `app.py` — this is what you select as "Main file path" |
| Alternative UI | `app_modern_ui.py` (tabbed; same pipeline, needs `gts_theme.py`) |
| Legacy UI | `app_legacy_ui.py` (kept for comparison) |
| Stages | `stage1_merge` → `stage2_analysis` → `stage3_erm_workbook` → `stage4_maps` / `stage_charts` → `stage5_report` / `stage6_pptx_report` / `stage7_pir_report` |
| Analysis modules | `team_daily_time`, `team_efficiency`, `team_time_range`, `target_population` |
| Support | `qpt_layout`, `pptx_xml_utils`, `pipeline_fonts`, `cloud_limits` |
| CLI runners | `run_pipeline.py`, `checkpoint_runner.py` (not used by the web app) |

**Assets the app loads at startup**, found next to `app.py` (or one folder up,
or the working directory — `_find_default` in `app.py`):

| File | Purpose | Size |
|---|---|---|
| `LGA.sqlite` | LGA boundaries for the maps | 11 MB |
| `Ward.sqlite` | Ward boundaries | 23 MB |
| `state.sqlite` | State boundaries | 8 MB |
| `organization's reporting template.pptx` | the deck stage 6 fills | 29 MB |
| `Post Implementation Report sample.pdf` | the PIR template stage 7 uses | 7 MB |
| `*.qpt`, `*.qgz` | QGIS map layouts driving stage 4 | <1 MB |
| `eha_logo.png`, `ehealthafrica blue.png` | branding | small |

Everything else the app needs is uploaded by the analyst each run.

**Paths are already portable.** `app.py` resolves everything from
`os.path.dirname(os.path.abspath(__file__))`; there were no absolute paths in
any module the app imports. The one hard-coded Windows path in the repo
(`Team Time Spent Range Analysis.py`, a superseded standalone script) has been
converted to command-line arguments — see section 8.

**There are no secrets.** No API keys, tokens, passwords or credentials appear
anywhere in the codebase; the app reads only uploaded files and the bundled
boundary layers. You do **not** need to configure anything under
*Advanced settings → Secrets*. `.streamlit/secrets.toml` is in `.gitignore` so
that stays true if one is ever added.

---

## 3. Read this before you deploy

Community Cloud gives an app roughly **1 GB of RAM**, a shared CPU and an
**ephemeral disk**. This pipeline was built for a workstation:

- it merges multi-million-row track exports and holds them in pandas
- it runs a geopandas spatial join of every track point against the gridded
  target area
- it renders A0 and A3 map pages at 300 dpi — about 3,500 × 5,000 px each, one
  per LGA, thirteen or more per state

**A full state-day will very likely exhaust the free tier.** That is a property
of the workload, not a bug, and no amount of configuration removes it.

What has been done about it (`cloud_limits.py`):

- upload cap lowered from 4 GB to **400 MB** in `.streamlit/config.toml`
- map DPI forced to **120** when hosted, from 300 — the single largest memory
  saving available, at the cost of print resolution only
- the app **warns before a run** whose inputs look too large, naming the reason,
  instead of dying mid-merge with an uninterpretable `MemoryError`
- a banner states plainly that output is reduced-resolution

None of this changes the analysis. Locally nothing applies: `is_hosted()` is
False, DPI stays at 300, and the upload cap is whatever your local config says.
Set `GTS_FORCE_LOCAL=1` to disable the caps on a self-hosted box.

**Use Community Cloud for demonstrations, training and small LGA-level runs.**
For a real campaign day, keep running the pipeline locally or on a VM with
8 GB+ of RAM. Section 9 covers that.

Also note the **ephemeral disk**: outputs are written into a temp folder and
survive only until the app restarts or sleeps. Download the workbook, report
and map zip before closing the tab — the app already offers them as downloads,
so no change was needed, but do not treat the server as storage.

---

## 4. Create the repository and push

Run these in **`C:\my work space\GTS_Automation\gts_pipeline`** (PowerShell or
Git Bash). If you do not have Git, install it from <https://git-scm.com/download/win>.

```bash
# 1. one-time identity, if you have never used Git on this machine
git config --global user.name  "Najib Adam"
git config --global user.email "najib.adam@ehealthnigeria.org"

# 2. start the repository and use 'main' as the branch name
git init
git branch -M main

# 3. stage everything that .gitignore permits, and check before committing
git add -A
git status                       # review the list — see section 5
git commit -m "GTS vaccination tracking pipeline — initial commit"
```

Now create the **empty** repository on GitHub — go to
<https://github.com/new>, name it `gts-vaccination-tracking`, and **do not**
tick "Add a README", ".gitignore" or "license" (an initialising commit would
conflict with yours).

Given the campaign data this handles, **private** is the safer default.
Community Cloud does deploy from private repos, with two conditions worth
knowing before you choose:

- the free tier allows **unlimited public apps but only ONE private app** —
  fine here, but it is your one slot
- Streamlit needs the broader `repo` OAuth scope to read a private repo, and
  creates a read-only **deploy key** on it. GitHub emails the repo admins when
  that key is created; that is expected, not a breach
- you must have **admin** permission on the repository to deploy from it

```bash
# 4. connect and push (replace YOUR-USERNAME)
git remote add origin https://github.com/YOUR-USERNAME/gts-vaccination-tracking.git
git push -u origin main
```

The first push moves ~78 MB and will take a few minutes.

**If a remote already exists and is wrong:**

```bash
git remote -v                                    # inspect
git remote set-url origin https://github.com/YOUR-USERNAME/gts-vaccination-tracking.git
git push -u origin main
```

**If the push is rejected** because the GitHub repo is not empty:

```bash
git pull --rebase origin main
git push -u origin main
```

---

## 5. What gets committed

`git add -A` plus the `.gitignore` in this repo yields **~78 MB across ~51
files**. Every file is well under GitHub's 100 MB per-file hard limit, so **no
Git LFS is required**.

**Committed** — code, the six asset groups in section 2, `requirements.txt`,
`.gitignore`, `.streamlit/config.toml`, `README.md`, `DEPLOYMENT.md`, the
`test_*.py` regression tests.

**Excluded, deliberately:**

| Excluded | Why |
|---|---|
| `Tracks_*.csv`, `merged_tracks.csv`, `settlement*.csv` | **campaign data** — GPS traces, team codes, population estimates. Never commit these. |
| `Day*_outputs/`, `charts/`, `maps/`, `*_ERM_Analysis.xlsx`, `*_Report.pptx` | regenerated on every run |
| `visited.sqlite`, `Not visited.sqlite`, `ta_settlement extent gridded *.sqlite`, `settlement vistation.sqlite` | ~100 MB of QGIS **working layers**, not pipeline inputs. The three boundary files are whitelisted by name. |
| `Daily Tracking Report sample.pdf`, `Vaccination Tracking Daily ERM Analysis sample.xlsx` | reference only |
| `GTS_Pipeline_Workflow_eHA.pptx` | documentation deck, 12 MB |
| `__pycache__/`, `.venv/`, `.streamlit/secrets.toml` | never belongs in a repo |

Confirm before pushing:

```bash
git ls-files | wc -l                             # ~51
git count-objects -vH | grep size-pack           # ~78 MB
git ls-files | grep -iE "tracks_|merged_tracks"  # must print nothing
```

That last command is the important one. **If it prints anything, stop** —
campaign data is staged. Remove it with `git rm --cached <file>` before you
commit.

### Repository structure

The current flat layout is fine and is what these instructions assume — Python
finds sibling modules without packaging, and `app.py` sits at the root where
Streamlit expects it. Do not restructure just before a deploy.

This matters more than it looks. Community Cloud **initialises every app from
the root of the repository**, even when the entry point is in a subdirectory,
and it recognises only **one `.streamlit/config.toml`, at the repo root**. So
the repository root must be `gts_pipeline/` itself — which is what `git init`
inside that folder gives you. If you instead created the repo one level up,
`app.py` and `.streamlit/` would sit in a subfolder, the config would be
ignored and the 400 MB upload cap would silently not apply. If you later
want a tidier tree, `src/` + `assets/` + `tests/` is the conventional shape,
but every `_find_default` path and the map-template discovery would need
re-checking.

---

## 6. Deploy on Streamlit Community Cloud

1. Sign in at <https://share.streamlit.io> with the **same GitHub account** that
   owns the repository.
2. If the repo is private, authorise Streamlit to read your repositories when
   prompted. Without this the repo will not appear in the dropdown.
3. **New app → Deploy a public app from GitHub** (works for private repos too).
4. Fill in exactly:

   | Field | Value |
   |---|---|
   | Repository | `YOUR-USERNAME/gts-vaccination-tracking` |
   | Branch | `main` |
   | Main file path | `app.py` |
   | Python version | **3.11** (under *Advanced settings*) |

5. **Secrets: leave empty.** This app has none.
6. Deploy. The first build takes 5–10 minutes — the geospatial wheels are large.

### Settings Community Cloud overrides

Cloud forces some configuration regardless of `config.toml`. Two are worth
knowing:

- `showErrorDetails = false` — a crash shows a generic message in the browser,
  **not** the traceback. Read the app logs (*Manage app* → the log pane at the
  bottom right) for the real error. This is where an out-of-memory kill will
  show up.
- `gatherUsageStats = true` — overrides the `false` in our `config.toml`. Left
  in place because it is honest about intent locally and harmless.

`maxUploadSize`, the theme and everything else in our config are respected.

Apps also **sleep after ~12 hours without traffic** and wake on the next visit,
taking a few seconds. Expected, not a fault.

### Runtime environment

Community Cloud runs **Debian 11 ("bullseye")** on Linux, so all paths are
forward-slash — already true throughout this codebase. Only released, still
supported Python versions are selectable; 3.11 is the safe choice here.

### Geospatial dependencies — what to expect

This is where deploys of GIS apps usually fail, so it is worth being precise.

- **`fiona` is deliberately not in `requirements.txt`.** It is the older I/O
  engine and needs a system GDAL, which is the single most common cause of a
  failed build on a hosted runner. geopandas 1.x defaults to **`pyogrio`**,
  whose wheel bundles GDAL. This repo uses pyogrio throughout.
- `shapely` ≥2 bundles **GEOS**; `pyproj` bundles **PROJ**. No system libraries
  are needed for either.
- **There is no `packages.txt` in this repo, and that is intentional.** Adding
  one with `gdal-bin` or `libgeos-dev` would install a *second*, different GDAL
  alongside the wheels' bundled copy and can break pyogrio. Only add a
  `packages.txt` if a build log explicitly asks for a system library.
- **SQLite needs nothing** — it is in the Python standard library, and the
  `.sqlite` boundary files are read through pyogrio's OGR driver.
- `rasterio` is **not** used by this pipeline; ignore any advice about it.
- Pin **Python 3.11**. Wheels for the whole geospatial stack are reliably
  available there; 3.13 occasionally lags.

If a build does fail, read the log for the *first* package that failed to
build — everything after it is noise.

---

## 7. Post-deployment testing

In this order, because each step rules out a class of problem:

1. **App loads** — no `ModuleNotFoundError` in the log. If there is one, a
   package is missing from `requirements.txt`.
2. **Cloud banner appears** at the top ("Running on a hosted runner…"). Its
   absence means `cloud_limits.is_hosted()` did not detect the environment and
   the caps are not applied — tell me and I will add the marker.
3. **Bundled assets found** — step 3 of the form should pre-fill the LGA, Ward
   and State boundaries without you uploading them. If not, the `.sqlite` files
   did not reach the repo.
4. **Map templates found** — step 6 should list the QGIS layouts.
5. **A small run end to end** — one LGA, one day, a few MB of tracks. Confirm
   the workbook, the .docx, the .pptx and the maps zip all download and open.
6. **Check the outputs**, particularly Time Spent Analysis and Team Time
   Efficiency, against a known local run of the same inputs. The figures must
   match; only map resolution should differ.
7. **Then try something bigger** and find where the runner gives out. Note the
   input size at which it fails — that is your practical ceiling.

---

## 8. Changes made for deployment

| Change | Why |
|---|---|
| `requirements.txt` rewritten with version bounds | it listed bare package names, so a future major release could break a deploy silently. `numpy`, `pyproj` and `pyarrow` were missing but imported. `fiona` deliberately excluded. |
| `.gitignore` added | there was none. Without it `git add -A` would have committed campaign track exports and ~100 MB of QGIS working layers. |
| `.streamlit/config.toml` upload cap 4000 MB → 400 MB | 4 GB cannot succeed in a 1 GB runner; it fails slowly and opaquely. Also disabled usage-stats gathering and enabled XSRF protection. |
| `cloud_limits.py` added | detects a hosted runner, caps map DPI at 120, warns before an over-sized run. No effect locally. |
| `app.py` / `app_legacy_ui.py` — banner, pre-run warnings, DPI cap | four small insertions; the analysis path is untouched. |
| `Team Time Spent Range Analysis.py` — hard-coded `C:\eHA\...` path replaced with `--input`/`--output` arguments | the only absolute path in the repo; it would have been dead code on any other machine. The script is superseded by `team_time_range.py` but was kept rather than deleted. |
| `dt.csv` removed | stray test artefact left in the folder. |

**Not changed, needs your decision:**

- **`app_modern_ui.py` did not get the cloud safeguards.** It is an alternative
  interface, not the entry point, and I did not want to touch it unasked. Say
  the word if you intend to deploy that one instead.
- **The three boundary `.sqlite` files (42 MB) are committed.** That is the
  simplest thing that works. If you would rather keep the repo small, they can
  move to a release asset or object storage and be fetched on first run — more
  moving parts, and needs a decision about where they live.
- **The repo is flat.** Restructuring into `src/`/`assets/` is defensible but
  touches every asset-discovery path; not something to do the day of a deploy.
- **Private vs public.** I have assumed private. If you make it public, be
  certain no campaign data has ever been committed — `git log --all --name-only`
  will show every file that has been in the history.

---

## 9. If Community Cloud proves too small

The same repository deploys unchanged to anything with more memory:

- **A VM (Azure/AWS/on-prem), 8 GB+ RAM** — `pip install -r requirements.txt`
  then `streamlit run app.py --server.address 0.0.0.0`. Restore the 4 GB upload
  cap in `config.toml`, or set `GTS_FORCE_LOCAL=1`.
- **Streamlit in Snowflake** — if your organisation already has Snowflake.
- **Docker** on any host: `python:3.11-slim`, `pip install -r requirements.txt`,
  `CMD ["streamlit", "run", "app.py"]`. No system GDAL needed, for the reasons
  in section 6.

The CLI runners (`run_pipeline.py`, `checkpoint_runner.py`) remain the right
tool for a full campaign day regardless of what is hosted —
`checkpoint_runner.py` in particular exists for exactly the case where a run is
too big to finish in one go.

---

## 10. Checklist

**Code readiness**
- [x] Entry point identified — `app.py`
- [x] All modules import cleanly (19/19 verified)
- [x] No absolute or Windows-specific paths remain
- [x] Assets resolved relative to the app's own location
- [x] Regression tests present and passing

**Dependencies**
- [x] `requirements.txt` complete, with version bounds
- [x] `numpy`, `pyproj`, `pyarrow` added (imported but previously unlisted)
- [x] `fiona` excluded in favour of `pyogrio`
- [x] No `packages.txt` needed
- [ ] Python 3.11 selected in Advanced settings — **you, at deploy time**

**File paths**
- [x] Portable throughout
- [x] Legacy hard-coded path converted to arguments

**GitHub repository**
- [ ] `git init` → commit → GitHub → push (section 4)
- [ ] `git ls-files | grep -iE "tracks_|merged_tracks"` prints nothing
- [ ] Repo size ~78 MB, no file over 100 MB
- [ ] Private unless you have verified no data was ever committed
- [ ] You have **admin** rights on the repo (required to deploy)

**Branch**
- [ ] `main`, pushed with `-u` so it tracks the remote

**Secrets**
- [x] None exist; nothing to configure
- [x] `.streamlit/secrets.toml` git-ignored for the future

**Streamlit configuration**
- [x] `.streamlit/config.toml` committed, upload cap 400 MB
- [x] Theme preserved
- [x] Cloud safeguards active when hosted

**Deployment**
- [ ] Repository / branch / `app.py` selected
- [ ] Build completes — watch for the first failing package if not

**Post-deployment testing**
- [ ] App loads, cloud banner shows
- [ ] Bundled boundaries and map templates detected
- [ ] Small run produces workbook, .docx, .pptx and maps
- [ ] Figures match a known local run
- [ ] Practical size ceiling established and noted
