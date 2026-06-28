"""HTTP server: JSON API + static web UI, behind a plaintext auth gate.

ThreadingHTTPServer with a single shared SQLite connection serialized by a
lock (fine for a single-user personal tool). Every /api/* route except
/api/login requires a valid token from the ``gym_token`` cookie.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import threading
import datetime as _dt
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, Optional, Tuple
from urllib.parse import urlparse, parse_qs

from . import ai, auth, docs, feed, ingest, map_store, refresh, retrieval
from .db import bootstrap, connect, link_source, reindex_entry, utcnow

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".pdf": "application/pdf",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
}


class AppContext:
    """Shared, thread-safe application state."""

    def __init__(self, db_path: str, reports_dir: str, docs_dir: str,
                 default_model: Optional[str] = None):
        self.db_path = db_path
        self.reports_dir = reports_dir
        self.docs_dir = docs_dir
        self.default_model = default_model
        self.lock = threading.Lock()
        self.conn = connect(db_path)
        bootstrap(self.conn)

    def new_conn(self) -> sqlite3.Connection:
        return connect(self.db_path)


class Handler(BaseHTTPRequestHandler):
    ctx: AppContext = None  # set on the server instance subclass

    server_version = "Gymnasium/0.1"

    # -- low-level helpers --------------------------------------------------
    def log_message(self, fmt, *args):  # quieter logs
        sys.stderr.write("[server] " + (fmt % args) + "\n")

    def _cookies(self) -> SimpleCookie:
        c = SimpleCookie()
        raw = self.headers.get("Cookie")
        if raw:
            c.load(raw)
        return c

    def _token(self) -> Optional[str]:
        c = self._cookies()
        if "gym_token" in c:
            return c["gym_token"].value
        return None

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    def _send_json(self, obj, status: int = 200, headers: Optional[Dict[str, str]] = None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if headers:
            for k, v in headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, data: bytes, content_type: str, status: int = 200,
                    download_name: Optional[str] = None, attachment: bool = False,
                    headers: Optional[Dict[str, str]] = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if download_name:
            disposition = "attachment" if attachment else "inline"
            self.send_header("Content-Disposition",
                             '{}; filename="{}"'.format(disposition, download_name))
        if headers:
            for k, v in headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _unauthorized(self):
        self._send_json({"error": "unauthorized"}, status=401)

    # -- auth ---------------------------------------------------------------
    def _current_user(self) -> Optional[int]:
        with self.ctx.lock:
            return auth.verify_token(self.ctx.conn, self._token())

    # -- routing ------------------------------------------------------------
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/"):
            return self._route_api("GET", path, parse_qs(parsed.query))
        return self._serve_static(path)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/"):
            return self._route_api("POST", path, parse_qs(parsed.query))
        self._send_json({"error": "not found"}, status=404)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/"):
            return self._route_api("DELETE", path, parse_qs(parsed.query))
        self._send_json({"error": "not found"}, status=404)

    # -- static -------------------------------------------------------------
    def _serve_file(self, abs_path: str, download_name: Optional[str] = None):
        if not os.path.isfile(abs_path):
            self._send_json({"error": "not found"}, status=404)
            return
        ext = os.path.splitext(abs_path)[1].lower()
        ctype = _CONTENT_TYPES.get(ext, "application/octet-stream")
        with open(abs_path, "rb") as fh:
            data = fh.read()
        self._send_bytes(data, ctype, download_name=download_name)

    def _serve_static(self, path: str):
        if path == "/" or path == "":
            # Login if unauthed, app shell if authed.
            user = self._current_user()
            target = "index.html" if user else "login.html"
            return self._serve_file(os.path.join(WEB_DIR, target))
        # Normalize and prevent path traversal.
        rel = path.lstrip("/")
        abs_path = os.path.normpath(os.path.join(WEB_DIR, rel))
        if not abs_path.startswith(WEB_DIR):
            self._send_json({"error": "forbidden"}, status=403)
            return
        self._serve_file(abs_path)

    # -- API dispatch -------------------------------------------------------
    def _route_api(self, method: str, path: str, query: Dict):
        # Public endpoint.
        if path == "/api/login" and method == "POST":
            return self._api_login()

        # Everything else needs auth.
        user_id = self._current_user()
        if user_id is None:
            return self._unauthorized()

        try:
            if path == "/api/logout" and method == "POST":
                return self._api_logout()
            if path == "/api/me" and method == "GET":
                return self._api_me(user_id)
            if path == "/api/models" and method == "GET":
                return self._api_models()
            if path == "/api/feed" and method == "GET":
                return self._api_feed(query)
            if path == "/api/items" and method == "POST":
                return self._api_items_create()
            if path.startswith("/api/items/") and method == "DELETE":
                return self._api_items_delete(int(path.rsplit("/", 1)[1]))
            if path == "/api/feed/facets" and method == "GET":
                return self._api_feed_facets(query)
            if path == "/api/summarize" and method == "POST":
                return self._api_summarize()
            if path == "/api/ask" and method == "POST":
                return self._api_ask()
            if path == "/api/explain" and method == "POST":
                return self._api_explain()
            if path == "/api/chat" and method == "POST":
                return self._api_chat()
            if path == "/api/chat" and method == "GET":
                return self._api_chat_get(query)
            if path == "/api/kb" and method == "GET":
                return self._api_kb_list()
            if path == "/api/kb/concepts" and method == "GET":
                return self._api_kb_concepts()
            if path == "/api/kb/search" and method == "GET":
                return self._api_kb_search(query)
            if path == "/api/kb/save" and method == "POST":
                return self._api_kb_save()
            if path == "/api/map" and method == "GET":
                return self._api_map()
            if path == "/api/map/edge" and method == "POST":
                return self._api_map_edge_add()
            if path == "/api/map/position" and method == "POST":
                return self._api_map_position()
            if path == "/api/map/ai-links" and method == "POST":
                return self._api_map_ai_links()
            if path == "/api/refresh" and method == "POST":
                return self._api_refresh()
            if path == "/api/refresh/status" and method == "GET":
                return self._api_refresh_status()

            # parameterized: /api/item/{id}, /api/item/{id}/document,
            # /api/kb/{id}, /api/map/edge/{id}
            parts = [p for p in path.split("/") if p]
            if len(parts) >= 3 and parts[0] == "api" and parts[1] == "item":
                if len(parts) == 3 and method == "GET":
                    return self._api_item(int(parts[2]))
                if len(parts) == 4 and parts[3] == "document" and method == "GET":
                    return self._api_item_document(int(parts[2]))
                if len(parts) == 4 and parts[3] == "document" and method == "POST":
                    return self._api_item_document_post(int(parts[2]))
                if len(parts) == 4 and parts[3] == "markdown" and method == "GET":
                    return self._api_item_markdown_get(int(parts[2]))
            if len(parts) == 3 and parts[0] == "api" and parts[1] == "kb" and method == "GET":
                return self._api_kb_get(int(parts[2]))
            if len(parts) == 3 and parts[0] == "api" and parts[1] == "kb" and method == "DELETE":
                return self._api_kb_delete(int(parts[2]))
            if len(parts) == 4 and parts[:3] == ["api", "map", "edge"] and method == "DELETE":
                return self._api_map_edge_delete(int(parts[3]))
        except ValueError:
            return self._send_json({"error": "bad request"}, status=400)
        except ai.AIError as exc:
            return self._send_json({"error": str(exc)}, status=502)

        self._send_json({"error": "not found"}, status=404)

    # -- auth endpoints -----------------------------------------------------
    def _api_login(self):
        data = self._read_json()
        username = data.get("username", "")
        password = data.get("password", "")
        with self.ctx.lock:
            uid = auth.verify_credentials(self.ctx.conn, username, password)
            if uid is None:
                return self._send_json({"error": "invalid credentials"}, status=401)
            token = auth.issue_token(self.ctx.conn, uid)
        cookie = (
            "gym_token={}; Path=/; HttpOnly; SameSite=Lax; Max-Age={}".format(
                token, 30 * 24 * 3600)
        )
        self._send_json({"ok": True, "username": username}, headers={"Set-Cookie": cookie})

    def _api_logout(self):
        with self.ctx.lock:
            auth.revoke_token(self.ctx.conn, self._token())
        cookie = "gym_token=; Path=/; HttpOnly; Max-Age=0"
        self._send_json({"ok": True}, headers={"Set-Cookie": cookie})

    def _api_me(self, user_id: int):
        with self.ctx.lock:
            row = auth.get_user(self.ctx.conn, user_id)
        if row is None:
            return self._unauthorized()
        self._send_json({"id": row["id"], "username": row["username"]})

    # -- models -------------------------------------------------------------
    def _api_models(self):
        result = ai.list_models()
        result["default"] = self._default_model(result)
        self._send_json(result)

    def _default_model(self, listing: dict) -> Optional[str]:
        if self.ctx.default_model:
            return self.ctx.default_model
        providers = listing.get("providers") or []
        for g in providers:
            if g.get("models"):
                return g["models"][0]["id"]
        return None

    # -- feed / items -------------------------------------------------------
    def _item_dict(self, row: sqlite3.Row) -> dict:
        raw = feed.parse_raw(row)
        tag_list = (raw.get("labs_matched") or raw.get("topics") or [])[:3]
        if raw.get("language"):
            tag_list = [raw["language"]] + list(tag_list)
        out = {
            "id": row["id"],
            "kind": row["kind"],
            "title": row["title"],
            "source": row["source"],
            "url": row["url"],
            "abstract": row["abstract"],
            "why": row["why"],
            "signal": row["signal"],
            "published_at": row["published_at"],
            "summary_readable": json.loads(row["summary_readable"]) if row["summary_readable"] else None,
            "summary_terms": json.loads(row["summary_terms"]) if row["summary_terms"] else None,
            "doc_path": row["doc_path"],
            "has_markdown": bool(row["markdown_path"]),
            "markdown_source": row["markdown_source"],
            "added_by_user": bool(row["added_by_user"]),
            "doc_uploaded": bool(row["doc_uploaded"]),
            "tags": [t for t in tag_list if t][:3],
        }
        # Fields the cards / filters need (authors, company, publication, language).
        out.update(feed.item_facets(row, raw))
        return out

    def _api_feed(self, query: Dict):
        kind = (query.get("kind", [None])[0])
        limit = int(query.get("limit", ["50"])[0] or 50)
        q = (query.get("q", [""])[0] or "").strip()
        sort = (query.get("sort", ["recency"])[0] or "recency")
        added = (query.get("added", [None])[0] or "").strip().lower() in ("1", "true", "yes")
        filters = {
            "author": query.get("author", [None])[0],
            "company": query.get("company", [None])[0],
            "publication": query.get("publication", [None])[0],
            "language": query.get("language", [None])[0],
        }
        sql = "SELECT * FROM corpus_item"
        clauses = []
        params = []
        if kind in ("paper", "repo"):
            clauses.append("kind=?")
            params.append(kind)
        if added:
            # The "Added" view: only user-added items.
            clauses.append("COALESCE(added_by_user, 0) = 1")
        else:
            # The tracker feeds never surface user-added items.
            clauses.append("COALESCE(added_by_user, 0) = 0")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        with self.ctx.lock:
            rows = self.ctx.conn.execute(sql, params).fetchall()
        items = feed.select(rows, q=q, sort=sort, filters=filters, limit=limit)
        self._send_json({"items": [self._item_dict(r) for r in items]})

    def _api_items_create(self):
        """Create (or update) a user-added item from a link OR an uploaded PDF.

        Branches on the request Content-Type: a ``multipart/form-data`` body is
        a PDF file upload (handled by ``_create_pdf_item``); otherwise a JSON
        ``{url, title?}`` body adds a paper or GitHub repo from a link.

        Kind-aware (link path): a ``github.com/<owner>/<name>`` URL becomes a
        ``repo`` whose ``raw_json`` carries owner/name/full_name/html_url so the
        existing ``ensure_document`` repo path fetches the README and the reader
        renders it as markdown. Any other URL stays a ``paper`` exactly as before.

        Deduped by ``external_id`` (the repo full_name, or the URL for a paper)
        so re-adding the same link updates the existing row instead of
        duplicating it. The title is the provided one, else — for a paper — a
        best-effort page <title>/og:title then the URL host, or — for a repo —
        the ``owner/name`` full_name.
        """
        ctype = (self.headers.get("Content-Type") or "").lower()
        if "multipart/form-data" in ctype:
            return self._create_pdf_item(ctype)
        data = self._read_json()
        url = (data.get("url") or "").strip()
        if not url:
            return self._send_json({"error": "url required"}, status=400)
        title = (data.get("title") or "").strip()
        repo = _github_repo(url)
        if repo is not None:
            return self._create_repo_item(repo, title)
        return self._create_paper_item(url, title)

    def _create_paper_item(self, url: str, title: str):
        if not title:
            title = _page_title(url) or _url_host(url) or url
        now = utcnow()
        with self.ctx.lock:
            existing = self.ctx.conn.execute(
                "SELECT id FROM corpus_item WHERE kind='paper' AND external_id=?",
                (url,)).fetchone()
            if existing is not None:
                item_id = int(existing["id"])
                self.ctx.conn.execute(
                    "UPDATE corpus_item SET title=?, url=?, source=?, "
                    "added_by_user=1, published_at=?, ingested_at=? WHERE id=?",
                    (title, url, "Added by you", now, now, item_id))
            else:
                cur = self.ctx.conn.execute(
                    "INSERT INTO corpus_item (kind, external_id, title, source, url, "
                    "signal, added_by_user, published_at, ingested_at) "
                    "VALUES ('paper', ?, ?, 'Added by you', ?, 0, 1, ?, ?)",
                    (url, title, url, now, now))
                item_id = int(cur.lastrowid)
            self.ctx.conn.commit()
        self._send_json({"ok": True, "id": item_id, "kind": "paper"})

    def _create_repo_item(self, repo: Tuple[str, str], title: str):
        owner, name = repo
        full_name = "{}/{}".format(owner, name)
        html_url = "https://github.com/{}".format(full_name)
        if not title:
            title = full_name
        raw = json.dumps({"owner": owner, "name": name,
                          "full_name": full_name, "html_url": html_url})
        now = utcnow()
        with self.ctx.lock:
            existing = self.ctx.conn.execute(
                "SELECT id FROM corpus_item WHERE kind='repo' AND external_id=?",
                (full_name,)).fetchone()
            if existing is not None:
                item_id = int(existing["id"])
                self.ctx.conn.execute(
                    "UPDATE corpus_item SET title=?, url=?, source=?, added_by_user=1, "
                    "raw_json=?, published_at=?, ingested_at=? WHERE id=?",
                    (title, html_url, "Added by you", raw, now, now, item_id))
            else:
                cur = self.ctx.conn.execute(
                    "INSERT INTO corpus_item (kind, external_id, title, source, url, "
                    "signal, added_by_user, raw_json, published_at, ingested_at) "
                    "VALUES ('repo', ?, ?, 'Added by you', ?, 0, 1, ?, ?, ?)",
                    (full_name, title, html_url, raw, now, now))
                item_id = int(cur.lastrowid)
            self.ctx.conn.commit()
        self._send_json({"ok": True, "id": item_id, "kind": "repo"})

    def _create_pdf_item(self, ctype: str):
        """Create (or update) a user-added paper from an uploaded PDF file.

        The PDF bytes are the authoritative source — there is no upstream URL —
        so we store them as ``papers/<slug>/source.pdf`` and point ``doc_path``
        straight at the file. ``external_id`` is derived from the file CONTENT
        hash, so re-uploading the same bytes updates the existing row (dedupe)
        instead of creating a duplicate. The title is the provided one, else the
        uploaded filename without its ``.pdf`` extension. The reader's existing
        markitdown auto-conversion then turns ``source.pdf`` into markdown on
        first open.
        """
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return self._send_json({"error": "empty body"}, status=400)
        if length > _MAX_PDF:
            return self._send_json({"error": "too large"}, status=413)
        raw = self.rfile.read(length)
        parsed = _parse_multipart_form(raw, ctype)
        pdf = parsed["file"]
        if not pdf:
            return self._send_json({"error": "no file field"}, status=400)
        title = (parsed["fields"].get("title") or "").strip()
        if not title:
            base = os.path.basename(parsed["filename"] or "")
            if base.lower().endswith(".pdf"):
                base = base[:-4]
            title = base.strip() or "Uploaded PDF"
        external_id = "pdf:" + hashlib.sha256(pdf).hexdigest()[:32]
        now = utcnow()
        with self.ctx.lock:
            existing = self.ctx.conn.execute(
                "SELECT id FROM corpus_item WHERE kind='paper' AND external_id=?",
                (external_id,)).fetchone()
            if existing is not None:
                item_id = int(existing["id"])
                self.ctx.conn.execute(
                    "UPDATE corpus_item SET title=?, source=?, url=NULL, "
                    "added_by_user=1, published_at=?, ingested_at=? WHERE id=?",
                    (title, "Uploaded PDF", now, now, item_id))
            else:
                cur = self.ctx.conn.execute(
                    "INSERT INTO corpus_item (kind, external_id, title, source, url, "
                    "signal, added_by_user, published_at, ingested_at) "
                    "VALUES ('paper', ?, ?, 'Uploaded PDF', NULL, 0, 1, ?, ?)",
                    (external_id, title, now, now))
                item_id = int(cur.lastrowid)
            row = self.ctx.conn.execute(
                "SELECT * FROM corpus_item WHERE id=?", (item_id,)).fetchone()
            doc_rel = docs.store_uploaded_pdf(row, pdf, self.ctx.docs_dir)
            self.ctx.conn.execute(
                "UPDATE corpus_item SET doc_path=?, doc_fetched_at=? WHERE id=?",
                (doc_rel, now, item_id))
            self.ctx.conn.commit()
        self._send_json({"ok": True, "id": item_id, "kind": "paper"})

    def _api_items_delete(self, item_id: int):
        """Delete a user-added item (paper or repo). Tracker items are protected.

        Only an ``added_by_user=1`` row may be removed; a tracker item (0) is
        rejected with 403 so it can never be deleted. The on-disk document folder
        is removed best-effort (non-fatal). Saved ``kb_entry`` rows are left
        intact — the FK drops their ``item_id`` to NULL but keeps their content.
        """
        with self.ctx.lock:
            row = self.ctx.conn.execute(
                "SELECT * FROM corpus_item WHERE id=?", (item_id,)).fetchone()
            if row is None:
                return self._send_json({"error": "not found"}, status=404)
            if not row["added_by_user"]:
                return self._send_json({"error": "forbidden"}, status=403)
            docs.remove_item_dir(row, self.ctx.docs_dir)
            self.ctx.conn.execute("DELETE FROM corpus_item WHERE id=?", (item_id,))
            self.ctx.conn.commit()
        self._send_json({"ok": True})

    def _api_feed_facets(self, query: Dict):
        kind = (query.get("kind", ["paper"])[0]) or "paper"
        if kind not in ("paper", "repo"):
            return self._send_json({"error": "bad kind"}, status=400)
        with self.ctx.lock:
            rows = self.ctx.conn.execute(
                "SELECT * FROM corpus_item WHERE kind=?", (kind,)).fetchall()
        self._send_json(feed.facets(rows, kind))

    def _api_item(self, item_id: int):
        with self.ctx.lock:
            row = self.ctx.conn.execute(
                "SELECT * FROM corpus_item WHERE id=?", (item_id,)).fetchone()
            if row is None:
                return self._send_json({"error": "not found"}, status=404)
            # Ensure the original document is fetched to disk.
            docs.ensure_document(row, self.ctx.docs_dir, self.ctx.conn)
            row = self.ctx.conn.execute(
                "SELECT * FROM corpus_item WHERE id=?", (item_id,)).fetchone()
            out = self._item_dict(row)
            # Cheap advertisement only: a real source document is on disk that
            # could be converted on first read. No conversion runs here — that
            # happens lazily in the markdown GET.
            out["markdown_available"] = \
                docs.has_convertible_source(row, self.ctx.docs_dir) or \
                docs.has_repo_readme(row, self.ctx.docs_dir)
        self._send_json(out)

    def _api_item_document(self, item_id: int):
        with self.ctx.lock:
            row = self.ctx.conn.execute(
                "SELECT * FROM corpus_item WHERE id=?", (item_id,)).fetchone()
            if row is None:
                return self._send_json({"error": "not found"}, status=404)
            doc_path = row["doc_path"]
            if not doc_path:
                doc_path = docs.ensure_document(row, self.ctx.docs_dir, self.ctx.conn)
        if not doc_path:
            return self._send_json({"error": "no document"}, status=404)
        abs_path = os.path.normpath(os.path.join(self.ctx.docs_dir, doc_path))
        docs_root = os.path.abspath(self.ctx.docs_dir)
        if not os.path.abspath(abs_path).startswith(docs_root):
            return self._send_json({"error": "forbidden"}, status=403)
        if not os.path.isfile(abs_path):
            return self._send_json({"error": "not found"}, status=404)
        name = os.path.basename(abs_path)
        ext = os.path.splitext(abs_path)[1].lower()
        with open(abs_path, "rb") as fh:
            data = fh.read()
        if ext in (".html", ".htm"):
            # Stored upstream HTML must never run inline on our origin (it would
            # execute same-origin with the authenticated API — stored XSS). Serve
            # it as a non-executing attachment instead.
            self._send_bytes(data, "text/plain; charset=utf-8",
                             download_name=name, attachment=True)
        else:
            ctype = _CONTENT_TYPES.get(ext, "application/octet-stream")
            self._send_bytes(data, ctype, download_name=name)

    # -- markdown attachment ------------------------------------------------
    def _api_item_markdown_get(self, item_id: int):
        with self.ctx.lock:
            row = self.ctx.conn.execute(
                "SELECT * FROM corpus_item WHERE id=?", (item_id,)).fetchone()
            if row is None:
                return self._send_json({"error": "not found"}, status=404)
            # Precedence: a user upload always wins over the auto version.
            content = docs.read_markdown(row, self.ctx.docs_dir)
            source = "user" if content is not None else None
            if content is None and row["kind"] == "repo":
                # A repo's README.md is already Markdown — serve it directly.
                # Ensure it's on disk first, then re-read the (possibly updated) row.
                docs.ensure_document(row, self.ctx.docs_dir, self.ctx.conn)
                row = self.ctx.conn.execute(
                    "SELECT * FROM corpus_item WHERE id=?", (item_id,)).fetchone()
                content = docs.read_repo_readme(row, self.ctx.docs_dir)
                if content is not None:
                    source = "readme"
            if content is None and row["kind"] != "repo":
                # Already-cached auto conversion, if any.
                content = docs.read_auto_markdown(row, self.ctx.docs_dir)
                if content is not None and docs.auto_markdown_is_stale(row, content):
                    # Legacy cache with relative image URLs that 404 in the
                    # reader — regenerate it once with absolute URLs.
                    regenerated = docs.auto_markdown(row, self.ctx.docs_dir, self.ctx.conn)
                    if regenerated is not None:
                        content = regenerated
                if content is None:
                    # Lazy first conversion: convert the stored original once.
                    content = docs.auto_markdown(row, self.ctx.docs_dir, self.ctx.conn)
                    if content is not None:
                        self.ctx.conn.execute(
                            "UPDATE corpus_item SET markdown_source=? WHERE id=?",
                            ("auto", item_id))
                        self.ctx.conn.commit()
                if content is not None:
                    source = "auto"
        if content is None:
            return self._send_json({"error": "no markdown"}, status=404)
        self._send_bytes(content.encode("utf-8"), "text/markdown; charset=utf-8",
                         headers={"X-Markdown-Source": source})

    def _api_item_document_post(self, item_id: int):
        """Upload a supporting document (PDF or markdown/text) for an item.

        A PDF becomes the item's authoritative ``source.pdf`` and the reader
        auto-converts it to markdown on next open. Any other file
        (``.md``/``.markdown``/``.html``/``.txt``) is stored as supplementary
        material and the cached auto-conversion is dropped so the reader
        regenerates a readable view from the new file. Marks the item
        ``doc_uploaded=1`` so ``ensure_document`` never refetches/replaces the
        user's file.
        """
        with self.ctx.lock:
            row = self.ctx.conn.execute(
                "SELECT * FROM corpus_item WHERE id=?", (item_id,)).fetchone()
        if row is None:
            return self._send_json({"error": "not found"}, status=404)
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return self._send_json({"error": "empty body"}, status=400)
        if length > _MAX_PDF:
            return self._send_json({"error": "too large"}, status=413)
        raw = self.rfile.read(length)
        ctype = (self.headers.get("Content-Type") or "").lower()
        if "multipart/form-data" in ctype:
            parsed = _parse_multipart_form(raw, ctype)
            data = parsed["file"]
            if not data:
                return self._send_json({"error": "no file field"}, status=400)
            filename = parsed["filename"] or ""
        else:
            data = raw
            filename = ""
        ext = os.path.splitext(filename or "")[1].lower()
        is_pdf = ext == ".pdf" or "pdf" in ctype
        if is_pdf:
            data = data if _looks_like_pdf(data) else data
        if not filename:
            filename = "source.pdf" if is_pdf else "support.md"
        now = utcnow()
        with self.ctx.lock:
            doc_rel = docs.store_uploaded_source(row, data, filename, self.ctx.docs_dir)
            # Drop any cached markdown so the reader regenerates from the new file.
            row = self.ctx.conn.execute(
                "SELECT * FROM corpus_item WHERE id=?", (item_id,)).fetchone()
            docs.clear_markdown_files(row, self.ctx.docs_dir)
            self.ctx.conn.execute(
                "UPDATE corpus_item SET doc_path=?, doc_fetched_at=?, doc_uploaded=1, "
                "markdown_path=NULL, markdown_source=NULL WHERE id=?",
                (doc_rel, now, item_id))
            self.ctx.conn.commit()
        self._send_json({"ok": True, "doc_path": doc_rel})

    # -- summarize ----------------------------------------------------------
    def _api_summarize(self):
        data = self._read_json()
        item_id = int(data.get("item_id"))
        model = data.get("model") or self.ctx.default_model
        with self.ctx.lock:
            row = self.ctx.conn.execute(
                "SELECT * FROM corpus_item WHERE id=?", (item_id,)).fetchone()
        if row is None:
            return self._send_json({"error": "not found"}, status=404)
        item = self._item_dict(row)
        # Summarize once and cache; reuse the cached summary thereafter.
        cached = item["summary_readable"]
        if not cached:
            result = ai.summarize_item(item, model)
            with self.ctx.lock:
                self.ctx.conn.execute(
                    "UPDATE corpus_item SET summary_readable=?, summary_terms=? WHERE id=?",
                    (json.dumps(result["summary"]), json.dumps(result["terms"]), item_id),
                )
                self.ctx.conn.commit()
            summary, terms = result["summary"], result["terms"]
        else:
            summary, terms = cached, item["summary_terms"] or []
        self._send_json({"summary": summary, "terms": terms, "model": model})

    # -- ask ----------------------------------------------------------------
    def _api_ask(self):
        data = self._read_json()
        span = data.get("span_text", "")
        mode = data.get("mode", "explain")
        model = data.get("model") or self.ctx.default_model
        item_id = data.get("item_id")
        kb_entry_id = data.get("kb_entry_id")
        message = data.get("message")  # follow-up question text, if any

        item = {}
        if item_id:
            with self.ctx.lock:
                row = self.ctx.conn.execute(
                    "SELECT * FROM corpus_item WHERE id=?", (int(item_id),)).fetchone()
            if row is not None:
                item = self._item_dict(row)

        history = []
        if kb_entry_id:
            with self.ctx.lock:
                rows = self.ctx.conn.execute(
                    "SELECT role, content FROM kb_message WHERE kb_entry_id=? ORDER BY id",
                    (int(kb_entry_id),)).fetchall()
            history = [{"role": r["role"], "content": r["content"]} for r in rows]

        # When it's a follow-up question, the question itself is the prompt.
        ask_span = message if (mode == "ask" and message) else span
        # Ground the answer in the reader's own KB notes / concept map. The
        # query is whatever the reader is asking about (the question or span).
        kb_notes = None
        graph = None
        if item:
            with self.ctx.lock:
                bundle = retrieval.retrieve_context(self.ctx.conn, item, ask_span)
            kb_notes = bundle["notes"]
            graph = {"concepts": bundle["concepts"], "edges": bundle["edges"]}
        answer = ai.explain(ask_span, mode, item, model, history=history,
                            kb_notes=kb_notes, graph=graph)

        # If tied to a saved entry, append the user turn + answer.
        if kb_entry_id:
            now = utcnow()
            answer_text = self._answer_text(answer)
            with self.ctx.lock:
                if message:
                    self.ctx.conn.execute(
                        "INSERT INTO kb_message (kb_entry_id, role, content, created_at) "
                        "VALUES (?,?,?,?)", (int(kb_entry_id), "user", message, now))
                self.ctx.conn.execute(
                    "INSERT INTO kb_message (kb_entry_id, role, content, created_at) "
                    "VALUES (?,?,?,?)", (int(kb_entry_id), "assistant", answer_text, now))
                reindex_entry(self.ctx.conn, int(kb_entry_id))
                self.ctx.conn.commit()
        self._send_json({"answer": answer, "model": model})

    # -- explain (concept-based glossary) -----------------------------------
    def _concept_by_label(self, norm_label: str) -> Optional[sqlite3.Row]:
        """Find a saved CONCEPT entry by its normalized label (caller holds lock)."""
        if not norm_label:
            return None
        return self.ctx.conn.execute(
            "SELECT * FROM kb_entry WHERE mode='concept' "
            "AND LOWER(TRIM(term))=? ORDER BY id LIMIT 1", (norm_label,)).fetchone()

    @staticmethod
    def _concept_payload(row: sqlite3.Row, reused: bool) -> dict:
        return {
            "label": row["term"],
            "lead": row["lead"] or "",
            "body": row["body"] or "",
            "analogy": row["analogy"],
            "reused": reused,
            "kb_entry_id": int(row["id"]),
        }

    def _api_explain(self):
        """Concept-based Explain: name the concept(s) in a span, reuse or define.

        For each salient concept in the selection we look it up in the KB by
        normalized label. Known concepts REUSE their cached definition with no
        AI call; new ones get a concise definition generated. When the selection
        is too vague to name a concept the AI returns a clarifying question and
        we surface that instead of guessing.
        """
        data = self._read_json()
        span = (data.get("span_text") or "").strip()
        model = data.get("model") or self.ctx.default_model
        item_id = data.get("item_id")

        item = {}
        if item_id:
            with self.ctx.lock:
                row = self.ctx.conn.execute(
                    "SELECT * FROM corpus_item WHERE id=?", (int(item_id),)).fetchone()
            if row is not None:
                item = self._item_dict(row)

        # Optimization: if the selection itself normalizes to a known concept
        # label, skip the extraction AI call entirely and return the cache.
        norm = _normalize_label(span)
        with self.ctx.lock:
            direct = self._concept_by_label(norm)
            if direct is not None:
                # Reused from within an article: the open article is a source.
                link_source(self.ctx.conn, int(direct["id"]),
                            int(item_id) if item_id else None)
                self.ctx.conn.commit()
        if direct is not None:
            return self._send_json({
                "concepts": [self._concept_payload(direct, reused=True)],
                "question": None, "model": model})

        extraction = ai.extract_concepts(span, item, model)
        question = extraction.get("question")
        labels = extraction.get("concepts") or []
        if question and not labels:
            return self._send_json(
                {"concepts": [], "question": question, "model": model})

        out = []
        seen = set()
        for label in labels:
            nlabel = _normalize_label(label)
            if not nlabel or nlabel in seen:
                continue
            seen.add(nlabel)
            with self.ctx.lock:
                existing = self._concept_by_label(nlabel)
                if existing is not None:
                    # Reused concept seen in this article: record the source.
                    link_source(self.ctx.conn, int(existing["id"]),
                                int(item_id) if item_id else None)
                    self.ctx.conn.commit()
            if existing is not None:
                out.append(self._concept_payload(existing, reused=True))
                continue
            # New concept -> generate a concise definition (not yet persisted;
            # persistence happens on Save so vague/abandoned explains add nothing).
            definition = ai.explain(label, "explain", item, model)
            out.append({
                "label": label,
                "lead": definition.get("lead", ""),
                "body": definition.get("body", ""),
                "analogy": definition.get("analogy"),
                "reused": False,
                "kb_entry_id": None,
            })
        self._send_json({"concepts": out, "question": None, "model": model})

    @staticmethod
    def _answer_text(answer: dict) -> str:
        parts = [answer.get("lead", ""), answer.get("body", "")]
        if answer.get("analogy"):
            parts.append("Picture it: " + answer["analogy"])
        return "\n".join(p for p in parts if p)

    # -- article chat -------------------------------------------------------
    def _chat_entry_id(self, item_id: int, title: str, create: bool = True):
        """Return the single persistent chat kb_entry id for an article.

        One thread per item, marked mode='chat'. Created on demand (under the
        caller's lock) when ``create`` is set.
        """
        row = self.ctx.conn.execute(
            "SELECT id FROM kb_entry WHERE item_id=? AND mode='chat' ORDER BY id LIMIT 1",
            (int(item_id),)).fetchone()
        if row is not None:
            return int(row["id"])
        if not create:
            return None
        term = _term_from_span(title) or "Article chat"
        irow = self.ctx.conn.execute(
            "SELECT url FROM corpus_item WHERE id=?", (int(item_id),)).fetchone()
        source_url = irow["url"] if irow else None
        cur = self.ctx.conn.execute(
            "INSERT INTO kb_entry (term, span_text, item_id, source_url, mode, "
            "tag, created_at) VALUES (?,?,?,?,?,?,?)",
            (term, title or term, int(item_id), source_url, "chat", "chat", utcnow()))
        self.ctx.conn.commit()
        return int(cur.lastrowid)

    def _api_chat_get(self, query: Dict):
        item_id = query.get("item_id", [None])[0]
        if not item_id:
            return self._send_json({"error": "item_id required"}, status=400)
        with self.ctx.lock:
            entry_id = self._chat_entry_id(int(item_id), "", create=False)
            messages = []
            if entry_id is not None:
                rows = self.ctx.conn.execute(
                    "SELECT role, content, created_at FROM kb_message "
                    "WHERE kb_entry_id=? ORDER BY id", (entry_id,)).fetchall()
                messages = [
                    {"role": r["role"], "content": r["content"], "created_at": r["created_at"]}
                    for r in rows
                ]
        self._send_json({"item_id": int(item_id), "kb_entry_id": entry_id,
                         "messages": messages})

    def _api_chat(self):
        data = self._read_json()
        item_id = data.get("item_id")
        message = (data.get("message") or "").strip()
        model = data.get("model") or self.ctx.default_model
        if not item_id or not message:
            return self._send_json({"error": "item_id and message required"}, status=400)

        with self.ctx.lock:
            row = self.ctx.conn.execute(
                "SELECT * FROM corpus_item WHERE id=?", (int(item_id),)).fetchone()
            if row is None:
                return self._send_json({"error": "not found"}, status=404)
            item = self._item_dict(row)
            entry_id = self._chat_entry_id(int(item_id), item.get("title") or "")
            hist_rows = self.ctx.conn.execute(
                "SELECT role, content FROM kb_message WHERE kb_entry_id=? ORDER BY id",
                (entry_id,)).fetchall()
            history = [{"role": r["role"], "content": r["content"]} for r in hist_rows]
            bundle = retrieval.retrieve_context(self.ctx.conn, item, message)

        graph = {"concepts": bundle["concepts"], "edges": bundle["edges"]}
        answer = ai.chat(item, history, message, kb_notes=bundle["notes"],
                         graph=graph, excerpt=bundle["excerpt"], model=model)

        now = utcnow()
        answer_text = self._answer_text(answer)
        with self.ctx.lock:
            self.ctx.conn.execute(
                "INSERT INTO kb_message (kb_entry_id, role, content, created_at) "
                "VALUES (?,?,?,?)", (entry_id, "user", message, now))
            self.ctx.conn.execute(
                "INSERT INTO kb_message (kb_entry_id, role, content, created_at) "
                "VALUES (?,?,?,?)", (entry_id, "assistant", answer_text, now))
            reindex_entry(self.ctx.conn, entry_id)
            self.ctx.conn.commit()
        self._send_json({
            "answer": answer,
            "model": model,
            "kb_entry_id": entry_id,
            "grounded": bundle["grounded"],
        })

    # -- knowledge base -----------------------------------------------------
    def _entry_dict(self, row: sqlite3.Row, with_messages: bool = False) -> dict:
        out = {
            "id": row["id"],
            "term": row["term"],
            "span_text": row["span_text"],
            "item_id": row["item_id"],
            "source_url": row["source_url"],
            "source_doc_path": row["source_doc_path"],
            "mode": row["mode"],
            "model": row["model"],
            "lead": row["lead"],
            "body": row["body"],
            "analogy": row["analogy"],
            "tag": row["tag"],
            "created_at": row["created_at"],
        }
        # Source title for the "From:" link.
        if row["item_id"]:
            irow = self.ctx.conn.execute(
                "SELECT title FROM corpus_item WHERE id=?", (row["item_id"],)).fetchone()
            out["source_title"] = irow["title"] if irow else None
        if with_messages:
            mrows = self.ctx.conn.execute(
                "SELECT role, content, created_at FROM kb_message WHERE kb_entry_id=? ORDER BY id",
                (row["id"],)).fetchall()
            out["messages"] = [
                {"role": m["role"], "content": m["content"], "created_at": m["created_at"]}
                for m in mrows
            ]
        return out

    def _api_kb_list(self):
        with self.ctx.lock:
            rows = self.ctx.conn.execute(
                "SELECT * FROM kb_entry ORDER BY id DESC").fetchall()
            out = [self._entry_dict(r) for r in rows]
        self._send_json({"entries": out})

    def _linked_items(self, entry_id: int) -> list:
        """The corpus_items this concept (kb_entry) has been seen in.

        Reads the back-reference table so a concept referenced by MANY articles
        lists them all. Returns id/title/source/kind per article (caller holds
        the lock).
        """
        rows = self.ctx.conn.execute(
            "SELECT c.id, c.title, c.source, c.kind FROM kb_entry_source s "
            "JOIN corpus_item c ON c.id = s.item_id WHERE s.kb_entry_id=? "
            "ORDER BY s.id", (entry_id,)).fetchall()
        return [{"id": r["id"], "title": r["title"], "source": r["source"],
                 "kind": r["kind"]} for r in rows]

    def _api_kb_get(self, entry_id: int):
        with self.ctx.lock:
            row = self.ctx.conn.execute(
                "SELECT * FROM kb_entry WHERE id=?", (entry_id,)).fetchone()
            if row is None:
                return self._send_json({"error": "not found"}, status=404)
            out = self._entry_dict(row, with_messages=True)
            out["linked_items"] = self._linked_items(entry_id)
        self._send_json(out)

    def _api_kb_delete(self, entry_id: int):
        """Remove a kb_entry and everything tied to it.

        Deletes the entry's messages, its FTS index row, and (best-effort) its
        knowledge-map node — the concept row plus any concept_edge referencing
        it. The map cleanup is non-fatal: a failure there must not block removing
        the entry itself. FK cascades cover kb_message / kb_entry_source / concept
        too, but we delete explicitly so the FTS shadow and edges go regardless.
        """
        with self.ctx.lock:
            row = self.ctx.conn.execute(
                "SELECT id FROM kb_entry WHERE id=?", (entry_id,)).fetchone()
            if row is None:
                return self._send_json({"error": "not found"}, status=404)
            # Best-effort knowledge-map cleanup: concept node + its edges.
            try:
                concepts = self.ctx.conn.execute(
                    "SELECT id FROM concept WHERE kb_entry_id=?", (entry_id,)).fetchall()
                for c in concepts:
                    cid = int(c["id"])
                    self.ctx.conn.execute(
                        "DELETE FROM concept_edge WHERE src_concept_id=? OR dst_concept_id=?",
                        (cid, cid))
                self.ctx.conn.execute(
                    "DELETE FROM concept WHERE kb_entry_id=?", (entry_id,))
            except sqlite3.Error as exc:
                print("[kb] map cleanup failed for entry {}: {}".format(entry_id, exc))
            self.ctx.conn.execute(
                "DELETE FROM kb_entry_source WHERE kb_entry_id=?", (entry_id,))
            self.ctx.conn.execute(
                "DELETE FROM kb_message WHERE kb_entry_id=?", (entry_id,))
            self.ctx.conn.execute("DELETE FROM kb_fts WHERE entry_id=?", (entry_id,))
            self.ctx.conn.execute("DELETE FROM kb_entry WHERE id=?", (entry_id,))
            self.ctx.conn.commit()
        self._send_json({"ok": True})

    def _api_kb_search(self, query: Dict):
        q = (query.get("q", [""])[0] or "").strip()
        if not q:
            return self._api_kb_list()
        # Build a safe FTS match: OR the tokens, prefix-match each.
        tokens = [t for t in _fts_tokens(q)]
        if not tokens:
            return self._send_json({"entries": []})
        match = " OR ".join('"{}"*'.format(t) for t in tokens)
        with self.ctx.lock:
            try:
                rows = self.ctx.conn.execute(
                    "SELECT e.* FROM kb_fts f JOIN kb_entry e ON e.id=f.entry_id "
                    "WHERE kb_fts MATCH ? ORDER BY rank", (match,)).fetchall()
            except sqlite3.OperationalError:
                rows = []
            out = [self._entry_dict(r) for r in rows]
        self._send_json({"entries": out})

    def _item_source(self, irow: Optional[sqlite3.Row]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Snapshot (source_url, source_doc_path, tag) for a corpus item row.

        Ensures the source document is on disk and derives a tag from the item's
        raw metadata. Returns (None, None, None) when there is no item.
        """
        if irow is None:
            return None, None, None
        source_url = irow["url"]
        source_doc_path = docs.ensure_document(irow, self.ctx.docs_dir, self.ctx.conn)
        try:
            raw = json.loads(irow["raw_json"]) if irow["raw_json"] else {}
        except (ValueError, TypeError):
            raw = {}
        tag = (raw.get("labs_matched") or raw.get("topics") or [None])[0]
        if not tag and raw.get("primary_field"):
            tag = raw["primary_field"]
        return source_url, source_doc_path, tag

    def _api_kb_save(self):
        data = self._read_json()
        # Concept-based save (the glossary path): dedupe by normalized label.
        if data.get("concepts") is not None:
            return self._api_kb_save_concepts(data)

        span = data.get("span_text", "")
        item_id = data.get("item_id")
        mode = data.get("mode", "explain")
        model = data.get("model")
        answer = data.get("answer") or {}
        thread = data.get("thread") or []  # [{role, content}, ...] follow-ups

        term = _term_from_span(span)
        lead = answer.get("lead", "")
        body = answer.get("body", "")
        analogy = answer.get("analogy")

        with self.ctx.lock:
            irow = None
            if item_id:
                irow = self.ctx.conn.execute(
                    "SELECT * FROM corpus_item WHERE id=?", (int(item_id),)).fetchone()
            source_url, source_doc_path, tag = self._item_source(irow)
            now = utcnow()
            cur = self.ctx.conn.execute(
                "INSERT INTO kb_entry (term, span_text, item_id, source_url, "
                "source_doc_path, mode, model, lead, body, analogy, tag, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (term, span, int(item_id) if item_id else None, source_url,
                 source_doc_path, mode, model, lead, body, analogy,
                 tag or "note", now),
            )
            entry_id = int(cur.lastrowid)

            # Persist the WHOLE conversation: the initial answer, then each
            # follow-up turn passed in `thread`.
            self.ctx.conn.execute(
                "INSERT INTO kb_message (kb_entry_id, role, content, created_at) "
                "VALUES (?,?,?,?)", (entry_id, "assistant", self._answer_text(answer), now))
            for turn in thread:
                role = "user" if turn.get("role") in ("user", "q") else "assistant"
                content = turn.get("content") or turn.get("text") or ""
                if content:
                    self.ctx.conn.execute(
                        "INSERT INTO kb_message (kb_entry_id, role, content, created_at) "
                        "VALUES (?,?,?,?)", (entry_id, role, content, now))
            reindex_entry(self.ctx.conn, entry_id)
            # A saved entry becomes a knowledge-map node.
            map_store.ensure_concept_for_entry(self.ctx.conn, entry_id)
            # Record the originating article as a back-reference source.
            link_source(self.ctx.conn, entry_id, int(item_id) if item_id else None)
            self.ctx.conn.commit()
            row = self.ctx.conn.execute(
                "SELECT * FROM kb_entry WHERE id=?", (entry_id,)).fetchone()
            out = self._entry_dict(row, with_messages=True)
        self._send_json({"ok": True, "entry": out})

    def _api_kb_save_concepts(self, data: dict):
        """Persist extracted CONCEPT(s), deduped by normalized label.

        A concept entry is term=label, lead/body=definition, mode='concept'.
        Re-saving a concept already in the KB reuses the existing row (and
        attaches it to this article if it had no source yet) instead of creating
        a duplicate, so the same concept is never stored twice.
        """
        concepts = data.get("concepts") or []
        item_id = data.get("item_id")
        model = data.get("model")
        span = data.get("span_text") or ""

        with self.ctx.lock:
            irow = None
            if item_id:
                irow = self.ctx.conn.execute(
                    "SELECT * FROM corpus_item WHERE id=?", (int(item_id),)).fetchone()
            source_url, source_doc_path, tag = self._item_source(irow)
            now = utcnow()
            saved = []
            for c in concepts:
                label = (str(c.get("label") or "")).strip()
                if not label:
                    continue
                nlabel = _normalize_label(label)
                lead = c.get("lead", "") or ""
                body = c.get("body", "") or ""
                analogy = c.get("analogy")
                existing = self._concept_by_label(nlabel)
                if existing is not None:
                    eid = int(existing["id"])
                    # Backfill a source if the cached concept had none yet.
                    if irow is not None and not existing["item_id"]:
                        self.ctx.conn.execute(
                            "UPDATE kb_entry SET item_id=?, source_url=?, "
                            "source_doc_path=? WHERE id=?",
                            (int(item_id), source_url, source_doc_path, eid))
                else:
                    cur = self.ctx.conn.execute(
                        "INSERT INTO kb_entry (term, span_text, item_id, source_url, "
                        "source_doc_path, mode, model, lead, body, analogy, tag, "
                        "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (label, span or label, int(item_id) if item_id else None,
                         source_url, source_doc_path, "concept", model, lead, body,
                         analogy, tag or "concept", now))
                    eid = int(cur.lastrowid)
                    self.ctx.conn.execute(
                        "INSERT INTO kb_message (kb_entry_id, role, content, created_at) "
                        "VALUES (?,?,?,?)",
                        (eid, "assistant", self._answer_text(
                            {"lead": lead, "body": body, "analogy": analogy}), now))
                    reindex_entry(self.ctx.conn, eid)
                    map_store.ensure_concept_for_entry(self.ctx.conn, eid)
                # Either way, link this concept to the current article (deduped).
                link_source(self.ctx.conn, eid, int(item_id) if item_id else None)
                row = self.ctx.conn.execute(
                    "SELECT * FROM kb_entry WHERE id=?", (eid,)).fetchone()
                saved.append(self._entry_dict(row, with_messages=True))
            self.ctx.conn.commit()
        self._send_json({"ok": True, "entries": saved})

    def _api_kb_concepts(self):
        """List known concept labels (+id and cached definition). No AI call.

        The reader uses this to underline concepts and to show a concept's cached
        definition with zero AI when one is tapped.
        """
        with self.ctx.lock:
            rows = self.ctx.conn.execute(
                "SELECT id, term, lead, body, analogy FROM kb_entry "
                "WHERE mode='concept' ORDER BY LENGTH(term) DESC, id DESC").fetchall()
            out = [{
                "id": int(r["id"]),
                "label": r["term"],
                "lead": r["lead"] or "",
                "body": r["body"] or "",
                "analogy": r["analogy"],
            } for r in rows]
        self._send_json({"concepts": out})

    # -- map ----------------------------------------------------------------
    def _api_map(self):
        with self.ctx.lock:
            data = map_store.get_map(self.ctx.conn)
        self._send_json(data)

    def _api_map_edge_add(self):
        data = self._read_json()
        src = int(data.get("src"))
        dst = int(data.get("dst"))
        with self.ctx.lock:
            edge_id = map_store.add_edge(self.ctx.conn, src, dst, source="manual")
        if edge_id is None:
            return self._send_json({"error": "invalid edge"}, status=400)
        self._send_json({"ok": True, "id": edge_id})

    def _api_map_edge_delete(self, edge_id: int):
        with self.ctx.lock:
            map_store.delete_edge(self.ctx.conn, edge_id)
        self._send_json({"ok": True})

    def _api_map_position(self):
        data = self._read_json()
        concept_id = int(data.get("concept_id"))
        with self.ctx.lock:
            map_store.set_position(self.ctx.conn, concept_id,
                                   float(data.get("x")), float(data.get("y")))
        self._send_json({"ok": True})

    def _api_map_ai_links(self):
        data = self._read_json()
        model = data.get("model") or self.ctx.default_model
        with self.ctx.lock:
            result = map_store.ai_links(self.ctx.conn, model)
        self._send_json(result)

    # -- refresh ------------------------------------------------------------
    def _api_refresh(self):
        data = self._read_json()
        kind = data.get("kind")
        days = int(data.get("days") or 7)
        state = refresh.run_refresh(kind, days, self.ctx.reports_dir, self.ctx.new_conn)
        self._send_json(state)

    def _api_refresh_status(self):
        self._send_json(refresh.status())


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
import re as _re

