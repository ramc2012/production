"""One call that climbs the twin ladder: validation (L1-L2) -> twin (L0-L1) ->
forecasts (L3) -> recommendations (L4) -> guarded closed loop (L5, simulator)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from ..engine import run as run_validation
from ..report_excel import write_excel
from ..report_html import build_html
from . import predict as P
from . import prescribe as X
from .autonomy import Supervisor, make_plant
from .levels import assess, level_status
from .model import build_twin, live_state


def run_twin(network, data_dir, config=None, out: str = "output", start: Optional[str] = None,
             end: Optional[str] = None, mode: Optional[str] = None, write: bool = True) -> dict:
    t0 = time.time()
    res = run_validation(network, data_dir, config, start, end)
    cfg = res.cfg
    tc = cfg["twin"]
    twin = build_twin(res, data_dir)
    live = live_state(res, twin)

    wsum, fc = P.well_forecasts(res, int(tc["forecast_days"]))
    inst = P.installation_forecast(fc)
    scen = P.lookahead_scenarios(twin, int(tc["lookahead_hours"]))
    la = P.lookahead_summary(scen, twin)
    drift = P.meter_drift(res)
    due = P.tests_due(res, twin)

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
    l5 = Supervisor(loop_twin, plant, res, mode=mode).run()

    l4 = dict(gl=gl, gl_summary=gls, curves=curves, chokes=cw, best_chokes=bc, srp=srp, plan=plan,
              schedule=sched, maintenance=maint)
    l3 = dict(wells=wsum, daily=fc, installation=inst, scenarios=scen, lookahead=la, drift=drift, due=due)
    assessment = assess(res, twin, l3, l4, l5)
    levels = level_status(assessment)
    ctx = dict(res=res, twin=twin, live=live, l3=l3, l4=l4, l5=l5, assessment=assessment, levels=levels,
               runtime_s=time.time() - t0)
    if write:
        outp = Path(out)
        outp.mkdir(parents=True, exist_ok=True)
        tag = f"{res.dates[0]}_{res.dates[-1]}"
        (outp / f"flow_validation_{tag}.html").write_text(build_html(res), encoding="utf-8")
        write_excel(res, outp / f"flow_validation_{tag}.xlsx")
        from .report import build_twin_html
        (outp / f"digital_twin_{res.dates[-1]}.html").write_text(build_twin_html(ctx, f"flow_validation_{tag}.html"),
                                                                 encoding="utf-8")
        with pd.ExcelWriter(outp / f"digital_twin_{res.dates[-1]}.xlsx", engine="openpyxl") as xw:
            for name, df in (("Levels", levels), ("Assessment", assessment), ("L1_live_wells", live["wells"]),
                             ("L3_well_forecast", wsum), ("L3_installation", inst), ("L3_lookahead", la),
                             ("L3_meter_drift", drift), ("L3_tests_due", due), ("L4_gas_lift", gl),
                             ("L4_gl_curves", curves), ("L4_chokes", cw), ("L4_srp", srp),
                             ("L4_test_schedule", sched), ("L4_maintenance", maint),
                             ("L5_trajectory", l5["trajectory"]), ("L5_audit_log", l5["log"])):
                (df if df is not None and len(df) else pd.DataFrame({"note": ["none"]})).to_excel(
                    xw, sheet_name=name, index=False)
        state = dict(date=twin.date, installation=twin.name,
                     levels=levels[["level", "name", "status"]].to_dict("records"),
                     wells=live["wells"].replace({np.nan: None}).to_dict("records"),
                     tank_fill=live["tank_fill"],
                     recommendations={w: {k: float(v) for k, v in sp.items()} for w, sp in plan["setpoints"].items()},
                     predicted_oil_gain_m3d=float(plan["totals"]["oil"][1] - plan["totals"]["oil"][0]))
        (outp / f"twin_state_{res.dates[-1]}.json").write_text(json.dumps(state, indent=2, default=str))
        ctx["out"] = outp
    return ctx
