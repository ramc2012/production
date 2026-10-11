"""Unit tests for the standard calculations against published reference values."""
import math
import unittest

import numpy as np

from flowval import fluids as F
from flowval import stats as S
from flowval import units as U


class TestStats(unittest.TestCase):
    def test_norm_ppf(self):
        self.assertAlmostEqual(S.norm_ppf(0.975), 1.959963985, places=8)
        self.assertAlmostEqual(S.norm_ppf(0.5), 0.0, places=10)

    def test_chi2(self):
        # tabulated chi-square 95 % quantiles
        for k, q in ((1, 3.841459), (5, 11.070498), (10, 18.307038), (13, 22.362032), (30, 43.772972)):
            self.assertAlmostEqual(S.chi2_ppf(0.95, k), q, places=5)
        self.assertAlmostEqual(S.chi2_sf(3.841459, 1), 0.05, places=6)

    def test_propagate_linear(self):
        y, cov = S.propagate(lambda x: [x[0] + 2 * x[1]], [1.0, 1.0], [0.1, 0.2])
        self.assertAlmostEqual(cov[0, 0], 0.1 ** 2 + (2 * 0.2) ** 2, places=8)


class TestPetroleumMeasurement(unittest.TestCase):
    def test_ctl_table54a(self):
        # ASTM D1250 Table 54A (crude): 850 kg/m3 @ 30 degC -> 0.9872
        self.assertAlmostEqual(F.ctl_54a(850.0, 30.0), 0.9872, places=4)
        # heavier oil expands less; hotter oil shrinks more to 15 degC
        self.assertGreater(F.ctl_54a(950.0, 50.0), F.ctl_54a(850.0, 50.0))
        self.assertLess(F.ctl_54a(850.0, 60.0), F.ctl_54a(850.0, 30.0))
        self.assertAlmostEqual(F.ctl_54a(860.0, 15.0), 1.0, places=12)

    def test_rho15_roundtrip(self):
        obs = 860.0 * F.ctl_54a(860.0, 40.0)
        self.assertAlmostEqual(F.rho15_from_observed(obs, 40.0), 860.0, places=4)

    def test_ctsh(self):
        # uninsulated: Ts = (7*40 + 30)/8 = 38.75 ; CTSh = 1 + 2a dT + (a dT)^2
        a, dt = 1.12e-5, 38.75 - 15.0
        self.assertAlmostEqual(F.ctsh(40, 30, False), 1 + 2 * a * dt + (a * dt) ** 2, places=12)
        self.assertAlmostEqual(F.ctsh(40, 30, True), 1 + 2 * a * 25 + (a * 25) ** 2, places=12)

    def test_api(self):
        self.assertAlmostEqual(F.api_gravity(U.WATER_DENSITY_15C), 10.0, places=6)


class TestWellPhysics(unittest.TestCase):
    def test_gilbert_petrowiki_example(self):
        # PetroWiki "Flow through chokes": 400 STB/d, GLR 800 scf/STB, 12/64 in -> Gilbert 1,405 psia, Ros 1,371
        self.assertAlmostEqual(F.choke_thp_psia(400, 800, 12, "gilbert"), 1405, delta=3)
        self.assertAlmostEqual(F.choke_thp_psia(400, 800, 12, "ros"), 1371, delta=6)

    def test_choke_inverse(self):
        p = F.choke_thp_psia(300, 600, 16, "gilbert", 1.07)
        self.assertAlmostEqual(F.choke_rate_bpd(p, 600, 16, "gilbert", 1.07), 300, places=8)

    def test_srp_displacement(self):
        # PD = 0.1166 * 64 in * 8 spm * 1.75^2 = 182.8 bbl/d
        self.assertAlmostEqual(F.srp_displacement_m3d(64, 8, 1.75) * U.M3_TO_BBL, 182.83, places=1)

    def test_z_factor_range(self):
        z = F.z_factor(3.5, 40.0, 0.75)
        self.assertTrue(0.95 < z < 1.0)


if __name__ == "__main__":
    unittest.main()
