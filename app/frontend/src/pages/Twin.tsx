import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, Row } from "../api";
import { Chart } from "../components/Chart";
import { DataTable } from "../components/DataTable";
import { Card, Chips, ErrorBox, KV, Loading, Modal, PageHead, Progress, Sev, State, Tabs, Tile, useToast } from "../components/ui";
import { useApp, useRunQuery } from "../context";
import { day, dt, human, int, LIFT, n, pct, signed } from "../fmt";
import { Ladder } from "./Dashboard";

type Lv = "0" | "1" | "2" | "3" | "4" | "5";

function useTwinTable(name: string) {
  return useRunQuery<Row>(`/twin/tables/${name}`, {}, { kind: "twin" });
}

function Assessment({ rows, level }: { rows: Row[]; level: number }) {
  return (
    <Card title={`L${level} criteria`} lead="DNV-RP-A204 capability assessment of this twin.">
      <DataTable rows={rows.filter((r) => r.level === level)} cols={[
        { key: "status", label: "Status", render: (r) => <State s={r.status} /> }, { key: "criterion", label: "Criterion" },
        { key: "evidence", label: "Evidence", wrap: true }, { key: "gap", label: "Gap / next step", wrap: true, render: (r) => <span className="text2">{r.gap}</span> }]} />
    </Card>
  );
}

function L0({ a }: { a: Row[] }) {
  const t = useTwinTable("L0_wells");
  return (
    <div className="stack">
      <Card title="Asset model: wells" lead="Each well is calibrated at its latest usable test (PI from the test Pwf, choke factor, pump efficiency) and set to the latest operating point.">
        {t.data && <DataTable rows={t.data.rows} csvName="L0_wells" search cols={[
          { key: "well_id", label: "Well", render: (r) => <Link to={`/wells/${r.well_id}`}>{r.well_id}</Link> },
          { key: "lift", label: "Lift", render: (r) => LIFT[r.lift] ?? r.lift }, { key: "on", label: "On", render: (r) => (r.on ? "yes" : "shut in") },
          { key: "modelled", label: "Physics model", render: (r) => (r.modelled ? "IPR + VLP" : "test rate") },
          { key: "pr_ksc", label: "Pr ksc", num: true, render: (r) => n(r.pr_ksc, 1) }, { key: "pb_ksc", label: "Pb ksc", num: true, render: (r) => n(r.pb_ksc, 1) },
          { key: "pi_m3d_per_ksc", label: "PI", num: true, render: (r) => n(r.pi_m3d_per_ksc, 3) },
          { key: "choke_64ths", label: "Choke", num: true, render: (r) => n(r.choke_64ths, 0) },
          { key: "lift_gas_sm3d", label: "Lift gas", num: true, render: (r) => int(r.lift_gas_sm3d) },
          { key: "spm", label: "SPM", num: true, render: (r) => n(r.spm, 1) }, { key: "srp_ve", label: "VE", num: true, render: (r) => pct(r.srp_ve, 0) },
          { key: "wc", label: "WC", num: true, render: (r) => pct(r.wc, 0) }, { key: "gor_sm3_m3", label: "GOR", num: true, render: (r) => n(r.gor_sm3_m3, 0) },
          { key: "perf_depth_m", label: "Perf m", num: true, render: (r) => int(r.perf_depth_m) }, { key: "test_id", label: "Calibration test" }]} />}
      </Card>
      <Assessment rows={a} level={0} />
    </div>
  );
}

