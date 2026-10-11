"""Validation tolerances and default uncertainties.

All limits are configurable from a YAML file (``config.yaml``); values not
given there fall back to the defaults below. Uncertainties are expanded
uncertainties U95 (k = 2) in percent unless the key says otherwise.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import yaml

DEFAULTS: dict = {
    "standard_conditions": {"temperature_degc": 15.0, "pressure_kpa": 101.325},

    "data_quality": {
        "frozen_days": 4,            # identical live reading for N days -> frozen
        "spike_window_days": 7,
        "spike_mad_k": 5.0,          # |x - median| > k * 1.4826 * MAD
        "spike_min_rel": 0.15,       # ...and at least 15 % away from median
        "ranges": {                  # [min, max] plausibility limits
            "thp_ksc": [0, 350], "flp_ksc": [0, 100], "chp_ksc": [0, 350],
            "choke_64ths": [2, 128], "hours_on": [0, 24], "test_hours": [0, 24],
            "lift_gas_sm3": [0, 200000], "spm": [0, 20],
            "pressure_ksc": [0, 100], "temperature_degc": [-5, 120],
            "oil_leg_bsw_pct": [0, 100], "bsw_pct": [0, 100],
            "level_mm": [0, 20000], "liquid_temp_degc": [-5, 95],
        },
    },

    "well_test": {
        "min_duration_h": 4.0,           # shorter -> rejected
        "recommended_duration_h": 8.0,   # shorter -> warning
        "age_warn_days": 30,
        "age_alarm_days": 60,
        "stability_last_hours": 4,
        "stability_cv_warn": 0.10,
        "stability_cv_alarm": 0.20,
        "liquid_change_warn": 0.20,      # vs previous accepted test (fraction)
        "liquid_change_alarm": 0.40,
        "wc_change_warn_pp": 10.0,       # percentage points
        "wc_change_alarm_pp": 20.0,
        "gor_change_warn": 0.30,
        "gor_change_alarm": 0.60,
        "srp_ve_max": 1.05,              # volumetric efficiency > 1 is not physical
        "srp_ve_min": 0.30,
        "wc_sample_vs_meter_pp": 5.0,
        "choke_cal_change_warn": 0.25,
    },

    "vfm": {
        "correlation": "gilbert",
        "critical_flow_ratio": 0.55,     # FLP_abs / THP_abs must be <= this
        "deviation_warn": 0.20,
        "deviation_alarm": 0.40,
        "persist_days": 2,
        "thp_high_ratio": 1.5,           # THP > 1.5 x test THP while 'on'
    },

    "well_model": {
        "enabled": True,
        "use_in_dvr": True,              # add the combined estimate as a well liquid measurement
        "estimator_u95_pct": {           # model uncertainty right after calibration at a test
            "choke": 20.0, "nodal": 15.0, "ipr_fl": 15.0, "ipr_pip": 10.0,
            "ipr_gauge": 8.0, "srp_pd": 20.0,
        },
        "aging_pct_per_day": 0.3,        # model drift since calibration test
        "fl_survey_max_age_days": 7,     # carry an echometer survey forward this long
        "fl_aging_pct_per_day": 1.5,
        "annulus_factor": 0.9,           # gassy-oil gradient factor above the pump
        "default_wht_degc": 35.0,
        "default_reservoir_temp_degc": 75.0,
        "pi_change_warn": 0.25,          # PI change between tests
        "min_submergence_m": 50.0,
        "fillage_warn_pct": 70.0,
        "dfl_rise_warn_m": 100.0,
        "inj_pressure_shift": 0.10,
        "conflict_sigma": 3.0,           # estimators disagreeing beyond this -> diagnosis
        "deviation_warn": 0.20,          # combined estimate vs test-based theoretical
        "deviation_alarm": 0.40,
        "persist_days": 2,
        "sample_max_age_days": 7,        # wellhead BS&W sample carried forward
        "sample_wc_u95_pp": 5.0,
        "sample_aging_pp_per_day": 0.5,
        "sample_vs_test_warn_pp": 10.0,
    },

    "separator": {
        "gas_capacity_warn": 0.80,       # fraction of Souders-Brown limit
        "gas_capacity_alarm": 1.00,
        "k_vertical_ms": 0.055,          # API 12J 0.18 ft/s (lower bound)
        "k_horizontal_10ft_ms": 0.122,   # API 12J 0.40 ft/s at 10 ft length
        "gas_area_fraction_horizontal": 0.5,
        "imbalance_warn": 0.10,          # simple pre-DVR phase imbalance
        "imbalance_alarm": 0.20,
    },

    "tank": {
        "level_sigma_mm": 2.0,
        "free_water_sigma_mm": 3.0,
        "temperature_sigma_c": 0.5,
        "density_sigma_kgm3": 1.0,
        "bsw_sigma_pp": 0.10,
        "drain_u95_pct": 10.0,
    },

    "uncertainty": {
        "meter_u95_pct": {               # by meter type
            "coriolis": 0.5, "pd": 0.5, "turbine": 1.0, "ultrasonic": 1.0,
            "vortex": 2.0, "orifice": 2.0, "venturi": 1.5, "mag": 1.0,
            "estimate": 30.0, "flare_estimate": 25.0,
        },
        "proving_overdue_factor": 2.0,   # widen U95 when proving/calibration overdue
        "shrinkage_u95_pct": 2.0,
        "oil_leg_bsw_u95_pp": 2.0,
        "dispatch_bsw_u95_pp": 0.2,
        "well_liquid_u95_pct": 10.0,     # fresh accepted test
        "well_wc_u95_pp": 4.0,
        "well_gor_u95_pct": 20.0,
        "fresh_test_days": 7,
        "aging_liquid_pct_per_day": 0.5,
        "aging_wc_pp_per_day": 0.2,
        "aging_gor_pct_per_day": 1.0,
        "provisional_factor": 1.5,       # widen for tests needing confirmation
        "no_test_u95_pct": 100.0,
    },

    "dvr": {
        "alpha": 0.05,
        "z_warn": 1.96,
        "max_eliminations": 3,
        "persistent_days": 3,
    },

    "twin": {                            # digital-twin levels 0-5
        "compressor_capacity_sm3d": 50000.0,
        "lift_gas_available_sm3d": 34000.0,
        "fuel_fraction": 0.03,
        "dispatch_rate_m3h": 12.0,
        "dispatch_start_frac": 0.60,
        "dispatch_stop_frac": 0.35,
        "lookahead_hours": 72,
        "forecast_days": 90,
        "gl_min_sm3d": 3000.0,
        "gl_max_sm3d": 14000.0,
        "gl_step_sm3d": 250.0,
        "gl_curve_step_sm3d": 500.0,
        "choke_min_64": 6, "choke_max_64": 32, "choke_step_64": 2,
        "spm_min": 4.0, "spm_max": 10.0,
        "max_drawdown": 0.35,            # sand / coning guard for choke what-ifs
        "target_submergence_m": 100.0,
        "min_gain_pct": 2.0,             # smallest worthwhile recommendation
        "test_capacity_per_day": 1,
        "autonomy": {
            "mode": "autonomous_sim",    # advisory | supervised | autonomous_sim (simulator only)
            "cycles": 12,
            "max_step_gl_sm3d": 1500.0,
            "max_step_choke_64": 2,
            "max_step_spm": 1.0,
            "max_total_gl_sm3d": 3000.0,     # cumulative change allowed per run (then a human review)
            "max_total_choke_64": 4,
            "max_total_spm": 2.0,
            "min_move_gain_m3d": 0.05,       # deadband: ignore moves that gain less (unless they free lift gas)
            "min_gl_saving_sm3d": 500.0,
            "verify_tolerance_pct": 3.0,
            "plant_pi_mismatch_sd": 0.12,
            "plant_noise_pct": 1.5,
            "freeze_on_untrusted": ["lift_header", "lift_gas"],
            "seed": 7,
        },
    },

    "allocation": {
        "reference": "tank",             # proportional AF basis: 'tank' (measured, API 20.1) or 'dvr'
        "af_warn": [0.90, 1.10],
        "af_alarm": [0.80, 1.20],
    },
}


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str | Path | None = None) -> dict:
    if path is None or not Path(path).exists():
        return copy.deepcopy(DEFAULTS)
    with open(path, "r", encoding="utf-8") as fh:
        user = yaml.safe_load(fh) or {}
    return deep_merge(DEFAULTS, user)


def config_hash(cfg: dict) -> str:
    """Stable short hash of an effective configuration (stored with every run)."""
    blob = json.dumps(cfg, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
