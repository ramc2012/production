"""Layer 1 - data quality screening of raw daily readings.

Checks: completeness, plausibility ranges, frozen (stuck) values, spikes
(robust median/MAD), cross-signal consistency and meter proving status.
Flagged readings are returned so downstream physics checks can skip them.
"""
from __future__ import annotations

from typing import Dict, List, Set, Tuple

import numpy as np
import pandas as pd

from .findings import FindingLog
from .network import Network

LIVE_WELL_SIGNALS = ["thp_ksc", "chp_ksc", "lift_gas_sm3"]   # FLP is separator-controlled -> often constant


class BadReadings(dict):
    """{(date, object_id, column): reason} - readings excluded from physics checks."""

    def add(self, key, reason: str = "flag"):
        self.setdefault(key, reason)


def _range_checks(df, cols, ranges, id_col, obj_type, log, bad):
    for col in cols:
        if col not in df.columns or col not in ranges:
            continue
        lo, hi = ranges[col]
        s = pd.to_numeric(df[col], errors="coerce")
        mask = s.notna() & ((s < lo) | (s > hi))
        for _, r in df[mask].iterrows():
            log.add(r["date"], "data_quality", "ALARM", obj_type, r[id_col], "range",
                    f"{col} = {r[col]:g} outside plausible range [{lo}, {hi}]",
                    float(r[col]), f"[{lo}, {hi}]", "Check transmitter / manual entry")
            bad.add((r["date"], r[id_col], col), "range")


def _frozen(df, col, id_col, n, obj_type, log, bad, active_mask=None):
    if col not in df.columns:
        return
    d = df if active_mask is None else df[active_mask]
    for oid, g in d.sort_values("date").groupby(id_col):
        v = pd.to_numeric(g[col], errors="coerce").values
        dates = g["date"].values
        run = 1
        for i in range(1, len(v)):
            if not np.isnan(v[i]) and v[i] != 0 and v[i] == v[i - 1]:
                run += 1
            else:
                if run >= n:
                    _emit_frozen(log, bad, obj_type, oid, col, dates[i - run:i], v[i - 1])
                run = 1
        if run >= n:
            _emit_frozen(log, bad, obj_type, oid, col, dates[len(v) - run:], v[-1])


def _emit_frozen(log, bad, obj_type, oid, col, dates, value):
    log.add(str(dates[0]), "data_quality", "WARN", obj_type, oid, "frozen",
            f"{col} frozen at {value:g} for {len(dates)} days ({dates[0]} to {dates[-1]})",
            float(value), None, "Transmitter stuck or value copied forward - verify at site")
    for d in dates:
        bad.add((str(d), oid, col), "frozen")


def _spikes(df, col, id_col, window, k, min_rel, obj_type, log, bad):
    if col not in df.columns:
        return
    for oid, g in df.sort_values("date").groupby(id_col):
        s = pd.to_numeric(g[col], errors="coerce")
        med = s.rolling(window, center=True, min_periods=3).median()
        mad = (s - med).abs().rolling(window, center=True, min_periods=3).median()
        scale = 1.4826 * mad
        dev = (s - med).abs()
        mask = (dev > k * scale) & (dev > min_rel * med.abs()) & s.notna()
        for idx in g.index[mask.values]:
            r = df.loc[idx]
            log.add(r["date"], "data_quality", "WARN", obj_type, oid, "spike",
                    f"{col} = {r[col]:g} deviates from 7-day median {med[idx]:.4g}",
                    float(r[col]), f"k={k} MAD", "Confirm reading; possible entry error")
            bad.add((r["date"], oid, col), "spike")


