/*
 * DeepSeek Harness - native chat dashboard for the Agent dropdown.
 *
 * Not a CLI screen: this module owns one persistent panel inside #ws-screens,
 * keeps its own conversation rail, and streams answers over SSE from
 * /api/deepseek/. Credentials never enter this code path - the backend only
 * reports whether a key is configured.
 *
 * Public API (called by workspace.js):
 *   DeepSeekChat.open({host, token, modelId, getModel})
 *   DeepSeekChat.close()
 *   DeepSeekChat.isOpen()
 *   DeepSeekChat.newChat()
 *   DeepSeekChat.setModel(id)
 *   DeepSeekChat.focus()
 */
(function () {
  "use strict";

  var LAST_KEY = "dennis.deepseek.last";

  var state = {
    token: null,
    host: null,
    el: null,
    opened: false,
    status: null,
    conversations: [],
    current: null,
    streaming: false,
    controller: null,
    getModel: null,
    modelId: null,
    live: null,
    scrollPinned: true,
  };

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function api(path, opts) {
    opts = opts || {};
    var headers = { "X-Dennis-Token": state.token || "" };
    if (opts.body !== undefined) headers["Content-Type"] = "application/json";
    return fetch(path, {
      method: opts.method || "GET",
      headers: headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        return { ok: res.ok, status: res.status, data: data };
      });
    });
  }

  function modelId() {
    var live = state.getModel ? state.getModel() : null;
    return live || state.modelId || (state.status && state.status.default_model) || "deepseek-chat";
  }

  function conversationId() {
    return state.current && state.current.id;
  }

  function savedConversation() {
    try { return window.localStorage.getItem(LAST_KEY) || null; } catch (e) { return null; }
  }
  function rememberConversation(id) {
    try {
      if (id) window.localStorage.setItem(LAST_KEY, id);
      else window.localStorage.removeItem(LAST_KEY);
    } catch (e) { /* storage unavailable */ }
  }

  // ------------------------------------------------------------------
  // DOM
  // ------------------------------------------------------------------
  function build(host) {
    var el = document.createElement("div");
    el.id = "ds-chat";
    el.className = "ds-chat hidden";
    el.innerHTML =
      '<aside class="ds-rail">' +
        '<div class="ds-rail-head">' +
          '<div class="ds-brand"><span class="ds-brand-mark">D</span><span>DeepSeek</span></div>' +
          '<button id="ds-new" class="ds-icon-btn" title="New chat">+</button>' +
        '</div>' +
        '<div id="ds-rail-list" class="ds-rail-list"></div>' +
        '<div class="ds-rail-foot">' +
          '<span id="ds-key-state" class="ds-key-state">Checking key…</span>' +
          '<button id="ds-key-btn" class="ds-link-btn">Key</button>' +
        '</div>' +
      '</aside>' +
      '<section class="ds-main">' +
        '<header class="ds-head">' +
          '<span id="ds-title" class="ds-title">New chat</span>' +
          '<span id="ds-model" class="ds-model-chip">deepseek-chat</span>' +
          '<span class="ds-head-spacer"></span>' +
          '<span id="ds-conn" class="ds-conn">Ready</span>' +
        '</header>' +
        '<div id="ds-messages" class="ds-messages" aria-live="polite">' +
          '<div class="ds-welcome">' +
            '<div class="ds-welcome-mark">D</div>' +
            '<h3>DeepSeek Harness</h3>' +
            '<p>A native DeepSeek chat inside DENNIS Agentic OS. Pick a model above and ask anything.</p>' +
          '</div>' +
        '</div>' +
        '<div id="ds-setup" class="ds-setup hidden">' +
          '<div class="ds-setup-title">Connect DeepSeek</div>' +
          '<p>Paste an API key from platform.deepseek.com. It is stored locally with owner-only permissions and never returned to the browser. You can also connect it from the <b>Provider</b> menu above.</p>' +
          '<div class="ds-setup-row">' +
            '<input id="ds-key-input" type="password" autocomplete="off" spellcheck="false" placeholder="sk-…" aria-label="DeepSeek API key">' +
            '<button id="ds-key-save" class="ds-btn ds-btn-primary">Save</button>' +
            '<button id="ds-key-cancel" class="ds-btn">Cancel</button>' +
          '</div>' +
          '<div id="ds-key-msg" class="ds-key-msg"></div>' +
        '</div>' +
        '<form id="ds-composer" class="ds-composer">' +
          '<textarea id="ds-input" rows="1" placeholder="Message DeepSeek…" aria-label="Message DeepSeek"></textarea>' +
          '<button id="ds-stop" type="button" class="ds-btn ds-btn-stop hidden">Stop</button>' +
          '<button id="ds-send" type="submit" class="ds-btn ds-btn-primary" disabled>Send</button>' +
        '</form>' +
        '<div class="ds-hint">Enter to send · Shift+Enter for a new line</div>' +
      '</section>';

    if (host) host.appendChild(el);
    state.el = el;

    $("ds-new").addEventListener("click", function () { newChat({ focus: true }); });
    $("ds-key-btn").addEventListener("click", showSetup);
    $("ds-key-cancel").addEventListener("click", hideSetup);
    $("ds-key-save").addEventListener("click", saveKey);
    $("ds-key-input").addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); saveKey(); }
      if (e.key === "Escape") { e.preventDefault(); hideSetup(); }
    });
    $("ds-composer").addEventListener("submit", function (e) {
      e.preventDefault();
      send();
    });
    $("ds-stop").addEventListener("click", stop);
    $("ds-input").addEventListener("input", function () {
      autoSize();
      updateSendState();
    });
    $("ds-input").addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        send();
      }
    });
    $("ds-messages").addEventListener("scroll", function () {
      var box = $("ds-messages");
      state.scrollPinned = (box.scrollHeight - box.scrollTop - box.clientHeight) < 80;
    });
  }

  function autoSize() {
    var input = $("ds-input");
    if (!input) return;
    input.style.height = "auto";
    input.style.height = Math.min(180, Math.max(38, input.scrollHeight)) + "px";
  }

  function showSetup() {
    var box = $("ds-setup");
    if (!box) return;
    box.classList.remove("hidden");
    var input = $("ds-key-input");
    if (input) { input.value = ""; input.focus(); }
  }

  function hideSetup() {
    var box = $("ds-setup");
    if (box) box.classList.add("hidden");
  }

  function setKeyMessage(text, kind) {
    var el = $("ds-key-msg");
    if (!el) return;
    el.textContent = text || "";
    el.className = "ds-key-msg" + (kind ? " ds-key-msg-" + kind : "");
  }

  function saveKey() {
    var input = $("ds-key-input");
    var key = input ? input.value.trim() : "";
    if (!key) { setKeyMessage("Paste a key first.", "error"); return; }
    var btn = $("ds-key-save");
    if (btn) btn.disabled = true;
    setKeyMessage("Saving…");
    api("/api/deepseek/credentials", { method: "POST", body: { api_key: key } })
      .then(function (r) {
        if (!r.ok) {
          setKeyMessage((r.data && r.data.error) || "Could not save that key.", "error");
          return;
        }
        if (input) input.value = "";
        hideSetup();
        return refreshStatus().then(function () {
          renderMessages();
          focus();
        });
      })
      .catch(function () { setKeyMessage("Backend unreachable.", "error"); })
      .then(function () { if (btn) btn.disabled = false; });
  }

  // ------------------------------------------------------------------
  // Rendering
  // ------------------------------------------------------------------
  function renderStatus() {
    var s = state.status || {};
    var model = $("ds-model");
    if (model) model.textContent = modelId();
    var key = $("ds-key-state");
    if (key) {
      if (s.configured) {
        key.textContent = "Key connected" + (s.hint ? " " + s.hint : "");
        key.className = "ds-key-state ds-key-ok";
      } else {
        key.textContent = "API key required";
        key.className = "ds-key-state ds-key-missing";
      }
    }
    var conn = $("ds-conn");
    if (conn) {
      conn.textContent = state.streaming ? "Replying…" : (s.configured ? "Ready" : "Setup needed");
      conn.className = "ds-conn" + (state.streaming ? " ds-conn-busy" : s.configured ? " ds-conn-ok" : " ds-conn-warn");
    }
    updateSendState();
    var setup = $("ds-setup");
    if (setup && s.configured) setup.classList.add("hidden");
  }

  function updateSendState() {
    var send = $("ds-send");
    var input = $("ds-input");
    if (!send || !input) return;
    var configured = !!(state.status && state.status.configured);
    send.disabled = state.streaming || !configured || !input.value.trim();
    send.classList.toggle("hidden", state.streaming);
    var stop = $("ds-stop");
    if (stop) stop.classList.toggle("hidden", !state.streaming);
  }

  function renderRail() {
    var host = $("ds-rail-list");
    if (!host) return;
    host.innerHTML = "";
    if (!state.conversations.length) {
      host.innerHTML = '<div class="ds-rail-empty">No chats yet.</div>';
      return;
    }
    state.conversations.forEach(function (conv) {
      var row = document.createElement("div");
      row.className = "ds-rail-item" + (state.current && conv.id === state.current.id ? " ds-rail-item-active" : "");
      row.setAttribute("data-conversation-id", conv.id);

      var open = document.createElement("button");
      open.type = "button";
      open.className = "ds-rail-open";
      open.innerHTML =
        '<span class="ds-rail-title">' + esc(conv.title || "New chat") + "</span>" +
        '<span class="ds-rail-meta">' + esc(conv.preview || conv.model_id || "") + "</span>";
      open.addEventListener("click", function () { openConversation(conv.id); });

      var del = document.createElement("button");
      del.type = "button";
      del.className = "ds-rail-del";
      del.title = "Delete chat";
      del.textContent = "\u00d7";
      del.addEventListener("click", function (e) {
        e.stopPropagation();
        removeConversation(conv.id);
      });

      row.appendChild(open);
      row.appendChild(del);
      host.appendChild(row);
    });
  }

  function renderTitle() {
    var el = $("ds-title");
    if (!el) return;
    el.textContent = (state.current && state.current.title) || "New chat";
    var model = $("ds-model");
    if (model) model.textContent = (state.current && state.current.model_id) || modelId();
  }

  function renderMessages() {
    var host = $("ds-messages");
    if (!host) return;
    host.innerHTML = "";
    var msgs = (state.current && state.current.messages) || [];
    if (!msgs.length) {
      if (state.status && !state.status.configured) {
        host.appendChild(setupWelcome());
      } else {
        host.appendChild(welcome());
      }
      return;
    }
    msgs.forEach(function (msg) { host.appendChild(messageEl(msg)); });
    scrollToBottom(true);
  }

  function welcome() {
    var div = document.createElement("div");
    div.className = "ds-welcome";
    div.innerHTML =
      '<div class="ds-welcome-mark">D</div>' +
      "<h3>DeepSeek Harness</h3>" +
      "<p>Ask anything. The conversation is saved locally and can be reopened from the rail.</p>";
    return div;
  }

  function setupWelcome() {
    var div = document.createElement("div");
    div.className = "ds-welcome";
    div.innerHTML =
      '<div class="ds-welcome-mark">D</div>' +
      "<h3>Connect DeepSeek</h3>" +
      "<p>Add a DeepSeek API key to start chatting. The key stays on this machine and is never sent to the browser again.</p>";
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "ds-btn ds-btn-primary";
    btn.textContent = "Add API key";
    btn.addEventListener("click", showSetup);
    div.appendChild(btn);
    return div;
  }

  function messageEl(msg) {
    var row = document.createElement("div");
    row.className = "ds-msg ds-msg-" + (msg.role === "user" ? "user" : "assistant");
    if (msg.id) row.setAttribute("data-message-id", msg.id);

    var label = document.createElement("div");
    label.className = "ds-msg-label";
    label.textContent = msg.role === "user" ? "You" : "DeepSeek";
    row.appendChild(label);

    var body = document.createElement("div");
    body.className = "ds-msg-body";
    body.textContent = msg.content || "";
    row.appendChild(body);

    if (msg.reasoning) row.appendChild(reasoningEl(msg.reasoning));
    if (msg.interrupted) {
      var note = document.createElement("div");
      note.className = "ds-msg-note";
      note.textContent = "Stopped before completion";
      row.appendChild(note);
    }
    return row;
  }

  function reasoningEl(text) {
    var wrap = document.createElement("details");
    wrap.className = "ds-reasoning";
    var summary = document.createElement("summary");
    summary.textContent = "Reasoning";
    var body = document.createElement("div");
    body.className = "ds-reasoning-body";
    body.textContent = text || "";
    wrap.appendChild(summary);
    wrap.appendChild(body);
    return wrap;
  }

  function appendLiveMessage() {
    var host = $("ds-messages");
    if (!host) return null;
    var welcomeEl = host.querySelector(".ds-welcome");
    if (welcomeEl) welcomeEl.remove();

    var row = document.createElement("div");
    row.className = "ds-msg ds-msg-assistant ds-msg-live";
    row.innerHTML =
      '<div class="ds-msg-label">DeepSeek</div>' +
      '<div class="ds-msg-body"><span class="ds-typing"><i></i><i></i><i></i></span></div>';
    host.appendChild(row);
    scrollToBottom(true);
    return row;
  }

  function appendUserMessage(text) {
    var host = $("ds-messages");
    if (!host) return;
    var welcomeEl = host.querySelector(".ds-welcome");
    if (welcomeEl) welcomeEl.remove();
    var row = messageEl({ role: "user", content: text });
    host.appendChild(row);
    scrollToBottom(true);
  }

  function scrollToBottom(force) {
    var box = $("ds-messages");
    if (!box) return;
    if (!force && !state.scrollPinned) return;
    box.scrollTop = box.scrollHeight;
  }

  // ------------------------------------------------------------------
  // Data
  // ------------------------------------------------------------------
  function refreshStatus() {
    return api("/api/deepseek/status").then(function (r) {
      if (r.ok) {
        state.status = r.data || {};
        if (state.status.default_model && !state.modelId) state.modelId = state.status.default_model;
      } else if (r.status === 401) {
        state.status = { configured: false };
      }
      renderStatus();
      return state.status;
    }).catch(function () { return null; });
  }

  function refreshList() {
    return api("/api/deepseek/conversations").then(function (r) {
      if (!r.ok) return;
      state.conversations = (r.data && r.data.conversations) || [];
      renderRail();
    }).catch(function () { /* leave the last rail intact */ });
  }

  function openConversation(id) {
    if (state.streaming) return;
    if (state.current && state.current.id === id) { focus(); return; }
    return api("/api/deepseek/conversations/" + encodeURIComponent(id)).then(function (r) {
      if (!r.ok) return;
      state.current = r.data.conversation;
      rememberConversation(id);
      renderTitle();
      renderMessages();
      renderRail();
      focus();
    });
  }

  function newChat(opts) {
    opts = opts || {};
    if (state.streaming) return Promise.resolve(null);
    if (state.current && !(state.current.messages || []).length && !opts.force) {
      if (opts.focus) focus();
      return Promise.resolve(state.current);
    }
    return api("/api/deepseek/conversations", {
      method: "POST",
      body: { model_id: modelId() },
    }).then(function (r) {
      if (!r.ok) {
        if (r.data && r.data.error) renderError(r.data.error);
        return null;
      }
      state.current = r.data.conversation;
      rememberConversation(state.current.id);
      renderTitle();
      renderMessages();
      refreshList();
      if (opts.focus !== false) focus();
      return state.current;
    }).catch(function () { renderError("Backend unreachable."); return null; });
  }

  function removeConversation(id) {
    if (state.streaming) return;
    if (!window.confirm("Delete this chat? This cannot be undone.")) return;
    api("/api/deepseek/conversations/" + encodeURIComponent(id), { method: "DELETE" }).then(function (r) {
      if (!r.ok) return;
      if (state.current && state.current.id === id) {
        state.current = null;
        rememberConversation(null);
        renderTitle();
        renderMessages();
      }
      refreshList();
    });
  }

  function renderError(text) {
    var host = $("ds-messages");
    if (!host) return;
    var el = document.createElement("div");
    el.className = "ds-error";
    el.textContent = text || "Something went wrong.";
    host.appendChild(el);
    scrollToBottom(true);
  }

  // ------------------------------------------------------------------
  // Streaming
  // ------------------------------------------------------------------
  function send() {
    if (state.streaming) return;
    var input = $("ds-input");
    var text = input ? input.value.trim() : "";
    if (!text) return;
    if (!state.status || !state.status.configured) {
      showSetup();
      renderMessages();
      return;
    }
    var ready = state.current ? Promise.resolve(state.current) : newChat({ focus: false });
    ready.then(function (conv) {
      if (!conv) return;
      if (input) { input.value = ""; autoSize(); updateSendState(); }
      appendUserMessage(text);
      streamTurn(conv.id, text);
    });
  }

  function streamTurn(cid, text) {
    state.streaming = true;
    state.scrollPinned = true;
    updateSendState();
    renderStatus();

    var live = appendLiveMessage();
    state.live = {
      row: live,
      body: live ? live.querySelector(".ds-msg-body") : null,
      content: "",
      reasoning: "",
      reasoningEl: null,
    };
    if (state.live.body) state.live.body.textContent = "";

    var controller = window.AbortController ? new AbortController() : null;
    state.controller = controller;

    fetch("/api/deepseek/conversations/" + encodeURIComponent(cid) + "/messages", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Dennis-Token": state.token || "",
      },
      body: JSON.stringify({ content: text, model_id: modelId() }),
      signal: controller ? controller.signal : undefined,
    }).then(function (res) {
      if (!res.ok) {
        return res.json().catch(function () { return {}; }).then(function (data) {
          throw new Error((data && data.error) || ("Request failed (" + res.status + ")."));
        });
      }
      if (!res.body || !res.body.getReader) {
        throw new Error("This browser cannot stream responses.");
      }
      return readSse(res.body.getReader(), handleEvent);
    }).catch(function (err) {
      if (err && err.name === "AbortError") {
        finishTurn(true);
        return;
      }
      if (state.live && state.live.body && !state.live.content) {
        state.live.body.textContent = "";
        if (state.live.row) state.live.row.classList.remove("ds-msg-live");
      }
      renderError((err && err.message) || "Could not reach DeepSeek.");
      finishTurn(true);
    });
  }

  function handleEvent(evt) {
    if (!evt || !evt.type) return;
    var live = state.live;
    if (evt.type === "reasoning") {
      if (!live) return;
      live.reasoning += evt.text || "";
      if (!live.reasoningEl && live.row) {
        live.reasoningEl = reasoningEl(live.reasoning);
        live.row.appendChild(live.reasoningEl);
      } else if (live.reasoningEl) {
        var body = live.reasoningEl.querySelector(".ds-reasoning-body");
        if (body) body.textContent = live.reasoning;
      }
      scrollToBottom(false);
      return;
    }
    if (evt.type === "delta") {
      if (!live) return;
      live.content += evt.text || "";
      if (live.body) live.body.textContent = live.content;
      scrollToBottom(false);
      return;
    }
    if (evt.type === "done") {
      if (live) {
        live.content = (evt.message && evt.message.content) || live.content;
        live.reasoning = (evt.message && evt.message.reasoning) || live.reasoning;
        if (live.body) live.body.textContent = live.content;
        if (live.row) live.row.classList.remove("ds-msg-live");
      }
      if (state.current && evt.conversation) {
        state.current.title = evt.conversation.title || state.current.title;
        state.current.updated_at = evt.conversation.updated_at;
        state.current.model_id = evt.conversation.model_id || state.current.model_id;
      }
      renderTitle();
      finishTurn(false);
      refreshList();
      return;
    }
    if (evt.type === "error") {
      renderError(evt.message || "DeepSeek returned an error.");
      finishTurn(true);
    }
  }

  function readSse(reader, onEvent) {
    var decoder = new TextDecoder();
    var buffer = "";
    function drain() {
      var idx;
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        var block = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        var lines = block.split("\n");
        var payload = [];
        for (var i = 0; i < lines.length; i++) {
          if (lines[i].indexOf("data:") === 0) payload.push(lines[i].slice(5).replace(/^ /, ""));
        }
        if (!payload.length) continue;
        var raw = payload.join("\n");
        if (!raw) continue;
        try { onEvent(JSON.parse(raw)); } catch (e) { /* ignore malformed frame */ }
      }
    }
    function pump() {
      return reader.read().then(function (chunk) {
        if (chunk.done) {
          buffer += decoder.decode();
          drain();
          return;
        }
        buffer += decoder.decode(chunk.value, { stream: true });
        drain();
        return pump();
      });
    }
    return pump();
  }

  function finishTurn(interrupted) {
    if (state.live) {
      if (state.live.row) state.live.row.classList.remove("ds-msg-live");
      if (interrupted && state.live.content) {
        var note = document.createElement("div");
        note.className = "ds-msg-note";
        note.textContent = "Stopped before completion";
        state.live.row.appendChild(note);
      }
    }
    state.live = null;
    state.streaming = false;
    state.controller = null;
    updateSendState();
    renderStatus();
    focus();
  }

  function stop() {
    if (state.controller) {
      try { state.controller.abort(); } catch (e) {}
    } else {
      finishTurn(true);
    }
  }

  // ------------------------------------------------------------------
  // Lifecycle
  // ------------------------------------------------------------------
  function open(opts) {
    opts = opts || {};
    if (opts.token) state.token = opts.token;
    if (typeof opts.getModel === "function") state.getModel = opts.getModel;
    if (opts.modelId) state.modelId = opts.modelId;
    if (opts.host) state.host = opts.host;

    if (!state.el) build(state.host);
    else if (state.host && state.el.parentNode !== state.host) state.host.appendChild(state.el);

    state.el.classList.remove("hidden");
    state.opened = true;
    renderTitle();
    renderRail();
    refreshStatus().then(function () { renderMessages(); });
    refreshList().then(function () {
      if (state.streaming) return;
      var wanted = savedConversation();
      var exists = wanted && state.conversations.some(function (c) { return c.id === wanted; });
      if (exists) return openConversation(wanted);
      if (state.conversations.length) return openConversation(state.conversations[0].id);
      state.current = null;
      renderMessages();
    });
    focus();
  }

  function close() {
    state.opened = false;
    if (state.el) state.el.classList.add("hidden");
  }

  function focus() {
    var input = $("ds-input");
    if (input && state.opened) window.setTimeout(function () { input.focus(); }, 0);
  }

  function setModel(id) {
    state.modelId = id;
    var model = $("ds-model");
    if (model && !state.current) model.textContent = id;
    renderStatus();
  }

  window.DeepSeekChat = {
    open: open,
    close: close,
    isOpen: function () { return !!(state.opened && state.el); },
    newChat: newChat,
    setModel: setModel,
    refreshStatus: refreshStatus,
    focus: focus,
    _state: state,
  };
})();
