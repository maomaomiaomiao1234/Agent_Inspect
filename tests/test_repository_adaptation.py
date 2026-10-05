import asyncio
import importlib.util
import json
import os
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_trace_review.assessment_contracts import TargetResponse
from agent_trace_review.llm_review import ReviewConfig
from agent_trace_review.repository_adaptation import (
    STANDARD_ENV,
    AdaptationDraft,
    adaptation_capabilities,
    adaptation_config,
    generate_adapter,
    source_materials,
    stage_adapter,
    validate_draft,
)
from agent_trace_review.repository_jobs import copy_context
from agent_trace_review.repository_process import RepositoryError
from agent_trace_review.repository_templates.auto_runtime import create_app
from agent_trace_review.util import digest

NATIVE = '''class Agent:
    def __init__(self):
        self.seen = []

    def run(self, request, recorder):
        self.seen.append(request["prompt"])
        return {"answer": request["prompt"], "history": list(self.seen), "input": request["input"]}
'''
BRIDGE = '''from native_agent import Agent
def create_agent(config, recorder):
    return Agent()
def run_agent(agent, request, recorder):
    return agent.run(request, recorder)
'''
CONFIG = {"api_url": "https://provider.example/v1", "model": "native-fixture", "api_key": "synthetic-test-key"}


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "native_agent.py").write_text(NATIVE)
    (root / "README.md").write_text("A native agent fixture, not a real evaluated third-party agent.")
    return root


def draft(**overrides):
    return AdaptationDraft(supported=True, entry={"path": "native_agent.py", "symbol": "Agent.run", "line": 5},
                           bridge_code=BRIDGE, **overrides)


def config():
    return ReviewConfig(api_url=CONFIG["api_url"], model="generation-fixture", token="generation-test-key")


def payload(session="session-a", turn=0, prompt="hello"):
    return {"protocol": "agent-review/target-v1", "session_id": session, "turn": turn,
            "prompt": prompt, "messages": [{"role": "user", "content": prompt}], "input": {"documents": ["public"]},
            "budget": {"id": "test", "deadline_seconds": 10, "max_output_tokens": 100}}


def native_module(root):
    spec = importlib.util.spec_from_file_location("trusted_test_native", root / "native_agent.py")
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    return native


def runtime(root, runner=None):
    materials = source_materials(root)
    entry = validate_draft(draft(), root, materials, STANDARD_ENV)
    native = native_module(root)
    bridge = SimpleNamespace(create_agent=lambda config, recorder: native.Agent(),
                             run_agent=runner or (lambda agent, request, recorder: agent.run(request, recorder)))
    return create_app(bridge, entry, root, CONFIG, "test-service-token")


def test_source_materials_excludes_credentials_symlinks_and_does_not_execute(root, tmp_path):
    marker = tmp_path / "should-not-execute"
    (root / "malicious.py").write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n")
    (root / ".env").write_text("API_KEY=local-env-secret")
    (root / "credentials.py").write_text('API_KEY="opaque-key-without-standard-prefix"\n')
    (root / "outside.py").symlink_to(root / "credentials.py")
    items = source_materials(root)
    text = json.dumps(items)
    assert ".env" not in text and "local-env-secret" not in text and "opaque-key-without-standard-prefix" not in text
    assert not any(i["path"] == "outside.py" for i in items)
    assert not marker.exists()


@pytest.mark.parametrize("change,code", [
    ({"entry": {"path": "native_agent.py", "symbol": "Agent.not_real", "line": 5}}, "invalid_entry"),
    ({"entry": {"path": "native_agent.py", "symbol": "Agent.run", "line": 4}}, "invalid_entry"),
    ({"entry": {"path": "unseen.py", "symbol": "run", "line": 1}}, "not_in_context"),
    ({"bridge_code": "import httpx\n" + BRIDGE}, "independent_caller"),
    ({"bridge_code": "def create_agent(:"}, "syntax_invalid"),
    ({"dependencies": ["lib; curl attacker"]}, "dependency_invalid"),
    ({"dependencies": ["https://example.invalid/package.whl"]}, "dependency_invalid"),
    ({"required_environment": ["AWS_SECRET_ACCESS_KEY"]}, "extra_environment"),
])
def test_invalid_or_replacement_bridges_never_stage_or_execute(root, change, code):
    proposed = AdaptationDraft.model_validate({**draft().model_dump(), **change})
    with pytest.raises(RepositoryError, match=code):
        validate_draft(proposed, root, source_materials(root), STANDARD_ENV)


