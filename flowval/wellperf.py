"""Layer 2b - well performance: rate estimators driven by well parameters.

For every accepted test the well model is *calibrated* (flowing BHP at test
conditions -> productivity index; SRP volumetric efficiency; choke factor).
Each day the measured well parameters are pushed through the calibrated
model to give independent liquid-rate estimates:

    choke      Gilbert choke equation           THP, choke, GLR (incl. lift gas)
    nodal      IPR = VLP (Beggs & Brill)        THP, lift-gas rate, Pr, PI
    ipr_fl     IPR at Pwf from fluid level      CHP, dynamic fluid level (SRP)
    ipr_pip    IPR at Pwf from intake pressure  PIP (ESP / pump gauge)
    ipr_gauge  IPR at downhole gauge FBHP       FBHP
    srp_pd     pump displacement x VE x fillage SPM, stroke, dynacard fillage

The estimates are combined by inverse variance into one well-parameter
virtual flow meter (VFM). Disagreements between estimators are diagnostic
(e.g. choke-model rate up while nodal rate down = restricted choke).
"""
from __future__ import annotations

import math
from typing import Dict, Optional

import numpy as np
import pandas as pd

from . import fluids as F
from . import units as U
from . import wellmodel as M
from .findings import FindingLog
from .network import Network, Well


def _nz(x, d=np.nan):
    try:
        return d if x is None or pd.isna(x) else float(x)
    except (TypeError, ValueError):
        return d


def geom_for(well: Well, wht: Optional[float], cfg: dict) -> Optional[M.WellGeom]:
    if well.perf_depth_m is None:
        return None
    wm = cfg["well_model"]
    return M.WellGeom(perf_depth_m=float(well.perf_depth_m),
                      tubing_depth_m=_nz(well.tubing_depth_m, None) if well.tubing_depth_m else None,
                      tubing_id_in=float(well.tubing_id_in or 2.441),
                      casing_id_in=float(well.casing_id_in or 4.892),
                      valve_depth_m=float(well.valve_depth_m) if well.valve_depth_m else None,
                      pump_depth_m=float(well.pump_depth_m) if well.pump_depth_m else None,
                      wht_c=wht if wht == wht and wht is not None else float(well.wht_degc or wm["default_wht_degc"]))


def fluid_for(net: Network, well: Well, gor: float, wc: float, cfg: dict) -> M.Fluid:
    return M.Fluid(oil_rho15=net.oil_rho15, gas_sg=net.gas_sg, water_rho=net.water_rho,
                   gor=gor if gor == gor and gor > 0 else 1.0, wc=min(max(wc, 0.0), 0.99) if wc == wc else 0.3,
                   pb_ksc=float(well.bubble_point_ksc) if well.bubble_point_ksc else None,
                   t_res_c=float(well.reservoir_temp_degc or cfg["well_model"]["default_reservoir_temp_degc"]))


class ReservoirPressure:
    """Reservoir pressure history: latest survey on/before a date, else static value."""

    def __init__(self, net: Network, surveys: pd.DataFrame):
        self.net = net
        self.s = surveys.sort_values("date") if surveys is not None and len(surveys) else pd.DataFrame(
            columns=["date", "well_id", "pr_ksc"])

    def at(self, well_id: str, date: str) -> float:
        s = self.s[(self.s["well_id"] == well_id) & (self.s["date"] <= date)]
        if len(s):
            return float(s["pr_ksc"].iloc[-1])
        w = self.net.wells.get(well_id)
        return float(w.reservoir_pressure_ksc) if w and w.reservoir_pressure_ksc else np.nan


def _pwf_measured(well: Well, row, geom, fl, cfg):
    """Flowing BHP from downhole data if available: (pwf, method)."""
    fb = _nz(row.get("fbhp_ksc"))
    if fb == fb:
        return fb, "gauge"
    pip = _nz(row.get("pip_ksc"))
    if pip == pip:
        return M.pwf_from_intake(pip, geom, fl), "pip"
    dfl, chp = _nz(row.get("dfl_m")), _nz(row.get("chp_ksc"))
    if dfl == dfl and well.lift in ("srp", "pcp"):
        return M.pwf_from_fluid_level(chp if chp == chp else 0.0, dfl, geom, fl,
                                      cfg["well_model"]["annulus_factor"]), "fluid_level"
    return np.nan, None


