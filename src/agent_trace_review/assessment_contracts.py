"""Operator configuration and data-only active assessment contracts."""

from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from .contracts import Contract, TaskProfile, TraceEvent, Usage

ID = r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}$"


class Budget(Contract):
    id: str = Field(default="default", pattern=ID)
    deadline_seconds: float = Field(default=30, ge=0.05, le=60)
    max_output_tokens: int | None = Field(default=None, ge=1, le=100000, strict=True)


class Turn(Contract):
    prompt: str = Field(min_length=1, max_length=20000)
    history: Literal["full", "current"] = "full"


class SourceReference(Contract):
    path: str = Field(min_length=1, max_length=500)
    line: int = Field(ge=1, strict=True)

    @model_validator(mode="after")
    def safe_path(self):
        if self.path.startswith(("/", "\\")) or ".." in self.path.split("/") or "\\" in self.path:
            raise ValueError("source_refs 需要仓库内相对路径。")
        return self


class AssessmentCase(Contract):
    id: str = Field(pattern=ID)
    description: str = Field(default="", max_length=2000)
    category: Literal["capability", "multi_turn", "robustness", "memory", "session_isolation"] = "capability"
    turns: list[Turn] = Field(min_length=1, max_length=10)
    input: dict[str, Any] = Field(default_factory=dict)
    profile: TaskProfile
    claim_ids: list[str] = Field(default_factory=list, max_length=20)
    source_refs: list[SourceReference] = Field(default_factory=list, max_length=20)


class AssessmentSuite(Contract):
    suite_version: Literal["agent-review/assessment-v1"] = "agent-review/assessment-v1"
    id: str = Field(pattern=ID)
    version: str = Field(default="1", min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    cases: list[AssessmentCase] = Field(min_length=1, max_length=30)
    budgets: list[Budget] = Field(default_factory=lambda: [Budget()], min_length=1, max_length=5)
    attempts: int = Field(default=1, ge=1, le=10, strict=True)

    @model_validator(mode="after")
    def bounded_work(self):
        if len({c.id for c in self.cases}) != len(self.cases) or len({b.id for b in self.budgets}) != len(
            self.budgets
        ):
            raise ValueError("case 和 budget 的 id 必须各自唯一。")
        requests = sum(len(c.turns) for c in self.cases) * len(self.budgets) * self.attempts
        duration = len(self.cases) * self.attempts * sum(b.deadline_seconds for b in self.budgets)
        if requests > 300 or duration > 900:
            raise ValueError("单次评测最多 300 个请求、900 秒累计任务 deadline。")
        return self


class DockerDeployment(Contract):
    image: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    port: int = Field(ge=1, le=65535, strict=True)
    user: str = Field(default="65534:65534", pattern=r"^[0-9]+:[0-9]+$")
    memory_mb: int = Field(default=512, ge=64, le=8192, strict=True)
    cpus: float = Field(default=1, ge=0.1, le=8)
    environment: dict[str, str] = Field(default_factory=dict, max_length=20)
    service_token_variable: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,99}$")

    @model_validator(mode="after")
    def env_names(self):
        import re

        if int(self.user.split(":")[0]) == 0:
            raise ValueError("被测容器需要非 root UID。")
        if any(
            not re.fullmatch(r"[A-Z][A-Z0-9_]{0,99}", k) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,99}", v)
            for k, v in self.environment.items()
        ):
            raise ValueError("environment 只接受容器变量名到宿主机变量名的映射。")
        if self.service_token_variable in self.environment:
            raise ValueError("自动生成的服务令牌不能被 environment 覆盖。")
        return self


class TargetDefinition(Contract):
    id: str = Field(pattern=ID)
    endpoint: str | None = Field(default=None, max_length=2000)
    deployment: DockerDeployment | None = None
    task_path: str = Field(default="/task", pattern=r"^/[a-zA-Z0-9/_-]*$")
    health_path: str | None = Field(default=None, pattern=r"^/[a-zA-Z0-9/_-]*$")
    health_status_field: str = Field(default="status", pattern=r"^[a-zA-Z0-9_]{1,100}$")
    health_status_value: str = Field(default="ok", min_length=1, max_length=100)
    token_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,99}$")
    repository: str | None = Field(default=None, max_length=2000)
    ref: str = Field(default="HEAD", max_length=200)
    repository_url: str | None = Field(default=None, max_length=2000)
    demo: bool = False

    @model_validator(mode="after")
    def endpoint_policy(self):
        if (self.endpoint is None) == (self.deployment is None):
            raise ValueError("必须且只能配置 endpoint 或 deployment。")
        if self.endpoint:
            url = urlsplit(self.endpoint)
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
                or url.path not in {"", "/"}
            ):
                raise ValueError("endpoint 需要无凭据、无 query 的 HTTP(S) 服务根地址。")
            _ = url.port
        if self.deployment and not self.health_path:
            raise ValueError("Docker 部署需要 health_path，以记录实际启动检查。")
        return self


