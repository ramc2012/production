# Kick-off prompt for Claude Code

Open a terminal in this folder (`flow_validation`), start `claude`, and paste the prompt below.

---

You are taking over the FlowTwin project: a well-to-tank flow validation, reconciliation and
digital-twin engine for an ONGC Ankleshwar gathering station. The task is to turn it into a
standalone application.

1. Read `CLAUDE.md`, then `docs/handoff/PRD.md`, `docs/handoff/ARCHITECTURE.md` and
   `docs/handoff/ROADMAP.md`. Skim `README.md`, `docs/METHODOLOGY.md` and `docs/DIGITAL_TWIN.md`.
2. Run `python -m unittest discover -s tests`. Confirm all tests (including `tests/test_golden.py`)
   pass on this machine before changing anything. If any fail, stop and report why.
3. Summarise back to me in 10 lines or fewer:
   - your understanding of the product;
   - the non-negotiables (read-only to plant systems, golden lock, no Docker, offline, explicit units);
   - which "Open decisions for the owner" from ROADMAP.md you need answered first.
4. Then start **M0** and **M1** only. Use plan mode first and show me the plan. Keep commits small,
   and do not mass-reformat engine code together with logic changes. Stop at the end of M1 with
   the milestone note.

Rules for the whole project:
- Never add any code that writes to a DCS, PLC, SCADA, OPC UA, Modbus or historian system.
- Never regenerate `tests/golden/baseline.json` without telling me what changed and why.
- Prefer the simplest thing that one person can maintain. Ask before adding a new service,
  database engine or framework.
- Explain trade-offs in plain language. I am an instrumentation engineer, not a full-time developer.

---

Useful custom commands in this repo: `/verify` runs every check, and `/milestone` writes the end-of-milestone note.
