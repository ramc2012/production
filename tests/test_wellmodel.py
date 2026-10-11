"""Well-performance physics: IPR, Beggs & Brill VLP, nodal, fluid level."""
import unittest

from flowval import wellmodel as M


class TestIPR(unittest.TestCase):
    def test_linear_above_pb(self):
        self.assertAlmostEqual(M.ipr_rate(80, 100, 2.0, 50), 40.0, places=6)

    def test_vogel_continuity_and_inverse(self):
        pr, j, pb = 120.0, 1.5, 90.0
        q_b = M.ipr_rate(pb, pr, j, pb)
        self.assertAlmostEqual(q_b, j * (pr - pb), places=6)
        for pwf in (100, 60, 20):
            q = M.ipr_rate(pwf, pr, j, pb)
            self.assertAlmostEqual(M.ipr_pwf(q, pr, j, pb), pwf, places=4)
        self.assertGreater(M.ipr_rate(10, pr, j, pb), M.ipr_rate(60, pr, j, pb))

    def test_calibrate_pi(self):
        j = M.calibrate_pi(30.0, 60.0, 120.0, 90.0)
        self.assertAlmostEqual(M.ipr_rate(60.0, 120.0, j, 90.0), 30.0, places=6)


class TestVLP(unittest.TestCase):
    def setUp(self):
        self.fl = M.Fluid(gor=80, wc=0.4, t_res_c=75)
        self.g = M.WellGeom(perf_depth_m=1300, tubing_id_in=1.995, wht_c=35)

    def test_static_liquid_column(self):
        # q = 0: pure liquid column ~ rho g h
        p = M.vlp_pwf(0.0, 0.0, self.fl, self.g)
        self.assertTrue(100 < p < 135)

    def test_round_trip(self):
        pwf = M.vlp_pwf(20.0, 40.0, self.fl, self.g)
        self.assertAlmostEqual(M.vlp_thp(pwf, 40.0, self.fl, self.g), 20.0, delta=0.05)

    def test_gas_lift_lightens_column(self):
        g = M.WellGeom(perf_depth_m=1300, tubing_id_in=1.995, valve_depth_m=1000)
        self.assertLess(M.vlp_pwf(20, 40, self.fl, g, 8000), M.vlp_pwf(20, 40, self.fl, g, 0))

    def test_nodal_recovers_calibration_point(self):
        g = M.WellGeom(perf_depth_m=1300, tubing_id_in=1.995, valve_depth_m=1000)
        fl = M.Fluid(gor=60, wc=0.6, t_res_c=75)
        pwf = M.vlp_pwf(22, 45, fl, g, 9000)
        pr, pb = pwf + 20, fl.bubble_point_ksc()
        j = M.calibrate_pi(45, pwf, pr, pb)
        self.assertAlmostEqual(M.nodal_rate(22, pr, j, pb, fl, g, 9000), 45, delta=0.5)
        # higher THP -> less rate
        self.assertLess(M.nodal_rate(30, pr, j, pb, fl, g, 9000), 45)

    def test_dead_well(self):
        g = M.WellGeom(perf_depth_m=1300, tubing_id_in=1.995)
        fl = M.Fluid(gor=20, wc=0.8, t_res_c=75)
        self.assertEqual(M.nodal_rate(40, 90, 1.0, 30, fl, g), 0.0)


class TestFluidLevel(unittest.TestCase):
    def test_deeper_level_lower_pwf(self):
        g = M.WellGeom(perf_depth_m=1300, pump_depth_m=1150)
        fl = M.Fluid(gor=25, wc=0.5)
        self.assertGreater(M.pwf_from_fluid_level(4, 600, g, fl), M.pwf_from_fluid_level(4, 1000, g, fl))

    def test_pump_off(self):
        g = M.WellGeom(perf_depth_m=1300, pump_depth_m=1150)
        fl = M.Fluid(gor=25, wc=0.5)
        p = M.pwf_from_fluid_level(4, 1150, g, fl)
        self.assertTrue(4 < p < 25)


class TestPVT(unittest.TestCase):
    def test_standing_consistency(self):
        fl = M.Fluid(gor=100, t_res_c=80)
        pb = fl.bubble_point_ksc()
        self.assertAlmostEqual(fl.rs(pb - 1e-6, 80), 100, delta=1.0)
        self.assertLess(fl.rs(pb / 2, 80), 100)
        self.assertTrue(1.0 < fl.bo(fl.rs(pb / 2, 80), 80) < 1.5)


if __name__ == "__main__":
    unittest.main()
