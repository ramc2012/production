"""Level 3 - predictive twin.

* Well decline forecasts (Arps hyperbolic, b in [0, 1]) fitted to the well's
  accepted tests and reconciled daily rates since its last regime change
  (bean change, breakthrough, pump leak, valve change...), with P10/P90 bands.
* Water-cut trend per well.
* Installation oil forecast (sum of wells, variance-combined bands).
* 72-hour look-ahead scenarios from the facility twin: normal operation,
  CTF dispatch outage, compressor trip.
* Meter drift projection for meters the reconciliation flagged.
* Well-test due list.
"""
from __future__ import annotations

import math
from typing import Dict, List

import numpy as np
import pandas as pd

from ..stats import norm_ppf

REGIME_CHECKS = {"choke_change", "vfm_deviation", "test_change", "wc_sample_change", "fluid_level_rise",
                 "inj_pressure_shift", "pi_change"}
MIN_WINDOW_DAYS, MIN_WINDOW_POINTS = 21, 10


def _arps(t, qi, D, b):
    if b < 1e-6:
        return qi * np.exp(-D * t)
    return qi / (1 + b * D * t) ** (1 / b)


def fit_arps(t: np.ndarray, q: np.ndarray):
    """Least squares over b grid; returns qi, D (1/day), b, sigma_log."""
    t, q = np.asarray(t, float), np.asarray(q, float)
    m = q > 0
    t, q = t[m], q[m]
    if len(q) < 3:
        return (float(np.mean(q)) if len(q) else 0.0), 0.0, 0.0, 0.25
    best = None
    for b in np.linspace(0, 1, 11):
        if b == 0:
            A = np.vstack([np.ones_like(t), -t]).T
            coef, *_ = np.linalg.lstsq(A, np.log(q), rcond=None)
            qi, D = math.exp(coef[0]), max(coef[1], 0.0)
        else:
            A = np.vstack([np.ones_like(t), t]).T
            coef, *_ = np.linalg.lstsq(A, q ** -b, rcond=None)
            a0, a1 = coef
            if a0 <= 0:
                continue
            qi = a0 ** (-1 / b)
            D = max(a1 / (a0 * b), 0.0)
        pred = _arps(t, qi, D, b)
        sse = float(np.sum((np.log(q) - np.log(np.maximum(pred, 1e-6))) ** 2))
        if best is None or sse < best[0] - 1e-9:
            best = (sse, qi, D, b)
    sse, qi, D, b = best
    sigma = math.sqrt(sse / max(len(q) - 2, 1))
    return qi, D, b, max(sigma, 0.02)


def _wc_slope(df: pd.DataFrame, end: pd.Timestamp) -> float:
    d = df.dropna(subset=["wc"])
    if len(d) < 3:
        return 0.0
    x = (pd.to_datetime(d["date"]) - end).dt.days.values.astype(float)
    if x.max() - x.min() < MIN_WINDOW_DAYS:
        return 0.0
    return float(np.polyfit(x, d["wc"].values, 1)[0])


def well_forecasts(res, days: int = 90) -> tuple:
    """Return (summary per well, daily forecast table)."""
    w = res.wells.copy()
    w["rate_oil"] = w["dvr_oil"] / (w["hours_on"] / 24.0)
    t = res.tests[res.tests["status"].isin(["ACCEPTED", "ACCEPTED_W", "PROVISIONAL"])]
    f = res.findings_daily
    end = pd.Timestamp(res.dates[-1])
    z90 = norm_ppf(0.9)
    rows, fc = [], []
    for wid in res.network.wells:
        hist = pd.concat([
            t[t["well_id"] == wid][["test_date", "oil_m3d", "wc"]].rename(columns={"test_date": "date", "oil_m3d": "q"}),
            w[w["well_id"] == wid][["date", "rate_oil", "wc"]].rename(columns={"rate_oil": "q"})])
        hist = hist.dropna(subset=["q"])
        if hist.empty:
            continue
        hist = hist[hist["q"] > 0.2 * hist["q"].median()].sort_values("date")   # drop shut-in / dead days
        reg = f[(f["object_id"] == wid) & f["check"].isin(REGIME_CHECKS)]
        start = None
        if len(reg):   # start of the latest cluster of regime-change findings (within 14 days)
            ds = sorted(pd.to_datetime(reg["date"].unique()))
            st_ = ds[-1]
            for d_ in reversed(ds[:-1]):
                if (st_ - d_).days <= 14:
                    st_ = d_
                else:
                    break
            start = st_.strftime("%Y-%m-%d")
        pre = hist[hist["date"] < start] if start else hist
        post = hist[hist["date"] >= start] if start else hist.iloc[0:0]
        span = (pd.to_datetime(post["date"]).max() - pd.to_datetime(post["date"]).min()).days if len(post) else 0

        def days_from_end(df):
            return (pd.to_datetime(df["date"]) - end).dt.days.values.astype(float)

        if start and len(post) >= MIN_WINDOW_POINTS and span >= MIN_WINDOW_DAYS:
            win, mode = post, "refit after regime change"
        elif start and len(post) >= 2 and len(pre) >= 4:
            win, mode = post, "level reset, long-term decline"
        else:
            win, mode = hist, "full history"

        if mode == "level reset, long-term decline":
            tp = days_from_end(pre)
            _, D, b, sig = fit_arps(tp - tp.min(), pre["q"].values)
            qi, t0 = float(post["q"].tail(5).median()), 0.0
            if len(post) > 2:
                sig = max(sig, float(np.std(np.log(post["q"].values))))
            sig = max(sig, 0.05)
            slope = 0.0
        else:
            tt = days_from_end(win)
            qi, D, b, sig = fit_arps(tt - tt.min(), win["q"].values)
            t0 = -tt.min()
            slope = _wc_slope(win, end)
        q_now = float(_arps(np.array([t0]), qi, D, b)[0])
        n = len(win)
        for k in range(1, days + 1):
            q = float(_arps(np.array([t0 + k]), qi, D, b)[0])
            s = sig * math.sqrt(1 + k / max(n, 1))
            fc.append(dict(well_id=wid, day=k, date=(end + pd.Timedelta(days=k)).strftime("%Y-%m-%d"),
                           p50=q, p10=q * math.exp(-z90 * s), p90=q * math.exp(z90 * s)))
        decl = 100 * (1 - float(_arps(np.array([t0 + 365]), qi, D, b)[0]) / q_now) if q_now > 0 else np.nan
        rows.append(dict(well_id=wid, method=mode, window_start=win["date"].min(), points=n, regime_change=start,
                         b=b, decline_pct_per_year=decl, sigma_log=sig, oil_now=q_now,
                         oil_30d=float(_arps(np.array([t0 + 30]), qi, D, b)[0]),
                         oil_90d=float(_arps(np.array([t0 + days]), qi, D, b)[0]),
                         wc_now=float(win["wc"].dropna().iloc[-1]) if win["wc"].notna().any() else np.nan,
                         wc_slope_pp_per_30d=100 * 30 * slope))
    return pd.DataFrame(rows), pd.DataFrame(fc)


