"""SQLite connection helpers and idempotent schema bootstrap.

Everything here is stdlib-only. The schema is created with ``IF NOT EXISTS``
so bootstrap can run on every server start without harm. FTS5 is probed
explicitly and a clear error is raised if the local SQLite build lacks it.
"""

from __future__ import annotations

import os
import sqlite3
import datetime as _dt
from typing import Optional


def utcnow() -> str:
    """ISO-8601 UTC timestamp, second resolution, no microseconds."""
    return _dt.datetime.utcnow().replace(microsecond=0).isoformat()


def connect(db_path: str) -> sqlite3.Connection:
    """Open a connection with sane defaults (Row factory, FK on, WAL)."""
    parent = os.path.dirname(os.path.abspath(db_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        pass
    return conn


def _fts5_available(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts5_probe USING fts5(x)")
        conn.execute("DROP TABLE IF EXISTS _fts5_probe")
        return True
    except sqlite3.OperationalError:
        return False


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT UNIQUE NOT NULL,
    password    TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_token (
    token       TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    expires_at  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS corpus_item (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    kind            TEXT NOT NULL,           -- 'paper' | 'repo'
    external_id     TEXT NOT NULL,
    title           TEXT NOT NULL,
    source          TEXT,
    url             TEXT,
    abstract        TEXT,
    summary_readable TEXT,
    summary_terms   TEXT,
    why             TEXT,
    signal          INTEGER DEFAULT 0,       -- normalized 0..100
    published_at    TEXT,
    ingested_at     TEXT NOT NULL,
    doc_path        TEXT,
    doc_fetched_at  TEXT,
    markdown_path   TEXT,
    markdown_source TEXT,                     -- 'user' | 'auto'
    added_by_user   INTEGER DEFAULT 0,        -- 1 = added by the user via a link
    doc_uploaded    INTEGER DEFAULT 0,        -- 1 = source doc was user-uploaded
    raw_json        TEXT,
    UNIQUE (kind, external_id)
);

CREATE TABLE IF NOT EXISTS kb_entry (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    term            TEXT NOT NULL,
    span_text       TEXT NOT NULL,
    item_id         INTEGER,
    source_url      TEXT,
    source_doc_path TEXT,
    mode            TEXT,
    model           TEXT,
    lead            TEXT,
    body            TEXT,
    analogy         TEXT,
    tag             TEXT,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (item_id) REFERENCES corpus_item(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS kb_message (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    kb_entry_id     INTEGER NOT NULL,
    role            TEXT NOT NULL,           -- 'user' | 'assistant'
    content         TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (kb_entry_id) REFERENCES kb_entry(id) ON DELETE CASCADE
);

-- Back-references: every corpus_item a concept (kb_entry) has been seen in.
-- A single concept can be linked by MANY articles, so this is a join table
-- (one row per (entry, item) pair) deduped by the UNIQUE constraint.
CREATE TABLE IF NOT EXISTS kb_entry_source (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    kb_entry_id     INTEGER NOT NULL,
    item_id         INTEGER NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE (kb_entry_id, item_id),
    FOREIGN KEY (kb_entry_id) REFERENCES kb_entry(id) ON DELETE CASCADE,
    FOREIGN KEY (item_id) REFERENCES corpus_item(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS concept (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    label           TEXT NOT NULL,
    kb_entry_id     INTEGER,
    x               REAL NOT NULL DEFAULT 50,
    y               REAL NOT NULL DEFAULT 50,
    tone            TEXT DEFAULT 'spark',
    created_at      TEXT NOT NULL,
    FOREIGN KEY (kb_entry_id) REFERENCES kb_entry(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS concept_edge (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    src_concept_id  INTEGER NOT NULL,
    dst_concept_id  INTEGER NOT NULL,
    source          TEXT NOT NULL DEFAULT 'manual',  -- 'ai' | 'manual'
    created_at      TEXT NOT NULL,
    UNIQUE (src_concept_id, dst_concept_id),
    FOREIGN KEY (src_concept_id) REFERENCES concept(id) ON DELETE CASCADE,
    FOREIGN KEY (dst_concept_id) REFERENCES concept(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_corpus_kind ON corpus_item(kind);
CREATE INDEX IF NOT EXISTS idx_kbmsg_entry ON kb_message(kb_entry_id);
CREATE INDEX IF NOT EXISTS idx_concept_entry ON concept(kb_entry_id);
CREATE INDEX IF NOT EXISTS idx_kbsource_entry ON kb_entry_source(kb_entry_id);
CREATE INDEX IF NOT EXISTS idx_kbsource_item ON kb_entry_source(item_id);
"""

# FTS5 over the saved-learning text. We index the entry's own fields plus the
# concatenated message bodies so a search hits words from ANY turn. The table
# is content-less (no external content table) and kept in sync by triggers on
# kb_entry and kb_message; we store kb_entry_id in an UNINDEXED column so a
# match maps straight back to the owning entry.
FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS kb_fts USING fts5(
    entry_id UNINDEXED,
    term,
    span_text,
    lead,
    body,
    messages
);
"""


def _messages_blob(conn: sqlite3.Connection, entry_id: int) -> str:
    rows = conn.execute(
        "SELECT content FROM kb_message WHERE kb_entry_id=? ORDER BY id", (entry_id,)
    ).fetchall()
    return "\n".join(r["content"] for r in rows)


def reindex_entry(conn: sqlite3.Connection, entry_id: int) -> None:
    """Rebuild the FTS row for a single kb_entry from current DB state."""
    conn.execute("DELETE FROM kb_fts WHERE entry_id=?", (entry_id,))
    row = conn.execute(
        "SELECT id, term, span_text, lead, body FROM kb_entry WHERE id=?", (entry_id,)
    ).fetchone()
    if row is None:
        return
    conn.execute(
        "INSERT INTO kb_fts (entry_id, term, span_text, lead, body, messages) "
        "VALUES (?,?,?,?,?,?)",
        (
            entry_id,
            row["term"] or "",
            row["span_text"] or "",
            row["lead"] or "",
            row["body"] or "",
            _messages_blob(conn, entry_id),
        ),
    )


def link_source(conn: sqlite3.Connection, kb_entry_id: int, item_id: Optional[int]) -> None:
    """Record that ``kb_entry_id`` (a concept) was seen in ``item_id``.

    Deduped by the UNIQUE(kb_entry_id, item_id) constraint via INSERT OR IGNORE,
    so calling it repeatedly for the same pair adds nothing. A falsy item_id is
    a no-op (a concept with no originating article). The caller commits.
    """
    if not kb_entry_id or not item_id:
        return
    conn.execute(
        "INSERT OR IGNORE INTO kb_entry_source (kb_entry_id, item_id, created_at) "
        "VALUES (?,?,?)", (int(kb_entry_id), int(item_id), utcnow()))


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    """Add ``column`` to ``table`` if an existing DB predates it. Idempotent.

    Existing installs already have a ``corpus_item`` table from before a column
    was introduced; CREATE TABLE IF NOT EXISTS won't alter it, so we add the
    column explicitly when ``PRAGMA table_info`` shows it absent.
    """
    cols = {r["name"] for r in conn.execute("PRAGMA table_info({})".format(table)).fetchall()}
    if column not in cols:
        conn.execute("ALTER TABLE {} ADD COLUMN {} {}".format(table, column, decl))


def bootstrap(conn: sqlite3.Connection) -> None:
    """Create every table/index/FTS structure if missing. Idempotent."""
    if not _fts5_available(conn):
        raise RuntimeError(
            "SQLite FTS5 is not available in this Python build; the knowledge "
            "base search requires it. Rebuild Python/SQLite with FTS5 enabled."
        )
    conn.executescript(SCHEMA)
    conn.executescript(FTS_SCHEMA)
    # Migrations for DBs created before a column existed.
    _ensure_column(conn, "corpus_item", "markdown_path", "TEXT")
    _ensure_column(conn, "corpus_item", "markdown_source", "TEXT")
    _ensure_column(conn, "corpus_item", "added_by_user", "INTEGER DEFAULT 0")
    _ensure_column(conn, "corpus_item", "doc_uploaded", "INTEGER DEFAULT 0")
    # Backfill the back-reference table from each entry's originating item_id so
    # existing concepts already list their origin article. Idempotent: the
    # UNIQUE constraint + INSERT OR IGNORE means re-running adds nothing.
    conn.execute(
        "INSERT OR IGNORE INTO kb_entry_source (kb_entry_id, item_id, created_at) "
        "SELECT id, item_id, created_at FROM kb_entry WHERE item_id IS NOT NULL")
    conn.commit()
