import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { api, ApiError, Row } from "./api";

type Role = "viewer" | "engineer" | "admin";
const RANK: Record<Role, number> = { viewer: 1, engineer: 2, admin: 3 };

type AppCtx = {
  user: Row | null;
  setUser: (u: Row | null) => void;
  can: (role: Role) => boolean;
  logout: () => Promise<void>;
  run: Row | null;                    // selected run; null = always the latest finished run
  setRun: (r: Row | null) => void;
  theme: string;
  setTheme: (t: string) => void;
};
const Ctx = createContext<AppCtx>(null as unknown as AppCtx);
export const useApp = () => useContext(Ctx);

function store(key: string, v?: string | null) {
  try {
    if (v === undefined) return localStorage.getItem(key);
    if (v === null) localStorage.removeItem(key);
    else localStorage.setItem(key, v);
  } catch {
    /* private mode */
  }
  return null;
}

export function AppProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [user, setUser] = useState<Row | null>(null);
  const [checked, setChecked] = useState(false);
  const [run, setRunState] = useState<Row | null>(null);
  const [theme, setThemeState] = useState<string>(store("ft_theme") || "auto");

  useEffect(() => {
    api.get("/auth/me").then(setUser).catch(() => setUser(null)).finally(() => setChecked(true));
    const h = () => setUser(null);
    window.addEventListener("ft-unauthorized", h);
    return () => window.removeEventListener("ft-unauthorized", h);
  }, []);
  useEffect(() => {
    if (theme === "auto") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  const setTheme = (t: string) => {
    store("ft_theme", t);
    setThemeState(t);
  };
  const setRun = (r: Row | null) => {
    setRunState(r);
    qc.invalidateQueries();
  };
  const can = useCallback((r: Role) => !!user && RANK[(user.role as Role) ?? "viewer"] >= RANK[r], [user]);
  const logout = async () => {
    await api.post("/auth/logout").catch(() => {});
    setUser(null);
    qc.clear();
  };
  if (!checked) return null;
  return <Ctx.Provider value={{ user, setUser, can, logout, run, setRun, theme, setTheme }}>{children}</Ctx.Provider>;
}

/** Query helper that adds the selected run to the URL. */
export function useRunQuery<T = any>(path: string, extra: Record<string, any> = {}, opts: { enabled?: boolean; kind?: "twin" } = {}) {
  const { run } = useApp();
  const params = new URLSearchParams();
  if (run && (opts.kind !== "twin" || run.kind === "twin")) params.set("run_id", String(run.id));
  for (const [k, v] of Object.entries(extra)) if (v !== undefined && v !== null && v !== "") params.set(k, String(v));
  const url = path + (params.toString() ? `?${params}` : "");
  return useQuery<T, ApiError>({ queryKey: [url], queryFn: () => api.get<T>(url), enabled: opts.enabled ?? true });
}
