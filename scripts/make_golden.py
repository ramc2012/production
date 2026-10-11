"""Freeze the current engine behaviour as a golden baseline (tests/golden/baseline.json).

Run only when a behaviour change is intended and reviewed:
    python scripts/make_golden.py
tests/test_golden.py then guards every refactor (e.g. the move into the standalone app).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SAMPLE = ROOT / "data" / "sample"


def snapshot() -> dict:
    from flowval.twin.run import run_twin
    ctx = run_twin(SAMPLE / "network.yaml", SAMPLE, SAMPLE / "config.yaml", write=False)
    res = ctx["res"]
    f = res.findings_daily
    s = res.summary
    d = res.dvr_days
    w = res.wells
    return {
        "period": [res.dates[0], res.dates[-1]],
        "findings_by_check": f.groupby(["object_id", "check"]).size().rename("n").reset_index()
                              .assign(key=lambda x: x["object_id"] + "|" + x["check"])
                              .set_index("key")["n"].astype(int).to_dict(),
        "dvr_eliminated": dict(zip(d["date"], d["eliminated"].fillna(""))),
        "dvr_passed_initial": int(d["passed_initial"].sum()),
        "totals": {k: round(float(s[k].sum()), 3) for k in ("dvr_oil", "dvr_water", "dvr_gas", "theo_oil", "meas_oil")},
        "well_oil_dvr": {k: round(float(v), 3) for k, v in w.groupby("well_id")["dvr_oil"].sum().items()},
        "tests_status": dict(zip(res.tests["test_id"], res.tests["status"])),
        "twin": {
            "levels": dict(zip(ctx["levels"]["level"].astype(str), ctx["levels"]["status"])),
            "l3_oil_30d_p50": round(float(ctx["l3"]["installation"].iloc[29]["p50"]), 3),
            "l4_plan_gain": round(float(ctx["l4"]["plan"]["totals"]["oil"][1] - ctx["l4"]["plan"]["totals"]["oil"][0]), 3),
            "l4_gl_lift_opt": round(float(ctx["l4"]["gl_summary"]["lift_opt"]), 1),
            "l5_gain": round(float(ctx["l5"]["gain"]), 3),
            "l5_locked": sorted(ctx["l5"]["locked"]),
        },
    }


if __name__ == "__main__":
    out = ROOT / "tests" / "golden" / "baseline.json"
    out.write_text(json.dumps(snapshot(), indent=1, sort_keys=True))
    print(f"golden baseline written to {out}")