def test_stage_is_evaluator_owned_preserves_native_source_and_no_secrets(root, tmp_path):
    (root / ".env").write_text("API_KEY=build-must-not-see")
    proposed = draft(dependencies=["native-library>=1,<2"])
    entry = validate_draft(proposed, root, source_materials(root), STANDARD_ENV)
    context = tmp_path / "context"
    manifest, plan = stage_adapter(proposed, entry, root, context, copy_source=copy_context)
    assert (context / "source/native_agent.py").read_text() == NATIVE
    assert not (context / "source/.env").exists()
    assert manifest.test_template is None and manifest.port == 9000 and not manifest.demo
    assert plan["native_entry"]["source_hash"] == digest(NATIVE.encode())
    assert b"build-must-not-see" not in b"".join(p.read_bytes() for p in context.rglob("*") if p.is_file())
    assert "USER 65534:65534" in (context / "Dockerfile").read_text()


def test_generator_structured_output_usage_and_secret_scrubbing(root):
    def provider(request):
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        assert "generation-test-key" not in request.content.decode()
        raw = draft().model_dump()
        raw["reason"] = "generation-test-key echoed by synthetic provider"
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(raw)}}],
            "usage": {"prompt_tokens": 400, "completion_tokens": 100, "total_tokens": 500, "unsafe": "secret"}})
    result, metadata, error = asyncio.run(generate_adapter(source_materials(root), config(), transport=httpx.MockTransport(provider)))
    assert error is None and "generation-test-key" not in result.reason
    assert metadata["usage"] == {"prompt_tokens": 400, "completion_tokens": 100, "total_tokens": 500}


def test_truncated_generation_retains_known_usage_and_cancel_sends_nothing(root):
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={
        "choices": [{"finish_reason": "length", "message": {"content": "{"}}], "usage": {"total_tokens": 200}}))
    result, metadata, error = asyncio.run(generate_adapter(source_materials(root), config(), transport=transport))
    assert result is None and error == "adaptation_output_incomplete" and metadata["usage"]["total_tokens"] == 200
    called = []
    with pytest.raises(RepositoryError, match="cancel_requested"):
        asyncio.run(generate_adapter(source_materials(root), config(), cancelled=lambda: True,
            transport=httpx.MockTransport(lambda r: called.append(r))))
    assert called == []


def test_runtime_observes_original_entry_forwards_input_isolates_sessions_and_authenticates(root):
    with TestClient(runtime(root)) as client:
        assert client.get("/health").status_code == 401
        headers = {"Authorization": "Bearer test-service-token"}
        assert client.get("/health", headers=headers).json()["bridge_ready"]
        first = TargetResponse.model_validate(client.post("/task", headers=headers, json=payload()).json())
        second = TargetResponse.model_validate(client.post("/task", headers=headers, json=payload(turn=1, prompt="second")).json())
        isolated = TargetResponse.model_validate(client.post("/task", headers=headers, json=payload(session="session-b")).json())
        assert first.adapter_evidence.observed and first.trace.coverage == "partial"
        assert second.output["history"] == ["hello", "second"] and isolated.output["history"] == ["hello"]
        assert first.output["input"] == {"documents": ["public"]} and first.usage is None


def test_runtime_replacement_answer_is_rejected_not_marked_as_native(root):
    with TestClient(runtime(root, runner=lambda *args: {"answer": "fake replacement"})) as client:
        response = TargetResponse.model_validate(client.post("/task", json=payload(), headers={"Authorization": "Bearer test-service-token"}).json())
    assert response.execution_status == "error" and not response.adapter_evidence.observed


