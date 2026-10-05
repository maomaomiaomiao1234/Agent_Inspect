import copy
import json

import httpx
import pytest

from agent_trace_review.assessment_contracts import AssessmentSuite, TargetDefinition, TargetResponse
from agent_trace_review.assessment_store import AssessmentStore
from agent_trace_review.assessments import (
    assessment_bundle,
    assessment_markdown,
    prepare_assessment,
    run_assessment,
    telemetry_readiness,
)
from agent_trace_review.target_client import TargetClient, TargetError
from agent_trace_review.util import canonical


def calls():
    return [
        {"id": "m0", "kind": "llm", "model": "fixture-model", "status": "completed", "duration_ms": 3,
         "usage": {"tokens": {"input": 10, "output": 5, "total": 15}}},
        {"id": "t0", "parent_id": "m0", "kind": "tool", "tool": "calculator", "effect": "read",
         "status": "completed", "input": {"a": 1, "b": 2}, "output": {"answer": 3}, "duration_ms": 2},
        {"id": "m1", "kind": "llm", "model": "fixture-model", "status": "completed", "duration_ms": 4,
         "usage": {"tokens": {"input": 20, "output": 5, "total": 25}}},
        {"id": "t1", "parent_id": "m1", "kind": "tool", "tool": "final_answer", "status": "completed",
         "input": {"answer": {"answer": 3}}, "output": {"answer": 3}, "duration_ms": 1},
    ]


def response(request, *, coverage="complete", aggregate=True):
    body = {"protocol": "agent-review/target-v1", "output": {"answer": 3}, "trace": {
        "session_id": request["session_id"], "turn": request["turn"], "coverage": coverage, "events": calls(),
    }}
    if aggregate:
        body["usage"] = {"tokens": {"input": 30, "output": 10, "total": 40}}
    return body


def suite(turns=2):
    return AssessmentSuite.model_validate({
        "id": "telemetry-fixture", "budgets": [{"id": "test", "deadline_seconds": 30, "max_output_tokens": 30}],
        "cases": [{"id": "addition", "turns": [{"prompt": "Compute 1 + 2."}] * turns, "profile": {
            "profile_version": "1", "id": "addition", "rules": [
                {"id": "answer", "op": "equals", "path": "/output/answer", "value": 3},
                {"id": "calculator", "dimension": "behavior", "op": "tool_required", "value": "calculator"},
                {"id": "no-email", "dimension": "behavior", "op": "tool_forbidden", "value": "email.send"},
                {"id": "tokens", "dimension": "resource", "op": "max", "path": "/metrics/tokens_total", "value": 100},
            ],
        }}],
    })


def execute(store, respond, *, turns=2):
    target = TargetDefinition(id="fixture", endpoint="http://fixture.test", demo=True)
    assessment = suite(turns)
    db = AssessmentStore(store)
    job, _ = prepare_assessment(db, target, assessment)
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=respond(json.loads(r.content))))
    return run_assessment(db, job["id"], target, assessment, transport=transport)


@pytest.mark.parametrize("aggregate", [True, False])
def test_model_usage_counted_once_across_turns_and_profiles_use_tools(store, aggregate):
    job = execute(store, lambda r: response(r, aggregate=aggregate))
    row = job["results"][0]
    assert row["outcome"] == "pass" and row["execution_state"] == "completed"
    assert {k: v for k, v in row["usage"].items() if k != "fields"} == {"input_tokens": 60, "output_tokens": 20, "total_tokens": 80,
                            "cost_usd": None, "provenance": "target_reported", "output_token_budget": "pass"}
    assert job["curves"][0]["reported_total_tokens"] == 80
    assert row["usage"]["fields"]["total_tokens"]["source"] == ("aggregate" if aggregate else "calls")
    telemetry = row["telemetry"]
    assert telemetry["coverage"] == "complete"
    assert (telemetry["llm_calls"], telemetry["tool_calls"], telemetry["llm_errors"], telemetry["tool_errors"]) == (4, 4, 0, 0)
    assert telemetry["llm_duration_ms"] == 14 and telemetry["tool_duration_ms"] == 6
    run = store.get_run(row["run_id"])
    assert run.trace_complete and run.models == ["fixture-model"]
    assert len({e.native_id for e in run.events}) == len(run.events) == 12
    evaluation = store.get_evaluation(run.id, row["revision_id"])
    metrics = {m.key: m for m in evaluation.metrics}
    assert metrics["tokens_total"].value == 80 and metrics["tokens_total"].status == "observed"
    assert metrics["llm_calls"].value == 4 and metrics["tool_calls"].value == 4
    target_events = [e for e in run.events if e.kind in {"tool", "llm"}]
    assert all(e.data["context"]["session_id"] == row["session_id"] for e in target_events)
    assert {e.data["context"]["turn"] for e in target_events} == {0, 1}
    assert all(e.data["context"]["case_id"] == "addition" for e in target_events)
    assert all(e.data["provenance"] == "target_reported" for e in target_events)
    assert target_events[1].parent_message_id == target_events[0].native_id
    assert "模型 4；工具 4" in assessment_markdown(job)
    exported = assessment_bundle(AssessmentStore(store), job)
    trace = exported["runs"][0]["bundle"]["trace"]
    assert len([e for e in trace["events"] if e["kind"] == "llm"]) == 4


