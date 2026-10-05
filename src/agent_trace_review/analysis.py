import fnmatch
import re
from collections import defaultdict

from .diffing import diff_files
from .models import VERSION, Evaluation, Evidence, Finding, Metric, Run
from .opencode import READ_TOOLS, WRITE_TOOLS
from .util import digest, now, number


def analyze(run: Run) -> Evaluation:
    evidence = list(run.evidence)
    findings: list[Finding] = []
    tools = [e for e in run.events if e.kind == "tool"]
    coding = run.source_format == "opencode"
    edits = [
        e
        for e in run.events
        if coding and (e.kind == "patch" or e.tool in WRITE_TOOLS and e.status == "completed")
    ]
    edit_ids = {e.id for e in edits}
    corpus = digest(run.model_dump(exclude={"imported_at"}))
    event_map = {e.id: e for e in run.events}

    def finding(
        category,
        title,
        explanation,
        refs,
        severity="warning",
        verdict="supported",
        counter=None,
        limitations=None,
        recommendation="",
    ):
        fid = "finding_" + digest([category, refs])[:16]
        findings.append(
            Finding(
                id=fid,
                category=category,
                severity=severity,
                verdict=verdict,
                title=title,
                explanation=explanation,
                evidence_ids=refs,
                counter_evidence_ids=counter or [],
                limitations=limitations or [],
                recommendation=recommendation,
            )
        )

    duplicate_refs = []
    seen = {}
    epoch = 0
    for event in run.events:
        readonly = event.tool in READ_TOOLS if coding else event.effect == "read"
        if event.id in edit_ids or event.kind == "tool" and not readonly:
            epoch += 1
        if event.kind != "tool" or not readonly or event.status != "completed" or event.data.get("truncated"):
            continue
        key = digest([event.tool, event.input, event.output, event.data.get("cwd"), epoch])
        if key in seen:
            duplicate_refs.append(event.evidence_id)
            previous = seen[key]
            finding(
                "duplicate_readonly_call",
                "重复的只读调用",
                f"{event.tool} 使用了相同参数，返回内容也相同；两次调用之间没有观察到写入或其他工具操作。",
                [previous.evidence_id, event.evidence_id],
                severity="info",
                limitations=["相同结果不一定代表无用；外部文件变化可能未被记录。"],
                recommendation="检查是否可以复用已有结果；输出截断、分页和文件变化时仍应重新读取。",
            )
        else:
            seen[key] = event

    if not coding:
        failures_by_call = defaultdict(list)
        for event in tools:
            key = digest([event.tool, event.input])
            if event.status == "error" and not event.data.get("truncated"):
                failed_events = failures_by_call[key]
                failed_events.append(event)
                same = [e for e in failed_events if e.output == event.output]
                if len(same) == 3:
                    finding(
                        "repeated_tool_failure",
                        "相同工具调用反复失败",
                        f"{event.tool} 相同输入和失败输出出现 3 次。",
                        [e.evidence_id for e in same],
                        limitations=["重复失败可能来自外部暂时故障，不能单独证明 agent 策略错误。"],
                        recommendation="检查错误原因和重试策略，必要时改变输入或执行路径。",
                    )
            elif event.status == "completed" and failures_by_call.get(key):
                previous = failures_by_call.pop(key)[-1]
                finding(
                    "tool_recovery",
                    "观察到工具调用恢复",
                    f"{event.tool} 相同输入先失败，随后完成。",
                    [previous.evidence_id, event.evidence_id],
                    severity="info",
                    limitations=["调用完成不等同于业务结果正确。"],
                )

    groups = defaultdict(list)
    failures = {}
    observed = [v for v in run.verifications if v.provenance == "observed_tool"]
    for verification in observed:
        event = event_map[verification.event_id]
        command = (verification.command, event.data.get("cwd"))
        if verification.result == "fail":
            signature = "\n".join(
                re.findall(
                    r"^.*(?:FAILED |AssertionError|TypeError|ReferenceError|Error:).*$", event.output, re.M
                )
            )
            if not signature:
                signature = event.output.strip()
            key = (command, digest(signature))
            groups[key].append(event)
            failures[command] = event
            group = groups[key]
            if len(group) == 3 and any(group[0].seq < edit.seq < event.seq for edit in edits):
                finding(
                    "repeated_failure_cycle",
                    "修改后仍重复出现相同失败",
                    "同一命令的相同失败签名出现了 3 次，其间存在代码修改。",
                    [e.evidence_id for e in group]
                    + [e.evidence_id for e in edits if group[0].seq < e.seq < event.seq],
                    recommendation="对照失败签名重新检查定位，确认修改是否触及失败路径。",
                    limitations=["这是相同输出/失败签名的观测，不能证明每次修改都没有局部进展。"],
                )
        elif verification.result == "pass":
            if command in failures:
                prior = failures.pop(command)
                finding(
                    "verified_recovery",
                    "观察到检查从失败恢复",
                    f"相同目录、相同命令先失败，随后以退出码 0 完成：{verification.command}",
                    [prior.evidence_id, event.evidence_id],
                    severity="info",
                    limitations=["工具记录不是独立验收；测试集合是否相同、其他行为是否回归仍需外部报告。"],
                )
            for key in list(groups):
                if key[0] == command:
                    del groups[key]

    external = [v for v in run.verifications if v.provenance == "external_verifier"]
    required = [c for c in run.task.checks if c.required] if run.task else []
    final_checks = {}
    for check in required:
        records = [v for v in external if v.check_id == check.id and v.phase == "final"]
        # Ambiguous contradictory records are not resolved by arbitrary upload order.
        if len(records) == 1:
            record = records[0]
            matched = (
                bool(run.task.final_state_hash)
                and record.state_hash == run.task.final_state_hash
                and bool(run.task.suite_hash)
                and record.suite_hash == run.task.suite_hash
                and record.kind == check.kind
            )
            if matched:
                final_checks[check.id] = record

    if edits:
        last_edit = max(edits, key=lambda e: e.seq)
        later = [
            v
            for v in observed
            if event_map[v.event_id].seq > last_edit.seq
            and v.result in {"pass", "fail"}
            and (not required or v.check_id in {c.id for c in required})
            and not (
                last_edit.end_ms is not None
                and event_map[v.event_id].start_ms is not None
                and event_map[v.event_id].start_ms < last_edit.end_ms
            )
        ]
        verified_final = required and all(
            c.id in final_checks and final_checks[c.id].result == "pass" for c in required
        )
        if not later and not verified_final:
            ref = "absence_final_verification"
            evidence.append(
                Evidence(
                    id=ref,
                    kind="absence",
                    description="最后一次可见修改之后没有可识别的验证事件",
                    query={
                        "after_event": last_edit.id,
                        "kind": "verification",
                        "matching_count": 0,
                        "corpus_hash": corpus,
                        "coverage": run.coverage["verification"],
                        "version": VERSION,
                    },
                )
            )
            finding(
                "final_change_unverified",
                "最后一次修改后未观察到验证",
                "轨迹中最后一次可见修改之后，没有可识别的检查结果，也没有匹配最终状态的完整独立验收。",
                [last_edit.evidence_id, ref],
                verdict="hypothesis",
                counter=[v.evidence_ids[0] for v in observed if v.result == "pass"],
                limitations=["导出日志无法覆盖任意脚本、外部操作和未导入的子会话。"],
                recommendation="为最终代码状态补充指定验收项和机器可读报告。",
            )

    final_paths = diff_files(run.diff)
    if run.task:
        for path in final_paths:
            protected = any(fnmatch.fnmatchcase(path, p) for p in run.task.protected_paths)
            outside = run.task.allowed_paths and not any(
                fnmatch.fnmatchcase(path, p) for p in run.task.allowed_paths
            )
            if protected or outside:
                finding(
                    "explicit_constraint_violation",
                    "最终变更违反路径约束",
                    f"{path} {'位于禁止修改范围' if protected else '不在允许修改范围'}。",
                    ["task", "final-diff"],
                    severity="high",
                    recommendation="核对任务约束，撤销或调整越界修改。",
                )

    regressions = []
    incomplete_checks = set()
    for final in final_checks.values():
        bases = [
            v
            for v in external
            if v.phase == "baseline"
            and v.check_id == final.check_id
            and v.suite_hash == final.suite_hash
            and v.kind == final.kind
            and v.state_hash
            and v.state_hash == (run.task.initial_state_hash or run.task.base_commit)
        ]
        if len(bases) != 1:
            continue
        base = bases[0]
        missing = [
            key
            for key, state in base.cases.items()
            if key not in final.cases or state != "skipped" and final.cases[key] == "skipped"
        ]
        if missing:
            incomplete_checks.add(final.check_id)
        regressed = [
            key
            for key, state in base.cases.items()
            if state == "pass" and final.cases.get(key) in {"fail", "error"}
        ]
        regressions.extend(regressed)
        if regressed or missing:
            finding(
                "test_regression" if regressed else "missing_tests",
                "发现测试回归" if regressed else "最终报告缺少原有测试",
                "、".join((regressed or missing)[:8]),
                base.evidence_ids + final.evidence_ids,
                severity="high" if regressed else "warning",
                recommendation="核对固定测试集合，检查回归或未执行用例。",
            )

    hard_violation = any(f.category == "explicit_constraint_violation" for f in findings)
    failed = [v for v in final_checks.values() if v.result == "fail"]
    passed = [
        v
        for v in final_checks.values()
        if v.result == "pass"
        and v.check_id not in incomplete_checks
        and (v.kind != "test" or v.executed is not None and v.executed > 0)
    ]
    if failed or hard_violation or regressions:
        outcome, reason = "fail", "存在匹配验收条件的失败、测试回归或明确路径约束违反。"
    elif (
        required
        and len(passed) == len(required)
        and (run.diff_provided or not (run.task.protected_paths or run.task.allowed_paths))
    ):
        outcome, reason = "pass", "所有必需验收项的外部报告通过，且最终状态和验收集合标识匹配。"
    else:
        outcome, reason = (
            "inconclusive",
            "缺少完整、匹配最终状态与验收集合的独立验收材料。工具检查结果单独展示。",
        )
    if not run.task:
        reason = "未提供任务验收定义。可评估可见行为，但不能据此确认任务完成。"

    metrics = []

    def metric(key, label, value, refs=None, unit="", status="observed", note=""):
        metrics.append(
            Metric(
                key=key,
                label=label,
                value=value,
                unit=unit,
                status="unknown" if value is None and status != "not_applicable" else status,
                evidence_ids=list(dict.fromkeys(refs or ["source"])),
                note=note,
            )
        )

    elapsed = run.end_ms - run.start_ms if run.start_ms is not None and run.end_ms is not None else None
    metric(
        "duration_ms",
        "运行跨度",
        elapsed if elapsed is not None and elapsed >= 0 else None,
        unit="ms",
        note="首个可见消息到最后回复完成；可能包含用户等待时间。",
    )
    refs = [e.evidence_id for e in tools]
    call_status = "partial" if not coding and not run.trace_complete else "observed"
    metric("tool_calls", "工具调用", len(tools), refs, status=call_status,
           note="可见调用数量；部分轨迹不能证明未调用其他工具。")
    llms = [e for e in run.events if e.kind == "llm"]
    metric("llm_calls", "模型调用", len(llms) if llms or run.trace_complete else None,
           [e.evidence_id for e in llms], status=call_status)
    for key, label, calls in (("llm_duration_ms", "模型调用耗时合计", llms),
                              ("tool_duration_ms", "工具调用耗时合计", tools)):
        durations = [e.data.get("duration_ms") if e.data.get("duration_ms") is not None
                     else e.end_ms - e.start_ms if e.start_ms is not None and e.end_ms is not None
                     else None for e in calls]
        known = [v for v in durations if v is not None]
        metric(key, label, sum(known) if known else 0 if run.trace_complete and not calls else None,
               [e.evidence_id for e in calls], unit="ms",
               status="partial" if len(known) != len(calls) else call_status,
               note="逐次调用耗时之和；并发调用可能重叠，不等于任务总耗时。")
    metric(
        "errors",
        "工具/模型错误",
        sum(e.status == "error" for e in tools)
        + sum(e.kind == "error" or e.kind == "llm" and e.status == "error" for e in run.events),
        refs,
    )
    metric(
        "retries",
        "重试事件",
        sum(e.kind == "retry" for e in run.events),
        [e.evidence_id for e in run.events if e.kind == "retry"],
    )
    metric(
        "files_read",
        "可见读取文件",
        len({f for e in tools if e.tool == "read" for f in e.files}),
        [e.evidence_id for e in tools if e.tool == "read"],
        status="partial",
        note="仅原生 read，不包含 shell/子会话内部读取。",
    )
    metric(
        "files_changed",
        "最终变更文件" if run.diff else "可见修改文件",
        len(final_paths) if run.diff else len({f for e in edits for f in e.files}),
        ["final-diff"] if run.diff else [e.evidence_id for e in edits],
        status="observed" if run.diff else "partial",
    )
    metric("duplicate_calls", "重复只读调用", len(duplicate_refs), duplicate_refs, status="derived")
    metric("observed_checks", "可见检查", len(observed), [ref for v in observed for ref in v.evidence_ids])
    metric(
        "observed_checks_passed",
        "通过的可见检查",
        sum(v.result == "pass" for v in observed),
        [ref for v in observed for ref in v.evidence_ids],
        note="工具输出，不代表独立验收通过。",
    )
    metric(
        "acceptance_pass_rate",
        "必需验收通过率",
        round(len(passed) / len(required) * 100, 1) if required else None,
        [ref for v in final_checks.values() for ref in v.evidence_ids] or ["task"]
        if run.task
        else ["source"],
        unit="%",
        status="derived",
        note=f"{len(passed)} / {len(required)} 项；未知项保留在分母中。",
    )
    for key, label, accessor, unit in [
        ("tokens_total", "已报告 Tokens", lambda u: u.get("tokens", {}).get("total"), "tokens"),
        ("tokens_input", "输入 Tokens", lambda u: u.get("tokens", {}).get("input"), "tokens"),
        ("tokens_output", "输出 Tokens", lambda u: u.get("tokens", {}).get("output"), "tokens"),
        ("tokens_reasoning", "推理 Tokens", lambda u: u.get("tokens", {}).get("reasoning"), "tokens"),
        (
            "tokens_cache_read",
            "缓存读取 Tokens",
            lambda u: u.get("tokens", {}).get("cache_read", u.get("tokens", {}).get("cache", {}).get("read")),
            "tokens",
        ),
        (
            "tokens_cache_write",
            "缓存写入 Tokens",
            lambda u: u.get("tokens", {}).get("cache_write", u.get("tokens", {}).get("cache", {}).get("write")),
            "tokens",
        ),
        ("tokens_cache_miss", "缓存未命中 Tokens", lambda u: u.get("tokens", {}).get("cache_miss"), "tokens"),
        ("cost_usd", "报告成本", lambda u: u.get("cost"), "USD"),
    ]:
        summary_key = key if key == "cost_usd" else key.removeprefix("tokens_") + "_tokens"
        if run.usage_summary and summary_key in run.usage_summary.fields:
            field = run.usage_summary.fields[summary_key]
            metric(key, label, field.value, ["usage-summary"], unit=unit,
                   status="observed" if field.status == "complete" else field.status,
                   note=f"{run.usage_summary.provenance} / {field.source}；{field.reason}")
            continue
        values = [number(accessor(u)) for u in run.usage]
        available = [v for v in values if v is not None]
        metric(
            key,
            label,
            round(sum(available), 8) if available else None,
            [u["evidence_id"] for u in run.usage],
            unit=unit,
            status="partial" if len(available) != len(values) or not coding and not run.trace_complete
            or any(u.get("usage_complete") is False for u in run.usage)
            else "observed",
            note=f"{run.framework} 报告值；不把缺失用量填为 0，不包含未导入子会话。"
            + (" 模型价格估算不等同实际账单。" if key == "cost_usd" else ""),
        )
    metric(
        "diff_added",
        "新增行",
        sum(line.startswith("+") and not line.startswith("+++") for line in run.diff.splitlines())
        if run.diff
        else None,
        ["final-diff"] if run.diff else [],
        unit="lines",
    )
    metric(
        "diff_deleted",
        "删除行",
        sum(line.startswith("-") and not line.startswith("---") for line in run.diff.splitlines())
        if run.diff
        else None,
        ["final-diff"] if run.diff else [],
        unit="lines",
    )
    finished = [e for e in tools if e.status in {"completed", "error"}]
    metric(
        "tool_success_rate",
        "已结束工具调用成功率",
        round(sum(e.status == "completed" for e in finished) / len(finished) * 100, 1) if finished else None,
        [e.evidence_id for e in finished],
        unit="%",
        status="observed" if len(finished) == len(tools) else "partial",
        note="仅已结束调用；执行成功不证明业务结果正确。",
    )
    if not coding:
        metrics = [
            m
            for m in metrics
            if m.key
            not in {
                "files_read",
                "files_changed",
                "diff_added",
                "diff_deleted",
                "acceptance_pass_rate",
                "observed_checks",
                "observed_checks_passed",
            }
        ]
        if not run.trace_complete:
            for item in metrics:
                if item.value is not None and item.status != "unknown" and "usage-summary" not in item.evidence_ids:
                    item.status = "partial"
    return Evaluation(
        id="eval_" + digest([corpus, VERSION])[:20],
        run_id=run.id,
        created_at=now(),
        corpus_hash=corpus,
        outcome=outcome,
        outcome_reason=reason,
        metrics=metrics,
        findings=findings,
        evidence=evidence,
        verifications=run.verifications,
    )


