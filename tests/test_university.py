"""Offline, fixture-based tests for the university package.

No live network: AI is stubbed via a fake `opencode` binary (OPENCODE_BIN),
document downloads are monkeypatched, and the tracker pipeline is stubbed.
"""

import json
import os
import sqlite3
import sys
import threading
import time
import types

import pytest

from university import ai, auth, db, docs, ingest, map_store, refresh, retrieval

FAKE_OPENCODE = os.path.join(os.path.dirname(__file__), "fixtures", "fake_opencode.py")


def _install_fake_markitdown(monkeypatch, text="# Auto\n\nConverted body."):
    """Inject an offline stand-in for ``markitdown`` so no binary/network runs.

    ``docs.auto_markdown`` does ``from markitdown import MarkItDown`` at call
    time, so swapping the module in ``sys.modules`` is enough to control it.
    """
    mod = types.ModuleType("markitdown")

    class _Result:
        def __init__(self, t):
            self.text_content = t

    class MarkItDown:
        def convert(self, path):
            return _Result(text)

    mod.MarkItDown = MarkItDown
    monkeypatch.setitem(sys.modules, "markitdown", mod)
    return text


@pytest.fixture(autouse=True)
def _fake_ai(monkeypatch):
    monkeypatch.setenv("OPENCODE_BIN", FAKE_OPENCODE)
    yield


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    db.bootstrap(c)
    return c


@pytest.fixture
def sample_reports(tmp_path):
    """A tiny reports dir with one paper sidecar and one repo sidecar."""
    papers = {
        "papers": [
            {"title": "Mixture-of-Experts at Scale", "arxiv_id": "2601.00001",
             "date": "2026-06-10", "abstract": "A study of routing in MoE models.",
             "abs_url": "https://arxiv.org/abs/2601.00001",
             "pdf_url": "https://arxiv.org/pdf/2601.00001",
             "max_author_cited_by": 40000, "labs_matched": ["google"],
             "primary_field": "Computer Science",
             "impact_summary": "max author citations 40000"},
            {"title": "Tiny Result", "arxiv_id": "2601.00002", "date": "2026-06-01",
             "abstract": "A small note.", "max_author_cited_by": 10,
             "labs_matched": ["openai"]},
        ]
    }
    repos = {
        "repos": [
            {"full_name": "acme/agent-forge", "owner": "acme", "name": "agent-forge",
             "description": "An autonomous PR pipeline.",
             "html_url": "https://github.com/acme/agent-forge", "language": "Python",
             "stargazers_count": 9000, "created_at": "2026-06-05T00:00:00Z",
             "topics": ["agents"]},
        ]
    }
    rp = tmp_path / "reports"
    rp.mkdir()
    (rp / "labpapers_2026-06-10_30d.json").write_text(json.dumps(papers))
    (rp / "labrepos_2026-06-05_30d.json").write_text(json.dumps(repos))
    return str(rp)


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------
class _CountingConn:
    """Wraps a sqlite3 connection and counts execute() calls."""
    def __init__(self, inner):
        self._inner = inner
        self.execute_calls = 0

    def execute(self, *a, **k):
        self.execute_calls += 1
        return self._inner.execute(*a, **k)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_alnum_guard_rejects_before_db(conn):
    # If the guard runs first, the DB is never queried for a bad credential.
    auth.add_user(conn, "maya", "secret1")
    spy = _CountingConn(conn)
    assert auth.verify_credentials(spy, "ma!ya", "secret1") is None
    assert spy.execute_calls == 0  # guard short-circuited before any query
    # a well-formed but wrong credential DOES hit the DB
    assert auth.verify_credentials(spy, "maya", "wrong") is None
    assert spy.execute_calls == 1


def test_password_right_and_wrong(conn):
    uid = auth.add_user(conn, "maya", "pw123")
    assert auth.verify_credentials(conn, "maya", "pw123") == uid
    assert auth.verify_credentials(conn, "maya", "nope") is None


def test_token_issue_reuse_slide_expire_revoke(conn):
    uid = auth.add_user(conn, "maya", "pw123")
    tok = auth.issue_token(conn, uid)
    assert auth.verify_token(conn, tok) == uid
    # expire it manually
    conn.execute("UPDATE auth_token SET expires_at=? WHERE token=?",
                 ("2000-01-01T00:00:00", tok))
    conn.commit()
    assert auth.verify_token(conn, tok) is None
    # revoke
    tok2 = auth.issue_token(conn, uid)
    auth.revoke_token(conn, tok2)
    assert auth.verify_token(conn, tok2) is None


def test_adduser_rejects_nonalnum(conn):
    with pytest.raises(ValueError):
        auth.add_user(conn, "ma ya", "pw")


# --------------------------------------------------------------------------
# bootstrap / fts
# --------------------------------------------------------------------------
def test_bootstrap_idempotent(conn):
    db.bootstrap(conn)  # second call must not raise
    db.bootstrap(conn)
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table')").fetchall()}
    assert {"users", "corpus_item", "kb_entry", "kb_message", "concept",
            "concept_edge"}.issubset(tables)


def test_bootstrap_adds_markdown_column_on_old_schema():
    """An existing DB whose corpus_item predates markdown_path is migrated."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    # Pre-existing OLD schema: corpus_item WITHOUT markdown_path.
    c.executescript(
        "CREATE TABLE corpus_item ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  kind TEXT NOT NULL, external_id TEXT NOT NULL, title TEXT NOT NULL,"
        "  ingested_at TEXT NOT NULL, raw_json TEXT,"
        "  UNIQUE (kind, external_id));"
    )
    cols = {r["name"] for r in c.execute("PRAGMA table_info(corpus_item)").fetchall()}
    assert "markdown_path" not in cols
    db.bootstrap(c)
    cols = {r["name"] for r in c.execute("PRAGMA table_info(corpus_item)").fetchall()}
    assert "markdown_path" in cols
    db.bootstrap(c)  # idempotent re-run on the migrated DB must not raise


def test_bootstrap_adds_added_by_user_column_on_old_schema():
    """An existing DB whose corpus_item predates added_by_user is migrated."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        "CREATE TABLE corpus_item ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  kind TEXT NOT NULL, external_id TEXT NOT NULL, title TEXT NOT NULL,"
        "  ingested_at TEXT NOT NULL, raw_json TEXT,"
        "  UNIQUE (kind, external_id));"
    )
    cols = {r["name"] for r in c.execute("PRAGMA table_info(corpus_item)").fetchall()}
    assert "added_by_user" not in cols
    db.bootstrap(c)
    cols = {r["name"] for r in c.execute("PRAGMA table_info(corpus_item)").fetchall()}
    assert "added_by_user" in cols
    db.bootstrap(c)  # idempotent re-run on the migrated DB must not raise


# --------------------------------------------------------------------------
# ingest
# --------------------------------------------------------------------------
def test_ingest_loads_and_normalizes(conn, sample_reports):
    counts = ingest.ingest_reports(sample_reports, conn)
    assert counts["new"] == 3
    rows = conn.execute("SELECT signal FROM corpus_item").fetchall()
    assert all(0 <= r["signal"] <= 100 for r in rows)
    assert conn.execute("SELECT COUNT(*) c FROM corpus_item").fetchone()["c"] == 3


def test_ingest_dedupes_on_rerun(conn, sample_reports):
    ingest.ingest_reports(sample_reports, conn)
    before = conn.execute("SELECT COUNT(*) c FROM corpus_item").fetchone()["c"]
    ingest.ingest_reports(sample_reports, conn)
    after = conn.execute("SELECT COUNT(*) c FROM corpus_item").fetchone()["c"]
    assert before == after == 3


# --------------------------------------------------------------------------
# docs
# --------------------------------------------------------------------------
def test_ensure_document_stores_paper_and_repo(conn, sample_reports, tmp_path, monkeypatch):
    monkeypatch.setattr(docs, "_fetch", lambda url: b"FAKEDOC")
    ingest.ingest_reports(sample_reports, conn)
    docs_dir = str(tmp_path / "documents")
    paper = conn.execute("SELECT * FROM corpus_item WHERE kind='paper' LIMIT 1").fetchone()
    rel = docs.ensure_document(paper, docs_dir, conn)
    assert rel.startswith("papers/")
    assert os.path.isfile(os.path.join(docs_dir, rel))
    # doc_path persisted
    refreshed = conn.execute("SELECT doc_path FROM corpus_item WHERE id=?", (paper["id"],)).fetchone()
    assert refreshed["doc_path"] == rel
    repo = conn.execute("SELECT * FROM corpus_item WHERE kind='repo' LIMIT 1").fetchone()
    rrel = docs.ensure_document(repo, docs_dir, conn)
    assert rrel.startswith("repos/") and os.path.isfile(os.path.join(docs_dir, rrel))


def test_ensure_document_idempotent_and_failsafe(conn, sample_reports, tmp_path, monkeypatch):
    monkeypatch.setattr(docs, "_fetch", lambda url: None)  # every download fails
    ingest.ingest_reports(sample_reports, conn)
    docs_dir = str(tmp_path / "documents")
    paper = conn.execute("SELECT * FROM corpus_item WHERE kind='paper' LIMIT 1").fetchone()
    rel = docs.ensure_document(paper, docs_dir, conn)  # must not raise
    assert rel is not None  # falls back to abstract.txt
    again = conn.execute("SELECT * FROM corpus_item WHERE id=?", (paper["id"],)).fetchone()
    assert docs.ensure_document(again, docs_dir, conn) == rel  # idempotent