def test_partial_trace_does_not_hide_independent_aggregate_or_confirm_absent_tools(store):
    job = execute(store, lambda r: response(r, coverage="partial"))
    row = job["results"][0]
    checks = {c["id"]: c["status"] for c in row["checks"]}
    assert checks == {"answer": "pass", "calculator": "pass", "no-email": "unknown", "tokens": "pass"}
    assert row["outcome"] == "inconclusive" and row["telemetry"]["coverage"] == "partial"
    assert row["usage"]["total_tokens"] == 80  # Independent per-turn reported aggregates, not trace sums.


def test_old_target_keeps_usage_and_does_not_invent_model_or_tool_calls(store):
    def old(request):
        result = response(request)
        del result["trace"]
        return result
    row = execute(store, old)["results"][0]
    assert row["telemetry"]["coverage"] == "unavailable"
    assert row["telemetry"]["llm_calls"] is None and row["telemetry"]["tool_calls"] is None
    assert row["usage"]["total_tokens"] == 80
    run = store.get_run(row["run_id"])
    assert not run.trace_complete and not run.usage
    assert not any(e.kind in {"llm", "tool"} for e in run.events)
    assert run.usage_summary.fields["total_tokens"].value == 80
    assert {c["id"]: c["status"] for c in row["checks"]}["tokens"] == "pass"


def test_target_failure_keeps_calls_and_stops_later_turns(store):
    received = []
    def failed(request):
        received.append(request)
        body = response(request, aggregate=False)
        body.update(output=None, execution_status="error", error="agent_execution_failed")
        body["trace"]["events"] = [{"id": "failed", "kind": "llm", "model": "fixture-model",
                                   "status": "error", "duration_ms": 9, "output": {"error_type": "ProviderError"}}]
        return body
    job = execute(store, failed)
    assert len(received) == 1 and job["state"] == "completed"
    row = job["results"][0]
    assert row["execution_state"] == "error" and row["error"] == "agent_execution_failed"
    assert row["outcome"] == "inconclusive" and row["telemetry"]["llm_errors"] == 1
    assert row["usage"]["total_tokens"] is None
    run = store.get_run(row["run_id"])
    assert not run.output_present and not run.trace_complete
    assert any(e.kind == "llm" and e.status == "error" for e in run.events)


def test_tool_failure_recovery_is_analyzed(store):
    def recovering(request):
        body = response(request)
        tools = [{"id": f"failure-{i}", "kind": "tool", "tool": "calculator", "status": "error",
                  "input": {"a": 1, "b": 2}, "output": {"error_type": "ValueError"}} for i in range(3)]
        body["trace"]["events"] = [*tools, *body["trace"]["events"]]
        return body
    row = execute(store, recovering, turns=1)["results"][0]
    evaluation = store.get_evaluation(row["run_id"], row["revision_id"])
    assert row["telemetry"]["tool_errors"] == 3
    assert {f.category for f in evaluation.findings} >= {"repeated_tool_failure", "tool_recovery"}


