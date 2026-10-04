"""Durable repository pipeline state, separate from individual assessment results."""

import json
import uuid

from .util import canonical, now


class RepositoryJobStore:
    def __init__(self, store):
        self.store = store
        with store.connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS repository_jobs (
                id TEXT PRIMARY KEY, state TEXT NOT NULL, cancel_requested INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, body TEXT NOT NULL)""")

    def create(self, request):
        job_id, timestamp = "repository_job_" + uuid.uuid4().hex, now()
        body = {"request": request.model_dump(), "stage": "queued", "commit": None,
                "image_id": None, "assessment_id": None, "error": None, "log_artifacts": [],
                "recipe": None, "source_binding": None, "finished_at": None, "cleanup": "pending"}
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT COUNT(*) FROM repository_jobs WHERE state IN ('queued','running')").fetchone()[0] >= 8:
                raise ValueError("仓库任务队列已满（最多 8 项）。")
            conn.execute("INSERT INTO repository_jobs VALUES (?, 'queued', 0, ?, ?, ?)",
                         (job_id, timestamp, timestamp, canonical(body)))
        return self.get(job_id)

    def get(self, job_id):
        with self.store.connect() as conn:
            row = conn.execute("SELECT state, cancel_requested, created_at, updated_at, body FROM repository_jobs WHERE id=?",
                               (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return {**json.loads(row[4]), "id": job_id, "state": row[0], "cancel_requested": bool(row[1]),
                "created_at": row[2], "updated_at": row[3]}

    def patch(self, job_id, *, state=None, **values):
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT body FROM repository_jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            body = {**json.loads(row[0]), **values}
            conn.execute("UPDATE repository_jobs SET body=?, state=COALESCE(?,state), updated_at=? WHERE id=?",
                         (canonical(body), state, now(), job_id))
        return self.get(job_id)

    def claim(self, job_id):
        with self.store.connect() as conn:
            return conn.execute("UPDATE repository_jobs SET state='running', updated_at=? "
                                "WHERE id=? AND state='queued' AND cancel_requested=0", (now(), job_id)).rowcount == 1

    def cancel(self, job_id):
        self.get(job_id)
        with self.store.connect() as conn:
            conn.execute("UPDATE repository_jobs SET cancel_requested=1, updated_at=?, "
                         "state=CASE WHEN state='queued' THEN 'cancelled' ELSE state END "
                         "WHERE id=? AND state IN ('queued','running')", (now(), job_id))
        return self.get(job_id)

    def list(self, limit=50):
        with self.store.connect() as conn:
            ids = [r[0] for r in conn.execute("SELECT id FROM repository_jobs ORDER BY created_at DESC LIMIT ?", (limit,))]
        return [self.get(job_id) for job_id in ids]

    def active(self):
        with self.store.connect() as conn:
            return [r[0] for r in conn.execute("SELECT id FROM repository_jobs WHERE state IN ('queued','running')")]
