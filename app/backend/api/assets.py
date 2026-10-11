"""Wells, well tests (with engineer overrides), meters, tanks, separators and reconciliation."""
from __future__ import annotations

import io
from typing import Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import engine_adapter as A
from ..db import get_db
from ..models import Finding, Issue, Recommendation, Site, TestOverride, User, WellDailyResult
from ..security import audit, engineer, viewer
from ..services.runs import latest_run
from .core import _f, object_status
from .deps import resolve_run, run_meta, site

router = APIRouter(tags=["assets"])
OVERRIDE_STATUSES = ("ACCEPTED", "PROVISIONAL", "REJECTED")


def _rows(df: pd.DataFrame) -> list:
    return df.astype(object).where(df.notna(), None).to_dict("records") if len(df) else []


# ---------------------------------------------------------------------------- wells
@router.get("/wells")
def wells(run_id: Optional[int] = None, db: Session = Depends(get_db), s: Site = Depends(site),
          _: User = Depends(viewer)):
    run = resolve_run(db, s.id, run_id)
    y = A.network_yaml(A.current_network(db, s.id))
    st = object_status(db, run)
    perf = {r["well_id"]: r for r in A.get_table(db, run.id, "well_perf")}
    last = {r.well_id: r for r in db.scalars(select(WellDailyResult).where(WellDailyResult.run_id == run.id,
                                                                            WellDailyResult.date == run.period_end))}
    twin_run = run if run.kind == "twin" else latest_run(db, s.id, "twin")
    l0 = {r["well_id"]: r for r in A.get_table(db, twin_run.id, "L0_wells")} if twin_run else {}
    fc = {r["well_id"]: r for r in A.get_table(db, twin_run.id, "L3_well_forecast")} if twin_run else {}
    out = []
    for w in y.get("wells", []):
        wid = w["id"]
        r = last.get(wid)
        d = r.data if r else {}
        out.append(dict(
            well_id=wid, lift=w.get("lift", "self_flow"), separator=w.get("separator"),
            status=st.get(wid, {}).get("status", "OK"), open_issues=st.get(wid, {}).get("open_issues", 0),
            date=run.period_end, hours_on=r.hours_on if r else None,
            theo_oil_m3=r.theo_oil_m3 if r else None, theo_oil_sd_m3=r.theo_oil_sd_m3 if r else None,
            prop_oil_m3=r.prop_oil_m3 if r else None, dvr_oil_m3=r.dvr_oil_m3 if r else None,
            dvr_oil_sd_m3=r.dvr_oil_sd_m3 if r else None, dvr_water_m3=r.dvr_water_m3 if r else None,
            dvr_gas_sm3=r.dvr_gas_sm3 if r else None,
            wc=d.get("wc"), test_id=d.get("test_id"), test_status=d.get("test_status"), test_age_d=d.get("test_age_d"),
            vfm_liquid_m3d=d.get("vfm_comb"), vfm_sd_m3d=d.get("vfm_sd"), vfm_methods=d.get("vfm_methods"),
            thp_ksc=d.get("thp_ksc"), choke_64ths=d.get("choke_64ths"), spm=d.get("spm"),
            lift_gas_sm3=d.get("lift_gas_sm3"),
            pi_last=perf.get(wid, {}).get("pi_last"), pr_ksc=perf.get(wid, {}).get("pr_ksc"),
            tuning=l0.get(wid, {}).get("tuning"), forecast_oil_30d_m3d=fc.get(wid, {}).get("oil_30d"),
            decline_pct_per_year=fc.get(wid, {}).get("decline_pct_per_year")))
    return dict(run=run_meta(run), twin_run_id=twin_run.id if twin_run else None, wells=out)


