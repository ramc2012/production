import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, Row } from "../api";
import { DataTable } from "../components/DataTable";
import { Card, ErrorBox, KV, Loading, Modal, PageHead, Sev, Tabs, useToast } from "../components/ui";
import { useApp } from "../context";
import { dt, int } from "../fmt";

function Users() {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["/admin/users"], queryFn: () => api.get<Row[]>("/admin/users") });
  const [nu, setNu] = useState({ username: "", full_name: "", designation: "", role: "engineer" });
  const [pw, setPw] = useState<Row | null>(null);
  const add = useMutation({
    mutationFn: () => api.post("/admin/users", nu),
    onSuccess: (r: Row) => { setPw(r); setNu({ username: "", full_name: "", designation: "", role: "engineer" }); qc.invalidateQueries({ queryKey: ["/admin/users"] }); },
    onError: (e: Error) => toast(e.message, true),
  });
  const patch = useMutation({
    mutationFn: ({ id, body }: { id: number; body: Row }) => api.patch(`/admin/users/${id}`, body),
    onSuccess: (r: Row) => { if (r.temporary_password) setPw(r); else toast("Saved"); qc.invalidateQueries({ queryKey: ["/admin/users"] }); },
    onError: (e: Error) => toast(e.message, true),
  });
  return (
    <div className="stack">
      <Card title="Add user" lead="Roles: viewer (read only), engineer (runs, uploads, issues, overrides, recommendation decisions), admin (also users, network, configuration, schedule).">
        <div className="row" style={{ alignItems: "flex-end" }}>
          <label className="f">User name<input value={nu.username} onChange={(e) => setNu({ ...nu, username: e.target.value })} /></label>
          <label className="f">Full name<input value={nu.full_name} onChange={(e) => setNu({ ...nu, full_name: e.target.value })} /></label>
          <label className="f">Designation<input value={nu.designation} onChange={(e) => setNu({ ...nu, designation: e.target.value })} /></label>
          <label className="f">Role<select value={nu.role} onChange={(e) => setNu({ ...nu, role: e.target.value })}><option>viewer</option><option>engineer</option><option>admin</option></select></label>
          <button className="primary" disabled={!nu.username} onClick={() => add.mutate()}>Add</button>
        </div>
      </Card>
      <Card>
        {q.data && <DataTable rows={q.data} cols={[
          { key: "username", label: "User" }, { key: "full_name", label: "Name" }, { key: "designation", label: "Designation" },
          { key: "role", label: "Role", render: (r) => (
            <select value={r.role} onChange={(e) => patch.mutate({ id: r.id, body: { role: e.target.value } })}><option>viewer</option><option>engineer</option><option>admin</option></select>) },
          { key: "active", label: "Active", render: (r) => <input type="checkbox" checked={r.active} onChange={(e) => patch.mutate({ id: r.id, body: { active: e.target.checked } })} /> },
          { key: "must_change_password", label: "Must change pw", render: (r) => (r.must_change_password ? "yes" : "") },
          { key: "_r", label: "", render: (r) => <button className="sm" onClick={() => patch.mutate({ id: r.id, body: { reset_password: true } })}>Reset password</button> }]} />}
      </Card>
      {pw && (
        <Modal title={`Temporary password for ${pw.username}`} onClose={() => setPw(null)} actions={<button className="primary" onClick={() => setPw(null)}>Done</button>}>
          <p>Give this to the user in person. It is shown once; they must change it at first sign-in.</p>
          <p><code style={{ fontSize: 16 }}>{pw.temporary_password}</code></p>
        </Modal>
      )}
    </div>
  );
}

