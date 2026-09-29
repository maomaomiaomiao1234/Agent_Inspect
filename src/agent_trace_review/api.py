import json
import os
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, SecretStr, ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .analysis import analyze, compare
from .contracts import EvaluatorResponse, GenericBundle, GenericTrace, TaskProfile
from .demo import DemoScenario, scenario_bundles
from .document_conversion import DocumentCandidate, evaluate_document_run, run_document_candidate
from .document_evaluator import DocumentOutput, source_pdf
from .judge import configuration, judge_run
from .llm_review import ReviewError, resolve_config, review_run
from .llm_review import configuration as llm_configuration
from .models import Evaluation, Run, Task
from .opencode import MAX_IMPORT
from .profiles import evaluate_profile, evaluator_request
from .reports import junit_report, markdown_report
from .service import ingest
from .storage import Store
from .util import canonical


class ComparisonInput(BaseModel):
    left_id: str
    right_id: str
    left_revision: str | None = None
    right_revision: str | None = None


class JudgeInput(BaseModel):
    send_trace: bool = False


class ProfileInput(BaseModel):
    profile: TaskProfile
    results: EvaluatorResponse | None = None


class LLMReviewInput(BaseModel):
    send_data: bool = False
    profile: TaskProfile
    api_url: str | None = None
    model: str | None = None
    token: SecretStr | None = None
    max_output_tokens: int = 4096
    timeout_seconds: float = 90
    max_input_chars: int = 60000
    token_parameter: str | None = None
    json_mode: bool = True


def read_upload(file: UploadFile, limit: int) -> bytes:
    content = file.file.read(limit + 1)
    if len(content) > limit:
        raise ValueError(f"{file.filename or '文件'} 超过 {limit // (1024 * 1024)} MB。")
    return content


