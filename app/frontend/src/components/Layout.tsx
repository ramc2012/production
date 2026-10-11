import { useQuery } from "@tanstack/react-query";
import { ReactNode, useEffect, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { api, Row } from "../api";
import { useApp } from "../context";
import { day } from "../fmt";

const NAV: [string, [string, string, string][]][] = [
  ["Operate", [["/", "Dashboard", "◧"], ["/network", "Network", "⌥"], ["/exceptions", "Exceptions", "▲"],
               ["/wells", "Wells", "⊙"], ["/tests", "Well tests", "≋"]]],
  ["Measure", [["/meters", "Meters", "◎"], ["/tanks", "Tanks & separators", "▭"], ["/reconciliation", "Reconciliation", "⚖"]]],
  ["Report", [["/allocation", "Allocation", "▦"], ["/runs", "Runs & reports", "↻"]]],
  ["Digital twin", [["/twin", "Twin L0–L5", "◈"], ["/twin/whatif", "What-if", "⇄"], ["/twin/recommendations", "Recommendations", "✓"]]],
  ["Data", [["/data", "Data in", "⇪"]]],
];

function RunPicker() {
  const { run, setRun } = useApp();
  const runs = useQuery({ queryKey: ["/runs?limit=60"], queryFn: () => api.get<Row[]>("/runs?limit=60"), refetchInterval: 15000 });
  const done = (runs.data ?? []).filter((r) => r.status === "done");
  return (
    <label className="row small" style={{ gap: 6 }}>
      <span className="muted">Run</span>
      <select aria-label="Run shown on every page" value={run ? String(run.id) : ""}
              onChange={(e) => setRun(e.target.value ? done.find((r) => String(r.id) === e.target.value) ?? null : null)}>
        <option value="">Latest{done[0] ? ` (#${done[0].id}, ${done[0].kind})` : ""}</option>
        {done.map((r) => (
          <option key={r.id} value={r.id}>#{r.id} {r.kind} · {day(r.period_start)}–{day(r.period_end)}</option>
        ))}
      </select>
    </label>
  );
}

function Jobs() {
  const jobs = useQuery({ queryKey: ["/jobs?active=1"], queryFn: () => api.get<Row[]>("/jobs?active=1"), refetchInterval: 3000 });
  const a = jobs.data ?? [];
  if (!a.length) return null;
  const j = a[0];
  return (
    <span className="badge s-warn" title={a.map((x) => `${x.kind}: ${x.stage}`).join("\n")}>
      ◆ {a.length} job{a.length > 1 ? "s" : ""} running · {j.stage || j.kind} {Math.round((j.progress || 0) * 100)} %
    </span>
  );
}

export function Layout({ children }: { children: ReactNode }) {
  const { user, can, logout, theme, setTheme } = useApp();
  const [open, setOpen] = useState(false);
  const loc = useLocation();
  useEffect(() => setOpen(false), [loc.pathname]);
  const counts = useQuery({
    queryKey: ["/issues?state=open&severity=ALARM"],
    queryFn: () => api.get<Row[]>("/issues?state=open&severity=ALARM"),
    refetchInterval: 60000,
  });
  const nav = [...NAV, ...(can("admin") ? ([["Admin", [["/admin", "Admin", "⚙"]]]] as typeof NAV) : [])];
  return (
    <div className="shell">
      {open && <div className="scrim" onClick={() => setOpen(false)} />}
      <nav className={`side ${open ? "open" : ""}`} aria-label="Main">
        <div className="brand">
          <img src="/favicon.svg" width={26} height={26} alt="" />
          <div>FlowTwin<small>GGS validation &amp; digital twin</small></div>
        </div>
        {nav.map(([group, items]) => (
          <div key={group}>
            <div className="nav-group">{group}</div>
            {items.map(([to, label, icon]) => (
              <NavLink key={to} to={to} end={to === "/" || to === "/twin"} className={({ isActive }) => `nav ${isActive ? "active" : ""}`}>
                <span aria-hidden style={{ width: 16, textAlign: "center" }}>{icon}</span>
                {label}
                {to === "/exceptions" && (counts.data?.length ?? 0) > 0 && (
                  <span className="count" title="Open (unacknowledged) alarms">{counts.data!.length}</span>
                )}
              </NavLink>
            ))}
          </div>
        ))}
        <div className="foot">
          Read-only towards plant systems. L5 runs on a simulator only.
        </div>
      </nav>
      <div className="main">
        <header className="topbar">
          <button className="menu-btn ghost" onClick={() => setOpen(true)} aria-label="Open menu">☰</button>
          <RunPicker />
          <Jobs />
          <span className="spacer" />
          <select className="hide-sm" aria-label="Theme" value={theme} onChange={(e) => setTheme(e.target.value)}>
            <option value="auto">Auto theme</option>
            <option value="light">Light</option>
            <option value="dark">Dark</option>
          </select>
          <span className="small text2 hide-sm" title={user?.designation}>
            {user?.full_name || user?.username} · <span className="muted">{user?.role}</span>
          </span>
          <NavLink to="/account" className="small">Account</NavLink>
          <button className="sm" onClick={logout}>Sign out</button>
        </header>
        <main className="content">{children}</main>
      </div>
    </div>
  );
}
