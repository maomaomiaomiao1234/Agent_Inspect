from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .usage_accounting import UsageSummary

VERSION = "agent-review/0.3.0"


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)


class Check(Model):
    id: str
    kind: Literal["test", "build", "lint", "typecheck", "custom"] = "test"
    command: str | None = None
    required: bool = True


class Task(Model):
    id: str
    version: str = "1"
    prompt: str = ""
    base_commit: str | None = None
    initial_state_hash: str | None = None
    environment_hash: str | None = None
    suite_hash: str | None = None
    budget_policy: str | None = None
    final_state_hash: str | None = None
    checks: list[Check] = Field(default_factory=list)
    protected_paths: list[str] = Field(default_factory=list)
    allowed_paths: list[str] = Field(default_factory=list)


class Evidence(Model):
    id: str
    kind: Literal["event", "artifact", "absence"]
    event_id: str | None = None
    artifact_id: str | None = None
    pointer: str | None = None
    description: str
    query: dict[str, Any] | None = None


class Event(Model):
    id: str
    seq: int
    native_id: str
    message_id: str
    parent_message_id: str | None = None
    kind: str
    title: str
    role: str = "assistant"
    start_ms: float | None = None
    end_ms: float | None = None
    time_basis: Literal["native", "message", "unknown"] = "unknown"
    status: str = "unknown"
    tool: str | None = None
    effect: Literal["read", "write", "unknown"] = "unknown"
    call_id: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    output: str = ""
    files: list[str] = Field(default_factory=list)
    stage: list[str] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)
    source_pointer: str
    evidence_id: str


class Verification(Model):
    id: str
    check_id: str | None = None
    kind: Literal["test", "build", "lint", "typecheck", "custom"] = "test"
    phase: Literal["baseline", "final", "intermediate"] = "final"
    provenance: Literal["observed_tool", "external_verifier"]
    result: Literal["pass", "fail", "error", "timeout", "skipped", "unknown"]
    state_hash: str | None = None
    suite_hash: str | None = None
    executed: int | None = Field(default=None, ge=0)
    passed: int | None = Field(default=None, ge=0)
    failed: int | None = Field(default=None, ge=0)
    skipped: int | None = Field(default=None, ge=0)
    cases: dict[str, Literal["pass", "fail", "error", "skipped"]] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    event_id: str | None = None
    command: str | None = None
    source_text: str | None = None

    @model_validator(mode="after")
    def consistent_counts(self):
        if self.provenance != "external_verifier":
            return self
        if self.result == "pass" and (
            (self.failed or 0) > 0 or any(v in {"fail", "error"} for v in self.cases.values())
        ):
            raise ValueError("通过的报告不能同时包含失败用例。")
        if self.executed is not None and self.passed is not None and self.failed is not None:
            if self.executed != self.passed + self.failed:
                raise ValueError("executed 必须等于 passed + failed（不含 skipped）。")
        if self.cases:
            actual = {
                "passed": sum(v == "pass" for v in self.cases.values()),
                "failed": sum(v in {"fail", "error"} for v in self.cases.values()),
                "skipped": sum(v == "skipped" for v in self.cases.values()),
            }
            actual["executed"] = actual["passed"] + actual["failed"]
            if any(
                getattr(self, name) is not None and getattr(self, name) != count
                for name, count in actual.items()
            ):
                raise ValueError("报告计数与完整 cases 集合不一致；不完整的 cases 应省略。")
            for name, count in actual.items():
                setattr(self, name, count)
        return self


class Metric(Model):
    key: str
    label: str
    value: int | float | str | None
    unit: str = ""
    status: Literal["observed", "derived", "partial", "unknown", "not_applicable"] = "observed"
    evidence_ids: list[str] = Field(default_factory=list)
    note: str = ""


class Finding(Model):
    id: str
    category: str
    severity: Literal["info", "warning", "high"]
    verdict: Literal["supported", "hypothesis", "insufficient_evidence"]
    title: str
    explanation: str
    evidence_ids: list[str]
    counter_evidence_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    recommendation: str = ""
    origin: str = "deterministic"


class Run(Model):
    id: str
    title: str
    session_id: str
    parent_session_id: str | None = None
    imported_at: str
    source_hash: str
    source_artifact: str
    adapter_version: str = VERSION
    agent_version: str | None = None
    directory: str = ""
    first_message: str | None = None
    last_message: str | None = None
    task: Task | None = None
    task_prompt: str = ""
    models: list[str] = Field(default_factory=list)
    execution_status: str = "unknown"
    start_ms: float | None = None
    end_ms: float | None = None
    coverage: dict[str, dict[str, str]] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    verifications: list[Verification] = Field(default_factory=list)
    usage: list[dict[str, Any]] = Field(default_factory=list)
    usage_summary: UsageSummary | None = None
    diff: str = ""
    diff_provided: bool = False
    diff_artifact: str | None = None
    demo: bool = False
    source_format: Literal["opencode", "generic"] = "opencode"
    framework: str = "opencode"
    output: Any = None
    output_present: bool = False
    artifacts: dict[str, Any] = Field(default_factory=dict)
    trace_complete: bool = False


class Evaluation(Model):
    id: str
    run_id: str
    created_at: str
    version: str = VERSION
    corpus_hash: str
    outcome: Literal["pass", "fail", "inconclusive"]
    outcome_reason: str
    metrics: list[Metric]
    findings: list[Finding]
    evidence: list[Evidence]
    verifications: list[Verification]
    judge: dict[str, Any] | None = None
    custom: dict[str, Any] | None = None
