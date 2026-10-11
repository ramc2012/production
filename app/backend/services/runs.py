"""Validation and twin runs: queue, execute in a background job, persist, then update issues and
recommendations. Re-running a period always creates a new run; old runs are never overwritten."""
from __future__ import annotations

import pickle
import time
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

import flowval
from flowval.config import config_hash
from flowval.engine import run_frames
from flowval.report_excel import write_excel
from flowval.report_html import build_html

from .. import engine_adapter as A
from ..db import session_scope
from ..models import Run, Site, now
from ..settings import get_settings
from . import issues as issue_svc
from .jobs import JobContext, manager

KINDS = ("validation", "twin")


def default_site(db: Session) -> Site:
    s = db.scalar(select(Site).order_by(Site.id).limit(1))
    if s is None:
        st = get_settings()
        s = Site(code=st.site_code, name=st.site_code)
        db.add(s)
        db.commit()
    return s


def run_dir(run_id: int) -> Path:
    d = get_settings().runs_dir / str(run_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def queue_run(db: Session, site_id: int, kind: str, start: Optional[str], end: Optional[str], user: str,
              trigger: str = "manual", mode: Optional[str] = None) -> Run:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    run = Run(site_id=site_id, kind=kind, period_start=start, period_end=end, trigger=trigger,
              created_by=user, params={"mode": mode} if mode else {})
    db.add(run)
    db.commit()
    rid = run.id
    job_id = manager().submit(f"run:{kind}", lambda ctx: execute_run(rid, ctx),
                              {"run_id": rid, "kind": kind, "start": start, "end": end}, user)
    run.job_id = job_id
    db.commit()
    return run


def execute_run(run_id: int, ctx: JobContext) -> dict:
    t0 = time.time()
    with session_scope() as db:
        run = db.get(Run, run_id)
        run.status, run.started_at = "running", now()
        site_id, kind, start, end = run.site_id, run.kind, run.period_start, run.period_end
        mode = (run.params or {}).get("mode")
        nv, cv = A.current_network(db, site_id), A.current_config(db, site_id)
        if nv is None:
            raise RuntimeError("No network model loaded. Admin > Network, or seed the sample GGS")
        net, cfg = A.build_network(nv), A.build_config(cv)
        data, batches = A.load_inputs(db, site_id)
        overrides = A.active_overrides(db, site_id)
        run.engine_version = flowval.__version__
        run.network_version_id, run.config_version_id = nv.id, cv.id if cv else None
        run.config_hash, run.input_batches = config_hash(cfg), batches
        run.test_overrides = overrides
    # progress updates open their own transaction: never call ctx.progress inside a session_scope
    try:
        if kind == "validation":
            ctx.progress("validation", 0.1)
            res = run_frames(net, data, cfg, start, end, overrides)
            twin_ctx = None
        else:
            twin_ctx = _twin(net, data, cfg, start, end, mode, overrides, ctx)
            res = twin_ctx["res"]
        ctx.progress("saving results", 0.96)
        out = run_dir(run_id)
        with session_scope() as db:
            run = db.get(Run, run_id)
            run.period_start, run.period_end = res.dates[0], res.dates[-1]
            A.persist_validation(db, run, res)
            if twin_ctx is not None:
                A.persist_twin(db, run, twin_ctx)
            db.flush()
            issue_svc.sync_issues(db, run)
            if twin_ctx is not None:
                from . import twin as twin_svc
                twin_svc.create_recommendations(db, run, twin_ctx)
            run.summary = _summary(res, twin_ctx)
        _write_reports(out, res, twin_ctx)
        if twin_ctx is not None:
            with open(out / "twin.pkl", "wb") as fh:
                pickle.dump({"twin": twin_ctx["twin"], "res": res}, fh, protocol=pickle.HIGHEST_PROTOCOL)
        with session_scope() as db:
            run = db.get(Run, run_id)
            run.status, run.finished_at, run.runtime_s = "done", now(), time.time() - t0
        return {"run_id": run_id}
    except BaseException as e:
        with session_scope() as db:
            run = db.get(Run, run_id)
            cancelled = type(e).__name__ == "JobCancelled"
            run.status = "cancelled" if cancelled else "failed"
            run.log = (run.log or "") + ("Cancelled" if cancelled else f"{type(e).__name__}: {e}")
            run.finished_at, run.runtime_s = now(), time.time() - t0
        raise


def _twin(net, data, cfg, start, end, mode, overrides, ctx: JobContext) -> dict:
    from flowval.twin.run import run_twin_frames
    return run_twin_frames(net, data, cfg, start, end, mode, overrides,
                           progress=lambda stage, f: ctx.progress(stage, 0.05 + 0.9 * f))


def _write_reports(out: Path, res, twin_ctx) -> None:
    if twin_ctx is not None:
        from flowval.twin.run import write_twin_outputs
        write_twin_outputs(twin_ctx, out)
    else:
        tag = f"{res.dates[0]}_{res.dates[-1]}"
        (out / f"flow_validation_{tag}.html").write_text(build_html(res), encoding="utf-8")
        write_excel(res, out / f"flow_validation_{tag}.xlsx")


def _summary(res, twin_ctx) -> dict:
    f, s, d = res.findings, res.summary, res.dvr_days
    last = s.iloc[-1] if len(s) else None
    out = dict(days=len(res.dates), start=res.dates[0], end=res.dates[-1],
               alarms=int((f["severity"] == "ALARM").sum()), warnings=int((f["severity"] == "WARN").sum()),
               chi2_passed=int(d["passed_initial"].sum()) if len(d) else 0, chi2_days=len(d),
               dvr_oil_m3=float(s["dvr_oil"].sum()) if len(s) else None,
               last_day=None if last is None else dict(
                   date=last["date"], dvr_oil_m3=float(last["dvr_oil"]), dvr_water_m3=float(last["dvr_water"]),
                   dvr_gas_sm3=float(last["dvr_gas"]), af_oil=float(last["af_oil"])))
    if twin_ctx is not None:
        lv = twin_ctx["levels"]
        pl = twin_ctx["l4"]["plan"]["totals"]["oil"]
        out["twin"] = dict(levels={int(r["level"]): r["status"] for _, r in lv.iterrows()},
                           l4_gain_m3d=float(pl[1] - pl[0]), l5_gain_m3d=float(twin_ctx["l5"]["gain"]),
                           l5_mode=twin_ctx["l5"]["mode"])
    return out


def latest_run(db: Session, site_id: int, kind: Optional[str] = None) -> Optional[Run]:
    q = select(Run).where(Run.site_id == site_id, Run.status == "done")
    if kind:
        q = q.where(Run.kind == kind)
    return db.scalar(q.order_by(Run.id.desc()).limit(1))


def report_files(run_id: int) -> list:
    d = get_settings().runs_dir / str(run_id)
    if not d.exists():
        return []
    return sorted(p.name for p in d.iterdir() if p.suffix in (".html", ".xlsx", ".json"))
