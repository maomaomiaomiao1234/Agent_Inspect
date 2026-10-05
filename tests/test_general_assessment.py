"""Calibration uses only public prompts/inputs, never the generated answer key."""

import json
import re
from collections import defaultdict

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from agent_trace_review.api import create_app
from agent_trace_review.assessment_contracts import AssessmentSuite, SuiteGenerationInput, TargetDefinition
from agent_trace_review.assessment_quality import quality_summary
from agent_trace_review.assessment_store import AssessmentStore
from agent_trace_review.assessments import assessment_markdown, prepare_assessment, run_assessment
from agent_trace_review.cli import app
from agent_trace_review.contracts import TaskProfile
from agent_trace_review.general_suite import FAMILIES
from agent_trace_review.profiles import comparison_preview
from agent_trace_review.storage import Store
from agent_trace_review.suite_generation import generate_suite


def solve(body):
    prompt, inputs = body["prompt"], body["input"]
    if "records" in inputs:
        records = inputs["records"]
        if "enabled" in records[0]:
            chosen = [r for r in records if r["enabled"] is True]
            return {"ids": [r["id"] for r in chosen], "texts": [r["text"] for r in chosen]}
        if "group" in records[0]:
            totals = defaultdict(int)
            for row in records:
                totals[row["group"]] += row["amount"]
            return {"totals": dict(totals)}
        chosen = [r for r in records if r["amount"] > 0]
        return {"ids": [r["id"] for r in chosen], "total": sum(r["amount"] for r in chosen)}
    if "values" in inputs:
        return {"values": sorted(set(inputs["values"]), reverse=True)}
    if "documents" in inputs:
        query = re.search(r"parcel ([^ ]+) by exact", prompt)[1]
        matches = [d for d in inputs["documents"] if d["text"].startswith(f"Parcel {query} has destination ")]
        destinations = {d["text"].split("destination ")[1].rstrip(".") for d in matches}
        return {"destination": next(iter(destinations)) if len(destinations) == 1 else None,
                "citations": sorted(d["id"] for d in matches)}
    if prompt.startswith("Compute"):
        a, b, c = map(int, re.search(r"\((-?\d+) \+ (\d+)\) \* (\d+)", prompt).groups())
        return {"answer": (a + b) * c}
    if prompt.startswith("Order"):
        quantity, price = map(int, re.search(r"Order (\d+) items at (\d+)", prompt).groups())
        return {"total": quantity * price}
    if prompt.startswith("Correction"):
        initial = body["messages"][0]["content"]
        quantity = int(re.search(r"quantity with (\d+)", prompt)[1])
        price = int(re.search(r"items at (\d+)", initial)[1])
        return {"total": quantity * price}
    if prompt.startswith("Calculate"):
        return {"needs_clarification": True, "total": None}
    if prompt.startswith("The unit price"):
        quantity = int(re.search(r"for (\d+) items", body["messages"][0]["content"])[1])
        price = int(re.search(r"price is (\d+)", prompt)[1])
        return {"needs_clarification": False, "total": quantity * price}
    raise AssertionError(prompt)


def execute(tmp_path, mode="correct", **settings):
    suite = generate_suite(SuiteGenerationInput(template="general", **settings))
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        assert set(body) == {"protocol", "session_id", "turn", "prompt", "messages", "input", "budget"}
        if mode == "timeout":
            raise httpx.ReadTimeout("fixture timeout")
        output = solve(body)
        if mode == "empty":
            output = {}
        elif mode == "wrong":
            output = {k: "wrong" for k in output}
        elif mode == "wrong-first-turn" and body["turn"] == 0:
            output = {k: "wrong" for k in output}
        elif mode == "wrong-citations" and output.get("citations"):
            output["citations"] = ["invented-source"]
        return httpx.Response(200, json={"output": output})

    db = AssessmentStore(Store(tmp_path))
    target = TargetDefinition(id="calibration", endpoint="https://fixture.example", demo=True)
    job, repo = prepare_assessment(db, target, suite)
    result = run_assessment(db, job["id"], target, suite, repo, transport=httpx.MockTransport(respond))
    return result, suite, requests


