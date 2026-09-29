"""Explicitly synthetic demonstration data, matching native OpenCode export shapes."""

from copy import deepcopy
from enum import Enum

from .util import digest

BASE = 1789185600000
TASK = {
    "id": "session-expiry",
    "version": "1",
    "prompt": "修复会话过期判断：刚好达到过期时间时应拒绝会话。补充边界测试并运行 pytest。",
    "base_commit": "demo-base-7e92c1",
    "environment_hash": "demo-python312",
    "suite_hash": "demo-auth-v1",
    "budget_policy": "900s-2usd",
    "final_state_hash": "demo-final-correct",
    "checks": [{"id": "auth-tests", "kind": "test", "command": "pytest tests/test_session.py -q"}],
    "protected_paths": ["config/secrets.*"],
}
DIFF = """diff --git a/auth/session.py b/auth/session.py
--- a/auth/session.py
+++ b/auth/session.py
@@ -12,3 +12,3 @@
 def is_valid(session, now):
-    return now <= session.expires_at
+    return now < session.expires_at
 
"""


class DemoScenario(str, Enum):
    focused = "focused"
    iterative = "iterative"
    failed = "failed"
    all = "all"


def demo_bundles() -> list[dict]:
    return _demo_bundles(("focused", "iterative"))


def scenario_bundles(scenario: DemoScenario | None = None) -> list[dict]:
    if scenario is None:
        return demo_bundles()
    scenario = DemoScenario(scenario)
    labels = ("focused", "iterative", "failed") if scenario == DemoScenario.all else (scenario.value,)
    return _demo_bundles(labels)