@pytest.mark.parametrize("change", ["session", "turn", "duplicate", "parent", "aggregate", "event_total", "duration", "tokens", "provenance", "too_many"])
def test_rejects_invalid_or_cross_session_telemetry(change):
    request = {"session_id": "expected", "turn": 0}
    body = response(request)
    trace = body["trace"]
    if change == "session":
        trace["session_id"] = "other"
    elif change == "turn":
        trace["turn"] = 1
    elif change == "duplicate":
        trace["events"].append(copy.deepcopy(trace["events"][0]))
    elif change == "parent":
        trace["events"][1]["parent_id"] = "not-recorded"
    elif change == "aggregate":
        body["usage"]["tokens"] = {"input": 60, "output": 20, "total": 80}
    elif change == "event_total":
        trace["events"][0]["usage"]["tokens"]["total"] = 99
    elif change == "duration":
        trace["events"][0]["duration_ms"] = -1
    elif change == "tokens":
        trace["events"][0]["usage"]["tokens"]["input"] = "10"
    elif change == "provenance":
        trace["events"][0]["provenance"] = "host_observed"
    else:
        trace["events"] = [{"id": str(i), "kind": "llm"} for i in range(201)]
    client = TargetClient(TargetDefinition(id="fixture", endpoint="http://fixture.test"),
                          transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body)))
    with pytest.raises(TargetError, match="invalid_target_contract"):
        client.call(request, 1)


def test_unknown_call_usage_is_not_filled_with_zero():
    body = response({"session_id": "test", "turn": 0}, aggregate=False)
    del body["trace"]["events"][2]["usage"]
    parsed = TargetResponse.model_validate(body)
    assert parsed.usage is None


def test_credentials_redacted_in_tool_arguments_and_results(store, monkeypatch):
    secret = "fixture-service-secret"
    monkeypatch.setenv("TARGET_TEST_TOKEN", secret)
    target = TargetDefinition(id="fixture", endpoint="http://fixture.test", token_env="TARGET_TEST_TOKEN")
    def respond(request):
        body = response(request)
        body["trace"]["events"][1].update(input={"document": secret}, output={"result": secret})
        return body
    client = TargetClient(target, transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json=respond(json.loads(r.content)))))
    parsed = client.call({"session_id": "s", "turn": 0}, 1)
    assert secret not in canonical(parsed.model_dump())
    assert "redacted:credential" in canonical(parsed.model_dump())


def test_complete_calls_fill_missing_aggregate_fields_and_cost():
    body = response({"session_id": "s", "turn": 0})
    body["usage"] = {"tokens": {"input": 30}}
    for event in body["trace"]["events"]:
        if event["kind"] == "llm":
            event["usage"]["cost_usd"] = 0.001
    parsed = TargetResponse.model_validate(body)
    assert parsed.usage.tokens.model_dump() == {"input": 30, "output": 10, "total": 40, "reasoning": None}
    assert parsed.usage.cost_usd == 0.002
    assert parsed._aggregate_fields == {"input_tokens"}


def test_per_field_completeness_does_not_hide_known_input(store):
    def respond(request):
        body = response(request)
        del body["trace"]
        body["usage"] = {"tokens": {"input": 30}}
        return body
    row = execute(store, respond, turns=1)["results"][0]
    assert row["usage"]["input_tokens"] == 30
    assert row["usage"]["fields"]["input_tokens"]["status"] == "complete"
    assert row["usage"]["fields"]["output_tokens"]["status"] == "unknown"
    assert row["usage"]["total_tokens"] is None


@pytest.mark.parametrize("kind", ["failure", "timeout", "missing_call"])
def test_incomplete_usage_is_kept_as_lower_bound_across_pages_and_exports(store, kind):
    def respond(request):
        if kind == "timeout" and request["turn"] == 1:
            raise httpx.ReadTimeout("synthetic timeout")
        body = response(request, aggregate=False)
        if kind == "missing_call":
            del body["trace"]["events"][2]["usage"]
        elif kind == "failure":
            body.update(output=None, execution_status="error", error="agent_execution_failed")
            body["trace"]["events"].append({"id": "failed", "kind": "llm", "status": "error"})
        return body
    job = execute(store, respond, turns=2 if kind == "timeout" else 1)
    row = job["results"][0]
    expected = 15 if kind == "missing_call" else 40
    usage = row["usage"]
    assert usage["total_tokens"] is None  # Compatibility: this field still means a complete total.
    assert usage["fields"]["total_tokens"]["value"] == expected
    assert usage["fields"]["total_tokens"]["status"] == "partial"
    assert usage["output_token_budget"] == "unknown"
    assert job["curves"][0]["usage"]["fields"]["total_tokens"]["value"] == expected
    assert job["quality"]["evidence"]["token_partial_runs"] == 1
    evaluation = store.get_evaluation(row["run_id"], row["revision_id"])
    metric = next(m for m in evaluation.metrics if m.key == "tokens_total")
    assert metric.value == expected and metric.status == "partial"
    model = row["telemetry"]["models"][0]
    assert model["usage"]["fields"]["total_tokens"]["value"] == expected
    assert f"总计 ≥{expected}" in assessment_markdown(job)
    exported = assessment_bundle(AssessmentStore(store), job)
    assert exported["runs"][0]["bundle"]["trace"]["usage_summary"]["fields"]["total_tokens"]["value"] == expected


