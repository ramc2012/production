import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, Row } from "../api";
import { Chart } from "../components/Chart";
import { DataTable } from "../components/DataTable";
import { Card, ErrorBox, KV, Loading, Modal, PageHead, Sev, State, Tabs, useToast } from "../components/ui";
import { useApp, useRunQuery } from "../context";
import { day, human, int, LAYER, LIFT, n, pct, signed, u95 } from "../fmt";
import { WellWhatIf } from "./Twin";

export function Wells() {
  const q = useRunQuery<Row>("/wells");
  const nav = useNavigate();
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data!;
  const tot = d.wells.reduce((s: number, w: Row) => s + (w.dvr_oil_m3 ?? 0), 0);
  return (
    <div className="stack">
      <PageHead title="Wells" sub={<>Production day {day(d.run.period_end)} · reconciled oil {n(tot, 1)} m³ · ± values are U95 (k = 2)</>} />
      <Card>
        <DataTable rows={d.wells} search csvName="wells" rowKey={(r) => r.well_id} onRow={(r) => nav(`/wells/${r.well_id}`)}
                   cols={[
                     { key: "status", label: "Status", render: (r) => <Sev s={r.status} />, sort: (r) => ({ ALARM: 0, WARN: 1 } as Row)[r.status] ?? 2 },
                     { key: "well_id", label: "Well" },
                     { key: "lift", label: "Lift", render: (r) => LIFT[r.lift] ?? r.lift },
                     { key: "separator", label: "Sep." },
                     { key: "hours_on", label: "Hours", num: true, render: (r) => n(r.hours_on, 1) },
                     { key: "dvr_oil_m3", label: "Oil, reconciled m³", num: true, render: (r) => <>{n(r.dvr_oil_m3, 2)} <span className="muted">{u95(r.dvr_oil_sd_m3, 2)}</span></> },
                     { key: "theo_oil_m3", label: "Oil, test-based m³", num: true, render: (r) => n(r.theo_oil_m3, 2) },
                     { key: "prop_oil_m3", label: "Oil, AF m³", num: true, render: (r) => n(r.prop_oil_m3, 2) },
                     { key: "wc", label: "WC", num: true, render: (r) => pct(r.wc, 0) },
                     { key: "vfm_liquid_m3d", label: "Well-param. liquid m³/d", num: true, render: (r) => <>{n(r.vfm_liquid_m3d, 1)} <span className="muted">{u95(r.vfm_sd_m3d, 1)}</span></> },
                     { key: "test_status", label: "Test", render: (r) => <State s={r.test_status} /> },
                     { key: "test_age_d", label: "Test age d", num: true, render: (r) => int(r.test_age_d) },
                     { key: "tuning", label: "Twin tuning", num: true, render: (r) => n(r.tuning, 2), title: "PI/VE multiplier from synchronisation; outside 0.7–1.3 suggests stale PI or Pr" },
                     { key: "open_issues", label: "Issues", num: true },
                   ]} />
      </Card>
    </div>
  );
}

export function OverrideDialog({ test, onClose }: { test: Row; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [status, setStatus] = useState(test.override?.status ?? (test.status === "REJECTED" ? "ACCEPTED" : "REJECTED"));
  const [reason, setReason] = useState("");
  const set = useMutation({
    mutationFn: () => api.post(`/tests/${test.test_id}/override`, { status, reason }),
    onSuccess: () => { toast("Override saved – it takes effect in the next run"); qc.invalidateQueries(); onClose(); },
    onError: (e: Error) => toast(e.message, true),
  });
  const clear = useMutation({
    mutationFn: () => api.del(`/tests/${test.test_id}/override`, { reason: reason || "cleared" }),
    onSuccess: () => { toast("Override cleared"); qc.invalidateQueries(); onClose(); },
    onError: (e: Error) => toast(e.message, true),
  });
  return (
    <Modal title={`Override test ${test.test_id}`} onClose={onClose}
           actions={<>
             {test.override && <button className="danger" onClick={() => clear.mutate()}>Clear override</button>}
             <button onClick={onClose}>Cancel</button>
             <button className="primary" disabled={reason.trim().length < 5 || set.isPending} onClick={() => set.mutate()}>Save override</button>
           </>}>
      <div className="stack">
        <KV items={[["Well", test.well_id], ["Date", day(test.test_date)], ["Engine verdict", <State s={test.engine_status ?? test.status} />],
                    ["Reasons", test.reasons || "–"]]} />
        <label className="f">New status
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="ACCEPTED">Accepted: use for allocation</option>
            <option value="PROVISIONAL">Provisional: use with wider uncertainty</option>
            <option value="REJECTED">Rejected: do not use</option>
          </select>
        </label>
        <label className="f">Reason (required, kept in the audit log)
          <textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)} style={{ fontFamily: "inherit", fontSize: 13 }}
                    placeholder="e.g. Test witnessed; SPM in the log sheet was a typing error" />
        </label>
        <div className="small muted">The override is applied when the next run starts; earlier runs keep their results.</div>
      </div>
    </Modal>
  );
}

