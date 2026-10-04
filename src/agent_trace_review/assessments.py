"""Active assessment orchestrator: fixed cases -> target calls -> independent Profiles."""

import json
import secrets
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .assessment_contracts import AssessmentSuite, TargetDefinition, TargetRequest
from .assessment_store import AssessmentStore
from .profiles import evaluate_profile
from .repositories import inspect_repository
from .service import ingest
from .service_lock import ServiceLock
from .storage import Store
from .target_client import TargetClient, TargetError, deployed_target, scrub
from .target_telemetry import telemetry_summary, trace_events
from .util import canonical, digest, now


def engine_hash():
    modules = (
        "assessments.py",
        "assessment_contracts.py",
        "assessment_store.py",
        "target_client.py",
        "target_telemetry.py",
        "repositories.py",
        "profiles.py",
        "generic.py",
        "analysis.py",
        "contracts.py",
        "util.py",
        "repository_jobs.py",
        "repository_contracts.py",
        "repository_process.py",
    )
    root = Path(__file__).parent
    return digest({name: digest((root / name).read_bytes()) for name in modules})


def load_targets(path: Path | None) -> dict[str, TargetDefinition]:
    if path is None:
        return {}
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("目标登记文件超过 1 MB。")
    raw = json.loads(path.read_text())
    if not isinstance(raw, list) or len(raw) > 100:
        raise ValueError("目标登记文件需要最多 100 项的 JSON 数组。")
    targets = [TargetDefinition.model_validate(v) for v in raw]
    if len({t.id for t in targets}) != len(targets):
        raise ValueError("目标 id 必须唯一。")
    return {t.id: t for t in targets}


def public_target(target: TargetDefinition):
    return {
        "id": target.id,
        "adapter": "agent-review/target-v1",
        "managed": target.deployment is not None,
        "repository_configured": target.repository is not None,
        "demo": target.demo,
    }


def prepare_assessment(db: AssessmentStore, target: TargetDefinition, suite: AssessmentSuite):
    repository = None
    if target.repository:
        repository = db.save_repository(
            inspect_repository(Path(target.repository), target.ref, target.repository_url)
        )
    claims = {c["id"] for c in repository["claims"]} if repository else set()
    files = {f["path"]: f for f in repository["files"]} if repository else {}
    for case in suite.cases:
        if any(c not in claims for c in case.claim_ids):
            raise ValueError(f"{case.id} 引用了不在当前仓库档案中的 claim_id。")
        if any(r.path not in files or r.line > files[r.path]["lines"] for r in case.source_refs):
            raise ValueError(f"{case.id} 的 source_refs 超出当前源码快照。")
    suite_hash = digest(suite.model_dump())
    target_hash = digest(target.model_dump())
    body = {
        "target_id": target.id,
        "target_hash": target_hash,
        "engine_hash": engine_hash(),
        "suite_id": suite.id,
        "suite_version": suite.version,
        "suite_hash": suite_hash,
        "repository_id": repository["id"] if repository else None,
        "commit": repository["commit"] if repository else None,
        "source_hash": repository["source_hash"] if repository else None,
        "demo": target.demo,
        "planned": len(suite.cases) * len(suite.budgets) * suite.attempts,
        "completed": 0,
        "results": [],
        "curves": [],
        "claims": [],
        "error": None,
        "deployment": None,
        "started_at": None,
        "finished_at": None,
        "suite_artifact": db.store.put_artifact(canonical(suite.model_dump()).encode()),
        "limitations": [
            "输出验收只覆盖固定 Suite；自适应出题和官方基准成绩尚未实现。",
            "目标服务与源码版本的绑定未认证；健康检查中的 commit 属于目标自报。",
            "外部对话由评审端观察；内部模型/工具轨迹由目标自报，未接入的调用不可见，源码关联不证明原因。",
            "Token/费用由目标自报；max_output_tokens 为请求约束，未强制供应商侧限额。",
            "每个 case/attempt/budget 使用新 session_id；会话隔离是否成立须由专项任务验证。",
        ],
    }
    return db.create_job(body), repository


def _body(job):
    return {
        k: v
        for k, v in job.items()
        if k not in {"id", "created_at", "updated_at", "state", "cancel_requested"}
    }


def _sources(case, repository):
    if repository is None:
        return []
    claims = {c["id"]: c for c in repository["claims"]}
    files = {f["path"]: f for f in repository["files"]}
    links = [{"kind": "claim", **claims[c]} for c in case.claim_ids]
    for ref in case.source_refs:
        entry = {
            "kind": "operator_source_reference",
            **ref.model_dump(),
            "file_hash": files[ref.path]["sha256"],
            "status": "hypothesis",
        }
        if repository["commit"] and repository["repository_url"]:
            from urllib.parse import quote

            entry["url"] = (
                f"{repository['repository_url']}/blob/{repository['commit']}/{quote(ref.path, safe='/')}#L{ref.line}"
            )
        links.append(entry)
    return links


