"""On-disk document store.

When an item is opened (or a fact is saved) we fetch the original document
once and keep it on disk, so a saved fact always points at a concrete file
even if the upstream source later disappears. Downloads use stdlib urllib and
failure is non-fatal: a down source must never block reading or saving.

Layout (relative to docs_dir):
    papers/<arxiv-id-or-slug>/  source.pdf | source.html, abstract.txt, metadata.json
    repos/<owner>__<name>/      README.md, metadata.json
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import urllib.request
import urllib.error
from urllib.parse import urljoin
from typing import List, Optional

from .db import utcnow

USER_AGENT = "GymnasiumUniversity/0.1 (+personal-research)"
_TIMEOUT = 30


def _safe_slug(value: str, fallback: str = "item") -> str:
    value = (value or "").strip()
    # arXiv ids and DOIs contain '/', '.', ':' — keep it filesystem-safe.
    value = re.sub(r"^https?://", "", value)
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    return slug[:120] or fallback


def _fetch(url: str) -> Optional[bytes]:
    """Best-effort GET. Returns bytes or None (never raises)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
        print("[docs] fetch failed for {}: {}".format(url, exc))
        return None


def _paper_dir_slug(item: sqlite3.Row, raw: dict) -> str:
    ext = raw.get("arxiv_id") or item["external_id"]
    return _safe_slug(str(ext), "paper")


# arXiv ids appear as bare ids, in abs URLs, or inside a DataCite DOI.
_ARXIV_ABS_RE = re.compile(r"arxiv\.org/abs/([^\s?#]+)", re.IGNORECASE)
_ARXIV_DOI_RE = re.compile(r"10\.48550/arxiv\.([^\s?#/]+)", re.IGNORECASE)


def _arxiv_id(item: sqlite3.Row, raw: dict) -> Optional[str]:
    """Derive the arXiv id for an item, or None when it is not an arXiv paper.

    Reads an explicit ``arxiv_id``, an ``arxiv.org/abs/<id>`` URL (version suffix
    preserved), or a ``10.48550/arXiv.<id>`` DOI.
    """
    arxiv_id = (raw.get("arxiv_id") or "").strip()
    if not arxiv_id:
        sources = (raw.get("abs_url"), raw.get("source_url"), item["url"],
                   raw.get("doi"), raw.get("url"))
        for source in sources:
            if not source:
                continue
            match = _ARXIV_ABS_RE.search(str(source)) or _ARXIV_DOI_RE.search(str(source))
            if match:
                arxiv_id = match.group(1).strip().rstrip("/")
                break
    return arxiv_id or None


def _arxiv_pdf_url(item: sqlite3.Row, raw: dict) -> Optional[str]:
    """Return the canonical arXiv PDF URL for an arXiv paper, else None.

    Builds ``https://arxiv.org/pdf/<id>``. Returns None when not arXiv.
    """
    arxiv_id = _arxiv_id(item, raw)
    return "https://arxiv.org/pdf/{}".format(arxiv_id) if arxiv_id else None


def _arxiv_html_url(item: sqlite3.Row, raw: dict) -> Optional[str]:
    """Return the arXiv full-text HTML URL for an arXiv paper, else None.

    ``https://arxiv.org/html/<id>`` is the clean rendered full text — no vertical
    margin watermark and no lost spaces — so markitdown converts it far more
    cleanly than the PDF. Returns None when the item is not arXiv.
    """
    arxiv_id = _arxiv_id(item, raw)
    return "https://arxiv.org/html/{}".format(arxiv_id) if arxiv_id else None


# Inline Markdown link/image: the [label], the target URL (optionally wrapped in
# <…>), and an optional "title". We rewrite only the target so the label/title
# survive untouched.
_MD_URL_RE = re.compile(r'(!?\[[^\]]*\])\(\s*(<[^>]*>|[^()\s]+)((?:\s+"[^"]*")?)\s*\)')
# Targets we must never rewrite: in-page anchors, protocol-relative, data URIs,
# and anything already carrying a scheme.
_ABSOLUTE_PREFIXES = ("http://", "https://", "data:", "mailto:", "ftp:", "javascript:")


def _md_target(raw_target: str) -> str:
    """Strip optional <…> angle brackets from a Markdown link target."""
    if raw_target.startswith("<") and raw_target.endswith(">"):
        return raw_target[1:-1]
    return raw_target


