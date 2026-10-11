Run the full verification for this repository and report the results concisely:

1. `python -m unittest discover -s tests` (engine, twin and golden lock). Report pass/fail counts and the slowest suites.
2. If an `app/` package exists: run its pytest suite and the read-only guard test, and if `app/frontend` exists, run `npm run build` and check the bundle for external URLs.
3. `python -m flowval twin --network data/sample/network.yaml --data data/sample --config data/sample/config.yaml --out output/verify` and confirm the level summary prints and that L5 reports `autonomous_sim` (simulator only).
4. If anything fails, show the first failure with the file and line, propose a fix, and do NOT change `tests/golden/baseline.json`.
