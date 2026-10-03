"""Exercise the judge against actual HTTP controls and optionally an actual Docker target."""

import argparse
import json
import runpy
import subprocess
import threading
from pathlib import Path

from agent_trace_review.assessment_contracts import AssessmentSuite, TargetDefinition
from agent_trace_review.assessment_store import AssessmentStore
from agent_trace_review.assessments import (
    assessment_markdown,
    compare_assessments,
    prepare_assessment,
    run_assessment,
)
from agent_trace_review.repositories import inspect_repository
from agent_trace_review.storage import Store
from agent_trace_review.util import canonical

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples/assessment"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--docker-image", help="Optional locally installed immutable sha256 image ID")
    args = parser.parse_args()
    db = AssessmentStore(Store(args.data_dir))
    repository = db.store.root / "control-repository"
    if repository.exists():
        raise ValueError("验证目录已包含 control-repository，请使用新的独立 data-dir。")
    repository.mkdir()
    for name in ("mock_agent.py", "Dockerfile"):
        (repository / name).write_bytes((EXAMPLES / name).read_bytes())
    (repository / "README.md").write_text("# Control Agent\nSupports persistent memory within a session.\n")
    for command in (
        ["init"],
        ["add", "."],
        [
            "-c",
            "user.name=Assessment control",
            "-c",
            "user.email=control@example.invalid",
            "commit",
            "-m",
            "Fixed control fixture",
        ],
    ):
        subprocess.run(["git", "-C", str(repository), *command], check=True, capture_output=True)
    profile = inspect_repository(repository)
    raw = json.loads((EXAMPLES / "suite.json").read_text())
    memory = next(c for c in raw["cases"] if c["id"] == "memory")
    memory["claim_ids"] = [profile["claims"][0]["id"]]
    memory["source_refs"] = [{"path": "mock_agent.py", "line": 11}]
    suite = AssessmentSuite.model_validate(raw)
    (db.store.root / "suite.json").write_text(suite.model_dump_json(indent=2))
    server = runpy.run_path(str(EXAMPLES / "mock_agent.py"))["serve"](port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    jobs = {}
    try:
        for candidate in ("correct", "incorrect", "noop", "missing", "leaky"):
            target = TargetDefinition(
                id="control",
                endpoint=f"http://127.0.0.1:{server.server_port}",
                task_path="/" + candidate,
                health_path="/health",
                repository=str(repository),
                ref=profile["commit"],
                demo=True,
            )
            job, repo = prepare_assessment(db, target, suite)
            result = run_assessment(db, job["id"], target, suite, repo)
            assert result["state"] == "completed", result["error"]
            expected = {
                "correct": "pass",
                "incorrect": "fail",
                "noop": "fail",
                "missing": "inconclusive",
            }.get(candidate)
            if expected:
                assert {r["outcome"] for r in result["results"]} == {expected}
            else:
                assert {r["outcome"] for r in result["results"] if r["case_id"] == "session-isolation"} == {
                    "fail"
                }
            jobs[candidate] = result
            (db.store.root / f"{candidate}.assessment.json").write_text(canonical(result))
            (db.store.root / f"{candidate}.md").write_text(assessment_markdown(result))
            print(
                f"{candidate}: {result['id']} {[(c['pass'], c['fail'], c['unknown']) for c in result['curves']]}"
            )
        comparison = compare_assessments(jobs["correct"], jobs["incorrect"])
        assert comparison["comparable"] and len(comparison["changes"]) == 16
        (db.store.root / "regression.json").write_text(canonical(comparison))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    if args.docker_image:
        target = TargetDefinition(
            id="docker-control",
            deployment={"image": args.docker_image, "port": 9081},
            task_path="/correct",
            health_path="/health",
            repository=str(repository),
            ref=profile["commit"],
            demo=True,
        )
        job, repo = prepare_assessment(db, target, suite)
        result = run_assessment(db, job["id"], target, suite, repo)
        assert result["state"] == "completed", result["error"]
        assert {r["outcome"] for r in result["results"]} == {"pass"}
        (db.store.root / "docker.assessment.json").write_text(canonical(result))
        print(
            f"Docker: {result['id']} {result['deployment']['image_id']} {result['completed']}/{result['planned']}"
        )
    print(f"Real HTTP assessment controls verified. Evidence: {db.store.root}")


if __name__ == "__main__":
    main()
