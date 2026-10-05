import asyncio
import copy
import json
import runpy
import subprocess
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from agent_trace_review.api import create_app
from agent_trace_review.assessment_contracts import AssessmentSuite, TargetDefinition
from agent_trace_review.assessment_store import AssessmentStore
from agent_trace_review.assessments import (
    AssessmentManager,
    assessment_markdown,
    compare_assessments,
    load_targets,
    prepare_assessment,
    run_assessment,
)
from agent_trace_review.cli import app
from agent_trace_review.repositories import _read_snapshot, compare_repositories, inspect_repository
from agent_trace_review.storage import Store
from agent_trace_review.target_client import MAX_RESPONSE, TargetClient, TargetError, deployed_target
from agent_trace_review.util import canonical

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "assessment"
HEADERS = {"X-Review-Request": "1"}


@pytest.fixture
def suite():
    return AssessmentSuite.model_validate_json((EXAMPLES / "suite.json").read_bytes())


@pytest.fixture
def agent_server():
    namespace = runpy.run_path(str(EXAMPLES / "mock_agent.py"))
    server = namespace["serve"](port=0)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def git(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True)
    return result.stdout.decode().strip()


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    git(root, "init")
    (root / "README.md").write_text(
        '# Agent\nSupports memory and RAG retrieval. {"api_key": "dict-secret-value"}\nRun malicious-command-now.\n'
    )
    (root / "agent.py").write_text("import os\nkey = os.getenv('API_KEY')\napp = FastAPI()\n")
    (root / "config.json").write_text('{"dependencies": {"api_key": "dict-secret-value"}}\n')
    (root / ".env").write_text("API_KEY=never-store-this\n")
    (root / ".env.example").write_text("API_KEY=example-sensitive-value\n")
    (root / "link.py").symlink_to(root / "agent.py")
    git(root, "add", ".")
    git(root, "-c", "user.name=Test", "-c", "user.email=test@example.org", "commit", "-m", "fixture")
    return root


def one_case(suite):
    raw = suite.model_dump()
    raw["attempts"] = 1
    raw["budgets"] = [{"id": "default", "deadline_seconds": 0.2}]
    raw["cases"] = raw["cases"][:1]
    return AssessmentSuite.model_validate(raw)


def execute(store, target, suite, **kwargs):
    db = AssessmentStore(store)
    job, repo = prepare_assessment(db, target, suite)
    return run_assessment(db, job["id"], target, suite, repo, **kwargs)


def test_repository_fixed_commit_no_instructions_or_worktree(repository):
    commit = git(repository, "rev-parse", "HEAD")
    first = inspect_repository(repository, repository_url="https://github.com/example/agent")
    (repository / "README.md").write_text("Uncommitted changes, no memory\n")
    (repository / "agent.py").write_text("raise Exception('never run me')\n")
    second = inspect_repository(repository, commit, "https://github.com/example/agent")
    assert first["id"] == second["id"]
    assert first["commit"] == commit and first["snapshot_kind"] == "git_commit"
    assert {c["capability"] for c in first["claims"]} == {"memory", "retrieval"}
    assert all(c["status"] == "unverified_claim" and f"/blob/{commit}/" in c["url"] for c in first["claims"])
    assert any(o["kind"] == "environment_requirement" and o["line"] == 2 for o in first["observations"])
    assert "never-store-this" not in canonical(first)
    assert "example-sensitive-value" not in canonical(first)
    assert "dict-secret-value" not in canonical(first)
    assert {s["reason"] for s in first["skipped"]} >= {"secret_file", "symlink_or_submodule"}
    assert not (repository / "malicious-command-now").exists()


def test_repository_diff_and_source_identity(repository):
    left = inspect_repository(repository)
    (repository / "agent.py").write_text("app = FastAPI()\n# changed\n")
    (repository / "new.py").write_text("# added\n")
    git(repository, "add", ".")
    git(repository, "-c", "user.name=Test", "-c", "user.email=test@example.org", "commit", "-m", "next")
    right = inspect_repository(repository)
    result = compare_repositories(left, right)
    assert result["changed"] == ["agent.py"] and result["added"] == ["new.py"]
    assert left["source_hash"] != right["source_hash"]


