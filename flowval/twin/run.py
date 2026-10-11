"""One call that climbs the twin ladder: validation (L1-L2) -> twin (L0-L1) ->
forecasts (L3) -> recommendations (L4) -> guarded closed loop (L5, simulator)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

from ..config import load_config
from ..engine import run_frames
from ..io import load_inputs
from ..network import load_network
from ..report_excel import write_excel
from ..report_html import build_html
from . import predict as P
from . import prescribe as X
from .autonomy import Supervisor, make_plant
from .levels import assess, level_status
from .model import build_twin, live_state


def run_twin(network, data_dir, config=None, out: str = "output", start: Optional[str] = None,
             end: Optional[str] = None, mode: Optional[str] = None, write: bool = True) -> dict:
    net = load_network(network)
    cfg = config if isinstance(config, dict) else load_config(config)
    ctx = run_twin_frames(net, load_inputs(data_dir), cfg, start, end, mode)
    if write:
        write_twin_outputs(ctx, out)
    return ctx


def run_twin_frames(net, data: dict, cfg: dict, start: Optional[str] = None, end: Optional[str] = None,
                    mode: Optional[str] = None, test_overrides: Optional[dict] = None,
                    progress: Optional[Callable[[str, float], None]] = None, res=None) -> dict:
    """Climb the twin ladder from in-memory inputs. ``progress(stage, fraction)`` is optional.
    Pass ``res`` to reuse an existing validation result for the same inputs."""
    t0 = time.time()
    step = progress or (lambda stage, frac: None)
    step("validation", 0.0)
    if res is None:
        res = run_frames(net, data, cfg, start, end, test_overrides)
    cfg = res.cfg
    tc = cfg["twin"]
    step("twin model", 0.35)
    twin = build_twin(res, data)
    live = live_state(res, twin)

    step("forecasts", 0.45)
    wsum, fc = P.well_forecasts(res, int(tc["forecast_days"]))
    inst = P.installation_forecast(fc)
    scen = P.lookahead_scenarios(twin, int(tc["lookahead_hours"]))
    la = P.lookahead_summary(scen, twin)
    drift = P.meter_drift(res)
    due = P.tests_due(res, twin)

    step("recommendations", 0.55)
    curves = X.gas_lift_curves(twin, cfg)
    gl, gls = X.optimise_gas_lift(twin, cfg, curves)
    cw = X.choke_whatifs(twin, cfg)
    bc = X.best_chokes(cw, cfg)
    srp = X.srp_speed(twin, cfg, res.findings_daily)
    plan = X.combined_plan(twin, cfg, gl, bc, srp)
    sched = X.test_schedule(res, twin)
    maint = X.maintenance(res, drift)

    loop_twin = twin.copy()
    plant = make_plant(twin, cfg)
    l5 = Supervisor(loop_twin, plant, res, mode=mode).run(
        on_cycle=lambda c, n: step(f"L5 simulator cycle {c}/{n}", 0.65 + 0.3 * (c - 1) / n))

    l4 = dict(gl=gl, gl_summary=gls, curves=curves, chokes=cw, best_chokes=bc, srp=srp, plan=plan,
              schedule=sched, maintenance=maint)
    l3 = dict(wells=wsum, daily=fc, installation=inst, scenarios=scen, lookahead=la, drift=drift, due=due)
    assessment = assess(res, twin, l3, l4, l5)
    levels = level_status(assessment)
    step("done", 1.0)
    return dict(res=res, twin=twin, live=live, l3=l3, l4=l4, l5=l5, assessment=assessment, levels=levels,
                runtime_s=time.time() - t0)


def twin_state(ctx: dict) -> dict:
    """The live state and recommended setpoints (twin_state_<date>.json)."""
    twin, live, levels, plan = ctx["twin"], ctx["live"], ctx["levels"], ctx["l4"]["plan"]
    return dict(date=twin.date, installation=twin.name,
                levels=levels[["level", "name", "status"]].to_dict("records"),
                wells=live["wells"].replace({np.nan: None}).to_dict("records"),
                tank_fill=live["tank_fill"],
                recommendations={w: {k: float(v) for k, v in sp.items()} for w, sp in plan["setpoints"].items()},
                predicted_oil_gain_m3d=float(plan["totals"]["oil"][1] - plan["totals"]["oil"][0]))


def write_twin_outputs(ctx: dict, out) -> Path:
    """Write the validation report/workbook, twin dashboard, twin workbook and twin_state JSON."""
    res = ctx["res"]
    outp = Path(out)
    outp.mkdir(parents=True, exist_ok=True)
    tag = f"{res.dates[0]}_{res.dates[-1]}"
    (outp / f"flow_validation_{tag}.html").write_text(build_html(res), encoding="utf-8")
    write_excel(res, outp / f"flow_validation_{tag}.xlsx")
    from .report import build_twin_html
    (outp / f"digital_twin_{res.dates[-1]}.html").write_text(build_twin_html(ctx, f"flow_validation_{tag}.html"),
                                                             encoding="utf-8")
    with pd.ExcelWriter(outp / f"digital_twin_{res.dates[-1]}.xlsx", engine="openpyxl") as xw:
        for name, df in twin_tables(ctx).items():
            (df if df is not None and len(df) else pd.DataFrame({"note": ["none"]})).to_excel(
                xw, sheet_name=name, index=False)
    (outp / f"twin_state_{res.dates[-1]}.json").write_text(json.dumps(twin_state(ctx), indent=2, default=str))
    ctx["out"] = outp
    return outp


def twin_tables(ctx: dict) -> dict:
    """Every twin table by sheet name (also what the app persists per twin run)."""
    l3, l4, l5, live = ctx["l3"], ctx["l4"], ctx["l5"], ctx["live"]
    return {"Levels": ctx["levels"], "Assessment": ctx["assessment"], "L1_live_wells": live["wells"],
            "L3_well_forecast": l3["wells"], "L3_installation": l3["installation"], "L3_lookahead": l3["lookahead"],
            "L3_meter_drift": l3["drift"], "L3_tests_due": l3["due"], "L4_gas_lift": l4["gl"],
            "L4_gl_curves": l4["curves"], "L4_chokes": l4["chokes"], "L4_srp": l4["srp"],
            "L4_test_schedule": l4["schedule"], "L4_maintenance": l4["maintenance"],
            "L5_trajectory": l5["trajectory"], "L5_audit_log": l5["log"]}
