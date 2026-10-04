import json
import re

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from agent_trace_review.api import create_app
from agent_trace_review.assessment_contracts import AssessmentSuite, RepositoryPlanInput, TargetDefinition
from agent_trace_review.assessment_store import AssessmentStore
from agent_trace_review.assessments import assessment_markdown, prepare_assessment, run_assessment
from agent_trace_review.cli import app
from agent_trace_review.repositories import inspect_repository
from agent_trace_review.repository_contracts import RepositoryAssessmentInput
from agent_trace_review.repository_planning import plan_repository
from agent_trace_review.source_tools import python_tools
from agent_trace_review.storage import Store

HEADERS = {"X-Review-Request": "1"}


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "agent"
    root.mkdir()
    (root / "README.md").write_text("RAG retrieval, memory, web search and tools.\n")
    (root / "agent.py").write_text('''
raise RuntimeError("This source must never execute")
@tool
def calculator(a: float, b: float, operation: str): pass
class Search(BaseTool):
    name: str = "search"
    def _run(self, query: str): pass
''')
    return root


def oracle_transport(mode="correct"):
    """Derive answers from PUBLIC request data, never from profiles/expected values."""
    sessions = {}

    def respond(request):
        body = json.loads(request.content)
        assert not {"profile", "rules", "source_refs", "claim_ids", "expected"} & body.keys()
        prompt, inputs = body["prompt"], body["input"]
        trace = None
        if "records" in inputs:
            rows = [r for r in inputs["records"] if r["amount"] > 0]
            output = {"ids": [r["id"] for r in rows], "total": sum(r["amount"] for r in rows)}
        elif "values" in inputs:
            output = {"values": sorted(set(inputs["values"]), reverse=True)}
        elif "documents" in inputs:
            parcel = re.search(r"parcel (P\d+-\d+)", prompt)[1]
            docs = [d for d in inputs["documents"] if f"Parcel {parcel} " in d["text"]]
            output = {"destination": docs[0]["text"].split("destination ")[1].rstrip(".") if docs else None,
                      "citations": [docs[0]["id"]] if docs else []}
            if mode == "wrong-citation" and docs:
                output["citations"] = ["invented-document"]
        elif prompt.startswith("Remember code"):
            sessions[body["session_id"]] = prompt.split()[2]
            output = {"stored": True}
        elif "remembered" in prompt:
            output = {"code": sessions.get(body["session_id"], "NONE")}
        else:
            a, b, c = map(int, re.search(r"\((-?\d+) \+ (\d+)\) \* (\d+)", prompt).groups())
            output = {"answer": (a + b) * c}
            if "calculator tool" in prompt and mode != "no-trace":
                trace = {"session_id": body["session_id"], "turn": body["turn"], "coverage": "complete",
                         "events": [{"id": "tool-1", "kind": "tool", "tool": "calculator",
                                     "status": "completed", "output": output}]}
        if mode == "wrong":
            output = {"answer": "wrong", "code": "wrong", "stored": False, "ids": [], "total": -999,
                      "values": [], "destination": "wrong", "citations": ["wrong"]}
        return httpx.Response(200, json={"output": {**output, "_execution": {"framework": "fixture"}}, "trace": trace})
    return httpx.MockTransport(respond)


def execute(source, data, mode="correct", **settings):
    repository = inspect_repository(source)
    plan = plan_repository(repository, RepositoryPlanInput(**settings))
    suite = AssessmentSuite.model_validate(plan["suite"])
    db = AssessmentStore(Store(data))
    target = TargetDefinition(id="fixture", endpoint="https://fixture.example", repository=str(source), demo=True)
    job, snapshot = prepare_assessment(db, target, suite)
    return run_assessment(db, job["id"], target, suite, snapshot, transport=oracle_transport(mode)), plan


def test_source_tools_static_only_and_bounded(source):
    snapshot = inspect_repository(source)
    assert [(t["name"], t["parameters"]) for t in snapshot["tools"]] == [
        ("calculator", ["a", "b", "operation"]), ("search", ["query"])]
    assert python_tools("def syntax broken", "broken.py") == []
    assert len(python_tools("\n".join(f"@tool\ndef t{i}(): pass" for i in range(200)), "many.py")) == 100


def test_source_tools_do_not_bypass_credential_redaction():
    content = 'class T(Tool):\n    name = "sk-' + 'x' * 24 + '"\n    def forward(self): pass\n'
    assert python_tools(content, "tool.py") == []


