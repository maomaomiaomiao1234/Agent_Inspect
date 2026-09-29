"""Regenerate synthetic fixtures; these are not real agent/model performance claims."""

import json
from pathlib import Path

from agent_trace_review.demo import demo_bundles

root = Path(__file__).resolve().parents[1] / "examples"
root.mkdir(exist_ok=True)
for name, bundle in zip(("focused", "iterative"), demo_bundles(), strict=True):
    (root / f"{name}.bundle.json").write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n")
    (root / f"{name}.native.json").write_text(
        json.dumps(bundle["export"], ensure_ascii=False, indent=2) + "\n"
    )
    if name == "focused":
        (root / "task.json").write_text(json.dumps(bundle["task"], ensure_ascii=False, indent=2) + "\n")
        (root / "final.patch").write_text(bundle["diff"])