def test_partial_usage_can_disprove_budget_but_cannot_prove_it(store):
    def respond(request):
        body = response(request, aggregate=False, coverage="partial")
        body["trace"]["events"] = [{"id": "m", "kind": "llm", "status": "completed",
                                   "usage": {"tokens": {"input": 120, "output": 40}}}]
        return body
    row = execute(store, respond, turns=1)["results"][0]
    assert row["usage"]["output_token_budget"] == "fail"
    assert {c["id"]: c["status"] for c in row["checks"]}["tokens"] == "fail"
    assert row["usage"]["fields"]["total_tokens"]["value"] == 160


def test_cancel_retains_observed_tokens(store):
    target = TargetDefinition(id="fixture", endpoint="http://fixture.test", demo=True)
    assessment = suite()
    db = AssessmentStore(store)
    job, _ = prepare_assessment(db, target, assessment)
    def respond(request):
        db.cancel(job["id"])
        return httpx.Response(200, json=response(json.loads(request.content)))
    job = run_assessment(db, job["id"], target, assessment, transport=httpx.MockTransport(respond))
    assert job["state"] == "cancelled"
    row = job["results"][0]
    assert row["execution_state"] == "cancelled"
    assert row["usage"]["fields"]["total_tokens"]["value"] == 40
    assert row["usage"]["fields"]["total_tokens"]["status"] == "partial"


def test_offline_is_not_applicable_and_demo_does_not_imply_offline(store):
    def offline(request):
        return {"output": {"answer": 3}, "usage_mode": "offline"}
    job = execute(store, offline, turns=1)
    row = job["results"][0]
    assert all(f["status"] == "not_applicable" and f["value"] is None for f in row["usage"]["fields"].values())
    assert job["quality"]["evidence"]["token_not_applicable_runs"] == 1
    assert "不适用（离线校准）" in assessment_markdown(job)
    metric = next(m for m in store.get_evaluation(row["run_id"], row["revision_id"]).metrics if m.key == "tokens_total")
    assert metric.status == "not_applicable" and metric.value is None
    row = execute(store, lambda _: {"output": {"answer": 3}}, turns=1)["results"][0]
    assert row["usage"]["fields"]["total_tokens"]["status"] == "unknown"


def test_zero_is_known_and_inconsistent_offline_or_partial_aggregate_rejected(store):
    row = execute(store, lambda _: {"output": {}, "usage": {"tokens": {"input": 0, "output": 0}}}, turns=1)["results"][0]
    assert row["usage"]["total_tokens"] == 0
    assert row["usage"]["fields"]["total_tokens"]["status"] == "complete"
    body = response({"session_id": "s", "turn": 0}, coverage="partial")
    body["usage"]["tokens"] = {"input": 1, "output": 1, "total": 2}
    with pytest.raises(ValueError, match="小于"):
        TargetResponse.model_validate(body)
    with pytest.raises(ValueError, match="离线"):
        TargetResponse.model_validate({"output": {}, "usage_mode": "offline", "usage": {"tokens": {"total": 0}}})


@pytest.mark.parametrize("mode,collection,expected", [
    ("offline", "not_applicable", "不适用"), ("model", "call_usage", "实际完整性"),
    ("unknown", "unknown", "不能证明"),
])
def test_preflight_only_checks_health_and_never_calls_model(mode, collection, expected):
    received = []
    def respond(request):
        received.append((request.method, request.url.path))
        return httpx.Response(200, json={"status": "ok", "usage_mode": mode, "usage_collection": collection})
    target = TargetDefinition(id="fixture", endpoint="http://fixture.test", health_path="/health")
    result = telemetry_readiness(target, transport=httpx.MockTransport(respond))
    assert received == [("GET", "/health")]
    assert result["usage_mode"] == mode and expected in result["reason"]
