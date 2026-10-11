"""Level 4 - prescriptive twin: what-if analysis and recommendations.

All recommendations are computed on the synchronised facility twin and are
advisory: a human approves them (Level 5 adds a guarded closed loop).

* Gas-lift allocation: oil-vs-injection curves per gas-lift well from the
  calibrated IPR/VLP/choke model; gas distributed by equal-marginal-oil
  (greedy) within the lift-gas supply and compressor capacity.
* Choke what-ifs for flowing wells within a drawdown (sand/coning) limit.
* SRP speed: SPM that brings pump submergence to a target (more drawdown
  without pumping off), or reduces SPM when the pump is starved.
* Well-test schedule ranked by allocation uncertainty and open findings.
* Meter maintenance ranked by misreported volume.
"""
from __future__ import annotations

import math
from typing import Dict, Optional

import numpy as np
import pandas as pd

from .. import fluids as F
from .. import wellmodel as M


def whatif(twin, changes: Dict[str, dict]) -> dict:
    """Run a scenario: {well: {choke|lift_gas|spm|on: value}} -> per-well and total deltas."""
    base = twin.steady()
    new = twin.steady(changes)
    b, n = base["wells"].set_index("well_id"), new["wells"].set_index("well_id")
    d = pd.DataFrame({"oil_base": b["oil"], "oil_new": n["oil"], "liquid_new": n["L"],
                      "gas_new": n["gas"] + n["lift_gas"]})
    d["d_oil"] = d["oil_new"] - d["oil_base"]
    tot = {k: (base[k], new[k]) for k in ("oil", "water", "gas_total", "lift", "flare", "compressor_load")}
    return dict(wells=d.reset_index(), totals=tot)


# ---------------------------------------------------------------------------
def gas_lift_curves(twin, cfg) -> pd.DataFrame:
    tc = cfg["twin"]
    grid = np.arange(tc["gl_min_sm3d"], tc["gl_max_sm3d"] + 1, float(tc.get("gl_curve_step_sm3d", 500.0)))
    rows = []
    for w in twin.wells.values():
        if w.lift != "gas_lift" or not w.on or not w.modelled:
            continue
        for g in sorted(set(list(grid) + [round(w.lift_gas)])):
            r = w.solve(lift_gas=g)
            rows.append(dict(well_id=w.id, lift_gas=g, oil=r["oil"], liquid=r["L"], gas_form=r["gas"],
                             current=abs(g - w.lift_gas) < 1))
    return pd.DataFrame(rows)


def optimise_gas_lift(twin, cfg, curves: Optional[pd.DataFrame] = None, available: Optional[float] = None):
    tc = cfg["twin"]
    curves = gas_lift_curves(twin, cfg) if curves is None else curves
    if curves.empty:
        return pd.DataFrame(), {}
    wells = list(curves["well_id"].unique())
    avail = twin.lift_available if available is None else available
    # gas left for lift once formation gas has been through the compressor
    base = twin.steady()
    comp_room = twin.compressor_capacity / (1 - twin.fuel_fraction) - base["gas_form"]
    budget = min(avail, comp_room)
    step = tc["gl_step_sm3d"]

    def oil_at(wid, g):
        c = curves[curves["well_id"] == wid].sort_values("lift_gas")
        c = c.drop_duplicates("lift_gas")
        return float(np.interp(g, c["lift_gas"], c["oil"]))
    alloc = {w: tc["gl_min_sm3d"] for w in wells}
    used = sum(alloc.values())
    while used + step <= budget + 1e-6:
        gains = {w: oil_at(w, alloc[w] + step) - oil_at(w, alloc[w]) for w in wells
                 if alloc[w] + step <= tc["gl_max_sm3d"]}
        if not gains:
            break
        best = max(gains, key=gains.get)
        if gains[best] <= 1e-4:
            break
        alloc[best] += step
        used += step
    cur = {w: twin.wells[w].lift_gas for w in wells}
    # verify with the full model
    sp = {w: dict(lift_gas=alloc[w]) for w in wells}
    res = whatif(twin, sp)
    rows = []
    wd = res["wells"].set_index("well_id")
    for w in wells:
        rows.append(dict(well_id=w, lift_now=cur[w], lift_opt=alloc[w], d_lift=alloc[w] - cur[w],
                         oil_now=wd.loc[w, "oil_base"], oil_opt=wd.loc[w, "oil_new"], d_oil=wd.loc[w, "d_oil"],
                         marginal_now=(oil_at(w, cur[w] + 500) - oil_at(w, cur[w])) / 500 * 1000))
    df = pd.DataFrame(rows)
    summ = dict(lift_now=sum(cur.values()), lift_opt=used, budget=budget, lift_available=avail,
                oil_now=res["totals"]["oil"][0], oil_opt=res["totals"]["oil"][1],
                d_oil=res["totals"]["oil"][1] - res["totals"]["oil"][0],
                flare_opt=res["totals"]["flare"][1], comp_load_opt=res["totals"]["compressor_load"][1])
    return df, summ


