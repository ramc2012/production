"""Digital-twin maturity assessment against the DNV-RP-A204 capability levels
(0 standalone ... 5 autonomous), the scale used across Norwegian oil & gas
(DNV, SINTEF, NTNU and Kongsberg Digital / Falkor partners)."""
from __future__ import annotations

import pandas as pd

LEVELS = [
    (0, "Standalone", "Asset model and physics-based simulation; no live data needed",
     "Kongsberg analogue: K-Spice / LedaFlow engineering models"),
    (1, "Descriptive", "Live, contextualised state of the asset; soft sensors for quantities not measured",
     "Kongsberg analogue: Kognitwin contextualised data + live simulation"),
    (2, "Diagnostic", "Data validation, condition monitoring, fault and anomaly detection",
     "Kongsberg analogue: proactive monitoring / production assurance"),
    (3, "Predictive", "Forecasts of future states, continuously updated from live data",
     "Kongsberg analogue: look-ahead simulation"),
    (4, "Prescriptive", "What-if analysis, optimisation and recommendations; humans decide",
     "Kongsberg analogue: what-if scenarios / production optimisation"),
    (5, "Autonomous", "Closed loop: the twin acts on the asset through the control system",
     "Kongsberg analogue: autonomous / unmanned operations (vision)"),
]


def assess(res, twin, forecasts, l4, l5) -> pd.DataFrame:
    net = res.network
    n_w = len(net.wells)
    static = sum(1 for w in net.wells.values() if w.perf_depth_m)
    tuned = [w.tuning for w in twin.wells.values() if w.modelled and w.on]
    in_band = sum(1 for t in tuned if 0.7 <= t <= 1.3)
    f = res.findings_daily
    rows = []

    def add(level, criterion, status, evidence, gap=""):
        rows.append(dict(level=level, criterion=criterion, status=status, evidence=evidence, gap=gap))

    # L0
    add(0, "Asset / network model", "met",
        f"{n_w} wells, {len(net.separators)} separators, {len(net.tanks)} tanks, {len(net.meters)} meters")
    add(0, "Well static data for physics models", "met" if static == n_w else ("partial" if static else "missing"),
        f"{static}/{n_w} wells with depths, tubing and reservoir data",
        "" if static == n_w else "Complete well_static.csv from well files")
    add(0, "Physics models (PVT, IPR, Beggs-Brill VLP, choke, SRP, tank CTL/CTSh)", "met", "flowval.wellmodel, fluids")
    add(0, "Facility simulator (wells, gas lift, compressor, tank farm)", "met", "flowval.twin.model.FacilityTwin")
    # L1
    add(1, "Data latency", "partial", "Daily DPR / totaliser batch (06:00 production day)",
        "Real-time (≤ 1 min) needs SCADA historian access, e.g. OPC UA / PI, for THP, CHP, meters, tank levels")
    add(1, "Contextualisation (every reading tied to an asset object)", "met" if not (f["check"] == "unknown").any() else "partial",
        "All inputs keyed to network objects (wells, meters, tanks)")
    add(1, "Soft sensors", "met", "NSV per tank, reconciled stream rates, well-parameter VFM per well")
    add(1, "Twin synchronised to the plant", "met" if tuned and in_band == len(tuned) else "partial",
        f"{in_band}/{len(tuned)} wells with tuning factor in 0.7-1.3",
        "" if in_band == len(tuned) else "Investigate wells with large tuning factors (stale PI / Pr)")
    # L2
    add(2, "Data-quality screening", "met", f"{(f['layer'] == 'data_quality').sum()} DQ events")
    add(2, "Well-test validation and well-performance health", "met",
        f"{(f['layer'].isin(['well_test', 'well_perf'])).sum()} well events")
    add(2, "Reconciliation with gross-error detection (VDI 2048)", "met",
        f"{(f['check'] == 'gross_error').sum()} gross-error isolations, χ² test daily")
    # L3
    fs = forecasts["wells"]
    add(3, "Production forecast per well with uncertainty", "met" if len(fs) else "missing",
        f"Arps decline + P10/P90 for {len(fs)} wells, regime-change aware")
    add(3, "Look-ahead simulation (tanks, gas)", "met", "72 h scenarios: normal, dispatch outage, compressor trip")
    add(3, "Meter drift and test-due prediction", "met", f"{len(forecasts['drift'])} drifting meter(s) projected")
    add(3, "Forecast accuracy demonstrated", "partial", "Only 4 months of history in the demo",
        "Back-test against ≥ 6-12 months of reconciled history")
    # L4
    add(4, "Gas-lift allocation optimisation", "met",
        f"Optimiser: {l4['gl_summary'].get('d_oil', 0):+.2f} m³/d oil, lift "
        f"{l4['gl_summary'].get('lift_now', 0):,.0f} → {l4['gl_summary'].get('lift_opt', 0):,.0f} Sm³/d")
    add(4, "What-if for chokes, SPM, shut-ins", "met", f"{len(l4['chokes'])} choke and {len(l4['srp'])} SRP evaluations")
    add(4, "Well-test scheduling and maintenance priorities", "met", "Ranked by allocation uncertainty / misreported volume")
    add(4, "Economic objective", "partial", "Optimises oil volume; lift-gas saving reported",
        "Add oil price, gas value and energy cost for a value-based objective")
    # L5
    add(5, "Closed loop with guardrails, verify and rollback", "partial",
        f"Demonstrated on a mismatched plant simulator: {l5['gain']:+.1f} m³/d, "
        f"{(l5['log']['event'] == 'rolled back').sum() if len(l5['log']) else 0} rollbacks",
        "Simulation only")
    add(5, "Control-system integration and governance", "missing", "No write path to DCS (by design)",
        "OPC UA write-back through the DCS, MOC, HAZOP/SIL review, IEC 62443 security, operator procedures")
    return pd.DataFrame(rows)


def level_status(assessment: pd.DataFrame) -> pd.DataFrame:
    out = []
    for lvl, name, desc, analogue in LEVELS:
        a = assessment[assessment["level"] == lvl]
        if len(a) and (a["status"] == "met").all():
            st = "achieved"
        elif len(a) and (a["status"] != "missing").any():
            st = "partial"
        else:
            st = "not achieved"
        out.append(dict(level=lvl, name=name, description=desc, analogue=analogue, status=st,
                        met=int((a["status"] == "met").sum()), total=len(a)))
    return pd.DataFrame(out)
