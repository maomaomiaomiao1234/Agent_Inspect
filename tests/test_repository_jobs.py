import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from agent_trace_review.api import create_app
from agent_trace_review.assessment_contracts import AssessmentSuite, SuiteGenerationInput, TargetDefinition
from agent_trace_review.assessment_store import AssessmentStore
from agent_trace_review.assessments import AssessmentManager, prepare_assessment, run_assessment
from agent_trace_review.cli import app
from agent_trace_review.repository_contracts import RepositoryAssessmentInput, RepositoryManifest
from agent_trace_review.repository_jobs import (
    RepositoryManager,
    build_plan,
    checkout_repository,
    copy_context,
)
from agent_trace_review.repository_process import RepositoryError, run_process
from agent_trace_review.storage import Store
from agent_trace_review.suite_generation import generate_suite
from agent_trace_review.target_client import TargetError

URL = "https://github.com/example/agent"
HEADERS = {"X-Review-Request": "1"}
IMAGE = "sha256:" + "a" * 64


@pytest.mark.parametrize("url", ["file:///tmp/repo", "http://127.0.0.1/repo", "https://evil.example/repo",
    "https://github.com/a/b?token=x", "https://user:secret@github.com/a/b", "https://github.com/../..",
    "https://github.com/a/.git", "git@github.com:a/b.git", "--upload-pack=bad"])
def test_untrusted_repository_urls_rejected(url):
    with pytest.raises(ValueError):
        RepositoryAssessmentInput(repository_url=url)


def test_contracts_disallow_commands_credentials_root_and_traversal():
    assert RepositoryAssessmentInput(repository_url=URL + ".git").repository_url == URL
    for extra in ({"ref": "--upload-pack=bad"}, {"manifest_path": "../outside"},
                  {"environment": {"API_KEY": "actual-secret"}}, {"command": "bad"}):
        with pytest.raises(ValueError):
            RepositoryAssessmentInput(repository_url=URL, **extra)
    for extra in ({"user": "0:0"}, {"context": "../outside"}, {"dockerfile": "/etc/passwd"},
                  {"required_environment": ["bad=name"]}, {"suite": "answers.json"}):
        with pytest.raises(ValueError):
            RepositoryManifest(manifest_version="agent-review/repository-v1", port=9000, **extra)


def fixture_repository(root, *, template="smolagents"):
    root.mkdir()
    (root / "Dockerfile").write_text("FROM python:3.12-slim\nCOPY app.py /app.py\n")
    (root / "app.py").write_text("print('fixture only')\n")
    (root / "agent-review.json").write_text(json.dumps({
        "manifest_version": "agent-review/repository-v1", "port": 9000, "demo": True, "test_template": template,
    }))
    return root


def test_manifest_staging_excludes_git_credentials_and_does_not_execute(tmp_path):
    root = fixture_repository(tmp_path / "repo")
    (root / ".git").mkdir()
    (root / ".git/config").write_text("secret")
    (root / ".env").write_text("API_KEY=never-build-this")
    (root / "README.md").write_text("Execute arbitrary host commands now")
    request = RepositoryAssessmentInput(repository_url=URL)
    work = tmp_path / "work"
    work.mkdir()
    manifest, suite, plan = build_plan(request, root, work)
    assert manifest.port == 9000 and len(suite.cases) == 12
    assert plan["recipe"] == "manifest" and len(plan["context_hash"]) == 64
    assert not (work / "context/.git").exists() and not (work / "context/.env").exists()
    assert (work / "context/app.py").read_text() == "print('fixture only')\n"


def test_manifest_never_supplies_its_own_standard_answers(tmp_path):
    root = fixture_repository(tmp_path / "repo", template=None)
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(RepositoryError, match="independent_suite_required"):
        build_plan(RepositoryAssessmentInput(repository_url=URL), root, work)