@router.get("/wells/{well_id}")
def well_detail(well_id: str, run_id: Optional[int] = None, db: Session = Depends(get_db), s: Site = Depends(site),
                _: User = Depends(viewer)):
    run = resolve_run(db, s.id, run_id)
    y = A.network_yaml(A.current_network(db, s.id))
    static = next((w for w in y.get("wells", []) if w["id"] == well_id), None)
    if static is None:
        raise HTTPException(404, f"Unknown well {well_id}")
    daily = db.scalars(select(WellDailyResult).where(WellDailyResult.run_id == run.id,
                                                     WellDailyResult.well_id == well_id)
                       .order_by(WellDailyResult.date)).all()
    days = [dict(r.data, dvr_oil_sd=r.dvr_oil_sd_m3, dvr_water_sd=r.dvr_water_sd_m3, dvr_gas_sd=r.dvr_gas_sd_sm3)
            for r in daily]
    tests = [t for t in A.get_table(db, run.id, "tests") if t["well_id"] == well_id]
    ovr = {o.test_id: o for o in db.scalars(select(TestOverride).where(TestOverride.site_id == s.id,
                                                                        TestOverride.active.is_(True)))}
    for t in tests:
        o = ovr.get(t["test_id"])
        t["override"] = None if o is None else dict(status=o.status, reason=o.reason, by=o.by, at=o.at.isoformat())
    perf = next((p for p in A.get_table(db, run.id, "well_perf") if p["well_id"] == well_id), None)
    finds = db.scalars(select(Finding).where(Finding.run_id == run.id, Finding.object_id == well_id)
                       .order_by(Finding.date.desc())).all()
    issues = db.scalars(select(Issue).where(Issue.site_id == s.id, Issue.object_id == well_id,
                                            Issue.state != "resolved")).all()
    twin_run = run if run.kind == "twin" else latest_run(db, s.id, "twin")
    twin = None
    if twin_run is not None:
        twin = dict(
            run_id=twin_run.id,
            l0=next((r for r in A.get_table(db, twin_run.id, "L0_wells") if r["well_id"] == well_id), None),
            live=next((r for r in A.get_table(db, twin_run.id, "L1_live_wells") if r["well_id"] == well_id), None),
            forecast=next((r for r in A.get_table(db, twin_run.id, "L3_well_forecast") if r["well_id"] == well_id),
                          None),
            forecast_daily=[r for r in A.get_table(db, twin_run.id, "L3_daily_forecast") if r["well_id"] == well_id],
            gl_curve=[r for r in A.get_table(db, twin_run.id, "L4_gl_curves") if r["well_id"] == well_id],
            chokes=[r for r in A.get_table(db, twin_run.id, "L4_chokes") if r["well_id"] == well_id],
            srp=next((r for r in A.get_table(db, twin_run.id, "L4_srp") if r["well_id"] == well_id), None),
            recommendations=[dict(id=r.id, kind=r.kind, current=r.current, proposed=r.proposed,
                                  predicted_d_oil_m3d=r.predicted_d_oil_m3d, status=r.status, rationale=r.rationale)
                             for r in db.scalars(select(Recommendation).where(Recommendation.run_id == twin_run.id,
                                                                              Recommendation.well_id == well_id))])
    return dict(run=run_meta(run), well=static, daily=days, tests=tests, performance=perf,
                findings=[_f(f) for f in finds], open_issues=len(issues), twin=twin)


# ---------------------------------------------------------------------------- tests & overrides
@router.get("/tests")
def tests(run_id: Optional[int] = None, db: Session = Depends(get_db), s: Site = Depends(site),
          _: User = Depends(viewer)):
    run = resolve_run(db, s.id, run_id)
    ovr = {o.test_id: o for o in db.scalars(select(TestOverride).where(TestOverride.site_id == s.id,
                                                                        TestOverride.active.is_(True)))}
    rows = A.get_table(db, run.id, "tests")
    for t in rows:
        o = ovr.get(t["test_id"])
        t["override"] = None if o is None else dict(status=o.status, reason=o.reason, by=o.by, at=o.at.isoformat())
        t["override_pending"] = o is not None and t["test_id"] not in (run.test_overrides or {})
    tr = run if run.kind == "twin" else latest_run(db, s.id, "twin")
    due = A.get_table(db, tr.id, "L3_tests_due") if tr else []
    sched = A.get_table(db, tr.id, "L4_test_schedule") if tr else []
    return dict(run=run_meta(run), tests=sorted(rows, key=lambda t: t["test_date"], reverse=True),
                due=due, schedule=sched)


class OverrideBody(BaseModel):
    status: str
    reason: str


@router.post("/tests/{test_id}/override")
def set_override(test_id: str, body: OverrideBody, db: Session = Depends(get_db), s: Site = Depends(site),
                 user: User = Depends(engineer)):
    if body.status not in OVERRIDE_STATUSES:
        raise HTTPException(400, f"status must be one of {OVERRIDE_STATUSES}")
    if len(body.reason.strip()) < 5:
        raise HTTPException(400, "Give a reason (at least a few words) for the override")
    prev = db.scalars(select(TestOverride).where(TestOverride.site_id == s.id, TestOverride.test_id == test_id,
                                                 TestOverride.active.is_(True))).all()
    for p in prev:
        p.active = False
    o = TestOverride(site_id=s.id, test_id=test_id, status=body.status, reason=body.reason.strip(), by=user.username)
    db.add(o)
    audit(db, user.username, "test.override", f"test:{test_id}",
          [dict(status=p.status, reason=p.reason) for p in prev] or None,
          dict(status=body.status, reason=body.reason.strip()))
    db.commit()
    return dict(test_id=test_id, status=o.status, reason=o.reason, by=o.by, at=o.at.isoformat(),
                note="Takes effect in the next run")


