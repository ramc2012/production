# CLAUDE.md: GGS Flow Validation & Digital Twin

This file is read by Claude Code at the start of every session. Keep it current.

## What this is

A well-to-tank **flow validation, reconciliation, allocation and digital-twin** engine for an oil gathering station (GGS) at ONGC Ankleshwar. The flow it covers:

> wells → test/group headers → separators → storage tanks → dispatch to CTF, with gas going to the compressor, flare and fuel.

It is a Python engine (`flowval/`) plus the **FlowTwin standalone application** (`app/`): FastAPI + SQLite backend, React frontend, background jobs, scheduler, Windows service packaging. Milestones M0-M6 and M8 (scripts) are built; M7 (live read-only connectors) is not. Status and open items: `docs/handoff/PROGRESS.md`. User and admin docs: `docs/USER_GUIDE.md`, `docs/ADMIN_GUIDE.md`.

**Owner:** Ramachandran (DGM, In-charge Instrumentation, ONGC Ankleshwar). He has no developer team and builds with Claude. Explain trade-offs plainly, keep the codebase small and maintainable by one person, and ask before adding heavy infrastructure.

## Non-negotiables

1. **Read-only towards plant systems.** No code path may write to a DCS, PLC, SCADA, OPC UA server, Modbus device or historian. The L5 "autonomous" loop runs against the internal plant **simulator** only. Connectors are read-only clients. A test must enforce this (see ROADMAP M2).
2. **Engine results must not change silently.** `tests/test_golden.py` locks the outputs against `tests/golden/baseline.json`. Refactors must keep it green. Regenerate the baseline (`python scripts/make_golden.py`) only for an intended, explained behaviour change.
3. **No Docker requirement.** Target is a Windows edge server or desktop on the ONGC intranet; Docker needs an enterprise licence and colleagues struggle to deploy it. The app must install and run as a native Windows service (macOS/Linux for development). Docker may be offered as an optional extra only.
4. **Offline-capable.** No runtime dependency on internet or CDNs: bundle frontend assets and vendor fonts, no telemetry.
5. **Units are explicit.** Column and field names carry units (`_m3`, `_sm3`, `_ksc`, `_degc`, `_pct`). Liquids are at 15 °C, gas at 15 °C / 101.325 kPa, pressure in kg/cm² gauge. Never mix units silently.
6. **Uncertainty is first-class.** Every measured or estimated quantity carries a U95 or σ. Do not drop it when moving data into the DB or API.

## Commands

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"   # engine + app + test tools
python scripts/dev.py verify                          # lint + engine tests + app tests + frontend build
python scripts/dev.py test                            # engine (unittest) then app (pytest tests/app)
python scripts/dev.py frontend                        # npm install + build into app/backend/static (offline check)
python scripts/dev.py perf                            # F2 timing: 60 wells x 30 days < 60 s (opt-in)
flowtwin --data-dir var seed-sample                   # load the synthetic GGS into a dev data folder
flowtwin --data-dir var serve                         # web app on http://127.0.0.1:8400
flowtwin --data-dir var run --kind twin               # queue a run and wait (also: create-user, reset-password, backup, restore)
python packaging/windows/build_bundle.py              # offline Windows installer bundle -> dist/
pip install -r requirements.txt                       # engine only: numpy, pandas, pyyaml, openpyxl (no SciPy)
python -m unittest discover -s tests                  # all engine tests (~50-70 s)
python -m unittest tests.test_golden                  # behaviour lock only (~35 s)
python -m flowval run  --network data/sample/network.yaml --data data/sample --config data/sample/config.yaml --out output
python -m flowval twin --network data/sample/network.yaml --data data/sample --config data/sample/config.yaml --out output
python scripts/generate_sample_data.py                # rebuild the synthetic GGS (deterministic seed)
python scripts/score_allocation.py                    # allocation error vs synthetic truth
python scripts/make_golden.py                         # ONLY for an intended behaviour change
```

Python 3.10+ (the target machine runs 3.10; development also on 3.13 with pandas 3). Keep code compatible with both: no `match`-heavy or 3.11+-only syntax, and no pandas deprecations (use `.eq(True)`, not `fillna(False).astype(bool)`).

## Architecture (current engine)

```
flowval/
  io.py, network.py, config.py   inputs: CSV schemas, network.yaml, tolerance/uncertainty defaults (deep-merged)
  stats.py, units.py, fluids.py  chi2/normal without SciPy, GUM propagation, CTL 54A, CTSh, Z, Gilbert choke, SRP PD
  dataquality.py                 L1 checks -> FindingLog + BadReadings {(date,obj,col): reason}
  welltests.py                   test validation, daily theoretical rates + covariance, choke VFM
  wellmodel.py, wellperf.py      Standing PVT, Vogel IPR, Beggs & Brill VLP, nodal, Pwf from FL/PIP; estimators, health
  separators.py, tanks.py        meter -> stock-tank volumes; TOV->GOV->GSV->NSV; dip-error signature
  reconciliation.py              DVR: WLS + projection, chi2 global, GLR group test, serial elimination, equivalence
  allocation.py                  allocation factors, proportional & DVR allocation
  engine.py                      orchestration -> Results dataclass (all tables are pandas DataFrames)
  report_html.py, report_excel.py, cli.py
  twin/  model.py (L0/L1 FacilityTwin, synchronise), predict.py (L3), prescribe.py (L4),
         autonomy.py (L5 simulator loop), levels.py (DNV-RP-A204 maturity), report.py, run.py
