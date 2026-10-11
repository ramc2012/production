"""Fluid property and petroleum-measurement corrections.

References
----------
* CTL: ASTM D1250 / API MPMS Ch.11.1, Table 54A (crude oils, 15 degC base):
    alpha15 = K0 / rho15**2 (+ K1 / rho15),  K0 = 613.9723, K1 = 0 for crude
    CTL = exp(-alpha15 * dT * (1 + 0.8 * alpha15 * dT)),  dT = T - 15
  (The 2004 revision uses a 60 degF base with K0 = 341.0957 plus an ITS-90
  temperature shift; for 15 degC metric work the 54A form is within the
  rounding of the printed tables.)
* CTSh: API MPMS Ch.12.1.1 - CTSh = 1 + 2*a*dTs + a**2*dTs**2,
  shell temperature Ts = TL (insulated) or (7*TL + TA)/8 (uninsulated).
* Gas Z factor: Papay (1968) explicit correlation with Sutton (1985)
  pseudo-critical properties from gas specific gravity.
"""
from __future__ import annotations

import math

from . import units as U

K0_CRUDE_54A = 613.9723
K1_CRUDE_54A = 0.0
STEEL_ALPHA_PER_C = 1.12e-5  # carbon steel linear expansion, 1/degC


# ---------------------------------------------------------------------------
# Liquids
# ---------------------------------------------------------------------------
def api_gravity(rho15: float) -> float:
    sg = rho15 / U.WATER_DENSITY_15C
    return 141.5 / sg - 131.5


def ctl_54a(rho15: float, t_c: float, k0: float = K0_CRUDE_54A,
            k1: float = K1_CRUDE_54A) -> float:
    """Volume correction factor (observed temperature -> 15 degC)."""
    alpha = k0 / rho15 ** 2 + k1 / rho15
    dt = t_c - 15.0
    return math.exp(-alpha * dt * (1.0 + 0.8 * alpha * dt))


def rho15_from_observed(rho_obs: float, t_obs_c: float, iters: int = 30) -> float:
    """Density at 15 degC from an observed (e.g. hydrometer) density."""
    rho15 = rho_obs
    for _ in range(iters):
        new = rho_obs / ctl_54a(rho15, t_obs_c)
        if abs(new - rho15) < 1e-6:
            return new
        rho15 = new
    return rho15


def shell_temperature(t_liquid_c: float, t_ambient_c: float, insulated: bool) -> float:
    if insulated or t_ambient_c is None or (isinstance(t_ambient_c, float) and math.isnan(t_ambient_c)):
        return t_liquid_c
    return (7.0 * t_liquid_c + t_ambient_c) / 8.0


def ctsh(t_liquid_c: float, t_ambient_c: float, insulated: bool,
         alpha: float = STEEL_ALPHA_PER_C, t_ref_c: float = 15.0) -> float:
    dts = shell_temperature(t_liquid_c, t_ambient_c, insulated) - t_ref_c
    return 1.0 + 2.0 * alpha * dts + (alpha * dts) ** 2


# ---------------------------------------------------------------------------
# Gas
# ---------------------------------------------------------------------------
def sutton_pseudocritical(gas_sg: float):
    """Return (Ppc psia, Tpc degR)."""
    ppc = 756.8 - 131.07 * gas_sg - 3.6 * gas_sg ** 2
    tpc = 169.2 + 349.5 * gas_sg - 74.0 * gas_sg ** 2
    return ppc, tpc


def z_factor(p_ksc_g: float, t_c: float, gas_sg: float) -> float:
    ppc, tpc = sutton_pseudocritical(gas_sg)
    ppr = U.ksc_g_to_psia(p_ksc_g) / ppc
    tpr = U.c_to_rankine(t_c) / tpc
    z = 1.0 - 3.52 * ppr / (10 ** (0.9813 * tpr)) + 0.274 * ppr ** 2 / (10 ** (0.8157 * tpr))
    return max(z, 0.3)


def gas_density(p_ksc_g: float, t_c: float, gas_sg: float) -> float:
    """Gas density at operating conditions, kg/m3."""
    p_pa = U.ksc_g_to_kpa_abs(p_ksc_g) * 1000.0
    m = gas_sg * U.M_AIR / 1000.0
    return p_pa * m / (z_factor(p_ksc_g, t_c, gas_sg) * U.R_GAS * U.c_to_k(t_c))


def actual_gas_volume(std_m3: float, p_ksc_g: float, t_c: float, gas_sg: float,
                      t_std_c: float = 15.0, p_std_kpa: float = 101.325) -> float:
    """Convert standard volume to actual volume at operating conditions."""
    p_abs = U.ksc_g_to_kpa_abs(p_ksc_g)
    return std_m3 * (p_std_kpa / p_abs) * (U.c_to_k(t_c) / U.c_to_k(t_std_c)) * \
        z_factor(p_ksc_g, t_c, gas_sg)


# ---------------------------------------------------------------------------
# Well-performance physics used for cross-checks
# ---------------------------------------------------------------------------
# Gilbert-type critical-flow choke correlations:
#   THP[psia] = A * q[STB/d gross liquid] * GLR[scf/STB]**B / S[1/64 in]**C
CHOKE_CORRELATIONS = {
    "gilbert": (10.00, 0.546, 1.89),
    "ros": (17.40, 0.500, 2.00),
    "baxendell": (9.56, 0.546, 1.93),
    "achong": (3.82, 0.650, 1.88),
}


def choke_thp_psia(q_bpd: float, glr_scf_bbl: float, choke_64: float,
                   correlation: str = "gilbert", cal: float = 1.0) -> float:
    a, b, c = CHOKE_CORRELATIONS[correlation]
    return cal * a * q_bpd * max(glr_scf_bbl, 1.0) ** b / choke_64 ** c


def choke_rate_bpd(thp_psia: float, glr_scf_bbl: float, choke_64: float,
                   correlation: str = "gilbert", cal: float = 1.0) -> float:
    a, b, c = CHOKE_CORRELATIONS[correlation]
    return thp_psia * choke_64 ** c / (cal * a * max(glr_scf_bbl, 1.0) ** b)


def srp_displacement_m3d(stroke_in: float, spm: float, plunger_in: float) -> float:
    """Sucker-rod pump theoretical displacement: PD[bbl/d] = 0.1166 S N D^2."""
    return 0.1166 * stroke_in * spm * plunger_in ** 2 * U.BBL_TO_M3
