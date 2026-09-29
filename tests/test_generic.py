import copy

import pytest
from fastapi.testclient import TestClient

from agent_trace_review.analysis import compare
from agent_trace_review.api import create_app
from agent_trace_review.service import ingest
from agent_trace_review.util import canonical


@pytest.fixture
def trace():
    return {
        "trace_version": "1",
        "framework": "invoice-agent",
        "run_id": "invoice-1",
        "title": "Invoice extraction",
        "demo": True,
        "coverage": "complete",
        "start_ms": 1000,
        "end_ms": 3000,
        "events": [
            {
                "id": "m1",
                "kind": "llm",
                "status": "completed",
                "usage": {"tokens": {"input": 100, "output": 20, "total": 120}, "cost_usd": 0.01},
            },
            {
                "id": "t1",
                "kind": "tool",
                "tool": "ocr",
                "effect": "read",
                "status": "completed",
                "input": {"document": "invoice"},
                "output": "invoice A-100",
            },
            {
                "id": "t2",
                "kind": "tool",
                "tool": "ocr",
                "effect": "read",
                "status": "completed",
                "input": {"document": "invoice"},
                "output": "invoice A-100",
            },
        ],
        "output": {"invoice_no": "A-100", "amount": 128.5, "currency": "CNY"},
    }


@pytest.fixture
def profile():
    return {
        "profile_version": "1",
        "id": "invoice",
        "version": "1",
        "rules": [
            {"id": "number", "op": "equals", "path": "/output/invoice_no", "value": "A-100"},
            {"id": "amount", "op": "equals", "path": "/output/amount", "value": 128.5},
            {
                "id": "budget",
                "dimension": "resource",
                "op": "max",
                "path": "/metrics/tokens_total",
                "value": 200,
            },
        ],
    }


def test_generic_import_metrics_and_roundtrip(store, trace):
    run, evaluation, created = ingest(store, canonical(trace).encode())
    assert created and run.source_format == "generic" and run.framework == "invoice-agent"
    assert evaluation.outcome == "inconclusive"
    metrics = {m.key: m.value for m in evaluation.metrics}
    assert metrics["tokens_total"] == 120 and metrics["cost_usd"] == 0.01
    assert metrics["duplicate_calls"] == 1
    assert metrics["duration_ms"] == 2000
    assert "final_change_unverified" not in {f.category for f in evaluation.findings}
    again, _, created = ingest(store, canonical(store.bundle(run.id)).encode())
    assert not created and again.id == run.id
    assert {ref for f in evaluation.findings for ref in f.evidence_ids} <= {e.id for e in evaluation.evidence}


def test_profile_acceptance_and_failed_output(store, trace, profile):
    _, evaluation, _ = ingest(store, canonical(trace).encode(), profile=profile)
    assert evaluation.outcome == "pass"
    assert len(evaluation.custom["checks"]) == 3
    trace["output"]["amount"] = 12
    _, evaluation, _ = ingest(store, canonical(trace).encode(), profile=profile)
    assert evaluation.outcome == "fail"
    assert evaluation.custom["checks"][1]["status"] == "fail"
    assert {ref for f in evaluation.findings for ref in f.evidence_ids} <= {e.id for e in evaluation.evidence}


def test_generic_writes_do_not_trigger_code_checks(store, trace):
    trace["events"].append(
        {"id": "w", "kind": "tool", "tool": "send_email", "effect": "write", "status": "completed"}
    )
    run, result, _ = ingest(store, canonical(trace).encode())
    assert "final_change_unverified" not in {f.category for f in result.findings}
    assert run.events[-1].effect == "write"


@pytest.mark.parametrize("mutation", ["duplicate", "time", "negative", "nan", "usage_on_tool"])
def test_invalid_generic_input(store, trace, mutation):
    if mutation == "duplicate":
        trace["events"].append(copy.deepcopy(trace["events"][0]))
    if mutation == "time":
        trace["end_ms"] = 500
    if mutation == "negative":
        trace["events"][0]["usage"]["tokens"]["total"] = -1
    if mutation == "nan":
        trace["events"][0]["usage"]["cost_usd"] = float("nan")
    if mutation == "usage_on_tool":
        trace["events"][1]["usage"] = {"cost_usd": 1}
    import json

    with pytest.raises(ValueError):
        ingest(store, json.dumps(trace).encode())


def test_missing_and_partial_evidence_not_pass(store, trace, profile):
    trace["events"].append({"id": "m2", "kind": "llm", "status": "completed"})
    _, result, _ = ingest(store, canonical(trace).encode(), profile=profile)
    assert result.outcome == "inconclusive"
    assert result.custom["checks"][-1]["status"] == "unknown"
    trace.pop("output")
    _, result, _ = ingest(store, canonical(trace).encode(), profile=profile)
    assert result.custom["checks"][0]["status"] == "unknown"


