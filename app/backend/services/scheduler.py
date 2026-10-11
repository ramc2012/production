"""In-process scheduler (APScheduler): daily twin run, drop-zone folder import, nightly backup.

Daily run: at ``time`` (default 06:30) a twin run covers the last ``days`` production days ending on
the latest day that has a closing tank gauge. It is skipped if an identical run already exists
(same end day and same input batches), and retried once 30 minutes later if it failed.
"""
from __future__ import annotations

import datetime as dt
import logging
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Optional

import pandas as pd
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select

from .. import engine_adapter as A
from ..db import engine, session_scope
from ..models import InputBatch, Run, Setting
from ..security import audit
from ..settings import get_settings
from . import importer
from .runs import default_site, queue_run

log = logging.getLogger("flowtwin.scheduler")
_sched: Optional[BackgroundScheduler] = None


def schedule_settings(db) -> dict:
    s = get_settings()
    base = dict(enabled=s.scheduler_enabled, time=s.daily_run_time, days=s.daily_run_days,
                watch_enabled=s.watch_enabled, backup_time=s.backup_time, backup_keep=s.backup_keep)
    row = db.get(Setting, "schedule")
    if row:
        base.update(row.value or {})
    return base


def latest_day(db, site_id: int) -> Optional[str]:
    raw, _ = A.raw_frames(db, site_id, datasets=["well_daily", "tank_gauges"])
    if "well_daily" not in raw or "tank_gauges" not in raw:
        return None
    wd = raw["well_daily"]
    tg = raw["tank_gauges"]
    data = {"well_daily": wd.assign(date=pd.to_datetime(wd["date"]).dt.strftime("%Y-%m-%d")),
            "tank_gauges": tg.assign(datetime=pd.to_datetime(tg["datetime"]))}
    days = A.production_days(data)
    return days[-1] if days else None


def daily_run(trigger: str = "schedule", force: bool = False) -> Optional[int]:
    with session_scope() as db:
        site = default_site(db)
        conf = schedule_settings(db)
        end = latest_day(db, site.id)
        if end is None:
            log.info("daily run: no complete production day yet")
            return None
        start = (pd.Timestamp(end) - pd.Timedelta(days=int(conf["days"]) - 1)).strftime("%Y-%m-%d")
        batches = sorted(db.scalars(select(InputBatch.id).where(InputBatch.site_id == site.id,
                                                                InputBatch.status == "committed")))
        if not force:
            prev = db.scalar(select(Run).where(Run.site_id == site.id, Run.kind == "twin", Run.period_end == end,
                                               Run.status.in_(["queued", "running", "done"]))
                             .order_by(Run.id.desc()).limit(1))
            if prev is not None and (prev.status != "done" or list(prev.input_batches or []) == batches):
                log.info("daily run: run %s already covers %s", prev.id, end)
                return None
        run = queue_run(db, site.id, "twin", start, end, "scheduler", trigger=trigger)
        audit(db, "scheduler", "run.queue", f"run:{run.id}", None, {"kind": "twin", "start": start, "end": end,
                                                                   "trigger": trigger})
        return run.id


def retry_failed() -> None:
    with session_scope() as db:
        today = dt.datetime.utcnow().date().isoformat()
        last = db.scalar(select(Run).where(Run.trigger == "schedule").order_by(Run.id.desc()).limit(1))
        if last is not None and last.status == "failed" and last.created_at.date().isoformat() == today:
            if not (last.params or {}).get("retried"):
                log.warning("daily run %s failed; retrying once", last.id)
                last.params = dict(last.params or {}, retried=True)
                db.commit()
                daily_run("schedule", force=True)