export function testCols(onOverride?: (t: Row) => void) {
  return [
    { key: "test_date", label: "Date", render: (r: Row) => day(r.test_date) },
    { key: "test_id", label: "Test" },
    { key: "well_id", label: "Well", render: (r: Row) => <Link to={`/wells/${r.well_id}`} onClick={(e) => e.stopPropagation()}>{r.well_id}</Link> },
    { key: "status", label: "Status", render: (r: Row) => (
      <span className="row" style={{ gap: 4 }}><State s={r.status} />{r.override && <span className="badge s-info" title={`${r.override.by}: ${r.override.reason}`}>override{r.override_pending ? " (next run)" : ""}</span>}</span>) },
    { key: "duration_h", label: "Dur. h", num: true, render: (r: Row) => n(r.duration_h, 1) },
    { key: "oil_m3d", label: "Oil m³/d", num: true, render: (r: Row) => n(r.oil_m3d, 2) },
    { key: "liquid_m3d", label: "Liquid m³/d", num: true, render: (r: Row) => n(r.liquid_m3d, 2) },
    { key: "wc", label: "WC", num: true, render: (r: Row) => pct(r.wc, 1) },
    { key: "gor", label: "GOR Sm³/m³", num: true, render: (r: Row) => n(r.gor, 0) },
    { key: "thp_ksc", label: "THP ksc", num: true, render: (r: Row) => n(r.thp_ksc, 1) },
    { key: "choke_64ths", label: "Choke /64", num: true, render: (r: Row) => n(r.choke_64ths, 0) },
    { key: "spm", label: "SPM", num: true, render: (r: Row) => n(r.spm, 1) },
    { key: "srp_ve", label: "VE", num: true, render: (r: Row) => pct(r.srp_ve, 0) },
    { key: "pi", label: "PI", num: true, render: (r: Row) => n(r.pi, 3), title: "Productivity index m³/d per ksc" },
    { key: "reasons", label: "Reasons", wrap: true, render: (r: Row) => <span className="text2">{r.reasons}</span> },
    ...(onOverride ? [{ key: "_o", label: "", render: (r: Row) => <button className="sm" onClick={(e) => { e.stopPropagation(); onOverride(r); }}>Override…</button> }] : []),
  ];
}

const ESTIMATORS: [string, string][] = [["est_nodal", "Nodal (IPR+VLP)"], ["est_choke", "Choke"], ["est_ipr_fl", "IPR at fluid level"], ["est_srp_pd", "Pump displacement"]];

