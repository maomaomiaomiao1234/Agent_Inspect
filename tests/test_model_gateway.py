import asyncio
import json
import subprocess
import sys
import time
from types import SimpleNamespace

import httpx
import pytest

from agent_trace_review.assessment_contracts import AssessmentSuite, TargetDefinition
from agent_trace_review.assessment_store import AssessmentStore
from agent_trace_review.assessments import _case, assessment_bundle, prepare_assessment
from agent_trace_review.model_gateway import GatewayStore, ModelGateway, gateway_trace_events
from agent_trace_review.storage import Store
from agent_trace_review.target_client import TargetClient
from agent_trace_review.usage_accounting import summarize_usage

USAGE = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
         "prompt_cache_hit_tokens": 6, "prompt_cache_miss_tokens": 4,
         "completion_tokens_details": {"reasoning_tokens": 2}}
PAYLOAD = {"session_id": "s", "turn": 0, "budget": {"id": "b", "deadline_seconds": 5, "max_output_tokens": 10}}


def reply():
    return {"id": "same-provider-response-id", "model": "fixture", "usage": USAGE,
            "choices": [{"message": {"content": "private answer"}, "finish_reason": "stop"}]}


@pytest.fixture
def make_gateway(store, monkeypatch):
    monkeypatch.setenv("AGENT_REVIEW_GATEWAY_HOST", "127.0.0.1")
    monkeypatch.setenv("AGENT_REVIEW_GATEWAY_ADVERTISE_HOST", "127.0.0.1")
    monkeypatch.setenv("GATEWAY_TEST_URL", "https://provider.invalid/v1")
    monkeypatch.setenv("GATEWAY_TEST_MODEL", "fixture")
    monkeypatch.setenv("GATEWAY_TEST_TOKEN", "private-provider-key")
    environment = {"AGENT_REVIEW_TARGET_API_URL": "GATEWAY_TEST_URL",
                   "AGENT_REVIEW_TARGET_MODEL": "GATEWAY_TEST_MODEL", "AGENT_REVIEW_TARGET_TOKEN": "GATEWAY_TEST_TOKEN",
                   "OPENAI_API_KEY": "GATEWAY_TEST_TOKEN"}
    gateways = []
    def create(provider=None, job_id="assessment-fixture"):
        provider = provider or (lambda r: httpx.Response(200, json=reply(), headers={"x-request-id": "provider-request"}))
        gateway = ModelGateway(store, transport=httpx.MockTransport(provider), drain_seconds=0.5)
        gateways.append(gateway)
        target = SimpleNamespace(deployment=SimpleNamespace(environment=environment))
        binding = gateway.bind(job_id, target)
        return gateway, binding, f"http://127.0.0.1:{gateway.port}/v1"
    yield create
    for gateway in gateways:
        gateway.close()


def lease(binding, **changes):
    payload = {**PAYLOAD, **changes}
    return binding.issue({"assessment_id": "forged", "case_id": "case", "budget_id": "b", "attempt": 1}, payload)


