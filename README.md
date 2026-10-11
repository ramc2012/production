# flowval: well-to-tank flow validation model

A daily validation, reconciliation and allocation model for an oil gathering station (GGS type):

```
wells ──► group/test headers ──► production & test separators ──► storage tanks ──► dispatch (CTF)
                                         │                               │
                                         ├─► gas: compressor / flare / fuel
                                         └─► water legs ──────────────── ┴─► ETP
```

For every production day (06:00 to 06:00) it answers four questions:

1. **Can the measurements be trusted?** It screens data quality, well tests, separator envelopes and tank dips.
2. **What do the well parameters say each well is producing?** A calibrated well model (IPR, tubing VLP, choke, fluid level, pump) turns THP, CHP, lift gas, fluid level, SPM and wellhead samples into a daily rate for each well.
3. **Do the measurements agree with each other?** Data Validation & Reconciliation (DVR) runs on the oil, water and gas balances, with statistical gross-error detection.
4. **How much did each well produce?** It allocates by the API MPMS 20.1 allocation-factor method and by DVR-reconciled rates, side by side, each with an uncertainty.

Only `numpy`, `pandas`, `pyyaml` and `openpyxl` are needed. SciPy is not required.

## Quick start

```bash
pip install -r requirements.txt
python scripts/generate_sample_data.py           # (re)build the synthetic demo dataset
python -m flowval run --network data/sample/network.yaml \
                      --data data/sample --config data/sample/config.yaml --out output
python -m flowval twin --network data/sample/network.yaml --data data/sample --config data/sample/config.yaml --out output
python -m unittest discover -s tests             # 65 tests: physics, well model, DVR, twin levels, planted-fault acceptance, golden lock
python scripts/score_allocation.py               # per-well allocation error vs the synthetic truth
```

Validate one day: add `--date 2026-09-26`. Validate a range: `--start … --end …`.

Outputs in `output/`:

| File | Content |
|---|---|
| `flow_validation_<period>.html` | Self-contained report: KPI tiles, network status diagram, allocation-factor and χ² trends, exception register (filterable), flagged meters, well allocation, test register, separators, tank balance |
| `flow_validation_<period>.xlsx` | Every intermediate table: daily findings, DVR variables/constraints, well daily theoretical vs allocated vs reconciled, tank NSV chain |
| `findings_<period>.csv` | Consolidated exception register |
| `well_allocation_<period>.csv` | Well × day: theoretical, proportional-allocated and reconciled oil/water/gas, VFM rate |

## The six validation layers

| # | Layer | What it checks | Module |
|---|---|---|---|
| 1 | Data quality | Missing data, plausibility ranges, frozen transmitters, spikes (median/MAD), status vs hours, THP ≤ FLP, gas-lift wells with zero injection, meter proving overdue | `dataquality.py` |
| 2 | Well tests | Duration, stability (CV and trend), SRP volumetric efficiency, negative formation GOR, change vs previous test (SPM-normalised, lift-gas aware), choke-model calibration; uncertainty grows with test age; Gilbert virtual flow meter, choke changes, THP that signals a shut-in | `welltests.py` |
| 2b | Well performance | PI calibrated at every test (Pwf from VLP, fluid level, PIP or gauge); daily rate estimators from the nodal model, the Gilbert choke, IPR at the measured Pwf, and SRP displacement × VE × fillage; wellhead BS&W samples; PI change, submergence, fillage, fluid-level rise, injection-pressure shift, estimator conflicts, "cannot flow at this THP" | `wellmodel.py`, `wellperf.py` |
| 3 | Separators | Meters → stock-tank volumes (MF, oil-leg BS&W, shrinkage) with GUM uncertainty; API 12J retention time; Souders-Brown gas load | `separators.py` |
| 4 | Tanks | TOV → GOV (CTSh) → GSV (CTL, Table 54A) → NSV; inventory change + dispatch; single-dip errors found by their +x/−x signature | `tanks.py` |
| 5 | Reconciliation | Weighted least squares with unmeasured streams (projection), χ² global test, z-test, group likelihood-ratio test, serial elimination, and an equivalence check that reports "ambiguous" instead of guessing | `reconciliation.py` |
| 6 | Allocation | Allocation factors at installation and separator level with acceptance bands; proportional and DVR well allocation | `allocation.py` |