function L1({ a, s }: { a: Row[]; s: Row }) {
  const w = useTwinTable("L1_live_wells");
  const f = s.facility ?? {};
  return (
    <div className="stack">
      <div className="tiles">
        <Tile label="Oil (twin steady state)" value={n(f.oil_m3d, 1)} unit="m³/d" />
        <Tile label="Water" value={n(f.water_m3d, 1)} unit="m³/d" />
        <Tile label="Total gas" value={int(f.gas_total_sm3d)} unit="Sm³/d" sub={<>formation {int(f.gas_formation_sm3d)} + lift {int(f.lift_gas_sm3d)}</>} />
        <Tile label="Compressor load" value={pct(f.compressor_load, 0)} sub={<>capacity {int(f.compressor_capacity_sm3d)} Sm³/d · flare {int(f.flare_sm3d)}</>}
              badge={<Sev s={(f.compressor_load ?? 0) > 1 ? "ALARM" : (f.compressor_load ?? 0) > 0.9 ? "WARN" : "OK"} />} />
        <Tile label="Lift gas used / available" value={`${pct((f.lift_gas_sm3d ?? 0) / (f.lift_available_sm3d || 1), 0)}`} sub={<>{int(f.lift_gas_sm3d)} of {int(f.lift_available_sm3d)} Sm³/d</>} />
        <Tile label="Tank farm" value={pct(f.tank_fill, 0)} unit="of safe fill" sub={<>{n(f.tank_inventory_m3, 0)} of {n(f.tank_capacity_m3, 0)} m³</>} />
      </div>
      <Card title="Synchronised state per well" lead="The twin re-tunes each well's PI (or pump VE) until it reproduces that day's reconciled rate. A tuning factor outside 0.7–1.3 points to a stale PI or reservoir pressure.">
        {w.data && <DataTable rows={w.data.rows} csvName="L1_wells" cols={[
          { key: "well_id", label: "Well", render: (r) => <Link to={`/wells/${r.well_id}`}>{r.well_id}</Link> },
          { key: "lift", label: "Lift", render: (r) => LIFT[r.lift] ?? r.lift }, { key: "on", label: "On", render: (r) => (r.on ? "yes" : "no") },
          { key: "thp", label: "THP ksc", num: true, render: (r) => n(r.thp, 1) },
          { key: "dvr_liquid", label: "Reconciled liquid m³", num: true, render: (r) => n(r.dvr_liquid, 1) },
          { key: "twin_liquid", label: "Twin liquid m³/d", num: true, render: (r) => n(r.twin_liquid, 1) },
          { key: "vfm", label: "Well-param. m³/d", num: true, render: (r) => n(r.vfm, 1) },
          { key: "twin_pwf", label: "Twin Pwf ksc", num: true, render: (r) => n(r.twin_pwf, 1) },
          { key: "tuning", label: "Tuning", num: true, render: (r) => <span>{n(r.tuning, 2)} {(r.tuning < 0.7 || r.tuning > 1.3) && <Sev s="WARN" />}</span> }]} />}
      </Card>
      <Assessment rows={a} level={1} />
    </div>
  );
}

function L2({ a }: { a: Row[] }) {
  const f = useRunQuery<Row>("/findings", { severity: "ALARM,WARN" }, { kind: "twin" });
  const byLayer = useMemo(() => {
    const m: Record<string, Row> = {};
    for (const x of f.data?.findings ?? []) {
      const r = (m[x.layer] ??= { layer: x.layer, ALARM: 0, WARN: 0, objects: new Set<string>() });
      r[x.severity]++;
      r.objects.add(x.object_id);
    }
    return Object.values(m).map((r) => ({ ...r, objects: [...r.objects].slice(0, 12).join(", ") }));
  }, [f.data]);
  return (
    <div className="stack">
      <Card title="Diagnostics by layer" lead="The six validation layers are the twin's diagnostic level. Work them in the Exceptions register." right={<Link to="/exceptions">Open exceptions →</Link>}>
        <DataTable rows={byLayer} cols={[{ key: "layer", label: "Layer", render: (r) => human(r.layer) }, { key: "ALARM", label: "Alarm days", num: true },
                                         { key: "WARN", label: "Warning days", num: true }, { key: "objects", label: "Objects", wrap: true }]} />
      </Card>
      <Assessment rows={a} level={2} />
    </div>
  );
}

