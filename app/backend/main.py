"""FastAPI application factory. Serves the API under /api/v1 and the built React app at /."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .db import session_scope
from .security import ensure_initial_admin
from .services import jobs
from .services.runs import default_site
from .settings import Settings, get_settings, use_settings

STATIC = Path(__file__).resolve().parent / "static"
API = "/api/v1"
log = logging.getLogger("flowtwin")

APP_CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
           "font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
# generated HTML reports carry their own inline styles and scripts, and need nothing external
REPORT_CSP = ("default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
              "frame-ancestors 'self'")


def create_app(settings: Optional[Settings] = None, start_scheduler: Optional[bool] = None) -> FastAPI:
    s = settings or get_settings()
    use_settings(s)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        s.ensure_dirs()
        db.configure(s.database_url)
        with session_scope() as d:
            default_site(d)
            pw = ensure_initial_admin(d)
        if pw:
            log.warning("First start: user 'admin' created; password in %s",
                        s.data_dir / "initial_admin_password.txt")
        jobs.recover_interrupted()
        jobs.set_manager(jobs.JobManager(s.workers))
        run_sched = s.scheduler_enabled if start_scheduler is None else start_scheduler
        if run_sched:
            from .services import scheduler
            scheduler.start()
        yield
        if run_sched:
            from .services import scheduler
            scheduler.stop()
        jobs.manager().shutdown()

    app = FastAPI(title="FlowTwin", version="1.0.0", lifespan=lifespan, docs_url=f"{API}/docs",
                  openapi_url=f"{API}/openapi.json", redoc_url=None)

    from .api import admin, assets, auth, core, data, ops
    for r in (auth.router, core.router, assets.router, ops.router, data.router, admin.router):
        app.include_router(r, prefix=API)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        path = request.url.path
        is_report = path.startswith(f"{API}/runs/") and path.endswith(".html")
        if not path.startswith(f"{API}/docs"):
            resp.headers["Content-Security-Policy"] = REPORT_CSP if is_report else APP_CSP
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["X-Frame-Options"] = "SAMEORIGIN" if is_report else "DENY"
        if path.startswith(API):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error on %s", request.url.path)
        return JSONResponse({"detail": f"Internal error ({type(exc).__name__}). See the server log."}, 500)

    @app.get(f"{API}/health")
    def health():
        return {"ok": True}

    if (STATIC / "index.html").exists():
        if (STATIC / "assets").exists():
            app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

        @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                return JSONResponse({"detail": "Not found"}, 404)
            f = (STATIC / path).resolve()
            if path and f.is_file() and STATIC in f.parents:
                return FileResponse(f)
            return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})
    else:
        @app.get("/", include_in_schema=False)
        def no_frontend():
            return JSONResponse({"detail": "Frontend not built. Run: python scripts/dev.py frontend",
                                 "api_docs": f"{API}/docs"})
    return app
