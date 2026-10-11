"""Shared API helpers: the current site and which run a page is looking at."""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Run, Site
from ..services.runs import default_site, latest_run


def site(db: Session = Depends(get_db)) -> Site:
    return default_site(db)


def resolve_run(db: Session, site_id: int, run_id: Optional[int], kind: Optional[str] = None,
                required: bool = True) -> Optional[Run]:
    if run_id:
        r = db.get(Run, run_id)
        if r is None or r.site_id != site_id:
            raise HTTPException(404, f"Run {run_id} not found")
        if r.status != "done":
            raise HTTPException(409, f"Run {run_id} is {r.status}")
        if kind and r.kind != kind:
            raise HTTPException(400, f"Run {run_id} is a {r.kind} run, not a {kind} run")
        return r
    r = latest_run(db, site_id, kind)
    if r is None and required:
        what = "twin" if kind == "twin" else "validation or twin"
        raise HTTPException(404, f"No finished {what} run yet. Load data and start a run (Runs page).")
    return r


def run_meta(r: Optional[Run]) -> Optional[dict]:
    if r is None:
        return None
    return dict(id=r.id, kind=r.kind, status=r.status, period_start=r.period_start, period_end=r.period_end,
                trigger=r.trigger, engine_version=r.engine_version, config_hash=r.config_hash,
                network_version_id=r.network_version_id, config_version_id=r.config_version_id,
                input_batches=r.input_batches, test_overrides=r.test_overrides, summary=r.summary,
                created_by=r.created_by, created_at=r.created_at.isoformat() if r.created_at else None,
                started_at=r.started_at.isoformat() if r.started_at else None,
                finished_at=r.finished_at.isoformat() if r.finished_at else None,
                runtime_s=r.runtime_s, job_id=r.job_id, log=r.log)
