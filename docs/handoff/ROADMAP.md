# Roadmap: milestones with acceptance checks

Work strictly in order. Each milestone ends with:

- (a) all tests green, including `tests/test_golden.py`;
- (b) the acceptance checks below demonstrated;
- (c) a commit with a clear message;
- (d) a 5-line note to the owner covering what changed, how to try it, and any open decisions.

Keep each milestone shippable.

## M0: Repository hygiene (½ day)

- `pyproject.toml` (package `flowval` + optional extras `app`, `dev`); keep `requirements.txt` for the engine.
- A `make`-free task runner: `scripts/dev.py` with sub-commands `test`, `golden`, `lint`, `run-sample`, `twin-sample`. Plain Python, so it works on Windows.
- Ruff (lint + format) configured conservatively. Do not mass-reformat the engine in the same commit as logic changes.
- **Accept:** fresh clone → `pip install -e .[dev]` → `python scripts/dev.py test` green on macOS and Windows.

## M1: Engine as a library (1-2 days)

- Add `engine.run_frames(net, data, cfg, start, end)`. Make `run()` a thin wrapper around it.
- Make `twin.model.build_twin` and `twin.run.run_twin` accept in-memory frames.
- Add `flowval.__version__`, a `config_hash(cfg)` function, and `Results.to_records()`, which converts each table to plain dicts for DB insertion. NaN must become None.
- Add an incremental window mode for daily runs. Prove it against a full run: the new day's findings and allocation match.
- **Accept:**
  - golden test green;
  - new tests for `run_frames` equivalence and the incremental window;
  - no behaviour change.

## M2: Backend skeleton + persistence (3-4 days)

- FastAPI app factory, settings, SQLAlchemy models (ARCHITECTURE § Data model), Alembic baseline, SQLite WAL.
- Engine adapter in both directions: DB → engine inputs, and Results → DB rows.
- Importer for the CSV templates, with validation and preview. `scripts/seed_sample.py` loads `data/sample`.
- Endpoints for auth (local users, roles), runs (sync for now), findings and wells (read).
- **Read-only guard test** (ARCHITECTURE § Safety).
- **Accept:**
  - seeding the sample and triggering a run gives findings identical to the golden baseline (F1);
  - every result row has a `run_id` (F3);
  - the guard test passes;
  - API tests run with pytest + httpx.

## M3: Frontend skeleton (3-4 days)

- Vite + React + TS. Layout, login, theme tokens copied from `report_html.py`, light and dark.
- Pages: Dashboard, Exceptions (read-only list for now), Network diagram, Run history.
- FastAPI serves the built assets. No CDN or external font URLs.
- **Accept:**
  - `npm run build` output is served by the backend;
  - the bundle has no external URLs (F10);
  - the dashboard numbers equal the API;
  - it works on phone width.

## M4: Operations workflows (4-5 days)

- Issue lifecycle for findings (F4); test overrides that feed the next run (F5).
- Well detail tabs; meters, tanks and reconciliation pages.
- Monthly allocation with close/lock and Excel export (F9).
- Run reports: the existing HTML and Excel files are downloadable per run.
- Audit log on every mutation.
- **Accept:**
  - F4, F5 and F9 tests;
  - the audit log has entries for each action;
  - an engineer can do the "09:00 routine" (review → acknowledge → assign → plan tests) without leaving the app.

## M5: Digital-twin area (4-5 days)

- L0-L5 tabs ported from `twin/report.py` to React.
- `POST /twin/whatif`: sliders for choke, lift gas, SPM and shut-in, returning the per-well and facility deltas in < 3 s (F6). Cache the synchronised twin per run.
- Recommendations with accept/decline/implemented status (F7); a decided recommendation expires on the next run if its premise changed.
- L5 sandbox as a background job with progress, cancel and a stored audit log (F8). UI banner: "Simulation only".
- **Accept:**
  - F6, F7 and F8 tests;
  - twin numbers equal the golden baseline;
  - the guard test still passes.

## M6: Ingestion & scheduling (3-4 days)

- Upload wizard for CSV/Excel with column mapping and saved mappings. Watched drop folder.
- Adapter for the owner's DPR consolidation pipeline output (agree the format with the owner first).
- APScheduler daily run at the configured time with a retry, plus an e-mail or summary-file hook (outbound, opt-in).
- Data-quality page per batch.
- **Accept:**
  - dropping the sample files in the folder produces a run automatically;
  - a malformed file is rejected with a clear report;
  - F2 timing holds at 50 wells (generate a larger synthetic set).

## M7: Live read-only connectors (v2, 4-6 days)

- `tag_map` UI. Adapters: OPC UA read (asyncua client, subscription or poll), PI Web API read, Modbus TCP read.
- Aggregation to hourly and daily values with quality flags. An hourly twin sync job that refreshes L1 and the L3 look-ahead.
- **Accept:**
  - works against a local OPC UA test server (asyncua's example server) and a Modbus simulator;
  - the guard test proves no write calls exist;
  - connector failure degrades gracefully, with stale-data badges.

## M8: Windows packaging & operations (3-4 days)

- PyInstaller one-folder build, WinSW/NSSM service, Inno Setup installer, data in `%ProgramData%\FlowTwin`.
- HTTPS with a provided certificate.
- Nightly backup with retention, a restore command, log rotation, an upgrade path that runs Alembic migrations.
- User guide (`docs/USER_GUIDE.md`) and admin guide.
- **Accept:**
  - install on a clean Windows 10/11 VM without Python or Docker;
  - the service starts on boot;
  - backup/restore round-trip test;
  - uninstall leaves the data folder intact.

## Later

- Multi-GGS and CTF roll-up.
- AD/LDAP login.
- Economic objective in L4.
- Forecast back-testing (needs 6-12 months of history).
- Alert digest.

## Open decisions for the owner (ask, don't assume)

1. Whether the first deployment target is a Windows server in the GGS control room or an engineer's laptop.
2. The DPR source format available today: Excel? Output of the WhatsApp-to-PDF consolidation pipeline?
3. Whether the historian is accessible (PI / Wonderware / other) and which protocol Infocom will allow from the DMZ.
4. Who approves L4 recommendations (designation), and whether accepted advice must be printed or signed.
5. The station code and naming for the first real site, and whether test separator data is available per test.
