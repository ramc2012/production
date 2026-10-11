// Number formatting in engineers' units. Every helper returns "–" for missing values.
export const DASH = "–";
const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

export function n(v: unknown, d = 1): string {
  if (!isNum(v)) return DASH;
  return v.toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });
}
export function int(v: unknown): string {
  return isNum(v) ? Math.round(v).toLocaleString("en-IN") : DASH;
}
export function pct(v: unknown, d = 1, frac = true): string {
  if (!isNum(v)) return DASH;
  return `${(frac ? v * 100 : v).toFixed(d)} %`;
}
export function signed(v: unknown, d = 1): string {
  if (!isNum(v)) return DASH;
  return `${v > 0 ? "+" : v < 0 ? "−" : "±"}${Math.abs(v).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d })}`;
}
export function u95(sd: unknown, d = 1): string {
  return isNum(sd) ? `± ${n(2 * sd, d)}` : "";
}
export function compact(v: unknown): string {
  if (!isNum(v)) return DASH;
  const a = Math.abs(v);
  if (a >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (a >= 1e4) return `${(v / 1e3).toFixed(1)}k`;
  return n(v, a < 10 ? 2 : 1);
}
export function dt(iso: unknown): string {
  if (typeof iso !== "string" || !iso) return DASH;
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
  return d.toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export function day(s: unknown): string {
  if (typeof s !== "string" || s.length < 10) return DASH;
  return `${+s.slice(8, 10)} ${MON[+s.slice(5, 7) - 1]}`;
}
export const LIFT: Record<string, string> = { self_flow: "Self-flow", gas_lift: "Gas lift", srp: "SRP", esp: "ESP" };
export const LAYER: Record<string, string> = {
  data_quality: "Data quality", well_test: "Well test", well_daily: "Well daily", well_perf: "Well performance",
  separator: "Separator", tank: "Tank", reconciliation: "Reconciliation", allocation: "Allocation",
};
export function human(s: unknown): string {
  return typeof s === "string" ? s.replace(/_/g, " ") : DASH;
}
