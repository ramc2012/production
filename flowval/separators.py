"""Layer 3 - separator measurements and operating-envelope checks.

* Converts raw oil-leg / water-leg / gas meter totals to standard conditions
  (meter factor, oil-leg BS&W split, shrinkage) with propagated uncertainty.
* Checks liquid retention time against API 12J guidance and gas-section load
  against the Souders-Brown limit (carry-over risk that biases meters).
"""
from __future__ import annotations

import math
from typing import Dict, List, Set, Tuple

import numpy as np
import pandas as pd

from . import fluids as F
from .dataquality import meter_overdue
from .findings import FindingLog
from .network import Network
from .stats import propagate


def meter_u95(net: Network, cfg: dict, meter_id: str, date: str) -> float:
    m = net.meters[meter_id]
    u = m.u95_pct if m.u95_pct is not None else cfg["uncertainty"]["meter_u95_pct"].get(m.type, 2.0)
    if meter_overdue(net, meter_id, date):
        u *= cfg["uncertainty"]["proving_overdue_factor"]
    return u


def _meter_value(md_day: pd.DataFrame, meter_id: str, date: str, bad: Set) -> float:
    if not meter_id or (date, meter_id, "volume") in bad:
        return np.nan
    r = md_day[md_day["meter_id"] == meter_id]
    if r.empty:
        return np.nan
    v = r["volume"].astype(float).sum()
    return v


def api12j_min_retention_min(api: float, temp_c: float, phases: int) -> Tuple[float, float]:
    """(lower, upper) recommended liquid retention time, minutes (API 12J)."""
    if phases == 2:
        if api > 35:
            return 1.0, 1.0
        if api >= 20:
            return 1.0, 2.0
        if api >= 10:
            return 2.0, 4.0
        return 4.0, 4.0
    if api > 35:
        return 3.0, 5.0
    if temp_c >= 37.8:
        return 5.0, 10.0
    if temp_c >= 26.7:
        return 10.0, 20.0
    if temp_c >= 15.6:
        return 20.0, 30.0
    return 30.0, 30.0