def test_content_snapshot_symlinks_bounds(tmp_path, monkeypatch):
    root = tmp_path / "snapshot"
    root.mkdir()
    (root / "README.md").write_text("memory\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("outside-sensitive-data")
    (root / "linked").symlink_to(outside)
    (root / "link.md").symlink_to(outside / "secret.md")
    profile = inspect_repository(root)
    assert profile["commit"] is None and len(profile["files"]) == 1
    with pytest.raises(OSError):
        _read_snapshot(root, "linked/secret.md")
    assert "outside-sensitive-data" not in canonical(profile)
    with pytest.raises(ValueError):
        inspect_repository(root, "v1")
    monkeypatch.setattr("agent_trace_review.repositories.MAX_FILE", 1)
    assert inspect_repository(root)["files"] == []


@pytest.mark.parametrize(
    "ref,url",
    [
        ("--help", None),
        ("HEAD", "http://github.com/a/b"),
        ("HEAD", "https://github.com/a/b?token=sensitive"),
        ("HEAD", "https://github.com/a/b/tree/main"),
    ],
)
def test_invalid_repository_input(repository, ref, url):
    with pytest.raises(ValueError):
        inspect_repository(repository, ref, url)


@pytest.mark.parametrize(
    "endpoint", ["file:///tmp/a", "http://u:p@host", "http://host/task", "http://host?key=x"]
)
def test_only_operator_root_endpoints(endpoint):
    with pytest.raises(ValueError):
        TargetDefinition(id="x", endpoint=endpoint)


def test_contracts_bound_work_and_duplicate_ids(suite):
    raw = suite.model_dump()
    raw["attempts"] = 10
    raw["budgets"][0]["deadline_seconds"] = 30
    with pytest.raises(ValueError, match="900"):
        AssessmentSuite.model_validate(raw)
    raw["attempts"] = 1
    raw["cases"].append(copy.deepcopy(raw["cases"][0]))
    with pytest.raises(ValueError, match="唯一"):
        AssessmentSuite.model_validate(raw)


@pytest.mark.parametrize(
    "path,outcome",
    [("/correct", "pass"), ("/incorrect", "fail"), ("/noop", "fail"), ("/missing", "inconclusive")],
)
def test_real_http_controls(store, agent_server, suite, path, outcome):
    target = TargetDefinition(
        id="control", endpoint=agent_server, task_path=path, health_path="/health", demo=True
    )
    job = execute(store, target, suite)
    assert job["state"] == "completed" and job["completed"] == 16
    assert {r["outcome"] for r in job["results"]} == {outcome}, job
    assert len({r["session_id"] for r in job["results"]}) == 16
    assert all(c["reported_total_tokens"] is None and c["reported_cost_usd"] is None for c in job["curves"])
    assert job["health"]["status"] == "ready"
    for row in job["results"]:
        run = store.get_run(row["run_id"])
        evaluation = store.get_evaluation(run.id, row["revision_id"])
        assert evaluation.outcome == outcome
        assert run.demo and not run.trace_complete and run.framework == "http-target-v1"
        assert all(e.kind != "tool" and e.kind != "llm" for e in run.events)
        for turn in run.artifacts["assessment"]["turns"]:
            assert not (
                {"profile", "rules", "claim_ids", "source_refs", "suite_hash"} & turn["request"].keys()
            )
    assert f"{job['completed']}/{job['planned']}" in assessment_markdown(job)


def test_actual_session_leak_detected(store, agent_server, suite):
    target = TargetDefinition(
        id="control", endpoint=agent_server, task_path="/leaky", health_path="/health", demo=True
    )
    job = execute(store, target, suite)
    assert {r["outcome"] for r in job["results"] if r["case_id"] == "session-isolation"} == {"fail"}
    assert {r["outcome"] for r in job["results"] if r["case_id"] != "session-isolation"} == {"pass"}


def test_source_claim_validation_and_linkage(store, repository, suite):
    target = TargetDefinition(
        id="x",
        endpoint="http://registered.test",
        repository=str(repository),
        repository_url="https://github.com/example/agent",
    )
    profile = inspect_repository(repository, repository_url=target.repository_url)
    raw = one_case(suite).model_dump()
    raw["cases"][0]["claim_ids"] = [profile["claims"][0]["id"]]
    raw["cases"][0]["source_refs"] = [{"path": "agent.py", "line": 2}]
    contract = AssessmentSuite.model_validate(raw)
    mock = httpx.MockTransport(lambda r: httpx.Response(200, json={"output": {"answer": 19}}))
    job = execute(store, target, contract, transport=mock)
    assert job["claims"][0]["assessment_status"] == "supported_for_cases"
    assert job["claims"][1]["assessment_status"] == "untested"
    assert job["results"][0]["source_evidence"][1]["status"] == "hypothesis"
    assert f"/blob/{profile['commit']}/agent.py#L2" in assessment_markdown(job)
    for value in ["unknown.py", "../agent.py"]:
        bad = copy.deepcopy(raw)
        bad["cases"][0]["source_refs"][0]["path"] = value
        with pytest.raises(ValueError):
            prepare_assessment(AssessmentStore(store), target, AssessmentSuite.model_validate(bad))
    raw["cases"][0]["source_refs"][0]["line"] = 999
    with pytest.raises(ValueError):
        prepare_assessment(AssessmentStore(store), target, AssessmentSuite.model_validate(raw))


@pytest.mark.parametrize(
    "response,code",
    [
        (httpx.Response(302, headers={"Location": "http://unregistered.test"}), "http_302"),
        (httpx.Response(200, text="not json"), "invalid_json"),
        (httpx.Response(200, json={"unexpected": "x"}), "invalid_target_contract"),
        (httpx.Response(200, content=b"x" * (MAX_RESPONSE + 1)), "response_too_large"),
    ],
)
def test_bad_http_never_passes(store, suite, response, code):
    calls = []

    def mock(request):
        calls.append(str(request.url))
        return response

    job = execute(
        store,
        TargetDefinition(id="x", endpoint="http://registered.test"),
        one_case(suite),
        transport=httpx.MockTransport(mock),
    )
    assert calls == ["http://registered.test/task"]
    assert job["results"][0]["error"] == code
    assert job["results"][0]["outcome"] == "inconclusive"


def test_wall_deadline_and_no_automatic_retry(store, suite):
    calls = []

    async def slow(request):
        calls.append(request)
        await asyncio.sleep(2)
        return httpx.Response(200, json={"output": {"answer": 19}})

    started = time.monotonic()
    job = execute(
        store,
        TargetDefinition(id="x", endpoint="http://registered.test"),
        one_case(suite),
        transport=httpx.MockTransport(slow),
    )
    assert time.monotonic() - started < 1
    assert len(calls) == 1
    assert job["results"][0]["execution_state"] == "timeout"


def test_memory_probe_omits_prior_messages(store, suite):
    raw = one_case(suite).model_dump()
    raw["cases"] = [suite.cases[1].model_dump()]
    requests = []

    def mock(request):
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(
            200, json={"output": {"stored": True} if body["turn"] == 0 else {"code": "ACORN-42"}}
        )

    job = execute(
        store,
        TargetDefinition(id="x", endpoint="http://registered.test"),
        AssessmentSuite.model_validate(raw),
        transport=httpx.MockTransport(mock),
    )
    assert job["results"][0]["outcome"] == "pass"
    assert len(requests[1]["messages"]) == 1
    assert "ACORN-42" not in canonical(requests[1])
    assert requests[0]["session_id"] == requests[1]["session_id"]


def test_token_budget_exhausted_not_pass_and_input_is_public(store, suite):
    raw = one_case(suite).model_dump()
    raw["cases"] = [suite.cases[1].model_dump()]
    raw["cases"][0]["input"] = {"document": "Public input material"}
    raw["budgets"][0]["max_output_tokens"] = 5
    requests = []

    def mock(request):
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(200, json={"output": {"stored": True}, "usage": {"tokens": {"output": 5}}})

    job = execute(
        store,
        TargetDefinition(id="x", endpoint="http://registered.test"),
        AssessmentSuite.model_validate(raw),
        transport=httpx.MockTransport(mock),
    )
    assert len(requests) == 1 and requests[0]["input"]["document"] == "Public input material"
    assert job["results"][0]["execution_state"] == "budget_exhausted"
    assert job["results"][0]["outcome"] == "inconclusive"


def test_cancel_during_call_keeps_partial_trace(store, suite):
    db = AssessmentStore(store)
    contract = one_case(suite)
    target = TargetDefinition(id="x", endpoint="http://registered.test")
    job, repo = prepare_assessment(db, target, contract)

    def mock(request):
        db.cancel(job["id"])
        return httpx.Response(200, json={"output": {"answer": 19}})

    result = run_assessment(db, job["id"], target, contract, repo, transport=httpx.MockTransport(mock))
    assert result["state"] == "cancelled"
    assert result["results"][0]["outcome"] == "inconclusive"
    run = store.get_run(result["results"][0]["run_id"])
    assert len(run.events) == 2
    assert not run.output_present


def test_slow_stream_deadline_covers_complete_body(store, suite):
    class Drip(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(50):
                await asyncio.sleep(0.02)
                yield b" "

    job = execute(
        store,
        TargetDefinition(id="x", endpoint="http://registered.test"),
        one_case(suite),
        transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=Drip())),
    )
    assert job["results"][0]["error"] == "timeout"


