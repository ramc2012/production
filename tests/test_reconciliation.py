"""DVR engine tests on small networks with known answers."""
import unittest

import numpy as np

from flowval.reconciliation import DVRProblem


def _m(p, name, v, rel):
    p.measure(dict(tag=name, kind="meter", vars=[name], values=[v], cov=np.array([[(rel * v) ** 2]])))


class TestDVR(unittest.TestCase):
    def test_consistent_data_unchanged(self):
        p = DVRProblem()
        for n, v in (("A", 100.0), ("B", 60.0), ("C", 40.0)):
            _m(p, n, v, 0.01)
        p.constraint("node", {"A": 1, "B": -1, "C": -1})
        r = p.solve()
        self.assertTrue(r.passed_initial)
        self.assertAlmostEqual(r.global_stat, 0.0, places=8)

    def test_weighted_adjustment(self):
        # A precise (0.1 %), B and C loose (5 %): imbalance is absorbed by B and C
        p = DVRProblem()
        _m(p, "A", 100.0, 0.001)
        _m(p, "B", 62.0, 0.05)
        _m(p, "C", 41.0, 0.05)
        p.constraint("node", {"A": 1, "B": -1, "C": -1})
        r = p.solve()
        rec = dict(zip(r.variables["var"], r.variables["rec"]))
        self.assertAlmostEqual(rec["A"], 100.0, delta=0.05)
        self.assertAlmostEqual(rec["B"] + rec["C"], rec["A"], places=6)

    def test_unmeasured_estimated(self):
        p = DVRProblem()
        _m(p, "A", 100.0, 0.01)
        _m(p, "B", 60.0, 0.01)
        p.constraint("node", {"A": 1, "B": -1, "C": -1})
        r = p.solve()
        rec = dict(zip(r.variables["var"], r.variables["rec"]))
        self.assertAlmostEqual(rec["C"], 40.0, places=6)
        self.assertEqual(r.dof, 0)

    def test_gross_error_isolated(self):
        # A = B + C ; C = D + E ; D = F + G with meter D reading +25 %
        p = DVRProblem()
        for n, v in (("A", 100.5), ("B", 39.8), ("C", 60.3), ("D", 31.25), ("E", 35.1), ("F", 10.05), ("G", 14.9)):
            _m(p, n, v, 0.01)
        p.constraint("n1", {"A": 1, "B": -1, "C": -1})
        p.constraint("n2", {"C": 1, "D": -1, "E": -1})
        p.constraint("n3", {"D": 1, "F": -1, "G": -1})
        r = p.solve()
        self.assertFalse(r.passed_initial)
        self.assertEqual([e["tag"] for e in r.eliminated], ["D"])
        self.assertTrue(r.passed)
        self.assertAlmostEqual(r.eliminated[0]["est"][0], 25.0, delta=0.6)

    def test_indistinguishable_reported_as_ambiguous(self):
        # D and E sit only in the same single balance -> cannot be told apart
        p = DVRProblem()
        for n, v in (("C", 60.0), ("D", 30.0), ("E", 45.0)):
            _m(p, n, v, 0.01)
        p.constraint("n2", {"C": 1, "D": -1, "E": -1})
        r = p.solve()
        self.assertFalse(r.passed)
        self.assertEqual(r.eliminated, [])
        self.assertEqual(len(r.ambiguous), 1)
        self.assertGreaterEqual(len(r.ambiguous[0]["candidates"]), 2)


if __name__ == "__main__":
    unittest.main()
