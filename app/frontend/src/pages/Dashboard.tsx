import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { api, Row } from "../api";
import { Chart } from "../components/Chart";
import { DataTable } from "../components/DataTable";
import { Card, ErrorBox, Loading, PageHead, Sev, State, Tile, useToast } from "../components/ui";
import { useApp, useRunQuery } from "../context";
import { day, dt, int, LAYER, n, signed } from "../fmt";

function afSev(v: unknown) {
  if (typeof v !== "number") return "INFO";
  if (v < 0.8 || v > 1.2) return "ALARM";
  if (v < 0.9 || v > 1.1) return "WARN";
  return "OK";
}

export function Ladder({ levels, onPick }: { levels: Row[]; onPick?: (l: number) => void }) {
  return (
    <div className="ladder">
      {levels.map((l) => (
        <button key={l.level} className={`rung ${l.status.replace(" ", "-")}`} onClick={() => onPick?.(l.level)}>
          <div className="lv">L{l.level}</div>
          <div className="nm">{l.name}</div>
          <State s={l.status} /> <span className="small muted">{l.met}/{l.total}</span>
        </button>
      ))}
    </div>
  );
}

function Onboarding({ hasNetwork }: { hasNetwork: boolean }) {
  const { can } = useApp();
  const qc = useQueryClient();
  const toast = useToast();
  const nav = useNavigate();
  const seed = useMutation({
    mutationFn: () => api.post("/admin/seed-sample"),
    onSuccess: () => { toast("Sample GGS loaded"); qc.invalidateQueries(); },
    onError: (e: Error) => toast(e.message, true),
  });
  const run = useMutation({
    mutationFn: () => api.post("/runs", { kind: "twin" }),
    onSuccess: () => { toast("Twin run started – it takes about 20 s"); nav("/runs"); },
    onError: (e: Error) => toast(e.message, true),
  });
  return (
    <Card title="Get started">
      <ol className="stack" style={{ paddingLeft: 18 }}>
        <li>
          <b>Network model.</b> Describe wells, separators, meters and tanks (Admin → Network), or load the synthetic
          demo station.{" "}
          {!hasNetwork && can("admin") && (
            <button className="primary sm" onClick={() => seed.mutate()} disabled={seed.isPending}>Load sample GGS</button>
          )}
          {hasNetwork && <State s="done" />}
        </li>
        <li><b>Data.</b> Upload the daily CSV/Excel templates (Data in) or drop them into the watched folder.</li>
        <li>
          <b>Run.</b> The scheduler runs the twin every morning; start one now if you like.{" "}
          {hasNetwork && can("engineer") && (
            <button className="sm" onClick={() => run.mutate()} disabled={run.isPending}>Start twin run</button>
          )}
        </li>
      </ol>
    </Card>
  );
}

