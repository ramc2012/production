"""Generate a synthetic 30-day GGS dataset with known ("planted") faults.

The ground truth is simulated physically (well rates -> routing -> separator
phase split & shrinkage -> tank inventory with CTL/CTSh -> dispatch), then
measurement noise and the faults below are applied. Running the validation
model on this data should detect every planted fault - that is the model's
acceptance test (see tests/test_end_to_end.py).

Planted faults
--------------
 F1  FT-123 (GS-2 gas meter) reads +12 % from 15-Sep (DP transmitter drift)
 F2  ANK-103 THP transmitter frozen 08-Sep .. 20-Sep
 F3  ANK-106 water breakthrough on 12-Sep (wc 30 % -> 55 %); next test 20-Sep
 F4  ANK-110 test on 10-Sep logged with SPM 6 instead of 9 (VE > 100 %)
 F5  ANK-102 test on 02-Sep only 2.5 h long
 F6  ANK-111 not tested since July (stale test)
 F7  T-2 06:00 dip on 20-Sep recorded 150 mm high
 F8  ANK-104 shut-in 25..27-Sep but DPR shows 24 h on-line on 26-Sep
 F9  FT-302 flare meter data missing 10..11-Sep
 F10 FT-201 dispatch meter proving overdue
 F11 ANK-107 test on 21-Sep slugging / unstable
 F12 GS-2 liquid hold-up small -> retention below API 12J
 F13 GS-1 oil-leg BS&W sample missing on 03-Sep
 F14 ANK-101 beaned up on 24-Sep without a retest (rate from choke/IPR/VLP equilibrium)
 F15 ANK-112 SRP pump leak from 18-Sep: VE 0.90 -> 0.60, fluid level rises, SPM unchanged
 F16 ANK-108 gas-lift injection moves to a shallower valve on 20-Sep (CHP 41.5 -> 35)
 F17 ANK-111 SRP pump fillage 62 % (gas interference)
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from flowval import fluids as F  # noqa: E402
from flowval import units as U  # noqa: E402
from flowval.config import DEFAULTS  # noqa: E402
from flowval import wellmodel as M  # noqa: E402

OUT = ROOT / "data" / "sample"
rng = np.random.default_rng(20260901)

START = pd.Timestamp("2026-09-01")
DAYS = 30
DATES = [START + pd.Timedelta(days=i) for i in range(DAYS)]
DS = [d.strftime("%Y-%m-%d") for d in DATES]
RHO15 = 860.0
GAS_SG = 0.75
SEP_P, SEP_T = 3.5, 40.0
SF = {"GS-1": 0.965, "GS-2": 0.965, "TS-1": 0.960}
OILLEG_BSW = {"GS-1": 5.0, "GS-2": 6.0, "TS-1": 4.0}
FLP = 5.5

# id, lift, separator, L m3/d, wc, GOR Sm3/m3, lift Sm3/d, choke-cal, SRP (plunger, stroke, spm)
WELLS = [
    ("ANK-101", "self_flow", "GS-1", 38, 0.35, 140, 0, 1.05, None),
    ("ANK-102", "self_flow", "GS-1", 25, 0.55, 110, 0, 0.95, None),
    ("ANK-103", "self_flow", "GS-1", 30, 0.20, 180, 0, 1.00, None),
    ("ANK-104", "self_flow", "GS-1", 22, 0.45, 120, 0, 0.92, None),
    ("ANK-105", "gas_lift", "GS-1", 45, 0.60, 60, 9000, 1.08, None),
    ("ANK-106", "gas_lift", "GS-1", 40, 0.30, 70, 8000, 0.97, None),
    ("ANK-107", "gas_lift", "GS-2", 35, 0.50, 65, 7500, 1.02, None),
    ("ANK-108", "gas_lift", "GS-2", 30, 0.70, 55, 7000, 0.94, None),
    ("ANK-109", "srp", "GS-2", 18, 0.40, 30, 0, None, (1.75, 64, 8)),
    ("ANK-110", "srp", "GS-2", 16, 0.55, 25, 0, None, (1.50, 54, 9)),
    ("ANK-111", "srp", "GS-2", 12, 0.65, 20, 0, None, (1.50, 54, 7)),
    ("ANK-112", "srp", "GS-2", 10, 0.50, 25, 0, None, (1.25, 48, 8)),
]
W = {w[0]: dict(zip(["id", "lift", "sep", "L", "wc", "gor", "lift_gas", "cal", "srp"], w)) for w in WELLS}

# --------------------------------------------------------------------------
# Physics-consistent truth: every flowing well sits at the intersection of its
# IPR, its tubing VLP (Beggs & Brill) and its choke; SRP wells produce
# VE x pump displacement and the fluid level follows from the IPR.
# The truth uses slightly different hydraulics from the model (holdup x1.06,
# annulus gradient 0.85 vs 0.9) so the model has to live with calibration.
# --------------------------------------------------------------------------
HL_TRUTH, ANNULUS_TRUTH, T_RES = 1.06, 0.85, 75.0
STATIC = {   # perf depth m, tubing ID in, true PI m3/d/ksc, GL valve m, pump m, SRP VE, CHP ksc
    "ANK-101": dict(perf=1280, tid=1.61, pi=2.0),
    "ANK-102": dict(perf=1310, tid=1.61, pi=1.2),
    "ANK-103": dict(perf=1260, tid=1.61, pi=1.8),
    "ANK-104": dict(perf=1330, tid=1.61, pi=1.0),
    "ANK-105": dict(perf=1350, tid=1.995, pi=3.0, valve=1000, chp=42.0),
    "ANK-106": dict(perf=1300, tid=1.995, pi=2.5, valve=1000, chp=41.0),
    "ANK-107": dict(perf=1320, tid=1.995, pi=2.2, valve=1000, chp=42.5),
    "ANK-108": dict(perf=1290, tid=1.995, pi=2.0, valve=1000, chp=41.5),
    "ANK-109": dict(perf=1300, tid=2.441, pi=0.35, pump=1150, ve=0.62, chp=4.0),
    "ANK-110": dict(perf=1280, tid=2.441, pi=0.30, pump=1130, ve=0.80, chp=3.5),
    "ANK-111": dict(perf=1310, tid=2.441, pi=0.25, pump=1160, ve=0.76, chp=4.5),
    "ANK-112": dict(perf=1270, tid=2.441, pi=0.20, pump=1120, ve=0.90, chp=3.0),
}
for wid, st in STATIC.items():
    w = W[wid]
    if w["srp"]:
        w["L"] = st["ve"] * F.srp_displacement_m3d(w["srp"][1], w["srp"][2], w["srp"][0])


def wht_of(L):
    return 28.0 + 0.2 * L


def geom_of(wid, day=None, wht=35.0):
    st = STATIC[wid]
    valve = st.get("valve")
    if wid == "ANK-108" and day is not None and day >= pd.Timestamp("2026-09-20"):
        valve = 750.0                                                    # F16 shallower valve
    return M.WellGeom(perf_depth_m=st["perf"], tubing_depth_m=st["perf"] - 50, tubing_id_in=st["tid"],
                      valve_depth_m=valve, pump_depth_m=st.get("pump"), wht_c=wht, hl_mult=HL_TRUTH)


def fluid_of(wid, gor=None, wc=None):
    w = W[wid]
    return M.Fluid(oil_rho15=RHO15, gas_sg=GAS_SG, water_rho=1010.0, gor=gor or w["gor"],
                   wc=w["wc"] if wc is None else wc, pb_ksc=STATIC[wid].get("pb"), t_res_c=T_RES)


def solve_pr(pwf, L, J, pb):
    lo, hi = pwf + 0.01, 600.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if M.ipr_rate(pwf, mid, J, pb) < L:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def choke_thp(wid, L, oil, gor, lift, choke):
    glr = (oil * gor + lift) / L
    psia = F.choke_thp_psia(U.m3d_to_bpd(L), U.gor_si_to_field(glr), choke, "gilbert", W[wid]["cal"])
    return psia / U.KSC_TO_PSI - U.ATM_KSC


# calibrate the true reservoir pressure (and choke size) of every well to its base rate
for wid, w in W.items():
    st = STATIC[wid]
    fl = fluid_of(wid)
    st["pb"] = fl.bubble_point_ksc()
    fl = fluid_of(wid)
    g = geom_of(wid, wht=wht_of(w["L"]))
    if w["lift"] in ("self_flow", "gas_lift"):
        pwf_b = M.vlp_pwf(22.0, w["L"], fl, g, w["lift_gas"])
        glr = (w["L"] * (1 - w["wc"]) * w["gor"] + w["lift_gas"]) / w["L"]
        a, b, c = F.CHOKE_CORRELATIONS["gilbert"]
        sz = (w["cal"] * a * U.m3d_to_bpd(w["L"]) * U.gor_si_to_field(glr) ** b / U.ksc_g_to_psia(22.0)) ** (1 / c)
        w["choke"] = float(round(sz))
    else:
        pwf_b = M.pwf_from_fluid_level(st["chp"], 900.0, g, fl, ANNULUS_TRUTH)
        w["choke"] = np.nan
    st["pr"] = solve_pr(pwf_b, w["L"], st["pi"], st["pb"])

ALL_DAYS = pd.date_range("2026-06-01", "2026-10-01")
NOISE = {wid: dict(zip(ALL_DAYS, 1 + 0.015 * rng.standard_normal(len(ALL_DAYS)))) for wid in W}
WCN = {wid: dict(zip(ALL_DAYS, 0.008 * rng.standard_normal(len(ALL_DAYS)))) for wid in W}


def choke_for(wid, day):
    if wid == "ANK-101" and day >= pd.Timestamp("2026-09-24"):
        return 22.0                                                      # F14 bean-up
    return W[wid]["choke"]


def equilibrium(wid, J, fl, g, lift, choke):
    """Liquid rate where tubing (IPR+VLP) and choke give the same THP."""
    st = STATIC[wid]
    oilf = 1 - fl.wc

    def f(L):
        g.wht_c = wht_of(L)
        thp_tub = M.vlp_thp(M.ipr_pwf(L, st["pr"], J, st["pb"]), L, fl, g, lift)
        return thp_tub - choke_thp(wid, L, L * oilf, fl.gor, lift, choke)
    aof = M.ipr_rate(-U.ATM_KSC + 1e-6, st["pr"], J, st["pb"])
    lo, hi = 0.5, aof * 0.98
    for _ in range(45):
        mid = 0.5 * (lo + hi)
        if f(mid) > 0:
            lo = mid
        else:
            hi = mid
    L = 0.5 * (lo + hi)
    g.wht_c = wht_of(L)
    pwf = M.ipr_pwf(L, st["pr"], J, st["pb"])
    return L, M.vlp_thp(pwf, L, fl, g, lift), pwf


def fluid_level_for(wid, pwf, fl, g, chp):
    st = STATIC[wid]
    lo, hi = 0.0, float(st["pump"])
    if M.pwf_from_fluid_level(chp, hi, g, fl, ANNULUS_TRUTH) > pwf:
        return hi                                                        # pumped off
    for _ in range(50):
        mid = 0.5 * (lo + hi)
        if M.pwf_from_fluid_level(chp, mid, g, fl, ANNULUS_TRUTH) > pwf:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


_CACHE = {}


def truth(wid: str, day: pd.Timestamp):
    """True stock-tank rates (per producing day) and well parameters."""
    if (wid, day) in _CACHE:
        return _CACHE[(wid, day)]
    w, st = W[wid], STATIC[wid]
    wc = w["wc"] + WCN[wid][day]
    gor = w["gor"] * (1 + 0.01 * (NOISE[wid][day] - 1) / 0.015)
    if wid == "ANK-106" and day >= pd.Timestamp("2026-09-12"):          # F3 water breakthrough
        wc = 0.55 + WCN[wid][day]
    wc = min(max(wc, 0.0), 0.98)
    J = st["pi"] * NOISE[wid][day] * (1 - 0.0003 * (day - START).days)
    fl = fluid_of(wid, gor, wc)
    spm = w["srp"][2] if w["srp"] else np.nan
    lift = w["lift_gas"] * (1 + 0.01 * rng.standard_normal()) if w["lift"] == "gas_lift" else 0.0
    out = dict(wc=wc, gor=gor, lift=lift, spm=spm, thp=np.nan, chp=st.get("chp", np.nan), dfl=np.nan,
               fillage=np.nan, inj_p=np.nan)
    if w["lift"] in ("self_flow", "gas_lift"):
        g = geom_of(wid, day)
        L, thp, pwf = equilibrium(wid, J, fl, g, lift, choke_for(wid, day))
        out.update(L=L, thp=thp, pwf=pwf, wht=g.wht_c)
        if w["lift"] == "gas_lift":
            inj = st["chp"] - (6.5 if (wid == "ANK-108" and day >= pd.Timestamp("2026-09-20")) else 0.0)
            out.update(inj_p=inj, chp=inj)
    else:
        if wid == "ANK-109" and day >= pd.Timestamp("2026-09-14"):
            spm = 9                                                      # SPM 8 -> 9
        ve = st["ve"]
        if wid == "ANK-112" and day >= pd.Timestamp("2026-09-18"):     # F15 pump leak
            ve = {0: 0.80, 1: 0.70}.get((day - pd.Timestamp("2026-09-18")).days, 0.60)
        L = ve * F.srp_displacement_m3d(w["srp"][1], spm, w["srp"][0]) * NOISE[wid][day]
        g = geom_of(wid, day, wht=wht_of(L))
        pwf = M.ipr_pwf(L, st["pr"], J, st["pb"])
        out.update(L=L, pwf=pwf, wht=wht_of(L), spm=spm,
                   dfl=fluid_level_for(wid, pwf, fl, g, st["chp"]),
                   fillage=62.0 if wid == "ANK-111" else 86.0)                 # F17 low fillage
    hours = 24.0
    if wid == "ANK-104" and pd.Timestamp("2026-09-25") <= day <= pd.Timestamp("2026-09-27"):
        hours = 0.0                                                      # F8 (true)
    if (wid, day.strftime("%Y-%m-%d")) in {("ANK-109", "2026-09-05"): 0, ("ANK-112", "2026-09-17"): 0,
                                           ("ANK-101", "2026-09-09"): 0}:
        hours = {"ANK-109": 20.0, "ANK-112": 18.0, "ANK-101": 22.0}[wid]
    out["hours"] = hours
    _CACHE[(wid, day)] = out
    return out


# --------------------------------------------------------------------------
# Test schedule: well k tested on day (1 + k) and (15 + k), 14-day cycle back to June
# --------------------------------------------------------------------------
ids = list(W)
TEST_DAYS = {}
for k, wid in enumerate(ids):
    days = []
    for base in (START + pd.Timedelta(days=k), START + pd.Timedelta(days=14 + k)):
        days.append(base)
    for n in range(1, 6):
        days.append(START + pd.Timedelta(days=k - 14 * n))
    if wid == "ANK-111":                                                  # F6
        days = [d for d in days if d <= pd.Timestamp("2026-07-17")]
    TEST_DAYS[wid] = sorted(days)


TRUTH = []


def build():
    (OUT / "strapping").mkdir(parents=True, exist_ok=True)
    tests, readings, well_daily = [], [], []
    sep_flow = {(d, s): dict(oil=0.0, water=0.0, gas=0.0) for d in DS for s in SF}
    tid_n = 0
    for wid in ids:
        w = W[wid]
        for day in sorted(set(TEST_DAYS[wid]) | set(DATES)):
            ds = day.strftime("%Y-%m-%d")
            t = truth(wid, day)
            in_period = day in DATES
            is_test = day in TEST_DAYS[wid]
            th = 0.0
            test_L = t["L"]
            if is_test:
                th = 2.5 if (wid == "ANK-102" and ds == "2026-09-02") else float(rng.choice([8.0, 10.0, 12.0]))
                test_L = t["L"] * (1 + 0.03 * rng.standard_normal())
                oil_t, wat_t = test_L * (1 - t["wc"]), test_L * t["wc"]
                gas_t = oil_t * t["gor"] * (1 + 0.03 * rng.standard_normal())
                frac = th / 24
                b = OILLEG_BSW["TS-1"] + 0.3 * rng.standard_normal()
                oil_sep = oil_t / SF["TS-1"] * frac
                oleg = oil_sep / (1 - b / 100)
                ol_w = oleg - oil_sep
                tid_n += 1
                test_id = f"WT-{day.strftime('%y%m%d')}-{wid[-3:]}"
                spm_log = t["spm"]
                if wid == "ANK-110" and ds == "2026-09-10":
                    spm_log = 6.0                                        # F4 entry error
                thp = t["thp"]
                tests.append(dict(
                    test_id=test_id, well_id=wid, test_date=ds, duration_h=th, separator_id="TS-1",
                    oil_m3=round(oleg * (1 + 0.0025 * rng.standard_normal()), 3),
                    oil_leg_bsw_pct=round(b, 2),
                    water_m3=round(max(wat_t * frac - ol_w, 0) * (1 + 0.005 * rng.standard_normal()), 3),
                    gas_sm3=round((gas_t + t["lift"]) * frac * (1 + 0.01 * rng.standard_normal()), 1),
                    lift_gas_sm3=round(t["lift"] * frac, 1) if w["lift"] == "gas_lift" else np.nan,
                    thp_ksc=round(thp * (1 + 0.01 * rng.standard_normal()), 2) if thp == thp else np.nan,
                    flp_ksc=FLP if thp == thp else np.nan,
                    chp_ksc=round(t["chp"] + 0.2 * rng.standard_normal(), 2) if t["chp"] == t["chp"] else np.nan,
                    inj_pressure_ksc=round(t["inj_p"] + 0.2 * rng.standard_normal(), 2) if t["inj_p"] == t["inj_p"] else np.nan,
                    wht_degc=round(t["wht"] + 0.5 * rng.standard_normal(), 1),
                    dfl_m=round(t["dfl"] + 5 * rng.standard_normal(), 0) if t["dfl"] == t["dfl"] else np.nan,
                    pump_fillage_pct=round(t["fillage"] + 2 * rng.standard_normal(), 0) if t["fillage"] == t["fillage"] else np.nan,
                    choke_64ths=choke_for(wid, day), sep_pressure_ksc=SEP_P, sep_temp_degc=SEP_T,
                    spm=spm_log, stroke_in=w["srp"][1] if w["srp"] else np.nan,
                    sample_bsw_pct=round(100 * t["wc"] + 1.0 * rng.standard_normal(), 1),
                    remarks=""))
                cvn = 0.25 if (wid == "ANK-107" and ds == "2026-09-21") else 0.03   # F11
                for h in range(1, int(math.ceil(th)) + 1):
                    readings.append(dict(test_id=test_id, hour=h,
                                         liquid_m3=round(test_L / 24 * max(1 + cvn * rng.standard_normal(), 0.2), 3),
                                         gas_sm3=round((gas_t + t["lift"]) / 24 * (1 + 0.03 * rng.standard_normal()), 1),
                                         thp_ksc=round(thp * (1 + 0.01 * rng.standard_normal()), 2) if thp == thp else np.nan))
            if not in_period:
                continue
            # ---- true flows to separators -------------------------------------
            hrs = t["hours"]
            tr = dict(date=ds, well_id=wid, oil=0.0, water=0.0, gas=0.0)
            TRUTH.append(tr)
            for sid, hh, Lr in (("TS-1", th, test_L), (w["sep"], max(hrs - th, 0.0), t["L"])):
                if hh <= 0 or hrs <= 0:
                    continue
                f = hh / 24
                o, wa = Lr * (1 - t["wc"]) * f, Lr * t["wc"] * f
                tr["oil"] += o
                tr["water"] += wa
                tr["gas"] += o * t["gor"]
                sep_flow[(ds, sid)]["oil"] += o
                sep_flow[(ds, sid)]["water"] += wa
                sep_flow[(ds, sid)]["gas"] += o * t["gor"] + t["lift"] * f
            # ---- DPR (reported) values ----------------------------------------
            rep_h, status = hrs, ("FLOWING" if hrs > 0 else "SI")
            thp = np.nan
            if w["choke"] == w["choke"]:
                if hrs > 0:
                    thp = t["thp"] * (1 + 0.01 * rng.standard_normal())
                else:
                    thp = 2.0 * 22.0 + rng.standard_normal()             # shut-in: gas cap builds THP
            k_idx = ids.index(wid)
            survey = w["lift"] == "srp" and hrs > 0 and ((day - START).days % 5 == k_idx % 5 or th > 0)
            if wid == "ANK-104" and ds == "2026-09-26":                     # F8 reported
                rep_h, status = 24.0, "FLOWING"
            well_daily.append(dict(
                date=ds, well_id=wid, status=status, hours_on=rep_h, test_hours=th if hrs > 0 else 0.0,
                thp_ksc=round(thp, 2) if thp == thp else np.nan,
                flp_ksc=round(FLP + 0.2 * rng.standard_normal(), 2) if w["choke"] == w["choke"] else np.nan,
                chp_ksc=round(t["chp"] + 0.2 * rng.standard_normal(), 2) if t["chp"] == t["chp"] else np.nan,
                inj_pressure_ksc=round(t["inj_p"] + 0.2 * rng.standard_normal(), 2) if t["inj_p"] == t["inj_p"] else np.nan,
                wht_degc=round(t["wht"] + 0.5 * rng.standard_normal(), 1) if hrs > 0 else np.nan,
                dfl_m=round(t["dfl"] + 5 * rng.standard_normal(), 0) if survey else np.nan,
                pump_fillage_pct=round(t["fillage"] + 2 * rng.standard_normal(), 0) if survey else np.nan,
                sample_bsw_pct=round(min(max(100 * t["wc"] + 2.0 * rng.standard_normal(), 0), 99), 1)
                if hrs > 0 and (day - START).days % 7 == (k_idx + 3) % 7 else np.nan,
                choke_64ths=choke_for(wid, day),
                lift_gas_sm3=round(t["lift"] * hrs / 24, 0) if w["lift"] == "gas_lift" else np.nan,
                spm=t["spm"] if hrs > 0 else 0.0, stroke_in=w["srp"][1] if w["srp"] else np.nan,
                remarks=""))
    wd = pd.DataFrame(well_daily)
    # F2 frozen THP on ANK-103
    m = (wd["well_id"] == "ANK-103") & (wd["date"] >= "2026-09-08") & (wd["date"] <= "2026-09-20")
    frozen = wd.loc[(wd["well_id"] == "ANK-103") & (wd["date"] == "2026-09-07"), "thp_ksc"].iloc[0]
    wd.loc[m, "thp_ksc"] = frozen
    wd.sort_values(["date", "well_id"]).to_csv(OUT / "well_daily.csv", index=False)
    pd.DataFrame(tests).sort_values(["test_date", "well_id"]).to_csv(OUT / "well_tests.csv", index=False)
    pd.DataFrame(readings).to_csv(OUT / "well_test_readings.csv", index=False)
    pd.DataFrame(TRUTH).sort_values(["date", "well_id"]).to_csv(OUT / "_truth_well_daily.csv", index=False)
    # well master + reservoir pressure surveys (what an asset would have on file)
    ws = []
    for wid in ids:
        st = STATIC[wid]
        ws.append(dict(well_id=wid, perf_depth_m=st["perf"], tubing_depth_m=st["perf"] - 50,
                       tubing_id_in=st["tid"], casing_id_in=4.892, valve_depth_m=st.get("valve", np.nan),
                       pump_depth_m=st.get("pump", np.nan), reservoir_temp_degc=T_RES))
    pd.DataFrame(ws).to_csv(OUT / "well_static.csv", index=False)
    pd.DataFrame([dict(date="2026-06-15", well_id=wid, pr_ksc=round(STATIC[wid]["pr"], 1), survey_type="SBHP")
                  for wid in ids]).to_csv(OUT / "reservoir_surveys.csv", index=False)

    # ---- separators, meters ---------------------------------------------------
    meters, sepd = [], []
    to_tank = {d: dict(oil=0.0, water=0.0) for d in DS}
    gas_total = {d: 0.0 for d in DS}
    mids = {"GS-1": ("FT-111", "FT-112", "FT-113"), "GS-2": ("FT-121", "FT-122", "FT-123"),
            "TS-1": ("FT-131", "FT-132", "FT-133")}
    for ds in DS:
        for sid in SF:
            fl = sep_flow[(ds, sid)]
            b = OILLEG_BSW[sid] + 0.4 * rng.standard_normal()
            oil_sep = fl["oil"] / SF[sid]
            oleg = oil_sep / (1 - b / 100) if fl["oil"] > 0 else 0.0
            ol_w = oleg - oil_sep
            wleg = max(fl["water"] - ol_w, 0.0)
            to_tank[ds]["oil"] += fl["oil"]
            to_tank[ds]["water"] += fl["water"] - wleg
            gas_total[ds] += fl["gas"]
            gbias = 1.12 if (sid == "GS-2" and ds >= "2026-09-15") else 1.0   # F1
            mo, mw, mg = mids[sid]
            meters += [dict(date=ds, meter_id=mo, volume=round(oleg * (1 + 0.0015 * rng.standard_normal()), 3)),
                       dict(date=ds, meter_id=mw, volume=round(wleg * (1 + 0.003 * rng.standard_normal()), 3)),
                       dict(date=ds, meter_id=mg, volume=round(fl["gas"] * gbias * (1 + 0.006 * rng.standard_normal()), 1))]
            if fl["oil"] > 0:
                bsw_rep = round(b + 0.3 * rng.standard_normal(), 2)
                if sid == "GS-1" and ds == "2026-09-03":
                    bsw_rep = np.nan                                     # F13
                sepd.append(dict(date=ds, separator_id=sid, pressure_ksc=round(SEP_P + 0.1 * rng.standard_normal(), 2),
                                 temperature_degc=round(SEP_T + 0.8 * rng.standard_normal(), 1), oil_leg_bsw_pct=bsw_rep))
        gt = gas_total[ds]
        flare = 0.05 * gt * (1 + 0.1 * rng.standard_normal())
        fuel = 0.03 * gt
        comp = gt - flare - fuel
        meters += [dict(date=ds, meter_id="FT-301", volume=round(comp * (1 + 0.003 * rng.standard_normal()), 1)),
                   dict(date=ds, meter_id="FT-302", volume=np.nan if ds in ("2026-09-10", "2026-09-11")
                        else round(flare * (1 + 0.08 * rng.standard_normal()), 1)),           # F9
                   dict(date=ds, meter_id="FT-303", volume=round(fuel * (1 + 0.006 * rng.standard_normal()), 1))]
        lift_true = wd[(wd["date"] == ds)]["lift_gas_sm3"].sum()
        meters.append(dict(date=ds, meter_id="FT-401", volume=round(lift_true * (1 + 0.005 * rng.standard_normal()), 1)))
    pd.DataFrame(meters).to_csv(OUT / "meter_daily.csv", index=False)
    pd.DataFrame(sepd).to_csv(OUT / "separator_daily.csv", index=False)

    # ---- tanks ------------------------------------------------------------------
    tanks = ["T-1", "T-2", "T-3"]
    area = math.pi * 7.0 ** 2 / 4                       # m2 -> m3 per m
    lv = np.arange(0, 8001, 100)
    vol = area * lv / 1000 - 0.5 * np.minimum(lv, 500) / 500
    for tk in tanks:
        pd.DataFrame({"level_mm": lv, "volume_m3": np.round(vol, 3)}).to_csv(OUT / "strapping" / f"{tk}.csv", index=False)

    def level_of(v):
        return float(np.interp(v, vol, lv))
    bsw_tank = 0.5
    state = {tk: dict(nsv=150.0 + 30 * i, fw=4.0) for i, tk in enumerate(tanks)}
    gauges, drains, disp = [], [], []

    def gauge(ts, tk, s):
        t_l = 37.0 + 0.8 * rng.standard_normal()
        t_a = 31.0 + 1.5 * rng.standard_normal()
        gsv = s["nsv"] / (1 - bsw_tank / 100)
        cs = F.ctsh(t_l, t_a, False)
        gov = gsv / F.ctl_54a(RHO15, t_l)
        fw_strap = s["fw"] / cs
        tov = gov / cs + fw_strap
        lvl = level_of(tov)
        if tk == "T-2" and ts.startswith("2026-09-20"):
            lvl += 150.0                                                 # F7
        return dict(datetime=ts, tank_id=tk, level_mm=round(lvl + 0.8 * rng.standard_normal(), 0),
                    free_water_mm=round(level_of(fw_strap), 0), liquid_temp_degc=round(t_l, 1),
                    ambient_temp_degc=round(t_a, 1),
                    density_obs_kgm3=round(RHO15 * F.ctl_54a(RHO15, t_l) + 0.2 * rng.standard_normal(), 1),
                    density_obs_temp_degc=round(t_l, 1), bsw_pct=round(bsw_tank + 0.03 * rng.standard_normal(), 2))

    for i, ds in enumerate(DS + ["2026-10-01"]):
        ts = f"{ds} 06:00"
        for tk in tanks:
            gauges.append(gauge(ts, tk, state[tk]))
        if ds == "2026-10-01":
            break
        prod = to_tank[ds]["oil"]
        dnsv = prod * (1 + 0.15 * math.sin(2 * math.pi * i / 7))
        tdisp = 0.0
        for tk in tanks:
            s = state[tk]
            old_susp = s["nsv"] * bsw_tank / (100 - bsw_tank)
            s["nsv"] += prod / 3 - dnsv / 3
            new_susp = s["nsv"] * bsw_tank / (100 - bsw_tank)
            disp_w = dnsv / 3 * bsw_tank / (100 - bsw_tank)
            s["fw"] += to_tank[ds]["water"] / 3 - disp_w - (new_susp - old_susp)
            dr = max(s["fw"] - 4.0, 0.0)
            s["fw"] -= dr
            drains.append(dict(date=ds, tank_id=tk, water_drained_m3=round(dr * (1 + 0.04 * rng.standard_normal()), 2)))
            tdisp += dnsv / 3
        t_d = 36.0 + 0.5 * rng.standard_normal()
        gross = tdisp / (1 - bsw_tank / 100) / F.ctl_54a(RHO15, t_d)
        disp.append(dict(date=ds, meter_id="FT-201", gross_m3=round(gross * (1 + 0.0008 * rng.standard_normal()), 3),
                         temp_degc=round(t_d, 1), density15_kgm3=RHO15, bsw_pct=bsw_tank))
    pd.DataFrame(gauges).to_csv(OUT / "tank_gauges.csv", index=False)
    pd.DataFrame(drains).to_csv(OUT / "tank_drains.csv", index=False)
    pd.DataFrame(disp).to_csv(OUT / "dispatch_daily.csv", index=False)

    # ---- network definition ----------------------------------------------------
    net = {
        "installation": {"name": "GGS-Demo (synthetic)", "gas_sg": GAS_SG,
                         "oil_rho15_kgm3": RHO15, "water_rho_kgm3": 1010.0},
        "wells": [], "separators": [
            dict(id="GS-1", kind="production", phases=3, orientation="horizontal", diameter_m=1.6, length_m=5.0,
                 liquid_volume_m3=5.0, shrinkage_factor=SF["GS-1"], oil_meter="FT-111", water_meter="FT-112",
                 gas_meter="FT-113"),
            dict(id="GS-2", kind="production", phases=3, orientation="vertical", diameter_m=1.2, length_m=3.5,
                 liquid_volume_m3=0.35, shrinkage_factor=SF["GS-2"], oil_meter="FT-121", water_meter="FT-122",
                 gas_meter="FT-123"),
            dict(id="TS-1", kind="test", phases=3, orientation="vertical", diameter_m=0.9, length_m=3.0,
                 liquid_volume_m3=0.4, shrinkage_factor=SF["TS-1"], oil_meter="FT-131", water_meter="FT-132",
                 gas_meter="FT-133"),
        ],
        "meters": [
            dict(id="FT-111", phase="oil", type="pd", last_proved="2026-08-10", proving_interval_days=90, description="GS-1 oil leg"),
            dict(id="FT-112", phase="water", type="mag", description="GS-1 water leg"),
            dict(id="FT-113", phase="gas", type="orifice", last_proved="2026-07-20", proving_interval_days=180, description="GS-1 gas"),
            dict(id="FT-121", phase="oil", type="pd", last_proved="2026-08-12", proving_interval_days=90, description="GS-2 oil leg"),
            dict(id="FT-122", phase="water", type="mag", description="GS-2 water leg"),
            dict(id="FT-123", phase="gas", type="orifice", last_proved="2026-07-22", proving_interval_days=180, description="GS-2 gas"),
            dict(id="FT-131", phase="oil", type="coriolis", last_proved="2026-08-01", proving_interval_days=90, description="Test sep oil"),
            dict(id="FT-132", phase="water", type="mag", description="Test sep water"),
            dict(id="FT-133", phase="gas", type="orifice", description="Test sep gas"),
            dict(id="FT-201", phase="oil", type="pd", u95_pct=0.3, last_proved="2026-04-20", proving_interval_days=90,
                 description="Dispatch to CTF"),
            dict(id="FT-301", phase="gas", type="ultrasonic", description="Compressor suction"),
            dict(id="FT-302", phase="gas", type="flare_estimate", description="Flare (estimated)"),
            dict(id="FT-303", phase="gas", type="vortex", description="Fuel gas"),
            dict(id="FT-401", phase="gas", type="orifice", description="Lift-gas header"),
        ],
        "tanks": [dict(id=tk, strapping_table=f"strapping/{tk}.csv", insulated=False, calibration_temp_c=15.0,
                       safe_fill_mm=7200) for tk in tanks],
        "dispatch": {"meter": "FT-201"},
        "gas_disposal": [dict(meter="FT-301", role="compressor"), dict(meter="FT-302", role="flare"),
                         dict(meter="FT-303", role="fuel")],
        "lift_gas": {"header_meter": "FT-401"},
    }
    for w in W.values():
        e = dict(id=w["id"], lift=w["lift"], separator=w["sep"])
        if w["srp"]:
            e.update(plunger_in=w["srp"][0], stroke_in=w["srp"][1])
        if w["lift"] == "gas_lift":
            mid = f"FT-4{w['id'][-2:]}"
            e["lift_gas_meter"] = mid
            net["meters"].append(dict(id=mid, phase="gas", type="orifice", description=f"{w['id']} lift gas"))
        net["wells"].append(e)
    with open(OUT / "network.yaml", "w") as fh:
        fh.write("# GGS network definition (synthetic demo). Units: m, m3, ksc(g), degC.\n")
        yaml.safe_dump(net, fh, sort_keys=False)
    with open(OUT / "config.yaml", "w") as fh:
        fh.write("# Validation tolerances & uncertainties. Delete any key to use the built-in default.\n"
                 "# Uncertainties are expanded U95 (k=2) in % unless the key says pp (percentage points).\n")
        yaml.safe_dump(DEFAULTS, fh, sort_keys=False)
    print(f"Sample data written to {OUT}")


if __name__ == "__main__":
    build()