def _usage(turns, budget, execution_complete=True):
    usages = [t.get("usage") for t in turns]
    complete = execution_complete and bool(usages) and all(u is not None for u in usages)
    totals, outputs, costs = [], [], []
    for usage in usages:
        tokens = (usage or {}).get("tokens", {})
        total = tokens.get("total")
        if total is None and tokens.get("input") is not None and tokens.get("output") is not None:
            total = tokens["input"] + tokens["output"]
        totals.append(total)
        outputs.append(tokens.get("output"))
        costs.append((usage or {}).get("cost_usd"))
    total_tokens = sum(totals) if complete and all(t is not None for t in totals) else None
    cost = sum(costs) if complete and all(c is not None for c in costs) else None
    adherence = "not_requested"
    if budget.max_output_tokens:
        adherence = "unknown"
        if sum(v for v in outputs if v is not None) > budget.max_output_tokens:
            adherence = "fail"
        elif complete and all(v is not None for v in outputs):
            adherence = "pass" if sum(outputs) <= budget.max_output_tokens else "fail"
    return {
        "input_tokens": sum((u["tokens"]["input"] for u in usages))
        if complete and all(u["tokens"]["input"] is not None for u in usages) else None,
        "output_tokens": sum((u["tokens"]["output"] for u in usages))
        if complete and all(u["tokens"]["output"] is not None for u in usages) else None,
        "total_tokens": total_tokens,
        "cost_usd": cost,
        "provenance": "target_reported",
        "output_token_budget": adherence,
    }


def _case(store, client, job_id, suite, case, budget, attempt, repository, cancelled):
    session = uuid.uuid4().hex
    messages, events, turns = [], [], []
    start_wall = time.time() * 1000
    started = time.monotonic()
    state, error = "completed", None
    for index, turn in enumerate(case.turns):
        if cancelled():
            state, error = "cancelled", "cancel_requested"
            break
        remaining = budget.deadline_seconds - (time.monotonic() - started)
        if remaining < 0.05:
            state, error = "timeout", "timeout"
            break
        messages.append({"role": "user", "content": turn.prompt})
        visible_messages = messages if turn.history == "full" else messages[-1:]
        request_budget = budget.model_copy(update={"deadline_seconds": remaining})
        reported_outputs = [
            t["usage"]["tokens"]["output"]
            for t in turns
            if t["usage"] and t["usage"]["tokens"]["output"] is not None
        ]
        if budget.max_output_tokens:
            available = budget.max_output_tokens - sum(reported_outputs)
            if available <= 0:
                state, error = "budget_exhausted", "output_token_budget_exhausted"
                break
            request_budget = request_budget.model_copy(update={"max_output_tokens": available})
        request = TargetRequest(
            session_id=session,
            turn=index,
            prompt=turn.prompt,
            messages=visible_messages,
            input=case.input,
            budget=request_budget,
        ).model_dump()
        sent = time.time() * 1000
        sent_mono = time.monotonic()
        events.append(
            {
                "id": f"user_{index}",
                "kind": "message",
                "role": "user",
                "output": turn.prompt,
                "status": "completed",
                "start_ms": sent,
                "provenance": "host_observed",
            }
        )
        try:
            response = client.call(request, remaining)
        except TargetError as exc:
            state, error = ("timeout" if exc.code == "timeout" else "error"), exc.code
            events.append(
                {
                    "id": f"error_{index}",
                    "kind": "error",
                    "role": "system",
                    "title": "评审端调用失败",
                    "output": exc.code,
                    "status": "error",
                    "start_ms": sent,
                    "end_ms": time.time() * 1000,
                    "provenance": "host_observed",
                }
            )
            break
        ended = time.time() * 1000
        trace = response.trace.model_dump() if response.trace else None
        messages.append({"role": "assistant", "content": response.output})
        turns.append(
            {
                "turn": index,
                "request": scrub(request, client.secrets),
                "output": response.output,
                "usage": response.usage.model_dump() if response.usage else None,
                "latency_ms": round((time.monotonic() - sent_mono) * 1000, 3),
                "trace": trace,
                "execution_status": response.execution_status,
                "error": response.error,
            }
        )
        events.extend(trace_events(trace, job_id=job_id, case_id=case.id, session_id=session, turn=index))
        events.append(
            {
                "id": f"assistant_{index}",
                "kind": "message",
                "role": "assistant",
                "output": response.output,
                "status": "completed" if response.execution_status == "completed" else "error",
                "start_ms": sent,
                "end_ms": ended,
                "provenance": "host_observed",
                "duration_ms": turns[-1]["latency_ms"],
            }
        )
        if response.execution_status == "error":
            state, error = "error", response.error
            break
    if cancelled():
        state, error = "cancelled", "cancel_requested"
    sources = _sources(case, repository)
    telemetry = telemetry_summary(turns, state == "completed")
    metadata = {
        "job_id": job_id,
        "suite_hash": digest(suite.model_dump()),
        "case_id": case.id,
        "category": case.category,
        "attempt": attempt,
        "budget": budget.model_dump(),
        "execution_state": state,
        "error": error,
        "turns": turns,
        "telemetry": telemetry,
        "source_evidence": sources,
        "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
        "repository_id": repository["id"] if repository else None,
        "commit": repository["commit"] if repository else None,
    }
    trace = {
        "trace_version": "1",
        "framework": "http-target-v1",
        "run_id": session,
        "title": f"{suite.id} / {case.id} / {budget.id} / {attempt}",
        "task_prompt": "\n\n".join(t.prompt for t in case.turns),
        "events": events,
        "status": state,
        "coverage": "complete" if telemetry["coverage"] == "complete" else "partial",
        "demo": client.target.demo,
        "start_ms": start_wall,
        "end_ms": time.time() * 1000,
        "agent_version": None,
        "artifacts": {"assessment": metadata},
    }
    if state == "completed":
        trace["output"] = turns[-1]["output"]
    run, _, _ = ingest(store, canonical(scrub(trace, client.secrets)).encode())
    evaluation = evaluate_profile(store, run, case.profile, persist=False)
    if state != "completed":
        evaluation.outcome = "inconclusive"
        evaluation.outcome_reason = "评审端未完成目标调用；保留已有对话，不据此确认能力通过或失败。"
    store.save_evaluation(evaluation)
    return {
        "case_id": case.id,
        "category": case.category,
        "budget_id": budget.id,
        "attempt": attempt,
        "session_id": session,
        "run_id": run.id,
        "revision_id": evaluation.id,
        "outcome": evaluation.outcome,
        "execution_state": state,
        "error": error,
        "duration_ms": round((time.monotonic() - started) * 1000, 3),
        "usage": _usage(turns, budget, state == "completed"),
        "telemetry": telemetry,
        "source_evidence": sources,
        "checks": evaluation.custom["checks"],
    }


