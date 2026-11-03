"""Banco de dados compartilhado por todos os módulos.

Funciona com dois motores:
- Postgres (Supabase), quando DATABASE_URL começa com postgres:// ou postgresql://
  As tabelas ficam no schema "backline" (separado das tabelas do Painel).
- SQLite (arquivo local), caso contrário. Usado nos testes e para rodar localmente.

O SQL dos módulos é escrito uma vez, com "?" como marcador; aqui ele é
convertido para "$1, $2..." quando o motor é Postgres.
Todos os horários são Unix timestamp (segundos, UTC).
"""
from __future__ import annotations

import re
import time
from typing import Any, Iterable, Mapping

SCHEMA_COMMON = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS members (
    user_id     BIGINT PRIMARY KEY,
    locale      TEXT DEFAULT 'en',
    joined_at   BIGINT,
    verify_code TEXT,
    verified_at BIGINT,
    left_at     BIGINT,
    manual_level INTEGER,
    is_test     INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS handles (
    user_id     BIGINT NOT NULL,
    platform    TEXT NOT NULL,
    handle      TEXT NOT NULL,
    external_id TEXT,
    verified    INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, platform)
);
CREATE INDEX IF NOT EXISTS idx_handles_lookup ON handles(platform, handle);
CREATE TABLE IF NOT EXISTS member_genres (
    user_id BIGINT NOT NULL,
    genre   TEXT NOT NULL,
    PRIMARY KEY (user_id, genre)
);
CREATE TABLE IF NOT EXISTS posts (
    id               {ID},
    user_id          BIGINT NOT NULL,
    platform         TEXT NOT NULL,
    url              TEXT NOT NULL,
    external_id      TEXT NOT NULL,
    created_at       BIGINT NOT NULL,
    channel_id       BIGINT,
    message_id       BIGINT,
    checkpoints_done INTEGER DEFAULT 0,
    last_collected   BIGINT,
    removed          INTEGER DEFAULT 0,
    UNIQUE (platform, external_id)
);
CREATE TABLE IF NOT EXISTS comments (
    platform           TEXT NOT NULL,
    comment_id         TEXT NOT NULL,
    post_id            BIGINT NOT NULL,
    author_handle      TEXT,
    author_external_id TEXT,
    created_at         BIGINT,
    collected_at       BIGINT,
    PRIMARY KEY (platform, comment_id)
);
CREATE INDEX IF NOT EXISTS idx_comments_post ON comments(post_id);
CREATE TABLE IF NOT EXISTS scores (
    user_id    BIGINT PRIMARY KEY,
    given      INTEGER DEFAULT 0,
    received   INTEGER DEFAULT 0,
    ratio      REAL DEFAULT 0,
    level      INTEGER DEFAULT 1,
    updated_at BIGINT
);
CREATE TABLE IF NOT EXISTS invites (
    code       TEXT PRIMARY KEY,
    owner_id   BIGINT NOT NULL,
    uses       INTEGER DEFAULT 0,
    created_at BIGINT
);
CREATE TABLE IF NOT EXISTS recruits (
    user_id    BIGINT PRIMARY KEY,
    inviter_id BIGINT NOT NULL,
    code       TEXT,
    joined_at  BIGINT,
    left_at    BIGINT
);
-- Mutual Follow (opcional)
CREATE TABLE IF NOT EXISTS mutual_members (
    user_id   BIGINT PRIMARY KEY,
    active    INTEGER DEFAULT 1,
    joined_at BIGINT
);
CREATE TABLE IF NOT EXISTS follows (
    follower_id BIGINT NOT NULL,
    followee_id BIGINT NOT NULL,
    platform    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',   -- pending | verified | failed | unfollowed
    claimed_at  BIGINT,
    verified_at BIGINT,
    last_check  BIGINT,
    attempts    INTEGER DEFAULT 0,
    notified    INTEGER DEFAULT 0,
    PRIMARY KEY (follower_id, followee_id, platform)
);
CREATE TABLE IF NOT EXISTS collection_log (
    id      {ID},
    post_id BIGINT,
    at      BIGINT,
    ok      INTEGER,
    found   INTEGER,
    error   TEXT
);
-- Discover
CREATE TABLE IF NOT EXISTS discover_seen (
    user_id BIGINT NOT NULL,
    post_id BIGINT NOT NULL,
    seen_at BIGINT,
    PRIMARY KEY (user_id, post_id)
);
CREATE TABLE IF NOT EXISTS discover_batch (
    user_id    BIGINT PRIMARY KEY,
    post_ids   TEXT NOT NULL,
    created_at BIGINT
);
CREATE TABLE IF NOT EXISTS discover_clicks (
    user_id    BIGINT NOT NULL,
    post_id    BIGINT NOT NULL,
    clicked_at BIGINT,
    PRIMARY KEY (user_id, post_id)
);
CREATE TABLE IF NOT EXISTS saves (
    user_id  BIGINT NOT NULL,
    post_id  BIGINT NOT NULL,
    saved_at BIGINT,
    PRIMARY KEY (user_id, post_id)
);
CREATE TABLE IF NOT EXISTS ratings (
    rater_id BIGINT NOT NULL,
    post_id  BIGINT NOT NULL,
    stars    INTEGER NOT NULL,
    rated_at BIGINT,
    PRIMARY KEY (rater_id, post_id)
);
CREATE TABLE IF NOT EXISTS rating_tracks (
    user_id     BIGINT NOT NULL,
    slot        INTEGER NOT NULL,
    platform    TEXT NOT NULL,
    url         TEXT NOT NULL,
    external_id TEXT NOT NULL,
    title       TEXT,
    thumb_url   TEXT,
    cover_path  TEXT,
    created_at  BIGINT,
    PRIMARY KEY (user_id, slot)
);
CREATE TABLE IF NOT EXISTS artist_ratings (
    rater_id       BIGINT NOT NULL,
    artist_id      BIGINT NOT NULL,
    execution      INTEGER NOT NULL,
    interpretation INTEGER NOT NULL,
    arrangement    INTEGER NOT NULL,
    technical      INTEGER NOT NULL,
    feedback       TEXT,
    rated_at       BIGINT,
    notified       INTEGER DEFAULT 0,
    PRIMARY KEY (rater_id, artist_id)
);
CREATE TABLE IF NOT EXISTS boosts (
    user_id    BIGINT PRIMARY KEY,
    multiplier REAL NOT NULL DEFAULT 1,
    until      BIGINT
);
"""

SQLITE_ID = "INTEGER PRIMARY KEY AUTOINCREMENT"
PG_ID = "BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY"
PG_SCHEMA = "backline"

# Colunas adicionadas depois da primeira versão (bancos antigos recebem via ALTER TABLE)
MIGRATIONS = [
    "ALTER TABLE members ADD COLUMN manual_level INTEGER",
    "ALTER TABLE members ADD COLUMN is_test INTEGER DEFAULT 0",
    "ALTER TABLE posts ADD COLUMN title TEXT",
    "ALTER TABLE posts ADD COLUMN thumb_url TEXT",
    "ALTER TABLE posts ADD COLUMN meta_at BIGINT",
    "ALTER TABLE members ADD COLUMN demo_name TEXT",
    "ALTER TABLE posts ADD COLUMN cover_path TEXT",
    "ALTER TABLE members ADD COLUMN verify_tried INTEGER DEFAULT 0",
    "ALTER TABLE posts ADD COLUMN kind TEXT",
    "ALTER TABLE posts ADD COLUMN genres TEXT",
]


def now() -> int:
    return int(time.time())


def _to_pg(sql: str) -> str:
    counter = iter(range(1, 10_000))
    return re.sub(r"\?", lambda _: f"${next(counter)}", sql)


class Database:
    def __init__(self, url: str):
        self.url = url
        self.is_pg = url.startswith(("postgres://", "postgresql://"))
        self.conn = None   # aiosqlite
        self.pool = None   # asyncpg

    async def connect(self) -> None:
        if self.is_pg:
            import asyncpg
            admin = await asyncpg.connect(self.url, statement_cache_size=0)
            try:
                await admin.execute(f"CREATE SCHEMA IF NOT EXISTS {PG_SCHEMA}")
            finally:
                await admin.close()
            self.pool = await asyncpg.create_pool(
                self.url, min_size=1, max_size=5, statement_cache_size=0,
                server_settings={"search_path": PG_SCHEMA})
            async with self.pool.acquire() as c:
                await c.execute(SCHEMA_COMMON.replace("{ID}", PG_ID))
                for m in MIGRATIONS:
                    await c.execute(m.replace("ADD COLUMN", "ADD COLUMN IF NOT EXISTS"))
        else:
            import aiosqlite
            self.conn = await aiosqlite.connect(self.url)
            self.conn.row_factory = aiosqlite.Row
            await self.conn.execute("PRAGMA journal_mode=WAL")
            await self.conn.executescript(SCHEMA_COMMON.replace("{ID}", SQLITE_ID))
            for m in MIGRATIONS:
                try:
                    await self.conn.execute(m)
                except Exception:  # coluna já existe
                    pass
            await self.conn.commit()

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()
        if self.conn:
            await self.conn.close()

    # ---------- utilitários ----------
    async def execute(self, sql: str, params: Iterable[Any] = ()) -> None:
        if self.is_pg:
            async with self.pool.acquire() as c:
                await c.execute(_to_pg(sql), *params)
        else:
            await self.conn.execute(sql, tuple(params))
            await self.conn.commit()

    async def executemany(self, sql: str, rows: list[tuple]) -> None:
        if not rows:
            return
        if self.is_pg:
            async with self.pool.acquire() as c:
                await c.executemany(_to_pg(sql), rows)
        else:
            await self.conn.executemany(sql, rows)
            await self.conn.commit()

    async def insert_returning_id(self, sql: str, params: Iterable[Any] = ()) -> int:
        row = await self.fetchone(sql + " RETURNING id", params)
        if not self.is_pg:
            await self.conn.commit()
        return row["id"]

    async def fetchone(self, sql: str, params: Iterable[Any] = ()) -> Mapping | None:
        if self.is_pg:
            async with self.pool.acquire() as c:
                return await c.fetchrow(_to_pg(sql), *params)
        async with self.conn.execute(sql, tuple(params)) as cur:
            return await cur.fetchone()

    async def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[Mapping]:
        if self.is_pg:
            async with self.pool.acquire() as c:
                return list(await c.fetch(_to_pg(sql), *params))
        async with self.conn.execute(sql, tuple(params)) as cur:
            return list(await cur.fetchall())

    # ---------- settings ----------
    async def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = await self.fetchone("SELECT value FROM settings WHERE key=?", (key,))
        return row["value"] if row else default

    async def set_setting(self, key: str, value: Any) -> None:
        await self.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )

    async def get_int_setting(self, key: str) -> int | None:
        value = await self.get_setting(key)
        return int(value) if value and value.isdigit() else None

    # ---------- membros ----------
    async def upsert_member(self, user_id: int, locale: str) -> None:
        await self.execute(
            "INSERT INTO members(user_id, locale, joined_at) VALUES(?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET locale=excluded.locale, left_at=NULL",
            (user_id, locale, now()),
        )

    async def get_member(self, user_id: int) -> Mapping | None:
        return await self.fetchone("SELECT * FROM members WHERE user_id=?", (user_id,))

    async def handles_of(self, user_id: int) -> list[Mapping]:
        return await self.fetchall("SELECT * FROM handles WHERE user_id=? AND platform IN ('instagram','youtube') "
                                   "ORDER BY platform", (user_id,))

    async def handle_owner(self, platform: str, handle: str) -> int | None:
        row = await self.fetchone(
            "SELECT user_id FROM handles WHERE platform=? AND handle=? AND verified=1",
            (platform, handle),
        )
        return row["user_id"] if row else None

    async def verified_member_ids(self) -> list[int]:
        rows = await self.fetchall(
            "SELECT user_id FROM members WHERE verified_at IS NOT NULL AND left_at IS NULL"
        )
        return [r["user_id"] for r in rows]
