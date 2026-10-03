"""Validate a built judge image with a separate target container, auth, persistence and artifacts."""

import argparse
import json
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import httpx

from agent_trace_review.util import canonical


def docker(*args):
    result = subprocess.run(["docker", *args], capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError(f"Docker validation command failed: {args[0]}")
    return result.stdout.decode().strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--judge-image", required=True)
    parser.add_argument("--target-image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    uid = "agent-inspect-validation-" + uuid.uuid4().hex
    network, volume, target, judge = (uid + suffix for suffix in ("-net", "-data", "-target", "-judge"))
    token = uuid.uuid4().hex
    created_containers = []
    created_network = created_volume = False
    try:
        docker("network", "create", network)
        created_network = True
        docker("volume", "create", volume)
        created_volume = True
        docker(
            "run",
            "-d",
            "--pull=never",
            "--name",
            target,
            "--network",
            network,
            "--network-alias",
            "control-agent",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--memory=256m",
            "--cpus=1",
            "--pids-limit=128",
            args.target_image,
        )
        created_containers.append(target)
        with tempfile.TemporaryDirectory(prefix="judge-deployment-validation-") as temporary:
            repository = Path(temporary) / "repository"
            repository.mkdir()
            root = Path(__file__).resolve().parents[1]
            (repository / "README.md").write_text("# Control\nSupports persistent memory.\n")
            (repository / "mock_agent.py").write_bytes(
                (root / "examples/assessment/mock_agent.py").read_bytes()
            )
            for command in (
                ["init"],
                ["add", "."],
                [
                    "-c",
                    "user.name=Service validation",
                    "-c",
                    "user.email=control@example.invalid",
                    "commit",
                    "-m",
                    "Fixed fixture",
                ],
            ):
                subprocess.run(["git", "-C", str(repository), *command], check=True, capture_output=True)
            commit = subprocess.run(
                ["git", "-C", str(repository), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            registry = Path(temporary) / "targets.json"
            registry.write_text(
                canonical(
                    [
                        {
                            "id": "control",
                            "endpoint": "http://control-agent:9081",
                            "task_path": "/correct",
                            "health_path": "/health",
                            "repository": "/repos/control",
                            "ref": commit,
                            "demo": True,
                        }
                    ]
                )
            )
            created_containers.append(judge)
            docker(
                "run",
                "-d",
                "--pull=never",
                "--name",
                judge,
                "--network",
                network,
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--cpus=2",
                "--memory=1g",
                "--pids-limit=128",
                "--publish",
                "127.0.0.1::8765",
                "--mount",
                f"type=bind,source={registry},target=/config/targets.json,readonly",
                "--mount",
                f"type=volume,source={volume},target=/data",
                "--mount",
                f"type=bind,source={repository},target=/repos/control,readonly",
                "--env",
                "AGENT_REVIEW_SERVICE_TOKEN=" + token,
                args.judge_image,
            )
            state = json.loads(docker("inspect", judge))[0]
            port = state["NetworkSettings"]["Ports"]["8765/tcp"][0]["HostPort"]
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=5) as client:
                ready = False
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    try:
                        ready = client.get("/api/health").status_code == 200
                        if ready:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.2)
                assert ready, "Judge did not become ready"
                assert client.get("/").status_code == 200
                assert client.get("/api/targets").status_code == 401
                headers = {"Authorization": "Bearer " + token, "X-Review-Request": "1"}
                assert client.get("/api/targets", headers=headers).json()[0]["id"] == "control"
                profile = client.post("/api/targets/control/repository-profile", headers=headers)
                assert profile.status_code == 200, profile.text
                assert profile.json()["commit"] == commit
                suite = json.loads((root / "examples/assessment/suite.json").read_text())
                created = client.post(
                    "/api/assessments", headers=headers, json={"target_id": "control", "suite": suite}
                )
                assert created.status_code == 202, created.status_code
                job_id = created.json()["id"]
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    result = client.get(f"/api/assessments/{job_id}", headers=headers).json()
                    if result["state"] not in {"queued", "running"}:
                        break
                    time.sleep(0.2)
                assert result["state"] == "completed", result.get("error")
                assert result["completed"] == 16 and all(r["outcome"] == "pass" for r in result["results"])
                bundle = client.get(f"/api/assessments/{job_id}/export?format=bundle", headers=headers).json()
                assert len(bundle["runs"]) == 16
                assert client.get("/api/artifacts/" + result["suite_artifact"]).status_code == 401
                report = client.get(f"/api/assessments/{job_id}/export", headers=headers)
                assert report.status_code == 200 and "16/16" in report.text
                docker("restart", judge)
                # Ephemeral host ports can change on restart; resolve the new publication.
                restarted = json.loads(docker("inspect", judge))[0]
                restarted_port = restarted["NetworkSettings"]["Ports"]["8765/tcp"][0]["HostPort"]
                client.base_url = f"http://127.0.0.1:{restarted_port}"
                ready = False
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    try:
                        ready = client.get("/api/health").status_code == 200
                        if ready:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.2)
                assert ready
                restored = client.get(f"/api/assessments/{job_id}", headers=headers).json()
                assert restored["state"] == "completed" and restored["completed"] == 16
                assert (
                    client.get("/api/artifacts/" + result["suite_artifact"], headers=headers).status_code
                    == 200
                )
                platform = docker(
                    "image", "inspect", args.judge_image, "--format", "{{.Os}}/{{.Architecture}}"
                )
                args.output.write_text(
                    canonical(
                        {
                            "judge_image_id": state["Image"],
                            "target_image": args.target_image,
                            "platform": platform,
                            "authenticated": True,
                            "restart_preserved_results": True,
                            "repository_profile_verified": True,
                            "persisted_data_volume": True,
                            "result": result,
                        }
                    )
                )
                print(
                    f"Container judge + separate target: {job_id} 16/16 passed; authentication and exports verified"
                )
    finally:
        for name in reversed(created_containers):
            docker("rm", "-f", name)
        if created_volume:
            docker("volume", "rm", volume)
        if created_network:
            docker("network", "rm", network)


if __name__ == "__main__":
    main()
