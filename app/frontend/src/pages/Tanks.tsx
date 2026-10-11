import { useState } from "react";
import { Row } from "../api";
import { Chart } from "../components/Chart";
import { DataTable } from "../components/DataTable";
import { Card, ErrorBox, KV, Loading, PageHead, Sev, Tabs } from "../components/ui";
import { useRunQuery } from "../context";
import { day, human, int, n, pct } from "../fmt";

function TanksTab() {
  const q = useRunQuery<Row>("/tanks");
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data!;
  const bal: Record<string, Row> = {};
  for (const b of d.balance) {
    const r = (bal[b.date] ??= { date: b.date });
    if (b.var === "tank:dinv_oil") { r.dinv_meas = b.meas; r.dinv_rec = b.rec; }
    else { r.disp_meas = b.meas; r.disp_rec = b.rec; }
  }
  const sumRows = d.summary.map((s: Row) => ({ ...s, ...(bal[s.date] ?? {}) }));
  const levels: Row[] = [];
  for (const t of d.tanks) for (const r of t.daily) {
    const row = levels.find((x) => x.date === r.date) ?? (levels.push({ date: r.date }), levels[levels.length - 1]);
    row[t.id] = r.close_level_mm;
  }
  const colors = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)"];
  return (
    <div className="stack">
      <div className="tiles">
        {d.tanks.map((t: Row) => (
          <div className="tile" key={t.id}>
            <Sev s={t.status} />
            <div className="tl" style={{ marginTop: 4 }}>{t.id} · close level</div>
            <div className="tv">{int(t.daily[t.daily.length - 1]?.close_level_mm)}<small>mm</small></div>
            <div className="ts">safe fill {int(t.safe_fill_mm)} mm{t.live ? ` · ${pct(t.live.fill_frac, 0)} of safe fill (twin)` : ""} · {t.strapping_points} strapping points</div>
          </div>
        ))}
      </div>
      <Card title="Tank levels at 06:00" lead="Closing dip per tank. A single-dip error shows as a +x/−x pair on consecutive days and is excluded from the reconciliation.">
        <Chart data={levels} x="date" xFmt={day} yLabel="mm" yFmt={(v) => int(v)}
               series={d.tanks.slice(0, 4).map((t: Row, i: number) => ({ key: t.id, label: t.id, color: colors[i] }))} />
      </Card>
      <Card title="Tank-farm oil balance" lead="Measured production (ΣΔNSV + dispatch NSV) against the reconciled installation oil.">
        <Chart data={sumRows} x="date" xFmt={day} yLabel="m³"
               series={[{ key: "meas_oil", label: "Measured (tanks + dispatch)", color: "var(--s2)" }, { key: "dvr_oil", label: "Reconciled", color: "var(--s1)" }]} />
      </Card>
      {d.tanks.map((t: Row) => (
        <Card key={t.id} title={`${t.id}: NSV chain per day`} lead="TOV → GOV (CTSh) → GSV (CTL, API 11.1 Table 54A) → NSV (less S&W). Volumes in m³ at 15 °C.">
          <DataTable rows={t.daily} csvName={`${t.id}_nsv`} cols={[
            { key: "date", label: "Day", render: (r) => day(r.date) },
            { key: "open_level_mm", label: "Open mm", num: true, render: (r) => int(r.open_level_mm) },
            { key: "close_level_mm", label: "Close mm", num: true, render: (r) => int(r.close_level_mm) },
            { key: "open_NSV", label: "Open NSV", num: true, render: (r) => n(r.open_NSV, 2) },
            { key: "close_NSV", label: "Close NSV", num: true, render: (r) => n(r.close_NSV, 2) },
            { key: "dNSV", label: "ΔNSV", num: true, render: (r) => n(r.dNSV, 2) },
            { key: "dWater", label: "ΔWater", num: true, render: (r) => n(r.dWater, 2) },
            { key: "CTL_close", label: "CTL", num: true, render: (r) => n(r.CTL_close, 5) },
            { key: "CTSh_close", label: "CTSh", num: true, render: (r) => n(r.CTSh_close, 5) },
            { key: "rho15_close", label: "ρ15 kg/m³", num: true, render: (r) => n(r.rho15_close, 1) }]} />
        </Card>
      ))}
      <Card title="Tank findings">
        <DataTable rows={d.findings} csvName="tank_findings" cols={[
          { key: "date", label: "Day", render: (r) => day(r.date) }, { key: "severity", label: "", render: (r) => <Sev s={r.severity} /> },
          { key: "object_id", label: "Tank" }, { key: "check", label: "Check", render: (r) => human(r.check) }, { key: "message", label: "Finding", wrap: true }]} />
      </Card>
    </div>
  );
}