def separator_measurements(net: Network, date: str, md_day: pd.DataFrame, sd_day: pd.DataFrame,
                           cfg: dict, bad: Set) -> Tuple[List[dict], pd.DataFrame, FindingLog]:
    """Return DVR measurement groups, a per-separator summary and findings."""
    uc = cfg["uncertainty"]
    sc = cfg["separator"]
    log = FindingLog()
    groups: List[dict] = []
    summary = []
    for s in net.separators.values():
        cond = sd_day[sd_day["separator_id"] == s.id]
        p = float(cond["pressure_ksc"].iloc[0]) if len(cond) and pd.notna(cond["pressure_ksc"].iloc[0]) else np.nan
        t = float(cond["temperature_degc"].iloc[0]) if len(cond) and pd.notna(cond["temperature_degc"].iloc[0]) else np.nan
        bsw = float(cond["oil_leg_bsw_pct"].iloc[0]) if len(cond) and pd.notna(cond["oil_leg_bsw_pct"].iloc[0]) else np.nan

        v_oil = _meter_value(md_day, s.oil_meter, date, bad)
        v_wat = _meter_value(md_day, s.water_meter, date, bad)
        v_gas = _meter_value(md_day, s.gas_meter, date, bad)
        if s.kind == "test" and not (v_oil > 0 or v_gas > 0):
            continue  # test separator idle

        row = dict(date=date, separator=s.id, kind=s.kind, pressure_ksc=p, temperature_degc=t,
                   oil_leg_bsw_pct=bsw, oil_meter_raw=v_oil, water_meter_raw=v_wat, gas_meter_raw=v_gas)

        # ---- oil leg -> stock-tank oil + water carried to tanks ------------
        if s.oil_meter and v_oil == v_oil:
            mf = net.meters[s.oil_meter].meter_factor
            if bsw != bsw:
                bsw_use, u_b = 0.0, 5.0
                log.add(date, "separator", "WARN", "separator", s.id, "no_bsw",
                        "Oil-leg BS&W not reported - assumed 0 % with wide uncertainty", None, None,
                        "Take daily oil-leg sample (centrifuge / Karl Fischer)")
            else:
                bsw_use, u_b = bsw, uc["oil_leg_bsw_u95_pp"]

            def f(x, mf=mf):
                V, b, sf = x
                return [V * mf * (1 - b / 100) * sf, V * mf * b / 100]
            y, cov = propagate(f, [v_oil, bsw_use, s.shrinkage_factor],
                               [v_oil * meter_u95(net, cfg, s.oil_meter, date) / 200, u_b / 2,
                                s.shrinkage_factor * uc["shrinkage_u95_pct"] / 200])
            groups.append(dict(tag=s.oil_meter, kind="separator_meter",
                               vars=[f"sep:{s.id}:oil", f"sep:{s.id}:oilleg_water"],
                               values=list(y), cov=cov, label=f"{s.id} oil-leg meter"))
            row.update(oil_std=y[0], oilleg_water=y[1])
        if s.water_meter and v_wat == v_wat:
            mf = net.meters[s.water_meter].meter_factor
            sd = v_wat * mf * meter_u95(net, cfg, s.water_meter, date) / 200
            groups.append(dict(tag=s.water_meter, kind="separator_meter", vars=[f"sep:{s.id}:water"],
                               values=[v_wat * mf], cov=np.array([[max(sd, 0.05) ** 2]]),
                               label=f"{s.id} water-leg meter"))
            row.update(water_std=v_wat * mf)
        if s.gas_meter and v_gas == v_gas:
            mf = net.meters[s.gas_meter].meter_factor
            sd = v_gas * mf * meter_u95(net, cfg, s.gas_meter, date) / 200
            groups.append(dict(tag=s.gas_meter, kind="separator_meter", vars=[f"sep:{s.id}:gas"],
                               values=[v_gas * mf], cov=np.array([[max(sd, 1.0) ** 2]]),
                               label=f"{s.id} gas meter"))
            row.update(gas_std=v_gas * mf)

        # ---- operating envelope -------------------------------------------
        q_liq_act = np.nansum([v_oil * (net.meters[s.oil_meter].meter_factor if s.oil_meter else 1),
                               v_wat if v_wat == v_wat else 0.0])
        rho_oil = net.oil_rho15
        api = F.api_gravity(rho_oil)
        if s.liquid_volume_m3 and q_liq_act > 0 and s.kind == "production":
            rt = s.liquid_volume_m3 / (q_liq_act / 1440.0)
            lo, hi = api12j_min_retention_min(api, t if t == t else 30.0, s.phases)
            row.update(retention_min=rt, retention_api12j_min=lo)
            if rt < 0.5 * lo:
                log.add(date, "separator", "ALARM", "separator", s.id, "retention",
                        f"Liquid retention {rt:.1f} min << API 12J {lo:g}-{hi:g} min (API {api:.0f})",
                        rt, f">= {lo:g} min", "Oil-water separation poor: BS&W & water-meter bias likely")
            elif rt < lo:
                log.add(date, "separator", "WARN", "separator", s.id, "retention",
                        f"Liquid retention {rt:.1f} min below API 12J {lo:g}-{hi:g} min (API {api:.0f})",
                        rt, f">= {lo:g} min", "Review NLL setpoint / throughput")
        if v_gas == v_gas and v_gas > 0 and p == p and t == t:
            q_act = F.actual_gas_volume(v_gas, p, t, net.gas_sg,
                                        cfg["standard_conditions"]["temperature_degc"],
                                        cfg["standard_conditions"]["pressure_kpa"]) / 86400.0
            area = math.pi * s.diameter_m ** 2 / 4
            if s.orientation == "horizontal":
                area *= sc["gas_area_fraction_horizontal"]
                k = sc["k_horizontal_10ft_ms"] * (s.length_m / 3.048) ** 0.56
            else:
                k = sc["k_vertical_ms"]
            rho_g = F.gas_density(p, t, net.gas_sg)
            vmax = k * math.sqrt(max(rho_oil - rho_g, 1.0) / rho_g)
            util = (q_act / area) / vmax
            row.update(gas_velocity_ms=q_act / area, souders_brown_vmax_ms=vmax, gas_load=util)
            if util > sc["gas_capacity_alarm"]:
                log.add(date, "separator", "ALARM", "separator", s.id, "gas_capacity",
                        f"Gas load {util:.0%} of Souders-Brown limit - liquid carry-over likely",
                        util, f"<= {sc['gas_capacity_alarm']:.0%}", "Gas meter over-reads; reduce throughput")
            elif util > sc["gas_capacity_warn"]:
                log.add(date, "separator", "WARN", "separator", s.id, "gas_capacity",
                        f"Gas load {util:.0%} of Souders-Brown limit", util,
                        f"<= {sc['gas_capacity_warn']:.0%}")
        summary.append(row)
    return groups, pd.DataFrame(summary), log
