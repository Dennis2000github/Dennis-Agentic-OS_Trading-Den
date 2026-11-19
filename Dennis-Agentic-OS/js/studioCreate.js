/* =====================================================================
   Ads Studio · Create.

   Additive to the existing five-step Generate workflow. Create composes a
   reviewable brief from the active Ads template, then hands it to a draft
   Ads project and prefills the Generate prompt. It never renders or
   publishes. Products load from the COSMOS Product Backlog — the single
   authoritative backlog management interface now lives in COSMOS, so this
   module no longer renders its own duplicate backlog screen.
   ===================================================================== */
(function () {
  "use strict";

  var S = {
    template: null,
    products: [],
    briefs: [],
    selectedBrief: null,
    loading: false,
    initialized: false
  };

  var STATUS_LABELS = {
    discovered: "Discovered",
    drafted: "Drafted",
    reviewing: "Reviewing",
    approved: "Approved",
    ready_for_creation: "Ready for creation",
    in_production: "In production",
    published: "Published",
    archived: "Archived"
  };

  var STATUS_ORDER = [
    "discovered", "drafted", "reviewing", "approved",
    "ready_for_creation", "in_production", "published", "archived"
  ];

  function $(id) { return document.getElementById(id); }

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function icons() {
    if (window.lucide && window.lucide.createIcons) {
      try { window.lucide.createIcons(); } catch (err) { /* icons are optional */ }
    }
  }

  function toast(message) {
    if (window.Studio && window.Studio.toast) return window.Studio.toast(message);
    return null;
  }

  function api(path, options) {
    var opts = options || {};
    var init = {
      method: opts.method || "GET",
      cache: "no-store",
      headers: new Headers(opts.headers || {})
    };
    if (opts.body !== undefined) {
      init.headers.set("Content-Type", "application/json");
      init.body = JSON.stringify(opts.body);
    }
    return fetch(path, init).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok) {
          var message = (data && (data.error || data.message)) || ("Request failed (" + response.status + ")");
          var error = new Error(message);
          error.status = response.status;
          error.code = data && data.code;
          throw error;
        }
        return data || {};
      });
    });
  }

  function setError(id, message) {
    var el = $(id);
    if (!el) return;
    el.textContent = message || "";
    el.classList.toggle("hidden", !message);
  }

  function busy(button, isBusy, label) {
    if (!button) return;
    if (isBusy) {
      button.dataset.originalLabel = button.textContent;
      button.textContent = label || "Working…";
      button.disabled = true;
    } else {
      if (button.dataset.originalLabel) button.textContent = button.dataset.originalLabel;
      button.disabled = false;
    }
  }

  function dateLabel(value) {
    if (!value) return "—";
    var date = new Date(value);
    if (isNaN(date.getTime())) return "—";
    try {
      return date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    } catch (err) {
      return String(value);
    }
  }

  function priceLabel(price) {
    if (!price || price.amount === null || price.amount === undefined) return "No price";
    return String(price.currency || "") + " " + String(price.amount);
  }

  // -----------------------------------------------------------------------
  // Context
  // -----------------------------------------------------------------------
  function loadContext() {
    return api("/api/studio/ads/create/context").then(function (data) {
      S.template = data.template || null;
      S.products = data.products || [];
      if (S.template) {
        var duration = $("adc-duration");
        if (duration && !duration.value) duration.value = data.duration_default || 15;
        var platforms = $("adc-platforms");
        if (platforms && !platforms.value && (data.platforms || []).length) {
          platforms.value = (data.platforms || []).join(", ");
        }
      }
      renderProductOptions();
      return data;
    });
  }

  function loadBriefs() {
    return api("/api/studio/ads/create/briefs?limit=20").then(function (data) {
      S.briefs = data.briefs || [];
      renderBriefs();
      return data;
    });
  }

  function refreshCreate() {
    setError("adc-error", "");
    return Promise.all([loadContext(), loadBriefs()]).catch(function (err) {
      setError("adc-error", err.message || String(err));
    });
  }

  // -----------------------------------------------------------------------
  // Create rendering
  // -----------------------------------------------------------------------
  function renderProductOptions() {
    var select = $("adc-product");
    if (!select) return;
    var current = select.value;
    var products = (S.products || []).filter(function (product) {
      return product && product.status !== "archived";
    });
    select.innerHTML = '<option value="">Enter a product manually</option>' + products.map(function (product) {
      var status = STATUS_LABELS[product.status] || product.status || "";
      return '<option value="' + esc(product.id) + '">' + esc(product.name) +
        (product.merchant ? " · " + esc(product.merchant) : "") +
        (status ? " · " + esc(status) : "") + "</option>";
    }).join("");
    if (current && products.some(function (product) { return product.id === current; })) {
      select.value = current;
    }
  }

  function renderBriefs() {
    var host = $("adc-briefs");
    var count = $("adc-brief-count");
    if (count) count.textContent = String((S.briefs || []).length);
    if (!host) return;
    if (!S.briefs.length) {
      host.innerHTML = '<div class="adc-empty">No briefs yet. Compose one above; it is stored locally until you send it to Generate.</div>';
      return;
    }
    host.innerHTML = S.briefs.map(function (brief) {
      var handed = brief.status === "handed_off";
      return [
        '<div class="adc-brief">',
        '  <div class="adc-brief-main"><b>' + esc((brief.product && brief.product.name) || brief.instruction || "Untitled brief") + "</b>",
        "    <em>" + esc(brief.template_name || brief.template_id || "Template") + " · " + esc(dateLabel(brief.created_iso)) +
          (brief.duration_seconds ? " · " + esc(brief.duration_seconds) + "s" : "") + "</em>",
        handed ? '    <em>Handed off to project ' + esc(brief.handoff_project_id) + "</em>" : "",
        "  </div>",
        '  <div class="adc-brief-actions">',
        '    <button type="button" class="collart-btn-soft" data-adc-open="' + esc(brief.id) + '">Open</button>',
        handed ? "" : '    <button type="button" class="collart-btn-soft" data-adc-handoff="' + esc(brief.id) + '">Send to Generate</button>',
        handed ? "" : '    <button type="button" class="collart-btn-soft" data-adc-delete="' + esc(brief.id) + '">Delete</button>',
        "  </div>",
        "</div>"
      ].filter(Boolean).join("\n");
    }).join("\n");
    icons();
  }

  function renderPreview(brief) {
    S.selectedBrief = brief || null;
    var wrap = $("adc-preview");
    if (!wrap) return;
    if (!brief) {
      wrap.classList.add("hidden");
      return;
    }
    wrap.classList.remove("hidden");
    var badge = $("adc-preview-badge");
    if (badge) {
      badge.textContent = brief.status === "handed_off" ? "Handed off" : "Draft";
      badge.className = "adc-badge" + (brief.status === "handed_off" ? " is-ok" : "");
    }
    var product = brief.product || {};
    var meta = [
      brief.template_name || brief.template_id,
      product.source === "backlog" ? "Backlog product" : "Manual product",
      product.name,
      product.merchant,
      product.price ? priceLabel(product.price) : "",
      brief.video_format,
      (brief.target_platforms || []).join(", "),
      brief.duration_seconds ? brief.duration_seconds + "s" : ""
    ].filter(Boolean);
    var metaHost = $("adc-preview-meta");
    if (metaHost) {
      metaHost.innerHTML = meta.map(function (item) {
        return "<span>" + esc(item) + "</span>";
      }).join("");
    }
    var disclosures = brief.disclosures || [];
    var discHost = $("adc-preview-disclosures");
    if (discHost) {
      discHost.innerHTML = disclosures.map(function (item) {
        return "<span>" + esc(item) + "</span>";
      }).join("");
    }
    var prompt = $("adc-preview-prompt");
    if (prompt) prompt.textContent = brief.prompt || "";
    var affiliate = brief.affiliate_destination || {};
    var note = $("adc-preview-note");
    if (note) {
      note.textContent = affiliate.status === "validated"
        ? "Affiliate link validated by the owner. The prompt includes it and the disclosure is set."
        : affiliate.status === "unvalidated"
          ? "Affiliate link not validated. It was excluded from the prompt."
          : "No affiliate link attached.";
    }
    var handoff = $("adc-handoff");
    if (handoff) handoff.textContent = brief.status === "handed_off" ? "Open in Generate" : "Send to Generate";
    icons();
    wrap.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function productPayload() {
    var select = $("adc-product");
    if (select && select.value) return { product_id: select.value };
    var name = ($("adc-name") || {}).value || "";
    var manual = {
      name: name.trim(),
      description: (($("adc-description") || {}).value || "").trim(),
      merchant: (($("adc-merchant") || {}).value || "").trim(),
      category: (($("adc-category") || {}).value || "").trim(),
      target_audience: (($("adc-audience") || {}).value || "").trim(),
      product_url: (($("adc-url") || {}).value || "").trim()
    };
    var amount = ($("adc-price") || {}).value;
    if (amount !== "" && amount !== undefined && amount !== null) {
      manual.price = {
        amount: Number(amount),
        currency: (($("adc-currency") || {}).value || "PHP").trim() || "PHP"
      };
    }
    return { product: manual };
  }

  function composeBrief() {
    var button = $("adc-compose");
    setError("adc-error", "");
    var payload = productPayload();
    payload.instruction = (($("adc-instruction") || {}).value || "").trim();
    payload.duration_seconds = Number((($("adc-duration") || {}).value || 15));
    var platforms = (($("adc-platforms") || {}).value || "").trim();
    if (platforms) {
      payload.target_platforms = platforms.split(",").map(function (item) { return item.trim(); }).filter(Boolean);
    }
    var style = (($("adc-style") || {}).value || "").trim();
    if (style) payload.creative_style = style;
    payload.sponsored = !!($("adc-sponsored") && $("adc-sponsored").checked);
    var affUrl = (($("adc-aff-url") || {}).value || "").trim();
    if (affUrl) {
      payload.affiliate_destination = {
        url: affUrl,
        program: (($("adc-aff-program") || {}).value || "").trim(),
        validated: !!($("adc-aff-validated") && $("adc-aff-validated").checked)
      };
    }
    busy(button, true, "Composing…");
    return api("/api/studio/ads/create/briefs", { method: "POST", body: payload }).then(function (data) {
      busy(button, false);
      S.briefs.unshift(data.brief);
      renderBriefs();
      renderPreview(data.brief);
      toast("Brief composed.");
    }).catch(function (err) {
      busy(button, false);
      setError("adc-error", err.message || String(err));
    });
  }

  function handoffBrief(briefId, button) {
    if (!briefId) return;
    busy(button, true, "Sending…");
    return api("/api/studio/ads/create/briefs/" + encodeURIComponent(briefId) + "/handoff", { method: "POST" })
      .then(function (data) {
        busy(button, false);
        var brief = data.brief || null;
        if (brief) {
          S.briefs = S.briefs.map(function (item) { return item.id === brief.id ? brief : item; });
          renderBriefs();
          renderPreview(brief);
        }
        var prompt = (data.prefill && data.prefill.prompt) || (brief && brief.prompt) || "";
        toast(data.already_handed_off ? "Opening the existing draft project." : "Draft project created. Prompt loaded into Generate.");
        openInGenerate(prompt);
      }).catch(function (err) {
        busy(button, false);
        setError("adc-error", err.message || String(err));
      });
  }

  function openInGenerate(prompt) {
    if (window.switchMainView) window.switchMainView("studio");
    var apply = function () {
      if (window.StudioWorkspaces && window.StudioWorkspaces.go) {
        try { window.StudioWorkspaces.go("ads", "fashion"); } catch (err) { /* fall through */ }
      }
      if (window.Studio && window.Studio.prefillPrompt) {
        window.Studio.prefillPrompt(prompt);
        return;
      }
      var area = $("studio-prompt");
      if (area) {
        area.value = prompt;
        area.dispatchEvent(new Event("input", { bubbles: true }));
        area.focus();
      }
    };
    if (window.setTimeout) window.setTimeout(apply, 0);
    else apply();
  }

  function copyPrompt() {
    var prompt = S.selectedBrief && S.selectedBrief.prompt;
    if (!prompt) return;
    var done = function () { toast("Prompt copied."); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(prompt).then(done).catch(function () { fallbackCopy(prompt, done); });
      return;
    }
    fallbackCopy(prompt, done);
  }

  function fallbackCopy(text, done) {
    var area = document.createElement("textarea");
    area.value = text;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    try { document.execCommand("copy"); done(); } catch (err) { toast("Copy failed. Select the prompt manually."); }
    document.body.removeChild(area);
  }

  // -----------------------------------------------------------------------
  // Events
  // -----------------------------------------------------------------------
  function bind() {
    var compose = $("adc-compose");
    if (compose) compose.addEventListener("click", composeBrief);
    var handoff = $("adc-handoff");
    if (handoff) {
      handoff.addEventListener("click", function () {
        if (S.selectedBrief) handoffBrief(S.selectedBrief.id, handoff);
      });
    }
    var copy = $("adc-copy");
    if (copy) copy.addEventListener("click", copyPrompt);
    var discard = $("adc-discard");
    if (discard) {
      discard.addEventListener("click", function () {
        S.selectedBrief = null;
        var wrap = $("adc-preview");
        if (wrap) wrap.classList.add("hidden");
      });
    }

    var briefs = $("adc-briefs");
    if (briefs) {
      briefs.addEventListener("click", function (event) {
        var button = event.target.closest("button");
        if (!button) return;
        var open = button.getAttribute("data-adc-open");
        if (open) {
          var brief = S.briefs.filter(function (item) { return item.id === open; })[0];
          if (brief) renderPreview(brief);
          return;
        }
        var send = button.getAttribute("data-adc-handoff");
        if (send) return handoffBrief(send, button);
        var remove = button.getAttribute("data-adc-delete");
        if (remove) {
          if (window.confirm && !window.confirm("Delete this brief? It is not a project.")) return;
          api("/api/studio/ads/create/briefs/" + encodeURIComponent(remove), { method: "DELETE" })
            .then(function () {
              S.briefs = S.briefs.filter(function (item) { return item.id !== remove; });
              if (S.selectedBrief && S.selectedBrief.id === remove) renderPreview(null);
              renderBriefs();
              toast("Brief deleted.");
            }).catch(function (err) { setError("adc-error", err.message || String(err)); });
        }
      });
    }

  }

  function init() {
    if (S.initialized) return;
    S.initialized = true;
    bind();
  }

  window.StudioCreate = {
    init: init,
    // Called by Studio.setTab; each panel loads its own data lazily.
    showCreate: function () {
      init();
      return refreshCreate();
    },
    refresh: function () {
      init();
      return refreshCreate();
    }
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
