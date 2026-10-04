"""Verify real Docker orphan recovery using the local control-agent image; never calls a model."""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from agent_trace_review.assessment_contracts import SuiteGenerationInput, TargetDefinition
from agent_trace_review.assessment_store import AssessmentStore
from agent_trace_review.assessments import AssessmentManager, prepare_assessment
from agent_trace_review.storage import Store
from agent_trace_review.suite_generation import generate_suite
from agent_trace_review.target_client import _docker, deployed_target
from agent_trace_review.util import canonical


def child(root, image):
    db = AssessmentStore(Store(root))
    target = TargetDefinition(id="recovery-control", deployment={"image": image, "port": 9081},
                              health_path="/health", task_path="/correct", demo=True)
    job, _ = prepare_assessment(db, target, generate_suite(SuiteGenerationInput(cases=1)))
    db.claim(job["id"])
    with deployed_target(target, resources=db, job_id=job["id"]) as (_, deployment):
        body = {k: v for k, v in job.items() if k not in {"id", "state", "created_at", "updated_at", "cancel_requested"}}
        body["deployment"] = deployment
        db.update(job["id"], body)
        (root / "crash-marker.json").write_text(canonical({"job_id": job["id"], "container_name": deployment["container_name"]}))
        os._exit(17)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="Existing fixed sha256 image ID for examples/assessment")
    parser.add_argument("--child", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.child:
        child(args.child, args.image)
        return
    with tempfile.TemporaryDirectory(prefix="agent-repository-recovery-") as directory:
        root = Path(directory)
        manager = None
        try:
            result = subprocess.run([sys.executable, __file__, "--image", args.image, "--child", str(root)],
                                    capture_output=True, timeout=60)
            assert result.returncode == 17, result.stderr.decode()
            import json

            marker = json.loads((root / "crash-marker.json").read_text())
            before = _docker("ps", "-q", "--filter", f"name=^/{marker['container_name']}$")
            assert before, "The crash must leave a real running container to exercise recovery"
            manager = AssessmentManager(Store(root), {})
            assert manager.db.job(marker["job_id"])["state"] == "interrupted"
            assert not manager.db.pending_resources()
            assert not _docker("ps", "-aq", "--filter", f"name=^/{marker['container_name']}$")
            report = {"check": "actual_docker_crash_recovery", "demo": True,
                      "child_exit_code": result.returncode, "container_running_after_crash": True,
                      "container_removed_after_restart": True, "task_state": "interrupted", "image_id": args.image}
            if args.output:
                args.output.write_text(canonical(report) + "\n")
            print(canonical(report))
        finally:
            if manager:
                manager.close()
            db = AssessmentStore(Store(root))
            db.recover_resources()
            if db.pending_resources():
                raise RuntimeError("Recovery check left pending resources; inspect the Docker daemon")


if __name__ == "__main__":
    main()
