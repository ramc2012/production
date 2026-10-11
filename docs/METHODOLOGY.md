# Methodology

Conventions:

- **Production day:** 06:00 to 06:00.
- **Volumes:** liquids in m³ at 15 °C; gas in Sm³ (15 °C, 101.325 kPa).
- **Pressure:** kg/cm² gauge (ksc).
- **Uncertainties:** quoted as expanded U95 (k = 2) and converted internally to standard uncertainty σ = U95/2.

## 1. Data quality (`dataquality.py`)

| Check | Rule (default) | Effect |
|---|---|---|
| Completeness | Every well has a daily record; every production-separator, gas-disposal and lift-header meter has a daily total | ALARM; the meter becomes *unmeasured* in DVR |
| Range | Configurable [min, max] per signal | ALARM; reading excluded |
| Frozen | Identical non-zero value for ≥ 4 days while the well is on (THP, CHP, lift gas, continuous meters) | WARN; excluded from the choke VFM |
| Spike | \|x − median₇\| > 5 · 1.4826 · MAD₇ and > 15 % of the median | WARN. Separator meters (they swing with test routing) and estimated meters are excluded |
| Consistency | SI status with hours > 0; test hours > hours on; THP ≤ FLP while flowing; gas-lift well on with zero injection | ALARM / WARN |
| Proving | `last_proved + interval < period end` | WARN; meter U95 × 2 in DVR |

## 2. Well tests (`welltests.py`)

**Stock-tank rates from a test.** With meter factor MF, oil-leg BS&W *b*, shrinkage SF and duration *h*:

```
oil   = V_oil · MF · (1 − b) · SF · 24/h
water = (V_water · MF_w + V_oil · MF · b) · 24/h
GOR   = (G_total − G_lift) / oil              (formation gas only)
```

**Acceptance rules:**

| Rule | Threshold | Outcome |
|---|---|---|
| Duration | < 4 h | REJECTED |
| Duration | < 8 h | WARN |
| Stability: CV of the last 4 hourly readings | > 20 % | REJECTED |
| Stability: CV of the last 4 hourly readings | > 10 % | WARN |
| Trend over the test | > 15 % | WARN |
| SRP volumetric efficiency VE = q_L / PD, with PD = 0.1166 · S[in] · N[spm] · D²[in] bbl/d | > 105 % | REJECTED (not physical) |
| SRP volumetric efficiency | < 30 % | WARN (pump wear, gas interference) |
| Gas-lift well formation gas | < 0 | REJECTED |
| Change vs last usable test: liquid | ±20 % warn / ±40 % alarm | Alarm → PROVISIONAL (used with wider uncertainty until a repeat test confirms it) |
| Change vs last usable test: water cut | ±10 pp warn / ±20 pp alarm | as above |
| Change vs last usable test: GOR | ±30 % warn / ±60 % alarm | as above |
| Wellhead sample BS&W vs metered water cut | > 5 pp | WARN |

How the comparison with the last test adapts:

- **SRP wells:** liquid is compared per stroke, normalised by SPM.
- **Gas-lift wells:** the GOR band is widened to 2σ/3σ of the subtraction uncertainty σ = √((u_G·G)² + (u_L·L)²)/(G − L). When lift gas dominates, formation gas is very uncertain.
- **Choke changed between the tests:** a deviation is downgraded, because a different rate is expected.

**Daily theoretical rate.**

- Rate = latest usable test with a test date before the day × hours_on/24. SRP wells are also scaled by SPM_day/SPM_test.
- Uncertainty:
  - Fresh test: U95 = 10 % on liquid, 4 pp on water cut, 20 % on GOR.
  - From day 7 onward, the uncertainty grows by +0.5 %/d (liquid), +0.2 pp/d (water cut) and +1 %/d (GOR).
  - The result is multiplied by 1.25 for an accepted-with-warning test and by 1.5 for a provisional test.
  - It is set to 100 % when only a rejected test exists.
- The 3×3 covariance of (oil, water, gas) comes from J·diag(σ_L², σ_wc², σ_GOR²)·Jᵀ, so oil and water from the same test are correlated.

**Virtual flow meter.** The Gilbert-type critical-flow choke equation is

```
THP[psia] = A · q_L[STB/d] · GLR[scf/STB]^B / S[1/64 in]^C
```

| Correlation | A | B | C |
|---|---|---|---|
| Gilbert | 10.00 | 0.546 | 1.89 |
| Ros | 17.40 | 0.500 | 2.00 |
| Baxendell | 9.56 | 0.546 | 1.93 |
| Achong | 3.82 | 0.650 | 1.88 |