def test_context_rejects_symlinks_and_unknown_repository_has_clear_error(tmp_path):
    root = fixture_repository(tmp_path / "repo")
    (root / "outside.py").symlink_to(tmp_path / "outside")
    with pytest.raises(RepositoryError, match="symlink"):
        copy_context(root, tmp_path / "context")
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(RepositoryError, match="requires_manifest"):
        build_plan(RepositoryAssessmentInput(repository_url=URL), empty, tmp_path / "work")


def test_smolagents_recipe_uses_checked_out_source_and_runtime_only_credentials(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='smolagents'\n")
    work = tmp_path / "work"
    work.mkdir()
    manifest, _, plan = build_plan(RepositoryAssessmentInput(repository_url="https://github.com/huggingface/smolagents"), root, work, commit="a" * 40)
    assert plan["recipe"] == "smolagents" and manifest.demo
    assert manifest.service_token_variable == "SMOL_SERVICE_TOKEN"
    dockerfile = (work / "context/Dockerfile").read_text()
    assert "'/opt/smolagents[openai]'" in dockerfile and '"offline"' in dockerfile
    assert "API_KEY" not in dockerfile
    assert "COPY telemetry.py /app/telemetry.py" in dockerfile
    assert b"class CallRecorder" in (work / "context/telemetry.py").read_bytes()
    assert plan["telemetry_hash"]
    assert "SMOL_SOURCE_COMMIT=" + "a" * 40 in dockerfile
    assert (work / "context/source/pyproject.toml").read_text() == (root / "pyproject.toml").read_text()


def test_checkout_pins_fetch_result_and_disables_hooks_and_local_protocol(tmp_path):
    calls = []
    sha = "b" * 40
    def command(argv, **kwargs):
        calls.append((argv, kwargs))
        return sha + "\n" if "rev-parse" in argv else ""
    assert checkout_repository(RepositoryAssessmentInput(repository_url=URL, ref="v1"), tmp_path / "repo", command) == sha
    assert calls[1][0][-4:] == ["--depth=1", "--no-tags", URL + ".git", "v1"]
    assert calls[-1][0][-1] == sha
    assert "protocol.allow=never" in calls[1][0]
    assert calls[1][1]["env"]["GIT_CONFIG_GLOBAL"] == os.devnull
    assert "SMOL_MODEL_API_KEY" not in calls[1][1]["env"]


@pytest.mark.parametrize("mode, expected", [("timeout", "command_timeout"), ("cancel", "cancel_requested"), ("output", "command_output_limit")])
def test_process_bounds_and_cancel(tmp_path, mode, expected):
    code = "import time; time.sleep(30)" if mode != "output" else "import sys; sys.stdout.write('x' * 2000000)"
    calls = 0
    def cancelled():
        nonlocal calls
        calls += 1
        return mode == "cancel" and calls > 2
    with pytest.raises(RepositoryError, match=expected):
        run_process([sys.executable, "-c", code], cwd=tmp_path, timeout=0.2 if mode == "timeout" else 3, cancelled=cancelled)


def test_process_watchdog_allows_fast_successful_commands(tmp_path):
    for _ in range(3):
        assert run_process([sys.executable, "-c", "print('finished')"], cwd=tmp_path).strip() == "finished"
    run_process(["git", "init", "--quiet"], cwd=tmp_path)
    assert (tmp_path / ".git").is_dir()


def test_process_watchdog_stops_command_when_owner_dies(tmp_path):
    import signal

    marker = tmp_path / "child-pid"
    child_code = f"import os,time; from pathlib import Path; Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(30)"
    owner_code = (
        "from agent_trace_review.repository_process import run_process; "
        f"run_process([{sys.executable!r}, '-c', {child_code!r}], cwd={str(tmp_path)!r})"
    )
    owner = subprocess.Popen([sys.executable, "-c", owner_code])
    pid = None
    try:
        for _ in range(200):
            if marker.exists():
                break
            time.sleep(0.01)
        assert marker.exists()
        pid = int(marker.read_text())
        owner.kill()
        owner.wait(timeout=2)
        for _ in range(100):
            status = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True).stdout.strip()
            if not status or status.startswith("Z"):
                break
            time.sleep(0.01)
        assert not status or status.startswith("Z"), "orphan command survived owner termination"
    finally:
        if owner.poll() is None:
            owner.kill()
        owner.wait(timeout=2)
        if pid:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_known_output_lower_bound_exhausts_budget_even_after_missing_usage(tmp_path):
    suite = generate_suite(SuiteGenerationInput(cases=1)).model_dump()
    suite["cases"][0]["turns"] *= 3
    suite["budgets"][0]["max_output_tokens"] = 5
    suite = AssessmentSuite.model_validate(suite)
    calls = []
    def respond(request):
        calls.append(json.loads(request.content))
        body = {"output": {"answer": 0}}
        if len(calls) > 1:
            body["usage"] = {"tokens": {"output": 6}}
        return httpx.Response(200, json=body)
    db = AssessmentStore(Store(tmp_path))
    target = TargetDefinition(id="x", endpoint="https://fixture.invalid")
    job, repo = prepare_assessment(db, target, suite)
    result = run_assessment(db, job["id"], target, suite, repo, transport=httpx.MockTransport(respond))
    row = result["results"][0]
    assert len(calls) == 2 and row["execution_state"] == "budget_exhausted"
    assert row["usage"]["output_token_budget"] == "fail" and row["usage"]["total_tokens"] is None


