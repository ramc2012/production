"""Acceptance test: every fault planted in the synthetic dataset must be detected."""
import unittest
from pathlib import Path

from flowval.engine import run

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "data" / "sample"


class TestPlantedFaults(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = run(SAMPLE / "network.yaml", SAMPLE, SAMPLE / "config.yaml")
        cls.f = cls.res.findings_daily

    def has(self, obj, check, date=None, severity=None):
        m = (self.f["object_id"] == obj) & (self.f["check"] == check)
        if date:
            m &= self.f["date"] == date
        if severity:
            m &= self.f["severity"] == severity
        return bool(m.any())

    def test_f1_gas_meter_drift(self):
        self.assertTrue(self.has("FT-123", "systematic_bias", severity="ALARM"))
        days = self.f[(self.f["object_id"] == "FT-123") & (self.f["check"] == "gross_error")]["date"]
        self.assertGreaterEqual(days.nunique(), 12)
        self.assertGreaterEqual(days.min(), "2026-09-15")   # no false alarms before the drift

    def test_f2_frozen_thp(self):
        self.assertTrue(self.has("ANK-103", "frozen"))

    def test_f3_water_breakthrough(self):
        self.assertTrue(self.has("ANK-106", "test_change", "2026-09-20", "ALARM"))
        t = self.res.tests.set_index("test_id")
        self.assertEqual(t.loc["WT-260920-106", "status"], "PROVISIONAL")

    def test_f4_srp_ve(self):
        t = self.res.tests.set_index("test_id")
        self.assertEqual(t.loc["WT-260910-110", "status"], "REJECTED")

    def test_f5_short_test(self):
        t = self.res.tests.set_index("test_id")
        self.assertEqual(t.loc["WT-260902-102", "status"], "REJECTED")

    def test_f6_stale_test(self):
        self.assertTrue(self.has("ANK-111", "test_age", severity="ALARM"))

    def test_f7_dip_error(self):
        r = self.f[(self.f["check"] == "dip_error")]
        self.assertEqual(list(r["object_id"]), ["T-2"])
        self.assertAlmostEqual(float(r["value"].iloc[0]), 150 * 0.03848, delta=1.5)

    def test_f8_shut_in_reported_flowing(self):
        self.assertTrue(self.has("ANK-104", "nodal_no_flow", "2026-09-26", "ALARM")
                        or self.has("ANK-104", "thp_high", "2026-09-26", "ALARM"))
        w = self.res.wells.set_index(["date", "well_id"])
        self.assertLess(w.loc[("2026-09-26", "ANK-104"), "dvr_oil"], 0.5 * w.loc[("2026-09-26", "ANK-104"), "theo_oil"])

    def test_f9_missing_flare(self):
        self.assertTrue(self.has("FT-302", "missing", "2026-09-10"))

    def test_f10_proving_overdue(self):
        self.assertTrue(self.has("FT-201", "proving_overdue"))

    def test_f11_unstable_test(self):
        t = self.res.tests.set_index("test_id")
        self.assertEqual(t.loc["WT-260921-107", "status"], "REJECTED")

    def test_f12_retention(self):
        self.assertTrue(self.has("GS-2", "retention"))

    def test_f13_missing_bsw(self):
        self.assertTrue(self.has("GS-1", "no_bsw", "2026-09-03"))

    def test_f14_bean_up_vfm(self):
        self.assertTrue(self.has("ANK-101", "choke_change"))
        self.assertTrue(self.has("ANK-101", "vfm_deviation"))

    def test_f15_srp_pump_leak(self):
        self.assertTrue(self.has("ANK-112", "fluid_level_rise"))
        self.assertTrue(self.has("ANK-112", "vfm_deviation"))

    def test_f16_gas_lift_valve_change(self):
        self.assertTrue(self.has("ANK-108", "inj_pressure_shift"))

    def test_f17_low_fillage(self):
        self.assertTrue(self.has("ANK-111", "low_fillage"))

    def test_water_breakthrough_caught_by_wellhead_sample(self):
        self.assertTrue(self.has("ANK-106", "wc_sample_change"))
        iso = self.f[(self.f["object_id"] == "ANK-106") & (self.f["check"] == "gross_error")]
        self.assertTrue(len(iso) and iso["date"].min() <= "2026-09-17")

    def test_pi_calibration_matches_truth_for_srp(self):
        t = self.res.tests
        pi = t[(t["well_id"] == "ANK-109") & (t["status"] == "ACCEPTED")]["pi"].dropna()
        self.assertAlmostEqual(float(pi.median()), 0.35, delta=0.04)

    def test_well_parameters_improve_allocation(self):
        import pandas as pd
        truth = pd.read_csv(SAMPLE / "_truth_well_daily.csv")
        w = self.res.wells.merge(truth, on=["date", "well_id"], suffixes=("", "_true")).dropna(subset=["dvr_oil", "prop_oil"])
        err_dvr = (w["dvr_oil"] - w["oil_true"]).abs().mean()
        err_prop = (w["prop_oil"] - w["oil_true"]).abs().mean()
        self.assertLess(err_dvr, 0.85 * err_prop)

    def test_clean_period_passes(self):
        d = self.res.dvr_days
        early = d[d["date"] <= "2026-09-11"]
        self.assertTrue(early["passed_initial"].all())

    def test_no_false_well_test_alarms(self):
        bad = self.f[(self.f["layer"] == "well_test") & (self.f["severity"] == "ALARM")]
        expected = {"WT-260902-102", "WT-260910-110", "WT-260920-106", "WT-260921-107"}
        found = {m.split("]")[0].strip("[") for m in bad["message"]}
        self.assertEqual(found, expected)


if __name__ == "__main__":
    unittest.main()
