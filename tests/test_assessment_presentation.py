from agent_trace_review.assessment_store import AssessmentStore


def test_summary_preserves_adapter_purpose_without_case_payloads(store):
    db = AssessmentStore(store)
    job = db.create_job({"purpose": "adapter_validation", "results": [{"case_id": "native-entry"}]})
    summary = db.list_jobs(summary=True)[0]
    assert summary["id"] == job["id"]
    assert summary["purpose"] == "adapter_validation"
    assert "results" not in summary