def test_save_and_read_markdown(conn, sample_reports, tmp_path):
    ingest.ingest_reports(sample_reports, conn)
    docs_dir = str(tmp_path / "documents")
    paper = conn.execute("SELECT * FROM corpus_item WHERE kind='paper' LIMIT 1").fetchone()
    rel = docs.save_markdown(paper, "# Title\n\nBody.", docs_dir)
    assert rel.startswith("papers/") and rel.endswith("article.md")
    assert os.path.isfile(os.path.join(docs_dir, rel))
    # read_markdown follows markdown_path on the row.
    conn.execute("UPDATE corpus_item SET markdown_path=? WHERE id=?", (rel, paper["id"]))
    conn.commit()
    paper = conn.execute("SELECT * FROM corpus_item WHERE id=?", (paper["id"],)).fetchone()
    assert docs.read_markdown(paper, docs_dir) == "# Title\n\nBody."
    # Overwrite is idempotent on the path and replaces the content.
    rel2 = docs.save_markdown(paper, "# New", docs_dir)
    assert rel2 == rel
    assert docs.read_markdown(paper, docs_dir) == "# New"
    # No attachment -> None.
    repo = conn.execute("SELECT * FROM corpus_item WHERE kind='repo' LIMIT 1").fetchone()
    assert docs.read_markdown(repo, docs_dir) is None


def test_auto_markdown_converts_and_caches(conn, sample_reports, tmp_path, monkeypatch):
    monkeypatch.setattr(docs, "_fetch", lambda url: b"%PDF-1.4 fake bytes")
    text = _install_fake_markitdown(monkeypatch, text="# Auto\n\nFrom the original.")
    ingest.ingest_reports(sample_reports, conn)
    docs_dir = str(tmp_path / "documents")
    paper = conn.execute(
        "SELECT * FROM corpus_item WHERE kind='paper' AND id="
        "(SELECT id FROM corpus_item WHERE kind='paper' ORDER BY id LIMIT 1)").fetchone()
    out = docs.auto_markdown(paper, docs_dir, conn)
    assert out == text
    # Cached on disk as article.auto.md and re-readable without re-converting.
    paper = conn.execute("SELECT * FROM corpus_item WHERE id=?", (paper["id"],)).fetchone()
    assert docs.read_auto_markdown(paper, docs_dir) == text
    assert docs.has_convertible_source(paper, docs_dir) is True


def test_auto_markdown_unavailable_returns_none(conn, sample_reports, tmp_path, monkeypatch):
    monkeypatch.setattr(docs, "_fetch", lambda url: b"FAKEDOC")
    # markitdown missing -> ImportError -> graceful None.
    monkeypatch.setitem(sys.modules, "markitdown", None)
    ingest.ingest_reports(sample_reports, conn)
    docs_dir = str(tmp_path / "documents")
    paper = conn.execute("SELECT * FROM corpus_item WHERE kind='paper' ORDER BY id LIMIT 1").fetchone()
    assert docs.auto_markdown(paper, docs_dir, conn) is None


def test_auto_markdown_no_source_document_returns_none(conn, sample_reports, tmp_path, monkeypatch):
    # A repo's stored doc is README.md, not a convertible source.* original.
    monkeypatch.setattr(docs, "_fetch", lambda url: b"FAKEDOC")
    _install_fake_markitdown(monkeypatch)
    ingest.ingest_reports(sample_reports, conn)
    docs_dir = str(tmp_path / "documents")
    repo = conn.execute("SELECT * FROM corpus_item WHERE kind='repo' LIMIT 1").fetchone()
    assert docs.auto_markdown(repo, docs_dir, conn) is None
    repo = conn.execute("SELECT * FROM corpus_item WHERE id=?", (repo["id"],)).fetchone()
    assert docs.has_convertible_source(repo, docs_dir) is False


def _insert_paper(conn, external_id, raw, url=None):
    cur = conn.execute(
        "INSERT INTO corpus_item (kind, external_id, title, url, abstract, "
        "ingested_at, raw_json) VALUES ('paper', ?, ?, ?, ?, ?, ?)",
        (external_id, "A Paper", url, "An abstract.", db.utcnow(), json.dumps(raw)))
    conn.commit()
    return conn.execute(
        "SELECT * FROM corpus_item WHERE id=?", (cur.lastrowid,)).fetchone()


@pytest.mark.parametrize("raw,url", [
    # explicit arxiv_id, no pdf_url
    ({"arxiv_id": "2601.12345", "pdf_url": None}, None),
    # arxiv abs url only (version suffix preserved)
    ({"pdf_url": None, "abs_url": "https://arxiv.org/abs/2601.12345"}, None),
    # arxiv abs url carried on the item url
    ({"pdf_url": None}, "https://arxiv.org/abs/2601.12345"),
    # DataCite arXiv DOI (case-insensitive)
    ({"pdf_url": None, "doi": "10.48550/arXiv.2601.12345"}, None),
])
def test_store_paper_fetches_derived_arxiv_pdf(conn, tmp_path, monkeypatch, raw, url):
    requested = []

    def fake_fetch(u):
        requested.append(u)
        return b"%PDF-1.4 fake bytes" if u == "https://arxiv.org/pdf/2601.12345" else None

    monkeypatch.setattr(docs, "_fetch", fake_fetch)
    paper = _insert_paper(conn, "2601.12345", raw, url=url)
    docs_dir = str(tmp_path / "documents")

    doc_rel = docs._store_paper(paper, raw, docs_dir)

    assert "https://arxiv.org/pdf/2601.12345" in requested
    assert os.path.basename(doc_rel) == "source.pdf"
    assert os.path.isfile(os.path.join(docs_dir, doc_rel))


def test_store_paper_prefers_arxiv_html(conn, tmp_path, monkeypatch):
    # arXiv items must fetch the clean full-text HTML (arxiv.org/html/<id>) FIRST
    # and use it as the source, never falling back to the watermarked PDF.
    requested = []

    def fake_fetch(u):
        requested.append(u)
        return b"<html><body>Clean text</body></html>" if "arxiv.org/html/" in u else None

    monkeypatch.setattr(docs, "_fetch", fake_fetch)
    raw = {"arxiv_id": "2606.19659", "pdf_url": "https://arxiv.org/pdf/2606.19659"}
    paper = _insert_paper(conn, "2606.19659", raw)
    docs_dir = str(tmp_path / "documents")

    doc_rel = docs._store_paper(paper, raw, docs_dir)

    assert requested[0] == "https://arxiv.org/html/2606.19659"  # HTML requested first
    assert os.path.basename(doc_rel) == "source.html"
    assert os.path.isfile(os.path.join(docs_dir, doc_rel))
    assert not any("arxiv.org/pdf" in u for u in requested)  # never used the PDF


def test_ensure_document_upgrades_pdf_to_html_and_drops_stale_conversion(
        conn, tmp_path, monkeypatch):
    # An already-opened arXiv item stuck on the watermarked PDF self-heals to the
    # clean HTML when reopened, and the stale article.auto.md is dropped so the
    # next read regenerates it from the new source.
    raw = {"arxiv_id": "2606.19659", "pdf_url": "https://arxiv.org/pdf/2606.19659"}
    paper = _insert_paper(conn, "2606.19659", raw)
    docs_dir = str(tmp_path / "documents")

    # First open: only the PDF is reachable -> source.pdf, plus a cached auto.md.
    monkeypatch.setattr(docs, "_fetch",
                        lambda u: b"%PDF-1.4 fake" if "arxiv.org/pdf/" in u else None)
    rel = docs.ensure_document(paper, docs_dir, conn)
    assert os.path.basename(rel) == "source.pdf"
    auto_path = os.path.join(docs_dir, os.path.dirname(rel), "article.auto.md")
    os.makedirs(os.path.dirname(auto_path), exist_ok=True)
    with open(auto_path, "w", encoding="utf-8") as fh:
        fh.write("STALE pdf-derived conversion")

    # Reopen: HTML is now reachable -> upgrades to source.html, stale auto dropped.
    monkeypatch.setattr(docs, "_fetch",
                        lambda u: b"<html>clean</html>" if "arxiv.org/html/" in u else None)
    paper = conn.execute("SELECT * FROM corpus_item WHERE id=?", (paper["id"],)).fetchone()
    rel2 = docs.ensure_document(paper, docs_dir, conn)
    assert os.path.basename(rel2) == "source.html"
    assert not os.path.isfile(auto_path)  # stale conversion removed -> will regenerate


def test_store_paper_non_arxiv_keeps_existing_behavior(conn, tmp_path, monkeypatch):
    # No arxiv signal anywhere -> we never invent an arxiv.org/pdf URL.
    requested = []
    monkeypatch.setattr(docs, "_fetch", lambda u: requested.append(u) or b"<html/>")
    raw = {"pdf_url": None, "abs_url": "https://example.org/papers/42"}
    paper = _insert_paper(conn, "ex-42", raw, url="https://example.org/papers/42")
    docs_dir = str(tmp_path / "documents")

    doc_rel = docs._store_paper(paper, raw, docs_dir)

    assert not any("arxiv.org/pdf" in u for u in requested)
    assert os.path.basename(doc_rel) == "source.html"


def test_auto_markdown_absolutizes_relative_arxiv_images(
        conn, tmp_path, monkeypatch):
    # arXiv HTML figures are relative to arxiv.org/html/<id>/ (e.g. x1.png), so a
    # converted ![](x1.png) would 404 once rendered on another host. The cached
    # conversion must store the ABSOLUTE arxiv.org/html/<id>/x1.png instead.
    monkeypatch.setattr(docs, "_fetch",
                        lambda u: b"<html>clean</html>" if "arxiv.org/html/" in u else None)
    _install_fake_markitdown(
        monkeypatch,
        text=("# Paper\n\n![Refer to caption](2606.19659v1/x1.png)\n\n"
              "See [the abs](/abs/2606.19659v1) and [home](https://example.org/x)."))
    raw = {"arxiv_id": "2606.19659"}
    paper = _insert_paper(conn, "2606.19659", raw)
    docs_dir = str(tmp_path / "documents")

    out = docs.auto_markdown(paper, docs_dir, conn)

    assert "![Refer to caption](https://arxiv.org/html/2606.19659v1/x1.png)" in out
    # Relative inline links are absolutized too; absolute links are left intact.
    assert "[the abs](https://arxiv.org/abs/2606.19659v1)" in out
    assert "[home](https://example.org/x)" in out
    # The absolute URLs are what got cached on disk, not the relative originals.
    cached = docs.read_auto_markdown(paper, docs_dir)
    assert "https://arxiv.org/html/2606.19659v1/x1.png" in cached
    assert "(2606.19659v1/x1.png)" not in cached


