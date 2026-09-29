"""Built-in candidate simulation with actual, fixed Docker verification.

Only the four packaged candidates are supported. This is not a runner for arbitrary
repositories or an attestation service for uploaded verification reports.
"""

import difflib
import json
import re
import subprocess
import time
import uuid
from enum import Enum
from importlib.resources import files

from .models import Verification
from .reports import junit_report
from .service import ingest
from .storage import Store
from .util import canonical, digest

IMAGE = "python:3.12-slim"
RUNNER_VERSION = "code-repair-fixture/1"
TIMEOUT_SECONDS = 10
CHECK_ID = "session-expiry"
EXPECTED_CASES = {
    "SessionExpiry::test_before_expiry",
    "SessionExpiry::test_at_expiry",
    "SessionExpiry::test_after_expiry",
}
# No host mounts, inherited environment, network, image pulls, or install commands.
CONSTRAINTS = [
    "--pull=never",
    "--network=none",
    "--read-only",
    "--user=65534:65534",
    "--cap-drop=ALL",
    "--security-opt=no-new-privileges",
    "--cpus=1",
    "--memory=128m",
    "--memory-swap=128m",
    "--pids-limit=32",
    "--no-healthcheck",
    "--log-driver=none",
]


class Candidate(str, Enum):
    correct = "correct"
    incorrect = "incorrect"
    regression = "regression"
    timeout = "timeout"
    all = "all"


class DockerError(RuntimeError):
    pass


def _docker(args, **kwargs):
    return subprocess.run(["docker", *args], capture_output=True, timeout=10, **kwargs)


def docker_environment():
    """Resolve the allowed local tag once; both phases use the immutable ID."""
    try:
        server = _docker(["version", "--format", "{{.Server.Version}}"])
        if server.returncode:
            raise DockerError(
                "Docker daemon 不可用，请先启动 Docker。" + server.stderr.decode(errors="replace")
            )
        image = _docker(["image", "inspect", IMAGE])
        if image.returncode:
            raise DockerError(
                f"本地缺少 {IMAGE}；请先显式执行 docker pull {IMAGE}。不会自动下载或在宿主机执行。"
            )
        info = json.loads(image.stdout)[0]
        image_id = info["Id"]
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise DockerError("镜像没有有效的不可变 image ID。")
        if info.get("Config", {}).get("Volumes"):
            raise DockerError("该镜像声明了额外 volume，不符合无挂载约束。")
        return {
            "image_id": image_id,
            "image_tag": IMAGE,
            "architecture": info["Architecture"],
            "os": info["Os"],
            "docker_server": server.stdout.decode().strip(),
            "constraints": CONSTRAINTS,
            "python_args": ["-I", "-B"],
            "runner_version": RUNNER_VERSION,
            "timeout_seconds": TIMEOUT_SECONDS,
        }
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, IndexError) as exc:
        raise DockerError(f"无法检查 Docker / 本地镜像：{exc}") from exc


def _cleanup(name):
    try:
        result = _docker(["rm", "--force", name])
        if result.returncode and "No such container" not in result.stderr.decode(errors="replace"):
            raise DockerError(result.stderr.decode(errors="replace"))
    except (OSError, subprocess.TimeoutExpired, DockerError) as exc:
        raise DockerError(
            f"容器清理失败：{name}；请检查并执行 docker rm --force {name}。原因：{exc}"
        ) from exc


def validate_result(stdout, exit_code, state_hash, suite_hash, phase):
    """Reject missing tests, skips and inconsistent container exit/JUnit results."""
    report = junit_report(stdout, CHECK_ID, state_hash, suite_hash, phase)
    if set(report["cases"]) != EXPECTED_CASES or report["executed"] != len(EXPECTED_CASES):
        raise ValueError("验证器未执行完整固定测试集合。")
    case_result = (
        "error"
        if "error" in report["cases"].values()
        else "fail"
        if "fail" in report["cases"].values()
        else "pass"
    )
    if report["result"] != case_result:
        raise ValueError("固定测试报告的汇总结果与完整用例集合不一致。")
    expected_exit = 0 if report["result"] == "pass" else 1
    if exit_code != expected_exit or report["result"] not in {"pass", "fail", "error"}:
        raise ValueError("容器退出码与 JUnit 报告不一致。")
    report["id"] = f"{phase}_{report['id']}"
    return report


