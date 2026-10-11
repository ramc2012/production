"""Data in: upload with validation and preview, commit/discard, batches, coverage, templates."""
from __future__ import annotations

import json
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from flowval.io import SCHEMAS

from ..db import get_db
from ..models import Finding, InputBatch, InputRow, Site, User
from ..security import audit, engineer, viewer
from ..services import importer
from .core import _f
from .deps import resolve_run, run_meta, site

router = APIRouter(prefix="/data", tags=["data"])
MAX_UPLOAD = 50 * 1024 * 1024


def _batch(b: InputBatch) -> dict:
    return dict(id=b.id, source=b.source, filename=b.filename, uploaded_by=b.uploaded_by, at=b.at.isoformat(),
                rows=b.rows, status=b.status, datasets=b.datasets, report=b.validation_report)


@router.get("/templates")
def templates(_: User = Depends(viewer)):
    return {k: dict(required=v["required"], optional=v["optional"]) for k, v in SCHEMAS.items()}


@router.get("/templates/{dataset}.csv", response_class=PlainTextResponse)
def template_csv(dataset: str, _: User = Depends(viewer)):
    if dataset not in SCHEMAS:
        raise HTTPException(404, "Unknown template")
    return ",".join(SCHEMAS[dataset]["required"] + SCHEMAS[dataset]["optional"]) + "\n"


@router.post("/upload")
async def upload(files: List[UploadFile] = File(...), hints: Optional[str] = Form(None),
                 mapping: Optional[str] = Form(None), save_mapping_as: Optional[str] = Form(None),
                 db: Session = Depends(get_db), s: Site = Depends(site), user: User = Depends(engineer)):
    """Validate and stage the files as a *preview* batch. Nothing is used until it is committed."""
    got = []
    for f in files:
        content = await f.read()
        if len(content) > MAX_UPLOAD:
            raise HTTPException(413, f"{f.filename} is larger than 50 MB")
        got.append((f.filename or "upload.csv", content))
    try:
        hint_d = json.loads(hints) if hints else None
        map_d = json.loads(mapping) if mapping else None
    except json.JSONDecodeError:
        raise HTTPException(400, "hints/mapping must be JSON")
    b = importer.import_files(db, s.id, got, user.username, "upload", hint_d, map_d)
    if save_mapping_as and map_d:
        for m in map_d.values():
            importer.save_mapping(db, save_mapping_as, m)
    audit(db, user.username, "data.upload", f"batch:{b.id}", None,
          dict(files=[g[0] for g in got], status=b.status, rows=b.rows))
    db.commit()
    return dict(_batch(b), preview=importer.preview_rows(db, b.id))


@router.get("/mappings")
def mappings(db: Session = Depends(get_db), _: User = Depends(viewer)):
    return importer.saved_mappings(db)


@router.post("/inspect")
async def inspect(file: UploadFile = File(...), _: User = Depends(engineer)):
    """Columns and detected template per file/sheet, for the column-mapping step."""
    content = await file.read()
    try:
        parts = importer.parse_file(file.filename or "upload.csv", content)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Cannot read {file.filename}: {e}")
    return [dict(label=label, dataset=ds, columns=list(df.columns), rows=len(df),
                 sample=df.head(3).to_dict("records")) for label, ds, df in parts]


@router.post("/batches/{batch_id}/commit")
def commit(batch_id: int, db: Session = Depends(get_db), s: Site = Depends(site), user: User = Depends(engineer)):
    b = db.get(InputBatch, batch_id)
    if b is None or b.site_id != s.id:
        raise HTTPException(404, "Batch not found")
    if b.status != "preview":
        raise HTTPException(409, f"Batch is {b.status}; only a preview batch can be committed")
    b.status = "committed"
    audit(db, user.username, "data.commit", f"batch:{b.id}", dict(status="preview"),
          dict(status="committed", rows=b.rows, datasets=b.datasets))
    db.commit()
    return _batch(b)


@router.post("/batches/{batch_id}/discard")
def discard(batch_id: int, db: Session = Depends(get_db), s: Site = Depends(site), user: User = Depends(engineer)):
    b = db.get(InputBatch, batch_id)
    if b is None or b.site_id != s.id:
        raise HTTPException(404, "Batch not found")
    if b.status not in ("preview", "rejected"):
        raise HTTPException(409, "Committed data is kept for traceability; upload a correction instead")
    b.status = "discarded"
    audit(db, user.username, "data.discard", f"batch:{b.id}")
    db.commit()
    return _batch(b)


@router.get("/batches")
def batches(db: Session = Depends(get_db), s: Site = Depends(site), _: User = Depends(viewer)):
    return [_batch(b) for b in db.scalars(select(InputBatch).where(InputBatch.site_id == s.id)
                                          .order_by(InputBatch.id.desc()).limit(200))]


@router.get("/batches/{batch_id}")
def batch(batch_id: int, db: Session = Depends(get_db), _: User = Depends(viewer)):
    b = db.get(InputBatch, batch_id)
    if b is None:
        raise HTTPException(404, "Batch not found")
    return dict(_batch(b), preview=importer.preview_rows(db, b.id, 20))


@router.get("/coverage")
def coverage(db: Session = Depends(get_db), s: Site = Depends(site), _: User = Depends(viewer)):
    """Committed rows per template, with date range (newest batch wins per key)."""
    q = (select(InputRow.dataset, func.count(func.distinct(InputRow.natural_key)), func.min(InputRow.date),
                func.max(InputRow.date))
         .join(InputBatch, InputBatch.id == InputRow.batch_id)
         .where(InputBatch.site_id == s.id, InputBatch.status == "committed").group_by(InputRow.dataset))
    have = {ds: dict(rows=int(n), first=a, last=b) for ds, n, a, b in db.execute(q)}
    return [dict(dataset=ds, required=ds not in ("well_test_readings", "tank_drains", "separator_daily",
                                                 "reservoir_surveys"),
                 **have.get(ds, dict(rows=0, first=None, last=None))) for ds in SCHEMAS]


@router.get("/quality")
def quality(run_id: Optional[int] = None, date: Optional[str] = None, db: Session = Depends(get_db),
            s: Site = Depends(site), _: User = Depends(viewer)):
    run = resolve_run(db, s.id, run_id)
    q = select(Finding).where(Finding.run_id == run.id, Finding.layer == "data_quality")
    if date:
        q = q.where(Finding.date == date)
    rows = db.scalars(q.order_by(Finding.date.desc())).all()
    by_check: dict = {}
    for f in rows:
        by_check[f.check] = by_check.get(f.check, 0) + 1
    return dict(run=run_meta(run), by_check=by_check, findings=[_f(f) for f in rows])


class MappingBody(BaseModel):
    name: str
    mapping: dict


@router.post("/mappings")
def add_mapping(body: MappingBody, db: Session = Depends(get_db), user: User = Depends(engineer)):
    importer.save_mapping(db, body.name, body.mapping)
    audit(db, user.username, "data.mapping_save", f"mapping:{body.name}", None, body.mapping)
    db.commit()
    return importer.saved_mappings(db)
