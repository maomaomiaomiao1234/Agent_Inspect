"""Evaluator-owned target-v1 server. Copied into Docker, never imports target on host."""

import asyncio
import contextvars
import hashlib
import inspect
import json
import os
import re
import secrets
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

CURRENT = contextvars.ContextVar("adapter_recorder", default=None)
EXPLICIT_MODEL = contextvars.ContextVar("adapter_explicit_model", default=False)


class TaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protocol: str = Field(default="agent-review/target-v1", pattern=r"^agent-review/target-v1$")
    session_id: str = Field(min_length=1, max_length=100)
    turn: int = Field(ge=0, le=9)
    prompt: str = Field(min_length=1, max_length=20000)
    messages: list[dict] = Field(max_length=30)
    input: dict = Field(default_factory=dict)
    budget: dict


def safe(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        return value if len(encoded) < 12000 else "[omitted:large-value]"
    except (TypeError, ValueError):
        return "[omitted:non-json-value]"


def usage_of(value):
    keys = ("usage", "usage_metadata", "token_usage")
    usage = next((value.get(k) for k in keys if value.get(k) is not None), None) if isinstance(value, dict) else next(
        (getattr(value, k) for k in keys if getattr(value, k, None) is not None), None)
    if usage is None:
        return None
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump()
    elif not isinstance(usage, dict):
        usage = {key: getattr(usage, key) for key in ("input_tokens", "output_tokens", "total_tokens") if hasattr(usage, key)}
    if not isinstance(usage, dict):
        return None
    tokens = {}
    for name, keys in {"input": ("prompt_tokens", "input_tokens"), "output": ("completion_tokens", "output_tokens"),
                       "total": ("total_tokens",)}.items():
        found = next((usage[k] for k in keys if type(usage.get(k)) is int and usage[k] >= 0), None)
        if found is not None:
            tokens[name] = found
    if "input" in tokens and "output" in tokens and "total" not in tokens:
        tokens["total"] = tokens["input"] + tokens["output"]
    return {"tokens": tokens} if tokens else None


class Recorder:
    def __init__(self, request, config):
        self.request, self.config = request, config
        self.events = []
        self.started = time.monotonic()

    def remaining(self):
        deadline = float(self.request["budget"].get("deadline_seconds", 60))
        remaining = deadline - (time.monotonic() - self.started)
        if remaining <= 0:
            raise TimeoutError("budget deadline")
        return remaining

    def begin(self, kind, name, args=None, kwargs=None):
        self.remaining()
        if len(self.events) >= 180:
            raise RuntimeError("event limit")
        event = {"id": f"call-{len(self.events) + 1}", "kind": kind, "status": "unknown"}
        event["model" if kind == "llm" else "tool"] = str(name)[:200]
        if kind == "tool":
            inputs = safe({"args": list(args or ()), "kwargs": kwargs or {}})
            event["input"] = inputs if isinstance(inputs, dict) else {"omitted": inputs}
        self.events.append(event)
        return event, time.monotonic()

    def finish(self, event, started, value=None, error=False):
        event.update(status="error" if error else "completed", duration_ms=round((time.monotonic() - started) * 1000, 3))
        if event["kind"] == "llm":
            usage = usage_of(value)
            if usage:
                event["usage"] = usage
        elif not error:
            event["output"] = safe(value)

    def _call(self, kind, name, fn, args, kwargs):
        event, started = self.begin(kind, name, args, kwargs)
        marker = EXPLICIT_MODEL.set(kind == "llm" or EXPLICIT_MODEL.get())
        try:
            value = fn(*args, **kwargs)
            self.finish(event, started, value)
            return value
        except Exception:
            self.finish(event, started, error=True)
            raise
        finally:
            EXPLICIT_MODEL.reset(marker)

    async def _async_call(self, kind, name, fn, args, kwargs):
        event, started = self.begin(kind, name, args, kwargs)
        marker = EXPLICIT_MODEL.set(kind == "llm" or EXPLICIT_MODEL.get())
        try:
            value = await fn(*args, **kwargs)
            self.finish(event, started, value)
            return value
        except Exception:
            self.finish(event, started, error=True)
            raise
        finally:
            EXPLICIT_MODEL.reset(marker)

    def model(self, name, fn, *args, **kwargs):
        return self._call("llm", name, fn, args, kwargs)

    def tool(self, name, fn, *args, **kwargs):
        return self._call("tool", name, fn, args, kwargs)

    async def async_model(self, name, fn, *args, **kwargs):
        return await self._async_call("llm", name, fn, args, kwargs)

    async def async_tool(self, name, fn, *args, **kwargs):
        return await self._async_call("tool", name, fn, args, kwargs)


def instrument_httpx():
    """Observe configured API only. Streaming/missing usage remains partial or unknown."""
    if getattr(httpx.Client.send, "_agent_review_observer", False):
        return
    send, async_send = httpx.Client.send, httpx.AsyncClient.send
    read, async_read = httpx.Response.read, httpx.Response.aread

    def prepare(request):
        recorder = CURRENT.get()
        if recorder is None:
            return None
        actual, configured = urlsplit(str(request.url)), urlsplit(recorder.config["api_url"])
        prefix = configured.path.rstrip("/").removesuffix("/chat/completions").removesuffix("/responses")
        if actual.scheme != configured.scheme or actual.netloc != configured.netloc or actual.path not in {prefix + "/chat/completions", prefix + "/responses"}:
            return None
        event, started = (None, time.monotonic()) if EXPLICIT_MODEL.get() else recorder.begin("llm", recorder.config["model"])
        remaining = recorder.remaining()
        request.extensions["timeout"] = {k: remaining for k in ("connect", "read", "write", "pool")}
        # Apply the remaining output budget to actual supported model requests.
        maximum = recorder.request["budget"].get("max_output_tokens")
        if maximum:
            used = sum(e.get("usage", {}).get("tokens", {}).get("output", 0) for e in recorder.events)
            available = int(maximum) - used
            if available <= 0:
                if event is not None:
                    recorder.finish(event, started, error=True)
                raise RuntimeError("output budget exhausted")
            try:
                body = json.loads(request.content)
                parameter = next((k for k in ("max_completion_tokens", "max_output_tokens", "max_tokens") if k in body),
                                 "max_output_tokens" if actual.path.endswith("/responses") else "max_tokens")
                existing = body.get(parameter)
                body[parameter] = min(existing, available) if type(existing) is int and existing > 0 else available
                encoded = json.dumps(body).encode()
                request._content = encoded
                request.stream = httpx.ByteStream(encoded)
                request.headers["content-length"] = str(len(encoded))
            except (ValueError, TypeError, httpx.RequestNotRead):
                pass
        return recorder, event, started

    def finish_response(response):
        pending = response.extensions.pop("agent_review_pending", None)
        if pending:
            recorder, event, started = pending
            if event is None:
                return
            try:
                payload = response.json()
            except (ValueError, httpx.ResponseNotRead):
                payload = None
            recorder.finish(event, started, payload, error=not response.is_success)

    def wrapped_send(self, request, **kwargs):
        pending = prepare(request)
        try:
            response = send(self, request, **kwargs)
        except Exception:
            if pending and pending[1] is not None:
                pending[0].finish(pending[1], pending[2], error=True)
            raise
        if pending:
            response.extensions["agent_review_pending"] = pending
            if response.is_stream_consumed:
                finish_response(response)
        return response

    async def wrapped_async_send(self, request, **kwargs):
        pending = prepare(request)
        try:
            response = await async_send(self, request, **kwargs)
        except Exception:
            if pending and pending[1] is not None:
                pending[0].finish(pending[1], pending[2], error=True)
            raise
        if pending:
            response.extensions["agent_review_pending"] = pending
            if response.is_stream_consumed:
                finish_response(response)
        return response

    def wrapped_read(self):
        value = read(self)
        finish_response(self)
        return value

    async def wrapped_async_read(self):
        value = await async_read(self)
        finish_response(self)
        return value

    wrapped_send._agent_review_observer = True
    httpx.Client.send, httpx.AsyncClient.send = wrapped_send, wrapped_async_send
    httpx.Response.read, httpx.Response.aread = wrapped_read, wrapped_async_read


def create_app(bridge, entry, source_root, config, service_token, startup_error=None):
    instrument_httpx()
    app = FastAPI(title="Generated Python agent target")
    sessions, guard = {}, threading.RLock()
    expected_path = (source_root / entry["path"]).resolve()
    if hashlib.sha256(expected_path.read_bytes()).hexdigest() != entry["source_hash"]:
        raise ValueError("native source hash differs")

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        if not service_token or not secrets.compare_digest(request.headers.get("authorization", ""), "Bearer " + service_token):
            return JSONResponse({"error": "target credential required"}, status_code=401)
        size, chunks = 0, []
        async for chunk in request.stream():
            size += len(chunk)
            if size > 128 * 1024:
                return JSONResponse({"error": "request too large"}, status_code=413)
            chunks.append(chunk)
        request._body = b"".join(chunks)
        return await call_next(request)

    @app.get("/health")
    def health():
        return {"status": "ok", "usage_mode": "model", "usage_collection": "call_usage",
                "adapter": "generated-python-v1", "native_entry": entry,
                "bridge_ready": startup_error is None,
                "limitations": ["Observed entry is target-reported, not attestation.",
                                "httpx nonstreaming configured API and explicitly wrapped calls only; coverage partial."]}

    @app.post("/task")
    def task(body: TaskRequest):
        request = body.model_dump()
        try:
            deadline = float(body.budget.get("deadline_seconds", 60))
            maximum = body.budget.get("max_output_tokens")
            if not 0.05 <= deadline <= 60 or maximum is not None and (type(maximum) is not int or not 1 <= maximum <= 100000):
                raise ValueError()
        except (ValueError, TypeError):
            raise HTTPException(status_code=422, detail="invalid budget") from None
        recorder = Recorder(request, config)
        observed = False
        failure = None
        def profile(frame, event, arg):
            nonlocal observed
            if event == "call" and frame.f_code.co_name == entry["symbol"].split(".")[-1] and frame.f_code.co_firstlineno == entry["line"]:
                if Path(frame.f_code.co_filename).resolve() == expected_path:
                    observed = True
        response = {"protocol": "agent-review/target-v1", "usage_mode": "model", "output": None}
        marker = CURRENT.set(recorder)
        previous_profile = sys.getprofile()
        try:
            if startup_error is not None:
                raise startup_error
            with guard:
                if body.session_id not in sessions:
                    if len(sessions) >= 64:
                        idle = next((k for k, v in sessions.items() if not v[1].locked()), None)
                        if idle is None:
                            raise RuntimeError("session limit")
                        sessions.pop(idle)
                    sessions[body.session_id] = [None, threading.Lock(), False]
                session = sessions[body.session_id]
                if not session[1].acquire(blocking=False):
                    raise RuntimeError("concurrent session")
            try:
                if not session[2]:
                    agent = bridge.create_agent(config, recorder)
                    session[0] = asyncio.run(agent) if inspect.isawaitable(agent) else agent
                    session[2] = True
                sys.setprofile(profile)
                value = bridge.run_agent(session[0], request, recorder)
                value = asyncio.run(value) if inspect.isawaitable(value) else value
                recorder.remaining()
                if isinstance(value, str):
                    try:
                        value = json.loads(value)
                    except ValueError:
                        pass
                json.dumps(value, allow_nan=False)
                response["output"] = value
                if not observed:
                    raise RuntimeError("native entry not observed")
            finally:
                session[1].release()
        except Exception as exc:
            failure = exc
            response.update(execution_status="error", error="agent_execution_failed")
        finally:
            sys.setprofile(previous_profile)
            CURRENT.reset(marker)
        response["adapter_evidence"] = {k: entry[k] for k in ("path", "symbol", "source_hash")}
        response["adapter_evidence"]["observed"] = observed
        if failure:
            response["adapter_evidence"]["error_type"] = type(failure).__name__[:100]
            missing = getattr(failure, "name", None) if isinstance(failure, ModuleNotFoundError) else None
            if isinstance(missing, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,199}", missing):
                response["adapter_evidence"]["missing_module"] = missing
        response["trace"] = {"trace_version": "agent-review/target-trace-v1", "session_id": body.session_id,
                             "turn": body.turn, "coverage": "partial", "events": recorder.events}
        if len(json.dumps(response).encode()) > 250 * 1024:
            response.update(output=None, execution_status="error", error="agent_execution_failed")
            response["trace"]["events"] = [{k: v for k, v in e.items() if k not in {"input", "output"}} for e in recorder.events]
        return response

    return app


if __name__ == "__main__":
    import uvicorn
    source = Path("/opt/agent/source")
    sys.path[:0] = [str(source), str(source / "src")]
    startup_error = None
    try:
        import bridge
    except Exception as exc:
        bridge, startup_error = None, exc
    config = {"api_url": os.environ["AGENT_REVIEW_TARGET_API_URL"], "model": os.environ["AGENT_REVIEW_TARGET_MODEL"],
              "api_key": os.environ["AGENT_REVIEW_TARGET_TOKEN"]}
    app = create_app(bridge, json.loads(Path("/opt/agent/entry.json").read_text()), source, config,
                     os.environ["ADAPTER_SERVICE_TOKEN"], startup_error=startup_error)
    uvicorn.run(app, host="0.0.0.0", port=9000)
