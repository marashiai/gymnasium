/* App state, mirroring the prototype's DCLogic state. Theme is persisted to
   localStorage; the model defaults to a cheap configured model, else the first
   one opencode reports. */
(function () {
  var THEME_KEY = 'gym.theme';
  var RAIL_KEY = 'gym.rail';

  function defaultTheme() {
    var saved = null;
    try { saved = localStorage.getItem(THEME_KEY); } catch (e) {}
    if (saved === 'light' || saved === 'dark') return saved;
    // Phone defaults light, desktop dark (per the handoff).
    return window.matchMedia('(min-width: 901px)').matches ? 'dark' : 'light';
  }

  function defaultRailCollapsed() {
    try { return localStorage.getItem(RAIL_KEY) === 'collapsed'; } catch (e) { return false; }
  }

  window.State = {
    user: null,
    screen: 'papers',         // papers | repos | reader | saved | map | added
    density: 'comfort',       // comfort | compact
    theme: defaultTheme(),
    railCollapsed: defaultRailCollapsed(),  // icon-only desktop rail
    // Two tracker feeds + the user-added feed, each with its own search/sort.
    feeds: {
      paper: { items: [], q: '', sort: 'recency', facets: null,
               filters: { author: '', company: '', publication: '' } },
      repo: { items: [], q: '', sort: 'recency', facets: null,
              filters: { company: '', language: '' } },
      added: { items: [] }
    },
    readerReturn: '#/papers', // hash to return to from the reader
    item: null,               // current reader item (full dict)
    summaryTerms: [],         // terms to underline in the reader
    glossary: {},             // term(lower) -> kb entry id, for underlining
    concepts: {},             // label(lower) -> {id,label,lead,body,analogy}
    // conversation
    panelOpen: false,
    selText: '',
    mode: 'explain',          // explain | summarize | ask
    answer: null,             // {lead, body, analogy?}
    answerCitations: [],      // resolved source passages for the latest answer
    explainConcepts: null,    // [{label,lead,body,analogy,reused,kb_entry_id}]
    clarifyQuestion: null,    // AI's clarifying question when the span is vague
    thread: [],               // [{role:'user'|'assistant', content, citations?}]
    savedEntryId: null,       // set once the entry is persisted
    justSaved: false,
    // article chat (whole-article, knowledge-grounded conversation)
    chatMode: false,          // panel is in article-chat mode
    chatEntryId: null,        // persistent per-article chat kb_entry id
    chatGrounded: null,       // {notes:[term], concepts:[label]} last grounding
    draft: '',
    affordance: null,         // {x, y} for the floating toolbar
    // models
    models: { providers: [] },
    model: null,
    modelMenuOpen: false,
    // kb / map
    kbEntries: [],
    kbQuery: '',
    conceptDetail: null,      // loaded kb_entry (+linked_items) for the detail view
    mapData: { nodes: [], edges: [] },
    mapSuggestions: [],       // semantic links awaiting explicit acceptance
    // misc
    busy: false,

    saveTheme: function () {
      try { localStorage.setItem(THEME_KEY, this.theme); } catch (e) {}
    },
    saveRail: function () {
      try { localStorage.setItem(RAIL_KEY, this.railCollapsed ? 'collapsed' : 'expanded'); } catch (e) {}
    },
    isDesktop: function () { return window.matchMedia('(min-width: 901px)').matches; },
    modelName: function (id) {
      id = id || this.model;
      var provs = (this.models && this.models.providers) || [];
      for (var i = 0; i < provs.length; i++) {
        var ms = provs[i].models || [];
        for (var j = 0; j < ms.length; j++) {
          if (ms[j].id === id) return ms[j].name;
        }
      }
      return id || 'Model';
    }
  };
})();
