"""Execute the fixed document evaluator, verify evidence, and export portable examples."""

import argparse
import tempfile
from pathlib import Path

from agent_trace_review.document_conversion import evaluate_document_run, run_document_candidate
from agent_trace_review.document_evaluator import fixture_json, source_pdf
from agent_trace_review.profiles import evaluator_request
from agent_trace_review.service import ingest
from agent_trace_review.storage import Store
from agent_trace_review.util import canonical, digest

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--data-dir", required=True)
parser.add_argument("--export-dir", type=Path)
args = parser.parse_args()
store = Store(args.data_dir)
profile = fixture_json("profile.json")
if args.export_dir:
    args.export_dir.mkdir(parents=True, exist_ok=True)
    (args.export_dir / "profile.json").write_text(canonical(profile) + "\n")
for candidate, outcome in [
    ("correct", "pass"),
    ("omitted", "fail"),
    ("table_error", "fail"),
    ("order_error", "fail"),
    ("missing_reference", "inconclusive"),
]:
    run, evaluation, _ = run_document_candidate(store, candidate)
    assert evaluation.outcome == outcome
    checks = evaluation.custom["checks"]
    assert len(checks) == 9 and sum(c["required"] for c in checks) == 7
    assert all(c["status"] == "unknown" for c in checks if not c["required"])
    assert digest(source_pdf(run.artifacts)) == fixture_json("reference.json")["source_sha256"]
    assert next(m for m in evaluation.metrics if m.key == "tokens_total").value is None
    bundle = canonical(store.bundle(run.id)).encode()
    with tempfile.TemporaryDirectory(prefix="document-roundtrip-") as directory:
        other = Store(directory)
        imported, initial, _ = ingest(other, bundle)
        assert imported.id == run.id and initial.outcome == "inconclusive"
        repeated = evaluate_document_run(other, imported)
        assert repeated.outcome == outcome and repeated.custom["results"] == evaluation.custom["results"]
        assert evaluate_document_run(other, imported).id == repeated.id
    if args.export_dir:
        (args.export_dir / f"{candidate}.bundle.json").write_bytes(bundle + b"\n")
        (args.export_dir / f"{candidate}.request.json").write_text(
            canonical(evaluator_request(run, profile)) + "\n"
        )
        (args.export_dir / f"{candidate}.results.json").write_text(
            canonical(evaluation.custom["results"]) + "\n"
        )
    print(canonical({"candidate": candidate, "run_id": run.id, "outcome": outcome}), flush=True)
print("Document checks, unknown scope, explicit reevaluation and bundle round-trip passed.")
