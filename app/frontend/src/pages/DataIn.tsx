import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DragEvent, useRef, useState } from "react";
import { api, Row } from "../api";
import { DataTable } from "../components/DataTable";
import { Card, ErrorBox, Loading, PageHead, Sev, State, Tabs, useToast } from "../components/ui";
import { useApp, useRunQuery } from "../context";
import { day, dt, human, int } from "../fmt";

type Part = { label: string; dataset: string | null; columns: string[]; rows: number; file: string };

function Wizard() {
  const qc = useQueryClient();
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  const templates = useQuery({ queryKey: ["/data/templates"], queryFn: () => api.get<Row>("/data/templates") });
  const saved = useQuery({ queryKey: ["/data/mappings"], queryFn: () => api.get<Row>("/data/mappings") });
  const [files, setFiles] = useState<File[]>([]);
  const [parts, setParts] = useState<Part[]>([]);
  const [hints, setHints] = useState<Record<string, string>>({});
  const [maps, setMaps] = useState<Record<string, Record<string, string>>>({});
  const [saveAs, setSaveAs] = useState("");
  const [batch, setBatch] = useState<Row | null>(null);
  const [over, setOver] = useState(false);
  const [err, setErr] = useState<unknown>(null);

  const pick = async (fl: FileList | File[]) => {
    const arr = Array.from(fl).filter((f) => /\.(csv|xlsx|xlsm)$/i.test(f.name));
    setFiles(arr); setBatch(null); setErr(null); setHints({}); setMaps({});
    const out: Part[] = [];
    for (const f of arr) {
      const fd = new FormData();
      fd.append("file", f);
      try {
        const r = await api.upload<Row[]>("/data/inspect", fd);
        r.forEach((p) => out.push({ ...(p as Part), file: f.name }));
      } catch (e) {
        setErr(e);
      }
    }
    setParts(out);
  };
  const tpl = templates.data ?? {};
  const missing = (p: Part) => {
    const ds = hints[p.label] ?? p.dataset;
    if (!ds || !tpl[ds]) return [];
    const mapped = new Set(Object.values(maps[p.label] ?? {}));
    return tpl[ds].required.filter((c: string) => !p.columns.includes(c) && !mapped.has(c));
  };
  const setMap = (label: string, src: string, target: string) =>
    setMaps((m) => ({ ...m, [label]: { ...(m[label] ?? {}), [src]: target } }));
  const applySaved = (label: string, name: string) => setMaps((m) => ({ ...m, [label]: { ...(saved.data?.[name] ?? {}) } }));

  const validate = useMutation({
    mutationFn: () => {
      const fd = new FormData();
      files.forEach((f) => fd.append("files", f));
      fd.append("hints", JSON.stringify(hints));
      const m = Object.fromEntries(Object.entries(maps).filter(([, v]) => Object.values(v).some(Boolean)));
      if (Object.keys(m).length) fd.append("mapping", JSON.stringify(m));
      if (saveAs) fd.append("save_mapping_as", saveAs);
      return api.upload<Row>("/data/upload", fd);
    },
    onSuccess: (b) => { setBatch(b); qc.invalidateQueries({ queryKey: ["/data/batches"] }); },
    onError: (e) => setErr(e),
  });
  const commit = useMutation({
    mutationFn: () => api.post(`/data/batches/${batch!.id}/commit`),
    onSuccess: () => { toast(`Batch ${batch!.id} committed. Start a run to use it.`); setBatch(null); setFiles([]); setParts([]); qc.invalidateQueries(); },
    onError: (e: Error) => toast(e.message, true),
  });
  const discard = useMutation({
    mutationFn: () => api.post(`/data/batches/${batch!.id}/discard`),
    onSuccess: () => { toast("Discarded"); setBatch(null); qc.invalidateQueries(); },
  });
  const onDrop = (e: DragEvent) => { e.preventDefault(); setOver(false); pick(e.dataTransfer.files); };

  return (
    <div className="stack">
      <Card title="1 · Choose files" lead="CSV files named after the templates (well_daily.csv, meter_daily.csv, …), or an Excel workbook with one sheet per template. Nothing is used until you commit.">
        <div className={`drop ${over ? "over" : ""}`} onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)} onDrop={onDrop}>
          Drop CSV / Excel files here, or <button onClick={() => input.current?.click()}>browse…</button>
          <input ref={input} type="file" multiple accept=".csv,.xlsx,.xlsm" hidden onChange={(e) => e.target.files && pick(e.target.files)} />
          {files.length > 0 && <div className="small" style={{ marginTop: 8 }}>{files.map((f) => f.name).join(", ")}</div>}
        </div>
      </Card>
      {parts.length > 0 && (
        <Card title="2 · Check templates and columns" lead="Pick the template for each file or sheet. If a required column has another name in your file, map it.">
          <div className="stack">
            {parts.map((p) => {
              const ds = hints[p.label] ?? p.dataset ?? "";
              const miss = missing(p);
              const unknown = ds && tpl[ds] ? p.columns.filter((c) => !tpl[ds].required.includes(c) && !tpl[ds].optional.includes(c)) : [];
              return (
                <div key={p.label} style={{ borderBottom: "1px solid var(--grid)", paddingBottom: 10 }}>
                  <div className="row">
                    <b>{p.label}</b><span className="muted small">{p.rows} rows</span>
                    <select value={ds} onChange={(e) => setHints((h) => ({ ...h, [p.label]: e.target.value }))} aria-label="Template">
                      <option value="">– which template? –</option>
                      {Object.keys(tpl).map((k) => <option key={k} value={k}>{k}</option>)}
                    </select>
                    {miss.length === 0 && ds ? <Sev s="OK" /> : <span className="badge s-warn">◆ missing: {miss.join(", ") || "template"}</span>}
                    {Object.keys(saved.data ?? {}).length > 0 && (
                      <select defaultValue="" onChange={(e) => e.target.value && applySaved(p.label, e.target.value)} aria-label="Saved mapping">
                        <option value="">Apply saved mapping…</option>
                        {Object.keys(saved.data ?? {}).map((k) => <option key={k}>{k}</option>)}
                      </select>
                    )}
                  </div>
                  {ds && unknown.length > 0 && (
                    <div className="row small" style={{ marginTop: 6 }}>
                      {unknown.map((c) => (
                        <label key={c} className="row" style={{ gap: 4 }}>
                          <code>{c}</code> →
                          <select value={maps[p.label]?.[c] ?? ""} onChange={(e) => setMap(p.label, c, e.target.value)}>
                            <option value="">(ignore)</option>
                            {[...tpl[ds].required, ...tpl[ds].optional].map((t: string) => <option key={t}>{t}</option>)}
                          </select>
                        </label>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
            <div className="row">
              <label className="f" style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>Save the mapping as
                <input placeholder="e.g. DPR sheet" value={saveAs} onChange={(e) => setSaveAs(e.target.value)} /></label>
              <span className="spacer" />
              <button className="primary" disabled={validate.isPending} onClick={() => validate.mutate()}>3 · Validate</button>
            </div>
          </div>
        </Card>
      )}
      <ErrorBox error={err} />
      {batch && (
        <Card title={<>Batch #{batch.id} <State s={batch.status} /></>}>
          <div className="stack">
            {batch.report.errors.length > 0 && <div className="banner err"><div><b>Rejected. Nothing will be imported.</b>{batch.report.errors.map((e: string) => <div key={e}>▲ {e}</div>)}</div></div>}
            {batch.report.warnings.length > 0 && <div className="banner warn"><div>{batch.report.warnings.map((e: string) => <div key={e}>◆ {e}</div>)}</div></div>}
            <DataTable rows={Object.entries(batch.datasets).map(([k, v]) => ({ dataset: k, ...(v as Row) }))} cols={[
              { key: "dataset", label: "Template" }, { key: "rows", label: "Rows", num: true }, { key: "new", label: "New keys", num: true },
              { key: "replaced", label: "Updates existing", num: true, title: "Rows whose key (e.g. date + well) is already in committed data: the columns in this file replace the stored values, other columns are kept" },
              { key: "first", label: "From", render: (r) => day(r.first) }, { key: "last", label: "To", render: (r) => day(r.last) }]} />
            {Object.entries(batch.preview ?? {}).map(([ds, rows]) => (
              <div key={ds}><h3>{ds}: first rows as stored</h3>
                <DataTable rows={rows as Row[]} cols={Object.keys((rows as Row[])[0] ?? {}).slice(0, 12).map((c) => ({ key: c, label: c }))} /></div>
            ))}
            {batch.status === "preview" && (
              <div className="row">
                <button className="primary" disabled={commit.isPending} onClick={() => commit.mutate()}>Commit batch</button>
                <button onClick={() => discard.mutate()}>Discard</button>
              </div>
            )}
          </div>
        </Card>
      )}
    </div>
  );
}

function Batches() {
  const q = useQuery({ queryKey: ["/data/batches"], queryFn: () => api.get<Row[]>("/data/batches") });
  if (q.isLoading) return <Loading />;
  return (
    <Card lead="Every upload, drop-zone import and seed. Committed data is never deleted; upload a correction instead. For the same key (e.g. date + well) a newer batch overrides only the columns it contains.">
      <DataTable rows={q.data ?? []} csvName="batches" cols={[
        { key: "id", label: "#", num: true }, { key: "at", label: "When", render: (r) => dt(r.at) }, { key: "source", label: "Source" },
        { key: "filename", label: "Files", wrap: true }, { key: "uploaded_by", label: "By" }, { key: "rows", label: "Rows", num: true },
        { key: "status", label: "Status", render: (r) => <State s={r.status} /> },
        { key: "datasets", label: "Templates", wrap: true, render: (r) => Object.keys(r.datasets ?? {}).join(", ") },
        { key: "report", label: "Errors", wrap: true, render: (r) => <span className="small">{(r.report?.errors ?? []).join("; ")}</span> }]} />
    </Card>
  );
}

function Coverage() {
  const cov = useQuery({ queryKey: ["/data/coverage"], queryFn: () => api.get<Row[]>("/data/coverage") });
  const sched = useQuery({ queryKey: ["/admin/schedule"], queryFn: () => api.get<Row>("/admin/schedule") });
  const tpl = useQuery({ queryKey: ["/data/templates"], queryFn: () => api.get<Row>("/data/templates") });
  return (
    <div className="stack">
      <Card title="Committed data per template">
        <DataTable rows={cov.data ?? []} cols={[
          { key: "dataset", label: "Template" }, { key: "required", label: "", render: (r) => (r.required ? "required" : <span className="muted">optional</span>) },
          { key: "rows", label: "Rows", num: true, render: (r) => int(r.rows) }, { key: "first", label: "From", render: (r) => day(r.first) },
          { key: "last", label: "To", render: (r) => day(r.last) },
          { key: "_t", label: "Template", render: (r) => <a href={`/api/v1/data/templates/${r.dataset}.csv`} download={`${r.dataset}.csv`}>empty CSV</a> }]} />
      </Card>
      <Card title="Watched drop folder" lead="Files placed in this folder on the server are imported automatically (if valid) and a twin run follows. Rejected files are moved to rejected/ with a report.">
        <code>{sched.data?.dropzone ?? "…"}</code> · {sched.data?.watch_enabled ? <Sev s="OK" /> : <span className="badge s-neutral">off</span>}
      </Card>
      <Card title="Template columns" lead="Column names carry their units (_m3, _sm3, _ksc, _degc, _pct). Liquids at 15 °C, gas at 15 °C / 101.325 kPa, pressure kg/cm² gauge.">
        <DataTable rows={Object.entries(tpl.data ?? {}).map(([k, v]) => ({ dataset: k, required: (v as Row).required.join(", "), optional: (v as Row).optional.join(", ") }))}
                   cols={[{ key: "dataset", label: "Template" }, { key: "required", label: "Required", wrap: true }, { key: "optional", label: "Optional", wrap: true, render: (r) => <span className="text2 small">{r.optional}</span> }]} />
      </Card>
    </div>
  );
}

function Quality() {
  const q = useRunQuery<Row>("/data/quality");
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data!;
  return (
    <div className="stack">
      <div className="tiles">{Object.entries(d.by_check).map(([k, v]) => <div className="tile" key={k}><div className="tl">{human(k)}</div><div className="tv">{v as number}</div></div>)}</div>
      <Card title="Data-quality findings in the selected run">
        <DataTable rows={d.findings} search csvName="data_quality" tall cols={[
          { key: "date", label: "Day", render: (r) => day(r.date) }, { key: "severity", label: "", render: (r) => <Sev s={r.severity} /> },
          { key: "object_id", label: "Object" }, { key: "check", label: "Check", render: (r) => human(r.check) },
          { key: "message", label: "Finding", wrap: true }, { key: "action", label: "Action", wrap: true, render: (r) => <span className="text2">{r.action}</span> }]} />
      </Card>
    </div>
  );
}

export function DataIn() {
  const { can } = useApp();
  const [tab, setTab] = useState<"upload" | "batches" | "coverage" | "quality">(can("engineer") ? "upload" : "coverage");
  return (
    <div>
      <PageHead title="Data in" sub="Daily DPR / SCADA data on the CSV templates. Upload → validate → preview → commit." />
      <Tabs value={tab} onChange={setTab} tabs={[...(can("engineer") ? [["upload", "Upload"]] as ["upload", string][] : []), ["batches", "Batches"], ["coverage", "Coverage & templates"], ["quality", "Data quality"]]} />
      {tab === "upload" && <Wizard />}
      {tab === "batches" && <Batches />}
      {tab === "coverage" && <Coverage />}
      {tab === "quality" && <Quality />}
    </div>
  );
}
