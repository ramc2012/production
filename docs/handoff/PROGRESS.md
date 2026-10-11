# Progress notes

## 2026-10-11 · M0–M6 built, M8 scripted (branch `standalone-app`)

**What changed**
- The engine is now a library (`run_frames`, windowed daily runs, engineer test overrides, in-memory network
  and twin). It produces the same results: the golden lock stayed green at every step and the baseline was
  not regenerated. A memo on `WellTwin.solve` makes the twin about 26 % faster without changing any number.
- **FlowTwin backend** (FastAPI + SQLite): versioned network/config, input batches with validation →
  preview → commit, background runs with progress and cancel, findings → issue tickets, test overrides,
  recommendations, what-if, the L5 simulator sandbox, monthly allocation close/export, users/roles, audit
  log, daily scheduler, drop folder, nightly backup and restore.
- **FlowTwin frontend** (React): 15 screens covering the 09:00 routine, the digital twin L0–L5 and admin;
  light/dark, phone layout, works offline.
- **Windows**: offline installer bundle (private Python 3.12, all wheels, WinSW service, LocalService
  account) with install/upgrade/uninstall scripts; user and admin guides.

**How to try it**
```bash
.venv/bin/flowtwin --data-dir var seed-sample && .venv/bin/flowtwin --data-dir var serve   # http://127.0.0.1:8400
```
The admin password is in `var/initial_admin_password.txt`.

**Acceptance checks**

| Check | Status | Evidence |
|---|---|---|
| Golden lock green throughout | done | `tests/test_golden.py`; baseline unchanged |
| M1: `run_frames` equivalence, incremental window | done | `tests/test_library.py` (window equals full run for the target day except period-level findings and centred-spike wording) |
| F1: sample imported through the app gives the CLI's findings | done | `tests/app/test_golden_db.py` (findings, gross errors, test statuses, volumes, twin levels/L3/L4/L5 against the baseline) |
| F2: 50 wells × 30 days < 60 s | done (on the dev Mac) | `scripts/dev.py perf`: 60 wells, validation + full twin, 46.5 s |
| F3: every result row traces to a run | done | `test_every_result_traces_to_a_run` |
| F4: issue lifecycle, illegal transitions rejected, audit | done | `test_issue_lifecycle`, `test_repeat_run_links_to_same_issue` |
| F5: override changes the next run, reason shown | done | `test_override_feeds_next_run` |
| F6: what-if < 3 s | done | `test_whatif_fast_and_consistent` |
| F7: recommendation decisions recorded, no plant write | done | `test_recommendation_decisions` + read-only guard |
| F8: L5 sandbox as a cancellable background job | done | `test_autonomy_sandbox_job_and_cancel` |
| F9: allocation export reproduces the engine to 0.01 m³ | done | `test_allocation_export_reproduces_engine` |
| F10: no external URLs in the bundle | done | `npm run build` runs `scripts/check-offline.mjs` |
| Read-only guard | done | `tests/app/test_readonly_guard.py` |
| Malformed upload rejected with a clear report; drop folder → run | done | `test_malformed_upload_rejected`, `test_dropzone_imports_valid_and_rejects_bad_files` |
| Backup/restore round trip | done | `test_backup_restore_round_trip` |
| Phone width, light/dark | done | checked in the browser at 375 px |
| M8: clean Windows 10/11 VM install, service on boot, uninstall keeps data | **not done** | scripts written; no Windows machine was available. Bundle build and an offline wheel install were tested on macOS |
| M7: live read-only connectors (OPC UA / PI / Modbus) | **not started** | v2 scope; `tag_map` table and the guarded `connectors/` package are ready for it |

**Decisions taken without the owner (easy to change)**
- Packaging uses a private Python + wheels + WinSW instead of PyInstaller + Inno Setup: simpler for one
  person to maintain, and no PyInstaller issues with numpy/pandas.
- No Alembic: SQLite with a small `schema_version` table and forward steps in `db.migrate()`.
- Jobs run in threads, not processes, so the cached twin is shared by what-if requests.
- Only ALARM and WARN findings open tickets; INFO findings stay in the register.
- Monthly allocation uncertainty is shown as two U95 bounds (independent vs fully correlated days).
- A newer data batch overrides only the columns it contains, so a correction file never blanks readings.
- The scheduled daily job is a 30-day twin run ending on the newest day with a closing tank gauge.

**Open decisions for the owner** (from ROADMAP, still open)
1. First target: GGS control-room Windows server or an engineer's laptop?
2. DPR source format today: Excel (which layout?) or the consolidation pipeline output?
3. Historian access and protocol through the DMZ (needed for M7).
4. Who approves L4 recommendations (designation), and must accepted advice be printed/signed? There is a
   *Print sheet* button on Recommendations for now.
5. Station code and naming of the first real site; is test-separator data available per test?

**Next**
Install the bundle on a Windows test VM (M8 acceptance), then load one month of real DPR data for the first
station with the owner, then M7 connectors.