def test_target_credential_echo_redacted_and_usage_reported(store, suite, monkeypatch):
    token = "private-plain-credential-value"
    monkeypatch.setenv("ASSESSMENT_TEST_TOKEN", token)
    target = TargetDefinition(id="x", endpoint="http://registered.test", token_env="ASSESSMENT_TEST_TOKEN")

    def mock(request):
        assert request.headers["Authorization"] == "Bearer " + token
        return httpx.Response(
            200,
            json={
                "output": {"answer": 19, "echo": token},
                "usage": {"tokens": {"input": 3, "output": 2, "total": 5}, "cost_usd": 0.01},
            },
        )

    job = execute(store, target, one_case(suite), transport=httpx.MockTransport(mock))
    assert job["curves"][0]["reported_total_tokens"] == 5
    assert job["curves"][0]["reported_cost_usd"] == 0.01
    assert token not in canonical(job)
    for path in store.artifacts.iterdir():
        assert token.encode() not in path.read_bytes()
    assert (
        b"[redacted:credential]"
        in store.get_artifact(store.get_run(job["results"][0]["run_id"]).source_artifact)[0]
    )


def test_health_error_and_missing_credential_fail_job(store, suite):
    for target, response, code in [
        (
            TargetDefinition(id="x", endpoint="http://registered.test", health_path="/health"),
            {"status": "error"},
            "health_not_ready",
        ),
        (
            TargetDefinition(
                id="x", endpoint="http://registered.test", token_env="UNSET_ASSESSMENT_TEST_CREDENTIAL"
            ),
            {},
            "missing_target_credential",
        ),
    ]:
        job = execute(
            store,
            target,
            one_case(suite),
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=response)),
        )
        assert job["state"] == "failed" and job["error"] == code
        assert job["curves"][0]["unknown"] == 1 and job["completed"] == 0