@pytest.mark.parametrize("seed", [0, 42, 2147483647])
def test_general_calibrates_all_families_from_public_inputs(tmp_path, seed):
    job, suite, requests = execute(tmp_path, seed=seed, cases=30, attempts=3, concurrency=4)
    assert job["state"] == "completed"
    assert len(job["results"]) == 90
    assert {r["outcome"] for r in job["results"]} == {"pass"}
    assert {c.id.rsplit("-", 1)[0] for c in suite.cases} == set(FAMILIES)
    assert len({c.category for c in suite.cases}) == 7
    assert len({r["session_id"] for r in job["results"]}) == 90
    assert len(requests) == sum(len(c.turns) for c in suite.cases) * 3
    assert suite == generate_suite(SuiteGenerationInput(template="general", cases=30, seed=seed, attempts=3, concurrency=4))
    summary = job["quality"]["summary"]
    assert summary["conclusion"] == "pass" and summary["pending"] == 0
    assert {d["id"] for d in summary["uncovered_categories"]} == {"tool_use", "memory", "session_isolation"}
    assert {s["status"] for s in job["quality"]["stability"]} == {"consistent_pass"}


@pytest.mark.parametrize("mode", ["empty", "wrong", "timeout"])
def test_negative_controls_and_execution_faults_are_distinct(tmp_path, mode):
    job, _, _ = execute(tmp_path, mode)
    expected = "inconclusive" if mode == "timeout" else "fail"
    assert {r["outcome"] for r in job["results"]} == {expected}
    summary = job["quality"]["summary"]
    assert summary[expected] == 12
    assert summary["execution_issues"] == (12 if mode == "timeout" else 0)
    assert {r["guidance"]["kind"] for r in job["results"]} == {"execution" if mode == "timeout" else "acceptance"}
    report = assessment_markdown(job)
    assert "结论与下一步" in report and "优先处理" in report and "尚未覆盖" in report
    if mode == "empty":
        assert "字段缺失" in report and "期望：" in report and "实际返回：" in report


def test_multiturn_checks_do_not_accept_only_the_final_answer(tmp_path):
    job, _, _ = execute(tmp_path, "wrong-first-turn")
    turns = [r for r in job["results"] if r["category"] == "multi_turn"]
    assert len(turns) == 2
    for row in turns:
        assert row["outcome"] == "fail"
        assert any(c["id"].startswith("first-") and c["status"] == "fail" for c in row["checks"])
        assert all(c["status"] == "pass" for c in row["checks"] if not c["id"].startswith("first-"))


def test_correct_fact_with_fabricated_citation_fails(tmp_path):
    job, _, _ = execute(tmp_path, "wrong-citations")
    affected = [r for r in job["results"] if r["case_id"].split("-")[0] in {"lookup", "injection", "conflict"}]
    assert len(affected) == 3 and all(r["outcome"] == "fail" for r in affected)
    assert all(next(c for c in r["checks"] if c["id"] == "correct-destination")["status"] == "pass" for r in affected)


def test_pending_summary_keeps_denominator_and_cannot_pass():
    suite = generate_suite(SuiteGenerationInput(template="general"))
    summary = quality_summary([], suite, None)["summary"]
    assert summary["conclusion"] == "inconclusive"
    assert summary["pending"] == summary["planned"] == 12
    assert summary["inconclusive"] == summary["fail"] == summary["pass"] == 0


def test_comparison_distinguishes_missing_null_and_redacts_before_truncating():
    rule = TaskProfile.model_validate({"profile_version": "1", "id": "comparison", "rules": [
        {"id": "answer", "path": "/output/answer", "op": "equals", "value": None},
    ]}).rules[0]
    assert comparison_preview(rule, {"output": {}})["actual"] == "（字段缺失）"
    assert comparison_preview(rule, {"output": {"answer": None}})["actual"] == "null"
    assert comparison_preview(rule, {"output": {"answer": False}})["actual"] == "false"
    result = comparison_preview(rule, {"output": {"answer": {"api_key": "private-value", "body": "a" * 2000}}})
    assert "private-value" not in result["actual"] and "已截断" in result["actual"]
    assert len(result["actual"]) < 650


def test_general_api_and_cli_preserve_repeats_and_do_not_schedule(tmp_path):
    with TestClient(create_app(str(tmp_path / "api"))) as client:
        response = client.post("/api/assessment-suites/generate", headers={"X-Review-Request": "1"},
                               json={"template": "general", "attempts": 3, "concurrency": 4})
        assert response.status_code == 200
        assert response.json()["attempts"] == 3 and response.json()["concurrency"] == 4
        assert client.get("/api/assessments").json() == []
        invalid = client.post("/api/assessment-suites/generate", headers={"X-Review-Request": "1"},
                              json={"template": "smolagents", "cases": 30, "attempts": 3})
        assert invalid.status_code == 422
    output = tmp_path / "suite.json"
    result = CliRunner().invoke(app, ["generate-suite", "--template", "general", "--attempts", "2",
                                     "--concurrency", "3", "--output", str(output)])
    assert result.exit_code == 0, result.output
    suite = AssessmentSuite.model_validate_json(output.read_bytes())
    assert suite.attempts == 2 and suite.concurrency == 3