def test_absolutize_md_urls_skips_absolute_and_anchor_targets():
    base = "https://arxiv.org/html/2606.19659"
    src = ("![a](x1.png) [b](#sec) [c](https://h/x) ![d](data:image/png;base64,AAA) "
           "[e](//cdn/x.js)")
    out = docs._absolutize_md_urls(src, base)
    assert "![a](https://arxiv.org/html/2606.19659v1/x1.png)" not in out  # base has no v1
    assert "![a](https://arxiv.org/html/x1.png)" in out
    assert "[b](#sec)" in out
    assert "[c](https://h/x)" in out
    assert "![d](data:image/png;base64,AAA)" in out
    assert "[e](//cdn/x.js)" in out


def test_auto_markdown_is_stale_flags_legacy_relative_images(conn, tmp_path):
    # A cached conversion holding relative image URLs (and a known arXiv base) is
    # stale and must regenerate; repos and absolute-only content are never stale.
    raw = {"arxiv_id": "2606.19659"}
    paper = _insert_paper(conn, "2606.19659", raw)
    assert docs.auto_markdown_is_stale(paper, "![x](2606.19659v1/x1.png)") is True
    assert docs.auto_markdown_is_stale(
        paper, "![x](https://arxiv.org/html/2606.19659v1/x1.png)") is False
    repo = conn.execute(
        "INSERT INTO corpus_item (kind, external_id, title, ingested_at, raw_json) "
        "VALUES ('repo', 'o__n', 'R', ?, '{}')", (db.utcnow(),))
    conn.commit()
    repo = conn.execute("SELECT * FROM corpus_item WHERE kind='repo' LIMIT 1").fetchone()
    assert docs.auto_markdown_is_stale(repo, "![x](rel.png)") is False


# --------------------------------------------------------------------------
# feed ordering
# --------------------------------------------------------------------------
def test_feed_ordering(conn, sample_reports):
    ingest.ingest_reports(sample_reports, conn)
    rows = conn.execute(
        "SELECT title, signal FROM corpus_item ORDER BY signal DESC, id DESC").fetchall()
    signals = [r["signal"] for r in rows]
    assert signals == sorted(signals, reverse=True)


# --------------------------------------------------------------------------
# ai (fake opencode)
# --------------------------------------------------------------------------
def test_list_models_parsing():
    out = ai.list_models()
    provs = {g["provider"] for g in out["providers"]}
    assert "openai" in provs and "anthropic" in provs
    openai = next(g for g in out["providers"] if g["provider"] == "openai")
    assert any(m["id"] == "openai/gpt-fake-mini" for m in openai["models"])


def test_summarize_item():
    out = ai.summarize_item({"kind": "paper", "title": "T", "abstract": "A"}, "openai/gpt-fake")
    assert isinstance(out["summary"], list) and 1 <= len(out["summary"]) <= 4
    assert "mixture-of-experts" in out["terms"]


def test_explain_modes():
    e = ai.explain("context window", "explain", {"title": "T"}, "openai/gpt-fake")
    assert e["lead"] and e["body"] and e.get("analogy")
    s = ai.explain("context window", "summarize", {"title": "T"}, "openai/gpt-fake")
    assert "analogy" not in s


def test_extract_concepts_clear_and_vague():
    out = ai.extract_concepts("Mixture of Experts", {"title": "T"}, "openai/gpt-fake")
    assert out["concepts"] == ["Mixture of Experts"]
    assert out["question"] is None
    vague = ai.extract_concepts("this vague thing", {"title": "T"}, "openai/gpt-fake")
    assert vague["concepts"] == []
    assert vague["question"]


def test_suggest_links():
    others = [{"id": 3, "label": "Router"}, {"id": 7, "label": "Tokens"}]
    out = ai.suggest_links({"id": 1, "label": "MoE"}, others, "openai/gpt-fake")
    assert out == [3]


# --------------------------------------------------------------------------
# map
# --------------------------------------------------------------------------
def _save_entry(conn, term, span, item_id=None):
    cur = conn.execute(
        "INSERT INTO kb_entry (term, span_text, item_id, mode, model, lead, body, "
        "analogy, tag, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (term, span, item_id, "explain", "m", term + " lead", term + " body",
         None, "note", db.utcnow()))
    eid = cur.lastrowid
    conn.execute("INSERT INTO kb_message (kb_entry_id, role, content, created_at) "
                 "VALUES (?,?,?,?)", (eid, "assistant", term + " body", db.utcnow()))
    db.reindex_entry(conn, eid)
    map_store.ensure_concept_for_entry(conn, eid)
    conn.commit()
    return eid


def test_map_nodes_edges_position(conn):
    e1 = _save_entry(conn, "Router", "router")
    e2 = _save_entry(conn, "Experts", "experts")
    m = map_store.get_map(conn)
    assert len(m["nodes"]) == 2
    c1, c2 = m["nodes"][0]["id"], m["nodes"][1]["id"]
    eid = map_store.add_edge(conn, c1, c2, "manual")
    assert eid is not None
    # dedupe / reverse
    assert map_store.add_edge(conn, c2, c1, "manual") == eid
    map_store.set_position(conn, c1, 12.5, 80.0)
    node = next(n for n in map_store.get_map(conn)["nodes"] if n["id"] == c1)
    assert round(node["x"], 1) == 12.5 and round(node["y"], 1) == 80.0
    map_store.delete_edge(conn, eid)
    assert map_store.get_map(conn)["edges"] == []


def test_map_ai_links(conn):
    _save_entry(conn, "Router", "router")
    _save_entry(conn, "Experts", "experts")
    res = map_store.ai_links(conn, "openai/gpt-fake")
    assert res["added"] >= 1
    assert any(e["source"] == "ai" for e in map_store.get_map(conn)["edges"])


# --------------------------------------------------------------------------
# refresh (tracker stubbed)
# --------------------------------------------------------------------------
def test_run_refresh_state(tmp_path, monkeypatch, sample_reports):
    refresh.reset()
    db_path = str(tmp_path / "g.db")

    def fake_tracker(kind, days, reports_dir):
        # Pretend the pipeline wrote the sample sidecars into reports_dir.
        import shutil
        for name in os.listdir(sample_reports):
            shutil.copy(os.path.join(sample_reports, name), os.path.join(reports_dir, name))

    monkeypatch.setattr(refresh, "_run_tracker", fake_tracker)
    rdir = str(tmp_path / "out_reports")
    os.makedirs(rdir)

    def factory():
        c = db.connect(db_path)
        db.bootstrap(c)
        return c

    state = refresh.run_refresh("papers", 7, rdir, factory)
    assert state["status"] == "running"
    refresh.join(10)
    final = refresh.status()
    assert final["status"] == "done"
    assert final["counts"]["new"] >= 1


def test_run_refresh_ingests_despite_failing_tracker(tmp_path, monkeypatch, sample_reports):
    """A failing tracker must not stop the worker from ingesting the others.

    The repos tracker raises (GitHub rate-limit / no token in practice) while
    the papers tracker writes its sidecar. The worker must still ingest the
    papers, end status=done, and surface the repos error in the message.
    """
    refresh.reset()
    db_path = str(tmp_path / "g.db")

    def fake_tracker(kind, days, reports_dir):
        if kind == "repos":
            raise RuntimeError("GitHub rate limit exceeded")
        # papers: copy only the labpapers sidecar into the reports dir.
        import shutil
        for name in os.listdir(sample_reports):
            if name.startswith("labpapers_"):
                shutil.copy(os.path.join(sample_reports, name),
                            os.path.join(reports_dir, name))

    monkeypatch.setattr(refresh, "_run_tracker", fake_tracker)
    rdir = str(tmp_path / "out_reports")
    os.makedirs(rdir)

    def factory():
        c = db.connect(db_path)
        db.bootstrap(c)
        return c

    # kind="all" so both trackers run; repos fails, papers succeeds.
    refresh.run_refresh(None, 7, rdir, factory)
    refresh.join(10)
    final = refresh.status()

    assert final["status"] == "done"
    assert "repos failed" in final["message"]
    assert "GitHub rate limit" in final["message"]

    # The papers were actually ingested into corpus_item.
    c = db.connect(db_path)
    papers = c.execute("SELECT COUNT(*) c FROM corpus_item WHERE kind='paper'").fetchone()["c"]
    repos = c.execute("SELECT COUNT(*) c FROM corpus_item WHERE kind='repo'").fetchone()["c"]
    c.close()
    assert papers >= 1
    assert repos == 0


# --------------------------------------------------------------------------
# end-to-end HTTP through the server handler
# --------------------------------------------------------------------------
@pytest.fixture
def live_server(tmp_path, monkeypatch, sample_reports):
    monkeypatch.setattr(docs, "_fetch", lambda url: b"FAKEDOC")
    from university import server
    db_path = str(tmp_path / "g.db")
    docs_dir = str(tmp_path / "documents")
    ctx = server.AppContext(db_path, sample_reports, docs_dir, default_model="openai/gpt-fake")
    auth.add_user(ctx.conn, "maya", "pw123")
    ingest.ingest_reports(sample_reports, ctx.conn)
    httpd = server.make_server("127.0.0.1", 0, ctx)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    time.sleep(0.1)
    yield "http://127.0.0.1:{}".format(port)
    httpd.shutdown()
    httpd.server_close()


def _http(method, url, body=None, cookie=None):
    import urllib.request
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        resp = urllib.request.urlopen(req)
        set_cookie = resp.headers.get("Set-Cookie")
        return resp.status, json.loads(resp.read().decode()), set_cookie
    except urllib.error.HTTPError as e:
        return e.code, None, None


def _http_raw(method, url, data=None, content_type=None, cookie=None):
    import urllib.request
    req = urllib.request.Request(url, data=data, method=method)
    if content_type:
        req.add_header("Content-Type", content_type)
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        resp = urllib.request.urlopen(req)
        return resp.status, resp.read(), resp.headers.get("Content-Type")
    except urllib.error.HTTPError as e:
        return e.code, None, None


