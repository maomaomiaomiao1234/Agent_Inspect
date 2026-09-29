"""Compile the UI and stage its assets for a self-contained Python wheel."""

import shutil
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
subprocess.run([sys.executable, str(root / "scripts/generate_types.py")], check=True, cwd=root)
subprocess.run(["npm", "run", "build"], check=True, cwd=root / "web")
target = root / "src/agent_trace_review/static"
if target.exists():
    shutil.rmtree(target)
shutil.copytree(root / "web/dist", target)
print(f"Packaged web assets: {target}")
