"""Active assessment orchestrator: fixed cases -> target calls -> independent Profiles."""

import json
import re
import secrets
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from itertools import groupby
from pathlib import Path

from .assessment_contracts import AssessmentSuite, TargetDefinition, TargetRequest
from .assessment_quality import quality_summary, result_guidance
from .assessment_store import AssessmentStore
from .profiles import evaluate_profile
from .repositories import inspect_repository
from .service import ingest
from .service_lock import ServiceLock
from .storage import Store
from .target_client import TargetClient, TargetError, deployed_target, scrub
from .target_telemetry import telemetry_summary, trace_events
from .usage_accounting import combine_usage, format_usage, summarize_usage
from .util import canonical, digest, now


def engine_hash():
    modules = (
        "assessments.py",
        "assessment_contracts.py",
        "assessment_store.py",
        "target_client.py",
        "target_telemetry.py",
        "provider_telemetry.py",
        "model_gateway.py",
        "usage_accounting.py",
        "server_config.py",
        "repositories.py",
        "profiles.py",
        "generic.py",
        "analysis.py",
        "contracts.py",
        "util.py",
        "repository_jobs.py",
        "repository_contracts.py",
        "repository_process.py",
        "source_tools.py",
        "repository_planning.py",
        "repository_adaptation.py",
        "repository_templates/auto_runtime.py",
        "assessment_quality.py",
        "general_suite.py",
        "suite_generation.py",
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


def telemetry_readiness(target, *, transport=None):
    """Only health-check an existing registered service; never start or call an agent."""
    result = {"target_id": target.id, "usage_mode": "unknown", "collection": "unknown",
              "provenance": "target_reported", "model": None}
    if target.deployment:
        return {**result, "status": "pending_deployment", "reason": "容器启动后才能检查目标的模式和采集声明。"}
    try:
        health = TargetClient(target, transport=transport).health()
    except TargetError as exc:
        return {**result, "status": "unavailable", "reason": f"健康检查未通过：{exc.code}"}
    raw = health.get("response", {})
    mode = raw.get("usage_mode", "unknown")
    if mode not in {"model", "offline"}:
        mode = "unknown"
    collection = raw.get("usage_collection", "unknown")
    if collection not in {"call_usage", "aggregate_usage", "not_applicable"}:
        collection = "unknown"
    reason = ("离线校准未调用模型服务，Token 统计不适用。" if mode == "offline" else
              "目标声明已采集模型用量；实际完整性以每次返回的 usage 为准。" if collection in {"call_usage", "aggregate_usage"}
              else "目标尚未声明模型模式或用量采集能力；健康检查不能证明 Token 已采集。")
    model = raw.get("model")
    return {**result, "status": health["status"], "usage_mode": mode, "collection": collection,
            "model": model[:200] if isinstance(model, str) else None, "reason": reason}


def prepare_assessment(db: AssessmentStore, target: TargetDefinition, suite: AssessmentSuite, *, repository=None):
    # RepositoryManager may supply the snapshot it just read from its immutable checkout.
    if target.repository and repository is None:
        repository = db.save_repository(
            inspect_repository(Path(target.repository), target.ref, target.repository_url)
        )
    claims = {c["id"] for c in repository["claims"]} if repository else set()
    if suite.repository_source_hash and (not repository or repository["source_hash"] != suite.repository_source_hash):
        raise ValueError("题集绑定的源码已改变；请重新扫描并生成评测计划。")
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
        "concurrency": suite.concurrency,
        "quality": quality_summary([], suite, repository),
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
        if k not in {"id", "created_at", "updated_at", "state", "cancel_requested", "gateway"}
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
    summary = summarize_usage(turns, execution_complete)
    output = summary["fields"]["output_tokens"]
    adherence = "not_requested"
    if budget.max_output_tokens:
        adherence = "unknown"
        if output["value"] is not None and output["value"] > budget.max_output_tokens:
            adherence = "fail"
        elif output["status"] == "complete":
            adherence = "pass"
        elif output["status"] == "not_applicable":
            adherence = "not_applicable"
    return {
        **{key: field["value"] if field["status"] == "complete" else None
           for key, field in summary["fields"].items()
           if key in {"input_tokens", "output_tokens", "total_tokens", "cost_usd"}},
        **summary,
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
        if budget.max_output_tokens:
            used = summarize_usage(turns)["fields"]["output_tokens"]["value"]
            available = budget.max_output_tokens - (used if used is not None else 0)
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
            response = client.call(request, remaining, context={
                "assessment_id": job_id, "case_id": case.id, "budget_id": budget.id, "attempt": attempt,
            })
        except TargetError as exc:
            state, error = ("timeout" if exc.code == "timeout" else "error"), exc.code
            if exc.gateway is not None:
                from .model_gateway import gateway_trace_events
                turns.append({"turn": index, "request": scrub(request, client.secrets), "output": None,
                              "usage": None, "usage_mode": "model", "trace": None, "gateway": exc.gateway,
                              "execution_status": "error", "error": error,
                              "latency_ms": round((time.monotonic() - sent_mono) * 1000, 3)})
                events.extend(gateway_trace_events(None, exc.gateway, job_id=job_id, case_id=case.id,
                    session_id=session, turn=index, budget_id=budget.id, attempt=attempt))
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
                "usage_mode": response.usage_mode,
                "aggregate_fields": sorted(response._aggregate_fields),
                "latency_ms": round((time.monotonic() - sent_mono) * 1000, 3),
                "trace": trace,
                "execution_status": response.execution_status,
                "error": response.error,
                "adapter_evidence": response.adapter_evidence.model_dump() if response.adapter_evidence else None,
            }
        )
        if response._gateway is not None:
            from .model_gateway import gateway_trace_events
            turns[-1]["gateway"] = response._gateway
            events.extend(gateway_trace_events(trace, response._gateway, job_id=job_id, case_id=case.id,
                session_id=session, turn=index, budget_id=budget.id, attempt=attempt))
        else:
            events.extend(trace_events(trace, job_id=job_id, case_id=case.id, session_id=session, turn=index,
                                       budget_id=budget.id, attempt=attempt))
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
        expected_entry = getattr(client, "required_entry", None)
        if expected_entry and (not response.adapter_evidence or not response.adapter_evidence.observed or any(
            getattr(response.adapter_evidence, key) != expected_entry[key] for key in ("path", "symbol", "source_hash")
        )):
            state, error = "error", "native_entry_not_observed"
            break
    if cancelled():
        state, error = "cancelled", "cancel_requested"
    sources = _sources(case, repository)
    telemetry = telemetry_summary(turns, state == "completed")
    usage = _usage(turns, budget, state == "completed")
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
        "usage": usage,
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
        "usage_summary": {key: usage[key] for key in ("provenance", "fields")},
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
    result = {
        "case_id": case.id,
        "description": case.description,
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
        "usage": usage,
        "telemetry": telemetry,
        "source_evidence": sources,
        "checks": evaluation.custom["checks"],
    }
    result["guidance"] = result_guidance(result)
    return result


