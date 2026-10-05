import copy

import pytest

from agent_trace_review.analysis import analyze
from agent_trace_review.contracts import GenericTrace
from agent_trace_review.reports import markdown_report
from agent_trace_review.service import ingest
from agent_trace_review.usage_accounting import combine_usage, summarize_usage
from agent_trace_review.util import canonical


def sample():
    return summarize_usage([{"usage": {"tokens": {"input": 10, "output": 5}}}])


def test_planned_runs_keep_missing_cases_and_ignore_explicit_offline_usage():
    partial = combine_usage([sample()], expected=2)
    assert partial["fields"]["total_tokens"]["value"] == 15
    assert partial["fields"]["total_tokens"]["status"] == "partial"
    offline = summarize_usage([{"usage_mode": "offline"}])
    mixed = combine_usage([sample(), offline], expected=2)
    assert mixed["fields"]["total_tokens"]["value"] == 15
    assert mixed["fields"]["total_tokens"]["status"] == "complete"


def test_summary_only_trace_uses_explicit_evidence_without_fake_calls(store):
    trace = {"trace_version": "1", "framework": "fixture", "run_id": "s", "usage_summary": sample()}
    run, evaluation, _ = ingest(store, canonical(trace).encode())
    assert not run.usage and not run.events and not run.trace_complete
    metric = next(m for m in evaluation.metrics if m.key == "tokens_total")
    assert metric.value == 15 and metric.status == "observed" and metric.evidence_ids == ["usage-summary"]
    assert store.bundle(run.id)["trace"]["usage_summary"] == trace["usage_summary"]


@pytest.mark.parametrize("change", ["negative", "boolean", "float_tokens", "missing_fields", "total", "duplicate"])
def test_invalid_summary_and_call_conflicts_are_rejected(change):
    trace = {"trace_version": "1", "framework": "fixture", "run_id": "s", "usage_summary": sample()}
    if change == "negative":
        trace["usage_summary"]["fields"]["input_tokens"]["value"] = -1
    elif change == "boolean":
        trace["usage_summary"]["fields"]["input_tokens"]["value"] = True
    elif change == "float_tokens":
        trace["usage_summary"]["fields"]["input_tokens"]["value"] = 10.0
    elif change == "missing_fields":
        del trace["usage_summary"]["fields"]["output_tokens"]
    elif change == "total":
        trace["usage_summary"]["fields"]["total_tokens"]["value"] = 99
    else:
        trace["events"] = [{"id": "m", "kind": "llm", "usage": {"tokens": {"input": 100, "output": 10}}}]
    with pytest.raises(ValueError):
        GenericTrace.model_validate(trace)


def test_partial_and_offline_metric_reports_are_explicit(store):
    for mode in ("partial", "offline"):
        summary = sample() if mode == "partial" else summarize_usage([{"usage_mode": "offline"}])
        if mode == "partial":
            summary = combine_usage([summary], complete=False)
        trace = {"trace_version": "1", "framework": "fixture", "run_id": mode, "usage_summary": summary}
        run, _, _ = ingest(store, canonical(trace).encode())
        report = markdown_report(run, analyze(run))
        assert ("≥15" if mode == "partial" else "不适用（离线校准）") in report
        rebuilt = copy.deepcopy(store.bundle(run.id)["trace"])
        assert rebuilt["usage_summary"] == summary
