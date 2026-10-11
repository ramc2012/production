import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, Row } from "../api";
import { DataTable } from "../components/DataTable";
import { Card, Drawer, ErrorBox, KV, Loading, PageHead, Progress, State, useToast } from "../components/ui";
import { useApp } from "../context";
import { day, dt, n } from "../fmt";

function RunDrawer({ id, onClose }: { id: number; onClose: () => void }) {
  const { setRun } = useApp();
  const q = useQuery({ queryKey: [`/runs/${id}`], queryFn: () => api.get<Row>(`/runs/${id}`), refetchInterval: (qq) => (["queued", "running"].includes(qq.state.data?.status) ? 2000 : false) });
  if (!q.data) return <Drawer onClose={onClose} label="Run"><Loading /></Drawer>;
  const r = q.data;
  const s = r.summary ?? {};
  return (
    <Drawer onClose={onClose} label={`Run ${id}`}>
      <div className="stack">
        <div className="row"><h2 style={{ fontSize: 18 }}>Run #{r.id} · {r.kind}</h2><State s={r.status} /></div>
        {r.job && ["queued", "running"].includes(r.status) && <Progress value={r.job.progress} stage={r.job.stage} />}
        <KV items={[["Period", `${day(r.period_start)} – ${day(r.period_end)}`], ["Trigger", r.trigger], ["Started by", r.created_by],
                    ["Started / finished", `${dt(r.started_at)} / ${dt(r.finished_at)}`], ["Runtime", `${n(r.runtime_s, 1)} s`],
                    ["Engine version", r.engine_version || "–"], ["Config hash", <code>{r.config_hash || "–"}</code>],
                    ["Network / config version", `v${r.network_version_id ?? "–"} / v${r.config_version_id ?? "–"}`],
                    ["Input batches", (r.input_batches ?? []).join(", ") || "–"],
                    ["Test overrides applied", Object.keys(r.test_overrides ?? {}).join(", ") || "none"],
                    ["Findings", s.alarms != null ? `${s.alarms} alarms, ${s.warnings} warnings (consolidated)` : "–"],
                    ["χ² passed", s.chi2_days ? `${s.chi2_passed}/${s.chi2_days} days` : "–"],
                    ...(s.twin ? [["Twin", `L4 ${n(s.twin.l4_gain_m3d, 1)} m³/d advice · L5 ${n(s.twin.l5_gain_m3d, 1)} m³/d on simulator`] as [string, string]] : [])]} />
        {r.log && <pre className="banner err" style={{ whiteSpace: "pre-wrap", fontSize: 12 }}>{r.log}</pre>}
        {r.files?.length > 0 && (
          <Card title="Reports (engine exports)">
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {r.files.map((f: string) => <li key={f}><a href={`/api/v1/runs/${r.id}/files/${f}`} target="_blank" rel="noreferrer">{f}</a></li>)}
            </ul>
          </Card>
        )}
        {r.status === "done" && <button className="primary" onClick={() => { setRun(r); onClose(); }}>Show this run on every page</button>}
      </div>
    </Drawer>
  );
}

export function Runs() {
  const { can } = useApp();
  const qc = useQueryClient();
  const toast = useToast();
  const [kind, setKind] = useState("twin");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const runs = useQuery({ queryKey: ["/runs?limit=200"], queryFn: () => api.get<Row[]>("/runs?limit=200"), refetchInterval: 3000 });
  const go = useMutation({
    mutationFn: () => api.post("/runs", { kind, start: start || null, end: end || null }),
    onSuccess: (r: Row) => { toast(`Run #${r.id} queued`); qc.invalidateQueries(); setOpen(r.id); },
    onError: (e: Error) => toast(e.message, true),
  });
  const cancel = useMutation({ mutationFn: (job: number) => api.del(`/jobs/${job}`), onSuccess: () => toast("Cancel requested") });
  return (
    <div className="stack">
      <PageHead title="Runs & reports" sub="Every run is kept with its engine version, configuration hash and input batches. Re-running a period creates a new run." />
      {can("engineer") && (
        <Card title="Start a run">
          <div className="row" style={{ alignItems: "flex-end" }}>
            <label className="f">Kind
              <select value={kind} onChange={(e) => setKind(e.target.value)}>
                <option value="twin">Twin (validation + L0–L5, ~20 s)</option>
                <option value="validation">Validation only (~5 s)</option>
              </select>
            </label>
            <label className="f">First day (blank = all data)<input type="date" value={start} onChange={(e) => setStart(e.target.value)} /></label>
            <label className="f">Last day<input type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></label>
            <button className="primary" disabled={go.isPending} onClick={() => go.mutate()}>Start</button>
          </div>
        </Card>
      )}
      <Card>
        {runs.isLoading ? <Loading /> : runs.error ? <ErrorBox error={runs.error} /> : (
          <DataTable rows={runs.data!} csvName="runs" rowKey={(r) => r.id} onRow={(r) => setOpen(r.id)} cols={[
            { key: "id", label: "#", num: true }, { key: "kind", label: "Kind" },
            { key: "status", label: "Status", render: (r) => (
              <div style={{ minWidth: 120 }}>
                <State s={r.status} />
                {r.job && <Progress value={r.job.progress} stage={r.job.stage} />}
              </div>) },
            { key: "period", label: "Period", render: (r) => `${day(r.period_start)} – ${day(r.period_end)}`, sort: (r) => r.period_end },
            { key: "trigger", label: "Trigger" }, { key: "created_by", label: "By" },
            { key: "created_at", label: "Created", render: (r) => dt(r.created_at) },
            { key: "runtime_s", label: "Runtime s", num: true, render: (r) => n(r.runtime_s, 1) },
            { key: "alarms", label: "Alarms", num: true, render: (r) => r.summary?.alarms ?? "", sort: (r) => r.summary?.alarms },
            { key: "_c", label: "", render: (r) => (["queued", "running"].includes(r.status) && r.job_id && can("engineer") ? (
              <button className="sm danger" onClick={(e) => { e.stopPropagation(); cancel.mutate(r.job_id); }}>Cancel</button>) : null) }]} />
        )}
      </Card>
      {open && <RunDrawer id={open} onClose={() => setOpen(null)} />}
    </div>
  );
}
