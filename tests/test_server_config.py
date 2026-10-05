import json
import os

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from agent_trace_review.api import create_app
from agent_trace_review.cli import app
from agent_trace_review.server_config import load_server_env, repository_defaults, repository_model_config

GROUPS = {
    "target": ("AGENT_REVIEW_TARGET_API_URL", "AGENT_REVIEW_TARGET_MODEL", "AGENT_REVIEW_TARGET_TOKEN"),
    "smolagents": ("SMOL_MODEL_API_BASE", "SMOL_MODEL_ID", "SMOL_MODEL_API_KEY"),
    "review_shared": ("AGENT_REVIEW_LLM_API_URL", "AGENT_REVIEW_LLM_MODEL", "AGENT_REVIEW_LLM_TOKEN"),
}


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    names = {n for group in GROUPS.values() for n in group}
    names.update(n for n in os.environ if n.startswith(("AGENT_REVIEW_ASSESSMENT_", "AGENT_REVIEW_REPOSITORY_")))
    names.update({"AGENT_REVIEW_ENABLE_REPOSITORY_BUILDS", "AGENT_REVIEW_DATA", "AGENT_REVIEW_TARGETS",
                  "AGENT_REVIEW_HOST", "AGENT_REVIEW_PORT", "AGENT_REVIEW_SERVICE_TOKEN"})
    for name in names:
        if name not in os.environ:
            monkeypatch.setenv(name, "temporary")
        monkeypatch.delenv(name, raising=False)


def model_env(monkeypatch, group="target"):
    for name, value in zip(GROUPS[group], ("https://provider.example/v1", "fixture-model", "fixture-api-secret"), strict=True):
        monkeypatch.setenv(name, value)


@pytest.mark.parametrize("group", list(GROUPS))
def test_model_config_resolves_complete_groups_and_never_exposes_key(monkeypatch, group):
    model_env(monkeypatch, group)
    config = repository_model_config()
    assert config.status == "configured" and config.source == group
    assert config.mappings()["SMOL_MODEL_API_KEY"] == GROUPS[group][2]
    assert config.mappings()["OPENAI_API_KEY"] == GROUPS[group][2]
    assert "fixture-api-secret" not in json.dumps(config.public())


def test_group_precedence_does_not_mix_provider_credentials(monkeypatch):
    model_env(monkeypatch, "review_shared")
    monkeypatch.setenv("SMOL_MODEL_API_BASE", "https://another.example")
    monkeypatch.setenv("SMOL_MODEL_ID", "other-model")
    assert repository_model_config().source == "review_shared"  # Blank legacy key permits sharing review config.
    monkeypatch.setenv("SMOL_MODEL_API_KEY", "other-test-secret")
    assert repository_model_config().source == "smolagents"
    monkeypatch.setenv("AGENT_REVIEW_TARGET_TOKEN", "explicit-test-secret")
    config = repository_model_config()
    assert config.source == "target" and config.status == "missing"  # Explicit partial config is an error.
    assert set(config.missing) == set(GROUPS["target"][:2])


@pytest.mark.parametrize("url", ["[https://provider.example](https://provider.example)",
                                "https://user:fixture-api-secret@provider.example", "https://provider.example?key=fixture-api-secret"])
def test_invalid_url_never_exposes_value_or_key(monkeypatch, url):
    model_env(monkeypatch)
    monkeypatch.setenv("AGENT_REVIEW_TARGET_API_URL", url)
    public = repository_model_config().public()
    assert public["status"] == "invalid" and public["api_url"] is None
    assert "fixture-api-secret" not in json.dumps(public)