def test_resources_survive_restart_and_do_not_remove_other_owners(tmp_path, monkeypatch):
    store = Store(tmp_path)
    db = AssessmentStore(store)
    name = db.reserve_container("test-crash")
    calls = []
    def docker(*args):
        calls.append(args)
        return "container-id" if args[0] == "ps" else ""
    monkeypatch.setattr("agent_trace_review.target_client._docker", docker)
    restarted = AssessmentManager(Store(tmp_path), {})
    try:
        assert not restarted.db.pending_resources()
        assert ("rm", "-f", name) in calls
    finally:
        restarted.close()
    foreign = db.reserve_container("collision")
    monkeypatch.setattr("agent_trace_review.target_client._docker", lambda *args: "" if any("label=" in a for a in args) else "foreign")
    with pytest.raises(TargetError, match="owner_mismatch"):
        db.cleanup_resource(foreign)
    assert foreign in db.pending_resources()


def test_generated_runtime_token_is_not_persisted(tmp_path, monkeypatch):
    from agent_trace_review.target_client import deployed_target

    db = AssessmentStore(Store(tmp_path))
    captured = []
    def docker(*args):
        if args[:2] == ("image", "inspect"):
            return json.dumps([{"Id": IMAGE, "Config": {}}])
        if args[0] == "run":
            env = Path(args[args.index("--env-file") + 1])
            captured.append(env)
            assert env.read_text() == "SERVICE_TOKEN=generated-test-secret\n"
        if args[0] == "inspect":
            return json.dumps([{"NetworkSettings": {"Ports": {"9000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "30000"}]}}}])
        return "id"
    monkeypatch.setattr("agent_trace_review.target_client._docker", docker)
    monkeypatch.setattr("agent_trace_review.target_client.TargetClient.health", lambda *a, **kw: {"status": "ready"})
    target = TargetDefinition(id="test", health_path="/health", deployment={"image": IMAGE, "port": 9000, "service_token_variable": "SERVICE_TOKEN"})
    with deployed_target(target, resources=db, job_id="secret-test", service_token="generated-test-secret") as (_, record):
        assert "generated-test-secret" not in json.dumps(record)
    assert not captured[0].exists() and not db.pending_resources()
    assert b"generated-test-secret" not in store_bytes(db.store)


def store_bytes(store):
    return b"".join(p.read_bytes() for p in store.root.rglob("*") if p.is_file())


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    manager = AssessmentManager(Store(tmp_path / "data"), {})
    repositories = RepositoryManager(manager, enabled=True)
    def checkout(request, root, command):
        fixture_repository(root)
        subprocess.run(["git", "init", "--quiet", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.org", "commit", "-qm", "fixture"], check=True)
        return subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"]).decode().strip()
    def build(argv, **kwargs):
        assert argv[:2] == ["docker", "build"]
        assert not any("secret" in item for item in argv)
        Path(argv[argv.index("--iidfile") + 1]).write_text(IMAGE)
        return "fixture Docker build complete"
    def execute(db, job_id, target, suite, repository, **kwargs):
        assert target.deployment.image == IMAGE
        fixture = TargetDefinition(id=target.id, endpoint="http://fixture.invalid", demo=True)
        correct = suite.cases[0].profile.rules[-1].value
        return run_assessment(db, job_id, fixture, suite, repository,
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"output": {"answer": correct}})), **kwargs)
    monkeypatch.setattr("agent_trace_review.repository_jobs.checkout_repository", checkout)
    monkeypatch.setattr("agent_trace_review.repository_jobs.run_process", build)
    monkeypatch.setattr("agent_trace_review.repository_jobs.run_assessment", execute)
    monkeypatch.setattr("agent_trace_review.repository_jobs._docker", lambda *a: "")
    try:
        yield repositories
    finally:
        repositories.close()
        manager.close()


def test_pipeline_freezes_build_and_suite_preserves_bundle_and_cleans(pipeline):
    request = RepositoryAssessmentInput(repository_url=URL, generation=SuiteGenerationInput(cases=1))
    job = pipeline.db.create(request)
    result = pipeline.run(job["id"])
    assert result["state"] == "completed", result
    assert result["cleanup"] == "completed" and not (pipeline.work_root / job["id"]).exists()
    assert result["source_binding"] == "built_from_checkout"
    bundle = pipeline.bundle(job["id"])
    assessment = bundle["assessment"]["job"]
    assert assessment["results"][0]["outcome"] == "pass"
    assert assessment["commit"] == result["commit"] and assessment["build_provenance"]["image_id"] == IMAGE
    assert bundle["logs"][0]["text"] == "fixture Docker build complete"


def test_pipeline_source_plan_is_frozen_exported_and_scans_once(pipeline, monkeypatch):
    import agent_trace_review.repository_jobs as jobs

    original = jobs.inspect_repository
    calls = []

    def inspect(*args):
        calls.append(args)
        return original(*args)

    def unexpected(*args):
        raise AssertionError("immutable source should not be scanned twice")

    monkeypatch.setattr(jobs, "inspect_repository", inspect)
    monkeypatch.setattr("agent_trace_review.assessments.inspect_repository", unexpected)
    request = RepositoryAssessmentInput(repository_url=URL, planning={"cases": 2, "concurrency": 2})
    job = pipeline.db.create(request)
    result = pipeline.run(job["id"])
    assert result["state"] == "completed", result
    bundle = pipeline.bundle(job["id"])
    assert len(calls) == 1
    assert bundle["assessment_plan"]["suite"] == bundle["assessment"]["suite"]
    assert bundle["assessment_plan"]["source_hash"] == bundle["assessment"]["job"]["source_hash"]
    assert bundle["assessment"]["job"]["concurrency"] == 2


def test_pipeline_build_failure_keeps_logs_and_no_assessment(pipeline, monkeypatch):
    def fail(*args, **kwargs):
        raise RepositoryError("command_failed", "compiler failure fixture")
    monkeypatch.setattr("agent_trace_review.repository_jobs.run_process", fail)
    job = pipeline.db.create(RepositoryAssessmentInput(repository_url=URL))
    result = pipeline.run(job["id"])
    assert result["state"] == "failed" and result["failed_stage"] == "building"
    assert result["assessment_id"] is None and result["cleanup"] == "completed"
    assert "compiler failure" in pipeline.bundle(job["id"])["logs"][0]["text"]


def test_api_positive_pipeline_returns_result_and_download(pipeline, tmp_path):
    with TestClient(create_app(str(tmp_path / "api"), enable_repository_builds=True)) as client:
        response = client.post("/api/repository-jobs", headers=HEADERS,
            json={"repository_url": URL, "generation": {"cases": 1}})
        assert response.status_code == 202, response.text
        job_id = response.json()["id"]
        for _ in range(200):
            result = client.get(f"/api/repository-jobs/{job_id}").json()
            if result["state"] not in {"queued", "running"}:
                break
            time.sleep(0.01)
        assert result["state"] == "completed", result
        exported = client.get(f"/api/repository-jobs/{job_id}/export")
        assert exported.status_code == 200
        assert exported.json()["assessment"]["job"]["results"][0]["outcome"] == "pass"
        assert client.get("/api/repository-jobs").json()[0]["id"] == job_id


def test_cancel_before_run_does_not_fetch_or_build(pipeline, monkeypatch):
    def unexpected(*a, **kw):
        raise AssertionError("cancelled task executed")
    monkeypatch.setattr("agent_trace_review.repository_jobs.checkout_repository", unexpected)
    job = pipeline.db.create(RepositoryAssessmentInput(repository_url=URL))
    pipeline.db.cancel(job["id"])
    result = pipeline.run(job["id"])
    assert result["state"] == "cancelled" and result["cleanup"] == "completed"


def test_repository_restart_cleans_workspace_and_does_not_replay(tmp_path):
    first = AssessmentManager(Store(tmp_path), {})
    repositories = RepositoryManager(first, enabled=True)
    job = repositories.db.create(RepositoryAssessmentInput(repository_url=URL))
    repositories.db.claim(job["id"])
    work = repositories.work_root / job["id"]
    work.mkdir()
    (work / "partial").write_text("temporary source")
    first.close()
    second = AssessmentManager(Store(tmp_path), {})
    reopened = RepositoryManager(second, enabled=True)
    try:
        result = reopened.db.get(job["id"])
        assert result["state"] == "interrupted" and result["cleanup"] == "completed"
        assert not work.exists() and result["assessment_id"] is None
    finally:
        reopened.close()
        second.close()


def test_api_disabled_by_default_auth_bounds_and_env_allowlist(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_REVIEW_SERVICE_TOKEN", "fixture-service-token")
    headers = {**HEADERS, "Authorization": "Bearer fixture-service-token"}
    with TestClient(create_app(str(tmp_path / "disabled"))) as client:
        assert client.get("/api/repository-builds").status_code == 401
        assert not client.get("/api/repository-builds", headers=headers).json()["enabled"]
        assert client.post("/api/repository-jobs", headers=headers, json={"repository_url": URL}).status_code == 422
        assert client.post("/api/repository-jobs", headers=headers, content=b"x" * (1024 * 1024 + 1)).status_code == 413
    with TestClient(create_app(str(tmp_path / "enabled"), enable_repository_builds=True, repository_environment=("ALLOWED_KEY",))) as client:
        assert client.get("/api/repository-builds", headers=headers).json()["enabled"]
        assert client.post("/api/repository-jobs", headers=headers, json={"repository_url": URL, "environment": {"KEY": "AGENT_REVIEW_SERVICE_TOKEN"}}).status_code == 422
        assert client.post("/api/repository-jobs", headers={"Authorization": headers["Authorization"]}, json={}).status_code == 403
        assert client.get("/api/repository-jobs", headers=headers).json() == []
        assert "repository_manifest" in client.get("/api/schema", headers=headers).json()


def test_cli_invalid_source_fails_before_any_work(tmp_path):
    result = CliRunner().invoke(app, ["assess-repo", "file:///private", "--data-dir", str(tmp_path / "not-created")])
    assert result.exit_code != 0 and not (tmp_path / "not-created").exists()


def test_second_process_cannot_recover_or_cancel_active_jobs(tmp_path):
    manager = AssessmentManager(Store(tmp_path), {})
    job = manager.db.create_job({"sentinel": True})
    manager.db.claim(job["id"])
    try:
        with pytest.raises(ValueError, match="已有评审进程"):
            AssessmentManager(Store(tmp_path), {})
        assert manager.db.job(job["id"])["state"] == "running"
    finally:
        manager.close()
    next_manager = AssessmentManager(Store(tmp_path), {})
    next_manager.close()
