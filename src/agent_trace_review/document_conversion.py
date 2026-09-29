"""Built-in document candidates and explicit execution of the fixed evaluator."""

import base64
import json
import platform
from enum import Enum
from importlib.resources import files

from . import document_evaluator
from .document_evaluator import EVALUATOR_VERSION, RESOURCE, evaluate_document, fixture_json
from .profiles import evaluate_profile, evaluator_request, load_profile
from .service import ingest
from .storage import Store
from .util import canonical, digest


class DocumentCandidate(str, Enum):
    correct = "correct"
    omitted = "omitted"
    table_error = "table_error"
    order_error = "order_error"
    missing_reference = "missing_reference"
    all = "all"


def candidate_output(candidate):
    candidate = DocumentCandidate(candidate)
    if candidate == DocumentCandidate.all:
        raise ValueError("all 需要逐个运行候选。")
    # These candidate files are authored separately. Never copy answers out of the
    # evaluator's reference; simulated errors only alter the candidate snapshot.
    document = fixture_json("correct.json")
    markdown = files("agent_trace_review").joinpath(RESOURCE, "correct.md").read_text()
    if candidate == DocumentCandidate.omitted:
        omitted = next(b for b in document["blocks"] if b["id"] == "limitation")
        document["blocks"].remove(omitted)
        markdown = markdown.replace(omitted["text"] + "\n\n", "")
    elif candidate == DocumentCandidate.table_error:
        table = next(b for b in document["blocks"] if b["kind"] == "table")
        table["rows"][2][2] = "71"
        markdown = markdown.replace("| Tuesday | 10 | 17 |", "| Tuesday | 10 | 71 |")
    elif candidate == DocumentCandidate.order_error:
        blocks = document["blocks"]
        first, second = blocks[6]["text"], blocks[7]["text"]
        blocks[6], blocks[7] = blocks[7], blocks[6]
        markdown = markdown.replace(first + "\n\n" + second, second + "\n\n" + first)
    return {"document": document, "markdown": markdown}


def document_bundle(candidate):
    candidate = DocumentCandidate(candidate)
    output = candidate_output(candidate)
    reference = fixture_json("reference.json")
    content = files("agent_trace_review").joinpath(RESOURCE, "source.pdf").read_bytes()
    if digest(content) != reference["source_sha256"]:
        raise ValueError("内置 PDF 与固定参考哈希不匹配；请检查 fixture 文件。")
    evaluator_hash = digest(files("agent_trace_review").joinpath("document_evaluator.py").read_bytes())
    profile = load_profile(fixture_json("profile.json"))
    artifacts = {
        "source_description": "PDF 为自制数字文档；Markdown/JSON 是内置模拟候选；固定独立参考评估器实际执行，未调用转换 Agent、OCR 或模型。",
        "source_document": {
            "filename": "document-fixture.pdf",
            "kind": "digital-pdf",
            "media_type": "application/pdf",
            "sha256": digest(content),
            "page_count": reference["page_count"],
            "content_base64": base64.b64encode(content).decode(),
        },
        "evaluator": {"version": EVALUATOR_VERSION, "sha256": evaluator_hash},
        "evaluation_scope": reference["scope"],
    }
    if candidate != DocumentCandidate.missing_reference:
        artifacts["reference"] = reference
    return {
        "bundle_version": "generic/1",
        "trace": {
            "trace_version": "1",
            "framework": "document-conversion-fixture",
            "run_id": f"garden-water-{candidate.value}",
            "title": f"文档转换 · {candidate.value}",
            "task_prompt": "将指定两页 PDF 转成 Markdown 与带内容块 ID/页码的 JSON，保留正文、标题层级、表格及阅读顺序；排除页脚。",
            "agent_version": "builtin-document-candidate/1",
            "status": "completed",
            "coverage": "partial",
            "events": [],
            "demo": True,
            "output": output,
            "artifacts": artifacts,
        },
        "task": {
            "id": reference["document_id"],
            "version": "1",
            "prompt": "按固定独立标注验收 PDF 转换输出；范围见 document-fixture-basic Profile。",
            "initial_state_hash": digest(content),
            "final_state_hash": digest(output),
            "environment_hash": digest({"python": platform.python_version(), "evaluator": EVALUATOR_VERSION}),
            "suite_hash": digest(
                {"evaluator": evaluator_hash, "reference": reference, "profile": profile.model_dump()}
            ),
            "budget_policy": "fixed-fixture;local-deterministic-checks;no-model",
            "checks": [],
        },
    }


def evaluate_document_run(store: Store, run):
    """Explicit local evaluation; importing a bundle alone does not execute it."""
    profile = fixture_json("profile.json")
    request = evaluator_request(run, profile)
    results = evaluate_document(request)
    try:
        latest = store.get_evaluation(run.id)
        if (
            latest.custom
            and all(latest.custom.get(key) == request[key] for key in ("profile_hash", "input_hash"))
            and latest.custom.get("results") == results
        ):
            return latest
    except KeyError:
        pass
    return evaluate_profile(store, run, profile, results)


def run_document_candidate(store: Store, candidate):
    run, _, created = ingest(store, canonical(document_bundle(candidate)).encode())
    return run, evaluate_document_run(store, run), created


def write_evaluator_response(request_file, output_file):
    request = json.loads(request_file.read_text())
    output_file.write_text(canonical(document_evaluator.evaluate_document(request)) + "\n")
