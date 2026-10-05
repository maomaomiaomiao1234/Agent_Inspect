"""Descriptive fixed-suite coverage and repeatability, without population score claims."""

import math
from collections import Counter

from .general_suite import CATEGORY_LABELS


def result_guidance(result):
    """Separate execution faults, unmet requirements and absent verification evidence."""
    if result["execution_state"] != "completed":
        state = result["execution_state"]
        reason, action = {
            "timeout": ("目标未在期限内完成", "检查目标服务延迟和多轮耗时，必要时增加期限后重测。"),
            "budget_exhausted": ("输出 Token 预算耗尽", "检查已采集的输出用量，调整预算或缩短回答后重测。"),
            "cancelled": ("本次执行已取消", "重新运行未完成案例后再确认结果。"),
        }.get(state, ("目标调用异常", "检查目标服务、协议返回和运行记录，恢复后重测。"))
        return {"kind": "execution", "reason": reason, "next_step": action}
    failed = [c for c in result["checks"] if c["required"] and c["status"] == "fail"]
    unknown = [c for c in result["checks"] if c["required"] and c["status"] == "unknown"]
    if result["outcome"] == "fail":
        return {"kind": "acceptance", "reason": f"{len(failed)} 项必需检查未通过" if failed else "任务验收未通过",
                "next_step": "对照期望值与实际返回定位问题；修复后使用相同题集重测。"}
    if result["outcome"] != "pass":
        return {"kind": "evidence", "reason": f"{len(unknown)} 项必需检查缺少证据" if unknown else "缺少任务结果验收",
                "next_step": "补充缺失字段、工具轨迹或独立评估器结果；证据不足不能确认通过。"}
    return {"kind": "passed", "reason": "本案例的必需检查均通过",
            "next_step": "可增加重复次数和新种子，检查稳定性与未覆盖场景。"}


def outcome_summary(rows, suite):
    counts = Counter(r["outcome"] for r in rows)
    planned = len(suite.cases) * len(suite.budgets) * suite.attempts
    pending = planned - len(rows)
    conclusion = "fail" if counts["fail"] else "pass" if counts["pass"] == planned else "inconclusive"
    headline = {"fail": "发现未通过的必需检查", "pass": "本题集的必需检查全部通过",
                "inconclusive": "尚不能确认本题集全部通过"}[conclusion]
    categories = {c.category for c in suite.cases}
    actions = []
    if counts["fail"]:
        actions.append("先查看未通过案例的期望/实际对照，修复后用同一题集验证。")
    if any(r["execution_state"] != "completed" for r in rows):
        actions.append("排查执行异常或取消的案例；调用未完成不直接判定能力失败。")
    if any(r["execution_state"] == "completed" and r["outcome"] == "inconclusive" for r in rows):
        actions.append("补齐必需检查所需证据，再确认任务是否通过。")
    if pending:
        actions.append(f"还有 {pending} 次计划执行未取得结果；等待完成或重新运行中断任务。")
    if suite.attempts < 2:
        actions.append("当前每个案例只测一次；增加重复次数后再判断稳定性。")
    if not actions:
        actions.append("使用新种子和业务专项题集扩充验收范围。")
    return {"conclusion": conclusion, "headline": headline, "planned": planned, "observed": len(rows),
            "pass": counts["pass"], "fail": counts["fail"], "inconclusive": counts["inconclusive"],
            "pending": pending,
            "execution_issues": sum(r["execution_state"] != "completed" for r in rows),
            "covered_categories": [{"id": c, "label": CATEGORY_LABELS.get(c, c)} for c in sorted(categories)],
            "uncovered_categories": [{"id": c, "label": label} for c, label in CATEGORY_LABELS.items()
                                     if c not in categories],
            "next_steps": actions,
            "scope_note": "结论只覆盖本题集的输入与必需检查；未测维度不计为失败，也不代表已具备对应能力。"}


