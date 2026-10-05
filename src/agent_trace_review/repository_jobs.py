"""Repository URL -> fixed checkout -> Docker build -> existing assessment engine."""

import asyncio
import io
import json
import os
import re
import shutil
import zipfile
from importlib.resources import files
from pathlib import Path

from .assessment_contracts import AssessmentSuite, DockerDeployment, TargetDefinition
from .assessment_store import AssessmentStore
from .assessments import assessment_bundle, prepare_assessment, run_assessment
from .llm_review import ReviewError
from .repositories import SKIP, inspect_repository
from .repository_adaptation import (
    STANDARD_ENV,
    adaptation_capabilities,
    adaptation_config,
    generate_adapter,
    source_materials,
    stage_adapter,
    validate_draft,
)
from .repository_contracts import ENV_NAME, RepositoryAssessmentInput, RepositoryManifest
from .repository_planning import plan_repository
from .repository_process import RepositoryError, run_process
from .repository_store import RepositoryJobStore
from .server_config import repository_defaults, repository_model_config
from .suite_generation import generate_suite
from .target_client import TargetError, _docker, scrub
from .usage_accounting import combine_usage, summarize_usage
from .util import canonical, digest, now

MAX_SOURCE_BYTES = 200 * 1024 * 1024
MAX_SOURCE_FILES = 20000
SMOLAGENTS = "https://github.com/huggingface/smolagents"


def _git_env():
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0", "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_LFS_SKIP_SMUDGE": "1"}


def _docker_env():
    # No model/service credentials enter the build process, build args, or build context.
    return {k: v for k, v in os.environ.items() if k in {
        "PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH",
    }}


def source_limit(root):
    count = total = 0
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if not (Path(directory) / name).is_symlink()]
        for name in names:
            count += 1
            total += (Path(directory) / name).lstat().st_size
            if count > MAX_SOURCE_FILES or total > MAX_SOURCE_BYTES:
                raise RepositoryError("repository_size_limit")


def checkout_repository(request, root, command):
    root.mkdir(mode=0o700)
    base = ["git", "-c", f"core.hooksPath={os.devnull}", "-c", "core.fsmonitor=false",
            "-c", "protocol.allow=never", "-c", "protocol.https.allow=always", "-c", "http.followRedirects=false"]
    options = {"cwd": root, "env": _git_env(), "timeout": 120, "monitor": lambda: source_limit(root)}
    command([*base, "init", "--quiet"], **options)
    command([*base, "fetch", "--depth=1", "--no-tags", request.repository_url + ".git", request.ref], **options)
    commit = command([*base, "rev-parse", "--verify", "FETCH_HEAD^{commit}"], **options).strip()
    if not re.fullmatch(r"[a-f0-9]{40,64}", commit):
        raise RepositoryError("invalid_repository_commit")
    command([*base, "checkout", "--detach", "--force", commit], **options)
    source_limit(root)
    return commit


def safe_path(root, relative):
    path = root
    for part in Path(relative).parts:
        if part in SKIP or part == "..":
            raise RepositoryError("invalid_repository_path")
        path /= part
        if path.is_symlink():
            raise RepositoryError("repository_symlink_not_supported")
    if not path.exists() or not path.resolve().is_relative_to(root.resolve()):
        raise RepositoryError("repository_file_missing")
    return path


def copy_context(source, destination):
    """Stage only regular repository files, excluding Git state and common credential files."""
    destination.mkdir(parents=True, mode=0o700)
    inventory = []
    total = 0
    for directory, dirs, names in os.walk(source, followlinks=False):
        for name in list(dirs):
            path = Path(directory) / name
            if name in SKIP:
                dirs.remove(name)
            elif path.is_symlink():
                raise RepositoryError("repository_symlink_not_supported")
        for name in sorted(names):
            if name == ".git" or name.startswith(".env") and name != ".env.example":
                continue
            path = Path(directory) / name
            if path.is_symlink() or not path.is_file():
                raise RepositoryError("repository_symlink_not_supported")
            relative = path.relative_to(source)
            data = path.read_bytes()
            total += len(data)
            if total > MAX_SOURCE_BYTES or len(inventory) >= MAX_SOURCE_FILES:
                raise RepositoryError("repository_size_limit")
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o755 if path.stat().st_mode & 0o111 else 0o644)
            inventory.append((relative.as_posix(), digest(data), target.stat().st_mode & 0o777))
    return digest(sorted(inventory))