def _multipart_doc(data, filename, content_type=None):
    """Build a multipart/form-data body with one binary file part."""
    boundary = "----gymdocboundary"
    parts = [
        "--{}\r\n".format(boundary).encode(),
        ('Content-Disposition: form-data; name="file"; '
         'filename="{}"\r\n'.format(filename)).encode(),
    ]
    if content_type:
        parts.append("Content-Type: {}\r\n\r\n".format(content_type).encode())
    else:
        parts.append(b"\r\n")
    parts.append(data)
    parts.append(b"\r\n")
    parts.append("--{}--\r\n".format(boundary).encode())
    return b"".join(parts), "multipart/form-data; boundary={}".format(boundary)


def test_markdown_attach_and_fetch(live_server, monkeypatch):
    base = live_server
    # gated without a cookie
    status, _, _ = _http_raw("GET", base + "/api/item/1/markdown")
    assert status == 401
    body, ctype = _multipart_doc(b"# x", "a.md", "text/markdown")
    status, _, _ = _http_raw("POST", base + "/api/item/1/document",
                             data=body, content_type=ctype)
    assert status == 401

    status, _, setck = _http("POST", base + "/api/login",
                             {"username": "maya", "password": "pw123"})
    cookie = setck.split(";")[0]
    status, feed, _ = _http("GET", base + "/api/feed", cookie=cookie)
    paper_ids = [i["id"] for i in feed["items"] if i["kind"] == "paper"]
    repo_id = next(i["id"] for i in feed["items"] if i["kind"] == "repo")
    item_id = paper_ids[0]

    # No uploaded source and auto-conversion forced unavailable -> 404. Pinning
    # the auto path keeps this assertion deterministic whether or not markitdown
    # is installed in the environment (it would otherwise auto-convert and 200).
    monkeypatch.setattr(docs, "auto_markdown", lambda *a, **k: None)
    status, item, _ = _http("GET", base + "/api/item/{}".format(item_id), cookie=cookie)
    assert status == 200 and item["has_markdown"] is False
    status, _, _ = _http_raw("GET", base + "/api/item/{}/markdown".format(item_id),
                             cookie=cookie)
    assert status == 404

    # A supporting markdown upload is stored as the source document; the cached
    # auto conversion is dropped so the reader regenerates from the new file.
    md = b"# Heading\n\nHello **world**."
    body, ctype = _multipart_doc(md, "note.md", "text/markdown")
    status, _, _ = _http_raw("POST", base + "/api/item/{}/document".format(item_id),
                             data=body, content_type=ctype, cookie=cookie)
    assert status == 200
    status, item, _ = _http("GET", base + "/api/item/{}".format(item_id), cookie=cookie)
    assert item["doc_uploaded"] is True
    assert os.path.basename(item["doc_path"]) == "note.md"
    # The markdown GET converts the uploaded note via markitdown (stubbed).
    monkeypatch.setattr(
        docs, "auto_markdown",
        lambda item, docs_dir, conn, _md=md: _md.decode("utf-8"))
    status, body, mctype = _http_raw(
        "GET", base + "/api/item/{}/markdown".format(item_id), cookie=cookie)
    assert status == 200 and body.decode("utf-8") == md.decode("utf-8")
    assert "text/markdown" in mctype

    # When auto-conversion IS available and NO upload exists, the converted text
    # is served and reports markdown_source=auto.
    auto_id = paper_ids[1]
    monkeypatch.setattr(docs, "auto_markdown", lambda *a, **k: "# Auto\n\nConverted body.")
    status, body, _ = _http_raw(
        "GET", base + "/api/item/{}/markdown".format(auto_id), cookie=cookie)
    assert status == 200 and body.decode("utf-8") == "# Auto\n\nConverted body."
    status, item, _ = _http("GET", base + "/api/item/{}".format(auto_id), cookie=cookie)
    assert item["markdown_source"] == "auto"


def test_upload_supporting_pdf_replaces_source_and_regenerates(
        live_server, tmp_path, monkeypatch):
    base = live_server
    _install_fake_markitdown(monkeypatch, text="# From uploaded PDF\n\nConverted.")
    status, _, setck = _http("POST", base + "/api/login",
                             {"username": "maya", "password": "pw123"})
    cookie = setck.split(";")[0]
    status, feed, _ = _http("GET", base + "/api/feed", cookie=cookie)
    paper = next(i for i in feed["items"] if i["kind"] == "paper")
    pid = paper["id"]

    # Upload a supporting PDF for the tracker paper.
    pdf = b"%PDF-1.4\nfake supporting pdf\n%%EOF\n"
    body, ctype = _multipart_doc(pdf, "supplement.pdf", "application/pdf")
    status, raw, _ = _http_raw("POST", base + "/api/item/{}/document".format(pid),
                               data=body, content_type=ctype, cookie=cookie)
    out = json.loads(raw.decode())
    assert status == 200 and out["ok"] is True
    assert out["doc_path"].endswith("source.pdf")

    # The item is flagged doc_uploaded and its doc_path now points at the upload.
    status, item, _ = _http("GET", base + "/api/item/{}".format(pid), cookie=cookie)
    assert item["doc_uploaded"] is True
    assert os.path.basename(item["doc_path"]) == "source.pdf"

    # The stored file is the uploaded bytes (authoritative — never refetched).
    c = db.connect(str(tmp_path / "g.db"))
    row = c.execute("SELECT doc_path FROM corpus_item WHERE id=?", (pid,)).fetchone()
    c.close()
    pdf_path = tmp_path / "documents" / row["doc_path"]
    assert pdf_path.read_bytes() == pdf

    # The markdown GET auto-converts the uploaded PDF (stubbed markitdown).
    status, md, _ = _http_raw(
        "GET", base + "/api/item/{}/markdown".format(pid), cookie=cookie)
    assert status == 200 and md.decode("utf-8") == "# From uploaded PDF\n\nConverted."

    # Reopening the item never refetches/replaces the uploaded doc: the stored
    # bytes are unchanged even when _fetch would return something different.
    monkeypatch.setattr(docs, "_fetch", lambda url: b"OTHER")
    status, item, _ = _http("GET", base + "/api/item/{}".format(pid), cookie=cookie)
    assert os.path.basename(item["doc_path"]) == "source.pdf"
    assert pdf_path.read_bytes() == pdf  # upload is authoritative


def test_markdown_auto_then_user_override(live_server, monkeypatch):
    base = live_server
    _install_fake_markitdown(monkeypatch, text="# Auto\n\nFrom the original.")
    status, _, setck = _http("POST", base + "/api/login",
                             {"username": "maya", "password": "pw123"})
    cookie = setck.split(";")[0]
    status, feed, _ = _http("GET", base + "/api/feed", cookie=cookie)
    paper = next(i for i in feed["items"] if i["kind"] == "paper")
    repo = next(i for i in feed["items"] if i["kind"] == "repo")
    pid = paper["id"]

    # Item endpoint advertises a convertible source but has NOT converted yet.
    status, item, _ = _http("GET", base + "/api/item/{}".format(pid), cookie=cookie)
    assert status == 200
    assert item["has_markdown"] is False
    assert item["markdown_available"] is True
    assert item["markdown_source"] is None
    assert item["doc_uploaded"] is False

    # First markdown GET triggers the lazy auto conversion and caches it.
    status, body, _ = _http_raw(
        "GET", base + "/api/item/{}/markdown".format(pid), cookie=cookie)
    assert status == 200 and body.decode("utf-8") == "# Auto\n\nFrom the original."
    status, item, _ = _http("GET", base + "/api/item/{}".format(pid), cookie=cookie)
    assert item["markdown_source"] == "auto"

    # A supporting PDF upload becomes the new authoritative source; the cached
    # auto conversion is dropped so the reader regenerates from the upload.
    _install_fake_markitdown(monkeypatch, text="# Mine\n\nUploaded.")
    pdf = b"%PDF-1.4\nupload\n%%EOF\n"
    body, ctype = _multipart_doc(pdf, "mine.pdf", "application/pdf")
    status, _, _ = _http_raw("POST", base + "/api/item/{}/document".format(pid),
                             data=body, content_type=ctype, cookie=cookie)
    assert status == 200
    status, item, _ = _http("GET", base + "/api/item/{}".format(pid), cookie=cookie)
    assert item["doc_uploaded"] is True
    assert os.path.basename(item["doc_path"]) == "source.pdf"
    status, md, _ = _http_raw(
        "GET", base + "/api/item/{}/markdown".format(pid), cookie=cookie)
    assert md.decode("utf-8") == "# Mine\n\nUploaded."  # regenerated from upload

    # A repo serves its stored README.md as markdown (already markdown, no
    # markitdown conversion). The fake fetch returns README bytes on ingest.
    status, item, _ = _http("GET", base + "/api/item/{}".format(repo["id"]), cookie=cookie)
    assert item["markdown_available"] is True
    status, body, _ = _http_raw(
        "GET", base + "/api/item/{}/markdown".format(repo["id"]), cookie=cookie)
    assert status == 200 and body.decode("utf-8") == "FAKEDOC"


def test_feed_facets_endpoint(live_server):
    base = live_server
    status, _, setck = _http("POST", base + "/api/login",
                             {"username": "maya", "password": "pw123"})
    cookie = setck.split(";")[0]

    # Papers: the seeded sidecar has two papers in google + openai labs.
    status, facets, _ = _http("GET", base + "/api/feed/facets?kind=paper", cookie=cookie)
    assert status == 200
    companies = {v["value"] for v in facets["companies"]["values"]}
    assert {"google", "openai"}.issubset(companies)
    assert "publications" in facets and "authors" in facets

    # Repos: one seeded repo, owner acme, language Python.
    status, facets, _ = _http("GET", base + "/api/feed/facets?kind=repo", cookie=cookie)
    assert status == 200
    assert {v["value"] for v in facets["companies"]["values"]} == {"acme"}
    assert {v["value"] for v in facets["languages"]["values"]} == {"Python"}