export function Dashboard() {
  const q = useRunQuery<Row>("/dashboard");
  const net = useRunQuery<Row>("/network");
  const nav = useNavigate();
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data!;
  if (!d.run) return (
    <div className="stack">
      <PageHead title="Dashboard" sub={d.site?.name} />
      <Onboarding hasNetwork={!net.error} />
    </div>
  );
  const k = d.kpi ?? {};
  const oi = d.open_issues ?? {};
  const tw = d.twin;
  return (
    <div className="stack">
      <PageHead title="Dashboard"
                sub={<>Production day <b>{day(k.date)}</b> (06:00–06:00) · run #{d.run.id} ({d.run.kind}) finished {dt(d.run.finished_at)}
                  {d.data?.latest_well_day && d.data.latest_well_day > (d.run.period_end ?? "") &&
                    <> · <span className="badge s-warn">◆ newer data ({day(d.data.latest_well_day)}) not run yet</span></>}</>} />
      <div className="tiles">
        <Tile label="Oil, reconciled" value={n(k.dvr_oil_m3, 1)} unit="m³" sub={<>measured (tanks) {n(k.meas_oil_m3, 1)} · test-based {n(k.theo_oil_m3, 1)}</>} />
        <Tile label="Water, reconciled" value={n(k.dvr_water_m3, 1)} unit="m³" />
        <Tile label="Gas, reconciled" value={int(k.dvr_gas_sm3)} unit="Sm³" sub={<>lift gas {int(k.lift_gas_sm3)} Sm³</>} />
        <Tile label="Oil allocation factor" value={n(k.af_oil, 3)} badge={<Sev s={afSev(k.af_oil)} />}
              sub={<>water {n(k.af_water, 3)} · gas {n(k.af_gas, 3)} · band 0.90–1.10</>} />
        <Tile label="Reconciliation χ² test" value={`${k.chi2_passed}/${k.chi2_days}`} unit="days passed"
              badge={<Sev s={k.chi2_last_passed ? "OK" : "WARN"} />}
              sub={<>last day ratio {n(k.chi2_last_ratio, 2)} (≤ 1 passes)</>} />
        <Tile label="Open alarms" value={int(oi.ALARM ?? 0)} badge={<Sev s={(oi.ALARM ?? 0) > 0 ? "ALARM" : "OK"} />}
              sub={<>{d.unacknowledged_alarms} unacknowledged · {oi.WARN ?? 0} warnings · <Link to="/exceptions">open register</Link></>} />
        <Tile label="Wells on line" value={int(d.wells_online)} sub={<>of {net.data?.wells?.length ?? "–"} in the network</>} />
        {tw?.summary && (
          <Tile label="Twin advice (L4)" value={signed(tw.summary.l4_gain_m3d, 1)} unit="m³/d oil"
                sub={<><Link to="/twin/recommendations">recommendations</Link> · L5 simulator {signed(tw.summary.l5_gain_m3d, 1)} m³/d</>} />
        )}
      </div>

      {tw?.levels?.length > 0 && (
        <Card title="Digital-twin maturity (DNV-RP-A204)" right={<Link to="/twin">Open twin</Link>}>
          <Ladder levels={tw.levels} onPick={(l) => nav(`/twin?level=${l}`)} />
        </Card>
      )}

      <div className="grid2">
        <Card title="Installation oil by day" lead="Reconciled (DVR) oil against tank-measured production and the sum of well-test rates.">
          <Chart data={d.series} x="date" xFmt={day} yLabel="m³/d"
                 series={[{ key: "dvr_oil", label: "Reconciled", color: "var(--s1)" },
                          { key: "meas_oil", label: "Measured (tanks)", color: "var(--s2)" },
                          { key: "theo_oil", label: "Test-based", color: "var(--s3)", dash: true }]} />
        </Card>
        <Card title="Allocation factors by day" lead="Measured total ÷ sum of test-based rates. Shaded: acceptance band 0.90–1.10.">
          <Chart data={d.series} x="date" xFmt={day} refBand={[0.9, 1.1]} refBandLabel="Acceptance band"
                 yFmt={(v) => v.toFixed(2)}
                 series={[{ key: "af_oil", label: "Oil", color: "var(--s1)" }, { key: "af_water", label: "Water", color: "var(--s2)" },
                          { key: "af_gas", label: "Gas", color: "var(--s3)" }]} />
        </Card>
      </div>
      <Card title="Reconciliation consistency" lead="Global χ² ÷ critical value (α = 0.05), before gross-error removal. Above 1 the measurements contradict each other; see Reconciliation for which one.">
        <Chart data={d.chi2} x="date" xFmt={day} height={170} yFmt={(v) => v.toFixed(1)} refLines={[{ y: 1, label: "critical" }]}
               series={[{ key: "ratio", label: "χ² ratio", color: "var(--s1)", type: "bar" }]} />
      </Card>
      <Card title={`Exceptions on ${day(k.date)}`} right={<Link to="/exceptions">All exceptions →</Link>}
            lead="Alarms and warnings the engine raised for the last production day of this run.">
        <DataTable rows={d.top_exceptions} rowKey={(r) => r.id} onRow={(r) => nav(`/exceptions?issue=${r.issue_id ?? ""}`)}
                   empty="No alarms or warnings on the last day."
                   cols={[
                     { key: "severity", label: "Severity", render: (r) => <Sev s={r.severity} /> },
                     { key: "layer", label: "Layer", render: (r) => LAYER[r.layer] ?? r.layer },
                     { key: "object_id", label: "Object" },
                     { key: "check", label: "Check" },
                     { key: "message", label: "Finding", wrap: true },
                     { key: "action", label: "Recommended action", wrap: true, render: (r) => <span className="text2">{r.action}</span> },
                   ]} />
      </Card>
    </div>
  );
}