def test_only_resource_checks_cannot_prove_task_completion(store, trace):
    profile = {
        "profile_version": "1",
        "id": "budget",
        "rules": [
            {"id": "cheap", "dimension": "resource", "op": "max", "path": "/metrics/cost_usd", "value": 1}
        ],
    }
    assert ingest(store, canonical(trace).encode(), profile=profile)[1].outcome == "inconclusive"


def test_tool_absence_requires_complete_coverage(store, trace):
    profile = {
        "profile_version": "1",
        "id": "policy",
        "rules": [{"id": "no_email", "op": "tool_forbidden", "value": "send_email"}],
    }
    trace["coverage"] = "partial"
    result = ingest(store, canonical(trace).encode(), profile=profile)[1]
    assert result.custom["checks"][0]["status"] == "unknown"


def test_external_results_bound_to_request(store, trace, profile):
    from agent_trace_review.profiles import evaluate_profile, evaluator_request

    profile["rules"].append({"id": "business", "op": "external", "description": "业务校验"})
    run, _, _ = ingest(store, canonical(trace).encode())
    request = evaluator_request(run, profile)
    response = {k: request[k] for k in ("protocol", "run_id", "input_hash", "profile_hash")}
    response["results"] = [
        {
            "id": "business",
            "status": "pass",
            "explanation": "匹配业务标准",
            "evidence_paths": ["/output/amount"],
        }
    ]
    result = evaluate_profile(store, run, profile, response)
    assert result.outcome == "pass"
    assert store.get_evaluation(run.id).id == result.id
    response["input_hash"] = "stale"
    with pytest.raises(ValueError, match="hash|匹配"):
        evaluate_profile(store, run, profile, response)


@pytest.mark.parametrize("change", ["unknown_id", "duplicate", "pointer", "no_evidence"])
def test_external_bad_evidence_rejected(store, trace, profile, change):
    from agent_trace_review.profiles import evaluate_profile, evaluator_request

    profile["rules"] = [{"id": "business", "op": "external"}]
    run = ingest(store, canonical(trace).encode())[0]
    request = evaluator_request(run, profile)
    response = {k: request[k] for k in ("protocol", "run_id", "input_hash", "profile_hash")}
    response["results"] = [
        {"id": "business", "status": "pass", "explanation": "ok", "evidence_paths": ["/output/amount"]}
    ]
    if change == "unknown_id":
        response["results"][0]["id"] = "other"
    if change == "duplicate":
        response["results"] *= 2
    if change == "pointer":
        response["results"][0]["evidence_paths"] = ["/absent"]
    if change == "no_evidence":
        response["results"][0]["evidence_paths"] = []
    with pytest.raises(ValueError):
        evaluate_profile(store, run, profile, response)


def test_profile_cannot_override_coding_failure(store, bundle, profile):
    bundle["task"]["protected_paths"] = ["auth/*"]
    profile["rules"] = [{"id": "has_events", "op": "length_min", "path": "/events", "value": 1}]
    assert ingest(store, canonical(bundle).encode(), profile=profile)[1].outcome == "fail"


def test_profile_comparison_uses_profile_hash(store, trace, profile):
    left, a, _ = ingest(store, canonical(trace).encode(), profile=profile)
    profile["rules"][0]["value"] = "other"
    right, b, _ = ingest(store, canonical(trace).encode(), profile=profile)
    assert any(issue["field"] == "profile_hash" for issue in compare(left, right, a, b)["issues"])


def test_generic_api_profile_flow(tmp_path, trace, profile):
    client = TestClient(create_app(str(tmp_path / "data")))
    headers = {"X-Review-Request": "1"}
    response = client.post(
        "/api/imports",
        headers=headers,
        files={
            "file": ("trace.json", canonical(trace)),
            "profile_file": ("profile.json", canonical(profile)),
        },
    )
    assert response.status_code == 200, response.text
    run_id = response.json()["run_id"]
    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["evaluation"]["outcome"] == "pass"
    request = client.post(f"/api/runs/{run_id}/evaluator-request", headers=headers, json=profile)
    assert request.status_code == 200
    evaluated = client.post(
        f"/api/runs/{run_id}/profile-evaluations", headers=headers, json={"profile": profile}
    )
    assert evaluated.status_code == 200
    assert "generic_trace" in client.get("/api/schema").json()


def test_declared_unknown_side_effects_break_duplicate_detection(store, trace):
    trace["events"].insert(2, {"id": "other", "kind": "tool", "tool": "action", "status": "completed"})
    evaluation = ingest(store, canonical(trace).encode())[1]
    assert not any(f.category == "duplicate_readonly_call" for f in evaluation.findings)


def test_generic_failure_recovery(store, trace):
    trace["events"] = [
        {
            "id": str(i),
            "kind": "tool",
            "tool": "api.fetch",
            "status": "error",
            "input": {"key": 1},
            "output": "HTTP 503",
        }
        for i in range(3)
    ] + [
        {
            "id": "success",
            "kind": "tool",
            "tool": "api.fetch",
            "status": "completed",
            "input": {"key": 1},
            "output": "ok",
        }
    ]
    evaluation = ingest(store, canonical(trace).encode())[1]
    assert {f.category for f in evaluation.findings} == {"repeated_tool_failure", "tool_recovery"}


