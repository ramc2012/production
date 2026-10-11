# GGS digital twin: capability levels 0 to 5

## Which "levels"

Kongsberg Digital, renamed **Falkor** in June 2026, does not publish its own numbered twin levels. Its digital-twin stack is:

- **Kognitwin:** contextualised live data, situational awareness, production optimisation.
- **K-Spice:** dynamic process simulation.
- **LedaFlow:** transient multiphase flow.
- **Kognitwin Simulation:** cloud simulation with snapshot, what-if and look-ahead.

The level scale used across Norwegian oil & gas by DNV, SINTEF, NTNU and Kongsberg's research partners is the **DNV-RP-A204 capability scale**, later refined by Stadtmann et al. (2023). A 2023 industry survey on that scale described Kongsberg Digital's Kognitwin work as a **Level 3 (predictive)** twin. This model is built and assessed on the same scale:

| Level | Name | Definition (DNV-RP-A204 / Stadtmann et al.) | Kongsberg / Falkor analogue | This model |
|---|---|---|---|---|
| 0 | Standalone | Asset model and physics simulation; no live data needed | K-Spice / LedaFlow engineering models | Network + well static data; PVT, IPR, Beggs & Brill, choke, SRP, tank CTL/CTSh; `FacilityTwin` simulator |
| 1 | Descriptive | Live, contextualised state; soft sensors estimate what is not measured | Kognitwin contextualised data + live simulation | Every reading tied to an asset object; NSV, reconciled rates and the well-parameter VFM as soft sensors; twin synchronised to the reconciled state (tuning factors) |
| 2 | Diagnostic | Validation, condition monitoring, faults and anomalies | Proactive monitoring | Data-quality checks, well-test validation, well-performance health, DVR gross-error detection (VDI 2048) |
| 3 | Predictive | Forecasts updated from live data | Look-ahead simulation | Arps forecasts per well with P10/P90 (regime-change aware); 72 h tank/gas look-ahead scenarios; meter drift; tests due |
| 4 | Prescriptive | What-if, optimisation, recommendations; humans decide | What-if scenarios, production optimisation | Gas-lift allocation (equal marginal oil within lift-gas supply and compressor capacity); choke what-ifs with a drawdown guard; SRP speed vs submergence (held when the pump is unhealthy); test schedule; maintenance list |
| 5 | Autonomous | Closed loop: the twin acts on the asset | Autonomous / unmanned operations | Guarded supervisor run against a mismatched **plant simulator**; never connected to a control system |

## Run

```bash
python -m flowval twin --network data/sample/network.yaml --data data/sample \
       --config data/sample/config.yaml --out output [--mode advisory|supervised|autonomous_sim]
```

The run writes:

- `digital_twin_<date>.html`: a dashboard with one tab per level and the maturity assessment.
- `digital_twin_<date>.xlsx`: every table.
- `twin_state_<date>.json`: the live state and the recommended setpoints, for integration with other systems.
- The validation report and workbook.

## How each level works

**L0 to L1: build and synchronise.**

1. Each well is calibrated from its latest usable test: Pr from SBHP, Pb, PI from the test Pwf, the choke factor, and SRP volumetric efficiency.
2. The well is set to the latest operating point: choke, lift gas, SPM, FLP.
3. A flowing or gas-lift well solves for the rate at which the THP from the tubing (IPR + Beggs & Brill VLP) equals the THP from the choke (Gilbert). The stable, right-hand intersection is taken.
4. An SRP well produces min(pump displacement × VE, inflow at pump-off), and its fluid level follows from the IPR.
5. **Synchronisation:** each well's PI (or VE, when the pump is the limit) is re-tuned until the model reproduces that day's reconciled rate. A tuning factor outside 0.7 to 1.3 flags a stale PI or reservoir pressure.

**L3: forecasts.**

- A decline curve is fitted to the accepted tests and reconciled daily rates.
- After a regime change (bean change, breakthrough, pump leak, valve change), the model re-fits once at least 21 days and 10 points of data exist. Until then it resets the level and keeps the long-term decline.
- The P10/P90 band grows with the forecast horizon.

