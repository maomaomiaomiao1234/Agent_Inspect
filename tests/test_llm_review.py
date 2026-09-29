import asyncio
import copy
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from agent_trace_review.api import create_app
from agent_trace_review.cli import app
from agent_trace_review.llm_review import ReviewConfig, ReviewError, configuration, resolve_config, review_run
from agent_trace_review.reports import markdown_report
from agent_trace_review.service import ingest
from agent_trace_review.util import canonical


@pytest.fixture
def case(store):
    trace = {
        "trace_version": "1",
        "framework": "input-output",
        "run_id": "case-1",
        "demo": True,
        "task_prompt": "根据政策回答退款问题",
        "output": "请提供订单号，以便核实是否符合退款条件。",
        "artifacts": {"reference": "退款前必须核实订单号。"},
    }
    run, original, _ = ingest(store, canonical(trace).encode())
    profile = {
        "profile_version": "1",
        "id": "answer",
        "rules": [
            {"id": "present", "op": "exists", "path": "/output"},
            {
                "id": "quality",
                "op": "external",
                "description": "回答符合政策为通过，违背政策失败，无法核实未知。",
            },
        ],
    }
    return run, original, profile


@pytest.fixture
def config():
    return ReviewConfig(api_url="https://provider.example/v1", model="review-model", token="test-secret-123")


def result(status="pass", paths=None):
    return {
        "results": [
            {
                "id": "quality",
                "status": status,
                "explanation": "答案要求核实订单号。",
                "evidence_paths": paths if paths is not None else ["/output", "/artifacts/reference"],
            }
        ]
    }


def response(value=None, **overrides):
    payload = {
        "choices": [{"finish_reason": "stop", "message": {"content": canonical(value or result())}}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160},
    }
    return httpx.Response(200, json=payload | overrides)


def test_input_output_review_wire_format_cache_and_provenance(store, case, config):
    run, original, profile = case
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url == "https://provider.example/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-secret-123"
        body = json.loads(request.content)
        assert body["model"] == "review-model" and body["max_tokens"] == 4096
        assert body["response_format"] == {"type": "json_object"}
        assert "test-secret-123" not in canonical(body)
        assert "tools" not in body
        data = json.loads(body["messages"][1]["content"])
        assert data["context"]["events"] == [] and data["context"]["trace_complete"] is False
        assert data["context"]["task_prompt"] == run.task_prompt
        return response()

    transport = httpx.MockTransport(handler)
    reviewed = review_run(store, run, profile, config, transport=transport)
    assert reviewed.outcome == "pass" and reviewed.id.startswith("llm_")
    assert reviewed.custom["checks"][1]["origin"] == "llm_judge"
    assert reviewed.findings[-1].verdict == "hypothesis"
    assert reviewed.judge["usage"]["total_tokens"] == 160
    assert reviewed.judge["cost_usd"] is None
    assert next(m for m in reviewed.metrics if m.key == "tokens_total").value is None
    assert "大模型评审" in markdown_report(run, reviewed)
    assert review_run(store, run, profile, config, transport=transport).id == reviewed.id
    assert len(calls) == 1 and len(store.revisions(run.id)) == 2
    assert store.get_evaluation(run.id, original.id).outcome == "inconclusive"
    assert "test-secret-123" not in canonical(reviewed.model_dump())
    for artifact in store.artifacts.iterdir():
        assert b"test-secret-123" not in artifact.read_bytes()


@pytest.mark.parametrize("status,expected", [("unknown", "inconclusive"), ("fail", "fail")])
def test_model_unknown_or_fail_is_preserved(store, case, config, status, expected):
    run, _, profile = case
    reviewed = review_run(
        store, run, profile, config, transport=httpx.MockTransport(lambda r: response(result(status)))
    )
    assert reviewed.outcome == expected


