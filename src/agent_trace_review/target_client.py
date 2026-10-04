"""Bounded HTTP calls and an optional immutable-image deployment managed by the judge."""

import asyncio
import json
import os
import subprocess
import tempfile
import time
import uuid
from contextlib import contextmanager

import httpx

from .assessment_contracts import TargetDefinition, TargetResponse
from .util import canonical, redact

MAX_REQUEST = 128 * 1024
MAX_RESPONSE = 256 * 1024


class TargetError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def scrub(value, secrets=()):
    value = redact(value)
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[redacted:credential]")
        return value
    if isinstance(value, dict):
        return {scrub(k, secrets): scrub(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v, secrets) for v in value]
    return value


class TargetClient:
    def __init__(self, target: TargetDefinition, endpoint: str | None = None, transport=None, token_override=None):
        self.target = target
        self.endpoint = (endpoint or target.endpoint or "").rstrip("/")
        self.transport = transport
        self.token = token_override if token_override is not None else os.environ.get(target.token_env, "") if target.token_env else ""
        if target.token_env and not self.token:
            raise TargetError("missing_target_credential")
        self.secrets = [self.token]
        if target.deployment:
            self.secrets += [os.environ.get(v, "") for v in target.deployment.environment.values()]

    async def _exchange(self, method, path, payload, timeout):
        async def stream():
            headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
            if self.token:
                headers["Authorization"] = "Bearer " + self.token
            async with httpx.AsyncClient(
                trust_env=False,
                follow_redirects=False,
                timeout=timeout,
                transport=self.transport,
            ) as client:
                async with client.stream(
                    method, self.endpoint + path, json=payload, headers=headers
                ) as response:
                    if not 200 <= response.status_code < 300:
                        raise TargetError(f"http_{response.status_code}")
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise TargetError("unsupported_response_encoding")
                    body = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE:
                            raise TargetError("response_too_large")
                    try:
                        value = json.loads(body)
                        canonical(value)
                        return value
                    except (ValueError, RecursionError):
                        raise TargetError("invalid_json") from None

        try:
            # Bounds connect + response + streaming together, including slow-drip responses.
            return await asyncio.wait_for(stream(), timeout)
        except (TimeoutError, httpx.TimeoutException):
            raise TargetError("timeout") from None
        except httpx.HTTPError:
            raise TargetError("connection_error") from None

    def call(self, payload: dict, timeout: float) -> TargetResponse:
        if len(canonical(payload).encode()) > MAX_REQUEST:
            raise TargetError("request_too_large")
        raw = asyncio.run(self._exchange("POST", self.target.task_path, payload, timeout))
        try:
            response = TargetResponse.model_validate(scrub(raw, self.secrets))
            if response.trace and (
                response.trace.session_id != payload["session_id"] or response.trace.turn != payload["turn"]
            ):
                raise ValueError("Target trace does not match the requested session/turn")
            return response
        except ValueError:
            raise TargetError("invalid_target_contract") from None

    def health(self, timeout=5) -> dict:
        if not self.target.health_path:
            return {"status": "not_configured"}
        raw = asyncio.run(self._exchange("GET", self.target.health_path, None, timeout))
        if (
            not isinstance(raw, dict)
            or raw.get(self.target.health_status_field) != self.target.health_status_value
        ):
            raise TargetError("health_not_ready")
        # Health JSON is an observation; a target's claimed commit is not attestation.
        return {"status": "ready", "response": scrub(raw, self.secrets)}


