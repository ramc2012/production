"""Digital-twin levels: forecasts, twin physics, optimiser and guarded closed loop."""
import copy
import unittest
from pathlib import Path

import numpy as np

from flowval.engine import run
from flowval.twin import predict as P
from flowval.twin import prescribe as X
from flowval.twin.autonomy import Supervisor, make_plant, trust_gate
from flowval.twin.levels import assess, level_status
from flowval.twin.model import build_twin, synchronise

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample"


class TestArps(unittest.TestCase):
    def test_recovers_exponential_decline(self):
        t = np.arange(0, 120, 4.0)
        q = 50 * np.exp(-0.002 * t)
        qi, D, b, s = P.fit_arps(t, q)
        self.assertAlmostEqual(qi, 50, delta=0.5)
        self.assertAlmostEqual(D, 0.002, delta=2e-4)


class TestTwin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = run(SAMPLE / "network.yaml", SAMPLE, SAMPLE / "config.yaml")
        cls.twin = build_twin(cls.res, SAMPLE)

    def test_l1_synchronised_to_reconciled_rates(self):
        last = self.res.wells[self.res.wells["date"] == self.res.dates[-1]].set_index("well_id")
        for wid, w in self.twin.wells.items():
            if not (w.modelled and w.on):
                continue
            q = (last.loc[wid, "dvr_oil"] + last.loc[wid, "dvr_water"]) / (last.loc[wid, "hours_on"] / 24)
            self.assertAlmostEqual(w.solve()["L"], q, delta=0.02 * q + 0.2, msg=wid)

    def test_physics_directions(self):
        w = self.twin.wells["ANK-101"]
        self.assertGreater(w.solve(choke=w.choke + 2)["L"], w.solve()["L"])
        s = self.twin.wells["ANK-109"]
        self.assertLess(s.solve(spm=s.spm + 1)["submergence"], s.solve()["submergence"])

    def test_synchronise_hits_target(self):
        t = copy.deepcopy(self.twin)
        target = t.wells["ANK-105"].solve()["L"] * 1.15
        synchronise(t.wells, {"ANK-105": target})
        self.assertAlmostEqual(t.wells["ANK-105"].solve()["L"], target, delta=0.02 * target)

    def test_l3_forecasts_and_lookahead(self):
        ws, fc = P.well_forecasts(self.res, 90)
        self.assertEqual(len(ws), len(self.res.network.wells))
        self.assertTrue((fc["p10"] <= fc["p50"]).all() and (fc["p50"] <= fc["p90"]).all())
        la = P.lookahead_summary(P.lookahead_scenarios(self.twin, 72), self.twin)
        normal, outage = la.iloc[0], la.iloc[1]
        self.assertGreater(outage["max_fill"], normal["max_fill"])
        drift = P.meter_drift(self.res)
        self.assertIn("FT-123", set(drift["tag"]))

    def test_l4_gas_lift_within_budget_and_not_worse(self):
        gl, s = X.optimise_gas_lift(self.twin, self.res.cfg)
        self.assertLessEqual(s["lift_opt"], s["budget"] + 1)
        self.assertGreaterEqual(s["oil_opt"], s["oil_now"] - 1e-6)

    def test_l4_srp_respects_pump_health(self):
        sr = X.srp_speed(self.twin, self.res.cfg, self.res.findings_daily).set_index("well_id")
        self.assertIn("repair pump", sr.loc["ANK-112", "action"])
        self.assertIn("gas interference", sr.loc["ANK-111", "action"])

    def test_l5_advisory_changes_nothing(self):
        plant = make_plant(self.twin, self.res.cfg)
        before = plant.setpoints()
        out = Supervisor(copy.deepcopy(self.twin), plant, self.res, mode="advisory").run(cycles=2)
        self.assertEqual(plant.setpoints(), before)
        self.assertEqual(out["gain"], 0.0)

    def test_l5_autonomous_sim_safe_and_better(self):
        plant = make_plant(self.twin, self.res.cfg)
        start = copy.deepcopy(plant.setpoints())
        out = Supervisor(copy.deepcopy(self.twin), plant, self.res, mode="autonomous_sim").run()
        a = self.res.cfg["twin"]["autonomy"]
        self.assertGreater(out["gain"], 0)
        for wid, sp in plant.setpoints().items():
            if start[wid]["choke"] == start[wid]["choke"]:
                self.assertLessEqual(abs(sp["choke"] - start[wid]["choke"]), a["max_total_choke_64"] + 1e-9)
            self.assertLessEqual(abs(sp["lift_gas"] - start[wid]["lift_gas"]), a["max_total_gl_sm3d"] + 1e-6)
        for wid in trust_gate(self.res)["locked_wells"]:
            self.assertEqual(plant.setpoints()[wid], start[wid])
        self.assertLessEqual(plant.steady()["lift"], plant.lift_available + 1)

    def test_l5_rolls_back_a_move_that_hurts(self):
        plant = make_plant(self.twin, self.res.cfg)
        w = plant.wells["ANK-103"]
        real = w.solve

        def bad(**kw):   # this plant well loses rate when the choke is opened (e.g. sand / coning)
            r = real(**kw)
            if kw.get("choke", w.choke) > 17:
                r = dict(r, oil=r["oil"] * 0.7, L=r["L"] * 0.7)
            return r
        w.solve = bad
        sv = Supervisor(copy.deepcopy(self.twin), plant, self.res, mode="autonomous_sim")
        out = sv.run(cycles=3)
        log = out["log"]
        self.assertTrue(((log["well_id"] == "ANK-103") & (log["event"] == "rolled back")).any())
        self.assertEqual(plant.wells["ANK-103"].choke, 17.0)

    def test_levels_assessment(self):
        ws, fc = P.well_forecasts(self.res, 30)
        l3 = dict(wells=ws, drift=P.meter_drift(self.res))
        gl, s = X.optimise_gas_lift(self.twin, self.res.cfg)
        l4 = dict(gl_summary=s, chokes=X.choke_whatifs(self.twin, self.res.cfg), srp=X.srp_speed(self.twin, self.res.cfg))
        l5 = dict(gain=1.0, log=__import__("pandas").DataFrame(columns=["event"]))
        lv = level_status(assess(self.res, self.twin, l3, l4, l5)).set_index("level")
        self.assertEqual(lv.loc[0, "status"], "achieved")
        self.assertEqual(lv.loc[2, "status"], "achieved")
        self.assertEqual(lv.loc[5, "status"], "partial")


if __name__ == "__main__":
    unittest.main()
