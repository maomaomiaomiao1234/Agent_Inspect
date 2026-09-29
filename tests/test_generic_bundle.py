import copy
import json

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from agent_trace_review.api import create_app
from agent_trace_review.cli import app
from agent_trace_review.models import VERSION, Task
from agent_trace_review.service import ingest
from agent_trace_review.storage import Store
from agent_trace_review.util import canonical, digest


@pytest.fixture
def generic_bundle(bundle):
    return {
        "bundle_version": "generic/2",
        "trace": {
            "trace_version": "1",
            "framework": "test",
            "run_id": "repair",
            "events": [],
            "output": "candidate",
            "demo": True,
        },
        "task": bundle["task"],
        "diff": bundle["diff"],
        "verifications": bundle["verifications"],
    }


def test_bundle_roundtrip_and_local_evidence(store, tmp_path, generic_bundle):
    for report in generic_bundle["verifications"]:
        report.update(evidence_ids=["untrusted"], event_id="untrusted")
    run, evaluation, created = ingest(store, canonical(generic_bundle).encode())
    assert created and evaluation.outcome == "pass"
    assert run.adapter_version == "generic/2" and run.diff_provided
    refs = {e.id for e in run.evidence}
    assert "final-diff" in refs
    for report in run.verifications:
        assert report.event_id is None and set(report.evidence_ids) <= refs
        assert "untrusted" not in report.evidence_ids
    exported = store.bundle(run.id)
    assert exported["bundle_version"] == "generic/2"
    assert exported["diff"] == generic_bundle["diff"]
    again = ingest(store, canonical(exported).encode())
    other = ingest(Store(tmp_path / "other"), canonical(exported).encode())
    assert again[0].id == other[0].id == run.id
    assert not again[2] and other[2]
    assert other[1].outcome == evaluation.outcome
    assert other[1].id == evaluation.id
    assert other[0].verifications == run.verifications


def test_generic_one_identity_unchanged(store, generic_bundle):
    trace = generic_bundle["trace"]
    task = generic_bundle["task"]
    original = {"bundle_version": "generic/1", "trace": trace, "task": task}
    run = ingest(store, canonical(original).encode())[0]
    expected = (
        "run_"
        + digest([digest(canonical(trace).encode()), Task.model_validate(task).model_dump(), None, VERSION])[
            :20
        ]
    )
    assert run.id == expected
    assert store.bundle(run.id) == original | {"task": Task.model_validate(task).model_dump()}
    for extra in ({"diff": ""}, {"verifications": []}):
        with pytest.raises(ValueError):
            ingest(store, canonical(original | extra).encode())
    with pytest.raises(ValueError):
        ingest(store, canonical(original).encode(), diff="")


def test_material_changes_identity(store, generic_bundle):
    first = ingest(store, canonical(generic_bundle).encode())[0]
    altered = copy.deepcopy(generic_bundle)
    altered["diff"] = ""
    second = ingest(store, canonical(altered).encode())[0]
    altered["verifications"] = []
    third = ingest(store, canonical(altered).encode())[0]
    assert len({first.id, second.id, third.id}) == 3
    assert second.diff_provided and not second.diff


@pytest.mark.parametrize("change", ["provenance", "duplicate", "diff", "counts", "pass-fail", "version"])
def test_invalid_bundle_rejected(store, generic_bundle, change):
    record = generic_bundle["verifications"][-1]
    if change == "provenance":
        record["provenance"] = "observed_tool"
    elif change == "duplicate":
        generic_bundle["verifications"].append(copy.deepcopy(record))
    elif change == "diff":
        generic_bundle["diff"] = "not a patch"
    elif change == "counts":
        record["executed"] = 999
    elif change == "pass-fail":
        record["cases"] = {"test": "fail"}
    else:
        generic_bundle["bundle_version"] = "generic/99"
    with pytest.raises(ValueError):
        ingest(store, canonical(generic_bundle).encode())
    assert not store.list_runs()


@pytest.mark.parametrize("change", ["missing", "state", "timeout", "zero"])
def test_insufficient_reports_never_pass(store, generic_bundle, change):
    final = next(v for v in generic_bundle["verifications"] if v["phase"] == "final")
    if change == "missing":
        generic_bundle["verifications"].remove(final)
    elif change == "state":
        final["state_hash"] = "wrong-state"
    elif change == "timeout":
        final.update(result="timeout", cases={}, executed=None, passed=None, failed=None, skipped=None)
    else:
        final.update(cases={}, executed=0, passed=0, failed=0, skipped=0)
    assert ingest(store, canonical(generic_bundle).encode())[1].outcome == "inconclusive"


def test_api_junit_requires_explicit_generic_two(tmp_path, generic_bundle):
    client = TestClient(create_app(str(tmp_path)))
    generic_bundle["verifications"] = []
    files = {
        "file": ("bundle.json", canonical(generic_bundle)),
        "junit_file": ("test.xml", '<testsuite tests="1"><testcase name="a"/></testsuite>'),
        "diff_file": ("final.patch", generic_bundle["diff"]),
    }
    response = client.post("/api/imports", headers={"X-Review-Request": "1"}, files=files)
    assert response.status_code == 200, response.text
    detail = client.get("/api/runs/" + response.json()["run_id"]).json()
    assert detail["evaluation"]["outcome"] == "pass"
    assert detail["run"]["source_format"] == "generic"
    for payload in [
        generic_bundle["trace"],
        {"bundle_version": "generic/1", "trace": generic_bundle["trace"]},
    ]:
        files["file"] = ("trace.json", canonical(payload))
        assert client.post("/api/imports", headers={"X-Review-Request": "1"}, files=files).status_code == 422
    assert (
        client.get("/api/schema").json()["generic_bundle"]["properties"]["bundle_version"]["const"]
        == "generic/2"
    )
    schema = CliRunner().invoke(app, ["schema"])
    assert schema.exit_code == 0 and "generic_bundle" in json.loads(schema.output)