def compare(left: Run, right: Run, a: Evaluation, b: Evaluation) -> dict:
    issues = []
    if (a.custom or {}).get("profile_hash") != (b.custom or {}).get("profile_hash"):
        issues.append({"field": "profile_hash", "reason": "自定义评估规则不同或只在一侧提供"})
    if left.source_format != right.source_format:
        issues.append({"field": "source_format", "reason": "轨迹格式和采集语义不同"})
    if (
        not left.trace_complete
        and left.source_format == "generic"
        or not right.trace_complete
        and right.source_format == "generic"
    ):
        issues.append({"field": "coverage", "reason": "通用轨迹不完整，仅比较可见部分"})
    fields = (
        "id",
        "version",
        "prompt",
        "base_commit",
        "environment_hash",
        "suite_hash",
        "budget_policy",
    )
    if left.source_format == right.source_format == "generic":
        fields = tuple("initial_state_hash" if field == "base_commit" else field for field in fields)
    for field in fields:
        x = getattr(left.task, field, None)
        y = getattr(right.task, field, None)
        if not x or not y:
            issues.append({"field": field, "reason": "缺少比较条件", "left": x, "right": y})
        elif x != y:
            issues.append({"field": field, "reason": "比较条件不同", "left": x, "right": y})
    if (
        left.task
        and right.task
        and [c.model_dump() for c in left.task.checks] != [c.model_dump() for c in right.task.checks]
    ):
        issues.append({"field": "checks", "reason": "验收定义不同"})
    if left.task and right.task:
        for field in ("initial_state_hash", "allowed_paths", "protected_paths"):
            if getattr(left.task, field) != getattr(right.task, field):
                issues.append({"field": field, "reason": "初始状态或路径约束不同"})
    if left.demo != right.demo:
        issues.append({"field": "demo", "reason": "合成示例和真实运行不能作为对等实验"})
    if a.version != b.version:
        issues.append({"field": "evaluation_version", "reason": "评估规则版本不同"})
    amap, bmap = {m.key: m for m in a.metrics}, {m.key: m for m in b.metrics}
    differences = []
    for key in amap.keys() & bmap.keys():
        av, bv = amap[key].value, bmap[key].value
        differences.append(
            {
                "key": key,
                "label": amap[key].label,
                "left": av,
                "right": bv,
                "delta": round(bv - av, 8)
                if isinstance(av, (int, float)) and isinstance(bv, (int, float))
                else None,
                "unit": amap[key].unit,
                "left_status": amap[key].status,
                "right_status": bmap[key].status,
            }
        )
    descriptions = []
    for category in sorted({f.category for f in a.findings + b.findings}):
        la, lb = (
            [f for f in a.findings if f.category == category],
            [f for f in b.findings if f.category == category],
        )
        if len(la) != len(lb):
            descriptions.append(
                {
                    "category": category,
                    "title": (la or lb)[0].title,
                    "left_count": len(la),
                    "right_count": len(lb),
                    "left_finding_ids": [f.id for f in la],
                    "right_finding_ids": [f.id for f in lb],
                }
            )
    return {
        "left_id": left.id,
        "right_id": right.id,
        "left_revision": a.id,
        "right_revision": b.id,
        "comparable": not issues,
        "issues": issues,
        "differences": sorted(differences, key=lambda d: d["key"]),
        "behavior_differences": descriptions,
        "left_outcome": a.outcome,
        "right_outcome": b.outcome,
        "conclusion": "实验条件匹配；这是两次运行的个案比较，不能推出总体排名。"
        if not issues
        else "比较条件不完整或不一致，仅作描述性并排查看，不判定胜负。",
    }
