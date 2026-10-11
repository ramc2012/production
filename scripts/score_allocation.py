"""Score well allocation against the synthetic ground truth.

Compares four ways of assigning production to wells:
  theoretical   last test x hours on (no measurement used)
  proportional  theoretical x installation allocation factor (API MPMS 20.1)
  DVR           reconciliation with test-based rates only
  DVR + well    reconciliation with the well-parameter VFM added
Usage: python scripts/score_allocation.py [data_dir]
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from flowval.config import load_config  # noqa: E402
from flowval import engine  # noqa: E402


def score(data_dir: Path) -> pd.DataFrame:
    truth = pd.read_csv(data_dir / "_truth_well_daily.csv")
    cfg_path = data_dir / "config.yaml"
    out = {}
    for label, use in (("DVR (tests only)", False), ("DVR + well parameters", True)):
        cfg = load_config(cfg_path)
        cfg["well_model"]["use_in_dvr"] = use
        res = engine.run(data_dir / "network.yaml", data_dir, cfg)
        w = res.wells.merge(truth, on=["date", "well_id"], how="left", suffixes=("", "_true"))
        if "theoretical" not in out:
            out["theoretical"] = w.assign(est=w["theo_oil"])
            out["proportional (API 20.1)"] = w.assign(est=w["prop_oil"])
        out[label] = w.assign(est=w["dvr_oil"])
    rows = []
    for label, w in out.items():
        w = w.dropna(subset=["est", "oil_true"])
        daily_err = (w["est"] - w["oil_true"]).abs()
        per_well = w.groupby("well_id")[["est", "oil_true"]].sum()
        well_err = (per_well["est"] / per_well["oil_true"] - 1).abs()
        rows.append(dict(method=label,
                         daily_MAE_m3=daily_err.mean(),
                         daily_MAPE_pct=100 * (daily_err / w["oil_true"].clip(lower=0.5)).mean(),
                         monthly_well_err_mean_pct=100 * well_err.mean(),
                         monthly_well_err_max_pct=100 * well_err.max(),
                         worst_well=well_err.idxmax()))
    return pd.DataFrame(rows)


if __name__ == "__main__":
    d = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "sample"
    pd.set_option("display.width", 200)
    print(score(d).round(2).to_string(index=False))
