/* studioTooltips.js — collapse long feature-explanation paragraphs into a
 * compact "?" affordance with a hover/focus tooltip.
 *
 * Only elements explicitly tagged with data-info-tip are converted, so status
 * messages, empty states and actual data display stay visible. Conversion is
 * idempotent and re-runs on DOM mutations because both Studios render panels
 * dynamically.
 */
(function (global) {
  "use strict";
  var ATTR = "data-info-tip";
  var DONE = "data-tip-ready";

  function convert(el) {
    if (el.getAttribute(DONE)) return;
    var raw = el.getAttribute(ATTR);
    if (raw === "off") { el.setAttribute(DONE, "1"); return; }
    var html = el.innerHTML;
    var text = (el.textContent || "").replace(/\s+/g, " ").trim();
    if (!text) return;

    el.setAttribute(DONE, "1");
    el.classList.add("info-tip-source");
    el.innerHTML = "";

    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "info-tip";
    btn.setAttribute("aria-label", "More information: " + text.slice(0, 90));
    btn.innerHTML = '<span aria-hidden="true">?</span>';

    var bubble = document.createElement("span");
    bubble.className = "info-tip-bubble";
    bubble.setAttribute("role", "tooltip");
    // Preserve inline markup (<b>, .yt-mono, <i data-lucide>) from the original
    // copy; the tooltip is the new home for the full explanation.
    bubble.innerHTML = html;
    btn.appendChild(bubble);
    el.appendChild(btn);
  }

  function scan(root) {
    var scope = root || document;
    var nodes = scope.querySelectorAll ? scope.querySelectorAll("[" + ATTR + "]") : [];
    for (var i = 0; i < nodes.length; i++) convert(nodes[i]);
  }

  function init() {
    scan(document);
    var host = document.getElementById("studio-view") || document.body;
    if (!host || !global.MutationObserver) return;
    var mo = new MutationObserver(function (muts) {
      for (var i = 0; i < muts.length; i++) {
        var added = muts[i].addedNodes;
        if (added && added.length) { scan(host); return; }
      }
    });
    mo.observe(host, { childList: true, subtree: true });
    global.StudioTooltips = { scan: scan };
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})(window);
