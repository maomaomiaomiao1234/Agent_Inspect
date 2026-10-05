import asyncio
import gzip
import json
from types import SimpleNamespace

import httpx
import pytest

from agent_trace_review.assessment_contracts import TargetResponse
from agent_trace_review.provider_telemetry import CURRENT, ModelRecorder, normalize_usage
from agent_trace_review.service import ingest
from agent_trace_review.target_telemetry import trace_events
from agent_trace_review.usage_accounting import BASE_KEYS, UsageSummary, summarize_usage
from agent_trace_review.util import canonical

URL = "https://provider.example/v1/chat/completions"
USAGE = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
         "prompt_cache_hit_tokens": 6, "prompt_cache_miss_tokens": 4,
         "completion_tokens_details": {"reasoning_tokens": 2}}


def payload(**kwargs):
    return {"id": "response-1", "model": "resolved-model", "usage": USAGE, **kwargs}


def recorder(session="s"):
    return ModelRecorder(session_id=session, turn=0, api_url="https://provider.example/v1", model="requested",
                         context={"assessment_id": "a", "case_id": session, "budget_id": "b", "attempt": 1})


def parsed(rec, *, coverage="complete"):
    return TargetResponse.model_validate({"output": "fixture", "usage_mode": "model", "trace": rec.trace(coverage=coverage)})


def sse(*values, done=True):
    data = "".join("data: " + json.dumps(v, ensure_ascii=False) + "\r\n\r\n" for v in values)
    return (data + ("data: [DONE]\r\n\r\n" if done else "")).encode()


class SyncChunks(httpx.SyncByteStream):
    def __init__(self, data, *, step=7, fail=False):
        self.data, self.step, self.fail = data, step, fail
        self.closed = False

    def __iter__(self):
        for start in range(0, len(self.data), self.step):
            yield self.data[start:start + self.step]
        if self.fail:
            raise httpx.ReadError("private exception content")

    def close(self):
        self.closed = True


class AsyncChunks(httpx.AsyncByteStream):
    def __init__(self, data, *, cancel=False):
        self.data, self.cancel = data, cancel
        self.closed = False

    async def __aiter__(self):
        for start in range(0, len(self.data), 11):
            await asyncio.sleep(0)
            yield self.data[start:start + 11]
        if self.cancel:
            raise asyncio.CancelledError()

    async def aclose(self):
        self.closed = True


@pytest.mark.parametrize("provider", ["chat", "responses", "deepseek", "native"])
def test_normalization_preserves_subdivisions_without_adding_to_total(provider):
    if provider == "chat":
        value = {"usage": {"prompt_tokens": 10, "completion_tokens": 5,
                           "prompt_tokens_details": {"cached_tokens": 6, "cache_write_tokens": 1},
                           "completion_tokens_details": {"reasoning_tokens": 2}}}
    elif provider == "responses":
        value = SimpleNamespace(usage=SimpleNamespace(input_tokens=10, output_tokens=5,
                 input_tokens_details=SimpleNamespace(cached_tokens=6, cache_write_tokens=1),
                 output_tokens_details=SimpleNamespace(reasoning_tokens=2)))
    elif provider == "deepseek":
        value = payload()
    else:
        value = SimpleNamespace(token_usage=SimpleNamespace(input_tokens=10, output_tokens=5))
    tokens = normalize_usage(value)["tokens"]
    assert (tokens["input"], tokens["output"], tokens["total"]) == (10, 5, 15)
    if provider != "native":
        assert tokens["reasoning"] == 2 and tokens["cache_read"] == 6
    if provider == "deepseek":
        assert tokens["cache_miss"] == 4
    elif provider != "native":
        assert tokens["cache_write"] == 1


@pytest.mark.parametrize("usage", [None, {}, {"prompt_tokens": True}, {"prompt_tokens": -1},
                                  {"completion_tokens": "5"}, {"total_tokens": float("inf")}])
def test_missing_invalid_usage_is_never_zero(usage):
    assert normalize_usage({"usage": usage}) is None


def test_explicit_zero_is_a_reported_value():
    assert normalize_usage({"usage": {"prompt_tokens": 0, "completion_tokens": 0}}) == {
        "tokens": {"input": 0, "output": 0, "total": 0}}


def test_http_and_native_wrapper_nested_once_with_context_and_no_content_leak():
    rec = recorder()
    def provider(request):
        assert json.loads(request.content) == {"model": "requested", "messages": ["private prompt"]}
        assert "x-agent-review-case-id" not in request.headers
        return httpx.Response(200, json=payload(choices=[{"message": {"content": "private answer",
                         "reasoning_content": "private reasoning"}}]), headers={"x-request-id": "provider-123"})
    with httpx.Client(transport=httpx.MockTransport(provider)) as client, rec.collect():
        result = rec.model("outer", lambda: rec.model("inner", lambda: client.post(URL,
                           json={"model": "requested", "messages": ["private prompt"]}).json()))
    assert result["usage"] == USAGE
    assert CURRENT.get() is None
    assert len(rec.events) == 1 and parsed(rec).usage.tokens.total == 15
    event = rec.events[0]
    assert event["model"] == "resolved-model"
    assert event["context"]["requested_model"] == "requested"
    assert event["context"]["provider_request_id"] == "provider-123"
    assert event["context"]["response_id"] == "response-1"
    assert event["context"]["assessment_id"] == "a" and event["context"]["call_id"] == event["id"]
    assert "private" not in json.dumps(rec.trace())


