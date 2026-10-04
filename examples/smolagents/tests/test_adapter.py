"""Exercise the actual smolagents library, protocol boundary, and SDK against a local model fixture."""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import OpenAI

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_server import AgentTarget, create_app  # noqa: E402


def request(prompt, session="example", turn=0, **extra):
    return {
        "protocol": "agent-review/target-v1", "session_id": session, "turn": turn,
        "prompt": prompt, "messages": [{"role": "user", "content": prompt}],
        "input": {}, "budget": {"id": "starter", "deadline_seconds": 30, "max_output_tokens": 1024},
        **extra,
    }


@pytest.fixture
def client():
    with TestClient(create_app(AgentTarget())) as app:
        yield app


def test_arithmetic_uses_real_framework_tools_and_observations(client):
    response = client.post("/task", json=request("Compute 52 - 11. Return JSON with answer."))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["output"]["answer"] == 41
    execution = body["output"]["_execution"]
    assert execution["framework"] == "smolagents.ToolCallingAgent"
    assert execution["version"] == "1.26.0" and execution["demo"] is True
    assert execution["model_calls"] == 2
    assert execution["tools"][0]["tool"] == "calculator"
    assert execution["tools"][0]["output"] == {"answer": 41}
    assert "usage" not in body  # No invented inference Token or zero cost.


def test_two_step_calculation_uses_intermediate_tool_result(client):
    response = client.post("/task", json=request("Compute (12 + 7) * 3. Return JSON with answer."))
    output = response.json()["output"]
    assert output["answer"] == 57
    tools = output["_execution"]["tools"]
    assert len(tools) == 2
    assert tools[0]["output"]["answer"] == tools[1]["arguments"]["a"] == 19


def test_current_only_turn_remembers_and_new_session_is_isolated(client):
    first = client.post("/task", json=request("Remember code PINE-73. Return JSON with stored=true."))
    assert first.json()["output"]["stored"] is True
    second = client.post("/task", json=request("Return the remembered code as JSON.", turn=1))
    assert second.json()["output"]["code"] == "PINE-73"
    assert len(second.json()["output"]["_execution"]["tools"]) == 1  # Per-turn observations.
    other = client.post("/task", json=request("Return the code remembered in this session.", session="other"))
    assert other.json()["output"]["code"] == "NONE"
    replay = client.post("/task", json=request("Compute 1 + 2.", turn=0))
    assert replay.status_code == 409
    assert client.post("/task", json=request("Recall.", session="missing", turn=1)).status_code == 409


def test_full_history_accepts_previous_json_output(client):
    prompt = "Remember code PINE-73. Return JSON with stored=true."
    first = client.post("/task", json=request(prompt))
    recall = "Return the remembered code as JSON."
    second = client.post("/task", json=request(recall, turn=1, messages=[
        {"role": "user", "content": prompt},
        {"role": "assistant", "content": first.json()["output"]},
        {"role": "user", "content": recall},
    ]))
    assert second.status_code == 200, second.text
    assert second.json()["output"]["code"] == "PINE-73"


def test_rejects_hidden_answers_and_excessive_body(client):
    assert client.post("/task", json=request("Compute 1 + 2.", profile={"answer": 3})).status_code == 422
    assert client.post("/task", content=b"x" * (128 * 1024 + 1)).status_code == 413


def test_target_auth_and_backend_identity(monkeypatch):
    monkeypatch.setenv("SMOL_SOURCE_COMMIT", "c" * 40)
    with TestClient(create_app(AgentTarget(), "local-service-test")) as client:
        assert client.get("/health").json()["backend"] == "offline"
        assert client.get("/health").json()["commit"] == "c" * 40
        assert client.post("/task", json=request("Compute 1 + 2.")).status_code == 401
        response = client.post(
            "/task", headers={"Authorization": "Bearer local-service-test"}, json=request("Compute 1 + 2.")
        )
        assert response.json()["output"]["answer"] == 3


