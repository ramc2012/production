import { useNavigate } from "react-router-dom";
import type { Row } from "../api";
import { LIFT, n } from "../fmt";

const ICON: Record<string, string> = { ALARM: "▲", WARN: "◆", INFO: "●", OK: "✓" };
const LABEL: Record<string, string> = { ALARM: "Alarm", WARN: "Warning", INFO: "Info", OK: "OK" };

/** wells → separators → tanks → dispositions, each node coloured by its worst finding on the run's last day. */
export function NetworkDiagram({ net }: { net: Row }) {
  const nav = useNavigate();
  const wells: Row[] = net.wells ?? [];
  const seps: Row[] = net.separators ?? [];
  const tanks: Row[] = net.tanks ?? [];
  const rowH = 36, top = 40;
  const H = Math.max(wells.length * rowH + top + 20, 470);
  const X = { well: 16, sep: 330, tank: 570, out: 800 };
  const W = 990;
  const pos: Record<string, [number, number, number, number]> = {};
  const nodes: JSX.Element[] = [];

  const node = (id: string, x: number, y: number, w: number, h: number, title: string, sub: string, sev: string, to?: string) => {
    sev = (sev || "OK").toUpperCase();
    pos[id] = [x, y, w, h];
    nodes.push(
      <g key={id} className="node" tabIndex={0} role="link" aria-label={`${title}, ${LABEL[sev] ?? sev}`}
         onClick={() => to && nav(to)} onKeyDown={(e) => e.key === "Enter" && to && nav(to)}>
        <title>{`${title} – ${LABEL[sev] ?? sev}`}</title>
        <rect x={x} y={y} width={w} height={h} rx={6} className="nbox" />
        <rect x={x} y={y} width={5} height={h} rx={2} className={`sbar s-${sev.toLowerCase()}`} />
        <text x={x + 14} y={y + h / 2 - 2} className="nt">{title}</text>
        <text x={x + 14} y={y + h / 2 + 11} className="ns">{sub}</text>
        <text x={x + w - 10} y={y + h / 2 + 4} className={`ni s-${sev.toLowerCase()}-t`} textAnchor="end">{ICON[sev] ?? "●"}</text>
      </g>,
    );
  };

  wells.forEach((w, i) => {
    const oil = typeof w.dvr_oil_m3 === "number" ? ` · ${n(w.dvr_oil_m3, 1)} m³` : "";
    const off = w.hours_on === 0 ? " · off" : "";
    node(w.id, X.well, top + i * rowH, 220, 30, w.id, `${LIFT[w.lift] ?? w.lift} → ${w.separator}${oil}${off}`, w.status, `/wells/${w.id}`);
  });
  const gap = (k: number) => (H - top - 40) / Math.max(k, 1);
  seps.forEach((s, i) => {
    node(s.id, X.sep, top + i * gap(seps.length) + gap(seps.length) / 2 - 35, 190, 70, s.id,
         `${s.kind} · ${s.phases}-phase`, s.status, `/separators`);
  });
  tanks.forEach((t, i) => {
    const fill = t.last?.close_level_mm && t.safe_fill_mm ? ` · ${Math.round((t.last.close_level_mm / t.safe_fill_mm) * 100)} % full` : "";
    node(t.id, X.tank, top + i * gap(tanks.length) + gap(tanks.length) / 2 - 30, 180, 60, t.id, `dip gauged${fill}`, t.status, `/tanks`);
  });
  const outs: [string, string, string, string, string][] = [];
  if (net.dispatch?.meter) outs.push(["dispatch", `Dispatch ${net.dispatch.meter}`, "oil to CTF", net.dispatch.status, `/meters/${net.dispatch.meter}`]);
  for (const g of net.gas_disposal ?? []) outs.push([g.meter, `${g.meter} ${g.role ?? ""}`, "gas", g.status, `/meters/${g.meter}`]);
  outs.push(["water", "Water to ETP", "water legs, tank drains", "OK", ""]);
  outs.forEach(([id, t, sub, sev, to], i) => node(id, X.out, top + i * gap(outs.length) + gap(outs.length) / 2 - 26, 175, 52, t, sub, sev, to || undefined));

  const edges: JSX.Element[] = [];
  const edge = (a: string, b: string, cls = "e") => {
    if (!pos[a] || !pos[b]) return;
    const [xa, ya, wa, ha] = pos[a];
    const [xb, yb, , hb] = pos[b];
    const x1 = xa + wa, y1 = ya + ha / 2, x2 = xb, y2 = yb + hb / 2, mx = (x1 + x2) / 2;
    edges.push(<path key={`${a}-${b}-${cls}`} className={cls} d={`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`} />);
  };
  const ts = seps.find((s) => s.kind === "test")?.id;
  for (const w of wells) {
    edge(w.id, w.separator);
    if (ts) edge(w.id, ts, "e test");
  }
  for (const s of seps) {
    for (const t of tanks) edge(s.id, t.id);
    for (const g of net.gas_disposal ?? []) edge(s.id, g.meter, "e gas");
    edge(s.id, "water", "e water");
  }
  for (const t of tanks) {
    edge(t.id, "dispatch");
    edge(t.id, "water", "e water");
  }
  const heads: [keyof typeof X, string][] = [["well", "Wells"], ["sep", "Separators"], ["tank", "Storage tanks"], ["out", "Dispositions"]];
  return (
    <div className="netwrap">
      <svg className="net" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Flow network status">
        {heads.map(([k, t]) => <text key={k} className="colh" x={X[k]} y={22}>{t}</text>)}
        {edges}
        {nodes}
      </svg>
      <div className="legend">
        <span className="badge s-ok">✓ OK</span><span className="badge s-warn">◆ Warning</span><span className="badge s-alarm">▲ Alarm</span>
        <span><span className="lk" style={{ borderColor: "var(--line)" }} />liquid</span>
        <span><span className="lk dash" style={{ borderColor: "var(--line)" }} />test separator rotation</span>
        <span className="muted">Click a node for details.</span>
      </div>
    </div>
  );
}