function NetworkEditor() {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["/admin/network"], queryFn: () => api.get<Row>("/admin/network") });
  const [y, setY] = useState("");
  const [st, setSt] = useState("");
  const [comment, setComment] = useState("");
  const [tank, setTank] = useState("");
  useEffect(() => { if (q.data) { setY(q.data.yaml_text); setSt(q.data.well_static_csv); } }, [q.data]);
  const save = useMutation({
    mutationFn: () => api.put("/admin/network", { yaml_text: y, well_static_csv: st, comment }),
    onSuccess: (r: Row) => { toast(`Network model v${r.version} saved; the next run uses it`); setComment(""); qc.invalidateQueries(); },
    onError: (e: Error) => toast(e.message, true),
  });
  const strap = async (f: File) => {
    const fd = new FormData();
    fd.append("file", f);
    try { const r = await api.upload<Row>(`/admin/network/strapping/${tank}`, fd); toast(`Strapping table for ${tank} saved (v${r.version})`); qc.invalidateQueries(); }
    catch (e) { toast((e as Error).message, true); }
  };
  if (q.isLoading) return <Loading />;
  return (
    <div className="stack">
      <Card title="network.yaml" lead="Wells (lift type, separator, SRP plunger/stroke, lift-gas meter), separators (meters, shrinkage, dimensions), meters (type, meter factor, proving), tanks, dispatch, gas disposal and the lift-gas header. Validated by the engine before it is saved; each save is a new version.">
        <textarea rows={24} value={y} onChange={(e) => setY(e.target.value)} spellCheck={false} aria-label="network.yaml" />
      </Card>
      <Card title="Well master (well_static.csv)" lead="perf_depth_m, tubing_depth_m, tubing_id_in, casing_id_in, valve_depth_m, pump_depth_m, reservoir_temp_degc per well.">
        <textarea rows={8} value={st} onChange={(e) => setSt(e.target.value)} spellCheck={false} aria-label="well_static.csv" />
        <div className="row" style={{ marginTop: 10 }}>
          <input placeholder="What changed? (audit log)" value={comment} onChange={(e) => setComment(e.target.value)} style={{ flex: 1 }} />
          <button className="primary" disabled={save.isPending} onClick={() => save.mutate()}>Validate & save new version</button>
        </div>
      </Card>
      <Card title="Strapping tables" lead="CSV with columns level_mm, volume_m3 per tank (from the tank calibration certificate).">
        <div className="row">
          {Object.entries(q.data?.strapping ?? {}).map(([k, v]) => <span key={k} className="badge s-info">{k}: {v as number} rows</span>)}
        </div>
        <div className="row" style={{ marginTop: 10 }}>
          <input placeholder="Tank id, e.g. T-1" value={tank} onChange={(e) => setTank(e.target.value)} />
          <input type="file" accept=".csv" disabled={!tank} onChange={(e) => e.target.files?.[0] && strap(e.target.files[0])} />
        </div>
      </Card>
      <Card title="Versions">
        <DataTable rows={q.data?.versions ?? []} cols={[{ key: "id", label: "v", num: true }, { key: "created_at", label: "When", render: (r) => dt(r.created_at) },
          { key: "created_by", label: "By" }, { key: "comment", label: "Comment", wrap: true }, { key: "hash", label: "Hash", render: (r) => <code>{r.hash}</code> }]} />
      </Card>
    </div>
  );
}