def _docker(*args: str) -> str:
    try:
        result = subprocess.run(["docker", *args], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        raise TargetError("docker_unavailable_or_timeout") from None
    if result.returncode:
        raise TargetError("docker_command_failed")
    return result.stdout.decode(errors="replace").strip()


def remove_container(name, owner=None):
    if owner:
        # Never remove an unrelated container whose name happens to collide.
        all_matches = _docker("ps", "-aq", "--filter", f"name=^/{name}$")
        if not all_matches:
            return
        owned = _docker("ps", "-aq", "--filter", f"name=^/{name}$", "--filter", f"label=agent-inspect.owner={owner}")
        if owned != all_matches:
            raise TargetError("container_owner_mismatch:" + name)
    try:
        _docker("rm", "-f", name)
    except TargetError:
        try:
            remaining = _docker("ps", "-aq", "--filter", f"name=^/{name}$")
        except TargetError:
            raise TargetError("container_cleanup_failed:" + name) from None
        if remaining:
            raise TargetError("container_cleanup_failed:" + name) from None


@contextmanager
def deployed_target(target: TargetDefinition, *, resources=None, job_id=None, cancelled=lambda: False, service_token=None):
    """Never builds/pulls images, mounts the host, or falls back to host execution."""
    if not target.deployment:
        yield (
            target.endpoint,
            {"mode": "existing_service", "status": "not_managed", "source_binding": "unverified"},
        )
        return
    config = target.deployment
    image = json.loads(_docker("image", "inspect", config.image))[0]
    if image["Id"] != config.image or image.get("Config", {}).get("Volumes"):
        raise TargetError("image_identity_or_volumes_invalid")
    if cancelled():
        raise TargetError("cancel_requested")
    name = resources.reserve_container(job_id) if resources else "agent-inspect-target-" + uuid.uuid4().hex
    env_file = None
    env_secrets = [os.environ.get(v, "") for v in config.environment.values()] + [service_token or ""]
    started = time.monotonic()
    try:
        args = [
            "run",
            "-d",
            "--pull=never",
            "--name",
            name,
            "--read-only",
            "--user",
            config.user,
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--cpus",
            str(config.cpus),
            "--memory",
            f"{config.memory_mb}m",
            "--pids-limit=128",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=64m",
            "--log-driver=none",
            "--publish",
            f"127.0.0.1::{config.port}",
        ]
        if resources:
            args += ["--label", f"agent-inspect.owner={resources.resource_owner}", "--label", f"agent-inspect.job={job_id}"]
        if config.environment or config.service_token_variable:
            fd, env_file = tempfile.mkstemp(prefix="agent-inspect-env-")
            with os.fdopen(fd, "w") as output:
                if resources:
                    resources.resource_env(name, env_file)
                for name_in_container, host_name in config.environment.items():
                    value = os.environ.get(host_name)
                    if value is None or "\n" in value or "\r" in value:
                        raise TargetError("invalid_deployment_environment")
                    output.write(f"{name_in_container}={value}\n")
                if config.service_token_variable:
                    if not service_token:
                        raise TargetError("missing_generated_service_token")
                    output.write(f"{config.service_token_variable}={service_token}\n")
            args += ["--env-file", env_file]
        _docker(*args, config.image)
        if env_file:
            os.unlink(env_file)
            env_file = None
        state = json.loads(_docker("inspect", name))[0]
        bindings = state["NetworkSettings"]["Ports"][f"{config.port}/tcp"]
        port = next(b["HostPort"] for b in bindings if b["HostIp"] == "127.0.0.1")
        endpoint = "http://127.0.0.1:" + port
        client = TargetClient(target, endpoint, token_override=service_token)
        health = None
        while time.monotonic() - started < 30:
            if cancelled():
                raise TargetError("cancel_requested")
            try:
                health = client.health(timeout=min(2, max(0.05, 30 - (time.monotonic() - started))))
                break
            except TargetError:
                time.sleep(0.2)
        if health is None:
            raise TargetError("deployment_not_ready")
        yield (
            endpoint,
            {
                "mode": "docker",
                "status": "ready",
                "image_id": config.image,
                "container_name": name,
                "startup_seconds": round(time.monotonic() - started, 3),
                "health": health,
                "policy": {
                    "read_only": True,
                    "user": config.user,
                    "memory_mb": config.memory_mb,
                    "cpus": config.cpus,
                    "network": "bridge_with_outbound",
                    "host_mounts": False,
                },
                "source_binding": "unverified",
                "build_labels": scrub(image.get("Config", {}).get("Labels") or {}, env_secrets),
            },
        )
    finally:
        if env_file and os.path.exists(env_file):
            os.unlink(env_file)
        if resources:
            resources.cleanup_resource(name)
        else:
            remove_container(name)
