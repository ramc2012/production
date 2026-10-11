"""Monthly well allocation: theoretical (last test x hours), proportional (API MPMS 20.1 AF) and
DVR-reconciled, with uncertainty; close/lock a month; Excel export with the engine's numbers.

Monthly uncertainty: the engine gives a daily standard deviation per well. How strongly the daily
errors are correlated is not modelled (well-test errors repeat until the next test), so the
month is reported with two bounds: days independent (sqrt of summed variances, lower bound) and
days fully correlated (sum of daily sd, upper bound). Both are U95 = 2 sd.
"""
from __future__ import annotations

import calendar
import io
import math
from typing import Optional

import pandas as pd
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AllocationClose, Run, WellDailyResult
from ..security import audit

METHOD_NOTE = ("Monthly U95 bounds: 'indep' assumes independent daily errors (lower bound); 'corr' assumes fully "
               "correlated daily errors, e.g. one stale test (upper bound).")


def month_days(month: str):
    y, m = map(int, month.split("-"))
    return f"{month}-01", f"{month}-{calendar.monthrange(y, m)[1]:02d}"


def pick_run(db: Session, site_id: int, month: str) -> Optional[Run]:
    closed = db.scalar(select(AllocationClose).where(AllocationClose.site_id == site_id,
                                                     AllocationClose.month == month))
    if closed:
        return db.get(Run, closed.run_id)
    first, last = month_days(month)
    best, best_cov = None, 0
    for r in db.scalars(select(Run).where(Run.site_id == site_id, Run.status == "done").order_by(Run.id.desc())):
        if not r.period_start or r.period_end < first or r.period_start > last:
            continue
        lo, hi = max(r.period_start, first), min(r.period_end, last)
        cov = (pd.Timestamp(hi) - pd.Timestamp(lo)).days + 1
        if cov > best_cov:
            best, best_cov = r, cov
    return best


def _daily(db: Session, run_id: int, month: str) -> pd.DataFrame:
    first, last = month_days(month)
    rows = db.scalars(select(WellDailyResult).where(WellDailyResult.run_id == run_id,
                                                    WellDailyResult.date >= first, WellDailyResult.date <= last)
                      .order_by(WellDailyResult.date, WellDailyResult.well_id)).all()
    return pd.DataFrame([dict(date=r.date, well_id=r.well_id, hours_on=r.hours_on,
                              theo_oil_m3=r.theo_oil_m3, theo_oil_sd_m3=r.theo_oil_sd_m3,
                              theo_water_m3=r.data.get("theo_water"), theo_gas_sm3=r.data.get("theo_gas"),
                              prop_oil_m3=r.prop_oil_m3, prop_water_m3=r.data.get("prop_water"),
                              prop_gas_sm3=r.data.get("prop_gas"),
                              dvr_oil_m3=r.dvr_oil_m3, dvr_oil_sd_m3=r.dvr_oil_sd_m3,
                              dvr_water_m3=r.dvr_water_m3, dvr_water_sd_m3=r.dvr_water_sd_m3,
                              dvr_gas_sm3=r.dvr_gas_sm3, dvr_gas_sd_sm3=r.dvr_gas_sd_sm3,
                              test_id=r.data.get("test_id"), test_status=r.data.get("test_status"))
                         for r in rows])


def _monthly(d: pd.DataFrame) -> pd.DataFrame:
    if d.empty:
        return d
    rows = []
    for wid, g in d.groupby("well_id"):
        r = dict(well_id=wid, days=int(g["date"].nunique()), hours_on=float(g["hours_on"].fillna(0).sum()))
        for c in ("theo_oil_m3", "theo_water_m3", "theo_gas_sm3", "prop_oil_m3", "prop_water_m3", "prop_gas_sm3",
                  "dvr_oil_m3", "dvr_water_m3", "dvr_gas_sm3"):
            r[c] = float(pd.to_numeric(g[c], errors="coerce").fillna(0).sum())
        for c, sdc in (("theo_oil", "theo_oil_sd_m3"), ("dvr_oil", "dvr_oil_sd_m3"),
                       ("dvr_water", "dvr_water_sd_m3"), ("dvr_gas", "dvr_gas_sd_sm3")):
            sd = pd.to_numeric(g[sdc], errors="coerce").fillna(0)
            unit = "sm3" if "gas" in c else "m3"
            r[f"{c}_u95_indep_{unit}"] = 2 * math.sqrt(float((sd ** 2).sum()))
            r[f"{c}_u95_corr_{unit}"] = 2 * float(sd.sum())
        rows.append(r)
    return pd.DataFrame(rows)


