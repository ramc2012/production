"""Admin: users, network model, configuration, schedule, backups, audit log, system info."""
from __future__ import annotations

import secrets
from typing import Optional

import yaml
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

import flowval
from flowval.config import DEFAULTS

from .. import engine_adapter as A
from ..db import get_db
from ..models import AuditLog, ConfigVersion, NetworkVersion, Setting, Site, User
from ..security import ROLES, admin, audit, hash_password, viewer
from ..services import scheduler
from ..services import site as site_svc
from ..settings import get_settings
from .auth import user_out
from .deps import site

router = APIRouter(prefix="/admin", tags=["admin"])


# ---------------------------------------------------------------------------- users
class NewUser(BaseModel):
    username: str
    full_name: str = ""
    designation: str = ""
    role: str = "viewer"


@router.get("/users")
def users(db: Session = Depends(get_db), _: User = Depends(admin)):
    return [user_out(u) for u in db.scalars(select(User).order_by(User.username))]


@router.post("/users")
def create_user(body: NewUser, db: Session = Depends(get_db), me: User = Depends(admin)):
    if body.role not in ROLES:
        raise HTTPException(400, f"role must be one of {list(ROLES)}")
    name = body.username.strip()
    if not name or db.scalar(select(User).where(User.username == name)):
        raise HTTPException(409, "User name empty or already taken")
    pw = secrets.token_urlsafe(9)
    u = User(username=name, full_name=body.full_name, designation=body.designation, role=body.role,
             password_hash=hash_password(pw), must_change_password=True)
    db.add(u)
    audit(db, me.username, "user.create", f"user:{name}", None, dict(role=body.role))
    db.commit()
    return dict(user_out(u), temporary_password=pw)


class UserPatch(BaseModel):
    role: Optional[str] = None
    active: Optional[bool] = None
    full_name: Optional[str] = None
    designation: Optional[str] = None
    reset_password: bool = False


@router.patch("/users/{user_id}")
def patch_user(user_id: int, body: UserPatch, db: Session = Depends(get_db), me: User = Depends(admin)):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "User not found")
    before = user_out(u)
    if body.role is not None:
        if body.role not in ROLES:
            raise HTTPException(400, "Unknown role")
        if u.id == me.id and body.role != "admin":
            raise HTTPException(400, "You cannot remove your own admin role")
        u.role = body.role
    if body.active is not None:
        if u.id == me.id and not body.active:
            raise HTTPException(400, "You cannot disable yourself")
        u.active = body.active
    if body.full_name is not None:
        u.full_name = body.full_name
    if body.designation is not None:
        u.designation = body.designation
    out = {}
    if body.reset_password:
        pw = secrets.token_urlsafe(9)
        u.password_hash, u.must_change_password = hash_password(pw), True
        out["temporary_password"] = pw
    audit(db, me.username, "user.update", f"user:{u.username}", before,
          dict(user_out(u), password_reset=body.reset_password))
    db.commit()
    return dict(user_out(u), **out)


# ---------------------------------------------------------------------------- network & config
@router.get("/network")
def get_network(db: Session = Depends(get_db), s: Site = Depends(site), _: User = Depends(viewer)):
    nv = A.current_network(db, s.id)
    versions = [dict(id=v.id, created_by=v.created_by, created_at=v.created_at.isoformat(), comment=v.comment,
                     hash=v.hash)
                for v in db.scalars(select(NetworkVersion).where(NetworkVersion.site_id == s.id)
                                    .order_by(NetworkVersion.id.desc()).limit(50))]
    if nv is None:
        return dict(version=None, yaml_text="", well_static_csv="", strapping={}, versions=versions)
    return dict(version=nv.id, yaml_text=nv.yaml_text, well_static_csv=nv.well_static_csv or "",
                strapping={k: v.count("\n") for k, v in (nv.strapping_csv or {}).items()}, versions=versions)


class NetworkBody(BaseModel):
    yaml_text: str
    well_static_csv: Optional[str] = None
    comment: str = ""


@router.put("/network")
def put_network(body: NetworkBody, db: Session = Depends(get_db), s: Site = Depends(site), me: User = Depends(admin)):
    cur = A.current_network(db, s.id)
    strap = dict(cur.strapping_csv) if cur else {}
    static = body.well_static_csv if body.well_static_csv is not None else (cur.well_static_csv if cur else None)
    nv = site_svc.save_network(db, s.id, body.yaml_text, static, strap, me.username, body.comment)
    db.commit()
    return dict(version=nv.id)


@router.post("/network/strapping/{tank_id}")
async def put_strapping(tank_id: str, file: UploadFile = File(...), db: Session = Depends(get_db),
                        s: Site = Depends(site), me: User = Depends(admin)):
    cur = A.current_network(db, s.id)
    if cur is None:
        raise HTTPException(400, "Save a network model first")
    txt = (await file.read()).decode("utf-8-sig")
    strap = dict(cur.strapping_csv or {})
    strap[tank_id] = txt
    nv = site_svc.save_network(db, s.id, cur.yaml_text, cur.well_static_csv, strap, me.username,
                               f"strapping table {tank_id} from {file.filename}")
    db.commit()
    return dict(version=nv.id)