def _is_relative_target(target: str) -> bool:
    """True when a Markdown target should be absolutized against the page base."""
    if not target or target.startswith("#") or target.startswith("//"):
        return False
    return not target.lower().startswith(_ABSOLUTE_PREFIXES)


def _source_url(item: sqlite3.Row, raw: dict) -> Optional[str]:
    """The web URL the stored ``source.html`` was fetched from, for resolving its
    relative asset links to absolute. Prefers the arXiv full-text HTML page (the
    base its ``x1.png``/``figure/…`` images are relative to). None when unknown.
    """
    arxiv_html = _arxiv_html_url(item, raw)
    if arxiv_html:
        return arxiv_html
    return raw.get("abs_url") or item["url"] or raw.get("source_url") or None


def _absolutize_md_urls(text: str, base_url: str) -> str:
    """Rewrite relative ``![alt](url)`` / ``[text](url)`` targets to absolute.

    Resolves each relative target against ``base_url`` (urljoin) so converted
    arXiv-HTML markdown keeps working images/links once rendered on another host.
    Absolute, in-page, protocol-relative and data-URI targets are left intact.
    """
    if not base_url:
        return text

    def repl(match: "re.Match") -> str:
        target = _md_target(match.group(2))
        if not _is_relative_target(target):
            return match.group(0)
        return "{}({}{})".format(match.group(1), urljoin(base_url, target), match.group(3))

    return _MD_URL_RE.sub(repl, text)


def _has_relative_image(text: Optional[str]) -> bool:
    """True when the markdown still has a relative ``![…](url)`` image target."""
    for match in _MD_URL_RE.finditer(text or ""):
        if match.group(1).startswith("!") and _is_relative_target(_md_target(match.group(2))):
            return True
    return False


def auto_markdown_is_stale(item: sqlite3.Row, content: Optional[str]) -> bool:
    """True when a cached conversion still holds relative image URLs we can fix.

    Legacy ``article.auto.md`` files cached before URL absolutization keep
    relative arXiv image targets that 404 in the reader. We only flag a rewrite
    when a base URL is known, so absolutized conversions never re-trigger.
    """
    if item["kind"] == "repo" or not _has_relative_image(content):
        return False
    try:
        raw = json.loads(item["raw_json"]) if item["raw_json"] else {}
    except (ValueError, TypeError):
        raw = {}
    return _source_url(item, raw) is not None


def _repo_dir_slug(item: sqlite3.Row, raw: dict) -> str:
    owner = raw.get("owner") or ""
    name = raw.get("name") or ""
    if owner and name:
        return "{}__{}".format(_safe_slug(owner, "owner"), _safe_slug(name, "repo"))
    return _safe_slug(item["external_id"], "repo")


def _store_paper(item: sqlite3.Row, raw: dict, docs_dir: str) -> Optional[str]:
    slug = _paper_dir_slug(item, raw)
    rel_dir = os.path.join("papers", slug)
    abs_dir = os.path.join(docs_dir, rel_dir)
    os.makedirs(abs_dir, exist_ok=True)

    # metadata + abstract are always written from what we already hold.
    with open(os.path.join(abs_dir, "metadata.json"), "w", encoding="utf-8") as fh:
        json.dump(raw or {}, fh, ensure_ascii=False, indent=2)
    abstract = item["abstract"] or raw.get("abstract") or ""
    with open(os.path.join(abs_dir, "abstract.txt"), "w", encoding="utf-8") as fh:
        fh.write(abstract)

    # original document: for arXiv papers prefer the clean full-text HTML
    # (arxiv.org/html/<id> — no vertical watermark, no lost spaces) which
    # markitdown converts cleanly; fall back to the PDF, then the abstract page.
    # For arXiv papers the tracker entry often lacks pdf_url, so derive the
    # canonical arxiv.org/pdf/<id> URL and try it before the html fallback.
    html_url = raw.get("abs_url") or item["url"] or raw.get("source_url")
    doc_rel = None
    arxiv_html = _arxiv_html_url(item, raw)
    if arxiv_html:
        data = _fetch(arxiv_html)
        if data:
            with open(os.path.join(abs_dir, "source.html"), "wb") as fh:
                fh.write(data)
            doc_rel = os.path.join(rel_dir, "source.html")
    if doc_rel is None:
        existing_pdf = os.path.join(abs_dir, "source.pdf")
        if os.path.isfile(existing_pdf):
            # A PDF is already on disk (e.g. arXiv HTML 404'd this open) — reuse
            # it rather than re-downloading the whole file on every open.
            doc_rel = os.path.join(rel_dir, "source.pdf")
        else:
            pdf_urls = []
            if raw.get("pdf_url"):
                pdf_urls.append(raw["pdf_url"])
            arxiv_pdf = _arxiv_pdf_url(item, raw)
            if arxiv_pdf and arxiv_pdf not in pdf_urls:
                pdf_urls.append(arxiv_pdf)
            for pdf_url in pdf_urls:
                data = _fetch(pdf_url)
                if data:
                    with open(existing_pdf, "wb") as fh:
                        fh.write(data)
                    doc_rel = os.path.join(rel_dir, "source.pdf")
                    break
    if doc_rel is None and html_url:
        data = _fetch(html_url)
        if data:
            with open(os.path.join(abs_dir, "source.html"), "wb") as fh:
                fh.write(data)
            doc_rel = os.path.join(rel_dir, "source.html")
    if doc_rel is None:
        # Even with no network, keep the abstract as the canonical local doc.
        doc_rel = os.path.join(rel_dir, "abstract.txt")
    return doc_rel


