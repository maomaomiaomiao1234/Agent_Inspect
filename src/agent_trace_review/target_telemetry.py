"""Summaries of validated, target-reported calls; no inference of missing calls or prices."""


def _sum_known(values):
    return round(sum(values), 6) if values and all(v is not None for v in values) else None


def telemetry_summary(turns, execution_complete=True):
    traces = [t.get("trace") for t in turns]
    available = [t for t in traces if t is not None]
    complete = execution_complete and bool(traces) and all(
        t is not None and t["coverage"] == "complete" for t in traces
    )
    calls = [e for trace in available for e in trace["events"]]
    llms = [e for e in calls if e["kind"] == "llm"]
    tools = [e for e in calls if e["kind"] == "tool"]

    def count(items):
        return len(items) if available else None

    def duration(items):
        if not items:
            return 0 if complete else None
        return _sum_known([e.get("duration_ms") for e in items])

    models = []
    for model in sorted({e.get("model") or "unknown" for e in llms}):
        selected = [e for e in llms if (e.get("model") or "unknown") == model]
        tokens = {}
        for key in ("input", "output", "total"):
            tokens[key] = _sum_known([(e.get("usage") or {}).get("tokens", {}).get(key) for e in selected])
        models.append({"model": model, "calls": len(selected), "tokens": tokens, "duration_ms": duration(selected)})
    return {
        "coverage": "complete" if complete else "partial" if available else "unavailable",
        "provenance": "target_reported",
        "llm_calls": count(llms),
        "tool_calls": count(tools),
        "llm_errors": sum(e["status"] == "error" for e in llms) if available else None,
        "tool_errors": sum(e["status"] == "error" for e in tools) if available else None,
        "llm_duration_ms": duration(llms),
        "tool_duration_ms": duration(tools),
        "models": models,
    }


def trace_events(trace, *, job_id, case_id, session_id, turn):
    if trace is None:
        return []
    prefix = f"target_{turn}_"
    return [
        {
            **event,
            "id": prefix + event["id"],
            "parent_id": prefix + event["parent_id"] if event.get("parent_id") else None,
            "provenance": "target_reported",
            "context": {"job_id": job_id, "case_id": case_id, "session_id": session_id, "turn": turn},
        }
        for event in trace["events"]
    ]
