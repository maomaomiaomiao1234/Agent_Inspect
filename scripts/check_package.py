"""Verify a wheel's Python API and UI assets independently of the source tree."""

import sys
import tempfile
import zipfile
from pathlib import Path

wheel = Path(sys.argv[1]).resolve()
with tempfile.TemporaryDirectory(prefix="agent-review-wheel-") as temporary:
    root = Path(temporary)
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(root / "site")
    sys.path.insert(0, str(root / "site"))
    from fastapi.testclient import TestClient

    import agent_trace_review
    from agent_trace_review.api import create_app

    assert str(root) in agent_trace_review.__file__
    client = TestClient(create_app(str(root / "data")))
    index = client.get("/")
    assert index.status_code == 200 and '<div id="root">' in index.text
    import re

    asset = re.search(r'src="(/assets/[^\"]+)"', index.text)[1]
    assert client.get(asset).status_code == 200
    assert len(client.post("/api/demo", headers={"X-Review-Request": "1"}).json()["run_ids"]) == 2
    from importlib.resources import files

    template = files("agent_trace_review").joinpath("templates", "invoice")
    imported = client.post(
        "/api/imports",
        headers={"X-Review-Request": "1"},
        files={
            "file": ("trace.json", template.joinpath("trace.json").read_bytes()),
            "profile_file": ("profile.json", template.joinpath("profile.json").read_bytes()),
        },
    )
    assert imported.status_code == 200, imported.text
    detail = client.get("/api/runs/" + imported.json()["run_id"]).json()
    assert detail["evaluation"]["outcome"] == "pass"
    assert "generic_trace" in client.get("/api/schema").json()
    assert template.joinpath("evaluator.py").is_file()
    import httpx

    from agent_trace_review.llm_review import ReviewConfig, review_run
    from agent_trace_review.service import ingest
    from agent_trace_review.storage import Store

    llm_template = files("agent_trace_review").joinpath("templates", "llm-review")
    import json

    profile = json.loads(llm_template.joinpath("profile.json").read_text())
    store = Store(root / "llm-data")
    run, initial, _ = ingest(store, llm_template.joinpath("trace.json").read_bytes(), profile=profile)
    assert initial.outcome == "inconclusive"
    def mock(request):
        data = json.loads(json.loads(request.content)["messages"][1]["content"])
        response = {"results": [{"id": rule["id"], "status": "unknown", "explanation": "Offline package check",
                                 "evidence_paths": []} for rule in data["checks"]]}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(response)}}]})
    review = review_run(store, run, profile,
                        ReviewConfig(api_url="https://mock.example/v1", model="mock", token="package-test"),
                        transport=httpx.MockTransport(mock))
    assert review.outcome == "inconclusive" and review.judge["backend"] == "chat-completions"
    print(f"Wheel API, UI, coding/generic imports, profiles, templates and mocked LLM review passed: {wheel.name}")
