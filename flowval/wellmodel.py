"""Well-performance physics: black-oil PVT, IPR, Beggs & Brill tubing flow,
nodal analysis and flowing bottom-hole pressure from fluid level / intake
pressure / gauge.

These models turn the *well parameters that define the flow* (reservoir
pressure, PI, depths, tubing size, THP, CHP, lift gas, fluid level, PIP...)
into an independent daily liquid-rate estimate for each well.

Correlations
------------
* Standing (1947): solution GOR Rs, bubble point Pb, oil FVF Bo
* Beggs & Robinson (1975): dead / live oil viscosity
* Lee, Gonzalez & Eakin (1966): gas viscosity
* Papay Z factor with Sutton pseudo-criticals (see fluids.py)
* Vogel (1968) composite IPR: straight-line PI above Pb, Vogel below
* Beggs & Brill (1973) multiphase pressure gradient (flow-pattern map,
  holdup with inclination correction, two-phase friction factor)
Internal SI; interfaces in ksc(g), m, m3/d, Sm3/m3, degC.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from . import fluids as F
from . import units as U

G = 9.80665
PA_PER_KSC = 98066.5
P_ATM_PA = 101325.0


def ksc_to_pa_abs(p_ksc_g: float) -> float:
    return p_ksc_g * PA_PER_KSC + P_ATM_PA


def pa_abs_to_ksc(p_pa: float) -> float:
    return (p_pa - P_ATM_PA) / PA_PER_KSC


# ---------------------------------------------------------------------------
# Black-oil PVT (Standing etc.) - field-unit correlations wrapped in SI
# ---------------------------------------------------------------------------
@dataclass
class Fluid:
    oil_rho15: float = 860.0       # kg/m3 stock-tank
    gas_sg: float = 0.75
    water_rho: float = 1010.0
    gor: float = 100.0             # formation GOR, Sm3/m3 (solution gas at Pb)
    wc: float = 0.3                # water cut (fraction of stock-tank liquid)
    pb_ksc: Optional[float] = None  # override Standing Pb
    t_res_c: float = 70.0

    @property
    def api(self) -> float:
        return F.api_gravity(self.oil_rho15)

    @property
    def oil_sg(self) -> float:
        return self.oil_rho15 / U.WATER_DENSITY_15C

    def bubble_point_ksc(self, t_c: Optional[float] = None) -> float:
        if self.pb_ksc is not None:
            return self.pb_ksc
        t_f = (self.t_res_c if t_c is None else t_c) * 1.8 + 32
        rsb = max(self.gor, 0.1) * U.SM3_M3_TO_SCF_BBL
        pb = 18.2 * ((rsb / self.gas_sg) ** 0.83 * 10 ** (0.00091 * t_f - 0.0125 * self.api) - 1.4)
        return max(pb / U.KSC_TO_PSI - U.ATM_KSC, 0.0)

    def rs(self, p_ksc_g: float, t_c: float) -> float:
        """Solution GOR, Sm3/m3 (capped at the formation GOR)."""
        pb = self.bubble_point_ksc(t_c)
        if p_ksc_g >= pb:
            return self.gor
        p = U.ksc_g_to_psia(max(p_ksc_g, 0.0))
        t_f = t_c * 1.8 + 32
        rs = self.gas_sg * ((p / 18.2 + 1.4) * 10 ** (0.0125 * self.api - 0.00091 * t_f)) ** 1.2048
        return min(rs / U.SM3_M3_TO_SCF_BBL, self.gor)

    def bo(self, rs_sm3: float, t_c: float) -> float:
        rs = rs_sm3 * U.SM3_M3_TO_SCF_BBL
        t_f = t_c * 1.8 + 32
        return 0.9759 + 0.00012 * (rs * (self.gas_sg / self.oil_sg) ** 0.5 + 1.25 * t_f) ** 1.2

    def oil_density(self, rs_sm3: float, bo: float) -> float:
        # mass of 1 m3 stock-tank oil + dissolved gas, divided by in-situ volume
        gas_mass = rs_sm3 * self.gas_sg * 1.2232   # kg (air 1.2232 kg/Sm3 at 15 C)
        return (self.oil_rho15 + gas_mass) / bo

    def oil_viscosity(self, rs_sm3: float, t_c: float) -> float:
        """cP (Beggs & Robinson)."""
        t_f = max(t_c * 1.8 + 32, 60.0)
        x = 10 ** (3.0324 - 0.02023 * self.api) * t_f ** -1.163
        mu_od = 10 ** x - 1.0
        rs = rs_sm3 * U.SM3_M3_TO_SCF_BBL
        a = 10.715 * (rs + 100) ** -0.515
        b = 5.44 * (rs + 150) ** -0.338
        return max(a * mu_od ** b, 0.2)

    def gas_viscosity(self, rho_g: float, t_c: float) -> float:
        """cP (Lee-Gonzalez-Eakin)."""
        t_r = U.c_to_rankine(t_c)
        m = 28.97 * self.gas_sg
        k = (9.4 + 0.02 * m) * t_r ** 1.5 / (209 + 19 * m + t_r)
        x = 3.5 + 986.0 / t_r + 0.01 * m
        y = 2.4 - 0.2 * x
        return 1e-4 * k * math.exp(x * (rho_g / 1000.0) ** y)


# ---------------------------------------------------------------------------
# Inflow performance
# ---------------------------------------------------------------------------
def ipr_rate(pwf: float, pr: float, j: float, pb: float) -> float:
    """Liquid rate (m3/d) for flowing BHP pwf (ksc g). Composite PI/Vogel."""
    pwf_a, pr_a, pb_a = pwf + U.ATM_KSC, pr + U.ATM_KSC, min(pb, pr) + U.ATM_KSC
    if pwf_a >= pr_a:
        return 0.0
    if pwf_a >= pb_a:
        return j * (pr_a - pwf_a)
    qb = j * (pr_a - pb_a)
    x = pwf_a / pb_a
    return qb + j * pb_a / 1.8 * (1 - 0.2 * x - 0.8 * x * x)


def ipr_pwf(q: float, pr: float, j: float, pb: float) -> float:
    """Inverse IPR by bisection (ksc g); returns -ATM if q exceeds AOF."""
    lo, hi = -U.ATM_KSC, pr
    if q <= 0:
        return pr
    if ipr_rate(lo, pr, j, pb) < q:
        return lo
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if ipr_rate(mid, pr, j, pb) > q:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def calibrate_pi(q: float, pwf: float, pr: float, pb: float) -> float:
    """Liquid PI (m3/d per ksc) that reproduces q at pwf."""
    unit = ipr_rate(pwf, pr, 1.0, pb)
    return q / unit if unit > 0 else float("nan")


# ---------------------------------------------------------------------------
# Beggs & Brill pressure gradient (vertical upward flow)
# ---------------------------------------------------------------------------
_BB = {  # a, b, c, (e, f, g, h) uphill
    "seg": (0.980, 0.4846, 0.0868, (0.011, -3.768, 3.539, -1.614)),
    "int": (0.845, 0.5351, 0.0173, (2.960, 0.305, -0.4473, 0.0978)),
    "dis": (1.065, 0.5824, 0.0609, None),
}


def _hl_pattern(pat, lam, nfr, nlv):
    a, b, c, coef = _BB[pat]
    hl0 = max(a * lam ** b / nfr ** c, lam)
    if coef is None:
        return min(hl0, 1.0)
    e, f, g, h = coef
    arg = e * lam ** f * nlv ** g * nfr ** h
    C = max((1 - lam) * math.log(arg), 0.0) if arg > 0 else 0.0
    psi = 1 + C * (math.sin(math.radians(1.8 * 90)) - math.sin(math.radians(1.8 * 90)) ** 3 / 3)
    return min(max(hl0 * psi, lam), 1.0)


def bb_holdup(lam: float, nfr: float, nlv: float) -> float:
    lam = min(max(lam, 1e-6), 1.0)
    if lam >= 0.999:
        return 1.0
    L1 = 316 * lam ** 0.302
    L2 = 0.0009252 * lam ** -2.4684
    L3 = 0.1 * lam ** -1.4516
    L4 = 0.5 * lam ** -6.738
    if (lam < 0.01 and nfr < L1) or (lam >= 0.01 and nfr < L2):
        return _hl_pattern("seg", lam, nfr, nlv)
    if lam >= 0.01 and L2 <= nfr <= L3:
        A = (L3 - nfr) / (L3 - L2)
        return A * _hl_pattern("seg", lam, nfr, nlv) + (1 - A) * _hl_pattern("int", lam, nfr, nlv)
    if (0.01 <= lam < 0.4 and L3 < nfr <= L1) or (lam >= 0.4 and L3 < nfr <= L4):
        return _hl_pattern("int", lam, nfr, nlv)
    return _hl_pattern("dis", lam, nfr, nlv)


def _darcy_f(re: float, rel_rough: float) -> float:
    if re < 2000:
        return 64.0 / max(re, 1.0)
    return 0.25 / math.log10(rel_rough / 3.7 + 5.74 / re ** 0.9) ** 2


def bb_gradient(p_ksc: float, t_c: float, q_oil: float, q_water: float, free_gas_sm3d: float,
                fl: Fluid, d_m: float, rough_m: float = 4.5e-5, hl_mult: float = 1.0) -> float:
    """dP/dz in ksc per metre for upward vertical flow at (p, T)."""
    rs = fl.rs(p_ksc, t_c)
    bo = fl.bo(rs, t_c)
    rho_o = fl.oil_density(rs, bo)
    qo_is = q_oil * bo / 86400.0
    qw_is = q_water / 86400.0
    gas_std = max(free_gas_sm3d + q_oil * (fl.gor - rs), 0.0)
    qg_is = F.actual_gas_volume(gas_std, max(p_ksc, 0.0), t_c, fl.gas_sg) / 86400.0
    rho_g = F.gas_density(max(p_ksc, 0.0), t_c, fl.gas_sg)
    area = math.pi * d_m ** 2 / 4
    vsl, vsg = (qo_is + qw_is) / area, qg_is / area
    vm = vsl + vsg
    ql = qo_is + qw_is
    rho_l = (rho_o * qo_is + fl.water_rho * qw_is) / ql if ql > 0 else rho_o * (1 - fl.wc) + fl.water_rho * fl.wc
    if vm < 1e-6:   # static column (shut-in): liquid gradient
        return rho_l * G / PA_PER_KSC
    lam = vsl / vm
    nfr = vm ** 2 / (G * d_m)
    sigma = 0.025 * (1 - fl.wc) + 0.060 * fl.wc
    nlv = vsl * (rho_l / (G * sigma)) ** 0.25 if vsl > 0 else 1e-6
    hl = bb_holdup(lam, nfr, max(nlv, 1e-6)) if vsl > 0 else 0.0
    hl = min(max(hl * hl_mult, lam), 1.0)
    rho_s = rho_l * hl + rho_g * (1 - hl)
    rho_n = rho_l * lam + rho_g * (1 - lam)
    wl = ql and qw_is / ql or 0.0
    mu_l = (fl.oil_viscosity(rs, t_c) * (1 - wl) + 0.5 * wl) * 1e-3
    mu_n = mu_l * lam + fl.gas_viscosity(rho_g, t_c) * 1e-3 * (1 - lam)
    re = rho_n * vm * d_m / mu_n
    fn = _darcy_f(re, rough_m / d_m)
    y = lam / hl ** 2 if hl > 0 else 1.0
    if 1.0 < y < 1.2:
        s = math.log(2.2 * y - 1.2)
    else:
        ly = math.log(y) if y > 0 else 0.0
        den = -0.0523 + 3.182 * ly - 0.8725 * ly ** 2 + 0.01853 * ly ** 4
        s = ly / den if abs(den) > 1e-9 else 0.0
    ftp = fn * math.exp(s)
    dpdz = rho_s * G + ftp * rho_n * vm ** 2 / (2 * d_m)
    return dpdz / PA_PER_KSC


# ---------------------------------------------------------------------------
# Well geometry + vertical lift performance
# ---------------------------------------------------------------------------
@dataclass
class WellGeom:
    perf_depth_m: float = 1300.0
    tubing_depth_m: Optional[float] = None
    tubing_id_in: float = 2.441
    casing_id_in: float = 4.892
    valve_depth_m: Optional[float] = None    # gas-lift operating valve
    pump_depth_m: Optional[float] = None     # SRP / ESP
    wht_c: float = 35.0
    hl_mult: float = 1.0                     # holdup multiplier (tuning)

    def diameter(self, z: float) -> float:
        td = self.tubing_depth_m if self.tubing_depth_m is not None else self.perf_depth_m
        return (self.tubing_id_in if z <= td else self.casing_id_in) * 0.0254

    def temp(self, z: float, t_res: float) -> float:
        return self.wht_c + (t_res - self.wht_c) * z / self.perf_depth_m


def _march(p0: float, q_liq: float, fl: Fluid, geom: WellGeom, lift_gas: float, down: bool,
           n: int = 24) -> float:
    q_o, q_w = q_liq * (1 - fl.wc), q_liq * fl.wc
    H = geom.perf_depth_m
    dz = H / n
    p = p0
    zs = [i * dz for i in range(n + 1)]
    if not down:
        zs = zs[::-1]
    for k in range(n):
        z0, z1 = zs[k], zs[k + 1]

        def grad(pp, zz):
            lg = lift_gas if (geom.valve_depth_m is not None and zz <= geom.valve_depth_m) else 0.0
            return bb_gradient(pp, geom.temp(zz, fl.t_res_c), q_o, q_w, lg, fl, geom.diameter(zz),
                               hl_mult=geom.hl_mult)
        sgn = 1.0 if down else -1.0
        g0 = grad(p, z0)
        p_pred = p + sgn * g0 * dz
        g1 = grad(max(p_pred, -0.9), z1)
        p = p + sgn * 0.5 * (g0 + g1) * dz
        if p < -U.ATM_KSC + 0.05:
            return -U.ATM_KSC
    return p


def vlp_pwf(thp: float, q_liq: float, fl: Fluid, geom: WellGeom, lift_gas: float = 0.0) -> float:
    """Flowing BHP (ksc g) at the perforations for wellhead pressure thp."""
    return _march(thp, q_liq, fl, geom, lift_gas, down=True)


def vlp_thp(pwf: float, q_liq: float, fl: Fluid, geom: WellGeom, lift_gas: float = 0.0) -> float:
    """Wellhead pressure (ksc g) reached by marching up from pwf."""
    return _march(pwf, q_liq, fl, geom, lift_gas, down=False)


def nodal_rate(thp: float, pr: float, j: float, pb: float, fl: Fluid, geom: WellGeom,
               lift_gas: float = 0.0, tol: float = 0.02) -> float:
    """Stable operating point (IPR = VLP) for the given THP; 0 if the well cannot flow."""
    aof = ipr_rate(-U.ATM_KSC + 1e-6, pr, j, pb)

    def f(q):
        return ipr_pwf(q, pr, j, pb) - vlp_pwf(thp, q, fl, geom, lift_gas)
    qs = [aof * x for x in (0.01, 0.05, 0.1, 0.2, 0.3, 0.45, 0.6, 0.75, 0.9, 0.99)]
    vals = [f(q) for q in qs]
    idx = None
    for i in range(len(qs) - 1, -1, -1):
        if vals[i] > 0:
            idx = i
            break
    if idx is None:
        return 0.0
    if idx == len(qs) - 1:
        return qs[-1]
    lo, hi = qs[idx], qs[idx + 1]
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        if f(mid) > 0:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return 0.5 * (lo + hi)


# ---------------------------------------------------------------------------
# Flowing BHP from annulus / downhole measurements
# ---------------------------------------------------------------------------
def pwf_from_fluid_level(chp: float, dfl_m: float, geom: WellGeom, fl: Fluid,
                         annulus_factor: float = 0.9, t_c: Optional[float] = None) -> float:
    """SRP well: casing pressure + gas column + gassy oil column above the pump
    + produced-fluid column from pump to perforations (ksc g)."""
    pump = geom.pump_depth_m if geom.pump_depth_m is not None else geom.perf_depth_m
    t = fl.t_res_c if t_c is None else t_c
    rho_g = F.gas_density(max(chp, 0.0), (geom.wht_c + t) / 2, fl.gas_sg)
    p = chp + rho_g * G * dfl_m / PA_PER_KSC
    h_oil = max(pump - dfl_m, 0.0)
    p += fl.oil_rho15 * 0.97 * annulus_factor * G * h_oil / PA_PER_KSC
    rho_mix = fl.oil_rho15 * 0.97 * (1 - fl.wc) + fl.water_rho * fl.wc
    p += rho_mix * G * max(geom.perf_depth_m - max(pump, dfl_m), 0.0) / PA_PER_KSC
    return p


def pwf_from_intake(pip: float, geom: WellGeom, fl: Fluid) -> float:
    """ESP / gauge at pump depth: add the produced-fluid column to the perforations."""
    pump = geom.pump_depth_m if geom.pump_depth_m is not None else geom.perf_depth_m
    rho_mix = fl.oil_rho15 * 0.97 * (1 - fl.wc) + fl.water_rho * fl.wc
    return pip + rho_mix * G * max(geom.perf_depth_m - pump, 0.0) / PA_PER_KSC


def static_thp(pr: float, fl: Fluid, geom: WellGeom) -> float:
    """Shut-in wellhead pressure for a liquid-filled tubing (upper bound)."""
    return vlp_thp(pr, 0.0, fl, geom)