function L3({ a, s }: { a: Row[]; s: Row }) {
  const inst = useTwinTable("L3_installation");
  const wells = useTwinTable("L3_well_forecast");
  const scen = useTwinTable("L3_scenarios");
  const drift = useTwinTable("L3_meter_drift");
  const scenRows = useMemo(() => {
    const m: Record<number, Row> = {};
    for (const r of scen.data?.rows ?? []) (m[r.hour] ??= { hour: r.hour })[r.scenario] = r.fill_frac * 100;
    return Object.values(m).sort((x, y) => x.hour - y.hour);
  }, [scen.data]);
  const names = [...new Set((scen.data?.rows ?? []).map((r: Row) => r.scenario))] as string[];
  const colors = ["var(--s1)", "var(--s2)", "var(--s3)"];
  return (
    <div className="stack">
      <div className="banner info">Forecast accuracy is not yet demonstrated: it needs 6–12 months of reconciled history for back-testing.</div>
      <Card title="Installation oil forecast" lead="Sum of the per-well decline forecasts; the band is P10–P90 and widens with the horizon.">
        {inst.data && <Chart data={inst.data.rows} x="date" xFmt={day} yLabel="m³/d"
                             bands={[{ lo: "p10", hi: "p90", color: "var(--s1)", label: "P10–P90" }]}
                             series={[{ key: "p50", label: "P50", color: "var(--s1)" }]} />}
      </Card>
      <div className="grid2">
        <Card title="72-hour tank-farm look-ahead" lead="Tank fill (% of safe fill) by scenario, from the current inventory and the twin's production.">
          {scen.data && <Chart data={scenRows} x="hour" xNumeric xFmt={(v) => `${v} h`} yFmt={(v) => `${v.toFixed(0)} %`} refLines={[{ y: 100, label: "safe fill" }]}
                               series={names.map((nm, i) => ({ key: nm, label: nm, color: colors[i % 3] }))} />}
        </Card>
        <Card title="Look-ahead summary">
          <DataTable rows={s.lookahead ?? []} cols={[{ key: "scenario", label: "Scenario" }, { key: "max_fill", label: "Max fill", num: true, render: (r) => pct(r.max_fill, 0) },
            { key: "hours_to_safe_fill", label: "Hours to safe fill", num: true, render: (r) => n(r.hours_to_safe_fill, 0) },
            { key: "flare_sm3", label: "Flare Sm³", num: true, render: (r) => int(r.flare_sm3) }]} />
          <h3>Meter drift projection</h3>
          {drift.data && <DataTable rows={drift.data.rows} empty="No drifting meter." cols={[
            { key: "tag", label: "Meter", render: (r) => <Link to={`/meters/${r.tag}`}>{r.tag}</Link> }, { key: "stream", label: "Stream" },
            { key: "bias_now_pct", label: "Bias now", num: true, render: (r) => `${signed(r.bias_now_pct, 1)} %` },
            { key: "projected_pct", label: "In 30 d", num: true, render: (r) => `${signed(r.projected_pct, 1)} %` }, { key: "status", label: "Status" }]} />}
        </Card>
      </div>
      <Card title="Per-well forecasts">
        {wells.data && <DataTable rows={wells.data.rows} csvName="L3_wells" cols={[
          { key: "well_id", label: "Well", render: (r) => <Link to={`/wells/${r.well_id}`}>{r.well_id}</Link> }, { key: "method", label: "Method" },
          { key: "regime_change", label: "Regime change", render: (r) => (r.regime_change ? day(r.regime_change) : "") },
          { key: "points", label: "Points", num: true }, { key: "oil_now", label: "Oil now m³/d", num: true, render: (r) => n(r.oil_now, 1) },
          { key: "oil_30d", label: "In 30 d", num: true, render: (r) => n(r.oil_30d, 1) }, { key: "oil_90d", label: "In 90 d", num: true, render: (r) => n(r.oil_90d, 1) },
          { key: "decline_pct_per_year", label: "Decline %/yr", num: true, render: (r) => n(r.decline_pct_per_year, 1) },
          { key: "wc_slope_pp_per_30d", label: "WC trend pp/30 d", num: true, render: (r) => signed(r.wc_slope_pp_per_30d, 1) }]} />}
      </Card>
      <Assessment rows={a} level={3} />
    </div>
  );
}

function L4({ a, s }: { a: Row[]; s: Row }) {
  const gl = useTwinTable("L4_gas_lift");
  const ch = useTwinTable("L4_chokes");
  const srp = useTwinTable("L4_srp");
  const g = s.gl_summary ?? {};
  const pt: Record<string, Row> = Object.fromEntries((s.plan_totals ?? []).map((r: Row) => [r.quantity, r]));
  return (
    <div className="stack">
      <div className="tiles">
        <Tile label="All advice together" value={signed((pt.oil?.plan ?? 0) - (pt.oil?.base ?? 0), 1)} unit="m³/d oil" sub={<>{n(pt.oil?.base, 1)} → {n(pt.oil?.plan, 1)} m³/d</>} />
        <Tile label="Gas-lift optimiser" value={signed(g.d_oil, 2)} unit="m³/d" sub={<>lift {int(g.lift_now)} → {int(g.lift_opt)} Sm³/d (budget {int(g.budget)})</>} />
        <Tile label="Compressor load (plan)" value={pct(pt.compressor_load?.plan, 0)} sub={<>now {pct(pt.compressor_load?.base, 0)} · flare {int(pt.flare?.plan)} Sm³/d</>} />
      </div>
      <div className="row"><Link className="btn" to="/twin/recommendations">Review recommendations →</Link><Link className="btn" to="/twin/whatif">Try your own what-if →</Link></div>
      <Card title="Gas-lift allocation" lead="Lift gas shared by equal marginal oil within the supply and compressor capacity.">
        {gl.data && <DataTable rows={gl.data.rows} csvName="L4_gas_lift" cols={[
          { key: "well_id", label: "Well", render: (r) => <Link to={`/wells/${r.well_id}`}>{r.well_id}</Link> },
          { key: "lift_now", label: "Lift now", num: true, render: (r) => int(r.lift_now) }, { key: "lift_opt", label: "Optimum", num: true, render: (r) => int(r.lift_opt) },
          { key: "oil_now", label: "Oil now", num: true, render: (r) => n(r.oil_now, 2) }, { key: "oil_opt", label: "Oil opt.", num: true, render: (r) => n(r.oil_opt, 2) },
          { key: "d_oil", label: "Δ oil", num: true, render: (r) => signed(r.d_oil, 2) },
          { key: "marginal_now", label: "m³ oil / 1000 Sm³", num: true, render: (r) => n(r.marginal_now, 2) }]} />}
      </Card>
      <div className="grid2">
        <Card title="Choke what-ifs (self-flowing wells)" lead="± 2/64 per well; moves beyond the drawdown guard are not advised.">
          {ch.data && <DataTable rows={ch.data.rows} csvName="L4_chokes" cols={[
            { key: "well_id", label: "Well" }, { key: "choke_now", label: "Now", num: true }, { key: "choke_new", label: "New", num: true },
            { key: "d_oil", label: "Δ oil", num: true, render: (r) => signed(r.d_oil, 2) },
            { key: "drawdown_new", label: "Drawdown", num: true, render: (r) => pct(r.drawdown_new, 0) },
            { key: "within_limits", label: "Allowed", render: (r) => (r.within_limits ? "yes" : <Sev s="WARN" />) }]} />}
        </Card>
        <Card title="SRP speed" lead="SPM for a target pump submergence; held when pump health findings say so.">
          {srp.data && <DataTable rows={srp.data.rows} csvName="L4_srp" cols={[
            { key: "well_id", label: "Well" }, { key: "spm_now", label: "SPM now", num: true, render: (r) => n(r.spm_now, 1) },
            { key: "spm_new", label: "SPM new", num: true, render: (r) => n(r.spm_new, 1) }, { key: "action", label: "Action", wrap: true },
            { key: "submergence_now", label: "Subm. m", num: true, render: (r) => int(r.submergence_now) }, { key: "d_oil", label: "Δ oil", num: true, render: (r) => signed(r.d_oil, 2) }]} />}
        </Card>
      </div>
      <Assessment rows={a} level={4} />
    </div>
  );
}

