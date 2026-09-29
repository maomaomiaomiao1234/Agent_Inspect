"""Skill launcher contracts: portable wheel, argument preservation, and runtime integrity."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/opencode-trace-review"


@pytest.fixture
def launcher():
    path = SKILL / "scripts/run_review.py"
    spec = importlib.util.spec_from_file_location("skill_launcher", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_launcher_preserves_arguments_and_has_no_source_path(launcher):
    args = ["import", "/tmp/含 空格/session;echo nope.json", "--data-dir", "/tmp/my data"]
    command = launcher.build_command(SKILL, "/usr/local/bin/uv", args)
    assert command[-len(args) :] == args
    assert "--no-project" in command
    assert str(SKILL / "assets/requirements.txt") in command
    assert "requirements-scout.txt" not in " ".join(command)
    assert not any(str(ROOT / "src") in argument for argument in command)


def test_judge_selects_optional_dependencies(launcher):
    command = launcher.build_command(SKILL, "uv", ["judge", "run_test"])
    assert str(SKILL / "assets/requirements-scout.txt") in command
    assert command[-2:] == ["judge", "run_test"]


def test_explicit_scout_server(launcher):
    command = launcher.build_command(SKILL, "uv", ["--with-scout", "serve", "--port", "8766"])
    assert "--with-scout" not in command
    assert str(SKILL / "assets/requirements-scout.txt") in command


def test_modified_runtime_rejected_before_execution(launcher, tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    meta = json.loads((SKILL / "assets/runtime.json").read_text())
    (assets / "runtime.json").write_text(json.dumps(meta))
    (assets / meta["wheel"]).write_bytes(b"broken package")
    with pytest.raises(ValueError, match="校验"):
        launcher.build_command(tmp_path, "uv", ["list"])


def test_missing_uv_has_actionable_error(launcher, monkeypatch, capsys):
    monkeypatch.setattr(launcher.shutil, "which", lambda _: None)
    assert launcher.main(["--help"]) == 2
    assert "uv" in capsys.readouterr().err


def test_wheel_content_changes_dependency_identity(launcher, tmp_path, monkeypatch):
    import hashlib

    monkeypatch.setattr(launcher.tempfile, "gettempdir", lambda: str(tmp_path / "cache"))
    assets = tmp_path / "skill/assets"
    assets.mkdir(parents=True)
    commands = []
    for content in (b"build-one", b"build-two"):
        (assets / "same-version.whl").write_bytes(content)
        (assets / "runtime.json").write_text(
            json.dumps({"wheel": "same-version.whl", "sha256": hashlib.sha256(content).hexdigest()})
        )
        command = launcher.build_command(assets.parent, "uv", ["--help"])
        dependency = command[command.index("--with") + 1]
        assert Path(dependency).read_bytes() == content
        commands.append(dependency)
    assert commands[0] != commands[1]
