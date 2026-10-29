(function () {
  "use strict";
  var providers = [
    ["chatgpt", "ChatGPT"], ["gemini", "Gemini"], ["claude", "Claude"],
    ["deepseek", "DeepSeek"], ["copilot", "Copilot"], ["perplexity", "Perplexity"],
    ["grok", "Grok"], ["qwen", "Qwen"], ["kimi", "Kimi"],
    ["mistral", "Mistral"], ["poe", "Poe"]
  ];
  var active = null;
  var host = document.getElementById("provider-tabs");

  providers.forEach(function (provider) {
    var button = document.createElement("button");
    button.className = "tab";
    button.textContent = provider[1];
    button.dataset.provider = provider[0];
    button.addEventListener("click", function () {
      window.dennisDesktop.selectWebModel(provider[0]);
    });
    host.appendChild(button);
  });

  document.querySelectorAll("[data-action]").forEach(function (button) {
    button.addEventListener("click", function () {
      window.dennisDesktop.webModelAction(button.dataset.action);
    });
  });

  function setActive(data) {
    active = data.id;
    document.querySelectorAll(".tab").forEach(function (button) {
      button.classList.toggle("active", button.dataset.provider === active);
    });
    var welcome = document.querySelector(".welcome");
    if (welcome) welcome.remove();
  }

  window.dennisDesktop.onWebModelSelected(setActive);
  window.dennisDesktop.onWebModelNavigated(function (data) {
    if (data.id === active) document.title = "DENNIS Web Model Dashboard — " + data.url;
  });
  window.dennisDesktop.onWebModelLoadError(function (data) {
    if (data.id === active) document.title = "DENNIS Web Model Dashboard — provider load failed";
  });
})();
