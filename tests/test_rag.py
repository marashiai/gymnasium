import asyncio
import sqlite3
import threading

from university import db, map_store, rag, rag_mcp


class FakeEmbeddings:
    model_name = "test-embeddings"
    dimensions = rag.EMBEDDING_DIMENSIONS

    _groups = {
        "routing": 0,
        "router": 0,
        "dispatch": 0,
        "expert": 1,
        "specialist": 1,
        "attention": 2,
        "transformer": 2,
        "diffusion": 3,
        "image": 3,
    }

    def _vector(self, text):
        vector = [0.0] * self.dimensions
        for word in rag.words(text):
            group = self._groups.get(word)
            if group is not None:
                vector[group] += 1.0
        return vector

    def embed_passages(self, texts):
        return [self._vector(text) for text in texts]

    def embed_query(self, text):
        return self._vector(text)


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    db.bootstrap(conn)
    return conn


def _item(conn, external_id, title, abstract=""):
    cur = conn.execute(
        "INSERT INTO corpus_item (kind, external_id, title, abstract, ingested_at) "
        "VALUES ('paper', ?, ?, ?, ?)",
        (external_id, title, abstract, db.utcnow()),
    )
    conn.commit()
    return conn.execute(
        "SELECT * FROM corpus_item WHERE id=?", (cur.lastrowid,)
    ).fetchone()


def test_chunk_markdown_keeps_headings_and_overlap():
    text = "# Overview\n\n" + " ".join("alpha{}".format(i) for i in range(30))
    text += "\n\n## Routing\n\n" + " ".join(
        "router{}".format(i) for i in range(30)
    )

    chunks = rag.chunk_markdown(text, target_words=20, overlap_words=4)

    assert len(chunks) >= 3
    assert chunks[0]["heading"] == "Overview"
    assert any(chunk["heading"] == "Routing" for chunk in chunks)
    assert all(chunk["locator"].startswith("chunk-") for chunk in chunks)
    assert set(rag.words(chunks[0]["text"])[-4:]).intersection(
        rag.words(chunks[1]["text"])
    )


def test_hybrid_search_finds_paraphrase_and_exact_term():
    conn = _conn()
    embeddings = FakeEmbeddings()
    routing = _item(conn, "routing", "Mixture of Experts")
    attention = _item(conn, "attention", "Transformers")
    rag.index_item(
        conn,
        routing,
        "# Routing\n\nA router sends each token to a suitable expert.",
        embeddings,
    )
    rag.index_item(
        conn,
        attention,
        "# Attention\n\nA transformer uses attention over its input.",
        embeddings,
    )

    semantic = rag.search(
        conn, "dispatch requests across specialists", embeddings, scope="library"
    )
    lexical = rag.search(conn, "transformer", embeddings, scope="library")

    assert semantic[0]["item_id"] == routing["id"]
    assert semantic[0]["vector_rank"] is not None
    assert lexical[0]["item_id"] == attention["id"]
    assert lexical[0]["lexical_rank"] is not None
    assert lexical[0]["citation_id"].startswith("passage:")


def test_current_item_scope_and_lexical_fallback():
    conn = _conn()
    embeddings = FakeEmbeddings()
    first = _item(conn, "first", "First")
    second = _item(conn, "second", "Second")
    rag.index_item(conn, first, "# One\n\nUnique routing evidence.", embeddings)
    rag.index_item(conn, second, "# Two\n\nUnique routing alternative.", embeddings)

    results = rag.search(
        conn, "routing", None, item_id=first["id"], scope="current"
    )

    assert results
    assert {result["item_id"] for result in results} == {first["id"]}
    assert results[0]["lexical_rank"] == 1
    assert results[0]["vector_rank"] is None


def test_current_scope_is_applied_inside_vector_search():
    conn = _conn()
    embeddings = FakeEmbeddings()
    for index in range(10):
        other = _item(conn, "other-{}".format(index), "Other {}".format(index))
        rag.index_item(conn, other, "# Other\n\nDispatch evidence.", embeddings)
    target = _item(conn, "target", "Target")
    rag.index_item(conn, target, "# Target\n\nRouter evidence.", embeddings)

    results = rag.search(
        conn, "dispatch", embeddings, item_id=target["id"],
        scope="current", limit=1,
    )

    assert len(results) == 1
    assert results[0]["item_id"] == target["id"]
    assert results[0]["vector_rank"] == 1
    assert results[0]["lexical_rank"] is None