function Sandbox({ runId }: { runId: number | undefined }) {
  const { can } = useApp();
  const qc = useQueryClient();
  const toast = useToast();
  const [mode, setMode] = useState("autonomous_sim");
  const [cycles, setCycles] = useState(12);
  const [minGain, setMinGain] = useState(0.1);
  const [job, setJob] = useState<number | null>(null);
  const [view, setView] = useState<number | null>(null);
  const list = useQuery({ queryKey: ["/twin/autonomy"], queryFn: () => api.get<Row[]>("/twin/autonomy") });
  const j = useQuery({ queryKey: [`/jobs/${job}`], queryFn: () => api.get<Row>(`/jobs/${job}`), enabled: !!job,
                       refetchInterval: (q) => (q.state.data && ["done", "failed", "cancelled"].includes(q.state.data.status) ? false : 1000) });
  useEffect(() => {
    if (j.data?.status === "done") { qc.invalidateQueries({ queryKey: ["/twin/autonomy"] }); setView(j.data.result?.autonomy_run_id ?? null); }
  }, [j.data?.status]); // eslint-disable-line
  const start = useMutation({
    mutationFn: () => api.post("/twin/autonomy", { run_id: runId, mode, cycles, approve_min_gain_m3d: minGain }),
    onSuccess: (r: Row) => { setJob(r.job_id); toast("Sandbox started on the plant simulator"); },
    onError: (e: Error) => toast(e.message, true),
  });
  const res = useQuery({ queryKey: [`/twin/autonomy/${view}`], queryFn: () => api.get<Row>(`/twin/autonomy/${view}`), enabled: !!view });
  const r = res.data?.result;
  return (
    <Card title="Sandbox: run the guarded loop yourself" lead="Each run builds a fresh plant simulator from this twin (with hidden model–plant mismatch) and lets the supervisor act on it.">
      {can("engineer") && (
        <div className="row" style={{ alignItems: "flex-end" }}>
          <label className="f">Mode
            <select value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="advisory">Advisory: log moves only</option>
              <option value="supervised">Supervised: approver rule decides each move</option>
              <option value="autonomous_sim">Autonomous (simulator)</option>
            </select>
          </label>
          <label className="f">Cycles<input type="number" min={1} max={30} value={cycles} onChange={(e) => setCycles(+e.target.value)} style={{ width: 80 }} /></label>
          {mode === "supervised" && <label className="f">Approve if gain ≥ (m³/d)<input type="number" step={0.05} value={minGain} onChange={(e) => setMinGain(+e.target.value)} style={{ width: 100 }} /></label>}
          <button className="primary" disabled={start.isPending || (j.data && ["queued", "running"].includes(j.data.status))} onClick={() => start.mutate()}>Run on simulator</button>
          {job && j.data && ["queued", "running"].includes(j.data.status) && <button className="danger" onClick={() => api.del(`/jobs/${job}`)}>Cancel</button>}
        </div>
      )}
      {job && j.data && ["queued", "running"].includes(j.data.status) && <div style={{ marginTop: 10 }}><Progress value={j.data.progress} stage={j.data.stage} /></div>}
      {j.data?.status === "failed" && <ErrorBox error={j.data.error} />}
      {j.data?.status === "cancelled" && <div className="banner info" style={{ marginTop: 10 }}>Sandbox run cancelled.</div>}
      <h3>Sandbox runs</h3>
      <DataTable rows={list.data ?? []} rowKey={(x) => x.id} selected={view} onRow={(x) => setView(x.id)} empty="None yet." cols={[
        { key: "id", label: "#", num: true }, { key: "created_at", label: "When", render: (x) => dt(x.created_at) }, { key: "created_by", label: "By" },
        { key: "mode", label: "Mode" }, { key: "gain", label: "Plant oil gain m³/d", num: true, render: (x) => signed(x.gain, 2) },
        { key: "lift", label: "Lift gas Sm³/d", num: true, render: (x) => `${int(x.lift_start)} → ${int(x.lift_end)}` }]} />
      {r && (
        <div className="stack" style={{ marginTop: 14 }}>
          <KV items={[["Mode", res.data!.mode], ["Plant oil", `${n(r.start_oil, 1)} → ${n(r.end_oil, 1)} m³/d (${signed(r.gain, 2)})`],
                      ["Locked wells (trust gate)", Object.entries(r.locked).map(([w, why]) => `${w}: ${why}`).join("; ") || "none"],
                      ["Gas lift frozen", r.gate.freeze_gas_lift ? `yes (${r.gate.gas_lift_reason})` : "no"], ...(r.approver_rule ? [["Approver rule", r.approver_rule]] as [string, string][] : [])]} />
          <Chart data={r.trajectory} x="cycle" xFmt={(c) => `cycle ${c}`} yLabel="m³/d oil"
                 series={[{ key: "plant_oil", label: "Plant (simulator, true)", color: "var(--s1)" }, { key: "measured_oil", label: "Measured", color: "var(--s2)", type: "dots" },
                          { key: "twin_oil", label: "Twin prediction", color: "var(--s3)", dash: true }]} />
          <DataTable rows={r.log} csvName="L5_sandbox_log" tall cols={[
            { key: "cycle", label: "Cycle", num: true }, { key: "well_id", label: "Well" }, { key: "event", label: "Event" },
            { key: "detail", label: "Detail", wrap: true }, { key: "predicted_d_oil", label: "Pred. Δ", num: true, render: (x) => signed(x.predicted_d_oil, 2) },
            { key: "measured_d_oil", label: "Meas. Δ", num: true, render: (x) => signed(x.measured_d_oil, 2) }]} />
        </div>
      )}
    </Card>
  );
}

