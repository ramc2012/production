"""Self-contained statistical helpers (no SciPy dependency).

Provides the normal quantile, the chi-square CDF/quantile and a generic
first-order (GUM) uncertainty propagator used throughout the model.
"""
from __future__ import annotations

import math
from typing import Callable, Sequence

import numpy as np


# ---------------------------------------------------------------------------
# Normal distribution
# ---------------------------------------------------------------------------
def norm_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def norm_ppf(p: float) -> float:
    """Inverse standard normal CDF (Acklam's rational approximation + one
    Halley refinement step; accurate to ~1e-12)."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    a = [-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
         1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00]
    b = [-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
         6.680131188771972e01, -1.328068155288572e01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
         -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00,
         3.754408661907416e00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        x = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    elif p <= phigh:
        q = p - 0.5
        r = q * q
        x = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
            (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    else:
        q = math.sqrt(-2 * math.log(1 - p))
        x = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    # Halley refinement
    e = norm_cdf(x) - p
    u = e * math.sqrt(2 * math.pi) * math.exp(x * x / 2)
    return x - u / (1 + x * u / 2)


# ---------------------------------------------------------------------------
# Chi-square distribution via the regularised incomplete gamma function
# ---------------------------------------------------------------------------
def _gammainc_lower_reg(a: float, x: float) -> float:
    """Regularised lower incomplete gamma P(a, x) (Numerical Recipes)."""
    if x <= 0:
        return 0.0
    gln = math.lgamma(a)
    if x < a + 1.0:  # series
        ap, s, delta = a, 1.0 / a, 1.0 / a
        for _ in range(1000):
            ap += 1.0
            delta *= x / ap
            s += delta
            if abs(delta) < abs(s) * 1e-15:
                break
        return s * math.exp(-x + a * math.log(x) - gln)
    # continued fraction for Q, return 1 - Q
    tiny = 1e-300
    b = x + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / b
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        d = tiny if abs(d) < tiny else d
        c = b + an / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            break
    q = math.exp(-x + a * math.log(x) - gln) * h
    return 1.0 - q


def chi2_cdf(x: float, dof: int) -> float:
    if dof <= 0:
        return float("nan")
    return _gammainc_lower_reg(dof / 2.0, x / 2.0)


def chi2_sf(x: float, dof: int) -> float:
    return 1.0 - chi2_cdf(x, dof)


def chi2_ppf(p: float, dof: int) -> float:
    """Chi-square quantile: Wilson-Hilferty start, then bisection/Newton."""
    if dof <= 0:
        return float("nan")
    z = norm_ppf(p)
    k = float(dof)
    x = max(k * (1 - 2 / (9 * k) + z * math.sqrt(2 / (9 * k))) ** 3, 1e-8)
    lo, hi = 0.0, max(10 * x, k + 50 * math.sqrt(2 * k) + 50)
    for _ in range(200):
        f = chi2_cdf(x, dof) - p
        if abs(f) < 1e-13:
            break
        if f > 0:
            hi = x
        else:
            lo = x
        # Newton step using the chi-square pdf; fall back to bisection
        a = k / 2
        pdf = math.exp((a - 1) * math.log(x) - x / 2 - a * math.log(2) - math.lgamma(a)) if x > 0 else 0
        xn = x - f / pdf if pdf > 0 else (lo + hi) / 2
        x = xn if lo < xn < hi else (lo + hi) / 2
    return x


def sidak_z(alpha: float, n_tests: int) -> float:
    """Two-sided critical z with Sidak correction for n simultaneous tests."""
    n = max(int(n_tests), 1)
    beta = 1.0 - (1.0 - alpha) ** (1.0 / n)
    return norm_ppf(1.0 - beta / 2.0)


# ---------------------------------------------------------------------------
# Uncertainty propagation (GUM first order, numerical Jacobian)
# ---------------------------------------------------------------------------
def propagate(func: Callable[[np.ndarray], Sequence[float]],
              x: Sequence[float], sigma: Sequence[float],
              rel_step: float = 1e-6):
    """Propagate independent input standard uncertainties through ``func``.

    Returns (y, cov_y) where y = func(x) and cov_y = J diag(sigma^2) J^T.
    ``func`` may return a scalar or a vector.
    """
    x = np.asarray(x, dtype=float)
    s = np.asarray(sigma, dtype=float)
    y0 = np.atleast_1d(np.asarray(func(x), dtype=float))
    J = np.zeros((y0.size, x.size))
    for j in range(x.size):
        if s[j] == 0:
            continue
        h = max(abs(x[j]) * rel_step, s[j] * 1e-3, 1e-9)
        xp, xm = x.copy(), x.copy()
        xp[j] += h
        xm[j] -= h
        J[:, j] = (np.atleast_1d(func(xp)) - np.atleast_1d(func(xm))) / (2 * h)
    cov = J @ np.diag(s ** 2) @ J.T
    return y0, cov


def u95_to_sigma(u95_pct: float, value: float) -> float:
    """Expanded uncertainty (k=2, % of value) -> standard uncertainty."""
    return abs(value) * (u95_pct / 100.0) / 2.0
