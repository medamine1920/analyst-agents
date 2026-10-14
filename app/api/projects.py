"""Projects: a user's folders of questions, each remembering the data source it is about.

Kept in the console database. A project is private to its owner; removing it keeps its questions in
the history, just no longer filed under it.
"""

from __future__ import annotations

import secrets
import time
from pathlib import Path

from app.db import connect

SCHEMA = """
create table if not exists projects (
    id text primary key, owner text not null, name text not null, source text not null default 'sample',
    created_at real not null, updated_at real not null);
create index if not exists projects_by_owner on projects (owner, updated_at);
"""


class ProjectStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        with self._db() as db:
            db.executescript(SCHEMA)

    def _db(self):
        return connect(self.path)

    @staticmethod
    def _clean(name: str) -> str:
        name = " ".join(name.split())[:60]
        if not name:
            raise ValueError("Give the project a name.")
        return name

    def create(self, owner: str, name: str, source: str = "sample") -> dict:
        project_id, now = secrets.token_hex(5), time.time()
        with self._db() as db:
            db.execute("insert into projects values (?, ?, ?, ?, ?, ?)",
                       (project_id, owner, self._clean(name), source, now, now))
        return self.get(owner, project_id)

    def list(self, owner: str) -> list[dict]:
        """Most recently used first, with how many questions each holds."""
        with self._db() as db:
            rows = db.execute(
                "select p.*, (select count(*) from questions q where q.project = p.id) as questions "
                "from projects p where p.owner = ? order by p.updated_at desc", (owner,))
            return [dict(row) for row in rows]

    def get(self, owner: str, project_id: str) -> dict:
        with self._db() as db:
            row = db.execute("select * from projects where id = ? and owner = ?", (project_id, owner)).fetchone()
        if not row:
            raise KeyError(project_id)
        return dict(row)

    def update(self, owner: str, project_id: str, name: str | None = None, source: str | None = None) -> dict:
        self.get(owner, project_id)
        with self._db() as db:
            if name is not None:
                db.execute("update projects set name = ? where id = ?", (self._clean(name), project_id))
            if source is not None:
                db.execute("update projects set source = ? where id = ?", (source, project_id))
            db.execute("update projects set updated_at = ? where id = ?", (time.time(), project_id))
        return self.get(owner, project_id)

    def touch(self, project_id: str) -> None:
        with self._db() as db:
            db.execute("update projects set updated_at = ? where id = ?", (time.time(), project_id))

    def remove(self, owner: str, project_id: str) -> None:
        self.get(owner, project_id)
        with self._db() as db:
            db.execute("update questions set project = null where project = ?", (project_id,))
            db.execute("delete from projects where id = ?", (project_id,))
