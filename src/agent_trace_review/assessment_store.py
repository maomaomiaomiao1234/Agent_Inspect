"""Durable assessment jobs; execution is deliberately single-service-process."""

import json
import uuid

from .storage import Store
from .util import canonical, digest, now


class AssessmentStore:
    def __init__(self, store: Store):
        self.store = store
        with store.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS repository_profiles (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS assessment_jobs (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    state TEXT NOT NULL, cancel_requested INTEGER NOT NULL DEFAULT 0, body TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS assessment_jobs_created ON assessment_jobs(created_at);
                CREATE TABLE IF NOT EXISTS deployment_resources (
                    name TEXT PRIMARY KEY, job_id TEXT NOT NULL, env_file TEXT,
                    state TEXT NOT NULL, error TEXT, updated_at TEXT NOT NULL);
            """)

    def save_repository(self, profile):
        with self.store.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO repository_profiles VALUES (?, ?, ?)",
                (profile["id"], profile["created_at"], canonical(profile)),
            )
        return self.repository(profile["id"])

    def repository(self, repository_id):
        with self.store.connect() as conn:
            row = conn.execute("SELECT body FROM repository_profiles WHERE id=?", (repository_id,)).fetchone()
        if row is None:
            raise KeyError(repository_id)
        return json.loads(row[0])

    def create_job(self, body, *, queue_limit=32):
        job_id = "assessment_" + uuid.uuid4().hex
        timestamp = now()
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute(
                "SELECT COUNT(*) FROM assessment_jobs WHERE state IN ('queued','running')"
            ).fetchone()[0]
            if active >= queue_limit:
                raise ValueError("评测队列已满，请等现有任务完成。")
            conn.execute(
                "INSERT INTO assessment_jobs VALUES (?, ?, ?, 'queued', 0, ?)",
                (job_id, timestamp, timestamp, canonical(body)),
            )
        return self.job(job_id)

    def job(self, job_id):
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT created_at, updated_at, state, cancel_requested, body FROM assessment_jobs WHERE id=?",
                (job_id,),
            ).fetchone()
        if not row:
            raise KeyError(job_id)
        return {
            **json.loads(row[4]),
            "id": job_id,
            "created_at": row[0],
            "updated_at": row[1],
            "state": row[2],
            "cancel_requested": bool(row[3]),
        }

    def update(self, job_id, body, state=None):
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE assessment_jobs SET body=?, updated_at=?, state=COALESCE(?,state) WHERE id=?",
                (canonical(body), now(), state, job_id),
            )

    def cancel(self, job_id):
        self.job(job_id)
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE assessment_jobs SET cancel_requested=1, updated_at=?, "
                "state=CASE WHEN state='queued' THEN 'cancelled' ELSE state END "
                "WHERE id=? AND state IN ('queued','running')",
                (now(), job_id),
            )
        return self.job(job_id)

    def claim(self, job_id):
        with self.store.connect() as conn:
            return (
                conn.execute(
                    "UPDATE assessment_jobs SET state='running', updated_at=? "
                    "WHERE id=? AND state='queued' AND cancel_requested=0",
                    (now(), job_id),
                ).rowcount
                == 1
            )

    def list_jobs(self, limit=50, offset=0, *, summary=False):
        with self.store.connect() as conn:
            rows = conn.execute(
                "SELECT id FROM assessment_jobs ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset)
            ).fetchall()
        fields = {
            "id",
            "state",
            "target_id",
            "suite_id",
            "planned",
            "completed",
            "commit",
            "repository_id",
            "error",
            "demo",
            "curves",
            "created_at",
            "updated_at",
            "cancel_requested",
        }
        items = []
        for row in rows:
            job = self.job(row[0])
            items.append({k: v for k, v in job.items() if k in fields} if summary else job)
        return items

    def active_jobs(self):
        with self.store.connect() as conn:
            return [
                row[0]
                for row in conn.execute("SELECT id FROM assessment_jobs WHERE state IN ('queued','running')")
            ]

    def recover_interrupted(self):
        # Never replays tasks/bills silently after a restart. Operators can submit a fresh assessment.
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE assessment_jobs SET state='interrupted', updated_at=? "
                "WHERE state IN ('queued','running')",
                (now(),),
            )

    @property
    def resource_owner(self):
        return digest(str(self.store.root))[:24]

    def reserve_container(self, job_id):
        name = "agent-inspect-target-" + digest([self.resource_owner, job_id])[:32]
        with self.store.connect() as conn:
            conn.execute(
                "INSERT INTO deployment_resources VALUES (?, ?, NULL, 'reserved', NULL, ?)",
                (name, job_id, now()),
            )
        return name

    def resource_env(self, name, path):
        with self.store.connect() as conn:
            conn.execute("UPDATE deployment_resources SET env_file=?, updated_at=? WHERE name=?", (path, now(), name))

    def cleanup_resource(self, name):
        from .target_client import TargetError, remove_container

        with self.store.connect() as conn:
            row = conn.execute("SELECT env_file FROM deployment_resources WHERE name=?", (name,)).fetchone()
        if row is None:
            raise ValueError("Unknown deployment resource")
        error = None
        try:
            remove_container(name, self.resource_owner)
        except TargetError as exc:
            error = exc.code
        if row[0]:
            from pathlib import Path

            try:
                Path(row[0]).unlink(missing_ok=True)
            except OSError:
                error = error or "deployment_environment_cleanup_failed"
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE deployment_resources SET state=?, error=?, updated_at=? WHERE name=?",
                ("cleanup_failed" if error else "removed", error, now(), name),
            )
        if error:
            raise TargetError(error)

    def pending_resources(self):
        with self.store.connect() as conn:
            return [r[0] for r in conn.execute("SELECT name FROM deployment_resources WHERE state!='removed'")]

    def recover_resources(self):
        from .target_client import TargetError

        for name in self.pending_resources():
            try:
                self.cleanup_resource(name)
            except TargetError:
                # Keep failed cleanup durable and retry on the next explicit recovery/startup.
                pass