It applies only when FLP_abs/THP_abs ≤ 0.55 (critical flow). A per-well calibration factor is fitted at every test. It is then inverted with the daily THP, choke and GLR (including lift gas) to estimate the daily liquid rate.

The VFM raises these findings:

- A deviation above 20 % (warn) or 40 % (alarm) for 2 consecutive days.
- Any choke change since the test.
- THP above 1.5 × the test THP while the well is reported on. This is treated as a probable shut-in, and the well's test-based rate is dropped from that day's reconciliation.

## 2b. Well performance: parameters that define the flow (`wellmodel.py`, `wellperf.py`)

**Static inputs** (from `well_static.csv`):
- Perforation depth.
- Tubing ID and shoe depth; casing ID.
- Gas-lift operating-valve depth.
- Pump setting depth.
- Reservoir temperature.
- Bubble point (optional; otherwise Standing).

Reservoir pressure Pr comes from the latest SBHP survey on or before the day (`reservoir_surveys.csv`).

**Fluids.**

| Property | Correlation |
|---|---|
| Rs, Pb, Bo | Standing |
| Live-oil viscosity | Beggs-Robinson |
| Gas viscosity | Lee-Gonzalez-Eakin |
| Z factor | Papay |

**Inflow (IPR).** Straight-line PI above Pb and Vogel below (composite):

```
q = J·(Pr − Pwf)                                          Pwf ≥ Pb
q = J·(Pr − Pb) + J·Pb/1.8·(1 − 0.2·Pwf/Pb − 0.8·(Pwf/Pb)²)   Pwf < Pb
```

**Tubing flow (VLP).** Beggs & Brill vertical flow:

- Flow-pattern map (L1 to L4), with holdup H_L = a·λᵇ/N_Fr^c × the inclination correction ψ.
- Two-phase friction factor f_tp = f_n·e^S.
- Integrated in 24 segments (Heun), with a linear temperature profile from WHT to Tres.
- Gas-lift gas is added above the operating valve.

**Flowing BHP from downhole measurements:**

| Source | Pwf |
|---|---|
| Downhole gauge | The gauge reading (FBHP) |
| ESP / pump-intake gauge | PIP + mixture column from pump to perforations |
| SRP fluid level (echometer) | CHP + gas column + gassy-oil column (factor 0.9) from the fluid level to the pump + mixture column from the pump to the perforations |

**Calibration at every accepted test.** Pwf at test conditions comes from the gauge, PIP or fluid level, or else from the VLP at the test THP and lift gas. Then J = q_test / IPR(Pwf, J = 1). The Gilbert choke factor and the SRP volumetric efficiency are fitted at the same test. Calibration absorbs model bias, so the estimators respond only to changes in the measured parameters.

**Daily estimators (U95 just after calibration, growing 0.3 %/day):**

| Estimator | Inputs | U95 |
|---|---|---|
| nodal: IPR = VLP | THP, lift gas, Pr, PI | 15 % |
| choke (Gilbert) | THP, choke, GLR | 20 % |
| ipr_fl | fluid level (survey ≤ 7 days old, +1.5 %/day) | 15 % |
| ipr_pip | pump-intake pressure | 10 % |
| ipr_gauge | downhole FBHP | 8 % |
| srp_pd | displacement × VE_test × fillage/fillage_test | 20 % |

**Combined estimate.** The estimators are combined by inverse variance into a well-parameter VFM, which enters DVR as a liquid measurement: oil + water − liq_vfm = 0.

**Wellhead BS&W samples** (≤ 7 days old) enter DVR as a water-cut measurement: (1 − wc_s)·water − wc_s·oil = 0, with U95 = 5 pp + 0.5 pp/day of sample age.

**Health checks:**

| Check | Trigger | Likely meaning |
|---|---|---|
| Inflow | Calibrated IPR/VLP gives no inflow at the reported THP while the well is "on" | Well shut in or dead |
| PI change between tests | > 25 % | Damage, scale or stimulation; for a gas-lift well, an injection-point change |
| SRP submergence | < 50 m | Pump-off or gas-interference risk |
| SRP fillage | < 70 % | Pump-off or gas interference |
| SRP fluid level | Rise > 100 m at unchanged SPM | Pump or valve leak |
| Gas-lift injection pressure | Shift > 10 % vs test | Operating valve changed |
| Wellhead sample vs test water cut | > 10 pp apart | Water cut has changed |
| Combined estimate vs test-based rate | Off by > 20 % for 2 days | Well has changed since its test |
| Estimator conflict | > 3σ apart, diagnosed by pair (see below) | See below |

