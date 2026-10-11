"""DB rows <-> flowval engine objects.

Inputs are stored as the original cell text. To give the engine exactly what the CLI gives it,
the adapter rebuilds each dataset as CSV text and parses it with the same ``pd.read_csv`` +
``flowval.io.prepare_inputs`` path. A run from the database therefore equals a run from files.
"""
from __future__ import annotations

import hashlib
import io
import json
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from flowval.config import DEFAULTS, deep_merge
from flowval.io import SCHEMAS, prepare_inputs
from flowval.network import Network, network_from_dict
from flowval.records import frame_records

from .models import (ConfigVersion, Finding, InputBatch, InputRow, NetworkVersion, Run, RunTable,
                     TestOverride, WellDailyResult)

# natural key per dataset: the newest committed batch wins for each key
NATURAL_KEYS: Dict[str, List[str]] = {
    "well_daily": ["date", "well_id"],
    "well_tests": ["test_id"],
    "well_test_readings": ["test_id", "hour"],
    "meter_daily": ["date", "meter_id"],
    "separator_daily": ["date", "separator_id"],
    "tank_gauges": ["datetime", "tank_id"],
    "tank_drains": ["date", "tank_id"],
    "reservoir_surveys": ["date", "well_id"],
    "dispatch_daily": ["date", "meter_id"],
}
DATE_COLUMN = {"well_tests": "test_date", "tank_gauges": "datetime", "well_test_readings": None}
assert set(NATURAL_KEYS) == set(SCHEMAS)


def text_hash(*parts) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(json.dumps(p, sort_keys=True, default=str).encode())
    return h.hexdigest()[:16]


# ---------------------------------------------------------------------------- network / config
def current_network(db: Session, site_id: int) -> Optional[NetworkVersion]:
    return db.scalar(select(NetworkVersion).where(NetworkVersion.site_id == site_id)
                     .order_by(NetworkVersion.id.desc()).limit(1))


def current_config(db: Session, site_id: int) -> Optional[ConfigVersion]:
    return db.scalar(select(ConfigVersion).where(ConfigVersion.site_id == site_id)
                     .order_by(ConfigVersion.id.desc()).limit(1))


def build_network(nv: NetworkVersion) -> Network:
    cfg = yaml.safe_load(nv.yaml_text) or {}
    static = pd.read_csv(io.StringIO(nv.well_static_csv)) if nv.well_static_csv else None
    strap = {tid: pd.read_csv(io.StringIO(txt)) for tid, txt in (nv.strapping_csv or {}).items()}
    return network_from_dict(cfg, static, strap)


def build_config(cv: Optional[ConfigVersion]) -> dict:
    user = (yaml.safe_load(cv.yaml_text) or {}) if cv else {}
    return deep_merge(DEFAULTS, user)


def network_yaml(nv: NetworkVersion) -> dict:
    return yaml.safe_load(nv.yaml_text) or {}


# ---------------------------------------------------------------------------- inputs
def raw_frames(db: Session, site_id: int, datasets=None) -> Tuple[Dict[str, pd.DataFrame], List[int]]:
    """Committed input rows as text frames, newest batch winning per natural key."""
    q = (select(InputRow.dataset, InputRow.natural_key, InputRow.payload, InputRow.batch_id)
         .join(InputBatch, InputBatch.id == InputRow.batch_id)
         .where(InputBatch.site_id == site_id, InputBatch.status == "committed")
         .order_by(InputRow.batch_id, InputRow.id))
    if datasets:
        q = q.where(InputRow.dataset.in_(list(datasets)))
    by_ds: Dict[str, Dict[str, dict]] = {}
    batches = set()
    for ds, key, payload, bid in db.execute(q):
        by_ds.setdefault(ds, {})[key] = payload     # later batch replaces the same key
        batches.add(bid)
    raw = {}
    for ds, rows in by_ds.items():
        cols: List[str] = []
        for p in rows.values():
            for c in p:
                if c not in cols:
                    cols.append(c)
        df = pd.DataFrame([{c: p.get(c, "") for c in cols} for p in rows.values()], columns=cols)
        raw[ds] = pd.read_csv(io.StringIO(df.to_csv(index=False)))
    return raw, sorted(batches)