def test_local_failure_not_overridden_and_cache_changes_with_profile(store, case, config):
    run, _, profile = case
    calls = []

    def handler(request):
        calls.append(request)
        return response()

    transport = httpx.MockTransport(handler)
    first = review_run(store, run, profile, config, transport=transport)
    profile["rules"].append({"id": "exact", "op": "equals", "path": "/output", "value": "different"})
    second = review_run(store, run, profile, config, transport=transport)
    assert first.id != second.id and second.outcome == "fail" and len(calls) == 2


@pytest.mark.parametrize(
    "bad",
    [
        {"results": []},
        {"results": [result()["results"][0], result()["results"][0]]},
        {"results": [result()["results"][0] | {"id": "invented"}]},
        result(paths=["/missing"]),
        result(paths=[]),
        {"results": [result()["results"][0] | {"status": "excellent"}]},
    ],
)
def test_invalid_model_result_preserves_original(store, case, config, bad):
    run, original, profile = case
    with pytest.raises(ReviewError):
        review_run(store, run, profile, config, transport=httpx.MockTransport(lambda r: response(bad)))
    assert store.get_evaluation(run.id).id == original.id and len(store.revisions(run.id)) == 1


@pytest.mark.parametrize(
    "kind", ["401", "429", "302", "invalid", "length", "refusal", "tool", "timeout", "large"]
)
def test_provider_failure_never_echoes_token_or_saves(store, case, config, kind):
    run, original, profile = case
    calls = []

    def handler(request):
        calls.append(request)
        if kind.isdigit():
            return httpx.Response(
                int(kind), text="test-secret-123", headers={"location": "https://other.example"}
            )
        if kind == "timeout":
            raise httpx.ReadTimeout("test-secret-123")
        if kind == "large":
            return httpx.Response(200, content=b"x" * (1024 * 1024 + 1))
        if kind == "invalid":
            return httpx.Response(200, json={"choices": [{"message": {"content": "test-secret-123"}}]})
        choice = {"finish_reason": "stop", "message": {"content": canonical(result())}}
        if kind == "length":
            choice["finish_reason"] = "length"
        else:
            choice["message"]["refusal" if kind == "refusal" else "tool_calls"] = "test-secret-123"
        return response(choices=[choice])

    with pytest.raises(ReviewError) as error:
        review_run(store, run, profile, config, transport=httpx.MockTransport(handler))
    assert "test-secret-123" not in str(error.value)
    assert len(calls) == 1 and store.get_evaluation(run.id).id == original.id


def test_deadline_and_input_limit(store, case, config):
    run, original, profile = case
    calls = []

    async def slow(request):
        calls.append(request)
        await asyncio.sleep(1)
        return response()

    with pytest.raises(ReviewError, match="超时"):
        review_run(
            store,
            run,
            profile,
            config.model_copy(update={"timeout_seconds": 0.01}),
            transport=httpx.MockTransport(slow),
        )
    with pytest.raises(ReviewError, match="字符上限"):
        review_run(
            store,
            run,
            profile,
            config.model_copy(update={"max_input_chars": 1000}),
            transport=httpx.MockTransport(slow),
        )
    assert len(calls) == 1 and store.get_evaluation(run.id).id == original.id


def test_compatibility_options_and_credential_echo(store, case, config):
    run, _, profile = case
    value = result()
    value["results"][0]["explanation"] = "test-secret-123"

    def handler(request):
        body = json.loads(request.content)
        assert body["max_completion_tokens"] == 4096
        assert "max_tokens" not in body and "response_format" not in body
        return response(value)

    reviewed = review_run(
        store,
        run,
        profile,
        config.model_copy(
            update={
                "api_url": config.endpoint,
                "token_parameter": "max_completion_tokens",
                "json_mode": False,
            }
        ),
        transport=httpx.MockTransport(handler),
    )
    assert "test-secret-123" not in canonical(reviewed.model_dump())