function ConfigEditor() {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["/admin/config"], queryFn: () => api.get<Row>("/admin/config") });
  const [y, setY] = useState("");
  const [comment, setComment] = useState("");
  const [showDef, setShowDef] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => { if (q.data) setY(q.data.yaml_text); }, [q.data]);
  const check = async () => { try { await api.post("/admin/config/validate", { yaml_text: y }); setErr(null); toast("Configuration is valid"); } catch (e) { setErr(e); } };
  const save = useMutation({
    mutationFn: () => api.put("/admin/config", { yaml_text: y, comment }),
    onSuccess: (r: Row) => { toast(`Configuration v${r.version} saved (hash ${r.hash})`); setErr(null); setComment(""); qc.invalidateQueries(); },
    onError: (e) => setErr(e),
  });
  if (q.isLoading) return <Loading />;
  return (
    <div className="stack">
      <Card title="config.yaml" lead="Tolerances, uncertainties (U95 %), DVR α and twin settings. Any key left out uses the built-in default. Unknown keys and wrong types are rejected." right={<button className="sm" onClick={() => setShowDef(!showDef)}>{showDef ? "Hide" : "Show"} defaults</button>}>
        <div className={showDef ? "grid2" : ""}>
          <textarea rows={30} value={y} onChange={(e) => setY(e.target.value)} spellCheck={false} aria-label="config.yaml" />
          {showDef && <textarea rows={30} readOnly value={q.data?.defaults_yaml} aria-label="defaults" />}
        </div>
        <ErrorBox error={err} />
        <div className="row" style={{ marginTop: 10 }}>
          <input placeholder="What changed and why? (audit log)" value={comment} onChange={(e) => setComment(e.target.value)} style={{ flex: 1 }} />
          <button onClick={check}>Validate</button>
          <button className="primary" disabled={save.isPending} onClick={() => save.mutate()}>Save new version</button>
        </div>
      </Card>
      <Card title="Versions">
        <DataTable rows={q.data?.versions ?? []} cols={[{ key: "id", label: "v", num: true }, { key: "created_at", label: "When", render: (r) => dt(r.created_at) },
          { key: "created_by", label: "By" }, { key: "comment", label: "Comment", wrap: true }, { key: "hash", label: "Hash", render: (r) => <code>{r.hash}</code> }]} />
      </Card>
    </div>
  );
}

function Schedule() {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["/admin/schedule"], queryFn: () => api.get<Row>("/admin/schedule") });
  const [s, setS] = useState<Row | null>(null);
  useEffect(() => { if (q.data) setS(q.data); }, [q.data]);
  const save = useMutation({
    mutationFn: () => api.put("/admin/schedule", { enabled: s!.enabled, time: s!.time, days: s!.days, watch_enabled: s!.watch_enabled, backup_time: s!.backup_time, backup_keep: s!.backup_keep }),
    onSuccess: () => { toast("Schedule saved"); qc.invalidateQueries({ queryKey: ["/admin/schedule"] }); },
    onError: (e: Error) => toast(e.message, true),
  });
  const now = useMutation({ mutationFn: () => api.post("/admin/schedule/run-now"), onSuccess: (r: Row) => toast(`Run #${r.run_id} queued`), onError: (e: Error) => toast(e.message, true) });
  const backups = useQuery({ queryKey: ["/admin/backups"], queryFn: () => api.get<Row[]>("/admin/backups") });
  const bk = useMutation({ mutationFn: () => api.post("/admin/backup"), onSuccess: (r: Row) => { toast(`Backup ${r.file} written`); backups.refetch(); } });
  if (!s) return <Loading />;
  return (
    <div className="stack">
      <Card title="Daily run" lead="Validates the production day that ended at 06:00 and refreshes the twin. Skipped if the same day and inputs were already run; retried once 30 minutes later if it fails.">
        <div className="row" style={{ alignItems: "flex-end" }}>
          <label className="row small"><input type="checkbox" checked={s.enabled} onChange={(e) => setS({ ...s, enabled: e.target.checked })} /> enabled</label>
          <label className="f">Time<input type="time" value={s.time} onChange={(e) => setS({ ...s, time: e.target.value })} /></label>
          <label className="f">Days per run<input type="number" min={1} max={366} value={s.days} onChange={(e) => setS({ ...s, days: +e.target.value })} style={{ width: 90 }} /></label>
          <label className="row small"><input type="checkbox" checked={s.watch_enabled} onChange={(e) => setS({ ...s, watch_enabled: e.target.checked })} /> watch drop folder</label>
          <label className="f">Backup time<input type="time" value={s.backup_time} onChange={(e) => setS({ ...s, backup_time: e.target.value })} /></label>
          <label className="f">Keep backups<input type="number" min={1} value={s.backup_keep} onChange={(e) => setS({ ...s, backup_keep: +e.target.value })} style={{ width: 80 }} /></label>
          <button className="primary" onClick={() => save.mutate()}>Save</button>
          <button onClick={() => now.mutate()}>Run daily job now</button>
        </div>
        <div className="small muted" style={{ marginTop: 8 }}>Next: {Object.entries(q.data?.next_runs ?? {}).map(([k, v]) => `${k} ${dt(v as string)}`).join(" · ") || "scheduler off"} · drop folder <code>{q.data?.dropzone}</code></div>
      </Card>
      <Card title="Backups" lead="A consistent copy of the database. Restore with the service stopped: flowtwin restore <file>." right={<button onClick={() => bk.mutate()}>Back up now</button>}>
        <DataTable rows={backups.data ?? []} cols={[{ key: "file", label: "File" }, { key: "size", label: "Size kB", num: true, render: (r) => int(r.size / 1024) }]} empty="No backups yet." />
      </Card>
    </div>
  );
}