def load_inputs(db: Session, site_id: int) -> Tuple[Dict[str, pd.DataFrame], List[int]]:
    raw, batches = raw_frames(db, site_id)
    return prepare_inputs(raw), batches


def production_days(data: Dict[str, pd.DataFrame]) -> List[str]:
    """Days the engine can validate: well data plus opening and closing tank gauges."""
    if "tank_gauges" not in data or data["tank_gauges"].empty:
        return []
    gauge_days = set(data["tank_gauges"]["datetime"].dt.strftime("%Y-%m-%d"))
    days = sorted(set(data["well_daily"]["date"].unique()))
    nxt = {d: (pd.Timestamp(d) + pd.Timedelta(days=1)).strftime("%Y-%m-%d") for d in days}
    return [d for d in days if d in gauge_days and nxt[d] in gauge_days]


def active_overrides(db: Session, site_id: int) -> Dict[str, dict]:
    rows = db.scalars(select(TestOverride).where(TestOverride.site_id == site_id, TestOverride.active.is_(True))
                      .order_by(TestOverride.id))
    return {o.test_id: {"status": o.status, "reason": o.reason, "by": o.by} for o in rows}


# ---------------------------------------------------------------------------- results
def _f(x) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if np.isfinite(v) else None


def put_table(db: Session, run_id: int, name: str, rows) -> None:
    if isinstance(rows, pd.DataFrame):
        rows = frame_records(rows)
    db.add(RunTable(run_id=run_id, name=name, rows=rows))


def persist_validation(db: Session, run: Run, res) -> None:
    """Results -> rows. Every row is keyed by run.id."""
    recs = res.to_records()
    for name in res.TABLES:
        if name in ("wells", "findings_daily"):
            continue                                   # typed tables below
        put_table(db, run.id, "register" if name == "findings" else name, recs[name])
    for r in recs["findings_daily"]:
        db.add(Finding(run_id=run.id, date=r["date"], layer=r["layer"], severity=r["severity"],
                       object_type=r["object_type"], object_id=str(r["object_id"]), check=r["check"],
                       message=r["message"], value=_f(r.get("value")),
                       limit=None if r.get("limit") is None else str(r["limit"]), action=r.get("action") or ""))
    # reconciled well uncertainty lives in the DVR variable table
    v = res.dvr_vars
    sd = {}
    if len(v):
        wv = v[v["var"].astype(str).str.startswith("well:")]
        sd = {(d, var): s for d, var, s in zip(wv["date"], wv["var"], wv["sd_rec"])}
    for r in recs["wells"]:
        d, w = r["date"], r["well_id"]
        db.add(WellDailyResult(
            run_id=run.id, date=d, well_id=w, hours_on=_f(r.get("hours_on")),
            theo_oil_m3=_f(r.get("theo_oil")), theo_oil_sd_m3=_f(r.get("sd_oil")),
            prop_oil_m3=_f(r.get("prop_oil")),
            dvr_oil_m3=_f(r.get("dvr_oil")), dvr_oil_sd_m3=_f(sd.get((d, f"well:{w}:oil"))),
            dvr_water_m3=_f(r.get("dvr_water")), dvr_water_sd_m3=_f(sd.get((d, f"well:{w}:water"))),
            dvr_gas_sm3=_f(r.get("dvr_gas")), dvr_gas_sd_sm3=_f(sd.get((d, f"well:{w}:gas"))),
            data=r))