def test_config_does_not_leak_or_redirect_server_token(monkeypatch):
    monkeypatch.setenv("AGENT_REVIEW_LLM_API_URL", "https://original.example/v1")
    monkeypatch.setenv("AGENT_REVIEW_LLM_MODEL", "model")
    monkeypatch.setenv("AGENT_REVIEW_LLM_TOKEN", "test-secret-123")
    assert configuration()["enabled"]
    assert "test-secret-123" not in canonical(configuration())
    with pytest.raises(ReviewError):
        resolve_config(api_url="https://different.example/v1")
    assert resolve_config(api_url="https://different.example/v1", token="new-token").model == "model"
    for url in (
        "https://user:secret@example.com/v1",
        "https://example.com/v1?token=secret",
        "http://remote.example/v1",
    ):
        with pytest.raises(ReviewError):
            resolve_config(api_url=url, token="new-token")


def test_http_endpoint_and_validation_errors_do_not_echo_secrets(tmp_path, monkeypatch):
    import agent_trace_review.api as api_module

    client = TestClient(create_app(str(tmp_path / "api")))
    headers = {"X-Review-Request": "1"}
    trace = {"trace_version": "1", "framework": "io", "run_id": "1", "output": "hello"}
    run_id = client.post(
        "/api/imports", headers=headers, files={"file": ("trace.json", canonical(trace))}
    ).json()["run_id"]
    profile = {
        "profile_version": "1",
        "id": "quality",
        "rules": [{"id": "quality", "op": "external", "description": "Check output"}],
    }
    body = {
        "send_data": True,
        "profile": profile,
        "api_url": "https://provider.example/v1",
        "model": "model",
        "token": "test-secret-123",
    }
    monkeypatch.setattr(
        api_module,
        "review_run",
        lambda store, run, profile, config: review_run(
            store,
            run,
            profile,
            config,
            transport=httpx.MockTransport(lambda r: response(result(paths=["/output"]))),
        ),
    )
    assert client.post(f"/api/runs/{run_id}/llm-review", json=body).status_code == 403
    assert (
        client.post(
            f"/api/runs/{run_id}/llm-review", headers=headers, json=body | {"send_data": False}
        ).status_code
        == 422
    )
    good = client.post(f"/api/runs/{run_id}/llm-review", headers=headers, json=body)
    assert good.status_code == 200, good.text
    assert good.json()["outcome"] == "pass" and "test-secret-123" not in good.text
    for bad in (
        body | {"profile": {}},
        body | {"max_output_tokens": "bad"},
        body | {"token": {"secret": "test-secret-123"}},
    ):
        resp = client.post(f"/api/runs/{run_id}/llm-review", headers=headers, json=bad)
        assert resp.status_code == 422 and "test-secret-123" not in resp.text


def test_cli_uses_environment_token(store, case, monkeypatch, tmp_path):
    import agent_trace_review.cli as cli_module

    run, _, profile = case
    path = tmp_path / "profile.json"
    path.write_text(canonical(profile))
    monkeypatch.setenv("MY_REVIEW_TOKEN", "test-secret-123")
    monkeypatch.setattr(
        cli_module,
        "review_run",
        lambda store, run, profile, config: review_run(
            store, run, profile, config, transport=httpx.MockTransport(lambda r: response())
        ),
    )
    outcome = CliRunner().invoke(
        app,
        [
            "llm-review",
            run.id,
            "--profile",
            str(path),
            "--data-dir",
            str(store.root),
            "--api-url",
            "https://provider.example/v1",
            "--model",
            "model",
            "--token-env",
            "MY_REVIEW_TOKEN",
        ],
    )
    assert outcome.exit_code == 0, outcome.output
    assert json.loads(outcome.output)["outcome"] == "pass" and "test-secret-123" not in outcome.output


def test_required_material_and_rubric_missing(store, case, config):
    run, _, profile = case
    variants = [copy.deepcopy(profile), copy.deepcopy(profile)]
    variants[0]["rules"][1]["description"] = ""
    variants[1]["rules"] = variants[1]["rules"][:1]
    for value in variants:
        with pytest.raises(ReviewError):
            review_run(store, run, value, config)
    run.output_present = False
    with pytest.raises(ReviewError, match="output"):
        review_run(store, run, profile, config)