The details, formulas and references are in [`docs/METHODOLOGY.md`](docs/METHODOLOGY.md).

## Demo dataset: planted faults and what the model reports

The synthetic GGS has 12 wells (4 self-flow, 4 gas-lift, 4 SRP), two production separators, one test separator, three tanks, dispatch, and compressor/flare/fuel gas meters. The faults below are planted, and `tests/test_end_to_end.py` asserts that each one is caught.

| Planted fault | Model output |
|---|---|
| GS-2 gas meter FT-123 reads +12 % from 15-Sep | Gross error isolated on every day from 15-Sep (estimated +12.5 to +14 %); systematic-bias alarm; no false alarm before 15-Sep |
| T-2 06:00 dip recorded 150 mm high on 20-Sep | "T-2 dip likely high by ~5.6 m³ (145 mm)"; tank inventory excluded from DVR for 19 and 20-Sep |
| ANK-106 water breakthrough on 12-Sep (wc 30 → 55 %) | First weekly wellhead sample (16-Sep) shows +26 pp; DVR isolates ANK-106 from that day; GS-1 oil AF drops to 0.89; the 20-Sep test is flagged +26 pp and made PROVISIONAL |
| ANK-104 shut in, but DPR shows 24 h on 26-Sep | "At THP 43 ksc the calibrated IPR/VLP gives no inflow"; DVR estimate 0.0 m³ vs 11.6 test-based |
| ANK-101 beaned up 18 → 22/64 without a retest | Choke-change warning; well-parameter estimate (choke + nodal) 47.6 m³/d vs 39.5 test-based |
| ANK-112 SRP pump leak (VE 0.90 → 0.60) | Fluid level rose 276 m at unchanged SPM; fluid-level IPR estimate −25 % vs test; PI unchanged at the next test, so the pump is at fault, not the reservoir |
| ANK-108 gas-lift injection moved to a shallower valve | Injection-pressure shift −15 %; apparent PI drop −56 %, which the model notes is a sign of a valve change |
| ANK-111 gas interference | Pump fillage 61 % |
| ANK-110 test logged with the wrong SPM | VE 114 % → test rejected |
| ANK-102 2.5 h test; ANK-107 slugging test | Both rejected |
| ANK-111 last tested in July | Test-age alarm from day 61; uncertainty widened in DVR |
| ANK-103 THP transmitter frozen | Frozen-value warning; VFM skipped for those days |
| Flare meter data missing; dispatch meter proving overdue; GS-1 oil-leg BS&W missing; GS-2 undersized hold-up | Missing / overdue / no-BS&W / API 12J retention findings |

### Does using the well parameters improve allocation?

`scripts/score_allocation.py` compares each method's daily oil per well with the synthetic truth (30 days, 12 wells):

| Method | Daily error (MAPE) | Monthly error per well, mean / worst |
|---|---|---|
| Last test × hours on | 12.7 % | 4.3 % / 16.9 % |
| Proportional allocation factor (API MPMS 20.1) | 13.3 % | 3.4 % / 13.4 % |
| Reconciliation, test-based rates only | 10.4 % | 3.7 % / 10.9 % |
| **Reconciliation + well parameters** | **5.4 %** | **3.3 % / 8.0 %** |

The proportional method fixes the installation total but cannot move production between wells. The well parameters supply that between-well information.

## Digital twin, levels 0 to 5

`python -m flowval twin ...` builds the model up the DNV-RP-A204 capability ladder used by Norwegian operators and Kongsberg Digital (now Falkor) partners:

- **Standalone:** asset model and physics simulator.
- **Descriptive:** a synchronised live state.
- **Diagnostic:** the validation layers above.
- **Predictive:** decline forecasts with P10/P90 bands and a 72 h look-ahead.
- **Prescriptive:** gas-lift optimiser and choke/SPM what-ifs.
- **Autonomous:** a guarded closed loop, run against a plant simulator only.

