"""Level 5 - autonomous twin: a guarded closed loop (SIMULATION ONLY).

The supervisor repeats observe -> trust-gate -> re-synchronise -> optimise
-> guard -> act -> verify on a *plant simulator* whose parameters differ from
the twin's (PI, choke factor, holdup, pump efficiency, measurement noise),
so the loop must cope with model-plant mismatch.

Safety design (applies before any field use):
* modes: ``advisory`` (log only), ``supervised`` (an approver callback must
  accept each move), ``autonomous_sim`` (applies moves to the simulator only);
* trust gate from Level 2: wells or systems whose data the reconciliation or
  data-quality layers do not trust in the last days are locked;
* hard limits (min/max) and rate-of-change limits per cycle;
* facility constraints: lift-gas supply, compressor capacity, drawdown limit;
* verify-or-rollback: if the measured change is worse than predicted beyond
  the noise tolerance, the move is reverted and the well is locked;
* full audit log of every proposal, approval, action and verification.

There is deliberately no interface to a real control system here. A field
implementation needs OPC UA write-back through the DCS with MOC, alarm
management, SIL/HAZOP review and IEC 62443 cyber-security controls.
"""
from __future__ import annotations

import copy
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from . import prescribe as X
from .model import FacilityTwin, synchronise

UNITS = {"lift_gas": ("lift gas", "Sm3/d", 0), "choke": ("choke", "/64", 0), "spm": ("SPM", "", 1)}


def _fmt_move(mv: dict, old: dict) -> str:
    parts = []
    for k, v in mv.items():
        name, unit, d = UNITS.get(k, (k, "", 2))
        parts.append(f"{name} {old.get(k, float('nan')):,.{d}f} -> {v:,.{d}f} {unit}".strip())
    return "; ".join(parts)


LOCK_CHECKS = {"nodal_no_flow", "offline_estimate", "frozen", "thp_high", "estimator_conflict"}


def make_plant(twin: FacilityTwin, cfg: dict) -> FacilityTwin:
    """A 'real' plant for the demo: same structure, different hidden parameters."""
    a = cfg["twin"]["autonomy"]
    rng = np.random.default_rng(a["seed"])
    plant = copy.deepcopy(twin)
    for w in plant.wells.values():
        w.tuning *= float(np.clip(rng.normal(1.0, a["plant_pi_mismatch_sd"]), 0.7, 1.3))
        if w.choke_cal == w.choke_cal:
            w.choke_cal *= float(np.clip(rng.normal(1.0, 0.03), 0.9, 1.1))
        if w.geom is not None:
            w.geom = copy.deepcopy(w.geom)
            w.geom.hl_mult = 1.05
    return plant


def measure(plant: FacilityTwin, rng, noise_pct: float) -> pd.DataFrame:
    st = plant.steady()
    df = st["wells"][["well_id", "L", "oil", "water", "gas", "lift_gas"]].copy()
    k = 1 + rng.normal(0, noise_pct / 100, len(df))
    df["L_meas"] = df["L"] * k
    df["oil_meas"] = df["oil"] * k
    df.attrs["true_oil"] = st["oil"]
    df.attrs["gas_total"] = st["gas_total"]
    df.attrs["flare"] = st["flare"]
    return df


def trust_gate(res, days: int = 5) -> dict:
    """Wells / systems the diagnostic layer does not currently trust."""
    f = res.findings_daily
    end = pd.Timestamp(res.dates[-1])
    recent = f[pd.to_datetime(f["date"]) > end - pd.Timedelta(days=days)]
    locked = {}
    for _, r in recent.iterrows():
        if r["object_type"] == "well" and (r["check"] in LOCK_CHECKS or
                                            (r["check"] == "gross_error" and r["severity"] == "ALARM")):
            locked.setdefault(r["object_id"], r["check"])
    lift_meters = {m.id for m in res.network.meters.values() if m.id == res.network.lift_gas_header_meter}
    lift_meters |= {w.lift_gas_meter for w in res.network.wells.values() if w.lift_gas_meter}
    bad_lift = recent[recent["object_id"].isin(lift_meters) &
                      recent["check"].isin(["gross_error", "systematic_bias", "missing", "frozen"])]
    notes = []
    gas_meters = recent[(recent["check"].isin(["gross_error", "systematic_bias"])) & (recent["object_type"] == "meter")]
    if len(gas_meters):
        notes.append("Meters with gross errors (" + ", ".join(sorted(gas_meters["object_id"].unique())) +
                     ") are replaced by reconciled values in the twin")
    return dict(locked_wells=locked, freeze_gas_lift=len(bad_lift) > 0,
                gas_lift_reason=", ".join(sorted(bad_lift["object_id"].unique())), notes=notes)