# ---------------------------------------------------------------------------
# Calibration at each usable test
# ---------------------------------------------------------------------------
def calibrate_tests(net: Network, tests: pd.DataFrame, raw_tests: pd.DataFrame, pres: ReservoirPressure,
                    cfg: dict) -> tuple:
    """Adds pwf_test, pwf_method, pi, pr, pb, fillage to the evaluated test table."""
    log = FindingLog()
    if not cfg["well_model"]["enabled"] or tests.empty:
        return tests, log
    raw = raw_tests.set_index("test_id")
    out = tests.copy()
    for c in ("pr_ksc", "pb_ksc", "pwf_test", "pwf_method", "pi", "fillage_test", "inj_p_test", "dfl_test"):
        out[c] = np.nan if c != "pwf_method" else None
    last_pi: Dict[str, tuple] = {}
    for i, t in out.sort_values(["well_id", "test_date"]).iterrows():
        well = net.wells.get(t["well_id"])
        if well is None or t["test_id"] not in raw.index:
            continue
        r = raw.loc[t["test_id"]]
        out.at[i, "fillage_test"] = _nz(r.get("pump_fillage_pct"))
        out.at[i, "inj_p_test"] = _nz(r.get("inj_pressure_ksc"), _nz(r.get("chp_ksc")))
        out.at[i, "dfl_test"] = _nz(r.get("dfl_m"))
        geom = geom_for(well, _nz(r.get("wht_degc")), cfg)
        pr = pres.at(well.id, t["test_date"])
        if geom is None or pr != pr or not (t["liquid_m3d"] > 0):
            continue
        fl = fluid_for(net, well, t["gor"], t["wc"], cfg)
        pb = fl.bubble_point_ksc()
        pwf, how = _pwf_measured(well, r, geom, fl, cfg)
        if pwf != pwf and well.lift in ("self_flow", "gas_lift") and t["thp_ksc"] == t["thp_ksc"]:
            lift_rate = t["lift_gas_sm3d"] if well.lift == "gas_lift" and t["lift_gas_sm3d"] == t["lift_gas_sm3d"] else 0.0
            pwf, how = M.vlp_pwf(t["thp_ksc"], t["liquid_m3d"], fl, geom, lift_rate), "vlp"
        out.at[i, "pr_ksc"], out.at[i, "pb_ksc"] = pr, pb
        if pwf != pwf:
            continue
        out.at[i, "pwf_test"], out.at[i, "pwf_method"] = pwf, how
        if pwf >= pr:
            log.add(t["test_date"], "well_perf", "WARN", "well", well.id, "pwf_above_pr",
                    f"[{t['test_id']}] Flowing BHP {pwf:.1f} ksc ({how}) >= reservoir pressure {pr:.1f} ksc "
                    f"while producing {t['liquid_m3d']:.1f} m3/d", pwf, f"< {pr:.1f}",
                    "Update reservoir pressure (SBHP survey) or check depth / fluid-level data")
            continue
        pi = M.calibrate_pi(t["liquid_m3d"], pwf, pr, pb)
        out.at[i, "pi"] = pi
        if t["status"] in ("ACCEPTED", "ACCEPTED_W", "PROVISIONAL"):
            prev = last_pi.get(well.id)
            if prev and prev[0] > 0:
                d = pi / prev[0] - 1
                if abs(d) > cfg["well_model"]["pi_change_warn"]:
                    hint = ("Skin / scale / wax build-up (drop) or stimulation effect (rise): review well history"
                            if well.lift != "gas_lift" else
                            "For a gas-lift well an apparent PI change often means the injection point moved "
                            "(valve change) - check CHP; otherwise skin / scale")
                    log.add(t["test_date"], "well_perf", "WARN", "well", well.id, "pi_change",
                            f"[{t['test_id']}] Productivity index {pi:.2f} m3/d/ksc changed {d:+.0%} since "
                            f"{prev[1]} ({prev[0]:.2f})", d, f"±{cfg['well_model']['pi_change_warn']:.0%}", hint)
            last_pi[well.id] = (pi, t["test_id"])
    return out, log