function SeparatorsTab() {
  const q = useRunQuery<Row>("/separators");
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  return (
    <div className="stack">
      {q.data!.separators.map((s: Row) => {
        const last = s.daily[s.daily.length - 1] ?? {};
        return (
          <Card key={s.id} title={<>{s.id} <Sev s={s.status} /></>} lead={`${s.kind} separator, ${s.phases}-phase, ${s.orientation}, ${s.diameter_m} m × ${s.length_m} m. Meters: oil ${s.oil_meter ?? "–"}, water ${s.water_meter ?? "–"}, gas ${s.gas_meter ?? "–"}.`}>
            <div className="grid2">
              <div>
                <KV items={[["Shrinkage factor", n(s.shrinkage_factor, 3)], ["Liquid hold-up at NLL", `${n(s.liquid_volume_m3, 2)} m³`],
                            ["Retention (last day)", `${n(last.retention_min, 1)} min (API 12J min ${n(last.retention_api12j_min, 1)})`],
                            ["Gas load (last day)", `${pct(last.gas_load, 0)} of Souders-Brown limit`],
                            ["Pressure / temperature", `${n(last.pressure_ksc, 2)} ksc / ${n(last.temperature_degc, 1)} °C`]]} />
              </div>
              <Chart data={s.af} x="date" xFmt={day} height={180} refBand={[0.9, 1.1]} refBandLabel="Acceptance band" yFmt={(v) => v.toFixed(2)}
                     series={[{ key: "af_oil", label: "AF oil", color: "var(--s1)" }, { key: "af_water", label: "AF water", color: "var(--s2)" }, { key: "af_gas", label: "AF gas", color: "var(--s3)" }]} />
            </div>
            <h3>Daily meters → stock-tank volumes</h3>
            <DataTable rows={s.daily} csvName={`${s.id}_daily`} cols={[
              { key: "date", label: "Day", render: (r) => day(r.date) },
              { key: "oil_meter_raw", label: "Oil meter m³", num: true, render: (r) => n(r.oil_meter_raw, 2) },
              { key: "oil_std", label: "Oil std m³", num: true, render: (r) => n(r.oil_std, 2) },
              { key: "water_std", label: "Water m³", num: true, render: (r) => n(r.water_std, 2) },
              { key: "gas_std", label: "Gas Sm³", num: true, render: (r) => int(r.gas_std) },
              { key: "oil_leg_bsw_pct", label: "Oil-leg BS&W %", num: true, render: (r) => n(r.oil_leg_bsw_pct, 1) },
              { key: "retention_min", label: "Retention min", num: true, render: (r) => n(r.retention_min, 1) },
              { key: "gas_load", label: "Gas load", num: true, render: (r) => pct(r.gas_load, 0) }]} />
          </Card>
        );
      })}
    </div>
  );
}

export function Tanks({ tab: initial = "tanks" }: { tab?: "tanks" | "separators" }) {
  const [tab, setTab] = useState<"tanks" | "separators">(initial);
  return (
    <div>
      <PageHead title="Tanks & separators" />
      <Tabs value={tab} onChange={setTab} tabs={[["tanks", "Tanks"], ["separators", "Separators"]]} />
      {tab === "tanks" ? <TanksTab /> : <SeparatorsTab />}
    </div>
  );
}