Estimator conflicts are diagnosed by which pair disagrees:
- Choke model above nodal: restricted or plugged choke (or THP gauge reading high).
- Nodal above choke model: eroded bean.
- Pump displacement above fluid-level inflow: VE has fallen (valve, tubing leak or worn pump).

## 3. Separators (`separators.py`)

**Meter conversion.** The oil-leg meter gives stock-tank oil V·MF·(1 − b)·SF and the water carried to the tanks V·MF·b. The water and gas legs are taken as metered. The uncertainty of (oil, carried water) is propagated numerically from (V, b, SF) with first-order GUM.

**Retention time** = liquid hold-up at NLL / actual liquid rate. It is compared with API 12J:

| Separator | Condition | Retention time (minutes) |
|---|---|---|
| 2-phase | API > 35 | 1 |
| 2-phase | API 20 to 35 | 1 to 2 |
| 2-phase | API 10 to 20 | 2 to 4 |
| 3-phase | API > 35 | 3 to 5 |
| 3-phase | API ≤ 35, ≥ 38 °C | 5 to 10 |
| 3-phase | API ≤ 35, ≥ 27 °C | 10 to 20 |
| 3-phase | API ≤ 35, ≥ 16 °C | 20 to 30 |

**Gas capacity** is checked with Souders-Brown, v_max = K·√((ρ_L − ρ_G)/ρ_G):

- Vertical vessels: K = 0.055 m/s.
- Horizontal vessels: K = 0.122 m/s × (L/3.048 m)^0.56.
- ρ_G = P·M/(Z·R·T), with Z from Papay and Sutton pseudo-criticals.
- Load above 80 % of v_max is a warning and above 100 % an alarm. Liquid carry-over then biases the gas meter high and the oil meter low.

## 4. Tanks (`tanks.py`)

The volume chain follows API MPMS Ch. 12.1.1 and Ch. 11.1 / ASTM D1250:

```
TOV  = strapping(level)
FW   = strapping(free-water level)
CTSh = 1 + 2α·ΔTs + (α·ΔTs)²,   Ts = (7·T_L + T_A)/8 (uninsulated) or T_L,   α = 1.12e-5 /°C
GOV  = (TOV − FW) · CTSh
ρ15  = ρ_obs / CTL(ρ15, T_obs)                (iterated)
CTL  = exp(−α15·Δt·(1 + 0.8·α15·Δt)),  α15 = 613.9723/ρ15²,  Δt = T_L − 15   (Table 54A, crude)
GSV  = GOV · CTL
NSV  = GSV · (1 − S&W/100)
Tank oil production = Σ(NSV_close − NSV_open) + dispatch NSV
Tank water          = Σ Δ(free water + suspended S&W) + drains + water in dispatch
```

The uncertainty of the inventory change is propagated from every dip (level σ = 2 mm, free water 3 mm, temperature 0.5 °C, density 1 kg/m³, S&W 0.1 pp).

**Dip-error signature.** A wrong gauge at the 06:00 boundary between days d−1 and d raises one day's tank-minus-separator residual and lowers the next by the same amount. When (r₁ − r₂)/2 > 3σ, each residual exceeds 1.5σ, the signs are opposite and \|r₁ + r₂\| < 0.35·max\|r\|, the gauge is flagged. The tank with the largest second difference of ΔNSV is named, the error is converted to mm, and both days' inventory measurement is dropped from DVR.

## 5. Data Validation & Reconciliation (`reconciliation.py`)

**Balances per day:**

- Oil, water and gas at every separator that receives flow. Wells feed their group separator, and the test separator receives the fraction test_hours/hours_on.
- Tank-farm oil and water.
- Gas disposal: Σ separator gas = compressor + flare + fuel.
- Lift-gas distribution: Σ well injection = header meter.

**Solution.** Minimise (ŷ − y)ᵀV⁻¹(ŷ − y) subject to A_y·ŷ + A_u·u = 0. V is block-diagonal and keeps the correlations within each measurement group. Unmeasured u is removed with the projection P (from the SVD of A_u, P·A_u = 0), giving the reduced constraints B = P·A_y:

```
r = B·y,  S = B·V·Bᵀ,  a = V·Bᵀ·S⁻¹·r,  ŷ = y − a,  V̂ = V − V·Bᵀ·S⁻¹·B·V
u = −A_u⁺·A_y·ŷ   (an unmeasured stream that is not observable is reported as such)
```