export function WellDetail() {
  const { id } = useParams();
  const { can } = useApp();
  const q = useRunQuery<Row>(`/wells/${id}`);
  const [tab, setTab] = useState<"prod" | "tests" | "perf" | "fc" | "twin" | "find">("prod");
  const [ovr, setOvr] = useState<Row | null>(null);
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data!;
  const w = d.well;
  const daily: Row[] = d.daily.map((r: Row) => ({
    ...r,
    dvr_lo: typeof r.dvr_oil === "number" && typeof r.dvr_oil_sd === "number" ? r.dvr_oil - 2 * r.dvr_oil_sd : null,
    dvr_hi: typeof r.dvr_oil === "number" && typeof r.dvr_oil_sd === "number" ? r.dvr_oil + 2 * r.dvr_oil_sd : null,
    dvr_liq: (r.dvr_oil ?? 0) + (r.dvr_water ?? 0),
    vfm_day: typeof r.vfm_comb === "number" ? (r.vfm_comb * r.hours_on) / 24 : null,
  }));
  const last = daily[daily.length - 1] ?? {};
  const tw = d.twin;
  const isSrp = w.lift === "srp";
  const fcHist = daily.map((r) => ({ date: r.date, hist: r.hours_on > 0 ? (r.dvr_oil ?? 0) / (r.hours_on / 24) : null }));
  const fcRows = [...fcHist, ...(tw?.forecast_daily ?? []).slice(0, 60).map((f: Row) => ({ date: f.date, p10: f.p10, p50: f.p50, p90: f.p90 }))];
  return (
    <div className="stack">
      <PageHead title={w.id}
                sub={<>{LIFT[w.lift] ?? w.lift} → {w.separator}{w.lift_gas_meter ? ` · lift-gas meter ${w.lift_gas_meter}` : ""} · {d.open_issues} open issue(s)</>}
                right={<Link to="/wells">← All wells</Link>} />
      <div className="tiles">
        <div className="tile"><div className="tl">Oil, reconciled ({day(last.date)})</div><div className="tv">{n(last.dvr_oil, 2)}<small>m³</small></div><div className="ts">U95 {u95(last.dvr_oil_sd, 2)} m³ · test-based {n(last.theo_oil, 2)}</div></div>
        <div className="tile"><div className="tl">Water cut (test)</div><div className="tv">{pct(last.wc, 0)}</div><div className="ts">wellhead sample {last.wc_sample != null ? pct(last.wc_sample, 0) : "–"}</div></div>
        <div className="tile"><div className="tl">Active test</div><div className="tv" style={{ fontSize: 16 }}>{last.test_id ?? "–"}</div><div className="ts"><State s={last.test_status} /> · {int(last.test_age_d)} days old</div></div>
        <div className="tile"><div className="tl">Well-parameter liquid</div><div className="tv">{n(last.vfm_comb, 1)}<small>m³/d</small></div><div className="ts">{last.vfm_methods ?? "no estimators"} · U95 {u95(last.vfm_sd, 1)}</div></div>
        {d.performance && <div className="tile"><div className="tl">Productivity index</div><div className="tv">{n(d.performance.pi_last, 3)}</div><div className="ts">m³/d per ksc · previous {n(d.performance.pi_prev, 3)} · Pr {n(d.performance.pr_ksc, 1)} ksc</div></div>}
        {tw?.l0 && <div className="tile"><div className="tl">Twin tuning factor</div><div className="tv">{n(tw.l0.tuning, 2)}</div><div className="ts">{tw.l0.tuning_target?.toUpperCase()} multiplier · healthy 0.7–1.3</div></div>}
      </div>
      <Tabs value={tab} onChange={setTab} tabs={[["prod", "Production"], ["tests", `Tests (${d.tests.length})`], ["perf", "Performance"], ["fc", "Forecast"], ["twin", "Twin & what-if"], ["find", `Findings (${d.findings.length})`]]} />

      {tab === "prod" && (
        <div className="stack">
          <Card title="Daily oil" lead="Reconciled (with its U95 band), test-based theoretical and proportionally allocated (API MPMS 20.1). The well-parameter estimate is independent of the test.">
            <Chart data={daily} x="date" xFmt={day} yLabel="m³"
                   bands={[{ lo: "dvr_lo", hi: "dvr_hi", color: "var(--s1)", label: "Reconciled U95" }]}
                   series={[{ key: "dvr_oil", label: "Reconciled", color: "var(--s1)" }, { key: "theo_oil", label: "Test-based", color: "var(--s3)", dash: true },
                            { key: "prop_oil", label: "Allocated (AF)", color: "var(--s2)" }]} />
          </Card>
          <Card title="Daily liquid" lead="Reconciled liquid against the well-parameter (VFM) estimate scaled to hours on.">
            <Chart data={daily} x="date" xFmt={day} yLabel="m³"
                   series={[{ key: "dvr_liq", label: "Reconciled liquid", color: "var(--s1)" }, { key: "vfm_day", label: "Well-parameter estimate", color: "var(--s2)", type: "dots" },
                            { key: "liquid", label: "Test-based liquid", color: "var(--s3)", dash: true }]} />
          </Card>
          <Card title="Daily table">
            <DataTable rows={daily} csvName={`${w.id}_daily`} tall cols={[
              { key: "date", label: "Day", render: (r) => day(r.date) }, { key: "hours_on", label: "Hours", num: true, render: (r) => n(r.hours_on, 1) },
              { key: "thp_ksc", label: "THP ksc", num: true, render: (r) => n(r.thp_ksc, 1) }, { key: "choke_64ths", label: "Choke", num: true, render: (r) => n(r.choke_64ths, 0) },
              { key: "lift_gas_sm3", label: "Lift gas Sm³", num: true, render: (r) => int(r.lift_gas_sm3) }, { key: "spm", label: "SPM", num: true, render: (r) => n(r.spm, 1) },
              { key: "theo_oil", label: "Theo oil", num: true, render: (r) => n(r.theo_oil, 2) }, { key: "prop_oil", label: "AF oil", num: true, render: (r) => n(r.prop_oil, 2) },
              { key: "dvr_oil", label: "DVR oil", num: true, render: (r) => <>{n(r.dvr_oil, 2)} <span className="muted">{u95(r.dvr_oil_sd, 2)}</span></> },
              { key: "dvr_water", label: "DVR water", num: true, render: (r) => n(r.dvr_water, 2) }, { key: "dvr_gas", label: "DVR gas Sm³", num: true, render: (r) => int(r.dvr_gas) },
              { key: "test_id", label: "Test" }]} />
          </Card>
        </div>
      )}
      {tab === "tests" && (
        <Card title="Well tests" lead="Engine verdict per test. Engineers can override with a reason; the next run uses the override.">
          <DataTable rows={d.tests} csvName={`${w.id}_tests`} cols={testCols(can("engineer") ? setOvr : undefined).filter((c) => c.key !== "well_id")} />
        </Card>
      )}
      {tab === "perf" && (
        <div className="stack">
          {d.performance && (
            <Card title="Well performance (latest)">
              <KV items={[["Reservoir pressure Pr", `${n(d.performance.pr_ksc, 1)} ksc`], ["Bubble point Pb", `${n(d.performance.pb_ksc, 1)} ksc`],
                          ["PI previous → last test", `${n(d.performance.pi_prev, 3)} → ${n(d.performance.pi_last, 3)} m³/d/ksc`],
                          ["Flowing BHP Pwf", `${n(d.performance.pwf_ksc, 1)} ksc (drawdown ${pct(d.performance.drawdown, 0)})`],
                          ["Estimators", d.performance.methods ?? "–"], ["VFM deviation, mean / last", `${pct(d.performance.vfm_dev_mean, 0)} / ${pct(d.performance.vfm_dev_last, 0)}`],
                          ...(isSrp ? [["Dynamic fluid level", `${n(d.performance.dfl_m, 0)} m`], ["Submergence", `${n(d.performance.submergence_m, 0)} m`], ["Pump fillage", pct(d.performance.fillage, 0, false)]] as [string, string][] : []),
                          ...(w.lift === "gas_lift" ? [["Injection pressure", `${n(d.performance.inj_p, 1)} ksc`]] as [string, string][] : []),
                          ["WC test / sample", `${pct(d.performance.wc_test, 0)} / ${pct(d.performance.wc_sample, 0)}`]]} />
            </Card>
          )}
          <Card title="Daily rate estimators" lead="Each estimator runs on that day's well parameters, calibrated at the active test. A sustained gap from the test-based rate means the well changed since its test.">
            <Chart data={daily} x="date" xFmt={day} yLabel="m³/d liquid"
                   series={[{ key: "liquid", label: "Test-based", color: "var(--s3)", dash: true },
                            ...ESTIMATORS.filter(([k]) => daily.some((r) => typeof r[k] === "number")).slice(0, 2)
                              .map(([k, l], i) => ({ key: k, label: l, color: i ? "var(--s2)" : "var(--s1)" }))]} />
          </Card>
          {isSrp && (
            <Card title="Dynamic fluid level" lead="A rising fluid level at unchanged SPM points to a pump problem rather than the reservoir.">
              <Chart data={daily} x="date" xFmt={day} yLabel="m below surface" series={[{ key: "dfl_m", label: "Fluid level", color: "var(--s1)", type: "dots" }]} />
            </Card>
          )}
          <Card title="Tubing-head pressure">
            <Chart data={daily} x="date" xFmt={day} yLabel="ksc" series={[{ key: "thp_ksc", label: "THP", color: "var(--s1)" }]} />
          </Card>
        </div>
      )}
      {tab === "fc" && (
        <Card title="Oil-rate forecast (L3)"
              lead={tw?.forecast ? <>Arps decline fitted on {tw.forecast.points} points ({tw.forecast.method}{tw.forecast.regime_change ? `, regime change ${day(tw.forecast.regime_change)}` : ""}). Decline {n(tw.forecast.decline_pct_per_year, 1)} %/yr. Band: P10–P90. Accuracy is not yet back-tested.</> : "Run a twin run to get a forecast."}>
          {tw?.forecast_daily?.length ? (
            <Chart data={fcRows} x="date" xFmt={day} yLabel="m³/d oil" vline={{ x: tw.forecast_daily[0]?.date, label: "forecast →" }}
                   bands={[{ lo: "p10", hi: "p90", color: "var(--s1)", label: "P10–P90" }]}
                   series={[{ key: "hist", label: "Reconciled rate", color: "var(--s3)", type: "dots" }, { key: "p50", label: "Forecast P50", color: "var(--s1)" }]} />
          ) : <div className="empty">No forecast.</div>}
        </Card>
      )}
      {tab === "twin" && (
        <div className="stack">
          {tw?.l0 ? (
            <Card title="Twin model of this well (L0/L1)">
              <KV items={[["Modelled", tw.l0.modelled ? "yes (IPR + VLP + choke/pump)" : "no: falls back to the test rate"], ["Calibration test", tw.l0.test_id || "–"],
                          ["Pr / Pb", `${n(tw.l0.pr_ksc, 1)} / ${n(tw.l0.pb_ksc, 1)} ksc`], ["PI", `${n(tw.l0.pi_m3d_per_ksc, 3)} m³/d/ksc × tuning ${n(tw.l0.tuning, 2)}`],
                          ["Choke / calibration", `${n(tw.l0.choke_64ths, 0)}/64 · factor ${n(tw.l0.choke_cal, 3)}`], ["Lift gas", `${int(tw.l0.lift_gas_sm3d)} Sm³/d`],
                          ["SPM / VE", `${n(tw.l0.spm, 1)} / ${pct(tw.l0.srp_ve, 0)}`], ["WC / GOR", `${pct(tw.l0.wc, 0)} / ${n(tw.l0.gor_sm3_m3, 0)} Sm³/m³`],
                          ["Twin liquid now", `${n(tw.live?.twin_liquid, 1)} m³/d (reconciled ${n(tw.live?.dvr_liquid, 1)})`]]} />
            </Card>
          ) : <div className="empty">No twin run yet.</div>}
          {tw?.recommendations?.length > 0 && (
            <Card title="Advice for this well (L4)" right={<Link to="/twin/recommendations">All recommendations</Link>}>
              {tw.recommendations.map((r: Row) => (
                <div key={r.id} className="row" style={{ marginBottom: 6 }}>
                  <State s={r.status} />
                  <span>{Object.entries(r.proposed).map(([k, v]) => `${human(k)} ${n(r.current[k], 0)} → ${n(v as number, 0)}`).join("; ") || r.rationale}</span>
                  <span className="muted">{signed(r.predicted_d_oil_m3d, 2)} m³/d</span>
                </div>
              ))}
            </Card>
          )}
          {tw?.gl_curve?.length > 0 && (
            <Card title="Gas-lift performance curve" lead="Oil from the calibrated well model at each lift-gas rate. The marginal gain falls as injection rises.">
              <Chart data={tw.gl_curve} x="lift_gas" xNumeric xFmt={(v) => int(v)} yLabel="m³/d oil"
                     series={[{ key: "oil", label: "Oil", color: "var(--s1)" }]} />
            </Card>
          )}
          {tw && <WellWhatIf wellId={w.id} lift={w.lift} />}
        </div>
      )}
      {tab === "find" && (
        <Card>
          <DataTable rows={d.findings} search csvName={`${w.id}_findings`} cols={[
            { key: "date", label: "Day", render: (r) => day(r.date) }, { key: "severity", label: "Severity", render: (r) => <Sev s={r.severity} /> },
            { key: "layer", label: "Layer", render: (r) => LAYER[r.layer] ?? r.layer }, { key: "check", label: "Check", render: (r) => human(r.check) },
            { key: "message", label: "Finding", wrap: true }, { key: "action", label: "Action", wrap: true, render: (r) => <span className="text2">{r.action}</span> }]} />
        </Card>
      )}
      {ovr && <OverrideDialog test={ovr} onClose={() => setOvr(null)} />}
    </div>
  );
}
