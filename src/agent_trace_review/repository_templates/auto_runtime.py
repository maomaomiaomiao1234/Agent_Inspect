"""Evaluator-owned target-v1 server. Copied into Docker, never imports target on host."""

import asyncio
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

if __package__:
    from ..provider_telemetry import CONTEXT_HEADERS, CURRENT, ModelRecorder, instrument_httpx
else:
    from provider_telemetry import CONTEXT_HEADERS, CURRENT, ModelRecorder, instrument_httpx


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


class Recorder(ModelRecorder):
    def __init__(self, request, config, context=None):
        super().__init__(session_id=request["session_id"], turn=request["turn"],
                         api_url=config["api_url"], model=config["model"], context=context)
        self.request, self.config = request, config
        self.started = time.monotonic()

    def remaining(self):
        remaining = float(self.request["budget"].get("deadline_seconds", 60)) - (time.monotonic() - self.started)
        if remaining <= 0:
            raise TimeoutError("budget deadline")
        return remaining

    def begin(self, kind, name, args=None, kwargs=None):
        self.remaining()
        event, started = super().begin(kind, name)
        if kind == "tool":
            event["tool"] = event.pop("model")
            inputs = safe({"args": list(args or ()), "kwargs": kwargs or {}})
            event["input"] = inputs if isinstance(inputs, dict) else {"omitted": inputs}
        return event, started

    def finish_tool(self, event, started, value=None, error=False):
        event.update(status="error" if error else "completed", duration_ms=round((time.monotonic() - started) * 1000, 3))
        if not error:
            event["output"] = safe(value)

    def tool(self, name, fn, *args, **kwargs):
        event, started = self.begin("tool", name, args, kwargs)
        try:
            value = fn(*args, **kwargs)
            self.finish_tool(event, started, value)
            return value
        except BaseException:
            self.finish_tool(event, started, error=True)
            raise

    async def async_tool(self, name, fn, *args, **kwargs):
        event, started = self.begin("tool", name, args, kwargs)
        try:
            value = await fn(*args, **kwargs)
            self.finish_tool(event, started, value)
            return value
        except BaseException:
            self.finish_tool(event, started, error=True)
            raise

    def prepare_http(self, request):
        remaining = self.remaining()
        request.extensions["timeout"] = {k: remaining for k in ("connect", "read", "write", "pool")}
        maximum = self.request["budget"].get("max_output_tokens")
        if maximum:
            used = sum(e.get("usage", {}).get("tokens", {}).get("output", 0) for e in self.events)
            available = int(maximum) - used
            if available <= 0:
                raise RuntimeError("output budget exhausted")
            try:
                body = json.loads(request.content)
                parameter = next((k for k in ("max_completion_tokens", "max_output_tokens", "max_tokens") if k in body),
                                 "max_output_tokens" if urlsplit(str(request.url)).path.endswith("/responses") else "max_tokens")
                existing = body.get(parameter)
                body[parameter] = min(existing, available) if type(existing) is int and existing > 0 else available
                encoded = json.dumps(body).encode()
                request._content = encoded
                request.stream = httpx.ByteStream(encoded)
                request.headers["content-length"] = str(len(encoded))
            except (ValueError, TypeError, httpx.RequestNotRead):
                pass


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
                                "Configured httpx JSON/SSE API and explicitly wrapped calls only; coverage partial."]}

    @app.post("/task")
    def task(body: TaskRequest, incoming: Request):
        request = body.model_dump()
        try:
            deadline = float(body.budget.get("deadline_seconds", 60))
            maximum = body.budget.get("max_output_tokens")
            if not 0.05 <= deadline <= 60 or maximum is not None and (type(maximum) is not int or not 1 <= maximum <= 100000):
                raise ValueError()
        except (ValueError, TypeError):
            raise HTTPException(status_code=422, detail="invalid budget") from None
        recorder = Recorder(request, config, context={
            key: incoming.headers[header][:200] for key, header in CONTEXT_HEADERS.items() if header in incoming.headers
        })
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
