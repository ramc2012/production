import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, Row } from "../api";
import { Chart } from "../components/Chart";
import { DataTable } from "../components/DataTable";
import { Card, ErrorBox, Loading, Modal, PageHead, Tile, useToast } from "../components/ui";
import { useApp } from "../context";
import { day, dt, int, n } from "../fmt";

export function Allocation() {
  const { can } = useApp();
  const qc = useQueryClient();
  const toast = useToast();
  const months = useQuery({ queryKey: ["/allocation/months"], queryFn: () => api.get<Row[]>("/allocation/months") });
  const [month, setMonth] = useState<string>("");
  const [reopen, setReopen] = useState(false);
  const [reason, setReason] = useState("");
  useEffect(() => {
    if (!month && months.data?.length) setMonth(months.data[0].month);
  }, [months.data, month]);
  const q = useQuery({ queryKey: [`/allocation?month=${month}`], queryFn: () => api.get<Row>(`/allocation?month=${month}`), enabled: !!month });
  const close = useMutation({
    mutationFn: () => api.post(`/allocation/${month}/close`, {}),
    onSuccess: () => { toast(`${month} closed`); qc.invalidateQueries(); },
    onError: (e: Error) => toast(e.message, true),
  });
  const reo = useMutation({
    mutationFn: () => api.post(`/allocation/${month}/reopen`, { reason }),
    onSuccess: () => { toast(`${month} re-opened`); setReopen(false); setReason(""); qc.invalidateQueries(); },
    onError: (e: Error) => toast(e.message, true),
  });
  if (months.isLoading) return <Loading />;
  if (!months.data?.length) return <div className="stack"><PageHead title="Allocation" /><div className="empty">No finished runs yet.</div></div>;
  const v = q.data;
  const t = v?.totals ?? {};
  const full = v?.coverage && v.coverage.first === v.coverage.month_first && v.coverage.last === v.coverage.month_last;
  return (
    <div className="stack">
      <PageHead title="Monthly allocation"
                sub="Per-well production by three methods, side by side, with uncertainty. Closing a month locks the run it uses; re-opening needs an admin and a reason."
                right={<div className="row">
                  <select value={month} onChange={(e) => setMonth(e.target.value)} aria-label="Month">
                    {months.data.map((m) => <option key={m.month} value={m.month}>{m.month}{m.closed ? " (closed)" : ""}</option>)}
                  </select>
                  <a className="btn" href={`/api/v1/allocation/${month}/export.xlsx`}>Export Excel</a>
                  {can("engineer") && v && !v.closed && v.run && <button className="primary" disabled={close.isPending} onClick={() => close.mutate()}>Close month</button>}
                  {can("admin") && v?.closed && <button onClick={() => setReopen(true)}>Re-open…</button>}
                </div>} />
      {q.isLoading || !v ? <Loading /> : q.error ? <ErrorBox error={q.error} /> : !v.run ? <div className="empty">No run covers {month}.</div> : (
        <>
          {v.closed ? (
            <div className="banner info">🔒 Closed by <b>{v.closed.by}</b> on {dt(v.closed.at)} using run #{v.closed.run_id}. The numbers below are locked to that run.</div>
          ) : !full ? (
            <div className="banner warn">Run #{v.run.id} covers {v.coverage.days} days ({day(v.coverage.first)}–{day(v.coverage.last)}), not the full month. Run the whole month before closing.</div>
          ) : null}
          <div className="tiles">
            <Tile label="Oil, reconciled" value={n(t.dvr_oil_m3, 1)} unit="m³" sub={<>U95 ± {n(t.dvr_oil_u95_indep_m3, 1)} m³ (independent days)</>} />
            <Tile label="Oil, allocated (AF)" value={n(t.prop_oil_m3, 1)} unit="m³" sub="API MPMS 20.1 proportional" />
            <Tile label="Oil, test-based" value={n(t.theo_oil_m3, 1)} unit="m³" sub="last valid test × hours on" />
            <Tile label="Water, reconciled" value={n(t.dvr_water_m3, 1)} unit="m³" />
            <Tile label="Gas, reconciled" value={int(t.dvr_gas_sm3)} unit="Sm³" />
            <Tile label="Run" value={`#${v.run.id}`} sub={<>{v.run.kind} · engine {v.run.engine_version} · config {v.run.config_hash?.slice(0, 8)}</>} />
          </div>
          <Card title="Oil by well" lead="Reconciled vs allocated vs test-based for the month.">
            <Chart data={v.wells} x="well_id" yLabel="m³"
                   series={[{ key: "dvr_oil_m3", label: "Reconciled", color: "var(--s1)", type: "bar" }, { key: "prop_oil_m3", label: "Allocated (AF)", color: "var(--s2)", type: "bar" },
                            { key: "theo_oil_m3", label: "Test-based", color: "var(--s3)", type: "bar" }]} />
          </Card>
          <Card title="Monthly by well" lead={v.note}>
            <DataTable rows={v.wells} csvName={`allocation_${month}`} search cols={[
              { key: "well_id", label: "Well", render: (r) => <Link to={`/wells/${r.well_id}`}>{r.well_id}</Link> },
              { key: "days", label: "Days", num: true }, { key: "hours_on", label: "Hours", num: true, render: (r) => n(r.hours_on, 0) },
              { key: "theo_oil_m3", label: "Oil test-based m³", num: true, render: (r) => n(r.theo_oil_m3, 2) },
              { key: "prop_oil_m3", label: "Oil AF m³", num: true, render: (r) => n(r.prop_oil_m3, 2) },
              { key: "dvr_oil_m3", label: "Oil reconciled m³", num: true, render: (r) => n(r.dvr_oil_m3, 2) },
              { key: "dvr_oil_u95_indep_m3", label: "U95 indep.", num: true, render: (r) => `± ${n(r.dvr_oil_u95_indep_m3, 2)}` },
              { key: "dvr_oil_u95_corr_m3", label: "U95 corr.", num: true, render: (r) => `± ${n(r.dvr_oil_u95_corr_m3, 2)}` },
              { key: "dvr_water_m3", label: "Water rec. m³", num: true, render: (r) => n(r.dvr_water_m3, 2) },
              { key: "dvr_gas_sm3", label: "Gas rec. Sm³", num: true, render: (r) => int(r.dvr_gas_sm3) }]} />
          </Card>
          <Card title="Daily by well">
            <DataTable rows={v.daily} csvName={`allocation_daily_${month}`} search tall cols={[
              { key: "date", label: "Day", render: (r) => day(r.date) }, { key: "well_id", label: "Well" },
              { key: "hours_on", label: "Hours", num: true, render: (r) => n(r.hours_on, 1) },
              { key: "theo_oil_m3", label: "Test-based", num: true, render: (r) => n(r.theo_oil_m3, 2) },
              { key: "prop_oil_m3", label: "AF", num: true, render: (r) => n(r.prop_oil_m3, 2) },
              { key: "dvr_oil_m3", label: "Reconciled", num: true, render: (r) => <>{n(r.dvr_oil_m3, 2)} <span className="muted">± {n(2 * (r.dvr_oil_sd_m3 ?? NaN), 2)}</span></> },
              { key: "test_id", label: "Test" }, { key: "test_status", label: "Status" }]} />
          </Card>
        </>
      )}
      {reopen && (
        <Modal title={`Re-open ${month}`} onClose={() => setReopen(false)}
               actions={<><button onClick={() => setReopen(false)}>Cancel</button><button className="danger" disabled={!reason.trim()} onClick={() => reo.mutate()}>Re-open</button></>}>
          <label className="f">Reason (audit-logged)<textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)} style={{ fontFamily: "inherit" }} /></label>
        </Modal>
      )}
    </div>
  );
}