def test_gateway_auth_budget_unique_attempts_and_no_secret_persistence(make_gateway, store):
    received = []
    def provider(request):
        assert request.headers["authorization"] == "Bearer private-provider-key"
        assert all(value is None for value in request.extensions["timeout"].values())  # Outer task deadline governs.
        assert "x-agent-review-case-id" not in request.headers
        received.append(json.loads(request.content))
        return httpx.Response(200, json=reply(), headers={"x-request-id": "provider-request"})
    gateway, binding, url = make_gateway(provider)
    lease_id, token = lease(binding)
    assert "private-provider-key" not in json.dumps(binding.environment)
    assert binding.environment["OPENAI_API_KEY"] == "gateway-turn-credential-required"
    with httpx.Client() as client:
        assert client.post(url + "/chat/completions", json={"model": "fixture"}).status_code == 401
        headers = {"authorization": "Bearer " + token, "x-agent-review-case-id": "spoof"}
        assert client.post(url + "/chat/completions", headers=headers, json={"model": "other"}).status_code == 400
        assert client.post(url + "/responses?upstream=attacker", headers=headers, json={"model": "fixture"}).status_code == 400
        for _ in range(2):
            response = client.post(url + "/chat/completions", headers=headers,
                                   json={"model": "fixture", "messages": ["private prompt"], "max_tokens": 1000})
            assert response.status_code == 200 and response.json() == reply()
            assert response.headers["x-agent-review-gateway-request-id"].startswith("gateway_")
        assert client.post(url + "/chat/completions", headers=headers, json={"model": "fixture"}).status_code == 429
        snapshot = binding.finish(lease_id)
        assert client.post(url + "/chat/completions", headers=headers, json={"model": "fixture"}).status_code == 401
    assert [body["max_tokens"] for body in received] == [10, 5]
    assert len(snapshot["events"]) == 2 and len({e["id"] for e in snapshot["events"]}) == 2
    assert {e["context"]["assessment_id"] for e in snapshot["events"]} == {binding.job_id}
    assert {e["context"]["case_id"] for e in snapshot["events"]} == {"case"}
    assert gateway.db.snapshot(binding.job_id)["usage"]["fields"]["total_tokens"]["value"] == 30
    with store.connect() as conn:
        records = json.dumps(list(conn.execute("SELECT * FROM gateway_calls")))
        leases = json.dumps(list(conn.execute("SELECT * FROM gateway_leases")))
    assert "private" not in records and token not in records + leases


class Stream(httpx.AsyncByteStream):
    def __init__(self, *, done=True, delay=0, error=False):
        self.done, self.delay, self.error = done, delay, error

    async def __aiter__(self):
        yield b'data: {"choices":[{"delta":{"content":"private text"}}]}\n\n'
        await asyncio.sleep(self.delay)
        yield ("data: " + json.dumps(reply()) + "\n\n").encode()
        if self.error:
            raise httpx.ReadError("private provider error")
        if self.done:
            yield b"data: [DONE]\n\n"


@pytest.mark.parametrize("mode", ["complete", "missing_end", "disconnect", "read_error"])
def test_sse_persists_usage_even_after_downstream_disconnect(make_gateway, mode):
    gateway, binding, url = make_gateway(lambda r: httpx.Response(200,
        stream=Stream(done=mode != "missing_end", delay=0.1, error=mode == "read_error"),
        headers={"content-type": "text/event-stream"}))
    lease_id, token = lease(binding)
    with httpx.Client() as client:
        with client.stream("POST", url + "/chat/completions", headers={"authorization": "Bearer " + token},
                           json={"model": "fixture", "stream": True}) as response:
            if mode == "disconnect":
                iterator = response.iter_bytes()
                next(iterator)
                response.close()
            else:
                response.read()
    snapshot = binding.finish(lease_id)
    assert len(snapshot["events"]) == 1
    event = snapshot["events"][0]
    assert event["usage"]["tokens"]["total"] == 15
    assert event["context"]["usage_complete"] == (mode in {"complete", "disconnect"})
    assert event["context"]["gateway_state"] == "finished"
    assert gateway.db.snapshot(binding.job_id)["pending"] == 0


def test_target_http_failure_keeps_external_usage_in_result_report_and_export(make_gateway, store):
    suite = AssessmentSuite.model_validate({"id": "gateway-test", "cases": [{"id": "case", "turns": [{"prompt": "fixture"}],
        "profile": {"profile_version": "1", "id": "p", "rules": [{"id": "a", "op": "equals", "path": "/output/answer", "value": 19}]}}]})
    target = TargetDefinition(id="fixture", endpoint="http://target.invalid", demo=True)
    db = AssessmentStore(store)
    job, repository = prepare_assessment(db, target, suite)
    gateway, binding, url = make_gateway(job_id=job["id"])
    def failed_target(request):
        token = request.headers["x-agent-review-gateway-token"]
        response = httpx.post(url + "/chat/completions", headers={"authorization": "Bearer " + token}, json={"model": "fixture"})
        assert response.status_code == 200
        return httpx.Response(500, json={"error": "synthetic target crash"})
    client = TargetClient(target, gateway=binding, transport=httpx.MockTransport(failed_target))
    result = _case(store, client, job["id"], suite, suite.cases[0], suite.budgets[0], 1, repository, lambda: False)
    assert result["outcome"] == "inconclusive" and result["execution_state"] == "error"
    assert result["usage"]["fields"]["total_tokens"]["value"] == 15
    assert result["usage"]["fields"]["total_tokens"]["source"] == "gateway"
    run = store.get_run(result["run_id"])
    assert len([e for e in run.events if e.kind == "llm"]) == 1
    job.update(model_gateway=True, results=[result])
    exported = assessment_bundle(db, job)
    assert exported["gateway"]["request_count"] == 1
    assert "private-provider-key" not in json.dumps(exported)
    db.update(job["id"], job)
    assert db.job(job["id"])["gateway"]["usage"]["fields"]["total_tokens"]["value"] == 15


