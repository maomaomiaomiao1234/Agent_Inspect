import json
import os
import subprocess
from importlib.resources import files
from pathlib import Path

import typer

from .analysis import compare
from .assessment_contracts import (
    AssessmentSuite,
    RepositoryPlanInput,
    SuiteGenerationInput,
    TargetDefinition,
    TargetRequest,
    TargetResponse,
)
from .assessment_store import AssessmentStore
from .assessments import (
    AssessmentManager,
    assessment_bundle,
    assessment_markdown,
    compare_assessments,
    load_targets,
    prepare_assessment,
    run_assessment,
)
from .code_repair import Candidate, DockerError, run_candidate
from .contracts import EvaluatorResponse, GenericBundle, GenericTrace, TaskProfile
from .demo import DemoScenario, scenario_bundles
from .document_conversion import (
    DocumentCandidate,
    evaluate_document_run,
    run_document_candidate,
    write_evaluator_response,
)
from .document_evaluator import DocumentOutput
from .judge import judge_run
from .llm_review import ReviewError, resolve_config, review_run
from .models import Evaluation, Run
from .profiles import evaluate_profile, evaluator_request
from .reports import markdown_report
from .repositories import compare_repositories, inspect_repository
from .repository_contracts import RepositoryAssessmentInput
from .repository_jobs import RepositoryManager, repository_bundle
from .repository_planning import plan_repository
from .repository_store import RepositoryJobStore
from .server_config import env_bool, load_server_env
from .service import ingest
from .storage import Store
from .suite_generation import generate_suite
from .util import canonical

app = typer.Typer(no_args_is_help=True, help="通用 agent / OpenCode 本地轨迹评估与自定义任务验收。")


@app.command("import")
def import_file(
    file: Path,
    data_dir: Path = Path(".agent-review"),
    task: Path | None = None,
    diff: Path | None = None,
    agent_version: str | None = None,
    first_message: str | None = None,
    last_message: str | None = None,
    profile: Path | None = None,
):
    try:
        run, evaluation, created = ingest(
            Store(data_dir),
            file.read_bytes(),
            filename=file.name,
            task=json.loads(task.read_text()) if task else None,
            diff=diff.read_text() if diff else None,
            agent_version=agent_version,
            first_message=first_message,
            last_message=last_message,
            profile=json.loads(profile.read_text()) if profile else None,
        )
        typer.echo(canonical({"run_id": run.id, "created": created, "outcome": evaluation.outcome}))
    except (ValueError, OSError) as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command("import-session")
