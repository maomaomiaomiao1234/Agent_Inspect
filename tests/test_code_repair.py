import json
import subprocess
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from agent_trace_review import code_repair as repair
from agent_trace_review.cli import app
from agent_trace_review.util import digest

IMAGE_ID = "sha256:" + "a" * 64


def xml(failed=(), skipped=(), names=("before_expiry", "at_expiry", "after_expiry")):
    cases = "".join(
        f'<testcase classname="SessionExpiry" name="test_{name}">'
        + ("<failure/>" if name in failed else "<skipped/>" if name in skipped else "")
        + "</testcase>"
        for name in names
    )
    return f'<testsuite tests="{len(names)}">{cases}</testsuite>'.encode()


@pytest.fixture
def docker(monkeypatch):
    state = SimpleNamespace(
        calls=[],
        starts=0,
        final_xml=xml(),
        final_exit=0,
        interrupt=None,
        cleanup_error=False,
        inspect_error=False,
        create_error=False,
        baseline_error=False,
        daemon_error=False,
        image_error=False,
        oom=False,
    )

    def run(args, **kwargs):
        state.calls.append((args, kwargs))
        code, stdout, stderr = 0, b"", b""
        command = args[1]
        if command == "version":
            code, stdout = (1, b"") if state.daemon_error else (0, b"29.8.1")
        elif command == "image":
            code = 1 if state.image_error else 0
            stdout = json.dumps(
                [{"Id": IMAGE_ID, "Architecture": "arm64", "Os": "linux", "Config": {}}]
            ).encode()
        elif command == "create":
            code = 125 if state.create_error else 0
        elif command == "start":
            state.starts += 1
            if state.interrupt:
                raise state.interrupt
            code, stdout = (
                (1, xml(failed=("at_expiry",))) if state.starts == 1 else (state.final_exit, state.final_xml)
            )
        elif command == "inspect":
            code = 1 if state.inspect_error or state.baseline_error and state.starts == 1 else 0
            stdout = json.dumps(
                {
                    "Status": "exited",
                    "ExitCode": 1 if state.starts == 1 else state.final_exit,
                    "OOMKilled": state.oom,
                    "Error": "",
                }
            ).encode()
        elif command == "rm":
            code, stderr = (1, b"daemon unavailable") if state.cleanup_error else (0, b"")
        else:
            raise AssertionError(args)
        return subprocess.CompletedProcess(args, code, stdout, stderr)

    monkeypatch.setattr(repair.subprocess, "run", run)
    return state


@pytest.mark.parametrize(
    "candidate,outcome,failed",
    [
        ("correct", "pass", ()),
        ("incorrect", "fail", ("at_expiry",)),
        ("regression", "fail", ("before_expiry",)),
    ],
)
def test_runner_evidence_and_isolation(store, docker, candidate, outcome, failed):
    docker.final_xml, docker.final_exit = xml(failed=failed), int(bool(failed))
    run, evaluation, _ = repair.run_candidate(store, candidate)
    assert evaluation.outcome == outcome and run.demo and not run.usage
    assert bool([f for f in evaluation.findings if f.category == "test_regression"]) == (
        candidate == "regression"
    )
    artifacts = run.artifacts
    assert run.task.initial_state_hash == digest(artifacts["baseline_files"])
    assert run.task.final_state_hash == digest(artifacts["final_files"])
    assert run.task.suite_hash == digest(artifacts["verifier_source"].encode())
    assert len({v.id for v in run.verifications}) == 2
    assert run.diff_provided
    creates = [args for args, _ in docker.calls if args[1] == "create"]
    assert len(creates) == 2 and creates[0][3] != creates[1][3]
    for args in creates:
        for flag in repair.CONSTRAINTS:
            assert flag in args
        assert IMAGE_ID in args and repair.IMAGE not in args
        assert args[-4:-1] == ["-I", "-B", "-c"]
        assert not any(a.startswith(("--volume", "--mount", "--env", "-v=")) for a in args)
    starts = [kw for args, kw in docker.calls if args[1] == "start"]
    assert json.loads(starts[0]["input"]) == artifacts["baseline_files"]
    assert json.loads(starts[1]["input"]) == artifacts["final_files"]
    assert [args[-1] for args, _ in docker.calls if args[1] == "rm"] == [args[3] for args in creates]
    assert all(kw.get("timeout") for _, kw in docker.calls)
    assert not any(kw.get("shell") for _, kw in docker.calls)
    for phase in ["baseline", "final"]:
        audit = artifacts["verification_runs"][phase]
        assert audit["cleanup"] == "removed" and audit["junit"]
        assert audit["duration_ms"] >= 0 and audit["exit_code"] in {0, 1}


