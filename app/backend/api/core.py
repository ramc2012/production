"""Dashboard, network status, runs, jobs, findings and the issue workflow."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from flowval.findings import SEVERITY_ORDER

from .. import engine_adapter as A
from ..db import get_db
from ..models import Finding, InputBatch, InputRow, Issue, IssueComment, Job, Run, Site, User
from ..security import audit, engineer, viewer
from ..services import issues as issue_svc
from ..services import runs as run_svc
from ..services.jobs import manager
from ..settings import get_settings
from .deps import resolve_run, run_meta, site

router = APIRouter(tags=["core"])


def _f(r: Finding) -> dict:
    return dict(id=r.id, run_id=r.run_id, date=r.date, layer=r.layer, severity=r.severity, object_type=r.object_type,
                object_id=r.object_id, check=r.check, message=r.message, value=r.value, limit=r.limit,
                action=r.action, issue_id=r.issue_id)


def _issue(i: Issue) -> dict:
    return dict(id=i.id, object_type=i.object_type, object_id=i.object_id, check=i.check, layer=i.layer,
                severity=i.severity, state=i.state, assignee=i.assignee, due=i.due, first_seen=i.first_seen,
                last_seen=i.last_seen, occurrences=i.occurrences, message=i.last_message, action=i.last_action,
                last_run_id=i.last_run_id, updated_at=i.updated_at.isoformat() if i.updated_at else None)


def object_status(db: Session, run: Run) -> dict:
    """Status per object: the worst of its findings on the run's last day and its still-open issues."""
    out: dict = {}
    for oid, sev in db.execute(select(Finding.object_id, Finding.severity)
                               .where(Finding.run_id == run.id, Finding.date == run.period_end)):
        cur = out.setdefault(oid, {"status": "OK", "open_issues": 0})
        if SEVERITY_ORDER.get(sev, 0) > SEVERITY_ORDER.get(cur["status"], 0):
            cur["status"] = sev
    for oid, sev in db.execute(select(Issue.object_id, Issue.severity).where(Issue.site_id == run.site_id,
                                                                             Issue.state != "resolved")):
        cur = out.setdefault(oid, {"status": "OK", "open_issues": 0})
        cur["open_issues"] += 1
        if SEVERITY_ORDER.get(sev, 0) > SEVERITY_ORDER.get(cur["status"], 0):
            cur["status"] = sev
    return out


# ---------------------------------------------------------------------------- dashboard
@router.get("/dashboard")
def dashboard(run_id: Optional[int] = None, db: Session = Depends(get_db), s: Site = Depends(site),
              _: User = Depends(viewer)):
    run = resolve_run(db, s.id, run_id, required=False)
    twin_run = run if run is not None and run.kind == "twin" else run_svc.latest_run(db, s.id, "twin")
    last_batch = db.scalar(select(InputBatch).where(InputBatch.site_id == s.id, InputBatch.status == "committed")
                           .order_by(InputBatch.id.desc()).limit(1))
    latest_input = db.scalar(select(func.max(InputRow.date)).join(InputBatch, InputBatch.id == InputRow.batch_id)
                             .where(InputBatch.status == "committed", InputRow.dataset == "well_daily"))
    open_issues = dict(db.execute(select(Issue.severity, func.count()).where(Issue.site_id == s.id,
                                                                             Issue.state != "resolved")
                                  .group_by(Issue.severity)).all())
    unack = db.scalar(select(func.count()).select_from(Issue).where(Issue.site_id == s.id, Issue.state == "open",
                                                                    Issue.severity == "ALARM"))
    out = dict(site=dict(code=s.code, name=s.name), run=run_meta(run), twin_run=run_meta(twin_run),
               data=dict(latest_well_day=latest_input,
                         last_batch=None if last_batch is None else dict(
                             id=last_batch.id, at=last_batch.at.isoformat(), source=last_batch.source,
                             filename=last_batch.filename, rows=last_batch.rows)),
               open_issues={k: int(v) for k, v in open_issues.items()}, unacknowledged_alarms=int(unack or 0))
    if run is None:
        return out
    summ = A.get_table(db, run.id, "summary")
    days = A.get_table(db, run.id, "dvr_days")
    last = summ[-1] if summ else {}
    out["kpi"] = dict(date=last.get("date"),
                      dvr_oil_m3=last.get("dvr_oil"), dvr_water_m3=last.get("dvr_water"),
                      dvr_gas_sm3=last.get("dvr_gas"), meas_oil_m3=last.get("meas_oil"),
                      theo_oil_m3=last.get("theo_oil"), af_oil=last.get("af_oil"), af_water=last.get("af_water"),
                      af_gas=last.get("af_gas"), lift_gas_sm3=last.get("lift_gas"),
                      chi2_passed=sum(1 for d in days if d.get("passed_initial")), chi2_days=len(days),
                      chi2_last_passed=bool(days[-1].get("passed_initial")) if days else None,
                      chi2_last_ratio=days[-1].get("ratio") if days else None)
    out["series"] = [dict(date=r["date"], dvr_oil=r.get("dvr_oil"), meas_oil=r.get("meas_oil"),
                          theo_oil=r.get("theo_oil"), af_oil=r.get("af_oil"), af_water=r.get("af_water"),
                          af_gas=r.get("af_gas"), dvr_water=r.get("dvr_water"), dvr_gas=r.get("dvr_gas"))
                     for r in summ]
    out["chi2"] = [dict(date=d["date"], ratio=d.get("ratio"), passed=d.get("passed_initial"),
                        eliminated=d.get("eliminated")) for d in days]
    top = db.scalars(select(Finding).where(Finding.run_id == run.id, Finding.date == run.period_end,
                                           Finding.severity.in_(["ALARM", "WARN"]))).all()
    top.sort(key=lambda f: (-SEVERITY_ORDER.get(f.severity, 0), f.layer, f.object_id))
    out["top_exceptions"] = [_f(f) for f in top[:12]]
    out["wells_online"] = len({r.well_id for r in _wells_on(db, run)})
    if twin_run is not None:
        out["twin"] = dict(levels=A.get_table(db, twin_run.id, "Levels"),
                           facility=(A.get_table(db, twin_run.id, "L1_facility") or [None])[0],
                           summary=twin_run.summary.get("twin") if twin_run.summary else None)
    return out