**Tests:**

- **Global:** γ = rᵀ·S⁻¹·r ~ χ²(rank B). The test fails when γ > χ²₀.₉₅.
- **Measurement:** z_i = \|a_i\| / √W_ii. The redundancy W_ii/V_ii is reported; measurements with zero redundancy cannot be checked.
- **Group likelihood ratio,** for one meter or one well's (oil, water, gas): T_K = d_Kᵀ·F_KK⁺·d_K ~ χ²(rank), with d = Bᵀ·S⁻¹·r and F = Bᵀ·S⁻¹·B. The phase pattern is what lets the test tell apart wells that share one separator.
- **Serial elimination:** take the candidates with p < α/n, re-solve with each one turned into an unmeasured variable (skipping any that would become unobservable), and eliminate the one that lowers χ² most. Repeat up to 3 times.
- **Equivalence:** if another candidate leaves a χ² within χ²₀.₉₅(1) = 3.84 of the best, the data cannot separate them. The model reports the candidate set as *ambiguous* rather than guessing.
- **Persistence:** a meter isolated, or significant and material (z > Šidák limit and \|deviation\| ≥ 1 %), on ≥ 3 days raises a *systematic bias* alarm with its mean deviation.

## 6. Allocation (`allocation.py`)

**Allocation factor per phase:** AF = measured total / Σ theoretical.

| Phase | Measured total |
|---|---|
| Oil | Tank ΔNSV + dispatch |
| Water | Separator water legs + water carried in oil legs |
| Gas | Separator gas − lift gas |

AF is reported at installation level and per separator, with a warning outside 0.90 to 1.10 and an alarm outside 0.80 to 1.20.

Well allocation is reported two ways:

- **Proportional:** theoretical × AF, per API MPMS 20.1. When the measured total is unavailable, the DVR total is used instead.
- **Reconciled:** the DVR well rates. They close every balance exactly, and each carries an uncertainty.

## Default uncertainties (U95)

| Item | U95 |
|---|---|
| Coriolis / positive-displacement meter | 0.5 % |
| Turbine / ultrasonic / magnetic meter | 1 % |
| Orifice / vortex meter | 2 % |
| Estimated stream | 30 % |
| Flare estimate | 25 % |
| Dispatch meter (sample) | 0.3 % |
| Shrinkage factor | 2 % |
| Oil-leg BS&W | 2 pp |
| Fresh well test, liquid | 10 % |
| Fresh well test, water cut | 4 pp |
| Fresh well test, GOR | 20 %, plus aging (see section 2) |

## References

- API MPMS Ch. 20.1, *Allocation Measurement of Oil and Natural Gas*. API MPMS Ch. 20.5, *Recommended Practice for Application of Production Well Testing in Measurement and Allocation*.
- API MPMS Ch. 11.1 / ASTM D1250, *Temperature and Pressure Volume Correction Factors* (Table 54A crude, K0 = 613.9723). The 2004 edition uses a 60 °F base with K0 = 341.0957.
- API MPMS Ch. 12.1.1, *Calculation of static petroleum quantities, upright cylindrical tanks* (CTSh, GOV, GSV, NSV).
- API Spec 12J, *Oil and Gas Separators* (retention time, Souders-Brown K).
- VDI 2048, *Control and quality improvement of process data and their uncertainties by correction calculation*.
- Narasimhan, S. & Jordache, C. (2000), *Data Reconciliation and Gross Error Detection*. Crowe, Garcia Campos & Hrymak (1983), AIChE J. 29(6), projection matrix for unmeasured variables.
- Amin et al., NFOGM 2016, *Application of Data Validation and Reconciliation to Production Allocation*: https://nfogm.no/wp-content/uploads/2019/02/2016-10-Application-of-Data-Validation-and-Reconciliation-to-Production-Allocation-Amin-Letton-Hall-Group.pdf
- Standing (1947) PVT correlations. Beggs & Robinson (1975) oil viscosity. Lee, Gonzalez & Eakin (1966) gas viscosity.
- Vogel, J.V. (1968), *Inflow performance relationships for solution-gas drive wells*, JPT.
- Beggs, H.D. & Brill, J.P. (1973), *A study of two-phase flow in inclined pipes*, JPT 25(5).
- Gilbert (1954), Ros (1960), Baxendell (1958), Achong (1961) choke correlations. PetroWiki *Flow through chokes*: https://petrowiki.org/Flow_through_chokes
- Emerson, *A practical approach to continuous liquid allocation measurement* (shrinkage, water-cut methods per MPMS 20.1).