The run writes a twin dashboard with one tab per level and a maturity assessment. See [`docs/DIGITAL_TWIN.md`](docs/DIGITAL_TWIN.md).

## Using it on real GGS data

1. **Describe the installation** in `network.yaml`, and the wells in `well_static.csv` (perforation, tubing and pump depths, tubing ID, gas-lift valve depth, reservoir temperature). Reservoir pressure comes from `reservoir_surveys.csv` (SBHP). The well model is skipped for any well without static data. In `network.yaml`, give: wells with lift type and normal separator, separators (meters, shrinkage factor, dimensions, NLL hold-up), meters (type, meter factor, last proving, interval), tanks (strapping table CSV), the dispatch meter, gas disposal meters and the lift-gas header.
2. **Map the DPR and SCADA data** onto the CSV templates in [`docs/DATA_DICTIONARY.md`](docs/DATA_DICTIONARY.md). Every column name carries its unit (`_ksc`, `_m3`, `_sm3`, `_degc`). The model needs `well_daily`, `well_tests`, `meter_daily`, `tank_gauges` and `dispatch_daily`. `separator_daily`, `tank_drains` and `well_test_readings` are optional but improve the checks.
3. **Tune `config.yaml`**: acceptance bands, test-age limits, meter uncertainties by type, DVR α.
4. **Run it daily** (`--date`) for exceptions, or monthly (`--start/--end`) for allocation and bias trends.

Practical notes:

- **Shrinkage factors** should come from PVT/flash data per separator pressure. If they are only estimates, raise `uncertainty.shrinkage_u95_pct`.
- **If there are no separator meters** (tank-dip-only GGS), leave the meter IDs out. The streams become unmeasured and DVR closes them from the tank balance. The output then shows lower redundancy, and the χ² test has fewer degrees of freedom.
- **The well model is calibrated, not predictive.** PI, choke factor and VE are re-fitted at every accepted test, so model error is absorbed, and the estimators carry only the *change* in well parameters since that test. Keep SBHP surveys current: a stale reservoir pressure shows up as a PI trend.
- **The well-level DVR estimate for a day a well was on test** uses its *previous* test as the prior, so the test-separator reading is an independent check.

## Limitations and next steps

- **Gross errors that cannot be told apart.** A well with no well-parameter data (e.g. a frozen THP) can absorb another well's imbalance. Such isolations are reported as *unconfirmed* warnings, not alarms. Liquid-rate estimators cannot see a water-cut change; wellhead samples or a retest are needed for that.
- **Measurement model.** Reconciliation is linear in standard volumes with fixed shrinkage. A compositional (EOS) DVR would also reconcile shrinkage and GOR. Floating-roof adjustment (FRA) and the CPL pressure correction are not included; both are negligible for atmospheric GGS tanks.
- **Possible extensions:**
  - Read directly from the SCADA historian or the DPR pipeline.
  - A FastAPI service with a scheduled daily run.
  - Ranking ambiguous candidates with VFM and test-age evidence.

## Standalone application (handoff to Claude Code)

The next step is a standalone web application (FastAPI + React, SQLite, a Windows service, no Docker). The engine stays a library behind it. Start with `CLAUDE.md` and `docs/handoff/` (PRD, architecture, roadmap, kickoff prompt). `tests/test_golden.py` locks the engine's results during the move.

## Layout

```
flowval/            the package (stats, fluids, units, config, network, io, dataquality,
                    welltests, wellmodel, wellperf, separators, tanks, reconciliation, allocation, engine,
                    twin/ = model, predict, prescribe, autonomy, levels, report, run,
                    report_html, report_excel, cli)
data/sample/        synthetic demo inputs (network.yaml, config.yaml, CSVs, strapping tables)
scripts/            generate_sample_data.py, score_allocation.py
tests/              unittest suites
docs/               METHODOLOGY.md, DATA_DICTIONARY.md, DIGITAL_TWIN.md
output/             generated reports
```
