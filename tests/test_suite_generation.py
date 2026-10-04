import json

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from agent_trace_review.api import create_app
from agent_trace_review.assessment_contracts import AssessmentSuite, SuiteGenerationInput, TargetDefinition
from agent_trace_review.assessment_store import AssessmentStore
from agent_trace_review.assessments import prepare_assessment, run_assessment
from agent_trace_review.cli import app
from agent_trace_review.storage import Store
from agent_trace_review.suite_generation import generate_suite
from agent_trace_review.util import canonical

HEADERS = {"X-Review-Request": "1"}


def test_reproducible_generation_and_maximum_budget():
    settings = SuiteGenerationInput(cases=30, seed=2147483647)
    first = generate_suite(settings)
    assert canonical(first.model_dump()) == canonical(generate_suite(settings).model_dump())
    assert canonical(first.model_dump()) != canonical(generate_suite(SuiteGenerationInput(cases=30, seed=0)).model_dump())
    assert len(first.cases) == len({case.id for case in first.cases}) == 30
    assert sum(len(case.turns) for case in first.cases) <= 300
    assert first.budgets[0].deadline_seconds * len(first.cases) <= 900
    assert {case.category for case in first.cases} >= {"capability", "memory", "robustness", "session_isolation"}


@pytest.mark.parametrize("values", [
    {"cases": 0}, {"cases": 31}, {"cases": True}, {"seed": -1}, {"seed": 2147483648},
    {"seed": "42"}, {"template": "unknown"}, {"command": "touch /tmp/not-authorized"},
])
def test_generation_rejects_unbounded_or_executable_configuration(values):
    with pytest.raises(ValueError):
        SuiteGenerationInput.model_validate(values)


def test_api_generates_without_scheduling_or_target_calls(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        assert client.post("/api/assessment-suites/generate", json={}).status_code == 403
        response = client.post("/api/assessment-suites/generate", headers=HEADERS, json={"cases": 7, "seed": 81})
        assert response.status_code == 200, response.text
        suite = AssessmentSuite.model_validate(response.json())
        assert len(suite.cases) == 7
        assert client.get("/api/assessments").json() == []
        assert client.post("/api/assessment-suites/generate", headers=HEADERS, json={"cases": 31}).status_code == 422
        assert "suite_generation_input" in client.get("/api/schema").json()


def test_generation_uses_service_authentication(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_REVIEW_SERVICE_TOKEN", "generation-local-service-token")
    with TestClient(create_app(str(tmp_path))) as client:
        assert client.post("/api/assessment-suites/generate", headers=HEADERS, json={}).status_code == 401
        response = client.post("/api/assessment-suites/generate", json={}, headers={
            **HEADERS, "Authorization": "Bearer generation-local-service-token",
        })
        assert response.status_code == 200


def test_cli_writes_valid_file_and_preserves_existing_suite(tmp_path):
    path = tmp_path / "generated" / "suite.json"
    runner = CliRunner()
    command = ["generate-suite", "--cases", "7", "--seed", "123", "--output", str(path)]
    result = runner.invoke(app, command)
    assert result.exit_code == 0, result.output
    initial = path.read_bytes()
    assert AssessmentSuite.model_validate_json(initial).id == "smolagents-generated-123-7"
    assert runner.invoke(app, command).exit_code != 0
    assert path.read_bytes() == initial


def test_generated_checks_reject_noop_and_never_send_answers(tmp_path):
    suite = generate_suite(SuiteGenerationInput(cases=7))
    requests = []

    def noop(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert not {"profile", "rules", "source_refs", "claim_ids"} & payload.keys()
        return httpx.Response(200, json={"output": {"answer": 999999, "code": "WRONG", "stored": False}})

    db = AssessmentStore(Store(tmp_path))
    target = TargetDefinition(id="noop", endpoint="https://local-fixture.example", demo=True)
    job, repository = prepare_assessment(db, target, suite)
    result = run_assessment(db, job["id"], target, suite, repository, transport=httpx.MockTransport(noop))
    assert result["state"] == "completed" and result["completed"] == 7
    assert len(requests) == 8
    assert {case["outcome"] for case in result["results"]} == {"fail"}
