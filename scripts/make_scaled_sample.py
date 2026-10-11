"""Build a larger synthetic GGS for timing tests (PRD F2: 50 wells / 30 days in < 60 s).

The 12-well sample is cloned ``--copies`` times into one network: every copy gets its own wells,
separators, separator and lift-gas meters and tanks (IDs suffixed ``-2``, ``-3`` ...); the dispatch,
gas-disposal and lift-gas header meters stay shared and carry the summed volumes, so all balances
still close.

    python scripts/make_scaled_sample.py --copies 5 --out var/scaled     # 60 wells
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "data" / "sample"


def build(copies: int, out: Path) -> Path:
    net = yaml.safe_load((SAMPLE / "network.yaml").read_text(encoding="utf-8"))
    shared = {net["dispatch"]["meter"], net["lift_gas"]["header_meter"]} | {g["meter"] for g in net["gas_disposal"]}
    out.mkdir(parents=True, exist_ok=True)
    (out / "strapping").mkdir(exist_ok=True)

    def sfx(x: str, k: int) -> str:
        return x if k == 1 else f"{x}-{k}"

    def mid(m, k):
        return m if (m is None or m in shared) else sfx(m, k)

    new = dict(net, wells=[], separators=[], tanks=[],
               meters=[m for m in net["meters"] if m["id"] in shared])
    new["installation"] = dict(net["installation"], name=f"GGS-Demo x{copies} (synthetic, timing)")
    for k in range(1, copies + 1):
        for w in net["wells"]:
            new["wells"].append(dict(w, id=sfx(w["id"], k), separator=sfx(w["separator"], k),
                                     **({"lift_gas_meter": mid(w["lift_gas_meter"], k)} if w.get("lift_gas_meter") else {})))
        for s in net["separators"]:
            new["separators"].append(dict(s, id=sfx(s["id"], k), oil_meter=mid(s.get("oil_meter"), k),
                                          water_meter=mid(s.get("water_meter"), k), gas_meter=mid(s.get("gas_meter"), k)))
        for m in net["meters"]:
            if m["id"] not in shared:
                new["meters"].append(dict(m, id=sfx(m["id"], k)))
        for t in net["tanks"]:
            tid = sfx(t["id"], k)
            shutil.copy(SAMPLE / t["strapping_table"], out / "strapping" / f"{tid}.csv")
            new["tanks"].append(dict(t, id=tid, strapping_table=f"strapping/{tid}.csv"))
    (out / "network.yaml").write_text(yaml.safe_dump(new, sort_keys=False), encoding="utf-8")
    shutil.copy(SAMPLE / "config.yaml", out / "config.yaml")

    def clone(name: str, cols: dict, sum_shared: list | None = None):
        f = SAMPLE / f"{name}.csv"
        if not f.exists():
            return
        df = pd.read_csv(f, dtype=str, keep_default_na=False)
        parts = []
        for k in range(1, copies + 1):
            c = df.copy()
            for col, kind in cols.items():
                if kind == "id":
                    c[col] = c[col].map(lambda v: sfx(v, k) if v else v)
                elif kind == "meter":
                    c[col] = c[col].map(lambda v: mid(v, k) if v else v)
            parts.append(c)
        all_ = pd.concat(parts, ignore_index=True)
        if sum_shared:   # shared meters: one row per key with the summed volume
            key, val = sum_shared
            is_sh = all_[key[-1]].isin(shared)
            sh = all_[is_sh].copy()
            for v in val:
                sh[v] = pd.to_numeric(sh[v], errors="coerce")
            agg = {v: "sum" for v in val}
            agg.update({c: "first" for c in sh.columns if c not in key + val})
            sh = sh.groupby(key, as_index=False).agg(agg)[all_.columns]
            all_ = pd.concat([all_[~is_sh], sh.astype(str)], ignore_index=True)
        all_.to_csv(out / f"{name}.csv", index=False)

    clone("well_daily", {"well_id": "id"})
    clone("well_static", {"well_id": "id"})
    clone("well_tests", {"test_id": "id", "well_id": "id", "separator_id": "id"})
    clone("well_test_readings", {"test_id": "id"})
    clone("reservoir_surveys", {"well_id": "id"})
    clone("separator_daily", {"separator_id": "id"})
    clone("tank_gauges", {"tank_id": "id"})
    clone("tank_drains", {"tank_id": "id"})
    clone("meter_daily", {"meter_id": "meter"}, (["date", "meter_id"], ["volume"]))
    df = pd.read_csv(SAMPLE / "dispatch_daily.csv")
    df["gross_m3"] = df["gross_m3"] * copies
    df.to_csv(out / "dispatch_daily.csv", index=False)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--copies", type=int, default=5)
    ap.add_argument("--out", default=str(ROOT / "var" / "scaled"))
    a = ap.parse_args()
    p = build(a.copies, Path(a.out))
    print(f"{a.copies * 12} wells written to {p}")