def _store_repo(item: sqlite3.Row, raw: dict, docs_dir: str) -> Optional[str]:
    slug = _repo_dir_slug(item, raw)
    rel_dir = os.path.join("repos", slug)
    abs_dir = os.path.join(docs_dir, rel_dir)
    os.makedirs(abs_dir, exist_ok=True)

    with open(os.path.join(abs_dir, "metadata.json"), "w", encoding="utf-8") as fh:
        json.dump(raw or {}, fh, ensure_ascii=False, indent=2)

    owner = raw.get("owner") or ""
    name = raw.get("name") or ""
    readme_text = None
    if owner and name:
        for branch in ("main", "master"):
            url = "https://raw.githubusercontent.com/{}/{}/{}/README.md".format(owner, name, branch)
            data = _fetch(url)
            if data:
                readme_text = data
                break
    readme_path = os.path.join(abs_dir, "README.md")
    if readme_text is not None:
        with open(readme_path, "wb") as fh:
            fh.write(readme_text)
    else:
        # Fall back to the description so there's always a readable file.
        with open(readme_path, "w", encoding="utf-8") as fh:
            fh.write((item["abstract"] or raw.get("description") or item["title"] or ""))
    return os.path.join(rel_dir, "README.md")


def _item_dir_rel(item: sqlite3.Row) -> str:
    """Repo-relative document folder for an item (papers/<slug> | repos/<slug>).

    Reuses the same slug logic as the document store so an attached markdown
    file lands next to the item's other artifacts.
    """
    try:
        raw = json.loads(item["raw_json"]) if item["raw_json"] else {}
    except (ValueError, TypeError):
        raw = {}
    if item["kind"] == "repo":
        return os.path.join("repos", _repo_dir_slug(item, raw))
    return os.path.join("papers", _paper_dir_slug(item, raw))


def remove_item_dir(item: sqlite3.Row, docs_dir: str) -> None:
    """Best-effort remove an item's on-disk document folder. Never raises.

    Used when a user-added item is deleted. Path-guarded against escaping
    ``docs_dir`` and a no-op when the folder is absent.
    """
    import shutil

    rel_dir = _item_dir_rel(item)
    abs_dir = os.path.normpath(os.path.join(docs_dir, rel_dir))
    root = os.path.abspath(docs_dir)
    if not os.path.abspath(abs_dir).startswith(root) or os.path.abspath(abs_dir) == root:
        return
    try:
        if os.path.isdir(abs_dir):
            shutil.rmtree(abs_dir)
    except OSError as exc:
        print("[docs] remove dir failed for item {}: {}".format(item["id"], exc))