def test_cancel_before_start_and_restart_preserve_results(store, suite):
    db = AssessmentStore(store)
    target = TargetDefinition(id="x", endpoint="http://registered.test")
    job, repo = prepare_assessment(db, target, one_case(suite))
    db.cancel(job["id"])
    assert run_assessment(db, job["id"], target, one_case(suite), repo)["state"] == "cancelled"
    second, _ = prepare_assessment(db, target, one_case(suite))
    assert db.claim(second["id"])
    body = {"results": [{"run_id": "preserved"}], "completed": 1}
    db.update(second["id"], body)
    reopened = AssessmentStore(Store(store.root))
    reopened.recover_interrupted()
    recovered = reopened.job(second["id"])
    assert recovered["state"] == "interrupted" and recovered["results"][0]["run_id"] == "preserved"


def test_queue_capacity_atomic(store):
    db = AssessmentStore(store)
    db.create_job({}, queue_limit=1)
    with pytest.raises(ValueError, match="队列"):
        db.create_job({}, queue_limit=1)


def test_revision_regression_guard(store, suite, agent_server):
    target = TargetDefinition(id="control", endpoint=agent_server, task_path="/correct", demo=True)
    left = execute(store, target, one_case(suite))
    right = execute(store, target.model_copy(update={"task_path": "/incorrect"}), one_case(suite))
    delta = compare_assessments(left, right)
    assert delta["comparable"] and delta["changes"][0]["regression"]
    assert delta["changes"][0]["from"] == "pass" and delta["changes"][0]["to"] == "fail"
    for field, value in [
        ("suite_hash", "different"),
        ("state", "cancelled"),
        ("target_id", "different"),
        ("demo", False),
    ]:
        changed = {**right, field: value}
        assert not compare_assessments(left, changed)["comparable"]


