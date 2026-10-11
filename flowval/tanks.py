"""Layer 4 - tank gauging to Net Standard Volume and daily tank production.

Calculation chain per gauge (API MPMS Ch. 12.1.1 / Ch. 11.1):
    TOV  = strapping(level)
    FW   = strapping(free-water level)
    GOV  = (TOV - FW) * CTSh
    GSV  = GOV * CTL(rho15, T_liquid)
    NSV  = GSV * (1 - S&W/100)
Daily tank production (oil) = sum over tanks (NSV_close - NSV_open) + dispatch NSV.
Water inventory = free water + suspended S&W.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from . import fluids as F
from .findings import FindingLog
from .network import Network, Tank
from .stats import propagate


def gauge_volumes(tank: Tank, level_mm, fw_mm, t_liq, t_amb, rho_obs, rho_t, bsw_pct) -> Dict[str, float]:
    tov = tank.volume(level_mm)
    fw = tank.volume(fw_mm) if fw_mm and fw_mm > 0 else 0.0
    cs = F.ctsh(t_liq, t_amb, tank.insulated, tank.shell_alpha_per_c, tank.calibration_temp_c)
    gov = (tov - fw) * cs
    rho15 = F.rho15_from_observed(rho_obs, rho_t)
    ctl = F.ctl_54a(rho15, t_liq)
    gsv = gov * ctl
    nsv = gsv * (1 - bsw_pct / 100.0)
    return dict(TOV=tov, FW=fw, CTSh=cs, GOV=gov, rho15=rho15, CTL=ctl, GSV=gsv, NSV=nsv,
                water=fw * cs + gsv - nsv)


def _gauge_inputs(net: Network, g: pd.Series) -> List[float]:
    def v(k, d):
        x = g.get(k)
        return float(x) if x is not None and pd.notna(x) else d
    t = v("liquid_temp_degc", 30.0)
    return [v("level_mm", 0.0), v("free_water_mm", 0.0), t, v("ambient_temp_degc", np.nan),
            v("density_obs_kgm3", net.oil_rho15), v("density_obs_temp_degc", t), v("bsw_pct", 0.0)]


def pick_gauges(tg: pd.DataFrame, date: str) -> Dict[str, pd.Series]:
    day = tg[tg["datetime"].dt.strftime("%Y-%m-%d") == date].sort_values("datetime")
    return {tid: g.iloc[0] for tid, g in day.groupby("tank_id")}


def tank_day(net: Network, data: Dict[str, pd.DataFrame], date: str, next_date: str,
             cfg: dict) -> Tuple[List[dict], pd.DataFrame, FindingLog]:
    """DVR measurement groups for the tank farm on ``date`` + per-tank table."""
    tc = cfg["tank"]
    log = FindingLog()
    tg = data["tank_gauges"]
    g0, g1 = pick_gauges(tg, date), pick_gauges(tg, next_date)
    groups: List[dict] = []
    rows = []
    tanks = [t for t in net.tanks.values() if t.id in g0 and t.id in g1]
    for t in net.tanks.values():
        if t not in tanks:
            log.add(date, "tank", "ALARM", "tank", t.id, "missing_gauge",
                    "Opening or closing gauge missing - tank excluded from balance", None, None,
                    "Record 06:00 dip")
    if not tanks:
        return groups, pd.DataFrame(), log

    x, s, layout = [], [], []
    sig = [tc["level_sigma_mm"], tc["free_water_sigma_mm"], tc["temperature_sigma_c"], 0.0,
           tc["density_sigma_kgm3"], 0.0, tc["bsw_sigma_pp"]]
    for t in tanks:
        for g in (g0[t.id], g1[t.id]):
            xi = _gauge_inputs(net, g)
            x += xi
            s += [sig[i] if not (i == 3 and np.isnan(xi[3])) else 0.0 for i in range(7)]
        layout.append(t)
        a = gauge_volumes(t, *_gauge_inputs(net, g0[t.id]))
        b = gauge_volumes(t, *_gauge_inputs(net, g1[t.id]))
        rows.append(dict(date=date, tank=t.id, open_level_mm=g0[t.id]["level_mm"],
                         close_level_mm=g1[t.id]["level_mm"], open_NSV=a["NSV"], close_NSV=b["NSV"],
                         dNSV=b["NSV"] - a["NSV"], dWater=b["water"] - a["water"],
                         CTL_close=b["CTL"], CTSh_close=b["CTSh"], rho15_close=b["rho15"]))
        if t.safe_fill_mm and g1[t.id]["level_mm"] > t.safe_fill_mm:
            log.add(next_date, "tank", "WARN", "tank", t.id, "safe_fill",
                    f"Level {g1[t.id]['level_mm']:.0f} mm above safe fill {t.safe_fill_mm:.0f} mm",
                    float(g1[t.id]["level_mm"]), f"<= {t.safe_fill_mm:.0f}")

    def inv(xx):
        d_oil = d_wat = 0.0
        for k, t in enumerate(layout):
            a = gauge_volumes(t, *xx[k * 14: k * 14 + 7])
            b = gauge_volumes(t, *xx[k * 14 + 7: k * 14 + 14])
            d_oil += b["NSV"] - a["NSV"]
            d_wat += b["water"] - a["water"]
        return [d_oil, d_wat]
    x_arr = np.array(x, dtype=float)
    # ambient temp may be NaN for insulated tanks; replace by liquid temp for numerics
    for k in range(len(layout) * 2):
        if np.isnan(x_arr[k * 7 + 3]):
            x_arr[k * 7 + 3] = x_arr[k * 7 + 2]
    y, cov = propagate(inv, x_arr, s)
    groups.append(dict(tag="TANK-GAUGING", kind="tank", vars=["tank:dinv_oil", "tank:dinv_water"],
                       values=list(y), cov=cov + np.diag([1e-6, 1e-6]), label="Tank inventory change (dips)"))

    # ---- dispatch -----------------------------------------------------------
    disp = data["dispatch_daily"]
    disp = disp[disp["date"] == date]
    if net.dispatch_meter:
        if disp.empty or disp["gross_m3"].isna().all():
            log.add(date, "tank", "ALARM", "meter", net.dispatch_meter, "missing",
                    "Dispatch meter reading missing", None, None, "Obtain LACT/dispatch ticket")
        else:
            r = disp.iloc[0]
            mid = net.dispatch_meter
            from .separators import meter_u95
            mf = net.meters[mid].meter_factor
            T = float(r["temp_degc"]) if pd.notna(r["temp_degc"]) else 30.0
            rho = float(r["density15_kgm3"]) if pd.notna(r["density15_kgm3"]) else net.oil_rho15
            bsw = float(r["bsw_pct"]) if pd.notna(r["bsw_pct"]) else 0.0

            def fd(xx):
                V, TT, rr, bb = xx
                gsv = V * mf * F.ctl_54a(rr, TT)
                return [gsv * (1 - bb / 100), gsv * bb / 100]
            yd, cd = propagate(fd, [float(r["gross_m3"]), T, rho, bsw],
                               [float(r["gross_m3"]) * meter_u95(net, cfg, mid, date) / 200,
                                tc["temperature_sigma_c"], tc["density_sigma_kgm3"],
                                cfg["uncertainty"]["dispatch_bsw_u95_pp"] / 2])
            groups.append(dict(tag=mid, kind="dispatch", vars=["dispatch:oil", "dispatch:water"],
                               values=list(yd), cov=cd + np.diag([1e-6, 1e-6]), label="Dispatch meter"))

    # ---- water drained ----------------------------------------------------
    dr = data["tank_drains"]
    dr = dr[dr["date"] == date]
    vol = float(dr["water_drained_m3"].sum()) if len(dr) else 0.0
    sd = max(vol * tc["drain_u95_pct"] / 200, 0.2)
    groups.append(dict(tag="TANK-DRAINS", kind="tank", vars=["tank:drain_water"], values=[vol],
                       cov=np.array([[sd ** 2]]), label="Tank water drains (dip estimate)"))
    return groups, pd.DataFrame(rows), log


def dip_signature_check(series: pd.DataFrame, tank_rows: pd.DataFrame, net: Network,
                        k_sigma: float = 3.0, cancel_ratio: float = 0.35):
    """Detect single-gauge errors from their +x / -x signature on consecutive days.

    ``series``: date, res (tank production - separator oil, m3), sd_res.
    A wrong dip at the boundary between day d-1 and day d inflates one day's
    tank production and deflates the next by the same amount. Returns
    (set of dates whose tank-inventory measurement should be dropped, findings).
    """
    log = FindingLog()
    drop = set()
    s = series.sort_values("date").reset_index(drop=True)
    if len(s) < 3:
        return drop, log
    r = s["res"] - s["res"].median()
    for i in range(1, len(s)):
        a, b = r.iloc[i - 1], r.iloc[i]
        sa, sb = s["sd_res"].iloc[i - 1], s["sd_res"].iloc[i]
        sig = (abs(a - b) / 2.0) / (np.hypot(sa, sb) / 2.0)
        if sig > k_sigma and abs(a) > 1.5 * sa and abs(b) > 1.5 * sb and a * b < 0 and \
                abs(a + b) < cancel_ratio * max(abs(a), abs(b)):
            d0, d1 = s["date"].iloc[i - 1], s["date"].iloc[i]
            err = (a - b) / 2.0          # m3 the boundary gauge is too high (+) / low (-)
            # which tank? largest second difference of its dNSV at the boundary
            best, score = None, 0.0
            for tid, g in tank_rows.groupby("tank"):
                g = g.set_index("date")["dNSV"]
                if d0 in g.index and d1 in g.index:
                    sc = (g[d0] - g[d1]) * np.sign(err)
                    if sc > score:
                        best, score = tid, sc
            mm = err / net.tanks[best].m3_per_mm(4000) if best else np.nan
            log.add(d1, "tank", "ALARM", "tank", best or "tank farm", "dip_error",
                    f"Gauge at {d1} 06:00 inconsistent: tank production {a:+.1f} m3 on {d0} and "
                    f"{b:+.1f} m3 on {d1} vs separators - {best or 'a tank'} dip likely "
                    f"{'high' if err > 0 else 'low'} by ~{abs(err):.1f} m3 ({abs(mm):.0f} mm)",
                    float(err), f"±{k_sigma} sigma", "Re-dip / check tape & bob; tank inventory excluded from DVR for both days")
            drop |= {d0, d1}
    return drop, log