def persist_twin(db: Session, run: Run, ctx: dict) -> None:
    from flowval.twin.run import twin_state, twin_tables
    for name, df in twin_tables(ctx).items():
        put_table(db, run.id, name, df)
    l3, l4, l5, twin, live = ctx["l3"], ctx["l4"], ctx["l5"], ctx["twin"], ctx["live"]
    put_table(db, run.id, "L3_daily_forecast", l3["daily"])
    scen = [df.assign(scenario=name) for name, df in l3["scenarios"].items()]
    put_table(db, run.id, "L3_scenarios", pd.concat(scen, ignore_index=True) if scen else pd.DataFrame())
    put_table(db, run.id, "L4_plan_wells", l4["plan"]["wells"])
    put_table(db, run.id, "L4_plan_totals", [dict(quantity=k, base=_f(a), plan=_f(b))
                                             for k, (a, b) in l4["plan"]["totals"].items()])
    put_table(db, run.id, "L4_gl_summary", [{k: _f(v) for k, v in (l4["gl_summary"] or {}).items()}])
    put_table(db, run.id, "L1_tanks", live["tanks"])
    st = live["steady"]
    put_table(db, run.id, "L1_facility", [dict(
        date=twin.date, installation=twin.name, oil_m3d=_f(st["oil"]), water_m3d=_f(st["water"]),
        gas_formation_sm3d=_f(st["gas_form"]), lift_gas_sm3d=_f(st["lift"]), gas_total_sm3d=_f(st["gas_total"]),
        flare_sm3d=_f(st["flare"]), compressor_load=_f(st["compressor_load"]),
        compressor_capacity_sm3d=_f(twin.compressor_capacity), lift_available_sm3d=_f(twin.lift_available),
        tank_fill=_f(live["tank_fill"]), tank_capacity_m3=_f(twin.tankfarm.capacity_m3),
        tank_inventory_m3=_f(twin.tankfarm.inventory_m3), runtime_s=_f(ctx.get("runtime_s")))])
    put_table(db, run.id, "L0_wells", l0_wells(twin))
    gate = l5["gate"]
    put_table(db, run.id, "L5_summary", [dict(
        mode=l5["mode"], start_oil=_f(l5["start_oil"]), end_oil=_f(l5["end_oil"]), gain=_f(l5["gain"]),
        lift_start=_f(l5["lift_start"]), lift_end=_f(l5["lift_end"]),
        locked=json.dumps(l5["locked"]), freeze_gas_lift=bool(gate["freeze_gas_lift"]),
        gas_lift_reason=gate.get("gas_lift_reason", ""), notes="; ".join(gate.get("notes", [])))])
    put_table(db, run.id, "twin_state", [json.loads(json.dumps(twin_state(ctx), default=str))])


def l0_wells(twin) -> List[dict]:
    rows = []
    for w in twin.wells.values():
        g, fl = w.geom, w.fluid
        rows.append(dict(
            well_id=w.id, lift=w.lift, separator=w.separator, on=w.on, modelled=w.modelled, test_id=w.test_id,
            pr_ksc=_f(w.pr), pb_ksc=_f(w.pb), pi_m3d_per_ksc=_f(w.pi), tuning=_f(w.tuning),
            tuning_target=w.tuning_target, choke_64ths=_f(w.choke), choke_cal=_f(w.choke_cal), flp_ksc=_f(w.flp),
            lift_gas_sm3d=_f(w.lift_gas), spm=_f(w.spm), stroke_in=_f(w.stroke), plunger_in=_f(w.plunger),
            srp_ve=_f(w.ve), chp_ksc=_f(w.chp), wc=_f(fl.wc) if fl else None, gor_sm3_m3=_f(fl.gor) if fl else None,
            perf_depth_m=_f(getattr(g, "perf_depth_m", None)) if g else None,
            pump_depth_m=_f(getattr(g, "pump_depth_m", None)) if g else None,
            fallback_liquid_m3d=_f(w.fallback_rate)))
    return rows


def get_table(db: Session, run_id: int, name: str) -> list:
    rt = db.scalar(select(RunTable).where(RunTable.run_id == run_id, RunTable.name == name))
    return rt.rows if rt is not None else []


def get_frame(db: Session, run_id: int, name: str) -> pd.DataFrame:
    return pd.DataFrame(get_table(db, run_id, name))
