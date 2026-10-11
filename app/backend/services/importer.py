"""Import the CSV templates (docs/DATA_DICTIONARY.md) or Excel workbooks with one sheet per template.

Flow: parse -> (optional column mapping) -> validate -> store as a *preview* batch -> commit.
Values are stored as the original cell text, so nothing is rounded or re-typed on the way in.
"""
from __future__ import annotations

import io
import math
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from flowval.io import SCHEMAS, TEXT_COLUMNS

from .. import engine_adapter as A
from ..models import InputBatch, InputRow, Setting

DATE_COLS = ("date", "test_date", "datetime")
ID_CHECKS = {"well_id": "wells", "meter_id": "meters", "separator_id": "separators", "tank_id": "tanks"}


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        if math.isnan(v):
            return ""
        return repr(v)
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d %H:%M") if (v.hour or v.minute) else v.strftime("%Y-%m-%d")
    return str(v).strip()


def detect_dataset(name: str, columns: List[str]) -> Optional[str]:
    stem = re.sub(r"[^a-z_]", "_", Path(name).stem.lower())
    for ds in sorted(SCHEMAS, key=len, reverse=True):     # well_test_readings before well_tests
        if stem == ds or stem.startswith(ds + "_") or stem.startswith(ds):
            return ds
    cols = set(columns)
    best, score = None, 0.0
    for ds, sc in SCHEMAS.items():
        req = set(sc["required"])
        if req <= cols:
            s = len(cols & (req | set(sc["optional"]))) / len(cols | req)
            if s > score:
                best, score = ds, s
    return best


def parse_file(filename: str, content: bytes) -> List[Tuple[str, Optional[str], pd.DataFrame]]:
    """-> [(source label, detected dataset or None, text frame)]"""
    out = []
    if filename.lower().endswith((".xlsx", ".xlsm")):
        sheets = pd.read_excel(io.BytesIO(content), sheet_name=None, dtype=object)
        for sheet, df in sheets.items():
            df = df.dropna(how="all")
            if df.empty:
                continue
            df.columns = [str(c).strip() for c in df.columns]
            txt = df.apply(lambda col: col.map(_cell))
            out.append((f"{filename}:{sheet}", detect_dataset(sheet, list(txt.columns)), txt))
    else:
        df = pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False, encoding="utf-8-sig")
        df.columns = [str(c).strip() for c in df.columns]
        out.append((filename, detect_dataset(filename, list(df.columns)), df.apply(lambda c: c.str.strip())))
    return out


def apply_mapping(df: pd.DataFrame, mapping: Optional[Dict[str, str]]) -> pd.DataFrame:
    """mapping: source column -> template column."""
    if not mapping:
        return df
    return df.rename(columns={k: v for k, v in mapping.items() if v})


def natural_key(ds: str, row: dict) -> str:
    return "|".join(row.get(c, "") for c in A.NATURAL_KEYS[ds])


def _row_date(ds: str, row: dict) -> Optional[str]:
    col = A.DATE_COLUMN.get(ds, "date")
    v = row.get(col) if col else None
    return v[:10] if v else None