function L5({ a, s }: { a: Row[]; s: Row }) {
  const traj = useTwinTable("L5_trajectory");
  const log = useTwinTable("L5_audit_log");
  const l5 = s.l5 ?? {};
  const locked = l5.locked ? JSON.parse(l5.locked) : {};
  return (
    <div className="stack">
      <div className="banner sim"><b>Simulation only.</b> The L5 loop acts on a plant <i>simulator</i> built from the twin. FlowTwin has no write path to the DCS, PLCs or any control system, by design.</div>
      <div className="tiles">
        <Tile label="Plant oil (simulator)" value={signed(l5.gain, 1)} unit="m³/d" sub={<>{n(l5.start_oil, 1)} → {n(l5.end_oil, 1)} m³/d</>} />
        <Tile label="Lift gas" value={int(l5.lift_end)} unit="Sm³/d" sub={<>from {int(l5.lift_start)} Sm³/d</>} />
        <Tile label="Locked by trust gate" value={Object.keys(locked).length} sub={Object.entries(locked).map(([w, why]) => `${w} (${why})`).join(", ") || "none"} />
        <Tile label="Mode in the twin run" value={human(l5.mode)} />
      </div>
      <Card title="Closed loop in this twin run" lead="Observe → trust gate → re-synchronise → optimise → guard → act → verify or roll back, on the simulator.">
        {traj.data && <Chart data={traj.data.rows} x="cycle" xFmt={(c) => `cycle ${c}`} yLabel="m³/d oil"
                             series={[{ key: "plant_oil", label: "Plant (simulator)", color: "var(--s1)" }, { key: "measured_oil", label: "Measured", color: "var(--s2)", type: "dots" },
                                      { key: "twin_oil", label: "Twin prediction", color: "var(--s3)", dash: true }]} />}
        {log.data && <DataTable rows={log.data.rows} csvName="L5_audit" cols={[
          { key: "cycle", label: "Cycle", num: true }, { key: "well_id", label: "Well" }, { key: "event", label: "Event" },
          { key: "detail", label: "Detail", wrap: true }, { key: "predicted_d_oil", label: "Pred. Δ", num: true, render: (x) => signed(x.predicted_d_oil, 2) },
          { key: "measured_d_oil", label: "Meas. Δ", num: true, render: (x) => signed(x.measured_d_oil, 2) }]} />}
      </Card>
      <Sandbox runId={s.run?.id} />
      <Assessment rows={a} level={5} />
    </div>
  );
}

