"""Behaviour lock: the engine and twin must reproduce tests/golden/baseline.json.

If a change is *intended* to alter results, review the diff, then regenerate with
`python scripts/make_golden.py` and explain the change in the commit message.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


class TestGolden(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from make_golden import snapshot
        cls.base = json.loads((ROOT / "tests" / "golden" / "baseline.json").read_text())
        cls.now = snapshot()

    def test_findings_identical(self):
        self.assertEqual(self.now["findings_by_check"], self.base["findings_by_check"])

    def test_gross_errors_identical(self):
        self.assertEqual(self.now["dvr_eliminated"], self.base["dvr_eliminated"])
        self.assertEqual(self.now["dvr_passed_initial"], self.base["dvr_passed_initial"])

    def test_test_statuses_identical(self):
        self.assertEqual(self.now["tests_status"], self.base["tests_status"])

    def test_volumes_within_tolerance(self):
        for k, v in self.base["totals"].items():
            self.assertAlmostEqual(self.now["totals"][k], v, delta=max(abs(v) * 1e-4, 1e-3), msg=k)
        for k, v in self.base["well_oil_dvr"].items():
            self.assertAlmostEqual(self.now["well_oil_dvr"][k], v, delta=max(abs(v) * 1e-4, 1e-3), msg=k)

    def test_twin_within_tolerance(self):
        b, n = self.base["twin"], self.now["twin"]
        self.assertEqual(n["levels"], b["levels"])
        self.assertEqual(n["l5_locked"], b["l5_locked"])
        for k in ("l3_oil_30d_p50", "l4_plan_gain", "l5_gain"):
            self.assertAlmostEqual(n[k], b[k], delta=max(abs(b[k]) * 0.01, 0.05), msg=k)
        self.assertAlmostEqual(n["l4_gl_lift_opt"], b["l4_gl_lift_opt"], delta=1.0)


if __name__ == "__main__":
    unittest.main()