def test_feed_kind_search_and_sort(live_server):
    base = live_server
    status, _, setck = _http("POST", base + "/api/login",
                             {"username": "maya", "password": "pw123"})
    cookie = setck.split(";")[0]
    # kind filter returns only that kind.
    status, res, _ = _http("GET", base + "/api/feed?kind=repo", cookie=cookie)
    assert status == 200 and all(i["kind"] == "repo" for i in res["items"])
    # search narrows papers by title word.
    status, res, _ = _http("GET", base + "/api/feed?kind=paper&q=mixture", cookie=cookie)
    assert status == 200 and len(res["items"]) == 1
    assert "Mixture" in res["items"][0]["title"]
    # rating sort orders papers by signal descending.
    status, res, _ = _http("GET", base + "/api/feed?kind=paper&sort=rating", cookie=cookie)
    signals = [i["signal"] for i in res["items"]]
    assert signals == sorted(signals, reverse=True)
    # paper item dict surfaces authors / company / publication.
    paper = res["items"][0]
    assert "authors" in paper and "company" in paper and "publication" in paper


def test_add_item_creates_and_dedupes(live_server):
    base = live_server
    # gated without a cookie
    status, _, _ = _http("POST", base + "/api/items", {"url": "https://example.org/a"})
    assert status == 401
    status, _, setck = _http("POST", base + "/api/login",
                             {"username": "maya", "password": "pw123"})
    cookie = setck.split(";")[0]

    # create with an explicit title
    status, res, _ = _http("POST", base + "/api/items",
                           {"url": "https://example.org/a", "title": "My Article"}, cookie=cookie)
    assert status == 200 and res["id"]
    item_id = res["id"]

    # appears in the Added feed, flagged added_by_user, source "Added by you"
    status, feed, _ = _http("GET", base + "/api/feed?added=1", cookie=cookie)
    added_item = next(i for i in feed["items"] if i["id"] == item_id)
    assert added_item["added_by_user"] is True
    assert added_item["title"] == "My Article"
    assert added_item["source"] == "Added by you"

    # re-adding the same url UPDATES the same row (dedupe), never duplicates
    status, res2, _ = _http("POST", base + "/api/items",
                            {"url": "https://example.org/a", "title": "Renamed"}, cookie=cookie)
    assert status == 200 and res2["id"] == item_id
    status, feed, _ = _http("GET", base + "/api/feed?added=1", cookie=cookie)
    assert [i["id"] for i in feed["items"]].count(item_id) == 1
    assert next(i for i in feed["items"] if i["id"] == item_id)["title"] == "Renamed"

    # url is required
    status, _, _ = _http("POST", base + "/api/items", {"title": "no url"}, cookie=cookie)
    assert status == 400


def test_add_item_falls_back_to_host_title(live_server):
    base = live_server
    status, _, setck = _http("POST", base + "/api/login",
                             {"username": "maya", "password": "pw123"})
    cookie = setck.split(";")[0]
    # The fake _fetch returns FAKEDOC (no <title>) -> title falls back to the host.
    status, res, _ = _http("POST", base + "/api/items",
                           {"url": "https://blog.example.com/post/42"}, cookie=cookie)
    assert status == 200
    status, feed, _ = _http("GET", base + "/api/feed?added=1", cookie=cookie)
    item = next(i for i in feed["items"] if i["id"] == res["id"])
    assert item["title"] == "blog.example.com"


def test_added_items_excluded_from_tracker_feed(live_server):
    base = live_server
    status, _, setck = _http("POST", base + "/api/login",
                             {"username": "maya", "password": "pw123"})
    cookie = setck.split(";")[0]
    status, res, _ = _http("POST", base + "/api/items",
                           {"url": "https://example.org/added-paper", "title": "Added Paper"},
                           cookie=cookie)
    aid = res["id"]

    # The tracker Papers feed (kind=paper) excludes user-added items.
    status, papers, _ = _http("GET", base + "/api/feed?kind=paper", cookie=cookie)
    assert aid not in [i["id"] for i in papers["items"]]
    assert all(i["added_by_user"] is False for i in papers["items"])

    # The default feed (no kind) excludes them too.
    status, allfeed, _ = _http("GET", base + "/api/feed", cookie=cookie)
    assert aid not in [i["id"] for i in allfeed["items"]]

    # The Added feed lists ONLY user-added items.
    status, added, _ = _http("GET", base + "/api/feed?added=1", cookie=cookie)
    ids = [i["id"] for i in added["items"]]
    assert aid in ids
    assert all(i["added_by_user"] is True for i in added["items"])


def test_github_repo_url_parsing():
    from university import server
    # Various GitHub repo URL shapes all resolve to (owner, name).
    for url in [
        "https://github.com/acme/widget",
        "http://github.com/acme/widget",
        "github.com/acme/widget",
        "https://www.github.com/acme/widget/",
        "https://github.com/acme/widget.git",
        "https://github.com/acme/widget/tree/main/src",
        "https://github.com/acme/widget?tab=readme-ov-file",
    ]:
        assert server._github_repo(url) == ("acme", "widget"), url
    # A dotted repo name survives (only a trailing .git is stripped).
    assert server._github_repo("https://github.com/acme/socket.io") == ("acme", "socket.io")
    # Non-repo URLs (other host, or a bare profile) are not repos -> stay papers.
    assert server._github_repo("https://example.org/acme/widget") is None
    assert server._github_repo("https://github.com/acme") is None
    assert server._github_repo("https://arxiv.org/abs/2601.00001") is None


def test_add_item_github_repo_creates_repo(live_server, tmp_path):
    base = live_server
    status, _, setck = _http("POST", base + "/api/login",
                             {"username": "maya", "password": "pw123"})
    cookie = setck.split(";")[0]

    # A GitHub URL (trailing slash) becomes a kind=repo added item.
    status, res, _ = _http("POST", base + "/api/items",
                           {"url": "https://github.com/acme/widget/"}, cookie=cookie)
    assert status == 200 and res["kind"] == "repo"
    rid = res["id"]

    # owner/name/full_name/html_url are parsed into raw_json (read straight from
    # the same on-disk DB the live server uses, under the shared tmp_path).
    c = db.connect(str(tmp_path / "g.db"))
    row = c.execute("SELECT kind, external_id, source, added_by_user, raw_json, title "
                    "FROM corpus_item WHERE id=?", (rid,)).fetchone()
    c.close()
    assert row["kind"] == "repo"
    assert row["external_id"] == "acme/widget"
    assert row["source"] == "Added by you"
    assert row["added_by_user"] == 1
    assert row["title"] == "acme/widget"  # falls back to full_name
    raw = json.loads(row["raw_json"])
    assert raw["owner"] == "acme" and raw["name"] == "widget"
    assert raw["full_name"] == "acme/widget"
    assert raw["html_url"] == "https://github.com/acme/widget"

    # Re-adding the same repo (different URL shape) dedupes onto the same row.
    status, res2, _ = _http("POST", base + "/api/items",
                            {"url": "https://github.com/acme/widget.git",
                             "title": "Widget"}, cookie=cookie)
    assert status == 200 and res2["id"] == rid

    # Also add a paper so the Added feed holds BOTH kinds.
    status, pres, _ = _http("POST", base + "/api/items",
                            {"url": "https://example.org/p", "title": "Added Paper"},
                            cookie=cookie)
    pid = pres["id"]

    status, added, _ = _http("GET", base + "/api/feed?added=1", cookie=cookie)
    kinds = {i["id"]: i["kind"] for i in added["items"]}
    assert kinds.get(rid) == "repo" and kinds.get(pid) == "paper"
    assert [i["id"] for i in added["items"]].count(rid) == 1  # no duplicate

    # The added repo is EXCLUDED from the tracker Repos feed.
    status, repos, _ = _http("GET", base + "/api/feed?kind=repo", cookie=cookie)
    assert rid not in [i["id"] for i in repos["items"]]
    assert all(i["added_by_user"] is False for i in repos["items"])

    # Opening the added repo renders its README markdown (fake _fetch -> FAKEDOC).
    status, item, _ = _http("GET", base + "/api/item/{}".format(rid), cookie=cookie)
    assert status == 200 and item["markdown_available"] is True
    status, body, ctype = _http_raw(
        "GET", base + "/api/item/{}/markdown".format(rid), cookie=cookie)
    assert status == 200 and body.decode("utf-8") == "FAKEDOC"
    assert "text/markdown" in ctype


def test_delete_added_item_and_protects_tracker(live_server, tmp_path):
    base = live_server
    status, _, setck = _http("POST", base + "/api/login",
                             {"username": "maya", "password": "pw123"})
    cookie = setck.split(";")[0]

    # gated without a cookie
    status, _, _ = _http("DELETE", base + "/api/items/1")
    assert status == 401

    # A tracker item (added_by_user=0) can NEVER be removed -> 403.
    status, feed, _ = _http("GET", base + "/api/feed", cookie=cookie)
    tracker_id = feed["items"][0]["id"]
    status, _, _ = _http("DELETE", base + "/api/items/{}".format(tracker_id), cookie=cookie)
    assert status == 403
    # still present
    status, feed, _ = _http("GET", base + "/api/feed", cookie=cookie)
    assert tracker_id in [i["id"] for i in feed["items"]]

    # Add an item, then its on-disk doc folder, then DELETE it.
    status, res, _ = _http("POST", base + "/api/items",
                           {"url": "https://github.com/acme/widget"}, cookie=cookie)
    rid = res["id"]
    # Materialize the document folder on disk by opening the item.
    _http_raw("GET", base + "/api/item/{}/markdown".format(rid), cookie=cookie)
    repo_dir = tmp_path / "documents" / "repos" / "acme__widget"
    assert repo_dir.is_dir()

    status, out, _ = _http("DELETE", base + "/api/items/{}".format(rid), cookie=cookie)
    assert status == 200 and out["ok"] is True
    # Gone from the Added feed and its doc folder is removed.
    status, added, _ = _http("GET", base + "/api/feed?added=1", cookie=cookie)
    assert rid not in [i["id"] for i in added["items"]]
    assert not repo_dir.exists()

    # Deleting a now-missing id -> 404.
    status, _, _ = _http("DELETE", base + "/api/items/{}".format(rid), cookie=cookie)
    assert status == 404


