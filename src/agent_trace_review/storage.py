import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .models import Evaluation, Run
from .util import digest


class Store:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.artifacts = self.root / "artifacts"
        self.artifacts.mkdir(exist_ok=True, mode=0o700)
        self.db = self.root / "review.sqlite3"
        with self.connect() as conn:
            conn.executescript("""
              PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, imported_at TEXT, body TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS evaluations (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), created_at TEXT, body TEXT NOT NULL);
              CREATE INDEX IF NOT EXISTS evaluations_run ON evaluations(run_id, created_at);
              CREATE TABLE IF NOT EXISTS artifacts (id TEXT PRIMARY KEY, media_type TEXT NOT NULL);
              PRAGMA user_version=1;
            """)
        os.chmod(self.db, 0o600)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db, timeout=30)
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def put_artifact(self, content: bytes, media_type="application/json") -> str:
        key = digest(content)
        target = self.artifacts / key
        if not target.exists():
            fd, pending = tempfile.mkstemp(prefix=".pending-", dir=self.artifacts)
            try:
                with os.fdopen(fd, "wb") as output:
                    output.write(content)
                os.replace(pending, target)
            finally:
                if os.path.exists(pending):
                    os.unlink(pending)
        with self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO artifacts VALUES (?, ?)", (key, media_type))
        return key

    def get_artifact(self, key: str) -> tuple[bytes, str]:
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise KeyError(key)
        with self.connect() as conn:
            row = conn.execute("SELECT media_type FROM artifacts WHERE id=?", (key,)).fetchone()
        if not row:
            raise KeyError(key)
        return (self.artifacts / key).read_bytes(), row[0]

    def save_run(self, run: Run) -> bool:
        with self.connect() as conn:
            result = conn.execute(
                "INSERT OR IGNORE INTO runs VALUES (?, ?, ?)",
                (run.id, run.imported_at, run.model_dump_json()),
            )
            return result.rowcount == 1

    def get_run(self, run_id: str) -> Run:
        with self.connect() as conn:
            row = conn.execute("SELECT body FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise KeyError(run_id)
        return Run.model_validate_json(row[0])

    def save_evaluation(self, evaluation: Evaluation):
        with self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO evaluations VALUES (?, ?, ?, ?)",
                (evaluation.id, evaluation.run_id, evaluation.created_at, evaluation.model_dump_json()),
            )

    def get_evaluation(self, run_id: str, revision: str | None = None) -> Evaluation:
        with self.connect() as conn:
            if revision:
                row = conn.execute(
                    "SELECT body FROM evaluations WHERE run_id=? AND id=?", (run_id, revision)
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT body FROM evaluations WHERE run_id=? ORDER BY created_at DESC LIMIT 1", (run_id,)
                ).fetchone()
        if not row:
            raise KeyError(run_id)
        return Evaluation.model_validate_json(row[0])

    def list_runs(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT body FROM runs WHERE EXISTS (SELECT 1 FROM evaluations WHERE run_id=runs.id) ORDER BY imported_at DESC"
            ).fetchall()
        result = []
        for row in rows:
            run = json.loads(row[0])
            evaluation = self.get_evaluation(run["id"])
            result.append(
                {
                    k: v
                    for k, v in run.items()
                    if k
                    not in {"events", "evidence", "usage", "diff", "verifications", "output", "artifacts"}
                }
                | {
                    "metrics": [m.model_dump() for m in evaluation.metrics],
                    "outcome": evaluation.outcome,
                    "finding_count": len(evaluation.findings),
                    "revision_id": evaluation.id,
                }
            )
        return result

    def revisions(self, run_id: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, created_at, json_extract(body, '$.version') FROM evaluations WHERE run_id=? ORDER BY created_at DESC",
                (run_id,),
            ).fetchall()
        return [{"id": row[0], "created_at": row[1], "version": row[2]} for row in rows]

    def bundle(self, run_id: str) -> dict:
        run = self.get_run(run_id)
        raw, _ = self.get_artifact(run.source_artifact)
        if run.source_format == "generic":
            trace = json.loads(raw)
            if run.agent_version:
                trace["agent_version"] = run.agent_version
            bundle = {
                "bundle_version": "generic/1",
                "trace": trace,
                "task": run.task.model_dump() if run.task else None,
            }
            if run.adapter_version == "generic/2":
                bundle.update(
                    bundle_version="generic/2",
                    diff=run.diff if run.diff_provided else None,
                    verifications=[v.model_dump() for v in run.verifications],
                )
            return bundle
        return {
            "bundle_version": "1",
            "export": json.loads(raw),
            "task": run.task.model_dump() if run.task else None,
            "diff": run.diff if run.diff_provided else None,
            "verifications": [
                v.model_dump() for v in run.verifications if v.provenance == "external_verifier"
            ],
            "agent_version": run.agent_version,
            "demo": run.demo,
            "first_message": run.first_message,
            "last_message": run.last_message,
        }
