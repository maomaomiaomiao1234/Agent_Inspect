"""Local browser-test app plus a deterministic mock API. No external model calls."""

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import uvicorn

from agent_trace_review.api import create_app


class MockProvider(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/v1/chat/completions" or self.headers.get("Authorization") != "Bearer local-test-token":
            self.send_error(401)
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        data = json.loads(body["messages"][1]["content"])
        result = {"results": [
            {"id": check["id"], "status": "pass", "explanation": "本地模拟响应，仅用于验证接口流程。",
             "evidence_paths": ["/output"]} for check in data["checks"]
        ]}
        payload = json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(result)}}],
                              "usage": {"prompt_tokens": 100, "completion_tokens": 80, "total_tokens": 180}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--mock-port", type=int, default=18766)
    args = parser.parse_args()
    provider = ThreadingHTTPServer(("127.0.0.1", args.mock_port), MockProvider)
    threading.Thread(target=provider.serve_forever, daemon=True).start()
    try:
        uvicorn.run(create_app(args.data_dir), host="127.0.0.1", port=args.port)
    finally:
        provider.shutdown()