_MAX_MARKDOWN = 2 * 1024 * 1024  # 2 MB cap on a markdown/text attachment
_MAX_PDF = 30 * 1024 * 1024  # 30 MB cap on an uploaded PDF


def _parse_multipart_form(raw: bytes, ctype: str) -> dict:
    """Parse a multipart/form-data body into its file part and text fields.

    Minimal stdlib-only parse (sibling of ``_parse_multipart_file``): enough for
    one binary file field plus simple text fields. Returns a dict with ``file``
    (raw bytes of the first part carrying a ``filename`` — kept binary so a PDF
    survives intact), ``filename`` (its declared name), and ``fields`` (a name →
    decoded-text map for the non-file parts, e.g. an optional ``title``). Missing
    pieces are ``None``/empty rather than an error.
    """
    out = {"file": None, "filename": None, "fields": {}}
    m = _re.search(r'boundary="?([^";]+)"?', ctype)
    if not m:
        return out
    delim = b"--" + m.group(1).encode()
    for part in raw.split(delim):
        if b"Content-Disposition" not in part:
            continue
        header_blob, sep, body = part.partition(b"\r\n\r\n")
        if not sep:
            continue
        if body.endswith(b"\r\n"):
            body = body[:-2]
        header_text = header_blob.decode("latin-1", "replace")
        file_m = _re.search(r'filename="([^"]*)"', header_text)
        name_m = _re.search(r'name="([^"]*)"', header_text)
        if file_m:
            if out["file"] is None:  # first file part wins
                out["file"] = body
                out["filename"] = file_m.group(1)
        elif name_m:
            out["fields"][name_m.group(1)] = body.decode("utf-8", "replace")
    return out