# ---------------------------------------------------------------------------
# Daily estimates
# ---------------------------------------------------------------------------
def _fl_survey(wd_well: pd.DataFrame, date: str, max_age: int):
    s = wd_well[(wd_well["date"] <= date) & wd_well["dfl_m"].notna()]
    if s.empty:
        return None
    r = s.sort_values("date").iloc[-1]
    age = (pd.Timestamp(date) - pd.Timestamp(r["date"])).days
    return (r, age) if age <= max_age else None


def daily_estimates(net: Network, theo_row: dict, r: pd.Series, t: pd.Series, wd_well: pd.DataFrame,
                    pres: ReservoirPressure, cfg: dict, bad) -> dict:
    """Return estimator rates (m3/d per producing day) and the combined VFM."""
    wm = cfg["well_model"]
    well = net.wells[r["well_id"]]
    date = r["date"]
    est, u95 = {}, {}
    age_t = max(0.0, (pd.Timestamp(date) - pd.Timestamp(t["test_date"])).days)
    drift = age_t * wm["aging_pct_per_day"]

    def add(name, q, extra=0.0):
        if q is None or q != q or q < 0:
            return
        est[name] = q
        u95[name] = wm["estimator_u95_pct"][name] + drift + extra

    # choke model (already computed in theoretical step)
    vc = theo_row.get("vfm_liquid_m3d")
    if vc is not None and vc == vc:
        add("choke", vc)

    geom = geom_for(well, _nz(r.get("wht_degc")), cfg)
    pr = pres.at(well.id, date)
    pi = _nz(t.get("pi"))
    out = {}
    if geom is not None and pr == pr and pi == pi and pi > 0:
        fl = fluid_for(net, well, _nz(t.get("gor")), _nz(t.get("wc")), cfg)
        pb = fl.bubble_point_ksc()
        thp = _nz(r.get("thp_ksc"))
        key = (date, well.id, "thp_ksc")
        reason = bad.get(key) if hasattr(bad, "get") else ("flag" if key in bad else None)
        thp_ok = thp == thp and reason in (None, "spike")   # a spike is still a real pressure
        if well.lift in ("self_flow", "gas_lift") and thp_ok:
            frac = max(_nz(r.get("hours_on"), 24.0), 0.1) / 24.0
            lift = _nz(r.get("lift_gas_sm3"), 0.0) / frac if well.lift == "gas_lift" else 0.0
            q = M.nodal_rate(thp, pr, pi, pb, fl, geom, lift)
            out["nodal_dead"] = q <= 0.0
            add("nodal", q)
        # downhole pressure based
        fb, pip = _nz(r.get("fbhp_ksc")), _nz(r.get("pip_ksc"))
        if fb == fb:
            add("ipr_gauge", M.ipr_rate(fb, pr, pi, pb))
            out["pwf"] = fb
        elif pip == pip:
            pwf = M.pwf_from_intake(pip, geom, fl)
            add("ipr_pip", M.ipr_rate(pwf, pr, pi, pb))
            out["pwf"] = pwf
        elif well.lift in ("srp", "pcp"):
            sv = _fl_survey(wd_well, date, int(wm["fl_survey_max_age_days"]))
            if sv is not None:
                s, age = sv
                chp = _nz(s.get("chp_ksc"), 0.0)
                pwf = M.pwf_from_fluid_level(chp, float(s["dfl_m"]), geom, fl, wm["annulus_factor"])
                add("ipr_fl", M.ipr_rate(pwf, pr, pi, pb), age * wm["fl_aging_pct_per_day"])
                out.update(pwf=pwf, dfl_m=float(s["dfl_m"]), dfl_age_d=age,
                           submergence_m=(geom.pump_depth_m - float(s["dfl_m"])) if geom.pump_depth_m else np.nan)
        out["pb"] = pb
        out["pr"] = pr
    # SRP displacement x VE x fillage
    if well.lift == "srp" and well.plunger_in:
        spm, stroke = _nz(r.get("spm")), _nz(r.get("stroke_in"), well.stroke_in)
        ve = _nz(t.get("srp_ve"))
        if spm == spm and spm > 0 and ve == ve and 0 < ve <= 1.05:
            fill_d, fill_t = _nz(r.get("pump_fillage_pct")), _nz(t.get("fillage_test"))
            ratio = fill_d / fill_t if fill_d == fill_d and fill_t == fill_t and fill_t > 0 else 1.0
            add("srp_pd", F.srp_displacement_m3d(stroke, spm, well.plunger_in) * ve * ratio)
        out["fillage"] = _nz(r.get("pump_fillage_pct"))

    # wellhead BS&W sample (water cut) - latest within max age
    sm = wd_well[(wd_well["date"] <= date) & wd_well["sample_bsw_pct"].notna()] if "sample_bsw_pct" in wd_well else None
    if sm is not None and len(sm):
        rs = sm.sort_values("date").iloc[-1]
        age = (pd.Timestamp(date) - pd.Timestamp(rs["date"])).days
        if age <= wm["sample_max_age_days"]:
            out.update(wc_sample=float(rs["sample_bsw_pct"]) / 100.0, wc_sample_age=age,
                       wc_sample_date=rs["date"])

    # ---- combine ---------------------------------------------------------
    if est:
        w = {k: 1.0 / (est[k] * u95[k] / 200.0 + 0.2) ** 2 for k in est}
        tot = sum(w.values())
        q = sum(w[k] * est[k] for k in est) / tot
        sd = math.sqrt(1.0 / tot)
        out.update(vfm_comb=q, vfm_sd=sd, vfm_methods=",".join(sorted(est)))
        # pairwise conflicts
        conf = []
        keys = sorted(est)
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                a, b = keys[i], keys[j]
                sa = est[a] * u95[a] / 200 + 0.2
                sb = est[b] * u95[b] / 200 + 0.2
                zab = (est[a] - est[b]) / math.hypot(sa, sb)
                if abs(zab) > wm["conflict_sigma"]:
                    conf.append((a, b, zab))
        out["conflicts"] = conf
    for k, v in est.items():
        out[f"est_{k}"] = v
    return out


