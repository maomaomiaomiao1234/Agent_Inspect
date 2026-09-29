import base64
import copy
import json
from importlib.resources import files

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from agent_trace_review.api import create_app
from agent_trace_review.cli import app
from agent_trace_review.document_conversion import (
    document_bundle,
    evaluate_document_run,
    run_document_candidate,
)
from agent_trace_review.document_evaluator import (
    RESOURCE,
    evaluate_document,
    fixture_json,
    normalize,
    source_pdf,
)
from agent_trace_review.profiles import MISSING, evaluate_profile, evaluator_request, pointer
from agent_trace_review.reports import markdown_report
from agent_trace_review.service import ingest
from agent_trace_review.storage import Store
from agent_trace_review.util import canonical, digest


def checks(evaluation):
    return {item["id"]: item["status"] for item in evaluation.custom["checks"]}


@pytest.mark.parametrize(
    "candidate,outcome,failures",
    [
        ("correct", "pass", set()),
        ("omitted", "fail", {"document_text", "document_completeness", "document_order"}),
        ("table_error", "fail", {"document_tables"}),
        ("order_error", "fail", {"document_order"}),
        ("missing_reference", "inconclusive", set()),
    ],
)
def test_document_scenarios_and_evidence(store, candidate, outcome, failures):
    run, result, created = run_document_candidate(store, candidate)
    assert created and result.outcome == outcome
    statuses = checks(result)
    assert {key for key, value in statuses.items() if value == "fail"} == failures
    assert statuses["document_formulas"] == statuses["document_ocr"] == "unknown"
    if candidate == "missing_reference":
        assert set(statuses.values()) == {"unknown"}
    assert run.demo and run.framework == "document-conversion-fixture" and not run.usage
    for key in ["tokens_total", "cost_usd", "duration_ms"]:
        assert next(m for m in result.metrics if m.key == key).value is None
    request = evaluator_request(run, fixture_json("profile.json"))
    for record in result.custom["results"]["results"]:
        if record["status"] != "unknown":
            assert record["evidence_paths"]
        for path in record["evidence_paths"]:
            assert pointer(request["context"], path) is not MISSING
    refs = {e.id for e in result.evidence}
    for item in result.custom["checks"]:
        assert set(item["evidence_ids"]) <= refs
    assert run.task.initial_state_hash == digest(source_pdf(run.artifacts))
    report = markdown_report(run, result)
    assert "独立参考" in report and "任务验收" in report and "未覆盖" in report
    again, repeated, created = run_document_candidate(store, candidate)
    assert again.id == run.id and not created and repeated.id == result.id
    if candidate == "table_error":
        assert (
            "行3列3"
            in next(c for c in result.custom["checks"] if c["id"] == "document_tables")["explanation"]
        )


def evaluate_bundle(store, bundle, profile=None):
    run = ingest(store, canonical(bundle).encode())[0]
    profile = profile or fixture_json("profile.json")
    response = evaluate_document(evaluator_request(run, profile))
    return run, evaluate_profile(store, run, profile, response)


@pytest.mark.parametrize(
    "tamper", ["reference", "missing_source", "base64", "hash", "different_pdf", "scan", "missing_output"]
)
def test_missing_or_untrusted_material_never_passes(store, tamper):
    bundle = document_bundle("correct")
    artifacts = bundle["trace"]["artifacts"]
    if tamper == "reference":
        bundle["trace"]["output"]["document"]["blocks"][1]["text"] = "invented"
        artifacts["reference"]["blocks"][1]["text"] = "invented"
    elif tamper == "missing_source":
        del artifacts["source_document"]
    elif tamper == "base64":
        artifacts["source_document"]["content_base64"] = "not base64"
    elif tamper == "hash":
        artifacts["source_document"]["sha256"] = "a" * 64
    elif tamper == "different_pdf":
        different = b"%PDF-1.4\nnot the fixed fixture"
        artifacts["source_document"].update(
            content_base64=base64.b64encode(different).decode(), sha256=digest(different)
        )
        artifacts["reference"]["source_sha256"] = digest(different)
    elif tamper == "scan":
        artifacts["source_document"]["kind"] = "scanned-pdf"
    else:
        del bundle["trace"]["output"]
    _, evaluation = evaluate_bundle(store, bundle)
    assert evaluation.outcome == "inconclusive"
    assert "pass" not in checks(evaluation).values()


