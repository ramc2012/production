"""Digital-twin services: cached synchronised twin per run, what-if, recommendations (L4) and the
L5 sandbox. Everything here runs on models and the plant SIMULATOR; nothing reaches the plant."""
from __future__ import annotations

import copy
import math
import pickle
import threading
from collections import OrderedDict
from typing import Dict, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from flowval.config import deep_merge
from flowval.records import frame_records
from flowval.twin import prescribe as X
from flowval.twin.autonomy import Supervisor, make_plant

from ..db import session_scope
from ..models import AutonomyRun, Recommendation, Run, now
from ..settings import get_settings
from .jobs import JobContext

SETPOINT_KEYS = ("choke", "lift_gas", "spm", "on")
_cache: "OrderedDict[int, dict]" = OrderedDict()
_lock = threading.Lock()


def _num(x) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def load_twin(run_id: int) -> dict:
    """{"twin": FacilityTwin, "res": Results} of a finished twin run (kept for the 3 latest runs)."""
    with _lock:
        if run_id in _cache:
            _cache.move_to_end(run_id)
            return _cache[run_id]
    f = get_settings().runs_dir / str(run_id) / "twin.pkl"
    if not f.exists():
        raise HTTPException(404, f"Run {run_id} has no twin model (run a twin run first)")
    with open(f, "rb") as fh:
        obj = pickle.load(fh)   # our own file, written by services.runs
    with _lock:
        _cache[run_id] = obj
        while len(_cache) > 3:
            _cache.popitem(last=False)
    return obj


# ---------------------------------------------------------------------------- what-if
def whatif(run_id: int, changes: Dict[str, dict]) -> dict:
    obj = load_twin(run_id)
    twin, cfg = obj["twin"], obj["res"].cfg
    clean: Dict[str, dict] = {}
    for wid, ch in (changes or {}).items():
        if wid not in twin.wells:
            raise HTTPException(400, f"Unknown well {wid}")
        c = {}
        for k, v in (ch or {}).items():
            if k not in SETPOINT_KEYS:
                raise HTTPException(400, f"Unknown setpoint '{k}' (use {', '.join(SETPOINT_KEYS)})")
            if v is None:
                continue
            c[k] = bool(v) if k == "on" else float(v)
        if c:
            clean[wid] = c
    r = X.whatif(twin, clean)
    st_new = twin.steady(clean)
    detail = st_new["wells"].set_index("well_id")
    tc = cfg["twin"]
    warnings = []
    rows = []
    for rec in frame_records(r["wells"]):
        wid = rec["well_id"]
        d = detail.loc[wid]
        row = dict(rec, changed=wid in clean, drawdown=_num(d.get("drawdown")), pwf_ksc=_num(d.get("pwf")),
                   submergence_m=_num(d.get("submergence")), choke=_num(d.get("choke")),
                   lift_gas_sm3d=_num(d.get("lift_gas")), spm=_num(d.get("spm")), on=bool(d.get("on")))
        rows.append(row)
        if wid in clean:
            if row["drawdown"] is not None and row["drawdown"] > tc["max_drawdown"]:
                warnings.append(f"{wid}: drawdown {row['drawdown']:.0%} exceeds the {tc['max_drawdown']:.0%} "
                                "sand/coning guard")
            if row["submergence_m"] is not None and row["submergence_m"] < cfg["well_model"]["min_submergence_m"]:
                warnings.append(f"{wid}: pump submergence {row['submergence_m']:.0f} m is below "
                                f"{cfg['well_model']['min_submergence_m']:.0f} m (pump-off risk)")
            if "choke" in clean[wid] and not tc["choke_min_64"] <= clean[wid]["choke"] <= tc["choke_max_64"]:
                warnings.append(f"{wid}: choke outside the {tc['choke_min_64']}-{tc['choke_max_64']}/64 range")
            if "spm" in clean[wid] and not tc["spm_min"] <= clean[wid]["spm"] <= tc["spm_max"]:
                warnings.append(f"{wid}: SPM outside {tc['spm_min']}-{tc['spm_max']}")
    tot = {k: dict(base=_num(a), new=_num(b), delta=_num(b - a)) for k, (a, b) in r["totals"].items()}
    if st_new["lift"] > twin.lift_available + 1:
        warnings.append(f"Lift gas {st_new['lift']:,.0f} Sm³/d exceeds the {twin.lift_available:,.0f} Sm³/d available")
    if tot["flare"]["delta"] and tot["flare"]["delta"] > 1:
        warnings.append(f"Compressor capacity exceeded: flare rises by {tot['flare']['delta']:,.0f} Sm³/d")
    return dict(run_id=run_id, date=twin.date, changes=clean, wells=rows, totals=tot, warnings=warnings,
                note="Model prediction on the synchronised twin. Advisory only; nothing is sent to the plant.")