def test_gateway_is_single_accounting_source_and_retains_tool_parent(make_gateway):
    _, binding, url = make_gateway()
    lease_id, token = lease(binding)
    response = httpx.post(url + "/chat/completions", headers={"authorization": "Bearer " + token}, json={"model": "fixture"})
    gateway = binding.finish(lease_id)
    call_id = response.headers["x-agent-review-gateway-request-id"]
    trace = {"coverage": "complete", "events": [
        {"id": "target-model", "kind": "llm", "usage": {"tokens": {"total": 999}},
         "context": {"gateway_request_id": call_id}},
        {"id": "tool", "kind": "tool", "tool": "calculator", "parent_id": "target-model"}]}
    summary = summarize_usage([{"trace": trace, "usage": {"tokens": {"total": 999}}, "gateway": gateway}])
    assert summary["fields"]["total_tokens"]["value"] == 15
    events = gateway_trace_events(trace, gateway, job_id="a", case_id="c", session_id="s", turn=0, budget_id="b", attempt=1)
    assert len(events) == 2 and events[1]["parent_id"] == call_id
    assert sum(e.get("usage", {}).get("tokens", {}).get("total", 0) for e in events) == 15


def test_process_crash_recovery_preserves_usage_and_revokes_leases(tmp_path):
    path = tmp_path / "crashed"
    code = '''import os, sys
from agent_trace_review.model_gateway import GatewayStore
from agent_trace_review.storage import Store
db = GatewayStore(Store(sys.argv[1]))
lease, token = db.issue("job", {"session_id":"s","turn":0}, {"deadline_seconds":30})
record = db.resolve(token)
event = {"id":"gateway_crash","kind":"llm","status":"unknown","usage":{"tokens":{"total":15}},
         "context":{"usage_complete":False,"gateway_state":"running"}}
db.reserve(record, event)
os._exit(23)
'''
    child = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True)
    assert child.returncode == 23, child.stderr.decode()
    store = Store(path)
    db = GatewayStore(store)
    db.recover()
    first = db.snapshot("job")
    db.recover()
    assert db.snapshot("job") == first
    assert first["usage"]["fields"]["total_tokens"]["value"] == 15
    assert first["usage"]["fields"]["total_tokens"]["status"] == "partial"
    assert first["events"][0]["context"]["usage_missing_reason"] == "gateway_restarted"
    with store.connect() as conn:
        assert conn.execute("SELECT SUM(active) FROM gateway_leases").fetchone()[0] == 0


def test_concurrent_turns_have_separate_credentials_and_context(make_gateway):
    gateway, binding, url = make_gateway()
    async def one(index):
        payload = {**PAYLOAD, "session_id": f"session-{index}"}
        lease_id, token = binding.issue({"case_id": f"case-{index}", "budget_id": "b", "attempt": 1}, payload)
        async with httpx.AsyncClient() as client:
            response = await client.post(url + "/chat/completions", headers={"authorization": "Bearer " + token},
                                         json={"model": "fixture"})
            assert response.status_code == 200
        return await asyncio.to_thread(binding.finish, lease_id)
    async def all_turns():
        return await asyncio.gather(*(one(i) for i in range(4)))
    snapshots = asyncio.run(all_turns())
    for index, snapshot in enumerate(snapshots):
        assert snapshot["events"][0]["context"]["case_id"] == f"case-{index}"
    assert gateway.db.snapshot(binding.job_id)["usage"]["fields"]["total_tokens"]["value"] == 60


