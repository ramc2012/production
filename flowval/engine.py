"""Orchestrates the six validation layers for every production day."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .allocation import allocate_day
from .config import load_config
from .dataquality import run_data_quality
from .findings import FindingLog
from .io import load_inputs
from .network import Network, load_network
from .records import frame_records
from .reconciliation import DVRProblem
from .separators import meter_u95, separator_measurements
from .stats import chi2_ppf, sidak_z
from .tanks import dip_signature_check, tank_day
from .welltests import daily_theoretical, daily_well_findings, evaluate_tests
from .wellperf import ReservoirPressure, calibrate_tests, daily_estimates, performance_table, wellperf_findings


@dataclass
class Results:
    network: Network
    cfg: dict
    dates: List[str]
    findings: pd.DataFrame
    findings_daily: pd.DataFrame
    tests: pd.DataFrame
    wells: pd.DataFrame
    separators: pd.DataFrame
    separator_af: pd.DataFrame
    tanks: pd.DataFrame
    dvr_vars: pd.DataFrame
    dvr_days: pd.DataFrame
    dvr_constraints: pd.DataFrame
    summary: pd.DataFrame
    bias_suspects: pd.DataFrame = field(default_factory=pd.DataFrame)
    well_perf: pd.DataFrame = field(default_factory=pd.DataFrame)

    TABLES = ("findings", "findings_daily", "tests", "wells", "separators", "separator_af", "tanks",
              "dvr_vars", "dvr_days", "dvr_constraints", "summary", "bias_suspects", "well_perf")

    def to_records(self) -> Dict[str, List[dict]]:
        """Every result table as plain dicts (NaN -> None) for database insertion."""
        return {n: frame_records(getattr(self, n)) for n in self.TABLES}


def _routing(net: Network, theo: pd.DataFrame, tests_today: pd.DataFrame) -> Dict[str, Dict[str, float]]:
    """separator -> {well: fraction of the well's daily flow}."""
    out: Dict[str, Dict[str, float]] = {}
    default_ts = net.test_separators[0] if net.test_separators else None
    for _, w in theo.iterrows():
        h, th = w["hours_on"], w.get("test_hours", 0.0) or 0.0
        ft = th / h if h > 0 else 0.0
        if ft > 0:
            tt = tests_today[tests_today["well_id"] == w["well_id"]]
            ts = tt["separator_id"].iloc[0] if len(tt) else default_ts
            if ts:
                out.setdefault(ts, {})[w["well_id"]] = ft
        else:
            ft = 0.0
        if 1 - ft > 1e-9:
            out.setdefault(w["separator"], {})[w["well_id"]] = 1 - ft
    return out


def build_problem(net: Network, date: str, theo: pd.DataFrame, covs: dict, sep_groups: List[dict],
                  tank_groups: List[dict], md_day: pd.DataFrame, cfg: dict, bad: set,
                  tests_today: pd.DataFrame):
    prob = DVRProblem()
    groups: List[dict] = []
    vfm_cons: List[str] = []
    wc_cons: List[tuple] = []
    for _, w in theo.iterrows():
        wid = w["well_id"]
        v = [f"well:{wid}:oil", f"well:{wid}:water", f"well:{wid}:gas"]
        if w.get("oil") == w.get("oil") and wid in covs and not w.get("suspect_offline", False):
            groups.append(dict(tag=f"{wid} (test {w['test_id']})", kind="well_test", vars=v,
                               values=[w["oil"], w["water"], w["gas"]],
                               cov=covs[wid] + np.diag([1e-6, 1e-6, 1e-2]),
                               label=f"{wid} test-based rate"))
        else:
            for x in v:
                prob.var(x)
        q = w.get("vfm_comb")
        if cfg["well_model"]["enabled"] and cfg["well_model"]["use_in_dvr"] and q is not None and q == q:
            frac = w["hours_on"] / 24.0
            groups.append(dict(tag=f"{wid} (well parameters)", kind="well_vfm", vars=[f"well:{wid}:liq"],
                               values=[q * frac], cov=np.array([[max(w["vfm_sd"] * frac, 0.2) ** 2]]),
                               label=f"{wid} well-parameter VFM ({w.get('vfm_methods')})"))
            vfm_cons.append(wid)
        wcs = w.get("wc_sample")
        wm = cfg["well_model"]
        if wm["enabled"] and wm["use_in_dvr"] and wcs is not None and wcs == wcs and w.get("liquid") == w.get("liquid"):
            # wellhead BS&W sample: (1-wc)*water - wc*oil = 0, uncertainty from the sample
            L = w["vfm_comb"] * w["hours_on"] / 24.0 if w.get("vfm_comb") == w.get("vfm_comb") and w.get("vfm_comb") is not None else w["liquid"]
            u = wm["sample_wc_u95_pp"] + wm["sample_aging_pp_per_day"] * (w.get("wc_sample_age") or 0)
            groups.append(dict(tag=f"{wid} (wellhead sample)", kind="well_sample", vars=[f"well:{wid}:wcchk"],
                               values=[0.0], cov=np.array([[max(u / 200.0 * L, 0.05) ** 2]]),
                               label=f"{wid} wellhead BS&W {100 * wcs:.0f}%"))
            wc_cons.append((wid, wcs))
        if w["lift"] == "gas_lift":
            lv = f"well:{wid}:lift"
            lg = w.get("lift_gas_sm3", np.nan)
            mid = net.wells[wid].lift_gas_meter
            if lg == lg and lg > 0 and (date, wid, "lift_gas_sm3") not in bad:
                u = meter_u95(net, cfg, mid, date) if mid else cfg["uncertainty"]["meter_u95_pct"]["orifice"]
                groups.append(dict(tag=mid or f"{wid} lift gas", kind="lift_gas", vars=[lv], values=[lg],
                                   cov=np.array([[max(lg * u / 200, 1.0) ** 2]]), label=f"{wid} lift-gas injection"))
            else:
                prob.var(lv)
    groups += sep_groups + tank_groups

    # gas disposal and lift-gas header meters
    def meter_group(mid, var, kind, label):
        r = md_day[md_day["meter_id"] == mid]
        val = float(r["volume"].sum()) if len(r) and r["volume"].notna().any() else np.nan
        if val == val and (date, mid, "volume") not in bad:
            sd = max(val * meter_u95(net, cfg, mid, date) / 200, 1.0)
            groups.append(dict(tag=mid, kind=kind, vars=[var], values=[val * net.meters[mid].meter_factor],
                               cov=np.array([[sd ** 2]]), label=label))
        else:
            prob.var(var)
    for g in net.gas_disposal:
        meter_group(g["meter"], f"gas:{g['meter']}", "gas_disposal", f"Gas to {g.get('role', 'disposal')}")
    if net.lift_gas_header_meter:
        meter_group(net.lift_gas_header_meter, "liftgas:header", "lift_header", "Lift-gas header meter")
    for g in groups:
        prob.measure(g)

    for wid in vfm_cons:
        prob.constraint(f"{wid} liquid = oil + water (well parameters)",
                        {f"well:{wid}:oil": 1.0, f"well:{wid}:water": 1.0, f"well:{wid}:liq": -1.0})
    for wid, wcs in wc_cons:
        prob.constraint(f"{wid} water cut = wellhead sample",
                        {f"well:{wid}:water": 1.0 - wcs, f"well:{wid}:oil": -wcs, f"well:{wid}:wcchk": -1.0})
    routing = _routing(net, theo, tests_today)
    for sid, wells in routing.items():
        s = net.separators[sid]
        oil = {f"well:{w}:oil": f for w, f in wells.items()}
        oil[f"sep:{sid}:oil"] = -1.0
        prob.constraint(f"{sid} oil balance", oil)
        wat = {f"well:{w}:water": f for w, f in wells.items()}
        wat[f"sep:{sid}:oilleg_water"] = -1.0
        if s.phases == 3:
            wat[f"sep:{sid}:water"] = -1.0
        prob.constraint(f"{sid} water balance", wat)
        gas = {f"well:{w}:gas": f for w, f in wells.items()}
        for w, f in wells.items():
            if net.wells[w].lift == "gas_lift":
                gas[f"well:{w}:lift"] = f
        gas[f"sep:{sid}:gas"] = -1.0
        prob.constraint(f"{sid} gas balance", gas)
    # separators with readings but nothing routed (e.g. test meter running with no test logged)
    for g in sep_groups:
        sid = g["vars"][0].split(":")[1]
        if sid not in routing:
            for v in g["vars"]:
                prob.constraint(f"{sid} no wells routed ({v.split(':')[2]})", {v: 1.0})
    active = list(routing) + [g["vars"][0].split(":")[1] for g in sep_groups if g["vars"][0].split(":")[1] not in routing]
    active = list(dict.fromkeys(active))
    to = {f"sep:{s}:oil": 1.0 for s in active}
    to.update({"tank:dinv_oil": -1.0, "dispatch:oil": -1.0})
    prob.constraint("Tank farm oil balance", to)
    tw = {f"sep:{s}:oilleg_water": 1.0 for s in active}
    tw.update({"tank:dinv_water": -1.0, "dispatch:water": -1.0, "tank:drain_water": -1.0})
    prob.constraint("Tank farm water balance", tw)
    if net.gas_disposal:
        gd = {f"sep:{s}:gas": 1.0 for s in active}
        gd.update({f"gas:{g['meter']}": -1.0 for g in net.gas_disposal})
        prob.constraint("Gas disposal balance", gd)
    if net.lift_gas_header_meter:
        lh = {f"well:{w}:lift": 1.0 for w in theo.loc[theo["lift"] == "gas_lift", "well_id"]}
        lh["liftgas:header"] = -1.0
        prob.constraint("Lift-gas distribution balance", lh)
    return prob, groups, routing



def run(network_path: str | Path, data_dir: str | Path, config_path=None,
        start: Optional[str] = None, end: Optional[str] = None) -> Results:
    net = load_network(network_path)
    cfg = config_path if isinstance(config_path, dict) else load_config(config_path)
    return run_frames(net, load_inputs(data_dir), cfg, start, end)


DAILY_TABLES = {"well_daily": "date", "meter_daily": "date", "separator_daily": "date",
                "tank_drains": "date", "dispatch_daily": "date"}


def run_window(net: Network, data: Dict[str, pd.DataFrame], cfg: dict, day: str, history_days: int = 35,
               test_overrides: Optional[Dict[str, dict]] = None) -> Results:
    """Daily-job mode: validate ``day`` using only the last ``history_days`` of daily data.

    Well tests and reservoir surveys are kept in full (test registry, test age). The result
    covers the whole window (persistence / bias need it); callers persist what they need.
    """
    lo = (pd.Timestamp(day) - pd.Timedelta(days=history_days)).strftime("%Y-%m-%d")
    nxt = (pd.Timestamp(day) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    win = {}
    for name, df in data.items():
        if name in DAILY_TABLES:
            win[name] = df[(df["date"] >= lo) & (df["date"] <= day)].reset_index(drop=True)
        elif name == "tank_gauges":
            d = df["datetime"].dt.strftime("%Y-%m-%d")
            win[name] = df[(d >= lo) & (d <= nxt)].reset_index(drop=True)
        else:
            win[name] = df
    return run_frames(net, win, cfg, lo, day, test_overrides)


def run_frames(net: Network, data: Dict[str, pd.DataFrame], cfg: dict,
               start: Optional[str] = None, end: Optional[str] = None,
               test_overrides: Optional[Dict[str, dict]] = None) -> Results:
    """Validate a period from in-memory inputs (``io.prepare_inputs`` output).

    ``test_overrides``: {test_id: {"status": ACCEPTED|PROVISIONAL|REJECTED, "reason": str}}
    from an engineer; they replace the engine's verdict for those tests.
    """
    gauge_days = set(data["tank_gauges"]["datetime"].dt.strftime("%Y-%m-%d"))
    all_dates = sorted(data["well_daily"]["date"].unique())
    dates = [d for d in all_dates if (start is None or d >= start) and (end is None or d <= end)]
    nxt = {d: (pd.Timestamp(d) + pd.Timedelta(days=1)).strftime("%Y-%m-%d") for d in dates}
    dates = [d for d in dates if d in gauge_days and nxt[d] in gauge_days]
    if not dates:
        raise ValueError("No production days with opening and closing tank gauges")

    log = FindingLog()
    dq_log, bad = run_data_quality(net, data, cfg, dates)
    log.extend(dq_log)
    tests, wt_log = evaluate_tests(net, data, cfg, test_overrides)
    log.extend(wt_log)
    pres = ReservoirPressure(net, data["reservoir_surveys"])
    tests, cal_log = calibrate_tests(net, tests, data["well_tests"], pres, cfg)
    log.extend(cal_log)
    wd_by_well = {w: g for w, g in data["well_daily"].groupby("well_id")}
    tests_by_id = tests.set_index("test_id")

    wd, md, sd = data["well_daily"], data["meter_daily"], data["separator_daily"]
    alpha = cfg["dvr"]["alpha"]

    # ---- pass 1: measurements per day + dip-error signature screen -------------
    pre = {}
    sig_rows = []
    for d in dates:
        sg, ssum, slog = separator_measurements(net, d, md[md["date"] == d], sd[sd["date"] == d], cfg, bad)
        tgp, trows, tlog = tank_day(net, data, d, nxt[d], cfg)
        log.extend(slog)
        log.extend(tlog)
        pre[d] = (sg, ssum, tgp, trows)
        gm = {v: (g["values"][j], g["cov"][j, j]) for g in sg + tgp for j, v in enumerate(g["vars"])}
        if "tank:dinv_oil" in gm and "dispatch:oil" in gm:
            sep_o = [gm[v] for v in gm if v.startswith("sep:") and v.endswith(":oil")]
            res = gm["tank:dinv_oil"][0] + gm["dispatch:oil"][0] - sum(x[0] for x in sep_o)
            var = gm["tank:dinv_oil"][1] + gm["dispatch:oil"][1] + sum(x[1] for x in sep_o)
            sig_rows.append(dict(date=d, res=res, sd_res=np.sqrt(var)))
    tank_rows_all = pd.concat([p[3] for p in pre.values()], ignore_index=True)
    drop_tank, dlog = dip_signature_check(pd.DataFrame(sig_rows), tank_rows_all, net)
    log.extend(dlog)

    # ---- pass 2: theoretical rates, DVR, allocation ------------------------------
    theo_all, sep_all, sepaf_all, tank_all, vars_all, days, cons_all, summ = [], [], [], [], [], [], [], []
    for d in dates:
        wd_day = wd[wd["date"] == d]
        theo, covs = daily_theoretical(net, wd_day, tests, cfg, d, bad)
        if cfg["well_model"]["enabled"] and len(theo):
            extra = []
            raw = wd_day.set_index("well_id")
            for _, tr in theo.iterrows():
                e = {}
                if tr.get("test_id") in tests_by_id.index and tr["well_id"] in raw.index:
                    r = raw.loc[tr["well_id"]].copy()
                    r["well_id"] = tr["well_id"]
                    t = tests_by_id.loc[tr["test_id"]]
                    e = daily_estimates(net, tr.to_dict(), r, t, wd_by_well[tr["well_id"]], pres, cfg, bad)
                    e["inj_p"] = r.get("inj_pressure_ksc") if pd.notna(r.get("inj_pressure_ksc")) else r.get("chp_ksc")
                    e["inj_p_test"] = t.get("inj_p_test")
                    e["spm"] = r.get("spm")
                extra.append(e)
            ex = pd.DataFrame(extra, index=theo.index)
            theo = pd.concat([theo, ex], axis=1)
            if "nodal_dead" in theo:
                theo["suspect_offline"] = theo["suspect_offline"].eq(True) | \
                    theo["nodal_dead"].eq(True)
        sg, ssum, tgp, trows = pre[d]
        if d in drop_tank:
            tgp = [g for g in tgp if g["tag"] != "TANK-GAUGING"]
        prob, groups, routing = build_problem(net, d, theo, covs, sg, tgp, md[md["date"] == d], cfg, bad,
                                              data["well_tests"][data["well_tests"]["test_date"] == d])
        res = prob.solve(alpha=alpha, max_eliminations=cfg["dvr"]["max_eliminations"])
        v = res.variables.copy()
        v.insert(0, "date", d)
        vars_all.append(v)
        c = res.constraints.copy()
        c.insert(0, "date", d)
        cons_all.append(c)
        days.append(dict(date=d, chi2=res.global_stat_initial, dof=res.dof_initial,
                         chi2_crit=res.chi2_crit_initial, chi2_final=res.global_stat, dof_final=res.dof,
                         chi2_crit_final=res.chi2_crit, passed_initial=res.passed_initial, passed_final=res.passed,
                         ratio=res.global_stat_initial / res.chi2_crit_initial if res.chi2_crit_initial == res.chi2_crit_initial else np.nan,
                         eliminated="; ".join(e["tag"] for e in res.eliminated),
                         ambiguous=" | ".join("/".join(a["candidates"]) for a in res.ambiguous),
                         unobservable="; ".join(res.unobservable)))
        _dvr_findings(log, d, res, theo)
        alloc, s, saf, alog = allocate_day(net, d, theo, res.variables, groups, routing, cfg)
        log.extend(alog)
        theo_all.append(theo.merge(alloc.drop(columns=["hours_on", "test_id", "test_status", "test_age_d",
                                                       "vfm_liquid_m3d", "vfm_dev"]),
                                   on=["date", "well_id"], how="left"))
        summ.append(s)
        sep_all.append(ssum)
        sepaf_all.append(saf)
        tank_all.append(trows)

    wells = pd.concat(theo_all, ignore_index=True) if theo_all else pd.DataFrame()
    log.extend(daily_well_findings(wells, cfg))
    log.extend(wellperf_findings(net, wells, tests, cfg))
    vars_df = pd.concat(vars_all, ignore_index=True)
    bias = _persistent(log, vars_df, cfg, pd.DataFrame(days))
    fdaily = log.to_frame()
    fdaily = fdaily[fdaily["date"] >= dates[0]].reset_index(drop=True)   # history tests are context only
    return Results(network=net, cfg=cfg, dates=dates, findings=consolidate(fdaily), findings_daily=fdaily,
                   tests=tests, wells=wells,
                   separators=pd.concat(sep_all, ignore_index=True),
                   separator_af=pd.concat(sepaf_all, ignore_index=True),
                   tanks=pd.concat(tank_all, ignore_index=True), dvr_vars=vars_df,
                   dvr_days=pd.DataFrame(days), dvr_constraints=pd.concat(cons_all, ignore_index=True),
                   summary=pd.DataFrame(summ), bias_suspects=bias,
                   well_perf=performance_table(net, wells, tests))


def _fmt(v):
    return f"{v:,.1f}" if abs(v) < 1e5 else f"{v:,.0f}"


def _dvr_findings(log: FindingLog, d: str, res, theo: pd.DataFrame) -> None:
    rec = dict(zip(res.variables["var"], res.variables["rec"]))
    if not res.passed_initial:
        log.add(d, "reconciliation", "WARN", "installation", "DVR", "global_test",
                f"Global chi-square test failed: {res.global_stat_initial:.1f} > "
                f"{res.chi2_crit_initial:.1f} (dof {res.dof_initial}); "
                "measurements are mutually inconsistent", float(res.global_stat_initial), "chi2(0.95, dof)",
                "See gross-error suspects for this day")
    for e in res.eliminated:
        parts = []
        for v, mv, ev in zip(e["vars"], e["meas"], e["est"]):
            ph = v.split(":")[-1]
            dev = (mv - ev) / ev * 100 if abs(ev) > 1e-9 else np.nan
            parts.append(f"{ph}: measured {_fmt(mv)} vs reconciled {_fmt(ev)} ({dev:+.1f}%)")
        otype = "well" if e["kind"] in ("well_test", "well_vfm", "well_sample") else ("tank" if e["kind"] == "tank" else "meter")
        oid = e["tag"].split(" ")[0] if otype == "well" else e["tag"]
        action = {"well_test": "Well rate inconsistent with separator/tank data: verify status, hours-on, retest",
                  "well_vfm": "Well-parameter estimate inconsistent: check THP/CHP/fluid-level data and calibration",
                  "well_sample": "Wellhead sample inconsistent with the balances: re-sample (representative point?)",
                  "tank": "Re-dip tanks / check strapping, free-water and temperature readings",
                  }.get(e["kind"], "Check meter: zero/span, proving, flow computer configuration")
        sev, lead = "ALARM", "Gross error isolated in"
        if e["kind"] == "well_test" and len(theo):
            tr = theo[theo["well_id"] == oid]
            has_ind = len(tr) and any(c in tr and pd.notna(tr[c].iloc[0]) for c in ("vfm_comb", "wc_sample"))
            if not has_ind:   # nothing independent confirms it: a well without data absorbs imbalances
                sev, lead = "WARN", "Probable (unconfirmed - no independent well-parameter data) gross error in"
                action = "Imbalance attributed to the least-constrained well; confirm with THP/fluid level or a retest"
        log.add(d, "reconciliation", sev, otype, oid, "gross_error",
                f"{lead} {e['label']} (GLR p = {e['glr_p']:.1e}): " + "; ".join(parts),
                float(e["glr_T"]), "p >= alpha/n", action)
    for a in res.ambiguous:
        cands = ", ".join(a["candidates"])
        n = len(a["candidates"])
        cands = ", ".join(c.split(" (test")[0] for c in a["candidates"][:6]) + (f" ... ({n} candidates)" if n > 6 else "")
        sepmap = dict(zip(theo["well_id"], theo["separator"])) if len(theo) else {}
        seps = sorted({sepmap[c.split(" ")[0]] for c in a["candidates"] if c.split(" ")[0] in sepmap})
        obj = "/".join(seps) if seps else "DVR"
        log.add(d, "reconciliation", "WARN", "separator" if seps else "installation", obj, "ambiguous",
                f"Inconsistency cannot be isolated to one source; equally likely (best first): {cands}",
                None, None, "Cross-check the listed meters; retest the listed wells")
    for _, w in theo[theo.get("suspect_offline", pd.Series(False, index=theo.index)).eq(True)].iterrows():
        est = rec.get(f"well:{w['well_id']}:oil", np.nan)
        log.add(d, "reconciliation", "ALARM", "well", w["well_id"], "offline_estimate",
                f"Well excluded from test-based balance (THP indicates shut-in); DVR-estimated oil "
                f"{est:.1f} m3 vs test-based {w['oil']:.1f} m3",
                float(est) if est == est else None, None,
                "Correct DPR hours-on; allocation should use the reconciled value")
    for u in res.unobservable:
        log.add(d, "reconciliation", "WARN", "installation", u, "unobservable",
                f"{u} is unmeasured and cannot be estimated from the balances", None, None,
                "Restore the meter or add redundancy")


def _persistent(log: FindingLog, vars_df: pd.DataFrame, cfg: dict, days_df: pd.DataFrame = None) -> pd.DataFrame:
    """Tags flagged on several days -> systematic bias suspects."""
    v = vars_df[vars_df["tag"].astype(str) != ""].copy()
    v["eliminated"] = v["kind"].astype(str).str.contains("eliminated")
    n_days = v["date"].nunique()
    zc = sidak_z(cfg["dvr"]["alpha"], 30)
    amb_days = set(days_df.loc[days_df["ambiguous"].astype(str) != "", "date"]) if days_df is not None else set()
    # relative deviation of measurement from reconciled/estimated value
    v["dev_pct"] = (v["meas"] - v["rec"]) / v["rec"].where(v["rec"].abs() > 1e-9) * 100
    # statistically significant AND material (>= 1 %) - tiny shifts on precise meters are not actionable
    v["flag"] = v["eliminated"] | ((v["z"] > zc) & ~v["date"].isin(amb_days) & (~v["kind"].isin(["well_test", "well_vfm", "well_sample"]))
                                   & (v["dev_pct"].abs() >= 1.0))
    rows = []
    for (tag, var), g in v.groupby(["tag", "var"]):
        k = int(g["flag"].sum())
        if k == 0:
            continue
        rows.append(dict(tag=tag, var=var, kind=g["kind"].iloc[0].replace(" (eliminated)", ""),
                         days_flagged=k, days_total=len(g), first_flag=g.loc[g["flag"], "date"].min(),
                         mean_dev_pct=g.loc[g["flag"], "dev_pct"].mean(),
                         mean_dev_all_pct=g["dev_pct"].mean(), max_z=g["z"].max()))
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.sort_values(["days_flagged", "max_z"], ascending=False).reset_index(drop=True)
    for _, r in df[df["days_flagged"] >= cfg["dvr"]["persistent_days"]].iterrows():
        otype = "well" if r["kind"] in ("well_test", "well_vfm", "well_sample") else ("tank" if r["kind"] == "tank" else "meter")
        log.add(r["first_flag"], "reconciliation", "ALARM", otype, str(r["tag"]).split(" ")[0],
                "systematic_bias",
                f"{r['var']} flagged on {r['days_flagged']} of {r['days_total']} days since {r['first_flag']}; "
                f"average deviation {r['mean_dev_pct']:+.1f}% on flagged days",
                float(r["mean_dev_pct"]), f"< {cfg['dvr']['persistent_days']} days",
                "Systematic error: calibrate / prove meter, apply correction until fixed")
    return df


CONSOLIDATE = {"retention", "gas_capacity", "af_oil", "af_water", "af_gas", "gross_error", "global_test",
               "ambiguous", "unobservable", "missing", "spike", "no_bsw", "offline_estimate", "thp_high",
               "missing_gauge", "safe_fill", "lift_gas_zero", "no_differential", "status_hours"}


def consolidate(df: pd.DataFrame) -> pd.DataFrame:
    """One register line per (object, check, severity) for repeated daily findings."""
    if df.empty:
        return df.assign(first_date=[], last_date=[], days=[])
    rows = []
    keys = ["layer", "severity", "object_type", "object_id", "check"]
    for _, g in df.groupby(keys, sort=False):
        g = g.sort_values("date")
        if g["check"].iloc[0] in CONSOLIDATE and len(g) > 1:
            r = g.iloc[0].to_dict()
            n = g["date"].nunique()
            r["message"] = f"{r['message']}  [repeated on {n} days, {g['date'].min()} to {g['date'].max()}]"
            r.update(first_date=g["date"].min(), last_date=g["date"].max(), days=n)
            vals = pd.to_numeric(g["value"], errors="coerce")
            r["value"] = float(vals.mean()) if vals.notna().any() else None
            rows.append(r)
        else:
            for _, x in g.iterrows():
                r = x.to_dict()
                r.update(first_date=r["date"], last_date=r["date"], days=1)
                rows.append(r)
    out = pd.DataFrame(rows)
    return out.sort_values(["rank", "first_date", "layer", "object_id"],
                           ascending=[False, True, True, True]).reset_index(drop=True)
