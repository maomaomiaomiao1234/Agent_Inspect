import json
import os
import subprocess
from importlib.resources import files
from pathlib import Path

import typer

from .analysis import compare
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
from .service import ingest
from .storage import Store
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
def serve(data_dir: Path = Path(".agent-review"), port: int = 8765):
    import uvicorn

    from .api import create_app

    uvicorn.run(create_app(str(data_dir)), host="127.0.0.1", port=port)


if __name__ == "__main__":
    app()
