"""OpenCode export importer. Pinned contract: OpenCode 1.18.18 SessionV1.WithParts.

Unknown parts are retained. Session exports are snapshots, not complete audit logs.
"""

import difflib
import json
import re
import shlex
from typing import Any

from .diffing import diff_files
from .models import VERSION, Event, Evidence, Run, Task, Verification
from .storage import Store
from .util import canonical, digest, now, redact, relative_path, timestamp

MAX_IMPORT = 100 * 1024 * 1024
READ_TOOLS = {"read", "grep", "glob", "list"}
WRITE_TOOLS = {"edit", "write", "apply_patch", "patch", "multiedit"}
KNOWN_PARTS = {
    "text",
    "reasoning",
    "file",
    "tool",
    "step-start",
    "step-finish",
    "snapshot",
    "patch",
    "retry",
    "compaction",
    "subtask",
    "agent",
}


def command_kind(command: str) -> str | None:
    args = simple_command(command)
    if not args:
        return None
    if args[:2] == ["uv", "run"]:
        args = args[2:]
    if args and args[0] == "npx":
        args = args[1:]
    if not args:
        return None
    executable = args[0].rsplit("/", 1)[-1]
    if executable.startswith("python") and args[1:2] == ["-m"]:
        args = args[2:]
        executable = args[0] if args else ""
    if executable in {"pytest", "vitest", "jest", "unittest"}:
        return "test"
    if executable == "eslint" or executable == "ruff" and args[1:2] == ["check"]:
        return "lint"
    if executable in {"tsc", "mypy", "pyright"}:
        return "typecheck"
    if executable in {"npm", "pnpm", "yarn", "bun", "cargo", "go", "swift"}:
        rest = args[1:]
        if rest[:1] == ["run"]:
            rest = rest[1:]
        action = rest[0].split(":")[0] if rest else ""
        if action in {"test", "build", "lint", "typecheck"}:
            return action
    if executable == "xcodebuild":
        return "build"
    return None


def simple_command(command: str) -> list[str]:
    """Only a single shell command has an unambiguous process exit status."""
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        args = list(lexer)
    except ValueError:
        return []
    if (
        any(token and all(char in ";|&()< >" for char in token) for token in args)
        or "\n" in command
        or "`" in command
        or "$(" in command
    ):
        return []
    while args and re.match(r"^[A-Za-z_]\w*=", args[0]):
        args = args[1:]
    return args


def mapping(value, label="字段") -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{label} 必须为对象。")
    return value


def counts_from_output(output: str) -> dict:
    counts = {}
    # A reported count is observational evidence, not an independent verifier.
    for word in ("passed", "failed", "skipped"):
        matches = re.findall(rf"\b(\d+)\s+{word}\b", output)
        if matches:
            counts[word] = int(matches[-1])
    if counts:
        counts["executed"] = counts.get("passed", 0) + counts.get("failed", 0)
    return counts


def _text(value: Any) -> str:
    return value if isinstance(value, str) else canonical(value) if value is not None else ""


