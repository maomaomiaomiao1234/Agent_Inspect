"""Declarative task rules and a data-only external evaluator protocol.

Profiles never contain executable commands. External programs run separately;
only explicitly submitted results bound to this exact request are imported.
"""

import math
import re
from typing import Any

from .analysis import analyze
from .contracts import EvaluatorResponse, TaskProfile
from .models import VERSION, Evidence, Finding, Metric, Run
from .storage import Store
from .util import canonical, digest, now, redact

MISSING = object()
PROTOCOL = "agent-review/evaluator-v1"


def pointer(document: Any, path: str):
    if not path.startswith("/") or re.search(r"~(?![01])", path):
        raise ValueError(f"无效 JSON Pointer：{path}")
    current = document
    for token in path[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            current = current.get(token, MISSING)
        elif isinstance(current, list) and re.fullmatch(r"0|[1-9][0-9]*", token):
            current = current[int(token)] if len(token) < 10 and int(token) < len(current) else MISSING
        else:
            return MISSING
        if current is MISSING:
            return MISSING
    return current


def load_profile(value) -> TaskProfile:
    raw = value.model_dump() if isinstance(value, TaskProfile) else value
    canonical(raw)
    return TaskProfile.model_validate(redact(raw))


def context_for(run: Run, base) -> dict:
    context = {
        "run_id": run.id,
        "source_hash": run.source_hash,
        "framework": run.framework,
        "task_prompt": run.task_prompt,
        "events": [e.model_dump() for e in run.events],
        "artifacts": run.artifacts,
        "metrics": {m.key: m.value for m in base.metrics},
        "metric_status": {m.key: m.status for m in base.metrics},
        "trace_complete": run.trace_complete,
        "demo": run.demo,
    }
    if run.output_present:
        context["output"] = run.output
    elif run.source_format == "opencode":
        replies = [e for e in run.events if e.kind == "message" and e.role == "assistant"]
        if replies:
            last = replies[-1].message_id
            context["output"] = "\n".join(e.output for e in replies if e.message_id == last)
    return context


def evaluator_request(run: Run, profile) -> dict:
    profile = load_profile(profile)
    context = context_for(run, analyze(run))
    return {
        "protocol": PROTOCOL,
        "run_id": run.id,
        "input_hash": digest(context),
        "profile_hash": digest(profile.model_dump()),
        "context": context,
        "checks": [r.model_dump() for r in profile.rules if r.op == "external"],
    }


def is_redacted(value) -> bool:
    return "[redacted:" in canonical(value)


def json_equal(left, right):
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(json_equal(left[k], right[k]) for k in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(json_equal(a, b) for a, b in zip(left, right))
    return left == right


def comparison_preview(rule, context):
    """Bounded, redacted display only; verdicts always use the full evaluator context."""
    if rule.op in {"external", "tool_required", "tool_forbidden"}:
        return None

    def preview(value):
        if value is MISSING:
            return "（字段缺失）"
        text = canonical(redact(value))
        return text if len(text) <= 600 else text[:600] + "…（已截断，完整值见证据）"

    return {"path": rule.path, "operator": rule.op,
            "expected": "字段存在" if rule.op == "exists" else preview(rule.value),
            "actual": preview(pointer(context, rule.path))}


def rule_result(rule, context):
    """Return status, explanation and pointers into the evaluator context."""
    if rule.op in {"tool_required", "tool_forbidden"}:
        matches = [
            i for i, e in enumerate(context["events"]) if e["kind"] == "tool" and e["tool"] == rule.value
        ]
        if not matches and not context["trace_complete"]:
            return "unknown", "轨迹不完整，无法根据缺失记录确认工具未调用。", ["/events"]
        passed = bool(matches) == (rule.op == "tool_required")
        return (
            "pass" if passed else "fail",
            f"观察到 {len(matches)} 次 {rule.value} 调用。",
            ([f"/events/{i}" for i in matches] or ["/events"]),
        )
    actual = pointer(context, rule.path)
    root = rule.path.split("/")[1].replace("~1", "/").replace("~0", "~")
    if root not in context:
        return "unknown", "缺少此规则所需的输入材料。", []
    if rule.path.startswith("/metrics/"):
        key = rule.path.split("/")[2].replace("~1", "/").replace("~0", "~")
        status = context["metric_status"].get(key)
        if (status == "partial" and (key.startswith("tokens_") or key == "cost_usd")
                and rule.op == "max" and isinstance(actual, (int, float)) and actual > rule.value):
            return "fail", "已观测用量下界已超过预算，缺失调用不会减少此消耗。", [rule.path]
        if status == "not_applicable":
            return "unknown", "此指标不适用；离线校准不能验证真实模型预算。", ["/metrics"]
        if status in {None, "unknown", "partial"}:
            return "unknown", "指标缺失或仅部分可见，无法确认满足阈值。", ["/metrics"]
    if actual is not MISSING and (is_redacted(actual) or is_redacted(rule.value)):
        return "unknown", "比较内容经过脱敏，不能据此判断相等或满足条件。", [rule.path]
    if rule.op == "exists":
        exists = actual is not MISSING
        return (
            "pass" if exists else "fail",
            "字段存在。" if exists else "在提供的材料中没有此字段。",
            ([rule.path] if exists else ["/" + rule.path[1:].split("/")[0]]),
        )
    if actual is MISSING:
        return "unknown", "缺少此规则所需字段；如字段必须存在，请增加 exists 规则。", ["/" + root]
    refs = [rule.path]
    if rule.op == "equals":
        # JSON booleans are not numbers, while 1 and 1.0 are numerically equal.
        passed = json_equal(actual, rule.value)
    elif rule.op == "contains":
        if isinstance(actual, str) and isinstance(rule.value, str):
            passed = rule.value in actual
        elif isinstance(actual, list):
            passed = any(json_equal(item, rule.value) for item in actual)
        elif isinstance(actual, dict) and isinstance(rule.value, str):
            passed = rule.value in actual
        else:
            return "unknown", "contains 需要字符串、数组或对象及兼容的 value。", refs
    else:
        if rule.op.startswith("length_"):
            if not isinstance(actual, (str, list, dict)):
                return "unknown", "长度规则需要字符串、数组或对象。", refs
            actual = len(actual)
        if isinstance(actual, bool) or not isinstance(actual, (int, float)) or not math.isfinite(actual):
            return "unknown", "数值规则需要有限数字。", refs
        passed = actual >= rule.value if rule.op in {"min", "length_min"} else actual <= rule.value
    return (
        "pass" if passed else "fail",
        f"{rule.path} {'满足' if passed else '不满足'} {rule.op} 条件。",
        refs,
    )


def evaluate_profile(store: Store, run: Run, profile, results=None, *, persist=True):
    profile = load_profile(profile)
    base = analyze(run)
    request = evaluator_request(run, profile)
    context = request["context"]
    external = {}
    response = None
    if results is not None:
        canonical(results)
        response = EvaluatorResponse.model_validate(redact(results))
        for key in ("protocol", "run_id", "input_hash", "profile_hash"):
            if getattr(response, key) != request[key]:
                raise ValueError(f"外部结果 {key} 不匹配当前运行/规则；请重新生成 evaluator-request。")
        allowed = {r.id for r in profile.rules if r.op == "external"}
        for result in response.results:
            if result.id not in allowed:
                raise ValueError(f"外部结果引用了未声明的 external 规则：{result.id}")
            if result.status != "unknown" and not result.evidence_paths:
                raise ValueError("外部 pass/fail 结果必须引用至少一处请求上下文证据。")
            for path in result.evidence_paths:
                if pointer(context, path) is MISSING:
                    raise ValueError(f"外部结果引用了不存在的证据：{path}")
            external[result.id] = result
    # Validate everything before saving an evaluation revision.
    entries = []
    for rule in profile.rules:
        if rule.op == "external":
            record = external.get(rule.id)
            status, explanation, paths = (
                (record.status, record.explanation, record.evidence_paths)
                if record
                else ("unknown", "尚未提交此自定义评估器的结果。", [])
            )
        else:
            status, explanation, paths = rule_result(rule, context)
        entries.append((rule, status, explanation, paths))
    profile_artifact = store.put_artifact(canonical(profile.model_dump()).encode())
    context_artifact = store.put_artifact(canonical(context).encode())
    base.evidence.append(
        Evidence(
            id="profile",
            kind="artifact",
            artifact_id=profile_artifact,
            description="用户明确提供的任务评估规则",
        )
    )
    base.evidence.append(
        Evidence(
            id="evaluation-context",
            kind="artifact",
            artifact_id=context_artifact,
            description="评估输入快照；包含轨迹、产物与指标",
        )
    )
    if response:
        artifact = store.put_artifact(canonical(response.model_dump()).encode())
        base.evidence.append(
            Evidence(
                id="external-results",
                kind="artifact",
                artifact_id=artifact,
                description="用户提交的自定义评估器结果；哈希绑定不证明来源真实性",
            )
        )
    checks = []
    for index, (rule, status, explanation, paths) in enumerate(entries):
        refs = ["profile"]
        for path in paths:
            ref = "custom_" + digest(path)[:16]
            if not any(e.id == ref for e in base.evidence):
                base.evidence.append(
                    Evidence(
                        id=ref,
                        kind="artifact",
                        artifact_id=context_artifact,
                        pointer=path,
                        description=f"自定义规则输入 {path}",
                    )
                )
            refs.append(ref)
        if rule.op == "external" and response:
            refs.append("external-results")
        checks.append(
            {
                "id": rule.id,
                "description": rule.description,
                "dimension": rule.dimension,
                "required": rule.required,
                "status": status,
                "explanation": explanation,
                "evidence_ids": refs,
                "origin": "external" if rule.op == "external" else "declarative",
                "comparison": comparison_preview(rule, context),
            }
        )
        base.findings.append(
            Finding(
                id="profile_" + digest([request["profile_hash"], rule.id])[:16],
                category="custom_check",
                title=f"{rule.description or rule.id} · {status}",
                severity="warning" if status == "fail" else "info",
                verdict="insufficient_evidence" if status == "unknown" else "supported",
                explanation=explanation,
                evidence_ids=refs,
                origin="custom",
                limitations=["只覆盖用户定义的此项条件，不代表任务质量的全部维度。"]
                + (
                    ["结果由用户自定义评估器提交，未验证其程序执行或声明真实性。"]
                    if rule.id in external
                    else []
                ),
            )
        )
    required = [c for c in checks if c["required"]]
    outcome_checks = [c for c in required if c["dimension"] == "outcome"]
    legacy_required = run.task and (
        any(c.required for c in run.task.checks) or run.task.allowed_paths or run.task.protected_paths
    )
    if base.outcome == "fail" or any(c["status"] == "fail" for c in required):
        base.outcome, base.outcome_reason = "fail", "内置验收或用户定义的必需检查未通过。"
    elif (
        all(c["status"] == "pass" for c in required)
        and (base.outcome == "pass" or outcome_checks)
        and (not legacy_required or base.outcome == "pass")
    ):
        base.outcome, base.outcome_reason = "pass", "用户定义的必需检查均通过；结论仅覆盖声明的验收条件。"
    else:
        base.outcome, base.outcome_reason = (
            "inconclusive",
            "存在未知的必需检查，或缺少任务结果验收；行为/成本通过不代表任务完成。",
        )
    passed = sum(c["status"] == "pass" for c in required)
    base.metrics.append(
        Metric(
            key="custom_acceptance_rate",
            label="自定义必需检查通过率",
            value=round(passed / len(required) * 100, 1) if required else None,
            unit="%",
            status="derived" if required else "unknown",
            evidence_ids=["profile"],
            note=f"{passed}/{len(required)}；未知项保留在分母。",
        )
    )
    base.custom = {
        "profile": profile.model_dump(),
        "profile_hash": request["profile_hash"],
        "input_hash": request["input_hash"],
        "checks": checks,
        "results": response.model_dump() if response else None,
    }
    base.created_at = now()
    base.id = "eval_" + digest([base.corpus_hash, base.custom, VERSION, base.created_at])[:20]
    if persist:
        store.save_evaluation(base)
    return base