class TargetRequest(Contract):
    protocol: Literal["agent-review/target-v1"] = "agent-review/target-v1"
    session_id: str
    turn: int = Field(ge=0)
    prompt: str
    messages: list[dict[str, Any]]
    input: dict[str, Any] = Field(default_factory=dict)
    budget: Budget


def validate_reported_usage(usage):
    if usage is None:
        return
    tokens = usage.tokens
    if any(v is not None and v > 10**12 for v in tokens.model_dump().values()):
        raise ValueError("单次自报 Token 超过上限。")
    if usage.cost_usd is not None and usage.cost_usd > 10**9:
        raise ValueError("单次自报费用超过上限。")
    if all(v is not None for v in (tokens.input, tokens.output, tokens.total)):
        if tokens.total != tokens.input + tokens.output:
            raise ValueError("自报 total 必须等于 input + output。")


class TargetTraceEvent(TraceEvent):
    id: str = Field(min_length=1, max_length=180)
    parent_id: str | None = Field(default=None, min_length=1, max_length=180)
    kind: Literal["llm", "tool"]
    status: Literal["completed", "error", "skipped", "unknown"] = "unknown"
    provenance: Literal["target_reported"] = "target_reported"

    @model_validator(mode="after")
    def bounded_call(self):
        validate_reported_usage(self.usage)
        return self


class TargetTrace(Contract):
    trace_version: Literal["agent-review/target-trace-v1"] = "agent-review/target-trace-v1"
    session_id: str = Field(min_length=1, max_length=100)
    turn: int = Field(ge=0, le=9, strict=True)
    coverage: Literal["complete", "partial"] = "partial"
    events: list[TargetTraceEvent] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def unique_calls(self):
        seen = set()
        for event in self.events:
            if event.id in seen or event.parent_id and event.parent_id not in seen:
                raise ValueError("调用 id 必须唯一；parent_id 必须引用本轮已记录的调用。")
            seen.add(event.id)
        return self


class TargetResponse(Contract):
    protocol: Literal["agent-review/target-v1"] = "agent-review/target-v1"
    output: Any
    usage: Usage | None = None
    execution_status: Literal["completed", "error"] = "completed"
    error: Literal["agent_execution_failed"] | None = None
    trace: TargetTrace | None = None

    @model_validator(mode="after")
    def bounded_usage(self):
        validate_reported_usage(self.usage)
        if (self.execution_status == "error") != (self.error is not None):
            raise ValueError("执行错误需要 error；成功响应不能声明 error。")
        if self.trace and self.trace.coverage == "complete":
            calls = [e for e in self.trace.events if e.kind == "llm"]
            if calls:
                totals = {}
                for key in ("input", "output", "total", "reasoning"):
                    values = [getattr(e.usage.tokens, key) if e.usage else None for e in calls]
                    totals[key] = sum(values) if all(v is not None for v in values) else None
                if totals["total"] is None and totals["input"] is not None and totals["output"] is not None:
                    totals["total"] = totals["input"] + totals["output"]
                costs = [e.usage.cost_usd if e.usage else None for e in calls]
                cost = sum(costs) if all(v is not None for v in costs) else None
                if self.usage is None and (any(v is not None for v in totals.values()) or cost is not None):
                    self.usage = Usage(tokens=totals, cost_usd=cost)
                elif self.usage:
                    for key, value in totals.items():
                        reported = getattr(self.usage.tokens, key)
                        if value is not None and reported is not None and value != reported:
                            raise ValueError("汇总 Token 与本轮逐次调用不一致。")
                    if cost is not None and self.usage.cost_usd is not None:
                        if abs(cost - self.usage.cost_usd) > 1e-8:
                            raise ValueError("汇总费用与本轮逐次调用不一致。")
                validate_reported_usage(self.usage)
        return self


class AssessmentInput(Contract):
    target_id: str = Field(pattern=ID)
    suite: AssessmentSuite


class SuiteGenerationInput(Contract):
    template: Literal["smolagents"] = "smolagents"
    cases: int = Field(default=12, ge=1, le=30, strict=True)
    seed: int = Field(default=42, ge=0, le=2147483647, strict=True)
