"""Layer 6 - back-allocation of measured production to wells.

Two methods are reported side by side:

* Proportional (API MPMS 20.1 "theoretical x allocation factor"):
      AF_phase   = measured installation total / sum(theoretical well rates)
      allocated  = theoretical_i x AF_phase
* DVR-based: reconciled well rates from the WLS solution. These honour every
  measurement in proportion to its uncertainty and close all balances exactly.
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from .findings import FindingLog
from .network import Network


def _af_flag(log, date, obj_type, obj, phase, af, cfg, basis):
    lo_w, hi_w = cfg["allocation"]["af_warn"]
    lo_a, hi_a = cfg["allocation"]["af_alarm"]
    if af != af:
        return
    if af < lo_a or af > hi_a:
        sev = "ALARM"
    elif af < lo_w or af > hi_w:
        sev = "WARN"
    else:
        return
    log.add(date, "allocation", sev, obj_type, obj, f"af_{phase}",
            f"{phase} allocation factor {af:.3f} ({basis})", float(af),
            f"[{lo_w}, {hi_w}]", "Investigate measurement or stale/invalid well tests")


def allocate_day(net: Network, date: str, theo: pd.DataFrame, vdf: pd.DataFrame,
                 groups: List[dict], routing: Dict[str, Dict[str, float]], cfg: dict):
    log = FindingLog()
    rec = dict(zip(vdf["var"], vdf["rec"]))
    meas = {}
    for g in groups:
        for j, v in enumerate(g["vars"]):
            meas[v] = g["values"][j]
    t = theo.copy()
    t = t[t["oil"].notna()] if "oil" in t else t
    th_oil, th_wat, th_gas = t["oil"].sum(), t["water"].sum(), t["gas"].sum()

    # ---- installation-level measured totals ------------------------------
    def m(v):
        return meas.get(v, np.nan)
    oil_meas = m("tank:dinv_oil") + m("dispatch:oil")
    seps = [s for s in routing]               # separators that received flow today
    # a missing meter makes the measured total unknown (NaN), never silently partial
    wat_meas = sum(m(f"sep:{s}:oilleg_water") + (m(f"sep:{s}:water") if net.separators[s].phases == 3 else 0.0)
                   for s in seps)
    lift_meas = sum(meas.get(f"well:{w}:lift", np.nan) for w in t.loc[t["lift"] == "gas_lift", "well_id"])
    gas_meas = sum(m(f"sep:{s}:gas") for s in seps) - lift_meas

    af = dict(oil=oil_meas / th_oil if th_oil > 0 else np.nan,
              water=wat_meas / th_wat if th_wat > 0 else np.nan,
              gas=gas_meas / th_gas if th_gas > 0 else np.nan)
    rec_tot = dict(oil=sum(rec.get(f"well:{w}:oil", 0) for w in theo["well_id"]),
                   water=sum(rec.get(f"well:{w}:water", 0) for w in theo["well_id"]),
                   gas=sum(rec.get(f"well:{w}:gas", 0) for w in theo["well_id"]))
    af_dvr = {k: rec_tot[k] / v if v > 0 else np.nan
              for k, v in (("oil", th_oil), ("water", th_wat), ("gas", th_gas))}
    for ph in ("oil", "water", "gas"):
        _af_flag(log, date, "installation", net.name, ph, af[ph], cfg, "measured / theoretical")
    # factor used for proportional allocation: measured (API 20.1) or reconciled reference;
    # if the measured total is unavailable (meter missing / dip rejected) fall back to DVR
    ref = cfg["allocation"].get("reference", "tank")
    af_use = {ph: (af_dvr[ph] if (ref == "dvr" or af[ph] != af[ph]) else af[ph]) for ph in af}

    # ---- separator-level factors -----------------------------------------
    sep_rows = []
    for s in net.separators.values():
        routed = routing.get(s.id, {})
        if not routed:
            continue
        tsum = {ph: sum(f * float(t.loc[t["well_id"] == w, ph].sum()) for w, f in routed.items())
                for ph in ("oil", "water", "gas")}
        lift = sum(f * meas.get(f"well:{w}:lift", 0.0) for w, f in routed.items())
        mo = m(f"sep:{s.id}:oil")
        mw = np.nansum([m(f"sep:{s.id}:water"), m(f"sep:{s.id}:oilleg_water")])
        mg = m(f"sep:{s.id}:gas") - lift
        r = dict(date=date, separator=s.id,
                 af_oil=mo / tsum["oil"] if tsum["oil"] > 0 else np.nan,
                 af_water=mw / tsum["water"] if tsum["water"] > 0 else np.nan,
                 af_gas=mg / tsum["gas"] if tsum["gas"] > 0 else np.nan)
        sep_rows.append(r)
        if s.kind == "production":
            for ph in ("oil", "water", "gas"):
                _af_flag(log, date, "separator", s.id, ph, r[f"af_{ph}"], cfg, "separator meter / routed theoretical")

    # ---- well allocation table -------------------------------------------
    rows = []
    for _, w in theo.iterrows():
        wid = w["well_id"]
        rows.append(dict(
            date=date, well_id=wid, hours_on=w["hours_on"], test_id=w.get("test_id"),
            test_status=w.get("test_status"), test_age_d=w.get("test_age_d"),
            theo_oil=w.get("oil"), theo_water=w.get("water"), theo_gas=w.get("gas"),
            prop_oil=w.get("oil") * af_use["oil"] if w.get("oil") == w.get("oil") else np.nan,
            prop_water=w.get("water") * af_use["water"] if w.get("water") == w.get("water") else np.nan,
            prop_gas=w.get("gas") * af_use["gas"] if w.get("gas") == w.get("gas") else np.nan,
            dvr_oil=rec.get(f"well:{wid}:oil"), dvr_water=rec.get(f"well:{wid}:water"),
            dvr_gas=rec.get(f"well:{wid}:gas"),
            vfm_liquid_m3d=w.get("vfm_liquid_m3d"), vfm_dev=w.get("vfm_dev"),
            dvr_liq_vfm=rec.get(f"well:{wid}:liq"),
        ))
    summary = dict(date=date, theo_oil=th_oil, theo_water=th_wat, theo_gas=th_gas,
                   meas_oil=oil_meas, meas_water=wat_meas, meas_gas=gas_meas,
                   dvr_oil=rec_tot["oil"], dvr_water=rec_tot["water"], dvr_gas=rec_tot["gas"],
                   af_oil=af["oil"], af_water=af["water"], af_gas=af["gas"],
                   af_used_oil=af_use["oil"], af_used_water=af_use["water"], af_used_gas=af_use["gas"],
                   af_oil_dvr=af_dvr["oil"], af_water_dvr=af_dvr["water"], af_gas_dvr=af_dvr["gas"],
                   lift_gas=lift_meas)
    return pd.DataFrame(rows), summary, pd.DataFrame(sep_rows), log
