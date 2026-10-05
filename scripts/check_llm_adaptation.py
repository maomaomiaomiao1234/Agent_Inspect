"""Actual Docker pipeline check with synthetic generation/model HTTP APIs; no paid calls.

The checkout is a local, explicitly synthetic Python repository. This verifies Docker,
native entry observation, task protocol, telemetry and cleanup, not LLM generation quality.
"""

import argparse
import ast
import json
import os
import shutil
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from agent_trace_review.assessment_contracts import AssessmentSuite
from agent_trace_review.assessments import AssessmentManager
from agent_trace_review.repository_contracts import RepositoryAssessmentInput
from agent_trace_review.repository_jobs import RepositoryManager
from agent_trace_review.storage import Store
from agent_trace_review.util import canonical

NATIVE = '''import json
import httpx

class NativeAgent:
    def __init__(self, config):
        self.config = config

    def calculate(self, a, b):
        return a + b

    def run(self, request, recorder):
        body = {"model": self.config["model"], "messages": request["messages"], "max_tokens": 1000}
        with httpx.Client() as client:
            response = client.post(self.config["api_url"] + "/chat/completions", json=body,
                headers={"Authorization": "Bearer " + self.config["api_key"]})
            response.raise_for_status()
            output = json.loads(response.json()["choices"][0]["message"]["content"])
        if "a" in request["input"]:
            output["answer"] = recorder.tool("calculator", self.calculate, request["input"]["a"], request["input"]["b"])
        return output
'''
BRIDGE = '''from native_agent import NativeAgent
def create_agent(config, recorder):
    return NativeAgent(config)
def run_agent(agent, request, recorder):
    return agent.run(request, recorder)
'''