def store_uploaded_pdf(item: sqlite3.Row, data: bytes, docs_dir: str) -> str:
    """Store an uploaded PDF as ``papers/<slug>/source.pdf`` and return its rel path.

    Used for user-uploaded papers: there is no upstream URL to fetch, so the
    bytes the user provided are the authoritative source document. The caller
    points ``corpus_item.doc_path`` straight at the returned path so
    ``ensure_document`` never tries to re-fetch it. Overwrites any prior file
    (re-uploading identical bytes is idempotent).
    """
    try:
        raw = json.loads(item["raw_json"]) if item["raw_json"] else {}
    except (ValueError, TypeError):
        raw = {}
    slug = _paper_dir_slug(item, raw)
    rel_dir = os.path.join("papers", slug)
    abs_dir = os.path.join(docs_dir, rel_dir)
    os.makedirs(abs_dir, exist_ok=True)
    with open(os.path.join(abs_dir, "source.pdf"), "wb") as fh:
        fh.write(data)
    return os.path.join(rel_dir, "source.pdf")


def store_uploaded_source(item: sqlite3.Row, data: bytes, filename: str,
                          docs_dir: str) -> str:
    """Store a user-uploaded supporting document and return its repo-relative path.

    A PDF becomes ``source.pdf`` and is treated as the authoritative source
    document (the reader auto-converts it to markdown on next open). Any other
    file (``.md``/``.markdown``/``.html``/``.txt``) is stored with a safe
    filename and is NOT marked as the source document — it is supplementary
    material listed alongside the original. Overwrites a prior upload with the
    same target filename (idempotent).
    """
    try:
        raw = json.loads(item["raw_json"]) if item["raw_json"] else {}
    except (ValueError, TypeError):
        raw = {}
    if item["kind"] == "repo":
        rel_dir = os.path.join("repos", _repo_dir_slug(item, raw))
    else:
        rel_dir = os.path.join("papers", _paper_dir_slug(item, raw))
    abs_dir = os.path.join(docs_dir, rel_dir)
    os.makedirs(abs_dir, exist_ok=True)

    ext = os.path.splitext(filename or "")[1].lower()
    if ext == ".pdf":
        target = "source.pdf"
    else:
        safe_name = _safe_slug(os.path.basename(filename or "support"), "support")
        # _safe_slug strips a leading extension dot; re-attach the original ext
        # so a .md/.html/.txt upload keeps a recognizable suffix.
        if ext and not safe_name.endswith(ext):
            safe_name = safe_name + ext
        target = safe_name or "support"
    with open(os.path.join(abs_dir, target), "wb") as fh:
        fh.write(data)
    return os.path.join(rel_dir, target)


def clear_markdown_files(item: sqlite3.Row, docs_dir: str) -> None:
    """Drop the cached auto-conversion AND any user markdown for an item.

    Called after a new source document is uploaded so the reader regenerates
    the readable view from the new file instead of showing a stale cached one.
    Best-effort and non-fatal.
    """
    try:
        raw = json.loads(item["raw_json"]) if item["raw_json"] else {}
    except (ValueError, TypeError):
        raw = {}
    if item["kind"] == "repo":
        rel_dir = os.path.join("repos", _repo_dir_slug(item, raw))
    else:
        rel_dir = os.path.join("papers", _paper_dir_slug(item, raw))
    abs_dir = os.path.join(docs_dir, rel_dir)
    for name in ("article.auto.md", "article.md"):
        path = os.path.join(abs_dir, name)
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError as exc:
            print("[docs] clear markdown failed for item {}: {}".format(
                item["id"], exc))


def save_markdown(item: sqlite3.Row, content: str, docs_dir: str) -> str:
    """Write uploaded markdown as ``article.md`` in the item's doc folder.

    Returns the repo-relative path. Overwrites any prior attachment (idempotent).
    """
    rel_dir = _item_dir_rel(item)
    abs_dir = os.path.join(docs_dir, rel_dir)
    os.makedirs(abs_dir, exist_ok=True)
    with open(os.path.join(abs_dir, "article.md"), "w", encoding="utf-8") as fh:
        fh.write(content)
    return os.path.join(rel_dir, "article.md")


def read_markdown(item: sqlite3.Row, docs_dir: str) -> Optional[str]:
    """Return the stored markdown text for an item, or None when absent."""
    rel = item["markdown_path"]
    if not rel:
        return None
    abs_path = os.path.join(docs_dir, rel)
    if not os.path.isfile(abs_path):
        return None
    with open(abs_path, "r", encoding="utf-8") as fh:
        return fh.read()