```

App (`app/`, see `docs/handoff/ARCHITECTURE.md` and `docs/ADMIN_GUIDE.md`):

```
app/backend/
  main.py (app factory, static SPA, CSP)  settings.py (FLOWTWIN_*)  db.py (SQLite WAL, schema_version)
  models.py      master data (versioned network/config), input batches + rows (original cell text),
                 runs, run_table (whole engine tables as JSON), finding, well_daily_result (+sd),
                 issue, test_override, recommendation, allocation_close, job, autonomy_run, user, audit_log
  engine_adapter.py   DB -> engine (rebuilds CSV text -> read_csv -> prepare_inputs, so DB runs == CLI)
  security.py    Argon2, cookie session + CSRF, roles viewer/engineer/admin, audit()
  services/      runs, issues, twin (what-if, recommendations, L5 sandbox), allocation, importer,
                 scheduler (daily run, dropzone, backup/restore), jobs (thread pool, progress, cancel), site
  api/           auth, core (dashboard, network, runs, jobs, findings, issues), assets, ops (allocation, twin),
                 data, admin
  connectors/    v2 read-only live connectors (empty; guarded by tests/app/test_readonly_guard.py)
app/frontend/    Vite + React + TS; pages/ per screen; components/Chart.tsx (own SVG charts), DataTable
packaging/windows/  build_bundle.py, install.ps1, uninstall.ps1, WinSW service xml
```

Engine additions for the app (M1): `engine.run_frames`, `run_window`, test overrides, `io.prepare_inputs`,
`network.network_from_dict`, `Results.to_records`, `config_hash`, `twin.run.run_twin_frames`,
`WellTwin.solve` memo. Tests for the app are pytest in `tests/app` (not picked up by unittest discover).

Data flow per production day: DQ → test registry → theoretical + well-parameter VFM → separator/tank measurement groups → DVR problem → solve → findings → allocation. A post-pass handles persistence (bias suspects, consolidation). `engine.run()` returns `Results`; `twin.run.run_twin()` returns a context dict.

## Domain glossary

| Term | Meaning |
|---|---|
| GGS | Group Gathering Station |
| CTF | Central Tank Farm |
| DPR | Daily Production Report |
| THP / FLP / CHP | Tubing-head / flow-line / casing pressure |
| SRP | Sucker-rod pump |
| SPM | Strokes per minute |
| VE | Pump volumetric efficiency |
| DFL | Dynamic fluid level |
| PI | Productivity index (m³/d per ksc) |
| Pr / Pb / Pwf | Reservoir / bubble-point / flowing bottom-hole pressure |
| SBHP | Static bottom-hole pressure survey |
| BS&W | Basic sediment & water |
| WC | Water cut |
| GOR | Gas-oil ratio |
| TOV / GOV / GSV / NSV | Total observed / gross observed / gross standard / net standard volume |
| CTL / CTSh | Temperature correction for liquid / for tank shell |
| DVR | Data validation & reconciliation |
| AF | Allocation factor |
| VFM | Virtual flow meter |
| MF | Meter factor |
| U95 | Expanded uncertainty (k = 2) |
| Twin levels L0-L5 | DNV-RP-A204: standalone, descriptive, diagnostic, predictive, prescriptive, autonomous |

## Conventions

- Keep the engine pure: no I/O inside calculation modules beyond what `io.py` and `network.py` already do. New app code goes in a separate package (see ARCHITECTURE) that *calls* the engine.
- **Findings** are the system's output contract: `Finding(date, layer, severity, object_type, object_id, check, message, value, limit, action)`. New checks add a `check` name and a recommended action.
- Thresholds live in `config.py` DEFAULTS, overridable per site via YAML. Never hard-code a tolerance in logic.
- Tests use stdlib `unittest`. Synthetic data with planted faults is the acceptance harness: a new detector needs a planted fault in `scripts/generate_sample_data.py` and an assertion in `tests/test_end_to_end.py`.
- The user's folder does not allow deletes from the Cowork sandbox. Locally, on his Mac or Windows box, normal git works.

## Known limitations and tech debt (be honest about these in the UI too)

- Well-level gross errors are only identifiable with independent well data (VFM or samples); otherwise they are reported as ambiguous or unconfirmed.
- L3 forecast accuracy is unproven (only ~4 months of synthetic history). L4 has no economic objective yet. L5 is simulator-only by design.
- Daily runs recompute the whole period (twin run ~17 s for 12 wells / 30 days, ~46 s for 60 wells). `run_window` exists for a daily-only mode; the centred spike check means a daily run can differ from the month-end run (month close should use a full-month run).
- Monthly allocation U95 is shown as bounds (independent vs fully correlated days); the engine does not model day-to-day error correlation.
- Jobs run in threads (not processes) to share the cached twin; a heavy run slows API responses a little.
- No Alembic: `db.migrate()` has a schema_version table and forward steps; add a step when a model changes.
- The Windows installer scripts have not been run on Windows yet (no Windows machine in development).
- The Excel DPR import expects sheets laid out like the CSV templates (with column mapping); the real DPR format is an open decision.
- The Python package name `app` is generic; fine inside FlowTwin's private venv, rename before publishing to an index.
- Pre-existing unused imports in `flowval/` are exempted in ruff; tidy them in a separate behaviour-neutral commit.

## Where to start

Read `docs/handoff/PROGRESS.md` (what is done, what is open), then PRD → ARCHITECTURE → ROADMAP for context. Next work: owner's open decisions, a Windows VM install test (M8), then M7 read-only connectors. Each milestone ends with its acceptance checks green and a short note to the owner.
