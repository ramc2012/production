"""Unit conversions. Internal convention (matches ONGC DPR practice):

* liquid volumes  : m3 (rates m3/d)
* gas volumes     : Sm3 at standard conditions (rates Sm3/d)
* pressure        : kg/cm2 gauge ("ksc")
* temperature     : degC
* density         : kg/m3
"""
KSC_TO_PSI = 14.223343
KSC_TO_KPA = 98.0665
ATM_KSC = 1.033227          # 1 atm in kg/cm2
M3_TO_BBL = 6.289811
BBL_TO_M3 = 1.0 / M3_TO_BBL
SM3_M3_TO_SCF_BBL = 5.614583  # GOR Sm3/m3 -> scf/bbl
WATER_DENSITY_15C = 999.016   # kg/m3 at 15 degC (60 degF basis for SG)
R_GAS = 8.314462618           # J/(mol K)
M_AIR = 28.9647               # g/mol


def ksc_g_to_psia(p_ksc_g: float, atm_ksc: float = ATM_KSC) -> float:
    return (p_ksc_g + atm_ksc) * KSC_TO_PSI


def ksc_g_to_kpa_abs(p_ksc_g: float, atm_ksc: float = ATM_KSC) -> float:
    return (p_ksc_g + atm_ksc) * KSC_TO_KPA


def c_to_k(t_c: float) -> float:
    return t_c + 273.15


def c_to_rankine(t_c: float) -> float:
    return (t_c + 273.15) * 1.8


def m3d_to_bpd(q: float) -> float:
    return q * M3_TO_BBL


def bpd_to_m3d(q: float) -> float:
    return q * BBL_TO_M3


def gor_si_to_field(gor_sm3_m3: float) -> float:
    return gor_sm3_m3 * SM3_M3_TO_SCF_BBL
