import { FormEvent, useState } from "react";
import { api } from "../api";
import { Card, ErrorBox, PageHead, useToast } from "../components/ui";
import { useApp } from "../context";

export function Login() {
  const { setUser } = useApp();
  const [u, setU] = useState("");
  const [p, setP] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      setUser(await api.post("/auth/login", { username: u, password: p }));
    } catch (x) {
      setErr(x);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="login">
      <form className="card stack" onSubmit={submit}>
        <div className="brand" style={{ padding: 0 }}>
          <img src="/favicon.svg" width={30} height={30} alt="" />
          <div>FlowTwin<small>GGS flow validation &amp; digital twin</small></div>
        </div>
        <label className="f">User name<input autoFocus autoComplete="username" value={u} onChange={(e) => setU(e.target.value)} /></label>
        <label className="f">Password<input type="password" autoComplete="current-password" value={p} onChange={(e) => setP(e.target.value)} /></label>
        <ErrorBox error={err} />
        <button className="primary" disabled={busy || !u || !p}>{busy ? "Signing in…" : "Sign in"}</button>
        <p className="small muted" style={{ margin: 0 }}>
          First start: the admin password is in <code>initial_admin_password.txt</code> in the FlowTwin data folder.
        </p>
      </form>
    </div>
  );
}

export function Account({ forced = false }: { forced?: boolean }) {
  const { user, setUser, logout, theme, setTheme } = useApp();
  const toast = useToast();
  const [o, setO] = useState("");
  const [n1, setN1] = useState("");
  const [n2, setN2] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null);
    if (n1 !== n2) return setErr(new Error("The new passwords do not match"));
    try {
      setUser(await api.post("/auth/password", { old_password: o, new_password: n1 }));
      toast("Password changed");
      setO(""); setN1(""); setN2("");
    } catch (x) {
      setErr(x);
    }
  };
  const form = (
    <form className="stack" onSubmit={submit} style={{ maxWidth: 380 }}>
      {forced && <div className="banner warn">Choose your own password before you continue.</div>}
      <label className="f">Current password<input type="password" autoComplete="current-password" value={o} onChange={(e) => setO(e.target.value)} /></label>
      <label className="f">New password (at least 10 characters)<input type="password" autoComplete="new-password" value={n1} onChange={(e) => setN1(e.target.value)} /></label>
      <label className="f">Repeat new password<input type="password" autoComplete="new-password" value={n2} onChange={(e) => setN2(e.target.value)} /></label>
      <ErrorBox error={err} />
      <div className="row">
        <button className="primary" disabled={!o || n1.length < 10}>Change password</button>
        {forced && <button type="button" onClick={logout}>Sign out</button>}
      </div>
    </form>
  );
  if (forced) return <div className="login"><div className="card">{form}</div></div>;
  return (
    <div className="stack">
      <PageHead title="Account" sub={`${user?.username} · ${user?.role}`} />
      <Card title="Change password">{form}</Card>
      <Card title="Appearance">
        <label className="f" style={{ maxWidth: 240 }}>Theme
          <select value={theme} onChange={(e) => setTheme(e.target.value)}>
            <option value="auto">Follow the device</option><option value="light">Light</option><option value="dark">Dark</option>
          </select>
        </label>
      </Card>
    </div>
  );
}