export function Twin() {
  const [sp, setSp] = useSearchParams();
  const lv = (sp.get("level") ?? "1") as Lv;
  const q = useRunQuery<Row>("/twin/summary", {}, { kind: "twin" });
  if (q.isLoading) return <Loading />;
  if (q.error) return <div className="stack"><PageHead title="Digital twin" /><ErrorBox error={q.error} /></div>;
  const s = q.data!;
  const tabs: [Lv, string][] = s.levels.map((l: Row) => [String(l.level) as Lv, `L${l.level} ${l.name}`]);
  return (
    <div className="stack">
      <PageHead title="Digital twin" sub={<>{s.facility?.installation} · state as of {day(s.facility?.date)} · twin run #{s.run.id} · DNV-RP-A204 levels 0–5</>} />
      <Ladder levels={s.levels} onPick={(l) => setSp({ level: String(l) })} />
      <Tabs value={lv} onChange={(v) => setSp({ level: v })} tabs={tabs} />
      {lv === "0" && <L0 a={s.assessment} />}
      {lv === "1" && <L1 a={s.assessment} s={s} />}
      {lv === "2" && <L2 a={s.assessment} />}
      {lv === "3" && <L3 a={s.assessment} s={s} />}
      {lv === "4" && <L4 a={s.assessment} s={s} />}
      {lv === "5" && <L5 a={s.assessment} s={s} />}
    </div>
  );
}

// ---------------------------------------------------------------------------- what-if
type Changes = Record<string, Record<string, number>>;

function WhatIfResult({ r }: { r: Row }) {
  const t = r.totals;
  return (
    <div className="stack">
      {r.warnings.length > 0 && <div className="banner warn"><div>{r.warnings.map((w: string) => <div key={w}>◆ {w}</div>)}</div></div>}
      <div className="tiles">
        <Tile label="Oil" value={signed(t.oil.delta, 2)} unit="m³/d" sub={<>{n(t.oil.base, 1)} → {n(t.oil.new, 1)}</>} />
        <Tile label="Water" value={signed(t.water.delta, 2)} unit="m³/d" />
        <Tile label="Lift gas" value={signed(t.lift.delta, 0)} unit="Sm³/d" sub={<>{int(t.lift.base)} → {int(t.lift.new)}</>} />
        <Tile label="Total gas" value={signed(t.gas_total.delta, 0)} unit="Sm³/d" />
        <Tile label="Flare" value={signed(t.flare.delta, 0)} unit="Sm³/d" />
        <Tile label="Compressor load" value={pct(t.compressor_load.new, 0)} sub={<>from {pct(t.compressor_load.base, 0)}</>} />
      </div>
      <div className="small muted">{r.note}</div>
    </div>
  );
}

function Slider(p: { label: string; min: number; max: number; step: number; base: number; value: number | undefined; unit: string; onChange: (v: number | undefined) => void }) {
  const v = p.value ?? p.base;
  return (
    <div className="slider-row">
      <span className="small">{p.label} {p.value !== undefined && p.value !== p.base && <span className="badge s-info">changed</span>}</span>
      <input type="range" min={p.min} max={p.max} step={p.step} value={v} aria-label={p.label}
             onChange={(e) => { const x = +e.target.value; p.onChange(Math.abs(x - p.base) < 1e-9 ? undefined : x); }} />
      <span className="num small">{p.step >= 1 ? int(v) : n(v, 1)} {p.unit}</span>
    </div>
  );
}

function WellControls({ w, ch, set }: { w: Row; ch: Record<string, number>; set: (k: string, v: number | undefined) => void }) {
  return (
    <div className="stack" style={{ gap: 6 }}>
      <div className="row"><b>{w.well_id}</b><span className="muted small">{LIFT[w.lift]}</span>
        <label className="small row" style={{ gap: 4 }}><input type="checkbox" checked={(ch.on ?? 1) === 1} onChange={(e) => set("on", e.target.checked ? undefined : 0)} /> on line</label>
      </div>
      {w.lift !== "srp" && typeof w.choke_64ths === "number" && (
        <Slider label="Choke /64" min={6} max={32} step={1} base={w.choke_64ths} value={ch.choke} unit="/64" onChange={(v) => set("choke", v as number)} />
      )}
      {w.lift === "gas_lift" && (
        <Slider label="Lift gas" min={0} max={16000} step={250} base={Math.round(w.lift_gas_sm3d ?? 0)} value={ch.lift_gas} unit="Sm³/d" onChange={(v) => set("lift_gas", v as number)} />
      )}
      {w.lift === "srp" && typeof w.spm === "number" && (
        <Slider label="SPM" min={3} max={12} step={0.5} base={w.spm} value={ch.spm} unit="" onChange={(v) => set("spm", v as number)} />
      )}
    </div>
  );
}

function useWhatIf(changes: Changes, runId?: number) {
  const [res, setRes] = useState<Row | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const t = setTimeout(async () => {
      setBusy(true);
      try {
        setRes(await api.post("/twin/whatif", { run_id: runId, changes }));
        setErr(null);
      } catch (e) {
        setErr(e);
      } finally {
        setBusy(false);
      }
    }, 300);
    return () => clearTimeout(t);
  }, [JSON.stringify(changes), runId]); // eslint-disable-line
  return { res, err, busy };
}

