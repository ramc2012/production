"""SQLAlchemy models (ARCHITECTURE.md, Data model). Units live in field names, as in the engine.

Design notes
- Inputs keep the *original cell text* of each CSV/Excel row (``InputRow.payload``). For the same
  natural key a newer batch overrides the columns it carries (column-wise merge). The engine
  adapter re-parses it exactly like the CLI, so a run from the database equals a run from files.
- Every result row carries ``run_id``. Large engine tables are kept whole per run in
  ``RunTable`` (JSON records, every column including uncertainties); the tables the app
  queries and joins on (findings, well daily results) are typed.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------- master data
class Site(Base):
    __tablename__ = "site"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    timezone: Mapped[str] = mapped_column(String(60), default="Asia/Kolkata")
    day_start: Mapped[str] = mapped_column(String(5), default="06:00")


class NetworkVersion(Base):
    """network.yaml + well master (well_static) + strapping tables, versioned as one unit."""
    __tablename__ = "network_version"
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("site.id"))
    yaml_text: Mapped[str] = mapped_column(Text)
    well_static_csv: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    strapping_csv: Mapped[dict] = mapped_column(JSON, default=dict)      # tank id -> CSV text
    hash: Mapped[str] = mapped_column(String(64))
    comment: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(80), default="system")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


class ConfigVersion(Base):
    """Site tolerance / uncertainty / twin YAML (deep-merged over flowval.config.DEFAULTS)."""
    __tablename__ = "config_version"
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("site.id"))
    yaml_text: Mapped[str] = mapped_column(Text)
    hash: Mapped[str] = mapped_column(String(64))
    comment: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(80), default="system")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


class TagMap(Base):
    """v2 live connectors: source tag -> asset attribute (read-only)."""
    __tablename__ = "tag_map"
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("site.id"))
    source: Mapped[str] = mapped_column(String(40))
    tag: Mapped[str] = mapped_column(String(200))
    object_type: Mapped[str] = mapped_column(String(40))
    object_id: Mapped[str] = mapped_column(String(80))
    attribute: Mapped[str] = mapped_column(String(80))
    unit: Mapped[str] = mapped_column(String(20), default="")
    scale: Mapped[float] = mapped_column(Float, default=1.0)
    offset: Mapped[float] = mapped_column(Float, default=0.0)
    aggregation: Mapped[str] = mapped_column(String(20), default="mean")


# ---------------------------------------------------------------------------- inputs
class InputBatch(Base):
    __tablename__ = "input_batch"
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("site.id"))
    source: Mapped[str] = mapped_column(String(40))          # upload | dropzone | seed | api
    filename: Mapped[str] = mapped_column(Text, default="")
    uploaded_by: Mapped[str] = mapped_column(String(80), default="system")
    at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    rows: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="preview")   # preview | committed | rejected | discarded
    datasets: Mapped[dict] = mapped_column(JSON, default=dict)          # dataset -> {rows, new, replaced, date range}
    validation_report: Mapped[dict] = mapped_column(JSON, default=dict)  # {errors: [], warnings: []}


class InputRow(Base):
    __tablename__ = "input_row"
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("input_batch.id", ondelete="CASCADE"), index=True)
    dataset: Mapped[str] = mapped_column(String(40))
    natural_key: Mapped[str] = mapped_column(String(200))
    date: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON)               # column -> original cell text
    __table_args__ = (Index("ix_input_row_ds_key", "dataset", "natural_key"),
                      Index("ix_input_row_ds_date", "dataset", "date"))


# ---------------------------------------------------------------------------- runs & results
class Run(Base):
    __tablename__ = "run"
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("site.id"))
    kind: Mapped[str] = mapped_column(String(20))             # validation | twin
    period_start: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    period_end: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")   # queued|running|done|failed|cancelled
    trigger: Mapped[str] = mapped_column(String(20), default="manual")  # manual | schedule | dropzone
    engine_version: Mapped[str] = mapped_column(String(20), default="")
    network_version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("network_version.id"), nullable=True)
    config_version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("config_version.id"), nullable=True)
    config_hash: Mapped[str] = mapped_column(String(64), default="")
    input_batches: Mapped[list] = mapped_column(JSON, default=list)
    test_overrides: Mapped[dict] = mapped_column(JSON, default=dict)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    log: Mapped[str] = mapped_column(Text, default="")
    job_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_by: Mapped[str] = mapped_column(String(80), default="system")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    started_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True)
    runtime_s: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class RunTable(Base):
    __tablename__ = "run_table"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("run.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(60))
    rows: Mapped[Any] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint("run_id", "name"),)


class Finding(Base):
    """One engine finding (daily register) of one run; linked to an Issue by (object_id, check)."""
    __tablename__ = "finding"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("run.id", ondelete="CASCADE"), index=True)
    date: Mapped[str] = mapped_column(String(10), index=True)
    layer: Mapped[str] = mapped_column(String(30))
    severity: Mapped[str] = mapped_column(String(10))
    object_type: Mapped[str] = mapped_column(String(30))
    object_id: Mapped[str] = mapped_column(String(80))
    check: Mapped[str] = mapped_column(String(60))
    message: Mapped[str] = mapped_column(Text)
    value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    limit: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    action: Mapped[str] = mapped_column(Text, default="")
    issue_id: Mapped[Optional[int]] = mapped_column(ForeignKey("issue.id"), nullable=True, index=True)


class WellDailyResult(Base):
    """Per well and day: theoretical / proportional / reconciled volumes with uncertainty (sd).
    ``data`` holds every engine column (estimators, VFM, tuning, test status...)."""
    __tablename__ = "well_daily_result"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("run.id", ondelete="CASCADE"), index=True)
    date: Mapped[str] = mapped_column(String(10))
    well_id: Mapped[str] = mapped_column(String(80))
    hours_on: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    theo_oil_m3: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    theo_oil_sd_m3: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    prop_oil_m3: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    dvr_oil_m3: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    dvr_oil_sd_m3: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    dvr_water_m3: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    dvr_water_sd_m3: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    dvr_gas_sm3: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    dvr_gas_sd_sm3: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    data: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (Index("ix_wdr_run_well_date", "run_id", "well_id", "date"),)


# ---------------------------------------------------------------------------- workflows
class Issue(Base):
    """Exception ticket: open -> acknowledged -> resolved (re-opens if the finding returns)."""
    __tablename__ = "issue"
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("site.id"))
    object_type: Mapped[str] = mapped_column(String(30))
    object_id: Mapped[str] = mapped_column(String(80))
    check: Mapped[str] = mapped_column(String(60))
    layer: Mapped[str] = mapped_column(String(30))
    severity: Mapped[str] = mapped_column(String(10))
    state: Mapped[str] = mapped_column(String(20), default="open")
    assignee: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    due: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    first_seen: Mapped[str] = mapped_column(String(10))
    last_seen: Mapped[str] = mapped_column(String(10))
    occurrences: Mapped[int] = mapped_column(Integer, default=0)
    last_message: Mapped[str] = mapped_column(Text, default="")
    last_action: Mapped[str] = mapped_column(Text, default="")
    last_run_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    __table_args__ = (UniqueConstraint("site_id", "object_id", "check"),)


class IssueComment(Base):
    __tablename__ = "issue_comment"
    id: Mapped[int] = mapped_column(primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issue.id", ondelete="CASCADE"), index=True)
    at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    user: Mapped[str] = mapped_column(String(80))
    text: Mapped[str] = mapped_column(Text)


class TestOverride(Base):
    __tablename__ = "test_override"
    __test__ = False   # not a pytest class
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("site.id"))
    test_id: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(20))           # ACCEPTED | PROVISIONAL | REJECTED
    reason: Mapped[str] = mapped_column(Text)
    by: Mapped[str] = mapped_column(String(80))
    at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Recommendation(Base):
    """L4 advice. Decisions are recorded only - nothing is ever written to a plant system."""
    __tablename__ = "recommendation"
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("site.id"))
    run_id: Mapped[int] = mapped_column(ForeignKey("run.id", ondelete="CASCADE"), index=True)
    well_id: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(40))             # setpoint | hold
    current: Mapped[dict] = mapped_column(JSON, default=dict)
    proposed: Mapped[dict] = mapped_column(JSON, default=dict)
    predicted_d_oil_m3d: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    rationale: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="proposed")  # proposed|accepted|declined|implemented|expired
    decided_by: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    decided_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True)
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


class AllocationClose(Base):
    __tablename__ = "allocation_close"
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("site.id"))
    month: Mapped[str] = mapped_column(String(7))             # YYYY-MM
    run_id: Mapped[int] = mapped_column(ForeignKey("run.id"))
    totals: Mapped[dict] = mapped_column(JSON, default=dict)
    closed_by: Mapped[str] = mapped_column(String(80))
    closed_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    __table_args__ = (UniqueConstraint("site_id", "month"),)


class Job(Base):
    __tablename__ = "job"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="queued")   # queued|running|done|failed|cancelled
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    stage: Mapped[str] = mapped_column(String(120), default="")
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str] = mapped_column(String(80), default="system")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    started_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True)


class AutonomyRun(Base):
    """L5 sandbox run against the plant SIMULATOR (never a control system)."""
    __tablename__ = "autonomy_run"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("run.id", ondelete="CASCADE"))
    job_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    mode: Mapped[str] = mapped_column(String(20))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


# ---------------------------------------------------------------------------- users & audit
class User(Base):
    __tablename__ = "user"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    full_name: Mapped[str] = mapped_column(String(200), default="")
    designation: Mapped[str] = mapped_column(String(120), default="")
    role: Mapped[str] = mapped_column(String(20), default="viewer")     # viewer | engineer | admin
    password_hash: Mapped[str] = mapped_column(String(255))
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


class UserSession(Base):
    __tablename__ = "user_session"
    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime)


class AuditLog(Base):
    """Every human action and configuration change. Never deleted."""
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[dt.datetime] = mapped_column(DateTime, default=now, index=True)
    user: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(80))
    object: Mapped[str] = mapped_column(String(200), default="")
    before: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    after: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)


class Setting(Base):
    """Runtime-editable settings (schedule on/off etc.)."""
    __tablename__ = "setting"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
