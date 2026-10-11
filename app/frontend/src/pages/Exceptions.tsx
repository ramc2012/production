import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, qs, Row } from "../api";
import { DataTable } from "../components/DataTable";
import { Card, Chips, Drawer, ErrorBox, KV, Loading, PageHead, Sev, State, Tabs, useToast } from "../components/ui";
import { useApp, useRunQuery } from "../context";
import { day, dt, human, LAYER, n } from "../fmt";

const LAYERS: [string, string][] = [["", "All layers"], ...Object.entries(LAYER)];

function objectLink(r: Row) {
  if (r.object_type === "well") return <Link to={`/wells/${r.object_id}`} onClick={(e) => e.stopPropagation()}>{r.object_id}</Link>;
  if (r.object_type === "meter") return <Link to={`/meters/${r.object_id}`} onClick={(e) => e.stopPropagation()}>{r.object_id}</Link>;
  return r.object_id;
}

function IssueDrawer({ id, onClose }: { id: number; onClose: () => void }) {
  const { can } = useApp();
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: [`/issues/${id}`], queryFn: () => api.get<Row>(`/issues/${id}`) });
  const users = useQuery({ queryKey: ["/users/names"], queryFn: () => api.get<Row[]>("/users/names") });
  const [comment, setComment] = useState("");
  const [assignee, setAssignee] = useState<string>("");
  const [due, setDue] = useState("");
  useEffect(() => {
    if (q.data) { setAssignee(q.data.assignee ?? ""); setDue(q.data.due ?? ""); }
  }, [q.data]);
  const m = useMutation({
    mutationFn: (body: Row) => api.patch(`/issues/${id}`, body),
    onSuccess: () => { setComment(""); qc.invalidateQueries(); toast("Issue updated"); },
    onError: (e: Error) => toast(e.message, true),
  });
  if (q.isLoading) return <Drawer onClose={onClose} label="Issue"><Loading /></Drawer>;
  if (q.error || !q.data) return <Drawer onClose={onClose} label="Issue"><ErrorBox error={q.error} /></Drawer>;
  const i = q.data;
  const next: [string, string][] = ({ open: [["acknowledged", "Acknowledge"], ["resolved", "Resolve"]],
    acknowledged: [["resolved", "Resolve"], ["open", "Re-open"]], resolved: [["open", "Re-open"]] } as Record<string, [string, string][]>)[i.state] ?? [];
  return (
    <Drawer onClose={onClose} label={`Issue ${i.id}`}>
      <div className="stack">
        <div className="row"><Sev s={i.severity} /><State s={i.state} /><span className="muted small">Issue #{i.id}</span></div>
        <h2 style={{ fontSize: 18 }}>{i.object_id} · {human(i.check)}</h2>
        <p style={{ margin: 0 }}>{i.message}</p>
        {i.action && <div className="banner info"><b>Recommended action:</b> {i.action}</div>}
        <KV items={[["Layer", LAYER[i.layer] ?? i.layer], ["First seen", day(i.first_seen)], ["Last seen", day(i.last_seen)],
                    ["Days reported", i.occurrences], ["Assignee", i.assignee ?? "–"], ["Due", i.due ? day(i.due) : "–"],
                    ["Updated", dt(i.updated_at)]]} />
        {can("engineer") && (
          <Card title="Update">
            <div className="stack">
              <div className="row">
                <label className="f">Assignee
                  <select value={assignee} onChange={(e) => setAssignee(e.target.value)}>
                    <option value="">– unassigned –</option>
                    {(users.data ?? []).map((u) => <option key={u.username} value={u.username}>{u.full_name || u.username}</option>)}
                  </select>
                </label>
                <label className="f">Due<input type="date" value={due} onChange={(e) => setDue(e.target.value)} /></label>
                <button style={{ alignSelf: "flex-end" }} onClick={() => m.mutate(assignee ? { assignee, due, comment: comment || undefined } : { clear_assignee: true, due, comment: comment || undefined })}>Save</button>
              </div>
              <textarea rows={3} placeholder="Comment (required to resolve)" value={comment} onChange={(e) => setComment(e.target.value)}
                        style={{ fontFamily: "inherit", fontSize: 13 }} />
              <div className="row">
                {next.map(([s, label]) => (
                  <button key={s} className={s === "resolved" ? "primary" : ""} disabled={m.isPending}
                          onClick={() => m.mutate({ state: s, comment: comment || undefined })}>{label}</button>
                ))}
              </div>
            </div>
          </Card>
        )}
        <h3>Comments</h3>
        {i.comments.length === 0 ? <div className="muted small">No comments yet.</div> :
          i.comments.map((c: Row, k: number) => (
            <div key={k} className="small" style={{ borderLeft: "3px solid var(--line)", paddingLeft: 10 }}>
              <div className="muted">{c.user} · {dt(c.at)}</div>{c.text}
            </div>
          ))}
        <h3>History (one line per production day)</h3>
        <DataTable rows={i.findings} cols={[
          { key: "date", label: "Day", render: (r) => day(r.date) }, { key: "severity", label: "", render: (r) => <Sev s={r.severity} /> },
          { key: "message", label: "Finding", wrap: true }, { key: "run_id", label: "Run", num: true }]} />
      </div>
    </Drawer>
  );
}