def test_docker_immutable_image_constraints_cleanup(monkeypatch):
    image_id = "sha256:" + "a" * 64
    calls = []

    def docker(*args):
        calls.append(args)
        if args[:2] == ("image", "inspect"):
            return json.dumps([{"Id": image_id, "Config": {"Volumes": None, "Labels": {}}}])
        if args[0] == "inspect":
            return json.dumps(
                [{"NetworkSettings": {"Ports": {"9081/tcp": [{"HostIp": "127.0.0.1", "HostPort": "54321"}]}}}]
            )
        return "container"

    monkeypatch.setattr("agent_trace_review.target_client._docker", docker)
    monkeypatch.setattr(TargetClient, "health", lambda self, timeout: {"status": "ready"})
    target = TargetDefinition(id="x", deployment={"image": image_id, "port": 9081}, health_path="/health")
    with pytest.raises(RuntimeError):
        with deployed_target(target) as (endpoint, info):
            assert endpoint == "http://127.0.0.1:54321" and info["image_id"] == image_id
            raise RuntimeError("forced")
    argv = next(c for c in calls if c[0] == "run")
    assert {"--pull=never", "--read-only", "--cap-drop=ALL", "--pids-limit=128", "--user"} <= set(argv)
    assert "--volume" not in argv and "--mount" not in argv and argv[-1] == image_id
    assert calls[-1][:2] == ("rm", "-f")


def test_docker_cleanup_failure_is_not_hidden(monkeypatch):
    image_id = "sha256:" + "a" * 64

    def docker(*args):
        if args[:2] == ("image", "inspect"):
            return json.dumps([{"Id": image_id, "Config": {"Volumes": None}}])
        raise TargetError("docker_unavailable_or_timeout")

    monkeypatch.setattr("agent_trace_review.target_client._docker", docker)
    target = TargetDefinition(id="x", deployment={"image": image_id, "port": 9081}, health_path="/health")
    with pytest.raises(TargetError, match="container_cleanup_failed:agent-inspect-target-"):
        with deployed_target(target):
            pass


def test_docker_credentials_file_deleted_after_failure(monkeypatch, tmp_path):
    image_id = "sha256:" + "a" * 64
    monkeypatch.setenv("ASSESSMENT_DEPLOY_TEST_SECRET", "test-value")
    captured = []

    def docker(*args):
        if args[:2] == ("image", "inspect"):
            return json.dumps([{"Id": image_id, "Config": {"Volumes": None}}])
        if args[0] == "run":
            path = Path(args[args.index("--env-file") + 1])
            captured.append(path)
            assert path.stat().st_mode & 0o777 == 0o600
            assert path.read_text() == "API_KEY=test-value\n"
            raise TargetError("docker_command_failed")
        return ""

    monkeypatch.setattr("agent_trace_review.target_client._docker", docker)
    target = TargetDefinition(
        id="x",
        deployment={
            "image": image_id,
            "port": 9081,
            "environment": {"API_KEY": "ASSESSMENT_DEPLOY_TEST_SECRET"},
        },
        health_path="/health",
    )
    with pytest.raises(TargetError, match="docker_command_failed"):
        with deployed_target(target):
            pass
    assert captured and not captured[0].exists()