def _multipart_pdf(pdf_bytes, filename="paper.pdf", title=None):
    """Build a multipart/form-data body with a binary PDF file part (+optional title)."""
    boundary = "----gympdfboundary"
    body = b"".join([
        "--{}\r\n".format(boundary).encode(),
        ('Content-Disposition: form-data; name="file"; '
         'filename="{}"\r\n'.format(filename)).encode(),
        b"Content-Type: application/pdf\r\n\r\n",
        pdf_bytes,
        b"\r\n",
    ])
    if title is not None:
        body += b"".join([
            "--{}\r\n".format(boundary).encode(),
            b'Content-Disposition: form-data; name="title"\r\n\r\n',
            title.encode(),
            b"\r\n",
        ])
    body += "--{}--\r\n".format(boundary).encode()
    return body, "multipart/form-data; boundary={}".format(boundary)


def test_upload_pdf_creates_paper_converts_dedupes_and_removes(
        live_server, tmp_path, monkeypatch):
    base = live_server
    _install_fake_markitdown(monkeypatch, text="# From PDF\n\nConverted text.")
    status, _, setck = _http("POST", base + "/api/login",
                             {"username": "maya", "password": "pw123"})
    cookie = setck.split(";")[0]

    # A multipart PDF upload (no title) creates a kind=paper added item whose
    # title falls back to the filename without its .pdf extension.
    pdf = b"%PDF-1.4\nfake pdf body bytes\n%%EOF\n"
    body, ctype = _multipart_pdf(pdf, filename="My Great Paper.pdf")
    status, raw, _ = _http_raw("POST", base + "/api/items", data=body,
                               content_type=ctype, cookie=cookie)
    assert status == 200
    res = json.loads(raw.decode())
    assert res["kind"] == "paper"
    pid = res["id"]

    # Flagged added_by_user, source "Uploaded PDF", url NULL, doc_path -> source.pdf.
    c = db.connect(str(tmp_path / "g.db"))
    row = c.execute("SELECT * FROM corpus_item WHERE id=?", (pid,)).fetchone()
    c.close()
    assert row["added_by_user"] == 1
    assert row["source"] == "Uploaded PDF"
    assert row["url"] is None
    assert row["title"] == "My Great Paper"
    assert row["external_id"].startswith("pdf:")
    assert os.path.basename(row["doc_path"]) == "source.pdf"
    pdf_path = tmp_path / "documents" / row["doc_path"]
    assert pdf_path.is_file() and pdf_path.read_bytes() == pdf

    # It shows up in the Added feed but NOT in the tracker Papers feed.
    status, added, _ = _http("GET", base + "/api/feed?added=1", cookie=cookie)
    assert pid in [i["id"] for i in added["items"]]
    status, papers, _ = _http("GET", base + "/api/feed?kind=paper", cookie=cookie)
    assert pid not in [i["id"] for i in papers["items"]]

    # The item advertises a convertible source and the markdown GET converts the
    # stored source.pdf (stub markitdown) without trying to re-fetch a URL.
    status, item, _ = _http("GET", base + "/api/item/{}".format(pid), cookie=cookie)
    assert status == 200 and item["markdown_available"] is True
    status, md, mctype = _http_raw(
        "GET", base + "/api/item/{}/markdown".format(pid), cookie=cookie)
    assert status == 200 and md.decode("utf-8") == "# From PDF\n\nConverted text."
    assert "text/markdown" in mctype
    # The stored PDF is untouched by ensure_document (url is NULL, no refetch).
    assert pdf_path.read_bytes() == pdf

    # An explicit title wins, and re-uploading the SAME bytes dedupes by content
    # hash onto the same row instead of creating a duplicate.
    body2, ctype2 = _multipart_pdf(pdf, filename="renamed.pdf", title="My Title")
    status, raw2, _ = _http_raw("POST", base + "/api/items", data=body2,
                                content_type=ctype2, cookie=cookie)
    assert status == 200
    res2 = json.loads(raw2.decode())
    assert res2["id"] == pid  # same content -> same row
    status, added, _ = _http("GET", base + "/api/feed?added=1", cookie=cookie)
    assert [i["id"] for i in added["items"]].count(pid) == 1
    assert next(i for i in added["items"] if i["id"] == pid)["title"] == "My Title"

    # Different bytes -> a distinct item.
    body3, ctype3 = _multipart_pdf(pdf + b"more", filename="other.pdf")
    status, raw3, _ = _http_raw("POST", base + "/api/items", data=body3,
                                content_type=ctype3, cookie=cookie)
    assert json.loads(raw3.decode())["id"] != pid

    # A multipart body with no file part -> 400.
    nofile = (b"------b\r\nContent-Disposition: form-data; name=\"title\"\r\n\r\n"
              b"x\r\n------b--\r\n")
    status, _, _ = _http_raw("POST", base + "/api/items", data=nofile,
                             content_type="multipart/form-data; boundary=----b",
                             cookie=cookie)
    assert status == 400

    # DELETE removes the row AND its on-disk source.pdf folder.
    doc_dir = pdf_path.parent
    assert doc_dir.is_dir()
    status, out, _ = _http("DELETE", base + "/api/items/{}".format(pid), cookie=cookie)
    assert status == 200 and out["ok"] is True
    status, added, _ = _http("GET", base + "/api/feed?added=1", cookie=cookie)
    assert pid not in [i["id"] for i in added["items"]]
    assert not doc_dir.exists()


def test_delete_added_item_keeps_kb_entry(live_server, tmp_path):
    base = live_server
    status, _, setck = _http("POST", base + "/api/login",
                             {"username": "maya", "password": "pw123"})
    cookie = setck.split(";")[0]

    status, res, _ = _http("POST", base + "/api/items",
                           {"url": "https://example.org/keepme", "title": "Keep Me"},
                           cookie=cookie)
    rid = res["id"]
    # Save a KB entry tied to the added item.
    status, ask, _ = _http("POST", base + "/api/ask", {
        "span_text": "something", "item_id": rid, "mode": "explain",
        "model": "openai/gpt-fake"}, cookie=cookie)
    status, saved, _ = _http("POST", base + "/api/kb/save", {
        "span_text": "something", "item_id": rid, "mode": "explain",
        "model": "openai/gpt-fake", "answer": ask["answer"]}, cookie=cookie)
    entry_id = saved["entry"]["id"]

    # Deleting the item leaves the KB entry intact (item_id dropped to NULL).
    status, _, _ = _http("DELETE", base + "/api/items/{}".format(rid), cookie=cookie)
    assert status == 200
    status, full, _ = _http("GET", base + "/api/kb/{}".format(entry_id), cookie=cookie)
    assert status == 200
    assert full["id"] == entry_id
    assert full["item_id"] is None
    assert full["messages"]  # content preserved


def test_repo_readme_served_as_markdown(live_server):
    base = live_server
    status, _, setck = _http("POST", base + "/api/login",
                             {"username": "maya", "password": "pw123"})
    cookie = setck.split(";")[0]
    status, feed, _ = _http("GET", base + "/api/feed?kind=repo", cookie=cookie)
    repo_id = feed["items"][0]["id"]
    # Opening the repo advertises markdown (its README), and the GET returns it.
    status, item, _ = _http("GET", base + "/api/item/{}".format(repo_id), cookie=cookie)
    assert status == 200 and item["markdown_available"] is True
    status, body, ctype = _http_raw(
        "GET", base + "/api/item/{}/markdown".format(repo_id), cookie=cookie)
    assert status == 200 and body.decode("utf-8") == "FAKEDOC"  # fake _fetch README
    assert "text/markdown" in ctype


def test_auth_gate_and_full_flow(live_server):
    base = live_server
    # gated without cookie
    status, _, _ = _http("GET", base + "/api/feed")
    assert status == 401
    # bad login
    status, _, _ = _http("POST", base + "/api/login", {"username": "maya", "password": "wrong"})
    assert status == 401
    # non-alphanumeric login rejected
    status, _, _ = _http("POST", base + "/api/login", {"username": "ma!ya", "password": "pw123"})
    assert status == 401
    # good login
    status, data, setck = _http("POST", base + "/api/login", {"username": "maya", "password": "pw123"})
    assert status == 200
    cookie = setck.split(";")[0]
    # feed works with cookie
    status, feed, _ = _http("GET", base + "/api/feed", cookie=cookie)
    assert status == 200 and len(feed["items"]) == 3
    item_id = feed["items"][0]["id"]
    # opening the item triggers ensure_document
    status, item, _ = _http("GET", base + "/api/item/{}".format(item_id), cookie=cookie)
    assert status == 200 and item["doc_path"]

    # ask -> save with full thread
    status, ask, _ = _http("POST", base + "/api/ask", {
        "span_text": "mixture-of-experts", "item_id": item_id, "mode": "explain",
        "model": "openai/gpt-fake"}, cookie=cookie)
    assert status == 200 and ask["answer"]["lead"]
    status, saved, _ = _http("POST", base + "/api/kb/save", {
        "span_text": "mixture-of-experts", "item_id": item_id, "mode": "explain",
        "model": "openai/gpt-fake", "answer": ask["answer"],
        "thread": [{"role": "user", "content": "what is the router"},
                   {"role": "assistant", "content": "the router picks experts"}]},
        cookie=cookie)
    assert status == 200
    entry_id = saved["entry"]["id"]
    assert saved["entry"]["source_doc_path"]
    assert saved["entry"]["source_url"]
    # whole thread persisted: 1 answer + 2 thread turns = 3 messages
    assert len(saved["entry"]["messages"]) == 3

    # follow-up after save appends server-side
    status, _, _ = _http("POST", base + "/api/ask", {
        "span_text": "mixture-of-experts", "item_id": item_id, "kb_entry_id": entry_id,
        "mode": "ask", "message": "and what about balancing", "model": "openai/gpt-fake"},
        cookie=cookie)
    assert status == 200
    status, full, _ = _http("GET", base + "/api/kb/{}".format(entry_id), cookie=cookie)
    assert len(full["messages"]) == 5  # 3 + (user + assistant)

    # search finds it by a word from a follow-up turn
    status, res, _ = _http("GET", base + "/api/kb/search?q=balancing", cookie=cookie)
    assert status == 200 and any(e["id"] == entry_id for e in res["entries"])
    # and a word from the original turn
    status, res, _ = _http("GET", base + "/api/kb/search?q=router", cookie=cookie)
    assert any(e["id"] == entry_id for e in res["entries"])

    # the saved term shows up as a map node
    status, mp, _ = _http("GET", base + "/api/map", cookie=cookie)
    assert len(mp["nodes"]) >= 1

    # models endpoint reflects fake opencode
    status, models, _ = _http("GET", base + "/api/models", cookie=cookie)
    assert any(g["provider"] == "openai" for g in models["providers"])

    # logout revokes
    status, _, _ = _http("POST", base + "/api/logout", {}, cookie=cookie)
    assert status == 200
    status, _, _ = _http("GET", base + "/api/feed", cookie=cookie)
    assert status == 401