def test_plan_reproducible_relevant_bounded_and_not_claim_certification(source):
    repo = inspect_repository(source)
    settings = RepositoryPlanInput(cases=30, attempts=3, concurrency=4)
    plan = plan_repository(repo, settings)
    assert plan == plan_repository(repo, settings)
    assert plan != plan_repository(repo, settings.model_copy(update={"seed": 99}))
    assert plan["max_serial_deadline_seconds"] == 900
    assert plan["estimated_requests"] <= 300
    assert {c["category"] for c in plan["suite"]["cases"]} == {
        "tool_use", "memory", "session_isolation", "retrieval", "grounding", "robustness",
        "instruction_following", "structured_output", "capability"}
    assert all(not c["claim_ids"] for c in plan["suite"]["cases"])
    assert any(g["name"] == "search" and g["kind"] == "tool" for g in plan["gaps"])
    assert all(c["source_refs"] for c in plan["suite"]["cases"] if c["category"] in {"retrieval", "memory", "tool_use"})
    short = plan_repository(repo, RepositoryPlanInput(cases=1))
    assert any(g["kind"] == "dimension" for g in short["gaps"])


@pytest.mark.parametrize("mode", ["correct", "wrong", "no-trace", "wrong-citation"])
def test_independent_oracle_checks_outcomes_citations_and_unknown_tools(source, tmp_path, mode):
    result, plan = execute(source, tmp_path / "data", mode, cases=9, attempts=2, concurrency=3)
    assert result["state"] == "completed" and result["completed"] == 18
    assert len({r["session_id"] for r in result["results"]}) == 18
    if mode == "correct":
        assert {r["outcome"] for r in result["results"]} == {"pass"}
        assert {s["status"] for s in result["quality"]["stability"]} == {"consistent_pass"}
    elif mode == "wrong":
        assert {r["outcome"] for r in result["results"]} == {"fail"}
    elif mode == "no-trace":
        assert {r["outcome"] for r in result["results"] if r["category"] == "tool_use"} == {"inconclusive"}
    else:
        assert {r["outcome"] for r in result["results"] if r["category"] in {"retrieval", "robustness"}} == {"fail"}
    assert result["quality"]["evidence"]["token_known_runs"] == 0
    assert all(c["assessment_status"] == "untested" for c in result["claims"])
    assert "维度覆盖与质量" in assessment_markdown(result)
    assert plan["suite"]["repository_source_hash"] == result["source_hash"]


def test_plan_binding_rejects_changed_source(source, tmp_path):
    suite = AssessmentSuite.model_validate(plan_repository(inspect_repository(source), RepositoryPlanInput())["suite"])
    (source / "agent.py").write_text("# changed\n")
    db = AssessmentStore(Store(tmp_path / "data"))
    target = TargetDefinition(id="fixture", endpoint="https://fixture.example", repository=str(source))
    with pytest.raises(ValueError, match="源码已改变"):
        prepare_assessment(db, target, suite)
    assert db.list_jobs() == []


def test_api_plan_auth_and_cli_preserves_output(source, tmp_path):
    store = Store(tmp_path / "data")
    repo = AssessmentStore(store).save_repository(inspect_repository(source))
    path = f"/api/assessment-suites/plan/{repo['id']}"
    with TestClient(create_app(str(store.root))) as client:
        assert client.post(path, json={}).status_code == 403
        assert client.post(path, json={"concurrency": 5}, headers=HEADERS).status_code == 422
        response = client.post(path, json={"cases": 9}, headers=HEADERS)
        assert response.status_code == 200
        assert response.json()["source_hash"] == repo["source_hash"]
        assert client.get("/api/assessments").json() == []
    output = tmp_path / "suite.json"
    command = ["plan-repository", str(source), "--output", str(output)]
    runner = CliRunner()
    assert runner.invoke(app, command).exit_code == 0
    before = output.read_bytes()
    assert runner.invoke(app, command).exit_code != 0
    assert output.read_bytes() == before


def test_repository_job_allows_explicit_planning_without_template():
    request = RepositoryAssessmentInput(repository_url="https://github.com/example/agent", planning={})
    assert request.planning.cases == 12
    with pytest.raises(ValueError):
        RepositoryPlanInput(concurrency=True)
