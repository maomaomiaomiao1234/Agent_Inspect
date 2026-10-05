"""Task-scoped model proxy with durable, content-free provider usage records.

Opt-in local deployment pilot. Upstream credentials stay in the review process;
the target receives a revocable turn credential, never a provider credential.
"""

import asyncio
import hashlib
import json
import os
import secrets
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from .provider_telemetry import BodyObserver, Capture, ModelRecorder
from .usage_accounting import summarize_usage
from .util import canonical, now

LEASE_HEADER = "x-agent-review-gateway-token"
REQUEST_HEADER = "x-agent-review-gateway-request-id"


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class GatewayStore:
    def __init__(self, store):
        self.store = store
        with store.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS gateway_leases (
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL,
                    expires REAL NOT NULL, active INTEGER NOT NULL, context TEXT NOT NULL, budget TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS gateway_calls (
                    id TEXT PRIMARY KEY, lease_id TEXT NOT NULL, job_id TEXT NOT NULL,
                    state TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, event TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS gateway_calls_job ON gateway_calls(job_id);
                CREATE INDEX IF NOT EXISTS gateway_calls_lease ON gateway_calls(lease_id);
            """)

    def issue(self, job_id, context, budget):
        token, lease = secrets.token_urlsafe(32), "lease_" + uuid.uuid4().hex
        expires = time.time() + budget["deadline_seconds"]
        with self.store.connect() as conn:
            conn.execute("INSERT INTO gateway_leases VALUES (?, ?, ?, ?, 1, ?, ?)",
                         (lease, job_id, token_hash(token), expires, canonical(context), canonical(budget)))
        return lease, token

    def resolve(self, token):
        with self.store.connect() as conn:
            row = conn.execute("SELECT id,job_id,expires,active,context,budget FROM gateway_leases WHERE token_hash=?",
                               (token_hash(token),)).fetchone()
        if row is None or not row[3] or row[2] <= time.time():
            raise HTTPException(401, "gateway_credential_expired_or_invalid")
        return {"id": row[0], "job_id": row[1], "expires": row[2], "context": json.loads(row[4]),
                "budget": json.loads(row[5])}

    def reserve(self, lease, event):
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute("SELECT active,expires FROM gateway_leases WHERE id=?", (lease["id"],)).fetchone()
            if not active or not active[0] or active[1] <= time.time():
                raise HTTPException(401, "gateway_credential_expired_or_invalid")
            rows = conn.execute("SELECT state,event FROM gateway_calls WHERE lease_id=?", (lease["id"],)).fetchall()
            if len(rows) >= 180 or conn.execute("SELECT COUNT(*) FROM gateway_calls WHERE job_id=?",
                                               (lease["job_id"],)).fetchone()[0] >= 2000:
                raise HTTPException(429, "gateway_request_limit")
            # Serialize model calls within a turn so outstanding requests cannot
            # reserve the same remaining output budget concurrently.
            if any(row[0] == "running" for row in rows):
                raise HTTPException(429, "gateway_turn_request_in_progress")
            known = sum(json.loads(row[1]).get("usage", {}).get("tokens", {}).get("output", 0) for row in rows)
            maximum = lease["budget"].get("max_output_tokens")
            remaining = maximum - known if maximum else None
            if remaining is not None and remaining <= 0:
                raise HTTPException(429, "gateway_output_budget_exhausted")
            stamp = now()
            conn.execute("INSERT INTO gateway_calls VALUES (?, ?, ?, 'running', ?, ?, ?)",
                         (event["id"], lease["id"], lease["job_id"], stamp, stamp, canonical(event)))
        return remaining

    def save(self, event, *, state="running"):
        with self.store.connect() as conn:
            conn.execute("UPDATE gateway_calls SET state=?,updated_at=?,event=? WHERE id=? AND state='running'",
                         (state, now(), canonical(event), event["id"]))

    def revoke(self, lease):
        with self.store.connect() as conn:
            conn.execute("UPDATE gateway_leases SET active=0 WHERE id=?", (lease,))

    def events(self, *, job_id=None, lease_id=None):
        column, key = ("job_id", job_id) if job_id is not None else ("lease_id", lease_id)
        with self.store.connect() as conn:
            rows = conn.execute(f"SELECT event FROM gateway_calls WHERE {column}=? ORDER BY created_at,id", (key,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def snapshot(self, job_id):
        events = self.events(job_id=job_id)
        usage = summarize_usage([{"usage_mode": "model", "trace": {"coverage": "complete", "events": events}}])
        usage["provenance"] = "gateway_reported"
        for item in usage["fields"].values():
            if item["value"] is not None:
                item["source"] = "gateway"
            item["reason"] = "仅统计经过网关的请求；不与目标自报用量相加。 " + item["reason"]
        return {"version": "agent-review/gateway-v1", "request_count": len(events), "usage": usage,
                "pending": sum(e["context"].get("gateway_state") == "running" for e in events),
                "scope": "gateway_requests_only", "events": events}

    def recover(self):
        with self.store.connect() as conn:
            conn.execute("UPDATE gateway_leases SET active=0")
            rows = conn.execute("SELECT id,event FROM gateway_calls WHERE state='running'").fetchall()
            for call_id, raw in rows:
                event = json.loads(raw)
                event["status"] = "unknown"
                event["context"].update(usage_complete=False, usage_missing_reason="gateway_restarted",
                                        gateway_state="interrupted")
                conn.execute("UPDATE gateway_calls SET state='interrupted',updated_at=?,event=? WHERE id=?",
                             (now(), canonical(event), call_id))


@dataclass(repr=False)
class GatewayBinding:
    gateway: object
    job_id: str
    api_url: str
    model: str
    token: str = field(repr=False)
    environment: dict

    def issue(self, context, payload):
        return self.gateway.db.issue(self.job_id, {**context, "assessment_id": self.job_id,
            "session_id": payload["session_id"], "turn": payload["turn"]}, payload["budget"])

    def finish(self, lease):
        self.gateway.db.revoke(lease)
        asyncio.run_coroutine_threadsafe(self.gateway.settle(lease), self.gateway.loop).result(timeout=10)
        return {"events": self.gateway.db.events(lease_id=lease), "scope": "gateway_requests_only"}


class ModelGateway:
    def __init__(self, store, *, transport=None, drain_seconds=3):
        self.db = GatewayStore(store)
        self.transport = transport
        self.drain_seconds = drain_seconds
        self.bindings, self.tasks = {}, {}
        self.server = self.thread = self.loop = self.socket = None
        self.lock = threading.Lock()
        self.app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        self.app.post("/v1/chat/completions")(self.proxy)
        self.app.post("/v1/responses")(self.proxy)

    def start(self):
        with self.lock:
            if self.server is not None:
                return
            host = os.environ.get("AGENT_REVIEW_GATEWAY_HOST", "127.0.0.1")
            if host not in {"127.0.0.1", "0.0.0.0"}:
                raise ValueError("网关监听地址仅支持 127.0.0.1 或 0.0.0.0。")
            sock = socket.socket()
            sock.bind((host, 0))
            self.socket = sock
            self.port = sock.getsockname()[1]
            self.server = uvicorn.Server(uvicorn.Config(self.app, log_level="error", access_log=False,
                                                       lifespan="off", timeout_graceful_shutdown=3))
            ready = threading.Event()
            def run():
                async def serve():
                    self.loop = asyncio.get_running_loop()
                    ready.set()
                    await self.server.serve(sockets=[sock])
                asyncio.run(serve())
            self.thread = threading.Thread(target=run, name="model-gateway", daemon=True)
            self.thread.start()
            if not ready.wait(5):
                raise RuntimeError("gateway_start_failed")
            deadline = time.monotonic() + 5
            while not self.server.started and time.monotonic() < deadline and self.thread.is_alive():
                time.sleep(0.01)
            if not self.server.started:
                self.server.should_exit = True
                sock.close()
                raise RuntimeError("gateway_start_failed")

    def bind(self, job_id, target):
        environment = target.deployment.environment if target.deployment else {}
        names = [environment.get(k) for k in ("AGENT_REVIEW_TARGET_API_URL", "AGENT_REVIEW_TARGET_MODEL", "AGENT_REVIEW_TARGET_TOKEN")]
        if not all(names) or not all(os.environ.get(n) for n in names):
            raise ValueError("网关需要 Python 自动适配的完整目标模型变量映射。")
        url, model, token = (os.environ[n] for n in names)
        parsed = urlsplit(url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or any(c.isspace() for c in url)):
            raise ValueError("网关上游地址无效。")
        self.start()
        advertise = os.environ.get("AGENT_REVIEW_GATEWAY_ADVERTISE_HOST", "host.docker.internal")
        if not advertise or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-" for c in advertise):
            raise ValueError("网关容器访问地址需要主机名。")
        gateway_url = f"http://{advertise}:{self.port}/v1"
        overrides = {"AGENT_REVIEW_GATEWAY_MODE": "true"}
        for key, host_name in environment.items():
            value = os.environ.get(host_name)
            if value == token:
                overrides[key] = "gateway-turn-credential-required"
            elif host_name == names[0]:
                overrides[key] = gateway_url
        overrides.update(AGENT_REVIEW_TARGET_API_URL=gateway_url,
                         AGENT_REVIEW_TARGET_TOKEN="gateway-turn-credential-required")
        binding = GatewayBinding(self, job_id, url.rstrip("/").removesuffix("/chat/completions").removesuffix("/responses"),
                                 model, token, overrides)
        self.bindings[job_id] = binding
        return binding

    async def settle(self, lease):
        pending = [task for task, owner in self.tasks.items() if owner == lease]
        if pending:
            _, unfinished = await asyncio.wait(pending, timeout=self.drain_seconds)
            for task in unfinished:
                task.cancel()
            await asyncio.gather(*unfinished, return_exceptions=True)

    async def proxy(self, request: Request):
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer ") or len(authorization) > 200:
            raise HTTPException(401, "gateway_credential_required")
        credential = authorization[7:]
        lease = self.db.resolve(credential)
        binding = self.bindings.get(lease["job_id"])
        if binding is None:
            raise HTTPException(401, "gateway_job_inactive")
        if request.url.query:
            raise HTTPException(400, "gateway_query_not_supported")
        content = bytearray()
        try:
            async with asyncio.timeout(max(0.05, lease["expires"] - time.time())):
                async for chunk in request.stream():
                    content.extend(chunk)
                    if len(content) > 1024 * 1024:
                        raise HTTPException(413, "gateway_request_too_large")
        except TimeoutError:
            raise HTTPException(408, "gateway_request_expired") from None
        try:
            body = json.loads(content)
            if not isinstance(body, dict) or body.get("model") != binding.model:
                raise ValueError()
            canonical(body)
        except (ValueError, RecursionError):
            raise HTTPException(400, "gateway_model_or_json_invalid") from None
        recorder = ModelRecorder(session_id=lease["context"]["session_id"], turn=lease["context"]["turn"],
                                 model=binding.model, context=lease["context"])
        capture = Capture(recorder, binding.model, "model_gateway")
        event = capture.event
        event["id"] = "gateway_" + uuid.uuid4().hex
        event["start_ms"] = time.time() * 1000
        event["provenance"] = "host_observed"
        event["context"].update(call_id=event["id"], gateway_request_id=event["id"], gateway_state="running")
        maximum = self.db.reserve(lease, event)
        endpoint = request.url.path.removeprefix("/v1")
        if maximum is not None:
            parameter = next((key for key in ("max_completion_tokens", "max_output_tokens", "max_tokens") if key in body),
                             "max_output_tokens" if endpoint == "/responses" else "max_tokens")
            value = body.get(parameter)
            body[parameter] = min(value, maximum) if type(value) is int and value > 0 else maximum
        headers_ready = asyncio.get_running_loop().create_future()
        queue, disconnected = asyncio.Queue(maxsize=16), asyncio.Event()

        async def emit(chunk):
            if disconnected.is_set():
                return
            put, closed = asyncio.create_task(queue.put(chunk)), asyncio.create_task(disconnected.wait())
            try:
                await asyncio.wait((put, closed), return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in (put, closed):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(put, closed, return_exceptions=True)

        async def produce():
            observer = None
            last = None
            def checkpoint(state="running"):
                nonlocal last
                from .assessment_contracts import validate_reported_usage
                from .contracts import Usage
                from .target_client import scrub
                if event.get("usage"):
                    try:
                        validate_reported_usage(Usage.model_validate(event["usage"]))
                    except ValueError:
                        event.pop("usage", None)
                        capture.invalid = True
                        event["context"].update(usage_complete=False, usage_status="unknown",
                                                usage_missing_reason="invalid_provider_usage")
                safe = scrub(event, [binding.token, credential])
                if canonical(safe) != last or state != "running":
                    self.db.save(safe, state=state)
                    last = canonical(safe)
            try:
                # Deadline applies to connection, stream, and backpressure together.
                async with asyncio.timeout(max(0.05, lease["expires"] - time.time()) + 1):
                    async with httpx.AsyncClient(transport=self.transport, trust_env=False, follow_redirects=False,
                                                  timeout=None) as client:
                        async with client.stream("POST", binding.api_url + endpoint, json=body,
                            headers={"Authorization": "Bearer " + binding.token, "Accept-Encoding": "identity"}) as upstream:
                            observer = BodyObserver(capture, upstream)
                            event["context"]["http_status"] = upstream.status_code
                            checkpoint()
                            headers = {"content-type": upstream.headers.get("content-type", "application/json"),
                                       REQUEST_HEADER: event["id"]}
                            if "x-request-id" in upstream.headers:
                                from .target_client import scrub
                                headers["x-request-id"] = scrub(upstream.headers["x-request-id"], [binding.token, credential])
                            headers_ready.set_result((upstream.status_code, headers))
                            size = 0
                            buffered = []
                            async for chunk in upstream.aiter_bytes():
                                size += len(chunk)
                                if size > 16 * 1024 * 1024:
                                    capture.invalid = True
                                    raise ValueError("gateway_response_limit")
                                observer.feed(chunk)
                                checkpoint()  # Commit usage before forwarding the associated chunk.
                                if capture.streaming:
                                    await emit(chunk)
                                else:
                                    buffered.append(chunk)
                            observer.finish()
                            checkpoint()
                            for chunk in buffered:
                                await emit(chunk)
            except BaseException:
                if observer:
                    observer.finish(error=True)
                else:
                    capture.finish(error=True)
                if not headers_ready.done():
                    headers_ready.set_result((502, {"content-type": "application/json", REQUEST_HEADER: event["id"]}))
                    await emit(b'{"error":"gateway_upstream_failed"}')
            finally:
                event["end_ms"] = max(event["start_ms"], time.time() * 1000)
                event["context"]["gateway_state"] = "finished"
                checkpoint("finished")
                try:
                    if asyncio.current_task().cancelling():
                        raise TimeoutError()
                    await asyncio.wait_for(emit(None), timeout=1)
                except TimeoutError:
                    disconnected.set()
                    while queue.full():
                        queue.get_nowait()
                    queue.put_nowait(None)

        task = asyncio.create_task(produce())
        self.tasks[task] = lease["id"]
        task.add_done_callback(lambda done: self.tasks.pop(done, None))
        try:
            status, headers = await headers_ready
        except BaseException:
            disconnected.set()
            raise

        async def consume():
            try:
                while True:
                    chunk = await queue.get()
                    if chunk is None:
                        break
                    yield chunk
            finally:
                disconnected.set()
        return StreamingResponse(consume(), status_code=status, headers=headers)

    def release(self, binding):
        self.bindings.pop(binding.job_id, None)

    def close(self):
        if self.server:
            self.server.should_exit = True
            self.thread.join(timeout=5)
            self.socket.close()
        self.bindings.clear()


def gateway_trace_events(target_trace, gateway, *, job_id, case_id, session_id, turn, budget_id, attempt):
    """Gateway calls are canonical; target originals remain in turn artifacts."""
    from .target_telemetry import trace_events
    calls = gateway["events"]
    ids = {e["id"] for e in calls}
    mapping = {e["id"]: e.get("context", {}).get("gateway_request_id") for e in (target_trace or {}).get("events", [])
               if e["kind"] == "llm"}
    tools = trace_events(target_trace, job_id=job_id, case_id=case_id, session_id=session_id,
                         turn=turn, budget_id=budget_id, attempt=attempt)
    tools = [e for e in tools if e["kind"] == "tool"]
    for event in tools:
        parent = event.get("parent_id")
        matched = mapping.get(parent.removeprefix(f"target_{turn}_")) if parent else None
        event["parent_id"] = matched if matched in ids else None
    return [*calls, *tools]