def auto_markdown(item: sqlite3.Row, docs_dir: str, conn: sqlite3.Connection) -> Optional[str]:
    """Convert the item's stored original (PDF/HTML) to Markdown and cache it.

    Ensures the original document is on disk (reusing ``ensure_document``), then
    runs microsoft/markitdown over the stored ``source.pdf``/``source.html`` and
    writes the result as ``article.auto.md`` next to the item's other artifacts.
    Returns the converted text, or ``None`` when conversion is not possible
    (markitdown missing, no source document, or any conversion error) so the
    reader degrades gracefully to the abstract view.
    """
    doc_rel = ensure_document(item, docs_dir, conn)
    if not doc_rel:
        return None
    # The authoritative source document (source.pdf / source.html) converts
    # directly. For a PAPER, a user-uploaded .md/.html/.txt supporting file
    # converts too (markitdown passes markdown/text through essentially
    # unchanged). A repo's README.md is served by the repo README path and is
    # not an auto-conversion candidate; the abstract.txt fallback isn't either.
    base = os.path.basename(doc_rel)
    convertible_extra = None
    if base not in ("source.pdf", "source.html") and item["kind"] != "repo":
        ext = os.path.splitext(base)[1].lower()
        if ext in (".md", ".markdown", ".html", ".htm", ".txt"):
            convertible_extra = base
    if base not in ("source.pdf", "source.html") and convertible_extra is None:
        return None
    abs_src = os.path.join(docs_dir, doc_rel)
    if not os.path.isfile(abs_src):
        return None
    try:
        from markitdown import MarkItDown  # third-party; guarded import.

        text = MarkItDown().convert(abs_src).text_content
    except Exception as exc:  # ImportError or any conversion failure.
        print("[docs] auto markdown failed for item {}: {}".format(item["id"], exc))
        return None
    if text is None:
        return None
    # HTML sources keep image/link URLs relative to the page they were fetched
    # from (e.g. arXiv ``x1.png``), which 404 once rendered on another host, so
    # absolutize them against the known source URL before caching.
    if base == "source.html":
        try:
            raw = json.loads(item["raw_json"]) if item["raw_json"] else {}
        except (ValueError, TypeError):
            raw = {}
        text = _absolutize_md_urls(text, _source_url(item, raw) or "")
    rel_dir = os.path.dirname(doc_rel)
    abs_dir = os.path.join(docs_dir, rel_dir)
    try:
        os.makedirs(abs_dir, exist_ok=True)
        with open(os.path.join(abs_dir, "article.auto.md"), "w", encoding="utf-8") as fh:
            fh.write(text)
    except OSError as exc:
        print("[docs] auto markdown write failed for item {}: {}".format(item["id"], exc))
        return None
    return text


def read_auto_markdown(item: sqlite3.Row, docs_dir: str) -> Optional[str]:
    """Return the cached ``article.auto.md`` text for an item, or None when absent."""
    rel_dir = _item_dir_rel(item)
    abs_path = os.path.join(docs_dir, rel_dir, "article.auto.md")
    if not os.path.isfile(abs_path):
        return None
    with open(abs_path, "r", encoding="utf-8") as fh:
        return fh.read()


def has_repo_readme(item: sqlite3.Row, docs_dir: str) -> bool:
    """Cheap check: is this a repo whose stored README.md is on disk?

    A repo's README.md is already Markdown, so it can be served as the reader's
    markdown directly (no markitdown conversion). Only inspects ``doc_path``.
    """
    if item["kind"] != "repo":
        return False
    doc_rel = item["doc_path"]
    if not doc_rel or os.path.basename(doc_rel) != "README.md":
        return False
    return os.path.isfile(os.path.join(docs_dir, doc_rel))


def read_repo_readme(item: sqlite3.Row, docs_dir: str) -> Optional[str]:
    """Return the stored README.md text for a repo item, or None when absent."""
    if not has_repo_readme(item, docs_dir):
        return None
    with open(os.path.join(docs_dir, item["doc_path"]), "r", encoding="utf-8") as fh:
        return fh.read()