class ReasonBody(BaseModel):
    reason: str


@router.delete("/tests/{test_id}/override")
def clear_override(test_id: str, body: ReasonBody, db: Session = Depends(get_db), s: Site = Depends(site),
                   user: User = Depends(engineer)):
    prev = db.scalars(select(TestOverride).where(TestOverride.site_id == s.id, TestOverride.test_id == test_id,
                                                 TestOverride.active.is_(True))).all()
    if not prev:
        raise HTTPException(404, "No active override for this test")
    for p in prev:
        p.active = False
    audit(db, user.username, "test.override_clear", f"test:{test_id}",
          [dict(status=p.status, reason=p.reason) for p in prev], dict(reason=body.reason))
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------- meters
@router.get("/meters")
def meters(run_id: Optional[int] = None, db: Session = Depends(get_db), s: Site = Depends(site),
           _: User = Depends(viewer)):
    run = resolve_run(db, s.id, run_id)
    y = A.network_yaml(A.current_network(db, s.id))
    st = object_status(db, run)
    v = A.get_frame(db, run.id, "dvr_vars")
    bias = A.get_frame(db, run.id, "bias_suspects")
    tr = run if run.kind == "twin" else latest_run(db, s.id, "twin")
    drift = {r["tag"]: r for r in A.get_table(db, tr.id, "L3_meter_drift")} if tr else {}
    maint = A.get_table(db, tr.id, "L4_maintenance") if tr else []
    role = {}
    for sp in y.get("separators", []):
        for ph in ("oil", "water", "gas"):
            if sp.get(f"{ph}_meter"):
                role[sp[f"{ph}_meter"]] = f"{sp['id']} {ph} leg"
    for g in y.get("gas_disposal", []):
        role[g["meter"]] = f"gas to {g.get('role', 'disposal')}"
    if (y.get("dispatch") or {}).get("meter"):
        role[y["dispatch"]["meter"]] = "dispatch to CTF"
    if (y.get("lift_gas") or {}).get("header_meter"):
        role[y["lift_gas"]["header_meter"]] = "lift-gas header"
    for w in y.get("wells", []):
        if w.get("lift_gas_meter"):
            role[w["lift_gas_meter"]] = f"{w['id']} lift gas"
    end = pd.Timestamp(run.period_end)
    out = []
    for m in y.get("meters", []):
        mid = m["id"]
        mv = v[v["tag"] == mid] if len(v) else v
        due = None
        if m.get("last_proved") and m.get("proving_interval_days"):
            due = (pd.Timestamp(str(m["last_proved"])) + pd.Timedelta(days=int(m["proving_interval_days"])))
        b = bias[bias["tag"] == mid] if len(bias) else bias
        out.append(dict(
            id=mid, phase=m.get("phase"), type=m.get("type", "turbine"), description=m.get("description", ""),
            role=role.get(mid, ""), meter_factor=m.get("meter_factor", 1.0), u95_pct=m.get("u95_pct"),
            last_proved=m.get("last_proved"), proving_interval_days=m.get("proving_interval_days"),
            proving_due=due.strftime("%Y-%m-%d") if due is not None else None,
            proving_overdue=bool(due is not None and due < end),
            status=st.get(mid, {}).get("status", "OK"), open_issues=st.get(mid, {}).get("open_issues", 0),
            days_in_dvr=int(mv["date"].nunique()) if len(mv) else 0,
            mean_adj_pct=float(mv["adj_pct"].mean()) if len(mv) and mv["adj_pct"].notna().any() else None,
            max_z=float(mv["z"].max()) if len(mv) and mv["z"].notna().any() else None,
            bias_days_flagged=int(b["days_flagged"].max()) if len(b) else 0,
            bias_mean_dev_pct=float(b["mean_dev_pct"].iloc[0]) if len(b) else None,
            drift=drift.get(mid)))
    return dict(run=run_meta(run), meters=out, maintenance=maint, bias_suspects=_rows(bias))


