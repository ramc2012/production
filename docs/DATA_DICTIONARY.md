# Data dictionary

Every input is a CSV with a header row. Column names carry their units:

| Suffix | Unit |
|---|---|
| `_m3` | m³ at line conditions, unless the column says otherwise |
| `_sm3` | standard m³ (15 °C, 101.325 kPa) |
| `_ksc` | kg/cm² gauge |
| `_degc` | °C |
| `_pct` | % |
| `_64ths` | choke size in 1/64 in |

Dates are `YYYY-MM-DD`. A *production day* runs from 06:00 on `date` to 06:00 the next day. Optional columns may be left out or left blank.

## well_daily.csv (required): one row per well per day (from the DPR)

| Column | Req. | Description |
|---|---|---|
| date | ✔ | Production day |
| well_id | ✔ | Must match `network.yaml` |
| hours_on | ✔ | Producing hours (0 to 24) |
| status | | FLOWING / SI |
| test_hours | | Hours routed to the test separator that day |
| thp_ksc, flp_ksc, chp_ksc | | Tubing-head, flow-line and casing pressure (daily average) |
| choke_64ths | | Bean size |
| lift_gas_sm3 | | Gas-lift injection for the day |
| spm, stroke_in | | SRP strokes per minute and stroke length |
| wht_degc | | Wellhead temperature (VLP temperature profile) |
| inj_pressure_ksc | | Gas-lift injection pressure (CHP is used if this is blank) |
| dfl_m | | SRP dynamic fluid level from the wellhead (echometer survey days only) |
| pump_fillage_pct | | Dynacard pump fillage |
| pip_ksc | | ESP / pump-intake pressure |
| fbhp_ksc | | Downhole gauge flowing BHP at the perforations |
| sample_bsw_pct | | Wellhead sample BS&W (days a sample is taken) |
| remarks | | Free text |

## well_tests.csv (required): one row per test

| Column | Req. | Description |
|---|---|---|
| test_id, well_id, test_date | ✔ | |
| duration_h | ✔ | Test duration |
| separator_id | ✔ | Test separator used |
| oil_m3 | ✔ | Oil-leg meter volume over the test (separator conditions, gross) |
| water_m3 | ✔ | Water-leg meter volume over the test |
| gas_sm3 | ✔ | Total separator gas over the test (includes lift gas) |
| oil_leg_bsw_pct | | BS&W of the oil-leg sample |
| lift_gas_sm3 | | Lift gas injected during the test |
| thp_ksc, flp_ksc, chp_ksc, choke_64ths | | Test conditions (needed to calibrate the choke model) |
| sep_pressure_ksc, sep_temp_degc | | Test separator conditions |
| spm, stroke_in | | SRP settings during the test |
| sample_bsw_pct | | Wellhead sample BS&W (cross-check) |
| shrinkage_factor | | Overrides the separator's default for this test |
| wht_degc, inj_pressure_ksc, dfl_m, pump_fillage_pct, pip_ksc, fbhp_ksc | | Well parameters at test conditions. Used to calibrate the well model (PI, VE, fillage baseline) |

## well_test_readings.csv (optional): hourly readings during a test

`test_id, hour, liquid_m3, gas_sm3, thp_ksc`. These are used for the stability (CV) and trend checks.

## meter_daily.csv (required): daily totals from flow computers / SCADA

`date, meter_id, volume`, with `hours_in_service` optional.

- Liquid meters report m³ at line conditions; the meter factor is applied by the model.
- Gas meters report Sm³.
- Include every separator, gas-disposal and lift-header meter.
- Test-separator meters read 0 on days with no test.

## separator_daily.csv (optional, strongly recommended)

`date, separator_id, pressure_ksc, temperature_degc, oil_leg_bsw_pct`. If the oil-leg BS&W is missing, the model assumes 0 % and widens the uncertainty.

## tank_gauges.csv (required): dips at the production-day boundary (06:00)

| Column | Req. | Description |
|---|---|---|
| datetime | ✔ | `YYYY-MM-DD 06:00` |
| tank_id | ✔ | |
| level_mm | ✔ | Innage (total level) |
| free_water_mm | | Free-water innage |
| liquid_temp_degc, ambient_temp_degc | | Used for CTL and CTSh |
| density_obs_kgm3, density_obs_temp_degc | | Hydrometer density and the temperature it was read at |
| bsw_pct | | S&W of the tank sample |

## tank_drains.csv (optional)

`date, tank_id, water_drained_m3`: water drained to ETP, estimated by dip difference.

## dispatch_daily.csv (required): oil pumped to CTF

`date, meter_id, gross_m3, temp_degc, density15_kgm3, bsw_pct`

## Strapping tables (`strapping/<tank>.csv`)

`level_mm, volume_m3`, the tank calibration table at the calibration reference temperature. The model interpolates linearly, so use the finest table available.

## well_static.csv (optional; enables the well-performance model)

| Column | Description |
|---|---|
| well_id | |
| perf_depth_m | Mid-perforation depth (TVD) |
| tubing_depth_m, tubing_id_in | Tubing shoe depth and internal diameter (2-7/8" = 2.441, 2-3/8" = 1.995) |
| casing_id_in | Production casing ID (below the tubing shoe) |
| valve_depth_m | Gas-lift operating-valve depth |
| pump_depth_m | SRP / ESP setting depth |
| reservoir_temp_degc | |
| reservoir_pressure_ksc | Used if there is no survey |
| bubble_point_ksc | Optional (otherwise Standing from the test GOR) |
| wht_degc | Default wellhead temperature |

## reservoir_surveys.csv (optional)

`date, well_id, pr_ksc, survey_type`: SBHP / build-up results referred to mid-perforation. The latest survey on or before each day is used.

## network.yaml

```yaml
installation: {name: GGS-X, gas_sg: 0.75, oil_rho15_kgm3: 860, water_rho_kgm3: 1010}
wells:
  - {id: ANK-101, lift: self_flow, separator: GS-1}            # lift: self_flow | gas_lift | srp | esp
  - {id: ANK-105, lift: gas_lift, separator: GS-1, lift_gas_meter: FT-405}
  - {id: ANK-109, lift: srp, separator: GS-2, plunger_in: 1.75, stroke_in: 64}
separators:
  - {id: GS-1, kind: production, phases: 3, orientation: horizontal, diameter_m: 1.6, length_m: 5.0,
     liquid_volume_m3: 5.0, shrinkage_factor: 0.965, oil_meter: FT-111, water_meter: FT-112, gas_meter: FT-113}
  - {id: TS-1, kind: test, ...}
meters:
  - {id: FT-111, phase: oil, type: pd, meter_factor: 1.0, last_proved: 2026-08-10, proving_interval_days: 90}
  # type: coriolis | pd | turbine | ultrasonic | vortex | orifice | venturi | mag | estimate | flare_estimate
  # u95_pct overrides the type default
tanks:
  - {id: T-1, strapping_table: strapping/T-1.csv, insulated: false, calibration_temp_c: 15, safe_fill_mm: 7200}
dispatch: {meter: FT-201}
gas_disposal: [{meter: FT-301, role: compressor}, {meter: FT-302, role: flare}, {meter: FT-303, role: fuel}]
lift_gas: {header_meter: FT-401}
```

Notes on network.yaml:

- **2-phase separator:** set `phases: 2`, put the gross-liquid meter as `oil_meter`, and give the liquid BS&W in `separator_daily.oil_leg_bsw_pct`.
- **Stream without a meter:** omit the meter ID. The stream becomes unmeasured and is estimated by reconciliation when it is observable.