def run_data_quality(net: Network, data: Dict[str, pd.DataFrame], cfg: dict,
                     dates: List[str]) -> Tuple[FindingLog, "BadReadings"]:
    log = FindingLog()
    bad = BadReadings()
    dq = cfg["data_quality"]
    ranges = dq["ranges"]

    wd = data["well_daily"]
    wd = wd[wd["date"].isin(dates)]
    md = data["meter_daily"]
    md = md[md["date"].isin(dates)]

    # --- completeness --------------------------------------------------------
    have = set(zip(wd["date"], wd["well_id"]))
    for d in dates:
        for w in net.wells:
            if (d, w) not in have:
                log.add(d, "data_quality", "ALARM", "well", w, "missing",
                        "No daily record for well", None, None,
                        "Obtain hours-on / status from field DPR")
    expected_meters = set()
    for s in net.separators.values():
        if s.kind == "production":
            expected_meters |= {m for m in (s.oil_meter, s.water_meter, s.gas_meter) if m}
    expected_meters |= {g["meter"] for g in net.gas_disposal}
    if net.lift_gas_header_meter:
        expected_meters.add(net.lift_gas_header_meter)
    mhave = {(r.date, r.meter_id) for r in md.itertuples() if pd.notna(r.volume)}
    for d in dates:
        for m in sorted(expected_meters):
            if (d, m) not in mhave:
                log.add(d, "data_quality", "ALARM", "meter", m, "missing",
                        "Meter daily total missing - treated as unmeasured in DVR",
                        None, None, "Retrieve totaliser from flow computer / SCADA")
                bad.add((d, m, "volume"), "missing")

    # --- ranges ----------------------------------------------------------------
    _range_checks(wd, ["thp_ksc", "flp_ksc", "chp_ksc", "choke_64ths", "hours_on",
                       "test_hours", "lift_gas_sm3", "spm"], ranges, "well_id", "well", log, bad)
    sd = data["separator_daily"]
    sd = sd[sd["date"].isin(dates)]
    _range_checks(sd, ["pressure_ksc", "temperature_degc", "oil_leg_bsw_pct"], ranges,
                  "separator_id", "separator", log, bad)
    neg = md[pd.to_numeric(md["volume"], errors="coerce") < 0]
    for _, r in neg.iterrows():
        log.add(r["date"], "data_quality", "ALARM", "meter", r["meter_id"], "range",
                f"Negative daily total {r['volume']:g}", float(r["volume"]), ">= 0",
                "Totaliser rollover or reset - correct manually")
        bad.add((r["date"], r["meter_id"], "volume"), "range")

    # --- frozen & spikes -----------------------------------------------------
    on = pd.to_numeric(wd["hours_on"], errors="coerce").fillna(0) > 0
    for col in LIVE_WELL_SIGNALS:
        _frozen(wd, col, "well_id", dq["frozen_days"], "well", log, bad, active_mask=on)
    for col in ["thp_ksc", "flp_ksc"]:
        _spikes(wd[on], col, "well_id", dq["spike_window_days"], dq["spike_mad_k"],
                dq["spike_min_rel"], "well", log, bad)
    sep_meters, test_meters = set(), set()
    for sp in net.separators.values():
        ms = {m for m in (sp.oil_meter, sp.water_meter, sp.gas_meter) if m}
        sep_meters |= ms
        if sp.kind == "test":
            test_meters |= ms
    estimates = {m.id for m in net.meters.values() if "estimate" in m.type}
    md_cont = md[~md["meter_id"].isin(test_meters)]   # test meters vary by design
    _frozen(md_cont, "volume", "meter_id", dq["frozen_days"], "meter", log, bad)
    # separator meters swing with test routing and estimates are noisy -> spike-screen the rest
    md_spk = md[~md["meter_id"].isin(sep_meters | estimates)]
    _spikes(md_spk, "volume", "meter_id", dq["spike_window_days"], dq["spike_mad_k"],
            dq["spike_min_rel"], "meter", log, bad)

    # --- cross-signal consistency ---------------------------------------------
    for _, r in wd.iterrows():
        w = net.wells.get(r["well_id"])
        if w is None:
            log.add(r["date"], "data_quality", "WARN", "well", r["well_id"], "unknown",
                    "Well not in network definition", None, None, "Add to network.yaml")
            continue
        h = r["hours_on"] if pd.notna(r["hours_on"]) else 0
        status = str(r.get("status") or "").upper()
        if status in ("SI", "SHUT-IN", "SHUTIN", "CLOSED") and h > 0:
            log.add(r["date"], "data_quality", "ALARM", "well", w.id, "status_hours",
                    f"Status '{status}' but hours_on = {h:g}", h, "0",
                    "Correct hours-on or status in DPR")
        th = r.get("test_hours")
        if pd.notna(th) and th > h + 1e-6:
            log.add(r["date"], "data_quality", "ALARM", "well", w.id, "test_hours",
                    f"test_hours {th:g} > hours_on {h:g}", th, f"<= {h:g}", "Correct test log")
        if h > 0 and w.lift == "gas_lift":
            lg = r.get("lift_gas_sm3")
            if pd.notna(lg) and lg <= 0:
                log.add(r["date"], "data_quality", "WARN", "well", w.id, "lift_gas_zero",
                        "Gas-lift well reported on-line with zero injection", 0.0, "> 0",
                        "Check injection meter / confirm well is flowing")
        if h > 0 and w.lift in ("self_flow", "gas_lift"):
            thp, flp = r.get("thp_ksc"), r.get("flp_ksc")
            if pd.notna(thp) and pd.notna(flp) and thp <= flp:
                log.add(r["date"], "data_quality", "WARN", "well", w.id, "no_differential",
                        f"THP {thp:g} <= FLP {flp:g} ksc while reported flowing", thp, f"> {flp:g}",
                        "Well may be dead/shut-in; verify hours-on")

    # --- meter proving / calibration ------------------------------------------
    end = pd.Timestamp(dates[-1])
    for m in net.meters.values():
        if m.last_proved and m.proving_interval_days:
            due = pd.Timestamp(m.last_proved) + pd.Timedelta(days=int(m.proving_interval_days))
            if due < end:
                log.add(dates[-1], "data_quality", "WARN", "meter", m.id, "proving_overdue",
                        f"Proving/calibration overdue since {due.date()} (last {m.last_proved})",
                        float((end - due).days), f"{m.proving_interval_days} d interval",
                        "Schedule proving; meter uncertainty widened in DVR")
    return log, bad


def meter_overdue(net: Network, meter_id: str, date: str) -> bool:
    m = net.meters.get(meter_id)
    if not m or not m.last_proved or not m.proving_interval_days:
        return False
    due = pd.Timestamp(m.last_proved) + pd.Timedelta(days=int(m.proving_interval_days))
    return pd.Timestamp(date) > due