def test_sdk_style_retry_keeps_failed_attempt_and_success_without_wrapper_double_count():
    rec = recorder()
    requests = []
    def provider(request):
        requests.append(request)
        return httpx.Response(429 if len(requests) == 1 else 200, json=payload(id="r" + str(len(requests))),
                              headers={"x-request-id": "request-" + str(len(requests))})
    with httpx.Client(transport=httpx.MockTransport(provider)) as client, rec.collect():
        def retry():
            for _ in range(2):
                result = client.post(URL, json={"model": "requested"})
                if result.is_success:
                    return result.json()
        rec.model("sdk", retry)
    assert len(rec.events) == 2
    assert [e["status"] for e in rec.events] == ["error", "completed"]
    summary = summarize_usage([{"trace": rec.trace(coverage="complete")}])["fields"]
    assert summary["total_tokens"]["value"] == 30 and summary["total_tokens"]["status"] == "partial"
    assert rec.events[0]["context"]["provider_request_id"] != rec.events[1]["context"]["provider_request_id"]


@pytest.mark.parametrize("compressed", [False, True])
@pytest.mark.parametrize("api", ["chat", "responses"])
def test_incremental_http_sse_captures_last_usage_and_does_not_sum_snapshots(compressed, api):
    rec = recorder()
    if api == "responses":
        values = [{"type": "response.output_text.delta", "delta": "private 思考"},
                  {"type": "response.completed", "response": payload()}]
    else:
        values = [payload(usage=None, choices=[{"delta": {"content": "private 思考"}}]), payload(), payload()]
    wire = sse(*values, done=api == "chat")
    headers = {"content-type": "text/event-stream", **({"content-encoding": "gzip"} if compressed else {})}
    body = SyncChunks(gzip.compress(wire) if compressed else wire, step=1)
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=body, headers=headers))) as client:
        with rec.collect(), client.stream("POST", URL.replace("chat/completions", "responses") if api == "responses" else URL,
                                          json={"stream": True}) as response:
            received = b"".join(response.iter_bytes())
    assert received == wire and body.closed
    assert len(rec.events) == 1 and rec.events[0]["status"] == "completed"
    assert parsed(rec).usage.tokens.total == 15
    assert "private" not in json.dumps(rec.trace())


@pytest.mark.parametrize("mode", ["missing_usage", "no_done", "read_error", "early_close", "unread_close"])
def test_stream_missing_or_interrupted_preserves_only_known_lower_bound(mode):
    rec = recorder()
    wire = sse(payload(usage=None if mode == "missing_usage" else USAGE), done=mode == "missing_usage")
    body = SyncChunks(wire, step=len(wire), fail=mode == "read_error")
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=body,
                            headers={"content-type": "text/event-stream"}))) as client, rec.collect():
        with client.stream("POST", URL, json={}) as response:
            if mode == "read_error":
                with pytest.raises(httpx.ReadError):
                    response.read()
            elif mode == "early_close":
                iterator = response.iter_bytes()
                next(iterator)
                iterator.close()
            elif mode != "unread_close":
                response.read()
    assert body.closed
    assert len(rec.events) == 1
    row = parsed(rec)
    summary = summarize_usage([row.model_dump()])["fields"]["total_tokens"]
    if mode in {"missing_usage", "unread_close"}:
        assert summary["value"] is None and summary["status"] == "unknown"
    else:
        assert summary["value"] == 15 and summary["status"] == "partial"
    assert "private" not in json.dumps(rec.trace())


def test_async_stream_cancel_retains_usage_and_isolated_concurrent_contexts():
    async def run_one(name, cancel):
        rec = recorder(name)
        body = AsyncChunks(sse(payload(id=name), done=not cancel), cancel=cancel)
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=body,
                                    headers={"content-type": "text/event-stream"}))) as client:
            with rec.collect():
                try:
                    async with client.stream("POST", URL, json={}) as response:
                        await response.aread()
                except asyncio.CancelledError:
                    assert cancel
                await asyncio.to_thread(rec.model, "child", lambda: payload(id=name + "-child"))
        assert body.closed
        return rec
    async def run_all():
        return await asyncio.gather(run_one("one", False), run_one("two", True))
    one, two = asyncio.run(run_all())
    for rec in (one, two):
        assert len(rec.events) == 2
        assert {e["context"]["case_id"] for e in rec.events} == {rec.request["session_id"]}
        field = summarize_usage([{"trace": rec.trace(coverage="complete")}])["fields"]["total_tokens"]
        assert field["value"] == 30
        assert field["status"] == ("complete" if rec is one else "partial")
    assert CURRENT.get() is None