def _run_stage(environment, source, verifier, phase):
    name = f"agent-review-repair-{uuid.uuid4().hex}-{phase}"
    state_hash = digest(source)
    suite_hash = digest(verifier.encode())
    started = time.time() * 1000
    clock = time.monotonic()
    audit = {
        "container": name,
        "image_id": environment["image_id"],
        "phase": phase,
        "state_hash": state_hash,
        "suite_hash": suite_hash,
        "exit_code": None,
        "cli_exit_code": None,
        "stdout": "",
        "stderr": "",
        "error": None,
        "timed_out": False,
        "junit": None,
        "start_ms": started,
    }
    report = None
    try:
        created = _docker(
            [
                "create",
                "--name",
                name,
                "--label",
                "agent-review.runner=code-repair-fixture",
                *CONSTRAINTS,
                "--interactive",
                "--entrypoint=python",
                environment["image_id"],
                "-I",
                "-B",
                "-c",
                verifier,
            ]
        )
        if created.returncode:
            raise DockerError("无法创建验证容器：" + created.stderr.decode(errors="replace"))
        result = subprocess.run(
            ["docker", "start", "--attach", "--interactive", name],
            input=canonical(source).encode(),
            capture_output=True,
            timeout=TIMEOUT_SECONDS,
        )
        audit.update(
            cli_exit_code=result.returncode,
            stdout=result.stdout.decode(errors="replace"),
            stderr=result.stderr.decode(errors="replace"),
        )
        inspected = _docker(["inspect", "--format", "{{json .State}}", name])
        if inspected.returncode:
            raise DockerError("无法读取验证容器的最终退出状态。")
        state = json.loads(inspected.stdout)
        audit["container_state"] = state
        audit["exit_code"] = state["ExitCode"]
        if state["Status"] != "exited" or state.get("OOMKilled") or state.get("Error"):
            raise DockerError("容器未正常完成，或发生内存/环境错误。")
        if result.returncode != state["ExitCode"]:
            raise DockerError("Docker CLI 与容器退出码不一致。")
        audit["junit"] = audit["stdout"]
        report = validate_result(result.stdout, state["ExitCode"], state_hash, suite_hash, phase)
    except subprocess.TimeoutExpired as exc:
        audit.update(
            timed_out=True,
            error="验证阶段超过宿主机时间限制。",
            stdout=(exc.stdout or b"").decode(errors="replace"),
            stderr=(exc.stderr or b"").decode(errors="replace"),
        )
    except (DockerError, OSError, ValueError, KeyError, TypeError) as exc:
        audit["error"] = str(exc)
    finally:
        # subprocess.run kills/reaps its client on timeout or Ctrl-C. The container
        # is separately removed here, including partially successful create calls.
        _cleanup(name)
    audit.update(
        duration_ms=round((time.monotonic() - clock) * 1000, 3), end_ms=time.time() * 1000, cleanup="removed"
    )
    if report is None:
        report = Verification(
            id=f"{phase}_{digest(audit)[:16]}",
            check_id=CHECK_ID,
            phase=phase,
            provenance="external_verifier",
            result="timeout" if audit["timed_out"] else "error",
            state_hash=state_hash,
            suite_hash=suite_hash,
            source_text=canonical(audit),
        ).model_dump()
    report["command"] = "python -I -B -c <fixed session-expiry verifier>"
    return report, audit


def candidate_source(candidate):
    candidate = Candidate(candidate)
    baseline = files("agent_trace_review").joinpath("fixtures/code_repair/session.py").read_text()
    replacements = {
        Candidate.correct: "return now < expires_at",
        Candidate.incorrect: "return now <= expires_at",
        Candidate.regression: "return False",
        Candidate.timeout: "while True:\n        pass",
    }
    if candidate == Candidate.all:
        raise ValueError("all 需要逐个运行候选。")
    return {"session.py": baseline}, {
        "session.py": baseline.replace("return now <= expires_at", replacements[candidate])
    }


def run_candidate(store: Store, candidate: Candidate):
    baseline, final = candidate_source(candidate)
    candidate = Candidate(candidate)
    environment = docker_environment()
    verifier = files("agent_trace_review").joinpath("fixtures/code_repair/verifier.py").read_text()
    base_report, base_audit = _run_stage(environment, baseline, verifier, "baseline")
    final_report, final_audit = _run_stage(environment, final, verifier, "final")
    # A controlled comparison requires a usable baseline. Preserve the actual
    # final execution in the audit, while reporting an orchestration error.
    usable_baseline = base_report["result"] in {"pass", "fail"}
    if not usable_baseline:
        final_report = Verification(
            id="final_baseline_unavailable",
            check_id=CHECK_ID,
            phase="final",
            provenance="external_verifier",
            result="error",
            state_hash=digest(final),
            suite_hash=digest(verifier.encode()),
            source_text=canonical(
                {
                    "error": "基线验证未完整完成，无法确认本次受控修复实验。",
                    "final_verification": final_report,
                }
            ),
        ).model_dump()
    patch = "".join(
        difflib.unified_diff(
            baseline["session.py"].splitlines(keepends=True),
            final["session.py"].splitlines(keepends=True),
            fromfile="a/session.py",
            tofile="b/session.py",
        )
    )
    bundle = {
        "bundle_version": "generic/2",
        "trace": {
            "trace_version": "1",
            "framework": "code-repair-fixture",
            "run_id": f"fixture-{candidate.value}-{uuid.uuid4().hex}",
            "title": f"代码修复 · {candidate.value}",
            "demo": True,
            "agent_version": "builtin-candidate/1",
            "coverage": "partial",
            "task_prompt": "修复会话过期判断：到达过期时间时应无效，保留过期前有效与过期后无效的行为。",
            "status": "completed"
            if final_report["result"] in {"pass", "fail"} and usable_baseline
            else "incomplete",
            "start_ms": base_audit["start_ms"],
            "end_ms": final_audit["end_ms"],
            "events": [],
            "output": {
                "candidate": candidate.value,
                "files": final,
                "note": "内置候选模拟；没有调用被测 Agent 或模型。",
            },
            "artifacts": {
                "source_description": "候选由内置模拟器生成；baseline/final 验证记录来自实际 Docker 执行。",
                "environment": environment,
                "baseline_files": baseline,
                "final_files": final,
                "verifier_source": verifier,
                "verification_runs": {"baseline": base_audit, "final": final_audit},
            },
        },
        "task": {
            "id": CHECK_ID,
            "version": "1",
            "prompt": "会话在 now < expires_at 时有效；固定三个边界测试全部通过。",
            "initial_state_hash": digest(baseline),
            "final_state_hash": digest(final),
            "environment_hash": digest(environment),
            "suite_hash": digest(verifier.encode()),
            "budget_policy": f"fixture-verification-{TIMEOUT_SECONDS}s-per-stage;no-model",
            "checks": [{"id": CHECK_ID, "kind": "test", "command": "fixed session-expiry verifier"}],
            "allowed_paths": ["session.py"],
        },
        "diff": patch,
        "verifications": [base_report, final_report],
    }
    return ingest(store, canonical(bundle).encode())
