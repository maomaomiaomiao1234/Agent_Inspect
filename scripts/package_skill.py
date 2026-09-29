"""Bundle the built wheel and lock-derived runtime requirements into the skill."""

import hashlib
import json
import shutil
import subprocess
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
wheel = ROOT / "dist" / f"agent_trace_review-{version}-py3-none-any.whl"
if not wheel.is_file():
    raise SystemExit("Build the frontend and wheel first: python scripts/build_web.py; uv build --wheel")
assets = ROOT / "skills/opencode-trace-review/assets"
assets.mkdir(parents=True, exist_ok=True)
previous = json.loads((assets / "runtime.json").read_text()) if (assets / "runtime.json").exists() else {}
shutil.copy2(wheel, assets / wheel.name)
for scout in (False, True):
    cmd = ["uv", "export", "--frozen", "--no-dev", "--no-emit-project", "--no-hashes"]
    if scout:
        cmd += ["--extra", "scout"]
    result = subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True, text=True)
    requirements = [line for line in result.stdout.splitlines() if line and not line.lstrip().startswith("#")]
    target = assets / ("requirements-scout.txt" if scout else "requirements.txt")
    target.write_text(
        "# Generated from the project's uv.lock by scripts/package_skill.py.\n"
        + "\n".join(requirements)
        + "\n"
    )
(assets / "runtime.json").write_text(
    json.dumps(
        {"version": version, "wheel": wheel.name, "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()},
        indent=2,
    )
    + "\n"
)
old_wheel = assets / previous.get("wheel", wheel.name)
if old_wheel.name != wheel.name and old_wheel.resolve().parent == assets.resolve() and old_wheel.is_file():
    old_wheel.unlink()
skill = assets.parent
archive_path = ROOT / "dist/opencode-trace-review.skill.zip"
with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(skill.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            archive.write(path, Path(skill.name) / path.relative_to(skill))
print(f"Bundled evaluator {version}: {assets}")
print(f"Skill archive: {archive_path}")