def _wells_on(db: Session, run: Run):
    from ..models import WellDailyResult
    return db.scalars(select(WellDailyResult).where(WellDailyResult.run_id == run.id,
                                                    WellDailyResult.date == run.period_end,
                                                    WellDailyResult.hours_on > 0)).all()


# ---------------------------------------------------------------------------- network
@router.get("/network")
def network(run_id: Optional[int] = None, db: Session = Depends(get_db), s: Site = Depends(site),
            _: User = Depends(viewer)):
    nv = A.current_network(db, s.id)
    if nv is None:
        raise HTTPException(404, "No network model loaded")
    y = A.network_yaml(nv)
    run = resolve_run(db, s.id, run_id, required=False)
    st = object_status(db, run) if run else {}
    wells_last = {}
    tanks_last = {}
    if run is not None:
        for r in _wells_day(db, run):
            wells_last[r.well_id] = dict(dvr_oil_m3=r.dvr_oil_m3, dvr_oil_sd_m3=r.dvr_oil_sd_m3, hours_on=r.hours_on,
                                         test_status=r.data.get("test_status"), test_age_d=r.data.get("test_age_d"))
        for t in A.get_table(db, run.id, "tanks"):
            if t["date"] == run.period_end:
                tanks_last[t["tank"]] = t

    def stat(i):
        return st.get(i, {"status": "OK", "open_issues": 0})
    meters = {m["id"]: m for m in y.get("meters", [])}
    return dict(
        name=(y.get("installation") or {}).get("name", s.name), network_version=nv.id,
        run=run_meta(run) if run else None,
        wells=[dict(id=w["id"], lift=w.get("lift", "self_flow"), separator=w.get("separator"),
                    lift_gas_meter=w.get("lift_gas_meter"), **stat(w["id"]), **wells_last.get(w["id"], {}))
               for w in y.get("wells", [])],
        separators=[dict(id=sp["id"], kind=sp.get("kind", "production"), phases=sp.get("phases", 3),
                         oil_meter=sp.get("oil_meter"), water_meter=sp.get("water_meter"),
                         gas_meter=sp.get("gas_meter"), **stat(sp["id"])) for sp in y.get("separators", [])],
        meters=[dict(m, **stat(m["id"])) for m in meters.values()],
        tanks=[dict(id=t["id"], safe_fill_mm=t.get("safe_fill_mm"), **stat(t["id"]), last=tanks_last.get(t["id"]))
               for t in y.get("tanks", [])],
        dispatch=dict(meter=(y.get("dispatch") or {}).get("meter"),
                      **stat((y.get("dispatch") or {}).get("meter") or "")),
        gas_disposal=[dict(g, **stat(g["meter"])) for g in y.get("gas_disposal", [])],
        lift_gas=dict(header_meter=(y.get("lift_gas") or {}).get("header_meter"),
                      **stat((y.get("lift_gas") or {}).get("header_meter") or "")),
        installation=dict(**stat("DVR")),
    )


def _wells_day(db: Session, run: Run):
    from ..models import WellDailyResult
    return db.scalars(select(WellDailyResult).where(WellDailyResult.run_id == run.id,
                                                    WellDailyResult.date == run.period_end)).all()


