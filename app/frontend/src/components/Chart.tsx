import { ReactNode, useMemo, useRef, useState } from "react";
import type { Row } from "../api";
import { n } from "../fmt";

/* One small chart component for the whole app (no chart library, nothing to load from the web).
   Rules from the dataviz guidance: one y-axis only; 2px lines; bars ≤ 24px with a 4px rounded
   data-end; recessive solid gridlines; legend whenever there are ≥ 2 series; crosshair tooltip;
   text in text tokens, never in series colours. */

export type Series = {
  key: string;
  label: string;
  color: string;
  type?: "line" | "bar" | "dots";
  dash?: boolean;
  fmt?: (v: number) => string;
};
export type Band = { lo: string; hi: string; color: string; label: string };

type Props = {
  data: Row[];
  x: string;
  xNumeric?: boolean;
  xFmt?: (v: any) => string;
  series: Series[];
  bands?: Band[];
  refBand?: [number, number];
  refBandLabel?: string;
  refLines?: { y: number; label: string }[];
  vline?: { x: any; label: string };
  yLabel?: string;
  yFmt?: (v: number) => string;
  zero?: boolean;
  height?: number;
  legendExtra?: ReactNode;
  ariaLabel?: string;
  onPick?: (row: Row) => void;        // click on the plot selects the nearest point (e.g. a day)
};

function niceTicks(lo: number, hi: number, count = 5): number[] {
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return [0, 1];
  if (lo === hi) {
    const d = Math.abs(lo) * 0.1 || 1;
    lo -= d;
    hi += d;
  }
  const raw = (hi - lo) / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const start = Math.floor(lo / step) * step;
  const out = [];
  for (let v = start; v <= hi + step * 0.5; v += step) out.push(+v.toFixed(10));
  return out;
}

const W = 720;
const M = { l: 56, r: 14, t: 10, b: 26 };

