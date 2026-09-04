"""Versioned local indexing and hybrid retrieval for Gymnasium."""

from __future__ import annotations

import hashlib
import math
import os
import queue
import re
import sqlite3
import threading
from typing import Callable, Dict, Iterable, List, Optional, Protocol

from . import docs
from .db import utcnow

EMBEDDING_DIMENSIONS = 384
DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
CHUNKER_VERSION = "markdown-v1"
MAX_RESULTS = 12
MAX_PASSAGE_CHARS = 1800
_RRF_K = 60.0
_VALID_SCOPES = {"current", "library", "knowledge", "all"}
_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


class Embeddings(Protocol):
    model_name: str
    dimensions: int

    def embed_passages(self, texts: Iterable[str]) -> List[List[float]]: ...

    def embed_query(self, text: str) -> List[float]: ...


class FastEmbedEmbeddings:
    """Lazy, process-local FastEmbed model shared by indexing and queries."""

    dimensions = EMBEDDING_DIMENSIONS

    def __init__(self, model_name: Optional[str] = None,
                 cache_dir: Optional[str] = None):
        # The sqlite-vec table is intentionally fixed at this model's 384
        # dimensions. Changing models therefore requires an explicit index
        # format/version change rather than an unsafe runtime toggle.
        self.model_name = model_name or DEFAULT_MODEL
        self.cache_dir = cache_dir or os.environ.get("GYM_EMBED_CACHE")
        self._model = None
        self._lock = threading.Lock()

    def _get_model(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from fastembed import TextEmbedding

                    self._model = TextEmbedding(
                        model_name=self.model_name,
                        cache_dir=self.cache_dir,
                        threads=max(1, min(4, os.cpu_count() or 1)),
                    )
        return self._model

    @staticmethod
    def _lists(vectors) -> List[List[float]]:
        return [vector.astype("float32", copy=False).tolist() for vector in vectors]

    def embed_passages(self, texts: Iterable[str]) -> List[List[float]]:
        return self._lists(self._get_model().embed(list(texts)))

    def embed_query(self, text: str) -> List[float]:
        return self._lists(self._get_model().query_embed(text))[0]


def words(text: str) -> List[str]:
    return [match.group(0).lower() for match in _WORD_RE.finditer(text or "")]


def _clean_heading(raw: str) -> str:
    return re.sub(r"\s+#+\s*$", "", raw).strip()


def chunk_markdown(text: str, target_words: int = 320,
                   overlap_words: int = 48) -> List[dict]:
    """Split Markdown into bounded, heading-aware passages."""
    target_words = max(20, int(target_words))
    overlap_words = max(0, min(int(overlap_words), target_words // 2))
    sections: List[tuple] = []
    heading = "Document"
    body: List[str] = []
    for line in (text or "").splitlines():
        match = _HEADING_RE.match(line)
        if match:
            if any(part.strip() for part in body):
                sections.append((heading, "\n".join(body).strip()))
            heading = _clean_heading(match.group(2)) or "Document"
            body = []
        else:
            body.append(line)
    if any(part.strip() for part in body):
        sections.append((heading, "\n".join(body).strip()))
    if not sections and (text or "").strip():
        sections.append(("Document", text.strip()))

    chunks: List[dict] = []
    step = max(1, target_words - overlap_words)
    for section_heading, section_text in sections:
        tokens = section_text.split()
        for start in range(0, len(tokens), step):
            part = tokens[start:start + target_words]
            if not part:
                continue
            chunks.append({
                "heading": section_heading,
                "locator": "chunk-{}".format(len(chunks) + 1),
                "text": " ".join(part),
            })
            if start + target_words >= len(tokens):
                break
    return chunks


def vector_available(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("SELECT vec_version()").fetchone()
        conn.execute("SELECT 1 FROM rag_vec LIMIT 1").fetchone()
        return True
    except sqlite3.Error:
        return False


def _model_name(embeddings: Optional[Embeddings]) -> Optional[str]:
    return embeddings.model_name if embeddings is not None else None


def _source_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _delete_passages(conn: sqlite3.Connection, source_type: str,
                     source_id: int) -> None:
    ids = [int(row["id"]) for row in conn.execute(
        "SELECT id FROM rag_passage WHERE source_type=? AND source_id=?",
        (source_type, source_id),
    ).fetchall()]
    if ids and vector_available(conn):
        conn.executemany("DELETE FROM rag_vec WHERE rowid=?", [(pid,) for pid in ids])
    conn.executemany("DELETE FROM rag_fts WHERE passage_id=?", [(pid,) for pid in ids])
    conn.execute(
        "DELETE FROM rag_passage WHERE source_type=? AND source_id=?",
        (source_type, source_id),
    )


def delete_source(conn: sqlite3.Connection, source_type: str,
                  source_id: int) -> None:
    _delete_passages(conn, source_type, int(source_id))
    conn.execute(
        "DELETE FROM rag_index_state WHERE source_type=? AND source_id=?",
        (source_type, int(source_id)),
    )
    conn.commit()


def _replace_source(conn: sqlite3.Connection, source_type: str, source_id: int,
                    item_id: Optional[int], chunks: List[dict], source_hash: str,
                    embeddings: Optional[Embeddings]) -> Dict[str, object]:
    model_name = _model_name(embeddings)
    vectors: List[List[float]] = []
    error = None
    have_vectors = embeddings is not None and vector_available(conn)
    if have_vectors:
        try:
            vectors = embeddings.embed_passages(
                "{}\n{}".format(chunk["heading"], chunk["text"])
                for chunk in chunks
            )
            if any(len(vector) != EMBEDDING_DIMENSIONS for vector in vectors):
                raise ValueError("embedding dimensions must be {}".format(
                    EMBEDDING_DIMENSIONS))
        except Exception as exc:
            vectors = []
            error = str(exc)[:500]

    _delete_passages(conn, source_type, source_id)
    passage_ids = []
    now = utcnow()
    for ordinal, chunk in enumerate(chunks):
        content = chunk["text"].strip()
        cur = conn.execute(
            "INSERT INTO rag_passage (source_type, source_id, item_id, ordinal, "
            "heading, locator, content, content_hash, embedding_model, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (source_type, source_id, item_id, ordinal, chunk["heading"],
             chunk["locator"], content, _source_hash(content), model_name, now),
        )
        passage_id = int(cur.lastrowid)
        passage_ids.append(passage_id)
        conn.execute(
            "INSERT INTO rag_fts (passage_id, heading, content) VALUES (?,?,?)",
            (passage_id, chunk["heading"], content),
        )
    if vectors:
        import sqlite_vec

        conn.executemany(
            "INSERT INTO rag_vec (rowid, embedding) VALUES (?,?)",
            [(pid, sqlite_vec.serialize_float32(vector))
             for pid, vector in zip(passage_ids, vectors)],
        )
    if vectors:
        status = "ready"
    elif embeddings is None or not vector_available(conn):
        status = "lexical"
    else:
        status = "partial"
    conn.execute(
        "INSERT INTO rag_index_state (source_type, source_id, content_hash, "
        "chunker_version, embedding_model, status, error, indexed_at) "
        "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(source_type, source_id) DO UPDATE SET "
        "content_hash=excluded.content_hash, chunker_version=excluded.chunker_version, "
        "embedding_model=excluded.embedding_model, status=excluded.status, "
        "error=excluded.error, indexed_at=excluded.indexed_at",
        (source_type, source_id, source_hash, CHUNKER_VERSION, model_name,
         status, error, now),
    )
    conn.commit()
    return {"indexed": True, "passages": len(passage_ids),
            "vectors": len(vectors), "status": status, "error": error}


def _unchanged(conn: sqlite3.Connection, source_type: str, source_id: int,
               source_hash: str, embeddings: Optional[Embeddings]) -> bool:
    row = conn.execute(
        "SELECT * FROM rag_index_state WHERE source_type=? AND source_id=?",
        (source_type, source_id),
    ).fetchone()
    if not (row and row["content_hash"] == source_hash
            and row["chunker_version"] == CHUNKER_VERSION
            and row["embedding_model"] == _model_name(embeddings)):
        return False
    if row["status"] == "ready":
        return True
    return row["status"] == "lexical" and (
        embeddings is None or not vector_available(conn))


def index_item(conn: sqlite3.Connection, item, text: str,
               embeddings: Optional[Embeddings]) -> Dict[str, object]:
    source_id = int(item["id"])
    title = (item["title"] or "Document").strip()
    content = (text or "").strip()
    source_hash = _source_hash(content)
    if _unchanged(conn, "item", source_id, source_hash, embeddings):
        return {"indexed": False, "passages": 0, "status": "ready"}
    chunks = chunk_markdown(content)
    if not chunks:
        chunks = [{"heading": title, "locator": "chunk-1", "text": title}]
    return _replace_source(conn, "item", source_id, source_id, chunks,
                           source_hash, embeddings)


def index_kb_entry(conn: sqlite3.Connection, entry_id: int,
                   embeddings: Optional[Embeddings]) -> Dict[str, object]:
    row = conn.execute(
        "SELECT id, item_id, term, span_text, lead, body FROM kb_entry WHERE id=?",
        (int(entry_id),),
    ).fetchone()
    if row is None:
        return {"indexed": False, "passages": 0, "status": "missing"}
    text = "\n\n".join(str(value).strip() for value in (
        row["term"], row["span_text"], row["lead"], row["body"]
    ) if value and str(value).strip())
    source_hash = _source_hash(text)
    if _unchanged(conn, "kb", int(entry_id), source_hash, embeddings):
        return {"indexed": False, "passages": 0, "status": "ready"}
    chunks = [{"heading": row["term"] or "Knowledge note",
               "locator": "note-{}".format(entry_id), "text": text}]
    return _replace_source(conn, "kb", int(entry_id), row["item_id"], chunks,
                           source_hash, embeddings)


def _fts_query(query: str) -> str:
    tokens = []
    for token in words(query):
        if len(token) >= 2 and token not in tokens:
            tokens.append(token)
    return " OR ".join('"{}"*'.format(token.replace('"', ''))
                       for token in tokens[:16])


def _scope_clause(scope: str, item_id: Optional[int]) -> tuple:
    if scope not in _VALID_SCOPES:
        raise ValueError("invalid scope")
    if scope == "current":
        if item_id is None:
            raise ValueError("current scope requires item_id")
        return "p.item_id=?", [int(item_id)]
    if scope == "library":
        return "p.source_type='item'", []
    if scope == "knowledge":
        return "p.source_type='kb'", []
    return "1=1", []


def _row_payload(row) -> dict:
    return {
        "passage_id": int(row["id"]),
        "citation_id": "passage:{}".format(row["id"]),
        "source_type": row["source_type"],
        "source_id": int(row["source_id"]),
        "item_id": int(row["item_id"]) if row["item_id"] is not None else None,
        "title": row["title"] or row["heading"] or "Knowledge note",
        "heading": row["heading"] or "Document",
        "locator": row["locator"],
        "content": row["content"][:MAX_PASSAGE_CHARS],
        "source_url": row["url"],
    }


def search(conn: sqlite3.Connection, query: str,
           embeddings: Optional[Embeddings], item_id: Optional[int] = None,
           scope: str = "all", limit: int = 8) -> List[dict]:
    """Return bounded hybrid results using reciprocal-rank fusion."""
    limit = max(1, min(int(limit), MAX_RESULTS))
    clause, params = _scope_clause(scope, item_id)
    candidates: Dict[int, dict] = {}
    select = (
        "SELECT p.*, c.title, c.url FROM rag_passage p "
        "LEFT JOIN corpus_item c ON c.id=p.item_id "
    )
    match = _fts_query(query)
    if match:
        try:
            rows = conn.execute(
                select + "JOIN rag_fts f ON CAST(f.passage_id AS INTEGER)=p.id "
                "WHERE rag_fts MATCH ? AND " + clause + " ORDER BY bm25(rag_fts) "
                "LIMIT ?", [match] + params + [limit * 4],
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        for rank, row in enumerate(rows, 1):
            payload = _row_payload(row)
            payload.update({"lexical_rank": rank, "vector_rank": None,
                            "distance": None, "score": 1.0 / (_RRF_K + rank)})
            candidates[payload["passage_id"]] = payload

    if embeddings is not None and vector_available(conn):
        try:
            vector = embeddings.embed_query(query)
            if len(vector) != EMBEDDING_DIMENSIONS or not any(vector):
                raise ValueError("empty or invalid query embedding")
            import sqlite_vec

            # Constrain the virtual-table KNN query itself. Filtering global
            # neighbours afterward can lose every in-scope result once the
            # index is larger than the candidate window.
            nearest = conn.execute(
                "SELECT rowid, distance FROM rag_vec WHERE embedding MATCH ? "
                "AND rowid IN (SELECT p.id FROM rag_passage p WHERE " + clause
                + ") ORDER BY distance LIMIT ?",
                [sqlite_vec.serialize_float32(vector)] + params + [limit * 8],
            ).fetchall()
            ids = [int(row["rowid"]) for row in nearest]
            distances = {int(row["rowid"]): float(row["distance"])
                         for row in nearest}
            if ids:
                placeholders = ",".join("?" for _ in ids)
                rows = conn.execute(
                    select + "WHERE p.id IN ({}) AND ".format(placeholders)
                    + clause, ids + params,
                ).fetchall()
                by_id = {int(row["id"]): row for row in rows}
                vector_rank = 0
                for pid in ids:
                    row = by_id.get(pid)
                    if row is None:
                        continue
                    vector_rank += 1
                    payload = candidates.get(pid) or _row_payload(row)
                    payload["vector_rank"] = vector_rank
                    payload.setdefault("lexical_rank", None)
                    payload["distance"] = distances[pid]
                    payload["score"] = payload.get("score", 0.0) + (
                        1.0 / (_RRF_K + vector_rank))
                    candidates[pid] = payload
        except (ImportError, sqlite3.Error, ValueError, RuntimeError):
            pass

    ranked = sorted(candidates.values(), key=lambda row: (
        -row["score"], row["distance"] if row["distance"] is not None else math.inf,
        row["passage_id"],
    ))
    selected = []
    source_counts: Dict[tuple, int] = {}
    token_sets = []
    for row in ranked:
        source = (row["source_type"], row["source_id"])
        if source_counts.get(source, 0) >= 2:
            continue
        token_set = set(words(row["content"]))
        if token_set and any(
            len(token_set & seen) / max(1, len(token_set | seen)) > 0.85
            for seen in token_sets
        ):
            continue
        selected.append(row)
        token_sets.append(token_set)
        source_counts[source] = source_counts.get(source, 0) + 1
        if len(selected) >= limit:
            break
    return selected


def get_passages(conn: sqlite3.Connection, citation_ids: Iterable[str],
                 item_id: Optional[int] = None) -> List[dict]:
    ids = []
    for citation_id in list(citation_ids)[:MAX_RESULTS]:
        match = re.fullmatch(r"passage:(\d+)", str(citation_id))
        if match:
            ids.append(int(match.group(1)))
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    item_clause = " AND p.item_id=?" if item_id is not None else ""
    query_params = ids + ([int(item_id)] if item_id is not None else [])
    rows = conn.execute(
        "SELECT p.*, c.title, c.url FROM rag_passage p "
        "LEFT JOIN corpus_item c ON c.id=p.item_id WHERE p.id IN ({}){}".format(
            placeholders, item_clause), query_params,
    ).fetchall()
    by_id = {int(row["id"]): _row_payload(row) for row in rows}
    return [by_id[pid] for pid in ids if pid in by_id]


def related_concepts(conn: sqlite3.Connection, embeddings: Embeddings,
                     concept_id: Optional[int] = None,
                     text: Optional[str] = None, limit: int = 8) -> List[dict]:
    exclude_entry = None
    if concept_id is not None:
        row = conn.execute(
            "SELECT c.kb_entry_id, e.term, e.lead, e.body FROM concept c "
            "JOIN kb_entry e ON e.id=c.kb_entry_id WHERE c.id=?",
            (int(concept_id),),
        ).fetchone()
        if row is None:
            return []
        exclude_entry = int(row["kb_entry_id"])
        text = " ".join(str(value) for value in (
            row["term"], row["lead"], row["body"]
        ) if value)
    if not (text or "").strip():
        return []
    results = search(conn, text or "", embeddings, scope="knowledge",
                     limit=min(MAX_RESULTS, int(limit) + 1))
    out = []
    for result in results:
        entry_id = result["source_id"]
        if entry_id == exclude_entry:
            continue
        concept = conn.execute(
            "SELECT id, label FROM concept WHERE kb_entry_id=?", (entry_id,)
        ).fetchone()
        if concept is None:
            continue
        distance = result["distance"]
        out.append({
            "concept_id": int(concept["id"]),
            "kb_entry_id": entry_id,
            "label": concept["label"],
            "similarity": round(max(0.0, 1.0 - float(distance)), 4)
            if distance is not None else None,
            "shared_source": result["item_id"],
        })
        if len(out) >= min(int(limit), MAX_RESULTS):
            break
    return out


def _item_text(item, docs_dir: str) -> str:
    content = docs.read_markdown(item, docs_dir)
    if content is None and item["kind"] == "repo":
        content = docs.read_repo_readme(item, docs_dir)
    if content is None and item["kind"] != "repo":
        content = docs.read_auto_markdown(item, docs_dir)
    if content:
        return content
    return "# {}\n\n{}\n\n{}".format(
        item["title"] or "Document", item["abstract"] or "", item["why"] or "")


class RAGService:
    """Own connection lifetime around the pure indexing/search functions."""

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection],
                 docs_dir: str, embeddings: Optional[Embeddings] = None,
                 close_connections: bool = True,
                 enable_embeddings: bool = True):
        self.connection_factory = connection_factory
        self.docs_dir = docs_dir
        self.embeddings = embeddings or (FastEmbedEmbeddings()
                                         if enable_embeddings else None)
        self.close_connections = close_connections

    def _conn(self):
        return self.connection_factory()

    def _close(self, conn):
        if self.close_connections:
            conn.close()

    def search(self, query: str, item_id: Optional[int] = None,
               scope: str = "all", limit: int = 8) -> List[dict]:
        conn = self._conn()
        try:
            return search(conn, query, self.embeddings, item_id, scope, limit)
        finally:
            self._close(conn)

    def passages(self, citation_ids: Iterable[str],
                 item_id: Optional[int] = None) -> List[dict]:
        conn = self._conn()
        try:
            return get_passages(conn, citation_ids, item_id=item_id)
        finally:
            self._close(conn)

    def related(self, concept_id: Optional[int] = None,
                text: Optional[str] = None, limit: int = 8) -> List[dict]:
        conn = self._conn()
        try:
            return related_concepts(conn, self.embeddings, concept_id, text, limit)
        finally:
            self._close(conn)

    def index_item_id(self, item_id: int) -> Dict[str, object]:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM corpus_item WHERE id=?", (int(item_id),)
            ).fetchone()
            if row is None:
                delete_source(conn, "item", int(item_id))
                return {"indexed": False, "status": "missing", "passages": 0}
            return index_item(conn, row, _item_text(row, self.docs_dir), self.embeddings)
        finally:
            self._close(conn)

    def index_kb_id(self, entry_id: int) -> Dict[str, object]:
        conn = self._conn()
        try:
            result = index_kb_entry(conn, int(entry_id), self.embeddings)
            if result.get("status") == "missing":
                delete_source(conn, "kb", int(entry_id))
            return result
        finally:
            self._close(conn)

    def index_all(self) -> Dict[str, int]:
        conn = self._conn()
        try:
            item_ids = [int(row["id"]) for row in conn.execute(
                "SELECT id FROM corpus_item ORDER BY id").fetchall()]
            entry_ids = [int(row["id"]) for row in conn.execute(
                "SELECT id FROM kb_entry WHERE mode IS NULL OR mode != 'chat' "
                "ORDER BY id").fetchall()]
        finally:
            self._close(conn)
        indexed = 0
        for item_id in item_ids:
            indexed += bool(self.index_item_id(item_id).get("indexed"))
        for entry_id in entry_ids:
            indexed += bool(self.index_kb_id(entry_id).get("indexed"))
        return {"sources": len(item_ids) + len(entry_ids), "indexed": indexed}


class RAGIndexer:
    """Single background worker; duplicate source updates collapse in the queue."""

    def __init__(self, service: RAGService):
        self.service = service
        self._queue: "queue.Queue[tuple]" = queue.Queue()
        self._pending = set()
        self._active = set()
        self._dirty = set()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="rag-indexer", daemon=True)

    def start(self, initial_scan: bool = True) -> None:
        self._thread.start()
        if initial_scan:
            self.enqueue("all", 0)

    def enqueue(self, source_type: str, source_id: int) -> None:
        key = (source_type, int(source_id))
        with self._lock:
            if key in self._pending:
                # A change that arrives while the same source is being
                # embedded must cause one more pass after the active pass.
                if key in self._active:
                    self._dirty.add(key)
                return
            self._pending.add(key)
        self._queue.put(key)

    def _run(self) -> None:
        while True:
            key = self._queue.get()
            source_type, source_id = key
            with self._lock:
                self._active.add(key)
            try:
                if source_type == "all":
                    self.service.index_all()
                elif source_type == "item":
                    self.service.index_item_id(source_id)
                elif source_type == "kb":
                    self.service.index_kb_id(source_id)
            except Exception as exc:
                print("[rag] indexing {} {} failed: {}".format(
                    source_type, source_id, exc))
            finally:
                with self._lock:
                    self._active.discard(key)
                    rerun = key in self._dirty
                    if rerun:
                        self._dirty.discard(key)
                    else:
                        self._pending.discard(key)
                if rerun:
                    self._queue.put(key)
                self._queue.task_done()
