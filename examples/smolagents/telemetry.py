"""Small framework-independent recorder for agent-review/target-trace-v1.

Wrap actual model/tool invocations. Never export model reasoning, raw provider bodies,
or exception messages. Durations use a monotonic clock; wall times are target-reported.
"""

import time


class CallRecorder:
    def __init__(self):
        self.begin("initial", 0)

    def begin(self, session_id, turn):
        self.session_id, self.turn = session_id, turn
        self.events = []
        self.last_model_id = None

    def invoke(self, kind, name, arguments, function, *, effect="unknown"):
        wall, mono = time.time() * 1000, time.monotonic()
        event = {
            "id": f"{kind}_{len(self.events)}", "kind": kind,
            "role": "assistant" if kind == "llm" else "tool",
            "status": "unknown", "input": arguments, "start_ms": wall,
            "provenance": "target_reported",
        }
        if kind == "llm":
            event["model"] = name
        else:
            event.update(tool=name, effect=effect, parent_id=self.last_model_id)
        self.events.append(event)
        try:
            result = function()
            event["status"] = "completed"
            if kind == "llm":
                # Only public call identities, no content / reasoning_content / raw SDK objects.
                event["output"] = {"requested_tools": [
                    {"call_id": call.id, "tool": call.function.name}
                    for call in result.tool_calls or []
                ]}
                usage = result.token_usage
                if usage is not None:
                    event["usage"] = {"tokens": {
                        "input": usage.input_tokens, "output": usage.output_tokens,
                        "total": usage.input_tokens + usage.output_tokens,
                    }}
                self.last_model_id = event["id"]
            else:
                event["output"] = result
            return result
        except Exception as exc:
            event.update(status="error", output={"error_type": type(exc).__name__})
            raise
        finally:
            event["end_ms"] = max(wall, time.time() * 1000)
            event["duration_ms"] = round((time.monotonic() - mono) * 1000, 6)

    def trace(self):
        return {
            "trace_version": "agent-review/target-trace-v1",
            "session_id": self.session_id, "turn": self.turn,
            "coverage": "complete", "events": self.events,
        }


class RecordedTool:
    """Mixin around Tool.__call__, including validation failures and final_answer."""

    effect = "unknown"

    def __call__(self, *args, **kwargs):
        arguments = {k: v for k, v in kwargs.items() if k != "sanitize_inputs_outputs"}
        if args:
            if len(args) == 1 and isinstance(args[0], dict):
                arguments = {**args[0], **arguments}
            else:
                arguments["positional"] = list(args)
        invoke = super().__call__
        first = len(self.state.recorder.events)
        try:
            return self.state.recorder.invoke(
                "tool", self.name, arguments, lambda: invoke(*args, **kwargs), effect=self.effect,
            )
        finally:
            event = self.state.recorder.events[first]
            if self.name != "final_answer":
                # Keep the original output._execution.tools summary for existing Profiles.
                self.state.calls.append({
                    "tool": self.name, "arguments": arguments, "output": event.get("output"),
                    "status": event["status"], "duration_ms": event["duration_ms"], "id": event["id"],
                })
