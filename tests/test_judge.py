import asyncio

import pytest

from agent_trace_review.judge import JudgeResponse, judge_run, prepare_window
from agent_trace_review.service import ingest
from agent_trace_review.util import canonical

CONFIG = {
    "enabled": True,
    "available": False,
    "model": "mockllm/model",
    "provider_url": None,
    "max_input_chars": 40000,
    "max_output_tokens": 1600,
    "timeout_seconds": 2,
}


def test_judge_window_does_not_send_reasoning(store, bundle):
    bundle["export"]["messages"][1]["parts"].append(
        {"id": "secret-reason", "type": "reasoning", "text": "HIDDEN"}
    )
    run, _, _ = ingest(store, canonical(bundle).encode())
    window, refs = prepare_window(run)
    assert "HIDDEN" not in canonical(window)
    assert len(canonical(window)) <= 40000
    assert all(event["evidence_id"] in refs for event in window["events"])


def test_judge_is_evidence_bound_and_cached(store, bundle):
    run, base, _ = ingest(store, canonical(bundle).encode())
    calls = []

    async def generate(window, config):
        calls.append(window)
        return JudgeResponse(
            observations=[
                {
                    "dimension": "change_scope",
                    "assessment": "aligned",
                    "explanation": "最终补丁集中于边界条件。",
                    "evidence_ids": ["final-diff"],
                    "limitation": "仅最终片段",
                }
            ]
        ), {"usage": {"input_tokens": 100, "output_tokens": 10}}

    evaluation = judge_run(store, run, generate, CONFIG)
    assert judge_run(store, run, generate, CONFIG).id == evaluation.id and len(calls) == 1
    assert evaluation.outcome == base.outcome and evaluation.metrics == base.metrics
    assert evaluation.findings[-1].origin == "llm_judge"
    assert evaluation.findings[-1].verdict == "hypothesis"
    assert store.get_evaluation(run.id, base.id).judge is None
    assert len(store.revisions(run.id)) == 2


def test_judge_invalid_evidence_never_saved(store, bundle):
    run, base, _ = ingest(store, canonical(bundle).encode())

    async def bad(window, config):
        return {
            "observations": [
                {
                    "dimension": "recovery",
                    "assessment": "concern",
                    "explanation": "bad",
                    "evidence_ids": ["another-run"],
                    "limitation": "",
                }
            ]
        }, {}

    with pytest.raises(ValueError, match="证据"):
        judge_run(store, run, bad, CONFIG)
    assert store.get_evaluation(run.id).id == base.id


def test_judge_timeout_preserves_original(store, bundle):
    run, base, _ = ingest(store, canonical(bundle).encode())

    async def slow(window, config):
        await asyncio.sleep(1)

    with pytest.raises(TimeoutError):
        judge_run(store, run, slow, CONFIG | {"timeout_seconds": 0.01})
    assert store.get_evaluation(run.id).id == base.id


def test_judge_disabled(store, bundle):
    run, _, _ = ingest(store, canonical(bundle).encode())
    with pytest.raises(ValueError, match="未启用"):
        judge_run(store, run, config=CONFIG | {"enabled": False})


def test_judge_preserves_task_profile_outcome(store, bundle):
    profile = {
        "profile_version": "1",
        "id": "strict-budget",
        "rules": [
            {"id": "budget", "dimension": "resource", "op": "max", "path": "/metrics/cost_usd", "value": 0}
        ],
    }
    run, base, _ = ingest(store, canonical(bundle).encode(), profile=profile)
    assert base.outcome == "fail"

    async def generate(window, config):
        return JudgeResponse(observations=[]), {}

    result = judge_run(store, run, generate, CONFIG)
    assert result.outcome == "fail" and result.custom == base.custom
    assert result.metrics == base.metrics


def test_real_scout_bridge_with_offline_provider(monkeypatch, store, bundle):
    pytest.importorskip("inspect_scout")
    import inspect_ai.model as model_module
    from inspect_ai.model import ModelOutput, ModelUsage

    from agent_trace_review.judge import scout_generate

    seen = []

    def output(input, tools, tool_choice, config):
        seen.append(input)
        assert tools == [] and config.max_tokens == 1600 and config.max_retries == 0
        result = ModelOutput.from_content(model="mockllm/model", content=canonical({"observations": []}))
        result.usage = ModelUsage(input_tokens=99, output_tokens=7, total_tokens=106)
        return result

    model = model_module.get_model("mockllm/model", custom_outputs=output)
    monkeypatch.setattr(model_module, "get_model", lambda *args, **kwargs: model)
    run, _, _ = ingest(store, canonical(bundle).encode())
    result, metadata = asyncio.run(scout_generate(prepare_window(run)[0], CONFIG))
    assert result.observations == [] and len(seen) == 1
    assert metadata["usage"]["total_tokens"] == 106