# --------------------------------------------------------------------------
# retrieval (knowledge-grounded context) — pure function
# --------------------------------------------------------------------------
def test_retrieve_context_grounds_and_is_bounded(conn):
    paper = _insert_paper(conn, "rc-1", {"arxiv_id": "rc-1"}, url="http://x")
    pid = paper["id"]
    # One note tied to this article, one general note; both become concepts.
    _save_entry(conn, "Router balancing", "router balancing span", item_id=pid)
    _save_entry(conn, "Experts", "experts span")
    nodes = map_store.get_map(conn)["nodes"]
    map_store.add_edge(conn, nodes[0]["id"], nodes[1]["id"], "manual")

    item = {"id": pid, "title": "Mixture of Experts",
            "abstract": "Routing tokens to experts in MoE."}
    bundle = retrieval.retrieve_context(conn, item, "router")

    # FTS / item-tie surfaces the seeded relevant note.
    terms = [n["term"] for n in bundle["notes"]]
    assert "Router balancing" in terms
    assert "Router balancing" in bundle["grounded"]["notes"]
    # The concept map contributes both linked concepts and an edge between them.
    assert "Router balancing" in bundle["concepts"]
    assert "Experts" in bundle["concepts"]
    assert any("Router balancing -- Experts" == e or "Experts -- Router balancing" == e
               for e in bundle["edges"])
    # A short article excerpt is included for the model to chat about.
    assert "expert" in bundle["excerpt"].lower()
    # Bounded.
    assert len(bundle["notes"]) <= retrieval.MAX_NOTES
    assert len(bundle["concepts"]) <= retrieval.MAX_CONCEPTS
    assert len(bundle["edges"]) <= retrieval.MAX_EDGES


def test_retrieve_context_excludes_chat_entries(conn):
    """A chat thread (mode='chat') must not ground itself."""
    paper = _insert_paper(conn, "rc-2", {"arxiv_id": "rc-2"}, url="http://x")
    pid = paper["id"]
    conn.execute(
        "INSERT INTO kb_entry (term, span_text, item_id, mode, tag, created_at) "
        "VALUES (?,?,?,?,?,?)",
        ("Chat thread", "Some Title", pid, "chat", "chat", db.utcnow()))
    conn.commit()
    item = {"id": pid, "title": "Some Title", "abstract": "Body."}
    bundle = retrieval.retrieve_context(conn, item, "title")
    assert all(n["term"] != "Chat thread" for n in bundle["notes"])


# --------------------------------------------------------------------------
# article chat (persistent per-item thread, knowledge-grounded)
# --------------------------------------------------------------------------
def test_article_chat_thread_and_grounding(live_server):
    base = live_server
    status, _, setck = _http("POST", base + "/api/login",
                             {"username": "maya", "password": "pw123"})
    cookie = setck.split(";")[0]
    status, feed, _ = _http("GET", base + "/api/feed?kind=paper", cookie=cookie)
    item_id = feed["items"][0]["id"]

    # Seed a distinctive KB note tied to this article so retrieval can ground.
    status, saved, _ = _http("POST", base + "/api/kb/save", {
        "span_text": "Photosynthesis routing trick", "item_id": item_id,
        "mode": "explain", "model": "openai/gpt-fake",
        "answer": {"lead": "Lead", "body": "A note about photosynthesis."}},
        cookie=cookie)
    assert status == 200
    term = saved["entry"]["term"]

    # Chat about the article — a question that should pull in the seeded note.
    status, res, _ = _http("POST", base + "/api/chat", {
        "item_id": item_id, "message": "Tell me about photosynthesis here",
        "model": "openai/gpt-fake"}, cookie=cookie)
    assert status == 200
    assert res["answer"]["body"]
    entry_id = res["kb_entry_id"]
    assert entry_id
    # The grounded list exposes what was injected (verifiable without a real model).
    assert term in res["grounded"]["notes"]
    # And the fake AI provably RECEIVED the grounding (it echoes it back).
    assert "Photosynthesis routing trick" in res["answer"]["body"]

    # The thread persisted: one user + one assistant message.
    status, thread, _ = _http("GET", base + "/api/chat?item_id={}".format(item_id),
                              cookie=cookie)
    assert status == 200
    assert thread["kb_entry_id"] == entry_id
    assert len(thread["messages"]) == 2
    assert thread["messages"][0]["role"] == "user"

    # A follow-up continues the SAME single thread (no new entry).
    status, res2, _ = _http("POST", base + "/api/chat", {
        "item_id": item_id, "message": "And what about the router?",
        "model": "openai/gpt-fake"}, cookie=cookie)
    assert status == 200 and res2["kb_entry_id"] == entry_id

    status, thread2, _ = _http("GET", base + "/api/chat?item_id={}".format(item_id),
                               cookie=cookie)
    assert len(thread2["messages"]) == 4

    # The chat is FTS-searchable like the rest of the KB.
    status, found, _ = _http("GET", base + "/api/kb/search?q=photosynthesis", cookie=cookie)
    assert any(e["id"] == entry_id for e in found["entries"])

    # Chat entries do not clutter the concept map.
    status, mp, _ = _http("GET", base + "/api/map", cookie=cookie)
    assert all(n["kb_entry_id"] != entry_id for n in mp["nodes"])


# --------------------------------------------------------------------------
# concept-based glossary (Explain -> extract / reuse / define / clarify)
# --------------------------------------------------------------------------
def _count_generate(monkeypatch):
    """Wrap ai.generate to count AI calls (the live server runs in-process)."""
    calls = []
    orig = ai.generate

    def counting(*a, **k):
        calls.append(1)
        return orig(*a, **k)

    monkeypatch.setattr(ai, "generate", counting)
    return calls


def test_explain_extracts_dedupes_and_reuses_without_ai(live_server, monkeypatch):
    base = live_server
    calls = _count_generate(monkeypatch)
    status, _, setck = _http("POST", base + "/api/login",
                             {"username": "maya", "password": "pw123"})
    cookie = setck.split(";")[0]
    status, feed, _ = _http("GET", base + "/api/feed?kind=paper", cookie=cookie)
    item_id = feed["items"][0]["id"]

    # A fresh selection -> extraction names the concept and a definition is
    # generated (>=2 AI calls), flagged NEW.
    status, res, _ = _http("POST", base + "/api/explain", {
        "span_text": "Token routing", "item_id": item_id,
        "model": "openai/gpt-fake"}, cookie=cookie)
    assert status == 200
    assert res["question"] is None
    assert len(res["concepts"]) == 1
    concept = res["concepts"][0]
    assert concept["label"] == "Token routing"
    assert concept["reused"] is False
    assert concept["body"]
    assert len(calls) >= 2

    # Saving it creates exactly one concept row.
    status, saved, _ = _http("POST", base + "/api/kb/save", {
        "concepts": [concept], "item_id": item_id, "model": "openai/gpt-fake",
        "span_text": "Token routing"}, cookie=cookie)
    assert status == 200 and len(saved["entries"]) == 1
    eid = saved["entries"][0]["id"]
    assert saved["entries"][0]["mode"] == "concept"

    # Re-selecting the SAME concept reuses the cached definition with ZERO AI
    # calls (the span normalizes straight to a known label).
    before = len(calls)
    status, res2, _ = _http("POST", base + "/api/explain", {
        "span_text": "token routing", "item_id": item_id,
        "model": "openai/gpt-fake"}, cookie=cookie)
    assert status == 200
    assert res2["concepts"][0]["reused"] is True
    assert res2["concepts"][0]["kb_entry_id"] == eid
    assert len(calls) == before  # no AI generation for a known concept

    # Saving the re-encountered concept does NOT create a duplicate row.
    status, saved2, _ = _http("POST", base + "/api/kb/save", {
        "concepts": [res2["concepts"][0]], "item_id": item_id,
        "model": "openai/gpt-fake"}, cookie=cookie)
    assert saved2["entries"][0]["id"] == eid
    status, conc, _ = _http("GET", base + "/api/kb/concepts", cookie=cookie)
    labels = [c["label"] for c in conc["concepts"]]
    assert labels.count("Token routing") == 1


def test_explain_returns_clarifying_question_and_saves_nothing(live_server, monkeypatch):
    base = live_server
    status, _, setck = _http("POST", base + "/api/login",
                             {"username": "maya", "password": "pw123"})
    cookie = setck.split(";")[0]
    status, feed, _ = _http("GET", base + "/api/feed?kind=paper", cookie=cookie)
    item_id = feed["items"][0]["id"]

    status, res, _ = _http("POST", base + "/api/explain", {
        "span_text": "this vague thing", "item_id": item_id,
        "model": "openai/gpt-fake"}, cookie=cookie)
    assert status == 200
    assert res["concepts"] == []
    assert res["question"]
    # Nothing was saved.
    status, conc, _ = _http("GET", base + "/api/kb/concepts", cookie=cookie)
    assert all("vague" not in c["label"].lower() for c in conc["concepts"])