def quality_summary(rows, suite, repository):
    dimensions, stability = [], []
    for budget in suite.budgets:
        for category in sorted({c.category for c in suite.cases}):
            cases = {c.id for c in suite.cases if c.category == category}
            selected = [r for r in rows if r["budget_id"] == budget.id and r["case_id"] in cases]
            counts = Counter(r["outcome"] for r in selected)
            planned = len(cases) * suite.attempts
            durations = sorted(r["duration_ms"] for r in selected)
            dimensions.append({
                "category": category, "label": CATEGORY_LABELS.get(category, category),
                "budget_id": budget.id, "cases": len(cases), "planned": planned,
                "observed": len(selected), "pass": counts["pass"], "fail": counts["fail"],
                "unknown": planned - counts["pass"] - counts["fail"],
                "confirmed_pass_rate": round(100 * counts["pass"] / planned, 2),
                "possible_pass_rate": round(100 * (planned - counts["fail"]) / planned, 2),
                "p50_duration_ms": durations[math.ceil(len(durations) * .5) - 1] if durations else None,
                "p95_duration_ms": durations[math.ceil(len(durations) * .95) - 1] if durations else None,
                "execution_errors": sum(r["execution_state"] not in {"completed", "cancelled"} for r in selected),
            })
        for case in suite.cases:
            selected = [r for r in rows if r["budget_id"] == budget.id and r["case_id"] == case.id]
            outcomes = Counter(r["outcome"] for r in selected)
            status = "insufficient_repeats"
            if suite.attempts >= 2:
                status = "incomplete"
                if outcomes["pass"] and outcomes["fail"]:
                    status = "variable"
                elif len(selected) == suite.attempts and not outcomes["inconclusive"]:
                    status = "consistent_pass" if outcomes["pass"] else "consistent_fail"
            stability.append({"case_id": case.id, "budget_id": budget.id, "status": status,
                              "observed": len(selected), "planned": suite.attempts,
                              "outcomes": dict(outcomes), "run_ids": [r["run_id"] for r in selected]})
    required = [check for r in rows for check in r["checks"] if check["required"]]
    telemetry = Counter(r.get("telemetry", {}).get("coverage", "unavailable") for r in rows)
    token_known = sum(r["usage"]["total_tokens"] is not None for r in rows)
    token_statuses = [r["usage"].get("fields", {}).get("total_tokens", {}).get("status", "unknown") for r in rows]
    linked_claims = {claim for c in suite.cases for claim in c.claim_ids}
    gaps = []
    for claim in (repository or {}).get("claims", []):
        if claim["id"] not in linked_claims:
            gaps.append({"kind": "claim", "id": claim["id"], "name": claim["capability"],
                         "path": claim["path"], "line": claim["line"], "status": "untested"})
    # A rule gives planned coverage; only an observed passing tool rule supports actual use.
    rules = {(c.id, r.id): r for c in suite.cases for r in c.profile.rules if r.op == "tool_required"}
    for tool in (repository or {}).get("tools", []):
        planned = {(cid, rid) for (cid, rid), rule in rules.items() if rule.value == tool["name"]}
        observed = any((row["case_id"], check["id"]) in planned and check["status"] == "pass"
                       for row in rows for check in row["checks"])
        gaps.append({"kind": "tool", "name": tool["name"], "path": tool["path"], "line": tool["line"],
                     "status": "observed_for_cases" if observed else "not_observed" if planned else "untested"})
    return {
        "summary": outcome_summary(rows, suite),
        "dimensions": dimensions, "stability": stability, "source_coverage": gaps,
        "evidence": {"planned_runs": len(suite.cases) * suite.attempts * len(suite.budgets),
                     "observed_runs": len(rows), "required_checks": len(required),
                     "unknown_required_checks": sum(c["status"] == "unknown" for c in required),
                     "telemetry_complete_runs": telemetry["complete"], "telemetry_partial_runs": telemetry["partial"],
                     "telemetry_unavailable_runs": telemetry["unavailable"], "token_known_runs": token_known,
                     "token_partial_runs": token_statuses.count("partial"),
                     "token_not_applicable_runs": token_statuses.count("not_applicable")},
        "limitations": ["通过率范围是固定题集中未知项的最好/最坏情况，不是统计置信区间或总体能力分数。",
                        "重复稳定性按同一案例和预算分别计算；少于两次不能判断稳定性。",
                        "p50/p95 使用已观测案例耗时的 nearest-rank；小样本分位数不能代表生产延迟。",
                        "静态候选、规则关联和自报调用均不能证明源码因果关系或工具的全部能力。"],
    }
