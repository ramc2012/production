"""Allocation (monthly, close/lock, export) and the digital-twin area (L0-L5)."""
from __future__ import annotations

from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import engine_adapter as A
from ..db import get_db
from ..models import AllocationClose, AutonomyRun, Recommendation, Run, Site, User
from ..security import admin, audit, engineer, viewer
from ..services import allocation as alloc_svc
from ..services import twin as twin_svc
from ..services.jobs import manager
from .deps import resolve_run, run_meta, site

router = APIRouter(tags=["operations"])


# ---------------------------------------------------------------------------- allocation
@router.get("/allocation/months")
def allocation_months(db: Session = Depends(get_db), s: Site = Depends(site), _: User = Depends(viewer)):
    months = set()
    for a, b in db.execute(select(Run.period_start, Run.period_end).where(Run.site_id == s.id, Run.status == "done")):
        if a and b:
            y, m = int(a[:4]), int(a[5:7])
            while f"{y:04d}-{m:02d}" <= b[:7]:
                months.add(f"{y:04d}-{m:02d}")
                y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    closed = {c.month: dict(by=c.closed_by, at=c.closed_at.isoformat(), run_id=c.run_id)
              for c in db.scalars(select(AllocationClose).where(AllocationClose.site_id == s.id))}
    return [dict(month=m, closed=closed.get(m)) for m in sorted(months, reverse=True)]


@router.get("/allocation")
def allocation(month: str, db: Session = Depends(get_db), s: Site = Depends(site), _: User = Depends(viewer)):
    return alloc_svc.month_view(db, s.id, month)


class CloseBody(BaseModel):
    run_id: Optional[int] = None


@router.post("/allocation/{month}/close")
def close_month(month: str, body: CloseBody, db: Session = Depends(get_db), s: Site = Depends(site),
                user: User = Depends(engineer)):
    c = alloc_svc.close_month(db, s.id, month, body.run_id, user.username)
    db.commit()
    return dict(month=c.month, run_id=c.run_id, totals=c.totals, closed_by=c.closed_by, closed_at=c.closed_at.isoformat())


class ReopenBody(BaseModel):
    reason: str


@router.post("/allocation/{month}/reopen")
def reopen_month(month: str, body: ReopenBody, db: Session = Depends(get_db), s: Site = Depends(site),
                 user: User = Depends(admin)):
    alloc_svc.reopen_month(db, s.id, month, user.username, body.reason)
    db.commit()
    return {"ok": True}


@router.get("/allocation/{month}/export.xlsx")
def export_month(month: str, db: Session = Depends(get_db), s: Site = Depends(site), user: User = Depends(viewer)):
    data = alloc_svc.export_xlsx(db, s.id, month)
    audit(db, user.username, "allocation.export", f"month:{month}")
    db.commit()
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="allocation_{s.code}_{month}.xlsx"'})


# ---------------------------------------------------------------------------- digital twin
TWIN_TABLES = {"Levels", "Assessment", "L0_wells", "L1_live_wells", "L1_tanks", "L1_facility", "L3_well_forecast",
               "L3_installation", "L3_lookahead", "L3_meter_drift", "L3_tests_due", "L3_daily_forecast",
               "L3_scenarios", "L4_gas_lift", "L4_gl_curves", "L4_gl_summary", "L4_chokes", "L4_srp",
               "L4_test_schedule", "L4_maintenance", "L4_plan_wells", "L4_plan_totals", "L5_trajectory",
               "L5_audit_log", "L5_summary", "twin_state"}


@router.get("/twin/summary")
def twin_summary(run_id: Optional[int] = None, db: Session = Depends(get_db), s: Site = Depends(site),
                 _: User = Depends(viewer)):
    run = resolve_run(db, s.id, run_id, kind="twin")
    t = lambda n: A.get_table(db, run.id, n)  # noqa: E731
    return dict(run=run_meta(run), levels=t("Levels"), assessment=t("Assessment"),
                facility=(t("L1_facility") or [None])[0], gl_summary=(t("L4_gl_summary") or [None])[0],
                plan_totals=t("L4_plan_totals"), l5=(t("L5_summary") or [None])[0],
                lookahead=t("L3_lookahead"))


@router.get("/twin/tables/{name}")
def twin_table(name: str, run_id: Optional[int] = None, db: Session = Depends(get_db), s: Site = Depends(site),
               _: User = Depends(viewer)):
    if name not in TWIN_TABLES:
        raise HTTPException(404, f"Unknown twin table {name}")
    run = resolve_run(db, s.id, run_id, kind="twin")
    return dict(run_id=run.id, name=name, rows=A.get_table(db, run.id, name))


class WhatIf(BaseModel):
    run_id: Optional[int] = None
    changes: Dict[str, Dict[str, Optional[float]]]


