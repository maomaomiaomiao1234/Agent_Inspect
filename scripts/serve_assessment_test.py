"""Local UI test server with actual HTTP controls and an optional service token from the environment."""

import argparse
import runpy
import tempfile
import threading
from pathlib import Path

import uvicorn

from agent_trace_review.api import create_app
from agent_trace_review.util import canonical


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--port", type=int, default=18765)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    server = runpy.run_path(str(root / "examples/assessment/mock_agent.py"))["serve"](port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="assessment-ui-test-") as temp:
            registry = Path(temp) / "targets.json"
            registry.write_text(
                canonical(
                    [
                        {
                            "id": "control" if mode == "correct" else mode,
                            "endpoint": f"http://127.0.0.1:{server.server_port}",
                            "task_path": "/" + mode,
                            "health_path": "/health",
                            "demo": True,
                        }
                        for mode in ("correct", "incorrect", "noop", "missing", "leaky")
                    ]
                )
            )
            uvicorn.run(
                create_app(args.data_dir, targets_file=str(registry)), host="127.0.0.1", port=args.port
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


if __name__ == "__main__":
    main()
