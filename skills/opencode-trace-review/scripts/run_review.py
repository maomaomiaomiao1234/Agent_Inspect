#!/usr/bin/env python3
"""Run the bundled evaluator from any working directory; uv caches dependencies."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def build_command(skill_root: Path, uv: str, arguments: list[str]) -> list[str]:
    arguments = list(arguments)
    scout = arguments[:1] == ["--with-scout"]
    if scout:
        arguments.pop(0)
    scout = scout or arguments[:1] == ["judge"]
    assets = skill_root / "assets"
    metadata = json.loads((assets / "runtime.json").read_text())
    wheel = assets / metadata["wheel"]
    if wheel.resolve().parent != assets.resolve():
        raise ValueError("运行包路径校验失败，请重新安装 skill。")
    content = wheel.read_bytes()
    if hashlib.sha256(content).hexdigest() != metadata["sha256"]:
        raise ValueError("运行包 SHA-256 校验失败，请重新安装 skill。")
    # uv may reuse an environment for the same wheel path/version after a rebuild.
    # A content-addressed path makes the dependency identity change with its bytes.
    runtime = Path(tempfile.gettempdir()) / "opencode-trace-review-runtime" / metadata["sha256"]
    runtime.mkdir(parents=True, exist_ok=True)
    cached_wheel = runtime / wheel.name
    if not cached_wheel.exists():
        with tempfile.NamedTemporaryFile(dir=runtime, delete=False) as pending:
            pending.write(content)
            pending_path = pending.name
        os.replace(pending_path, cached_wheel)
    if hashlib.sha256(cached_wheel.read_bytes()).hexdigest() != metadata["sha256"]:
        raise ValueError("运行包缓存校验失败，请清理对应 opencode-trace-review-runtime 缓存后重试。")
    requirements = assets / ("requirements-scout.txt" if scout else "requirements.txt")
    return [
        uv,
        "run",
        "--quiet",
        "--no-project",
        "--python",
        "3.12",
        "--with-requirements",
        str(requirements),
        "--with",
        str(cached_wheel),
        "agent-review",
        *arguments,
    ]


def main(arguments=None) -> int:
    uv = shutil.which("uv")
    if not uv:
        print("需要 uv 运行封装的评估引擎；请安装 uv 后重试。", file=sys.stderr)
        return 2
    try:
        command = build_command(
            Path(__file__).resolve().parents[1], uv, sys.argv[1:] if arguments is None else arguments
        )
        env = os.environ.copy()
        env.setdefault("UV_CACHE_DIR", str(Path(tempfile.gettempdir()) / "opencode-trace-review-uv"))
        return subprocess.run(command, env=env).returncode
    except (OSError, ValueError, KeyError) as exc:
        print(f"无法启动评估引擎：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
