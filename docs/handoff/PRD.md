# PRD: FlowTwin, a standalone GGS flow validation & digital-twin application

## 1. Problem

At an ONGC Ankleshwar gathering station (GGS), daily production is reported through the DPR: well hours, well tests, separator meters, tank dips and dispatch. Production is allocated to wells from stale tests using a single allocation factor. Meter faults, wrong dips, shut-in wells reported as flowing, and wells whose behaviour changed since their last test all go undetected. Engineers find out weeks later, if at all.

The `flowval` engine already solves the analysis. It validates every layer, reconciles the measurements, allocates with uncertainty, and runs a digital twin (DNV-RP-A204 levels 0-5). On a synthetic station it detected all 17 planted faults and cut per-well daily allocation error from 13 % to 5 %. But it is a command-line tool that writes static reports. FlowTwin turns it into an application people use every day.

## 2. Users and jobs

| User | Jobs | Key screens |
|---|---|---|
| **GGS production engineer** (daily) | Check yesterday's numbers by 09:00; act on exceptions; plan well tests; approve or decline optimisation advice | Dashboard, Exceptions, Wells, Recommendations |
| **Instrumentation / maintenance** (owner's team) | Find drifting or failed meters, missing proving, frozen transmitters; plan calibration | Meters, Exceptions (DQ / reconciliation), Maintenance list |
| **Well services / reservoir** | Well-test quality, PI trends, pump health, SBHP, water breakthrough | Well detail, Tests, Well performance |
| **Asset / monthly allocation** | Monthly well allocation with uncertainty; audit trail | Allocation, Reports |
| **Admin** (owner) | Network model, strapping tables, tag map, tolerances, users | Admin |

## 3. Scope

### In scope (v1)

1. **Data in:**
   - Upload of the existing CSV templates (`docs/DATA_DICTIONARY.md`) and Excel DPR.
   - A watched folder (drop zone).
   - Optional ingest of the DPR consolidation pipeline's output.
   - Validation with a preview before commit.
2. **Daily run:**
   - Scheduled at a configurable time (default 06:30 for the 06:00 day) and on demand.
   - Results persisted with engine version and config hash.
   - Re-running a day creates a new run version and never overwrites the old one.
3. **Exceptions workflow:**
   - The engine's findings become tickets: open → acknowledged → resolved, with assignee, comment and due date.
   - Repeats link to the same ticket.
   - Filter by layer, severity and object.
4. **Asset views:**
   - Network diagram with live status.
   - Detail pages for wells, separators, meters and tanks.
5. **Well tests:**
   - Register with the engine's verdict.
   - Engineer override (accept / reject / provisional) with mandatory reason, audit-logged and fed back to the next run.
6. **Reconciliation:** daily χ² view, gross errors, ambiguous candidates, bias suspects.
7. **Allocation:**
   - Daily and monthly per-well allocation: theoretical, proportional AF and reconciled, with uncertainty.
   - Excel/PDF export.
   - Monthly close lock.
8. **Digital twin (levels 0-5)**, with one area per level:
   - L0 asset model;
   - L1 live state and synchronisation;
   - L2 diagnostics summary;
   - L3 forecasts and look-ahead;
   - L4 interactive what-if (choke / lift gas / SPM / shut-in) plus recommendations with accept/decline;
   - L5 autonomy **sandbox**: simulation runs only, with the audit log.
9. **Reports:** the existing HTML/Excel reports remain downloadable per run.
10. **Admin:**
    - Network editor (wells, separators, meters, tanks, routing).
    - Strapping-table upload, well static data, reservoir surveys.
    - Tolerance and uncertainty config (versioned).
    - Users and roles.
11. **Audit log** of every human action and config change.

### Later (v2+)

- Read-only live connectors (OPC UA, PI Web API, Modbus TCP) with hourly twin sync.
- Several GGS in one install and roll-up to CTF.
- Active Directory login.
- Economic objective (oil price, gas value, power).
- Forecast back-testing.
- Mobile-friendly alert digest (e-mail/WhatsApp summary). It must be outbound only and opt-in.

### Out of scope (permanently, without an explicit separate project)

- **Any write to plant control systems.** L5 stays a simulator sandbox.
- Replacing the DPR, SCADA or the historian.

## 4. Functional requirements (testable)

| ID | Requirement | Acceptance |
|---|---|---|
| F1 | Import the sample dataset through the UI and get the same findings as the CLI | Golden comparison on imported data passes |
| F2 | Scheduled daily run completes for 50 wells / 30 days in < 60 s on a 4-core laptop | Timing test |
| F3 | Every result row traces to a run (engine version, config hash, input batch IDs) | DB constraint plus API check |
| F4 | Finding lifecycle with assignee, comment and audit | API tests for transitions; illegal transitions rejected |
| F5 | Test override changes the next run's test status and is shown with the reason | Integration test |
| F6 | What-if returns the predicted well and facility deltas in < 3 s for a single-well change | API timing test |
| F7 | Recommendation accept/decline is recorded and never touches a plant system | Read-only guard test plus audit entry |
| F8 | L5 sandbox runs as a background job with progress and is cancellable | Job API test |
| F9 | Monthly allocation export reproduces the engine's numbers to 0.01 m³ | Export test |
| F10 | All pages work without internet | Build check: no external URLs in the bundle |

## 5. Non-functional requirements

- **Platform:** Windows 10/11 or Windows Server as the primary target, running as a Windows service; macOS/Linux for development. No Docker requirement.
- **Footprint:** < 1 GB RAM in normal use. Single process plus a worker pool. SQLite by default (PostgreSQL optional).
- **Security:**
  - Login with roles: viewer, engineer, admin.
  - Argon2 password hashes; HTTPS (enterprise or self-signed certificate); CSRF protection.
  - Intranet only, never exposed to the internet.
  - OT segregation: connectors read through a DMZ / historian, never directly from PLCs on the control network.
- **Reliability:**
  - Runs are idempotent and versioned.
  - Nightly DB backup with a retention setting.
  - Restore procedure documented and tested.
- **Maintainability:**
  - One developer working with Claude.
  - Typed Python and TypeScript.
  - The engine stays a library with its own tests; the golden lock is mandatory.
- **Usability:**
  - Engineers' language (ksc, m³/d, /64, SPM).
  - Light and dark themes.
  - Readable on a phone for the daily dashboard.
- **Data retention:** raw inputs and run results kept indefinitely (small volume); audit log never deleted.

## 6. Success measures

- By 09:00 each day, yesterday's validated numbers and a short exception list are ready without manual spreadsheet work.
- Meter faults are found within 3 days (today: weeks).
- Wells tested on priority (the uncertainty-ranked schedule) and fewer stale tests (> 30 days).
- Monthly allocation signed off from the app with its uncertainty statement.
