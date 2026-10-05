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
    assert "assessment_suite" in client.get("/api/schema").json()
    assert "suite_generation_input" in client.get("/api/schema").json()
    assert "repository_plan_input" in client.get("/api/schema").json()
    from agent_trace_review.assessment_contracts import RepositoryPlanInput
    from agent_trace_review.repositories import inspect_repository
    from agent_trace_review.repository_planning import plan_repository

    source = root / "source"
    source.mkdir()
    (source / "tools.py").write_text("@tool\ndef calculator(a, b, operation): pass\n")
    plan = plan_repository(inspect_repository(source), RepositoryPlanInput(cases=3, concurrency=2))
    assert plan["tool_candidates"][0]["name"] == "calculator"
    assert plan["suite"]["repository_source_hash"] == plan["source_hash"]
    assert plan["suite"]["concurrency"] == 2
    assert "repository_manifest" in client.get("/api/schema").json()
    assert not client.get("/api/repository-builds").json()["enabled"]
    from agent_trace_review.repository_jobs import smolagents_adapter, smolagents_telemetry

    assert b"class AgentTarget" in smolagents_adapter()
    assert b"class CallRecorder" in smolagents_telemetry()
    assert "TargetTrace" in client.get("/api/schema").json()["target_response"]["$defs"]
    generated = client.post("/api/assessment-suites/generate", headers={"X-Review-Request": "1"},
                            json={"template": "smolagents", "cases": 7, "seed": 81})
    assert generated.status_code == 200 and len(generated.json()["cases"]) == 7
    general = client.post("/api/assessment-suites/generate", headers={"X-Review-Request": "1"},
                          json={"template": "general", "cases": 12, "attempts": 2, "concurrency": 3})
    assert general.status_code == 200 and len(general.json()["cases"]) == 12
    assert general.json()["attempts"] == 2 and general.json()["concurrency"] == 3
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
    from agent_trace_review.assessment_contracts import AssessmentSuite, TargetDefinition
    from agent_trace_review.assessment_store import AssessmentStore
    from agent_trace_review.assessments import assessment_markdown, prepare_assessment, run_assessment

    suite = AssessmentSuite.model_validate({"id": "package-assessment", "cases": [{"id": "echo",
                                           "turns": [{"prompt": "Return answer=19"}],
                                           "profile": {"profile_version": "1", "id": "answer",
                                                       "rules": [{"id": "correct", "op": "equals", "path": "/output/answer", "value": 19}]}}]})
    target = TargetDefinition(id="package-target", endpoint="https://mock.example", demo=True)
    db = AssessmentStore(Store(root / "assessment-data"))
    job, repository = prepare_assessment(db, target, suite)
    result = run_assessment(db, job["id"], target, suite, repository,
                            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"output": {"answer": 19}})))
    assert result["state"] == "completed" and result["results"][0]["outcome"] == "pass"
    assert result["quality"]["summary"]["conclusion"] == "pass"
    assert result["results"][0]["checks"][0]["comparison"]["actual"] == "19"
    suite = AssessmentSuite.model_validate(general.json())
    job, repository = prepare_assessment(db, target, suite)
    result = run_assessment(db, job["id"], target, suite, repository,
                           transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"output": {}})))
    assert result["quality"]["summary"]["fail"] == 24
    assert "结论与下一步" in assessment_markdown(result) and "实际返回：" in assessment_markdown(result)
    print(f"Wheel API, UI, coding/generic imports, profiles, templates and mocked LLM review passed: {wheel.name}")
    print("Wheel active assessment contracts, storage, suite generation and HTTP adapter passed.")
