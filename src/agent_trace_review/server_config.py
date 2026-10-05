"""Trusted server configuration; credentials never enter browser capabilities."""

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values


def load_server_env(path: Path | None = None):
    selected = path if path is not None else Path.cwd() / ".env"
    if not selected.exists():
        if path is not None:
            raise ValueError("指定的环境配置文件不存在。")
        return False
    if not selected.is_file() or selected.stat().st_size > 1024 * 1024:
        raise ValueError("环境配置必须是小于 1 MiB 的文件。")
    # Existing process environment wins. No shell evaluation or secret interpolation.
    for name, value in dotenv_values(selected, interpolate=False).items():
        if value is not None:
            os.environ.setdefault(name, value)
    return True


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    if value.strip().lower() not in {"true", "false", "1", "0", "yes", "no"}:
        raise ValueError(f"{name} 需要 true 或 false。")
    return value.strip().lower() in {"true", "1", "yes"}


@dataclass(frozen=True)
class RepositoryModelConfig:
    source: str
    names: tuple[str, str, str]
    status: str
    missing: tuple[str, ...]
    api_url: str | None = None
    model: str | None = None

    def public(self):
        return {"source": self.source, "status": self.status, "missing": list(self.missing),
                "api_url": self.api_url, "model": self.model,
                "reason": "模型配置就绪，凭据仅在服务端读取。" if self.status == "configured" else
                "API 地址或模型名格式无效，请检查服务端 .env。" if self.status == "invalid" else
                "请在服务端 .env 填写模型配置并重启服务。"}

    def mappings(self):
        if self.status != "configured":
            return {}
        url, model, token = self.names
        return {"SMOL_MODEL_API_BASE": url, "SMOL_MODEL_ID": model, "SMOL_MODEL_API_KEY": token,
                "AGENT_REVIEW_TARGET_API_URL": url, "AGENT_REVIEW_TARGET_MODEL": model, "AGENT_REVIEW_TARGET_TOKEN": token,
                "OPENAI_BASE_URL": url, "OPENAI_MODEL": model, "OPENAI_API_KEY": token}


def repository_model_config():
    groups = (
        ("target", ("AGENT_REVIEW_TARGET_API_URL", "AGENT_REVIEW_TARGET_MODEL", "AGENT_REVIEW_TARGET_TOKEN")),
        ("smolagents", ("SMOL_MODEL_API_BASE", "SMOL_MODEL_ID", "SMOL_MODEL_API_KEY")),
        ("review_shared", ("AGENT_REVIEW_LLM_API_URL", "AGENT_REVIEW_LLM_MODEL", "AGENT_REVIEW_LLM_TOKEN")),
    )
    # Do not mix a provider URL/model with a different group's key.
    selected = next((g for g in groups if g[0] == "target" and any(os.environ.get(n, "").strip() for n in g[1])), None)
    if selected is None:
        selected = next((g for g in groups[1:] if os.environ.get(g[1][2], "").strip()), None)
    if selected is None:
        selected = next((g for g in groups if any(os.environ.get(n, "").strip() for n in g[1])), groups[0])
    source, names = selected
    values = tuple(os.environ.get(n, "").strip() for n in names)
    missing = tuple(n for n, v in zip(names, values, strict=True) if not v)
    if missing:
        return RepositoryModelConfig(source, names, "missing", missing)
    url, model, token = values
    try:
        parsed = urlsplit(url)
        valid = (parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username
                 and not parsed.password and not parsed.query and not parsed.fragment and not any(c.isspace() for c in url))
        _ = parsed.port
    except ValueError:
        valid = False
    if not valid or len(model) > 200 or any(ord(c) < 32 for c in model) or token in url or token in model:
        return RepositoryModelConfig(source, names, "invalid", ())
    return RepositoryModelConfig(source, names, "configured", (), url, model)


def repository_defaults():
    from .repository_contracts import RepositoryRunSettings

    backend = os.environ.get("AGENT_REVIEW_REPOSITORY_BACKEND", "openai").strip() or "openai"
    if backend not in {"offline", "openai"}:
        raise ValueError("AGENT_REVIEW_REPOSITORY_BACKEND 需要 openai 或 offline。")
    try:
        settings = RepositoryRunSettings(
            deadline_seconds=float(os.environ.get("AGENT_REVIEW_ASSESSMENT_DEADLINE", "60")),
            max_output_tokens=int(os.environ.get("AGENT_REVIEW_ASSESSMENT_MAX_OUTPUT_TOKENS", "2048")),
            attempts=int(os.environ.get("AGENT_REVIEW_ASSESSMENT_ATTEMPTS", "1")),
            concurrency=int(os.environ.get("AGENT_REVIEW_ASSESSMENT_CONCURRENCY", "1")),
        )
        cases = int(os.environ.get("AGENT_REVIEW_ASSESSMENT_CASES", "12"))
        seed = int(os.environ.get("AGENT_REVIEW_ASSESSMENT_SEED", "42"))
        if not 1 <= cases <= 30 or not 0 <= seed <= 2147483647:
            raise ValueError()
        if cases * settings.attempts * settings.deadline_seconds > 900:
            raise ValueError()
    except ValueError:
        raise ValueError(".env 中的评测默认参数无效，请检查案例数、种子、期限、Token、重复和并发限制。") from None
    return {"backend": backend, "cases": cases, "seed": seed, **settings.model_dump()}