def _parse_multipart_file(raw: bytes, ctype: str) -> Optional[bytes]:
    """Extract the single uploaded file's bytes from a multipart/form-data body.

    Minimal stdlib-only parse: enough for one file field. Returns None when no
    form-data part is found.
    """
    m = _re.search(r'boundary="?([^";]+)"?', ctype)
    if not m:
        return None
    delim = b"--" + m.group(1).encode()
    fallback = None
    for part in raw.split(delim):
        if b"Content-Disposition" not in part:
            continue
        header_blob, sep, body = part.partition(b"\r\n\r\n")
        if not sep:
            continue
        if body.endswith(b"\r\n"):
            body = body[:-2]
        if b"filename=" in header_blob:
            return body  # prefer the actual file field
        if fallback is None:
            fallback = body
    return fallback


def _looks_like_pdf(data: bytes) -> bool:
    """True when the bytes start with a PDF header (%PDF-)."""
    return data[:5] == b"%PDF-" if data else False


def _github_repo(url: str) -> Optional[Tuple[str, str]]:
    """Return ``(owner, name)`` when a URL points at a GitHub repository, else None.

    Accepts ``github.com/<owner>/<name>`` with or without a scheme/``www.``,
    tolerating a trailing slash, a ``.git`` suffix, and any extra path segments
    or query/fragment. Non-GitHub URLs (and bare ``github.com/<owner>`` profile
    links) return None so they stay papers.
    """
    try:
        parsed = urlparse(url if "://" in url else "https://" + url)
    except ValueError:
        return None
    if (parsed.netloc or "").lower() not in ("github.com", "www.github.com"):
        return None
    parts = [seg for seg in parsed.path.split("/") if seg]
    if len(parts) < 2:
        return None
    owner, name = parts[0], parts[1]
    if name.endswith(".git"):
        name = name[:-4]
    if not owner or not name:
        return None
    return owner, name