def _smolagents_file(source_name, bundled_name):
    bundled = files("agent_trace_review").joinpath("repository_templates/" + bundled_name)
    if bundled.is_file():
        return bundled.read_bytes()
    return (Path(__file__).resolve().parents[2] / "examples/smolagents" / source_name).read_bytes()


def smolagents_adapter():
    return _smolagents_file("agent_server.py", "smolagents_agent.py")


def smolagents_telemetry():
    return _smolagents_file("telemetry.py", "smolagents_telemetry.py")


def build_plan(request, root, work, *, commit=None, default_environment=None):
    if commit is not None and not re.fullmatch(r"[a-f0-9]{40,64}", commit):
        raise RepositoryError("invalid_checkout_commit")
    manifest_file = root / request.manifest_path
    recipe = request.recipe
    if recipe == "auto":
        recipe = "manifest" if manifest_file.exists() else "smolagents" if request.repository_url.lower() == SMOLAGENTS else "unsupported"
    context = work / "context"
    if recipe == "manifest":
        manifest_file = safe_path(root, request.manifest_path)
        if not manifest_file.is_file() or manifest_file.stat().st_size > 65536:
            raise RepositoryError("invalid_repository_manifest")
        manifest = RepositoryManifest.model_validate_json(manifest_file.read_bytes())
        origin = safe_path(root, manifest.context)
        if not origin.is_dir():
            raise RepositoryError("invalid_build_context")
        dockerfile = safe_path(origin, manifest.dockerfile)
        if not dockerfile.is_file():
            raise RepositoryError("dockerfile_missing")
        context_hash = copy_context(origin, context)
        plan = {"recipe": recipe, "manifest_hash": digest(manifest.model_dump()),
                "context_hash": context_hash, "dockerfile": manifest.dockerfile}
    elif recipe == "smolagents" and request.repository_url.lower() == SMOLAGENTS:
        context.mkdir(mode=0o700)
        source_hash = copy_context(root, context / "source")
        adapter = smolagents_adapter()
        (context / "agent_server.py").write_bytes(adapter)
        telemetry = smolagents_telemetry()
        (context / "telemetry.py").write_bytes(telemetry)
        dockerfile = (
            "FROM python:3.12-slim\nWORKDIR /app\nCOPY source /opt/smolagents\n"
            "RUN pip install --no-cache-dir '/opt/smolagents[openai]' 'fastapi>=0.115,<1' 'uvicorn>=0.34,<1' 'httpx>=0.28,<1'\n"
            "COPY agent_server.py /app/agent_server.py\n"
            "COPY telemetry.py /app/telemetry.py\n"
            f"ENV SMOL_SOURCE_COMMIT={commit or 'unknown'}\n"
            "ENV PYTHONDONTWRITEBYTECODE=1 HF_HOME=/tmp/huggingface\nUSER 65534:65534\nEXPOSE 9091\n"
            'ENTRYPOINT ["python", "-B", "/app/agent_server.py", "--host", "0.0.0.0", "--port", "9091", "--backend", '
            f'"{request.backend}"]\n'
        )
        (context / "Dockerfile").write_text(dockerfile)
        manifest = RepositoryManifest(manifest_version="agent-review/repository-v1", port=9091,
            memory_mb=1024, test_template="smolagents", demo=request.backend == "offline",
            service_token_variable="SMOL_SERVICE_TOKEN",
            health_status_field="backend", health_status_value=request.backend,
            required_environment=["SMOL_MODEL_API_BASE", "SMOL_MODEL_ID", "SMOL_MODEL_API_KEY"] if request.backend == "openai" else [])
        plan = {"recipe": recipe, "source_context_hash": source_hash, "adapter_hash": digest(adapter),
                "telemetry_hash": digest(telemetry),
                "context_hash": digest([source_hash, digest(adapter), digest(telemetry), dockerfile]), "dockerfile": "Dockerfile"}
    else:
        raise RepositoryError("unsupported_repository_requires_manifest")
    if request.suite is None and request.planning is None and manifest.test_template is None:
        raise RepositoryError("independent_suite_required")
    if request.backend == "openai" and default_environment:
        # Only provision recognized names explicitly required by this target's manifest.
        mappings = {key: default_environment[key] for key in manifest.required_environment if key in default_environment}
        request.environment = {**mappings, **request.environment}
        if len(request.environment) > 20:
            raise RepositoryError("too_many_environment_mappings")
    if any(key not in request.environment for key in manifest.required_environment):
        raise RepositoryError("required_environment_mapping_missing")
    for host_name in request.environment.values():
        value = os.environ.get(host_name)
        if value is None or "\n" in value or "\r" in value:
            raise RepositoryError("required_environment_unavailable")
    suite = request.suite or generate_suite(request.generation)
    return manifest, suite, plan


