import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from agent_trace_review.api import create_app
from agent_trace_review.cli import app
from agent_trace_review.demo import DemoScenario, demo_bundles, scenario_bundles
from agent_trace_review.models import Verification
from agent_trace_review.reports import markdown_report
from agent_trace_review.service import ingest
from agent_trace_review.storage import Store
from agent_trace_review.util import canonical

HEADERS = {"X-Review-Request": "1"}
OUTCOMES = {"focused": "pass", "iterative": "inconclusive", "failed": "fail"}


def test_legacy_demo_unchanged():
    root = Path(__file__).resolve().parents[1] / "examples"
    expected = [json.loads((root / f"{name}.bundle.json").read_text()) for name in ("focused", "iterative")]
    assert demo_bundles() == expected
    assert scenario_bundles() == expected
    assert scenario_bundles(DemoScenario.all)[:2] == expected


@pytest.mark.parametrize("scenario,outcome", OUTCOMES.items())
def test_scenario_evidence_and_report(tmp_path, scenario, outcome):
    bundle = scenario_bundles(DemoScenario(scenario))[0]
    store = Store(tmp_path)
    run, evaluation, created = ingest(store, canonical(bundle).encode())
    assert created and run.demo
    assert evaluation.outcome == outcome
    assert run.source_format == "opencode"
    for report in bundle["verifications"]:
        verification = Verification.model_validate(report)
        assert verification.state_hash == run.task.final_state_hash
        assert verification.suite_hash == run.task.suite_hash
        assert verification.executed == 8
    evidence_ids = {e.id for e in evaluation.evidence}
    for finding in evaluation.findings:
        assert set(finding.evidence_ids) <= evidence_ids
    for evidence in evaluation.evidence:
        if evidence.artifact_id:
            assert store.get_artifact(evidence.artifact_id)
    report = markdown_report(run, evaluation)
    assert f"**结果：{outcome}**" in report
    assert "证据索引" in report and "如何理解结论" in report
    assert "示例不代表真实 Agent 能力" in report
    again, repeated, created = ingest(store, canonical(bundle).encode())
    assert not created and again.id == run.id and repeated.id == evaluation.id
    if scenario == "failed":
        assert "return now < session.expires_at + 1" in run.diff
        assert bundle["verifications"][0]["failed"] == 1
        assert bundle["verifications"][0]["cases"]["test_session::test_0"] == "fail"


def test_scenarios_are_distinct_and_do_not_mutate_defaults(tmp_path):
    store = Store(tmp_path)
    runs = [ingest(store, canonical(b).encode())[0] for b in scenario_bundles(DemoScenario.all)]
    assert len({r.id for r in runs}) == len({r.session_id for r in runs}) == 3
    changed = scenario_bundles(DemoScenario.failed)[0]
    changed["task"]["final_state_hash"] = "changed"
    assert demo_bundles()[0]["task"]["final_state_hash"] == "demo-final-correct"
    assert scenario_bundles(DemoScenario.failed)[0]["task"]["final_state_hash"] == "demo-final-incorrect"


def test_successful_tool_and_missing_usage_do_not_invent_success(tmp_path):
    bundle = demo_bundles()[0]
    bundle["verifications"] = []
    for message in bundle["export"]["messages"]:
        message["info"].pop("tokens", None)
        message["info"].pop("cost", None)
        for part in message["parts"]:
            part.pop("tokens", None)
            part.pop("cost", None)
    _, evaluation, _ = ingest(Store(tmp_path), canonical(bundle).encode())
    assert evaluation.outcome == "inconclusive"
    metrics = {m.key: m for m in evaluation.metrics}
    for key in ("tokens_total", "cost_usd"):
        assert metrics[key].value is None
        assert metrics[key].status == "unknown"


def test_scenario_api_validation_and_idempotency(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    assert client.post("/api/demo?scenario=failed").status_code == 403
    assert client.post("/api/demo?scenario=invalid", headers=HEADERS).status_code == 422
    assert client.get("/api/runs").json() == []
    original = client.post("/api/demo", headers=HEADERS).json()
    assert original["synthetic"] and len(original["run_ids"]) == 2
    response = client.post("/api/demo?scenario=all", headers=HEADERS)
    assert response.status_code == 200
    ids = response.json()["run_ids"]
    assert len(ids) == 3 and ids[:2] == original["run_ids"]
    for scenario, rid in zip(OUTCOMES, ids, strict=True):
        single = client.post(f"/api/demo?scenario={scenario}", headers=HEADERS).json()
        assert single["run_ids"] == [rid] and single["synthetic"]
        detail = client.get(f"/api/runs/{rid}").json()
        assert detail["evaluation"]["outcome"] == OUTCOMES[scenario]
        assert detail["run"]["demo"]
        report = client.get(f"/api/runs/{rid}/export?format=markdown")
        assert f"**结果：{OUTCOMES[scenario]}**" in report.text
    assert len(client.get("/api/runs").json()) == 3


def test_scenario_cli_defaults_selection_and_report(tmp_path):
    runner = CliRunner()
    args = ["--data-dir", str(tmp_path / "data")]
    invalid = runner.invoke(app, ["demo", "--scenario", "invalid", *args])
    assert invalid.exit_code == 2
    assert not (tmp_path / "data").exists()
    default = runner.invoke(app, ["demo", *args])
    assert default.exit_code == 0, default.output
    assert len(default.output.strip().splitlines()) == 2
    all_result = runner.invoke(app, ["demo", "--scenario", "all", *args])
    assert all_result.exit_code == 0, all_result.output
    assert len(all_result.output.strip().splitlines()) == 3
    for scenario, outcome in OUTCOMES.items():
        selected = runner.invoke(app, ["demo", "--scenario", scenario, *args])
        assert selected.exit_code == 0, selected.output
        assert len(selected.output.strip().splitlines()) == 1
        rid = selected.output.split()[0]
        output = tmp_path / f"{scenario}.md"
        exported = runner.invoke(app, ["report", rid, *args, "--output", str(output)])
        assert exported.exit_code == 0, exported.output
        assert f"**结果：{outcome}**" in output.read_text()
    assert len(Store(tmp_path / "data").list_runs()) == 3