@router.post("/twin/whatif")
def twin_whatif(body: WhatIf, db: Session = Depends(get_db), s: Site = Depends(site), _: User = Depends(viewer)):
    run = resolve_run(db, s.id, body.run_id, kind="twin")
    return twin_svc.whatif(run.id, body.changes)


def _rec(r: Recommendation) -> dict:
    return dict(id=r.id, run_id=r.run_id, well_id=r.well_id, kind=r.kind, current=r.current, proposed=r.proposed,
                predicted_d_oil_m3d=r.predicted_d_oil_m3d, rationale=r.rationale, status=r.status,
                decided_by=r.decided_by, decided_at=r.decided_at.isoformat() if r.decided_at else None,
                comment=r.comment, created_at=r.created_at.isoformat())


@router.get("/twin/recommendations")
def recommendations(status: Optional[str] = None, run_id: Optional[int] = None, db: Session = Depends(get_db),
                    s: Site = Depends(site), _: User = Depends(viewer)):
    q = select(Recommendation).where(Recommendation.site_id == s.id)
    if run_id:
        q = q.where(Recommendation.run_id == run_id)
    if status:
        q = q.where(Recommendation.status.in_(status.split(",")))
    return [_rec(r) for r in db.scalars(q.order_by(Recommendation.id.desc()).limit(500))]


class Decision(BaseModel):
    status: str
    comment: str = ""


@router.patch("/twin/recommendations/{rec_id}")
def decide(rec_id: int, body: Decision, db: Session = Depends(get_db), s: Site = Depends(site),
           user: User = Depends(engineer)):
    r = db.get(Recommendation, rec_id)
    if r is None or r.site_id != s.id:
        raise HTTPException(404, "Recommendation not found")
    before = dict(status=r.status)
    twin_svc.decide(db, r, body.status, user.username, body.comment)
    audit(db, user.username, "recommendation.decide", f"recommendation:{r.id} {r.well_id}", before,
          dict(status=r.status, comment=body.comment, proposed=r.proposed,
               note="recorded only; no plant system is written"))
    db.commit()
    return _rec(r)


class AutonomyBody(BaseModel):
    run_id: Optional[int] = None
    mode: str = "autonomous_sim"
    cycles: Optional[int] = None
    approve_min_gain_m3d: Optional[float] = None
    plant_pi_mismatch_sd: Optional[float] = None
    plant_noise_pct: Optional[float] = None
    seed: Optional[int] = None


@router.post("/twin/autonomy")
def start_autonomy(body: AutonomyBody, db: Session = Depends(get_db), s: Site = Depends(site),
                   user: User = Depends(engineer)):
    run = resolve_run(db, s.id, body.run_id, kind="twin")
    if body.mode not in twin_svc.AUTONOMY_MODES:
        raise HTTPException(400, f"mode must be one of {twin_svc.AUTONOMY_MODES}")
    if body.cycles is not None and not 1 <= body.cycles <= 30:
        raise HTTPException(400, "cycles must be 1-30")
    params = {k: v for k, v in body.model_dump().items() if v is not None and k != "run_id"}
    twin_svc.load_twin(run.id)       # fail fast if the model is missing
    rid, uname = run.id, user.username
    job_id = manager().submit("twin:autonomy_sandbox", lambda ctx: twin_svc.autonomy_job(rid, params, uname, ctx),
                              {"run_id": rid, **params}, uname)
    audit(db, uname, "twin.autonomy_sandbox", f"run:{rid}", None, dict(params, job_id=job_id, target="simulator"))
    db.commit()
    return dict(job_id=job_id, run_id=rid, banner="Simulation only - the plant simulator, never a control system")


@router.get("/twin/autonomy")
def list_autonomy(db: Session = Depends(get_db), s: Site = Depends(site), _: User = Depends(viewer)):
    rows = db.scalars(select(AutonomyRun).join(Run, Run.id == AutonomyRun.run_id).where(Run.site_id == s.id)
                      .order_by(AutonomyRun.id.desc()).limit(50))
    return [dict(id=a.id, run_id=a.run_id, job_id=a.job_id, mode=a.mode, params=a.params, created_by=a.created_by,
                 created_at=a.created_at.isoformat(), gain=a.result.get("gain"), lift_start=a.result.get("lift_start"),
                 lift_end=a.result.get("lift_end")) for a in rows]


@router.get("/twin/autonomy/{aid}")
def get_autonomy(aid: int, db: Session = Depends(get_db), _: User = Depends(viewer)):
    a = db.get(AutonomyRun, aid)
    if a is None:
        raise HTTPException(404, "Sandbox run not found")
    return dict(id=a.id, run_id=a.run_id, job_id=a.job_id, mode=a.mode, params=a.params, result=a.result,
                created_by=a.created_by, created_at=a.created_at.isoformat())