export function Chart(p: Props) {
  const H = p.height ?? 220;
  const ref = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const fmtY = p.yFmt ?? ((v: number) => (Math.abs(v) >= 1000 ? Math.round(v).toLocaleString("en-IN") : n(v, Math.abs(v) < 10 ? 2 : 1)));
  const xs = p.data.map((r) => r[p.x]);

  const geo = useMemo(() => {
    const vals: number[] = [];
    for (const r of p.data) {
      for (const s of p.series) if (typeof r[s.key] === "number") vals.push(r[s.key]);
      for (const b of p.bands ?? []) for (const k of [b.lo, b.hi]) if (typeof r[k] === "number") vals.push(r[k]);
    }
    if (p.refBand) vals.push(...p.refBand);
    for (const l of p.refLines ?? []) vals.push(l.y);
    const hasBar = p.series.some((s) => s.type === "bar");
    let lo = vals.length ? Math.min(...vals) : 0;
    let hi = vals.length ? Math.max(...vals) : 1;
    if (p.zero || hasBar) lo = Math.min(0, lo);
    const pad = (hi - lo) * 0.06 || 1;
    if (!(p.zero || hasBar)) lo -= pad;
    hi += pad;
    const ticks = niceTicks(lo, hi);
    const y0 = ticks[0], y1 = ticks[ticks.length - 1];
    const iw = W - M.l - M.r, ih = H - M.t - M.b;
    let xpos: (i: number) => number;
    let xmin = 0, xmax = 1;
    if (p.xNumeric) {
      const nx = xs.filter((v) => typeof v === "number") as number[];
      xmin = Math.min(...nx);
      xmax = Math.max(...nx);
      xpos = (i: number) => M.l + ((xs[i] - xmin) / (xmax - xmin || 1)) * iw;
    } else {
      const slot = iw / Math.max(xs.length, 1);
      xpos = (i: number) => M.l + slot * (i + 0.5);
    }
    const ypos = (v: number) => M.t + ih - ((v - y0) / (y1 - y0 || 1)) * ih;
    const slot = iw / Math.max(xs.length, 1);
    return { ticks, y0, y1, iw, ih, xpos, ypos, slot, xmin, xmax };
  }, [p.data, p.series, p.bands, p.refBand, p.refLines, p.zero, p.xNumeric, H, xs]);

  if (!p.data.length) return <div className="empty">No data for this chart.</div>;
  const { ticks, xpos, ypos, ih, slot } = geo;
  const fx = p.xFmt ?? ((v: any) => String(v));
  const nLabels = Math.min(8, xs.length);
  const every = Math.max(1, Math.ceil(xs.length / nLabels));
  const bars = p.series.filter((s) => s.type === "bar");
  const bw = Math.min(24, (slot * 0.7) / Math.max(bars.length, 1));

  const path = (key: string) => {
    let d = "", pen = false;
    p.data.forEach((r, i) => {
      const v = r[key];
      if (typeof v !== "number") { pen = false; return; }
      d += `${pen ? "L" : "M"}${xpos(i).toFixed(1)},${ypos(v).toFixed(1)}`;
      pen = true;
    });
    return d;
  };
  const bandPath = (b: Band) => {
    const pts = p.data.map((r, i) => [i, r[b.lo], r[b.hi]] as const).filter(([, a, c]) => typeof a === "number" && typeof c === "number");
    if (!pts.length) return "";
    const top = pts.map(([i, , h]) => `${xpos(i).toFixed(1)},${ypos(h as number).toFixed(1)}`).join("L");
    const bot = [...pts].reverse().map(([i, l]) => `${xpos(i).toFixed(1)},${ypos(l as number).toFixed(1)}`).join("L");
    return `M${top}L${bot}Z`;
  };
  const barPath = (x: number, y: number, w: number, base: number) => {
    const h = base - y;
    if (Math.abs(h) < 0.5) return "";
    const r = Math.min(4, Math.abs(h), w / 2);
    if (h > 0) return `M${x},${base}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${base}Z`;
    return `M${x},${base}V${y - r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y - r}V${base}Z`;
  };

  const nearest = (e: React.MouseEvent<SVGRectElement>) => {
    const svg = e.currentTarget.ownerSVGElement!;
    const pt = svg.createSVGPoint();
    pt.x = e.clientX;
    pt.y = e.clientY;
    const loc = pt.matrixTransform(svg.getScreenCTM()!.inverse());
    let best = 0, bd = Infinity;
    xs.forEach((_, i) => {
      const d = Math.abs(xpos(i) - loc.x);
      if (d < bd) { bd = d; best = i; }
    });
    return best;
  };
  const onMove = (e: React.MouseEvent<SVGRectElement>) => setHover(nearest(e));

  const hv = hover !== null ? p.data[hover] : null;
  const tipLeft = hover !== null ? (xpos(hover) / W) * 100 : 0;
  const zeroY = ypos(Math.max(geo.y0, Math.min(0, geo.y1)));

  return (
    <div className="chart" ref={ref}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={p.ariaLabel ?? p.series.map((s) => s.label).join(", ")}>
        {p.refBand && (
          <rect x={M.l} width={geo.iw} y={ypos(p.refBand[1])} height={Math.max(0, ypos(p.refBand[0]) - ypos(p.refBand[1]))}
                fill="var(--band)" />
        )}
        {ticks.map((t) => (
          <g key={t}>
            <line className="gl" x1={M.l} x2={W - M.r} y1={ypos(t)} y2={ypos(t)} />
            <text className="ax" x={M.l - 6} y={ypos(t) + 3.5} textAnchor="end">{fmtY(t)}</text>
          </g>
        ))}
        {p.yLabel && <text className="ax" x={12} y={M.t + ih / 2} transform={`rotate(-90 12 ${M.t + ih / 2})`} textAnchor="middle">{p.yLabel}</text>}
        {p.xNumeric
          ? niceTicks(geo.xmin, geo.xmax, 6).filter((t) => t >= geo.xmin && t <= geo.xmax).map((t) => (
              <text key={t} className="ax" x={M.l + ((t - geo.xmin) / (geo.xmax - geo.xmin || 1)) * geo.iw} y={H - 7} textAnchor="middle">{fx(t)}</text>
            ))
          : xs.map((_, i) => {
              const last = xs.length - 1;
              // label every n-th point, plus the last one when it does not crowd its neighbour
              const show = i % every === 0 ? !(i !== last && last - i < every * 0.6) : i === last && last % every >= every * 0.6;
              return show ? <text key={i} className="ax" x={xpos(i)} y={H - 7} textAnchor="middle">{fx(xs[i])}</text> : null;
            })}
        {(p.refLines ?? []).map((l) => (
          <g key={l.label}>
            <line x1={M.l} x2={W - M.r} y1={ypos(l.y)} y2={ypos(l.y)} stroke="var(--text2)" strokeDasharray="5 4" strokeWidth={1.2} />
            <text className="ax" x={W - M.r} y={ypos(l.y) - 4} textAnchor="end">{l.label}</text>
          </g>
        ))}
        {(p.bands ?? []).map((b) => <path key={b.label} d={bandPath(b)} fill={b.color} opacity={0.14} />)}
        {bars.map((s, si) =>
          p.data.map((r, i) => typeof r[s.key] === "number" ? (
            <path key={`${s.key}${i}`} fill={s.color}
                  d={barPath(xpos(i) - (bw * bars.length) / 2 + si * bw + 1, ypos(r[s.key]), bw - 2, zeroY)} />
          ) : null))}
        {p.series.filter((s) => s.type !== "bar" && s.type !== "dots").map((s) => (
          <path key={s.key} d={path(s.key)} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round"
                strokeDasharray={s.dash ? "6 4" : undefined} />
        ))}
        {p.series.filter((s) => s.type === "dots").map((s) =>
          p.data.map((r, i) => typeof r[s.key] === "number" ? (
            <circle key={`${s.key}${i}`} cx={xpos(i)} cy={ypos(r[s.key])} r={4.5} fill={s.color} stroke="var(--surface)" strokeWidth={2} />
          ) : null))}
        {p.vline && (() => {
          const i = xs.indexOf(p.vline.x);
          if (i < 0) return null;
          return (
            <g>
              <line x1={xpos(i)} x2={xpos(i)} y1={M.t} y2={M.t + ih} stroke="var(--text2)" strokeDasharray="3 3" />
              <text className="ax" x={xpos(i) + 4} y={M.t + 10}>{p.vline.label}</text>
            </g>
          );
        })()}
        {hover !== null && (
          <g pointerEvents="none">
            <line x1={xpos(hover)} x2={xpos(hover)} y1={M.t} y2={M.t + ih} stroke="var(--muted)" strokeWidth={1} />
            {p.series.filter((s) => s.type !== "bar").map((s) => typeof hv?.[s.key] === "number" ? (
              <circle key={s.key} cx={xpos(hover)} cy={ypos(hv![s.key])} r={4.5} fill={s.color} stroke="var(--surface)" strokeWidth={2} />
            ) : null)}
          </g>
        )}
        <rect x={M.l} y={M.t} width={geo.iw} height={ih} fill="transparent" onMouseMove={onMove} onMouseLeave={() => setHover(null)}
              style={{ cursor: p.onPick ? "pointer" : undefined }} onClick={(e) => p.onPick?.(p.data[nearest(e)])} />
      </svg>
      {hv && (
        <div className="tip" style={{ left: `${tipLeft}%`, top: 8, transform: `translateX(${tipLeft > 60 ? "-105%" : "8%"})` }}>
          <div style={{ fontWeight: 600, marginBottom: 3 }}>{fx(hv[p.x])}</div>
          {p.series.map((s) => (
            <div className="r" key={s.key}>
              <span><span className="k" style={{ borderColor: s.color, borderTopStyle: s.dash ? "dashed" : "solid" }} />{s.label}</span>
              <b>{typeof hv[s.key] === "number" ? (s.fmt ?? fmtY)(hv[s.key]) : "–"}</b>
            </div>
          ))}
          {(p.bands ?? []).map((b) => typeof hv[b.lo] === "number" ? (
            <div className="r" key={b.label}>
              <span className="muted">{b.label}</span>
              <b>{fmtY(hv[b.lo])} – {fmtY(hv[b.hi])}</b>
            </div>
          ) : null)}
        </div>
      )}
      {(p.series.length > 1 || p.bands?.length || p.refBandLabel || p.legendExtra) && (
        <div className="legend">
          {p.series.map((s) => (
            <span key={s.key}>
              <span className={`lk ${s.type === "bar" || s.type === "dots" ? "box" : ""} ${s.dash ? "dash" : ""}`}
                    style={{ borderColor: s.color, background: s.type === "bar" || s.type === "dots" ? s.color : undefined }} />
              {s.label}
            </span>
          ))}
          {(p.bands ?? []).map((b) => (
            <span key={b.label}><span className="lk box" style={{ background: b.color, opacity: 0.3 }} />{b.label}</span>
          ))}
          {p.refBandLabel && <span><span className="lk box" style={{ background: "var(--band)", outline: "1px solid var(--line)" }} />{p.refBandLabel}</span>}
          {p.legendExtra}
        </div>
      )}
    </div>
  );
}