# ---------------------------------------------------------------------------- runs & jobs
class RunRequest(BaseModel):
    kind: str = "validation"
    start: Optional[str] = None
    end: Optional[str] = None
    mode: Optional[str] = None


@router.post("/runs")
def start_run(body: RunRequest, db: Session = Depends(get_db), s: Site = Depends(site),
              user: User = Depends(engineer)):
    if body.start and body.end and body.start > body.end:
        raise HTTPException(400, "start must be on or before end")
    if A.current_network(db, s.id) is None:
        raise HTTPException(400, "Load a network model first (Admin > Network, or seed the sample GGS)")
    try:
        run = run_svc.queue_run(db, s.id, body.kind, body.start, body.end, user.username, mode=body.mode)
    except ValueError as e:
        raise HTTPException(400, str(e))
    audit(db, user.username, "run.queue", f"run:{run.id}", None, body.model_dump())
    db.commit()
    return run_meta(run)


@router.get("/runs")
def list_runs(limit: int = 100, kind: Optional[str] = None, db: Session = Depends(get_db), s: Site = Depends(site),
              _: User = Depends(viewer)):
    q = select(Run).where(Run.site_id == s.id)
    if kind:
        q = q.where(Run.kind == kind)
    runs = db.scalars(q.order_by(Run.id.desc()).limit(min(limit, 500))).all()
    out = []
    for r in runs:
        m = run_meta(r)
        if r.job_id and r.status in ("queued", "running"):
            j = db.get(Job, r.job_id)
            m["job"] = dict(progress=j.progress, stage=j.stage) if j else None
        out.append(m)
    return out


@router.get("/runs/{run_id}")
def get_run(run_id: int, db: Session = Depends(get_db), _: User = Depends(viewer)):
    r = db.get(Run, run_id)
    if r is None:
        raise HTTPException(404, "Run not found")
    m = run_meta(r)
    m["files"] = run_svc.report_files(run_id)
    m["job"] = _job(db.get(Job, r.job_id)) if r.job_id else None
    return m


@router.get("/runs/{run_id}/files/{name}")
def run_file(run_id: int, name: str, _: User = Depends(viewer)):
    if name not in run_svc.report_files(run_id):
        raise HTTPException(404, "File not found")
    path = get_settings().runs_dir / str(run_id) / name
    media = {".html": "text/html", ".json": "application/json",
             ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}[path.suffix]
    return FileResponse(path, media_type=media, filename=name if path.suffix != ".html" else None)


def _job(j: Optional[Job]) -> Optional[dict]:
    if j is None:
        return None
    return dict(id=j.id, kind=j.kind, status=j.status, progress=j.progress, stage=j.stage, params=j.params,
                result=j.result, error=j.error, cancel_requested=j.cancel_requested, created_by=j.created_by,
                created_at=j.created_at.isoformat(), started_at=j.started_at.isoformat() if j.started_at else None,
                finished_at=j.finished_at.isoformat() if j.finished_at else None)


@router.get("/jobs")
def list_jobs(active: bool = False, db: Session = Depends(get_db), _: User = Depends(viewer)):
    q = select(Job)
    if active:
        q = q.where(Job.status.in_(["queued", "running"]))
    return [_job(j) for j in db.scalars(q.order_by(Job.id.desc()).limit(50))]


@router.get("/jobs/{job_id}")
def get_job(job_id: int, db: Session = Depends(get_db), _: User = Depends(viewer)):
    j = db.get(Job, job_id)
    if j is None:
        raise HTTPException(404, "Job not found")
    return _job(j)


@router.delete("/jobs/{job_id}")
def cancel_job(job_id: int, db: Session = Depends(get_db), user: User = Depends(engineer)):
    ok = manager().cancel(job_id)
    if not ok:
        raise HTTPException(409, "Job is not running")
    audit(db, user.username, "job.cancel", f"job:{job_id}")
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------- findings & issues
@router.get("/findings")
def findings(run_id: Optional[int] = None, date: Optional[str] = None, date_from: Optional[str] = None,
             date_to: Optional[str] = None, layer: Optional[str] = None, severity: Optional[str] = None,
             object_id: Optional[str] = None, check: Optional[str] = None, q: Optional[str] = None,
             limit: int = 2000, db: Session = Depends(get_db), s: Site = Depends(site), _: User = Depends(viewer)):
    run = resolve_run(db, s.id, run_id)
    qy = select(Finding).where(Finding.run_id == run.id)
    if date:
        qy = qy.where(Finding.date == date)
    if date_from:
        qy = qy.where(Finding.date >= date_from)
    if date_to:
        qy = qy.where(Finding.date <= date_to)
    for col, val in ((Finding.layer, layer), (Finding.severity, severity), (Finding.object_id, object_id),
                     (Finding.check, check)):
        if val:
            qy = qy.where(col.in_(val.split(",")))
    if q:
        like = f"%{q}%"
        qy = qy.where(or_(Finding.message.ilike(like), Finding.object_id.ilike(like), Finding.check.ilike(like)))
    rows = db.scalars(qy.limit(min(limit, 10000))).all()
    rows.sort(key=lambda f: (-SEVERITY_ORDER.get(f.severity, 0), f.date, f.layer, f.object_id))
    return dict(run=run_meta(run), findings=[_f(f) for f in rows])


