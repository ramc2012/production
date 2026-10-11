"""FlowTwin command line (also used by the Windows service).

    python -m app.backend.cli serve                 start the web app (FLOWTWIN_* settings apply)
    python -m app.backend.cli seed-sample           load the synthetic sample GGS into an empty install
    python -m app.backend.cli run [--kind twin]     queue a run over all data and wait for it
    python -m app.backend.cli create-user NAME --role engineer
    python -m app.backend.cli reset-password NAME
    python -m app.backend.cli backup                copy the database to <data>/backups
    python -m app.backend.cli restore FILE          restore a backup (stop the service first)
"""
from __future__ import annotations

import argparse
import logging
import secrets
import sys
from pathlib import Path


def _init():
    from . import db
    from .db import session_scope
    from .security import ensure_initial_admin
    from .services.runs import default_site
    from .settings import get_settings
    s = get_settings()
    s.ensure_dirs()
    db.configure(s.database_url)
    with session_scope() as d:
        default_site(d)
        pw = ensure_initial_admin(d)
    if pw:
        print(f"First start: user 'admin', password in {s.data_dir / 'initial_admin_password.txt'}")
    return s


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="flowtwin", description="FlowTwin GGS flow validation & digital twin")
    sub = p.add_subparsers(dest="cmd", required=True)
    sv = sub.add_parser("serve")
    sv.add_argument("--host")
    sv.add_argument("--port", type=int)
    sub.add_parser("seed-sample")
    r = sub.add_parser("run")
    r.add_argument("--kind", default="twin", choices=["validation", "twin"])
    r.add_argument("--start")
    r.add_argument("--end")
    cu = sub.add_parser("create-user")
    cu.add_argument("username")
    cu.add_argument("--role", default="viewer", choices=["viewer", "engineer", "admin"])
    cu.add_argument("--full-name", default="")
    rp = sub.add_parser("reset-password")
    rp.add_argument("username")
    sub.add_parser("backup")
    rs = sub.add_parser("restore")
    rs.add_argument("file")
    p.add_argument("--data-dir", help="data folder (overrides FLOWTWIN_DATA_DIR)")
    a = p.parse_args(argv)
    if a.data_dir:
        import os
        os.environ["FLOWTWIN_DATA_DIR"] = str(Path(a.data_dir).resolve())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if a.cmd == "serve":
        import uvicorn
        from .settings import get_settings
        s = get_settings()
        s.ensure_dirs()
        from .main import create_app
        kw = {}
        if s.tls_cert and s.tls_key:
            kw = dict(ssl_certfile=str(s.tls_cert), ssl_keyfile=str(s.tls_key))
        host, port = a.host or s.host, a.port or s.port
        print(f"FlowTwin on {'https' if kw else 'http'}://{host}:{port}  (data: {s.data_dir})")
        uvicorn.run(create_app(s), host=host, port=port, log_level="info", **kw)
        return 0

    if a.cmd == "restore":
        from .services.scheduler import restore_backup
        from .settings import get_settings
        try:
            src, dst, kept = restore_backup(a.file, get_settings().data_dir)
        except FileNotFoundError as e:
            print(e)
            return 1
        if kept:
            print(f"current database kept as {kept}")
        print(f"restored {src.name} -> {dst}. Start the service again.")
        return 0

    s = _init()
    from .db import session_scope
    from .models import User
    from .security import hash_password
    from .services.runs import default_site

    if a.cmd == "seed-sample":
        from . import engine_adapter as A
        from .services.site import seed_sample
        with session_scope() as d:
            site = default_site(d)
            if A.current_network(d, site.id) is not None:
                print("A network model is already loaded; nothing done.")
                return 1
            out = seed_sample(d, site.id, "cli")
        print(f"Sample GGS loaded: {out}")
        return 0
    if a.cmd == "run":
        from .services import jobs
        from .services.runs import queue_run
        jobs.set_manager(jobs.JobManager(1))
        with session_scope() as d:
            site = default_site(d)
            run = queue_run(d, site.id, a.kind, a.start, a.end, "cli")
            rid, jid = run.id, run.job_id
        jobs.manager().wait(jid)
        from .models import Run
        with session_scope() as d:
            r = d.get(Run, rid)
            print(f"run {rid}: {r.status} {r.period_start}..{r.period_end} in {r.runtime_s or 0:.1f}s")
            print(r.summary if r.status == "done" else r.log)
        return 0 if r.status == "done" else 1
    if a.cmd in ("create-user", "reset-password"):
        from sqlalchemy import select
        pw = secrets.token_urlsafe(9)
        with session_scope() as d:
            u = d.scalar(select(User).where(User.username == a.username))
            if a.cmd == "create-user":
                if u is not None:
                    print("user exists")
                    return 1
                d.add(User(username=a.username, role=a.role, full_name=a.full_name, password_hash=hash_password(pw),
                           must_change_password=True))
            else:
                if u is None:
                    print("no such user")
                    return 1
                u.password_hash, u.must_change_password, u.active = hash_password(pw), True, True
        print(f"{a.username}: temporary password {pw} (must be changed at first sign-in)")
        return 0
    if a.cmd == "backup":
        from .services.scheduler import backup_now
        print(f"backup written: {backup_now()} ({s.backups_dir})")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
