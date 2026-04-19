from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import structlog

log = structlog.get_logger(__name__)

_MIGRATION_FILENAME = re.compile(r"^\d{3}_[a-z0-9_\-]+\.sql$")
_PRAGMAS = (
    "PRAGMA journal_mode=WAL;",
    "PRAGMA synchronous=NORMAL;",
    "PRAGMA busy_timeout=5000;",
    "PRAGMA foreign_keys=ON;",
)


class JobStore:
    """Owns the aiosqlite connection + the write lock.

    One connection for the process lifetime. WAL lets reads be lock-free; writes
    go through `write()` which holds an asyncio.Lock — Cycle 1 is conservative
    and can split reader/writer connections in Cycle 4 if contention shows up.
    """

    def __init__(self, database_path: str, migrations_dir: str | Path = "migrations") -> None:
        self._database_path = database_path
        self._migrations_dir = Path(migrations_dir)
        self._conn: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    async def connect(self) -> None:
        Path(self._database_path).parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self._database_path)
        for pragma in _PRAGMAS:
            await conn.execute(pragma)
        await conn.commit()
        applied = await apply_migrations(conn, self._migrations_dir)
        self._conn = conn
        if applied:
            log.info("jobstore.migrations.applied", files=applied)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @asynccontextmanager
    async def write(self) -> AsyncIterator[aiosqlite.Connection]:
        if self._conn is None:
            raise RuntimeError("JobStore.connect() must be called before write()")
        async with self._write_lock:
            try:
                yield self._conn
                await self._conn.commit()
            except BaseException:
                await self._conn.rollback()
                raise

    async def read(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("JobStore.connect() must be called before read()")
        return self._conn

    async def healthcheck(self) -> bool:
        if self._conn is None:
            return False
        try:
            cursor = await self._conn.execute("SELECT 1")
            row = await cursor.fetchone()
            return row is not None and row[0] == 1
        except aiosqlite.Error:
            return False


async def apply_migrations(conn: aiosqlite.Connection, migrations_dir: Path) -> list[str]:
    """Scan `migrations_dir` for `NNN_<name>.sql`, apply any not yet in schema_version.

    Returns the list of filenames applied this call (empty if up-to-date).
    """
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "filename TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    await conn.commit()

    # Migration scan runs once at startup against a local directory of <20 files.
    # Sync pathlib is fine here — no blocking network IO and no hot path.
    if not migrations_dir.exists():  # noqa: ASYNC240
        return []

    files = sorted(p for p in migrations_dir.iterdir() if p.suffix == ".sql")  # noqa: ASYNC240
    for f in files:
        if not _MIGRATION_FILENAME.match(f.name):
            raise RuntimeError(
                f"Migration filename {f.name!r} does not match NNN_<name>.sql pattern"
            )

    # strictly-ascending numeric prefix — filename sort already gives ascending order,
    # so the only way this fails is duplicate prefixes (two files sharing "001_", etc.).
    prefixes = [int(f.name[:3]) for f in files]
    if len(prefixes) != len(set(prefixes)):
        raise RuntimeError(f"Migration numeric prefixes must be unique: {prefixes}")

    cursor = await conn.execute("SELECT filename FROM schema_version")
    applied_already = {row[0] for row in await cursor.fetchall()}

    applied_now: list[str] = []
    for f in files:
        if f.name in applied_already:
            continue
        sql = f.read_text(encoding="utf-8")
        try:
            await conn.executescript(sql)
            await conn.execute(
                "INSERT INTO schema_version(filename, applied_at) VALUES (?, ?)",
                (f.name, datetime.now(UTC).isoformat()),
            )
            await conn.commit()
        except aiosqlite.Error:
            await conn.rollback()
            raise
        applied_now.append(f.name)

    return applied_now