function Audit() {
  const [action, setAction] = useState("");
  const q = useQuery({ queryKey: [`/admin/audit?action=${action}`], queryFn: () => api.get<Row[]>(`/admin/audit?limit=1000&action=${action}`) });
  return (
    <Card lead="Every human action and configuration change. The log is never deleted.">
      <DataTable rows={q.data ?? []} search csvName="audit_log" tall tools={
        <select value={action} onChange={(e) => setAction(e.target.value)} aria-label="Action">
          <option value="">All actions</option>
          {["auth", "issue", "test", "recommendation", "allocation", "run", "data", "network", "config", "user", "twin", "schedule", "backup"].map((a) => <option key={a}>{a}</option>)}
        </select>} cols={[
        { key: "at", label: "When (UTC)", render: (r) => dt(r.at) }, { key: "user", label: "User" }, { key: "action", label: "Action" }, { key: "object", label: "Object" },
        { key: "after", label: "Details", wrap: true, render: (r) => <code style={{ whiteSpace: "pre-wrap", fontSize: 11.5 }}>{r.after ? JSON.stringify(r.after) : ""}</code>, csv: (r) => JSON.stringify(r.after) }]} />
    </Card>
  );
}

function System() {
  const q = useQuery({ queryKey: ["/admin/system"], queryFn: () => api.get<Row>("/admin/system") });
  if (!q.data) return <Loading />;
  const s = q.data;
  return (
    <Card>
      <KV items={[["FlowTwin", s.app_version], ["Engine (flowval)", s.engine_version], ["Data folder", <code>{s.data_dir}</code>],
                  ["Database", `${s.database} (${int((s.db_size ?? 0) / 1024)} kB)`], ["HTTPS", s.tls ? <Sev s="OK" /> : "off (set FLOWTWIN_TLS_CERT / _KEY)"],
                  ["Plant systems", "read-only by design; no write path exists (checked by an automated test)"]]} />
    </Card>
  );
}

export function Admin() {
  const { can } = useApp();
  const [tab, setTab] = useState<"users" | "network" | "config" | "schedule" | "audit" | "system">("users");
  if (!can("admin")) return <ErrorBox error="Admin only" />;
  return (
    <div>
      <PageHead title="Admin" />
      <Tabs value={tab} onChange={setTab} tabs={[["users", "Users"], ["network", "Network model"], ["config", "Configuration"], ["schedule", "Schedule & backup"], ["audit", "Audit log"], ["system", "System"]]} />
      {tab === "users" && <Users />}
      {tab === "network" && <NetworkEditor />}
      {tab === "config" && <ConfigEditor />}
      {tab === "schedule" && <Schedule />}
      {tab === "audit" && <Audit />}
      {tab === "system" && <System />}
    </div>
  );
}
