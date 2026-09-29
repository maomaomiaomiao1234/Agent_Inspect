"""Offline compatibility check against the installed OpenCode, with isolated XDG storage."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

from agent_trace_review.demo import demo_bundles
from agent_trace_review.service import ingest
from agent_trace_review.storage import Store
from agent_trace_review.util import canonical

with tempfile.TemporaryDirectory(prefix="agent-review-opencode-") as temporary:
    root = Path(temporary)
    env = dict(os.environ)
    for key, suffix in (
        ("XDG_DATA_HOME", "data"),
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_CACHE_HOME", "cache"),
        ("XDG_STATE_HOME", "state"),
    ):
        env[key] = str(root / suffix)
    env.update(OPENCODE_DISABLE_MODELS_FETCH="true", OPENCODE_DISABLE_AUTOUPDATE="true")
    source = demo_bundles()[0]["export"]
    file = root / "native.json"
    file.write_text(canonical(source))
    version = subprocess.run(
        ["opencode", "--version"], cwd=root, env=env, check=True, capture_output=True, text=True
    ).stdout.strip()
    imported = subprocess.run(
        ["opencode", "import", str(file), "--pure"], cwd=root, env=env, capture_output=True, timeout=60
    )
    if imported.returncode:
        raise RuntimeError(imported.stderr.decode(errors="replace"))
    output = subprocess.run(
        ["opencode", "export", source["info"]["id"], "--pure"],
        cwd=root,
        env=env,
        capture_output=True,
        check=True,
        timeout=60,
    ).stdout
    native = json.loads(output)
    assert len(native["messages"]) == len(source["messages"])
    store = Store(root / "review")
    run, evaluation, _ = ingest(store, output)
    assert len([e for e in run.events if e.kind == "tool"]) == 4
    assert evaluation.outcome == "inconclusive"  # Native exports contain no independent acceptance report.
    print(
        canonical(
            {
                "opencode_version": version,
                "messages": len(native["messages"]),
                "normalized_events": len(run.events),
                "native_roundtrip": "passed",
            }
        )
    )
