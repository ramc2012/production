# FlowTwin user guide

FlowTwin checks the gathering station's daily numbers, reconciles them, allocates production to wells
and runs a digital twin of the station. It **only reads data**: nothing in FlowTwin can change a
setpoint in the plant.

Volumes: oil and water in m³ at 15 °C, gas in Sm³ (15 °C, 101.325 kPa), pressure in ksc (kg/cm² g).
"± x" next to a number is its U95 uncertainty (about 95 % confidence).

## Roles

| Role | Can |
|---|---|
| Viewer | See every page and download reports |
| Engineer | Also: start runs, upload data, work exceptions, override well tests, accept/decline recommendations, run the L5 simulator sandbox, close a month |
| Admin | Also: users, network model, configuration, schedule, backups, re-open a closed month |

## The 09:00 routine (production engineer)

1. **Dashboard.** Check the tiles for yesterday's production day (06:00 to 06:00): reconciled oil,
   water and gas, the oil allocation factor (band 0.90 to 1.10), the χ² consistency test and open alarms.
   If the banner says *newer data not run yet*, start a run (Runs & reports) or wait for the scheduler.
2. **Exceptions → Issues.** Work the open alarms top-down. Open an issue to see the finding, the
   recommended action and its history. **Acknowledge** it (optionally assign it and set a due date).
   **Resolve** it with a comment once it is fixed. If the problem comes back on a later day, the issue
   re-opens by itself. Tick several rows to acknowledge them together.
3. **Wells.** Look at wells with a warning or alarm. On a well's page:
   *Production* compares reconciled, test-based and AF-allocated oil;
   *Performance* shows the rate estimators from THP, fluid level and pump data;
   *Tests* lets you **override** an engine verdict with a reason (applies from the next run).
4. **Well tests.** The *suggested test schedule* ranks wells by how much uncertainty a new test removes.
5. **Recommendations.** Accept or decline the twin's advice (decline needs a reason). Accepting only
   records your decision: make the change in the field or DCS through the normal procedure. When a later
   run sees the plant at the proposed setpoint, the advice is marked *implemented* automatically.

## Pages

| Page | What it is for |
|---|---|
| Network | The station as a diagram; each object is coloured by its worst finding on the last day or an open issue. Click a node. |
| Meters | Proving due dates, how much reconciliation adjusts each meter, systematic bias, drift projection and maintenance priority. A meter page shows measured vs reconciled per day. |
| Tanks & separators | Tank levels and the NSV chain (TOV → GOV → GSV → NSV), tank-farm balance; separator meters, retention and allocation factors. |
| Reconciliation | χ² per day (click a bar), every measurement against its reconciled value, isolated gross errors, ambiguous cases, balance residuals. |
| Allocation | Monthly per-well allocation by three methods with U95 bounds. **Close month** locks it to a run; **Export Excel** gives the signed-off workbook. |
| Runs & reports | Every run with its engine version, configuration hash and input batches; the engine's HTML and Excel reports per run. |
| Twin L0–L5 | The maturity ladder and one tab per level: asset model, live state, diagnostics, forecasts, prescriptive optimisation, and the L5 closed loop on the simulator. |
| What-if | Move choke, lift gas, SPM or switch a well off; the twin predicts the well and station response (oil, gas, compressor load, flare) in about a second. |
| Data in | Upload the daily templates, check and commit them; batches; coverage; data-quality findings. |

The **Run** selector at the top shows any older run on every page; *Latest* always follows the newest.

## Uploading data

1. *Data in → Upload*: drop the CSV files (named like the templates, e.g. `well_daily.csv`) or one Excel
   workbook with a sheet per template.
2. If FlowTwin cannot tell the template or a column has another name in your file, pick the template and
   map the columns. Save the mapping under a name to reuse it next time.
3. **Validate** shows the rows found, how many are new and how many update existing rows, plus any errors
   (bad dates, text in number columns, unknown wells). A batch with errors cannot be committed.
4. **Commit**. Then start a run, or let the scheduler pick it up.

A correction file only needs the key columns and the columns you correct (for example
`date, well_id, hours_on`). The other readings of that row are kept. Committed data is never deleted;
upload a correction instead.

Files dropped into the server's drop folder (`dropzone`) are imported automatically when they are valid,
followed by a run. Rejected files are moved to `dropzone\rejected\<time>\` with a report.

## The digital twin in one paragraph

Every twin run builds a physics model of each well (inflow, tubing, choke or pump) calibrated at its last
good test, then tunes it until it reproduces the reconciled rates (**L1**). It forecasts each well with a
P10–P90 band and simulates the tank farm 72 h ahead (**L3**). It searches for better gas-lift sharing,
chokes and pump speeds within drawdown, submergence, lift-gas and compressor limits (**L4**). It then
shows what a guarded closed loop would do on a *simulator* of the plant (**L5**). FlowTwin never connects
L5 to a control system.

## Honest limits

- Forecast accuracy is not yet proven: it needs 6 to 12 months of history to back-test.
- The optimiser maximises oil volume; oil price, gas value and power cost are not in it yet.
- A well without its own data (frozen THP, no samples) can absorb another well's imbalance. FlowTwin then
  says *unconfirmed* or *ambiguous* instead of guessing.
- A daily run cannot see the following days, so a few spike checks can differ slightly from the
  month-end run. Close the month on a run that covers the whole month.