def validate(db: Session, site_id: int, parts: List[Tuple[str, str, pd.DataFrame]]) -> dict:
    """parts: [(label, dataset, text frame)] -> report with errors (block commit) and warnings."""
    errors, warnings, stats = [], [], {}
    nv = A.current_network(db, site_id)
    known = {}
    if nv is not None:
        y = A.network_yaml(nv)
        known = {"wells": {w["id"] for w in y.get("wells", [])},
                 "separators": {s["id"] for s in y.get("separators", [])},
                 "meters": {m["id"] for m in y.get("meters", [])},
                 "tanks": {t["id"] for t in y.get("tanks", [])}}
    existing_keys = {}
    for label, ds, df in parts:
        if ds is None:
            errors.append(f"{label}: cannot tell which template this is (rename the file/sheet to a template "
                          f"name such as well_daily.csv, or map the columns)")
            continue
        sc = SCHEMAS[ds]
        if df.empty:
            errors.append(f"{label}: no rows")
            continue
        miss = [c for c in sc["required"] if c not in df.columns]
        if miss:
            errors.append(f"{label} ({ds}): missing required columns {miss}")
            continue
        extra = [c for c in df.columns if c not in sc["required"] + sc["optional"]]
        if extra:
            warnings.append(f"{label} ({ds}): columns not used by the engine: {extra}")
        for c in df.columns:
            if c in DATE_COLS:
                bad = df[c][(df[c] != "") & pd.to_datetime(df[c], errors="coerce").isna()]
                if len(bad):
                    errors.append(f"{label} ({ds}): {len(bad)} unreadable {c} value(s), first at row "
                                  f"{bad.index[0] + 2}: '{bad.iloc[0]}'")
                if c in sc["required"] and (df[c] == "").any():
                    errors.append(f"{label} ({ds}): empty {c} in {(df[c] == '').sum()} row(s)")
            elif c not in TEXT_COLUMNS and c in sc["required"] + sc["optional"]:
                bad = df[c][(df[c] != "") & pd.to_numeric(df[c], errors="coerce").isna()]
                if len(bad):
                    errors.append(f"{label} ({ds}): {len(bad)} non-numeric {c} value(s), first at row "
                                  f"{bad.index[0] + 2}: '{bad.iloc[0]}'")
        for c, kind in ID_CHECKS.items():
            if c in df.columns and known.get(kind):
                unk = sorted(set(df[c]) - known[kind] - {""})
                if unk:
                    warnings.append(f"{label} ({ds}): {c} not in the network model: {unk[:8]}"
                                    + (" ..." if len(unk) > 8 else ""))
        keys = df.apply(lambda r: natural_key(ds, r.to_dict()), axis=1)
        dup = keys[keys.duplicated()]
        if len(dup):
            warnings.append(f"{label} ({ds}): {len(dup)} duplicate key(s) in the file, last one kept "
                            f"(e.g. {dup.iloc[0]})")
        if ds not in existing_keys:
            existing_keys[ds] = set(db.scalars(
                select(InputRow.natural_key).join(InputBatch, InputBatch.id == InputRow.batch_id)
                .where(InputBatch.site_id == site_id, InputBatch.status == "committed", InputRow.dataset == ds)))
        uk = set(keys)
        dates = [d for d in (_row_date(ds, r) for r in df.to_dict("records")) if d]
        st = stats.setdefault(ds, dict(rows=0, new=0, replaced=0, first=None, last=None, sources=[]))
        st["rows"] += len(df)
        st["new"] += len(uk - existing_keys[ds])
        st["replaced"] += len(uk & existing_keys[ds])
        st["sources"].append(label)
        if dates:
            st["first"] = min(filter(None, [st["first"], min(dates)]))
            st["last"] = max(filter(None, [st["last"], max(dates)]))
    return dict(errors=errors, warnings=warnings, datasets=stats)


def create_batch(db: Session, site_id: int, source: str, filename: str, user: str,
                 parts: List[Tuple[str, str, pd.DataFrame]], report: dict) -> InputBatch:
    b = InputBatch(site_id=site_id, source=source, filename=filename, uploaded_by=user,
                   status="rejected" if report["errors"] else "preview",
                   datasets=report["datasets"], validation_report=dict(errors=report["errors"],
                                                                        warnings=report["warnings"]))
    db.add(b)
    db.flush()
    n = 0
    if not report["errors"]:
        rows = []
        for _, ds, df in parts:
            cols = [c for c in df.columns if c]
            for r in df[cols].to_dict("records"):
                rows.append(dict(batch_id=b.id, dataset=ds, natural_key=natural_key(ds, r),
                                 date=_row_date(ds, r), payload=r))
        if rows:
            db.execute(InputRow.__table__.insert(), rows)
        n = len(rows)
    b.rows = n
    return b


def saved_mappings(db: Session) -> dict:
    s = db.get(Setting, "column_mappings")
    return s.value if s else {}


def save_mapping(db: Session, name: str, mapping: Dict[str, str]) -> None:
    s = db.get(Setting, "column_mappings")
    cur = dict(s.value) if s else {}
    cur[name] = mapping
    if s:
        s.value = cur
    else:
        db.add(Setting(key="column_mappings", value=cur))


def import_files(db: Session, site_id: int, files: List[Tuple[str, bytes]], user: str, source: str = "upload",
                 dataset_hint: Optional[Dict[str, str]] = None, mapping: Optional[Dict[str, Dict[str, str]]] = None
                 ) -> InputBatch:
    """files: [(filename, bytes)]; dataset_hint: label -> dataset; mapping: label -> {src: target}."""
    parts, unreadable = [], []
    for fn, content in files:
        try:
            parsed = parse_file(fn, content)
        except Exception as e:  # noqa: BLE001 - report unreadable files, do not crash
            unreadable.append(f"{fn}: unreadable ({type(e).__name__}: {e})")
            continue
        for label, ds, df in parsed:
            if mapping and label in mapping:
                df = apply_mapping(df, mapping[label])
                ds = detect_dataset(label, list(df.columns))
            if dataset_hint and dataset_hint.get(label) in SCHEMAS:
                ds = dataset_hint[label]
            parts.append((label, ds, df))
    report = validate(db, site_id, parts)
    report["errors"] = unreadable + report["errors"]
    if not parts and not unreadable:
        report["errors"].append("No data found in the upload")
    return create_batch(db, site_id, source, ", ".join(fn for fn, _ in files), user,
                        [p for p in parts if p[1] in SCHEMAS], report)


def preview_rows(db: Session, batch_id: int, limit: int = 5) -> Dict[str, List[dict]]:
    out: Dict[str, List[dict]] = {}
    for r in db.scalars(select(InputRow).where(InputRow.batch_id == batch_id).order_by(InputRow.id)):
        lst = out.setdefault(r.dataset, [])
        if len(lst) < limit:
            lst.append(r.payload)
    return out