class RepositoryManager:
    def __init__(self, assessments, *, enabled=False, allowed_environment=()):
        self.assessments = assessments
        self.store = assessments.db.store
        self.db = RepositoryJobStore(self.store)
        self.enabled = enabled
        self.model_config = repository_model_config()
        self.defaults = repository_defaults()
        self.default_environment = self.model_config.mappings()
        if any(not re.fullmatch(ENV_NAME, name) for name in allowed_environment):
            raise ValueError("仓库环境白名单只接受变量名。")
        self.allowed_environment = frozenset(allowed_environment) | frozenset(self.default_environment.values())
        self.work_root = self.store.root / "repository-work"
        self.work_root.mkdir(mode=0o700, exist_ok=True)
        self.closed = False
        for job_id in self.db.active():
            self.db.patch(job_id, state="interrupted", error="service_restarted", finished_at=now())
        with self.store.connect() as conn:
            pending = [r[0] for r in conn.execute("SELECT id FROM repository_jobs WHERE json_extract(body, '$.cleanup')!='completed'")]
        for job_id in pending:
            self.cleanup(job_id)

    def capabilities(self):
        return {"enabled": self.enabled, "recipes": ["auto", "manifest", "smolagents", "llm"],
                "allowed_environment": sorted(self.allowed_environment), "providers": ["public_github_https"],
                "model": self.model_config.public(), "defaults": self.defaults, "adaptation": adaptation_capabilities(),
                "runtime": {"git_available": shutil.which("git") is not None, "docker_available": shutil.which("docker") is not None}}

    def resolve_request(self, request):
        if set(request.environment.values()) - self.allowed_environment:
            raise ValueError("环境变量未获管理员授权；配置 AGENT_REVIEW_REPOSITORY_ENV 允许的名称。")
        backend = self.defaults["backend"] if request.backend == "auto" else request.backend
        if request.recipe == "llm":
            if backend != "openai":
                raise ValueError("LLM 自动适配需要真实模型模式。")
            if not adaptation_capabilities()["enabled"]:
                raise ValueError("自动适配模型配置未就绪，请检查 .env 并重启服务。")
        environment = dict(request.environment)
        adaptation = request.adaptation.model_dump()
        if "adaptation" not in request.model_fields_set:
            adaptation["max_repairs"] = adaptation_capabilities()["default_repairs"]
        if backend == "openai":
            builtin = request.recipe != "manifest" and request.repository_url.lower() == SMOLAGENTS
            required = ("SMOL_MODEL_API_BASE", "SMOL_MODEL_ID", "SMOL_MODEL_API_KEY")
            manual = builtin and all(k in environment and os.environ.get(environment[k], "").strip() for k in required)
            if (request.backend == "auto" or builtin) and not manual and self.model_config.status != "configured":
                details = "、".join(self.model_config.missing)
                raise ValueError("真实模型配置未就绪，请检查服务端 .env 并重启服务。" + (f"缺少：{details}" if details else "API 地址或模型名格式无效。"))
            if builtin:
                environment = {**{k: self.default_environment[k] for k in required if k in self.default_environment}, **environment}
        return RepositoryAssessmentInput.model_validate({**request.model_dump(), "backend": backend, "environment": environment,
                                                         "adaptation": adaptation})

    def submit(self, request):
        if not self.enabled or self.closed:
            raise ValueError("仓库构建未启用；管理员需使用 --enable-repository-builds 启动服务。")
        request = self.resolve_request(request)
        job = self.db.create(request)
        try:
            self.assessments.executor.submit(self.run, job["id"])
        except RuntimeError:
            self.db.patch(job["id"], state="interrupted", error="service_closing", finished_at=now())
            raise ValueError("服务正在关闭。") from None
        return job

    def cleanup(self, job_id):
        job = self.db.get(job_id)
        if not re.fullmatch(r"repository_job_[a-f0-9]{32}", job_id):
            raise ValueError("Invalid internal repository job ID")
        error = None
        child_ids = list(dict.fromkeys([*job.get("validation_ids", []), *([job["assessment_id"]] if job.get("assessment_id") else [])]))
        for child_id in child_ids:
            with self.store.connect() as conn:
                pending = [r[0] for r in conn.execute(
                    "SELECT name FROM deployment_resources WHERE job_id=? AND state!='removed'", (child_id,))]
            for name in pending:
                try:
                    self.assessments.db.cleanup_resource(name)
                except TargetError:
                    error = "repository_container_cleanup_failed"
        if job.get("image_tag"):
            try:
                tag = "agent-inspect-repository:" + job_id
                if _docker("image", "ls", "--quiet", tag):
                    _docker("image", "rm", tag)
            except TargetError:
                error = error or "repository_image_cleanup_failed"
        try:
            work = self.work_root / job_id
            if work.exists():
                shutil.rmtree(work)
        except OSError:
            error = error or "repository_workspace_cleanup_failed"
        self.db.patch(job_id, cleanup="failed" if error else "completed", cleanup_error=error)
        return error

    def run(self, job_id):
        if not self.db.claim(job_id):
            self.cleanup(job_id)
            return self.db.get(job_id)
        request = RepositoryAssessmentInput.model_validate(self.db.get(job_id)["request"])
        work = self.work_root / job_id
        logs = []

        def cancelled():
            return self.closed or self.db.get(job_id)["cancel_requested"]

        def stage(value):
            if cancelled():
                raise RepositoryError("cancel_requested")
            self.db.patch(job_id, stage=value)

        def command(argv, **kwargs):
            try:
                text = run_process(argv, cancelled=cancelled, **kwargs)
            except RepositoryError as exc:
                save_log(exc.log)
                raise
            save_log(text)
            return text

        def save_log(text):
            secrets = [os.environ.get(v, "") for v in request.environment.values()]
            secrets += [os.environ.get(v, "") for v in ("AGENT_REVIEW_LLM_TOKEN", "AGENT_REVIEW_TARGET_TOKEN", "SMOL_MODEL_API_KEY")]
            artifact = self.store.put_artifact(scrub(text, secrets).encode(), "text/plain")
            logs.append({"stage": self.db.get(job_id)["stage"], "artifact_id": artifact})
            self.db.patch(job_id, log_artifacts=logs)

        def build_image(plan):
            stage("building")
            tag = "agent-inspect-repository:" + job_id
            self.db.patch(job_id, image_tag=tag)
            iidfile = work / "image-id"
            iidfile.unlink(missing_ok=True)
            command(["docker", "build", "--tag", tag, "--iidfile", str(iidfile),
                     "--label", f"agent-inspect.owner={self.assessments.db.resource_owner}",
                     "--label", f"org.opencontainers.image.revision={commit}",
                     "--file", plan["dockerfile"], "."], cwd=work / "context", env=_docker_env(), timeout=600)
            image_id = iidfile.read_text().strip()
            if not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
                raise RepositoryError("invalid_built_image_id")
            self.db.patch(job_id, image_id=image_id, source_binding="built_from_checkout")
            return image_id

        def make_target(manifest, image_id):
            return TargetDefinition(id="repo-" + digest(request.repository_url)[:20],
                deployment=DockerDeployment(image=image_id, port=manifest.port, user=manifest.user,
                    memory_mb=manifest.memory_mb, cpus=manifest.cpus, environment=request.environment,
                    service_token_variable=manifest.service_token_variable),
                task_path=manifest.task_path, health_path=manifest.health_path,
                health_status_field=manifest.health_status_field, health_status_value=manifest.health_status_value,
                repository=str(root), ref=commit, repository_url=request.repository_url, demo=manifest.demo)

        def adapt():
            if request.backend != "openai":
                raise RepositoryError("adaptation_requires_real_model")
            stage("adapting")
            try:
                config = adaptation_config()
            except (ReviewError, ValueError):
                raise RepositoryError("adaptation_model_not_configured") from None
            secrets = [config.token.get_secret_value(), *[os.environ.get(v, "") for v in request.environment.values()],
                       *[os.environ.get(v, "") for v in ("AGENT_REVIEW_TARGET_TOKEN", "SMOL_MODEL_API_KEY", "AGENT_REVIEW_LLM_TOKEN")]]
            materials = source_materials(root, secrets=secrets, max_chars=config.max_input_chars // 2)
            rounds, previous, feedback = [], None, None
            def save_adaptation():
                generated_turns = []
                for row in rounds:
                    usage = row.get("generation", {}).get("usage") or {}
                    generated_turns.append({"usage_mode": "model", "usage": {"tokens": {
                        "input": usage.get("prompt_tokens"), "output": usage.get("completion_tokens"), "total": usage.get("total_tokens")}}})
                generation_usage = summarize_usage(generated_turns)
                generation_usage["provenance"] = "exporter_reported"
                for field in generation_usage["fields"].values():
                    field["reason"] = ("适配模型 API 返回的用量，与被测 Agent 分开统计。" if field["status"] == "complete" else
                                       "部分适配请求未返回此字段，仅表示已观测下界。" if field["status"] == "partial" else
                                       "适配模型未返回此字段。")
                validation_usage = combine_usage([usage for row in rounds for usage in row.get("validation_usage", [])])
                artifact = self.store.put_artifact(canonical({"version": "agent-review/python-adaptation-v1",
                    "commit": commit, "source_hash": repository["source_hash"],
                    "source_materials": materials, "rounds": rounds, "generation_usage": generation_usage,
                    "validation_usage": validation_usage,
                    "limitations": ["入口观察由容器适配器自报，不构成抗恶意篡改的认证。",
                                     "生成代码仅在 Docker 内执行；框架、外部服务和硬件兼容性需实际验证。",
                                     "模型/工具轨迹按实际采集提供，完整性默认 partial。"]}).encode())
                self.db.patch(job_id, adaptation_artifact=artifact, adaptation_rounds=len(rounds),
                              adaptation_usage={"generation": generation_usage, "validation": validation_usage})
            save_adaptation()
            for index in range(request.adaptation.max_repairs + 1):
                stage("adapting" if index == 0 else "repairing")
                try:
                    draft, metadata, generation_error = asyncio.run(generate_adapter(materials, config,
                        previous=previous, feedback=feedback, cancelled=cancelled))
                except RepositoryError as exc:
                    rounds.append({"attempt": index + 1, "generation": {"model": config.model, "usage": None, "cost_usd": None},
                                   "status": "failed", "error": exc.code, "validation_id": None})
                    save_adaptation()
                    raise
                row = {"attempt": index + 1, "generation": metadata, "draft": draft.model_dump() if draft else None,
                       "status": "generated", "error": generation_error, "validation_id": None}
                rounds.append(row)
                save_adaptation()
                if draft and not draft.supported:
                    row.update(status="unsupported", error="adaptation_unsupported")
                    save_adaptation()
                    raise RepositoryError("adaptation_unsupported")
                try:
                    if generation_error:
                        raise RepositoryError(generation_error)
                    entry = validate_draft(draft, root, materials, STANDARD_ENV | set(request.environment))
                    manifest, plan = stage_adapter(draft, entry, root, work / "context", copy_source=copy_context)
                    request.environment = {**{key: self.default_environment[key] for key in manifest.required_environment
                                               if key in self.default_environment}, **request.environment}
                    if len(request.environment) > 20 or any(key not in request.environment for key in manifest.required_environment):
                        raise RepositoryError("required_environment_mapping_missing")
                    if any(not os.environ.get(v) or "\n" in os.environ[v] or "\r" in os.environ[v] for v in request.environment.values()):
                        raise RepositoryError("required_environment_unavailable")
                    self.db.patch(job_id, request=request.model_dump(), recipe="llm")
                    row["files"] = {name: (work / "context" / name).read_text() for name in
                                    ("bridge.py", "Dockerfile", "agent-review.json", "entry.json", "adapter-requirements.txt", "auto_runtime.py")}
                    save_adaptation()
                    image_id = build_image(plan)
                    stage("validating")
                    smoke = AssessmentSuite.model_validate({"id": "adapter-smoke", "cases": [{"id": "native-entry",
                        "turns": [{"prompt": "Return only a JSON object with ready=true."}],
                        "profile": {"profile_version": "1", "id": "adapter-smoke", "rules": [
                            {"id": "answer-present", "op": "exists", "path": "/output"}]}}],
                        "budgets": [{"id": "smoke", "deadline_seconds": 30, "max_output_tokens": 512}]})
                    target = make_target(manifest, image_id)
                    child, snapshot = prepare_assessment(self.assessments.db, target, smoke, repository=repository)
                    child_body = {k: v for k, v in child.items() if k not in {"id", "state", "created_at", "updated_at", "cancel_requested"}}
                    child_body.update(purpose="adapter_validation", build_provenance={**plan, "image_id": image_id,
                        "repository_job_id": job_id})
                    self.assessments.db.update(child["id"], child_body)
                    row["validation_id"] = child["id"]
                    validation_ids = [*self.db.get(job_id).get("validation_ids", []), child["id"]]
                    self.db.patch(job_id, validation_ids=validation_ids)
                    save_adaptation()
                    checked = run_assessment(self.assessments.db, child["id"], target, smoke, snapshot, cancel_check=cancelled)
                    row["validation_state"] = checked["state"]
                    row["validation_usage"] = [c["usage"] for c in checked.get("curves", [])]
                    row["validation_diagnostics"] = [
                        turn.get("adapter_evidence") for result in checked["results"]
                        for turn in self.store.get_run(result["run_id"]).artifacts.get("assessment", {}).get("turns", [])
                        if turn.get("adapter_evidence")]
                    if cancelled():
                        raise RepositoryError("cancel_requested")
                    if checked["state"] != "completed" or len(checked["results"]) != 1 or checked["results"][0]["execution_state"] != "completed":
                        reason = checked.get("error") or next((r["error"] for r in checked["results"] if r.get("error")), "adaptation_protocol_check_failed")
                        raise RepositoryError(reason)
                    row.update(status="validated", error=None)
                    save_adaptation()
                    plan["adaptation_artifact"] = self.db.get(job_id)["adaptation_artifact"]
                    return manifest, plan, image_id
                except (RepositoryError, ValueError, OSError) as exc:
                    if not isinstance(exc, RepositoryError):
                        exc = RepositoryError("adaptation_invalid_files")
                    row.update(status="failed", error=exc.code)
                    save_adaptation()
                    if exc.code in {"cancel_requested", "command_unavailable", "required_environment_unavailable",
                                    "required_environment_mapping_missing"} or index >= request.adaptation.max_repairs:
                        raise
                    # Only this attempt's bounded, scrubbed diagnostics go back to the model.
                    feedback = {"error": exc.code, "log": scrub(exc.log[-4000:], secrets),
                                "validation": row.get("validation_state"), "diagnostics": row.get("validation_diagnostics")}
                    previous = {"entry": draft.entry.model_dump() if draft and draft.entry else None,
                                "bridge_code": draft.bridge_code[-10000:] if draft else None}
            raise RepositoryError("adaptation_repair_limit")

        state, error = "completed", None
        try:
            request = self.resolve_request(request)
            self.db.patch(job_id, request=request.model_dump())
            work.mkdir(mode=0o700)
            stage("fetching")
            root = work / "checkout"
            commit = checkout_repository(request, root, command)
            self.db.patch(job_id, commit=commit)
            stage("inspecting")
            repository = self.assessments.db.save_repository(inspect_repository(root, commit, request.repository_url))
            generated = request.recipe == "llm" or (request.recipe == "auto" and request.adaptation.enabled
                and not (root / request.manifest_path).exists() and request.repository_url.lower() != SMOLAGENTS)
            image_id = None
            if generated:
                manifest, plan, image_id = adapt()
                # Generic source probes have independent programmatic answers, not LLM-supplied oracles.
                if request.suite is None and request.planning is None:
                    from .assessment_contracts import RepositoryPlanInput
                    request.planning = RepositoryPlanInput(cases=request.generation.cases, seed=request.generation.seed)
                suite = request.suite or generate_suite(request.generation)
            else:
                manifest, suite, plan = build_plan(request, root, work, commit=commit, default_environment=self.default_environment)
            self.db.patch(job_id, request=request.model_dump())
            if request.planning:
                assessment_plan = plan_repository(repository, request.planning)
                suite = AssessmentSuite.model_validate(assessment_plan["suite"])
                plan["assessment_plan_artifact"] = self.store.put_artifact(canonical(assessment_plan).encode())
            if request.settings:
                settings = request.settings
                raw = suite.model_dump()
                raw.update(attempts=settings.attempts, concurrency=settings.concurrency)
                raw["budgets"] = [{**b, "deadline_seconds": settings.deadline_seconds,
                                   "max_output_tokens": settings.max_output_tokens} for b in raw["budgets"]]
                suite = AssessmentSuite.model_validate(raw)
                if request.planning:
                    assessment_plan.update(suite=suite.model_dump(),
                        estimated_requests=sum(len(c.turns) for c in suite.cases) * suite.attempts * len(suite.budgets),
                        max_serial_deadline_seconds=len(suite.cases) * suite.attempts * len(suite.budgets) * settings.deadline_seconds)
                    plan["assessment_plan_artifact"] = self.store.put_artifact(canonical(assessment_plan).encode())
            plan.update({"repository_url": request.repository_url, "commit": commit,
                         "source_binding": "built_from_checkout", "dependency_locking": "repository_defined"})
            self.db.patch(job_id, recipe=plan["recipe"], provenance=plan, repository_id=repository["id"],
                          suite_hash=digest(suite.model_dump()), demo=manifest.demo)
            if image_id is None:
                image_id = build_image(plan)
            stage("assessing")
            target = make_target(manifest, image_id)
            child, snapshot = prepare_assessment(self.assessments.db, target, suite, repository=repository)
            body = {k: v for k, v in child.items() if k not in {"id", "state", "created_at", "updated_at", "cancel_requested"}}
            body["build_provenance"] = {**plan, "image_id": image_id, "repository_job_id": job_id}
            body["target_hash"] = digest({**target.model_dump(), "repository": request.repository_url})
            body["limitations"] = [v for v in body["limitations"] if "目标服务与源码版本" not in v]
            body["limitations"].append("记录本机从固定 checkout 构建的镜像；外部依赖及基础镜像由 Dockerfile 决定，未认证上游签名。")
            self.assessments.db.update(child["id"], body)
            self.db.patch(job_id, assessment_id=child["id"])
            result = run_assessment(self.assessments.db, child["id"], target, suite, snapshot, cancel_check=cancelled)
            state, error = result["state"], result.get("error")
        except RepositoryError as exc:
            state, error = ("cancelled" if exc.code == "cancel_requested" else "failed"), exc.code
        except (ValueError, OSError):
            state, error = "failed", "invalid_repository_configuration"
        except (KeyboardInterrupt, SystemExit):
            state, error = "interrupted", "process_interrupted"
            raise
        except Exception:
            state, error = "failed", "repository_internal_error"
        finally:
            previous = self.db.get(job_id)["stage"]
            self.db.patch(job_id, stage="cleaning", failed_stage=previous if error else None)
            cleanup_error = self.cleanup(job_id)
            if cleanup_error:
                state, error = "failed", error or cleanup_error
            if cancelled():
                state = "cancelled"
            self.db.patch(job_id, state=state, stage="finished", error=error, finished_at=now())
        return self.db.get(job_id)

    def bundle(self, job_id):
        return repository_bundle(self.store, job_id)

    def adapter_archive(self, job_id):
        job = self.db.get(job_id)
        artifact = job.get("adaptation_artifact")
        adaptation = json.loads(self.store.get_artifact(artifact)[0]) if artifact else {}
        selected = next((row for row in reversed(adaptation.get("rounds", [])) if row.get("files")), None)
        if not selected:
            raise ValueError("此任务没有可下载的生成适配文件。")
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, content in selected["files"].items():
                if name in {"bridge.py", "Dockerfile", "agent-review.json", "entry.json", "adapter-requirements.txt", "auto_runtime.py"}:
                    archive.writestr(name, content)
            archive.writestr("provenance.json", canonical({"repository_url": job["request"]["repository_url"],
                "commit": job["commit"], "source_hash": adaptation["source_hash"], "attempt": selected["attempt"],
                "status": selected["status"], "error": selected.get("error"), "limitations": adaptation["limitations"]}))
            archive.writestr("README.txt", "Generated adapter files; check provenance.json for validation status.\n"
                "The native checkout is NOT included. Place its fixed commit in ./source before building.\n"
                "Model credentials are supplied only at runtime. No .env file is included.\n")
        return output.getvalue()

    def close(self):
        self.closed = True
        for job_id in self.db.active():
            self.db.cancel(job_id)


def repository_bundle(store, job_id):
    job = RepositoryJobStore(store).get(job_id)
    assessments = AssessmentStore(store)
    plan_artifact = (job.get("provenance") or {}).get("assessment_plan_artifact")
    adaptation_artifact = job.get("adaptation_artifact")
    return {"repository_assessment_version": "agent-review/repository-assessment-v1", "job": job,
            "adaptation": json.loads(store.get_artifact(adaptation_artifact)[0]) if adaptation_artifact else None,
            "adapter_validations": [assessment_bundle(assessments, assessments.job(child_id))
                                    for child_id in job.get("validation_ids", [])],
            "assessment_plan": json.loads(store.get_artifact(plan_artifact)[0]) if plan_artifact else None,
            "assessment": assessment_bundle(assessments, assessments.job(job["assessment_id"])) if job["assessment_id"] else None,
            "logs": [{**item, "text": store.get_artifact(item["artifact_id"])[0].decode()}
                     for item in job["log_artifacts"]]}
