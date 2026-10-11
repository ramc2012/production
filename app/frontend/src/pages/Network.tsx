import { useNavigate } from "react-router-dom";
import { Row } from "../api";
import { DataTable } from "../components/DataTable";
import { NetworkDiagram } from "../components/NetworkDiagram";
import { Card, ErrorBox, Loading, PageHead, Sev } from "../components/ui";
import { useRunQuery } from "../context";
import { day } from "../fmt";

export function Network() {
  const q = useRunQuery<Row>("/network");
  const nav = useNavigate();
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const net = q.data!;
  const objects: Row[] = [
    ...net.separators.map((s: Row) => ({ ...s, type: "separator", desc: `${s.kind}, ${s.phases}-phase`, to: "/separators" })),
    ...net.tanks.map((t: Row) => ({ ...t, type: "tank", desc: `safe fill ${t.safe_fill_mm ?? "–"} mm`, to: "/tanks" })),
    ...net.meters.map((m: Row) => ({ ...m, type: "meter", desc: `${m.phase} · ${m.type} · ${m.description ?? ""}`, to: `/meters/${m.id}` })),
  ];
  return (
    <div className="stack">
      <PageHead title={net.name} sub={<>Network model v{net.network_version} · status on {day(net.run?.period_end)} (run #{net.run?.id ?? "–"})</>} />
      <Card title="Flow network" lead="Each object shows the worst of its findings on the run's last production day and its still-open issues. The test separator (dashed links) receives each well in rotation; gas from all separators goes to the compressor, flare and fuel meters.">
        <NetworkDiagram net={net} />
      </Card>
      <Card title="Equipment">
        <DataTable rows={objects} search csvName="equipment" onRow={(r) => nav(r.to)} rowKey={(r) => `${r.type}:${r.id}`}
                   cols={[
                     { key: "status", label: "Status", render: (r) => <Sev s={r.status} />, sort: (r) => ({ ALARM: 0, WARN: 1, INFO: 2 } as Row)[r.status] ?? 3 },
                     { key: "id", label: "ID" }, { key: "type", label: "Type" }, { key: "desc", label: "Description" },
                     { key: "open_issues", label: "Open issues", num: true },
                   ]} />
      </Card>
    </div>
  );
}