# ---------------------------------------------------------------------------- recommendations
def create_recommendations(db: Session, run: Run, ctx: dict) -> None:
    twin, l4 = ctx["twin"], ctx["l4"]
    plan = l4["plan"]
    pw = plan["wells"].set_index("well_id") if len(plan["wells"]) else None
    gl = l4["gl"].set_index("well_id") if len(l4["gl"]) else None
    bc = l4["best_chokes"].set_index("well_id") if len(l4["best_chokes"]) else None
    srp = l4["srp"].set_index("well_id") if len(l4["srp"]) else None
    new = []
    for wid, sp in plan["setpoints"].items():
        w = twin.wells[wid]
        cur = {k: _num(getattr(w, k)) for k in sp}
        why = []
        if "lift_gas" in sp and gl is not None and wid in gl.index:
            why.append(f"gas-lift allocation (equal marginal oil); marginal now "
                       f"{gl.loc[wid, 'marginal_now']:.2f} m³ oil per 1000 Sm³")
        if "choke" in sp and bc is not None and wid in bc.index:
            why.append(f"choke what-if within drawdown guard (drawdown {bc.loc[wid, 'drawdown_new']:.0%})")
        if "spm" in sp and srp is not None and wid in srp.index:
            why.append(f"SRP speed: {srp.loc[wid, 'action']}, submergence "
                       f"{srp.loc[wid, 'submergence_now']:.0f} → {srp.loc[wid, 'submergence_new']:.0f} m")
        gain = _num(pw.loc[wid, "d_oil"]) if pw is not None and wid in pw.index else None
        new.append(Recommendation(site_id=run.site_id, run_id=run.id, well_id=wid, kind="setpoint", current=cur,
                                  proposed={k: _num(v) for k, v in sp.items()}, predicted_d_oil_m3d=gain,
                                  rationale="; ".join(why)))
    if srp is not None:
        for wid, r in srp.iterrows():
            a = str(r["action"])
            if a.startswith("repair") or a.startswith("hold:"):
                new.append(Recommendation(site_id=run.site_id, run_id=run.id, well_id=wid, kind="hold",
                                          current={"spm": _num(r["spm_now"])}, proposed={},
                                          predicted_d_oil_m3d=0.0, rationale=a))
    _expire_previous(db, run, twin)
    for r in new:
        db.add(r)