def _summarize(body, suite, repository):
    rows = body["results"]
    body["completed"] = len(rows)
    body["quality"] = quality_summary(rows, suite, repository)
    body["curves"] = []
    for budget in suite.budgets:
        selected = [r for r in rows if r["budget_id"] == budget.id]
        planned = len(suite.cases) * suite.attempts
        passed = sum(r["outcome"] == "pass" for r in selected)
        failed = sum(r["outcome"] == "fail" for r in selected)
        usage = combine_usage([r["usage"] for r in selected], expected=planned)
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
                "reported_total_tokens": usage["fields"]["total_tokens"]["value"]
                if usage["fields"]["total_tokens"]["status"] == "complete" else None,
                "reported_cost_usd": usage["fields"]["cost_usd"]["value"]
                if usage["fields"]["cost_usd"]["status"] == "complete" else None,
                "usage": usage,
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


def _scheduled_cases(store, client, job_id, suite, repository, cancelled):
    """Bounded in-flight work; memory/multi-turn cases form serial barriers."""
    def serial(item):
        case, _, _ = item
        return len(case.turns) > 1 or case.category in {"memory", "session_isolation"}

    work = ((case, budget, attempt) for budget in suite.budgets for case in suite.cases
            for attempt in range(1, suite.attempts + 1))

    def execute(item):
        case, budget, attempt = item
        return _case(store, client, job_id, suite, case, budget, attempt, repository, cancelled)

    with ThreadPoolExecutor(max_workers=suite.concurrency, thread_name_prefix="assessment-case") as pool:
        for is_serial, group in groupby(work, serial):
            if cancelled():
                return
            if is_serial or suite.concurrency == 1:
                for item in group:
                    if cancelled():
                        return
                    yield execute(item)
                continue
            pending = set()
            exhausted, failed = False, False
            while pending or not exhausted:
                while not exhausted and not failed and not cancelled() and len(pending) < suite.concurrency:
                    item = next(group, None)
                    if item is None:
                        exhausted = True
                    else:
                        pending.add(pool.submit(execute, item))
                if not pending:
                    break
                ready, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in ready:
                    try:
                        result = future.result()
                    except Exception:
                        # Drain and persist other already-started cases before failing the job.
                        failed = True
                    else:
                        yield result
            if failed:
                raise RuntimeError("assessment_case_failed")


