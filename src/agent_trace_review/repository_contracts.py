"""Data-only contracts for explicitly enabled repository builds."""

import re
from pathlib import PurePosixPath
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .assessment_contracts import AssessmentSuite, DockerDeployment, RepositoryPlanInput, SuiteGenerationInput
from .contracts import Contract

ENV_NAME = r"^[A-Z][A-Z0-9_]{0,99}$"


def github_url(value: str) -> str:
    match = re.fullmatch(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/?", value)
    if not match:
        raise ValueError("首版需要公开 GitHub HTTPS 仓库地址，不接受凭据、query 或其他协议。")
    owner, repo = match.groups()
    repo = repo.removesuffix(".git")
    if owner in {".", ".."} or repo in {"", ".", ".."}:
        raise ValueError("无效仓库地址。")
    return f"https://github.com/{owner}/{repo}"


def relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value or "\x00" in value:
        raise ValueError("需要仓库内相对路径。")
    return value


class RepositoryRunSettings(Contract):
    deadline_seconds: float = Field(default=60, ge=0.05, le=60)
    max_output_tokens: int = Field(default=2048, ge=1, le=100000, strict=True)
    attempts: int = Field(default=1, ge=1, le=3, strict=True)
    concurrency: int = Field(default=1, ge=1, le=4, strict=True)


class RepositoryAdaptationSettings(Contract):
    enabled: bool = True
    max_repairs: int = Field(default=1, ge=0, le=2, strict=True)


class RepositoryAssessmentInput(Contract):
    repository_url: str = Field(max_length=500)
    ref: str = Field(default="HEAD", pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,199}$")
    recipe: Literal["auto", "manifest", "smolagents", "llm"] = "auto"
    manifest_path: str = Field(default="agent-review.json", max_length=300)
    generation: SuiteGenerationInput = Field(default_factory=SuiteGenerationInput)
    suite: AssessmentSuite | None = None
    planning: RepositoryPlanInput | None = None
    backend: Literal["auto", "offline", "openai"] = "offline"
    settings: RepositoryRunSettings | None = None
    adaptation: RepositoryAdaptationSettings = Field(default_factory=RepositoryAdaptationSettings)
    model_gateway: bool = False
    environment: dict[str, str] = Field(default_factory=dict, max_length=20)

    _url = field_validator("repository_url")(github_url)
    _manifest = field_validator("manifest_path")(relative_path)

    @model_validator(mode="after")
    def environment_names(self):
        if self.suite is not None and self.planning is not None:
            raise ValueError("自定义 suite 和源码 planning 只能选择一个。")
        if self.suite is not None and self.settings is not None:
            raise ValueError("上传的独立题集保留自身预算；settings 仅用于生成题集。")
        cases = self.planning.cases if self.planning else self.generation.cases
        if self.settings and cases * self.settings.attempts * self.settings.deadline_seconds > 900:
            raise ValueError("案例数 × 重复次数 × 每案例期限不能超过 900 秒。")
        if any(not re.fullmatch(ENV_NAME, k) or not re.fullmatch(ENV_NAME, v) for k, v in self.environment.items()):
            raise ValueError("环境变量仅接受名称映射，不能包含密钥值。")
        return self


class RepositoryManifest(Contract):
    manifest_version: Literal["agent-review/repository-v1"]
    context: str = Field(default=".", max_length=300)
    dockerfile: str = Field(default="Dockerfile", max_length=300)
    port: int = Field(ge=1, le=65535, strict=True)
    task_path: str = Field(default="/task", pattern=r"^/[a-zA-Z0-9/_-]*$")
    health_path: str = Field(default="/health", pattern=r"^/[a-zA-Z0-9/_-]*$")
    health_status_field: str = Field(default="status", pattern=r"^[a-zA-Z0-9_]{1,100}$")
    health_status_value: str = Field(default="ok", min_length=1, max_length=100)
    user: str = "65534:65534"
    memory_mb: int = 512
    cpus: float = 1
    required_environment: list[str] = Field(default_factory=list, max_length=20)
    test_template: Literal["smolagents"] | None = None
    demo: bool = False
    service_token_variable: str | None = Field(default=None, pattern=ENV_NAME)

    _context = field_validator("context")(relative_path)
    _dockerfile = field_validator("dockerfile")(relative_path)

    @model_validator(mode="after")
    def runtime_limits(self):
        DockerDeployment(image="sha256:" + "0" * 64, port=self.port, user=self.user,
                         memory_mb=self.memory_mb, cpus=self.cpus)
        if any(not re.fullmatch(ENV_NAME, name) for name in self.required_environment):
            raise ValueError("required_environment 只接受变量名。")
        return self