def _summarize(body, suite, repository):
    rows = body["results"]
    body["completed"] = len(rows)
    body["curves"] = []
    for budget in suite.budgets:
        selected = [r for r in rows if r["budget_id"] == budget.id]
        planned = len(suite.cases) * suite.attempts
        passed = sum(r["outcome"] == "pass" for r in selected)
        failed = sum(r["outcome"] == "fail" for r in selected)
        complete_usage = len(selected) == planned and all(
            r["usage"]["total_tokens"] is not None for r in selected
        )
        complete_cost = len(selected) == planned and all(r["usage"]["cost_usd"] is not None for r in selected)
        body["curves"].append(
            {
                "budget": budget.model_dump(),
                "planned": planned,
                "observed": len(selected),
                "pass": passed,
                "fail": failed,
                "unknown": planned - passed - failed,
                "pass_rate": round(passed / planned * 100, 2),
                "mean_duration_ms": round(sum(r["duration_ms"] for r in selected) / len(selected), 3)
                if selected
                else None,
                "reported_total_tokens": sum(r["usage"]["total_tokens"] for r in selected)
                if complete_usage
                else None,
                "reported_cost_usd": sum(r["usage"]["cost_usd"] for r in selected) if complete_cost else None,
            }
        )
    if repository:
        body["claims"] = []
        for claim in repository["claims"]:
            case_ids = {c.id for c in suite.cases if claim["id"] in c.claim_ids}
            tested = [r for r in rows if r["case_id"] in case_ids]
            expected = len(case_ids) * len(suite.budgets) * suite.attempts
            status = "untested"
            if tested:
                if any(r["outcome"] == "fail" for r in tested):
                    status = "contradicted_by_cases"
                elif len(tested) == expected and all(r["outcome"] == "pass" for r in tested):
                    status = "supported_for_cases"
                else:
                    status = "inconclusive"
            body["claims"].append({**claim, "assessment_status": status, "case_ids": sorted(case_ids)})