def test_kb_concepts_lists_labels_without_invoking_ai(live_server, monkeypatch):
    base = live_server
    status, _, setck = _http("POST", base + "/api/login",
                             {"username": "maya", "password": "pw123"})
    cookie = setck.split(";")[0]
    status, feed, _ = _http("GET", base + "/api/feed?kind=paper", cookie=cookie)
    item_id = feed["items"][0]["id"]

    # Seed a concept directly via save (a fully-formed definition, no AI needed).
    status, saved, _ = _http("POST", base + "/api/kb/save", {
        "concepts": [{"label": "Backpropagation", "lead": "Gradient flow",
                      "body": "How errors propagate back through a network.",
                      "reused": False}],
        "item_id": item_id, "model": "openai/gpt-fake"}, cookie=cookie)
    assert status == 200

    # Listing concepts must never call the AI.
    def _boom(*a, **k):
        raise AssertionError("AI must not be called to list concepts")

    monkeypatch.setattr(ai, "generate", _boom)
    status, conc, _ = _http("GET", base + "/api/kb/concepts", cookie=cookie)
    assert status == 200
    match = next(c for c in conc["concepts"] if c["label"] == "Backpropagation")
    assert match["body"]
    assert match["id"]


def test_chat_requires_item_and_message(live_server):
    base = live_server
    status, _, setck = _http("POST", base + "/api/login",
                             {"username": "maya", "password": "pw123"})
    cookie = setck.split(";")[0]
    status, _, _ = _http("POST", base + "/api/chat", {"message": "hi"}, cookie=cookie)
    assert status == 400
    # gated without a cookie
    status, _, _ = _http("POST", base + "/api/chat", {"item_id": 1, "message": "hi"})
    assert status == 401


# --------------------------------------------------------------------------
# kb entry deletion + linked-article back-references
# --------------------------------------------------------------------------
def _save_concept(conn, term, item_id=None):
    """Insert a concept kb_entry (+ message + map node) like the save path does."""
    cur = conn.execute(
        "INSERT INTO kb_entry (term, span_text, item_id, mode, model, lead, body, "
        "analogy, tag, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (term, term, item_id, "concept", "m", term + " lead", term + " body",
         None, "concept", db.utcnow()))
    eid = cur.lastrowid
    conn.execute("INSERT INTO kb_message (kb_entry_id, role, content, created_at) "
                 "VALUES (?,?,?,?)", (eid, "assistant", term + " body", db.utcnow()))
    db.reindex_entry(conn, eid)
    map_store.ensure_concept_for_entry(conn, eid)
    db.link_source(conn, eid, item_id)
    conn.commit()
    return eid


def test_bootstrap_backfills_kb_entry_source_from_item_id(conn):
    """An existing concept with an item_id is backfilled into kb_entry_source."""
    paper = _insert_paper(conn, "bf-1", {"arxiv_id": "bf-1"}, url="http://x")
    # Insert directly WITHOUT touching link_source, simulating a pre-migration DB.
    cur = conn.execute(
        "INSERT INTO kb_entry (term, span_text, item_id, mode, tag, created_at) "
        "VALUES (?,?,?,?,?,?)",
        ("Legacy concept", "legacy", paper["id"], "concept", "concept", db.utcnow()))
    eid = cur.lastrowid
    conn.commit()
    assert conn.execute(
        "SELECT COUNT(*) c FROM kb_entry_source WHERE kb_entry_id=?", (eid,)
    ).fetchone()["c"] == 0
    # Re-running bootstrap backfills the link from kb_entry.item_id.
    db.bootstrap(conn)
    row = conn.execute(
        "SELECT item_id FROM kb_entry_source WHERE kb_entry_id=?", (eid,)).fetchone()
    assert row["item_id"] == paper["id"]
    # Idempotent: a second bootstrap does not duplicate the link.
    db.bootstrap(conn)
    assert conn.execute(
        "SELECT COUNT(*) c FROM kb_entry_source WHERE kb_entry_id=?", (eid,)
    ).fetchone()["c"] == 1


def test_link_source_dedupes(conn):
    paper = _insert_paper(conn, "ls-1", {"arxiv_id": "ls-1"}, url="http://x")
    eid = _save_concept(conn, "Routing", item_id=paper["id"])
    # The save already linked it; linking the same pair again is a no-op.
    db.link_source(conn, eid, paper["id"])
    db.link_source(conn, eid, paper["id"])
    conn.commit()
    assert conn.execute(
        "SELECT COUNT(*) c FROM kb_entry_source WHERE kb_entry_id=? AND item_id=?",
        (eid, paper["id"])).fetchone()["c"] == 1


def test_kb_delete_removes_entry_messages_and_map(live_server, tmp_path):
    base = live_server
    status, _, setck = _http("POST", base + "/api/login",
                             {"username": "maya", "password": "pw123"})
    cookie = setck.split(";")[0]

    # gated without a cookie
    status, _, _ = _http("DELETE", base + "/api/kb/1")
    assert status == 401

    status, feed, _ = _http("GET", base + "/api/feed?kind=paper", cookie=cookie)
    item_id = feed["items"][0]["id"]

    # Save two concepts and link them on the map so an edge references one node.
    status, s1, _ = _http("POST", base + "/api/kb/save", {
        "concepts": [{"label": "Sparse routing", "lead": "L", "body": "B"}],
        "item_id": item_id, "model": "openai/gpt-fake"}, cookie=cookie)
    status, s2, _ = _http("POST", base + "/api/kb/save", {
        "concepts": [{"label": "Load balancing", "lead": "L", "body": "B"}],
        "item_id": item_id, "model": "openai/gpt-fake"}, cookie=cookie)
    eid = s1["entries"][0]["id"]
    status, mp, _ = _http("GET", base + "/api/map", cookie=cookie)
    n1 = next(n for n in mp["nodes"] if n["kb_entry_id"] == eid)
    n2 = next(n for n in mp["nodes"] if n["kb_entry_id"] == s2["entries"][0]["id"])
    status, _, _ = _http("POST", base + "/api/map/edge",
                         {"src": n1["id"], "dst": n2["id"]}, cookie=cookie)

    # Confirm it is searchable and present before deletion.
    status, found, _ = _http("GET", base + "/api/kb/search?q=sparse", cookie=cookie)
    assert any(e["id"] == eid for e in found["entries"])

    # DELETE removes the entry; 200.
    status, out, _ = _http("DELETE", base + "/api/kb/{}".format(eid), cookie=cookie)
    assert status == 200 and out["ok"] is True

    # Gone from the KB list, from search, and from /api/kb/{id}.
    status, kb, _ = _http("GET", base + "/api/kb", cookie=cookie)
    assert eid not in [e["id"] for e in kb["entries"]]
    status, found, _ = _http("GET", base + "/api/kb/search?q=sparse", cookie=cookie)
    assert all(e["id"] != eid for e in found["entries"])
    status, _, _ = _http("GET", base + "/api/kb/{}".format(eid), cookie=cookie)
    assert status == 404

    # The map node for that entry — and the edge that referenced it — are gone.
    status, mp, _ = _http("GET", base + "/api/map", cookie=cookie)
    assert all(n["kb_entry_id"] != eid for n in mp["nodes"])
    node_ids = {n["id"] for n in mp["nodes"]}
    assert all(e["src"] in node_ids and e["dst"] in node_ids for e in mp["edges"])
    assert all(e["src"] != n1["id"] and e["dst"] != n1["id"] for e in mp["edges"])

    # Its messages were removed too (verified directly on the shared DB).
    c = db.connect(str(tmp_path / "g.db"))
    msgs = c.execute(
        "SELECT COUNT(*) c FROM kb_message WHERE kb_entry_id=?", (eid,)).fetchone()["c"]
    c.close()
    assert msgs == 0

    # Deleting a now-missing id -> 404.
    status, _, _ = _http("DELETE", base + "/api/kb/{}".format(eid), cookie=cookie)
    assert status == 404


def test_kb_save_and_explain_populate_links_with_dedupe(live_server):
    base = live_server
    status, _, setck = _http("POST", base + "/api/login",
                             {"username": "maya", "password": "pw123"})
    cookie = setck.split(";")[0]
    status, feed, _ = _http("GET", base + "/api/feed?kind=paper", cookie=cookie)
    item_id = feed["items"][0]["id"]

    # Saving a concept from an article links that article as a source.
    status, saved, _ = _http("POST", base + "/api/kb/save", {
        "concepts": [{"label": "Token routing", "lead": "L", "body": "B"}],
        "item_id": item_id, "model": "openai/gpt-fake",
        "span_text": "Token routing"}, cookie=cookie)
    eid = saved["entries"][0]["id"]
    status, full, _ = _http("GET", base + "/api/kb/{}".format(eid), cookie=cookie)
    assert [it["id"] for it in full["linked_items"]] == [item_id]

    # Re-explaining the SAME concept within the SAME article dedupes the link.
    status, _, _ = _http("POST", base + "/api/explain", {
        "span_text": "token routing", "item_id": item_id,
        "model": "openai/gpt-fake"}, cookie=cookie)
    status, full, _ = _http("GET", base + "/api/kb/{}".format(eid), cookie=cookie)
    assert [it["id"] for it in full["linked_items"]] == [item_id]


def test_concept_seen_in_two_articles_lists_both(live_server):
    base = live_server
    status, _, setck = _http("POST", base + "/api/login",
                             {"username": "maya", "password": "pw123"})
    cookie = setck.split(";")[0]
    status, feed, _ = _http("GET", base + "/api/feed?kind=paper", cookie=cookie)
    paper_ids = [i["id"] for i in feed["items"] if i["kind"] == "paper"]
    a, b = paper_ids[0], paper_ids[1]

    # Save the concept from article A.
    status, saved, _ = _http("POST", base + "/api/kb/save", {
        "concepts": [{"label": "Expert routing", "lead": "L", "body": "B"}],
        "item_id": a, "model": "openai/gpt-fake",
        "span_text": "Expert routing"}, cookie=cookie)
    eid = saved["entries"][0]["id"]

    # Reuse it from article B (explain reuse path within a different article).
    status, res, _ = _http("POST", base + "/api/explain", {
        "span_text": "expert routing", "item_id": b,
        "model": "openai/gpt-fake"}, cookie=cookie)
    assert res["concepts"][0]["reused"] is True

    # GET /api/kb/{id} lists BOTH articles as sources.
    status, full, _ = _http("GET", base + "/api/kb/{}".format(eid), cookie=cookie)
    linked = {it["id"] for it in full["linked_items"]}
    assert linked == {a, b}
    # Each linked item carries the fields the UI needs.
    for it in full["linked_items"]:
        assert it["title"] and it["kind"] in ("paper", "repo")
        assert "source" in it
