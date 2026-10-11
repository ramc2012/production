# CLAUDE.md: GGS Flow Validation & Digital Twin

This file is read by Claude Code at the start of every session. Keep it current.

## What this is

A well-to-tank **flow validation, reconciliation, allocation and digital-twin** engine for an oil gathering station (GGS) at ONGC Ankleshwar. The flow it covers:

> wells → test/group headers → separators → storage tanks → dispatch to CTF, with gas going to the compressor, flare and fuel.

It is a working Python engine (`flowval/`, ~6,600 lines, 65 passing tests) with a CLI that writes static HTML and Excel reports. **The current goal is to turn it into a standalone application** with a web UI, a database, scheduled runs and data connectors, without changing the engine's results. Start with `docs/handoff/` before writing code.

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
pip install -r requirements.txt                       # numpy, pandas, pyyaml, openpyxl (no SciPy)
python -m unittest discover -s tests                  # all tests (~40-75 s)
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

- Batch daily processing only; `engine.run` loops per day and recomputes everything (~5 s for 30 days, 12 wells). The app needs incremental per-day runs and persisted results.
- Well-level gross errors are only identifiable with independent well data (VFM or samples); otherwise they are reported as ambiguous or unconfirmed.
- L3 forecast accuracy is unproven (only ~4 months of synthetic history). L4 has no economic objective yet. L5 is simulator-only by design.
- HTML reports are string templates. In the app they become React views over the API; keep them working as exports.
- `twin.autonomy` is slow (~10-20 s). Run it as a background job, never inside a request.

## Where to start

Read `docs/handoff/PRD.md` → `docs/handoff/ARCHITECTURE.md` → `docs/handoff/ROADMAP.md`, then work milestone by milestone. Each milestone ends with its acceptance checks green and a short note to the owner.