def test_unconfigured_model_mode_fails_before_requests():
    with pytest.raises(ValueError, match="SMOL_MODEL_API_BASE"):
        AgentTarget(backend="openai")
    with pytest.raises(ValueError, match="SMOL_MODEL_API_KEY"):
        AgentTarget(backend="openai", model_id="chosen-model", api_base="https://provider.example/v1")


def test_real_sdk_http_tool_loop_reports_each_turn_usage_without_double_counting():
    requests = []

    class ModelFixture(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            assert self.path == "/v1/chat/completions"
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            assert "thinking" not in body  # Other compatible providers receive no DeepSeek-only option.
            content = str(body["messages"][-1]["content"])
            # The SDK merges adjacent user/tool-response messages; old observations can precede a new task.
            if content.rfind("Observation:") > content.rfind("New task:"):
                tool = "final_answer"
                arguments = {"answer": {"answer": 19, "echo": 'fixture-key-with-quote"'}}
            else:
                tool = "calculator"
                arguments = {"a": 12, "b": 7, "operation": "add"}
            payload = {
                "id": "local-fixture", "object": "chat.completion", "created": 0, "model": "local-fixture",
                "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                    "role": "assistant", "content": None,
                    "tool_calls": [{"id": f"call-{len(requests)}", "type": "function", "function": {
                        "name": tool, "arguments": json.dumps(arguments),
                    }}],
                }}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }
            encoded = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), ModelFixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        target = AgentTarget(
            backend="openai", model_id="local-fixture", api_base=f"http://127.0.0.1:{server.server_port}/v1",
            api_key='fixture-key-with-quote"',
        )
        with TestClient(create_app(target)) as client:
            for turn in (0, 1):
                response = client.post("/task", json=request("Compute 12 + 7.", turn=turn))
                assert response.status_code == 200, response.text
                body = response.json()
                assert body["output"]["answer"] == 19
                assert body["output"]["echo"] == "[REDACTED]"
                assert body["usage"]["tokens"] == {"input": 20, "output": 10, "total": 30}
                assert "cost_usd" not in body["usage"]
        assert len(requests) == 4
        assert requests[0]["model"] == "local-fixture"
        assert requests[1]["max_tokens"] == 1019
        assert "profile" not in json.dumps(requests)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_deepseek_sdk_requests_disable_thinking_for_required_tool_calls(monkeypatch):
    requests = []

    def respond(http_request):
        assert str(http_request.url) == "https://api.deepseek.com/chat/completions"
        body = json.loads(http_request.content)
        requests.append(body)
        assert body["model"] == "deepseek-flash"
        assert body["thinking"] == {"type": "disabled"}
        assert body["tool_choice"] == "required"
        assert {t["function"]["name"] for t in body["tools"]} >= {"calculator", "final_answer"}
        tool, arguments = (
            ("calculator", {"a": 12, "b": 7, "operation": "add"})
            if len(requests) == 1 else ("final_answer", {"answer": {"answer": 19}})
        )
        return httpx.Response(200, json={
            "id": "deepseek-protocol-fixture", "object": "chat.completion", "created": 0,
            "model": "deepseek-flash",
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": f"call-{len(requests)}", "type": "function", "function": {
                    "name": tool, "arguments": json.dumps(arguments),
                }}],
            }}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })

    target = AgentTarget(
        backend="openai", model_id="deepseek-flash", api_base="https://api.deepseek.com",
        api_key="local-test-key",
    )
    original_new_session = target.new_session

    def fixture_session():
        session = original_new_session()
        session.model.backend.client.close()
        session.model.backend.client = OpenAI(
            api_key="local-test-key", base_url=target.api_base, max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(respond), trust_env=False),
        )
        return session

    monkeypatch.setattr(target, "new_session", fixture_session)
    with TestClient(create_app(target)) as client:
        response = client.post("/task", json=request("Compute 12 + 7."))
        assert response.status_code == 200, response.text
        assert response.json()["output"]["answer"] == 19
        assert response.json()["output"]["_execution"]["model_calls"] == 2
    assert len(requests) == 2
