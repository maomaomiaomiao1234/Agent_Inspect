import copy

import pytest

from agent_trace_review.analysis import compare, diff_files
from agent_trace_review.demo import demo_bundles
from agent_trace_review.service import ingest
from agent_trace_review.util import canonical


def evaluate(store, bundle):
    return ingest(store, canonical(bundle).encode())[:2]


def test_five_patterns(store, bundle):
    focused, a = evaluate(store, bundle)
    slow, b = evaluate(store, demo_bundles()[1])
    assert {f.category for f in a.findings} == {"verified_recovery"}
    assert {f.category for f in b.findings} == {
        "duplicate_readonly_call",
        "repeated_failure_cycle",
        "final_change_unverified",
    }
    assert b.outcome == "inconclusive"
    result = compare(focused, slow, a, b)
    assert result["comparable"] and len(result["behavior_differences"]) == 4
    bundle["task"]["protected_paths"] = ["auth/*"]
    assert evaluate(store, bundle)[1].outcome == "fail"


@pytest.mark.parametrize(
    "field,value",
    [("state_hash", "different"), ("suite_hash", "different"), ("kind", "lint"), ("result", "error")],
)
def test_report_mismatch_not_pass(store, bundle, field, value):
    bundle["verifications"][0][field] = value
    assert evaluate(store, bundle)[1].outcome == "inconclusive"


def test_ambiguous_reports_not_pass(store, bundle):
    bundle["verifications"].append(copy.deepcopy(bundle["verifications"][0]))
    assert evaluate(store, bundle)[1].outcome == "inconclusive"


@pytest.mark.parametrize("mutation", ["missing", "skipped", "fail"])
def test_baseline_regression(store, bundle, mutation):
    final = bundle["verifications"][0]
    baseline = copy.deepcopy(final)
    baseline.update(id="baseline", phase="baseline", state_hash=bundle["task"]["base_commit"])
    bundle["verifications"].append(baseline)
    name = next(iter(final["cases"]))
    if mutation == "missing":
        del final["cases"][name]
    else:
        final["cases"][name] = mutation
    if mutation == "fail":
        final["result"] = "fail"
    for key in ("executed", "passed", "failed", "skipped"):
        final.pop(key)
    evaluation = evaluate(store, bundle)[1]
    assert evaluation.outcome == ("fail" if mutation == "fail" else "inconclusive")
    assert any(
        f.category == ("test_regression" if mutation == "fail" else "missing_tests")
        for f in evaluation.findings
    )


def test_unrelated_baseline_is_not_regression(store, bundle):
    baseline = copy.deepcopy(bundle["verifications"][0])
    baseline.update(phase="baseline", state_hash="unrelated", cases={"other": "pass"}, executed=1, passed=1)
    bundle["verifications"].append(baseline)
    assert evaluate(store, bundle)[1].outcome == "pass"


def test_missing_diff_cannot_prove_path_constraints(store, bundle):
    bundle.pop("diff")
    assert evaluate(store, bundle)[1].outcome == "inconclusive"


def test_changed_constraints_not_comparable(store, bundle):
    a, ea = evaluate(store, bundle)
    bundle["task"]["allowed_paths"] = ["auth/*"]
    b, eb = evaluate(store, bundle)
    assert not compare(a, b, ea, eb)["comparable"]


@pytest.mark.parametrize(
    "diff,expected",
    [
        ("diff --git a/old.py b/new.py\nrename from old.py\nrename to new.py", ["new.py", "old.py"]),
        ("diff --git a/image.png b/image.png\nBinary files differ", ["image.png"]),
        ("--- a/a b.py\n+++ b/a b.py", ["a b.py"]),
        ('--- "a/my file.py"\n+++ "b/my file.py"', ["my file.py"]),
        ("--- a/auth/../secrets.txt\n+++ b/auth/../secrets.txt", ["secrets.txt"]),
        ("--- /dev/null\n+++ b/new.py", ["new.py"]),
    ],
)
def test_final_diff_paths(diff, expected):
    assert diff_files(diff) == expected


def test_hunk_text_does_not_invent_changed_paths():
    diff = "--- a/safe.txt\n+++ b/safe.txt\n@@ -1 +1 @@\n--- a/secrets.txt\n+++ b/secrets.txt\n"
    assert diff_files(diff) == ["safe.txt"]
