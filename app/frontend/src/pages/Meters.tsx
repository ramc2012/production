import { Link, useNavigate, useParams } from "react-router-dom";
import { Row } from "../api";
import { Chart } from "../components/Chart";
import { DataTable } from "../components/DataTable";
import { Card, ErrorBox, Loading, PageHead, Sev } from "../components/ui";
import { useRunQuery } from "../context";
import { day, human, LAYER, n, signed } from "../fmt";

export function Meters() {
  const q = useRunQuery<Row>("/meters");
  const nav = useNavigate();
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data!;
  return (
    <div className="stack">
      <PageHead title="Meters" sub="Proving status, how far reconciliation had to adjust each meter, and systematic bias found over the run." />
      <Card title="Maintenance priorities (L4)" lead="Meters to prove first, ranked by the volume they misreport per day; plus overdue provings and pump/valve field checks.">
        <DataTable rows={d.maintenance} csvName="maintenance" cols={[
          { key: "item", label: "Item", render: (r) => (r.item?.startsWith("FT") ? <Link to={`/meters/${r.item}`}>{r.item}</Link> : <Link to={`/wells/${r.item}`}>{r.item}</Link>) },
          { key: "action", label: "Action" }, { key: "reason", label: "Why", wrap: true },
          { key: "misreport_per_day", label: "Misreported per day", num: true, render: (r) => n(r.misreport_per_day, 1) }]} empty="Nothing to maintain." />
      </Card>
      <Card title="All meters">
        <DataTable rows={d.meters} search csvName="meters" rowKey={(r) => r.id} onRow={(r) => nav(`/meters/${r.id}`)} cols={[
          { key: "status", label: "Status", render: (r) => <Sev s={r.status} />, sort: (r) => ({ ALARM: 0, WARN: 1 } as Row)[r.status] ?? 2 },
          { key: "id", label: "Meter" }, { key: "role", label: "Service" }, { key: "phase", label: "Phase" }, { key: "type", label: "Type" },
          { key: "meter_factor", label: "MF", num: true, render: (r) => n(r.meter_factor, 4) },
          { key: "proving_due", label: "Proving due", render: (r) => (r.proving_due ? <>{day(r.proving_due)} {r.proving_overdue && <span className="badge s-warn">◆ overdue</span>}</> : "–") },
          { key: "mean_adj_pct", label: "Mean DVR adjustment", num: true, render: (r) => (r.mean_adj_pct == null ? "–" : `${signed(r.mean_adj_pct, 2)} %`), title: "Average correction reconciliation applied to this meter" },
          { key: "max_z", label: "Max z", num: true, render: (r) => n(r.max_z, 1) },
          { key: "bias_days_flagged", label: "Days flagged", num: true },
          { key: "bias_mean_dev_pct", label: "Bias when flagged", num: true, render: (r) => (r.bias_mean_dev_pct == null ? "–" : `${signed(r.bias_mean_dev_pct, 1)} %`) },
          { key: "drift", label: "Drift projection", render: (r) => (r.drift ? `${signed(r.drift.bias_now_pct, 1)} % now → ${signed(r.drift.projected_pct, 1)} % in 30 d` : "") },
          { key: "open_issues", label: "Issues", num: true }]} />
      </Card>
    </div>
  );
}

export function MeterDetail() {
  const { id } = useParams();
  const q = useRunQuery<Row>(`/meters/${id}`);
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data!;
  const vars = Array.from(new Set(d.series.map((r: Row) => r.var))) as string[];
  return (
    <div className="stack">
      <PageHead title={`Meter ${id}`} sub="Measured vs reconciled value per day. A stable non-zero deviation is a systematic bias: prove or recalibrate the meter." right={<Link to="/meters">← All meters</Link>} />
      {vars.length === 0 && <div className="empty">This meter is not part of the reconciliation in this run.</div>}
      {vars.map((v) => {
        const rows = d.series.filter((r: Row) => r.var === v).map((r: Row) => ({
          ...r, lo: r.rec - 2 * (r.sd_rec ?? 0), hi: r.rec + 2 * (r.sd_rec ?? 0),
          dev: r.rec ? ((r.meas - r.rec) / r.rec) * 100 : null,
        }));
        return (
          <div className="grid2" key={v}>
            <Card title={`${v}: measured vs reconciled`}>
              <Chart data={rows} x="date" xFmt={day} bands={[{ lo: "lo", hi: "hi", color: "var(--s1)", label: "Reconciled U95" }]}
                     series={[{ key: "rec", label: "Reconciled", color: "var(--s1)" }, { key: "meas", label: "Measured", color: "var(--s2)", type: "dots" }]} />
            </Card>
            <Card title="Deviation of the measurement" lead="(measured − reconciled) ÷ reconciled.">
              <Chart data={rows} x="date" xFmt={day} yFmt={(x) => `${x.toFixed(1)} %`} refLines={[{ y: 0, label: "0" }]}
                     series={[{ key: "dev", label: "Deviation %", color: "var(--s1)", type: "bar" }]} />
            </Card>
          </div>
        );
      })}
      <Card title="Findings">
        <DataTable rows={d.findings} csvName={`${id}_findings`} cols={[
          { key: "date", label: "Day", render: (r) => day(r.date) }, { key: "severity", label: "Severity", render: (r) => <Sev s={r.severity} /> },
          { key: "layer", label: "Layer", render: (r) => LAYER[r.layer] ?? r.layer }, { key: "check", label: "Check", render: (r) => human(r.check) },
          { key: "message", label: "Finding", wrap: true }]} />
      </Card>
      {d.series.length > 0 && (
        <Card title="Reconciliation detail">
          <DataTable rows={d.series} csvName={`${id}_dvr`} tall cols={[
            { key: "date", label: "Day", render: (r) => day(r.date) }, { key: "var", label: "Stream" },
            { key: "meas", label: "Measured", num: true, render: (r) => n(r.meas, 2) }, { key: "sd_meas", label: "σ meas", num: true, render: (r) => n(r.sd_meas, 2) },
            { key: "rec", label: "Reconciled", num: true, render: (r) => n(r.rec, 2) }, { key: "sd_rec", label: "σ rec", num: true, render: (r) => n(r.sd_rec, 2) },
            { key: "adj_pct", label: "Adj %", num: true, render: (r) => signed(r.adj_pct, 2) }, { key: "z", label: "z", num: true, render: (r) => n(r.z, 2) },
            { key: "glr_p", label: "GLR p", num: true, render: (r) => (r.glr_p == null ? "–" : r.glr_p.toExponential(1)) },
            { key: "kind", label: "Kind" }]} />
        </Card>
      )}
    </div>
  );
}