def _url_host(url: str) -> str:
    """The host of a URL, used as a last-resort title for an added article."""
    try:
        return urlparse(url).netloc or ""
    except ValueError:
        return ""


def _page_title(url: str) -> Optional[str]:
    """Best-effort <title>/og:title for an added link. Non-fatal; None on failure.

    Reuses ``docs._fetch`` (bounded timeout, never raises) so a slow or down
    page can't block adding the article — the caller falls back to the host.
    """
    data = docs._fetch(url)
    if not data:
        return None
    try:
        html = data.decode("utf-8", "ignore")
    except Exception:
        return None
    m = _re.search(
        r'<meta[^>]+(?:property|name)=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
        html, _re.IGNORECASE)
    if not m:
        m = _re.search(
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']og:title["\']',
            html, _re.IGNORECASE)
    if m:
        return _re.sub(r"\s+", " ", m.group(1)).strip() or None
    m = _re.search(r"<title[^>]*>(.*?)</title>", html, _re.IGNORECASE | _re.DOTALL)
    if m:
        return _re.sub(r"\s+", " ", m.group(1)).strip() or None
    return None


def _fts_tokens(q: str):
    return [t for t in _re.findall(r"[A-Za-z0-9]+", q.lower()) if t]


def _normalize_label(label: str) -> str:
    """Normalize a concept label for case-insensitive, trimmed dedupe lookups."""
    return _re.sub(r"\s+", " ", label or "").strip().lower()


