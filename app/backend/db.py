"""Database engine and sessions. SQLite in WAL mode by default; PostgreSQL works through db_url."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


SCHEMA_VERSION = 1   # bump with an upgrade step in migrate() when models change


class _State:
    engine: Optional[Engine] = None
    factory: Optional[sessionmaker] = None


def configure(url: str) -> Engine:
    """Create the engine and the schema. Called once at start-up (and by tests)."""
    from . import models  # noqa: F401  (register tables)

    kw = {}
    if url.startswith("sqlite"):
        kw["connect_args"] = {"check_same_thread": False, "timeout": 30}
    eng = create_engine(url, future=True, **kw)
    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _pragmas(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()
    Base.metadata.create_all(eng)
    migrate(eng)
    _State.engine = eng
    _State.factory = sessionmaker(bind=eng, expire_on_commit=False, future=True)
    return eng


def migrate(eng: Engine) -> None:
    """Minimal forward-only schema versioning (no Alembic needed for one SQLite file).
    New tables appear through create_all; column changes get an explicit step here."""
    with eng.begin() as c:
        c.execute(text("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"))
        v = c.execute(text("SELECT MAX(version) FROM schema_version")).scalar()
        if v is None:
            c.execute(text("INSERT INTO schema_version (version) VALUES (:v)"), {"v": SCHEMA_VERSION})
        # elif v < 2: c.execute(text("ALTER TABLE ... ADD COLUMN ...")); insert version 2


def engine() -> Engine:
    if _State.engine is None:
        raise RuntimeError("database not configured")
    return _State.engine


def new_session() -> Session:
    if _State.factory is None:
        raise RuntimeError("database not configured")
    return _State.factory()


@contextmanager
def session_scope() -> Iterator[Session]:
    s = new_session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    s = new_session()
    try:
        yield s
    finally:
        s.close()