@pytest.mark.parametrize(
    "actual,value,expected",
    [
        ({"a": True}, {"a": 1}, "fail"),
        ([True], [1], "fail"),
        (1, 1.0, "pass"),
        ("[redacted:secret]", "[redacted:secret]", "unknown"),
    ],
)
def test_profile_json_types_and_redaction(store, trace, actual, value, expected):
    trace["output"] = actual
    profile = {
        "profile_version": "1",
        "id": "types",
        "rules": [{"id": "exact", "op": "equals", "path": "/output", "value": value}],
    }
    result = ingest(store, canonical(trace).encode(), profile=profile)[1]
    assert result.custom["checks"][0]["status"] == expected


@pytest.mark.parametrize(
    "op,path,value,status",
    [
        ("exists", "/output/amount", None, "pass"),
        ("exists", "/output/absent", None, "fail"),
        ("contains", "/output/currency", "CN", "pass"),
        ("min", "/output/amount", 130, "fail"),
        ("max", "/output/amount", 130, "pass"),
        ("length_max", "/output/invoice_no", 5, "pass"),
        ("tool_required", "", "ocr", "pass"),
        ("tool_forbidden", "", "ocr", "fail"),
    ],
)
def test_rule_operations(store, trace, op, path, value, status):
    profile = {
        "profile_version": "1",
        "id": "operations",
        "rules": [{"id": "rule", "op": op, "path": path, "value": value}],
    }
    result = ingest(store, canonical(trace).encode(), profile=profile)[1]
    assert result.custom["checks"][0]["status"] == status


def test_unknown_rules_and_executable_commands_rejected(store, trace):
    for rule in [
        {"id": "bad", "op": "execute", "value": "echo 1"},
        {"id": "bad", "op": "external", "command": "echo 1"},
    ]:
        with pytest.raises(ValueError):
            ingest(
                store,
                canonical(trace).encode(),
                profile={"profile_version": "1", "id": "bad", "rules": [rule]},
            )
    assert store.list_runs() == []


def test_generic_comparable_without_git_commit(store, trace, profile):
    task = {
        "id": "invoice",
        "prompt": "extract",
        "initial_state_hash": "same-input",
        "environment_hash": "same-env",
        "suite_hash": "same-suite",
        "budget_policy": "same-budget",
    }
    left, a, _ = ingest(store, canonical(trace).encode(), task=task, profile=profile)
    trace["run_id"] = "invoice-2"
    right, b, _ = ingest(store, canonical(trace).encode(), task=task, profile=profile)
    assert a.outcome == b.outcome == "pass"
    assert compare(left, right, a, b)["comparable"]


def test_agent_version_bundle_roundtrip(store, trace):
    run = ingest(store, canonical(trace).encode(), agent_version="custom-2")[0]
    again = ingest(store, canonical(store.bundle(run.id)).encode())[0]
    assert run.id == again.id


def test_research_template_does_not_claim_fact_validation(store):
    import json
    from importlib.resources import files

    template = files("agent_trace_review").joinpath("templates", "research")
    result = ingest(
        store,
        template.joinpath("trace.json").read_bytes(),
        profile=json.loads(template.joinpath("profile.json").read_text()),
    )[1]
    assert result.outcome == "inconclusive"
    assert next(c for c in result.custom["checks"] if c["id"] == "facts")["status"] == "unknown"


def test_cli_template_and_external_evaluator(tmp_path):
    import json
    import subprocess
    import sys

    from typer.testing import CliRunner

    from agent_trace_review.cli import app

    runner = CliRunner()
    task = tmp_path / "task"
    data = tmp_path / "data"
    assert runner.invoke(app, ["init-task", str(task)]).exit_code == 0
    assert runner.invoke(app, ["init-task", str(task)]).exit_code != 0
    imported = runner.invoke(
        app,
        [
            "import",
            str(task / "trace.json"),
            "--data-dir",
            str(data),
            "--profile",
            str(task / "profile.json"),
        ],
    )
    assert imported.exit_code == 0, imported.output
    run_id = json.loads(imported.output)["run_id"]
    assert json.loads(imported.output)["outcome"] == "pass"
    args = [run_id, "--data-dir", str(data), "--profile", str(task / "external.profile.json")]
    requested = runner.invoke(app, ["evaluator-request", *args, "--output", str(task / "request.json")])
    assert requested.exit_code == 0, requested.output
    subprocess.run(
        [sys.executable, str(task / "evaluator.py"), str(task / "request.json"), str(task / "results.json")],
        check=True,
    )
    result = runner.invoke(app, ["evaluate", *args, "--results", str(task / "results.json")])
    assert result.exit_code == 0 and json.loads(result.output)["outcome"] == "pass"
    report = runner.invoke(app, ["report", run_id, "--data-dir", str(data)])
    assert report.exit_code == 0 and "invoice_total" in report.output or "独立参考" in report.output
