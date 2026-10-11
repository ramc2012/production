import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from "react";

// ---------------------------------------------------------------- severity & state badges
const ICON: Record<string, string> = { ALARM: "▲", WARN: "◆", INFO: "●", OK: "✓" };
const LABEL: Record<string, string> = { ALARM: "Alarm", WARN: "Warning", INFO: "Info", OK: "OK" };

export function Sev({ s }: { s?: string | null }) {
  const k = (s || "OK").toUpperCase();
  return (
    <span className={`badge s-${k.toLowerCase()}`}>
      <span aria-hidden>{ICON[k] ?? "●"}</span> {LABEL[k] ?? k}
    </span>
  );
}

const STATE: Record<string, [string, string]> = {
  open: ["s-alarm", "Open"], acknowledged: ["s-warn", "Acknowledged"], resolved: ["s-ok", "Resolved"],
  proposed: ["s-info", "Proposed"], accepted: ["s-ok", "Accepted"], declined: ["s-neutral", "Declined"],
  implemented: ["s-ok", "Implemented"], expired: ["s-neutral", "Expired"],
  done: ["s-ok", "Done"], running: ["s-warn", "Running"], queued: ["s-info", "Queued"], failed: ["s-alarm", "Failed"],
  cancelled: ["s-neutral", "Cancelled"], committed: ["s-ok", "Committed"], preview: ["s-warn", "Preview"],
  rejected: ["s-alarm", "Rejected"], discarded: ["s-neutral", "Discarded"],
  achieved: ["s-ok", "Achieved"], partial: ["s-warn", "Partial"], "not achieved": ["s-neutral", "Not achieved"],
  met: ["s-ok", "Met"], missing: ["s-alarm", "Missing"],
  ACCEPTED: ["s-ok", "Accepted"], ACCEPTED_W: ["s-ok", "Accepted (warn)"], PROVISIONAL: ["s-warn", "Provisional"],
  REJECTED: ["s-alarm", "Rejected"], FALLBACK: ["s-alarm", "Fallback"], NONE: ["s-neutral", "None"],
};
export function State({ s }: { s?: string | null }) {
  const [cls, label] = STATE[s ?? ""] ?? ["s-neutral", s ?? "–"];
  const icon = cls === "s-ok" ? "✓" : cls === "s-alarm" ? "▲" : cls === "s-warn" ? "◆" : "●";
  return (
    <span className={`badge ${cls}`}>
      <span aria-hidden>{icon}</span> {label}
    </span>
  );
}

// ---------------------------------------------------------------- layout bits
export function Tile(p: { label: string; value: ReactNode; unit?: string; sub?: ReactNode; badge?: ReactNode }) {
  return (
    <div className="tile">
      {p.badge}
      <div className="tl">{p.label}</div>
      <div className="tv">
        {p.value}
        {p.unit && <small>{p.unit}</small>}
      </div>
      {p.sub && <div className="ts">{p.sub}</div>}
    </div>
  );
}

export function Card(p: { title?: ReactNode; lead?: ReactNode; right?: ReactNode; children?: ReactNode; className?: string }) {
  return (
    <section className={`card ${p.className ?? ""}`}>
      {(p.title || p.right) && (
        <div className="row" style={{ marginBottom: p.lead ? 2 : 10 }}>
          {p.title && <h2>{p.title}</h2>}
          <span className="spacer" />
          {p.right}
        </div>
      )}
      {p.lead && <p className="lead">{p.lead}</p>}
      {p.children}
    </section>
  );
}

export function PageHead(p: { title: string; sub?: ReactNode; right?: ReactNode }) {
  return (
    <div className="page-head">
      <div>
        <h1>{p.title}</h1>
        {p.sub && <div className="sub">{p.sub}</div>}
      </div>
      <span className="spacer" />
      {p.right}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Loading({ what = "Loading" }: { what?: string }) {
  return <div className="muted" style={{ padding: 20 }}>{what}…</div>;
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  return <div className="banner err" role="alert">{error instanceof Error ? error.message : String(error)}</div>;
}

export function Tabs<T extends string>(p: { tabs: [T, ReactNode][]; value: T; onChange: (v: T) => void }) {
  return (
    <div className="tabs" role="tablist">
      {p.tabs.map(([k, label]) => (
        <button key={k} role="tab" aria-selected={p.value === k} onClick={() => p.onChange(k)}>
          {label}
        </button>
      ))}
    </div>
  );
}

export function Chips<T extends string>(p: { options: [T, string][]; value: T; onChange: (v: T) => void }) {
  return (
    <div className="chips">
      {p.options.map(([k, label]) => (
        <button key={k} className="chip" aria-pressed={p.value === k} onClick={() => p.onChange(k)}>
          {label}
        </button>
      ))}
    </div>
  );
}

export function Modal(p: { title: string; onClose: () => void; children: ReactNode; wide?: boolean; actions?: ReactNode }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && p.onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [p]);
  return (
    <div className="modal-bg" onMouseDown={(e) => e.target === e.currentTarget && p.onClose()}>
      <div className={`modal ${p.wide ? "wide" : ""}`} role="dialog" aria-modal="true" aria-label={p.title}>
        <h2>{p.title}</h2>
        {p.children}
        {p.actions && <div className="actions">{p.actions}</div>}
      </div>
    </div>
  );
}

export function Drawer(p: { onClose: () => void; children: ReactNode; label: string }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && p.onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [p]);
  return (
    <>
      <div className="drawer-bg" onClick={p.onClose} />
      <aside className="drawer" role="dialog" aria-label={p.label}>
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button className="ghost" onClick={p.onClose} aria-label="Close">✕</button>
        </div>
        {p.children}
      </aside>
    </>
  );
}

export function Progress({ value, stage }: { value: number; stage?: string }) {
  return (
    <div>
      <div className="progress" role="progressbar" aria-valuenow={Math.round(value * 100)} aria-valuemin={0} aria-valuemax={100}>
        <div style={{ width: `${Math.max(2, value * 100)}%` }} />
      </div>
      {stage && <div className="small muted" style={{ marginTop: 4 }}>{stage} · {Math.round(value * 100)} %</div>}
    </div>
  );
}

export function KV({ items }: { items: [ReactNode, ReactNode][] }) {
  return (
    <dl className="kv">
      {items.map(([k, v], i) => (
        <div key={i} style={{ display: "contents" }}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

// ---------------------------------------------------------------- toasts
type Toast = { id: number; text: string; err?: boolean };
const ToastCtx = createContext<(text: string, err?: boolean) => void>(() => {});
export function ToastProvider({ children }: { children: ReactNode }) {
  const [t, setT] = useState<Toast | null>(null);
  const show = useCallback((text: string, err = false) => {
    const id = Date.now();
    setT({ id, text, err });
    setTimeout(() => setT((cur) => (cur && cur.id === id ? null : cur)), err ? 7000 : 3500);
  }, []);
  return (
    <ToastCtx.Provider value={show}>
      {children}
      {t && <div className={`toast ${t.err ? "err" : ""}`} role="status">{t.text}</div>}
    </ToastCtx.Provider>
  );
}
export const useToast = () => useContext(ToastCtx);