class Supervisor:
    def __init__(self, twin: FacilityTwin, plant: FacilityTwin, res, mode: Optional[str] = None,
                 approver: Optional[Callable[[dict], bool]] = None):
        self.twin, self.plant, self.res, self.cfg = twin, plant, res, res.cfg
        self.a = self.cfg["twin"]["autonomy"]
        self.mode = mode or self.a["mode"]
        self.approver = approver or (lambda move: True)
        self.rng = np.random.default_rng(self.a["seed"] + 1)
        self.gate = trust_gate(res)
        self.locked = dict(self.gate["locked_wells"])
        self.log: List[dict] = []
        self.trajectory: List[dict] = []
        self.initial = {w.id: dict(choke=w.choke, lift_gas=w.lift_gas, spm=w.spm) for w in plant.wells.values()}

    # -- helpers ------------------------------------------------------------
    def _limit(self, wid: str, key: str, cur: float, target: float) -> float:
        tc = self.cfg["twin"]
        if key == "lift_gas":
            step, lo, hi, tot = self.a["max_step_gl_sm3d"], tc["gl_min_sm3d"], tc["gl_max_sm3d"], self.a["max_total_gl_sm3d"]
        elif key == "choke":
            step, lo, hi, tot = self.a["max_step_choke_64"], tc["choke_min_64"], tc["choke_max_64"], self.a["max_total_choke_64"]
        else:
            step, lo, hi, tot = self.a["max_step_spm"], tc["spm_min"], tc["spm_max"], self.a["max_total_spm"]
        v = cur + float(np.clip(target - cur, -step, step))
        start = self.initial[wid][key]
        if start == start:   # cumulative envelope around the starting setpoint
            v = float(np.clip(v, start - tot, start + tot))
        return float(np.clip(v, lo, hi))

    def _set(self, model: FacilityTwin, wid: str, key: str, value: float):
        setattr(model.wells[wid], key, value)

    def propose(self) -> Dict[str, dict]:
        tw, cfg = self.twin, self.cfg
        moves: Dict[str, dict] = {}
        if not self.gate["freeze_gas_lift"]:
            gl, _ = X.optimise_gas_lift(tw, cfg)
            for _, r in gl.iterrows():
                if abs(r["d_lift"]) >= cfg["twin"]["gl_step_sm3d"]:
                    moves.setdefault(r["well_id"], {})["lift_gas"] = r["lift_opt"]
        ch = X.best_chokes(X.choke_whatifs(tw, cfg), cfg)
        for _, r in ch.iterrows():
            moves.setdefault(r["well_id"], {})["choke"] = r["choke_new"]
        sr = X.srp_speed(tw, cfg, self.res.findings_daily)
        for _, r in sr.iterrows():
            if r["action"] in ("increase", "reduce (pump starved)"):
                moves.setdefault(r["well_id"], {})["spm"] = r["spm_new"]
        return moves

    # -- main loop ------------------------------------------------------------
    def run(self, cycles: Optional[int] = None) -> dict:
        cycles = cycles or int(self.a["cycles"])
        noise = self.a["plant_noise_pct"]
        tol = self.a["verify_tolerance_pct"]
        obs = measure(self.plant, self.rng, noise)
        start_oil = obs.attrs["true_oil"]
        self.trajectory.append(dict(cycle=0, plant_oil=start_oil, measured_oil=obs["oil_meas"].sum(),
                                    twin_oil=self.twin.steady()["oil"], lift=self.plant.steady()["lift"],
                                    moves=0, rollbacks=0))
        quiet = 0
        for c in range(1, cycles + 1):
            # 1. re-synchronise the twin to what the plant is doing now
            synchronise(self.twin.wells, dict(zip(obs["well_id"], obs["L_meas"])))
            # 2. optimise on the twin, 3. guard
            targets = self.propose()
            sp: Dict[str, dict] = {}
            for wid, mv in targets.items():
                if wid in self.locked:
                    self.log.append(dict(cycle=c, well_id=wid, event="blocked", detail=f"locked: {self.locked[wid]}"))
                    continue
                w = self.twin.wells[wid]
                for key, target in mv.items():
                    cur = getattr(w, key)
                    new = self._limit(wid, key, cur, target)
                    if abs(new - cur) > 1e-6:
                        sp.setdefault(wid, {})[key] = new
            # deadband: drop moves whose predicted benefit is negligible
            if sp:
                pred = X.whatif(self.twin, sp)
                pw0 = pred["wells"].set_index("well_id")
                for wid in list(sp):
                    gain = float(pw0.loc[wid, "d_oil"])
                    saving = self.twin.wells[wid].lift_gas - sp[wid].get("lift_gas", self.twin.wells[wid].lift_gas)
                    if gain < self.a["min_move_gain_m3d"] and not (saving >= self.a["min_gl_saving_sm3d"] and gain >= -0.02):
                        del sp[wid]
            # facility check of the guarded move set
            if sp:
                pred = X.whatif(self.twin, sp)
                lift_new = pred["totals"]["lift"][1]
                if lift_new > self.twin.lift_available + 1 or pred["totals"]["flare"][1] > pred["totals"]["flare"][0] + 1:
                    self.log.append(dict(cycle=c, well_id="*", event="rejected",
                                         detail="facility constraint (lift supply / compressor) violated"))
                    sp = {}
            if self.mode == "advisory" and c > 1:
                break   # nothing is applied in advisory mode, so later cycles would repeat cycle 1
            if not sp:
                quiet += 1
                self.trajectory.append(dict(cycle=c, plant_oil=obs.attrs["true_oil"], measured_oil=obs["oil_meas"].sum(),
                                            twin_oil=self.twin.steady()["oil"], lift=self.plant.steady()["lift"],
                                            moves=0, rollbacks=0))
                if quiet >= 2:
                    break
                continue
            quiet = 0
            pred = X.whatif(self.twin, sp)
            pw = pred["wells"].set_index("well_id")
            # 4. act (mode dependent)
            applied = {}
            for wid, mv in sp.items():
                move = dict(well_id=wid, **mv, predicted_d_oil=float(pw.loc[wid, "d_oil"]))
                if self.mode == "advisory":
                    self.log.append(dict(cycle=c, well_id=wid, event="advised",
                                         detail=_fmt_move(mv, {k: getattr(self.twin.wells[wid], k) for k in mv}),
                                         predicted_d_oil=move["predicted_d_oil"]))
                    continue
                if self.mode == "supervised" and not self.approver(move):
                    self.log.append(dict(cycle=c, well_id=wid, event="declined", detail=str(mv)))
                    continue
                old = {k: getattr(self.plant.wells[wid], k) for k in mv}
                for k, v in mv.items():
                    self._set(self.plant, wid, k, v)
                    self._set(self.twin, wid, k, v)
                applied[wid] = (mv, old)
                self.log.append(dict(cycle=c, well_id=wid, event="applied", detail=_fmt_move(mv, old),
                                     predicted_d_oil=move["predicted_d_oil"]))
            # 5. verify against the plant, roll back what made things worse
            new_obs = measure(self.plant, self.rng, noise)
            o0, o1 = obs.set_index("well_id"), new_obs.set_index("well_id")
            rollbacks = 0
            for wid, (mv, old) in applied.items():
                d_meas = o1.loc[wid, "oil_meas"] - o0.loc[wid, "oil_meas"]
                d_pred = float(pw.loc[wid, "d_oil"])
                band = 2 * noise / 100 * max(o0.loc[wid, "oil_meas"], 1.0) + tol / 100 * o0.loc[wid, "oil_meas"]
                ok = d_meas >= min(d_pred, 0.0) - band
                self.log.append(dict(cycle=c, well_id=wid, event="verified" if ok else "rolled back",
                                     detail=f"measured {d_meas:+.2f} vs predicted {d_pred:+.2f} m3/d",
                                     predicted_d_oil=d_pred, measured_d_oil=d_meas))
                if not ok:
                    for k, v in old.items():
                        self._set(self.plant, wid, k, v)
                        self._set(self.twin, wid, k, v)
                    self.locked[wid] = "rolled back"
                    rollbacks += 1
            if rollbacks:
                new_obs = measure(self.plant, self.rng, noise)
            obs = new_obs
            self.trajectory.append(dict(cycle=c, plant_oil=obs.attrs["true_oil"], measured_oil=obs["oil_meas"].sum(),
                                        twin_oil=self.twin.steady()["oil"], lift=self.plant.steady()["lift"],
                                        moves=len(applied), rollbacks=rollbacks))
        traj = pd.DataFrame(self.trajectory)
        return dict(mode=self.mode, trajectory=traj, log=pd.DataFrame(self.log), gate=self.gate,
                    start_oil=start_oil, end_oil=float(traj["plant_oil"].iloc[-1]),
                    gain=float(traj["plant_oil"].iloc[-1] - start_oil),
                    lift_start=float(traj["lift"].iloc[0]), lift_end=float(traj["lift"].iloc[-1]),
                    final_setpoints=self.plant.setpoints(), locked=self.locked)
