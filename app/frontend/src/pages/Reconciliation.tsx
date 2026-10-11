import { useState } from "react";
import { Row } from "../api";
import { Chart } from "../components/Chart";
import { DataTable } from "../components/DataTable";
import { Card, Chips, ErrorBox, KV, Loading, PageHead, Sev } from "../components/ui";
import { useRunQuery } from "../context";
import { day, n, signed } from "../fmt";

export function Reconciliation() {
  const [date, setDate] = useState<string | null>(null);
  const [show, setShow] = useState("flagged");
  const q = useRunQuery<Row>("/reconciliation", { date });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data!;
  const vars: Row[] = (d.variables ?? []).filter((v: Row) => v.measured || show === "all");
  const shown = show === "flagged" ? vars.filter((v) => String(v.kind).includes("eliminated") || (v.z ?? 0) > 1.96) : vars;
  const day0 = d.day ?? {};
  return (
    <div className="stack">
      <PageHead title="Reconciliation (DVR)" sub="Weighted least squares on the oil, water and gas balances. The χ² test checks whether the measurements agree; gross errors are isolated one at a time (GLR test). When two sources explain the data equally well the result is 'ambiguous', not a guess." />
      <Card title="Daily consistency" lead="Click a bar to inspect that day. χ² ratio = statistic ÷ critical value before gross-error removal.">
        <Chart data={d.days} x="date" xFmt={day} height={170} yFmt={(v) => v.toFixed(1)} refLines={[{ y: 1, label: "critical" }]}
               onPick={(r) => setDate(r.date)} vline={d.date ? { x: d.date, label: "" } : undefined}
               series={[{ key: "ratio", label: "χ² ratio", color: "var(--s1)", type: "bar" }]} />
        <div className="row" style={{ marginTop: 8 }}>
          <label className="f" style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>Day
            <select value={d.date ?? ""} onChange={(e) => setDate(e.target.value)}>
              {d.days.map((x: Row) => <option key={x.date} value={x.date}>{day(x.date)} {x.passed_initial ? "✓" : "▲"}</option>)}
            </select>
          </label>
        </div>
      </Card>
      <div className="grid2">
        <Card title={`Result for ${day(d.date)}`}>
          <KV items={[["χ² before / critical", `${n(day0.chi2, 1)} / ${n(day0.chi2_crit, 1)} (dof ${day0.dof ?? "–"})`],
                      ["Global test before", day0.passed_initial ? <Sev s="OK" /> : <Sev s="WARN" />],
                      ["χ² after / critical", `${n(day0.chi2_final, 1)} / ${n(day0.chi2_crit_final, 1)} (dof ${day0.dof_final ?? "–"})`],
                      ["Global test after", day0.passed_final ? <Sev s="OK" /> : <Sev s="WARN" />],
                      ["Gross errors isolated", day0.eliminated || "none"], ["Ambiguous sets", day0.ambiguous || "none"],
                      ["Unobservable", day0.unobservable || "none"]]} />
        </Card>
        <Card title="Reconciliation findings this day">
          <DataTable rows={d.findings ?? []} cols={[{ key: "severity", label: "", render: (r) => <Sev s={r.severity} /> }, { key: "object_id", label: "Object" },
                                                    { key: "message", label: "Finding", wrap: true }]} empty="None: all measurements agree." />
        </Card>
      </div>
      <Card title="Measurements and reconciled values" right={<Chips value={show} onChange={setShow} options={[["flagged", "Flagged (z > 1.96)"], ["measured", "All measured"], ["all", "Incl. unmeasured"]]} />}>
        <DataTable rows={shown} search csvName={`dvr_${d.date}`} tall cols={[
          { key: "var", label: "Variable" }, { key: "tag", label: "Source" }, { key: "kind", label: "Kind" },
          { key: "meas", label: "Measured", num: true, render: (r) => n(r.meas, 2) }, { key: "sd_meas", label: "σ", num: true, render: (r) => n(r.sd_meas, 2) },
          { key: "rec", label: "Reconciled", num: true, render: (r) => n(r.rec, 2) }, { key: "sd_rec", label: "σ rec", num: true, render: (r) => n(r.sd_rec, 2) },
          { key: "adj_pct", label: "Adjust %", num: true, render: (r) => signed(r.adj_pct, 2) },
          { key: "z", label: "z", num: true, render: (r) => <span style={{ fontWeight: (r.z ?? 0) > 1.96 ? 650 : 400 }}>{n(r.z, 2)}</span> },
          { key: "redundancy", label: "Redundancy", num: true, render: (r) => n(r.redundancy, 2) },
          { key: "glr_p", label: "GLR p", num: true, render: (r) => (r.glr_p == null ? "–" : Number(r.glr_p).toExponential(1)) }]} />
      </Card>
      <div className="grid2">
        <Card title="Balance residuals" lead="Raw residual of each balance with the measured values, and after reconciliation (≈ 0).">
          <DataTable rows={d.constraints ?? []} csvName={`constraints_${d.date}`} tall cols={[
            { key: "constraint", label: "Balance" }, { key: "raw_residual", label: "Raw residual", num: true, render: (r) => n(r.raw_residual, 2) },
            { key: "rec_residual", label: "After DVR", num: true, render: (r) => n(r.rec_residual, 4) }]} />
        </Card>
        <Card title="Systematic bias suspects (whole run)" lead="Streams flagged on several days. Three or more days raises a systematic-bias alarm.">
          <DataTable rows={d.bias_suspects} csvName="bias_suspects" cols={[
            { key: "tag", label: "Source" }, { key: "var", label: "Stream" }, { key: "days_flagged", label: "Days", num: true },
            { key: "first_flag", label: "Since", render: (r) => day(r.first_flag) },
            { key: "mean_dev_pct", label: "Mean deviation", num: true, render: (r) => `${signed(r.mean_dev_pct, 1)} %` },
            { key: "max_z", label: "Max z", num: true, render: (r) => n(r.max_z, 1) }]} empty="No persistent bias." />
        </Card>
      </div>
    </div>
  );
}
