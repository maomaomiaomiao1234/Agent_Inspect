"""Deterministic HTTP controls. These agents are fixtures, not models or benchmark baselines."""

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def serve(host="127.0.0.1", port=9081):
    memory = {}
    global_memory = ["NONE"]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send_json(self, payload, status=200):
            content = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            try:
                self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            if self.path in {"/token-health", "/offline-health"}:
                offline = self.path == "/offline-health"
                self.send_json({"status": "ok", "fixture": True, "usage_mode": "offline" if offline else "model",
                                "usage_collection": "not_applicable" if offline else "call_usage"})
                return
            self.send_json({"status": "ok", "fixture": True})

        def do_POST(self):
            size = int(self.headers.get("Content-Length", "0"))
            if size > 128 * 1024:
                self.send_json({}, 413)
                return
            request = json.loads(self.rfile.read(size))
            if set(request) != {"protocol", "session_id", "turn", "prompt", "messages", "budget", "input"}:
                self.send_json({"error": "Only public task fields are accepted"}, 400)
                return
            if self.path == "/timeout":
                time.sleep(2)
            if self.path == "/missing":
                self.send_json({"status": "I finished, trust me"})
                return
            prompt, session = request["prompt"], request["session_id"]
            output = {"answer": 19}
            if prompt.startswith("Remember code "):
                code = prompt.split()[2].rstrip(".")
                memory[session] = code
                global_memory[0] = code
                output = {"stored": True}
            elif "remembered code" in prompt or "code remembered" in prompt:
                output = {"code": global_memory[0] if self.path == "/leaky" else memory.get(session, "NONE")}
            if self.path == "/incorrect":
                output = {"answer": -1, "code": "WRONG", "stored": False}
            elif self.path == "/noop":
                output = {}
            body = {"protocol": "agent-review/target-v1", "output": output}
            # Synthetic usage for UI accounting checks; never represents a provider bill.
            if self.path.startswith("/token-"):
                body["usage_mode"] = "offline" if self.path == "/token-offline" else "model"
                if self.path != "/token-offline":
                    events = [{"id": f"model-{i}", "kind": "llm", "model": "synthetic-ui-fixture",
                               "status": "completed", "usage": {"tokens": {"input": 10, "output": 5, "total": 15,
                                   "reasoning": 2, "cache_read": 6, "cache_miss": 4}},
                               "context": {"provider_request_id": f"synthetic-request-{i}", "usage_complete": True}}
                              for i in range(2)]
                    if self.path == "/token-partial":
                        events[-1] = {"id": "model-1", "kind": "llm", "model": "synthetic-ui-fixture", "status": "error"}
                        body.update(output=None, execution_status="error", error="agent_execution_failed")
                    body["trace"] = {"session_id": session, "turn": request["turn"], "coverage": "complete", "events": events}
            self.send_json(body)

    return ThreadingHTTPServer((host, port), Handler)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9081)
    args = parser.parse_args()
    server = serve(args.host, args.port)
    try:
        server.serve_forever()
    finally:
        server.server_close()