def month_view(db: Session, site_id: int, month: str) -> dict:
    run = pick_run(db, site_id, month)
    closed = db.scalar(select(AllocationClose).where(AllocationClose.site_id == site_id,
                                                     AllocationClose.month == month))
    if run is None:
        return dict(month=month, run=None, closed=None, wells=[], daily=[], totals={}, note=METHOD_NOTE)
    d = _daily(db, run.id, month)
    m = _monthly(d)
    totals = {}
    if len(m):
        for c in m.columns:
            if c.endswith(("_m3", "_sm3")) and "u95" not in c:
                totals[c] = float(m[c].sum())
        for c in ("dvr_oil", "dvr_water", "dvr_gas", "theo_oil"):
            unit = "sm3" if "gas" in c else "m3"
            totals[f"{c}_u95_indep_{unit}"] = math.sqrt(float((m[f"{c}_u95_indep_{unit}"] ** 2).sum()))
    first, last = month_days(month)
    days_in_run = sorted(d["date"].unique()) if len(d) else []
    return dict(month=month,
                run=dict(id=run.id, kind=run.kind, period_start=run.period_start, period_end=run.period_end,
                         engine_version=run.engine_version, config_hash=run.config_hash),
                coverage=dict(days=len(days_in_run), first=days_in_run[0] if days_in_run else None,
                              last=days_in_run[-1] if days_in_run else None, month_first=first, month_last=last),
                closed=None if closed is None else dict(by=closed.closed_by, at=closed.closed_at.isoformat(),
                                                        run_id=closed.run_id, totals=closed.totals),
                wells=m.to_dict("records") if len(m) else [], daily=d.where(d.notna(), None).to_dict("records"),
                totals=totals, note=METHOD_NOTE)


def close_month(db: Session, site_id: int, month: str, run_id: Optional[int], user: str) -> AllocationClose:
    if db.scalar(select(AllocationClose).where(AllocationClose.site_id == site_id, AllocationClose.month == month)):
        raise HTTPException(409, f"{month} is already closed")
    run = db.get(Run, run_id) if run_id else pick_run(db, site_id, month)
    if run is None or run.status != "done":
        raise HTTPException(400, f"No finished run covers {month}")
    m = _monthly(_daily(db, run.id, month))
    if m.empty:
        raise HTTPException(400, f"Run {run.id} has no allocation for {month}")
    totals = {c: float(m[c].sum()) for c in ("theo_oil_m3", "prop_oil_m3", "dvr_oil_m3", "dvr_water_m3", "dvr_gas_sm3")}
    totals["days"] = int(m["days"].max())
    c = AllocationClose(site_id=site_id, month=month, run_id=run.id, totals=totals, closed_by=user)
    db.add(c)
    audit(db, user, "allocation.close", f"month:{month}", None, {"run_id": run.id, **totals})
    return c


def reopen_month(db: Session, site_id: int, month: str, user: str, reason: str) -> None:
    c = db.scalar(select(AllocationClose).where(AllocationClose.site_id == site_id, AllocationClose.month == month))
    if c is None:
        raise HTTPException(404, f"{month} is not closed")
    if not reason.strip():
        raise HTTPException(400, "A reason is required to re-open a closed month")
    audit(db, user, "allocation.reopen", f"month:{month}", {"run_id": c.run_id, **(c.totals or {})},
          {"reason": reason})
    db.delete(c)


def export_xlsx(db: Session, site_id: int, month: str) -> bytes:
    v = month_view(db, site_id, month)
    if v["run"] is None:
        raise HTTPException(404, f"No run covers {month}")
    buf = io.BytesIO()
    meta = pd.DataFrame([
        ("Month", month), ("Status", "CLOSED" if v["closed"] else "open (not closed)"),
        ("Closed by", (v["closed"] or {}).get("by", "")), ("Closed at (UTC)", (v["closed"] or {}).get("at", "")),
        ("Run", v["run"]["id"]), ("Run period", f"{v['run']['period_start']} to {v['run']['period_end']}"),
        ("Engine version", v["run"]["engine_version"]), ("Config hash", v["run"]["config_hash"]),
        ("Days in month covered", v["coverage"]["days"]),
        ("Units", "Oil and water m3 at 15 °C; gas Sm3 at 15 °C / 101.325 kPa"),
        ("Methods", "theo = last valid test x hours on; prop = theo x allocation factor (API MPMS 20.1); "
                    "dvr = data validation & reconciliation (WLS) estimate"),
        ("Uncertainty", METHOD_NOTE),
    ], columns=["item", "value"])
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        meta.to_excel(xw, sheet_name="About", index=False)
        pd.DataFrame(v["wells"]).to_excel(xw, sheet_name="Monthly by well", index=False)
        pd.DataFrame(v["daily"]).to_excel(xw, sheet_name="Daily by well", index=False)
        pd.DataFrame([v["totals"]]).to_excel(xw, sheet_name="Installation totals", index=False)
    return buf.getvalue()