def import_session(session_id: str, data_dir: Path = Path(".agent-review"), task: Path | None = None):
    """调用本机 opencode export 导入指定会话；不会启动模型。"""
    if not session_id.startswith("ses_") or not all(c.isalnum() or c == "_" for c in session_id):
        raise typer.BadParameter("需要有效的 OpenCode session ID。")
    try:
        result = subprocess.run(["opencode", "export", session_id, "--pure"], capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise typer.BadParameter(
            "无法运行 opencode export；请确认已安装 OpenCode 且命令在 PATH 中，或手动导入导出文件。"
        ) from exc
    if result.returncode:
        raise typer.BadParameter(result.stderr.decode(errors="replace"))
    run, evaluation, created = ingest(
        Store(data_dir), result.stdout, task=json.loads(task.read_text()) if task else None
    )
    typer.echo(canonical({"run_id": run.id, "created": created, "outcome": evaluation.outcome}))


@app.command()
def demo(data_dir: Path = Path(".agent-review"), scenario: DemoScenario | None = None):
    """导入合成轨迹；默认两例，--scenario all 加载通过、待补证据、失败三例。"""
    store = Store(data_dir)
    for bundle in scenario_bundles(scenario):
        run, evaluation, _ = ingest(store, canonical(bundle).encode())
        typer.echo(f"{run.id}  {evaluation.outcome:12}  {run.title} [合成示例]")


@app.command("list")
def list_runs(data_dir: Path = Path(".agent-review")):
    for run in Store(data_dir).list_runs():
        typer.echo(f"{run['id']}  {run['outcome']:12}  {run['title']}")


@app.command("document-conversion")
def document_conversion(
    candidate: DocumentCandidate = DocumentCandidate.correct, data_dir: Path = Path(".agent-review")
):
    """内置 PDF 转换候选 + 固定独立参考验收；不运行转换 Agent 或 OCR。"""
    candidates = (
        [c for c in DocumentCandidate if c != DocumentCandidate.all]
        if candidate == DocumentCandidate.all
        else [candidate]
    )
    store = Store(data_dir)
    for item in candidates:
        run, evaluation, _ = run_document_candidate(store, item)
        typer.echo(canonical({"candidate": item.value, "run_id": run.id, "outcome": evaluation.outcome}))


@app.command("document-evaluate")
def document_evaluate(run_id: str, data_dir: Path = Path(".agent-review")):
    """显式运行固定 PDF 评估器；用于重导入样例或已适配的同题候选。"""
    store = Store(data_dir)
    try:
        result = evaluate_document_run(store, store.get_run(run_id))
        typer.echo(canonical({"run_id": run_id, "outcome": result.outcome, "revision_id": result.id}))
    except (ValueError, KeyError) as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command("document-evaluator")
def document_evaluator(request: Path, output: Path = typer.Option(...)):
    """读取 evaluator-request，输出固定 PDF 评估器的 evaluator-v1 响应。"""
    try:
        write_evaluator_response(request, output)
        typer.echo(str(output.resolve()))
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command("code-repair")
def code_repair(candidate: Candidate = Candidate.correct, data_dir: Path = Path(".agent-review")):
    """内置候选模拟 + 实际 Docker 独立验证；需要已启动 Docker 和本地 python:3.12-slim。"""
    selected = [c for c in Candidate if c != Candidate.all] if candidate == Candidate.all else [candidate]
    store = Store(data_dir)
    try:
        for item in selected:
            run, evaluation, _ = run_candidate(store, item)
            typer.echo(canonical({"candidate": item.value, "run_id": run.id, "outcome": evaluation.outcome}))
    except (DockerError, OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command()
def report(
    run_id: str,
    data_dir: Path = Path(".agent-review"),
    format: str = "markdown",
    output: Path | None = None,
    revision: str | None = None,
):
    store = Store(data_dir)
    run, evaluation = store.get_run(run_id), store.get_evaluation(run_id, revision)
    if format == "markdown":
        content = markdown_report(run, evaluation)
    elif format == "bundle":
        content = canonical(store.bundle(run_id))
    elif format == "json":
        content = canonical({"run": run.model_dump(), "evaluation": evaluation.model_dump()})
    else:
        raise typer.BadParameter("format: markdown / json / bundle")
    if output:
        output.write_text(content)
    else:
        typer.echo(content)


@app.command("compare")
def compare_runs(left: str, right: str, data_dir: Path = Path(".agent-review")):
    store = Store(data_dir)
    typer.echo(
        canonical(
            compare(
                store.get_run(left),
                store.get_run(right),
                store.get_evaluation(left),
                store.get_evaluation(right),
            )
        )
    )


@app.command()
def judge(run_id: str, data_dir: Path = Path(".agent-review")):
    """发送限定的可见轨迹到已配置的 Judge 模型；可能产生 API 费用。"""
    store = Store(data_dir)
    try:
        evaluation = judge_run(store, store.get_run(run_id))
        typer.echo(canonical({"revision_id": evaluation.id, "judge": evaluation.judge}))
    except (ValueError, KeyError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    except Exception as exc:
        raise typer.BadParameter(f"Judge 调用失败（{type(exc).__name__}），原评估保留。") from exc


@app.command()
def schema(output: Path | None = None):
    content = canonical(schema_contracts())
    output.write_text(content) if output else typer.echo(content)


@app.command("llm-review")
def llm_review(
    run_id: str,
    profile: Path = typer.Option(...),
    data_dir: Path = Path(".agent-review"),
    api_url: str | None = None,
    model: str | None = None,
    token_env: str = "AGENT_REVIEW_LLM_TOKEN",
    max_output_tokens: int = 4096,
    timeout_seconds: float = 90,
    max_input_chars: int = 60000,
    token_parameter: str | None = None,
    json_mode: bool = True,
):
    """显式发送输入、输出与 Profile 到兼容 API；Token 从环境变量读取，可能产生费用。"""
    try:
        config = resolve_config(
            api_url=api_url,
            model=model,
            token=os.getenv(token_env, ""),
            max_output_tokens=max_output_tokens,
            timeout_seconds=timeout_seconds,
            max_input_chars=max_input_chars,
            token_parameter=token_parameter,
            json_mode=json_mode,
        )
        store = Store(data_dir)
        result = review_run(store, store.get_run(run_id), json.loads(profile.read_text()), config)
        typer.echo(
            canonical(
                {"run_id": run_id, "revision_id": result.id, "outcome": result.outcome, "judge": result.judge}
            )
        )
    except ReviewError as exc:
        raise typer.BadParameter(str(exc)) from None
    except (ValueError, OSError, KeyError):
        raise typer.BadParameter("无法读取运行或 Profile；请检查 run_id、数据目录与 JSON 文件。") from None


def schema_contracts():
    return {
        "run": Run.model_json_schema(),
        "evaluation": Evaluation.model_json_schema(),
        "generic_trace": GenericTrace.model_json_schema(),
        "generic_bundle": GenericBundle.model_json_schema(),
        "document_output": DocumentOutput.model_json_schema(),
        "task_profile": TaskProfile.model_json_schema(),
        "evaluator_response": EvaluatorResponse.model_json_schema(),
        "assessment_suite": AssessmentSuite.model_json_schema(),
        "suite_generation_input": SuiteGenerationInput.model_json_schema(),
        "target_definition": TargetDefinition.model_json_schema(),
        "target_request": TargetRequest.model_json_schema(),
        "target_response": TargetResponse.model_json_schema(),
    }


@app.command("evaluate")
def evaluate_run(
    run_id: str,
    profile: Path = typer.Option(...),
    results: Path | None = None,
    data_dir: Path = Path(".agent-review"),
):
    """按用户指定的 Profile 评估；可附加独立运行的自定义评估器结果。"""
    store = Store(data_dir)
    try:
        result = evaluate_profile(
            store,
            store.get_run(run_id),
            json.loads(profile.read_text()),
            json.loads(results.read_text()) if results else None,
        )
        typer.echo(canonical({"run_id": run_id, "revision_id": result.id, "outcome": result.outcome}))
    except (ValueError, OSError, KeyError) as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command("evaluator-request")
def export_evaluator_request(
    run_id: str,
    profile: Path = typer.Option(...),
    data_dir: Path = Path(".agent-review"),
    output: Path | None = None,
):
    """导出跨语言自定义评估器的输入；不运行任何外部代码。"""
    try:
        request = evaluator_request(Store(data_dir).get_run(run_id), json.loads(profile.read_text()))
        content = canonical(request)
        output.write_text(content) if output else typer.echo(content)
    except (ValueError, OSError, KeyError) as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command("init-task")
def init_task(directory: Path, template: str = "invoice"):
    """创建可修改的任务模板（invoice / research / llm-review）。"""
    if template not in {"invoice", "research", "llm-review"}:
        raise typer.BadParameter("template: invoice / research / llm-review")
    if directory.exists() and any(directory.iterdir()):
        raise typer.BadParameter("输出目录非空；请选择新目录，避免覆盖已有规则。")
    directory.mkdir(parents=True, exist_ok=True)
    source = files("agent_trace_review").joinpath("templates", template)
    for item in source.iterdir():
        if item.is_file() and not item.name.startswith("."):
            (directory / item.name).write_bytes(item.read_bytes())
    typer.echo(str(directory.resolve()))


@app.command()
def serve(
    data_dir: Path | None = None, port: int | None = None, host: str | None = None,
    targets: Path | None = None,
    env_file: Path | None = typer.Option(None, help="启动配置文件；默认读取当前目录 .env，已有进程变量优先。"),
    enable_repository_builds: bool | None = typer.Option(None, "--enable-repository-builds/--disable-repository-builds"),
    repository_env: list[str] = typer.Option([], help="允许仓库目标使用的宿主机环境变量名；可重复。"),
):
    import uvicorn

    from .api import create_app

    try:
        load_server_env(env_file)
        host = host or os.environ.get("AGENT_REVIEW_HOST", "127.0.0.1")
        port = port if port is not None else int(os.environ.get("AGENT_REVIEW_PORT", "8765"))
        if not 1 <= port <= 65535:
            raise ValueError("AGENT_REVIEW_PORT 需要 1–65535。")
        if enable_repository_builds is None:
            enable_repository_builds = env_bool("AGENT_REVIEW_ENABLE_REPOSITORY_BUILDS", host in {"127.0.0.1", "localhost", "::1"})
        repository_env = list(dict.fromkeys(repository_env + [v.strip() for v in
            os.environ.get("AGENT_REVIEW_REPOSITORY_ENV", "").split(",") if v.strip()]))
    except (ValueError, OSError):
        raise typer.BadParameter("无法加载启动配置，请检查 .env 文件和 Host/端口/构建开关。") from None
    if host not in {"127.0.0.1", "localhost", "::1"} and not os.environ.get("AGENT_REVIEW_SERVICE_TOKEN"):
        raise typer.BadParameter("监听非回环地址需要设置 AGENT_REVIEW_SERVICE_TOKEN。")
    uvicorn.run(create_app(str(data_dir) if data_dir is not None else None, targets_file=str(targets) if targets else None,
                          enable_repository_builds=enable_repository_builds, repository_environment=tuple(repository_env)),
                host=host, port=port)


@app.command("assess-repo")
def assess_repository(
    repository_url: str, ref: str = "HEAD", recipe: str = "auto", manifest: str = "agent-review.json",
    suite: Path | None = None, cases: int = typer.Option(12, min=1, max=30),
    seed: int = typer.Option(42, min=0, max=2147483647), backend: str = "offline",
    source_plan: bool = typer.Option(False, help="从固定源码规划受控题集；不能与 --suite 同用。"),
    attempts: int = typer.Option(1, min=1, max=3), concurrency: int = typer.Option(1, min=1, max=4),
    auto_adapt: bool = typer.Option(True, "--auto-adapt/--no-auto-adapt", help="未知 Python 仓库使用服务端 LLM 生成适配器。"),
    adaptation_repairs: int = typer.Option(1, min=0, max=2, help="自动适配最多修复次数。"),
    env: list[str] = typer.Option([], help="运行期环境变量 NAME=HOST_ENV；只传名称，不传密钥值。"),
    data_dir: Path = Path(".agent-review"), output: Path | None = None,
):
    """拉取、构建并评测；auto 优先清单/配方，再尝试 Python LLM 适配。"""
    manager = None
    repositories = None
    try:
        environment = {}
        for mapping in env:
            key, separator, value = mapping.partition("=")
            if not separator or key in environment:
                raise ValueError("env 需要不重复的 NAME=HOST_ENV。")
            environment[key] = value
        if suite and suite.stat().st_size > 1024 * 1024:
            raise ValueError("Suite 超过 1 MB。")
        request = RepositoryAssessmentInput(repository_url=repository_url, ref=ref, recipe=recipe,
            manifest_path=manifest, generation=SuiteGenerationInput(cases=cases, seed=seed), backend=backend,
            adaptation={"enabled": auto_adapt, "max_repairs": adaptation_repairs},
            planning=RepositoryPlanInput(cases=cases, seed=seed, attempts=attempts, concurrency=concurrency) if source_plan else None,
            environment=environment, suite=AssessmentSuite.model_validate_json(suite.read_bytes()) if suite else None)
        manager = AssessmentManager(Store(data_dir), {})
        repositories = RepositoryManager(manager, enabled=True, allowed_environment=environment.values())
        job = repositories.db.create(request)
        typer.echo(f"仓库任务：{job['id']}；开始拉取、构建和评测。", err=True)
        result = repositories.run(job["id"])
        content = canonical(repositories.bundle(job["id"]))
        output.write_text(content) if output else typer.echo(content)
        if result["state"] != "completed":
            raise typer.Exit(1)
    except (ValueError, OSError):
        raise typer.BadParameter("无法发起仓库评测；检查公开 GitHub 地址、配置、题集和环境变量映射。") from None
    finally:
        if repositories:
            repositories.close()
        if manager:
            manager.close()


@app.command("repo-inspect")
def repo_inspect(
    repository: Path, ref: str = "HEAD", repository_url: str | None = None,
    data_dir: Path = Path(".agent-review"), output: Path | None = None,
):
    """只读扫描固定 Git 提交或目录快照，记录声明、入口、依赖和文件行号。"""
    try:
        profile = AssessmentStore(Store(data_dir)).save_repository(inspect_repository(repository, ref, repository_url))
        content = canonical(profile)
        output.write_text(content) if output else typer.echo(content)
    except (ValueError, OSError):
        raise typer.BadParameter("无法读取仓库版本；检查路径、ref、源码 URL 和扫描上限。") from None


@app.command("repository-jobs")
def repository_jobs(data_dir: Path = Path(".agent-review")):
    """列出仓库任务，不执行恢复或重放。"""
    for job in RepositoryJobStore(Store(data_dir)).list():
        typer.echo(f"{job['id']}  {job['state']}  {job['stage']}  {job['request']['repository_url']}")


@app.command("repository-cancel")
def repository_cancel(job_id: str, data_dir: Path = Path(".agent-review")):
    """请求取消仓库拉取/构建/评测；后台执行器完成资源清理。"""
    try:
        store = Store(data_dir)
        job = RepositoryJobStore(store).cancel(job_id)
        if job["assessment_id"]:
            AssessmentStore(store).cancel(job["assessment_id"])
        typer.echo(canonical(job))
    except KeyError:
        raise typer.BadParameter("仓库任务不存在。") from None


@app.command("repository-report")
def repository_report(job_id: str, data_dir: Path = Path(".agent-review"), output: Path | None = None):
    """导出阶段日志、构建来源和评测证据包；不启动目标。"""
    try:
        content = canonical(repository_bundle(Store(data_dir), job_id))
        output.write_text(content) if output else typer.echo(content)
    except (KeyError, OSError):
        raise typer.BadParameter("无法导出；检查仓库任务 ID 和输出路径。") from None


@app.command("repo-compare")
def repo_compare(left: str, right: str, data_dir: Path = Path(".agent-review")):
    """比较两个已保存源码档案的新增、删除、修改文本文件。"""
    db = AssessmentStore(Store(data_dir))
    try:
        typer.echo(canonical(compare_repositories(db.repository(left), db.repository(right))))
    except KeyError:
        raise typer.BadParameter("仓库档案 ID 不存在。") from None


@app.command("assess")
def assess(
    target_id: str, suite: Path = typer.Option(...), targets: Path = typer.Option(...),
    data_dir: Path = Path(".agent-review"), output: Path | None = None,
):
    """向登记的真实 HTTP 目标发起固定多轮任务；标准答案留在评审端。"""
    manager = None
    try:
        registry = load_targets(targets)
        if target_id not in registry:
            raise ValueError("目标未登记。")
        if suite.stat().st_size > 1024 * 1024:
            raise ValueError("Suite 超过 1 MB。")
        contract = AssessmentSuite.model_validate_json(suite.read_bytes())
        manager = AssessmentManager(Store(data_dir), registry)
        db = manager.db
        target = registry[target_id]
        job, repository = prepare_assessment(db, target, contract)
        result = run_assessment(db, job["id"], target, contract, repository)
        content = canonical(result)
        output.write_text(content) if output else typer.echo(content)
        if result["state"] != "completed":
            raise typer.Exit(1)
    except (ValueError, OSError):
        raise typer.BadParameter("无法启动评测；检查目标登记、Suite、源码引用和队列容量。") from None
    finally:
        if manager:
            manager.close()


@app.command("generate-suite")
def generate_assessment_suite(
    output: Path = Path("suite.generated.json"), template: str = "smolagents",
    cases: int = typer.Option(12, min=1, max=30), seed: int = typer.Option(42, min=0, max=2147483647),
    attempts: int = typer.Option(1, min=1, max=3), concurrency: int = typer.Option(1, min=1, max=4),
):
    """生成 general 或 smolagents 题集与独立答案；不调用模型，不覆盖已有文件。"""
    try:
        suite = generate_suite(SuiteGenerationInput(template=template, cases=cases, seed=seed,
                                                    attempts=attempts, concurrency=concurrency))
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(suite.model_dump(), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        typer.echo(str(output.resolve()))
    except FileExistsError:
        raise typer.BadParameter("输出文件已存在，请选择新文件名以保留已有题集。") from None
    except (ValueError, OSError):
        raise typer.BadParameter("无法生成题集；template 需为 general 或 smolagents，请检查参数、累计期限（≤900 秒）与输出路径。") from None


@app.command("plan-repository")
def plan_repository_command(
    repository: Path, output: Path = Path("suite.repository.json"), ref: str = "HEAD",
    cases: int = typer.Option(12, min=1, max=30), seed: int = typer.Option(42, min=0, max=2147483647),
    attempts: int = typer.Option(1, min=1, max=3), concurrency: int = typer.Option(1, min=1, max=4),
):
    """只读源码，生成绑定快照的题集文件；评测计划和漏测项输出到终端。"""
    try:
        repository_profile = inspect_repository(repository, ref)
        plan = plan_repository(repository_profile, RepositoryPlanInput(
            cases=cases, seed=seed, attempts=attempts, concurrency=concurrency))
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(plan["suite"], ensure_ascii=False, indent=2) + "\n")
        typer.echo(canonical(plan))
    except FileExistsError:
        raise typer.BadParameter("输出文件已存在，请选择新文件名。") from None
    except (ValueError, OSError):
        raise typer.BadParameter("无法规划题集；检查仓库路径、ref、扫描限制和输出路径。") from None


@app.command("assessments")
def assessment_list(data_dir: Path = Path(".agent-review"), limit: int = typer.Option(50, min=1, max=100)):
    """列出主动评测任务及已完成案例数。"""
    for job in AssessmentStore(Store(data_dir)).list_jobs(limit, summary=True):
        typer.echo(f"{job['id']}  {job['state']}  {job['completed']}/{job['planned']}  {job['target_id']}")


@app.command("assessment-report")
def assessment_report(
    job_id: str, data_dir: Path = Path(".agent-review"), format: str = "markdown", output: Path | None = None,
):
    """导出完整主动评测 Markdown 或 JSON 报告。"""
    try:
        db = AssessmentStore(Store(data_dir))
        job = db.job(job_id)
        if format == "markdown":
            content = assessment_markdown(job)
        elif format == "json":
            content = canonical(job)
        elif format == "bundle":
            content = canonical(assessment_bundle(db, job))
        else:
            raise ValueError("format: markdown / json / bundle")
        output.write_text(content) if output else typer.echo(content)
    except (ValueError, OSError, KeyError):
        raise typer.BadParameter("无法导出评测；检查 ID、format 和输出路径。") from None


@app.command("assessment-compare")
def assessment_compare(left: str, right: str, data_dir: Path = Path(".agent-review")):
    """相同冻结题目、验收和预算下，对比同一目标两次评测的回归。"""
    db = AssessmentStore(Store(data_dir))
    try:
        typer.echo(canonical(compare_assessments(db.job(left), db.job(right))))
    except KeyError:
        raise typer.BadParameter("评测 ID 不存在。") from None


@app.command("assessment-cancel")
def assessment_cancel(job_id: str, data_dir: Path = Path(".agent-review")):
    """请求取消；正在进行的调用会在当前 deadline 内退出，保存已有结果。"""
    try:
        typer.echo(canonical(AssessmentStore(Store(data_dir)).cancel(job_id)))
    except KeyError:
        raise typer.BadParameter("评测 ID 不存在。") from None


if __name__ == "__main__":
    app()
