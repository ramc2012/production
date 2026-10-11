import { ReactNode, useMemo, useState } from "react";
import type { Row } from "../api";

export type Col = {
  key: string;
  label: ReactNode;
  num?: boolean;
  render?: (r: Row) => ReactNode;
  sort?: (r: Row) => number | string | null | undefined;
  csv?: (r: Row) => string | number | null | undefined;
  wrap?: boolean;
  title?: string;
};

function cmp(a: unknown, b: unknown) {
  if (a === b) return 0;
  if (a === null || a === undefined || a === "") return 1;
  if (b === null || b === undefined || b === "") return -1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b), undefined, { numeric: true });
}

function toCsv(cols: Col[], rows: Row[]): string {
  const esc = (v: unknown) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const head = cols.map((c) => esc(typeof c.label === "string" ? c.label : c.key)).join(",");
  const body = rows.map((r) => cols.map((c) => esc(c.csv ? c.csv(r) : r[c.key])).join(","));
  return [head, ...body].join("\n");
}

/** Sortable table with an optional search box and CSV download (the table view behind every chart). */
export function DataTable(p: {
  cols: Col[];
  rows: Row[];
  search?: boolean;
  csvName?: string;
  tall?: boolean;
  onRow?: (r: Row) => void;
  rowKey?: (r: Row, i: number) => string | number;
  selected?: string | number | null;
  empty?: ReactNode;
  tools?: ReactNode;
  initialSort?: [string, "asc" | "desc"];
}) {
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<[string, "asc" | "desc"] | null>(p.initialSort ?? null);
  const rows = useMemo(() => {
    let r = p.rows;
    if (q) {
      const s = q.toLowerCase();
      r = r.filter((x) => Object.values(x).some((v) => v !== null && typeof v !== "object" && String(v).toLowerCase().includes(s)));
    }
    if (sort) {
      const col = p.cols.find((c) => c.key === sort[0]);
      const val = (x: Row) => (col?.sort ? col.sort(x) : x[sort[0]]);
      r = [...r].sort((a, b) => cmp(val(a), val(b)) * (sort[1] === "asc" ? 1 : -1));
    }
    return r;
  }, [p.rows, p.cols, q, sort]);

  const download = () => {
    const blob = new Blob([toCsv(p.cols, rows)], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${p.csvName ?? "table"}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <div>
      {(p.search || p.csvName || p.tools) && (
        <div className="tbl-tools">
          {p.search && <input placeholder="Filter rows…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Filter rows" />}
          {p.tools}
          <span className="spacer" />
          <span className="small muted">{rows.length} row{rows.length === 1 ? "" : "s"}</span>
          {p.csvName && <button className="sm" onClick={download}>Download CSV</button>}
        </div>
      )}
      {rows.length === 0 ? (
        <div className="empty">{p.empty ?? "Nothing to show."}</div>
      ) : (
        <div className={`tbl-wrap ${p.tall ? "tall" : ""}`}>
          <table>
            <thead>
              <tr>
                {p.cols.map((c) => {
                  const active = sort?.[0] === c.key;
                  return (
                    <th key={c.key} className={`sortable ${c.num ? "num" : ""}`} title={c.title}
                        aria-sort={active ? (sort![1] === "asc" ? "ascending" : "descending") : "none"}
                        onClick={() => setSort(active && sort![1] === "desc" ? [c.key, "asc"] : [c.key, active ? "desc" : c.num ? "desc" : "asc"])}>
                      {c.label}
                      {active ? (sort![1] === "asc" ? " ↑" : " ↓") : ""}
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => {
                const k = p.rowKey ? p.rowKey(r, i) : i;
                return (
                  <tr key={k} className={`${p.onRow ? "clickable" : ""} ${p.selected !== undefined && p.selected === k ? "sel" : ""}`}
                      onClick={p.onRow ? () => p.onRow!(r) : undefined}>
                    {p.cols.map((c) => (
                      <td key={c.key} className={`${c.num ? "num" : ""} ${c.wrap ? "wrap" : ""}`}>
                        {c.render ? c.render(r) : (r[c.key] ?? "–")}
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