@router.get("/config")
def get_config(db: Session = Depends(get_db), s: Site = Depends(site), _: User = Depends(viewer)):
    cv = A.current_config(db, s.id)
    versions = [dict(id=v.id, created_by=v.created_by, created_at=v.created_at.isoformat(), comment=v.comment,
                     hash=v.hash)
                for v in db.scalars(select(ConfigVersion).where(ConfigVersion.site_id == s.id)
                                    .order_by(ConfigVersion.id.desc()).limit(50))]
    return dict(version=cv.id if cv else None, yaml_text=cv.yaml_text if cv else "",
                defaults_yaml=yaml.safe_dump(DEFAULTS, sort_keys=False), versions=versions)


class ConfigBody(BaseModel):
    yaml_text: str
    comment: str = ""


@router.post("/config/validate")
def validate_config(body: ConfigBody, _: User = Depends(admin)):
    site_svc.validate_config(body.yaml_text)
    return {"ok": True}


@router.put("/config")
def put_config(body: ConfigBody, db: Session = Depends(get_db), s: Site = Depends(site), me: User = Depends(admin)):
    cv = site_svc.save_config(db, s.id, body.yaml_text, me.username, body.comment)
    db.commit()
    return dict(version=cv.id, hash=cv.hash)


@router.post("/seed-sample")
def seed_sample(db: Session = Depends(get_db), s: Site = Depends(site), me: User = Depends(admin)):
    if A.current_network(db, s.id) is not None:
        raise HTTPException(409, "A network model is already loaded; the sample is only for an empty install")
    out = site_svc.seed_sample(db, s.id, me.username)
    return out


# ---------------------------------------------------------------------------- schedule, backups, audit
class ScheduleBody(BaseModel):
    enabled: Optional[bool] = None
    time: Optional[str] = None
    days: Optional[int] = None
    watch_enabled: Optional[bool] = None
    backup_time: Optional[str] = None
    backup_keep: Optional[int] = None


@router.get("/schedule")
def get_schedule(db: Session = Depends(get_db), _: User = Depends(viewer)):
    return dict(scheduler.schedule_settings(db), next_runs=scheduler.next_runs(),
                dropzone=str(get_settings().dropzone))


@router.put("/schedule")
def put_schedule(body: ScheduleBody, db: Session = Depends(get_db), me: User = Depends(admin)):
    for t in (body.time, body.backup_time):
        if t is not None:
            try:
                h, m = t.split(":")
                assert 0 <= int(h) < 24 and 0 <= int(m) < 60
            except (ValueError, AssertionError):
                raise HTTPException(400, "Times are HH:MM")
    if body.days is not None and not 1 <= body.days <= 366:
        raise HTTPException(400, "days must be 1-366")
    before = scheduler.schedule_settings(db)
    row = db.get(Setting, "schedule")
    val = dict(row.value) if row else {}
    val.update({k: v for k, v in body.model_dump().items() if v is not None})
    if row:
        row.value = val
    else:
        db.add(Setting(key="schedule", value=val))
    audit(db, me.username, "schedule.update", "schedule", before, val)
    db.commit()
    if get_settings().scheduler_enabled or val.get("enabled"):
        scheduler.start()
    return dict(scheduler.schedule_settings(db), next_runs=scheduler.next_runs())


@router.post("/schedule/run-now")
def run_now(db: Session = Depends(get_db), me: User = Depends(admin)):
    rid = scheduler.daily_run("manual", force=True)
    audit(db, me.username, "schedule.run_now", f"run:{rid}")
    db.commit()
    if rid is None:
        raise HTTPException(400, "No complete production day in the data yet")
    return dict(run_id=rid)


@router.post("/backup")
def backup(db: Session = Depends(get_db), me: User = Depends(admin)):
    p = scheduler.backup_now()
    audit(db, me.username, "backup.create", p.name)
    db.commit()
    return dict(file=p.name, size=p.stat().st_size)


@router.get("/backups")
def backups(_: User = Depends(admin)):
    d = get_settings().backups_dir
    return [dict(file=p.name, size=p.stat().st_size) for p in sorted(d.glob("flowtwin-*.sqlite"), reverse=True)] \
        if d.exists() else []


@router.get("/audit")
def audit_log(limit: int = 300, user: Optional[str] = None, action: Optional[str] = None,
              db: Session = Depends(get_db), _: User = Depends(admin)):
    q = select(AuditLog)
    if user:
        q = q.where(AuditLog.user == user)
    if action:
        q = q.where(AuditLog.action.like(f"{action}%"))
    return [dict(id=a.id, at=a.at.isoformat(), user=a.user, action=a.action, object=a.object, before=a.before,
                 after=a.after) for a in db.scalars(q.order_by(AuditLog.id.desc()).limit(min(limit, 5000)))]


@router.get("/system")
def system(_: User = Depends(viewer)):
    s = get_settings()
    db_file = s.data_dir / "flowtwin.sqlite"
    return dict(engine_version=flowval.__version__, app_version="1.0.0", data_dir=str(s.data_dir),
                database=s.database_url.split("///")[0] + "///…", db_size=db_file.stat().st_size if db_file.exists() else None,
                scheduler=scheduler.next_runs(), tls=s.tls_cert is not None, read_only_to_plant=True)