function IssuesTab() {
  const { can } = useApp();
  const qc = useQueryClient();
  const toast = useToast();
  const [sp, setSp] = useSearchParams();
  const [state, setState] = useState("open,acknowledged");
  const [sev, setSev] = useState("");
  const [layer, setLayer] = useState("");
  const [sel, setSel] = useState<Set<number>>(new Set());
  const url = "/issues" + qs({ state, severity: sev, layer });
  const q = useQuery({ queryKey: [url], queryFn: () => api.get<Row[]>(url) });
  const open = Number(sp.get("issue")) || null;
  const bulk = useMutation({
    mutationFn: (s: string) => api.post("/issues/bulk", { ids: [...sel], state: s }),
    onSuccess: (r: Row) => { toast(`${r.updated.length} updated${r.skipped.length ? `, ${r.skipped.length} skipped` : ""}`); setSel(new Set()); qc.invalidateQueries(); },
  });
  const toggle = (id: number) => setSel((s) => { const x = new Set(s); x.has(id) ? x.delete(id) : x.add(id); return x; });
  return (
    <div className="stack">
      <div className="row">
        <Chips value={state} onChange={setState} options={[["open,acknowledged", "Open + acknowledged"], ["open", "Open"], ["acknowledged", "Acknowledged"], ["resolved", "Resolved"], ["", "All"]]} />
        <Chips value={sev} onChange={setSev} options={[["", "All severities"], ["ALARM", "Alarms"], ["WARN", "Warnings"]]} />
        <select value={layer} onChange={(e) => setLayer(e.target.value)} aria-label="Layer">
          {LAYERS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
      </div>
      {can("engineer") && sel.size > 0 && (
        <div className="banner info row">
          <b>{sel.size} selected</b>
          <button className="sm" onClick={() => bulk.mutate("acknowledged")}>Acknowledge</button>
          <button className="sm" onClick={() => setSel(new Set())}>Clear</button>
          <span className="small muted">Resolving needs a comment: open the issue.</span>
        </div>
      )}
      {q.isLoading ? <Loading /> : q.error ? <ErrorBox error={q.error} /> : (
        <DataTable rows={q.data!} search csvName="issues" tall rowKey={(r) => r.id} selected={open}
                   onRow={(r) => setSp({ issue: String(r.id) })}
                   empty="No issues match these filters."
                   cols={[
                     ...(can("engineer") ? [{ key: "_sel", label: "", render: (r: Row) => (
                       <input type="checkbox" aria-label={`select issue ${r.id}`} checked={sel.has(r.id)}
                              onClick={(e) => e.stopPropagation()} onChange={() => toggle(r.id)} />) }] : []),
                     { key: "severity", label: "Severity", render: (r) => <Sev s={r.severity} />, sort: (r) => (r.severity === "ALARM" ? 0 : 1) },
                     { key: "state", label: "State", render: (r) => <State s={r.state} /> },
                     { key: "object_id", label: "Object", render: objectLink },
                     { key: "check", label: "Check", render: (r) => human(r.check) },
                     { key: "message", label: "Latest finding", wrap: true },
                     { key: "last_seen", label: "Last seen", render: (r) => day(r.last_seen) },
                     { key: "occurrences", label: "Days", num: true },
                     { key: "assignee", label: "Assignee" },
                     { key: "due", label: "Due", render: (r) => (r.due ? day(r.due) : "") },
                   ]} />
      )}
      {open && <IssueDrawer id={open} onClose={() => setSp({})} />}
    </div>
  );
}

function FindingsTab() {
  const [sev, setSev] = useState("ALARM,WARN");
  const [layer, setLayer] = useState("");
  const q = useRunQuery<Row>("/findings", { severity: sev, layer });
  return (
    <div className="stack">
      <p className="lead">Every finding of the selected run, one line per production day, exactly as the engine reported it.</p>
      <div className="row">
        <Chips value={sev} onChange={setSev} options={[["ALARM,WARN", "Alarms + warnings"], ["ALARM", "Alarms"], ["WARN", "Warnings"], ["INFO", "Info"], ["", "All"]]} />
        <select value={layer} onChange={(e) => setLayer(e.target.value)} aria-label="Layer">
          {LAYERS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
      </div>
      {q.isLoading ? <Loading /> : q.error ? <ErrorBox error={q.error} /> : (
        <DataTable rows={q.data!.findings} search csvName="findings" tall
                   cols={[
                     { key: "date", label: "Day", render: (r) => day(r.date) },
                     { key: "severity", label: "Severity", render: (r) => <Sev s={r.severity} /> },
                     { key: "layer", label: "Layer", render: (r) => LAYER[r.layer] ?? r.layer },
                     { key: "object_id", label: "Object", render: objectLink },
                     { key: "check", label: "Check", render: (r) => human(r.check) },
                     { key: "message", label: "Finding", wrap: true },
                     { key: "value", label: "Value", num: true, render: (r) => n(r.value, 2) },
                     { key: "limit", label: "Limit" },
                     { key: "action", label: "Action", wrap: true, render: (r) => <span className="text2">{r.action}</span> },
                   ]} />
      )}
    </div>
  );
}

function RegisterTab() {
  const q = useRunQuery<Row>("/register");
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  return (
    <div className="stack">
      <p className="lead">The consolidated register of the run (as in the HTML report): repeated daily findings merged into one line.</p>
      <DataTable rows={q.data!.register} search csvName="register" tall
                 cols={[
                   { key: "severity", label: "Severity", render: (r) => <Sev s={r.severity} />, sort: (r) => -r.rank },
                   { key: "first_date", label: "From", render: (r) => day(r.first_date) },
                   { key: "last_date", label: "To", render: (r) => day(r.last_date) },
                   { key: "days", label: "Days", num: true },
                   { key: "layer", label: "Layer", render: (r) => LAYER[r.layer] ?? r.layer },
                   { key: "object_id", label: "Object", render: objectLink },
                   { key: "check", label: "Check", render: (r) => human(r.check) },
                   { key: "message", label: "Finding", wrap: true },
                   { key: "action", label: "Action", wrap: true, render: (r) => <span className="text2">{r.action}</span> },
                 ]} />
    </div>
  );
}

export function Exceptions() {
  const [tab, setTab] = useState<"issues" | "findings" | "register">("issues");
  return (
    <div>
      <PageHead title="Exceptions" sub="Engine findings become tickets: open → acknowledged → resolved. Repeats update the same ticket; a resolved ticket re-opens if the finding returns." />
      <Tabs value={tab} onChange={setTab} tabs={[["issues", "Issues (workflow)"], ["findings", "Daily findings"], ["register", "Consolidated register"]]} />
      {tab === "issues" ? <IssuesTab /> : tab === "findings" ? <FindingsTab /> : <RegisterTab />}
    </div>
  );
}