def choke_whatifs(twin, cfg) -> pd.DataFrame:
    tc = cfg["twin"]
    rows = []
    for w in twin.wells.values():
        if w.lift != "self_flow" or not w.on or not w.modelled or w.choke != w.choke:
            continue
        base = w.solve()
        for dc in (-int(tc["choke_step_64"]), int(tc["choke_step_64"])):
            c = w.choke + dc
            if c < tc["choke_min_64"] or c > tc["choke_max_64"]:
                continue
            r = w.solve(choke=c)
            rows.append(dict(well_id=w.id, choke_now=w.choke, choke_new=c, oil_now=base["oil"], oil_new=r["oil"],
                             d_oil=r["oil"] - base["oil"], drawdown_now=base.get("drawdown"),
                             drawdown_new=r.get("drawdown"), gas_new=r["gas"],
                             within_limits=bool(r.get("drawdown", 0) <= tc["max_drawdown"])))
    return pd.DataFrame(rows)


def best_chokes(cw: pd.DataFrame, cfg) -> pd.DataFrame:
    if cw.empty:
        return cw
    tc = cfg["twin"]
    ok = cw[cw["within_limits"] & (cw["d_oil"] / cw["oil_now"] * 100 >= tc["min_gain_pct"])]
    return ok.sort_values("d_oil", ascending=False).groupby("well_id").head(1).reset_index(drop=True)


PUMP_HEALTH = {"fluid_level_rise": "repair pump first (efficiency loss)",
               "low_fillage": "hold: gas interference - fix gas anchor before speeding up",
               "estimator_conflict": "hold: estimators disagree - verify data"}