def _path_list(part: dict, directory: str) -> list[str]:
    state = part.get("state", {})
    args = state.get("input", {})
    paths = []
    if part.get("tool") in {"read", "edit", "write", "multiedit"}:
        paths.append(args.get("filePath", args.get("path")))
        for edit in args.get("edits", []) if isinstance(args.get("edits"), list) else []:
            if isinstance(edit, dict):
                paths.append(edit.get("filePath"))
    if part.get("type") == "patch":
        value = part.get("files", [])
        if not isinstance(value, list):
            raise ValueError("patch.files 必须为数组。")
        paths.extend(value)
    patch = args.get("patchText", args.get("patch", ""))
    if isinstance(patch, str):
        paths.extend(re.findall(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$", patch, re.M))
    return sorted({p for raw in paths if (p := relative_path(raw, directory))})


def _patch(part: dict) -> str:
    state = part.get("state", {})
    metadata = state.get("metadata", {}) or {}
    if isinstance(metadata.get("diff"), str):
        return metadata["diff"]
    args = state.get("input", {})
    if (
        part.get("tool") == "edit"
        and isinstance(args.get("oldString"), str)
        and isinstance(args.get("newString"), str)
    ):
        path = str(args.get("filePath", "file"))
        return "\n".join(
            difflib.unified_diff(
                args["oldString"].splitlines(),
                args["newString"].splitlines(),
                fromfile="a/" + path,
                tofile="b/" + path,
                lineterm="",
            )
        )
    return str(args.get("patchText", ""))


def import_data(
    store: Store,
    content: bytes,
    filename="export.json",
    task: dict | None = None,
    diff: str | None = None,
    agent_version: str | None = None,
    first_message: str | None = None,
    last_message: str | None = None,
) -> tuple[Run, bool]:
    if len(content) > MAX_IMPORT:
        raise ValueError("文件超过 100 MB，请先按会话或任务拆分。")
    try:
        payload = json.loads(content)
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise ValueError("无法读取 JSON。请使用 opencode export <sessionID> 导出的完整 JSON 文件。") from exc
    if not isinstance(payload, dict):
        raise ValueError("导出文件必须是 JSON 对象。")
    bundle = payload if "export" in payload and "bundle_version" in payload else {}
    if bundle and bundle["bundle_version"] != "1":
        raise ValueError("不支持此运行包版本。")
    native = bundle.get("export", payload)
    if (
        not isinstance(native, dict)
        or not isinstance(native.get("info"), dict)
        or not isinstance(native.get("messages"), list)
    ):
        raise ValueError("缺少 OpenCode export 的 info / messages 字段；不支持将聊天文本当作轨迹。")
    if not isinstance(native["info"].get("id"), str) or not native["info"]["id"]:
        raise ValueError("会话缺少有效的 info.id。")
    task = task if task is not None else bundle.get("task")
    manifest = Task.model_validate(redact(task)) if task is not None else None
    if manifest and len({check.id for check in manifest.checks}) != len(manifest.checks):
        raise ValueError("任务验收 check.id 必须唯一。")
    native = redact(native)
    source_id = store.put_artifact(canonical(native).encode())
    diff_provided = diff is not None or bundle.get("diff") is not None
    diff_input = diff if diff is not None else bundle.get("diff")
    diff = redact(diff_input if diff_input is not None else "")
    if not isinstance(diff, str):
        raise ValueError("diff 必须为统一补丁文本。")
    if diff.strip() and not diff_files(diff):
        raise ValueError("无法识别最终 diff 中的文件路径；请提供标准 git diff 或 unified diff。")
    first_message = first_message or bundle.get("first_message")
    last_message = last_message or bundle.get("last_message")
    effective_version = agent_version or bundle.get("agent_version") or native["info"].get("version")
    reports = bundle.get("verifications", [])
    if not isinstance(reports, list):
        raise ValueError("verifications 必须为数组。")
    records = []
    for report in reports:
        record = Verification.model_validate(redact(report))
        if record.provenance != "external_verifier":
            raise ValueError("运行包只允许附加 external_verifier 报告。")
        record.evidence_ids, record.event_id = [], None
        records.append(record)
    identity = {
        "source": source_id,
        "task": manifest.model_dump() if manifest else None,
        "diff": diff,
        "diff_provided": diff_provided,
        "verifications": [v.model_dump() for v in records],
        "first": first_message,
        "last": last_message,
        "adapter": VERSION,
        "agent_version": effective_version,
        "demo": bool(bundle.get("demo", False)),
    }
    run_id = "run_" + digest(identity)[:20]
    try:
        return store.get_run(run_id), False
    except KeyError:
        pass
    info = native["info"]
    run = Run(
        id=run_id,
        title=str(info.get("title") or filename),
        session_id=info["id"],
        parent_session_id=info.get("parentID"),
        imported_at=now(),
        source_hash=source_id,
        source_artifact=source_id,
        directory=str(info.get("directory", "")),
        task=manifest,
        agent_version=effective_version,
        first_message=first_message,
        last_message=last_message,
        diff=diff,
        diff_provided=diff_provided,
        demo=bool(bundle.get("demo", False)),
    )
    run.evidence.append(
        Evidence(
            id="source",
            kind="artifact",
            artifact_id=source_id,
            pointer="/info",
            description="OpenCode 会话导出信息",
        )
    )
    if manifest:
        task_artifact = store.put_artifact(canonical(manifest.model_dump()).encode())
        run.evidence.append(
            Evidence(
                id="task",
                kind="artifact",
                artifact_id=task_artifact,
                description="导入的任务、验收条件与约束",
            )
        )
    if diff_provided:
        run.diff_artifact = store.put_artifact(diff.encode(), "text/x-diff")
        run.evidence.append(
            Evidence(
                id="final-diff",
                kind="artifact",
                artifact_id=run.diff_artifact,
                description="用户提供的最终 diff；未自动验证仓库状态",
            )
        )
    # Merge snapshots by native identity. A repeated part is an update, not a new call.
    messages: dict[str, tuple[dict, str, dict]] = {}
    calls = {}
    duplicate_count = 0
    for index, msg in enumerate(native["messages"]):
        if (
            not isinstance(msg, dict)
            or not isinstance(msg.get("info"), dict)
            or not isinstance(msg.get("parts"), list)
        ):
            raise ValueError(f"messages[{index}] 缺少 info / parts。")
        mid = msg["info"].get("id")
        if not isinstance(mid, str) or not mid:
            raise ValueError(f"messages[{index}] 缺少消息 ID。")
        if msg["info"].get("sessionID") not in (None, run.session_id):
            raise ValueError("导出文件包含不同会话的消息，请分别导入。")
        existing = messages.get(mid)
        parts = existing[2] if existing else {}
        for pi, part in enumerate(msg["parts"]):
            if not isinstance(part, dict):
                raise ValueError(f"messages[{index}].parts[{pi}] 不是对象。")
            if part.get("sessionID") not in (None, run.session_id) or part.get("messageID") not in (
                None,
                mid,
            ):
                raise ValueError("part 的 sessionID/messageID 与所属消息不一致。")
            for name in ("time", "tokens", "metadata"):
                if name in part:
                    part[name] = mapping(part[name], f"part.{name}")
            pid = str(part.get("id", f"missing-{index}-{pi}"))
            if part.get("type") == "tool" and part.get("callID"):
                call_key = (mid, str(part["callID"]))
                previous = calls.get(call_key)
                if previous and previous != pid and previous in parts:
                    del parts[previous]
                    duplicate_count += 1
                calls[call_key] = pid
            if pid in parts:
                duplicate_count += 1
            parts[pid] = (part, f"/messages/{index}/parts/{pi}")
        messages[mid] = (msg["info"], f"/messages/{index}/info", parts)
    mids = list(messages)
    if first_message and first_message not in messages or last_message and last_message not in messages:
        raise ValueError("指定的任务消息边界不在导出文件中。")
    lower = mids.index(first_message) if first_message else 0
    upper = mids.index(last_message) + 1 if last_message else len(mids)
    if lower > upper or lower == upper and (first_message or last_message):
        raise ValueError("任务开始消息必须位于结束消息之前。")
    selected = mids[lower:upper]
    incomplete = False
    redacted = False
    unknown = 0
    compacted = False
    has_subagents = bool(info.get("parentID"))
    models = set()

    def add(part: dict, pointer: str, mi: dict, kind: str, title: str, **kwargs) -> Event:
        seq = len(run.events)
        eid = "ev_" + digest([mi["id"], part.get("id", pointer), kind])[:16]
        evidence_id = "ref_" + eid
        event = Event(
            id=eid,
            seq=seq,
            native_id=str(part.get("id", mi["id"])),
            message_id=mi["id"],
            parent_message_id=mi.get("parentID"),
            role=mi.get("role", "unknown"),
            kind=kind,
            title=title,
            source_pointer=pointer,
            evidence_id=evidence_id,
            **kwargs,
        )
        run.events.append(event)
        run.evidence.append(
            Evidence(
                id=evidence_id,
                kind="event",
                event_id=eid,
                artifact_id=source_id,
                pointer=pointer,
                description=title,
            )
        )
        return event

    assistant_info = []
    for mid in selected:
        mi, mpointer, parts = messages[mid]
        mi["time"] = mapping(mi.get("time"), "message.time")
        mi["tokens"] = mapping(mi.get("tokens"), "message.tokens")
        if "cache" in mi["tokens"]:
            mi["tokens"]["cache"] = mapping(mi["tokens"]["cache"], "tokens.cache")
        mt = timestamp(mi["time"].get("created"))
        completed = timestamp(mi["time"].get("completed"))
        if mi.get("role") == "assistant":
            assistant_info.append(mi)
            if mi.get("providerID") and mi.get("modelID"):
                models.add(f"{mi['providerID']}/{mi['modelID']}")
            if not completed:
                incomplete = True
            if mi.get("error"):
                add(
                    {},
                    mpointer,
                    mi,
                    "error",
                    "模型调用错误",
                    start_ms=mt,
                    time_basis="message",
                    status="error",
                    output=_text(mi["error"]),
                    stage=["recovery"],
                )
        step_usage = []
        for part, pointer in parts.values():
            pt = part.get("type", "unknown")
            start = timestamp(part.get("time", {}).get("start"))
            end = timestamp(part.get("time", {}).get("end"))
            base = {
                "start_ms": start if start is not None else mt,
                "end_ms": end,
                "time_basis": "native" if start is not None else "message" if mt is not None else "unknown",
            }
            redacted = redacted or "[redacted" in canonical(part)
            if pt == "text":
                body = _text(part.get("text"))
                if mi.get("role") == "user" and not part.get("synthetic") and not part.get("ignored"):
                    run.task_prompt += ("\n\n" if run.task_prompt else "") + body
                add(
                    part,
                    pointer,
                    mi,
                    "message",
                    "用户任务" if mi.get("role") == "user" else "Agent 回复",
                    output=body,
                    status="completed",
                    data={"synthetic": bool(part.get("synthetic")), "ignored": bool(part.get("ignored"))},
                    **base,
                )
            elif pt == "tool":
                state = part.get("state")
                if not isinstance(state, dict) or not isinstance(state.get("input", {}), dict):
                    raise ValueError(f"工具 part {part.get('id')} 的 state/input 格式无效。")
                tool = str(part.get("tool", "unknown"))
                status = state.get("status", "unknown")
                args = state.get("input", {})
                metadata = mapping(state.get("metadata"), "tool.metadata")
                times = mapping(state.get("time"), "tool.time")
                state["metadata"] = metadata
                ts, te = timestamp(times.get("start")), timestamp(times.get("end"))
                exit_code = metadata.get("exit", metadata.get("exitCode", metadata.get("exit_code")))
                if isinstance(exit_code, bool) or not isinstance(exit_code, int):
                    exit_code = None
                output = _text(state.get("output", state.get("error", metadata.get("output", ""))))
                is_compacted = bool(times.get("compacted")) or "[Old tool result content cleared]" in output
                truncated = bool(metadata.get("truncated")) or is_compacted or "[redacted" in canonical(state)
                compacted = compacted or is_compacted
                incomplete = incomplete or status not in {"completed", "error"}
                has_subagents = has_subagents or tool == "task"
                command = args.get("command", "") if tool == "bash" else ""
                command = command if isinstance(command, str) else ""
                check_kind = command_kind(command)
                check = next((c for c in manifest.checks if c.command == command), None) if manifest else None
                check_kind = check.kind if check else check_kind
                stages = (
                    ["verification"]
                    if check_kind
                    else ["exploration"]
                    if tool in READ_TOOLS
                    else ["implementation"]
                    if tool in WRITE_TOOLS
                    else []
                )
                files = _path_list(part, run.directory)
                title = command or (f"{tool} · {files[0]}" if files else tool)
                event = add(
                    part,
                    pointer,
                    mi,
                    "tool",
                    title,
                    tool=tool,
                    call_id=part.get("callID"),
                    status=status,
                    input=args,
                    output=output,
                    files=files,
                    stage=stages,
                    start_ms=ts if ts is not None else mt,
                    end_ms=te,
                    time_basis="native" if ts is not None else "message" if mt is not None else "unknown",
                    data={
                        "exit_code": exit_code,
                        "truncated": truncated,
                        "patch": _patch(part),
                        "check_kind": check_kind,
                        "cwd": args.get("workdir", run.directory),
                        "compacted": is_compacted,
                    },
                )
                if check_kind:
                    result = (
                        "unknown"
                        if truncated
                        or exit_code is None
                        or not simple_command(command)
                        or status != "completed"
                        else "pass"
                        if exit_code == 0
                        else "fail"
                    )
                    if status == "error":
                        result = "error"
                    counts = counts_from_output(output) if not truncated else {}
                    if check_kind == "test" and counts.get("executed") == 0 and result == "pass":
                        result = "unknown"
                    run.verifications.append(
                        Verification(
                            id="verify_" + event.id,
                            check_id=check.id if check else None,
                            kind=check_kind,
                            phase="intermediate",
                            provenance="observed_tool",
                            result=result,
                            evidence_ids=[event.evidence_id],
                            event_id=event.id,
                            command=command,
                            **counts,
                        )
                    )
            elif pt == "step-finish":
                part["tokens"] = mapping(part.get("tokens"), "step.tokens")
                if "cache" in part["tokens"]:
                    part["tokens"]["cache"] = mapping(part["tokens"]["cache"], "tokens.cache")
                event = add(
                    part,
                    pointer,
                    mi,
                    "model",
                    "模型用量",
                    status="completed",
                    data={
                        "reason": part.get("reason"),
                        "tokens": part.get("tokens"),
                        "cost": part.get("cost"),
                    },
                    **base,
                )
                step_usage.append(
                    {
                        "scope_id": event.id,
                        "tokens": part.get("tokens", {}),
                        "cost": part.get("cost"),
                        "evidence_id": event.evidence_id,
                    }
                )
            elif pt == "patch":
                add(
                    part,
                    pointer,
                    mi,
                    "patch",
                    "仓库变更记录",
                    files=_path_list(part, run.directory),
                    status="observed",
                    stage=["implementation"],
                    data={"hash": part.get("hash")},
                    **base,
                )
            elif pt == "reasoning":
                add(
                    part,
                    pointer,
                    mi,
                    "context",
                    "推理内容不参与评估",
                    status="omitted",
                    data={"native_type": pt},
                    **base,
                )
            elif pt in {"compaction", "snapshot", "step-start", "retry", "subtask", "agent", "file"}:
                compacted = compacted or pt == "compaction"
                has_subagents = has_subagents or pt == "subtask"
                title = {
                    "compaction": "上下文压缩",
                    "snapshot": "快照引用",
                    "step-start": "步骤开始",
                    "retry": "模型重试",
                    "subtask": "子任务",
                    "agent": "Agent 选择",
                    "file": "文件附件",
                }[pt]
                add(
                    part,
                    pointer,
                    mi,
                    "retry" if pt == "retry" else "context",
                    title,
                    status="observed",
                    data={
                        "native_type": pt,
                        "snapshot": part.get("snapshot"),
                        "attempt": part.get("attempt"),
                    },
                    **base,
                )
            else:
                unknown += 1
                add(part, pointer, mi, "unknown", f"未识别事件 · {pt}", data={"native_type": pt}, **base)
        if mi.get("role") == "assistant":
            # step-finish records cover individual inferences; message totals are only a fallback.
            if step_usage:
                run.usage.extend(step_usage)
            else:
                ref = "usage_" + digest(mid)[:12]
                run.evidence.append(
                    Evidence(
                        id=ref,
                        kind="artifact",
                        artifact_id=source_id,
                        pointer=mpointer,
                        description="消息用量（没有 step-finish 时使用）",
                    )
                )
                run.usage.append(
                    {
                        "scope_id": mid,
                        "tokens": mi.get("tokens", {}),
                        "cost": mi.get("cost"),
                        "evidence_id": ref,
                    }
                )
    times = [e.start_ms for e in run.events if e.start_ms is not None]
    run.start_ms = min(times) if times else None
    last_mi = messages[selected[-1]][0] if selected else {}
    run.end_ms = (
        timestamp(last_mi.get("time", {}).get("completed")) if last_mi.get("role") == "assistant" else None
    )
    # Assignment bypasses Pydantic field conversion. Match the float representation
    # used when reading persisted Run JSON so evaluation hashes remain identical.
    if run.end_ms is not None:
        run.end_ms = float(run.end_ms)
    if last_mi.get("role") == "assistant" and last_mi.get("error"):
        run.execution_status = "failed"
    elif incomplete or last_mi.get("role") != "assistant":
        run.execution_status = "interrupted" if incomplete else "unknown"
    elif last_mi.get("finish") in {"stop", "end_turn"}:
        run.execution_status = "completed"
    else:
        run.execution_status = "unknown"
    run.models = sorted(models)
    if duplicate_count:
        run.warnings.append(f"合并了 {duplicate_count} 条重复 part 更新，没有重复计算调用。")
    if info.get("revert"):
        run.warnings.append("会话包含 revert；保留所有导出执行记录，不将当前上下文等同于全部执行历史。")
    if unknown:
        run.warnings.append(f"保留了 {unknown} 个未知类型事件，相关行为指标可能不完整。")
    if not selected:
        run.warnings.append("这是空会话，没有可分析的消息。")
    if first_message or last_message:
        run.warnings.append("本次只评估指定消息区间，区间外的调用与费用不计入。")
    run.warnings.append("会话导出是历史记录快照，不保证包含外部操作、所有子会话或完整文件状态。")
    run.coverage = {
        "messages": {
            "level": "partial" if redacted or compacted else "observed",
            "reason": "原生导出中的消息；压缩/脱敏内容不会补写。",
        },
        "tools": {
            "level": "partial" if incomplete or unknown or redacted else "observed",
            "reason": "仅覆盖导出中可见的工具调用。",
        },
        "file_access": {"level": "partial", "reason": "能识别原生文件工具；不展开任意 shell 脚本。"},
        "verification": {"level": "partial", "reason": "工具执行可见；外部或未识别脚本内检查不可推断。"},
        "repository": {
            "level": "partial" if diff else "unavailable",
            "reason": "提供了最终 diff。" if diff else "没有完整代码快照，变更记录不是最终 diff。",
        },
        "subagents": {
            "level": "partial" if has_subagents else "unknown",
            "reason": "子会话需要分别导出，不推断缺失的子 agent 费用。",
        },
        "usage": {
            "level": "observed" if run.usage else "unavailable",
            "reason": "优先 step-finish，回退消息用量；不同时累加两者。",
        },
    }
    for index, record in enumerate(records):
        report_artifact = store.put_artifact(canonical(record.model_dump()).encode())
        ref = "report_" + str(index)
        record.evidence_ids = [ref]
        record.event_id = None
        run.evidence.append(
            Evidence(
                id=ref,
                kind="artifact",
                artifact_id=report_artifact,
                description=f"外部验证报告 · {record.check_id or record.id} · {record.phase}",
            )
        )
        run.verifications.append(record)
    # Save only after full validation. Artifact orphans are harmless content-addressed files.
    created = store.save_run(run)
    return run, created
