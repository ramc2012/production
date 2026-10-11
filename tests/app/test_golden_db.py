"""F1 + F3: data imported through the app and run from the database reproduces the golden baseline,
and every stored result traces back to its run (engine version, config hash, input batches)."""
import json
from collections import Counter
from pathlib import Path

from sqlalchemy import func, select

from app.backend import db as dbm
from app.backend import engine_adapter as A
from app.backend.models import Finding, Recommendation, Run, RunTable, WellDailyResult

BASE = json.loads((Path(__file__).resolve().parents[1] / "golden" / "baseline.json").read_text())


def test_findings_match_golden(twin_run):
    with dbm.session_scope() as db:
        rows = db.execute(select(Finding.object_id, Finding.check).where(Finding.run_id == twin_run["id"])).all()
    got = Counter(f"{o}|{c}" for o, c in rows)
    assert dict(got) == BASE["findings_by_check"]


def test_dvr_and_tests_match_golden(twin_run):
    with dbm.session_scope() as db:
        days = A.get_table(db, twin_run["id"], "dvr_days")
        tests = A.get_table(db, twin_run["id"], "tests")
        summ = A.get_table(db, twin_run["id"], "summary")
        wells = db.execute(select(WellDailyResult.well_id, func.sum(WellDailyResult.dvr_oil_m3))
                           .where(WellDailyResult.run_id == twin_run["id"]).group_by(WellDailyResult.well_id)).all()
    assert {d["date"]: d["eliminated"] or "" for d in days} == BASE["dvr_eliminated"]
    assert sum(1 for d in days if d["passed_initial"]) == BASE["dvr_passed_initial"]
    assert {t["test_id"]: t["status"] for t in tests} == BASE["tests_status"]
    for k, v in BASE["totals"].items():
        assert abs(sum(r[k] for r in summ if r[k] is not None) - v) <= max(abs(v) * 1e-4, 1e-3), k
    for w, v in wells:
        assert abs(v - BASE["well_oil_dvr"][w]) <= max(abs(v) * 1e-4, 1e-3), w


def test_twin_matches_golden(api, twin_run):
    b = BASE["twin"]
    s = api.get(f"/api/v1/twin/summary?run_id={twin_run['id']}").json()
    assert {str(r["level"]): r["status"] for r in s["levels"]} == b["levels"]
    inst = api.get(f"/api/v1/twin/tables/L3_installation?run_id={twin_run['id']}").json()["rows"]
    assert abs(inst[29]["p50"] - b["l3_oil_30d_p50"]) <= max(abs(b["l3_oil_30d_p50"]) * 0.01, 0.05)
    oil = next(r for r in s["plan_totals"] if r["quantity"] == "oil")
    assert abs((oil["plan"] - oil["base"]) - b["l4_plan_gain"]) <= max(abs(b["l4_plan_gain"]) * 0.01, 0.05)
    assert abs(s["gl_summary"]["lift_opt"] - b["l4_gl_lift_opt"]) <= 1.0
    assert abs(s["l5"]["gain"] - b["l5_gain"]) <= max(abs(b["l5_gain"]) * 0.01, 0.05)
    assert sorted(json.loads(s["l5"]["locked"])) == b["l5_locked"]
    assert s["l5"]["mode"] == "autonomous_sim"


def test_every_result_traces_to_a_run(twin_run):
    with dbm.session_scope() as db:
        run = db.get(Run, twin_run["id"])
        assert run.engine_version and run.config_hash and run.input_batches
        assert run.network_version_id and run.config_version_id
        for model in (Finding, WellDailyResult, RunTable, Recommendation):
            assert db.scalar(select(func.count()).select_from(model).where(model.run_id.is_(None))) == 0
            assert db.scalar(select(func.count()).select_from(model).where(model.run_id == run.id)) > 0
    meta = twin_run
    assert meta["engine_version"] and meta["config_hash"] and meta["input_batches"]
    assert any(f.endswith(".html") for f in meta["files"]) and any(f.endswith(".xlsx") for f in meta["files"])
