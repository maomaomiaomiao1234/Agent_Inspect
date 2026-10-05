"""Opt-in, context-local collection of provider-reported model usage.

No SDK dependency, prompt logging, price estimation, or automatic retries. This file
is also copied verbatim into generated targets, so keep it independent of the app.
"""

import codecs
import contextvars
import io
import json
import threading
import time
from contextlib import contextmanager
from urllib.parse import urlsplit

CURRENT = contextvars.ContextVar("agent_review_collector", default=None)
_MODEL_SCOPE = contextvars.ContextVar("agent_review_model_scope", default=None)
_INSTALL_LOCK = threading.Lock()
_LIMIT = 512 * 1024
CONTEXT_HEADERS = {
    key: "x-agent-review-" + key.replace("_", "-")
    for key in ("assessment_id", "case_id", "budget_id", "attempt")
}


def _get(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _integer(value):
    return value if type(value) is int and 0 <= value <= 10**12 else None


def normalize_usage(value):
    """Normalize Chat Completions, Responses, DeepSeek and native token_usage.

    Cache/reasoning are subdivisions, never additional total tokens. Missing or
    invalid numbers stay unknown. Conflicting totals are left to contract checks.
    """
    usage = next((_get(value, key) for key in ("usage", "usage_metadata", "token_usage")
                  if _get(value, key) is not None), None)
    if usage is None:
        return None
    fields = {
        "input": ("prompt_tokens", "input_tokens"),
        "output": ("completion_tokens", "output_tokens"),
        "total": ("total_tokens",),
        "reasoning": ("reasoning_tokens",),
        "cache_read": ("prompt_cache_hit_tokens",),
        "cache_miss": ("prompt_cache_miss_tokens",),
    }
    tokens = {}
    for name, keys in fields.items():
        number = next((_integer(_get(usage, key)) for key in keys
                       if _integer(_get(usage, key)) is not None), None)
        if number is not None:
            tokens[name] = number
    for keys, mapping in (
        (("prompt_tokens_details", "input_tokens_details"),
         {"cached_tokens": "cache_read", "cache_write_tokens": "cache_write"}),
        (("completion_tokens_details", "output_tokens_details"), {"reasoning_tokens": "reasoning"}),
    ):
        for key in keys:
            for provider_key, name in mapping.items():
                number = _integer(_get(_get(usage, key), provider_key))
                if number is not None:
                    tokens.setdefault(name, number)
    if "total" not in tokens and "input" in tokens and "output" in tokens:
        tokens["total"] = tokens["input"] + tokens["output"]
    return {"tokens": tokens} if tokens else None


def _identity(value, limit=200):
    return value if isinstance(value, str) and 0 < len(value) <= limit and all(
        32 <= ord(c) < 127 for c in value) else None


class ModelRecorder:
    """One recorder per task turn. Context propagates through asyncio/to_thread.

    Use collect() around calls to an httpx-based SDK, or model()/async_model()
    around a native model function. Explicit stream wrappers support SDK chunks.
    Raw threads must run in contextvars.copy_context().run.
    """

    def __init__(self, *, session_id, turn, api_url="", model="unknown", context=None):
        self.request = {"session_id": session_id, "turn": turn, "budget": {}}
        self.config = {"api_url": api_url, "model": model}
        self.context = {k: v for k, v in (context or {}).items() if k in CONTEXT_HEADERS}
        self.context.update(session_id=session_id, turn=turn)
        self.events = []
        self._lock = threading.RLock()

    @contextmanager
    def collect(self):
        instrument_httpx()
        marker = CURRENT.set(self)
        try:
            yield self
        finally:
            CURRENT.reset(marker)

    def begin(self, kind, name, args=None, kwargs=None):
        with self._lock:
            if len(self.events) >= 180:
                raise RuntimeError("event limit")
            event = {"id": f"call-{len(self.events) + 1}", "kind": kind, "status": "unknown",
                     "model": str(name)[:200], "context": dict(self.context)}
            event["context"]["call_id"] = event["id"]
            self.events.append(event)
            return event, time.monotonic()

    def prepare_http(self, request):
        """Hook for target-owned deadline/output limits; default observes only."""

    def trace(self, *, coverage="partial"):
        # An unfinished call must not turn known intermediate usage into a full total.
        if any(e["status"] == "unknown" for e in self.events):
            coverage = "partial"
        return {"trace_version": "agent-review/target-trace-v1",
                "session_id": self.request["session_id"], "turn": self.request["turn"],
                "coverage": coverage, "events": self.events}

    def model(self, name, fn, *args, **kwargs):
        parent = _MODEL_SCOPE.get()
        if parent is not None and parent.recorder is self:
            return fn(*args, **kwargs)
        capture = Capture(self, name, "model_wrapper")
        marker = _MODEL_SCOPE.set(capture)
        current = CURRENT.set(self)
        try:
            value = fn(*args, **kwargs)
            if capture.http_calls:
                return value
            if hasattr(value, "__next__"):
                capture.streaming = True
                return ModelStream(value, capture)
            capture.observe(value)
            capture.finish()
            return value
        except BaseException:
            if not capture.http_calls:
                capture.finish(error=True)
            raise
        finally:
            CURRENT.reset(current)
            _MODEL_SCOPE.reset(marker)

    async def async_model(self, name, fn, *args, **kwargs):
        parent = _MODEL_SCOPE.get()
        if parent is not None and parent.recorder is self:
            return await fn(*args, **kwargs)
        capture = Capture(self, name, "model_wrapper")
        marker = _MODEL_SCOPE.set(capture)
        current = CURRENT.set(self)
        try:
            value = await fn(*args, **kwargs)
            if capture.http_calls:
                return value
            if hasattr(value, "__anext__"):
                capture.streaming = True
                return AsyncModelStream(value, capture)
            capture.observe(value)
            capture.finish()
            return value
        except BaseException:
            if not capture.http_calls:
                capture.finish(error=True)
            raise
        finally:
            CURRENT.reset(current)
            _MODEL_SCOPE.reset(marker)


class Capture:
    def __init__(self, recorder, model, source):
        self.recorder = recorder
        self.event, self.started = recorder.begin("llm", model)
        self.event.setdefault("context", {}).update(usage_source="provider_reported", collection=source,
                                                     requested_model=str(model)[:200], usage_complete=False)
        self.http_calls = 0
        self.streaming = self.terminal = self.failed = self.done = self.invalid = False

    def observe(self, value):
        kind = _get(value, "type")
        if kind in {"response.completed", "response.incomplete", "response.failed"}:
            self.terminal = True
            self.failed = kind == "response.failed"
        payload = _get(value, "response", value)
        if _get(payload, "status") == "failed":
            self.failed = True
        context = self.event["context"]
        for key, original in (("response_id", "id"), ("provider_request_id", "_request_id")):
            identity = _identity(_get(payload, original))
            if identity:
                context[key] = identity
        model = _identity(_get(payload, "model"))
        if model:
            self.event["model"] = model
        for choice in _get(payload, "choices", []) or []:
            reason = _get(choice, "finish_reason")
            if reason in {"stop", "length", "tool_calls", "content_filter", "function_call", "insufficient_system_resource"}:
                context["finish_reason"] = reason
        usage = normalize_usage(payload)
        if usage:
            # A streaming usage chunk is a snapshot for this request, not a delta.
            self.event["usage"] = usage

    def finish(self, *, error=False, interrupted=False):
        if self.done:
            return
        self.done = True
        complete = not (error or self.failed or self.invalid or interrupted)
        if self.streaming and not self.terminal:
            complete = False
        self.event.update(status="error" if error or self.failed else "completed" if complete else "unknown",
                          duration_ms=round((time.monotonic() - self.started) * 1000, 3))
        context = self.event["context"]
        context["usage_complete"] = complete
        context["usage_status"] = ("reported" if complete else "partial") if self.event.get("usage") else "unknown"
        if not complete:
            context["usage_missing_reason"] = "invalid_or_oversized_response" if self.invalid else (
                "request_failed" if error or self.failed else "stream_interrupted")
        elif not self.event.get("usage"):
            context["usage_missing_reason"] = "provider_usage_missing"


class ModelStream:
    def __init__(self, stream, capture):
        self.stream, self.capture = stream, capture

    def __iter__(self):
        return self

    def __next__(self):
        try:
            value = next(self.stream)
            self.capture.observe(value)
            return value
        except StopIteration:
            # SDK iterators consume the transport's DONE marker internally.
            self.capture.terminal = True
            self.capture.finish()
            raise
        except BaseException:
            self.capture.finish(error=True)
            raise

    def close(self):
        try:
            close = getattr(self.stream, "close", None)
            if close:
                close()
        finally:
            self.capture.finish(interrupted=True)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class AsyncModelStream:
    def __init__(self, stream, capture):
        self.stream, self.capture = stream, capture

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            value = await self.stream.__anext__()
            self.capture.observe(value)
            return value
        except StopAsyncIteration:
            self.capture.terminal = True
            self.capture.finish()
            raise
        except BaseException:
            self.capture.finish(error=True)
            raise

    async def aclose(self):
        try:
            close = getattr(self.stream, "aclose", None) or getattr(self.stream, "close", None)
            if close:
                await close()
        finally:
            self.capture.finish(interrupted=True)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.aclose()


class BodyObserver:
    """Incremental decoded SSE/JSON reader. Never retains a full streamed body."""

    def __init__(self, capture, response):
        self.capture, self.response = capture, response
        capture.streaming = "text/event-stream" in response.headers.get("content-type", "").lower()
        identity = _identity(response.headers.get("x-request-id"))
        if identity:
            capture.event["context"]["provider_request_id"] = identity
        self.decoder = io.IncrementalNewlineDecoder(codecs.getincrementaldecoder("utf-8")("replace"), True)
        self.buffer, self.data = "", ""
        self.discard = False
        self.yielding = False

    def feed(self, chunk):
        if self.capture.done:
            return
        self.text(self.decoder.decode(chunk))

    def text(self, text):
        if not self.capture.streaming:
            if len(self.buffer) + len(text) <= _LIMIT:
                self.buffer += text
            else:
                self.capture.invalid = True
                self.buffer = ""
            return
        # Splitting each transport chunk also bounds a single giant delta line.
        for part in text.splitlines(keepends=True):
            if len(self.buffer) + len(part) > _LIMIT:
                self.capture.invalid = True
                self.discard = True
                self.buffer = self.data = ""
            elif not self.discard:
                self.buffer += part
            if part.endswith("\n"):
                if not self.discard:
                    self.line(self.buffer.rstrip("\n"))
                self.buffer = ""
                self.discard = False

    def line(self, line):
        if not line:
            value, self.data = self.data, ""
            if value.strip() == "[DONE]":
                self.capture.terminal = True
            elif value:
                self.parse(value)
        elif line.startswith("data:"):
            if len(self.data) + len(line) <= _LIMIT:
                self.data += line[5:].lstrip(" ") + "\n"
            else:
                self.capture.invalid = True
                self.data = ""

    def parse(self, value):
        try:
            self.capture.observe(json.loads(value))
        except (ValueError, TypeError, RecursionError):
            self.capture.invalid = True

    def finish(self, *, error=False, interrupted=False):
        if self.capture.done:
            return
        if not interrupted and not error:
            self.text(self.decoder.decode(b"", final=True))
        if not self.capture.streaming and not interrupted and not error and not self.capture.invalid:
            self.parse(self.buffer)
        self.capture.finish(error=error or not self.response.is_success, interrupted=interrupted)
        self.buffer = self.data = ""


def instrument_httpx():
    """Install once; only requests inside CURRENT to the configured model API count."""
    import httpx

    with _INSTALL_LOCK:
        if getattr(httpx.Client.send, "_agent_review_observer", False):
            return
        send, async_send = httpx.Client.send, httpx.AsyncClient.send
        iter_bytes, aiter_bytes = httpx.Response.iter_bytes, httpx.Response.aiter_bytes
        close, aclose = httpx.Response.close, httpx.Response.aclose

        def prepare(request):
            recorder = CURRENT.get()
            if recorder is None or not recorder.config.get("api_url") or request.method != "POST":
                return None
            actual, configured = urlsplit(str(request.url)), urlsplit(recorder.config["api_url"])
            prefix = configured.path.rstrip("/").removesuffix("/chat/completions").removesuffix("/responses")
            if (actual.scheme, actual.netloc) != (configured.scheme, configured.netloc) or actual.path not in {
                prefix + "/chat/completions", prefix + "/responses"
            }:
                return None
            scope = _MODEL_SCOPE.get()
            with recorder._lock:
                if scope is not None and scope.recorder is recorder and scope.http_calls == 0:
                    capture = scope
                    capture.event["context"]["collection"] = "httpx"
                else:
                    capture = Capture(recorder, recorder.config["model"], "httpx")
                if scope is not None and scope.recorder is recorder:
                    scope.http_calls += 1
            try:
                body = json.loads(request.content)
                requested = _identity(body.get("model"))
                if requested:
                    capture.event["model"] = requested
                    capture.event["context"]["requested_model"] = requested
            except (ValueError, TypeError, AttributeError, httpx.RequestNotRead):
                pass
            try:
                recorder.prepare_http(request)
            except BaseException:
                capture.finish(error=True)
                raise
            return capture

        def wrapped_send(self, request, **kwargs):
            capture = prepare(request)
            if capture is None:
                return send(self, request, **kwargs)
            streaming = kwargs.pop("stream", False)
            response = None
            try:
                response = send(self, request, stream=True, **kwargs)
                observer = BodyObserver(capture, response)
                response.extensions["agent_review_observer"] = observer
                if response.is_stream_consumed:
                    observer.feed(response.content)
                    observer.finish()
                elif not streaming:
                    response.read()
                return response
            except BaseException:
                capture.finish(error=True)
                if response is not None:
                    response.close()
                raise

        async def wrapped_async_send(self, request, **kwargs):
            capture = prepare(request)
            if capture is None:
                return await async_send(self, request, **kwargs)
            streaming = kwargs.pop("stream", False)
            response = None
            try:
                response = await async_send(self, request, stream=True, **kwargs)
                observer = BodyObserver(capture, response)
                response.extensions["agent_review_observer"] = observer
                if response.is_stream_consumed:
                    observer.feed(response.content)
                    observer.finish()
                elif not streaming:
                    await response.aread()
                return response
            except BaseException:
                capture.finish(error=True)
                if response is not None:
                    await response.aclose()
                raise

        def wrapped_iter(self, chunk_size=None):
            observer = self.extensions.get("agent_review_observer")
            try:
                for chunk in iter_bytes(self, chunk_size):
                    if observer:
                        observer.feed(chunk)
                        observer.yielding = True
                    yield chunk
                    if observer:
                        observer.yielding = False
            except BaseException:
                if observer:
                    observer.finish(error=True)
                raise
            else:
                if observer:
                    observer.finish()

        async def wrapped_aiter(self, chunk_size=None):
            observer = self.extensions.get("agent_review_observer")
            try:
                async for chunk in aiter_bytes(self, chunk_size):
                    if observer:
                        observer.feed(chunk)
                        observer.yielding = True
                    yield chunk
                    if observer:
                        observer.yielding = False
            except BaseException:
                if observer:
                    observer.finish(error=True)
                raise
            else:
                if observer:
                    observer.finish()

        def wrapped_close(self):
            observer = self.extensions.get("agent_review_observer")
            # httpx automatically closes the raw stream before iter_bytes exits.
            if observer and (not self.is_stream_consumed or observer.yielding):
                observer.finish(interrupted=True)
            return close(self)

        async def wrapped_aclose(self):
            observer = self.extensions.get("agent_review_observer")
            if observer and (not self.is_stream_consumed or observer.yielding):
                observer.finish(interrupted=True)
            return await aclose(self)

        wrapped_send._agent_review_observer = True
        httpx.Client.send, httpx.AsyncClient.send = wrapped_send, wrapped_async_send
        httpx.Response.iter_bytes, httpx.Response.aiter_bytes = wrapped_iter, wrapped_aiter
        httpx.Response.close, httpx.Response.aclose = wrapped_close, wrapped_aclose