def test_expired_lease_does_not_call_provider(make_gateway):
    calls = []
    _, binding, url = make_gateway(lambda r: calls.append(r))
    _, token = lease(binding, budget={"deadline_seconds": 0.01})
    time.sleep(0.02)
    assert httpx.post(url + "/responses", headers={"authorization": "Bearer " + token},
                      json={"model": "fixture"}).status_code == 401
    assert not calls


def test_gateway_process_death_preserves_usage_from_actual_inflight_sse(tmp_path):
    ready = tmp_path / "ready.json"
    path = tmp_path / "data"
    code = '''import asyncio, json, os, sys, time
from pathlib import Path
from types import SimpleNamespace
import httpx
from agent_trace_review.model_gateway import ModelGateway
from agent_trace_review.storage import Store
os.environ.update(AGENT_REVIEW_GATEWAY_HOST="127.0.0.1", AGENT_REVIEW_GATEWAY_ADVERTISE_HOST="127.0.0.1",
                  TEST_API="https://fixture.invalid/v1", TEST_MODEL="fixture", TEST_KEY="synthetic-key")
class Stream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b'data: {"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}\\n\\n'
        await asyncio.sleep(60)
gateway=ModelGateway(Store(sys.argv[1]), transport=httpx.MockTransport(lambda r: httpx.Response(200,
                    stream=Stream(),headers={"content-type":"text/event-stream"})))
target=SimpleNamespace(deployment=SimpleNamespace(environment={"AGENT_REVIEW_TARGET_API_URL":"TEST_API",
       "AGENT_REVIEW_TARGET_MODEL":"TEST_MODEL", "AGENT_REVIEW_TARGET_TOKEN":"TEST_KEY"}))
binding=gateway.bind("crash-job",target)
lease, token=binding.issue({"case_id":"case","budget_id":"b","attempt":1},
                           {"session_id":"s","turn":0,"budget":{"deadline_seconds":60}})
Path(sys.argv[2]).write_text(json.dumps({"port":gateway.port,"token":token}))
time.sleep(60)
'''
    process = subprocess.Popen([sys.executable, "-c", code, str(path), str(ready)], stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), "Gateway subprocess did not start"
        config = json.loads(ready.read_text())
        with httpx.stream("POST", f"http://127.0.0.1:{config['port']}/v1/chat/completions",
                          headers={"authorization": "Bearer " + config["token"]}, json={"model": "fixture", "stream": True}) as response:
            assert b"total_tokens" in next(response.iter_bytes())
            process.kill()
            process.wait(timeout=5)
        database = GatewayStore(Store(path))
        database.recover()
        result = database.snapshot("crash-job")
        assert result["request_count"] == 1
        assert result["usage"]["fields"]["total_tokens"]["value"] == 15
        assert result["usage"]["fields"]["total_tokens"]["status"] == "partial"
        assert result["events"][0]["context"]["gateway_state"] == "interrupted"
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def test_invalid_provider_usage_is_unknown_but_payload_is_forwarded(make_gateway):
    _, binding, url = make_gateway(lambda r: httpx.Response(200, json={"usage": {
        "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 900}}))
    lease_id, token = lease(binding)
    response = httpx.post(url + "/chat/completions", headers={"authorization": "Bearer " + token}, json={"model": "fixture"})
    assert response.status_code == 200 and response.json()["usage"]["total_tokens"] == 900
    snapshot = binding.finish(lease_id)
    assert "usage" not in snapshot["events"][0]
    assert snapshot["events"][0]["context"]["usage_complete"] is False


def test_failed_upstream_attempt_and_later_success_are_separate(make_gateway):
    requests = []
    def provider(request):
        requests.append(request)
        return httpx.Response(503 if len(requests) == 1 else 200, json=reply())
    gateway, binding, url = make_gateway(provider)
    lease_id, token = lease(binding)
    for status in (503, 200):
        assert httpx.post(url + "/chat/completions", headers={"authorization": "Bearer " + token},
                          json={"model": "fixture"}).status_code == status
    snapshot = binding.finish(lease_id)
    assert len(snapshot["events"]) == 2
    assert gateway.db.snapshot(binding.job_id)["usage"]["fields"]["total_tokens"]["value"] == 30
    assert [event["status"] for event in snapshot["events"]] == ["error", "completed"]