def _demo_bundles(labels: tuple[str, ...]) -> list[dict]:
    result = []
    for label in labels:
        slow = label == "iterative"
        failed = label == "failed"
        task = deepcopy(TASK)
        if failed:
            task["final_state_hash"] = "demo-final-incorrect"
        replacement = "return now < session.expires_at" + (" + 1" if failed else "")
        sid = "ses_" + digest(label)[:24]
        uid = "msg_" + digest(label + "user")[:24]
        messages = [
            {
                "info": {
                    "id": uid,
                    "sessionID": sid,
                    "role": "user",
                    "time": {"created": BASE},
                    "agent": "build",
                    "model": {"providerID": "demo", "modelID": "coding-model"},
                },
                "parts": [
                    {
                        "id": "prt_" + digest(uid)[:24],
                        "sessionID": sid,
                        "messageID": uid,
                        "type": "text",
                        "text": TASK["prompt"],
                    }
                ],
            }
        ]
        seq = 0

        def message(tool, args, output, seconds, exit_code=None):
            nonlocal seq
            seq += 1
            mid = "msg_" + digest(label + str(seq))[:24]
            t = BASE + seconds * 1000
            tokens = {
                "total": 4200,
                "input": 2000,
                "output": 600,
                "reasoning": 300,
                "cache": {"read": 1300, "write": 0},
            }
            info = {
                "id": mid,
                "sessionID": sid,
                "parentID": uid,
                "role": "assistant",
                "agent": "build",
                "mode": "build",
                "modelID": "coding-model",
                "providerID": "demo",
                "path": {"cwd": "/demo/auth-service", "root": "/demo/auth-service"},
                "time": {"created": t, "completed": t + 1800},
                "cost": 0.012,
                "tokens": tokens,
                "finish": "tool-calls",
            }
            pid = "prt_" + digest(mid)[:24]
            metadata = {"exit": exit_code} if exit_code is not None else {}
            messages.append(
                {
                    "info": info,
                    "parts": [
                        {
                            "id": pid,
                            "sessionID": sid,
                            "messageID": mid,
                            "type": "tool",
                            "tool": tool,
                            "callID": "call_" + pid,
                            "state": {
                                "status": "completed",
                                "input": args,
                                "output": output,
                                "title": tool,
                                "metadata": metadata,
                                "time": {"start": t + 100, "end": t + 1700},
                            },
                        },
                        {
                            "id": "prt_" + digest(pid + "usage")[:24],
                            "sessionID": sid,
                            "messageID": mid,
                            "type": "step-finish",
                            "reason": "tool-calls",
                            "tokens": tokens,
                            "cost": 0.012,
                        },
                    ],
                }
            )

        message(
            "read",
            {"filePath": "/demo/auth-service/auth/session.py"},
            "def is_valid(session, now):\n    return now <= session.expires_at",
            4,
        )
        if slow:
            message(
                "read",
                {"filePath": "/demo/auth-service/auth/session.py"},
                "def is_valid(session, now):\n    return now <= session.expires_at",
                8,
            )
            message(
                "grep",
                {"pattern": "expires_at", "path": "/demo/auth-service"},
                "auth/session.py:13: return now <= session.expires_at",
                12,
            )
            message(
                "grep",
                {"pattern": "expires_at", "path": "/demo/auth-service"},
                "auth/session.py:13: return now <= session.expires_at",
                18,
            )
        message(
            "bash",
            {"command": "pytest tests/test_session.py -q"},
            "FAILED tests/test_session.py::test_exact_expiry - AssertionError\n1 failed, 7 passed",
            24,
            1,
        )
        if slow:
            for i in range(2):
                message(
                    "edit",
                    {
                        "filePath": "/demo/auth-service/auth/api.py",
                        "oldString": f"offset = {i}",
                        "newString": f"offset = {i + 1}",
                    },
                    "Edit applied.",
                    40 + i * 35,
                )
                message(
                    "bash",
                    {"command": "pytest tests/test_session.py -q"},
                    "FAILED tests/test_session.py::test_exact_expiry - AssertionError\n1 failed, 7 passed",
                    50 + i * 35,
                    1,
                )
        message(
            "edit",
            {
                "filePath": "/demo/auth-service/auth/session.py",
                "oldString": "return now <= session.expires_at",
                "newString": replacement,
            },
            "Edit applied.",
            110 if slow else 35,
        )
        if not slow:
            message(
                "bash",
                {"command": "pytest tests/test_session.py -q"},
                (
                    "FAILED tests/test_session.py::test_exact_expiry - AssertionError\n1 failed, 7 passed"
                    if failed
                    else "8 passed in 0.31s"
                ),
                44,
                1 if failed else 0,
            )
        mid = "msg_" + digest(label + "final")[:24]
        t = BASE + (140 if slow else 51) * 1000
        messages.append(
            {
                "info": {
                    "id": mid,
                    "sessionID": sid,
                    "parentID": uid,
                    "role": "assistant",
                    "agent": "build",
                    "mode": "build",
                    "modelID": "coding-model",
                    "providerID": "demo",
                    "path": {"cwd": "/demo/auth-service", "root": "/demo/auth-service"},
                    "time": {"created": t, "completed": t + 1200},
                    "finish": "stop",
                    "cost": 0.002,
                    "tokens": {
                        "total": 1200,
                        "input": 1000,
                        "output": 200,
                        "reasoning": 0,
                        "cache": {"read": 0, "write": 0},
                    },
                },
                "parts": [
                    {
                        "id": "prt_" + digest(mid)[:24],
                        "sessionID": sid,
                        "messageID": mid,
                        "type": "text",
                        "text": (
                            "修改后边界测试仍失败，尚未完成修复。"
                            if failed
                            else "已修复过期时间的边界判断。"
                            + ("所有 8 项测试通过。" if not slow else "修改已完成。")
                        ),
                    }
                ],
            }
        )
        native = {
            "info": {
                "id": sid,
                "title": "会话过期边界 · " + ("修复失败" if failed else "反复定位" if slow else "聚焦修复"),
                "slug": label,
                "projectID": "global",
                "directory": "/demo/auth-service",
                "version": "1.18.18",
                "time": {"created": BASE, "updated": t + 1200},
            },
            "messages": messages,
        }
        reports = []
        if not slow:
            reports.append(
                {
                    "id": "demo-external-failed" if failed else "demo-external",
                    "check_id": "auth-tests",
                    "kind": "test",
                    "phase": "final",
                    "provenance": "external_verifier",
                    "state_hash": task["final_state_hash"],
                    "suite_hash": task["suite_hash"],
                    "result": "fail" if failed else "pass",
                    "executed": 8,
                    "passed": 7 if failed else 8,
                    "failed": 1 if failed else 0,
                    "skipped": 0,
                    "cases": {
                        f"test_session::test_{i}": "fail" if failed and i == 0 else "pass" for i in range(8)
                    },
                }
            )
        result.append(
            {
                "bundle_version": "1",
                "export": native,
                "task": task,
                "diff": DIFF.replace("+    return now < session.expires_at", "+    " + replacement),
                "verifications": reports,
                "agent_version": "1.18.18",
                "demo": True,
            }
        )
    return result