def run_assessment(db, job_id, target, suite, repository=None, *, transport=None, cancel_check=None):
    if not db.claim(job_id):
        return db.job(job_id)
    body = _body(db.job(job_id))
    body["started_at"] = now()
    db.update(job_id, body)

    def cancelled():
        return db.job(job_id)["cancel_requested"] or bool(cancel_check and cancel_check())

    state = "completed"
    try:
        token = secrets.token_urlsafe(32) if target.deployment and target.deployment.service_token_variable else None
        with deployed_target(target, resources=db, job_id=job_id, cancelled=cancelled, service_token=token) as (endpoint, deployment):
            if body.get("build_provenance", {}).get("image_id") == deployment.get("image_id") and deployment.get("image_id"):
                deployment["source_binding"] = "built_from_checkout"
            body["deployment"] = deployment
            client = TargetClient(target, endpoint, transport=transport, token_override=token)
            if target.health_path and not target.deployment:
                body["health"] = client.health()
            db.update(job_id, body)
            for budget in suite.budgets:
                for case in suite.cases:
                    for attempt in range(1, suite.attempts + 1):
                        if cancelled():
                            state = "cancelled"
                            break
                        result = _case(
                            db.store, client, job_id, suite, case, budget, attempt, repository, cancelled
                        )
                        body["results"].append(result)
                        _summarize(body, suite, repository)
                        db.update(job_id, body)
                    if state == "cancelled":
                        break
                if state == "cancelled":
                    break
    except TargetError as exc:
        state, body["error"] = "failed", exc.code
    except Exception:
        # Exceptions can include target content, credentials or operator paths.
        state, body["error"] = "failed", "assessment_internal_error"
    if cancelled():
        state = "cancelled"
    body["finished_at"] = now()
    _summarize(body, suite, repository)
    db.update(job_id, body, state)
    return db.job(job_id)


class AssessmentManager:
    def __init__(self, store: Store, targets: dict[str, TargetDefinition]):
        self._lock = ServiceLock(store.root)
        self.db = AssessmentStore(store)
        self.targets = targets
        self.db.recover_resources()
        self.db.recover_interrupted()
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="agent-assessment")

    def submit(self, target_id, suite):
        if target_id not in self.targets:
            raise ValueError("目标未由管理员登记。")
        target = self.targets[target_id]
        job, repository = prepare_assessment(self.db, target, suite)
        try:
            self.executor.submit(run_assessment, self.db, job["id"], target, suite, repository)
        except RuntimeError:
            self.db.update(job["id"], _body(job), "interrupted")
            raise ValueError("服务正在关闭，请稍后重试。") from None
        return job

    def close(self):
        for job_id in self.db.active_jobs():
            self.db.cancel(job_id)
        self.executor.shutdown(wait=True, cancel_futures=True)
        self._lock.close()


def compare_assessments(left, right):
    reasons = []
    if left["suite_hash"] != right["suite_hash"]:
        reasons.append("suite_hash 不同：题目、验收或预算不同。")
    if left["state"] != "completed" or right["state"] != "completed":
        reasons.append("评测未全部完成。")
    if left["target_id"] != right["target_id"]:
        reasons.append("target_id 不同：这不是同一目标的版本回归。")
    if left["demo"] != right["demo"]:
        reasons.append("示例与真实目标不能混合比较。")
    if left.get("engine_hash") != right.get("engine_hash"):
        reasons.append("评审执行器代码版本不同。")
    comparable = not reasons

    def key(row):
        return row["case_id"], row["budget_id"], row["attempt"]

    a, b = {key(r): r for r in left["results"]}, {key(r): r for r in right["results"]}
    changes = []
    if comparable:
        for item in sorted(a.keys() & b.keys()):
            if a[item]["outcome"] != b[item]["outcome"]:
                changes.append(
                    {
                        "case_id": item[0],
                        "budget_id": item[1],
                        "attempt": item[2],
                        "from": a[item]["outcome"],
                        "to": b[item]["outcome"],
                        "regression": a[item]["outcome"] == "pass" and b[item]["outcome"] == "fail",
                        "left_run": a[item]["run_id"],
                        "right_run": b[item]["run_id"],
                    }
                )
    return {
        "comparable": comparable,
        "reasons": reasons,
        "left": left["id"],
        "right": right["id"],
        "left_commit": left["commit"],
        "right_commit": right["commit"],
        "changes": changes,
        "target_configuration_changed": left["target_hash"] != right["target_hash"],
        "limitations": ["重复结果反映观测变化；单次变化不证明统计显著性。", "部署源码绑定未认证。"],
    }