def test_dotenv_no_shell_or_interpolation_and_process_environment_wins(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    token = "literal-$NO_EXPAND-$(touch marker)"
    path.write_text(f"AGENT_REVIEW_TARGET_API_URL=https://provider.example/v1\nAGENT_REVIEW_TARGET_MODEL=file-model\nAGENT_REVIEW_TARGET_TOKEN='{token}'\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENT_REVIEW_TARGET_MODEL", "process-model")
    for name in GROUPS["target"]:
        if name != "AGENT_REVIEW_TARGET_MODEL":
            monkeypatch.setenv(name, "temporary")
            monkeypatch.delenv(name)
    assert load_server_env()
    assert os.environ["AGENT_REVIEW_TARGET_MODEL"] == "process-model"
    assert os.environ["AGENT_REVIEW_TARGET_TOKEN"] == token
    assert not (tmp_path / "marker").exists()


def test_missing_env_file_is_optional_but_explicit_path_is_required(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert load_server_env() is False
    with pytest.raises(ValueError, match="不存在"):
        load_server_env(tmp_path / "missing.env")


@pytest.mark.parametrize("disable", [False, True])
def test_one_command_serve_loads_env_and_enables_local_repository_flow(tmp_path, monkeypatch, disable):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / ".env"
    path.write_text("AGENT_REVIEW_PORT=18888\nAGENT_REVIEW_ENABLE_REPOSITORY_BUILDS=true\nAGENT_REVIEW_TARGET_MODEL=dotenv-model\n")
    monkeypatch.setenv("AGENT_REVIEW_TARGET_MODEL", "")
    monkeypatch.delenv("AGENT_REVIEW_TARGET_MODEL")
    captured = {}
    def fake_app(data, **kwargs):
        captured.update(data=data, **kwargs)
        return "fixture-app"
    def fake_run(instance, **kwargs):
        captured.update(instance=instance, **kwargs)
    monkeypatch.setattr("agent_trace_review.api.create_app", fake_app)
    monkeypatch.setattr("uvicorn.run", fake_run)
    args = ["serve"] + (["--disable-repository-builds"] if disable else [])
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    assert captured["enable_repository_builds"] is not disable
    assert captured["data"] is None and captured["port"] == 18888
    assert os.environ["AGENT_REVIEW_TARGET_MODEL"] == "dotenv-model"


def test_api_resolves_url_only_auto_request_before_queue_and_key_stays_server_side(tmp_path, monkeypatch):
    model_env(monkeypatch)
    with TestClient(create_app(str(tmp_path), enable_repository_builds=True)) as client:
        manager = client.app.state.repository_jobs
        monkeypatch.setattr(manager.assessments.executor, "submit", lambda *args: None)
        capabilities = client.get("/api/repository-builds").json()
        assert capabilities["model"]["status"] == "configured" and capabilities["defaults"]["backend"] == "openai"
        result = client.post("/api/repository-jobs", headers={"X-Review-Request": "1"}, json={
            "repository_url": "https://github.com/huggingface/smolagents", "backend": "auto",
            "generation": {"cases": 1},
            "settings": {"deadline_seconds": 20, "max_output_tokens": 1234, "attempts": 2, "concurrency": 2},
        })
        assert result.status_code == 202, result.text
        request = result.json()["request"]
        assert request["backend"] == "openai"
        assert request["environment"] == dict(zip(("SMOL_MODEL_API_BASE", "SMOL_MODEL_ID", "SMOL_MODEL_API_KEY"), GROUPS["target"], strict=True))
        assert request["settings"]["max_output_tokens"] == 1234
        assert "fixture-api-secret" not in result.text and "fixture-api-secret" not in json.dumps(capabilities)
        assert "AGENT_REVIEW_SERVICE_TOKEN" not in capabilities["allowed_environment"]


def test_missing_model_does_not_queue_or_fall_back_to_offline(tmp_path, monkeypatch):
    with TestClient(create_app(str(tmp_path), enable_repository_builds=True)) as client:
        manager = client.app.state.repository_jobs
        monkeypatch.setattr(manager.assessments.executor, "submit", lambda *args: None)
        result = client.post("/api/repository-jobs", headers={"X-Review-Request": "1"}, json={
            "repository_url": "https://github.com/huggingface/smolagents", "backend": "auto",
        })
        assert result.status_code == 422 and ".env" in result.text
        assert manager.db.list() == []
        result = client.post("/api/repository-jobs", headers={"X-Review-Request": "1"}, json={
            "repository_url": "https://github.com/huggingface/smolagents", "backend": "offline",
        })
        assert result.status_code == 202 and result.json()["request"]["backend"] == "offline"


def test_web_defaults_read_env_and_validate_cumulative_budget(monkeypatch):
    monkeypatch.setenv("AGENT_REVIEW_ASSESSMENT_CASES", "2")
    monkeypatch.setenv("AGENT_REVIEW_ASSESSMENT_MAX_OUTPUT_TOKENS", "4096")
    assert repository_defaults()["cases"] == 2 and repository_defaults()["max_output_tokens"] == 4096
    monkeypatch.setenv("AGENT_REVIEW_ASSESSMENT_CASES", "30")
    with pytest.raises(ValueError, match="默认参数"):
        repository_defaults()


def test_manifest_model_aliases_bind_only_declared_variables_and_do_not_enter_build(tmp_path, monkeypatch):
    from agent_trace_review.repository_contracts import RepositoryAssessmentInput
    from agent_trace_review.repository_jobs import build_plan

    model_env(monkeypatch)
    root = tmp_path / "source"
    root.mkdir()
    (root / "Dockerfile").write_text("FROM scratch\n")
    (root / "agent-review.json").write_text(json.dumps({
        "manifest_version": "agent-review/repository-v1", "port": 9000, "test_template": "smolagents",
        "required_environment": ["OPENAI_BASE_URL", "OPENAI_API_KEY"],
    }))
    work = tmp_path / "work"
    work.mkdir()
    request = RepositoryAssessmentInput(repository_url="https://github.com/example/agent", backend="openai")
    build_plan(request, root, work, default_environment=repository_model_config().mappings())
    assert request.environment == {"OPENAI_BASE_URL": "AGENT_REVIEW_TARGET_API_URL", "OPENAI_API_KEY": "AGENT_REVIEW_TARGET_TOKEN"}
    assert b"fixture-api-secret" not in b"".join(p.read_bytes() for p in (work / "context").rglob("*") if p.is_file())
