"""Network description: wells -> separators -> tanks -> dispatch / gas disposal."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import yaml


@dataclass
class Meter:
    id: str
    phase: str                      # oil | water | gas | liquid
    type: str = "turbine"
    meter_factor: float = 1.0
    u95_pct: Optional[float] = None  # overrides type default
    last_proved: Optional[str] = None
    proving_interval_days: Optional[int] = None
    description: str = ""


@dataclass
class Well:
    id: str
    lift: str = "self_flow"         # self_flow | gas_lift | srp | esp
    separator: str = ""             # normal (group) separator
    choke_correlation: str = "gilbert"
    plunger_in: Optional[float] = None
    stroke_in: Optional[float] = None
    lift_gas_meter: Optional[str] = None
    field_block: str = ""
    # --- static well data for the well-performance model (all optional) ---
    perf_depth_m: Optional[float] = None
    tubing_depth_m: Optional[float] = None
    tubing_id_in: Optional[float] = None
    casing_id_in: Optional[float] = None
    valve_depth_m: Optional[float] = None       # gas-lift operating valve
    pump_depth_m: Optional[float] = None        # SRP / ESP setting depth
    reservoir_pressure_ksc: Optional[float] = None
    reservoir_temp_degc: Optional[float] = None
    bubble_point_ksc: Optional[float] = None
    wht_degc: Optional[float] = None


@dataclass
class Separator:
    id: str
    kind: str = "production"        # production | test
    phases: int = 3
    orientation: str = "horizontal"
    diameter_m: float = 1.5
    length_m: float = 4.5
    liquid_volume_m3: Optional[float] = None   # liquid hold-up at NLL
    shrinkage_factor: float = 0.97             # stock-tank oil / separator oil
    oil_meter: Optional[str] = None            # oil leg (gross liquid if 2-phase)
    water_meter: Optional[str] = None
    gas_meter: Optional[str] = None


@dataclass
class Tank:
    id: str
    strapping: pd.DataFrame
    insulated: bool = False
    shell_alpha_per_c: float = 1.12e-5
    calibration_temp_c: float = 15.0
    safe_fill_mm: Optional[float] = None

    def volume(self, level_mm: float) -> float:
        s = self.strapping
        return float(np.interp(level_mm, s["level_mm"].values, s["volume_m3"].values))

    def m3_per_mm(self, level_mm: float) -> float:
        return (self.volume(level_mm + 5) - self.volume(level_mm - 5)) / 10.0


@dataclass
class Network:
    name: str
    wells: Dict[str, Well]
    separators: Dict[str, Separator]
    meters: Dict[str, Meter]
    tanks: Dict[str, Tank]
    dispatch_meter: Optional[str] = None
    gas_disposal: List[dict] = field(default_factory=list)  # [{meter, role}]
    lift_gas_header_meter: Optional[str] = None
    gas_sg: float = 0.75
    oil_rho15: float = 860.0
    water_rho: float = 1010.0

    @property
    def test_separators(self) -> List[str]:
        return [s.id for s in self.separators.values() if s.kind == "test"]


def load_network(path: str | Path) -> Network:
    path = Path(path)
    with open(path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    base = path.parent

    meters = {m["id"]: Meter(**m) for m in cfg.get("meters", [])}
    wells = {w["id"]: Well(**w) for w in cfg.get("wells", [])}
    static_f = base / cfg.get("well_static", "well_static.csv")
    if static_f.exists():   # tabular well master overrides / completes network.yaml
        st = pd.read_csv(static_f)
        fields = set(Well.__dataclass_fields__) - {"id"}
        for _, r in st.iterrows():
            w = wells.get(str(r["well_id"]))
            if w is None:
                continue
            for c in st.columns:
                if c in fields and pd.notna(r[c]):
                    setattr(w, c, r[c] if isinstance(r[c], str) else float(r[c]))
    seps = {s["id"]: Separator(**s) for s in cfg.get("separators", [])}
    tanks = {}
    for t in cfg.get("tanks", []):
        t = dict(t)
        strap = pd.read_csv(base / t.pop("strapping_table"))
        strap = strap.sort_values("level_mm").reset_index(drop=True)
        tanks[t["id"]] = Tank(strapping=strap, **t)

    inst = cfg.get("installation", {})
    net = Network(
        name=inst.get("name", "Installation"),
        wells=wells, separators=seps, meters=meters, tanks=tanks,
        dispatch_meter=cfg.get("dispatch", {}).get("meter"),
        gas_disposal=cfg.get("gas_disposal", []),
        lift_gas_header_meter=cfg.get("lift_gas", {}).get("header_meter"),
        gas_sg=inst.get("gas_sg", 0.75),
        oil_rho15=inst.get("oil_rho15_kgm3", 860.0),
        water_rho=inst.get("water_rho_kgm3", 1010.0),
    )
    _check(net)
    return net


def _check(net: Network) -> None:
    errs = []
    for w in net.wells.values():
        if w.separator not in net.separators:
            errs.append(f"well {w.id}: unknown separator '{w.separator}'")
        if w.lift == "srp" and (w.plunger_in is None or w.stroke_in is None):
            errs.append(f"well {w.id}: SRP well needs plunger_in and stroke_in")
        if w.lift_gas_meter and w.lift_gas_meter not in net.meters:
            errs.append(f"well {w.id}: unknown lift gas meter '{w.lift_gas_meter}'")
    for s in net.separators.values():
        for m in (s.oil_meter, s.water_meter, s.gas_meter):
            if m and m not in net.meters:
                errs.append(f"separator {s.id}: unknown meter '{m}'")
    for g in net.gas_disposal:
        if g["meter"] not in net.meters:
            errs.append(f"gas disposal: unknown meter '{g['meter']}'")
    if net.dispatch_meter and net.dispatch_meter not in net.meters:
        errs.append(f"dispatch: unknown meter '{net.dispatch_meter}'")
    if errs:
        raise ValueError("Network definition errors:\n  " + "\n  ".join(errs))