def installation_forecast(fc: pd.DataFrame) -> pd.DataFrame:
    if fc.empty:
        return fc
    z90 = norm_ppf(0.9)
    g = fc.copy()
    g["sd"] = (np.log(g["p90"]) - np.log(g["p50"])) / z90 * g["p50"]   # approx absolute sd
    out = g.groupby(["day", "date"]).agg(p50=("p50", "sum"), var=("sd", lambda s: float((s ** 2).sum()))).reset_index()
    out["p10"] = out["p50"] - z90 * np.sqrt(out["var"])
    out["p90"] = out["p50"] + z90 * np.sqrt(out["var"])
    return out.drop(columns="var")


def lookahead_scenarios(twin, hours: int = 72) -> Dict[str, pd.DataFrame]:
    return {
        "Normal operation": twin.lookahead(hours),
        "CTF dispatch outage 0-36 h": twin.lookahead(hours, dispatch_outage=(0, 36)),
        "Compressor trip 6-12 h": twin.lookahead(hours, compressor_trip=(6, 12)),
    }


def lookahead_summary(scen: Dict[str, pd.DataFrame], twin) -> pd.DataFrame:
    rows = []
    for name, df in scen.items():
        over = df[df["fill_frac"] >= 1.0]
        flare_m3 = float((df["flare_sm3d"] / 24.0).sum())
        rows.append(dict(scenario=name, max_fill=df["fill_frac"].max(), min_fill=df["fill_frac"].min(),
                         hours_to_safe_fill=float(over["hour"].min()) if len(over) else np.nan,
                         flare_sm3=flare_m3))
    # time to safe fill with no dispatch at all
    st = twin.steady()
    inflow = (st["oil"] + st["water"] * 0.05) / 24.0
    free = twin.tankfarm.capacity_m3 - twin.tankfarm.inventory_m3
    rows.append(dict(scenario="Ullage with no dispatch", max_fill=np.nan, min_fill=np.nan,
                     hours_to_safe_fill=free / inflow if inflow > 0 else np.nan, flare_sm3=np.nan))
    return pd.DataFrame(rows)


def meter_drift(res, horizon_days: int = 30, tolerance_pct: float = 2.0) -> pd.DataFrame:
    b = res.bias_suspects
    if b is None or b.empty:
        return pd.DataFrame()
    v = res.dvr_vars
    rows = []
    for _, r in b[b["kind"].isin(["separator_meter", "gas_disposal", "dispatch", "lift_header", "lift_gas"])].iterrows():
        s = v[(v["tag"] == r["tag"]) & (v["var"] == r["var"])].copy()
        s["dev"] = (s["meas"] - s["rec"]) / s["rec"] * 100
        s = s[s["date"] >= r["first_flag"]].dropna(subset=["dev"])
        if len(s) < 3:
            continue
        x = (pd.to_datetime(s["date"]) - pd.to_datetime(s["date"]).min()).dt.days.values.astype(float)
        slope, icpt = np.polyfit(x, s["dev"].values, 1)
        now = icpt + slope * x[-1]
        proj = now + slope * horizon_days
        rows.append(dict(tag=r["tag"], stream=r["var"], first_flag=r["first_flag"], bias_now_pct=now,
                         drift_pct_per_30d=slope * 30, projected_pct=proj, tolerance_pct=tolerance_pct,
                         status="out of tolerance" if abs(now) > tolerance_pct else "within tolerance"))
    return pd.DataFrame(rows)


def tests_due(res, twin) -> pd.DataFrame:
    cfg = res.cfg["well_test"]
    end = pd.Timestamp(res.dates[-1])
    t = res.tests[res.tests["status"].isin(["ACCEPTED", "ACCEPTED_W", "PROVISIONAL"])]
    rows = []
    for wid in res.network.wells:
        x = t[t["well_id"] == wid].sort_values("test_date")
        last = x.iloc[-1] if len(x) else None
        age = (end - pd.Timestamp(last["test_date"])).days if last is not None else 999
        due = pd.Timestamp(last["test_date"]) + pd.Timedelta(days=cfg["age_warn_days"]) if last is not None else end
        rows.append(dict(well_id=wid, last_test=last["test_date"] if last is not None else None,
                         status=last["status"] if last is not None else "NONE", age_days=age,
                         due_date=due.strftime("%Y-%m-%d"), overdue=due <= end))
    return pd.DataFrame(rows)
