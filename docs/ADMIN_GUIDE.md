# FlowTwin admin guide

## 1. Install on Windows (production)

Build the offline bundle on any PC with internet (once per version), then copy the zip to the server:

```bash
python packaging/windows/build_bundle.py        # needs Python 3.10+ and Node.js 18+; writes dist/FlowTwin-<v>-win64.zip
```

On the server (Windows 10/11 or Server 2016+, 64-bit), unzip, open PowerShell **as administrator** in the
folder and run `install.ps1` (see `packaging/windows/README.txt` for options):

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1                                  # local only, http://127.0.0.1:8400
powershell -ExecutionPolicy Bypass -File .\install.ps1 -BindHost 0.0.0.0 -Port 8443 -TlsCert C:\certs\ggs.pem -TlsKey C:\certs\ggs.key -OpenFirewall
```

What it does: installs a private Python 3.12 under `C:\Program Files\FlowTwin\python`, installs FlowTwin
from the bundled wheels (no internet), creates `C:\ProgramData\FlowTwin`, and registers the **FlowTwin**
service (automatic start, restart on failure, runs as `NT AUTHORITY\LocalService`).

**Upgrade:** run `install.ps1` from the newer bundle. The service stops, the program is replaced, the data
folder is untouched, and database changes are applied when the service starts.
**Uninstall:** `uninstall.ps1` removes the service and program; the data folder stays.

> The Windows scripts were written for and checked against Windows conventions, but they have not yet been
> run on a Windows machine. Try them on a test VM first (ROADMAP M8 acceptance).

## 2. Run on a Mac/Linux (development)

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
python scripts/dev.py frontend                      # build the React app into app/backend/static
.venv/bin/flowtwin --data-dir var seed-sample       # optional: the synthetic demo station
.venv/bin/flowtwin --data-dir var serve             # http://127.0.0.1:8400
```

Frontend with hot reload: `cd app/frontend && npm run dev` (proxies `/api` to port 8400).

## 3. First sign-in and users

The first start creates user `admin`; the password is in `<data>\initial_admin_password.txt`. Sign in,
choose a new password (at least 10 characters), then delete the file. Add users under **Admin → Users**.
Each new user gets a one-time password, shown once, and must change it at first sign-in.
Forgotten password: **Reset password** (Admin), or on the server:
`flowtwin.exe reset-password <name>` (with `FLOWTWIN_DATA_DIR` set).

## 4. Settings (`<data>\flowtwin.env`, restart the service after a change)

| Setting | Default | Meaning |
|---|---|---|
| `FLOWTWIN_HOST` / `FLOWTWIN_PORT` | 127.0.0.1 / 8400 | Listen address. `0.0.0.0` serves the intranet. |
| `FLOWTWIN_TLS_CERT` / `FLOWTWIN_TLS_KEY` | – | PEM files: enables HTTPS and secure cookies. |
| `FLOWTWIN_SITE_CODE` | GGS-1 | Station code shown in exports. |
| `FLOWTWIN_DAILY_RUN_TIME` | 06:30 | Default time of the daily run (also editable in Admin → Schedule). |
| `FLOWTWIN_DAILY_RUN_DAYS` | 30 | Days covered by the daily twin run. |
| `FLOWTWIN_BACKUP_KEEP` | 14 | Nightly backups kept. |
| `FLOWTWIN_WORKERS` | 2 | Background job threads. |
| `FLOWTWIN_DB_URL` | SQLite in the data folder | `postgresql+psycopg://…` for PostgreSQL (needs the driver installed). |

## 5. Network model and configuration

**Admin → Network model**: edit `network.yaml` (wells, separators, meters, tanks, dispatch, gas disposal,
lift-gas header) and the well master table, and upload each tank's strapping table (`level_mm,volume_m3`).
The engine builds the model before saving, so a wrong reference (an unknown meter, say) is rejected with
the reason. **Admin → Configuration**: tolerances, uncertainties and twin settings as YAML. Unknown keys
and wrong types are rejected; anything left out uses the built-in default (the *Show defaults* view).
Every save is a new version with a comment, and each run records the versions it used.

## 6. Schedule, drop folder, backups

**Admin → Schedule & backup**:
- Daily twin run (default 06:30) over the last 30 days up to the newest day with a closing tank gauge.
  It is skipped if that day was already run with the same input batches, and retried once after 30 min.
- Drop folder `<data>\dropzone`: valid files are imported and followed by a run. Files are moved to
  `processed\` or `rejected\` (with a report).
- Nightly SQLite backup at 02:00 into `<data>\backups` (14 kept). **Back up now** makes one immediately.
  Copy the backups folder to another machine or share regularly.

**Restore** (service stopped):

```powershell
Stop-Service FlowTwin
$env:FLOWTWIN_DATA_DIR = "C:\ProgramData\FlowTwin"
& "C:\Program Files\FlowTwin\venv\Scripts\flowtwin.exe" restore flowtwin-20261011-020000.sqlite
Start-Service FlowTwin
```

The replaced database is kept as `flowtwin.before-restore.sqlite`. Twin what-if needs the run's model
file in `<data>\runs\<id>`; after restoring onto a new machine, start a twin run once.

## 7. Audit and traceability

**Admin → Audit log** lists every sign-in, upload, commit, run, issue change, test override, recommendation
decision, month close/re-open, user change and configuration change. It is never deleted. Every result row
belongs to a run that records the engine version, configuration hash, network/config versions, input batches
and test overrides used.

## 8. Security and the OT boundary

- FlowTwin has **no write path** to any plant system. An automated test (`tests/app/test_readonly_guard.py`)
  fails the build if code calls an OPC UA or Modbus write API, uses a non-GET HTTP verb in the connectors
  package, or imports a plant-protocol library elsewhere.
- Install it on the IT/DMZ side. Future live connectors will read from the historian or a DMZ OPC UA server,
  never from the control network directly.
- Passwords: Argon2 hashes; sessions in HttpOnly cookies; CSRF token on every change; 5 failed sign-ins
  lock that user name for 5 minutes. Use HTTPS when serving the intranet.
- The app needs no internet at runtime: no CDN, web fonts or telemetry (checked at build time).

## 9. Troubleshooting

| Symptom | Check |
|---|---|
| Service does not start | `<data>\logs\FlowTwin.err.log`; port already in use; certificate paths |
| "No complete production day" | Tank gauges for day D **and** D+1 at 06:00 are needed |
| Run failed | Runs & reports → the run → log; usually a network model / data mismatch |
| What-if says "no twin model" | Start a twin run (the model file is per run) |
| Page blank after upgrade | Refresh the browser (Ctrl+F5) |