def scan_dropzone() -> Optional[int]:
    """Import files dropped in <data>/dropzone (stable for 10 s) as one batch, then run the twin."""
    s = get_settings()
    dz = s.dropzone
    dz.mkdir(parents=True, exist_ok=True)
    files = [p for p in dz.iterdir() if p.is_file() and p.suffix.lower() in (".csv", ".xlsx")
             and time.time() - p.stat().st_mtime > 10]
    if not files:
        return None
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    with session_scope() as db:
        site = default_site(db)
        b = importer.import_files(db, site.id, [(p.name, p.read_bytes()) for p in files], "dropzone",
                                  source="dropzone")
        ok = b.status == "preview"
        if ok:
            b.status = "committed"
        audit(db, "dropzone", "data.commit" if ok else "data.reject", f"batch:{b.id}", None,
              {"files": [p.name for p in files], "rows": b.rows, "report": b.validation_report})
        bid = b.id
        report = b.validation_report
    dest = dz / ("processed" if ok else "rejected") / stamp
    dest.mkdir(parents=True, exist_ok=True)
    for p in files:
        shutil.move(str(p), str(dest / p.name))
    if not ok:
        (dest / "REJECTED_report.txt").write_text(
            "FlowTwin rejected these files. Nothing was imported.\n\nErrors:\n  - "
            + "\n  - ".join(report.get("errors", [])) + "\n\nWarnings:\n  - "
            + "\n  - ".join(report.get("warnings", []) or ["none"]) + "\n", encoding="utf-8")
        log.warning("dropzone batch %s rejected", bid)
        return None
    daily_run("dropzone")
    return bid


def backup_now(keep: Optional[int] = None) -> Path:
    s = get_settings()
    s.backups_dir.mkdir(parents=True, exist_ok=True)
    url = s.database_url
    if not url.startswith("sqlite"):
        raise RuntimeError("Built-in backup covers SQLite only; use pg_dump for PostgreSQL")
    target = s.backups_dir / f"flowtwin-{dt.datetime.now():%Y%m%d-%H%M%S}.sqlite"
    raw = engine().raw_connection()
    try:
        dst = sqlite3.connect(str(target))
        with dst:
            raw.driver_connection.backup(dst)
        dst.close()
    finally:
        raw.close()
    with session_scope() as db:
        keep = keep or int(schedule_settings(db)["backup_keep"])
    olds = sorted(s.backups_dir.glob("flowtwin-*.sqlite"))
    for old in olds[:-keep] if keep > 0 else []:
        old.unlink()
    return target


def restore_backup(file, data_dir: Path):
    """Replace <data_dir>/flowtwin.sqlite with a backup (service stopped). The current database is
    kept as flowtwin.before-restore.sqlite. ``file`` is a path or a name in <data_dir>/backups."""
    src = Path(file)
    if not src.exists():
        src = Path(data_dir) / "backups" / str(file)
    if not src.exists():
        raise FileNotFoundError(f"backup not found: {file}")
    with sqlite3.connect(str(src)) as c:          # refuse anything that is not a FlowTwin database
        if c.execute("SELECT count(*) FROM sqlite_master WHERE name IN ('run', 'audit_log')").fetchone()[0] != 2:
            raise FileNotFoundError(f"{src.name} is not a FlowTwin database")
    dst = Path(data_dir) / "flowtwin.sqlite"
    kept = None
    if dst.exists():
        kept = dst.with_name("flowtwin.before-restore.sqlite")
        shutil.copy2(dst, kept)
    for ext in ("-wal", "-shm"):
        Path(str(dst) + ext).unlink(missing_ok=True)
    shutil.copy2(src, dst)
    return src, dst, kept


def _hm(t: str):
    h, m = t.split(":")
    return int(h), int(m)


def start() -> BackgroundScheduler:
    global _sched
    stop()
    s = get_settings()
    with session_scope() as db:
        conf = schedule_settings(db)
    sch = BackgroundScheduler(daemon=True)
    if conf["enabled"]:
        h, m = _hm(conf["time"])
        sch.add_job(daily_run, "cron", hour=h, minute=m, id="daily_run", coalesce=True, misfire_grace_time=3600)
        rh, rm = divmod(h * 60 + m + 30, 60)
        sch.add_job(retry_failed, "cron", hour=rh % 24, minute=rm, id="daily_retry", coalesce=True)
    if conf["watch_enabled"]:
        sch.add_job(scan_dropzone, "interval", seconds=s.watch_interval_s, id="dropzone", coalesce=True,
                    max_instances=1)
    h, m = _hm(conf["backup_time"])
    sch.add_job(backup_now, "cron", hour=h, minute=m, id="backup", coalesce=True)
    sch.start()
    _sched = sch
    return sch


def stop() -> None:
    global _sched
    if _sched is not None:
        _sched.shutdown(wait=False)
        _sched = None


def next_runs() -> dict:
    if _sched is None:
        return {}
    return {j.id: j.next_run_time.isoformat() if j.next_run_time else None for j in _sched.get_jobs()}
