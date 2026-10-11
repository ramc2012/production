"""Levels 0-1: the twin's asset model and steady-state / look-ahead plant simulator.

A ``FacilityTwin`` is built from the validated results:

* every well becomes a ``WellTwin``, calibrated from its latest usable test
  (PI, reservoir and bubble-point pressure, choke factor, SRP volumetric
  efficiency) and set to the latest day's operating point (choke, lift gas,
  SPM, FLP);
* the twin is then *synchronised* (state estimation): each well's PI (or SRP
  VE) is re-tuned so that the model reproduces the reconciled rate of the
  latest day; the tuning factors are reported as model-health indicators;
* separators, compressor, lift-gas supply and the tank farm form the facility.

The same classes run the hidden "plant" in the closed-loop demo (Level 5) with
deliberately perturbed parameters, so the twin always has to cope with
model-plant mismatch.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .. import fluids as F
from .. import units as U
from .. import wellmodel as M
from ..io import load_inputs
from ..tanks import gauge_volumes, pick_gauges
from ..wellperf import ReservoirPressure, fluid_for, geom_for

USABLE = ("ACCEPTED", "ACCEPTED_W", "PROVISIONAL")


def _nz(x, d=np.nan):
    try:
        return d if x is None or pd.isna(x) else float(x)
    except (TypeError, ValueError):
        return d


# ---------------------------------------------------------------------------
# Well twin
# ---------------------------------------------------------------------------
@dataclass
class WellTwin:
    id: str
    lift: str
    separator: str
    fluid: Optional[M.Fluid] = None
    geom: Optional[M.WellGeom] = None
    pr: float = np.nan
    pb: float = np.nan
    pi: float = np.nan
    choke: float = np.nan
    choke_cal: float = np.nan
    flp: float = 5.5
    lift_gas: float = 0.0           # Sm3/d
    spm: float = np.nan
    stroke: float = np.nan
    plunger: float = np.nan
    ve: float = np.nan
    chp: float = 4.0
    on: bool = True
    tuning: float = 1.0             # PI (or VE) multiplier from synchronisation
    test_id: str = ""
    fallback_rate: float = np.nan   # used when no physics model is available

    @property
    def modelled(self) -> bool:
        return self.fluid is not None and self.geom is not None and self.pi == self.pi and self.pr == self.pr

    # -- choke-limited equilibrium for flowing / gas-lift wells --------------
    def _choke_thp(self, L: float, lift: float) -> float:
        glr = (L * (1 - self.fluid.wc) * self.fluid.gor + lift) / max(L, 1e-6)
        psia = F.choke_thp_psia(U.m3d_to_bpd(L), U.gor_si_to_field(glr), self.choke, "gilbert", self.choke_cal)
        return max(psia / U.KSC_TO_PSI - U.ATM_KSC, self.flp + 0.3)

    def _flowing(self, choke: float, lift: float) -> dict:
        pi = self.pi * self.tuning
        old_choke, self.choke = self.choke, choke
        try:
            aof = M.ipr_rate(-U.ATM_KSC + 1e-6, self.pr, pi, self.pb)

            def f(L):
                pwf = M.ipr_pwf(L, self.pr, pi, self.pb)
                return M.vlp_thp(pwf, L, self.fluid, self.geom, lift) - self._choke_thp(L, lift)
            # stable operating point = right-most + -> - crossing (low-rate side can be unstable)
            qs = [aof * x for x in (0.02, 0.06, 0.12, 0.2, 0.3, 0.42, 0.56, 0.72, 0.86, 0.97)]
            vals = [f(q) for q in qs]
            idx = max((i for i, v in enumerate(vals) if v > 0), default=None)
            if idx is None:
                return dict(L=0.0, thp=np.nan, pwf=self.pr)
            if idx == len(qs) - 1:
                L = qs[-1]
                return dict(L=L, thp=self._choke_thp(L, lift), pwf=M.ipr_pwf(L, self.pr, pi, self.pb))
            lo, hi = qs[idx], qs[idx + 1]
            for _ in range(30):
                mid = 0.5 * (lo + hi)
                if f(mid) > 0:
                    lo = mid
                else:
                    hi = mid
            L = 0.5 * (lo + hi)
            pwf = M.ipr_pwf(L, self.pr, pi, self.pb)
            return dict(L=L, thp=self._choke_thp(L, lift), pwf=pwf)
        finally:
            self.choke = old_choke

    def _srp(self, spm: float) -> dict:
        pd_cap = F.srp_displacement_m3d(self.stroke, spm, self.plunger) * self.ve * \
            (self.tuning if self.tuning_target == "ve" else 1.0)
        pi = self.pi * (self.tuning if self.tuning_target == "pi" else 1.0)
        pump = self.geom.pump_depth_m or self.geom.perf_depth_m
        pwf_min = M.pwf_from_fluid_level(self.chp, pump, self.geom, self.fluid)
        q_max = M.ipr_rate(pwf_min, self.pr, pi, self.pb)
        L = min(pd_cap, q_max)
        pwf = M.ipr_pwf(L, self.pr, pi, self.pb)
        lo, hi = 0.0, float(pump)
        for _ in range(40):
            mid = 0.5 * (lo + hi)
            if M.pwf_from_fluid_level(self.chp, mid, self.geom, self.fluid) > pwf:
                lo = mid
            else:
                hi = mid
        dfl = 0.5 * (lo + hi)
        return dict(L=L, pwf=pwf, dfl=dfl, submergence=pump - dfl, pump_limited=pd_cap < q_max,
                    inflow_capacity=q_max)

    tuning_target: str = "pi"

    def solve(self, choke: float = None, lift_gas: float = None, spm: float = None, on: bool = None) -> dict:
        choke = self.choke if choke is None else choke
        lift = self.lift_gas if lift_gas is None else lift_gas
        spm = self.spm if spm is None else spm
        on = self.on if on is None else on
        base = dict(well_id=self.id, lift=self.lift, separator=self.separator, choke=choke,
                    lift_gas=lift if self.lift == "gas_lift" else 0.0, spm=spm)
        if not on:
            return {**base, "L": 0.0, "oil": 0.0, "water": 0.0, "gas": 0.0, "on": False}
        if not self.modelled:
            L = self.fallback_rate if self.fallback_rate == self.fallback_rate else 0.0
            r = dict(L=L)
        elif self.lift in ("self_flow", "gas_lift") and self.choke == self.choke and self.choke_cal == self.choke_cal:
            r = self._flowing(choke, lift if self.lift == "gas_lift" else 0.0)
        elif self.lift == "srp" and self.plunger == self.plunger and self.ve == self.ve and spm == spm:
            r = self._srp(spm)
        else:
            L = self.fallback_rate if self.fallback_rate == self.fallback_rate else 0.0
            r = dict(L=L)
        wc = self.fluid.wc if self.fluid else 0.5
        gor = self.fluid.gor if self.fluid else 0.0
        L = r["L"]
        r.update(base)
        r.update(oil=L * (1 - wc), water=L * wc, gas=L * (1 - wc) * gor, on=True,
                 drawdown=(1 - r["pwf"] / self.pr) if r.get("pwf") == r.get("pwf") and self.pr == self.pr and "pwf" in r else np.nan)
        return r


# ---------------------------------------------------------------------------
# Facility twin
# ---------------------------------------------------------------------------
@dataclass
class TankFarm:
    capacity_m3: float          # sum of volumes to safe-fill level
    inventory_m3: float         # gross liquid now (oil + water)
    tanks: List[dict] = field(default_factory=list)


@dataclass
class FacilityTwin:
    name: str
    wells: Dict[str, WellTwin]
    separators: List[str]
    compressor_capacity: float
    lift_available: float
    fuel_fraction: float
    tankfarm: TankFarm
    dispatch_rate_m3h: float
    dispatch_start_frac: float
    dispatch_stop_frac: float
    shrinkage: Dict[str, float] = field(default_factory=dict)
    date: str = ""

    def setpoints(self) -> Dict[str, dict]:
        return {w.id: dict(choke=w.choke, lift_gas=w.lift_gas, spm=w.spm, on=w.on) for w in self.wells.values()}

    def steady(self, sp: Optional[Dict[str, dict]] = None) -> dict:
        sp = sp or {}
        rows = [w.solve(**sp.get(w.id, {})) for w in self.wells.values()]
        df = pd.DataFrame(rows)
        oil, water, gas_f = df["oil"].sum(), df["water"].sum(), df["gas"].sum()
        lift = df["lift_gas"].sum()
        total_gas = gas_f + lift
        comp = min(total_gas * (1 - self.fuel_fraction), self.compressor_capacity)
        flare = max(total_gas * (1 - self.fuel_fraction) - self.compressor_capacity, 0.0)
        seps = df.groupby("separator")[["oil", "water", "gas", "lift_gas"]].sum()
        seps["gas_total"] = seps["gas"] + seps["lift_gas"]
        return dict(wells=df, oil=oil, water=water, gas_form=gas_f, lift=lift, gas_total=total_gas,
                    compressor_load=comp / self.compressor_capacity if self.compressor_capacity else np.nan,
                    flare=flare, lift_excess=max(lift - self.lift_available, 0.0), separators=seps)

    def lookahead(self, hours: int = 72, sp=None, dispatch_outage: tuple = (), compressor_trip: tuple = (),
                  step_h: float = 1.0) -> pd.DataFrame:
        """Hour-by-hour tank-farm and gas look-ahead from the current state.

        dispatch_outage / compressor_trip: (start_h, end_h) windows.
        """
        st = self.steady(sp)
        inflow = (st["oil"] + st["water"] * 0.05) / 24.0      # oil + water carried in oil legs (m3/h)
        inv = self.tankfarm.inventory_m3
        cap = self.tankfarm.capacity_m3
        pumping = inv / cap > self.dispatch_start_frac
        rows = []
        t = 0.0
        while t <= hours + 1e-9:
            out_ok = not (dispatch_outage and dispatch_outage[0] <= t < dispatch_outage[1])
            if inv / cap >= self.dispatch_start_frac:
                pumping = True
            if inv / cap <= self.dispatch_stop_frac:
                pumping = False
            disp = self.dispatch_rate_m3h if (pumping and out_ok) else 0.0
            trip = compressor_trip and compressor_trip[0] <= t < compressor_trip[1]
            flare = st["gas_total"] * (1 - self.fuel_fraction) if trip else st["flare"]
            rows.append(dict(hour=t, inventory_m3=inv, fill_frac=inv / cap, dispatch_m3h=disp,
                             flare_sm3d=flare, oil_m3d=st["oil"]))
            inv = min(max(inv + (inflow - disp) * step_h, 0.0), cap * 1.2)
            t += step_h
        return pd.DataFrame(rows)

    def copy(self) -> "FacilityTwin":
        return copy.deepcopy(self)


# ---------------------------------------------------------------------------
# Build from validation results
# ---------------------------------------------------------------------------
def synchronise(wells: Dict[str, "WellTwin"], targets: Dict[str, float], iterations: int = 4) -> Dict[str, float]:
    """State estimation: re-tune each well's PI (or SRP VE) so the model reproduces
    the observed / reconciled liquid rate. Returns the tuning factors."""
    for wid, target in targets.items():
        wt = wells.get(wid)
        if wt is None or not wt.modelled or not wt.on or not target > 0:
            continue
        if wt.lift == "srp":
            r0 = wt.solve()
            wt.tuning_target = "ve" if r0.get("pump_limited", True) else "pi"
        for _ in range(iterations):
            q = wt.solve()["L"]
            if not q > 0:
                break
            ratio = min(max(target / q, 0.5), 2.0)
            wt.tuning = min(max(wt.tuning * ratio ** (1.0 if wt.lift == "srp" else 1.3), 0.3), 3.0)
            if abs(ratio - 1) < 0.005:
                break
    return {w: wells[w].tuning for w in wells}


def build_twin(res, data_dir, date: Optional[str] = None) -> FacilityTwin:
    net, cfg = res.network, res.cfg
    tc = cfg["twin"]
    data = load_inputs(data_dir)
    date = date or res.dates[-1]
    pres = ReservoirPressure(net, data["reservoir_surveys"])
    wd = data["well_daily"]
    wd_last = wd[wd["date"] <= date].sort_values("date").groupby("well_id").tail(1).set_index("well_id")
    tests = res.tests
    wells = {}
    for wid, w in net.wells.items():
        t = tests[(tests["well_id"] == wid) & tests["status"].isin(USABLE) & (tests["test_date"] <= date)]
        t = t.sort_values("test_date").iloc[-1] if len(t) else None
        r = wd_last.loc[wid] if wid in wd_last.index else None
        wt = WellTwin(id=wid, lift=w.lift, separator=w.separator)
        if r is not None:
            wt.on = _nz(r.get("hours_on"), 24.0) > 0 and str(r.get("status") or "").upper() not in ("SI",)
            wt.choke = _nz(r.get("choke_64ths"))
            wt.flp = _nz(r.get("flp_ksc"), 5.5)
            wt.spm = _nz(r.get("spm"))
            wt.stroke = _nz(r.get("stroke_in"), _nz(w.stroke_in))
            wt.chp = _nz(r.get("chp_ksc"), 4.0)
            h = max(_nz(r.get("hours_on"), 24.0), 0.1)
            wt.lift_gas = _nz(r.get("lift_gas_sm3"), 0.0) * 24.0 / h if w.lift == "gas_lift" else 0.0
        wt.plunger = _nz(w.plunger_in)
        if t is not None:
            wt.test_id = t["test_id"]
            wt.fluid = fluid_for(net, w, _nz(t.get("gor"), 50.0), _nz(t.get("wc"), 0.5), cfg)
            wt.geom = geom_for(w, _nz(r.get("wht_degc")) if r is not None else None, cfg)
            wt.pr = pres.at(wid, date)
            wt.pb = _nz(t.get("pb_ksc"), wt.fluid.bubble_point_ksc())
            wt.pi = _nz(t.get("pi"))
            wt.choke_cal = _nz(t.get("choke_cal"))
            wt.ve = _nz(t.get("srp_ve"))
            wt.fallback_rate = _nz(t.get("liquid_m3d"))
            if wt.choke != wt.choke:
                wt.choke = _nz(t.get("choke_64ths"))
        wells[wid] = wt

    # synchronise to the reconciled state of the latest day (state estimation)
    last = res.wells[res.wells["date"] == date]
    targets = {}
    for _, x in last.iterrows():
        frac = x["hours_on"] / 24.0 if x["hours_on"] > 0 else 1.0
        q = (_nz(x.get("dvr_oil"), 0) + _nz(x.get("dvr_water"), 0)) / frac
        if q > 0:
            targets[x["well_id"]] = q
    synchronise(wells, targets)

    # tank farm from the latest gauges
    tg = data["tank_gauges"]
    last_ts = tg["datetime"].max().strftime("%Y-%m-%d")
    g = pick_gauges(tg, last_ts)
    cap = inv = 0.0
    tanks = []
    from ..tanks import _gauge_inputs
    for tid, tk in net.tanks.items():
        safe = tk.safe_fill_mm or float(tk.strapping["level_mm"].max()) * 0.9
        c = tk.volume(safe)
        v = tk.volume(float(g[tid]["level_mm"])) if tid in g else 0.0
        cap += c
        inv += v
        tanks.append(dict(tank=tid, level_mm=float(g[tid]["level_mm"]) if tid in g else np.nan,
                          volume_m3=v, safe_fill_m3=c, fill_frac=v / c if c else np.nan))
    return FacilityTwin(
        name=net.name, wells=wells, separators=list(net.separators),
        compressor_capacity=float(tc["compressor_capacity_sm3d"]),
        lift_available=float(tc["lift_gas_available_sm3d"]),
        fuel_fraction=float(tc["fuel_fraction"]),
        tankfarm=TankFarm(capacity_m3=cap, inventory_m3=inv, tanks=tanks),
        dispatch_rate_m3h=float(tc["dispatch_rate_m3h"]),
        dispatch_start_frac=float(tc["dispatch_start_frac"]),
        dispatch_stop_frac=float(tc["dispatch_stop_frac"]),
        shrinkage={s.id: s.shrinkage_factor for s in net.separators.values()},
        date=date)


def live_state(res, twin: FacilityTwin) -> dict:
    """Level 1: contextualised current state of every object (latest day)."""
    d = twin.date
    w = res.wells[res.wells["date"] == d].set_index("well_id")
    st = twin.steady()
    wm = st["wells"].set_index("well_id")
    wells = []
    for wid, wt in twin.wells.items():
        x = w.loc[wid] if wid in w.index else None
        wells.append(dict(
            well_id=wid, lift=wt.lift, separator=wt.separator, on=wt.on,
            choke=wt.choke, lift_gas=wt.lift_gas if wt.lift == "gas_lift" else np.nan, spm=wt.spm,
            thp=_nz(x.get("thp_ksc")) if x is not None else np.nan,
            dvr_oil=_nz(x.get("dvr_oil")) if x is not None else np.nan,
            dvr_liquid=(_nz(x.get("dvr_oil")) + _nz(x.get("dvr_water"))) if x is not None else np.nan,
            vfm=_nz(x.get("vfm_comb")) if x is not None else np.nan,
            twin_liquid=wm.loc[wid, "L"] if wid in wm.index else np.nan,
            twin_pwf=wm.loc[wid, "pwf"] if wid in wm.index and "pwf" in wm.columns else np.nan,
            tuning=wt.tuning, test_id=wt.test_id, modelled=wt.modelled))
    sep = res.separators[res.separators["date"] == d]
    tf = twin.tankfarm
    return dict(date=d, wells=pd.DataFrame(wells), separators=sep, tanks=pd.DataFrame(tf.tanks),
                tank_fill=tf.inventory_m3 / tf.capacity_m3, steady=st)
