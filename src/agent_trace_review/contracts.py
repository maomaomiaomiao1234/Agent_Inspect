"""Versioned, framework-independent input and evaluator contracts."""

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import Task, Verification
from .usage_accounting import UsageSummary, usage_value


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    @model_validator(mode="after")
    def finite_json(self):
        json.dumps(self.model_dump(), allow_nan=False)
        return self


class Tokens(Contract):
    input: int | None = Field(None, ge=0, strict=True)
    output: int | None = Field(None, ge=0, strict=True)
    total: int | None = Field(None, ge=0, strict=True)
    reasoning: int | None = Field(None, ge=0, strict=True)
    cache_read: int | None = Field(None, ge=0, strict=True)
    cache_write: int | None = Field(None, ge=0, strict=True)
    cache_miss: int | None = Field(None, ge=0, strict=True)


class Usage(Contract):
    tokens: Tokens = Field(default_factory=Tokens)
    cost_usd: float | None = Field(None, ge=0, strict=True)


class TraceEvent(Contract):
    id: str = Field(min_length=1, max_length=200)
    kind: Literal["message", "tool", "llm", "retry", "error"]
    role: Literal["user", "assistant", "system", "tool"] = "assistant"
    title: str = ""
    tool: str | None = None
    effect: Literal["read", "write", "unknown"] = "unknown"
    status: Literal["completed", "error", "running", "skipped", "unknown"] = "unknown"
    input: dict[str, Any] = Field(default_factory=dict)
    output: Any = None
    start_ms: float | None = Field(None, ge=0)
    end_ms: float | None = Field(None, ge=0)
    truncated: bool = False
    usage: Usage | None = None
    model: str | None = Field(default=None, min_length=1, max_length=200)
    duration_ms: float | None = Field(default=None, ge=0, le=10**12, strict=True)
    parent_id: str | None = Field(default=None, min_length=1, max_length=200)
    provenance: Literal["exporter_reported", "target_reported", "host_observed"] = "exporter_reported"
    context: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_event(self):
        if self.kind == "tool" and not self.tool:
            raise ValueError("tool 事件需要 tool 名称。")
        if self.kind != "tool" and (self.tool or self.effect != "unknown"):
            raise ValueError("只有 tool 事件能声明 tool / effect。")
        if self.usage is not None and self.kind != "llm":
            raise ValueError("usage 只放在独立 llm 调用事件中，避免重复计费。")
        if self.model is not None and self.kind != "llm":
            raise ValueError("只有 llm 事件能声明 model。")
        if self.start_ms is not None and self.end_ms is not None and self.end_ms < self.start_ms:
            raise ValueError("事件 end_ms 不能早于 start_ms。")
        return self


class GenericTrace(Contract):
    trace_version: Literal["1"]
    framework: str = Field(min_length=1, max_length=200)
    run_id: str = Field(min_length=1, max_length=200)
    title: str = ""
    task_prompt: str = ""
    agent_version: str | None = None
    models: list[str] = Field(default_factory=list)
    status: str = "unknown"
    start_ms: float | None = Field(None, ge=0)
    end_ms: float | None = Field(None, ge=0)
    coverage: Literal["complete", "partial"] = "partial"
    usage_summary: UsageSummary | None = None
    events: list[TraceEvent] = Field(default_factory=list, max_length=100000)
    output: Any = None
    artifacts: dict[str, Any] = Field(default_factory=dict)
    demo: bool = False

    @model_validator(mode="after")
    def validate_trace(self):
        if len({e.id for e in self.events}) != len(self.events):
            raise ValueError("通用轨迹 event.id 必须唯一；每次调用只能计入一次。")
        if self.start_ms is not None and self.end_ms is not None and self.end_ms < self.start_ms:
            raise ValueError("轨迹 end_ms 不能早于 start_ms。")
        if self.usage_summary:
            calls = [e for e in self.events if e.kind == "llm"]
            for key, field in self.usage_summary.fields.items():
                values = [usage_value(e.usage.model_dump() if e.usage else None, key) for e in calls]
                known = [v for v in values if v is not None]
                if known and (field.value is None or
                              field.value is not None and field.value + 1e-8 < sum(known)):
                    raise ValueError("用量摘要不能小于已报告调用，也不能将其标为不适用。")
                if known and self.coverage == "complete" and len(known) == len(calls) and field.status == "complete":
                    if abs(field.value - sum(known)) > 1e-8:
                        raise ValueError("完整用量摘要与逐次调用不一致。")
        return self


class GenericBundle(Contract):
    """Data-only code evidence envelope; generic/1 remains unchanged."""

    bundle_version: Literal["generic/2"]
    trace: GenericTrace
    task: Task | None = None
    diff: str | None = Field(None, max_length=20 * 1024 * 1024)
    verifications: list[Verification] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def validate_reports(self):
        if any(v.provenance != "external_verifier" for v in self.verifications):
            raise ValueError("运行包只允许附加 external_verifier 报告。")
        if len({v.id for v in self.verifications}) != len(self.verifications):
            raise ValueError("外部验证报告 id 必须唯一。")
        return self


class Rule(Contract):
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}$")
    description: str = ""
    dimension: Literal["outcome", "behavior", "resource"] = "outcome"
    required: bool = True
    op: Literal[
        "exists",
        "equals",
        "contains",
        "min",
        "max",
        "length_min",
        "length_max",
        "tool_required",
        "tool_forbidden",
        "external",
    ]
    path: str = ""
    value: Any = None

    @model_validator(mode="after")
    def validate_rule(self):
        if self.op not in {"external", "tool_required", "tool_forbidden"}:
            if not self.path.startswith("/") or re.search(r"~(?![01])", self.path):
                raise ValueError("规则 path 必须是 RFC 6901 JSON Pointer，例如 /output/amount。")
        elif self.path:
            raise ValueError("external / tool 规则不使用 path。")
        if self.op in {"min", "max", "length_min", "length_max"}:
            if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
                raise ValueError("数值/长度规则需要数字 value。")
            if self.op.startswith("length_") and (self.value < 0 or int(self.value) != self.value):
                raise ValueError("长度规则 value 必须是非负整数。")
        if self.op in {"tool_required", "tool_forbidden"} and not isinstance(self.value, str):
            raise ValueError("工具规则 value 必须是工具名称。")
        if self.op not in {"exists", "external"} and "value" not in self.model_fields_set:
            raise ValueError("此规则需要显式 value。")
        return self


class TaskProfile(Contract):
    profile_version: Literal["1"]
    id: str = Field(min_length=1, max_length=200)
    version: str = "1"
    description: str = ""
    rules: list[Rule] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_rules(self):
        if len({r.id for r in self.rules}) != len(self.rules):
            raise ValueError("profile 中 rule.id 必须唯一。")
        return self


class ExternalResult(Contract):
    id: str
    status: Literal["pass", "fail", "unknown"]
    explanation: str = Field(min_length=1, max_length=10000)
    evidence_paths: list[str] = Field(default_factory=list, max_length=100)


class EvaluatorResponse(Contract):
    protocol: Literal["agent-review/evaluator-v1"]
    run_id: str
    input_hash: str
    profile_hash: str
    results: list[ExternalResult] = Field(max_length=500)

    @model_validator(mode="after")
    def unique_results(self):
        if len({r.id for r in self.results}) != len(self.results):
            raise ValueError("外部结果 id 必须唯一。")
        return self