@pytest.mark.parametrize(
    "mutation,failed_check",
    [
        ("duplicate_id", "document_completeness"),
        ("ragged_table", "document_completeness"),
        ("extra_block", "document_completeness"),
        ("empty_blocks", "document_completeness"),
        ("title_level", "document_headings"),
        ("page", "document_order"),
        ("wrong_document", "document_source"),
        ("punctuation", "document_text"),
        ("units", "document_tables"),
        ("markdown_only_error", "document_markdown"),
        ("markdown_table_missing", "document_markdown"),
        ("markdown_heading", "document_markdown"),
    ],
)
def test_damaged_outputs_detected(store, mutation, failed_check):
    bundle = document_bundle("correct")
    output = bundle["trace"]["output"]
    document = output["document"]
    blocks = document["blocks"]
    if mutation == "duplicate_id":
        blocks.append(copy.deepcopy(blocks[0]))
    elif mutation == "ragged_table":
        blocks[3]["rows"][2].pop()
    elif mutation == "extra_block":
        blocks.append({"id": "invented", "page": 2, "kind": "paragraph", "text": "invented"})
    elif mutation == "empty_blocks":
        document["blocks"] = []
    elif mutation == "title_level":
        blocks[0]["level"] = 2
    elif mutation == "page":
        blocks[1]["page"] = 2
    elif mutation == "wrong_document":
        document["document_id"] = "another-document"
    elif mutation == "punctuation":
        blocks[1]["text"] = blocks[1]["text"].replace(".", "!")
    elif mutation == "units":
        blocks[3]["rows"][0][1] = "Drip (mL)"
    elif mutation == "markdown_only_error":
        output["markdown"] = output["markdown"].replace("| Tuesday | 10 | 17 |", "| Tuesday | 10 | 71 |")
    elif mutation == "markdown_table_missing":
        output["markdown"] = "\n".join(
            line for line in output["markdown"].splitlines() if not line.startswith("|")
        )
    else:
        output["markdown"] = output["markdown"].replace("# Field study", "## Field study")
    _, evaluation = evaluate_bundle(store, bundle)
    assert evaluation.outcome == "fail" and checks(evaluation)[failed_check] == "fail"


def test_missing_markdown_is_unknown(store):
    bundle = document_bundle("correct")
    del bundle["trace"]["output"]["markdown"]
    _, result = evaluate_bundle(store, bundle)
    assert result.outcome == "inconclusive" and checks(result)["document_markdown"] == "unknown"


def test_whitespace_normalization_preserves_meaning(store):
    bundle = document_bundle("correct")
    output = bundle["trace"]["output"]
    output["document"]["blocks"][1]["text"] = "  " + output["document"]["blocks"][1]["text"].replace(
        " ", "  \n"
    )
    output["markdown"] = (
        output["markdown"]
        .replace("over three days.", "over\nthree days.")
        .replace("| Tuesday |", "|   Tuesday   |")
    )
    assert evaluate_bundle(store, bundle)[1].outcome == "pass"


@pytest.mark.parametrize("rule", ["document_ocr", "document_formulas", "unimplemented"])
def test_required_unsupported_dimensions_stay_inconclusive(store, rule):
    profile = fixture_json("profile.json")
    profile["rules"].append({"id": "unimplemented", "op": "external", "required": rule == "unimplemented"})
    next(r for r in profile["rules"] if r["id"] == rule)["required"] = True
    _, result = evaluate_bundle(store, document_bundle("correct"), profile)
    assert result.outcome == "inconclusive" and checks(result)[rule] == "unknown"


def test_request_binding_and_response_binding(store):
    run = ingest(store, canonical(document_bundle("correct")).encode())[0]
    profile = fixture_json("profile.json")
    request = evaluator_request(run, profile)
    changed = copy.deepcopy(request)
    changed["context"]["output"]["markdown"] = "changed"
    with pytest.raises(ValueError, match="input_hash"):
        evaluate_document(changed)
    response = evaluate_document(request)
    response["profile_hash"] = "stale"
    with pytest.raises(ValueError, match="profile_hash"):
        evaluate_profile(store, run, profile, response)


