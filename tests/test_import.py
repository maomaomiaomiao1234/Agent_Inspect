import copy
import json

import pytest

from agent_trace_review.opencode import command_kind, import_data
from agent_trace_review.service import ingest
from agent_trace_review.util import canonical


def load(store, payload, **kwargs):
    return ingest(store, canonical(payload).encode(), **kwargs)


def test_import_usage_evidence_and_roundtrip(store, bundle):
    run, evaluation, created = load(store, bundle)
    assert created and evaluation.outcome == "pass"
    assert sum(e.kind == "tool" for e in run.events) == 4
    metrics = {m.key: m for m in evaluation.metrics}
    assert metrics["tokens_total"].value == 18000  # Steps, not steps + assistant totals.
    assert metrics["cost_usd"].value == 0.05
    assert load(store, bundle)[2] is False
    assert load(store, store.bundle(run.id))[0].id == run.id
    source = json.loads(store.get_artifact(run.source_artifact)[0])
    refs = {r.id for r in evaluation.evidence}
    for ref in evaluation.evidence:
        if ref.artifact_id:
            artifact = json.loads(store.get_artifact(ref.artifact_id)[0]) if ref.pointer else None
            for part in (ref.pointer or "").split("/")[1:]:
                artifact = artifact[int(part)] if isinstance(artifact, list) else artifact[part]
    for finding in evaluation.findings:
        assert set(finding.evidence_ids + finding.counter_evidence_ids) <= refs
    assert source["info"]["id"] == run.session_id


def test_updates_are_deduplicated(store, bundle):
    original = copy.deepcopy(bundle["export"]["messages"][1])
    bundle["export"]["messages"].insert(1, original)
    run, _, _ = load(store, bundle)
    assert len([e for e in run.events if e.kind == "tool"]) == 4
    assert any("合并" in w for w in run.warnings)


def test_call_updates_with_distinct_part_ids(store, bundle):
    parts = bundle["export"]["messages"][1]["parts"]
    update = copy.deepcopy(parts[0])
    update["id"] = "prt_updated"
    parts.append(update)
    run, _, _ = load(store, bundle)
    assert len([e for e in run.events if e.kind == "tool"]) == 4


def test_boundary_and_bundle_roundtrip(store, bundle):
    first = bundle["export"]["messages"][2]["info"]["id"]
    last = bundle["export"]["messages"][3]["info"]["id"]
    run, _, _ = load(store, bundle, first_message=first, last_message=last)
    assert {e.message_id for e in run.events} == {first, last}
    assert load(store, store.bundle(run.id))[0].id == run.id
    with pytest.raises(ValueError):
        load(store, bundle, first_message=last, last_message=first)


def test_unknown_usage_and_empty_session(store, bundle):
    native = bundle["export"]
    native["messages"] = []
    run, evaluation, _ = load(store, native)
    assert run.execution_status == "unknown" and evaluation.outcome == "inconclusive"
    assert next(m.value for m in evaluation.metrics if m.key == "tokens_total") is None
    assert not run.diff_provided
    assert load(store, store.bundle(run.id))[0].id == run.id


def test_redaction_and_hidden_reasoning(store, bundle):
    msg = bundle["export"]["messages"][1]
    msg["parts"][0]["state"]["input"]["api_key"] = "should-never-store"
    msg["parts"].append({"type": "reasoning", "id": "reason", "text": "HIDDEN_THOUGHT"})
    run, _, _ = load(store, bundle)
    assert b"should-never-store" not in store.get_artifact(run.source_artifact)[0]
    assert "HIDDEN_THOUGHT" not in canonical([e.model_dump() for e in run.events])
    assert run.coverage["messages"]["level"] == "partial"


def test_missing_ids_get_distinct_event_ids(store, bundle):
    bundle["export"]["messages"][1]["parts"].extend(
        [{"type": "text", "text": "one"}, {"type": "text", "text": "two"}]
    )
    run, _, _ = load(store, bundle)
    assert len(run.events) == len({e.id for e in run.events})


@pytest.mark.parametrize("field,value", [("state", []), ("time", []), ("tokens", []), ("metadata", [])])
def test_malformed_nested_types_rejected(store, bundle, field, value):
    bundle["export"]["messages"][1]["parts"][0][field] = value
    with pytest.raises(ValueError):
        load(store, bundle)
    assert store.list_runs() == []


@pytest.mark.parametrize(
    "command,expected",
    [
        ("pytest -q", "test"),
        ("python3 -m pytest -q", "test"),
        ("uv run pytest", "test"),
        ("npm run typecheck", "typecheck"),
        ("npx tsc --noEmit", "typecheck"),
        ("ruff check .", "lint"),
        ("go test ./...", "test"),
        ("cargo build", "build"),
        ("echo pytest", None),
        ("cat pytest.log", None),
        ("pytest || true", None),
        ("pytest | tee log", None),
        ("pytest; echo done", None),
        ("echo $(pytest)", None),
        ("export X=1\npytest", None),
    ],
)
def test_shell_classification(command, expected):
    assert command_kind(command) == expected


@pytest.mark.parametrize(
    "status,metadata,expected",
    [
        ("completed", {"exit": 0}, "pass"),
        ("completed", {"exit": 1}, "fail"),
        ("completed", {"exit": 0, "truncated": True}, "unknown"),
        ("running", {"exit": 0}, "unknown"),
        ("completed", {"exit": True}, "unknown"),
    ],
)
def test_checks_need_actual_completion(store, bundle, status, metadata, expected):
    state = bundle["export"]["messages"][2]["parts"][0]["state"]
    state.update(status=status, metadata=metadata)
    run, _, _ = load(store, bundle)
    assert run.verifications[0].result == expected


def test_user_followup_has_no_final_end_time(store, bundle):
    user = copy.deepcopy(bundle["export"]["messages"][0])
    user["info"]["id"] = "msg_next"
    for part in user["parts"]:
        part["messageID"] = "msg_next"
    bundle["export"]["messages"].append(user)
    run, _, _ = load(store, bundle)
    assert run.end_ms is None and run.execution_status == "unknown"


def test_plain_text_and_cross_session_rejected(store, bundle):
    with pytest.raises(ValueError):
        import_data(store, b"hello")
    bundle["export"]["messages"][1]["parts"][0]["sessionID"] = "ses_other"
    with pytest.raises(ValueError):
        load(store, bundle)


@pytest.mark.parametrize("value", [False, [], {}, 0])
def test_invalid_diff_not_silently_empty(store, bundle, value):
    bundle["diff"] = value
    with pytest.raises(ValueError):
        load(store, bundle)


def test_concurrent_imports_are_idempotent(store, bundle):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: load(store, bundle), range(4)))
    assert len({run.id for run, _, _ in results}) == 1
    assert sum(created for _, _, created in results) == 1
    assert len(store.list_runs()) == 1
    assert len(store.revisions(results[0][0].id)) == 1


def test_evaluation_identity_survives_persistence(store, bundle):
    from agent_trace_review.analysis import analyze

    run, evaluation, _ = load(store, bundle)
    persisted = store.get_run(run.id)
    assert canonical(run.model_dump(exclude={"imported_at"})) == canonical(
        persisted.model_dump(exclude={"imported_at"})
    )
    assert analyze(persisted).id == evaluation.id