def test_reindex_is_idempotent_and_replaces_stale_passages():
    conn = _conn()
    embeddings = FakeEmbeddings()
    item = _item(conn, "changing", "Changing")

    first = rag.index_item(conn, item, "# Old\n\nOld transformer text.", embeddings)
    second = rag.index_item(conn, item, "# Old\n\nOld transformer text.", embeddings)
    changed = rag.index_item(conn, item, "# New\n\nNew routing text.", embeddings)

    assert first["indexed"] is True
    assert second["indexed"] is False
    assert changed["indexed"] is True
    rows = conn.execute(
        "SELECT heading, content FROM rag_passage WHERE source_type='item' "
        "AND source_id=?", (item["id"],)
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["heading"] == "New"
    assert "Old transformer" not in rows[0]["content"]


def test_missing_source_cleanup_removes_stale_index_rows():
    conn = _conn()
    item = _item(conn, "deleted", "Deleted")
    rag.index_item(conn, item, "# Deleted\n\nRouter evidence.", FakeEmbeddings())
    conn.execute("DELETE FROM corpus_item WHERE id=?", (item["id"],))
    conn.commit()
    service = rag.RAGService(
        lambda: conn, "", embeddings=FakeEmbeddings(), close_connections=False)

    result = service.index_item_id(item["id"])

    assert result["status"] == "missing"
    assert conn.execute(
        "SELECT COUNT(*) FROM rag_passage WHERE source_type='item' AND source_id=?",
        (item["id"],),
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM rag_index_state WHERE source_type='item' AND source_id=?",
        (item["id"],),
    ).fetchone()[0] == 0


def test_indexer_reruns_when_source_changes_during_active_pass():
    class BlockingService:
        def __init__(self):
            self.calls = 0
            self.started = threading.Event()
            self.release = threading.Event()

        def index_item_id(self, _source_id):
            self.calls += 1
            if self.calls == 1:
                self.started.set()
                assert self.release.wait(2)

    service = BlockingService()
    indexer = rag.RAGIndexer(service)
    indexer.start(initial_scan=False)
    indexer.enqueue("item", 7)
    assert service.started.wait(2)

    indexer.enqueue("item", 7)
    indexer.enqueue("item", 7)
    service.release.set()
    indexer._queue.join()

    assert service.calls == 2


def test_semantic_map_suggestions_are_not_persisted():
    conn = _conn()
    embeddings = FakeEmbeddings()
    entries = []
    for term, body in (
        ("Router", "Dispatch tokens"),
        ("Expert selection", "Dispatch to a specialist"),
        ("Image diffusion", "Generate an image"),
    ):
        cur = conn.execute(
            "INSERT INTO kb_entry (term, span_text, mode, lead, body, created_at) "
            "VALUES (?, ?, 'concept', ?, ?, ?)",
            (term, term, term, body, db.utcnow()),
        )
        entries.append(int(cur.lastrowid))
    conn.commit()
    concept_ids = []
    for entry_id in entries:
        concept_ids.append(map_store.ensure_concept_for_entry(conn, entry_id))
        rag.index_kb_entry(conn, entry_id, embeddings)

    suggestions = rag.related_concepts(
        conn, embeddings, concept_id=concept_ids[0]
    )

    assert suggestions[0]["kb_entry_id"] == entries[1]
    assert conn.execute("SELECT COUNT(*) FROM concept_edge").fetchone()[0] == 0


def test_mcp_tools_bound_results_and_reject_bad_scope():
    conn = _conn()
    embeddings = FakeEmbeddings()
    item = _item(conn, "mcp", "MCP")
    rag.index_item(conn, item, "# Evidence\n\nRouter evidence.", embeddings)
    service = rag.RAGService(
        lambda: conn, "", embeddings=embeddings, close_connections=False
    )
    tools = rag_mcp.RAGTools(service)

    result = tools.search_knowledge("router", item_id=item["id"], limit=999)

    assert len(result["passages"]) <= rag.MAX_RESULTS
    assert result["passages"][0]["citation_id"].startswith("passage:")
    bad = tools.search_knowledge("router", scope="everything")
    assert bad == {"error": "invalid scope"}


def test_mcp_server_exposes_only_read_tools():
    conn = _conn()
    service = rag.RAGService(
        lambda: conn, "", embeddings=FakeEmbeddings(), close_connections=False
    )

    tools = asyncio.run(rag_mcp.create_server(service).list_tools())

    assert {tool.name for tool in tools} == {
        "search_knowledge", "get_passages", "related_concepts"
    }
