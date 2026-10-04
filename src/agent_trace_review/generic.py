"""Import data only; no task/profile/plugin code is executed."""

from .contracts import GenericBundle, GenericTrace
from .diffing import diff_files
from .models import VERSION, Event, Evidence, Run, Task
from .storage import Store
from .util import canonical, digest, now, redact


def import_generic(
    store: Store,
    payload: dict,
    filename="trace.json",
    task=None,
    agent_version=None,
    diff=None,
    first_message=None,
    last_message=None,
):
    bundle_version = payload.get("bundle_version", "generic/1")
    records = []
    final_diff = None
    if first_message or last_message or (diff is not None and bundle_version != "generic/2"):
        raise ValueError("通用轨迹不使用 OpenCode 消息区间；独立 diff 参数仅适用于 generic/2 运行包。")
    if "bundle_version" in payload:
        if bundle_version == "generic/2":
            bundle = GenericBundle.model_validate(redact(payload))
            records = bundle.verifications
            for record in records:
                record.evidence_ids, record.event_id = [], None
            final_diff = redact(diff) if diff is not None else bundle.diff
            if not isinstance(final_diff, (str, type(None))):
                raise ValueError("diff 必须为统一补丁文本。")
            if final_diff is not None and len(final_diff.encode()) > 20 * 1024 * 1024:
                raise ValueError("diff 超过 20 MB。")
            if final_diff and final_diff.strip() and not diff_files(final_diff):
                raise ValueError("无法识别最终 diff 中的文件路径；请提供标准 git diff 或 unified diff。")
        elif bundle_version != "generic/1" or set(payload) - {"bundle_version", "trace", "task"}:
            raise ValueError("不支持的通用运行包。")
        task = task if task is not None else payload.get("task")
        payload = payload.get("trace")
    # Canonical serialization rejects NaN / Infinity even inside untyped JSON fields.
    canonical(payload)
    raw = redact(payload)
    trace = GenericTrace.model_validate(raw)
    if agent_version:
        raw["agent_version"] = agent_version
    manifest = Task.model_validate(redact(task)) if task is not None else None
    if manifest and len({c.id for c in manifest.checks}) != len(manifest.checks):
        raise ValueError("任务验收 check.id 必须唯一。")
    source = store.put_artifact(canonical(raw).encode())
    version = agent_version or trace.agent_version
    identity = [source, manifest.model_dump() if manifest else None, version, VERSION]
    if bundle_version == "generic/2":
        identity += [bundle_version, final_diff, [v.model_dump() for v in records]]
    run_id = "run_" + digest(identity)[:20]
    try:
        return store.get_run(run_id), False
    except KeyError:
        pass
    run = Run(
        id=run_id,
        title=trace.title or filename,
        session_id=trace.run_id,
        imported_at=now(),
        source_hash=source,
        source_artifact=source,
        adapter_version=bundle_version,
        diff=final_diff or "",
        diff_provided=final_diff is not None,
        source_format="generic",
        framework=trace.framework,
        agent_version=version,
        task=manifest,
        task_prompt=trace.task_prompt,
        models=list(dict.fromkeys(trace.models + [e.model for e in trace.events if e.model])),
        execution_status=trace.status,
        start_ms=trace.start_ms,
        end_ms=trace.end_ms,
        output=trace.output,
        output_present="output" in raw,
        artifacts=trace.artifacts,
        trace_complete=trace.coverage == "complete",
        demo=trace.demo,
    )
    run.evidence.append(
        Evidence(
            id="source",
            kind="artifact",
            artifact_id=source,
            pointer="",
            description=f"{trace.framework} 通用轨迹；由提供方声明覆盖范围",
        )
    )
    if manifest:
        artifact = store.put_artifact(canonical(manifest.model_dump()).encode())
        run.evidence.append(
            Evidence(id="task", kind="artifact", artifact_id=artifact, description="用户提供的任务验收定义")
        )
    if run.diff_provided:
        run.diff_artifact = store.put_artifact(run.diff.encode(), "text/x-diff")
        run.evidence.append(
            Evidence(
                id="final-diff",
                kind="artifact",
                artifact_id=run.diff_artifact,
                description="导入的最终 diff；导入器不认证代码状态或报告真实性",
            )
        )
    for index, record in enumerate(records):
        artifact = store.put_artifact(canonical(record.model_dump()).encode())
        ref = f"report_{index}"
        record.evidence_ids = [ref]
        run.evidence.append(
            Evidence(
                id=ref,
                kind="artifact",
                artifact_id=artifact,
                description=f"外部验证报告 · {record.check_id or record.id} · {record.phase}",
            )
        )
        executions = trace.artifacts.get("verification_runs")
        if isinstance(executions, dict) and record.phase in executions:
            execution_ref = f"{ref}_execution"
            record.evidence_ids.append(execution_ref)
            run.evidence.append(
                Evidence(
                    id=execution_ref,
                    kind="artifact",
                    artifact_id=source,
                    pointer=f"/artifacts/verification_runs/{record.phase}",
                    description=f"验证执行记录 · {record.phase} · 日志、退出码与耗时（提供方记录）",
                )
            )
        run.verifications.append(record)
    for index, item in enumerate(trace.events):
        ref = f"event_{index}"
        pointer = f"/events/{index}"
        event = Event(
            id=f"event_{index}",
            seq=index,
            native_id=item.id,
            message_id=item.id,
            kind=item.kind,
            title=item.title or item.tool or item.kind,
            role=item.role,
            tool=item.tool,
            effect=item.effect,
            status=item.status,
            input=item.input,
            output=item.output if isinstance(item.output, str) else canonical(item.output),
            start_ms=item.start_ms,
            end_ms=item.end_ms,
            time_basis="native" if item.start_ms is not None else "unknown",
            source_pointer=pointer,
            evidence_id=ref,
            parent_message_id=item.parent_id,
            call_id=item.id if item.kind in {"llm", "tool"} else None,
            data={
                "truncated": item.truncated or raw["events"][index] != payload["events"][index],
                "model": item.model,
                "duration_ms": item.duration_ms,
                "usage": item.usage.model_dump() if item.usage else None,
                "provenance": item.provenance,
                "context": item.context,
            },
        )
        run.events.append(event)
        run.evidence.append(
            Evidence(
                id=ref,
                kind="event",
                event_id=event.id,
                artifact_id=source,
                pointer=pointer,
                description=event.title,
            )
        )
        if item.kind == "llm":
            run.usage.append(
                {
                    "scope_id": item.id,
                    "evidence_id": ref,
                    "tokens": item.usage.tokens.model_dump() if item.usage else {},
                    "cost": item.usage.cost_usd if item.usage else None,
                }
            )
    run.coverage = {
        "tools": {
            "level": "observed" if run.trace_complete else "partial",
            "reason": "覆盖范围由导出器声明；工具名称不自动推断为只读。",
        },
        "usage": {
            "level": "observed" if run.usage else "unavailable",
            "reason": "逐次 llm 调用用量；未提供的字段保持未知，不从工具层重复累计。",
        },
        "verification": {
            "level": "partial" if records else "unavailable",
            "reason": (
                "提供了外部验证报告，按代码状态和测试集合标识匹配；不认证报告真实性。"
                if records
                else "源轨迹未包含独立验收报告；Profile 和自定义检查结果见所选评估版本。"
            ),
        },
        "output": {
            "level": "observed" if run.output_present else "unavailable",
            "reason": "导出器提供的最终结果；不自动读取其中引用的路径或 URL。",
        },
    }
    run.warnings.append("仅分析提供的轨迹和内嵌产物；不推断未导入子任务，不执行任何记录命令。")
    return run, store.save_run(run)
