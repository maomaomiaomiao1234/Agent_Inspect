import json
import threading
import time

import httpx
import pytest

from agent_trace_review.assessment_contracts import AssessmentSuite, TargetDefinition
from agent_trace_review.assessment_store import AssessmentStore
from agent_trace_review.assessments import prepare_assessment, run_assessment
from agent_trace_review.storage import Store


def make_suite(concurrency=3, attempts=1):
    cases = [{"id": f"case-{i}", "turns": [{"prompt": str(i)}],
              "profile": {"profile_version": "1", "id": f"case-{i}", "rules": [
                  {"id": "correct", "op": "equals", "path": "/output/answer", "value": i}]}}
             for i in range(6)]
    return AssessmentSuite.model_validate({"id": "schedule", "cases": cases, "concurrency": concurrency,
                                           "attempts": attempts, "budgets": [{"deadline_seconds": 1}]})


def run(tmp_path, suite, handler, prepare=None):
    db = AssessmentStore(Store(tmp_path))
    target = TargetDefinition(id="fixture", endpoint="https://fixture.example", demo=True)
    job, repo = prepare_assessment(db, target, suite)
    if prepare:
        prepare(db, job)
    return run_assessment(db, job["id"], target, suite, repo, transport=httpx.MockTransport(handler))


def test_concurrency_limit_order_and_multi_turn_barriers(tmp_path):
    suite = make_suite()
    raw = suite.model_dump()
    raw["cases"][3]["turns"].append({"prompt": "3"})
    raw["cases"][4]["category"] = "session_isolation"
    suite = AssessmentSuite.model_validate(raw)
    barrier = threading.Barrier(3, timeout=3)
    lock = threading.Lock()
    active, maximum, calls = 0, 0, []

    def handler(request):
        nonlocal active, maximum
        body = json.loads(request.content)
        case = int(body["prompt"])
        with lock:
            active += 1
            maximum = max(maximum, active)
            calls.append((case, body["turn"]))
            if case in {3, 4}:
                assert active == 1
        if case < 3:
            barrier.wait()
        with lock:
            active -= 1
        return httpx.Response(200, json={"output": {"answer": case}})

    result = run(tmp_path, suite, handler)
    assert result["state"] == "completed" and maximum == 3
    assert [r["case_id"] for r in result["results"]] == [f"case-{i}" for i in range(6)]
    assert calls[3:] == [(3, 0), (3, 1), (4, 0), (5, 0)]
    assert {r["outcome"] for r in result["results"]} == {"pass"}


def test_cancel_drains_started_cases_without_scheduling_remaining(tmp_path):
    barrier = threading.Barrier(3, timeout=3)
    saved = {}
    calls = []

    def prepare(db, job):
        saved.update(db=db, job=job)

    def handler(request):
        body = json.loads(request.content)
        calls.append(body["prompt"])
        barrier.wait()
        saved["db"].cancel(saved["job"]["id"])
        return httpx.Response(200, json={"output": {"answer": int(body["prompt"])}})

    result = run(tmp_path, make_suite(), handler, prepare)
    assert result["state"] == "cancelled"
    assert len(calls) == result["completed"] == 3
    assert all(r["outcome"] == "inconclusive" for r in result["results"])
    assert all(saved["db"].store.get_run(r["run_id"]) for r in result["results"])
    dimension = result["quality"]["dimensions"][0]
    assert dimension["unknown"] == 6
    assert (dimension["confirmed_pass_rate"], dimension["possible_pass_rate"]) == (0, 100)


def test_repeat_variability_separate_from_unknown_and_missing_cost(tmp_path):
    suite = make_suite(concurrency=1, attempts=3)
    counts = {}

    def handler(request):
        case = int(json.loads(request.content)["prompt"])
        counts[case] = counts.get(case, 0) + 1
        if case == 0 and counts[case] == 2:
            return httpx.Response(200, json={"output": {"answer": -1}})
        if case == 1 and counts[case] == 2:
            return httpx.Response(503)
        return httpx.Response(200, json={"output": {"answer": case}})

    result = run(tmp_path, suite, handler)
    assert result["quality"]["stability"][0]["status"] == "variable"
    assert result["quality"]["stability"][1]["status"] == "incomplete"
    assert result["quality"]["stability"][2]["status"] == "consistent_pass"
    dimension = result["quality"]["dimensions"][0]
    assert (dimension["pass"], dimension["fail"], dimension["unknown"], dimension["execution_errors"]) == (16, 1, 1, 1)
    assert result["curves"][0]["reported_cost_usd"] is None


def test_internal_worker_error_still_persists_other_inflight_results(tmp_path, monkeypatch):
    import agent_trace_review.assessments as module

    original = module._case
    barrier = threading.Barrier(3, timeout=3)

    def fail_one(*args):
        barrier.wait()
        if args[4].id == "case-0":
            raise RuntimeError("private internal error")
        time.sleep(.01)
        return original(*args)

    monkeypatch.setattr(module, "_case", fail_one)
    result = run(tmp_path, make_suite(), lambda request: httpx.Response(
        200, json={"output": {"answer": int(json.loads(request.content)["prompt"])}}))
    assert result["state"] == "failed" and result["error"] == "assessment_internal_error"
    assert result["completed"] == 2
    assert {r["case_id"] for r in result["results"]} == {"case-1", "case-2"}
    assert "private internal error" not in json.dumps(result)


@pytest.mark.parametrize("concurrency", [0, 5, True, "2"])
def test_invalid_concurrency_rejected(concurrency):
    with pytest.raises(ValueError):
        make_suite(concurrency)
