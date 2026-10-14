"""Which data sources exist, who owns them, where their files are, and their connection secrets.

Kept in the console database (a volume on the server) next to accounts; each source's files live in
SOURCES_DIR/<id>/ (default checkpoints/sources). A source is private to its owner. Passwords for
live databases are encrypted with a key from SOURCES_SECRET, or a key file created on first use in
SOURCES_DIR (readable by the console only), and are decrypted only to hand them to the processes that
build and query the source.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from cryptography.fernet import Fernet

from app.api.security import DEFAULT_DB

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE = "sample"  # the governed sample shop, visible to everyone
MAX_UPLOAD_MB = float(os.environ.get("SOURCE_MAX_MB", "50"))
CSV_SUFFIXES = (".csv", ".tsv", ".txt")
FILE_TYPES = {".sqlite": "sqlite", ".sqlite3": "sqlite", ".db": "sqlite", ".duckdb": "duckdb"}
LIVE_KINDS = {"postgres": 5432, "mysql": 3306}

SCHEMA = """
create table if not exists sources (
    id text primary key, owner text not null, name text not null, kind text not null,
    status text not null, error text, details text, secret text, created_at real not null, built_at real);
create index if not exists sources_by_owner on sources (owner);
"""


def sources_dir() -> Path:
    return Path(os.environ.get("SOURCES_DIR") or REPO_ROOT / "checkpoints" / "sources")


def check_host(host: str) -> None:
    """Refuse addresses inside the server's own network, so a source cannot reach internal services.

    ALLOW_PRIVATE_SOURCES=1 lifts this, for a database on the same private network (or for testing).
    """
    if os.environ.get("ALLOW_PRIVATE_SOURCES") == "1":
        return
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except socket.gaierror as exc:
        raise ValueError(f"The address '{host}' could not be found.") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError(f"'{host}' is inside a private network, which this console does not connect to.")


PLAIN_VALUE = re.compile(r"^[^\s'\"=\\]+$")
# The database drivers read the password from these, so it never has to be written into a connection string.
PASSWORD_VARIABLES = {"postgres": "PGPASSWORD", "mysql": "MYSQL_PWD"}


def connection_string(kind: str, host: str, port: int, database: str, user: str) -> str:
    """'host=... port=... dbname=... user=...' without the password (passed separately, see environment()).

    DuckDB's MySQL reader accepts no quoting at all, so values with spaces or quotes are refused rather
    than half-escaped.
    """
    keys = {"postgres": "dbname", "mysql": "database"}
    parts = {"host": host, "port": port, keys[kind]: database, "user": user}
    for key, value in parts.items():
        if not PLAIN_VALUE.match(str(value)):
            raise ValueError(f"The {key} cannot contain spaces, quotes, '=' or backslashes.")
    if kind == "postgres":
        parts.update({"sslmode": "prefer", "connect_timeout": 10})
    return " ".join(f"{key}={value}" for key, value in parts.items())


class SourceStore:
    def __init__(self, path: Path | None = None, folder: Path | None = None) -> None:
        self.path = Path(path or os.environ.get("CONSOLE_DB") or DEFAULT_DB)
        self.folder = Path(folder or sources_dir())
        self.folder.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(SCHEMA)
        self._fernet = Fernet(self._key())

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _key(self) -> bytes:
        configured = os.environ.get("SOURCES_SECRET")
        if configured:  # any text: stretched into a key, so an operator can use a plain passphrase
            return base64.urlsafe_b64encode(hashlib.sha256(configured.encode("utf-8")).digest())
        key_file = self.folder / ".key"
        if not key_file.exists():
            key_file.write_bytes(Fernet.generate_key())
            key_file.chmod(0o600)
        return key_file.read_bytes().strip()

    # ---- creating ---------------------------------------------------------------------------

    def _new(self, owner: str, name: str, kind: str, spec: dict, secret: str | None = None) -> dict:
        source_id = secrets.token_hex(6)
        folder = self.folder / source_id
        (folder / "files").mkdir(parents=True)
        (folder / "source.json").write_text(json.dumps({"id": source_id, "name": name, "kind": kind, **spec}),
                                            encoding="utf-8")
        with self._db() as db:
            db.execute("insert into sources (id, owner, name, kind, status, secret, created_at) values (?, ?, ?, ?, ?, ?, ?)",
                       (source_id, owner, name.strip()[:80] or "Untitled source", kind, "building",
                        self._fernet.encrypt(secret.encode()).decode() if secret else None, time.time()))
        return self.get(owner, source_id)

    @staticmethod
    def _check_size(files: list[tuple[str, bytes]]) -> None:
        total = sum(len(content) for _, content in files) / 1e6
        if total > MAX_UPLOAD_MB:
            raise ValueError(f"The files are {total:.0f} MB together; the limit is {MAX_UPLOAD_MB:.0f} MB.")

    @staticmethod
    def _safe_name(filename: str) -> str:
        name = re.sub(r"[^A-Za-z0-9 ._-]+", "_", Path(filename).name).strip(" .") or "file"
        return name[:100]

    def add_csv(self, owner: str, name: str, files: list[tuple[str, bytes]]) -> dict:
        if not files:
            raise ValueError("Choose at least one CSV file.")
        bad = [f for f, _ in files if not f.lower().endswith(CSV_SUFFIXES)]
        if bad:
            raise ValueError(f"Only CSV files can be uploaded here: {', '.join(bad)}.")
        self._check_size(files)
        names = []
        for filename, _ in files:
            safe = self._safe_name(filename)
            while safe in names:
                safe = f"copy_{safe}"
            names.append(safe)
        source = self._new(owner, name, "csv", {"files": names})
        for safe, (_, content) in zip(names, files, strict=True):
            (self.folder / source["id"] / "files" / safe).write_bytes(content)
        return source

    def add_file(self, owner: str, name: str, filename: str, content: bytes) -> dict:
        suffix = Path(filename).suffix.lower()
        if suffix not in FILE_TYPES:
            raise ValueError("Upload a SQLite (.sqlite, .db) or DuckDB (.duckdb) database file.")
        self._check_size([(filename, content)])
        safe = self._safe_name(filename)
        source = self._new(owner, name, "file", {"attach": {"type": FILE_TYPES[suffix], "path": ""}})
        folder = self.folder / source["id"]
        (folder / "files" / safe).write_bytes(content)
        spec = json.loads((folder / "source.json").read_text(encoding="utf-8"))
        spec["attach"]["path"] = (folder / "files" / safe).as_posix()
        (folder / "source.json").write_text(json.dumps(spec), encoding="utf-8")
        return source

    def add_connection(self, owner: str, name: str, kind: str, host: str, port: int | None, database: str,
                       user: str, password: str, schema: str | None = None, tables: list[str] | None = None) -> dict:
        if kind not in LIVE_KINDS:
            raise ValueError(f"Choose one of: {', '.join(LIVE_KINDS)}.")
        if not (host and database and user):
            raise ValueError("Fill in the address, the database name and the user.")
        check_host(host)
        dsn = connection_string(kind, host.strip(), port or LIVE_KINDS[kind], database.strip(), user.strip())
        secret = json.dumps({"dsn": dsn, "password": password})
        wanted = [t.strip() for t in tables or [] if t.strip()]
        attach = {"type": kind, "secret": True, "schema": (schema or "").strip() or None, "tables": wanted or None}
        return self._new(owner, name, kind, {"attach": attach, "host": host, "database": database}, secret)

    # ---- reading ----------------------------------------------------------------------------

    @staticmethod
    def _public(row: sqlite3.Row) -> dict:
        details = json.loads(row["details"]) if row["details"] else {}
        details.pop("time_dimensions", None)  # an internal detail
        return {"id": row["id"], "name": row["name"], "kind": row["kind"], "status": row["status"],
                "error": row["error"], "created_at": row["created_at"], "built_at": row["built_at"], **details}

    def list(self, owner: str) -> list[dict]:
        with self._db() as db:
            rows = db.execute("select * from sources where owner = ? order by created_at desc", (owner,))
            return [self._public(row) for row in rows]

    def get(self, owner: str, source_id: str) -> dict:
        """The source, if this user owns it; otherwise KeyError (the same for 'not yours' and 'missing')."""
        with self._db() as db:
            row = db.execute("select * from sources where id = ? and owner = ?", (source_id, owner)).fetchone()
        if not row:
            raise KeyError(source_id)
        return self._public(row)

    def environment(self, source_id: str) -> dict[str, str]:
        """What a process needs to build or query this source, including its decrypted connection string."""
        folder = self.folder / source_id
        env = {"DBT_PROJECT_DIR": str(folder), "DUCKDB_PATH": str(folder / "source.duckdb")}
        with self._db() as db:
            row = db.execute("select kind, secret, details from sources where id = ?", (source_id,)).fetchone()
        if row and row["secret"]:
            secret = self._fernet.decrypt(row["secret"].encode()).decode()
            try:
                parts = json.loads(secret)
            except json.JSONDecodeError:  # stored before passwords were kept apart: the whole string
                parts = {"dsn": secret, "password": ""}
            env["SOURCE_ATTACH"] = parts["dsn"]
            if parts.get("password") and row["kind"] in PASSWORD_VARIABLES:
                env[PASSWORD_VARIABLES[row["kind"]]] = parts["password"]
        details = json.loads(row["details"]) if row and row["details"] else {}
        if details.get("time_dimensions"):  # the date column of each table, for the source's date range
            env["DATA_TIME_DIMENSIONS"] = ",".join(details["time_dimensions"])
        return env

    def mark(self, source_id: str, status: str, error: str | None = None, details: dict | None = None) -> None:
        with self._db() as db:
            db.execute("update sources set status = ?, error = ?, details = coalesce(?, details), built_at = ? where id = ?",
                       (status, error, json.dumps(details) if details is not None else None,
                        time.time() if status == "ready" else None, source_id))

    def remove(self, owner: str, source_id: str) -> None:
        self.get(owner, source_id)  # ownership check
        with self._db() as db:
            db.execute("delete from sources where id = ?", (source_id,))
        shutil.rmtree(self.folder / source_id, ignore_errors=True)
