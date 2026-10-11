"""Load and schema-check the CSV inputs (see docs/DATA_DICTIONARY.md)."""
from __future__ import annotations

from pathlib import Path
from typing import Dict

import numpy as np
import pandas as pd

SCHEMAS = {
    "well_daily": {
        "required": ["date", "well_id", "hours_on"],
        "optional": ["status", "test_hours", "thp_ksc", "flp_ksc", "chp_ksc",
                     "choke_64ths", "lift_gas_sm3", "spm", "stroke_in", "remarks",
                     "wht_degc", "inj_pressure_ksc", "dfl_m", "pump_fillage_pct",
                     "pip_ksc", "fbhp_ksc", "esp_hz", "sample_bsw_pct"],
    },
    "well_tests": {
        "required": ["test_id", "well_id", "test_date", "duration_h", "separator_id",
                     "oil_m3", "water_m3", "gas_sm3"],
        "optional": ["oil_leg_bsw_pct", "lift_gas_sm3", "thp_ksc", "flp_ksc", "chp_ksc",
                     "choke_64ths", "sep_pressure_ksc", "sep_temp_degc", "spm",
                     "stroke_in", "sample_bsw_pct", "shrinkage_factor", "remarks",
                     "wht_degc", "inj_pressure_ksc", "dfl_m", "pump_fillage_pct", "pip_ksc", "fbhp_ksc"],
    },
    "well_test_readings": {
        "required": ["test_id", "hour", "liquid_m3"],
        "optional": ["gas_sm3", "thp_ksc"],
    },
    "meter_daily": {
        "required": ["date", "meter_id", "volume"],
        "optional": ["hours_in_service"],
    },
    "separator_daily": {
        "required": ["date", "separator_id"],
        "optional": ["pressure_ksc", "temperature_degc", "oil_leg_bsw_pct"],
    },
    "tank_gauges": {
        "required": ["datetime", "tank_id", "level_mm"],
        "optional": ["free_water_mm", "liquid_temp_degc", "ambient_temp_degc",
                     "density_obs_kgm3", "density_obs_temp_degc", "bsw_pct"],
    },
    "tank_drains": {
        "required": ["date", "tank_id", "water_drained_m3"],
        "optional": [],
    },
    "reservoir_surveys": {
        "required": ["date", "well_id", "pr_ksc"],
        "optional": ["survey_type", "remarks"],
    },
    "dispatch_daily": {
        "required": ["date", "meter_id", "gross_m3"],
        "optional": ["temp_degc", "density15_kgm3", "bsw_pct"],
    },
}

OPTIONAL_FILES = {"well_test_readings", "tank_drains", "separator_daily", "reservoir_surveys"}
TEXT_COLUMNS = {"well_id", "test_id", "meter_id", "separator_id", "tank_id",
                "status", "remarks", "survey_type"}


def read_csv_folder(folder: str | Path) -> Dict[str, pd.DataFrame]:
    """Raw CSV templates found in ``folder`` (no typing or schema checks)."""
    folder = Path(folder)
    raw: Dict[str, pd.DataFrame] = {}
    for name in SCHEMAS:
        f = folder / f"{name}.csv"
        if f.exists():
            raw[name] = pd.read_csv(f)
    return raw


def prepare_inputs(raw: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
    """Schema-check and type the input tables, wherever they came from (CSV folder or database)."""
    data: Dict[str, pd.DataFrame] = {}
    problems = []
    for name, schema in SCHEMAS.items():
        if name not in raw or raw[name] is None:
            if name in OPTIONAL_FILES:
                data[name] = pd.DataFrame(columns=schema["required"] + schema["optional"])
                continue
            problems.append(f"missing required file {name}.csv")
            continue
        df = raw[name].copy()
        df.columns = [str(c).strip() for c in df.columns]
        miss = [c for c in schema["required"] if c not in df.columns]
        if miss:
            problems.append(f"{name}.csv: missing columns {miss}")
        for c in schema["optional"]:
            if c not in df.columns:
                df[c] = np.nan
        for c in df.columns:
            if c not in TEXT_COLUMNS:
                df[c] = pd.to_numeric(df[c], errors="coerce") if c not in ("date", "test_date", "datetime") else df[c]
        for c in ("date", "test_date"):
            if c in df.columns:
                df[c] = pd.to_datetime(df[c]).dt.strftime("%Y-%m-%d")
        if "datetime" in df.columns:
            df["datetime"] = pd.to_datetime(df["datetime"])
        data[name] = df
    if problems:
        raise ValueError("Input problems:\n  " + "\n  ".join(problems))
    return data


def load_inputs(folder: str | Path) -> Dict[str, pd.DataFrame]:
    return prepare_inputs(read_csv_folder(folder))