def diagnose(a: str, b: str, ea: float, eb: float, theo: float) -> str:
    pair = {a, b}
    if pair == {"choke", "nodal"}:
        choke_v = ea if a == "choke" else eb
        nodal_v = eb if a == "choke" else ea
        if choke_v > nodal_v:
            return ("choke model reads higher than nodal: THP up with less inflow - restricted/plugged "
                    "choke or flow-line restriction (or THP gauge high)")
        return "nodal reads higher than choke model: bean eroded / washed out (or THP gauge low)"
    if pair == {"srp_pd", "ipr_fl"}:
        pd_v = ea if a == "srp_pd" else eb
        fl_v = eb if a == "srp_pd" else ea
        if pd_v > fl_v:
            return ("pump displacement says more than the inflow (fluid level high): volumetric efficiency "
                    "has dropped - travelling/standing valve or tubing leak, or worn pump")
        return "inflow exceeds pump displacement: fluid level falling / pumped-off risk or SPM data error"
    return "well-parameter estimators disagree - check gauges and data"


def wellperf_findings(net: Network, wells: pd.DataFrame, tests: pd.DataFrame, cfg: dict) -> FindingLog:
    """Period post-pass: health checks + estimator conflicts + persistent VFM deviation."""
    wm = cfg["well_model"]
    log = FindingLog()
    if wells.empty or "vfm_comb" not in wells:
        return log
    for wid, g in wells.sort_values("date").groupby("well_id"):
        well = net.wells.get(wid)
        # nodal says the well cannot flow at the reported THP
        if "nodal_dead" in g:
            for _, r in g[g["nodal_dead"].eq(True)].iterrows():
                log.add(r["date"], "well_perf", "ALARM", "well", wid, "nodal_no_flow",
                        f"At THP {r['thp_ksc']:.1f} ksc the calibrated IPR/VLP gives no inflow, yet the well is "
                        f"reported on-line {r['hours_on']:g} h", float(r["thp_ksc"]), None,
                        "Well probably shut-in / dead: verify hours-on, choke and SSV status")
        # estimator conflicts (report first day of each diagnosis)
        seen = set()
        for _, r in g.iterrows():
            cf = r.get("conflicts")
            for a, b, z in (cf if isinstance(cf, list) else []):
                key = (a, b)
                if key in seen:
                    continue
                seen.add(key)
                ea, eb = r[f"est_{a}"], r[f"est_{b}"]
                log.add(r["date"], "well_perf", "WARN", "well", wid, "estimator_conflict",
                        f"{a} {ea:.1f} vs {b} {eb:.1f} m3/d (z = {z:+.1f}): {diagnose(a, b, ea, eb, r.get('liquid'))}",
                        float(z), f"±{wm['conflict_sigma']} sigma", "Field check of the indicated equipment")
        # wellhead sample water cut vs the test in use
        if "wc_sample" in g:
            sw = g.dropna(subset=["wc_sample"])
            d = 100 * (sw["wc_sample"] - sw["wc"])
            hit = sw[d.abs() > wm["sample_vs_test_warn_pp"]]
            if len(hit):
                r = hit.iloc[0]
                log.add(r["wc_sample_date"], "well_perf", "WARN", "well", wid, "wc_sample_change",
                        f"Wellhead sample BS&W {100 * r['wc_sample']:.0f}% vs {100 * r['wc']:.0f}% in test "
                        f"{r['test_id']} ({d[hit.index[0]]:+.0f} pp)", float(d[hit.index[0]]),
                        f"±{wm['sample_vs_test_warn_pp']:.0f} pp",
                        "Water cut has changed (breakthrough / coning or sampling error) - retest the well")
        # combined VFM vs test-based theoretical, persistent
        rate_theo = g["liquid"] / (g["hours_on"] / 24.0)
        dev = g["vfm_comb"] / rate_theo - 1
        run = 0
        for idx, v in dev.items():
            if v == v and abs(v) > wm["deviation_warn"]:
                run += 1
                if run == wm["persist_days"]:
                    r = g.loc[idx]
                    sev = "ALARM" if abs(v) > wm["deviation_alarm"] else "WARN"
                    log.add(r["date"], "well_perf", sev, "well", wid, "vfm_deviation",
                            f"Well-parameter estimate {r['vfm_comb']:.1f} m3/d ({r['vfm_methods']}) vs test-based "
                            f"{rate_theo[idx]:.1f} m3/d ({v:+.0%}) for {run} days", float(v),
                            f"±{wm['deviation_warn']:.0%}", "Well behaviour changed since the test - retest")
            else:
                run = 0
        if well is None:
            continue
        # SRP health
        if well.lift == "srp":
            if "submergence_m" in g:
                low = g[g["submergence_m"] < wm["min_submergence_m"]]
                if len(low):
                    r = low.iloc[0]
                    log.add(r["date"], "well_perf", "WARN", "well", wid, "low_submergence",
                            f"Pump submergence {r['submergence_m']:.0f} m (fluid level {r['dfl_m']:.0f} m)",
                            float(r["submergence_m"]), f">= {wm['min_submergence_m']:.0f} m",
                            "Pumped-off / gas interference risk: reduce SPM or lower pump")
            if "fillage" in g:
                lf = g[g["fillage"] < wm["fillage_warn_pct"]]
                if len(lf):
                    r = lf.iloc[0]
                    log.add(r["date"], "well_perf", "WARN", "well", wid, "low_fillage",
                            f"Pump fillage {r['fillage']:.0f}% (dynacard)", float(r["fillage"]),
                            f">= {wm['fillage_warn_pct']:.0f}%", "Gas interference / pump-off: check gas anchor, SPM")
            if "dfl_m" in g:
                sv = g.dropna(subset=["dfl_m"]).drop_duplicates("dfl_m")
                for k in range(1, len(sv)):
                    a, b = sv.iloc[k - 1], sv.iloc[k]
                    same_spm = abs(_nz(b.get("spm"), 0) - _nz(a.get("spm"), 0)) < 0.5
                    if b["dfl_m"] - a["dfl_m"] < -wm["dfl_rise_warn_m"] and same_spm:
                        log.add(b["date"], "well_perf", "WARN", "well", wid, "fluid_level_rise",
                                f"Dynamic fluid level rose {a['dfl_m'] - b['dfl_m']:.0f} m "
                                f"({a['dfl_m']:.0f} -> {b['dfl_m']:.0f} m) with SPM unchanged",
                                float(a["dfl_m"] - b["dfl_m"]), f"< {wm['dfl_rise_warn_m']:.0f} m",
                                "Pump efficiency loss (valve/tubing leak) or higher inflow - dynacard & retest")
                        break
        # gas-lift injection pressure shift vs test
        if well.lift == "gas_lift" and "inj_p" in g and "inj_p_test" in g:
            d = g["inj_p"] / g["inj_p_test"] - 1
            sh = g[d.abs() > wm["inj_pressure_shift"]]
            if len(sh):
                r = sh.iloc[0]
                log.add(r["date"], "well_perf", "WARN", "well", wid, "inj_pressure_shift",
                        f"Injection pressure {r['inj_p']:.1f} ksc vs {r['inj_p_test']:.1f} at test "
                        f"({d[sh.index[0]]:+.0%})", float(d[sh.index[0]]), f"±{wm['inj_pressure_shift']:.0%}",
                        "Operating valve changed (multi-pointing / valve failure) - check CHP chart")
    return log