def _term_from_span(span: str) -> str:
    term = _re.sub(r"\s+", " ", span or "").strip()
    if len(term) > 42:
        term = term[:42].strip() + "…"
    if term:
        term = term[0].upper() + term[1:]
    return term or "Note"


# --------------------------------------------------------------------------
# Server bootstrap & CLI
# --------------------------------------------------------------------------
def make_server(host: str, port: int, ctx: AppContext) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"ctx": ctx})
    return ThreadingHTTPServer((host, port), handler)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # Console subcommand: gymnasium adduser <username> <password>
    if argv and argv[0] == "adduser":
        return _cmd_adduser(argv[1:])

    parser = argparse.ArgumentParser(
        prog="gymnasium", description="Gymnasium University server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8077)
    parser.add_argument("--db", default="data/gymnasium.db")
    parser.add_argument("--reports", default="reports")
    parser.add_argument("--docs-dir", default="data/documents")
    parser.add_argument("--model", default=os.environ.get("GYM_MODEL"),
                        help="default AI model id (else first listed)")
    parser.add_argument("--ingest-on-start", action="store_true",
                        help="ingest the latest report sidecars before serving")
    args = parser.parse_args(argv)

    ctx = AppContext(args.db, args.reports, args.docs_dir, default_model=args.model)
    if args.ingest_on_start:
        counts = ingest.ingest_latest(args.reports, ctx.conn)
        print("[server] ingested:", counts)

    httpd = make_server(args.host, args.port, ctx)
    print("[server] Gymnasium University on http://{}:{}".format(args.host, args.port))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[server] shutting down")
    finally:
        httpd.server_close()
    return 0


def _cmd_adduser(argv) -> int:
    parser = argparse.ArgumentParser(prog="gymnasium adduser")
    parser.add_argument("username")
    parser.add_argument("password")
    parser.add_argument("--db", default="data/gymnasium.db")
    args = parser.parse_args(argv)

    conn = connect(args.db)
    bootstrap(conn)
    try:
        uid = auth.add_user(conn, args.username, args.password)
    except ValueError as exc:
        print("error:", exc, file=sys.stderr)
        return 2
    except sqlite3.IntegrityError:
        print("error: user '{}' already exists".format(args.username), file=sys.stderr)
        return 2
    print("created user '{}' (id {})".format(args.username, uid))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
