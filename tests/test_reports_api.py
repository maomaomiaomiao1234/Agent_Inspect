import json

import pytest
from fastapi.testclient import TestClient

from agent_trace_review.api import create_app
from agent_trace_review.models import Verification
from agent_trace_review.reports import junit_report
from agent_trace_review.util import canonical

HEADERS = {"X-Review-Request": "1"}


def test_junit_counts_and_raw_source():
    xml = b'<testsuite tests="3"><testcase name="a"/><testcase name="b"><failure/></testcase><testcase name="c"><skipped/></testcase></testsuite>'
    report = junit_report(xml, "tests", "state", "suite")
    assert report["result"] == "fail" and report["executed"] == 2 and report["skipped"] == 1
    assert report["source_text"] == xml.decode()


@pytest.mark.parametrize(
    "xml",
    [
        b'<testsuite tests="4"><testcase name="one"/></testsuite>',
        b'<testsuite><testcase name="a"/><testcase name="a"/></testsuite>',
        b'<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><testsuite>&xxe;</testsuite>',
    ],
)
def test_junit_invalid_or_unsafe(xml):
    with pytest.raises(ValueError):
        junit_report(xml, "t", "s", "suite")


def test_suite_failures_not_erased():
    report = junit_report(b'<testsuite failures="1"><testcase name="a"/></testsuite>', "t", "s", "suite")
    assert report["result"] == "fail"


def test_no_zero_test_success():
    assert junit_report(b'<testsuite tests="0"/>', "t", "s", "suite")["result"] == "unknown"


def test_contradictory_report_rejected():
    with pytest.raises(ValueError):
        Verification(id="x", provenance="external_verifier", result="pass", cases={"a": "fail"})


def test_api_full_workflow(tmp_path, bundle):
    client = TestClient(create_app(str(tmp_path / "data")))
    assert client.post("/api/demo").status_code == 403
    assert client.get("/api/health", headers={"Host": "evil.test"}).status_code == 400
    assert (
        client.post("/api/imports", headers=HEADERS, files={"file": ("bad.json", b"broken")}).status_code
        == 422
    )
    imported = client.post(
        "/api/imports", headers=HEADERS, files={"file": ("run.json", canonical(bundle).encode())}
    )
    assert imported.status_code == 200, imported.text
    rid = imported.json()["run_id"]
    assert len(client.get("/api/runs").json()) == 1
    detail = client.get(f"/api/runs/{rid}").json()
    assert "events" not in detail["run"]
    events = client.get(f"/api/runs/{rid}/events?limit=2").json()
    assert len(events["items"]) == 2 and events["next_offset"] == 2
    event = events["items"][0]
    assert client.get(f"/api/runs/{rid}/events/{event['id']}").json()["id"] == event["id"]
    assert client.get("/api/artifacts/" + detail["run"]["source_artifact"]).status_code == 200
    assert client.get(f"/api/runs/{rid}?revision=missing").status_code == 404
    markdown = client.get(f"/api/runs/{rid}/export").text
    assert "证据索引" in markdown and rid in markdown
    export = client.get(f"/api/runs/{rid}/export?format=bundle").content
    again = client.post("/api/imports", headers=HEADERS, files={"file": ("bundle.json", export)}).json()
    assert again["run_id"] == rid and not again["created"]
    assert client.get(f"/api/runs/{rid}/revisions").json()
    assert client.post(f"/api/runs/{rid}/judge", headers=HEADERS, json={}).status_code == 422
    demos = client.post("/api/demo", headers=HEADERS).json()["run_ids"]
    compared = client.post(
        "/api/comparisons", headers=HEADERS, json={"left_id": demos[0], "right_id": demos[1]}
    )
    assert compared.json()["comparable"]


def test_api_junit_upload(tmp_path, bundle):
    client = TestClient(create_app(str(tmp_path / "data")))
    response = client.post(
        "/api/imports",
        headers=HEADERS,
        files={
            "file": ("export.json", canonical(bundle["export"]).encode()),
            "task_file": ("task.json", canonical(bundle["task"]).encode()),
            "diff_file": ("final.patch", bundle["diff"].encode()),
            "junit_file": ("report.xml", b'<testsuite tests="1"><testcase name="one"/></testsuite>'),
        },
    )
    assert response.status_code == 200, response.text
    detail = client.get("/api/runs/" + response.json()["run_id"]).json()
    assert detail["evaluation"]["outcome"] == "pass"


@pytest.mark.parametrize(
    "payload", [[], {"info": {"id": "s"}, "messages": "bad"}, {"export": [], "bundle_version": "1"}]
)
def test_malformed_import_always_client_error(tmp_path, payload):
    client = TestClient(create_app(str(tmp_path / "data")))
    result = client.post("/api/imports", headers=HEADERS, files={"file": ("bad.json", json.dumps(payload))})
    assert result.status_code == 422