def _convertible_doc_names(item: sqlite3.Row, docs_dir: str) -> List[str]:
    """Basenames of on-disk documents that markitdown can convert for an item.

    The authoritative source document (``source.pdf`` / ``source.html``) is
    preferred; for a PAPER a user-uploaded ``.md``/``.markdown``/``.html``/
    ``.txt`` supporting file is also convertible (markitdown passes plain text
    and markdown through essentially unchanged). A repo's ``README.md`` is
    excluded — it is already markdown and is served directly by the repo
    README path, not via auto-conversion.
    """
    try:
        raw = json.loads(item["raw_json"]) if item["raw_json"] else {}
    except (ValueError, TypeError):
        raw = {}
    if item["kind"] == "repo":
        rel_dir = os.path.join("repos", _repo_dir_slug(item, raw))
    else:
        rel_dir = os.path.join("papers", _paper_dir_slug(item, raw))
    abs_dir = os.path.join(docs_dir, rel_dir)
    if not os.path.isdir(abs_dir):
        return []
    preferred = ("source.pdf", "source.html")
    out = []
    for name in preferred:
        if os.path.isfile(os.path.join(abs_dir, name)):
            out.append(name)
    if item["kind"] != "repo":
        for name in sorted(os.listdir(abs_dir)):
            if name in out or name == "README.md":
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext in (".md", ".markdown", ".html", ".htm", ".txt"):
                out.append(name)
    return out


def has_convertible_source(item: sqlite3.Row, docs_dir: str) -> bool:
    """Cheap check: is a real source doc already on disk that could convert?

    Does NOT fetch or convert — only inspects the stored ``doc_path``. Used by
    the item endpoint to advertise a ``markdown_available`` flag without work.
    """
    doc_rel = item["doc_path"]
    if doc_rel:
        base = os.path.basename(doc_rel)
        if base in ("source.pdf", "source.html"):
            return os.path.isfile(os.path.join(docs_dir, doc_rel))
        # A user-uploaded markdown/text/html supporting file (papers only).
        if item["kind"] != "repo" and base != "README.md":
            ext = os.path.splitext(base)[1].lower()
            if ext in (".md", ".markdown", ".html", ".htm", ".txt"):
                return os.path.isfile(os.path.join(docs_dir, doc_rel))
    # A supporting upload may live alongside the source even when doc_path
    # still points at a fetched original (papers only).
    return bool(_convertible_doc_names(item, docs_dir))


def ensure_document(item: sqlite3.Row, docs_dir: str, conn: sqlite3.Connection) -> Optional[str]:
    """Ensure the original document is on disk; set doc_path. Idempotent.

    Returns the relative doc_path, or None if nothing could be stored.
    """
    existing = item["doc_path"]
    have_existing = bool(existing) and os.path.isfile(os.path.join(docs_dir, existing))

    try:
        raw = json.loads(item["raw_json"]) if item["raw_json"] else {}
    except (ValueError, TypeError):
        raw = {}

    if have_existing:
        # Idempotent, except for two self-healing upgrades of a DOWNLOADED doc:
        # an abstract-only item that can now reach the real PDF, and a
        # PDF-derived item that can be replaced by the clean full-text HTML
        # (which drops the watermark/spacing damage that breaks rendering).
        # A doc the USER uploaded (doc_uploaded=1) is authoritative — we never
        # refetch or replace it.
        if item["doc_uploaded"] if "doc_uploaded" in item.keys() else False:
            return existing
        basename = os.path.basename(existing)
        upgradable = item["kind"] != "repo" and (
            (basename == "abstract.txt" and _arxiv_pdf_url(item, raw) is not None)
            or (basename in ("abstract.txt", "source.pdf")
                and _arxiv_html_url(item, raw) is not None)
        )
        if not upgradable:
            return existing

    try:
        if item["kind"] == "repo":
            doc_rel = _store_repo(item, raw, docs_dir)
        else:
            doc_rel = _store_paper(item, raw, docs_dir)
    except OSError as exc:
        print("[docs] store failed for item {}: {}".format(item["id"], exc))
        return existing if have_existing else None

    if doc_rel:
        # When the source document changed (e.g. an arXiv item upgraded from the
        # watermarked PDF to the clean HTML), drop the stale cached conversion so
        # the next markdown read regenerates article.auto.md from the new source.
        if have_existing and os.path.basename(doc_rel) != os.path.basename(existing):
            stale = os.path.join(docs_dir, os.path.dirname(doc_rel), "article.auto.md")
            try:
                if os.path.isfile(stale):
                    os.remove(stale)
            except OSError:
                pass
        conn.execute(
            "UPDATE corpus_item SET doc_path=?, doc_fetched_at=? WHERE id=?",
            (doc_rel, utcnow(), item["id"]),
        )
        conn.commit()
    return doc_rel
