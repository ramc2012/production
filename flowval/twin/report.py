"""Digital-twin dashboard: one tab per capability level (DNV-RP-A204 0-5)."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from ..report_html import _badge, _e, _n, _table
from .levels import LEVELS

STATUS_SEV = {"achieved": "OK", "partial": "WARN", "not achieved": "ALARM",
              "met": "OK", "missing": "ALARM"}


def _pts(xs, ys):
    return [[float(x), None if (y is None or (isinstance(y, float) and np.isnan(y))) else round(float(y), 4)]
            for x, y in zip(xs, ys)]


def build_twin_html(ctx: dict, validation_file: str = "") -> str:
    res, twin, live, l3, l4, l5 = ctx["res"], ctx["twin"], ctx["live"], ctx["l3"], ctx["l4"], ctx["l5"]
    levels, assessment = ctx["levels"], ctx["assessment"]
    cfg = res.cfg
    date = twin.date

    # ---- overview ladder -------------------------------------------------------
    ladder = []
    for _, r in levels.iterrows():
        sev = STATUS_SEV.get(r["status"], "INFO")
        ladder.append(
            f'<button class="rung" data-go="L{r["level"]}"><div class="rl">L{r["level"]}</div>'
            f'<div class="rn">{_e(r["name"])}</div><div class="rd">{_e(r["description"])}</div>'
            f'<div class="rs">{_badge(sev)} <span>{_e(r["status"])}</span> · {r["met"]}/{r["total"]} criteria</div>'
            f'<div class="ra">{_e(r["analogue"])}</div></button>')
    a2 = assessment.copy()
    a2["sev"] = a2["status"].map(lambda s: STATUS_SEV.get(s, "INFO"))
    assess_html = _table(a2, [("sev", "", "sev"), ("level", "Level", "n0"), ("criterion", "Criterion", "w"),
                              ("status", "Status", "t"), ("evidence", "Evidence", "w"), ("gap", "Gap to close", "w")])

    # ---- L0 ------------------------------------------------------------------------
    net = res.network
    inv = pd.DataFrame([
        dict(item="Wells", count=len(net.wells), detail=", ".join(
            f"{sum(1 for w in net.wells.values() if w.lift == k)} {k.replace('_', '-')}"
            for k in ("self_flow", "gas_lift", "srp", "esp") if any(w.lift == k for w in net.wells.values()))),
        dict(item="Separators", count=len(net.separators), detail=", ".join(net.separators)),
        dict(item="Tanks", count=len(net.tanks), detail=f"{twin.tankfarm.capacity_m3:,.0f} m³ to safe fill"),
        dict(item="Meters", count=len(net.meters), detail="oil, water, gas, dispatch, lift gas, flare, fuel"),
        dict(item="Compressor", count=1, detail=f"{twin.compressor_capacity:,.0f} Sm³/d capacity"),
        dict(item="Lift-gas supply", count=1, detail=f"{twin.lift_available:,.0f} Sm³/d available"),
    ])
    l0_html = _table(inv, [("item", "Object", "t"), ("count", "Count", "n0"), ("detail", "Detail", "w")])
    wm = pd.DataFrame([dict(well_id=w.id, lift=w.lift, model="physics" if w.modelled else "test rate only",
                            pr=w.pr, pb=w.pb, pi=w.pi, choke_cal=w.choke_cal, ve=w.ve, tuning=w.tuning,
                            calibrated_on=w.test_id) for w in twin.wells.values()])
    l0_models = _table(wm, [("well_id", "Well", "t"), ("lift", "Lift", "t"), ("model", "Model", "t"),
                            ("pr", "Pr ksc", "n1"), ("pb", "Pb ksc", "n0"), ("pi", "PI m³/d/ksc", "n2"),
                            ("choke_cal", "Choke factor", "n2"), ("ve", "SRP VE", "n2"),
                            ("calibrated_on", "Calibrated on test", "t")])

    # ---- L1 ------------------------------------------------------------------------
    lw = live["wells"].copy()
    lw["state"] = np.where(lw["on"], "on", "off")
    lw["sev"] = lw["tuning"].map(lambda t: "OK" if 0.7 <= t <= 1.3 else "WARN")
    l1_wells = _table(lw, [("sev", "", "sev"), ("well_id", "Well", "t"), ("lift", "Lift", "t"),
                           ("separator", "Sep.", "t"), ("state", "State", "t"), ("thp", "THP ksc", "n1"),
                           ("choke", "Choke /64", "n0"), ("lift_gas", "Lift gas Sm³/d", "n0"), ("spm", "SPM", "n1"),
                           ("dvr_liquid", "Reconciled liquid m³/d", "n1"), ("vfm", "Well-parameter estimate", "n1"),
                           ("twin_liquid", "Twin liquid m³/d", "n1"), ("twin_pwf", "Twin Pwf ksc", "n1"),
                           ("tuning", "Tuning factor", "n2")])
    st = live["steady"]
    tk = live["tanks"].copy()
    l1_tanks = _table(tk, [("tank", "Tank", "t"), ("level_mm", "Level mm", "n0"), ("volume_m3", "Gross m³", "n1"),
                           ("safe_fill_m3", "Safe fill m³", "n1"), ("fill_frac", "Fill %", "pct")])
    l1_tiles = [
        ("Oil (twin, now)", f"{_n(st['oil'], 1)} m³/d", "synchronised to reconciled rates"),
        ("Water", f"{_n(st['water'], 1)} m³/d", ""),
        ("Gas to compressor", f"{_n(st['gas_total'] / 1000, 1)} kSm³/d",
         f"load {_n(100 * st['compressor_load'], 0)} % of {twin.compressor_capacity / 1000:.0f} kSm³/d"),
        ("Lift gas", f"{_n(st['lift'] / 1000, 1)} kSm³/d", f"of {twin.lift_available / 1000:.0f} kSm³/d available"),
        ("Tank farm", f"{_n(100 * live['tank_fill'], 0)} %", f"{twin.tankfarm.inventory_m3:,.0f} of {twin.tankfarm.capacity_m3:,.0f} m³"),
    ]

    # ---- L2 ------------------------------------------------------------------------
    f = res.findings
    l2_counts = f.groupby(["layer", "severity"]).size().unstack(fill_value=0).reset_index()
    for c in ("ALARM", "WARN"):
        if c not in l2_counts:
            l2_counts[c] = 0
    l2_html = _table(l2_counts, [("layer", "Layer", "t"), ("ALARM", "Alarms", "n0"), ("WARN", "Warnings", "n0")])
    top = f[f["severity"] == "ALARM"].head(12)
    l2_top = _table(top, [("severity", "", "sev"), ("first_date", "From", "t"), ("object_id", "Object", "t"),
                          ("check", "Check", "t"), ("message", "Finding", "w")], "reg")

    # ---- L3 ------------------------------------------------------------------------
    hist = res.summary[["date", "dvr_oil"]].tail(30)
    inst = l3["installation"]
    xs_lab = list(hist["date"]) + list(inst["date"])
    nh = len(hist)
    fc_chart = dict(
        labels=xs_lab, xcat=True, yfmt=0, ylabel="m³/d",
        series=[dict(name="Reconciled oil (history)", color="--s1", pts=_pts(range(nh), hist["dvr_oil"])),
                dict(name="Forecast P50", color="--s2", dash=True,
                     pts=_pts(range(nh - 1, nh + len(inst)),
                              [hist["dvr_oil"].iloc[-1]] + list(inst["p50"])),
                     band=[_pts(range(nh, nh + len(inst)), inst["p10"]), _pts(range(nh, nh + len(inst)), inst["p90"])])])
    sc = l3["scenarios"]
    cols = ["--s1", "--s2", "--s3"]
    la_chart = dict(xcat=False, xlabel="hours ahead", yfmt=0, ylabel="% of safe fill",
                    series=[dict(name=k, color=cols[i % 3], pts=_pts(v["hour"], 100 * v["fill_frac"]))
                            for i, (k, v) in enumerate((k, v) for k, v in sc.items() if "Compressor" not in k)],
                    refs=[dict(y=100, label="safe fill"),
                          dict(y=100 * cfg["twin"]["dispatch_start_frac"], label="dispatch start")])
    ws = l3["wells"]
    l3_wells = _table(ws, [("well_id", "Well", "t"), ("method", "Fit", "t"), ("regime_change", "Since", "t"),
                           ("points", "Points", "n0"), ("decline_pct_per_year", "Decline %/yr", "n1"),
                           ("oil_now", "Oil now m³/d", "n2"), ("oil_30d", "+30 d", "n2"), ("oil_90d", "+90 d", "n2"),
                           ("wc_now", "WC now", "pct"), ("wc_slope_pp_per_30d", "WC trend pp/30 d", "n1")])
    l3_la = _table(l3["lookahead"], [("scenario", "Scenario", "t"), ("max_fill", "Max fill", "pct"),
                                     ("min_fill", "Min fill", "pct"), ("hours_to_safe_fill", "Hours to safe fill", "n1"),
                                     ("flare_sm3", "Flared Sm³", "n0")])
    l3_drift = _table(l3["drift"], [("tag", "Meter", "t"), ("stream", "Stream", "t"), ("first_flag", "Since", "t"),
                                    ("bias_now_pct", "Bias now %", "n1"), ("drift_pct_per_30d", "Drift %/30 d", "n1"),
                                    ("projected_pct", "In 30 d %", "n1"), ("status", "Status", "t")]) \
        if len(l3["drift"]) else '<p class="muted">No drifting meters.</p>'
    due = l3["due"].copy()
    due["sev"] = np.where(due["overdue"], "ALARM", "OK")
    l3_due = _table(due, [("sev", "", "sev"), ("well_id", "Well", "t"), ("last_test", "Last test", "t"),
                          ("status", "Status", "t"), ("age_days", "Age d", "n0"), ("due_date", "Due", "t")])
    inst_end = inst.iloc[-1] if len(inst) else None

    # ---- L4 ------------------------------------------------------------------------
    cv = l4["curves"]
    gl = l4["gl"]
    gcols = ["--s1", "--s2", "--s3", "--s4"]
    gl_chart = dict(xcat=False, xlabel="lift gas Sm³/d", yfmt=1, ylabel="oil m³/d", series=[], markers=[])
    for i, wid in enumerate(cv["well_id"].unique() if len(cv) else []):
        c = cv[cv["well_id"] == wid].sort_values("lift_gas")
        gl_chart["series"].append(dict(name=wid, color=gcols[i % 4], pts=_pts(c["lift_gas"], c["oil"])))
        g = gl[gl["well_id"] == wid]
        if len(g):
            g = g.iloc[0]
            gl_chart["markers"].append(dict(color=gcols[i % 4], x=float(g["lift_now"]), y=float(g["oil_now"]), kind="now"))
            gl_chart["markers"].append(dict(color=gcols[i % 4], x=float(g["lift_opt"]), y=float(g["oil_opt"]), kind="opt"))
    gls = l4["gl_summary"]
    l4_gl = _table(gl, [("well_id", "Well", "t"), ("lift_now", "Lift now", "n0"), ("lift_opt", "Lift optimal", "n0"),
                        ("d_lift", "Change", "n0"), ("oil_now", "Oil now", "n2"), ("oil_opt", "Oil optimal", "n2"),
                        ("d_oil", "Δ oil m³/d", "n2"), ("marginal_now", "Marginal m³ per kSm³", "n3")]) if len(gl) else ""
    locked = l5["gate"]["locked_wells"]
    bc = l4["best_chokes"].copy()
    if len(bc):
        bc["caution"] = bc["well_id"].map(lambda w: f"data distrusted ({locked[w]}) - verify first" if w in locked else "")
    l4["best_chokes"] = bc
    l4_ch = _table(l4["best_chokes"], [("well_id", "Well", "t"), ("choke_now", "Choke now", "n0"),
                                       ("choke_new", "Proposed", "n0"), ("oil_now", "Oil now", "n2"),
                                       ("oil_new", "Oil new", "n2"), ("d_oil", "Δ oil", "n2"),
                                       ("drawdown_new", "Drawdown", "pct"), ("caution", "Caution", "w")]) if len(l4["best_chokes"]) else \
        '<p class="muted">No choke change worth making within the drawdown limit.</p>'
    l4_srp = _table(l4["srp"], [("well_id", "Well", "t"), ("spm_now", "SPM now", "n1"), ("spm_new", "SPM new", "n1"),
                                ("action", "Advice", "w"), ("submergence_now", "Submergence m", "n0"),
                                ("submergence_new", "New submergence m", "n0"), ("d_oil", "Δ oil", "n2")])
    plan = l4["plan"]
    p_oil = plan["totals"]["oil"]
    p_lift = plan["totals"]["lift"]
    l4_sched = _table(l4["schedule"].head(10), [("planned_date", "Test on", "t"), ("well_id", "Well", "t"),
                                                ("oil_m3d", "Oil m³/d", "n1"), ("test_age_d", "Test age d", "n0"),
                                                ("test_status", "Status", "t"), ("uncertainty_m3d", "± m³/d", "n2"),
                                                ("open_findings", "Open findings", "w")])
    l4_maint = _table(l4["maintenance"], [("item", "Item", "t"), ("action", "Action", "w"), ("reason", "Reason", "w"),
                                          ("misreport_per_day", "Misreport /d", "n0")]) if len(l4["maintenance"]) else ""

    # ---- L5 ------------------------------------------------------------------------
    tr = l5["trajectory"]
    l5_chart = dict(xcat=False, xlabel="control cycle", yfmt=1, ylabel="oil m³/d",
                    series=[dict(name="Plant (true)", color="--s1", pts=_pts(tr["cycle"], tr["plant_oil"]), dots=True),
                            dict(name="Twin prediction", color="--s2", dash=True, pts=_pts(tr["cycle"], tr["twin_oil"]), dots=True)])
    log = l5["log"]
    l5_log = _table(log, [("cycle", "Cycle", "n0"), ("well_id", "Well", "t"), ("event", "Event", "t"),
                          ("detail", "Detail", "w"), ("predicted_d_oil", "Predicted Δ", "n2"),
                          ("measured_d_oil", "Measured Δ", "n2")], "compact") if len(log) else ""
    gate = l5["gate"]
    locked_txt = ", ".join(f"{k} ({v})" for k, v in l5["locked"].items()) or "none"
    traj_tab = _table(tr, [("cycle", "Cycle", "n0"), ("plant_oil", "Plant oil", "n2"), ("measured_oil", "Measured", "n2"),
                           ("twin_oil", "Twin", "n2"), ("lift", "Lift gas", "n0"), ("moves", "Moves", "n0"),
                           ("rollbacks", "Rollbacks", "n0")], "compact")

    charts = json.dumps(dict(fc=fc_chart, la=la_chart, gl=gl_chart, l5=l5_chart)).replace("</", "<\\/")

    def tiles(items):
        return "".join(f'<div class="tile"><div class="tl">{_e(a)}</div><div class="tv">{b}</div>'
                       f'<div class="ts">{_e(c)}</div></div>' for a, b, c in items)
    achieved = levels[levels["status"] == "achieved"]["level"]
    top_level = int(achieved.max()) if len(achieved) else -1
    return TEMPLATE.format(
        name=_e(twin.name), date=date, ladder="".join(ladder), assess=assess_html, top=top_level,
        l0=l0_html, l0m=l0_models, l1t=tiles(l1_tiles), l1w=l1_wells, l1k=l1_tanks,
        l2=l2_html, l2top=l2_top, vfile=_e(validation_file),
        l3t=tiles([("Oil in 30 days (P50)", f"{_n(inst.iloc[29]['p50'], 1)} m³/d" if len(inst) > 29 else "–",
                    f"P10-P90 {_n(inst.iloc[29]['p10'], 1)}-{_n(inst.iloc[29]['p90'], 1)}" if len(inst) > 29 else ""),
                   ("Oil in 90 days (P50)", f"{_n(inst_end['p50'], 1)} m³/d" if inst_end is not None else "–",
                    f"P10-P90 {_n(inst_end['p10'], 1)}-{_n(inst_end['p90'], 1)}" if inst_end is not None else ""),
                   ("Ullage with dispatch stopped", f"{_n(l3['lookahead'].iloc[-1]['hours_to_safe_fill'], 0)} h",
                    "until tanks reach safe fill"),
                   ("Tests overdue", str(int(due["overdue"].sum())), f"of {len(due)} wells")]),
        l3w=l3_wells, l3la=l3_la, l3d=l3_drift, l3due=l3_due,
        l4t=tiles([("Gas-lift re-allocation", f"{gls.get('d_oil', 0):+.2f} m³/d",
                    f"lift {gls.get('lift_now', 0):,.0f} → {gls.get('lift_opt', 0):,.0f} Sm³/d "
                    f"({gls.get('lift_opt', 0) - gls.get('lift_now', 0):+,.0f})"),
                   ("All recommendations together", f"{p_oil[1] - p_oil[0]:+.1f} m³/d",
                    f"{100 * (p_oil[1] / p_oil[0] - 1):+.1f} % oil; lift {p_lift[0]:,.0f} → {p_lift[1]:,.0f} Sm³/d"),
                   ("Compressor load after", f"{_n(100 * plan['totals']['compressor_load'][1], 0)} %",
                    f"flare {plan['totals']['flare'][1]:,.0f} Sm³/d")]),
        l4gl=l4_gl, l4ch=l4_ch, l4srp=l4_srp, l4s=l4_sched, l4m=l4_maint,
        l5t=tiles([("Mode", _e(l5["mode"]), "simulation only - no control-system interface"),
                   ("Plant oil gain", f"{l5['gain']:+.1f} m³/d", f"{l5['start_oil']:.1f} → {l5['end_oil']:.1f} m³/d"),
                   ("Lift gas", f"{l5['lift_start']:,.0f} → {l5['lift_end']:,.0f}", "Sm³/d"),
                   ("Rollbacks", str(int((log['event'] == 'rolled back').sum())) if len(log) else "0",
                    f"locked: {locked_txt}")]),
        gate_notes=_e("; ".join(gate["notes"]) or "All inputs trusted"),
        l5log=l5_log, l5tr=traj_tab, charts=charts,
        max_dd=int(100 * cfg["twin"]["max_drawdown"]))


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>GGS Digital Twin</title>
<style>
:root{{color-scheme:light;--bg:#f6f6f4;--surface:#fcfcfb;--line:#e4e3df;--grid:#ecebe7;--text:#0b0b0b;--text2:#52514e;--muted:#7a7974;
--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s4:#eda100;--good:#0ca30c;--warn:#fab219;--crit:#d03b3b;--chip:#ecebe7;--band:rgba(235,104,52,.13)}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{color-scheme:dark;--bg:#121211;--surface:#1a1a19;--line:#33332f;--grid:#2a2a27;--text:#fff;--text2:#c3c2b7;--muted:#8f8e86;
--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--chip:#2a2a27;--band:rgba(217,89,38,.18)}}}}
:root[data-theme="dark"]{{color-scheme:dark;--bg:#121211;--surface:#1a1a19;--line:#33332f;--grid:#2a2a27;--text:#fff;--text2:#c3c2b7;--muted:#8f8e86;
--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--chip:#2a2a27;--band:rgba(217,89,38,.18)}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}}
header{{padding:26px 32px 6px}} h1{{font-size:22px;margin:0 0 4px;font-weight:650}} .sub{{color:var(--text2)}}
main{{padding:6px 32px 48px;max-width:1500px}} section{{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:18px 20px;margin:14px 0}}
h2{{font-size:16px;margin:0 0 4px;font-weight:650}} h3{{font-size:14px;margin:16px 0 6px}} .lead{{color:var(--text2);margin:0 0 12px;max-width:115ch}}
nav.tabs{{display:flex;gap:6px;flex-wrap:wrap;position:sticky;top:0;background:var(--bg);padding:10px 0;z-index:3}}
nav.tabs button{{border:1px solid var(--line);background:var(--surface);color:var(--text);border-radius:999px;padding:6px 14px;font:inherit;font-size:13px;cursor:pointer}}
nav.tabs button[aria-selected="true"]{{background:var(--text);color:var(--surface);border-color:var(--text)}}
.pane{{display:none}} .pane.on{{display:block}}
.ladder{{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;align-items:end}}
.rung{{text-align:left;border:1px solid var(--line);background:var(--surface);color:var(--text);border-radius:10px;padding:12px;font:inherit;cursor:pointer;display:flex;flex-direction:column;gap:4px}}
.rung:nth-child(1){{min-height:170px}} .rung:nth-child(2){{min-height:190px}} .rung:nth-child(3){{min-height:210px}} .rung:nth-child(4){{min-height:230px}} .rung:nth-child(5){{min-height:250px}} .rung:nth-child(6){{min-height:270px}}
.rl{{font-size:12px;color:var(--muted);font-weight:600}} .rn{{font-size:16px;font-weight:650}} .rd{{font-size:12.5px;color:var(--text2);flex:1}} .rs{{font-size:12px;color:var(--text2)}} .ra{{font-size:11.5px;color:var(--muted)}}
@media (max-width:1000px){{.ladder{{grid-template-columns:repeat(2,1fr)}} .rung{{min-height:0!important}} main,header{{padding-left:16px;padding-right:16px}}}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px;margin:10px 0}}
.tile{{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:13px 15px}} .tl{{color:var(--text2);font-size:12.5px}} .tv{{font-size:21px;font-weight:650;margin:3px 0;font-variant-numeric:tabular-nums}} .ts{{color:var(--muted);font-size:12px}}
.badge{{display:inline-flex;gap:4px;align-items:center;font-size:11.5px;font-weight:600;padding:2px 7px;border-radius:999px;white-space:nowrap;border:1px solid var(--line)}}
.badge.s-alarm{{background:color-mix(in srgb,var(--crit) 16%,transparent);border-color:color-mix(in srgb,var(--crit) 45%,transparent)}}
.badge.s-warn{{background:color-mix(in srgb,var(--warn) 22%,transparent);border-color:color-mix(in srgb,var(--warn) 55%,transparent)}}
.badge.s-ok{{background:color-mix(in srgb,var(--good) 14%,transparent);border-color:color-mix(in srgb,var(--good) 40%,transparent)}}
.tbl-wrap{{overflow-x:auto;border:1px solid var(--line);border-radius:8px}} table{{border-collapse:collapse;width:100%;font-size:12.8px}}
th,td{{padding:7px 10px;border-bottom:1px solid var(--grid);text-align:left;vertical-align:top}} th{{background:var(--surface);color:var(--text2);font-weight:600;white-space:nowrap}}
td.num,th.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}} td.nw{{white-space:nowrap}} table.compact td{{padding:5px 9px}}
.scroll{{max-height:460px;overflow:auto}} .grid2{{display:grid;grid-template-columns:1fr 1fr;gap:16px}} .grid2>div{{min-width:0}}
@media (max-width:1000px){{.grid2{{grid-template-columns:1fr}}}}
.chart{{position:relative}} .chart svg{{width:100%;height:auto;display:block}} .ax{{font-size:11px;fill:var(--muted)}} .gl{{stroke:var(--grid)}}
.legend{{display:flex;gap:14px;flex-wrap:wrap;font-size:12.5px;color:var(--text2);margin:6px 0 0}} .lk{{display:inline-block;width:18px;border-top:2px solid;vertical-align:middle;margin-right:6px}}
.tip{{position:absolute;pointer-events:none;background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:8px 10px;font-size:12px;box-shadow:0 4px 14px rgba(0,0,0,.12);display:none;min-width:150px;z-index:5}}
.tip .r{{display:flex;gap:10px;justify-content:space-between}} .tip b{{font-variant-numeric:tabular-nums}} .tip .k{{display:inline-block;width:12px;border-top:2px solid;margin-right:4px;vertical-align:middle}}
.note{{border-left:3px solid var(--warn);padding:8px 12px;background:color-mix(in srgb,var(--warn) 8%,transparent);border-radius:4px;color:var(--text2);margin:8px 0}}
.muted{{color:var(--muted)}} a{{color:var(--s1)}}
</style></head><body>
<header><h1>{name}: digital twin</h1><div class="sub">State as of production day {date} · capability levels per DNV-RP-A204 (0 standalone → 5 autonomous), the scale used by Norwegian operators and Kongsberg Digital (now Falkor) partners</div></header>
<main>
<nav class="tabs" role="tablist">
<button data-tab="OV" aria-selected="true">Overview</button><button data-tab="L0">L0 Standalone</button><button data-tab="L1">L1 Descriptive</button>
<button data-tab="L2">L2 Diagnostic</button><button data-tab="L3">L3 Predictive</button><button data-tab="L4">L4 Prescriptive</button><button data-tab="L5">L5 Autonomous</button></nav>

<div class="pane on" id="OV">
<section><h2>Where this twin stands</h2><p class="lead">Each level builds on the ones below it. Highest level fully achieved: <b>L{top}</b>. Level 4 runs as advice to the operator; Level 5 is demonstrated only against a plant simulator. Click a level to open it.</p>
<div class="ladder">{ladder}</div></section>
<section><h2>Maturity assessment</h2><p class="lead">What is in place at each level, the evidence for it, and what is still needed to reach a fully operational twin at an Ankleshwar-type gathering station.</p>{assess}</section>
</div>

<div class="pane" id="L0">
<section><h2>L0 Standalone: asset model and simulator</h2><p class="lead">The gathering-station network and physics models, which can run without any live data (engineering studies, what-ifs, training). Comparable to building K-Spice / LedaFlow models of the facility.</p>{l0}
<h3>Well models: calibrated at each accepted test</h3>{l0m}</section></div>

<div class="pane" id="L1">
<section><h2>L1 Descriptive: live, contextualised state</h2><p class="lead">Every reading is tied to its asset object. Soft sensors estimate what is not metered. Each well's PI (or pump efficiency) is re-tuned so the twin reproduces the reconciled rates: the twin is <i>synchronised</i>. A tuning factor far from 1 means the well model needs attention (stale PI or reservoir pressure).</p>
<div class="tiles">{l1t}</div><div class="scroll">{l1w}</div><h3>Tank farm</h3>{l1k}</section></div>

<div class="pane" id="L2">
<section><h2>L2 Diagnostic</h2><p class="lead">Data-quality screening, well-test validation, well-performance health and data reconciliation with gross-error detection. The full exception register is in the validation report (<a href="{vfile}">{vfile}</a>).</p>
<div class="grid2"><div>{l2}</div><div>{l2top}</div></div></section></div>

<div class="pane" id="L3">
<section><h2>L3 Predictive</h2><p class="lead">Decline forecasts per well (Arps, refitted after each detected regime change), 72-hour look-ahead of the tank farm and gas system, meter drift, and well-test due dates.</p>
<div class="tiles">{l3t}</div>
<div class="grid2"><div><h3>Installation oil: history and forecast (P10-P90 band)</h3><div class="chart" id="c_fc"></div><div class="legend" id="lg_fc"></div></div>
<div><h3>Tank-farm look-ahead (compressor-trip case affects flare only; see table)</h3><div class="chart" id="c_la"></div><div class="legend" id="lg_la"></div></div></div>
<h3>Look-ahead scenarios</h3>{l3la}<h3>Well forecasts</h3><div class="scroll">{l3w}</div>
<div class="grid2"><div><h3>Meter drift</h3>{l3d}</div><div><h3>Well tests due</h3>{l3due}</div></div></section></div>

<div class="pane" id="L4">
<section><h2>L4 Prescriptive: what-if and recommendations</h2><p class="lead">The synchronised twin evaluates changes before anyone touches the plant. Gas-lift gas is allocated by equal marginal oil within the lift-gas supply and compressor capacity. Choke changes are limited to a {max_dd} % drawdown (sand and coning guard). SRP speeds aim at the target submergence, and wells with open pump-health findings are held. The operator decides.</p>
<div class="tiles">{l4t}</div>
<div class="grid2"><div><h3>Gas-lift performance curves (○ now, ● optimal)</h3><div class="chart" id="c_gl"></div><div class="legend" id="lg_gl"></div></div><div><h3>Gas-lift allocation</h3>{l4gl}</div></div>
<div class="grid2"><div><h3>Choke changes</h3>{l4ch}</div><div><h3>SRP speed</h3>{l4srp}</div></div>
<div class="grid2"><div><h3>Well-test schedule (next 7 days)</h3>{l4s}</div><div><h3>Maintenance priorities</h3>{l4m}</div></div></section></div>

<div class="pane" id="L5">
<section><h2>L5 Autonomous: guarded closed loop (simulation only)</h2>
<div class="note">This loop drives a plant <b>simulator</b> whose parameters deliberately differ from the twin's. Nothing here can write to a real control system. A field loop needs OPC UA write-back through the DCS, management of change, HAZOP/SIL review, IEC 62443 cyber-security and operator procedures.</div>
<p class="lead">Each cycle the loop does five things:</p>
<ol class="lead">
<li><b>Trust gate:</b> wells whose data the diagnostics doubt are locked.</li>
<li><b>Re-synchronise</b> the twin to plant measurements.</li>
<li><b>Optimise</b> (L4).</li>
<li><b>Guard:</b> step limits, cumulative limits, a deadband, and lift-gas and compressor constraints.</li>
<li><b>Act and verify:</b> any move that turns out worse than predicted is rolled back, and the well is locked.</li>
</ol>
<p class="lead">Trust-gate notes: {gate_notes}.</p>
<div class="tiles">{l5t}</div>
<div class="grid2"><div><h3>Plant response vs twin prediction</h3><div class="chart" id="c_l5"></div><div class="legend" id="lg_l5"></div></div><div><h3>Trajectory</h3>{l5tr}</div></div>
<h3>Audit log</h3><div class="scroll">{l5log}</div></section></div>
</main>
<script type="application/json" id="cd">{charts}</script>
<script>
(function(){{
const C=JSON.parse(document.getElementById('cd').textContent);
const NS='http://www.w3.org/2000/svg';const css=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
function el(t,a,p){{const e=document.createElementNS(NS,t);for(const k in a)e.setAttribute(k,a[k]);if(p)p.appendChild(e);return e}}
function nice(lo,hi){{const span=hi-lo||1;const st=Math.pow(10,Math.floor(Math.log10(span/5)));const m=[1,2,2.5,5,10].find(x=>span/(st*x)<=6)*st;return {{lo:Math.floor(lo/m)*m,hi:Math.ceil(hi/m)*m,step:m}}}}
function chart(id,spec){{const host=document.getElementById(id);if(!host)return;host.textContent='';const W=640,H=300,m={{l:50,r:14,t:24,b:34}};
 const all=[];spec.series.forEach(s=>{{s.pts.forEach(p=>{{if(p[1]!=null)all.push(p)}});(s.band||[]).forEach(b=>b.forEach(p=>{{if(p[1]!=null)all.push(p)}}))}});(spec.markers||[]).forEach(k=>all.push([k.x,k.y]));(spec.refs||[]).forEach(r=>all.push([all.length?all[0][0]:0,r.y]));
 const xs=all.map(p=>p[0]),ys=all.map(p=>p[1]);const X0=Math.min(...xs),X1=Math.max(...xs);const yn=nice(Math.min(...ys)*0.97,Math.max(...ys)*1.03);
 const x=v=>m.l+(W-m.l-m.r)*((v-X0)/((X1-X0)||1));const y=v=>m.t+(H-m.t-m.b)*(1-(v-yn.lo)/(yn.hi-yn.lo));
 const svg=el('svg',{{viewBox:`0 0 ${{W}} ${{H}}`,role:'img'}},host);
 for(let t=yn.lo;t<=yn.hi+1e-9;t+=yn.step){{el('line',{{x1:m.l,x2:W-m.r,y1:y(t),y2:y(t),class:'gl'}},svg);const tx=el('text',{{x:m.l-6,y:y(t)+4,'text-anchor':'end',class:'ax'}},svg);tx.textContent=t.toFixed(spec.yfmt||0)}}
 const xt=spec.xcat?spec.labels.map((l,i)=>[i,l]).filter((_,i,a)=>i%Math.ceil(a.length/7)===0):nice(X0,X1);
 if(spec.xcat){{xt.forEach(([i,l])=>{{const tx=el('text',{{x:x(i),y:H-12,'text-anchor':'middle',class:'ax'}},svg);tx.textContent=l.slice(5)}})}}
 else{{for(let t=xt.lo;t<=X1+1e-9;t+=xt.step){{if(t<X0-1e-9)continue;const tx=el('text',{{x:x(t),y:H-12,'text-anchor':'middle',class:'ax'}},svg);tx.textContent=t>=1000?(t/1000)+'k':t}}}}
 if(spec.xlabel){{const tx=el('text',{{x:W-m.r,y:H-1,'text-anchor':'end',class:'ax'}},svg);tx.textContent=spec.xlabel}}
 if(spec.ylabel){{const tx=el('text',{{x:4,y:11,class:'ax'}},svg);tx.textContent=spec.ylabel}}
 (spec.refs||[]).forEach(r=>{{el('line',{{x1:m.l,x2:W-m.r,y1:y(r.y),y2:y(r.y),stroke:css('--text2'),'stroke-dasharray':'5 4','stroke-width':1.2}},svg);const tx=el('text',{{x:W-m.r-4,y:y(r.y)-4,'text-anchor':'end',class:'ax'}},svg);tx.textContent=r.label}});
 spec.series.forEach(s=>{{if(s.band){{const lo=s.band[0],hi=s.band[1];let d='M'+lo.map(p=>x(p[0])+','+y(p[1])).join('L')+'L'+hi.slice().reverse().map(p=>x(p[0])+','+y(p[1])).join('L')+'Z';el('path',{{d,fill:css('--band')}},svg)}}}});
 spec.series.forEach(s=>{{let d='',pen=false;s.pts.forEach(p=>{{if(p[1]==null){{pen=false;return}}d+=(pen?'L':'M')+x(p[0])+','+y(p[1]);pen=true}});el('path',{{d,fill:'none',stroke:css(s.color),'stroke-width':2,'stroke-dasharray':s.dash?'6 4':'','stroke-linejoin':'round'}},svg);
  if(s.dots)s.pts.forEach(p=>{{if(p[1]!=null)el('circle',{{cx:x(p[0]),cy:y(p[1]),r:4,fill:css(s.color),stroke:css('--surface'),'stroke-width':2}},svg)}})}});
 (spec.markers||[]).forEach(k=>el('circle',{{cx:x(k.x),cy:y(k.y),r:5,fill:k.kind==='opt'?css(k.color):css('--surface'),stroke:css(k.color),'stroke-width':2}},svg));
 const lg=document.getElementById('lg_'+id.slice(2));if(lg){{lg.textContent='';spec.series.forEach(s=>{{const sp=document.createElement('span');const k=document.createElement('span');k.className='lk';k.style.borderColor=css(s.color);if(s.dash)k.style.borderTopStyle='dashed';sp.appendChild(k);sp.appendChild(document.createTextNode(s.name));lg.appendChild(sp)}});if(spec.series.some(s=>s.band)){{const sp=document.createElement('span');sp.textContent='▮ P10-P90';sp.style.color=css('--s2');lg.appendChild(sp)}}}}
 const tip=document.createElement('div');tip.className='tip';host.appendChild(tip);const hair=el('line',{{y1:m.t,y2:H-m.b,stroke:css('--muted'),opacity:0}},svg);
 const hit=el('rect',{{x:m.l,y:m.t,width:W-m.l-m.r,height:H-m.t-m.b,fill:'transparent'}},svg);
 hit.addEventListener('pointermove',e=>{{const r=svg.getBoundingClientRect();const fx=X0+((e.clientX-r.left)/r.width*W-m.l)/(W-m.l-m.r)*(X1-X0);
  tip.textContent='';let best=null;spec.series.forEach(s=>{{let q=null,dq=1e18;s.pts.forEach(p=>{{if(p[1]!=null&&Math.abs(p[0]-fx)<dq){{dq=Math.abs(p[0]-fx);q=p}}}});if(q){{if(!best||Math.abs(q[0]-fx)<Math.abs(best-fx))best=q[0]}}}});if(best==null)return;
  const h=document.createElement('div');h.style.color='var(--text2)';h.textContent=spec.xcat?spec.labels[best]:(spec.xlabel||'x')+' '+(+best).toLocaleString();tip.appendChild(h);
  spec.series.forEach(s=>{{const q=s.pts.find(p=>p[0]===best&&p[1]!=null);if(!q)return;const d=document.createElement('div');d.className='r';const l=document.createElement('span');const k=document.createElement('span');k.className='k';k.style.borderColor=css(s.color);l.appendChild(k);l.appendChild(document.createTextNode(s.name));const b=document.createElement('b');b.textContent=q[1].toFixed((spec.yfmt||0)+1);d.appendChild(l);d.appendChild(b);tip.appendChild(d)}});
  hair.setAttribute('x1',x(best));hair.setAttribute('x2',x(best));hair.setAttribute('opacity',1);tip.style.display='block';tip.style.left=Math.min(x(best)/W*r.width+12,r.width-190)+'px';tip.style.top='8px'}});
 hit.addEventListener('pointerleave',()=>{{tip.style.display='none';hair.setAttribute('opacity',0)}})}}
function drawAll(){{chart('c_fc',C.fc);chart('c_la',C.la);chart('c_gl',C.gl);chart('c_l5',C.l5)}}
const tabs=document.querySelectorAll('nav.tabs button');function show(t){{tabs.forEach(b=>b.setAttribute('aria-selected',b.dataset.tab===t?'true':'false'));document.querySelectorAll('.pane').forEach(p=>p.classList.toggle('on',p.id===t));drawAll();try{{localStorage.setItem('twin_tab',t)}}catch(e){{}}}}
tabs.forEach(b=>b.addEventListener('click',()=>show(b.dataset.tab)));document.querySelectorAll('.rung').forEach(r=>r.addEventListener('click',()=>{{show(r.dataset.go);window.scrollTo(0,0)}}));
let t0='OV';try{{t0=localStorage.getItem('twin_tab')||'OV'}}catch(e){{}}show(t0);
matchMedia('(prefers-color-scheme: dark)').addEventListener('change',drawAll);
}})();
</script></body></html>"""