export function WellWhatIf({ wellId }: { wellId: string; lift: string }) {
  const l0 = useTwinTable("L0_wells");
  const [ch, setCh] = useState<Record<string, number>>({});
  const { res, err } = useWhatIf(Object.keys(ch).length ? { [wellId]: ch } : {});
  const w = l0.data?.rows.find((r: Row) => r.well_id === wellId);
  if (!w) return null;
  const row = res?.wells.find((x: Row) => x.well_id === wellId);
  return (
    <Card title="What-if for this well" lead="Move a setpoint; the synchronised twin predicts the well and facility response in about a second.">
      <WellControls w={w} ch={ch} set={(k, v) => setCh((c) => { const x = { ...c }; v === undefined ? delete x[k] : (x[k] = v); return x; })} />
      <ErrorBox error={err} />
      {row && <div className="row" style={{ marginTop: 10 }}>
        <span>Oil {n(row.oil_base, 2)} → <b>{n(row.oil_new, 2)}</b> m³/d ({signed(row.d_oil, 2)})</span>
        {row.drawdown != null && <span className="muted">drawdown {pct(row.drawdown, 0)}</span>}
        {row.submergence_m != null && <span className="muted">submergence {int(row.submergence_m)} m</span>}
      </div>}
      {res && res.warnings.length > 0 && <div className="banner warn" style={{ marginTop: 8 }}>{res.warnings.join(" · ")}</div>}
    </Card>
  );
}

