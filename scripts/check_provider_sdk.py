"""Exercise an installed OpenAI SDK using only httpx.MockTransport, no API/network.

Run with an environment that already has openai, e.g. the smolagents example:
PYTHONPATH=src examples/smolagents/.venv/bin/python scripts/check_provider_sdk.py
"""

import asyncio
import importlib.metadata
import json

import httpx
from openai import AsyncOpenAI, OpenAI

from agent_trace_review.provider_telemetry import ModelRecorder
from agent_trace_review.usage_accounting import summarize_usage

USAGE = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
         "prompt_tokens_details": {"cached_tokens": 6}, "completion_tokens_details": {"reasoning_tokens": 2}}


def recorder(name):
    return ModelRecorder(session_id=name, turn=0, api_url="https://sdk-fixture.invalid/v1", model="requested",
                         context={"assessment_id": "sdk-check", "case_id": name, "budget_id": "b", "attempt": 1})


def completion(*, stream=False):
    return {"id": "chatcmpl-fixture", "created": 1, "model": "resolved-model", "usage": USAGE,
            "object": "chat.completion.chunk" if stream else "chat.completion",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "delta" if stream else "message": {"role": "assistant", "content": "fixture"}}]}


def totals(rec):
    return summarize_usage([{"trace": rec.trace(coverage="complete")}])["fields"]


def sync_check():
    calls = []
    def provider(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(429, json={"error": {"message": "synthetic retry", "type": "rate_limit"}},
                                  headers={"retry-after-ms": "1", "x-request-id": "retry-request"})
        if calls[-1].get("stream"):
            body = "data: " + json.dumps(completion(stream=True)) + "\n\ndata: [DONE]\n\n"
            return httpx.Response(200, text=body, headers={"content-type": "text/event-stream", "x-request-id": "stream-request"})
        return httpx.Response(200, json=completion(), headers={"x-request-id": "success-request"})
    rec = recorder("sync")
    with OpenAI(api_key="synthetic", base_url=rec.config["api_url"], max_retries=1,
                http_client=httpx.Client(transport=httpx.MockTransport(provider))) as client, rec.collect():
        result = rec.model("sdk", client.chat.completions.create, model="requested", messages=[])
        assert result.usage.total_tokens == 15
        assert len(rec.events) == 2 and rec.events[0]["status"] == "error"
        with rec.model("sdk", client.chat.completions.create, model="requested", messages=[], stream=True,
                       stream_options={"include_usage": True}) as stream:
            assert list(stream)[0].usage.total_tokens == 15
    assert len(rec.events) == 3 and len(calls) == 3
    assert totals(rec)["total_tokens"]["value"] == 30
    assert totals(rec)["total_tokens"]["status"] == "partial"  # Retry had no usage.
    assert rec.events[-1]["context"]["provider_request_id"] == "stream-request"
    return {"requests": len(calls), "events": len(rec.events), "known_tokens": 30}


async def async_check():
    rec = recorder("async")
    def provider(request):
        data = json.loads(request.content)
        if request.url.path.endswith("/responses"):
            response = {"id": "resp-fixture", "object": "response", "created_at": 1, "model": "resolved-model",
                        "status": "completed", "output": [], "usage": {"input_tokens": 10, "output_tokens": 5,
                        "total_tokens": 15, "input_tokens_details": {"cached_tokens": 6},
                        "output_tokens_details": {"reasoning_tokens": 2}}}
            if data.get("stream"):
                body = "event: response.completed\ndata: " + json.dumps({"type": "response.completed",
                         "sequence_number": 1, "response": response}) + "\n\n"
                return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
            return httpx.Response(200, json=response)
        return httpx.Response(200, json=completion())
    async with AsyncOpenAI(api_key="synthetic", base_url=rec.config["api_url"], max_retries=0,
                           http_client=httpx.AsyncClient(transport=httpx.MockTransport(provider))) as client:
        with rec.collect():
            await rec.async_model("sdk", client.chat.completions.create, model="requested", messages=[])
            await rec.async_model("sdk", client.responses.create, model="requested", input="fixture")
            async with await rec.async_model("sdk", client.responses.create, model="requested", input="fixture",
                                             stream=True) as stream:
                assert len([event async for event in stream]) == 1
    assert len(rec.events) == 3
    assert totals(rec)["total_tokens"]["value"] == 45 and totals(rec)["total_tokens"]["status"] == "complete"
    assert totals(rec)["reasoning_tokens"]["value"] == 6
    return {"events": len(rec.events), "tokens": 45}


if __name__ == "__main__":
    print(json.dumps({"openai_version": importlib.metadata.version("openai"), "sync": sync_check(),
                      "async": asyncio.run(async_check()), "network": "none; MockTransport only"}))