STREAM_NATIVE = NATIVE.replace(
    'response = client.post(self.config["api_url"] + "/chat/completions", json=body,\n'
    '                headers={"Authorization": "Bearer " + self.config["api_key"]})\n'
    '            response.raise_for_status()\n'
    '            output = json.loads(response.json()["choices"][0]["message"]["content"])',
    'body.update(stream=True, stream_options={"include_usage": True})\n'
    '            text = ""\n'
    '            with client.stream("POST", self.config["api_url"] + "/chat/completions", json=body,\n'
    '                    headers={"Authorization": "Bearer " + self.config["api_key"]}) as response:\n'
    '                response.raise_for_status()\n'
    '                for line in response.iter_lines():\n'
    '                    if line.startswith("data: ") and line != "data: [DONE]":\n'
    '                        for choice in json.loads(line[6:]).get("choices", []):\n'
    '                            text += choice.get("delta", {}).get("content", "")\n'
    '            output = json.loads(text)',
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--stream", action="store_true", help="Exercise incremental provider SSE in the real container")
    parser.add_argument("--gateway", action="store_true", help="Route target model calls through the persistent gateway")
    parser.add_argument("--target-crash", action="store_true", help="Exit the target after its formal model request; requires --gateway")
    args = parser.parse_args()
    if args.target_crash and not args.gateway:
        parser.error("--target-crash requires --gateway")
    data_dir = args.data_dir or Path(tempfile.mkdtemp(prefix="agent-inspect-auto-adapt-validation-"))
    counters = {"generation": 0, "target": 0}
    native = STREAM_NATIVE if args.stream else NATIVE
    if args.target_crash:
        native = native.replace('        if "a" in request["input"]:',
            '        if request["input"].get("crash"):\n            import os\n            os._exit(23)\n        if "a" in request["input"]:')
    definition = next(n for n in ast.walk(ast.parse(native)) if isinstance(n, ast.FunctionDef) and n.name == "run")
    proposal = {"supported": True, "entry": {"path": "native_agent.py", "symbol": "NativeAgent.run", "line": definition.lineno},
                "bridge_code": BRIDGE, "limitations": ["Synthetic validation fixture, not third-party capability scores."]}

    class Provider(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            generation = self.path.startswith("/generate/")
            counters["generation" if generation else "target"] += 1
            content = canonical(proposal) if generation else canonical({"ready": True} if "ready=true" in body["messages"][-1]["content"] else {"answer": 19})
            usage = {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300} if generation else {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
            if not generation:
                usage.update(prompt_cache_hit_tokens=6, prompt_cache_miss_tokens=4,
                             completion_tokens_details={"reasoning_tokens": 2})
            result = canonical({"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": content}}], "usage": usage}).encode()
            streaming = not generation and body.get("stream")
            if streaming:
                result = ("data: " + canonical({"choices": [{"delta": {"content": content}}]}) + "\n\n"
                          + "data: " + canonical({"choices": [], "usage": usage}) + "\n\ndata: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream" if streaming else "application/json")
            self.send_header("x-request-id", "synthetic-" + str(counters["target"]))
            self.send_header("Content-Length", str(len(result)))
            self.end_headers()
            self.wfile.write(result)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("0.0.0.0", 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    manager = repositories = None
    try:
        with tempfile.TemporaryDirectory(prefix="agent-inspect-native-fixture-") as temp:
            source = Path(temp)
            (source / "native_agent.py").write_text(native)
            (source / "README.md").write_text("Synthetic native Python agent for integration validation only.\n")
            subprocess.run(["git", "init", "--quiet", str(source)], check=True)
            subprocess.run(["git", "-C", str(source), "add", "."], check=True)
            subprocess.run(["git", "-C", str(source), "-c", "user.name=Validation", "-c", "user.email=validation@example.invalid", "commit", "-qm", "synthetic native agent"], check=True)
            commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"]).decode().strip()
            def checkout(request, destination, command):
                shutil.copytree(source, destination)
                return commit
            settings = {"AGENT_REVIEW_LLM_API_URL": f"http://127.0.0.1:{server.server_port}/generate",
                "AGENT_REVIEW_LLM_MODEL": "synthetic-generator", "AGENT_REVIEW_LLM_TOKEN": "synthetic-generator-key",
                "AGENT_REVIEW_TARGET_API_URL": f"http://host.docker.internal:{server.server_port}/v1",
                "AGENT_REVIEW_TARGET_MODEL": "synthetic-target-model", "AGENT_REVIEW_TARGET_TOKEN": "synthetic-target-key",
                "AGENT_REVIEW_AUTO_ADAPT": "true"}
            if args.gateway:
                settings.update(AGENT_REVIEW_GATEWAY_HOST="0.0.0.0",
                                AGENT_REVIEW_GATEWAY_ADVERTISE_HOST="host.docker.internal",
                                AGENT_REVIEW_TARGET_API_URL=f"http://127.0.0.1:{server.server_port}/v1")
            suite = AssessmentSuite.model_validate({"id": "native-addition", "cases": [{"id": "sum",
                "turns": [{"prompt": "Compute 12 + 7. Return JSON with answer."}], "input": {"a": 12, "b": 7, "crash": args.target_crash},
                "profile": {"profile_version": "1", "id": "native-addition", "rules": [
                    {"id": "correct", "op": "equals", "path": "/output/answer", "value": 19},
                    {"id": "calculator", "op": "tool_required", "value": "calculator", "dimension": "behavior"}]}}],
                "budgets": [{"id": "default", "deadline_seconds": 30, "max_output_tokens": 100}]})
            with patch.dict(os.environ, settings), patch("agent_trace_review.repository_jobs.checkout_repository", checkout):
                manager = AssessmentManager(Store(data_dir), {})
                repositories = RepositoryManager(manager, enabled=True)
                job = repositories.db.create(RepositoryAssessmentInput(repository_url="https://github.com/example/synthetic-native-agent",
                    backend="auto", suite=suite, model_gateway=args.gateway, adaptation={"max_repairs": 0}))
                result = repositories.run(job["id"])
                bundle = repositories.bundle(job["id"])
                destination = data_dir / "validation.bundle.json"
                destination.write_text(canonical(bundle))
                assert result["state"] == "completed", result["error"]
                assert result["cleanup"] == "completed"
                row = bundle["assessment"]["job"]["results"][0]
                assert row["outcome"] == ("inconclusive" if args.target_crash else "pass"), row
                assert row["usage"]["fields"]["total_tokens"]["value"] == 15
                turns = bundle["assessment"]["runs"][0]["bundle"]["trace"]["artifacts"]["assessment"]["turns"]
                if not args.target_crash:
                    assert turns[0]["adapter_evidence"]["observed"]
                else:
                    assert row["execution_state"] == "error" and turns[0]["trace"] is None
                assert row["usage"]["fields"]["cache_read_tokens"]["value"] == 6
                assert row["usage"]["fields"]["reasoning_tokens"]["value"] == 2
                model_call = next(e for e in turns[0]["gateway" if args.target_crash else "trace"]["events"] if e["kind"] == "llm")
                assert model_call["context"]["assessment_id"] == result["assessment_id"]
                assert model_call["context"]["case_id"] == "sum"
                assert model_call["context"]["budget_id"] == "default"
                assert model_call["context"]["provider_request_id"].startswith("synthetic-")
                assert counters == {"generation": 1, "target": 2}
                if args.gateway:
                    assert bundle["assessment"]["gateway"]["request_count"] == 1
                    assert row["usage"]["fields"]["total_tokens"]["source"] == "gateway"
                    canonical_calls = [e for e in bundle["assessment"]["runs"][0]["bundle"]["trace"]["events"] if e["kind"] == "llm"]
                    assert len(canonical_calls) == 1 and canonical_calls[0]["provenance"] == "host_observed"
                    assert model_call["context"]["gateway_request_id"] == canonical_calls[0]["id"]
                for value in ("synthetic-generator-key", "synthetic-target-key"):
                    assert value not in destination.read_text()
                print(canonical({"state": result["state"], "cleanup": result["cleanup"], "outcome": row["outcome"],
                    "native_entry_observed": None if args.target_crash else True,
                    "target_crash": args.target_crash, "target_tokens": row["usage"]["fields"]["total_tokens"],
                    "requests": counters, "stream": args.stream, "gateway": args.gateway,
                    "evidence": str(destination), "job_id": job["id"]}))
    finally:
        if repositories:
            repositories.close()
        if manager:
            manager.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


if __name__ == "__main__":
    main()
