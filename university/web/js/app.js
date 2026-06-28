/* Gymnasium University — app shell, router and screens.
   Translated faithfully from GymApp.dc.html to vanilla JS. One fluid layout;
   the rail/tabs/panel-mode follow CSS media queries (matchMedia for the
   sheet/side panel + scrim). */
(function () {
  var S = window.State;
  var $ = function (id) { return document.getElementById(id); };

  // ---- tiny helpers --------------------------------------------------------
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
  function ico(paths, attrs) {
    return '<svg viewBox="0 0 24 24" class="ico" ' + (attrs || '') + '>' + paths + '</svg>';
  }
  var ICON = {
    sun: '<circle cx="12" cy="12" r="4"></circle><path d="M12 2v2M12 20v2M4 12H2M22 12h-2M5 5l1.5 1.5M17.5 17.5 19 19M19 5l-1.5 1.5M6.5 17.5 5 19"></path>',
    moon: '<path d="M21 13a8 8 0 1 1-9.5-9 6.5 6.5 0 0 0 9.5 9z"></path>',
    back: '<path d="M15 6l-6 6 6 6"></path>',
    arrow: '<path d="M5 12h13M13 6l6 6-6 6"></path>',
    bookmark: '<path d="M6 4h12v16l-6-4-6 4V4z"></path>',
    spark: '<path d="M12 3l1.7 5.1L19 10l-5.3 1.9L12 17l-1.7-5.1L5 10l5.3-1.9z"></path>',
    link: '<path d="M10 13a5 5 0 0 0 7 0l2-2a5 5 0 0 0-7-7l-1 1"></path><path d="M14 11a5 5 0 0 0-7 0l-2 2a5 5 0 0 0 7 7l1-1"></path>',
    external: '<path d="M14 4h6v6"></path><path d="M20 4l-9 9"></path><path d="M19 14v5a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"></path>',
    search: '<circle cx="11" cy="11" r="7"></circle><path d="M21 21l-4-4"></path>',
    close: '<path d="M6 6l12 12M18 6L6 18"></path>',
    send: '<path d="M5 12h13M13 6l6 6-6 6"></path>',
    plus: '<path d="M12 5v14M5 12h14"></path>',
    check: '<path d="M5 12l4 4 10-10"></path>',
    chevron: '<path d="M6 9l6 6 6-6"></path>',
    refresh: '<path d="M21 12a9 9 0 1 1-2.6-6.4"></path><path d="M21 4v5h-5"></path>',
    trash: '<path d="M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13M10 11v6M14 11v6"></path>'
  };

  // ---- theme ---------------------------------------------------------------
  function applyTheme() {
    var app = $('app');
    if (S.theme === 'dark') app.classList.add('dark'); else app.classList.remove('dark');
    var light = S.theme === 'light';
    $('topbarThemeIcon').innerHTML = light ? ICON.moon : ICON.sun;
    var rti = $('railThemeIcon'); if (rti) rti.innerHTML = light ? ICON.moon : ICON.sun;
    var rtl = $('railThemeLabel'); if (rtl) rtl.textContent = light ? 'Dark' : 'Light';
  }
  function toggleTheme() { S.theme = S.theme === 'light' ? 'dark' : 'light'; S.saveTheme(); applyTheme(); }

  // ---- collapsible rail ----------------------------------------------------
  function applyRail() {
    $('app').classList.toggle('rail-collapsed', !!S.railCollapsed);
    var rc = $('railCollapse');
    if (rc) rc.setAttribute('aria-label', S.railCollapsed ? 'Expand sidebar' : 'Collapse sidebar');
  }
  function toggleRail() { S.railCollapsed = !S.railCollapsed; S.saveRail(); applyRail(); }

  // ---- account menu (profile / log out) ------------------------------------
  function accountMenuOpen() { return !$('accountMenu').hidden; }
  function closeAccountMenu() {
    var m = $('accountMenu');
    m.hidden = true; m.innerHTML = '';
  }
  function openAccountMenu(trigger) {
    var m = $('accountMenu');
    var name = (S.user && S.user.username) || $('railUserName').textContent || 'You';
    m.innerHTML =
      '<div class="account-name">@' + esc(name) + '</div>' +
      '<div class="account-sub">Signed in</div>' +
      '<button id="accountLogout" type="button">' +
        ico('<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"></path><path d="M16 17l5-5-5-5"></path><path d="M21 12H9"></path>', 'style="width:17px;height:17px"') +
        'Log out</button>';
    m.hidden = false;
    // Position near the trigger: above it for the rail-user (bottom-left), below
    // it for the topbar account button (top-right). Off-screen edges are clamped.
    var r = trigger.getBoundingClientRect();
    var mw = m.offsetWidth, mh = m.offsetHeight;
    var vw = window.innerWidth, vh = window.innerHeight;
    var below = r.top < vh / 2;
    var top = below ? r.bottom + 6 : r.top - mh - 6;
    var left = trigger.id === 'topbarAccount' ? r.right - mw : r.left;
    left = Math.max(8, Math.min(left, vw - mw - 8));
    top = Math.max(8, Math.min(top, vh - mh - 8));
    m.style.left = left + 'px';
    m.style.top = top + 'px';
  }
  function toggleAccountMenu(trigger) {
    if (accountMenuOpen()) closeAccountMenu(); else openAccountMenu(trigger);
  }
  function logout() {
    closeAccountMenu();
    API.logout().then(function () { window.location.href = '/'; })
      .catch(function () { window.location.href = '/'; });
  }

  // ---- toast ---------------------------------------------------------------
  var toastTimer = null;
  function toast(msg) {
    var t = $('toast');
    t.innerHTML = ico(ICON.check, 'style="stroke:var(--grass-500);width:16px;height:16px"') + esc(msg);
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.hidden = true; }, 2400);
  }

  // ---- nav -----------------------------------------------------------------
  var SECTION = { papers: 'Papers', repos: 'Repos', added: 'Added', reader: 'Reader', saved: 'Knowledge base', concept: 'Knowledge base', map: 'Knowledge map' };
  function kindFor(screen) { return screen === 'repos' ? 'repo' : 'paper'; }
  function setNavActive() {
    // The concept detail view lives under the Knowledge base tab.
    var screen = S.screen === 'concept' ? 'saved' : S.screen;
    document.querySelectorAll('.rail-nav[data-screen]').forEach(function (b) {
      b.classList.toggle('on', b.dataset.screen === screen);
    });
    document.querySelectorAll('.gym-tab[data-screen]').forEach(function (b) {
      b.classList.toggle('on', b.dataset.screen === screen);
    });
    $('topbarSection').textContent = SECTION[screen] || '';
  }
  function go(screen) {
    S.screen = screen;
    closePanel();
    render();
    $('scroll').scrollTop = 0;
  }

  // ====================================================================
  // FEED
  // ====================================================================
  function recency(iso) {
    if (!iso) return '';
    var then = new Date(iso); if (isNaN(then)) return '';
    var days = Math.floor((Date.now() - then.getTime()) / 86400000);
    if (days <= 0) return 'today';
    if (days < 7) return days + 'd ago';
    if (days < 30) return Math.floor(days / 7) + 'w ago';
    return Math.floor(days / 30) + 'mo ago';
  }
  function feedCard(item, opts) {
    opts = (opts && opts.removable) ? opts : null;  // map() passes an index 2nd arg
    var kindLabel = item.kind === 'repo' ? 'Repo' : 'Paper';
    var why = S.density === 'comfort' && item.why
      ? '<p style="font:500 15px/1.55 var(--font-sans);color:var(--fg-2)">' + esc(item.why) + '</p>' : '';
    // Repo cards show stars + language; paper cards show the source line.
    var metaLeft;
    if (item.kind === 'repo') {
      var bits = [];
      if (item.stars != null) bits.push('★ ' + item.stars.toLocaleString());
      if (item.language) bits.push(esc(item.language));
      metaLeft = '<span style="font:600 12px/1.4 var(--font-sans);color:var(--fg-3)">' + (bits.join(' · ') || esc(item.source || '')) + '</span>';
    } else {
      metaLeft = '<span style="font:600 12px/1.4 var(--font-sans);color:var(--fg-3)">' + esc(item.source || '') + '</span>';
    }
    var ratingLabel = item.kind === 'repo' ? 'Stars' : 'Impact';
    var removeBtn = opts
      ? '<button class="gym-press feed-remove" data-id="' + item.id + '" type="button" aria-label="Remove" title="Remove" style="display:inline-flex;align-items:center;justify-content:center;width:30px;height:30px;border:none;background:none;border-radius:8px;cursor:pointer;color:var(--fg-muted)">' + ico(ICON.trash, 'style="width:17px;height:17px"') + '</button>'
      : '';
    return '' +
      '<article class="gym-card feed-card" data-id="' + item.id + '" style="background:var(--bg-surface);border:1px solid var(--border-hair);border-radius:14px;box-shadow:var(--shadow-1);padding:16px 18px;cursor:pointer;display:flex;flex-direction:column;gap:10px">' +
        '<div style="display:flex;align-items:center;gap:10px">' +
          '<span style="display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:999px;font:700 11px/1.4 var(--font-sans);background:var(--spark-100);color:var(--spark-700)">' + kindLabel + '</span>' +
          metaLeft +
          '<span style="margin-left:auto;font:600 12px/1.4 var(--font-sans);color:var(--fg-muted)">' + esc(recency(item.published_at)) + '</span>' +
          removeBtn +
        '</div>' +
        '<h3 style="font:700 19px/1.3 var(--font-sans);color:var(--fg-1);letter-spacing:-.01em">' + esc(item.title) + '</h3>' +
        why +
        '<div style="display:flex;align-items:center;gap:12px;margin-top:2px">' +
          '<span style="font:700 12px/1 var(--font-sans);color:var(--fg-3);font-variant-numeric:tabular-nums">' + ratingLabel + ' ' + (item.signal || 0) + '</span>' +
          '<span style="position:relative;flex:1;max-width:140px;height:6px;border-radius:3px;background:var(--paper-200);overflow:hidden"><span style="position:absolute;left:0;top:0;bottom:0;width:' + (item.signal || 0) + '%;background:var(--spark-500);border-radius:3px"></span></span>' +
          '<span style="flex:1"></span>' +
          '<button class="gym-press feed-read btn-spark" data-id="' + item.id + '">Read' + ico(ICON.arrow, 'style="width:16px;height:16px;stroke-width:2.2"') + '</button>' +
        '</div>' +
      '</article>';
  }
  // Build one filter <select> from a facet list ({values:[{value,count}], capped}).
  function filterSelect(name, label, facet, current) {
    var opts = '<option value="">' + esc(label) + '</option>';
    var values = (facet && facet.values) || [];
    // Keep the active value selectable even if it fell outside the capped list.
    var present = false;
    values.forEach(function (v) {
      if (v.value === current) present = true;
      opts += '<option value="' + esc(v.value) + '"' + (v.value === current ? ' selected' : '') + '>' + esc(v.value) + ' (' + v.count + ')</option>';
    });
    if (current && !present) opts += '<option value="' + esc(current) + '" selected>' + esc(current) + '</option>';
    return '<select class="feed-filter" data-filter="' + name + '" style="height:38px;border:1.5px solid var(--border-default);background:var(--bg-input);color:var(--fg-1);border-radius:10px;padding:0 10px;font:500 16px/1 var(--font-sans);cursor:pointer;max-width:200px">' + opts + '</select>';
  }
  function renderFeed(kind) {
    var fs = S.feeds[kind];
    var isRepo = kind === 'repo';
    var title = isRepo ? 'Repos' : 'Papers';
    var blurb = isRepo
      ? 'Repositories from tracked labs. Search, filter and sort.'
      : 'Papers from the frontier labs. Search, filter and sort.';
    var cards = fs.items.length
      ? fs.items.map(feedCard).join('')
      : '<div style="text-align:center;padding:48px 24px;color:var(--fg-3);font:500 15px/1.5 var(--font-sans)">No items match. Try clearing the search or filters, or Refresh feed.</div>';
    var filters;
    if (isRepo) {
      filters = filterSelect('company', 'All companies', fs.facets && fs.facets.companies, fs.filters.company) +
                filterSelect('language', 'All languages', fs.facets && fs.facets.languages, fs.filters.language);
    } else {
      filters = filterSelect('author', 'All authors', fs.facets && fs.facets.authors, fs.filters.author) +
                filterSelect('company', 'All companies', fs.facets && fs.facets.companies, fs.filters.company) +
                filterSelect('publication', 'All publications', fs.facets && fs.facets.publications, fs.filters.publication);
    }
    return '' +
      '<div style="display:flex;align-items:flex-end;justify-content:space-between;gap:14px;flex-wrap:wrap;margin-bottom:16px">' +
        '<div>' +
          '<h1 style="font:700 32px/1.1 var(--font-display);letter-spacing:-.02em;color:var(--fg-1)">' + title + '</h1>' +
          '<p style="font:500 15px/1.5 var(--font-sans);color:var(--fg-3);margin-top:6px">' + blurb + '</p>' +
        '</div>' +
        '<div class="seg-row">' +
          '<button class="gym-seg' + (S.density === 'comfort' ? ' on' : '') + '" data-d="comfort">Comfort</button>' +
          '<button class="gym-seg' + (S.density === 'compact' ? ' on' : '') + '" data-d="compact">Compact</button>' +
        '</div>' +
      '</div>' +
      '<div style="position:relative;margin-bottom:12px">' +
        ico(ICON.search, 'style="position:absolute;left:13px;top:50%;transform:translateY(-50%);width:18px;height:18px;stroke:var(--ink-400);pointer-events:none"') +
        '<input class="gym-term-input" id="feedSearch" value="' + esc(fs.q) + '" placeholder="Search ' + (isRepo ? 'repos' : 'papers') + '…" style="padding-left:40px;height:46px" />' +
      '</div>' +
      '<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:18px">' +
        '<div class="seg-row">' +
          '<button class="gym-seg feed-sort' + (fs.sort === 'recency' ? ' on' : '') + '" data-sort="recency">Recency</button>' +
          '<button class="gym-seg feed-sort' + (fs.sort === 'rating' ? ' on' : '') + '" data-sort="rating">Rating</button>' +
        '</div>' +
        '<span style="flex:1"></span>' + filters +
      '</div>' +
      '<div style="display:flex;flex-direction:column;gap:14px">' + cards + '</div>';
  }

  // ====================================================================
  // ADDED (self-added articles)
  // ====================================================================
  function renderAdded() {
    var items = S.feeds.added.items;
    var cards = items.length
      ? items.map(function (it) { return feedCard(it, { removable: true }); }).join('')
      : '<div style="text-align:center;padding:44px 24px;color:var(--fg-3);font:500 15px/1.5 var(--font-sans)">No items yet. Paste an article or GitHub repo link above to add your first one.</div>';
    return '' +
      '<h1 style="font:700 32px/1.1 var(--font-display);letter-spacing:-.02em;color:var(--fg-1)">Added</h1>' +
      '<p style="font:500 15px/1.5 var(--font-sans);color:var(--fg-3);margin-top:6px">Papers and repos you added with a link, or a PDF you uploaded. Open one to read it like any other; remove one with the trash button.</p>' +
      '<form id="addForm" style="display:flex;flex-direction:column;gap:10px;margin:18px 0 22px;background:var(--bg-surface);border:1px solid var(--border-hair);border-radius:14px;box-shadow:var(--shadow-1);padding:16px 18px">' +
        '<input class="gym-term-input" id="addUrl" type="url" placeholder="Paste an article or GitHub repo link…" autocomplete="off" />' +
        '<input class="gym-term-input" id="addTitle" type="text" placeholder="Title (optional)" autocomplete="off" />' +
        '<div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap">' +
          '<button class="gym-press" id="addPdfBtn" type="button" style="display:inline-flex;align-items:center;gap:6px;height:36px;padding:0 14px;border:1px solid var(--border-hair);background:var(--bg-surface);border-radius:999px;cursor:pointer;font:700 13px/1 var(--font-sans);color:var(--fg-2)">' + ico(ICON.plus, 'style="width:15px;height:15px"') + 'Upload PDF</button>' +
          '<input type="file" id="addPdf" accept=".pdf,application/pdf" hidden />' +
          '<button class="btn-spark" id="addBtn" type="submit">' + ico(ICON.plus, 'style="width:16px;height:16px;stroke-width:2.2"') + 'Add article</button>' +
        '</div>' +
      '</form>' +
      '<div style="display:flex;flex-direction:column;gap:14px">' + cards + '</div>';
  }
  function loadAdded() {
    return API.feed(null, { added: 1, sort: 'recency', limit: 100 }).then(function (res) {
      S.feeds.added.items = res.items || [];
      if (S.screen === 'added') render();
    }).catch(function () {});
  }
  function addArticle() {
    var urlEl = $('addUrl'), titleEl = $('addTitle');
    var url = (urlEl && urlEl.value || '').trim();
    if (!url) { toast('Paste a link first'); if (urlEl) urlEl.focus(); return; }
    var title = (titleEl && titleEl.value || '').trim();
    var btn = $('addBtn');
    if (btn) btn.disabled = true;
    var payload = { url: url };
    if (title) payload.title = title;
    API.addItem(payload).then(function (res) {
      toast('Article added');
      loadAdded();
      // Open the new article straight away (returning to Added on Back).
      if (res && res.id) { S.readerReturn = '#/added'; navigate('reader', { id: res.id }); }
    }).catch(function () { toast('Could not add'); if (btn) btn.disabled = false; });
  }
  function uploadPdf(file) {
    if (!file) return;
    var titleEl = $('addTitle');
    var title = (titleEl && titleEl.value || '').trim();
    var btn = $('addPdfBtn');
    if (btn) btn.disabled = true;
    toast('Uploading PDF…');
    API.addItemFile(file, title).then(function (res) {
      toast('PDF added');
      loadAdded();
      // Open the new paper straight away (returning to Added on Back).
      if (res && res.id) { S.readerReturn = '#/added'; navigate('reader', { id: res.id }); }
    }).catch(function () { toast('Could not upload PDF'); }).then(function () {
      if (btn) btn.disabled = false;
    });
  }
  function removeAdded(id) {
    if (!id) return;
    if (!window.confirm('Remove this from your Added list?')) return;
    API.deleteItem(id).then(function () {
      toast('Removed');
      loadAdded();
    }).catch(function () { toast('Could not remove'); });
  }

  // ====================================================================
  // READER
  // ====================================================================
  function termRegex() {
    var terms = (S.summaryTerms || []).slice();
    terms = terms.filter(function (t) { return t && t.length >= 3; });
    if (!terms.length) return null;
    // longest-first so multi-word terms win.
    terms.sort(function (a, b) { return b.length - a.length; });
    var escd = terms.map(function (t) { return t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); });
    return new RegExp('\\b(' + escd.join('|') + ')\\b', 'gi');
  }
  function paragraphHTML(text) {
    var re = termRegex();
    if (!re) return esc(text);
    var out = '', last = 0, m;
    while ((m = re.exec(text)) !== null) {
      out += esc(text.slice(last, m.index));
      out += '<span class="gym-term" data-term="' + esc(m[0]) + '">' + esc(m[0]) + '</span>';
      last = m.index + m[0].length;
      if (m.index === re.lastIndex) re.lastIndex++;
    }
    out += esc(text.slice(last));
    return out;
  }
  // Underline every occurrence of a known KB concept label in the rendered
  // reader body. Whole-word, case-insensitive, longest-match-first; text inside
  // links/code and already-marked terms is skipped. Tapping a concept span
  // shows its cached definition with zero AI (see openConceptPanel).
  function conceptRegex() {
    var labels = Object.keys(S.concepts || {})
      .map(function (k) { return S.concepts[k].label; })
      .filter(function (l) { return l && l.length >= 3; });
    if (!labels.length) return null;
    labels.sort(function (a, b) { return b.length - a.length; });
    var escd = labels.map(function (t) { return t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); });
    return new RegExp('\\b(' + escd.join('|') + ')\\b', 'gi');
  }
  function underlineConcepts(root) {
    var re = conceptRegex();
    if (!re || !root) return;
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: function (node) {
        if (!node.nodeValue || !node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
        var p = node.parentNode;
        while (p && p !== root) {
          var tag = p.nodeName;
          if (tag === 'A' || tag === 'CODE' || tag === 'PRE' || tag === 'SCRIPT' || tag === 'STYLE')
            return NodeFilter.FILTER_REJECT;
          if (p.classList && p.classList.contains('gym-term')) return NodeFilter.FILTER_REJECT;
          p = p.parentNode;
        }
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    var targets = [], n;
    while ((n = walker.nextNode())) targets.push(n);
    targets.forEach(function (node) {
      var text = node.nodeValue;
      re.lastIndex = 0;
      if (!re.test(text)) return;
      re.lastIndex = 0;
      var frag = document.createDocumentFragment(), last = 0, m;
      while ((m = re.exec(text)) !== null) {
        if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
        var span = document.createElement('span');
        span.className = 'gym-term';
        span.setAttribute('data-concept', m[0]);
        span.textContent = m[0];
        frag.appendChild(span);
        last = m.index + m[0].length;
        if (m.index === re.lastIndex) re.lastIndex++;
      }
      if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
      node.parentNode.replaceChild(frag, node);
    });
  }

  // The ONE hardened markdown renderer lives in md.js (window.MD) so the article
  // body, attached markdown, repo README and chat all share the same
  // bracket-hardening (against snarkdown's runaway-link bug) and <script>/on*=
  // sanitization. Fall back to escaped text if md.js somehow failed to load.
  function renderMarkdownHTML(md) {
    if (window.MD) return window.MD.renderMarkdownHTML(md);
    return esc(md);
  }
  function renderReader() {
    var it = S.item;
    if (!it) return '<p>Loading…</p>';
    var kindLabel = it.kind === 'repo' ? 'Repo' : 'Paper';
    var tags = (it.tags || []).map(function (t) {
      return '<span style="display:inline-flex;align-items:center;padding:3px 10px;border-radius:999px;font:600 11px/1.4 var(--font-sans);background:var(--paper-200);color:var(--fg-3)">' + esc(t) + '</span>';
    }).join('');
    var bodyHTML;
    if (it._markdownHTML != null) {
      // Markdown (uploaded or auto-converted) becomes the reader body (no
      // [[term]] re-marking). A small label shows where it came from.
      var srcLabel = it._markdownSource === 'user'
        ? 'Your uploaded markdown'
        : it._markdownSource === 'readme'
          ? 'README' : 'Auto-converted from the original';
      var labelHTML = '<div style="display:inline-flex;align-items:center;gap:6px;font:600 12px/1.3 var(--font-sans);color:var(--fg-muted);margin-bottom:14px">' + esc(srcLabel) + '</div>';
      bodyHTML = labelHTML + '<div class="gym-md" style="font:500 17px/1.7 var(--font-sans);color:var(--fg-1);max-width:64ch">' + it._markdownHTML + '</div>';
    } else if (it._markdownLoading) {
      bodyHTML = '<div style="display:flex;align-items:center;gap:8px;color:var(--fg-3);font:500 14px/1.5 var(--font-sans)">' + ico(ICON.refresh, 'class="ico spin" style="width:16px;height:16px"') + 'Converting the original to a readable view…</div>';
    } else {
      var bodyText = it.abstract || it.why || '';
      var paras = bodyText.split(/\n{2,}|\.\s{2,}/).filter(function (p) { return p.trim(); });
      if (!paras.length) paras = [bodyText];
      bodyHTML = paras.map(function (p) {
        return '<p style="font:500 18px/1.75 var(--font-sans);color:var(--fg-1);margin:0 0 20px;max-width:64ch">' + paragraphHTML(p.trim()) + '</p>';
      }).join('');
    }

    var summaryHTML;
    if (S.item._summary) {
      summaryHTML = S.item._summary.map(function (line) {
        return '<div style="display:flex;gap:10px;align-items:flex-start"><span style="margin-top:8px;width:6px;height:6px;border-radius:50%;background:var(--spark-500);flex:0 0 auto"></span><span style="font:500 16px/1.55 var(--font-sans);color:var(--fg-1)">' + esc(line) + '</span></div>';
      }).join('');
    } else {
      summaryHTML = '<div style="display:flex;align-items:center;gap:8px;color:var(--fg-3);font:500 14px/1.5 var(--font-sans)">' + ico(ICON.refresh, 'class="ico spin" style="width:16px;height:16px"') + 'Summarizing…</div>';
    }
    var modelLabel = esc(S.item._summaryModel ? S.modelName(S.item._summaryModel) : S.modelName());
    var docLink = '<a href="' + API.documentUrl(it.id) + '" target="_blank" rel="noopener" style="font:600 13px/1.3 var(--font-sans)">Open the stored document</a>';
    var origLink = it.url
      ? '<a href="' + esc(it.url) + '" target="_blank" rel="noopener noreferrer" style="font:600 13px/1.3 var(--font-sans);color:var(--fg-link);margin-left:10px">' + ico(ICON.external, 'style="width:14px;height:14px;vertical-align:-2px;margin-right:3px"') + 'Open original</a>'
      : '';
    var attachLabel = it.doc_uploaded ? 'Replace supporting PDF' : 'Upload supporting PDF';
    var attachControl = '<div style="margin-top:10px">' +
      '<button class="gym-press" id="mdAttachBtn" style="display:inline-flex;align-items:center;gap:6px;height:32px;padding:0 12px;border:1px solid var(--border-hair);background:var(--bg-surface);border-radius:999px;cursor:pointer;font:700 12px/1 var(--font-sans);color:var(--fg-2)">' + ico(ICON.plus, 'style="width:15px;height:15px"') + attachLabel + '</button>' +
      '<input type="file" id="mdFile" accept="application/pdf,.pdf" hidden>' +
      '</div>';

    // A real <article> with a single <h1> title and <p>/markdown body lets
    // Safari detect the reading content and offer Reader Mode (a Safari UI
    // feature that cannot be automated). id=readBody stays here for selection.
    return '<article class="gym-read" id="readBody">' +
      '<button class="gym-press reader-back" style="display:inline-flex;align-items:center;gap:6px;height:34px;padding:0 12px 0 8px;border:none;background:none;color:var(--fg-3);cursor:pointer;font:600 14px/1 var(--font-sans);margin-bottom:12px;border-radius:8px">' + ico(ICON.back, 'style="width:18px;height:18px"') + (it.kind === 'repo' ? 'Repos' : 'Papers') + '</button>' +
      '<div style="display:flex;align-items:center;gap:8px;margin-bottom:12px;flex-wrap:wrap">' +
        '<span style="display:inline-flex;align-items:center;padding:3px 10px;border-radius:999px;font:700 11px/1.4 var(--font-sans);background:var(--spark-100);color:var(--spark-700)">' + kindLabel + '</span>' + tags +
      '</div>' +
      '<h1 style="font:700 31px/1.18 var(--font-display);letter-spacing:-.02em;color:var(--fg-1)">' + esc(it.title) + '</h1>' +
      '<p style="font:600 14px/1.5 var(--font-sans);color:var(--fg-3);margin-top:10px">' + esc(it.source || '') + ' · ' + docLink + origLink + '</p>' +
      attachControl +
      '<div style="background:var(--spark-100);border:1px solid var(--spark-200);border-radius:14px;padding:18px;margin:22px 0 26px">' +
        '<div style="display:flex;align-items:center;gap:8px;margin-bottom:12px">' +
          ico(ICON.spark, 'style="fill:var(--spark-500);stroke:none;width:18px;height:18px"') +
          '<span style="font:700 13px/1 var(--font-sans);color:var(--spark-700)">Readable summary</span>' +
          '<span style="margin-left:auto;font:600 11px/1 var(--font-sans);color:var(--fg-muted)">' + modelLabel + '</span>' +
        '</div>' +
        '<div style="display:flex;flex-direction:column;gap:10px">' + summaryHTML + '</div>' +
      '</div>' +
      '<button class="gym-press" id="chatArticleBtn" style="display:inline-flex;align-items:center;gap:8px;height:42px;padding:0 18px;border:none;background:var(--sky-500);color:#fff;border-radius:999px;cursor:pointer;font:700 14px/1 var(--font-sans);margin-bottom:18px">' +
        ico(ICON.send, 'style="width:17px;height:17px;stroke-width:2.2"') +
        'Chat about this article' +
      '</button>' +
      '<div style="display:inline-flex;align-items:center;gap:8px;padding:8px 12px;border-radius:999px;background:var(--sky-100);color:var(--sky-600);font:600 13px/1.3 var(--font-sans);margin-bottom:22px">' +
        ico('<path d="M4 7l5-3 6 3 5-2v12l-5 2-6-3-5 3z"></path>', 'style="width:16px;height:16px"') +
        'Select any text to explain, summarize, or ask — or tap an <span style="border-bottom:2px solid var(--spark-400);color:var(--spark-700)">underlined term</span>.' +
      '</div>' +
      bodyHTML +
    '</article>';
  }

  // ====================================================================
  // KNOWLEDGE BASE
  // ====================================================================
  function kbCard(e) {
    var def = e.lead && e.body ? (e.lead + ' — ' + e.body) : (e.body || e.lead || '');
    var srcName = e.source_title || e.source_url || 'source';
    var sourceBtn = e.item_id
      ? '<button class="gym-press kb-open" data-item="' + e.item_id + '" style="display:inline-flex;align-items:center;gap:6px;border:none;background:none;color:var(--fg-link);cursor:pointer;font:600 13px/1.3 var(--font-sans);padding:4px 0">' + ico(ICON.link, 'style="width:15px;height:15px"') + 'From: ' + esc(srcName) + '</button>'
      : '<span style="font:600 13px/1.3 var(--font-sans);color:var(--fg-muted)">From: ' + esc(srcName) + '</span>';
    var docLink = e.item_id
      ? '<a href="' + API.documentUrl(e.item_id) + '" target="_blank" rel="noopener" style="font:600 12px/1.3 var(--font-sans);margin-left:10px">document</a>' : '';
    var removeBtn = '<button class="gym-press kb-remove" data-id="' + e.id + '" type="button" aria-label="Remove" title="Remove from knowledge base" style="display:inline-flex;align-items:center;justify-content:center;width:30px;height:30px;border:none;background:none;border-radius:8px;cursor:pointer;color:var(--fg-muted);flex:0 0 auto">' + ico(ICON.trash, 'style="width:17px;height:17px"') + '</button>';
    // The whole card opens the concept detail view; inner buttons/links stop
    // propagation so they keep their own behaviour.
    return '<article class="kb-card" data-id="' + e.id + '" style="background:var(--bg-surface);border:1px solid var(--border-hair);border-radius:14px;box-shadow:var(--shadow-1);padding:16px 18px;display:flex;flex-direction:column;gap:8px;cursor:pointer">' +
      '<div style="display:flex;align-items:center;gap:10px"><h3 style="font:700 18px/1.3 var(--font-sans);color:var(--fg-1)">' + esc(e.term) + '</h3><span style="margin-left:auto;display:inline-flex;align-items:center;padding:3px 10px;border-radius:999px;font:600 11px/1.4 var(--font-sans);background:var(--berry-100);color:var(--berry-600)">' + esc(e.tag || 'note') + '</span>' + removeBtn + '</div>' +
      '<p style="font:500 15px/1.6 var(--font-sans);color:var(--fg-2)">' + esc(def) + '</p>' +
      '<div style="display:flex;align-items:center;gap:8px;margin-top:4px;flex-wrap:wrap">' + sourceBtn + docLink +
        '<span style="margin-left:auto;font:600 12px/1.3 var(--font-sans);color:var(--fg-muted)">First seen ' + esc(recency(e.created_at) || 'just now') + '</span>' +
      '</div>' +
    '</article>';
  }
  function renderSaved() {
    var entries = S.kbEntries;
    var empty = entries.length === 0;
    var emptyBox = '';
    if (empty) {
      emptyBox = S.kbQuery
        ? '<div style="text-align:center;padding:48px 24px;background:var(--bg-surface);border:1px solid var(--border-hair);border-radius:16px"><p style="font:600 17px/1.4 var(--font-sans);color:var(--fg-2)">Nothing matches “' + esc(S.kbQuery) + '”.</p><p style="font:500 14px/1.5 var(--font-sans);color:var(--fg-3);margin-top:6px">Try a shorter word, or clear the search.</p></div>'
        : '<div style="text-align:center;padding:48px 24px;background:var(--bg-surface);border:1px solid var(--border-hair);border-radius:16px"><p style="font:600 17px/1.4 var(--font-sans);color:var(--fg-2)">Nothing saved yet.</p><p style="font:500 14px/1.5 var(--font-sans);color:var(--fg-3);margin-top:6px">Open a paper, select some text, and save what you learn.</p></div>';
    }
    return '<h1 style="font:700 32px/1.1 var(--font-display);letter-spacing:-.02em;color:var(--fg-1)">Knowledge base</h1>' +
      '<p style="font:500 15px/1.5 var(--font-sans);color:var(--fg-3);margin-top:6px">' + (S.kbCount != null ? S.kbCount : entries.length) + ' things you’ve learned. Each one links back to where you met it.</p>' +
      '<div style="position:relative;margin:18px 0 18px">' +
        ico(ICON.search, 'style="position:absolute;left:13px;top:50%;transform:translateY(-50%);width:18px;height:18px;stroke:var(--ink-400);pointer-events:none"') +
        '<input class="gym-term-input" id="kbSearch" value="' + esc(S.kbQuery) + '" placeholder="Search terms, definitions, sources…" style="padding-left:40px;height:46px" />' +
      '</div>' + emptyBox +
      '<div style="display:flex;flex-direction:column;gap:12px">' + entries.map(kbCard).join('') + '</div>';
  }

  // A single concept's detail view: term + definition, a Remove button, and
  // EVERY article that points to this concept (its back-references). Each
  // article links into the reader.
  function renderConcept() {
    var e = S.conceptDetail;
    if (!e) {
      return '<button class="gym-press concept-back" style="display:inline-flex;align-items:center;gap:6px;height:34px;padding:0 12px 0 8px;border:none;background:none;color:var(--fg-3);cursor:pointer;font:600 14px/1 var(--font-sans);margin-bottom:12px;border-radius:8px">' + ico(ICON.back, 'style="width:18px;height:18px"') + 'Knowledge base</button>' +
        '<div style="display:flex;align-items:center;gap:8px;color:var(--fg-3);font:500 14px/1.5 var(--font-sans)">' + ico(ICON.refresh, 'class="ico spin" style="width:16px;height:16px"') + 'Loading…</div>';
    }
    var def = e.lead && e.body ? (e.lead + ' — ' + e.body) : (e.body || e.lead || '');
    var analogyHTML = e.analogy
      ? '<div style="background:var(--spark-100);border:1px solid var(--spark-200);border-radius:12px;padding:14px 16px;margin-top:6px;font:500 15px/1.6 var(--font-sans);color:var(--fg-1)"><span style="font:700 12px/1 var(--font-sans);color:var(--spark-700)">Picture it</span><br>' + esc(e.analogy) + '</div>'
      : '';
    var links = e.linked_items || [];
    var linksHTML = links.length
      ? links.map(function (it) {
          var meta = it.source || (it.kind === 'repo' ? 'Repo' : 'Paper');
          return '<button class="gym-press kb-article" data-id="' + it.id + '" style="display:flex;align-items:center;gap:10px;width:100%;text-align:left;background:var(--bg-surface);border:1px solid var(--border-hair);border-radius:12px;padding:14px 16px;cursor:pointer">' +
            ico(it.kind === 'repo' ? ICON.link : ICON.bookmark, 'style="width:17px;height:17px;flex:0 0 auto;color:var(--fg-3)"') +
            '<span style="display:flex;flex-direction:column;gap:2px;min-width:0"><span style="font:700 15px/1.3 var(--font-sans);color:var(--fg-1)">' + esc(it.title) + '</span><span style="font:600 12px/1.3 var(--font-sans);color:var(--fg-muted)">' + esc(meta) + '</span></span>' +
            '<span style="margin-left:auto;flex:0 0 auto">' + ico(ICON.arrow, 'style="width:16px;height:16px;color:var(--fg-3)"') + '</span>' +
          '</button>';
        }).join('')
      : '<div style="text-align:center;padding:32px 24px;background:var(--bg-surface);border:1px solid var(--border-hair);border-radius:14px"><p style="font:500 14px/1.5 var(--font-sans);color:var(--fg-3)">No articles point to this concept yet.</p></div>';
    var count = links.length;
    return '<button class="gym-press concept-back" style="display:inline-flex;align-items:center;gap:6px;height:34px;padding:0 12px 0 8px;border:none;background:none;color:var(--fg-3);cursor:pointer;font:600 14px/1 var(--font-sans);margin-bottom:12px;border-radius:8px">' + ico(ICON.back, 'style="width:18px;height:18px"') + 'Knowledge base</button>' +
      '<div style="display:flex;align-items:flex-start;gap:12px">' +
        '<h1 style="font:700 32px/1.1 var(--font-display);letter-spacing:-.02em;color:var(--fg-1);flex:1 1 auto;min-width:0">' + esc(e.term) + '</h1>' +
        '<button class="gym-press concept-remove" data-id="' + e.id + '" type="button" style="display:inline-flex;align-items:center;gap:6px;height:36px;padding:0 14px;border:1px solid var(--border-hair);background:var(--bg-surface);border-radius:999px;cursor:pointer;font:700 13px/1 var(--font-sans);color:var(--rose-600);flex:0 0 auto">' + ico(ICON.trash, 'style="width:16px;height:16px"') + 'Remove</button>' +
      '</div>' +
      '<p style="font:500 16px/1.6 var(--font-sans);color:var(--fg-2);margin-top:10px;max-width:64ch">' + esc(def) + '</p>' + analogyHTML +
      '<h2 style="font:700 16px/1.2 var(--font-sans);color:var(--fg-1);margin:26px 0 12px">Seen in ' + count + ' article' + (count === 1 ? '' : 's') + '</h2>' +
      '<div style="display:flex;flex-direction:column;gap:10px">' + linksHTML + '</div>';
  }

  // ====================================================================
  // KNOWLEDGE MAP
  // ====================================================================
  var TONE = { spark: 'var(--spark-500)', sky: 'var(--sky-500)', grass: 'var(--grass-500)', berry: 'var(--berry-500)', sun: 'var(--sun-500)', rose: 'var(--rose-600)' };
  function renderMap() {
    var d = S.mapData;
    var nodeById = {};
    d.nodes.forEach(function (n) { nodeById[n.id] = n; });
    var edges = d.edges.map(function (e) {
      var a = nodeById[e.src], b = nodeById[e.dst];
      if (!a || !b) return '';
      var col = e.source === 'ai' ? 'var(--sky-500)' : 'var(--paper-400)';
      var w = e.source === 'ai' ? 0.5 : 0.4;
      return '<line data-edge="' + e.id + '" x1="' + a.x + '" y1="' + a.y + '" x2="' + b.x + '" y2="' + b.y + '" stroke="' + col + '" stroke-width="' + w + '"></line>';
    }).join('');
    var nodes = d.nodes.map(function (n) {
      return '<div class="map-node" data-node="' + n.id + '" style="left:' + n.x + '%;top:' + n.y + '%"><span class="map-dot" style="background:' + (TONE[n.tone] || TONE.spark) + '"></span>' + esc(n.label) + '</div>';
    }).join('');
    var empty = d.nodes.length === 0
      ? '<div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:var(--fg-3);font:500 15px/1.5 var(--font-sans);text-align:center;padding:24px">Save terms in the knowledge base and they appear here automatically.</div>' : '';
    return '<h1 style="font:700 32px/1.1 var(--font-display);letter-spacing:-.02em;color:var(--fg-1)">Knowledge map</h1>' +
      '<p style="font:500 15px/1.5 var(--font-sans);color:var(--fg-3);margin-top:6px">Concepts you’ve met, linked by what explains what. Drag to rearrange; draw a link, or let AI suggest them.</p>' +
      '<div class="map-stage" id="mapStage">' +
        '<svg class="map-edges" viewBox="0 0 100 100" preserveAspectRatio="none">' + edges + '</svg>' + nodes + empty +
      '</div>' +
      '<div class="map-controls">' +
        '<button class="btn-ghost" id="mapLinkBtn">' + ico(ICON.link, 'style="width:15px;height:15px"') + 'Link two concepts</button>' +
        '<button class="btn-spark" id="mapAiBtn">' + ico(ICON.spark, 'style="width:16px;height:16px;fill:#fff;stroke:none"') + 'AI-suggested links</button>' +
        '<span id="mapLinkHint" style="font:600 12px/1.3 var(--font-sans);color:var(--fg-muted)"></span>' +
      '</div>' +
      '<div class="map-hint">' + ico(ICON.link, 'style="width:15px;height:15px"') + 'Tip: with “Link” active, tap two nodes to connect them. Tap a line to remove it.</div>';
  }

  // ====================================================================
  // CONVERSATION PANEL
  // ====================================================================
  function modeLabel() {
    return S.mode === 'summarize' ? 'Summary' : S.mode === 'ask' ? 'Ask a follow-up' : 'Explain simply';
  }
  function modelMenuHTML() {
    var provs = (S.models.providers || []);
    return provs.map(function (g) {
      var items = (g.models || []).map(function (m) {
        var on = m.id === S.model;
        return '<button class="gym-nav model-pick" data-id="' + esc(m.id) + '" style="display:flex;align-items:center;gap:8px;border:none;background:none;cursor:pointer;border-radius:8px;padding:9px 10px;font:600 14px/1 var(--font-sans);color:var(--fg-1);text-align:left;min-height:40px;width:100%">' +
          (on ? ico(ICON.check, 'style="width:16px;height:16px;stroke:var(--grass-600);stroke-width:2.6"') : '<span style="width:16px;flex:0 0 auto"></span>') +
          esc(m.name) + '</button>';
      }).join('');
      return '<div style="font:700 10px/1 var(--font-sans);letter-spacing:.08em;text-transform:uppercase;color:var(--fg-muted);padding:9px 10px 5px">' + esc(g.name) + '</div>' + items;
    }).join('');
  }
  function renderChatPanel() {
    var thread = S.thread.map(function (m) {
      if (m.role === 'user') {
        return '<div style="align-self:flex-end;max-width:86%;background:var(--sky-500);color:#fff;padding:10px 14px;border-radius:14px 14px 4px 14px;font:500 15px/1.5 var(--font-sans);white-space:pre-wrap">' + esc(m.content) + '</div>';
      }
      // Assistant answers come back as markdown — render them through the same
      // hardened renderer the article body uses so lists/bold/code/headings show.
      return '<div class="gym-md" style="align-self:flex-start;max-width:90%;background:var(--paper-200);color:var(--fg-1);padding:10px 14px;border-radius:14px 14px 14px 4px;font:500 15px/1.6 var(--font-sans)">' + renderMarkdownHTML(m.content) + '</div>';
    }).join('');
    var grounded = '';
    var g = S.chatGrounded;
    if (g && ((g.notes && g.notes.length) || (g.concepts && g.concepts.length))) {
      var parts = [];
      var n = (g.notes || []).length;
      if (n) parts.push('Grounded in ' + n + ' note' + (n === 1 ? '' : 's') + ' from your knowledge base');
      if (g.concepts && g.concepts.length) parts.push('concepts: ' + g.concepts.join(', '));
      grounded = '<div class="chat-grounded" style="align-self:flex-start;display:flex;align-items:center;gap:6px;font:600 12px/1.5 var(--font-sans);color:var(--fg-muted)">' +
        ico(ICON.spark, 'style="fill:var(--spark-500);stroke:none;width:14px;height:14px;flex:0 0 auto"') +
        '<span>' + esc(parts.join(' · ')) + '</span></div>';
    }
    var loading = S.busy
      ? '<div style="align-self:flex-start;display:flex;align-items:center;gap:8px;color:var(--fg-3);font:500 14px/1.5 var(--font-sans)">' + ico(ICON.refresh, 'class="ico spin" style="width:16px;height:16px"') + 'Thinking…</div>' : '';
    var empty = (!thread && !loading)
      ? '<div style="color:var(--fg-3);font:500 15px/1.6 var(--font-sans)">Ask anything about this article. Answers draw on your saved notes and concept map.</div>' : '';
    var menu = S.modelMenuOpen
      ? '<div style="position:absolute;right:0;top:40px;z-index:50;width:250px;max-height:340px;overflow:auto;background:var(--bg-surface);border:1px solid var(--border-default);border-radius:12px;box-shadow:var(--shadow-3);padding:6px;display:flex;flex-direction:column;gap:2px" class="gym-scroll">' + modelMenuHTML() + '</div>' : '';
    var title = S.item ? S.item.title : 'this article';
    return '' +
      (S.isDesktop() ? '' : '<div style="display:flex;justify-content:center;padding:9px 0 2px"><div style="width:38px;height:4px;border-radius:2px;background:var(--paper-400)"></div></div>') +
      '<div style="display:flex;align-items:center;gap:8px;padding:12px 14px 12px 16px;border-bottom:1px solid var(--border-hair);flex:0 0 auto">' +
        '<span style="font:700 16px/1.2 var(--font-sans);color:var(--fg-1)">Chat about this article</span>' +
        '<div style="position:relative;margin-left:auto">' +
          '<button class="gym-press" id="modelBtn" style="display:inline-flex;align-items:center;gap:7px;height:34px;padding:0 10px;border:1px solid var(--border-hair);background:var(--bg-surface);border-radius:999px;cursor:pointer;font:700 12px/1 var(--font-sans);color:var(--fg-2)"><span style="width:7px;height:7px;border-radius:50%;background:var(--grass-500);flex:0 0 auto"></span>' + esc(S.modelName()) + ico(ICON.chevron, 'style="width:14px;height:14px;stroke-width:2.4"') + '</button>' + menu +
        '</div>' +
        '<button class="gym-press" id="panelClose" aria-label="Close" style="display:inline-flex;align-items:center;justify-content:center;width:34px;height:34px;border:none;background:var(--paper-200);border-radius:9px;cursor:pointer;color:var(--fg-2)">' + ico(ICON.close, 'style="width:17px;height:17px;stroke-width:2.2"') + '</button>' +
      '</div>' +
      '<div class="gym-scroll" id="chatScroll" style="flex:1;min-height:0;overflow:auto;padding:16px;display:flex;flex-direction:column;gap:12px">' +
        '<div style="font:600 12px/1.5 var(--font-sans);color:var(--fg-muted)">About: “' + esc(title) + '”</div>' +
        empty + thread + loading + grounded +
      '</div>' +
      '<div style="border-top:1px solid var(--border-hair);padding:12px 14px;display:flex;flex-direction:column;gap:10px;flex:0 0 auto">' +
        '<div style="display:flex;gap:8px;align-items:center">' +
          '<input class="gym-term-input" id="panelDraft" value="' + esc(S.draft) + '" placeholder="Ask about this article…" />' +
          '<button class="gym-press" id="panelSend" aria-label="Send" style="display:inline-flex;align-items:center;justify-content:center;width:46px;height:46px;flex:0 0 auto;border:none;background:var(--sky-500);color:#fff;border-radius:10px;cursor:pointer">' + ico(ICON.send, 'style="width:19px;height:19px;stroke-width:2.2"') + '</button>' +
        '</div>' +
      '</div>';
  }
  function analogyBlock(text) {
    return '<div style="display:flex;gap:9px;align-items:flex-start;padding:11px 13px;border-radius:10px;background:var(--sky-100);font:500 14px/1.55 var(--font-sans);color:var(--fg-1)"><span style="font-weight:700;color:var(--sky-600);white-space:nowrap">Picture it</span><span>' + esc(text) + '</span></div>';
  }
  // One extracted concept: its label, a reused/new badge, and its definition.
  function conceptCard(c) {
    var badge = c.reused
      ? '<span style="margin-left:auto;display:inline-flex;align-items:center;padding:3px 10px;border-radius:999px;font:700 11px/1.4 var(--font-sans);background:var(--grass-100);color:var(--grass-600)">From your KB</span>'
      : '<span style="margin-left:auto;display:inline-flex;align-items:center;padding:3px 10px;border-radius:999px;font:700 11px/1.4 var(--font-sans);background:var(--spark-100);color:var(--spark-700)">New</span>';
    return '<div class="concept-card" style="background:var(--bg-surface);border:1px solid var(--border-hair);border-radius:14px;padding:16px 18px;display:flex;flex-direction:column;gap:10px">' +
      '<div style="display:flex;align-items:center;gap:8px"><span class="concept-label" style="font:700 19px/1.3 var(--font-sans);color:var(--fg-1)">' + esc(c.label) + '</span>' + badge + '</div>' +
      (c.lead ? '<div style="font:700 16px/1.35 var(--font-display);letter-spacing:-.01em;color:var(--fg-1)">' + esc(c.lead) + '</div>' : '') +
      '<div class="gym-md" style="font:500 16px/1.7 var(--font-sans);color:var(--fg-2)">' + renderMarkdownHTML(c.body || '') + '</div>' +
      (c.analogy ? analogyBlock(c.analogy) : '') +
    '</div>';
  }
  function renderPanel() {
    if (S.chatMode) return renderChatPanel();
    var a = S.answer || { lead: '', body: '', analogy: null };
    var analogy = a.analogy ? analogyBlock(a.analogy) : '';
    var thread = S.thread.map(function (m) {
      if (m.role === 'user') {
        return '<div style="align-self:flex-end;max-width:86%;background:var(--sky-500);color:#fff;padding:10px 14px;border-radius:14px 14px 4px 14px;font:500 15px/1.5 var(--font-sans)">' + esc(m.content) + '</div>';
      }
      // Markdown answer -> hardened renderer (same path as the article body).
      return '<div class="gym-md" style="align-self:flex-start;max-width:90%;background:var(--paper-200);color:var(--fg-1);padding:10px 14px;border-radius:14px 14px 14px 4px;font:500 15px/1.6 var(--font-sans)">' + renderMarkdownHTML(m.content) + '</div>';
    }).join('');
    var savedConfirm = S.justSaved
      ? '<div style="display:flex;align-items:center;gap:9px;padding:11px 13px;border-radius:10px;background:var(--grass-100);color:var(--grass-600);font:600 14px/1.4 var(--font-sans)">' + ico(ICON.check, 'style="width:17px;height:17px;stroke-width:2.6"') + 'Saved to your knowledge base.<button class="gym-press panel-gosaved" style="margin-left:auto;border:none;background:none;color:var(--fg-link);cursor:pointer;font:700 14px/1 var(--font-sans)">Open</button></div>' : '';
    var explainMode = S.mode === 'explain';
    var busyExplain = S.busy && !(S.explainConcepts && S.explainConcepts.length) && !S.clarifyQuestion;
    var loadingExplain = busyExplain
      ? '<div style="display:flex;align-items:center;gap:8px;color:var(--fg-3);font:500 14px/1.5 var(--font-sans)">' + ico(ICON.refresh, 'class="ico spin" style="width:16px;height:16px"') + 'Naming the concept…</div>' : '';
    var loading = S.busy && !S.answer
      ? '<div style="display:flex;align-items:center;gap:8px;color:var(--fg-3);font:500 14px/1.5 var(--font-sans)">' + ico(ICON.refresh, 'class="ico spin" style="width:16px;height:16px"') + 'Thinking…</div>' : '';
    // Body: concept cards (Explain) or a single answer (Summarize).
    var bodyBlock;
    if (explainMode) {
      if (loadingExplain) bodyBlock = loadingExplain;
      else if (S.clarifyQuestion) {
        bodyBlock = '<div style="background:var(--sun-100);border:1px solid var(--sun-200);border-radius:14px;padding:16px 18px;display:flex;flex-direction:column;gap:8px">' +
          '<div style="display:flex;align-items:center;gap:7px;font:700 13px/1 var(--font-sans);color:var(--ink-900)">' + ico(ICON.spark, 'style="fill:var(--sun-500);stroke:none;width:16px;height:16px"') + 'One quick question</div>' +
          '<div style="font:500 16px/1.6 var(--font-sans);color:var(--fg-1)">' + esc(S.clarifyQuestion) + '</div>' +
          '<div style="font:500 13px/1.5 var(--font-sans);color:var(--fg-muted)">Select a clearer phrase, or ask it as a follow-up below.</div>' +
        '</div>';
      } else if (S.explainConcepts && S.explainConcepts.length) {
        bodyBlock = S.explainConcepts.map(conceptCard).join('');
      } else {
        bodyBlock = '<div style="color:var(--fg-3);font:500 14px/1.5 var(--font-sans)">No concept found in the selection.</div>';
      }
    } else {
      bodyBlock = '<div style="background:var(--bg-surface);border:1px solid var(--border-hair);border-radius:14px;padding:16px 18px;display:flex;flex-direction:column;gap:10px">' +
        (loading || (
          '<div style="font:700 19px/1.35 var(--font-display);letter-spacing:-.01em;color:var(--fg-1)">' + esc(a.lead) + '</div>' +
          '<div class="gym-md" style="font:500 16px/1.7 var(--font-sans);color:var(--fg-2)">' + renderMarkdownHTML(a.body) + '</div>' + analogy)) +
      '</div>';
    }
    // Save is available when there is something to save (concepts, or an answer).
    var canSave = explainMode
      ? !!(S.explainConcepts && S.explainConcepts.length && !S.clarifyQuestion)
      : !!S.answer;
    var saveLabel = S.justSaved ? 'Saved'
      : explainMode
        ? ((S.explainConcepts && S.explainConcepts.length > 1) ? 'Save concepts' : 'Save concept')
        : 'Save to knowledge base';
    var menu = S.modelMenuOpen
      ? '<div style="position:absolute;right:0;top:40px;z-index:50;width:250px;max-height:340px;overflow:auto;background:var(--bg-surface);border:1px solid var(--border-default);border-radius:12px;box-shadow:var(--shadow-3);padding:6px;display:flex;flex-direction:column;gap:2px" class="gym-scroll">' + modelMenuHTML() + '</div>' : '';

    return '' +
      (S.isDesktop() ? '' : '<div style="display:flex;justify-content:center;padding:9px 0 2px"><div style="width:38px;height:4px;border-radius:2px;background:var(--paper-400)"></div></div>') +
      '<div style="display:flex;align-items:center;gap:8px;padding:12px 14px 12px 16px;border-bottom:1px solid var(--border-hair);flex:0 0 auto">' +
        '<span style="font:700 16px/1.2 var(--font-sans);color:var(--fg-1)">' + modeLabel() + '</span>' +
        '<div style="position:relative;margin-left:auto">' +
          '<button class="gym-press" id="modelBtn" style="display:inline-flex;align-items:center;gap:7px;height:34px;padding:0 10px;border:1px solid var(--border-hair);background:var(--bg-surface);border-radius:999px;cursor:pointer;font:700 12px/1 var(--font-sans);color:var(--fg-2)"><span style="width:7px;height:7px;border-radius:50%;background:var(--grass-500);flex:0 0 auto"></span>' + esc(S.modelName()) + ico(ICON.chevron, 'style="width:14px;height:14px;stroke-width:2.4"') + '</button>' + menu +
        '</div>' +
        '<button class="gym-press" id="panelClose" aria-label="Close" style="display:inline-flex;align-items:center;justify-content:center;width:34px;height:34px;border:none;background:var(--paper-200);border-radius:9px;cursor:pointer;color:var(--fg-2)">' + ico(ICON.close, 'style="width:17px;height:17px;stroke-width:2.2"') + '</button>' +
      '</div>' +
      '<div class="gym-scroll" style="flex:1;min-height:0;overflow:auto;padding:16px;display:flex;flex-direction:column;gap:14px">' +
        '<div style="display:flex;align-items:flex-start;gap:8px;flex-wrap:wrap"><span style="font:600 12px/1.7 var(--font-sans);color:var(--fg-muted)">From the text</span><span style="display:inline-block;padding:5px 11px;border-radius:9px;background:var(--sun-100);color:var(--ink-900);font:600 13px/1.4 var(--font-sans);max-width:100%">“' + esc(S.selText) + '”</span></div>' +
        '<div class="seg-row"><button class="gym-seg' + (S.mode === 'explain' ? ' on' : '') + '" data-mode="explain">Explain simply</button><button class="gym-seg' + (S.mode === 'summarize' ? ' on' : '') + '" data-mode="summarize">Summarize</button></div>' +
        bodyBlock +
        thread + savedConfirm +
      '</div>' +
      '<div style="border-top:1px solid var(--border-hair);padding:12px 14px;display:flex;flex-direction:column;gap:10px;flex:0 0 auto">' +
        '<div style="display:flex;gap:8px;align-items:center">' +
          '<input class="gym-term-input" id="panelDraft" value="' + esc(S.draft) + '" placeholder="Ask a follow-up…" />' +
          '<button class="gym-press" id="panelSend" aria-label="Send" style="display:inline-flex;align-items:center;justify-content:center;width:46px;height:46px;flex:0 0 auto;border:none;background:var(--sky-500);color:#fff;border-radius:10px;cursor:pointer">' + ico(ICON.send, 'style="width:19px;height:19px;stroke-width:2.2"') + '</button>' +
        '</div>' +
        (canSave ? '<button class="gym-press" id="panelSave"' + (S.justSaved ? ' disabled' : '') + ' style="display:inline-flex;align-items:center;justify-content:center;gap:8px;height:46px;border:none;background:var(--spark-500);color:#fff;border-radius:10px;cursor:' + (S.justSaved ? 'default' : 'pointer') + ';font:700 15px/1 var(--font-sans)' + (S.justSaved ? ';opacity:.7' : '') + '">' + ico(S.justSaved ? ICON.check : ICON.plus, 'style="width:18px;height:18px;stroke-width:2.2"') + saveLabel + '</button>' : '') +
      '</div>';
  }
  function syncPanel() {
    var panel = $('panel'), scrim = $('scrim');
    if (!S.panelOpen) { panel.hidden = true; scrim.hidden = true; return; }
    panel.classList.remove('sheet', 'side');
    panel.classList.add(S.isDesktop() ? 'side' : 'sheet');
    panel.hidden = false;
    scrim.hidden = S.isDesktop();
    panel.innerHTML = renderPanel();
  }

  // ====================================================================
  // RENDER + ROUTER
  // ====================================================================
  function render() {
    setNavActive();
    var host = $('screen');
    if (S.screen === 'papers' || S.screen === 'repos') host.innerHTML = renderFeed(kindFor(S.screen));
    else if (S.screen === 'added') host.innerHTML = renderAdded();
    else if (S.screen === 'reader') { host.innerHTML = renderReader(); underlineConcepts($('readBody')); }
    else if (S.screen === 'saved') host.innerHTML = renderSaved();
    else if (S.screen === 'concept') host.innerHTML = renderConcept();
    else if (S.screen === 'map') { host.innerHTML = renderMap(); wireMap(); }
    syncPanel();
  }

  // ---- hash router ---------------------------------------------------------
  // Hash-based so it works against the static file server with no backend
  // change. Routes: #/papers[?q&sort&author&company&publication],
  // #/repos[?q&sort&company&language], #/read/<id>, #/saved[?q=...], #/map.
  // #/ or #/feed (legacy) fall through to #/papers.
  var FEED_PARAMS = {
    paper: ['q', 'sort', 'author', 'company', 'publication'],
    repo: ['q', 'sort', 'company', 'language']
  };
  function parseHash() {
    var raw = location.hash || '';
    if (raw.charAt(0) === '#') raw = raw.slice(1);
    var query = '';
    var qi = raw.indexOf('?');
    if (qi !== -1) { query = raw.slice(qi + 1); raw = raw.slice(0, qi); }
    var parts = raw.split('/').filter(Boolean);
    var seg = parts[0] || '';
    var sp; try { sp = new URLSearchParams(query); } catch (e) { sp = new URLSearchParams(''); }
    if (seg === 'read') {
      return { screen: 'reader', id: parts[1] ? parseInt(parts[1], 10) : null };
    }
    if (seg === 'papers' || seg === 'repos') {
      var kind = kindFor(seg);
      var filters = {};
      FEED_PARAMS[kind].forEach(function (k) {
        if (k === 'q' || k === 'sort') return;
        filters[k] = sp.get(k) || '';
      });
      return {
        screen: seg, kind: kind,
        q: sp.get('q') || '',
        sort: sp.get('sort') === 'rating' ? 'rating' : 'recency',
        filters: filters
      };
    }
    if (seg === 'added') return { screen: 'added' };
    if (seg === 'concept') {
      return { screen: 'concept', id: parts[1] ? parseInt(parts[1], 10) : null };
    }
    if (seg === 'saved') {
      return { screen: 'saved', q: sp.get('q') || '' };
    }
    if (seg === 'map') return { screen: 'map' };
    return { screen: 'papers', kind: 'paper', q: '', sort: 'recency', filters: {} };
  }
  function buildHash(screen, opts) {
    opts = opts || {};
    if (screen === 'reader') return '#/read/' + opts.id;
    if (screen === 'added') return '#/added';
    if (screen === 'concept') return '#/concept/' + opts.id;
    if (screen === 'saved') return '#/saved' + (opts.q ? '?q=' + encodeURIComponent(opts.q) : '');
    if (screen === 'map') return '#/map';
    // papers / repos: serialize this feed's q + sort + active filters.
    var kind = kindFor(screen);
    var fs = S.feeds[kind];
    var q = [];
    if (fs.q) q.push('q=' + encodeURIComponent(fs.q));
    if (fs.sort && fs.sort !== 'recency') q.push('sort=' + encodeURIComponent(fs.sort));
    Object.keys(fs.filters).forEach(function (k) {
      if (fs.filters[k]) q.push(k + '=' + encodeURIComponent(fs.filters[k]));
    });
    return '#/' + screen + (q.length ? '?' + q.join('&') : '');
  }
  // Real in-app navigation: push a history entry (or replace it) and apply.
  // Setting location.hash fires hashchange, which is where the route is
  // actually applied — so Back/Forward and address-bar edits behave the same.
  function navigate(screen, opts, replace) {
    var h = buildHash(screen, opts);
    if (replace) { history.replaceState(null, '', h); applyRoute(); return; }
    if (location.hash === h) { applyRoute(); return; }  // hashchange won't fire
    location.hash = h;
  }
  function applyRoute() {
    var r = parseHash();
    if (r.screen === 'reader') {
      if (r.id == null || isNaN(r.id)) { navigate('papers', null, true); return; }
      openReader(r.id);
      return;
    }
    if (r.screen === 'saved') {
      S.kbQuery = r.q || '';
      go('saved');
      runKbSearch(S.kbQuery, false);
      return;
    }
    if (r.screen === 'added') { S.readerReturn = '#/added'; go('added'); loadAdded(); return; }
    if (r.screen === 'concept') {
      if (r.id == null || isNaN(r.id)) { navigate('saved', null, true); return; }
      S.readerReturn = location.hash || '#/concept/' + r.id;
      openConcept(r.id);
      return;
    }
    if (r.screen === 'map') { go('map'); loadMap(); return; }
    // papers / repos: hydrate this feed's state from the hash, then query.
    var fs = S.feeds[r.kind];
    fs.q = r.q;
    fs.sort = r.sort;
    Object.keys(fs.filters).forEach(function (k) { fs.filters[k] = (r.filters[k] || ''); });
    S.readerReturn = location.hash || ('#/' + r.screen);
    go(r.screen);
    if (!fs.facets) loadFacets(r.kind);
    loadFeed(r.kind);
  }

  // ====================================================================
  // BEHAVIOUR
  // ====================================================================
  // -- feeds (papers / repos) --
  function feedParams(kind) {
    var fs = S.feeds[kind];
    var p = { q: fs.q, sort: fs.sort, limit: 60 };
    Object.keys(fs.filters).forEach(function (k) { if (fs.filters[k]) p[k] = fs.filters[k]; });
    return p;
  }
  function loadFeed(kind) {
    return API.feed(kind, feedParams(kind)).then(function (res) {
      S.feeds[kind].items = res.items || [];
      if (S.screen === (kind === 'repo' ? 'repos' : 'papers')) render();
    }).catch(function () {});
  }
  function loadFacets(kind) {
    return API.facets(kind).then(function (f) {
      S.feeds[kind].facets = f;
      if (S.screen === (kind === 'repo' ? 'repos' : 'papers')) render();
    }).catch(function () {});
  }
  // Live search: reflect the query in the URL (replaceState so typing doesn't
  // spam Back/Forward), debounce the query, and re-render keeping caret/focus.
  var feedTimer = null;
  function onFeedSearch(kind, v) {
    S.feeds[kind].q = v;
    history.replaceState(null, '', buildHash(kind === 'repo' ? 'repos' : 'papers'));
    clearTimeout(feedTimer);
    feedTimer = setTimeout(function () {
      API.feed(kind, feedParams(kind)).then(function (res) {
        S.feeds[kind].items = res.items || [];
        if (S.screen !== (kind === 'repo' ? 'repos' : 'papers')) return;
        var input = $('feedSearch');
        var pos = input ? input.selectionStart : null;
        $('screen').innerHTML = renderFeed(kind);
        var ni = $('feedSearch');
        if (ni) { ni.focus(); if (pos != null) try { ni.setSelectionRange(pos, pos); } catch (e) {} }
      }).catch(function () {});
    }, 220);
  }

  function openReader(id) {
    S.screen = 'reader';
    S.item = null; S.summaryTerms = [];
    render();
    API.item(id).then(function (it) {
      S.item = it;
      S.summaryTerms = it.summary_terms || [];
      if (it.summary_readable) { it._summary = it.summary_readable; }
      // Try to load markdown when an original could be converted. The first
      // open may trigger a lazy auto-conversion server-side, so show a small
      // loading state until it resolves (404 falls back to abstract).
      if (it.markdown_available) { it._markdownLoading = true; }
      render();
      if (it.markdown_available) {
        API.markdown(id).then(function (md) {
          if (S.item && S.item.id === id) {
            S.item._markdownLoading = false;
            if (md != null) {
              S.item._markdownHTML = renderMarkdownHTML(md.text);
              S.item._markdownSource = md.source;
            }
            render();
          }
        }).catch(function () {
          if (S.item && S.item.id === id) { S.item._markdownLoading = false; render(); }
        });
      }
      if (!it.summary_readable) {
        API.summarize(id, S.model).then(function (res) {
          if (S.item && S.item.id === id) {
            S.item._summary = res.summary;
            S.item._summaryModel = res.model;
            S.summaryTerms = res.terms || S.summaryTerms;
            render();
          }
        }).catch(function () {});
      } else {
        it._summaryModel = S.model;
      }
    }).catch(function (e) { toast('Could not open item'); });
  }

  function uploadSupportingDoc(file) {
    if (!file || !S.item) return;
    var id = S.item.id;
    toast('Uploading supporting PDF…');
    API.uploadDocument(id, file).then(function () {
      toast('Supporting PDF attached');
      // Reload the item so doc_uploaded/markdown_available reflect the new file,
      // then re-fetch the readable markdown (the server drops the cached auto
      // conversion so this regenerates from the new source).
      return API.item(id);
    }).then(function (it) {
      if (S.item && S.item.id === id) {
        S.item = it;
        S.item._markdownLoading = true;
        render();
        return API.markdown(id);
      }
    }).then(function (md) {
      if (S.item && S.item.id === id && md != null) {
        S.item._markdownLoading = false;
        S.item._markdownHTML = renderMarkdownHTML(md.text);
        S.item._markdownSource = md.source;
        render();
      }
    }).catch(function () { toast('Could not attach supporting PDF'); });
  }

  // -- selection toolbar --
  function hideToolbar() { $('toolbar').hidden = true; S.affordance = null; }
  function onSelect() {
    if (S.screen !== 'reader' || S.panelOpen) return;
    var sel = window.getSelection && window.getSelection();
    if (!sel || sel.isCollapsed || sel.rangeCount === 0) { hideToolbar(); return; }
    var text = sel.toString().replace(/\s+/g, ' ').trim();
    if (text.length < 2) { hideToolbar(); return; }
    var readEl = $('readBody');
    var range = sel.getRangeAt(0);
    if (!readEl || !readEl.contains(range.commonAncestorContainer)) return;
    var r = range.getBoundingClientRect();
    var root = $('col').getBoundingClientRect();
    S.selText = text;
    var tb = $('toolbar');
    tb.style.left = (r.left - root.left + r.width / 2) + 'px';
    tb.style.top = Math.max(8, r.top - root.top) + 'px';
    tb.hidden = false;
  }

  function resetPanel(span, mode) {
    S.panelOpen = true; S.chatMode = false; S.selText = span; S.mode = mode;
    S.answer = null; S.thread = []; S.draft = '';
    S.explainConcepts = null; S.clarifyQuestion = null;
    S.savedEntryId = null; S.justSaved = false; S.modelMenuOpen = false;
    hideToolbar();
    try { window.getSelection().removeAllRanges(); } catch (e) {}
  }
  function openPanel(span, mode) {
    resetPanel(span, mode);
    S.busy = true; syncPanel();
    // Explain is concept-based: name the concept(s) and reuse/define each.
    if (mode === 'explain') explainSelection(span);
    else askServer(mode, null);
  }
  // Tapping an underlined concept shows its CACHED definition with zero AI.
  function openConceptPanel(label) {
    var c = S.concepts[(label || '').toLowerCase()];
    resetPanel(label, 'explain');
    if (c) {
      S.busy = false;
      S.explainConcepts = [{
        label: c.label, lead: c.lead, body: c.body, analogy: c.analogy,
        reused: true, kb_entry_id: c.id
      }];
      S.savedEntryId = c.id; S.justSaved = true;  // already in the KB
      syncPanel();
      // The currently-open article counts as a source for this concept. The
      // server's explain reuse path records the back-reference with zero AI; we
      // fire it in the background so the cached panel stays instant.
      if (S.item) {
        API.explain({ span_text: c.label, model: S.model, item_id: S.item.id })
          .catch(function () {});
      }
    } else {
      // Not in the local cache (e.g. just-loaded) — fall back to the explain
      // flow which still reuses the server-side cached definition with no
      // generation when the label is known.
      S.busy = true; syncPanel();
      explainSelection(label);
    }
  }
  function explainSelection(span) {
    S.busy = true;
    return API.explain({
      span_text: span, model: S.model, item_id: S.item ? S.item.id : null
    }).then(function (res) {
      S.busy = false;
      S.clarifyQuestion = res.question || null;
      S.explainConcepts = res.concepts || [];
      // A reused concept is already saved; reflect that on the Save button.
      var reused = (S.explainConcepts || []).filter(function (c) { return c.reused; });
      if (reused.length && reused.length === (S.explainConcepts || []).length) {
        S.justSaved = true; S.savedEntryId = reused[0].kb_entry_id || null;
      }
      syncPanel();
    }).catch(function () { S.busy = false; toast('AI request failed'); syncPanel(); });
  }
  // -- article chat (whole-article, knowledge-grounded) --
  function openChat() {
    if (!S.item) return;
    var id = S.item.id;
    S.panelOpen = true; S.chatMode = true; S.modelMenuOpen = false;
    S.thread = []; S.draft = ''; S.chatGrounded = null; S.chatEntryId = null;
    S.busy = true;
    hideToolbar();
    try { window.getSelection().removeAllRanges(); } catch (e) {}
    syncPanel();
    API.chatThread(id).then(function (res) {
      if (!S.chatMode || !S.item || S.item.id !== id) return;
      S.busy = false;
      S.chatEntryId = res.kb_entry_id || null;
      S.thread = (res.messages || []).map(function (m) {
        return { role: m.role, content: m.content };
      });
      syncPanel();
    }).catch(function () { S.busy = false; syncPanel(); });
  }
  function sendChat() {
    var q = (S.draft || '').trim(); if (!q || !S.item) return;
    var id = S.item.id;
    S.draft = '';
    S.thread.push({ role: 'user', content: q });
    S.busy = true; syncPanel();
    API.chat({ item_id: id, message: q, model: S.model }).then(function (res) {
      if (!S.chatMode || !S.item || S.item.id !== id) return;
      S.busy = false;
      S.chatEntryId = res.kb_entry_id || S.chatEntryId;
      S.thread.push({ role: 'assistant', content: answerText(res.answer) });
      S.chatGrounded = res.grounded || null;
      syncPanel();
    }).catch(function () { S.busy = false; toast('Chat failed'); syncPanel(); });
  }
  function askServer(mode, message) {
    var payload = {
      span_text: S.selText, mode: mode, model: S.model,
      item_id: S.item ? S.item.id : null
    };
    if (S.savedEntryId) payload.kb_entry_id = S.savedEntryId;
    if (message) payload.message = message;
    S.busy = true;
    return API.ask(payload).then(function (res) {
      S.busy = false;
      if (message) {
        S.thread.push({ role: 'user', content: message });
        S.thread.push({ role: 'assistant', content: answerText(res.answer) });
      } else {
        S.answer = res.answer;
      }
      syncPanel();
    }).catch(function () { S.busy = false; toast('AI request failed'); syncPanel(); });
  }
  function answerText(a) {
    var parts = [a.lead, a.body];
    if (a.analogy) parts.push('Picture it: ' + a.analogy);
    return parts.filter(Boolean).join('\n');
  }
  function setMode(mode) {
    S.mode = mode; S.answer = null;
    S.explainConcepts = null; S.clarifyQuestion = null;
    S.justSaved = false; S.savedEntryId = null;
    S.busy = true; syncPanel();
    if (mode === 'explain') explainSelection(S.selText);
    else askServer(mode, null);
  }
  function send() {
    var q = (S.draft || '').trim(); if (!q) return;
    S.draft = ''; S.mode = 'ask';
    askServer('ask', q);
  }
  function closePanel() {
    S.panelOpen = false; S.chatMode = false; S.modelMenuOpen = false;
    hideToolbar(); syncPanel();
  }

  // ---- Image viewer (lightbox) ----
  // Tapping a figure in the reader opens it full size in a scrollable pane.
  function openImageViewer(src, alt) {
    var img = $('imgViewerImg');
    img.src = src;
    img.alt = alt || '';
    $('imgViewerPane').scrollTop = 0;
    $('imgViewerPane').scrollLeft = 0;
    $('imgViewer').hidden = false;
  }
  function closeImageViewer() {
    var v = $('imgViewer');
    if (v.hidden) return;
    v.hidden = true;
    $('imgViewerImg').removeAttribute('src');
  }
  function saveEntry() {
    // Concept-based Explain saves the extracted concept(s).
    if (S.mode === 'explain') {
      if (S.justSaved) return;
      var toSave = (S.explainConcepts || []).filter(function (c) { return c.label; });
      if (!toSave.length) return;
      API.kbSave({
        concepts: toSave, item_id: S.item ? S.item.id : null,
        model: S.model, span_text: S.selText
      }).then(function (res) {
        S.justSaved = true;
        var entries = res.entries || [];
        if (entries.length) S.savedEntryId = entries[0].id;
        S.explainConcepts = (S.explainConcepts || []).map(function (c) {
          c.reused = true; return c;
        });
        toast('Saved to your knowledge base');
        syncPanel();
        loadConcepts();
      }).catch(function () { toast('Save failed'); });
      return;
    }
    // Summarize / ask keep the single-answer save path.
    if (!S.answer || S.savedEntryId) return;
    var payload = {
      span_text: S.selText, item_id: S.item ? S.item.id : null,
      mode: S.mode, model: S.model, answer: S.answer, thread: S.thread
    };
    API.kbSave(payload).then(function (res) {
      S.savedEntryId = res.entry.id;
      S.justSaved = true;
      toast('Saved to your knowledge base');
      syncPanel();
      loadConcepts();
    }).catch(function () { toast('Save failed'); });
  }

  // -- knowledge base search (debounced) --
  var kbTimer = null;
  // Fetch results for the current query and re-render the saved screen.
  // inPlace keeps caret/focus in the search box (live typing); a full render
  // is used when the route is applied (deep link / Back-Forward restore).
  function runKbSearch(q, inPlace) {
    var query = (q || '').trim();
    var p = query ? API.kbSearch(query) : API.kb();
    return p.then(function (res) {
      S.kbEntries = res.entries || [];
      if (!query) S.kbCount = S.kbEntries.length;
      if (S.screen !== 'saved') return;
      if (inPlace) renderInPlaceSaved(); else render();
    });
  }
  function onKbSearch(v) {
    S.kbQuery = v;
    // Reflect the query in the URL as the user types, but replace the current
    // history entry so typing does not spam Back/Forward.
    history.replaceState(null, '', buildHash('saved', { q: v }));
    clearTimeout(kbTimer);
    kbTimer = setTimeout(function () { runKbSearch(v, true); }, 220);
  }
  function renderInPlaceSaved() {
    // Re-render saved screen but keep focus in the search box.
    var input = $('kbSearch');
    var pos = input ? input.selectionStart : null;
    $('screen').innerHTML = renderSaved();
    var ni = $('kbSearch');
    if (ni) { ni.focus(); if (pos != null) try { ni.setSelectionRange(pos, pos); } catch (e) {} }
  }
  // The reader underlines KB CONCEPTS (term + reusable definition) from cache.
  // Concepts carry their cached definition so a tap shows it with zero AI.
  function loadConcepts() {
    return API.kbConcepts().then(function (res) {
      S.concepts = {};
      (res.concepts || []).forEach(function (c) {
        if (c.label) S.concepts[c.label.toLowerCase()] = c;
      });
      if (S.screen === 'reader') render();
    }).catch(function () {});
  }

  // -- concept detail (back-references) --
  // Open a concept's detail view: term + definition + every article that points
  // to it. Shows a loading state first, then the fetched entry (+linked_items).
  function openConcept(id) {
    S.conceptDetail = null;
    go('concept');
    return API.kbGet(id).then(function (e) {
      if (S.screen !== 'concept') return;
      S.conceptDetail = e;
      render();
    }).catch(function () { toast('Could not open concept'); navigate('saved'); });
  }
  // Remove a kb_entry (concept) and refresh the KB list / map cache. From the
  // detail view we return to the list; from the list we re-query in place.
  function removeKbEntry(id, fromDetail) {
    return API.deleteKbEntry(id).then(function () {
      toast('Removed from your knowledge base');
      loadConcepts();            // refresh the reader's underline cache
      if (S.mapData) loadMap();  // keep the knowledge map in sync
      if (fromDetail) navigate('saved');
      else if (S.screen === 'saved') runKbSearch(S.kbQuery, false);
    }).catch(function () { toast('Could not remove'); });
  }

  // -- map interactions --
  var linkMode = false, linkFirst = null, dragState = null;
  function loadMap() { API.map().then(function (d) { S.mapData = d; if (S.screen === 'map') render(); }); }
  function wireMap() {
    linkFirst = null;
    var stage = $('mapStage');
    if (!stage) return;
    var linkBtn = $('mapLinkBtn'), aiBtn = $('mapAiBtn'), hint = $('mapLinkHint');
    if (linkBtn) linkBtn.classList.toggle('on', linkMode);
    function setHint() {
      hint.textContent = linkMode ? (linkFirst ? 'Pick the second concept…' : 'Pick the first concept…') : '';
    }
    setHint();
    if (linkBtn) linkBtn.addEventListener('click', function () {
      linkMode = !linkMode; linkFirst = null;
      linkBtn.classList.toggle('on', linkMode);
      document.querySelectorAll('.map-node').forEach(function (n) { n.classList.remove('linking'); });
      setHint();
    });
    if (aiBtn) aiBtn.addEventListener('click', function () {
      aiBtn.disabled = true; aiBtn.innerHTML = '<svg viewBox="0 0 24 24" class="ico spin" style="width:16px;height:16px;stroke:#fff">' + ICON.refresh + '</svg>Asking AI…';
      API.mapAiLinks(S.model).then(function (r) {
        toast(r.added ? ('Added ' + r.added + ' link' + (r.added === 1 ? '' : 's')) : 'No new links found');
        loadMap();
      }).catch(function () { toast('AI links failed'); loadMap(); });
    });
    // edges: tap to delete
    stage.querySelectorAll('line[data-edge]').forEach(function (ln) {
      ln.style.cursor = 'pointer'; ln.style.strokeWidth = '0.8';
      ln.addEventListener('click', function () {
        var id = ln.getAttribute('data-edge');
        API.mapEdgeDelete(id).then(function () { toast('Link removed'); loadMap(); });
      });
    });
    // nodes: drag or link
    stage.querySelectorAll('.map-node').forEach(function (node) {
      node.addEventListener('pointerdown', function (ev) {
        if (linkMode) return; // linking handled on click
        ev.preventDefault();
        var rect = stage.getBoundingClientRect();
        dragState = { node: node, id: node.getAttribute('data-node'), rect: rect, moved: false };
        node.setPointerCapture(ev.pointerId);
        node.classList.add('dragging');
      });
      node.addEventListener('pointermove', function (ev) {
        if (!dragState || dragState.node !== node) return;
        dragState.moved = true;
        var rect = dragState.rect;
        var x = ((ev.clientX - rect.left) / rect.width) * 100;
        var y = ((ev.clientY - rect.top) / rect.height) * 100;
        x = Math.max(3, Math.min(97, x)); y = Math.max(4, Math.min(96, y));
        node.style.left = x + '%'; node.style.top = y + '%';
        dragState.x = x; dragState.y = y;
        updateEdgesFor(dragState.id, x, y);
      });
      node.addEventListener('pointerup', function (ev) {
        if (linkMode) return; // linking handled on click (single path)
        if (!dragState || dragState.node !== node) return;
        node.classList.remove('dragging');
        if (dragState.moved && dragState.x != null) {
          API.mapPosition(dragState.id, dragState.x, dragState.y).catch(function () {});
        }
        dragState = null;
      });
      node.addEventListener('click', function () { if (linkMode) handleLinkClick(node); });
    });
    function handleLinkClick(node) {
      var id = node.getAttribute('data-node');
      if (!linkFirst) {
        linkFirst = id; node.classList.add('linking'); setHint();
        return;
      }
      if (linkFirst === id) { node.classList.remove('linking'); linkFirst = null; setHint(); return; }
      API.mapEdgeAdd(linkFirst, id).then(function () {
        toast('Linked'); linkFirst = null; loadMap();
      }).catch(function () { toast('Could not link'); linkFirst = null; loadMap(); });
    }
  }
  function updateEdgesFor(nodeId, x, y) {
    var stage = $('mapStage'); if (!stage) return;
    stage.querySelectorAll('line[data-edge]').forEach(function (ln) {
      S.mapData.edges.forEach(function (e) {
        if (String(e.id) !== ln.getAttribute('data-edge')) return;
        if (String(e.src) === String(nodeId)) { ln.setAttribute('x1', x); ln.setAttribute('y1', y); }
        if (String(e.dst) === String(nodeId)) { ln.setAttribute('x2', x); ln.setAttribute('y2', y); }
      });
    });
  }

  // ====================================================================
  // EVENT WIRING (delegation)
  // ====================================================================
  function wireGlobal() {
    $('topbarTheme').addEventListener('click', toggleTheme);
    $('railTheme').addEventListener('click', toggleTheme);

    // Collapsible rail (a toggle in the rail and one in the topbar).
    $('railCollapse').addEventListener('click', toggleRail);
    $('topbarCollapse').addEventListener('click', toggleRail);

    // Account menu (profile / log out), reachable on desktop (rail) + phone (topbar).
    $('railUser').addEventListener('click', function (e) { e.stopPropagation(); toggleAccountMenu($('railUser')); });
    $('topbarAccount').addEventListener('click', function (e) { e.stopPropagation(); toggleAccountMenu($('topbarAccount')); });
    $('accountMenu').addEventListener('click', function (e) { if (e.target.closest('#accountLogout')) logout(); });
    document.addEventListener('click', function (e) {
      if (!accountMenuOpen()) return;
      if (e.target.closest('#accountMenu') || e.target.closest('#railUser') || e.target.closest('#topbarAccount')) return;
      closeAccountMenu();
    });

    document.querySelectorAll('[data-screen]').forEach(function (b) {
      if (b.dataset.screen === 'refresh-trigger') return;
      b.addEventListener('click', function () { navigate(b.dataset.screen); });
    });
    $('railRefresh').addEventListener('click', triggerRefresh);

    // Image viewer: close on the backdrop, the close button, or Escape.
    $('imgViewer').addEventListener('click', function (e) {
      if (!e.target.closest('#imgViewerImg')) closeImageViewer();
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !$('imgViewer').hidden) closeImageViewer();
    });

    // Browser Back/Forward (and address-bar hash edits) re-apply the route.
    window.addEventListener('hashchange', applyRoute);

    // Screen-level delegation.
    $('screen').addEventListener('click', function (e) {
      // Tapping a reader figure opens it in the fullscreen viewer. (Text
      // selection drags don't fire a click, so this won't disturb selecting.)
      var fig = e.target.closest('.gym-read img');
      if (fig && fig.getAttribute('src')) { openImageViewer(fig.src, fig.alt); return; }
      var sortBtn = e.target.closest('.feed-sort[data-sort]');
      if (sortBtn) {
        var sk = kindFor(S.screen);
        S.feeds[sk].sort = sortBtn.dataset.sort;
        navigate(S.screen);
        return;
      }
      var rm = e.target.closest('.feed-remove');
      if (rm) { e.stopPropagation(); removeAdded(parseInt(rm.getAttribute('data-id'), 10)); return; }
      var card = e.target.closest('.feed-card, .feed-read');
      if (card) {
        var id = card.getAttribute('data-id');
        if (id) { S.readerReturn = location.hash; navigate('reader', { id: parseInt(id, 10) }); return; }
      }
      var seg = e.target.closest('.gym-seg[data-d]');
      if (seg) { S.density = seg.dataset.d; render(); return; }
      var back = e.target.closest('.reader-back');
      if (back) {
        if (S.readerReturn && location.hash !== S.readerReturn) location.hash = S.readerReturn;
        else navigate('papers');
        return;
      }
      var mdBtn = e.target.closest('#mdAttachBtn');
      if (mdBtn) { var inp = $('mdFile'); if (inp) inp.click(); return; }
      var pdfBtn = e.target.closest('#addPdfBtn');
      if (pdfBtn) { var pinp = $('addPdf'); if (pinp) pinp.click(); return; }
      if (e.target.closest('#chatArticleBtn')) { openChat(); return; }
      var term = e.target.closest('.gym-term');
      if (term) {
        var concept = term.getAttribute('data-concept');
        if (concept) { openConceptPanel(concept); return; }
        openPanel(term.getAttribute('data-term'), 'explain'); return;
      }
      var kbOpen = e.target.closest('.kb-open');
      if (kbOpen) { e.stopPropagation(); navigate('reader', { id: parseInt(kbOpen.getAttribute('data-item'), 10) }); return; }
      var kbRemove = e.target.closest('.kb-remove');
      if (kbRemove) {
        e.stopPropagation();
        var rmId = parseInt(kbRemove.getAttribute('data-id'), 10);
        if (window.confirm('Remove this from your knowledge base?')) removeKbEntry(rmId, false);
        return;
      }
      // Concept detail view: open a linked article, remove the concept, go back.
      var kbArticle = e.target.closest('.kb-article');
      if (kbArticle) { navigate('reader', { id: parseInt(kbArticle.getAttribute('data-id'), 10) }); return; }
      var conceptRemove = e.target.closest('.concept-remove');
      if (conceptRemove) {
        var crId = parseInt(conceptRemove.getAttribute('data-id'), 10);
        if (window.confirm('Remove this from your knowledge base?')) removeKbEntry(crId, true);
        return;
      }
      if (e.target.closest('.concept-back')) { navigate('saved'); return; }
      // Tapping a KB card (outside its inner buttons/links) opens the concept.
      var kbCard = e.target.closest('.kb-card');
      if (kbCard) {
        if (e.target.closest('a')) return;  // let an inner document link work
        navigate('concept', { id: parseInt(kbCard.getAttribute('data-id'), 10) });
        return;
      }
    });
    $('screen').addEventListener('input', function (e) {
      if (e.target.id === 'kbSearch') onKbSearch(e.target.value);
      else if (e.target.id === 'feedSearch') onFeedSearch(kindFor(S.screen), e.target.value);
    });
    // Add-article form (Added screen): submit (button or Enter) posts the link.
    $('screen').addEventListener('submit', function (e) {
      if (e.target.id === 'addForm') { e.preventDefault(); addArticle(); }
    });
    $('screen').addEventListener('change', function (e) {
      if (e.target.id === 'mdFile' && e.target.files && e.target.files[0]) {
        uploadSupportingDoc(e.target.files[0]);
        e.target.value = '';
      }
      if (e.target.id === 'addPdf' && e.target.files && e.target.files[0]) {
        uploadPdf(e.target.files[0]);
        e.target.value = '';
      }
      var filt = e.target.closest && e.target.closest('.feed-filter');
      if (filt) {
        var fk = kindFor(S.screen);
        S.feeds[fk].filters[filt.getAttribute('data-filter')] = filt.value;
        navigate(S.screen);
      }
    });

    // Reader text selection.
    var scroll = $('scroll');
    scroll.addEventListener('mouseup', onSelect);
    scroll.addEventListener('touchend', onSelect);

    // Toolbar.
    $('toolbar').addEventListener('click', function (e) {
      var b = e.target.closest('button[data-mode]');
      if (b) openPanel(S.selText, b.dataset.mode);
    });

    // Scrim closes panel.
    $('scrim').addEventListener('click', closePanel);

    // Panel delegation (panel re-renders, so delegate on the container).
    $('panel').addEventListener('click', function (e) {
      if (e.target.closest('#panelClose')) { closePanel(); return; }
      if (e.target.closest('#modelBtn')) { S.modelMenuOpen = !S.modelMenuOpen; syncPanel(); return; }
      var pick = e.target.closest('.model-pick');
      if (pick) { S.model = pick.getAttribute('data-id'); S.modelMenuOpen = false; syncPanel(); return; }
      var seg = e.target.closest('.gym-seg[data-mode]');
      if (seg) { setMode(seg.dataset.mode); return; }
      if (e.target.closest('#panelSend')) { S.chatMode ? sendChat() : send(); return; }
      if (e.target.closest('#panelSave')) { saveEntry(); return; }
      if (e.target.closest('.panel-gosaved')) { closePanel(); navigate('saved'); return; }
    });
    $('panel').addEventListener('input', function (e) {
      if (e.target.id === 'panelDraft') S.draft = e.target.value;
    });
    $('panel').addEventListener('keydown', function (e) {
      if (e.target.id === 'panelDraft' && e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        S.chatMode ? sendChat() : send();
      }
    });

    // Re-sync panel mode on viewport change.
    window.matchMedia('(min-width: 901px)').addListener(function () { if (S.panelOpen) syncPanel(); });
  }

  // -- refresh --
  var refreshPoll = null;
  function triggerRefresh() {
    API.refresh(null, 7).then(function (st) {
      toast('Refreshing the feed…');
      pollRefresh();
    }).catch(function () { toast('Could not start refresh'); });
  }
  function pollRefresh() {
    clearInterval(refreshPoll);
    refreshPoll = setInterval(function () {
      API.refreshStatus().then(function (st) {
        if (st.status === 'done') {
          clearInterval(refreshPoll);
          toast('Feed updated');
          // Re-pull both feeds' facets and the active feed's items.
          loadFacets('paper'); loadFacets('repo');
          if (S.screen === 'papers' || S.screen === 'repos') loadFeed(kindFor(S.screen));
        } else if (st.status === 'error') {
          clearInterval(refreshPoll);
          toast('Refresh failed');
        }
      }).catch(function () { clearInterval(refreshPoll); });
    }, 2000);
  }

  // ====================================================================
  // BOOT
  // ====================================================================
  function boot() {
    applyTheme();
    applyRail();
    wireGlobal();

    API.me().then(function (me) {
      S.user = me;
      $('railUserName').textContent = me.username;
      $('railAvatar').textContent = (me.username || '?').charAt(0);
      // Apply the current hash as a deep link once authenticated. A direct
      // #/read/<id> visit fetches the item via openReader, so cold loads work.
      applyRoute();
    }).catch(function () {});

    API.models().then(function (m) {
      S.models = m;
      S.model = m.default || (m.providers[0] && m.providers[0].models[0] && m.providers[0].models[0].id) || null;
    }).catch(function () {});

    loadConcepts();
    render();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