def srp_speed(twin, cfg, findings: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    tc = cfg["twin"]
    rows = []
    for w in twin.wells.values():
        if w.lift != "srp" or not w.on or not w.modelled or w.spm != w.spm:
            continue
        base = w.solve()
        blocked = None
        if findings is not None and len(findings):
            hits = findings[(findings["object_id"] == w.id) & findings["check"].isin(PUMP_HEALTH)]
            if len(hits):
                blocked = PUMP_HEALTH[hits["check"].iloc[-1]]
        if blocked and base["submergence"] >= 50:
            rows.append(dict(well_id=w.id, spm_now=w.spm, spm_new=w.spm, action=blocked,
                             submergence_now=base["submergence"], submergence_new=base["submergence"],
                             oil_now=base["oil"], oil_new=base["oil"], d_oil=0.0))
            continue
        best_spm, best = w.spm, base
        for spm in np.arange(tc["spm_min"], tc["spm_max"] + 0.01, 0.5):
            r = w.solve(spm=float(spm))
            if r["submergence"] >= tc["target_submergence_m"] and r["L"] > best["L"] + 1e-6:
                best_spm, best = float(spm), r
        action = "hold"
        if base["submergence"] < 50:
            # starved: back off until submergence recovers
            for spm in np.arange(w.spm, tc["spm_min"] - 0.01, -0.5):
                r = w.solve(spm=float(spm))
                if r["submergence"] >= tc["target_submergence_m"]:
                    best_spm, best = float(spm), r
                    break
            action = "reduce (pump starved)"
        elif best_spm > w.spm and (best["oil"] - base["oil"]) / max(base["oil"], 1e-6) * 100 >= tc["min_gain_pct"]:
            action = "increase"
        else:
            best_spm, best = w.spm, base
        rows.append(dict(well_id=w.id, spm_now=w.spm, spm_new=best_spm, action=action,
                         submergence_now=base["submergence"], submergence_new=best["submergence"],
                         oil_now=base["oil"], oil_new=best["oil"], d_oil=best["oil"] - base["oil"]))
    return pd.DataFrame(rows)


def test_schedule(res, twin, days: int = 7) -> pd.DataFrame:
    cfg = res.cfg
    uc = cfg["uncertainty"]
    end = pd.Timestamp(res.dates[-1])
    last = res.wells[res.wells["date"] == res.dates[-1]].set_index("well_id")
    f = res.findings_daily
    recent = f[(pd.to_datetime(f["date"]) >= end - pd.Timedelta(days=14)) & (f["object_type"] == "well")]
    rows = []
    for wid, w in twin.wells.items():
        x = last.loc[wid] if wid in last.index else None
        if x is None or not w.on:
            continue
        age = float(x["test_age_d"]) if x["test_age_d"] == x["test_age_d"] else 90.0
        u = uc["well_liquid_u95_pct"] + max(0.0, age - uc["fresh_test_days"]) * uc["aging_liquid_pct_per_day"]
        if x["test_status"] == "PROVISIONAL":
            u *= uc["provisional_factor"]
        oil = float(x["dvr_oil"]) if x["dvr_oil"] == x["dvr_oil"] else float(x["oil"])
        flags = sorted(set(recent[recent["object_id"] == wid]["check"]))
        score = oil * u / 100 * (1 + 0.5 * len(flags)) * (2.0 if age > cfg["well_test"]["age_warn_days"] else 1.0)
        rows.append(dict(well_id=wid, oil_m3d=oil, test_age_d=age, test_status=x["test_status"],
                         uncertainty_m3d=oil * u / 100, open_findings=", ".join(flags), priority=score))
    df = pd.DataFrame(rows).sort_values("priority", ascending=False).reset_index(drop=True)
    cap = int(cfg["twin"]["test_capacity_per_day"])
    df["planned_date"] = [(end + pd.Timedelta(days=1 + i // cap)).strftime("%Y-%m-%d") if i < days * cap else ""
                          for i in range(len(df))]
    return df


def maintenance(res, drift: pd.DataFrame) -> pd.DataFrame:
    rows = []
    v = res.dvr_vars
    for _, d in (drift.iterrows() if drift is not None and len(drift) else []):
        s = v[(v["tag"] == d["tag"]) & (v["var"] == d["stream"]) & (v["date"] >= d["first_flag"])]
        err = float((s["meas"] - s["rec"]).mean()) if len(s) else np.nan
        rows.append(dict(item=d["tag"], action="Prove / recalibrate meter", reason=f"bias {d['bias_now_pct']:+.1f}% "
                         f"({d['stream']}), drift {d['drift_pct_per_30d']:+.1f}%/30 d",
                         misreport_per_day=err, priority=abs(err) if err == err else 0))
    f = res.findings
    for _, r in f[f["check"] == "proving_overdue"].iterrows():
        rows.append(dict(item=r["object_id"], action="Prove meter (overdue)", reason=r["message"],
                         misreport_per_day=np.nan, priority=0.5))
    for _, r in f[f["check"].isin(["fluid_level_rise", "low_fillage", "estimator_conflict",
                                   "inj_pressure_shift"])].iterrows():
        rows.append(dict(item=r["object_id"], action=r["action"] or "Field check", reason=r["message"],
                         misreport_per_day=np.nan, priority=0.3))
    return pd.DataFrame(rows).sort_values("priority", ascending=False).reset_index(drop=True) if rows else pd.DataFrame()


def combined_plan(twin, cfg, gl: pd.DataFrame, chokes: pd.DataFrame, srp: pd.DataFrame) -> dict:
    sp: Dict[str, dict] = {}
    for _, r in gl.iterrows():
        if abs(r["d_lift"]) >= cfg["twin"]["gl_step_sm3d"]:
            sp.setdefault(r["well_id"], {})["lift_gas"] = r["lift_opt"]
    for _, r in chokes.iterrows():
        sp.setdefault(r["well_id"], {})["choke"] = r["choke_new"]
    for _, r in srp.iterrows():
        if r["action"] in ("increase", "reduce (pump starved)"):
            sp.setdefault(r["well_id"], {})["spm"] = r["spm_new"]
    res = whatif(twin, sp)
    return dict(setpoints=sp, **res)
