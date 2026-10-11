// Thin fetch wrapper: same-origin cookies, CSRF header on every mutating request, JSON errors.
export type Row = Record<string, any>;

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function csrf(): string {
  const m = document.cookie.match(/(?:^|;\s*)ft_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

async function call<T>(method: string, url: string, body?: unknown, form?: FormData): Promise<T> {
  const headers: Record<string, string> = {};
  if (method !== "GET") headers["X-CSRF-Token"] = csrf();
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const r = await fetch(`/api/v1${url}`, {
    method,
    headers,
    credentials: "same-origin",
    body: form ?? (body !== undefined ? JSON.stringify(body) : undefined),
  });
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try {
      const j = await r.json();
      if (j?.detail) msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* not JSON */
    }
    if (r.status === 401) window.dispatchEvent(new CustomEvent("ft-unauthorized"));
    throw new ApiError(r.status, msg);
  }
  const ct = r.headers.get("content-type") || "";
  return (ct.includes("json") ? r.json() : (r.text() as unknown)) as Promise<T>;
}

export const api = {
  get: <T = any>(url: string) => call<T>("GET", url),
  post: <T = any>(url: string, body?: unknown) => call<T>("POST", url, body ?? {}),
  put: <T = any>(url: string, body?: unknown) => call<T>("PUT", url, body ?? {}),
  patch: <T = any>(url: string, body?: unknown) => call<T>("PATCH", url, body ?? {}),
  del: <T = any>(url: string, body?: unknown) => call<T>("DELETE", url, body),
  upload: <T = any>(url: string, form: FormData) => call<T>("POST", url, undefined, form),
};

export function qs(params: Record<string, string | number | null | undefined | boolean>): string {
  const p = Object.entries(params).filter(([, v]) => v !== null && v !== undefined && v !== "" && v !== false);
  return p.length ? "?" + p.map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join("&") : "";
}