def run_assessment(db, job_id, target, suite, repository=None, *, transport=None, cancel_check=None):
    if not db.claim(job_id):
        return db.job(job_id)
    body = _body(db.job(job_id))
    body["started_at"] = now()
    db.update(job_id, body)

    def cancelled():
        return db.job(job_id)["cancel_requested"] or bool(cancel_check and cancel_check())

    state = "completed"
    binding = None
    try:
        if body.get("model_gateway"):
            if not target.deployment or not body.get("build_provenance", {}).get("native_entry"):
                raise TargetError("gateway_requires_generated_python_adapter")
            try:
                binding = db.gateway.bind(job_id, target)
            except (ValueError, OSError, RuntimeError):
                raise TargetError("gateway_configuration_or_start_failed") from None
        token = secrets.token_urlsafe(32) if target.deployment and target.deployment.service_token_variable else None
        options = {"runtime_environment": binding.environment} if binding else {}
        with deployed_target(target, resources=db, job_id=job_id, cancelled=cancelled, service_token=token, **options) as (endpoint, deployment):
            if body.get("build_provenance", {}).get("image_id") == deployment.get("image_id") and deployment.get("image_id"):
                deployment["source_binding"] = "built_from_checkout"
            body["deployment"] = deployment
            client = TargetClient(target, endpoint, transport=transport, token_override=token, gateway=binding)
            client.required_entry = body.get("build_provenance", {}).get("native_entry")
            if target.health_path and not target.deployment:
                body["health"] = client.health()
            db.update(job_id, body)
            order = {case.id: index for index, case in enumerate(suite.cases)}
            budgets = {budget.id: index for index, budget in enumerate(suite.budgets)}
            for result in _scheduled_cases(db.store, client, job_id, suite, repository, cancelled):
                body["results"].append(result)
                body["results"].sort(key=lambda r: (budgets[r["budget_id"]], order[r["case_id"]], r["attempt"]))
                _summarize(body, suite, repository)
                db.update(job_id, body)
    except TargetError as exc:
        state, body["error"] = "failed", exc.code
    except Exception:
        # Exceptions can include target content, credentials or operator paths.
        state, body["error"] = "failed", "assessment_internal_error"
    finally:
        if binding:
            db.gateway.release(binding)
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
        from .model_gateway import ModelGateway
        self.db.gateway = ModelGateway(store)
        self.db.gateway.db.recover()
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
        self.db.gateway.close()
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
    def safe(value):
        text = str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return re.sub(r"([\\`*{}\[\]()#+!|_])", r"\\\1", text).replace("\n", " ").replace("\r", " ")

    outcome_labels = {"pass": "通过", "fail": "未通过", "inconclusive": "证据不足", "unknown": "证据不足"}
    state_labels = {"completed": "已完成", "running": "执行中", "queued": "排队中", "failed": "执行异常",
                    "cancelled": "已取消", "interrupted": "已中断", "error": "调用异常", "timeout": "超时",
                    "budget_exhausted": "输出预算耗尽"}
    lines = [
        f"# Agent 主动评测：{job['suite_id']}",
        "",
        f"- 任务：{job['id']}",
        f"- 目标：{job['target_id']}；执行状态：{state_labels.get(job['state'], job['state'])}；示例：{'是' if job['demo'] else '否'}",
        f"- 完成：{job['completed']}/{job['planned']}",
    ]
    summary = job.get("quality", {}).get("summary")
    if job.get("gateway"):
        gateway = job["gateway"]
        lines += ["", "## 模型网关记录", "",
                  f"- 已保存请求：{gateway['request_count']}；未结束：{gateway['pending']}",
                  f"- 网关路径 Token：{format_usage(gateway['usage'], 'total_tokens')}",
                  "- 来源为网关采集的供应商报告；仅覆盖经过网关的请求，不与目标自报重复相加。"]
    if summary:
        lines += ["", "## 结论与下一步", "", f"**{summary['headline']}**", "",
                  f"通过 {summary['pass']} 次；未通过 {summary['fail']} 次；证据不足 {summary['inconclusive']} 次；"
                  f"未取得结果 {summary['pending']} 次。执行异常或取消 {summary['execution_issues']} 次。", "",
                  summary["scope_note"], ""]
        if job["demo"]:
            lines += ["本次为控制示例，仅用于校准评测流程。", ""]
        lines += [f"- {step}" for step in summary["next_steps"]]
        lines += ["", "题集覆盖（按计划）：" + "、".join(c["label"] for c in summary["covered_categories"])]
        if summary["uncovered_categories"]:
            lines += ["", "尚未覆盖（按需补测）：" + "、".join(c["label"] for c in summary["uncovered_categories"])]
        attention = [r for r in job["results"] if r["outcome"] != "pass"]
        if attention:
            lines += ["", "### 优先处理", "", "|案例 / 预算 / 次数|原因|下一步|", "|---|---|---|"]
            for row in attention:
                guidance = row.get("guidance") or result_guidance(row)
                lines.append(f"|{safe(row.get('description') or row['case_id'])} / {safe(row['budget_id'])} / {row['attempt']}|"
                             f"{safe(guidance['reason'])}|{safe(guidance['next_step'])}|")
    lines += ["",
        "## 预算与结果",
        "",
        "|预算|通过|失败|未知|通过率|平均观测耗时 ms|自报 Token|自报费用 USD|",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for curve in job["curves"]:
        tokens = format_usage(curve.get("usage", {"total_tokens": curve["reported_total_tokens"]}), "total_tokens")
        cost = format_usage(curve.get("usage", {"cost_usd": curve["reported_cost_usd"]}), "cost_usd")
        lines.append(
            f"|{curve['budget']['id']}|{curve['pass']}|{curve['fail']}|{curve['unknown']}|{curve['pass_rate']}%|{curve['mean_duration_ms']}|{tokens}|{cost}|"
        )
    if job.get("quality"):
        quality = job["quality"]
        lines += ["", "## 维度覆盖与质量", "",
                  f"案例并发上限：{job.get('concurrency', 1)}；多轮/记忆/隔离案例串行。", "",
                  "|维度 / 预算|完成 / 计划|通过 / 失败 / 未知|通过率范围|p50 / p95 ms|",
                  "|---|---:|---:|---:|---:|"]
        for row in quality["dimensions"]:
            lines.append(f"|{safe(row.get('label', row['category']))} / {safe(row['budget_id'])}|{row['observed']} / {row['planned']}|"
                         f"{row['pass']} / {row['fail']} / {row['unknown']}|"
                         f"{row['confirmed_pass_rate']}%–{row['possible_pass_rate']}%|"
                         f"{row['p50_duration_ms']} / {row['p95_duration_ms']}|")
        lines += ["", "### 重复稳定性", ""]
        for row in quality["stability"]:
            lines.append(f"- {row['case_id']} / {row['budget_id']}：{row['status']}，"
                         f"{row['observed']}/{row['planned']}；runs={','.join(row['run_ids'])}")
        lines += ["", "### 源码覆盖与漏测", ""]
        for gap in quality["source_coverage"]:
            lines.append(f"- {gap['kind']} {gap['name']}：{gap['status']}；{gap['path']}:{gap['line']}")
        evidence = quality["evidence"]
        lines += ["", f"- Token 已知的运行：{evidence['token_known_runs']}/{evidence['planned_runs']}",
                  f"- Token 部分记录：{evidence.get('token_partial_runs', 0)}；离线不适用：{evidence.get('token_not_applicable_runs', 0)}",
                  f"- 未知的必需检查：{evidence['unknown_required_checks']}/{evidence['required_checks']}"]
        lines += [f"- {v}" for v in quality["limitations"]]
    lines += ["", "## 独立任务验收与源码线索", ""]
    for result in job["results"]:
        lines += [
            f"### {safe(result.get('description') or result['case_id'])} / {safe(result['budget_id'])} / {result['attempt']}",
            "",
            f"{outcome_labels.get(result['outcome'], result['outcome'])} · "
            f"{state_labels.get(result['execution_state'], result['execution_state'])} · run_id={result['run_id']}",
            f"案例编号：{safe(result['case_id'])}",
            "",
        ]
        for check in result["checks"]:
            lines.append(f"- {safe(check.get('description') or check['id'])}："
                         f"{outcome_labels.get(check['status'], check['status'])} · {safe(check['explanation'])}")
            comparison = check.get("comparison")
            if comparison and check["status"] != "pass":
                lines += [f"  - 条件：{safe(comparison['path'])} / {safe(comparison['operator'])}",
                          f"  - 期望：{safe(comparison['expected'])}",
                          f"  - 实际返回：{safe(comparison['actual'])}"]
        usage = result.get("usage", {})
        telemetry = result.get("telemetry", {})
        def display(value):
            return "未知" if value is None else value
        lines += [
            f"- {'网关' if usage.get('provenance') == 'gateway_reported' else '自报'} Token：输入 {format_usage(usage, 'input_tokens')}；输出 {format_usage(usage, 'output_tokens')}；总计 {format_usage(usage, 'total_tokens')}",
            f"- 自报调用：模型 {display(telemetry.get('llm_calls'))}；工具 {display(telemetry.get('tool_calls'))}；覆盖 {telemetry.get('coverage', 'unavailable')}",
            f"- 自报错误：模型 {display(telemetry.get('llm_errors'))}；工具 {display(telemetry.get('tool_errors'))}",
            f"- 自报调用耗时合计 ms：模型 {display(telemetry.get('llm_duration_ms'))}；工具 {display(telemetry.get('tool_duration_ms'))}",
        ]
        for key in ("input_tokens", "output_tokens", "total_tokens", "cost_usd"):
            field = usage.get("fields", {}).get(key)
            if field:
                lines.append(f"- {key}：{field['status']} / {field['source']}；{field['reason']}")
        for key, label in (("reasoning_tokens", "推理"), ("cache_read_tokens", "缓存命中"),
                           ("cache_write_tokens", "缓存写入"), ("cache_miss_tokens", "缓存未命中")):
            field = usage.get("fields", {}).get(key, {})
            if field.get("value") is not None:
                lines.append(f"- {label} Token：{format_usage(usage, key)}（输入或输出细分，不重复加入总量）")
        for model in telemetry.get("models", []):
            model_usage = model.get("usage", {"total_tokens": model["tokens"]["total"]})
            lines.append(f"- 模型 {model['model']}：{model['calls']} 次；Token {format_usage(model_usage, 'total_tokens')}；耗时 {display(model['duration_ms'])} ms")
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
              f"- 仓库提交：{job['commit'] or '未知'}",
              f"- Suite SHA-256：{job['suite_hash']}",
              f"- 执行器 SHA-256：{job['engine_hash']}",
              f"- 目标配置 SHA-256：{job['target_hash']}",
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
        **({"gateway": db.gateway_records.snapshot(job["id"])} if job.get("model_gateway") else {}),
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