def _expire_previous(db: Session, run: Run, twin) -> None:
    """Undecided advice from earlier runs is superseded. Accepted advice becomes 'implemented'
    when the plant now runs at the proposed setpoint, or 'expired' if its premise changed."""
    old = db.scalars(select(Recommendation).where(Recommendation.site_id == run.site_id,
                                                  Recommendation.run_id != run.id,
                                                  Recommendation.status.in_(["proposed", "accepted"]))).all()
    for r in old:
        if r.status == "proposed":
            r.status, r.comment = "expired", f"Superseded by run {run.id}"
            continue
        w = twin.wells.get(r.well_id)
        if w is None or r.kind != "setpoint":
            continue
        now_sp = {k: _num(getattr(w, k)) for k in r.proposed}

        def close(a, b, k):
            tol = {"lift_gas": 100.0, "choke": 0.5, "spm": 0.1}.get(k, 1e-6)
            return a is not None and b is not None and abs(a - b) <= tol
        if all(close(now_sp[k], v, k) for k, v in r.proposed.items()):
            r.status, r.comment = "implemented", f"Plant at the proposed setpoint in run {run.id}"
        elif not all(close(now_sp[k], r.current.get(k), k) for k in r.proposed):
            r.status, r.comment = "expired", f"Premise changed (setpoints moved otherwise) by run {run.id}"


# ---------------------------------------------------------------------------- L5 sandbox
AUTONOMY_MODES = ("advisory", "supervised", "autonomous_sim")


def autonomy_job(run_id: int, params: dict, user: str, ctx: JobContext) -> dict:
    """Guarded closed loop against the plant SIMULATOR built from this run's twin."""
    obj = load_twin(run_id)
    twin, res = obj["twin"], obj["res"]
    mode = params.get("mode", "autonomous_sim")
    if mode not in AUTONOMY_MODES:
        raise ValueError(f"mode must be one of {AUTONOMY_MODES}")
    over = {k: params[k] for k in ("cycles", "plant_pi_mismatch_sd", "plant_noise_pct", "seed",
                                   "max_step_gl_sm3d", "max_total_gl_sm3d", "verify_tolerance_pct") if k in params}
    res2 = copy.copy(res)
    res2.cfg = deep_merge(res.cfg, {"twin": {"autonomy": over}})
    min_gain = float(params.get("approve_min_gain_m3d", 0.1))
    approver = (lambda move: move.get("predicted_d_oil", 0.0) >= min_gain) if mode == "supervised" else None
    plant = make_plant(twin, res2.cfg)
    sup = Supervisor(twin.copy(), plant, res2, mode=mode, approver=approver)
    out = sup.run(on_cycle=lambda c, n: ctx.progress(f"simulator cycle {c}/{n}", (c - 1) / n))
    result = dict(mode=out["mode"], start_oil=_num(out["start_oil"]), end_oil=_num(out["end_oil"]),
                  gain=_num(out["gain"]), lift_start=_num(out["lift_start"]), lift_end=_num(out["lift_end"]),
                  locked=out["locked"], gate=dict(freeze_gas_lift=bool(out["gate"]["freeze_gas_lift"]),
                                                  gas_lift_reason=out["gate"].get("gas_lift_reason", ""),
                                                  notes=out["gate"].get("notes", [])),
                  trajectory=frame_records(out["trajectory"]), log=frame_records(out["log"]),
                  final_setpoints={w: {k: _num(v) if k != "on" else bool(v) for k, v in sp.items()}
                                   for w, sp in out["final_setpoints"].items()},
                  approver_rule=(f"approve moves with predicted gain ≥ {min_gain} m³/d" if mode == "supervised" else None))
    with session_scope() as db:
        ar = AutonomyRun(run_id=run_id, job_id=ctx.job_id, mode=mode, params=params, result=result, created_by=user)
        db.add(ar)
        db.flush()
        result_id = ar.id
    return {"autonomy_run_id": result_id, "gain": result["gain"]}


def decide(db: Session, rec: Recommendation, status: str, user: str, comment: str = "") -> Recommendation:
    allowed = {"proposed": {"accepted", "declined"}, "accepted": {"implemented", "declined"},
               "declined": {"accepted"}}
    if status not in allowed.get(rec.status, set()):
        raise HTTPException(409, f"Cannot change a {rec.status} recommendation to {status}")
    if status == "declined" and not comment.strip():
        raise HTTPException(400, "A reason is required to decline")
    rec.status, rec.decided_by, rec.decided_at = status, user, now()
    if comment:
        rec.comment = comment
    return rec