@router.get("/register")
def register(run_id: Optional[int] = None, db: Session = Depends(get_db), s: Site = Depends(site),
             _: User = Depends(viewer)):
    run = resolve_run(db, s.id, run_id)
    return dict(run=run_meta(run), register=A.get_table(db, run.id, "register"))


@router.get("/issues")
def list_issues(state: Optional[str] = "open,acknowledged", severity: Optional[str] = None,
                layer: Optional[str] = None, object_id: Optional[str] = None, assignee: Optional[str] = None,
                q: Optional[str] = None, db: Session = Depends(get_db), s: Site = Depends(site),
                _: User = Depends(viewer)):
    qy = select(Issue).where(Issue.site_id == s.id)
    for col, val in ((Issue.state, state), (Issue.severity, severity), (Issue.layer, layer),
                     (Issue.object_id, object_id), (Issue.assignee, assignee)):
        if val:
            qy = qy.where(col.in_(val.split(",")))
    if q:
        like = f"%{q}%"
        qy = qy.where(or_(Issue.last_message.ilike(like), Issue.object_id.ilike(like), Issue.check.ilike(like)))
    rows = db.scalars(qy).all()
    rows.sort(key=lambda i: i.last_seen, reverse=True)          # newest first within each group
    rows.sort(key=lambda i: (i.state == "resolved", -SEVERITY_ORDER.get(i.severity, 0)))
    return [_issue(i) for i in rows]


@router.get("/issues/{issue_id}")
def get_issue(issue_id: int, db: Session = Depends(get_db), s: Site = Depends(site), _: User = Depends(viewer)):
    i = db.get(Issue, issue_id)
    if i is None or i.site_id != s.id:
        raise HTTPException(404, "Issue not found")
    comments = db.scalars(select(IssueComment).where(IssueComment.issue_id == i.id).order_by(IssueComment.id)).all()
    hist = db.scalars(select(Finding).where(Finding.issue_id == i.id).order_by(Finding.date.desc(), Finding.id.desc())
                      .limit(200)).all()
    seen, recent = set(), []
    for f in hist:              # one line per production day (latest run wins)
        if f.date not in seen:
            seen.add(f.date)
            recent.append(_f(f))
    return dict(_issue(i), comments=[dict(at=c.at.isoformat(), user=c.user, text=c.text) for c in comments],
                findings=recent)


class IssuePatch(BaseModel):
    state: Optional[str] = None
    assignee: Optional[str] = None
    clear_assignee: bool = False
    due: Optional[str] = None
    comment: Optional[str] = None


@router.patch("/issues/{issue_id}")
def patch_issue(issue_id: int, body: IssuePatch, db: Session = Depends(get_db), s: Site = Depends(site),
                user: User = Depends(engineer)):
    i = db.get(Issue, issue_id)
    if i is None or i.site_id != s.id:
        raise HTTPException(404, "Issue not found")
    issue_svc.update_issue(db, i, user.username, body.state, body.assignee, body.due, body.comment,
                           body.clear_assignee)
    db.commit()
    return _issue(i)


class BulkIssues(BaseModel):
    ids: List[int]
    state: Optional[str] = None
    assignee: Optional[str] = None
    comment: Optional[str] = None


@router.post("/issues/bulk")
def bulk_issues(body: BulkIssues, db: Session = Depends(get_db), s: Site = Depends(site),
                user: User = Depends(engineer)):
    done, skipped = [], []
    for iid in body.ids:
        i = db.get(Issue, iid)
        if i is None or i.site_id != s.id:
            skipped.append(dict(id=iid, reason="not found"))
            continue
        try:
            issue_svc.update_issue(db, i, user.username, body.state, body.assignee, None, body.comment)
            done.append(iid)
        except HTTPException as e:
            skipped.append(dict(id=iid, reason=e.detail))
    db.commit()
    return dict(updated=done, skipped=skipped)


@router.get("/users/names")
def user_names(db: Session = Depends(get_db), _: User = Depends(viewer)):
    """For assignee pickers."""
    return [dict(username=u.username, full_name=u.full_name, role=u.role)
            for u in db.scalars(select(User).where(User.active.is_(True)).order_by(User.username))]
