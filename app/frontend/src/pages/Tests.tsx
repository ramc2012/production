import { useState } from "react";
import { Link } from "react-router-dom";
import { Row } from "../api";
import { DataTable } from "../components/DataTable";
import { Card, Chips, ErrorBox, Loading, PageHead, State } from "../components/ui";
import { useApp, useRunQuery } from "../context";
import { day, int, n } from "../fmt";
import { OverrideDialog, testCols } from "./Wells";

export function Tests() {
  const { can } = useApp();
  const q = useRunQuery<Row>("/tests");
  const [filter, setFilter] = useState("");
  const [ovr, setOvr] = useState<Row | null>(null);
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data!;
  const rows = filter ? d.tests.filter((t: Row) => (filter === "override" ? t.override : t.status === filter)) : d.tests;
  return (
    <div className="stack">
      <PageHead title="Well tests" sub="Every test with the engine's verdict. Rejected tests are not used; provisional tests are used with wider uncertainty until a repeat confirms them." />
      <div className="grid2">
        <Card title="Test due dates" lead="Per well: last usable test and when the next one is due (test-age warning limit, 30 days by default).">
          <DataTable rows={d.due} csvName="tests_due" initialSort={["age_days", "desc"]} cols={[
            { key: "well_id", label: "Well", render: (r) => <Link to={`/wells/${r.well_id}`}>{r.well_id}</Link> },
            { key: "last_test", label: "Last usable test", render: (r) => day(r.last_test) }, { key: "status", label: "Status", render: (r) => <State s={r.status} /> },
            { key: "age_days", label: "Age d", num: true, render: (r) => int(r.age_days) }, { key: "due_date", label: "Due", render: (r) => day(r.due_date) },
            { key: "overdue", label: "", render: (r) => (r.overdue ? <span className="badge s-warn">◆ overdue</span> : "") }]} />
        </Card>
        <Card title="Suggested test schedule (L4)" lead="Ranked by allocation uncertainty × oil rate, open findings and test age; one test-separator slot per day.">
          <DataTable rows={d.schedule} csvName="test_schedule" cols={[
            { key: "planned_date", label: "Planned", render: (r) => (r.planned_date ? day(r.planned_date) : "later") },
            { key: "well_id", label: "Well", render: (r) => <Link to={`/wells/${r.well_id}`}>{r.well_id}</Link> },
            { key: "uncertainty_m3d", label: "± oil m³/d", num: true, render: (r) => n(r.uncertainty_m3d, 2) },
            { key: "test_age_d", label: "Age d", num: true, render: (r) => int(r.test_age_d) },
            { key: "open_findings", label: "Open findings", wrap: true, render: (r) => <span className="text2 small">{r.open_findings}</span> }]} />
        </Card>
      </div>
      <Card title="Test register" right={<Chips value={filter} onChange={setFilter} options={[["", "All"], ["REJECTED", "Rejected"], ["PROVISIONAL", "Provisional"], ["ACCEPTED_W", "With warnings"], ["override", "Overridden"]]} />}>
        <DataTable rows={rows} search csvName="well_tests" tall cols={testCols(can("engineer") ? setOvr : undefined)} />
      </Card>
      {ovr && <OverrideDialog test={ovr} onClose={() => setOvr(null)} />}
    </div>
  );
}
