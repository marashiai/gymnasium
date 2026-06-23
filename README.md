# Gymnasium

My personal university — a place to discover, track, and study papers and
articles in the fields I'm learning about.

The goal is to make it easy to find relevant research (e.g. from
[arXiv](https://arxiv.org/)) and keep what I'm reading organized.

![The Gymnasium university web app — the Papers feed](docs/screenshots/university.png)

## Ideas / Roadmap

- [ ] Search and pull papers from arXiv by topic, author, or keyword
- [ ] Keep a reading list with notes and status (to-read / reading / done)
- [ ] Track the fields and subfields I'm currently studying
- [ ] Surface new/related papers based on what I've saved
- [ ] Export summaries and notes

## Structure

_To be defined as the project grows._

## labpapers — GenAI lab-paper tracker (giant-weight)

`labpapers` is a runnable CLI that helps you stand on the shoulders of giants.
Its organizing principle is **author prominence** ("giant-weight"): one shared
data set both (1) ranks the most influential researchers currently at six labs
and (2) sorts a comprehensive, impact-ranked report of their recent GenAI papers
so that work by/with giants rises to the top.

Labs tracked: **Anthropic, Meta, Google (incl. Google DeepMind), OpenAI,
Z.AI (Zhipu AI), DeepSeek**. Scope: GenAI (LLMs/foundation models, agents &
tool use, multimodal & generation). Window is configurable (default 7 days).

### How it works (source-agnostic)

Sourcing is **source-agnostic**: every source implements a small `LabSource`
protocol (`labpapers/sources/base.py`) and returns papers already tagged with
the lab(s) it covers. The pipeline runs the sources configured per-lab
(`config.SOURCES`), unions and dedups the results (by versionless arXiv id, DOI,
canonical source URL, then normalized title), and runs one shared
prominence + scoring + report over everything. The default sources are:

- **OpenAlex institution source** — clean institution-id filtering for the labs
  OpenAlex tags well (OpenAI, Meta, Google/DeepMind, Zhipu).
- **arXiv affiliation source** — arXiv exposes no usable author-affiliation
  filter, so recent arXiv papers are resolved per-paper: first via OpenAlex DOI
  lookup (matching institution ids, regexes over raw affiliation strings, **and**
  an org-collective byline such as `DeepSeek-AI`), then, for whatever OpenAlex
  misses or returns with empty affiliations, via a fallback that parses the
  author/affiliation frontmatter of `arxiv.org/html/<id>`. This is the primary
  path for DeepSeek and a secondary path for Anthropic.
- **Anthropic site source** — OpenAlex reports *zero* works for Anthropic and
  only a fraction of its work lands on arXiv, so this source reads Anthropic's
  own [research listing](https://www.anthropic.com/research) (plus the sitemap,
  to catch items the listing paginates away). Each in-window publication's
  detail page yields the title, publish date, subject, best-effort author byline,
  and summary. Research subjects are in-scope by default (a configurable exclude
  list drops pure Policy posts); author names are matched to OpenAlex by name for
  best-effort prominence, falling back to *prominence unavailable* (date-sorted).
- **Author-prominence engine** — the single source of "giant-weight". Each
  matched paper's authors are looked up in OpenAlex (`cited_by_count`, h-index,
  works count). `impact_signal(paper)` sorts by
  `(max author citations, sum author citations, paper citations, date)`, and a
  paper is flagged a *giant-author paper* when any author exceeds the
  thresholds (default: citations ≥ 10000 or h-index ≥ 40). The same data powers
  the Key People ranking.

### Install

Requires Python 3.10+ (for markitdown markdown auto-conversion).

```bash
pip install -e .            # add ".[dev]" for the test deps
```

### Usage

```bash
labpapers --days 7 --mailto you@example.com
```

Reports are written to `reports/` as Markdown (Key People first, then the
Netherlands GenAI map, then papers grouped by lab and sorted by impact signal)
plus a JSON sidecar.

### Netherlands GenAI map (watchlist)

Above the per-lab sections, the report renders a **Netherlands GenAI map**: a
curated watchlist of NL-based people and research institutions working on GenAI
*independently of the six big labs*, plus a reference appendix of NL GenAI
companies. It reuses the same engines — OpenAlex resolution, the STRICT GenAI
topic filter, the prominence engine, and transitive dedup — so watchlist papers
union into the same pool (a paper can carry both a lab and a watchlist tag).

- **People** (`labpapers/watchlist.py`) are resolved by name to an OpenAlex
  AI/CS profile (the resolved affiliation is shown; names we want a human to
  double-check carry a *verify affiliation* marker), then their in-window GenAI
  papers are listed. Dutch-origin researchers based abroad (e.g. Kingma) are
  rendered in a distinct sub-bucket and not counted as NL.
- **Institutions** are resolved to OpenAlex ids and scanned with the shared
  institution-works engine. Because a whole university spans every field, an
  extra positive CS-primary-field gate keeps these sections to actual GenAI work.
- **Companies** are a static reference list (not paper-tracked), with an
  exclusions note.

Use `--no-watchlist` to omit the section. Per-entry resolution failures degrade
gracefully (an unresolved name/institution is flagged, never fatal) and lookups
are cached.

Useful flags:

- `--days N` — lookback window (default 7)
- `--labs anthropic,deepseek` — restrict to specific labs
- `--no-watchlist` — skip the Netherlands GenAI map / watchlist section
- `--no-fulltext` — skip the `arxiv.org/html` fallback for a faster, smaller run
- `--format md|json|both`, `--out DIR`
- `--top-people N` (default 25)
- `--giant-cited-by N` / `--giant-hindex N` — tune the giant thresholds
- `--mailto` / `OPENALEX_MAILTO` — contact email for the OpenAlex polite pool

Run `labpapers --help` for the full list, and `pytest` to run the offline test
suite (it uses saved fixtures — no live network).

## labrepos — GitHub giant-attribution tracker

`labrepos` is a self-contained sibling of `labpapers` that applies the same
method to **GitHub** instead of arXiv/OpenAlex. It surfaces **new and
recently-active repositories** created or contributed to by "giants" — the top
AI research labs *and* the leading coding / dev-AI-tooling orgs and people — so
you track the bleeding edge instead of re-discovering already-famous,
mega-starred projects.

The organizing principle is **giant-attribution + recency + topic**, mirroring
labpapers' lab-attribution: a repo qualifies because it is owned by a configured
giant org, or owned/recently-pushed-to by a watchlist person — not because it is
popular. A configurable window (default 30 days) keeps it recent, and a STRICT
GenAI/agentic-coding topic filter keeps it on-scope (broad orgs like
`microsoft` / `facebookresearch` / `github` / `jetbrains` carry a lot of
non-GenAI work). Instead of "giant-weight", a **freshness signal** floats the
newest work to the top — repos are sorted by `(new-in-window, pushed_at, stars)`
descending — while already-notable repos (stars ≥ 500 by default) get a 🌟, the
GitHub analogue of the giant-author flag.

Giants tracked (each separately selectable):

- **Labs:** Anthropic, Meta, Google (incl. DeepMind), OpenAI, Z.AI/Zhipu,
  DeepSeek.
- **Coding / dev-AI:** Microsoft, GitHub (its own giant, not folded under
  Microsoft), Cursor, Cline, OpenCode, OpenHands, Goose, Aider, Continue,
  Sourcegraph, Roo Code, LangChain, LlamaIndex, CrewAI, Hugging Face, Vercel,
  Mistral, Replit, JetBrains.

### How it works (source-agnostic)

Like labpapers, sourcing is source-agnostic: every source implements a small
`RepoSource` protocol (`labrepos/sources/base.py`) and returns repos already
tagged with the giant(s) it covers. The pipeline unions and dedups by repo
`full_name` (a repo matched by several giants/sources carries all tags),
computes the freshness signal, sorts, and groups. The sources are:

- **Org repos** (primary) — for each giant org, a single `sort=pushed`
  descending scan of `/orgs/{org}/repos` that **stops early** the moment it sees
  a repo pushed before the window. Because GitHub guarantees
  `pushed_at >= created_at`, this one pass is complete for the "new OR active"
  definition (forks are dropped, since their `pushed_at` can reflect upstream).
  The early stop is what bounds huge orgs without pulling every page.
- **User repos** — repos *owned* by watchlist people (`labrepos/watchlist.py`),
  same window + topic filtering; tagged to the synthetic **Watchlist people**
  bucket (plus the owner's giant if it is itself configured).
- **User events** — the "contributing to" signal: repos a watchlist person
  recently pushed to (via the public Events API) but does not necessarily own.

Every per-org/per-user lookup is best-effort: a 404 or error on one giant
degrades coverage and is skipped, never fatal.

### Install & usage

```bash
pip install -e .            # add ".[dev]" for the test deps

labrepos --days 30
GITHUB_TOKEN=$(gh auth token) labrepos --giants anthropic,cline
```

Set `GITHUB_TOKEN` (or `GH_TOKEN`) to avoid GitHub's 60 req/hr unauthenticated
limit. Reports are written to `reports/` as `labrepos_<date>_<days>d.md` (repos
grouped by giant, labs first then coding then watchlist people, sorted by
freshness) plus a JSON sidecar. Useful flags:

- `--days N` — lookback window (default 30)
- `--giants anthropic,cline` — restrict to specific giant keys
- `--no-keyword-filter` — keep every in-window repo (skip the topic filter)
- `--include-forks` — include forks (dropped by default)
- `--notable-stars N` — star threshold for the 🌟 mark (default 500)
- `--no-people` — skip the watchlist-people sources
- `--max-pages N`, `--format md|json|both`, `--out DIR`, `--cache-dir DIR`

Run `labrepos --help` for the full list. The watchlist is a configurable starter
set meant to be edited. The offline test suite (`pytest`) uses saved GitHub
fixtures — no live network.

## university — the personal AI university (web app)

`university` is the reading-and-studying front end that sits on top of the two
trackers. It is a stdlib-only Python + SQLite backend (no new third-party
dependencies) plus a mobile-first web UI, served together by one command:

```bash
pip install -e .
gymnasium adduser maya hunter2          # plaintext, alphanumeric-only
gymnasium --ingest-on-start             # serves http://127.0.0.1:8077
```

What it does:

- **Auth gate** — a plaintext, alphanumeric login (single-tenant, personal).
  Every `/api/*` route except login requires a 30-day cookie token.
- **Corpus** — ingests the `labpapers` / `labrepos` JSON report sidecars from
  `reports/` into a SQLite `corpus_item` table (deduped, impact normalized to
  0–100). A UI button kicks off a background **refresh** that re-runs the
  trackers in-process and re-ingests.
- **Document store** — opening an item (or saving a fact) fetches the original
  paper/repo document once and keeps it on disk under `data/documents/` so a
  saved fact always points at a concrete local file.
- **AI via opencode only** — all AI goes through the `opencode` CLI
  (`OPENCODE_BIN`), with the model list pulled dynamically from
  `opencode models` (no hardcoded providers). It produces readable summaries,
  explains/summarizes/answers about any selected span, and suggests
  knowledge-map links.
- **Reading flow** — a faithful build of the Gymnasium design handoff: a feed,
  a reader with select-any-span → Explain / Summarize / Ask, a conversation
  panel whose whole thread can be **saved** to the knowledge base (FTS5 search
  across every turn), and an interactive **knowledge map** (drag, manual links,
  AI-suggested links). One fluid layout spans phone / tablet / desktop; light
  and dark themes persist.

`pytest` covers the backend offline (AI stubbed via a fake `opencode`, network
patched, trackers stubbed). `data/` (the SQLite DB and the document store) is
git-ignored.

## Notes

This is a private repository for personal study and research curation.
