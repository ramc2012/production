# Target architecture: FlowTwin standalone application

## Guiding decisions

| Decision | Choice | Why | Rejected alternatives |
|---|---|---|---|
| Backend | **Python 3.10+, FastAPI, Uvicorn** | The engine is Python. The owner already uses FastAPI (gas-compressor twin) | Django (heavier); a Node backend (rewriting the engine) |
| Engine coupling | **In-process library** (`flowval`), called through an adapter | No duplication. The golden test protects behaviour | Microservice (needless for one site) |
| Database | **SQLite (WAL) by default**, PostgreSQL optional through the same SQLAlchemy 2.0 models + Alembic | Zero-admin on Windows, tiny data volume (daily rows) | InfluxDB as the primary store: the data is relational daily records, and high-rate data stays in the historian |
| Jobs | **In-process scheduler (APScheduler) + `job` table + `ProcessPoolExecutor`** | No Redis/Celery to install on Windows | Celery/RQ (needs a broker) |
| Frontend | **React + TypeScript + Vite**, TanStack Query/Table, a small chart layer (uPlot or Recharts), hand-drawn SVG network | The owner's known stack; static build served by FastAPI, fully offline | Server-rendered templates (weak for what-if interactivity) |
| Packaging | **PyInstaller one-folder build + WinSW (or NSSM) Windows service + Inno Setup installer** | Single installer, no Docker, no system Python needed | Docker (licence and deployment friction) |
| Auth | Local users with roles (argon2); AD/LDAP later | Works offline; AD needs Infocom coordination | — |
| Live data (v2) | Read-only adapters: `asyncua` (OPC UA client), PI Web API (HTTPS GET), `pymodbus` read-only | All read-only by construction | Any write-capable SDK usage |

## Layout (proposed)

```
flow_validation/
  flowval/                  # ENGINE, unchanged API + small additions (M1), still pip-installable
  app/
    backend/
      main.py               # FastAPI app factory, static files, lifespan (scheduler start/stop)
      settings.py           # pydantic-settings: data dir (%ProgramData%\FlowTwin), DB URL, schedule, TLS
      db/                   # SQLAlchemy models, session, Alembic migrations
      adapters/
        engine_adapter.py   # DB rows <-> flowval Network/DataFrames; Results -> DB rows
        importers/          # csv_templates.py, dpr_excel.py, dpr_pipeline.py, folder_watch.py
        connectors/         # (v2) opcua_read.py, pi_webapi_read.py, modbus_read.py - READ ONLY
      services/             # runs.py, findings.py, tests_override.py, allocation.py, twin.py, jobs.py, audit.py
      api/                  # routers: auth, sites, network, data, runs, findings, wells, meters, tanks,
                            #          reconciliation, allocation, twin, jobs, admin, reports
      security.py           # auth, roles, CSRF
    frontend/               # Vite React TS app; build output -> app/backend/static
  packaging/windows/        # pyinstaller spec, winsw xml, inno setup script, service install docs
  tests/                    # engine tests (existing) + app tests (pytest is fine for app/)
```

## Engine integration (M1)

Today `engine.run(network_path, data_dir, config, start, end)` loads files itself. Split it without changing behaviour:

```python
def run_frames(net: Network, data: dict[str, pd.DataFrame], cfg: dict,
               start: str | None = None, end: str | None = None) -> Results: ...
def run(network_path, data_dir, config=None, start=None, end=None) -> Results:   # unchanged wrapper
    return run_frames(load_network(network_path), load_inputs(data_dir), _cfg(config), start, end)
```

Do the same for the twin: `build_twin(res, data)` should accept frames, not a folder. Add `flowval.__version__` to every persisted run. **`tests/test_golden.py` must stay green after every step.**

Performance: the engine recomputes all days. For the daily job, run a **window** (the target day plus enough history for the test registry, the frozen and spike windows, and persistence: default the last 35 days), but persist only the new day's rows plus the period-level outputs. Verify against a full run in a test.

## Data model (SQLAlchemy, first cut)

**Master data (versioned with `valid_from` / `valid_to`):**
- `site(id, name, timezone, day_start)`
- `well(id, site_id, code, lift, separator_id, plunger_in, stroke_in, lift_gas_meter_id, …)`
- `well_static(well_id, valid_from, perf_depth_m, tubing_depth_m, tubing_id_in, casing_id_in, valve_depth_m, pump_depth_m, reservoir_temp_degc, bubble_point_ksc)`
- `separator(id, site_id, code, kind, phases, orientation, diameter_m, length_m, liquid_volume_m3, shrinkage_factor, oil_meter_id, water_meter_id, gas_meter_id)`
- `meter(id, site_id, code, phase, type, meter_factor, u95_pct, last_proved, proving_interval_days)`
- `tank(id, site_id, code, insulated, shell_alpha, calibration_temp_c, safe_fill_mm)`
- `strapping_point(tank_id, level_mm, volume_m3)`
- `gas_disposal(site_id, meter_id, role)`
- `tag_map(id, source, tag, object_type, object_id, attribute, unit, scale, offset, aggregation)` (used from v2)
- `config_version(id, site_id, yaml, created_by, created_at, hash)`