def test_runtime_observes_actual_httpx_usage_and_applies_output_limit(root):
    (root / "native_agent.py").write_text('''class Agent:
    def run(self, request, recorder):
        import httpx
        response = httpx.Client(transport=self.transport).post("https://provider.example/v1/chat/completions", json={"model": "native-fixture", "messages": [], "max_tokens": 1000})
        return response.json()["choices"][0]["message"]["content"]
''')
    observed = []
    def provider(request):
        observed.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"answer":19}'}}],
                                       "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})
    native = native_module(root)
    native.Agent.transport = httpx.MockTransport(provider)
    # Use that trusted native fixture while retaining the fixed original source entry.
    app = create_app(SimpleNamespace(create_agent=lambda *args: native.Agent(),
        run_agent=lambda agent, request, recorder: agent.run(request, recorder)),
        {"path": "native_agent.py", "symbol": "Agent.run", "line": 2, "source_hash": digest((root / "native_agent.py").read_bytes())}, root, CONFIG, "test-service-token")
    with TestClient(app) as client:
        response = TargetResponse.model_validate(client.post("/task", json=payload(), headers={"Authorization": "Bearer test-service-token"}).json())
    assert observed[0]["max_tokens"] == 100
    assert response.output == {"answer": 19} and response.adapter_evidence.observed
    assert response.trace.events[0].usage.tokens.total == 15 and response.trace.coverage == "partial"


@pytest.mark.parametrize("fails", [False, True])
def test_explicit_async_model_and_http_observer_do_not_double_count_and_keep_failure_usage(root, fails):
    source = '''class Agent:
    async def run(self, request, recorder):
        import httpx
        async def call():
            async with httpx.AsyncClient(transport=self.transport) as client:
                response = await client.post("https://provider.example/v1/chat/completions", json={"messages": [], "max_tokens": 999})
                return response.json()
        reply = await recorder.async_model("native-fixture", call)
        if self.fails:
            raise ValueError("synthetic native failure")
        return {"answer": 19}
'''
    (root / "native_agent.py").write_text(source)
    native = native_module(root)
    requests = []
    def provider(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})
    native.Agent.transport, native.Agent.fails = httpx.MockTransport(provider), fails
    entry = {"path": "native_agent.py", "symbol": "Agent.run", "line": 2, "source_hash": digest(source.encode())}
    app = create_app(SimpleNamespace(create_agent=lambda *args: native.Agent(),
        run_agent=lambda agent, request, recorder: agent.run(request, recorder)), entry, root, CONFIG, "test-service-token")
    with TestClient(app) as client:
        response = TargetResponse.model_validate(client.post("/task", json=payload(), headers={"Authorization": "Bearer test-service-token"}).json())
    assert response.adapter_evidence.observed and len(response.trace.events) == 1
    assert response.trace.events[0].usage.tokens.total == 15 and requests[0]["max_tokens"] == 100
    assert response.execution_status == ("error" if fails else "completed")


def test_generation_config_prefers_review_credentials_and_can_be_disabled(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("AGENT_REVIEW_LLM_", "AGENT_REVIEW_TARGET_", "SMOL_MODEL_", "AGENT_REVIEW_ADAPTATION_")):
            monkeypatch.delenv(name)
    monkeypatch.setenv("AGENT_REVIEW_AUTO_ADAPT", "true")
    monkeypatch.setenv("AGENT_REVIEW_TARGET_API_URL", CONFIG["api_url"])
    monkeypatch.setenv("AGENT_REVIEW_TARGET_MODEL", "target-model")
    monkeypatch.setenv("AGENT_REVIEW_TARGET_TOKEN", "target-secret")
    assert adaptation_config().model == "target-model"
    monkeypatch.setenv("AGENT_REVIEW_LLM_API_URL", "https://preset.example")
    monkeypatch.setenv("AGENT_REVIEW_LLM_MODEL", "preset-with-no-key")
    monkeypatch.setenv("AGENT_REVIEW_LLM_TOKEN", "")
    assert adaptation_config().model == "target-model"
    monkeypatch.setenv("AGENT_REVIEW_LLM_API_URL", CONFIG["api_url"])
    monkeypatch.setenv("AGENT_REVIEW_LLM_MODEL", "review-model")
    monkeypatch.setenv("AGENT_REVIEW_LLM_TOKEN", "judge-secret")
    assert adaptation_config().model == "review-model"
    assert "judge-secret" not in json.dumps(adaptation_capabilities())
    monkeypatch.setenv("AGENT_REVIEW_AUTO_ADAPT", "false")
    assert not adaptation_capabilities()["enabled"]