export function WhatIf() {
  const { run } = useApp();
  const l0 = useTwinTable("L0_wells");
  const [changes, setChanges] = useState<Changes>({});
  const [filter, setFilter] = useState("all");
  const runId = run?.kind === "twin" ? run.id : undefined;
  const { res, err, busy } = useWhatIf(changes, runId);
  if (l0.isLoading) return <Loading />;
  if (l0.error) return <div className="stack"><PageHead title="What-if" /><ErrorBox error={l0.error} /></div>;
  const wells: Row[] = l0.data!.rows.filter((w: Row) => filter === "all" || w.lift === filter);
  const set = (wid: string) => (k: string, v: number | undefined) =>
    setChanges((c) => {
      const x = { ...c, [wid]: { ...(c[wid] ?? {}) } };
      if (v === undefined) delete x[wid][k];
      else x[wid][k] = v;
      if (!Object.keys(x[wid]).length) delete x[wid];
      return x;
    });
  const nChanged = Object.keys(changes).length;
  return (
    <div className="stack">
      <PageHead title="What-if" sub={<>Interactive scenarios on the synchronised twin (run #{l0.data!.run_id}). Advisory only: nothing is sent to the plant.</>}
                right={<div className="row">{busy && <span className="small muted">computing…</span>}<button disabled={!nChanged} onClick={() => setChanges({})}>Reset all</button></div>} />
      <div className="whatif">
        <div className="stack effect">
          <Card title={nChanged ? `Effect of ${nChanged} well change(s)` : "Effect"}>
            <ErrorBox error={err} />
            {res ? <WhatIfResult r={res} /> : <Loading what="Computing" />}
          </Card>
          {res && nChanged > 0 && (
            <Card title="Changed wells">
              <DataTable rows={res.wells.filter((w: Row) => w.changed)} cols={[
                { key: "well_id", label: "Well" }, { key: "oil_base", label: "Oil now", num: true, render: (r) => n(r.oil_base, 2) },
                { key: "oil_new", label: "Oil new", num: true, render: (r) => n(r.oil_new, 2) }, { key: "d_oil", label: "Δ", num: true, render: (r) => signed(r.d_oil, 2) },
                { key: "drawdown", label: "Drawdown", num: true, render: (r) => pct(r.drawdown, 0) },
                { key: "submergence_m", label: "Subm. m", num: true, render: (r) => int(r.submergence_m) }]} />
            </Card>
          )}
        </div>
        <Card title="Setpoints" right={<Chips value={filter} onChange={setFilter} options={[["all", "All"], ["self_flow", "Self-flow"], ["gas_lift", "Gas lift"], ["srp", "SRP"]]} />}>
          <div className="stack">
            {wells.map((w) => <div key={w.well_id} style={{ borderBottom: "1px solid var(--grid)", paddingBottom: 10 }}><WellControls w={w} ch={changes[w.well_id] ?? {}} set={set(w.well_id)} /></div>)}
          </div>
        </Card>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------- recommendations
export function Recommendations() {
  const { can, user } = useApp();
  const qc = useQueryClient();
  const toast = useToast();
  const [status, setStatus] = useState("proposed,accepted");
  const [decide, setDecide] = useState<{ rec: Row; status: string } | null>(null);
  const [comment, setComment] = useState("");
  const url = `/twin/recommendations${status ? `?status=${status}` : ""}`;
  const q = useQuery({ queryKey: [url], queryFn: () => api.get<Row[]>(url) });
  const m = useMutation({
    mutationFn: () => api.patch(`/twin/recommendations/${decide!.rec.id}`, { status: decide!.status, comment }),
    onSuccess: () => { toast("Decision recorded (nothing is sent to the plant)"); setDecide(null); setComment(""); qc.invalidateQueries(); },
    onError: (e: Error) => toast(e.message, true),
  });
  const rows = q.data ?? [];
  const gain = rows.filter((r) => r.status === "proposed" && r.kind === "setpoint").reduce((s, r) => s + (r.predicted_d_oil_m3d ?? 0), 0);
  const fmtSp = (r: Row) => r.kind === "hold" ? r.rationale :
    Object.entries(r.proposed).map(([k, v]) => `${human(k)} ${k === "lift_gas" ? int(r.current[k]) : n(r.current[k], 1)} → ${k === "lift_gas" ? int(v) : n(v as number, 1)}`).join("; ");
  return (
    <div className="stack">
      <PageHead title="Recommendations (L4)" sub="Advice from the latest twin runs. Engineers accept or decline; the decision is recorded and audited. Nothing is ever written to a plant system."
                right={<button onClick={() => window.print()}>Print sheet</button>} />
      <div className="row">
        <Chips value={status} onChange={setStatus} options={[["proposed,accepted", "Open"], ["proposed", "Proposed"], ["accepted", "Accepted"], ["implemented", "Implemented"], ["declined", "Declined"], ["expired", "Expired"], ["", "All"]]} />
        <span className="spacer" />
        {gain !== 0 && <span className="badge s-info">proposed setpoints together ≈ {signed(gain, 1)} m³/d oil</span>}
      </div>
      <Card>
        {q.isLoading ? <Loading /> : q.error ? <ErrorBox error={q.error} /> : (
          <DataTable rows={rows} csvName="recommendations" cols={[
            { key: "well_id", label: "Well", render: (r) => <Link to={`/wells/${r.well_id}`}>{r.well_id}</Link> },
            { key: "kind", label: "Type", render: (r) => (r.kind === "hold" ? <span className="badge s-warn">◆ Hold</span> : "setpoint") },
            { key: "change", label: "Advice", wrap: true, render: fmtSp, csv: fmtSp },
            { key: "predicted_d_oil_m3d", label: "Predicted Δ oil m³/d", num: true, render: (r) => signed(r.predicted_d_oil_m3d, 2) },
            { key: "rationale", label: "Why", wrap: true, render: (r) => <span className="text2 small">{r.kind === "hold" ? "" : r.rationale}</span> },
            { key: "status", label: "Status", render: (r) => <State s={r.status} /> },
            { key: "decided_by", label: "Decided", render: (r) => (r.decided_by ? `${r.decided_by} ${dt(r.decided_at)}` : ""), wrap: true },
            { key: "comment", label: "Comment", wrap: true },
            { key: "run_id", label: "Run", num: true },
            ...(can("engineer") ? [{ key: "_a", label: "", render: (r: Row) => (
              <div className="row" style={{ gap: 4 }}>
                {r.status === "proposed" && r.kind === "setpoint" && <button className="sm primary" onClick={() => setDecide({ rec: r, status: "accepted" })}>Accept</button>}
                {["proposed", "accepted"].includes(r.status) && <button className="sm" onClick={() => setDecide({ rec: r, status: "declined" })}>Decline</button>}
                {r.status === "accepted" && <button className="sm" onClick={() => setDecide({ rec: r, status: "implemented" })}>Mark implemented</button>}
              </div>) }] : []),
          ]} empty="No recommendations with this status." />
        )}
      </Card>
      {decide && (
        <Modal title={`${human(decide.status)}: ${decide.rec.well_id}`} onClose={() => setDecide(null)}
               actions={<><button onClick={() => setDecide(null)}>Cancel</button>
                 <button className="primary" disabled={m.isPending || (decide.status === "declined" && !comment.trim())} onClick={() => m.mutate()}>Record decision</button></>}>
          <div className="stack">
            <div>{fmtSp(decide.rec)} · predicted {signed(decide.rec.predicted_d_oil_m3d, 2)} m³/d oil</div>
            <div className="banner info">This records your decision ({user?.full_name || user?.username}). Change the setpoint in the field / DCS through the normal operating procedure; FlowTwin does not.</div>
            <label className="f">Comment {decide.status === "declined" ? "(required)" : "(optional)"}
              <textarea rows={3} value={comment} onChange={(e) => setComment(e.target.value)} style={{ fontFamily: "inherit" }} /></label>
          </div>
        </Modal>
      )}
    </div>
  );
}