def assessment_markdown(job):
    lines = [
        f"# Agent 主动评测：{job['suite_id']}",
        "",
        f"- 任务：{job['id']}",
        f"- 目标：{job['target_id']}；状态：{job['state']}；示例：{job['demo']}",
        f"- 仓库提交：{job['commit'] or '未知'}",
        f"- Suite SHA-256：{job['suite_hash']}",
        f"- 执行器 SHA-256：{job['engine_hash']}",
        f"- 目标配置 SHA-256：{job['target_hash']}",
        f"- 完成：{job['completed']}/{job['planned']}",
        "",
        "## 预算与结果",
        "",
        "|预算|通过|失败|未知|通过率|平均观测耗时 ms|自报 Token|自报费用 USD|",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for curve in job["curves"]:
        tokens = curve["reported_total_tokens"] if curve["reported_total_tokens"] is not None else "未知"
        cost = curve["reported_cost_usd"] if curve["reported_cost_usd"] is not None else "未知"
        lines.append(
            f"|{curve['budget']['id']}|{curve['pass']}|{curve['fail']}|{curve['unknown']}|{curve['pass_rate']}%|{curve['mean_duration_ms']}|{tokens}|{cost}|"
        )
    lines += ["", "## 独立任务验收与源码线索", ""]
    for result in job["results"]:
        lines += [
            f"### {result['case_id']} / {result['budget_id']} / {result['attempt']}",
            "",
            f"{result['outcome']} · {result['execution_state']} · run_id={result['run_id']}",
            "",
        ]
        for check in result["checks"]:
            lines.append(f"- {check['id']}：{check['status']} · {check['explanation']}")
        usage = result.get("usage", {})
        telemetry = result.get("telemetry", {})
        def display(value):
            return "未知" if value is None else value
        lines += [
            f"- 自报 Token：输入 {display(usage.get('input_tokens'))}；输出 {display(usage.get('output_tokens'))}；总计 {display(usage.get('total_tokens'))}",
            f"- 自报调用：模型 {display(telemetry.get('llm_calls'))}；工具 {display(telemetry.get('tool_calls'))}；覆盖 {telemetry.get('coverage', 'unavailable')}",
            f"- 自报错误：模型 {display(telemetry.get('llm_errors'))}；工具 {display(telemetry.get('tool_errors'))}",
            f"- 自报调用耗时合计 ms：模型 {display(telemetry.get('llm_duration_ms'))}；工具 {display(telemetry.get('tool_duration_ms'))}",
        ]
        for model in telemetry.get("models", []):
            lines.append(f"- 模型 {model['model']}：{model['calls']} 次；Token {display(model['tokens']['total'])}；耗时 {display(model['duration_ms'])} ms")
        for source in result["source_evidence"]:
            location = f"{source['path']}:{source['line']}"
            if source.get("url"):
                location = f"[{location}]({source['url']})"
            lines.append(f"- 源码线索（不证明原因）：{location}")
        lines.append("")
    lines += ["## 能力声明", ""]
    for claim in job["claims"]:
        lines.append(
            f"- {claim['id']} / {claim['capability']}：{claim['assessment_status']}；{claim['path']}:{claim['line']}"
        )
    lines += ["", "## 可复现材料与限制", "", f"- Suite 工件：{job['suite_artifact']}",
              "- 内部调用与完整覆盖均由目标声明；partial 的计数是可见下界，未采集数据保持未知。",
              "- final_answer 计入工具调用；逐次耗时可能重叠。汇总用量不与调用用量重复相加。",
              "- 逐次调用的参数、结果、状态、耗时、Token 与 session/case/run 关联见证据包和运行轨迹。"]
    if job.get("build_provenance"):
        build = job["build_provenance"]
        lines += [f"- 源码构建：{build['repository_url']} @ {build['commit']}",
                  f"- 构建输入 SHA-256：{build['context_hash']}", f"- 实际镜像：{build['image_id']}",
                  f"- 仓库任务：{build['repository_job_id']}；本机从固定 checkout 构建"]
    if job.get("error"):
        lines.append(f"- 执行错误：{job['error']}")
    lines += [f"- {v}" for v in job["limitations"]]
    return "\n".join(lines) + "\n"


def assessment_bundle(db, job):
    suite, _ = db.store.get_artifact(job["suite_artifact"])
    return {
        "assessment_version": "agent-review/assessment-bundle-v1",
        "job": job,
        "suite": json.loads(suite),
        "repository": db.repository(job["repository_id"]) if job["repository_id"] else None,
        "runs": [
            {
                "bundle": db.store.bundle(r["run_id"]),
                "evaluation": db.store.get_evaluation(r["run_id"], r["revision_id"]).model_dump(),
            }
            for r in job["results"]
        ],
    }