def test_sync_and_async_native_stream_wrappers_keep_usage_and_close():
    rec = recorder()
    with rec.model("native", lambda: iter([payload(usage=None), payload(), payload()])) as stream:
        assert len(list(stream)) == 3
    assert parsed(rec).usage.tokens.total == 15
    async def run():
        rec = recorder("async")
        async def chunks():
            yield payload()
            raise asyncio.CancelledError()
        async def create():
            return chunks()
        with pytest.raises(asyncio.CancelledError):
            async with await rec.async_model("native", create) as stream:
                async for _ in stream:
                    pass
        return rec
    cancelled = asyncio.run(run())
    assert cancelled.events[0]["usage"]["tokens"]["total"] == 15
    assert cancelled.events[0]["context"]["usage_complete"] is False


def test_scope_filters_other_endpoints_and_does_not_record_judge_or_health():
    rec = recorder()
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload()))) as client:
        with rec.collect():
            client.get(URL)
            client.post("https://other.example/v1/chat/completions")
            client.post("https://provider.example/v1/models")
        client.post(URL)
    assert rec.events == []


def test_usage_survives_two_turns_import_export_and_legacy_summary(store):
    turns, events = [], []
    for turn in range(2):
        rec = recorder()
        rec.request["turn"] = turn
        rec.context["turn"] = turn
        rec.model("native", payload)
        rec.model("native", payload)
        body = parsed(rec).model_dump()
        turns.append(body)
        events.extend(trace_events(body["trace"], job_id="a", case_id="s", session_id="s", turn=turn,
                                   budget_id="b", attempt=1))
    summary = summarize_usage(turns)
    assert [summary["fields"][key]["value"] for key in ("input_tokens", "output_tokens", "total_tokens")] == [40, 20, 60]
    assert summary["fields"]["cache_read_tokens"]["value"] == 24
    assert summary["fields"]["reasoning_tokens"]["value"] == 8
    trace = {"trace_version": "1", "framework": "provider-fixture", "run_id": "f", "coverage": "complete",
             "events": events, "usage_summary": summary, "demo": True}
    run, evaluation, _ = ingest(store, canonical(trace).encode())
    repeated, _, _ = ingest(store, canonical(store.bundle(run.id)).encode())
    assert repeated.id == run.id
    metrics = {m.key: m.value for m in evaluation.metrics}
    assert metrics["tokens_total"] == 60 and metrics["tokens_cache_read"] == 24 and metrics["tokens_reasoning"] == 8
    assert store.bundle(run.id)["trace"]["events"][0]["context"]["response_id"] == "response-1"
    assert len(run.events) == 4  # Reused response IDs do not erase separately observed requests.
    legacy = {**summary, "fields": {k: summary["fields"][k] for k in BASE_KEYS}}
    assert set(UsageSummary.model_validate(legacy).fields) == set(BASE_KEYS)


def test_oversized_stream_delta_is_bounded_but_later_usage_survives():
    rec = recorder()
    wire = sse({"choices": [{"delta": {"content": "x" * 600000}}]}, payload())
    body = SyncChunks(wire, step=65536)
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=body,
                            headers={"content-type": "text/event-stream"}))) as client, rec.collect():
        with client.stream("POST", URL, json={}) as response:
            for _ in response.iter_bytes():
                pass
            observer = response.extensions["agent_review_observer"]
    assert rec.events[0]["usage"]["tokens"]["total"] == 15
    assert rec.events[0]["context"]["usage_complete"] is False
    assert len(observer.buffer) + len(observer.data) == 0


def test_cr_only_sse_and_explicit_close_of_partly_consumed_response():
    for early in (False, True):
        rec = recorder()
        wire = sse(payload()).replace(b"\r\n", b"\r")
        body = SyncChunks(wire, step=len(wire))
        with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=body,
                                headers={"content-type": "text/event-stream"}))) as client, rec.collect():
            response = client.send(client.build_request("POST", URL, json={}), stream=True)
            iterator = response.iter_bytes()
            if early:
                next(iterator)
                response.close()
                assert rec.events[0]["context"]["usage_missing_reason"] == "stream_interrupted"
                iterator.close()
            else:
                assert b"".join(iterator) == wire
                assert parsed(rec).usage.tokens.total == 15
        assert body.closed


def test_generic_import_without_summary_keeps_interrupted_call_partial(store):
    rec = recorder()
    rec.model("native", payload)
    rec.events[0]["context"]["usage_complete"] = False
    trace = {"trace_version": "1", "framework": "provider-fixture", "run_id": "interrupted", "coverage": "complete",
             "events": rec.events, "demo": True}
    _, evaluation, _ = ingest(store, canonical(trace).encode())
    metric = next(m for m in evaluation.metrics if m.key == "tokens_total")
    assert metric.value == 15 and metric.status == "partial"