@router.get("/meters/{meter_id}")
def meter_detail(meter_id: str, run_id: Optional[int] = None, db: Session = Depends(get_db), s: Site = Depends(site),
                 _: User = Depends(viewer)):
    run = resolve_run(db, s.id, run_id)
    v = A.get_frame(db, run.id, "dvr_vars")
    mv = v[v["tag"] == meter_id] if len(v) else v
    finds = db.scalars(select(Finding).where(Finding.run_id == run.id, Finding.object_id == meter_id)
                       .order_by(Finding.date.desc())).all()
    return dict(run=run_meta(run), meter_id=meter_id, series=_rows(mv.sort_values("date")) if len(mv) else [],
                findings=[_f(f) for f in finds])


# ---------------------------------------------------------------------------- tanks & separators
@router.get("/tanks")
def tanks(run_id: Optional[int] = None, db: Session = Depends(get_db), s: Site = Depends(site),
          _: User = Depends(viewer)):
    run = resolve_run(db, s.id, run_id)
    nv = A.current_network(db, s.id)
    y = A.network_yaml(nv)
    st = object_status(db, run)
    rows = A.get_table(db, run.id, "tanks")
    tr = run if run.kind == "twin" else latest_run(db, s.id, "twin")
    live = {r["tank"]: r for r in A.get_table(db, tr.id, "L1_tanks")} if tr else {}
    finds = db.scalars(select(Finding).where(Finding.run_id == run.id, Finding.layer == "tank")
                       .order_by(Finding.date)).all()
    out = []
    for t in y.get("tanks", []):
        tid = t["id"]
        strap = pd.read_csv(io.StringIO(nv.strapping_csv.get(tid, "level_mm,volume_m3\n")))
        out.append(dict(id=tid, safe_fill_mm=t.get("safe_fill_mm"), insulated=t.get("insulated", False),
                        status=st.get(tid, {}).get("status", "OK"), open_issues=st.get(tid, {}).get("open_issues", 0),
                        strapping_points=len(strap),
                        max_level_mm=float(strap["level_mm"].max()) if len(strap) else None,
                        max_volume_m3=float(strap["volume_m3"].max()) if len(strap) else None,
                        live=live.get(tid), daily=[r for r in rows if r["tank"] == tid]))
    summ = A.get_table(db, run.id, "summary")
    balance = []
    for d in A.get_table(db, run.id, "dvr_vars"):
        if d["var"] in ("tank:dinv_oil", "dispatch:oil"):
            balance.append(dict(date=d["date"], var=d["var"], meas=d.get("meas"), rec=d.get("rec"),
                                sd_meas=d.get("sd_meas"), sd_rec=d.get("sd_rec"), z=d.get("z")))
    return dict(run=run_meta(run), tanks=out, findings=[_f(f) for f in finds], balance=balance,
                summary=[dict(date=r["date"], meas_oil=r.get("meas_oil"), dvr_oil=r.get("dvr_oil")) for r in summ])


@router.get("/separators")
def separators(run_id: Optional[int] = None, db: Session = Depends(get_db), s: Site = Depends(site),
               _: User = Depends(viewer)):
    run = resolve_run(db, s.id, run_id)
    y = A.network_yaml(A.current_network(db, s.id))
    st = object_status(db, run)
    rows = A.get_table(db, run.id, "separators")
    af = A.get_table(db, run.id, "separator_af")
    return dict(run=run_meta(run),
                separators=[dict(sp, status=st.get(sp["id"], {}).get("status", "OK"),
                                 open_issues=st.get(sp["id"], {}).get("open_issues", 0),
                                 daily=[r for r in rows if r["separator"] == sp["id"]],
                                 af=[r for r in af if r["separator"] == sp["id"]])
                            for sp in y.get("separators", [])])


# ---------------------------------------------------------------------------- reconciliation
@router.get("/reconciliation")
def reconciliation(run_id: Optional[int] = None, date: Optional[str] = None, db: Session = Depends(get_db),
                   s: Site = Depends(site), _: User = Depends(viewer)):
    run = resolve_run(db, s.id, run_id)
    days = A.get_table(db, run.id, "dvr_days")
    date = date or (days[-1]["date"] if days else None)
    out = dict(run=run_meta(run), days=days, date=date, bias_suspects=A.get_table(db, run.id, "bias_suspects"))
    if date:
        out["variables"] = [v for v in A.get_table(db, run.id, "dvr_vars") if v["date"] == date]
        out["constraints"] = [c for c in A.get_table(db, run.id, "dvr_constraints") if c["date"] == date]
        out["day"] = next((d for d in days if d["date"] == date), None)
        out["findings"] = [_f(f) for f in db.scalars(select(Finding).where(
            Finding.run_id == run.id, Finding.date == date, Finding.layer == "reconciliation"))]
    return out