def test_bundle_roundtrip_requires_explicit_evaluation(store, tmp_path):
    run, result, _ = run_document_candidate(store, "table_error")
    other_store = Store(tmp_path / "other")
    other, initial, _ = ingest(other_store, canonical(store.bundle(run.id)).encode())
    assert other.id == run.id and initial.custom is None and initial.outcome == "inconclusive"
    final = evaluate_document_run(other_store, other)
    assert final.outcome == result.outcome and checks(final) == checks(result)
    assert final.custom["results"] == result.custom["results"]
    assert source_pdf(other.artifacts) == source_pdf(run.artifacts)


def test_external_cli_protocol(tmp_path):
    runner = CliRunner()
    data = tmp_path / "data"
    response = runner.invoke(app, ["document-conversion", "--candidate", "correct", "--data-dir", str(data)])
    assert response.exit_code == 0, response.output
    run_id = json.loads(response.output)["run_id"]
    profile = tmp_path / "profile.json"
    profile.write_text(canonical(fixture_json("profile.json")))
    request, results = tmp_path / "request.json", tmp_path / "results.json"
    args = [run_id, "--data-dir", str(data), "--profile", str(profile)]
    exported = runner.invoke(app, ["evaluator-request", *args, "--output", str(request)])
    assert exported.exit_code == 0, exported.output
    evaluated = runner.invoke(app, ["document-evaluator", str(request), "--output", str(results)])
    assert evaluated.exit_code == 0, evaluated.output
    imported = runner.invoke(app, ["evaluate", *args, "--results", str(results)])
    assert imported.exit_code == 0 and json.loads(imported.output)["outcome"] == "pass"
    repeated = runner.invoke(app, ["document-evaluate", run_id, "--data-dir", str(data)])
    assert repeated.exit_code == 0 and json.loads(repeated.output)["outcome"] == "pass"
    assert runner.invoke(app, ["document-conversion", "--candidate", "arbitrary"]).exit_code != 0


def test_document_api_workflow(tmp_path):
    client = TestClient(create_app(str(tmp_path / "data")))
    headers = {"X-Review-Request": "1"}
    assert client.post("/api/document-demo").status_code == 403
    assert client.post("/api/document-demo?candidate=bad", headers=headers).status_code == 422
    ids = client.post("/api/document-demo?candidate=all", headers=headers).json()["run_ids"]
    assert len(ids) == 5
    rid = ids[0]
    assert client.get("/api/runs/" + rid).json()["evaluation"]["outcome"] == "pass"
    pdf = client.get(f"/api/runs/{rid}/document.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")
    assert pdf.headers["content-type"] == "application/pdf"
    assert digest(pdf.content) == fixture_json("reference.json")["source_sha256"]
    assert client.post(f"/api/runs/{rid}/document-evaluation").status_code == 403
    assert client.post(f"/api/runs/{rid}/document-evaluation", headers=headers).json()["outcome"] == "pass"
    assert "document_output" in client.get("/api/schema").json()


def test_pdf_matches_independent_annotations():
    pdfplumber = pytest.importorskip("pdfplumber", reason="Optional pdf-fixtures extra for source PDF QA")
    reference = fixture_json("reference.json")
    source = files("agent_trace_review").joinpath(RESOURCE, "source.pdf")
    assert digest(source.read_bytes()) == reference["source_sha256"]
    with pdfplumber.open(source) as pdf:
        assert len(pdf.pages) == reference["page_count"]
        for page_number, page in enumerate(pdf.pages, 1):
            text = normalize(page.extract_text())
            page_blocks = [b for b in reference["blocks"] if b["page"] == page_number]
            for block in page_blocks:
                if block["kind"] != "table":
                    assert normalize(block["text"]) in text
            tables = [b["rows"] for b in page_blocks if b["kind"] == "table"]
            assert page.extract_tables() == tables