def performance_table(net: Network, wells: pd.DataFrame, tests: pd.DataFrame) -> pd.DataFrame:
    """One row per well: reservoir/inflow state, estimator agreement, lift health."""
    rows = []
    for wid, well in net.wells.items():
        g = wells[wells["well_id"] == wid].sort_values("date") if len(wells) else wells
        t = tests[(tests["well_id"] == wid) & tests["status"].isin(["ACCEPTED", "ACCEPTED_W", "PROVISIONAL"])]
        t = t.sort_values("test_date")
        pis = t["pi"].dropna() if "pi" in t else pd.Series(dtype=float)
        last = g.iloc[-1] if len(g) else pd.Series(dtype=object)
        rate_theo = (g["liquid"] / (g["hours_on"] / 24.0)) if len(g) else pd.Series(dtype=float)
        dev = (g["vfm_comb"] / rate_theo - 1) if "vfm_comb" in g and len(g) else pd.Series(dtype=float)
        pr = _nz(last.get("pr")) if len(last) else np.nan
        pwf = g["pwf"].dropna().iloc[-1] if "pwf" in g and g["pwf"].notna().any() else (
            t["pwf_test"].dropna().iloc[-1] if "pwf_test" in t and t["pwf_test"].notna().any() else np.nan)
        rows.append(dict(
            well_id=wid, lift=well.lift, pr_ksc=pr if pr == pr else (t["pr_ksc"].dropna().iloc[-1] if len(t) and t["pr_ksc"].notna().any() else np.nan),
            pb_ksc=t["pb_ksc"].dropna().iloc[-1] if len(t) and "pb_ksc" in t and t["pb_ksc"].notna().any() else np.nan,
            pi_prev=pis.iloc[-2] if len(pis) > 1 else np.nan, pi_last=pis.iloc[-1] if len(pis) else np.nan,
            pwf_ksc=pwf, drawdown=(1 - pwf / pr) if pr == pr and pwf == pwf and pr > 0 else np.nan,
            methods=",".join(sorted({m for ms in g.get("vfm_methods", pd.Series(dtype=object)).dropna() for m in str(ms).split(",")})),
            vfm_dev_mean=dev.mean() if len(dev) else np.nan,
            vfm_dev_last=dev.dropna().iloc[-1] if len(dev.dropna()) else np.nan,
            dfl_m=g["dfl_m"].dropna().iloc[-1] if "dfl_m" in g and g["dfl_m"].notna().any() else np.nan,
            submergence_m=g["submergence_m"].dropna().iloc[-1] if "submergence_m" in g and g["submergence_m"].notna().any() else np.nan,
            fillage=g["fillage"].dropna().iloc[-1] if "fillage" in g and g["fillage"].notna().any() else np.nan,
            inj_p=g["inj_p"].dropna().iloc[-1] if well.lift == "gas_lift" and "inj_p" in g and g["inj_p"].notna().any() else np.nan,
            wc_test=last.get("wc") if len(last) else np.nan,
            wc_sample=g["wc_sample"].dropna().iloc[-1] if "wc_sample" in g and g["wc_sample"].notna().any() else np.nan,
        ))
    return pd.DataFrame(rows)