def create_app(data_dir: str | None = None, static_dir: str | None = None) -> FastAPI:
    store = Store(data_dir or os.environ.get("AGENT_REVIEW_DATA", ".agent-review"))
    app = FastAPI(title="Agent Trace Review", version="0.3.0")
    app.state.store = store
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])

    @app.middleware("http")
    async def local_writes(request: Request, call_next):
        if (
            request.method in {"POST", "PUT", "DELETE", "PATCH"}
            and request.headers.get("x-review-request") != "1"
        ):
            return Response("Missing X-Review-Request header", status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.exception_handler(KeyError)
    async def not_found(request, exc):
        return Response("Run, revision or artifact not found", status_code=404)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        if request.url.path.endswith("/llm-review"):
            # Pydantic's default error payload may echo the full body, including a token.
            return Response("评审请求格式无效；请检查 Profile 和模型配置。", status_code=422)
        return await request_validation_exception_handler(request, exc)

    @app.get("/api/health")
    def health():
        return {
            "status": "ok",
            "version": "0.3.0",
            "adapter": "OpenCode / generic trace v1",
            "data_dir": str(store.root),
        }

    @app.get("/api/runs")
    def list_runs():
        return store.list_runs()

    @app.post("/api/document-demo")
    def document_demo(candidate: DocumentCandidate = DocumentCandidate.correct):
        selected = (
            [c for c in DocumentCandidate if c != DocumentCandidate.all]
            if candidate == DocumentCandidate.all
            else [candidate]
        )
        return {"run_ids": [run_document_candidate(store, item)[0].id for item in selected]}

    @app.post("/api/runs/{run_id}/document-evaluation")
    def document_evaluation(run_id: str):
        return evaluate_document_run(store, store.get_run(run_id))

    @app.get("/api/runs/{run_id}/document.pdf")
    def document_source(run_id: str):
        try:
            content = source_pdf(store.get_run(run_id).artifacts)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return Response(
            content,
            media_type="application/pdf",
            headers={
                "Content-Disposition": 'attachment; filename="document-fixture.pdf"',
            },
        )

    @app.get("/api/judge/config")
    def judge_config():
        return configuration()

    @app.get("/api/llm-review/config")
    def llm_config():
        return llm_configuration()

    @app.post("/api/runs/{run_id}/llm-review")
    def task_review(run_id: str, body: LLMReviewInput):
        if not body.send_data:
            raise HTTPException(422, "请显式设置 send_data=true，发送任务材料并进行模型评审。")
        run = store.get_run(run_id)
        try:
            config = resolve_config(**body.model_dump(exclude={"send_data", "profile"}, exclude_none=True))
            return review_run(store, run, body.profile, config)
        except ReviewError as exc:
            raise HTTPException(422, str(exc)) from None
        except Exception:
            raise HTTPException(502, "大模型评审未完成，原有评估保留。") from None

    @app.post("/api/runs/{run_id}/judge")
    def judge(run_id: str, body: JudgeInput):
        if not body.send_trace:
            raise HTTPException(422, "请显式选择发送可见轨迹摘录到已配置的 Judge 模型。")
        run = store.get_run(run_id)
        try:
            return judge_run(store, run)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except Exception as exc:
            # Provider exceptions can contain credentials, URLs and source text.
            raise HTTPException(
                502, f"Judge 调用未完成（{type(exc).__name__}），原有评估保留。检查模型配置或稍后重试。"
            ) from exc

    @app.post("/api/imports")
    def import_run(
        file: UploadFile = File(...),
        task_file: UploadFile | None = File(None),
        diff_file: UploadFile | None = File(None),
        junit_file: UploadFile | None = File(None),
        profile_file: UploadFile | None = File(None),
        agent_version: str | None = Form(None),
        first_message: str | None = Form(None),
        last_message: str | None = Form(None),
    ):
        try:
            content = read_upload(file, MAX_IMPORT)
            task = json.loads(read_upload(task_file, 1024 * 1024)) if task_file else None
            profile = json.loads(read_upload(profile_file, 1024 * 1024)) if profile_file else None
            diff = read_upload(diff_file, 20 * 1024 * 1024).decode() if diff_file else None
            if junit_file:
                payload = json.loads(content)
                if not isinstance(payload, dict):
                    raise ValueError("导出文件必须是 JSON 对象。")
                if "trace_version" in payload or payload.get("bundle_version") == "generic/1":
                    raise ValueError("通用轨迹附加 JUnit 需要明确使用 generic/2 运行包。")
                bundle = (
                    payload if "bundle_version" in payload else {"bundle_version": "1", "export": payload}
                )
                manifest = task or bundle.get("task")
                if manifest is not None:
                    manifest = Task.model_validate(manifest).model_dump()
                if (
                    not isinstance(manifest, dict)
                    or not isinstance(manifest.get("checks"), list)
                    or len(manifest["checks"]) != 1
                    or not isinstance(manifest["checks"][0], dict)
                    or manifest["checks"][0].get("kind", "test") != "test"
                    or not manifest.get("final_state_hash")
                    or not manifest.get("suite_hash")
                ):
                    raise ValueError(
                        "上传 JUnit 时需提供包含唯一 check、final_state_hash、suite_hash 的 Task Manifest。多检查请使用运行包。"
                    )
                report = junit_report(
                    read_upload(junit_file, 20 * 1024 * 1024),
                    manifest["checks"][0]["id"],
                    manifest["final_state_hash"],
                    manifest["suite_hash"],
                )
                bundle["verifications"] = [*bundle.get("verifications", []), report]
                content = canonical(bundle).encode()
            run, evaluation, created = ingest(
                store,
                content,
                filename=file.filename or "export.json",
                task=task,
                diff=diff,
                agent_version=agent_version,
                first_message=first_message,
                last_message=last_message,
                profile=profile,
            )
            return {"run_id": run.id, "revision_id": evaluation.id, "created": created}
        except (
            ValueError,
            ValidationError,
            UnicodeDecodeError,
            TypeError,
            RecursionError,
            AttributeError,
        ) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/demo")
    def demo(scenario: DemoScenario | None = None):
        runs = [ingest(store, canonical(bundle).encode())[0].id for bundle in scenario_bundles(scenario)]
        return {"run_ids": runs, "synthetic": True}

    @app.get("/api/runs/{run_id}")
    def run_detail(run_id: str, revision: str | None = None):
        run = store.get_run(run_id)
        evaluation = store.get_evaluation(run_id, revision)
        return {
            "run": run.model_dump(exclude={"events"}),
            "evaluation": evaluation.model_dump(),
            "event_count": len(run.events),
        }

    @app.get("/api/runs/{run_id}/revisions")
    def revisions(run_id: str):
        store.get_run(run_id)
        return store.revisions(run_id)

    @app.get("/api/runs/{run_id}/events")
    def events(
        run_id: str,
        offset: int = Query(0, ge=0),
        limit: int = Query(200, ge=1, le=1000),
        kind: str | None = None,
        search: str | None = None,
    ):
        items = store.get_run(run_id).events
        if kind:
            items = [e for e in items if e.kind == kind or kind in e.stage]
        if search:
            query = search.casefold()
            items = [e for e in items if query in (e.title + e.output + canonical(e.input)).casefold()]
        return {
            "items": [e.model_dump() for e in items[offset : offset + limit]],
            "total": len(items),
            "next_offset": offset + limit if offset + limit < len(items) else None,
        }

    @app.get("/api/runs/{run_id}/events/{event_id}")
    def event_detail(run_id: str, event_id: str):
        item = next((e for e in store.get_run(run_id).events if e.id == event_id), None)
        if not item:
            raise HTTPException(404, "Event not found")
        return item

    @app.get("/api/artifacts/{artifact_id}")
    def artifact(artifact_id: str):
        content, media_type = store.get_artifact(artifact_id)
        return Response(content, media_type="text/plain", headers={"Content-Disposition": "inline"})

    @app.post("/api/runs/{run_id}/evaluations")
    def evaluate(run_id: str):
        result = analyze(store.get_run(run_id))
        store.save_evaluation(result)
        return result

    @app.post("/api/runs/{run_id}/evaluator-request")
    def custom_request(run_id: str, profile: TaskProfile):
        return evaluator_request(store.get_run(run_id), profile)

    @app.post("/api/runs/{run_id}/profile-evaluations")
    def custom_evaluation(run_id: str, body: ProfileInput):
        try:
            return evaluate_profile(
                store,
                store.get_run(run_id),
                body.profile,
                body.results.model_dump() if body.results else None,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/comparisons")
    def compare_runs(body: ComparisonInput):
        return compare(
            store.get_run(body.left_id),
            store.get_run(body.right_id),
            store.get_evaluation(body.left_id, body.left_revision),
            store.get_evaluation(body.right_id, body.right_revision),
        )

    @app.get("/api/runs/{run_id}/export")
    def export(run_id: str, format: str = "markdown", revision: str | None = None):
        run, evaluation = store.get_run(run_id), store.get_evaluation(run_id, revision)
        if format == "markdown":
            content, ext = markdown_report(run, evaluation), "md"
        elif format == "json":
            content, ext = canonical({"run": run.model_dump(), "evaluation": evaluation.model_dump()}), "json"
        elif format == "bundle":
            content, ext = canonical(store.bundle(run_id)), "bundle.json"
        else:
            raise HTTPException(422, "format must be markdown, json or bundle")
        return Response(
            content,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{run_id}.{ext}"'},
        )

    @app.get("/api/schema")
    def schema():
        return {
            "run": Run.model_json_schema(),
            "evaluation": Evaluation.model_json_schema(),
            "generic_trace": GenericTrace.model_json_schema(),
            "generic_bundle": GenericBundle.model_json_schema(),
            "document_output": DocumentOutput.model_json_schema(),
            "task_profile": TaskProfile.model_json_schema(),
            "evaluator_response": EvaluatorResponse.model_json_schema(),
        }

    web = Path(static_dir) if static_dir else Path(__file__).parent / "static"
    if not web.is_dir():
        web = Path(__file__).resolve().parents[2] / "web" / "dist"
    if web.is_dir():
        app.mount("/assets", StaticFiles(directory=web / "assets"), name="assets")

        @app.get("/{path:path}")
        def frontend(path: str):
            if path.startswith("api/"):
                raise HTTPException(404)
            return FileResponse(web / "index.html")

    return app
