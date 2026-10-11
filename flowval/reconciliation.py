"""Layer 5 - Data Validation & Reconciliation (DVR).

Linear weighted-least-squares reconciliation with unmeasured variables
(Crowe et al. 1983 projection) and gross-error detection, following the
approach of VDI 2048 / Narasimhan & Jordache (2000):

    minimise  (y_hat - y)^T V^-1 (y_hat - y)
    s.t.      A_y y_hat + A_u u = 0

* Unmeasured variables are removed with a projection P (P A_u = 0), giving
  the reduced constraints B = P A_y.
* Global test:   gamma = r^T (B V B^T)^-1 r  ~  chi2(dof = rank B),  r = B y
* Measurement test: z_i = |a_i| / sqrt(W_ii),  a = V B^T (B V B^T)^-1 r,
  W = V B^T (B V B^T)^-1 B V  (covariance of adjustments)
* Group test (generalised likelihood ratio): for a measurement group K
  (e.g. one well's oil/water/gas, or one meter) T_K = d_K^T F_KK^+ d_K ~
  chi2(rank F_KK), d = B^T S^-1 r, F = B^T S^-1 B. The phase pattern lets the
  test tell apart wells that share a single separator.
* Gross errors are isolated by serial elimination: the group with the
  smallest p-value (below a Bonferroni-corrected alpha) is turned into an
  unmeasured variable and the problem re-solved, while observability holds.
* Equivalence check: if several suspects leave (almost) the same residual
  chi-square after elimination (difference < chi2(0.95, 1)), the data cannot
  tell them apart; the model reports the candidate set instead of guessing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .stats import chi2_ppf, chi2_sf, sidak_z

_DEBUG = False


@dataclass
class DVRResult:
    variables: pd.DataFrame
    constraints: pd.DataFrame
    global_stat: float
    dof: int
    chi2_crit: float
    passed: bool
    eliminated: List[dict] = field(default_factory=list)
    ambiguous: List[dict] = field(default_factory=list)
    unobservable: List[str] = field(default_factory=list)
    passed_initial: bool = True
    dof_initial: int = 0
    chi2_crit_initial: float = float("nan")
    global_stat_initial: float = float("nan")
    message: str = ""


class DVRProblem:
    def __init__(self):
        self.var_names: List[str] = []
        self.var_meta: Dict[str, dict] = {}
        self.groups: List[dict] = []
        self.constraints: List[tuple] = []

    # ---- model building ---------------------------------------------------
    def var(self, name: str, **meta) -> str:
        if name not in self.var_meta:
            self.var_names.append(name)
            self.var_meta[name] = meta
        return name

    def measure(self, group: dict) -> None:
        for v in group["vars"]:
            self.var(v)
        self.groups.append(group)

    def constraint(self, name: str, coeffs: Dict[str, float]) -> None:
        coeffs = {k: v for k, v in coeffs.items() if v != 0}
        for v in coeffs:
            self.var(v)
        if coeffs:
            self.constraints.append((name, coeffs))

    # ---- solving -------------------------------------------------------------
    def _solve_once(self, excluded: set):
        names = self.var_names
        idx = {n: i for i, n in enumerate(names)}
        m, n = len(self.constraints), len(names)
        A = np.zeros((m, n))
        for r, (_, co) in enumerate(self.constraints):
            for v, c in co.items():
                A[r, idx[v]] = c
        groups = [g for k, g in enumerate(self.groups) if k not in excluded]
        meas_vars, y, blocks, owner = [], [], [], []
        for k, g in enumerate(self.groups):
            if k in excluded:
                continue
            for j, v in enumerate(g["vars"]):
                meas_vars.append(v)
                y.append(g["values"][j])
                owner.append(k)
            blocks.append(np.atleast_2d(g["cov"]))
        y = np.asarray(y, dtype=float)
        V = _block_diag(blocks) if blocks else np.zeros((0, 0))
        mi = [idx[v] for v in meas_vars]
        un_vars = [v for v in names if v not in set(meas_vars)]
        ui = [idx[v] for v in un_vars]
        Ay, Au = A[:, mi], A[:, ui]

        # projection to eliminate unmeasured variables
        unobs: List[str] = []
        if Au.shape[1]:
            Uu, Su, Vtu = np.linalg.svd(Au, full_matrices=True)
            tol = max(Au.shape) * np.finfo(float).eps * (Su[0] if Su.size else 1.0) * 1e3
            ru = int((Su > tol).sum())
            P = Uu[:, ru:].T
            N = Vtu[ru:].T  # null space of Au
            for j, v in enumerate(un_vars):
                if N.size and np.abs(N[j]).max() > 1e-8:
                    unobs.append(v)
        else:
            P = np.eye(m)
        B = P @ Ay
        # drop dependent rows
        if B.size:
            Ub, Sb, Vtb = np.linalg.svd(B, full_matrices=False)
            rb = int((Sb > max(B.shape) * np.finfo(float).eps * (Sb[0] if Sb.size else 1) * 1e3).sum())
            B = np.diag(Sb[:rb]) @ Vtb[:rb]
        else:
            rb = 0
        dof = rb
        if dof > 0:
            r = B @ y
            S = B @ V @ B.T
            Sinv = np.linalg.pinv(S)
            dvec = B.T @ Sinv @ r          # a = V d
            Fm = B.T @ Sinv @ B            # W = V F V
            a = V @ dvec
            gamma = float(r @ Sinv @ r)
            W = V @ Fm @ V
        else:
            a = np.zeros_like(y)
            gamma = 0.0
            W = np.zeros_like(V)
            dvec, Fm = np.zeros_like(y), np.zeros((len(y), len(y)))
        # generalised-likelihood-ratio statistic per measurement group:
        # T_k = d_K^T pinv(F_KK) d_K ~ chi2(rank F_KK) under H0 (no bias in group k)
        gst = []
        owner_arr = np.asarray(owner)
        for k in sorted(set(owner)):
            K = np.where(owner_arr == k)[0]
            FK = Fm[np.ix_(K, K)]
            rk = np.linalg.matrix_rank(FK, tol=1e-10 * max(np.abs(FK).max(), 1e-300)) if FK.size else 0
            if rk == 0:
                gst.append(dict(group=k, T=np.nan, dof=0, p=np.nan))
                continue
            T = float(dvec[K] @ np.linalg.pinv(FK, rcond=1e-10) @ dvec[K])
            gst.append(dict(group=k, T=T, dof=rk, p=chi2_sf(T, rk)))
        gstats = pd.DataFrame(gst, columns=["group", "T", "dof", "p"])
        y_hat = y - a
        V_hat = V - W
        # unmeasured estimates
        if Au.shape[1]:
            Aup = np.linalg.pinv(Au)
            u = -Aup @ Ay @ y_hat
            Cu = Aup @ Ay @ V_hat @ Ay.T @ Aup.T
        else:
            u, Cu = np.zeros(0), np.zeros((0, 0))

        rows = []
        dV = np.diag(V) if V.size else np.zeros(0)
        dW = np.diag(W) if W.size else np.zeros(0)
        for j, v in enumerate(meas_vars):
            g = self.groups[owner[j]]
            sd = np.sqrt(dV[j])
            red = dW[j] / dV[j] if dV[j] > 0 else 0.0
            z = abs(a[j]) / np.sqrt(dW[j]) if dW[j] > 1e-12 * max(dV[j], 1e-30) and red > 1e-6 else np.nan
            rows.append(dict(var=v, measured=True, tag=g["tag"], kind=g["kind"], label=g.get("label", ""),
                             group=owner[j], meas=y[j], sd_meas=sd, rec=y_hat[j],
                             sd_rec=np.sqrt(max(np.diag(V_hat)[j], 0.0)), adj=-a[j],
                             adj_pct=(-a[j] / y[j] * 100 if abs(y[j]) > 1e-9 else np.nan),
                             penalty=(a[j] / sd) ** 2 if sd > 0 else np.nan, z=z, redundancy=red))
        for j, v in enumerate(un_vars):
            rows.append(dict(var=v, measured=False, tag="", kind="unmeasured", label="", group=-1,
                             meas=np.nan, sd_meas=np.nan, rec=u[j],
                             sd_rec=np.sqrt(max(Cu[j, j], 0.0)) if v not in unobs else np.nan,
                             adj=np.nan, adj_pct=np.nan, penalty=np.nan, z=np.nan,
                             redundancy=np.nan, observable=v not in unobs))
        vdf = pd.DataFrame(rows)
        if len(gstats):
            vdf = vdf.merge(gstats.rename(columns={"T": "glr_T", "dof": "glr_dof", "p": "glr_p"}),
                            on="group", how="left")
        return vdf, gamma, dof, unobs, gstats

    def solve(self, alpha: float = 0.05, max_eliminations: int = 3) -> DVRResult:
        excluded: set = set()
        vdf, gamma, dof, unobs, gstats = self._solve_once(excluded)
        gamma0, dof0 = gamma, dof
        crit = chi2_ppf(1 - alpha, dof) if dof > 0 else np.nan
        crit0 = crit
        passed0 = (dof == 0) or gamma <= crit
        eliminated, ambiguous = [], []
        while dof > 0 and gamma > chi2_ppf(1 - alpha, dof) and len(eliminated) < max_eliminations:
            cand = gstats.dropna(subset=["p"])
            if cand.empty:
                break
            # Bonferroni over the groups that can be tested
            cand = cand[cand["p"] < alpha / len(cand)].sort_values("p").head(12)
            trials = []
            for gk in cand["group"]:
                trial = excluded | {int(gk)}
                tv, tg, td, tu, ts = self._solve_once(trial)
                gvars = self.groups[int(gk)]["vars"]
                if any(v in tu for v in gvars):
                    continue  # elimination would make the stream unobservable
                trials.append((tg, int(gk), (tv, tg, td, tu, ts)))
            if not trials:
                break
            trials.sort(key=lambda x: x[0])
            if _DEBUG:
                print("TRIALS", [(self.groups[t[1]]["tag"], round(t[0], 1)) for t in trials[:5]])
            best_gamma = trials[0][0]
            # equivalence set: other suspects that explain the data (almost) as well
            equiv = [t for t in trials if t[0] - best_gamma < chi2_ppf(0.95, 1)]
            if len(equiv) > 1:
                ambiguous.append(dict(
                    candidates=[self.groups[t[1]]["tag"] for t in equiv],
                    kinds=[self.groups[t[1]]["kind"] for t in equiv],
                    gamma_after=[float(t[0]) for t in equiv],
                    est=[dict(zip(self.groups[t[1]]["vars"],
                                  zip(self.groups[t[1]]["values"],
                                      t[2][0].set_index("var").loc[self.groups[t[1]]["vars"], "rec"].values)))
                         for t in equiv]))
                break
            _, gk, sol = trials[0]
            g = self.groups[gk]
            row = gstats[gstats["group"] == gk].iloc[0]
            zmax = vdf.loc[vdf["group"] == gk, "z"].max()
            excluded = excluded | {gk}
            tv = sol[0]
            gvars = g["vars"]
            est = tv.set_index("var").loc[gvars]
            eliminated.append(dict(group=gk, tag=g["tag"], kind=g["kind"], label=g.get("label", ""),
                                   z=float(zmax) if zmax == zmax else float("nan"),
                                   glr_T=float(row["T"]), glr_p=float(row["p"]),
                                   vars=list(gvars), meas=list(g["values"]),
                                   est=list(est["rec"].values), sd_est=list(est["sd_rec"].values)))
            vdf, gamma, dof, unobs, gstats = sol
        crit = chi2_ppf(1 - alpha, dof) if dof > 0 else np.nan
        passed = (dof == 0) or gamma <= crit
        # constraint residuals with raw measurements (where fully measured)
        meas_map = {}
        for g in self.groups:
            for j, v in enumerate(g["vars"]):
                meas_map[v] = g["values"][j]
        rec_map = dict(zip(vdf["var"], vdf["rec"]))
        crow = []
        for name, co in self.constraints:
            raw = sum(c * meas_map[v] for v, c in co.items()) if all(v in meas_map for v in co) else np.nan
            rec = sum(c * rec_map.get(v, np.nan) for v, c in co.items())
            crow.append(dict(constraint=name, raw_residual=raw, rec_residual=rec, n_terms=len(co)))
        # mark eliminated groups in the variable table
        if eliminated:
            for e in eliminated:
                for j, v in enumerate(e["vars"]):
                    m = vdf["var"] == v
                    vdf.loc[m, "tag"] = e["tag"]
                    vdf.loc[m, "kind"] = e["kind"] + " (eliminated)"
                    vdf.loc[m, "meas"] = e["meas"][j]
        return DVRResult(variables=vdf, constraints=pd.DataFrame(crow), global_stat=gamma, dof=dof,
                         chi2_crit=crit, passed=passed, eliminated=eliminated, ambiguous=ambiguous, unobservable=unobs,
                         passed_initial=passed0, global_stat_initial=gamma0,
                         dof_initial=dof0, chi2_crit_initial=crit0)


def _block_diag(blocks):
    n = sum(b.shape[0] for b in blocks)
    out = np.zeros((n, n))
    i = 0
    for b in blocks:
        k = b.shape[0]
        out[i:i + k, i:i + k] = b
        i += k
    return out