**Inputs** (one table per CSV template; each row carries `batch_id`; the newest batch wins per natural key):
- `well_daily`, `well_test`, `well_test_reading`, `meter_daily`, `separator_daily`
- `tank_gauge`, `tank_drain`, `dispatch_daily`, `reservoir_survey`
- `input_batch(id, source, filename, uploaded_by, at, rows, status, validation_report)`

**Results** (all keyed by `run_id`):
- `run(id, site_id, kind[validation|twin], period_start, period_end, engine_version, config_version_id, input_batches, status, started, finished, log)`
- `finding(run_id, date, layer, severity, object_type, object_id, check, message, value, limit, action)` plus `issue(id, site_id, object_id, check, state, assignee, due, first_seen, last_seen, comments…)`. Findings link to issues by (object_id, check); repeats update `last_seen`.
- `test_eval`, `test_override(test_id, status, reason, by, at)`
- `well_daily_result` (theoretical / prop / dvr oil, water, gas, VFM, estimators, tuning)
- `dvr_day`, `dvr_variable`, `dvr_constraint`, `tank_daily`, `separator_daily_result`, `allocation_summary`, `bias_suspect`
- `twin_snapshot(run_id, json)`, `forecast(run_id, well_id, date, p10, p50, p90)`
- `recommendation(id, run_id, well_id, kind, current, proposed, predicted_gain, status[proposed|accepted|declined|implemented|expired], decided_by, decided_at, comment)`
- `autonomy_run(id, run_id, mode, params, result_json)`, `autonomy_log(...)`
- `audit_log(id, at, user, action, object, before, after)`

## API (REST, JSON; `/api/v1`)

| Area | Endpoints |
|---|---|
| Auth | `POST /auth/login`, `POST /auth/logout`, `GET /auth/me` |
| Network | `GET/PUT /sites/{id}/network` (wells, separators, meters, tanks, routing), `POST /tanks/{id}/strapping` |
| Data | `POST /data/upload` (validate → preview), `POST /data/batches/{id}/commit`, `GET /data/batches`, `GET /data/quality?date=` |
| Runs | `POST /runs` (validation\|twin, period), `GET /runs`, `GET /runs/{id}`, `GET /runs/{id}/report.html`, `GET /runs/{id}/report.xlsx` |
| Findings | `GET /findings?date&layer&severity&object`, `GET /issues`, `PATCH /issues/{id}` (state, assignee, comment) |
| Wells | `GET /wells`, `GET /wells/{id}` (daily results, tests, performance, forecast, estimators), `GET /tests`, `POST /tests/{id}/override` |
| Meters, tanks, reconciliation | `GET /meters/{id}`, `GET /tanks/{id}`, `GET /reconciliation?date=` |
| Allocation | `GET /allocation?month=`, `POST /allocation/{month}/close`, `GET /allocation/{month}/export.xlsx` |
| Twin | `GET /twin/levels`, `GET /twin/state`, `GET /twin/forecast`, `POST /twin/whatif` (body: setpoint changes), `GET /twin/recommendations`, `PATCH /twin/recommendations/{id}`, `POST /twin/autonomy` (sandbox job) |
| Jobs | `GET /jobs/{id}` (progress), `DELETE /jobs/{id}` (cancel) |
| Admin | users, config versions, schedule, backup/restore, `GET /audit` |

## Frontend pages

1. **Dashboard:** KPI tiles (oil, water, gas, AFs, χ² pass, open alarms), twin ladder, yesterday's top exceptions.
2. **Network:** the SVG network diagram with status, clickable to detail pages.
3. **Exceptions:** register with filters, issue workflow, bulk acknowledge.
4. **Wells:** list plus detail tabs: Production (theoretical / allocated / reconciled), Tests, Performance (PI, Pwf, estimators, fluid level), Forecast, What-if.
5. **Tests:** register and override dialog.
6. **Meters:** bias trend, proving status, maintenance priority.
7. **Tanks:** NSV chain per gauge, balance, dip-error flags.
8. **Reconciliation:** daily χ², isolated gross errors, ambiguous sets.
9. **Allocation:** monthly table, close and export.
10. **Digital twin:** L0-L5 tabs. Reuse the existing `twin/report.py` content and charts.
11. **Data:** upload wizard, batches, data quality.
12. **Admin:** network editor, config editor (YAML with validation), users, schedule, backup.

Port the visual language of the existing HTML reports: tokens, status badges with icon + label, the validated chart palette, light/dark themes.

## Safety and OT boundary

- The connectors package exposes **read functions only**. A test scans `app/` and `flowval/` for write APIs:
  - `asyncua` write methods (`write_value`, `set_value`, `write_attribute`);
  - `pymodbus` write functions (`write_register(s)`, `write_coil(s)`);
  - HTTP verbs other than GET towards connector hosts.
  
  The test fails if any is found.
- L5 runs only in `services/twin.py` against `make_plant()`. The UI labels it "Simulation sandbox".
- Deployment: the app server sits on the IT/DMZ side and reads from the historian or an OPC UA server in the DMZ. It never reaches the control network directly (same principle as the AHWR edge box, with dual NICs and no IT-to-PLC path).