**L4: recommendations.** Every recommendation is evaluated on the synchronised twin with `whatif()`, so the totals (oil, gas, compressor load, flare) always come from the same physics.

**L5: the guarded loop.** Each cycle runs these steps:

1. **Trust gate:** wells with recent `nodal_no_flow`, `offline_estimate`, frozen or conflicting data, or a confirmed gross error are locked. Untrusted lift-gas metering freezes gas-lift moves.
2. **Re-synchronise** the twin to plant measurements (online learning).
3. **Optimise** (L4).
4. **Guard:**
   - per-cycle step limits;
   - a cumulative envelope per run (lift gas ±3,000 Sm³/d, choke ±4/64, SPM ±2);
   - a deadband (gain below 0.05 m³/d is ignored unless the move frees ≥ 500 Sm³/d of lift gas);
   - lift-gas supply and compressor and flare constraints.
5. **Act**, according to the mode:
   - `advisory`: log only;
   - `supervised`: an approver callback must accept each move;
   - `autonomous_sim`: apply to the simulator.
6. **Verify or roll back:** if the measured change is worse than predicted beyond the noise band (2σ + 3 %), the move is reverted and the well locked.
7. **Audit:** every proposal, block, application, verification and rollback is logged.

## Demo results (synthetic GGS, 30 Sep 2026)

| Level | Result |
|---|---|
| L1 | 12/12 wells synchronised (tuning factors 0.85 to 1.10) |
| L3 | Installation oil 153.5 m³/d in 30 days (P10 to P90: 144 to 163). With dispatch stopped, the tanks reach safe fill in 41 h. FT-123 drift projected to +14 % within 30 days |
| L4 | Lift gas is over-injected: 31,486 → 24,000 Sm³/d gives +0.6 m³/d oil and frees 7,500 Sm³/d. All recommendations together (gas lift + 4 bean-ups within 35 % drawdown + 1 SPM increase): **+11.5 m³/d (+7 %)**. ANK-112 gets "repair pump first" and ANK-111 gets "fix gas interference" instead of more SPM |
| L5 | Plant oil 149.8 → 166.0 m³/d (**+16.2 m³/d**) with 7,000 Sm³/d less lift gas, in 2 active cycles. ANK-104 stayed locked (data distrusted). 0 rollbacks. The test suite also checks that a harmful move is rolled back |

## Getting an Ankleshwar GGS up the ladder

| Step | What it takes | Level unlocked |
|---|---|---|
| 1 | Well master data (depths, tubing, valves, pumps) and SBHP surveys into `well_static.csv` / `reservoir_surveys.csv`; DPR mapped onto the CSV templates | L0 and L2, plus L1/L3 on daily data |
| 2 | Read-only SCADA historian / OPC UA feed (THP, CHP, lift-gas, separator meters, tank radar) at ≤ 1 min, with the twin running hourly | L1 real-time, L3 intraday look-ahead |
| 3 | ≥ 6 to 12 months of reconciled history to back-test the forecasts; an economic objective (oil price, gas value, power) | L3 and L4 complete |
| 4 | Operator advisory workflow: daily recommendation sheet, accept/decline captured back into the twin | L4 operational |
| 5 | Supervised write-back for low-risk setpoints only (gas-lift injection), through the DCS with interlocks, MOC, HAZOP/SIL review, IEC 62443 zones, and operator override | L5 (supervised) |

## References

- DNV-RP-A204, *Qualification and assurance of digital twins* (2020).
- Stadtmann, F. et al. (2023), *Digital twins in wind energy: emerging technologies and industry-informed future directions*, arXiv:2304.11405.
- Falkor (formerly Kongsberg Digital): Kognitwin, K-Spice, LedaFlow product pages; *Kongsberg Digital rebrands as Falkor* (June 2026).
- Arps, J.J. (1945), *Analysis of decline curves*, Trans. AIME 160.
