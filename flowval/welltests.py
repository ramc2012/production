"""Layer 2 - well test validation and daily theoretical (test-based) rates.

Each well test is converted to stock-tank rates (meter factor, oil-leg BS&W,
shrinkage) and screened for duration, stability, physical plausibility
(SRP volumetric efficiency, negative formation gas on gas-lift wells),
consistency with the previous accepted test and with the choke model.

Daily theoretical production for each well = latest usable test rate x
hours-on (x SPM ratio for SRP wells) with an uncertainty that grows with test
age. A calibrated Gilbert-type choke model acts as a virtual flow meter (VFM)
to detect wells whose behaviour has changed since their last test.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd

from . import fluids as F
from . import units as U
from .findings import FindingLog
from .network import Network

USABLE = ("ACCEPTED", "ACCEPTED_W", "PROVISIONAL")


def _nz(x, default=np.nan):
    try:
        return default if x is None or pd.isna(x) else float(x)
    except (TypeError, ValueError):
        return default


def _stability(readings: pd.DataFrame, last_h: int):
    if readings is None or len(readings) < 3:
        return np.nan, np.nan
    r = readings.sort_values("hour")
    q = r["liquid_m3"].astype(float).values
    tail = q[-last_h:] if len(q) >= last_h else q
    cv = float(np.std(tail, ddof=1) / np.mean(tail)) if np.mean(tail) > 0 else np.nan
    x = r["hour"].astype(float).values
    slope = np.polyfit(x, q, 1)[0] if len(q) >= 3 else 0.0
    trend = float(abs(slope) * (x[-1] - x[0] + 1) / np.mean(q)) if np.mean(q) > 0 else np.nan
    return cv, trend


def evaluate_tests(net: Network, data: Dict[str, pd.DataFrame], cfg: dict) -> Tuple[pd.DataFrame, FindingLog]:
    wt = cfg["well_test"]
    vf = cfg["vfm"]
    log = FindingLog()
    tests = data["well_tests"].copy()
    readings = data["well_test_readings"]
    rows = []
    last_ok: Dict[str, dict] = {}

    for _, t in tests.sort_values(["well_id", "test_date"]).iterrows():
        wid, tid, tdate = t["well_id"], t["test_id"], t["test_date"]
        well = net.wells.get(wid)
        sep = net.separators.get(t["separator_id"])
        reasons_rej: List[str] = []
        reasons_prov: List[str] = []
        reasons_warn: List[str] = []

        def flag(sev, check, msg, value=None, limit=None, action=""):
            log.add(tdate, "well_test", sev, "well", wid, check, f"[{tid}] {msg}",
                    value, limit, action)

        if well is None or sep is None:
            flag("ALARM", "unknown", "Well or separator not in network definition")
            continue
        dur = _nz(t["duration_h"], 0.0)
        mf_o = net.meters[sep.oil_meter].meter_factor if sep.oil_meter else 1.0
        mf_w = net.meters[sep.water_meter].meter_factor if sep.water_meter else 1.0
        sf = _nz(t.get("shrinkage_factor"), sep.shrinkage_factor)
        b = _nz(t.get("oil_leg_bsw_pct"), 0.0) / 100.0
        oil_raw, wat_raw = _nz(t["oil_m3"], 0.0), _nz(t["water_m3"], 0.0)
        gas_tot, lift = _nz(t["gas_sm3"], 0.0), _nz(t.get("lift_gas_sm3"), 0.0)

        if dur <= 0:
            flag("ALARM", "duration", "Zero/blank test duration - test rejected")
            reasons_rej.append("no duration")
            dur = np.nan
        k = 24.0 / dur if dur and not math.isnan(dur) else np.nan
        oil = oil_raw * mf_o * (1 - b) * sf * k
        water = (wat_raw * mf_w + oil_raw * mf_o * b) * k
        liquid = oil + water
        wc = water / liquid if liquid > 0 else np.nan
        gas_form = (gas_tot - lift) * k
        gor = gas_form / oil if oil > 0 else np.nan
        glr_total = (gas_tot * k) / liquid if liquid > 0 else np.nan
        # formation gas of a gas-lift well = total - injected: relative uncertainty explodes
        # when lift gas dominates, so GOR tolerances are widened accordingly
        gor_sd_rel = np.nan
        if well.lift == "gas_lift" and gas_form > 0 and sep.gas_meter:
            u_g = cfg["uncertainty"]["meter_u95_pct"].get(net.meters[sep.gas_meter].type, 2.0) / 200
            lm = net.meters.get(well.lift_gas_meter) if well.lift_gas_meter else None
            u_l = cfg["uncertainty"]["meter_u95_pct"].get(lm.type if lm else "orifice", 2.0) / 200
            gor_sd_rel = math.hypot(u_g * gas_tot, u_l * lift) / (gas_tot - lift)

        # ---- duration -------------------------------------------------------
        if dur == dur:
            if dur < wt["min_duration_h"]:
                flag("ALARM", "duration", f"Test duration {dur:g} h below minimum {wt['min_duration_h']} h - rejected",
                     dur, f">= {wt['min_duration_h']} h", "Repeat test with full stabilised duration")
                reasons_rej.append("short duration")
            elif dur < wt["recommended_duration_h"]:
                flag("WARN", "duration", f"Test duration {dur:g} h below recommended {wt['recommended_duration_h']} h",
                     dur, f">= {wt['recommended_duration_h']} h")
                reasons_warn.append("short-ish duration")
        if liquid <= 0 or math.isnan(liquid):
            flag("ALARM", "no_liquid", "No liquid recorded in test - rejected")
            reasons_rej.append("no liquid")

        # ---- stability ------------------------------------------------------
        rd = readings[readings["test_id"] == tid] if len(readings) else None
        cv, trend = _stability(rd, int(wt["stability_last_hours"]))
        if cv == cv:
            if cv > wt["stability_cv_alarm"]:
                flag("ALARM", "stability", f"Unstable test: CV of last hours {cv:.0%}",
                     cv, f"<= {wt['stability_cv_alarm']:.0%}", "Well slugging / not stabilised - retest")
                reasons_rej.append("unstable")
            elif cv > wt["stability_cv_warn"]:
                flag("WARN", "stability", f"Marginal stability: CV {cv:.0%}", cv, f"<= {wt['stability_cv_warn']:.0%}")
                reasons_warn.append("marginal stability")
        if trend == trend and trend > 0.15:
            flag("WARN", "trend", f"Rate trending during test ({trend:.0%} over test)", trend, "<= 15%",
                 "Extend test until stable")
            reasons_warn.append("trending")

        # ---- gas-lift formation gas ----------------------------------------
        if well.lift == "gas_lift":
            if lift <= 0:
                flag("WARN", "lift_gas", "Gas-lift injection during test not recorded - formation GOR overstated")
                reasons_warn.append("no lift gas data")
            elif gas_form < 0:
                flag("ALARM", "negative_gor", f"Separator gas {gas_tot:g} < injected lift gas {lift:g} Sm3",
                     gas_form, ">= 0", "Check test gas meter and lift-gas meter")
                reasons_rej.append("negative formation gas")

        # ---- SRP volumetric efficiency -------------------------------------
        ve = np.nan
        if well.lift == "srp":
            spm = _nz(t.get("spm"))
            stroke = _nz(t.get("stroke_in"), well.stroke_in)
            if spm == spm and spm > 0:
                pd_m3d = F.srp_displacement_m3d(stroke, spm, well.plunger_in)
                ve = liquid / pd_m3d if pd_m3d > 0 else np.nan
                if ve > wt["srp_ve_max"]:
                    flag("ALARM", "srp_ve", f"Liquid {liquid:.1f} m3/d exceeds pump displacement {pd_m3d:.1f} m3/d (VE {ve:.0%})",
                         ve, f"<= {wt['srp_ve_max']:.0%}", "Test meter error or wrong SPM/stroke - retest")
                    reasons_rej.append("VE > 100%")
                elif ve < wt["srp_ve_min"]:
                    flag("WARN", "srp_ve", f"Low pump volumetric efficiency {ve:.0%}", ve,
                         f">= {wt['srp_ve_min']:.0%}", "Check pump wear / gas interference / tubing leak")
                    reasons_warn.append("low VE")

        # ---- sample vs meter water cut -------------------------------------
        sbsw = _nz(t.get("sample_bsw_pct"))
        if sbsw == sbsw and wc == wc and abs(sbsw - 100 * wc) > wt["wc_sample_vs_meter_pp"]:
            flag("WARN", "wc_sample", f"Wellhead sample BS&W {sbsw:.1f}% vs metered water cut {100*wc:.1f}%",
                 100 * wc - sbsw, f"±{wt['wc_sample_vs_meter_pp']} pp", "Check water meter / sampling")
            reasons_warn.append("sample mismatch")

        # ---- choke-model calibration ---------------------------------------
        thp, flp, ch = _nz(t.get("thp_ksc")), _nz(t.get("flp_ksc")), _nz(t.get("choke_64ths"))
        cal, critical = np.nan, False
        if well.lift in ("self_flow", "gas_lift") and thp == thp and ch == ch and liquid > 0:
            thp_a, flp_a = U.ksc_g_to_psia(thp), U.ksc_g_to_psia(_nz(flp, 0.0))
            critical = flp_a / thp_a <= vf["critical_flow_ratio"]
            if critical:
                pred = F.choke_thp_psia(U.m3d_to_bpd(liquid), U.gor_si_to_field(glr_total), ch,
                                        well.choke_correlation)
                cal = thp_a / pred

        # ---- comparison with previous usable test --------------------------
        prev = last_ok.get(wid)
        if prev is not None and not reasons_rej:
            same_choke = (ch != ch) or (prev["choke_64ths"] != prev["choke_64ths"]) or abs(ch - prev["choke_64ths"]) < 0.5
            note = "" if same_choke else f" (choke changed {prev['choke_64ths']:g}->{ch:g}/64)"
            spm_now, spm_prev = _nz(t.get("spm")), prev.get("spm", np.nan)
            norm = 1.0
            if well.lift == "srp" and spm_now == spm_now and spm_prev == spm_prev and spm_now > 0:
                norm = spm_prev / spm_now      # compare liquid per stroke for SRP wells
                if abs(norm - 1) > 0.02:
                    note += f" (normalised for SPM {spm_prev:g}->{spm_now:g})"
            dl = liquid * norm / prev["liquid_m3d"] - 1 if prev["liquid_m3d"] > 0 else np.nan
            dwc = 100 * (wc - prev["wc"])
            dg = gor / prev["gor"] - 1 if prev["gor"] and prev["gor"] > 0 and gor == gor else np.nan
            g_sd = np.hypot(np.nan_to_num(gor_sd_rel), np.nan_to_num(prev.get("gor_sd_rel", 0.0)))
            g_warn = max(wt["gor_change_warn"], 2 * g_sd)
            g_alarm = max(wt["gor_change_alarm"], 3 * g_sd)
            for name, val, w_, a_, unit in (("liquid", dl, wt["liquid_change_warn"], wt["liquid_change_alarm"], "frac"),
                                             ("water cut", dwc, wt["wc_change_warn_pp"], wt["wc_change_alarm_pp"], "pp"),
                                             ("GOR", dg, g_warn, g_alarm, "frac")):
                if val != val:
                    continue
                txt = f"{val:+.0%}" if unit == "frac" else f"{val:+.1f} pp"
                if abs(val) > a_:
                    sev = "WARN" if not same_choke else "ALARM"
                    flag(sev, "test_change", f"{name} changed {txt} vs previous test {prev['test_id']}{note}",
                         float(val), f"±{a_}", "Confirm with a repeat test before using for allocation")
                    reasons_prov.append(f"{name} {txt}")
                elif abs(val) > w_:
                    flag("WARN" if same_choke else "INFO", "test_change",
                         f"{name} changed {txt} vs previous test {prev['test_id']}{note}", float(val), f"±{w_}")
                    reasons_warn.append(f"{name} {txt}")
            if cal == cal and prev.get("choke_cal") == prev.get("choke_cal") and prev.get("choke_cal"):
                dc = cal / prev["choke_cal"] - 1
                if abs(dc) > wt["choke_cal_change_warn"]:
                    flag("WARN", "choke_cal", f"Choke-model calibration moved {dc:+.0%} since last test",
                         dc, f"±{wt['choke_cal_change_warn']:.0%}", "Possible choke erosion/plugging or THP gauge error")
                    reasons_warn.append("choke calibration shift")

        if reasons_rej:
            status = "REJECTED"
        elif reasons_prov:
            status = "PROVISIONAL"
        elif reasons_warn:
            status = "ACCEPTED_W"
        else:
            status = "ACCEPTED"

        row = dict(test_id=tid, well_id=wid, test_date=tdate, separator_id=sep.id, duration_h=dur,
                   oil_m3d=oil, water_m3d=water, liquid_m3d=liquid, wc=wc, gas_form_sm3d=gas_form,
                   gor=gor, glr_total=glr_total, lift_gas_sm3d=lift * k if k == k else np.nan,
                   thp_ksc=thp, flp_ksc=flp, choke_64ths=ch, spm=_nz(t.get("spm")),
                   choke_cal=cal, critical_flow=critical, gor_sd_rel=gor_sd_rel, srp_ve=ve, stability_cv=cv,
                   status=status, reasons="; ".join(reasons_rej + reasons_prov + reasons_warn))
        rows.append(row)
        if status in USABLE:
            last_ok[wid] = row
    return pd.DataFrame(rows), log


# ---------------------------------------------------------------------------
# Daily theoretical rates + VFM
# ---------------------------------------------------------------------------
def _active_test(tests: pd.DataFrame, well_id: str, date: str) -> Optional[pd.Series]:
    t = tests[(tests["well_id"] == well_id) & (tests["test_date"] < date)]
    ok = t[t["status"].isin(USABLE)]
    if len(ok):
        return ok.sort_values("test_date").iloc[-1]
    if len(t):  # only rejected tests -> fall back with very wide uncertainty
        s = t.sort_values("test_date").iloc[-1].copy()
        s["status"] = "FALLBACK"
        return s
    return None


def daily_theoretical(net: Network, wd_day: pd.DataFrame, tests: pd.DataFrame, cfg: dict,
                      date: str, bad: Set[Tuple[str, str, str]]):
    """Return (frame, covariances) for wells on-line on ``date``."""
    uc = cfg["uncertainty"]
    vf = cfg["vfm"]
    rows, covs = [], {}
    for _, r in wd_day.iterrows():
        well = net.wells.get(r["well_id"])
        h = _nz(r["hours_on"], 0.0)
        if well is None or h <= 0:
            continue
        t = _active_test(tests, well.id, date)
        th = min(_nz(r.get("test_hours"), 0.0), h)
        row = dict(date=date, well_id=well.id, lift=well.lift, hours_on=h, test_hours=th,
                   separator=well.separator, lift_gas_sm3=_nz(r.get("lift_gas_sm3"), 0.0)
                   if well.lift == "gas_lift" else 0.0)
        if t is None:
            row.update(test_id=None, test_status="NONE", test_age_d=np.nan, oil=np.nan, water=np.nan,
                       gas=np.nan, liquid=np.nan, wc=np.nan, gor=np.nan)
            rows.append(row)
            continue
        age = (pd.Timestamp(date) - pd.Timestamp(t["test_date"])).days
        frac = h / 24.0
        adj = 1.0
        if well.lift == "srp":
            spm_d, spm_t = _nz(r.get("spm")), _nz(t.get("spm"))
            if spm_d == spm_d and spm_t == spm_t and spm_t > 0 and (r["date"], well.id, "spm") not in bad:
                adj = min(max(spm_d / spm_t, 0.5), 2.0)
        L = t["liquid_m3d"] * frac * adj
        wc = t["wc"]
        gor = t["gor"] if t["gor"] == t["gor"] else 0.0
        oil, water = L * (1 - wc), L * wc
        gas = oil * gor

        # --- uncertainty grows with test age / status -----------------------
        extra = max(0.0, age - uc["fresh_test_days"])
        uL = uc["well_liquid_u95_pct"] + extra * uc["aging_liquid_pct_per_day"]
        uW = uc["well_wc_u95_pp"] + extra * uc["aging_wc_pp_per_day"]
        uG = max(uc["well_gor_u95_pct"], 200 * _nz(t.get("gor_sd_rel"), 0.0)) + extra * uc["aging_gor_pct_per_day"]
        f = {"ACCEPTED": 1.0, "ACCEPTED_W": 1.25, "PROVISIONAL": uc["provisional_factor"]}.get(t["status"], None)
        if f is None:  # FALLBACK on a rejected test
            uL, uW, uG = uc["no_test_u95_pct"], 30.0, uc["no_test_u95_pct"]
        else:
            uL, uW, uG = uL * f, uW * f, uG * f
        sL, sW, sG = L * uL / 200.0, min(uW / 200.0, 0.5), max(gor, 1.0) * uG / 200.0
        J = np.array([[1 - wc, -L, 0.0],
                      [wc, L, 0.0],
                      [(1 - wc) * gor, -L * gor, L * (1 - wc)]])
        cov = J @ np.diag([sL ** 2, sW ** 2, sG ** 2]) @ J.T

        row.update(test_id=t["test_id"], test_status=t["status"], test_age_d=age, oil=oil,
                   water=water, gas=gas, liquid=L, wc=wc, gor=gor, srp_adj=adj,
                   sd_oil=math.sqrt(cov[0, 0]), sd_water=math.sqrt(cov[1, 1]), sd_gas=math.sqrt(cov[2, 2]))

        # --- virtual flow meter (choke model) -------------------------------
        thp, flp, ch = _nz(r.get("thp_ksc")), _nz(r.get("flp_ksc")), _nz(r.get("choke_64ths"))
        row.update(thp_ksc=thp, choke_64ths=ch, test_thp_ksc=t.get("thp_ksc"), test_choke=t.get("choke_64ths"))
        reason = bad.get((date, well.id, "thp_ksc")) if hasattr(bad, "get") else None
        thp_bad = reason is not None or (date, well.id, "thp_ksc") in bad
        row["thp_flagged"] = thp_bad
        row["thp_flag_reason"] = reason
        tthp = _nz(t.get("thp_ksc"))
        # a spike is still physical evidence of shut-in; range/frozen readings are not
        row["suspect_offline"] = bool(reason in (None, "spike") and thp == thp and tthp == tthp
                                      and tthp > 0 and thp > vf["thp_high_ratio"] * tthp)
        if well.lift in ("self_flow", "gas_lift") and not thp_bad and thp == thp and ch == ch \
                and t.get("choke_cal") == t.get("choke_cal") and t.get("choke_cal"):
            thp_a, flp_a = U.ksc_g_to_psia(thp), U.ksc_g_to_psia(_nz(flp, 0.0))
            if flp_a / thp_a <= vf["critical_flow_ratio"]:
                lift_rate = row["lift_gas_sm3"] / frac if frac > 0 else 0.0
                L_rate = t["liquid_m3d"]
                glr = ((t["oil_m3d"] * gor) + (lift_rate if well.lift == "gas_lift" else 0.0)) / L_rate
                q = U.bpd_to_m3d(F.choke_rate_bpd(thp_a, U.gor_si_to_field(glr), ch,
                                                   well.choke_correlation, t["choke_cal"]))
                row["vfm_liquid_m3d"] = q
                row["vfm_dev"] = q / (L_rate * adj) - 1
            else:
                row["vfm_note"] = "sub-critical flow"
        rows.append(row)
        covs[well.id] = cov
    return pd.DataFrame(rows), covs


def daily_well_findings(all_days: pd.DataFrame, cfg: dict) -> FindingLog:
    """Post-pass over the whole period: test age, VFM persistence, THP anomalies."""
    log = FindingLog()
    if all_days.empty:
        return log
    wt, vf = cfg["well_test"], cfg["vfm"]
    for wid, g in all_days.sort_values("date").groupby("well_id"):
        # test age - report first day each threshold is crossed
        for lim, sev in ((wt["age_alarm_days"], "ALARM"), (wt["age_warn_days"], "WARN")):
            over = g[g["test_age_d"] > lim]
            if len(over):
                r = over.iloc[0]
                log.add(r["date"], "well_daily", sev, "well", wid, "test_age",
                        f"Allocation using test {r['test_id']} that is {int(r['test_age_d'])} days old",
                        float(r["test_age_d"]), f"<= {lim} d", "Schedule well test")
                break
        none = g[g["test_status"].isin(["NONE", "FALLBACK"])]
        if len(none):
            r = none.iloc[0]
            log.add(r["date"], "well_daily", "ALARM", "well", wid, "no_valid_test",
                    "No accepted well test available - well carried with wide uncertainty",
                    None, None, "Test the well")
        # THP far above test THP while reported on-line -> possible shut-in
        if "thp_ksc" in g:
            hi = g[g["suspect_offline"].eq(True)] if "suspect_offline" in g else g.iloc[0:0]
            if "nodal_dead" in g:   # the nodal check supersedes this heuristic where it ran
                hi = hi[hi["nodal_dead"].isna()]
            for _, r in hi.iterrows():
                log.add(r["date"], "well_daily", "ALARM", "well", wid, "thp_high",
                        f"THP {r['thp_ksc']:.1f} ksc is {r['thp_ksc']/r['test_thp_ksc']:.1f}x test THP while "
                        f"reported on-line {r['hours_on']:g} h", float(r["thp_ksc"]),
                        f"<= {vf['thp_high_ratio']}x test", "Possible shut-in / choke plugged - verify hours-on")
        # choke changed since the test
        if "choke_64ths" in g:
            chg = g[(g["choke_64ths"].notna()) & (g["test_choke"].notna()) &
                    ((g["choke_64ths"] - g["test_choke"]).abs() >= 1)]
            if len(chg):
                r = chg.iloc[0]
                log.add(r["date"], "well_daily", "WARN", "well", wid, "choke_change",
                        f"Choke {r['choke_64ths']:g}/64 differs from test choke {r['test_choke']:g}/64",
                        float(r["choke_64ths"]), f"{r['test_choke']:g}/64", "Retest after bean change")
    return log