@pytest.mark.parametrize(
    "stdout,exit_code",
    [
        (xml(), 1),
        (xml(failed=("at_expiry",)), 0),
        (xml(), 125),
        (xml(names=("at_expiry",)), 0),
        (xml(names=()), 0),
        (xml(skipped=("at_expiry",)), 0),
        (b"not xml", 0),
    ],
)
def test_unusable_report_is_environment_error(store, docker, stdout, exit_code):
    docker.final_xml, docker.final_exit = stdout, exit_code
    run, evaluation, _ = repair.run_candidate(store, "correct")
    assert evaluation.outcome == "inconclusive"
    assert run.verifications[-1].result == "error"
    assert run.artifacts["verification_runs"]["final"]["error"]
    assert len([a for a, _ in docker.calls if a[1] == "rm"]) == 2


@pytest.mark.parametrize("failure", ["timeout", "interrupt", "oserror", "create", "inspect"])
def test_cleanup_on_every_exit(store, docker, failure):
    if failure == "timeout":
        docker.interrupt = subprocess.TimeoutExpired("docker", 10, output=b"partial", stderr=b"log")
    elif failure == "interrupt":
        docker.interrupt = KeyboardInterrupt()
    elif failure == "oserror":
        docker.interrupt = OSError("failed to start client")
    elif failure == "create":
        docker.create_error = True
    else:
        docker.inspect_error = True
    if failure == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            repair.run_candidate(store, "correct")
    else:
        run, evaluation, _ = repair.run_candidate(store, "correct")
        assert evaluation.outcome == "inconclusive"
        if failure == "timeout":
            assert run.verifications[0].result == "timeout"
            assert run.artifacts["verification_runs"]["baseline"]["stdout"] == "partial"
    created = [a[3] for a, _ in docker.calls if a[1] == "create"]
    removed = [a[-1] for a, _ in docker.calls if a[1] == "rm"]
    assert created == removed


def test_cleanup_failure_is_visible_and_never_saves_success(store, docker):
    docker.cleanup_error = True
    with pytest.raises(repair.DockerError, match="容器清理失败：agent-review-repair-"):
        repair.run_candidate(store, "correct")
    assert store.list_runs() == []


def test_baseline_error_prevents_final_pass(store, docker):
    docker.baseline_error = True
    run, evaluation, _ = repair.run_candidate(store, "correct")
    assert evaluation.outcome == "inconclusive"
    assert run.task.final_state_hash == digest(run.artifacts["final_files"])
    assert run.artifacts["verification_runs"]["final"]["exit_code"] == 0
    assert run.verifications[-1].result == "error"


def test_missing_docker_does_not_fallback_or_download(store, docker):
    docker.daemon_error = True
    with pytest.raises(repair.DockerError, match="daemon"):
        repair.run_candidate(store, "correct")
    assert len(docker.calls) == 1 and not store.list_runs()


def test_missing_image_never_downloads(store, docker):
    docker.image_error = True
    with pytest.raises(repair.DockerError, match="本地缺少"):
        repair.run_candidate(store, "correct")
    assert [a[1] for a, _ in docker.calls] == ["version", "image"]
    assert not store.list_runs()


def test_oom_is_environment_error(store, docker):
    docker.oom = True
    run, evaluation, _ = repair.run_candidate(store, "correct")
    assert evaluation.outcome == "inconclusive"
    assert run.verifications[0].result == "error"


def test_suite_failure_with_passing_cases_is_not_candidate_failure():
    with pytest.raises(ValueError, match="汇总结果"):
        repair.validate_result(
            xml().replace(b"<testsuite ", b'<testsuite failures="1" '), 1, "state", "suite", "final"
        )


def test_only_named_candidates_allowed(store, docker):
    for candidate in ["sh -c id", "/some/repository", "all"]:
        with pytest.raises(ValueError):
            repair.run_candidate(store, candidate)
    assert not docker.calls


def test_cli_uses_fixture_runner(tmp_path, docker):
    result = CliRunner().invoke(app, ["code-repair", "--candidate", "correct", "--data-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["outcome"] == "pass"
    result = CliRunner().invoke(app, ["code-repair", "--candidate", "arbitrary"])
    assert result.exit_code != 0
