"""Repository URL -> fixed checkout -> Docker build -> existing assessment engine."""

import os
import re
import shutil
from importlib.resources import files
from pathlib import Path

from .assessment_contracts import DockerDeployment, TargetDefinition
from .assessment_store import AssessmentStore
from .assessments import assessment_bundle, prepare_assessment, run_assessment
from .repositories import SKIP, inspect_repository
from .repository_contracts import RepositoryAssessmentInput, RepositoryManifest
from .repository_process import RepositoryError, run_process
from .repository_store import RepositoryJobStore
from .suite_generation import generate_suite
from .target_client import TargetError, _docker, scrub
from .util import digest, now

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


def build_plan(request, root, work, *, commit=None):
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
    if request.suite is None and manifest.test_template is None:
        raise RepositoryError("independent_suite_required")
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
        self.allowed_environment = frozenset(allowed_environment)
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
        return {"enabled": self.enabled, "recipes": ["auto", "manifest", "smolagents"],
                "allowed_environment": sorted(self.allowed_environment), "providers": ["public_github_https"]}

    def submit(self, request):
        if not self.enabled or self.closed:
            raise ValueError("仓库构建未启用；管理员需使用 --enable-repository-builds 启动服务。")
        if set(request.environment.values()) - self.allowed_environment:
            raise ValueError("环境变量未获管理员授权；配置 --repository-env 允许的名称。")
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
        if job.get("assessment_id"):
            with self.store.connect() as conn:
                pending = [r[0] for r in conn.execute(
                    "SELECT name FROM deployment_resources WHERE job_id=? AND state!='removed'", (job["assessment_id"],))]
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
            artifact = self.store.put_artifact(scrub(text, secrets).encode(), "text/plain")
            logs.append({"stage": self.db.get(job_id)["stage"], "artifact_id": artifact})
            self.db.patch(job_id, log_artifacts=logs)

        state, error = "completed", None
        try:
            work.mkdir(mode=0o700)
            stage("fetching")
            root = work / "checkout"
            commit = checkout_repository(request, root, command)
            self.db.patch(job_id, commit=commit)
            stage("inspecting")
            manifest, suite, plan = build_plan(request, root, work, commit=commit)
            repository = self.assessments.db.save_repository(inspect_repository(root, commit, request.repository_url))
            plan.update({"repository_url": request.repository_url, "commit": commit,
                         "source_binding": "built_from_checkout", "dependency_locking": "repository_defined"})
            self.db.patch(job_id, recipe=plan["recipe"], provenance=plan, repository_id=repository["id"],
                          suite_hash=digest(suite.model_dump()), demo=manifest.demo)
            stage("building")
            tag = "agent-inspect-repository:" + job_id
            self.db.patch(job_id, image_tag=tag)
            iidfile = work / "image-id"
            command(["docker", "build", "--tag", tag, "--iidfile", str(iidfile),
                     "--label", f"agent-inspect.owner={self.assessments.db.resource_owner}",
                     "--label", f"org.opencontainers.image.revision={commit}",
                     "--file", plan["dockerfile"], "."], cwd=work / "context", env=_docker_env(), timeout=600)
            image_id = iidfile.read_text().strip()
            if not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
                raise RepositoryError("invalid_built_image_id")
            self.db.patch(job_id, image_id=image_id, source_binding="built_from_checkout")
            stage("assessing")
            target = TargetDefinition(id="repo-" + digest(request.repository_url)[:20],
                deployment=DockerDeployment(image=image_id, port=manifest.port, user=manifest.user,
                    memory_mb=manifest.memory_mb, cpus=manifest.cpus, environment=request.environment,
                    service_token_variable=manifest.service_token_variable),
                task_path=manifest.task_path, health_path=manifest.health_path,
                health_status_field=manifest.health_status_field, health_status_value=manifest.health_status_value,
                repository=str(root), ref=commit, repository_url=request.repository_url, demo=manifest.demo)
            child, snapshot = prepare_assessment(self.assessments.db, target, suite)
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

    def close(self):
        self.closed = True
        for job_id in self.db.active():
            self.db.cancel(job_id)


def repository_bundle(store, job_id):
    job = RepositoryJobStore(store).get(job_id)
    assessments = AssessmentStore(store)
    return {"repository_assessment_version": "agent-review/repository-assessment-v1", "job": job,
            "assessment": assessment_bundle(assessments, assessments.job(job["assessment_id"])) if job["assessment_id"] else None,
            "logs": [{**item, "text": store.get_artifact(item["artifact_id"])[0].decode()}
                     for item in job["log_artifacts"]]}
