"""Actual Docker acceptance; run explicitly, never substituted by mocked results."""

import argparse
import json
import subprocess
import tempfile

from agent_trace_review.code_repair import EXPECTED_CASES, Candidate, run_candidate
from agent_trace_review.service import ingest
from agent_trace_review.storage import Store
from agent_trace_review.util import canonical

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--data-dir", required=True)
args = parser.parse_args()
store = Store(args.data_dir)
owned = set()
for candidate, expected in [
    (Candidate.correct, "pass"),
    (Candidate.incorrect, "fail"),
    (Candidate.regression, "fail"),
    (Candidate.timeout, "inconclusive"),
]:
    run, evaluation, _ = run_candidate(store, candidate)
    assert evaluation.outcome == expected, (candidate, evaluation.outcome)
    baseline, final = run.verifications
    assert baseline.result == "fail" and baseline.passed == 2 and baseline.failed == 1
    assert set(baseline.cases) == EXPECTED_CASES
    audits = run.artifacts["verification_runs"]
    for audit in audits.values():
        owned.add(audit["container"])
        assert audit["cleanup"] == "removed"
        assert audit["image_id"] == run.artifacts["environment"]["image_id"]
    if candidate == Candidate.timeout:
        assert final.result == "timeout" and audits["final"]["timed_out"]
        assert audits["final"]["exit_code"] is None
    else:
        assert final.executed == 3 and set(final.cases) == EXPECTED_CASES
        assert audits["final"]["exit_code"] == (0 if candidate == Candidate.correct else 1)
    assert any(f.category == "test_regression" for f in evaluation.findings) == (
        candidate == Candidate.regression
    )
    assert next(m for m in evaluation.metrics if m.key == "tokens_total").value is None
    assert next(m for m in evaluation.metrics if m.key == "cost_usd").value is None
    with tempfile.TemporaryDirectory(prefix="agent-review-roundtrip-") as directory:
        exported = canonical(store.bundle(run.id)).encode()
        imported, other, _ = ingest(Store(directory), exported)
        assert imported.id == run.id and other.id == evaluation.id and other.outcome == expected
    print(
        json.dumps({"candidate": candidate.value, "run_id": run.id, "outcome": evaluation.outcome}),
        flush=True,
    )
remaining = subprocess.run(
    [
        "docker",
        "ps",
        "-a",
        "--filter",
        "label=agent-review.runner=code-repair-fixture",
        "--format",
        "{{.Names}}",
    ],
    capture_output=True,
    check=True,
    timeout=10,
)
assert not owned.intersection(remaining.stdout.decode().splitlines()), "本次容器未清理"
print("Actual Docker acceptance and bundle round-trip passed; all owned containers removed.")
