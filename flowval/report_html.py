"""Self-contained HTML validation report (no external dependencies)."""
from __future__ import annotations

import html
import json
from typing import List

import numpy as np
import pandas as pd

from .findings import SEVERITY_ORDER, worst

ICON = {"ALARM": "&#9650;", "WARN": "&#9670;", "INFO": "&#9679;", "OK": "&#10003;"}
LABEL = {"ALARM": "Alarm", "WARN": "Warning", "INFO": "Info", "OK": "OK"}


def _e(x) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return ""
    return html.escape(str(x))


def _n(x, d=1) -> str:
    try:
        if x is None or pd.isna(x):
            return ""
        return f"{float(x):,.{d}f}"
    except (TypeError, ValueError):
        return _e(x)


def _badge(sev) -> str:
    if not isinstance(sev, str) or not sev:
        return ""
    return f'<span class="badge s-{sev.lower()}">{ICON.get(sev, "")} {LABEL.get(sev, sev)}</span>'


def _table(df: pd.DataFrame, cols: List[tuple], cls: str = "", row_attrs=None) -> str:
    """cols: [(column, header, kind)] kind in {'t','n0','n1','n2','n3','pct','sev'}"""
    h = "".join(f'<th class="{"num" if k.startswith(("n", "pct")) else ""}">{_e(t)}</th>' for _, t, k in cols)
    body = []
    for i, (_, r) in enumerate(df.iterrows()):
        tds = []
        for c, _, k in cols:
            v = r.get(c)
            if k == "sev":
                tds.append(f"<td>{_badge(v)}</td>")
            elif k.startswith("n"):
                tds.append(f'<td class="num">{_n(v, int(k[1]))}</td>')
            elif k == "pct":
                tds.append(f'<td class="num">{_n(v * 100 if v is not None and pd.notna(v) else v, 1)}</td>')
            elif k == "w":
                tds.append(f"<td>{_e(v)}</td>")
            else:
                tds.append(f'<td class="nw">{_e(v)}</td>')
        attrs = row_attrs(r) if row_attrs else ""
        body.append(f"<tr {attrs}>{''.join(tds)}</tr>")
    return f'<div class="tbl-wrap"><table class="{cls}"><thead><tr>{h}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


# ---------------------------------------------------------------------------
def _status_map(res) -> dict:
    f = res.findings_daily
    st = {}
    for oid, g in f.groupby("object_id"):
        st[str(oid)] = worst(g["severity"])
    net = res.network
    for s in net.separators.values():
        ms = [m for m in (s.oil_meter, s.water_meter, s.gas_meter) if m]
        st[s.id] = worst([st.get(s.id, "OK")] + [st.get(m, "OK") for m in ms])
    return st


def _network_svg(res) -> str:
    net = res.network
    st = _status_map(res)
    wells = list(net.wells.values())
    seps = list(net.separators.values())
    tanks = list(net.tanks.values())
    row_h, top = 34, 40
    H = max(len(wells) * row_h + top + 20, 460)
    X = dict(well=20, sep=330, tank=560, out=790)
    W = 980
    pos = {}
    parts = []
    # columns headings
    for k, t in (("well", "Wells"), ("sep", "Separators"), ("tank", "Storage tanks"), ("out", "Dispositions")):
        parts.append(f'<text class="colh" x="{X[k]}" y="22">{t}</text>')

    def node(nid, x, y, w, h, title, sub, sev):
        sev = sev or "OK"
        pos[nid] = (x, y, w, h)
        parts.append(
            f'<g class="node" tabindex="0"><title>{_e(title)} - {LABEL[sev]}</title>'
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" class="nbox"/>'
            f'<rect x="{x}" y="{y}" width="5" height="{h}" rx="2" class="sbar s-{sev.lower()}"/>'
            f'<text x="{x + 14}" y="{y + h / 2 - 2}" class="nt">{_e(title)}</text>'
            f'<text x="{x + 14}" y="{y + h / 2 + 11}" class="ns">{_e(sub)}</text>'
            f'<text x="{x + w - 10}" y="{y + h / 2 + 4}" class="ni s-{sev.lower()}-t" text-anchor="end">{ICON[sev]}</text></g>')

    lift = {"self_flow": "self-flow", "gas_lift": "gas lift", "srp": "SRP", "esp": "ESP"}
    for i, w in enumerate(wells):
        node(w.id, X["well"], top + i * row_h, 200, 28, w.id, f"{lift.get(w.lift, w.lift)} → {w.separator}", st.get(w.id))
    sep_gap = (H - top - 40) / max(len(seps), 1)
    for i, s in enumerate(seps):
        y = top + i * sep_gap + sep_gap / 2 - 35
        node(s.id, X["sep"], y, 190, 70, s.id, f"{s.kind} · {s.orientation}", st.get(s.id))
    t_gap = (H - top - 40) / max(len(tanks), 1)
    for i, t in enumerate(tanks):
        y = top + i * t_gap + t_gap / 2 - 30
        node(t.id, X["tank"], y, 160, 60, t.id, "fixed roof, dip gauged", st.get(t.id))
    outs = []
    if net.dispatch_meter:
        outs.append(("dispatch", f"Dispatch {net.dispatch_meter}", "oil to CTF", st.get(net.dispatch_meter)))
    for g in net.gas_disposal:
        outs.append((g["meter"], f"{g['meter']} {g.get('role', '')}", "gas", st.get(g["meter"])))
    outs.append(("water", "Water to ETP", "water legs, tank drains", "OK"))
    o_gap = (H - top - 40) / max(len(outs), 1)
    for i, (nid, t, sub, sev) in enumerate(outs):
        node(nid, X["out"], top + i * o_gap + o_gap / 2 - 26, 170, 52, t, sub, sev)

    edges = []

    def edge(a, b, cls="e"):
        xa, ya, wa, ha = pos[a]
        xb, yb, wb, hb = pos[b]
        x1, y1, x2, y2 = xa + wa, ya + ha / 2, xb, yb + hb / 2
        mx = (x1 + x2) / 2
        edges.append(f'<path class="{cls}" d="M{x1},{y1} C{mx},{y1} {mx},{y2} {x2},{y2}"/>')
    ts = net.test_separators[0] if net.test_separators else None
    for w in wells:
        edge(w.id, w.separator)
        if ts:
            edge(w.id, ts, "e test")
    for s in seps:
        for t in tanks:
            edge(s.id, t.id, "e thin")
        for g in net.gas_disposal:
            edge(s.id, g["meter"], "e gas")
        edge(s.id, "water", "e water")
    for t in tanks:
        if net.dispatch_meter:
            edge(t.id, "dispatch")
        edge(t.id, "water", "e water")
    return (f'<svg class="net" viewBox="0 0 {W} {H}" role="img" aria-label="Flow network status">'
            f'{"".join(edges)}{"".join(parts)}</svg>')