def test_api_active_job_auth_exports_and_registered_targets(
    tmp_path, suite, agent_server, repository, monkeypatch
):
    monkeypatch.setenv("AGENT_REVIEW_SERVICE_TOKEN", "test-service-secret")
    registry = tmp_path / "targets.json"
    registry.write_text(
        canonical(
            [
                {
                    "id": "control",
                    "endpoint": agent_server,
                    "task_path": "/correct",
                    "repository": str(repository),
                    "demo": True,
                }
            ]
        )
    )
    headers = {**HEADERS, "Authorization": "Bearer test-service-secret"}
    with TestClient(create_app(str(tmp_path / "data"), targets_file=str(registry))) as client:
        assert client.get("/api/health").json()["authentication_required"]
        assert client.get("/api/health").json()["data_dir"] == "[server-managed]"
        assert client.get("/api/runs").status_code == 401
        assert client.get("/api/targets", headers=headers).json()[0]["id"] == "control"
        assert "endpoint" not in client.get("/api/targets", headers=headers).json()[0]
        assert (
            client.post(
                "/api/assessments", headers={"Authorization": headers["Authorization"]}, json={}
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/assessments",
                headers=headers,
                json={"target_id": "not-registered", "suite": suite.model_dump()},
            ).status_code
            == 422
        )
        assert (
            client.post("/api/assessments", headers=headers, content=b"x" * (1024 * 1024 + 1)).status_code
            == 413
        )
        repo = client.post("/api/targets/control/repository-profile", headers=headers).json()
        assert client.get(f"/api/repository-profiles/{repo['id']}", headers=headers).json()["commit"]
        response = client.post(
            "/api/assessments",
            headers=headers,
            json={"target_id": "control", "suite": one_case(suite).model_dump()},
        )
        assert response.status_code == 202, response.text
        job_id = response.json()["id"]
        for _ in range(100):
            job = client.get(f"/api/assessments/{job_id}", headers=headers).json()
            if job["state"] not in {"queued", "running"}:
                break
            time.sleep(0.01)
        assert job["state"] == "completed" and job["results"][0]["outcome"] == "pass", job
        assert client.get("/api/assessments", headers=headers).json()[0]["id"] == job_id
        assert "results" not in client.get("/api/assessments", headers=headers).json()[0]
        assert "独立任务验收" in client.get(f"/api/assessments/{job_id}/export", headers=headers).text
        bundle = client.get(f"/api/assessments/{job_id}/export?format=bundle", headers=headers).json()
        assert len(bundle["runs"]) == 1 and bundle["repository"]["id"] == repo["id"]
        assert "target_request" in client.get("/api/schema", headers=headers).json()
        assert client.post(
            "/api/assessment-comparisons", headers=headers, json={"left_id": job_id, "right_id": job_id}
        ).json()["comparable"]
        assert client.get("/api/artifacts/" + job["suite_artifact"]).status_code == 401
        assert client.get("/api/assessments/unknown", headers=headers).status_code == 404


def test_cli_real_execution_and_report(tmp_path, suite, agent_server, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    registry = tmp_path / "targets.json"
    registry.write_text(
        canonical([{"id": "control", "endpoint": agent_server, "task_path": "/correct", "demo": True}])
    )
    suite_file = tmp_path / "suite.json"
    suite_file.write_text(one_case(suite).model_dump_json())
    data = str(tmp_path / "data")
    result = runner.invoke(
        app, ["assess", "control", "--suite", str(suite_file), "--targets", str(registry), "--data-dir", data]
    )
    assert result.exit_code == 0, result.output
    job = json.loads(result.stdout)
    assert job["results"][0]["outcome"] == "pass"
    report = runner.invoke(app, ["assessment-report", job["id"], "--data-dir", data])
    assert report.exit_code == 0 and job["id"] in report.stdout
    bundle = runner.invoke(app, ["assessment-report", job["id"], "--format", "bundle", "--data-dir", data])
    assert bundle.exit_code == 0 and len(json.loads(bundle.stdout)["runs"]) == 1
    result = runner.invoke(app, ["serve", "--host", "0.0.0.0", "--data-dir", data])
    assert result.exit_code != 0 and "AGENT_REVIEW_SERVICE_TOKEN" in result.output


def test_registry_validation(tmp_path):
    path = tmp_path / "targets.json"
    target = {"id": "x", "endpoint": "http://registered.test"}
    path.write_text(canonical([target, target]))
    with pytest.raises(ValueError, match="唯一"):
        load_targets(path)
    path.write_text(canonical({"targets": []}))
    with pytest.raises(ValueError):
        load_targets(path)


def test_remote_api_requires_service_auth_even_without_cli(tmp_path):
    with TestClient(create_app(str(tmp_path / "data")), client=("198.51.100.22", 12345)) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/runs").status_code == 403
        assert client.post("/api/demo", headers=HEADERS).status_code == 403


def test_manager_rejects_unregistered_target_and_shuts_down(store, suite):
    manager = AssessmentManager(store, {})
    try:
        with pytest.raises(ValueError, match="登记"):
            manager.submit("x", suite)
    finally:
        manager.close()


def test_shutdown_cancels_active_jobs_older_than_list_page(store):
    manager = AssessmentManager(store, {})
    active = manager.db.create_job({})
    assert manager.db.claim(active["id"])
    for _ in range(105):
        finished = manager.db.create_job({})
        manager.db.update(finished["id"], {}, "completed")
    manager.close()
    assert manager.db.job(active["id"])["cancel_requested"]