# ---------------------------------------------------------------------------
def build_html(res, title: str | None = None) -> str:
    net, cfg = res.network, res.cfg
    f = res.findings
    s = res.summary.copy()
    d0, d1 = res.dates[0], res.dates[-1]
    title = title or f"Flow validation - {net.name}"
    n_alarm = int((res.findings_daily["severity"] == "ALARM").sum())
    n_warn = int((res.findings_daily["severity"] == "WARN").sum())
    reg_alarm = int((f["severity"] == "ALARM").sum())
    reg_warn = int((f["severity"] == "WARN").sum())
    dd = res.dvr_days
    pass_days = int(dd["passed_initial"].sum())
    clean_days = int(dd["passed_final"].sum())

    tot = dict(oil=s["dvr_oil"].sum(), water=s["dvr_water"].sum(), gas=s["dvr_gas"].sum())
    af = {}
    for k in ("oil", "water", "gas"):
        ok = s[f"meas_{k}"].notna() & s[f"theo_{k}"].notna()
        af[k] = s.loc[ok, f"meas_{k}"].sum() / s.loc[ok, f"theo_{k}"].sum() if ok.any() else np.nan
    lo_w, hi_w = cfg["allocation"]["af_warn"]

    def af_sev(x):
        lo_a, hi_a = cfg["allocation"]["af_alarm"]
        if x != x:
            return "INFO"
        return "ALARM" if (x < lo_a or x > hi_a) else ("WARN" if (x < lo_w or x > hi_w) else "OK")

    tiles = [
        ("Reconciled oil", f"{_n(tot['oil'], 0)} m³", f"{_n(tot['oil'] / len(res.dates), 1)} m³/d net standard (15 °C)", None),
        ("Reconciled water", f"{_n(tot['water'], 0)} m³", f"{_n(tot['water'] / len(res.dates), 1)} m³/d", None),
        ("Reconciled formation gas", f"{_n(tot['gas'] / 1000, 1)} kSm³", f"{_n(tot['gas'] / len(res.dates) / 1000, 2)} kSm³/d (excl. lift gas)", None),
        ("Allocation factor oil", _n(af["oil"], 3), f"tank / theoretical, band {lo_w}-{hi_w}", af_sev(af["oil"])),
        ("Allocation factor water", _n(af["water"], 3), "separator meters / theoretical", af_sev(af["water"])),
        ("Allocation factor gas", _n(af["gas"], 3), "separator gas − lift / theoretical", af_sev(af["gas"])),
        ("DVR global test", f"{pass_days}/{len(dd)} days", f"passed as measured; {clean_days} after gross-error removal",
         "OK" if pass_days == len(dd) else ("WARN" if pass_days >= 0.7 * len(dd) else "ALARM")),
        ("Exceptions", f"{reg_alarm} alarms · {reg_warn} warnings", f"register lines ({n_alarm} / {n_warn} daily events)",
         "ALARM" if reg_alarm else ("WARN" if reg_warn else "OK")),
    ]
    tile_html = "".join(
        f'<div class="tile">{"" if sev is None else _badge(sev)}<div class="tl">{_e(t)}</div>'
        f'<div class="tv">{v}</div><div class="ts">{_e(sub)}</div></div>' for t, v, sub, sev in tiles)

    # ---- chart data --------------------------------------------------------
    chart = dict(
        dates=list(s["date"]),
        af=dict(oil=[None if pd.isna(x) else round(float(x), 4) for x in s["af_oil"]],
                water=[None if pd.isna(x) else round(float(x), 4) for x in s["af_water"]],
                gas=[None if pd.isna(x) else round(float(x), 4) for x in s["af_gas"]]),
        band=[lo_w, hi_w],
        ratio=[None if pd.isna(x) else round(float(x), 3) for x in dd["ratio"]],
        elim=list(dd["eliminated"].fillna("")),
        amb=list(dd["ambiguous"].fillna("").map(lambda x: "ambiguous" if x else "")),
    )

    # ---- register ------------------------------------------------------------
    f2 = f.copy()
    f2["period"] = np.where(f2["days"] > 1, f2["first_date"] + " → " + f2["last_date"], f2["first_date"])
    reg = _table(f2, [("severity", "Severity", "sev"), ("period", "Date(s)", "t"), ("days", "Days", "n0"),
                      ("layer", "Layer", "t"), ("object_id", "Object", "t"), ("check", "Check", "t"),
                      ("message", "Finding", "w"), ("action", "Recommended action", "w")], "reg",
                 row_attrs=lambda r: f'data-sev="{_e(r["severity"])}" data-layer="{_e(r["layer"])}"')
    layers = sorted(f["layer"].unique())
    layer_btns = "".join(f'<button class="chip" data-layer="{_e(l)}">{_e(l.replace("_", " "))}</button>' for l in layers)

    # ---- bias suspects -----------------------------------------------------------
    b = res.bias_suspects
    if b is not None and len(b):
        b = b[b["kind"] != "well_test"].copy() if (b["kind"] != "well_test").any() else b
        bias_html = _table(b.head(15), [("tag", "Measurement", "t"), ("var", "Stream", "t"), ("kind", "Type", "t"),
                                        ("days_flagged", "Days flagged", "n0"), ("days_total", "Days", "n0"),
                                        ("first_flag", "First flagged", "t"), ("mean_dev_pct", "Mean deviation % (flagged)", "n1"),
                                        ("mean_dev_all_pct", "Mean deviation % (all days)", "n1")])
    else:
        bias_html = '<p class="muted">No measurement was flagged by reconciliation.</p>'

    # ---- tests -------------------------------------------------------------------
    t = res.tests.copy()
    period_tests = t[(t["test_date"] >= d0) & (t["test_date"] <= d1)]
    last_before = t[t["test_date"] < d0].sort_values("test_date").groupby("well_id").tail(1)
    tt = pd.concat([last_before.assign(scope="before period"), period_tests.assign(scope="in period")])
    tt = tt.sort_values(["well_id", "test_date"])
    tt["sev"] = tt["status"].map({"ACCEPTED": "OK", "ACCEPTED_W": "WARN", "PROVISIONAL": "WARN", "REJECTED": "ALARM"})
    tests_html = _table(tt, [("sev", "", "sev"), ("well_id", "Well", "t"), ("test_id", "Test", "t"), ("test_date", "Date", "t"),
                             ("scope", "", "t"), ("status", "Status", "t"), ("duration_h", "Hours", "n1"),
                             ("liquid_m3d", "Liquid m³/d", "n1"), ("oil_m3d", "Oil m³/d", "n1"), ("wc", "WC %", "pct"),
                             ("gor", "GOR Sm³/m³", "n0"), ("srp_ve", "SRP VE", "n2"), ("choke_cal", "Choke cal.", "n2"),
                             ("reasons", "Reasons", "w")])

    # ---- well allocation summary -------------------------------------------------
    w = res.wells.copy()
    g = w.groupby("well_id")
    ws = pd.DataFrame(dict(
        lift=g["lift"].first(), separator=g["separator"].first(), days_on=g["hours_on"].apply(lambda x: (x > 0).sum()),
        theo_oil=g["theo_oil"].sum(), prop_oil=g["prop_oil"].sum(), dvr_oil=g["dvr_oil"].sum(),
        theo_water=g["theo_water"].sum(), dvr_water=g["dvr_water"].sum(),
        theo_gas=g["theo_gas"].sum(), dvr_gas=g["dvr_gas"].sum(),
        last_test=g["test_id"].last(), test_status=g["test_status"].last(), age=g["test_age_d"].last(),
    )).reset_index()
    ws["dvr_vs_theo"] = ws["dvr_oil"] / ws["theo_oil"] - 1
    st = _status_map(res)
    ws["sev"] = ws["well_id"].map(lambda x: st.get(x, "OK"))
    tot_row = pd.DataFrame([dict(well_id="Total", theo_oil=ws["theo_oil"].sum(), prop_oil=ws["prop_oil"].sum(),
                                 dvr_oil=ws["dvr_oil"].sum(), theo_water=ws["theo_water"].sum(),
                                 dvr_water=ws["dvr_water"].sum(), theo_gas=ws["theo_gas"].sum(),
                                 dvr_gas=ws["dvr_gas"].sum(),
                                 dvr_vs_theo=ws["dvr_oil"].sum() / ws["theo_oil"].sum() - 1)])
    ws = pd.concat([ws, tot_row], ignore_index=True)
    wells_html = _table(ws, [("sev", "", "sev"), ("well_id", "Well", "t"), ("lift", "Lift", "t"), ("separator", "Sep.", "t"),
                             ("days_on", "Days on", "n0"), ("theo_oil", "Oil theoretical", "n1"),
                             ("prop_oil", "Oil allocated (AF)", "n1"), ("dvr_oil", "Oil reconciled", "n1"),
                             ("dvr_vs_theo", "Recon. vs theo. %", "pct"), ("theo_water", "Water theo.", "n1"),
                             ("dvr_water", "Water recon.", "n1"), ("theo_gas", "Gas theo. Sm³", "n0"),
                             ("dvr_gas", "Gas recon. Sm³", "n0"), ("last_test", "Test in use (end)", "t"),
                             ("test_status", "Status", "t"), ("age", "Age d", "n0")], "wells")

    # ---- separators -----------------------------------------------------------------
    sp = res.separators
    sa = res.separator_af
    sg = sp[sp["kind"] == "production"].groupby("separator").agg(
        oil=("oil_std", "mean"), water=("water_std", "mean"), oilleg_w=("oilleg_water", "mean"),
        gas=("gas_std", "mean"), p=("pressure_ksc", "mean"), t=("temperature_degc", "mean"),
        rt=("retention_min", "mean"), rt_min=("retention_api12j_min", "first"), load=("gas_load", "max")).reset_index()
    sg = sg.merge(sa.groupby("separator")[["af_oil", "af_water", "af_gas"]].mean().reset_index(), on="separator", how="left")
    sg["sev"] = sg["separator"].map(lambda x: st.get(x, "OK"))
    sep_html = _table(sg, [("sev", "", "sev"), ("separator", "Separator", "t"), ("p", "P ksc", "n2"), ("t", "T °C", "n1"),
                           ("oil", "Oil m³/d (15 °C)", "n1"), ("water", "Water leg m³/d", "n1"),
                           ("oilleg_w", "Water in oil leg", "n1"), ("gas", "Gas Sm³/d", "n0"),
                           ("rt", "Retention min", "n1"), ("rt_min", "API 12J min", "n0"),
                           ("load", "Max gas load % of S-B", "pct"), ("af_oil", "AF oil", "n3"),
                           ("af_water", "AF water", "n3"), ("af_gas", "AF gas", "n3")])

    # ---- tank balance -------------------------------------------------------------------
    v = res.dvr_vars
    piv = v.pivot_table(index="date", columns="var", values="meas", aggfunc="first")
    sep_oil_cols = [c for c in piv.columns if c.startswith("sep:") and c.endswith(":oil")]
    tb = pd.DataFrame(index=piv.index)
    tb["dinv"] = piv.get("tank:dinv_oil")
    tb["disp"] = piv.get("dispatch:oil")
    tb["tank_prod"] = tb["dinv"] + tb["disp"]
    tb["sep_oil"] = piv[sep_oil_cols].sum(axis=1, min_count=1)
    tb["res"] = tb["tank_prod"] - tb["sep_oil"]
    tb["res_pct"] = tb["res"] / tb["sep_oil"]
    rec = v.pivot_table(index="date", columns="var", values="rec", aggfunc="first")
    tb["rec_prod"] = rec.get("tank:dinv_oil") + rec.get("dispatch:oil")
    tb = tb.reset_index()
    tank_html = _table(tb, [("date", "Day", "t"), ("dinv", "ΔNSV tanks m³", "n2"), ("disp", "Dispatch NSV m³", "n2"),
                            ("tank_prod", "Tank production m³", "n2"), ("sep_oil", "Σ separator oil m³", "n2"),
                            ("res", "Difference m³", "n2"), ("res_pct", "Difference %", "pct"),
                            ("rec_prod", "Reconciled m³", "n2")], "compact")

    dvr_html = _table(dd, [("date", "Day", "t"), ("chi2", "χ² measured", "n1"), ("dof", "dof", "n0"),
                           ("chi2_crit", "χ² crit (95%)", "n1"), ("ratio", "Ratio", "n2"),
                           ("eliminated", "Gross errors isolated", "w"), ("ambiguous", "Ambiguous candidates", "w"),
                           ("chi2_final", "χ² after", "n1")], "compact")

    wp = res.well_perf.copy() if res.well_perf is not None else pd.DataFrame()
    if len(wp):
        wp["sev"] = wp["well_id"].map(lambda x: st.get(x, "OK"))
        wp["wc_test_pct"] = wp["wc_test"] * 100
        wp["wc_sample_pct"] = wp["wc_sample"] * 100
        perf_html = _table(wp, [("sev", "", "sev"), ("well_id", "Well", "t"), ("lift", "Lift", "t"),
                                ("pr_ksc", "Pr ksc", "n1"), ("pb_ksc", "Pb ksc", "n0"), ("pwf_ksc", "Pwf ksc", "n1"),
                                ("drawdown", "Drawdown %", "pct"), ("pi_prev", "PI prev.", "n2"), ("pi_last", "PI last", "n2"),
                                ("methods", "Rate estimators", "t"), ("vfm_dev_mean", "Estimate vs test (mean) %", "pct"),
                                ("vfm_dev_last", "Estimate vs test (last) %", "pct"), ("dfl_m", "Fluid level m", "n0"),
                                ("submergence_m", "Submergence m", "n0"), ("fillage", "Fillage %", "n0"),
                                ("inj_p", "Inj. pressure ksc", "n1"), ("wc_test_pct", "WC test %", "n0"),
                                ("wc_sample_pct", "WC sample %", "n0")])
    else:
        perf_html = '<p class="muted">No well static data supplied (perforation depth, tubing, reservoir pressure).</p>'
    net_svg = _network_svg(res)
    data_json = json.dumps(chart).replace("</", "<\\/")
    return TEMPLATE.format(title=_e(title), name=_e(net.name), d0=d0, d1=d1, ndays=len(res.dates),
                           tiles=tile_html, net=net_svg, reg=reg, layer_btns=layer_btns, bias=bias_html,
                           tests=tests_html, wells=wells_html, perf=perf_html, seps=sep_html, tanks=tank_html, dvr=dvr_html,
                           data=data_json, alpha=cfg["dvr"]["alpha"])


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Flow Validation Report</title>
<style>
:root{{color-scheme:light;--bg:#f6f6f4;--surface:#fcfcfb;--line:#e4e3df;--grid:#ecebe7;--text:#0b0b0b;--text2:#52514e;--muted:#7a7974;
--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--good:#0ca30c;--warn:#fab219;--serious:#ec835a;--crit:#d03b3b;--chip:#ecebe7;--band:rgba(42,120,214,.08)}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{color-scheme:dark;--bg:#121211;--surface:#1a1a19;--line:#33332f;--grid:#2a2a27;--text:#ffffff;--text2:#c3c2b7;--muted:#8f8e86;
--s1:#3987e5;--s2:#d95926;--s3:#199e70;--chip:#2a2a27;--band:rgba(57,135,229,.12)}}}}
:root[data-theme="dark"]{{color-scheme:dark;--bg:#121211;--surface:#1a1a19;--line:#33332f;--grid:#2a2a27;--text:#ffffff;--text2:#c3c2b7;--muted:#8f8e86;
--s1:#3987e5;--s2:#d95926;--s3:#199e70;--chip:#2a2a27;--band:rgba(57,135,229,.12)}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}}
header{{padding:28px 32px 8px}} h1{{font-size:22px;margin:0 0 4px;font-weight:650;letter-spacing:-.01em}} .sub{{color:var(--text2)}}
main{{padding:8px 32px 48px;max-width:1500px}} section{{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:18px 20px;margin:16px 0}}
h2{{font-size:16px;margin:0 0 4px;font-weight:650}} .lead{{color:var(--text2);margin:0 0 12px;max-width:110ch}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px;margin:16px 0}}
.tile{{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:14px 16px;position:relative}}
.tile .badge{{position:absolute;top:12px;right:12px}} .tl{{color:var(--text2);font-size:12.5px}} .tv{{font-size:22px;font-weight:650;margin:4px 0 2px;font-variant-numeric:tabular-nums}} .ts{{color:var(--muted);font-size:12px}}
.badge{{display:inline-flex;gap:4px;align-items:center;font-size:11.5px;font-weight:600;padding:2px 7px;border-radius:999px;white-space:nowrap;border:1px solid var(--line);color:var(--text)}}
.badge.s-alarm{{background:color-mix(in srgb,var(--crit) 16%,transparent);border-color:color-mix(in srgb,var(--crit) 45%,transparent)}}
.badge.s-warn{{background:color-mix(in srgb,var(--warn) 22%,transparent);border-color:color-mix(in srgb,var(--warn) 55%,transparent)}}
.badge.s-ok{{background:color-mix(in srgb,var(--good) 14%,transparent);border-color:color-mix(in srgb,var(--good) 40%,transparent)}}
.badge.s-info{{background:var(--chip)}}
.tbl-wrap{{overflow-x:auto;border:1px solid var(--line);border-radius:8px}} table{{border-collapse:collapse;width:100%;font-size:12.8px}}
th,td{{padding:7px 10px;border-bottom:1px solid var(--grid);text-align:left;vertical-align:top}} th{{background:var(--surface);color:var(--text2);font-weight:600;position:sticky;top:0;white-space:nowrap}}
td.num,th.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}} tbody tr:hover{{background:color-mix(in srgb,var(--s1) 6%,transparent)}}
table.reg td:nth-child(7){{min-width:380px}} table.reg td:nth-child(8){{min-width:220px;color:var(--text2)}}
table.compact td,table.compact th{{padding:5px 9px}} .scroll{{max-height:520px;overflow:auto}}
.filters{{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 10px}} .chip{{border:1px solid var(--line);background:var(--chip);color:var(--text);border-radius:999px;padding:4px 11px;font:inherit;font-size:12.5px;cursor:pointer}}
.chip[aria-pressed="true"]{{background:var(--text);color:var(--surface);border-color:var(--text)}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:16px}} @media (max-width:1000px){{.grid2{{grid-template-columns:1fr}} main,header{{padding-left:16px;padding-right:16px}}}}
.netwrap{{overflow-x:auto}} svg.net{{width:100%;min-width:860px;height:auto;display:block}} .colh{{font-size:12px;fill:var(--text2);font-weight:600}}
.nbox{{fill:var(--surface);stroke:var(--line)}} .nt{{font-size:12.5px;font-weight:600;fill:var(--text)}} .ns{{font-size:10.5px;fill:var(--muted)}} .ni{{font-size:12px}}
.sbar.s-ok{{fill:var(--good)}} .sbar.s-warn{{fill:var(--warn)}} .sbar.s-alarm{{fill:var(--crit)}} .sbar.s-info{{fill:var(--muted)}}
.s-ok-t{{fill:var(--good)}} .s-warn-t{{fill:#b07a00}} .s-alarm-t{{fill:var(--crit)}} .s-info-t{{fill:var(--muted)}}
.e{{fill:none;stroke:var(--line);stroke-width:1.5}} .e.test{{stroke-dasharray:3 4;opacity:.55}} .e.thin{{opacity:.8}} .e.gas{{stroke-dasharray:1 3}} .e.water{{opacity:.45}}
.legend{{display:flex;gap:16px;flex-wrap:wrap;font-size:12.5px;color:var(--text2);margin:6px 0 0}} .lk{{display:inline-block;width:18px;height:0;border-top:2px solid;vertical-align:middle;margin-right:6px}}
.chart{{position:relative}} .chart svg{{width:100%;height:auto;display:block}} .ax{{font-size:11px;fill:var(--muted)}} .gl{{stroke:var(--grid)}}
.tip{{position:absolute;pointer-events:none;background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:8px 10px;font-size:12px;box-shadow:0 4px 14px rgba(0,0,0,.12);display:none;min-width:150px;z-index:5}}
.tip b{{font-variant-numeric:tabular-nums}} .tip .r{{display:flex;gap:8px;align-items:center;justify-content:space-between}} .tip .k{{display:inline-block;width:12px;border-top:2px solid}}
.muted{{color:var(--muted)}} td.nw{{white-space:nowrap}} .grid2>section{{min-width:0}} details summary{{cursor:pointer;color:var(--text2);margin:6px 0}} ol.m li{{margin:4px 0}} code{{font-size:12px;background:var(--chip);padding:1px 4px;border-radius:4px}}
</style></head><body>
<header><h1>{title}</h1><div class="sub">Production days {d0} to {d1} ({ndays} days) · wells → separators → tanks → dispatch · oil &amp; water at 15 °C, gas at standard conditions</div></header>
<main>
<div class="tiles">{tiles}</div>

<section><h2>Network status</h2><p class="lead">Worst finding per object over the period. The test separator (dashed links) receives each well in rotation; gas from all separators goes to the compressor, flare and fuel meters.</p><div class="netwrap">{net}</div>
<div class="legend"><span><span class="badge s-ok">&#10003; OK</span></span><span><span class="badge s-warn">&#9670; Warning</span></span><span><span class="badge s-alarm">&#9650; Alarm</span></span></div></section>

<div class="grid2">
<section><h2>Allocation factors by day</h2><p class="lead">Measured installation total ÷ sum of well-test theoretical rates. The shaded band is the acceptance band.</p>
<div class="chart" id="afchart"></div><div class="legend"><span><span class="lk" style="border-color:var(--s1)"></span>Oil (tank)</span><span><span class="lk" style="border-color:var(--s2)"></span>Water (separator meters)</span><span><span class="lk" style="border-color:var(--s3)"></span>Gas (separator − lift gas)</span></div></section>
<section><h2>Reconciliation consistency by day</h2><p class="lead">Global χ² statistic ÷ critical value at α = {alpha}, before gross-error removal. Above 1.0 means the measurements contradict each other.</p>
<div class="chart" id="chichart"></div><div class="legend"><span><span class="lk" style="border-color:var(--s1);border-top-width:8px"></span>χ² ratio</span><span><span class="lk" style="border-color:var(--text2);border-top-style:dashed"></span>Critical value (1.0)</span></div></section>
</div>

<section><h2>Exception register</h2><p class="lead">Each line is one problem. Repeated daily occurrences are merged; the Excel workbook keeps every daily event.</p>
<div class="filters" id="sevf"><button class="chip" data-sev="" aria-pressed="true">All</button><button class="chip" data-sev="ALARM" aria-pressed="false">Alarms</button><button class="chip" data-sev="WARN" aria-pressed="false">Warnings</button><span style="width:12px"></span><button class="chip" data-layer="" aria-pressed="true">All layers</button>{layer_btns}</div>
<div class="scroll">{reg}</div></section>

<section><h2>Measurements flagged by reconciliation</h2><p class="lead">How often each meter or stream was isolated as a gross error, and its mean deviation from the reconciled value. A meter flagged on many days with a stable deviation has a systematic bias: prove or recalibrate it.</p>{bias}</section>

<section><h2>Well allocation for the period</h2><p class="lead">Theoretical = latest usable test × hours on (SRP wells scaled by SPM). Allocated (AF) = theoretical × installation allocation factor, the API MPMS 20.1 proportional method. Reconciled = DVR estimate, which weighs every measurement by its uncertainty.</p>{wells}</section>

<section><h2>Well performance</h2><p class="lead">How the well parameters reproduce each well's rate. PI is calibrated at every accepted test from the flowing BHP: VLP from THP for flowing wells, fluid level for SRP, PIP or gauge where fitted. Each day the calibrated IPR/VLP, choke, fluid-level and pump-displacement estimators run on that day's THP, CHP, lift gas, fluid level, SPM and fillage. A sustained gap from the test-based rate means the well has changed since its test.</p>{perf}</section>

<section><h2>Well test register</h2><p class="lead">Tests in the period, plus the last test before it for each well. Rejected tests are not used. Provisional tests are used, with wider uncertainty, until a repeat test confirms them.</p><div class="scroll">{tests}</div></section>

<section><h2>Separators</h2><p class="lead">Period averages. Retention is compared with API 12J guidance; gas load is actual gas velocity ÷ the Souders-Brown limit.</p>{seps}</section>

<section><h2>Tank-farm oil balance</h2><p class="lead">Tank production = ΣΔNSV + dispatch NSV, compared with the sum of separator oil after shrinkage. Days blank in ΔNSV had a suspect dip.</p><div class="scroll">{tanks}</div></section>
<section><h2>Reconciliation by day</h2><p class="lead">χ² before and after isolating gross errors.</p><div class="scroll">{dvr}</div></section>

<section><h2>Method</h2>
<ol class="m">
<li><b>Data quality:</b> completeness, plausibility ranges, frozen transmitters (identical for ≥ N days), robust spikes (median ± k·MAD), cross-signal consistency (status vs hours, THP ≤ FLP, gas-lift injection), and meter proving status.</li>
<li><b>Well tests:</b> stock-tank rates from the test-separator meters (meter factor, oil-leg BS&amp;W, shrinkage). Each test is checked for duration, stability (CV of the last hours, trend), SRP volumetric efficiency (PD = 0.1166·S·N·D² bbl/d), negative formation gas on gas-lift wells, and change against the previous accepted test (SRP normalised for SPM; GOR tolerance widened for the lift-gas subtraction). A Gilbert choke model calibrated on each test acts as a virtual flow meter.</li>
<li><b>Well performance:</b> Standing PVT, a composite PI/Vogel IPR and Beggs &amp; Brill tubing flow. PI is calibrated at each test. Daily rate estimators: nodal (THP, lift gas), Gilbert choke, IPR at Pwf from fluid level, PIP or gauge, and SRP displacement × VE × fillage. The estimators are combined by inverse variance and added to DVR as a liquid measurement for each well. Wellhead BS&amp;W samples enter as water-cut measurements. Health checks: PI change, low submergence or fillage, fluid-level rise at constant SPM, gas-lift injection-pressure shift, estimators disagreeing (choke vs nodal points to a choke restriction or erosion; displacement vs inflow points to a pump leak), and a well that cannot flow at its THP.</li>
<li><b>Separators:</b> meters converted to 15 °C stock-tank volumes, with uncertainty propagated (GUM). Liquid retention is checked against API 12J and gas load against the Souders-Brown limit.</li>
<li><b>Tanks:</b> TOV → GOV (CTSh, shell temperature (7T<sub>L</sub>+T<sub>A</sub>)/8) → GSV (CTL, ASTM D1250 Table 54A) → NSV (S&amp;W). Single-dip errors are caught by their +x/−x signature on consecutive days.</li>
<li><b>Reconciliation (DVR):</b> weighted least squares on the oil, water and gas balances at every separator, the tank farm and the gas disposal and lift-gas headers. Unmeasured streams are eliminated by projection. Tests used: χ² global test, per-measurement z-test, and a group likelihood-ratio test with serial elimination and an equivalence check (VDI 2048; Narasimhan &amp; Jordache).</li>
<li><b>Allocation:</b> the proportional allocation-factor method (API MPMS 20.1) and the DVR-reconciled well rates, side by side, with AF acceptance bands at installation and separator level.</li>
</ol></section>
</main>
<script type="application/json" id="d">{data}</script>
<script>
(function(){{
const D=JSON.parse(document.getElementById('d').textContent);
const css=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const NS='http://www.w3.org/2000/svg';
function el(t,a,p){{const e=document.createElementNS(NS,t);for(const k in a)e.setAttribute(k,a[k]);if(p)p.appendChild(e);return e}}
function tipRows(tip,title,rows){{tip.textContent='';const h=document.createElement('div');h.style.color='var(--text2)';h.style.marginBottom='4px';h.textContent=title;tip.appendChild(h);
 rows.forEach(r=>{{const d=document.createElement('div');d.className='r';const l=document.createElement('span');const k=document.createElement('span');k.className='k';k.style.borderColor=r.c;l.appendChild(k);l.appendChild(document.createTextNode(' '+r.n));const b=document.createElement('b');b.textContent=r.v;d.appendChild(l);d.appendChild(b);tip.appendChild(d)}})}}
function frame(id,W,H,ymin,ymax,ticks){{const host=document.getElementById(id);host.textContent='';const svg=el('svg',{{viewBox:`0 0 ${{W}} ${{H}}`}},host);
 const m={{l:44,r:12,t:10,b:28}};const x=i=>m.l+(W-m.l-m.r)*(D.dates.length<2?0.5:i/(D.dates.length-1));const y=v=>m.t+(H-m.t-m.b)*(1-(v-ymin)/(ymax-ymin));
 ticks.forEach(t=>{{el('line',{{x1:m.l,x2:W-m.r,y1:y(t),y2:y(t),class:'gl'}},svg);const tx=el('text',{{x:m.l-6,y:y(t)+4,'text-anchor':'end',class:'ax'}},svg);tx.textContent=t.toFixed(t<10?2:0)}});
 D.dates.forEach((d,i)=>{{if(i%5===0||i===D.dates.length-1){{if(i!==D.dates.length-1&&D.dates.length-1-i<3)return;const tx=el('text',{{x:x(i),y:H-8,'text-anchor':i===D.dates.length-1?'end':'middle',class:'ax'}},svg);tx.textContent=d.slice(5)}}}});
 const tip=document.createElement('div');tip.className='tip';host.appendChild(tip);return {{svg,x,y,m,tip,host,W,H}}}}
function afChart(){{const vals=[].concat(D.af.oil,D.af.water,D.af.gas).filter(v=>v!=null);let lo=Math.min(0.8,...vals),hi=Math.max(1.2,...vals);lo=Math.floor(lo*10)/10;hi=Math.ceil(hi*10)/10;
 const step=(hi-lo)>1?0.25:0.1;const ticks=[];for(let t=lo;t<=hi+1e-9;t+=step)ticks.push(Math.round(t*100)/100);
 const F=frame('afchart',640,280,lo,hi,ticks);const {{svg,x,y,tip,host,W,H,m}}=F;
 el('rect',{{x:m.l,width:W-m.l-m.r,y:y(D.band[1]),height:y(D.band[0])-y(D.band[1]),fill:css('--band')}},svg);
 const S=[['oil','--s1','Oil'],['water','--s2','Water'],['gas','--s3','Gas']];
 S.forEach(([k,c])=>{{let p='';D.af[k].forEach((v,i)=>{{if(v==null)return;p+=(p&&D.af[k][i-1]!=null?'L':'M')+x(i)+','+y(v)}});el('path',{{d:p,fill:'none',stroke:css(c),'stroke-width':2,'stroke-linejoin':'round'}},svg)}});
 const hair=el('line',{{y1:m.t,y2:H-m.b,stroke:css('--muted'),'stroke-width':1,opacity:0}},svg);const dots=S.map(([k,c])=>el('circle',{{r:4,fill:css(c),stroke:css('--surface'),'stroke-width':2,opacity:0}},svg));
 const hit=el('rect',{{x:m.l,y:m.t,width:W-m.l-m.r,height:H-m.t-m.b,fill:'transparent',tabindex:0}},svg);
 function show(i){{hair.setAttribute('x1',x(i));hair.setAttribute('x2',x(i));hair.setAttribute('opacity',1);S.forEach(([k],j)=>{{const v=D.af[k][i];if(v==null){{dots[j].setAttribute('opacity',0);return}}dots[j].setAttribute('cx',x(i));dots[j].setAttribute('cy',y(v));dots[j].setAttribute('opacity',1)}});
  tipRows(tip,D.dates[i],S.map(([k,c,n])=>({{n,c:css(c),v:D.af[k][i]==null?'n/a':D.af[k][i].toFixed(3)}})));tip.style.display='block';const r=host.getBoundingClientRect();const px=x(i)/W*r.width;tip.style.left=Math.min(px+12,r.width-170)+'px';tip.style.top='8px'}}
 hit.addEventListener('pointermove',e=>{{const r=svg.getBoundingClientRect();const fx=(e.clientX-r.left)/r.width*W;let i=Math.round((fx-m.l)/(W-m.l-m.r)*(D.dates.length-1));show(Math.max(0,Math.min(D.dates.length-1,i)))}});
 hit.addEventListener('pointerleave',()=>{{tip.style.display='none';hair.setAttribute('opacity',0);dots.forEach(d=>d.setAttribute('opacity',0))}});
 let ki=0;hit.addEventListener('keydown',e=>{{if(e.key==='ArrowRight')ki=Math.min(D.dates.length-1,ki+1);else if(e.key==='ArrowLeft')ki=Math.max(0,ki-1);else return;show(ki);e.preventDefault()}});hit.addEventListener('focus',()=>show(ki))}}
function chiChart(){{const vals=D.ratio.filter(v=>v!=null);const hi=Math.max(1.5,Math.ceil(Math.max(...vals)));const st=hi>6?2:(hi>3?1:0.5);const ticks=[];for(let t=0;t<=hi+1e-9;t+=st)ticks.push(t);
 const F=frame('chichart',640,280,0,hi,ticks);const {{svg,x,y,tip,host,W,H,m}}=F;const n=D.dates.length;const bw=Math.max(4,(W-m.l-m.r)/n-3);
 D.ratio.forEach((v,i)=>{{if(v==null)return;const g=el('g',{{tabindex:0}},svg);const top=y(v),base=y(0);const h=Math.max(base-top,1);
  el('path',{{d:`M${{x(i)-bw/2}},${{base}} V${{top+Math.min(4,h)}} Q${{x(i)-bw/2}},${{top}} ${{x(i)-bw/2+Math.min(4,bw/2)}},${{top}} H${{x(i)+bw/2-Math.min(4,bw/2)}} Q${{x(i)+bw/2}},${{top}} ${{x(i)+bw/2}},${{top+Math.min(4,h)}} V${{base}} Z`,fill:css('--s1')}},g);
  el('rect',{{x:x(i)-bw/2-1.5,width:bw+3,y:m.t,height:H-m.t-m.b,fill:'transparent'}},g);
  const sh=()=>{{const rows=[{{n:'χ² ratio',c:css('--s1'),v:v.toFixed(2)}}];if(D.elim[i])rows.push({{n:'isolated',c:css('--crit'),v:D.elim[i]}});if(D.amb[i])rows.push({{n:'also',c:css('--warn'),v:'ambiguous'}});tipRows(tip,D.dates[i],rows);tip.style.display='block';const r=host.getBoundingClientRect();tip.style.left=Math.min(x(i)/W*r.width+12,r.width-200)+'px';tip.style.top='8px';g.style.opacity=.75}};
  g.addEventListener('pointerenter',sh);g.addEventListener('focus',sh);const hd=()=>{{tip.style.display='none';g.style.opacity=1}};g.addEventListener('pointerleave',hd);g.addEventListener('blur',hd)}});
 el('line',{{x1:m.l,x2:W-m.r,y1:y(1),y2:y(1),stroke:css('--text2'),'stroke-dasharray':'5 4','stroke-width':1.5}},svg)}}
function draw(){{afChart();chiChart()}}draw();
matchMedia('(prefers-color-scheme: dark)').addEventListener('change',draw);
// register filters
const st={{sev:'',layer:''}};document.querySelectorAll('#sevf .chip').forEach(b=>b.addEventListener('click',()=>{{const key=b.hasAttribute('data-sev')?'sev':'layer';st[key]=b.getAttribute('data-'+key);
 document.querySelectorAll('#sevf .chip[data-'+key+']').forEach(o=>o.setAttribute('aria-pressed',o===b?'true':'false'));
 document.querySelectorAll('table.reg tbody tr').forEach(r=>{{r.style.display=((!st.sev||r.dataset.sev===st.sev)&&(!st.layer||r.dataset.layer===st.layer))?'':'none'}})}}));
document.querySelectorAll('#sevf .chip[data-layer]:not([aria-pressed])').forEach(b=>b.setAttribute('aria-pressed','false'));
}})();
</script></body></html>"""
