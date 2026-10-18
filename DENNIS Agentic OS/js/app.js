// Dennis Agentic OS - Core Application Logic & State

document.addEventListener('DOMContentLoaded', () => {
  // Initialize Lucide Icons
  if (window.lucide) {
    lucide.createIcons();
  }

  // 1. Digital Clock & Date
  initClock();

  // 2. Navigation Tabs (Top Header)
  initTopNav();

  // 3. Sidebar Navigation & Trading Den
  initSidebar();
  initTradingDen();

  // 4. Registries & Filters (Model, MCP Connectors, Router)
  initRegistries();

  // 5. Agent Registry & In-OS Installer / Discovery Workflow
  initAgentRegistryAndWorkflows();

  // 6. Quick Tools & Modals
  initQuickToolsAndModals();

  // The Agent Workspace (terminal + Code & Preview panel + project sessions)
  // initializes itself from js/workspace.js.
});

/* ----------------------------------------------------
   1. DIGITAL CLOCK & DATE
---------------------------------------------------- */
function initClock() {
  const clockEl = document.getElementById('digital-clock');
  const dateEl = document.getElementById('current-date');
  const greetingEl = document.getElementById('dynamic-greeting');

  function updateTime() {
    const now = new Date();
    
    // Format Time: HH:mm:ss
    const hours = String(now.getHours()).padStart(2, '0');
    const minutes = String(now.getMinutes()).padStart(2, '0');
    const seconds = String(now.getSeconds()).padStart(2, '0');
    if (clockEl) clockEl.textContent = `${hours}:${minutes}:${seconds}`;

    // Format Date: e.g. Tuesday, October 14, 2025
    const options = { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' };
    if (dateEl) dateEl.textContent = now.toLocaleDateString('en-US', options);

    // Dynamic Greeting based on time of day
    if (greetingEl) {
      const h = now.getHours();
      let greeting = 'Good evening,';
      if (h < 12) greeting = 'Good morning,';
      else if (h < 18) greeting = 'Good afternoon,';
      greetingEl.innerHTML = `${greeting}<br><span class="text-cyan-400 glow-cyan">Dennis.</span>`;
    }
  }

  updateTime();
  setInterval(updateTime, 1000);
}

/* ----------------------------------------------------
   2. TOP NAVIGATION TABS
---------------------------------------------------- */
function initTopNav() {
  const tabs = document.querySelectorAll('.header-nav-tab');
  tabs.forEach(tab => {
    tab.addEventListener('click', () => {
      tabs.forEach(t => {
        t.classList.remove('nav-tab-active', 'text-blue-400', 'bg-blue-900/30');
        t.classList.add('text-slate-400');
      });
      tab.classList.remove('text-slate-400');
      tab.classList.add('nav-tab-active', 'text-blue-400');
    });
  });
}

/* ----------------------------------------------------
   3. SIDEBAR NAVIGATION
---------------------------------------------------- */
function switchMainView(viewName) {
  const heroSection = document.getElementById('hero-banner-section');
  const workspaceSection = document.getElementById('agent-workspace');
  const registrySection = document.getElementById('registry-section');
  const tradingDenView = document.getElementById('trading-den-view');
  const workspaceNav = document.getElementById('sidebar-nav-workspace');
  const tradingNav = document.getElementById('sidebar-nav-trading');

  if (viewName === 'trading-den') {
    if (heroSection) heroSection.classList.add('hidden');
    if (workspaceSection) workspaceSection.classList.add('hidden');
    if (registrySection) registrySection.classList.add('hidden');
    if (tradingDenView) tradingDenView.classList.remove('hidden');

    if (tradingNav) {
      tradingNav.classList.remove('text-slate-400', 'hover:bg-slate-800/40', 'hover:text-slate-200');
      tradingNav.classList.add('bg-blue-600', 'text-white', 'shadow-lg', 'shadow-blue-500/20');
    }
    if (workspaceNav) {
      workspaceNav.classList.remove('bg-blue-600', 'text-white', 'shadow-lg', 'shadow-blue-500/20');
      workspaceNav.classList.add('text-slate-400', 'hover:bg-slate-800/40', 'hover:text-slate-200');
    }
  } else {
    // Default to workspace
    if (heroSection) heroSection.classList.remove('hidden');
    if (workspaceSection) workspaceSection.classList.remove('hidden');
    if (registrySection) registrySection.classList.remove('hidden');
    if (tradingDenView) tradingDenView.classList.add('hidden');

    if (workspaceNav) {
      workspaceNav.classList.remove('text-slate-400', 'hover:bg-slate-800/40', 'hover:text-slate-200');
      workspaceNav.classList.add('bg-blue-600', 'text-white', 'shadow-lg', 'shadow-blue-500/20');
    }
    if (tradingNav) {
      tradingNav.classList.remove('bg-blue-600', 'text-white', 'shadow-lg', 'shadow-blue-500/20');
      tradingNav.classList.add('text-slate-400', 'hover:bg-slate-800/40', 'hover:text-slate-200');
    }
  }
}
window.switchMainView = switchMainView;

function initSidebar() {
  const sidebarItems = document.querySelectorAll('.sidebar-nav-item');
  sidebarItems.forEach(item => {
    item.addEventListener('click', (e) => {
      e.preventDefault();
      sidebarItems.forEach(i => {
        i.classList.remove('bg-blue-600', 'text-white', 'shadow-lg', 'shadow-blue-500/20');
        i.classList.add('text-slate-400', 'hover:bg-slate-800/40', 'hover:text-slate-200');
      });
      item.classList.remove('text-slate-400', 'hover:bg-slate-800/40', 'hover:text-slate-200');
      item.classList.add('bg-blue-600', 'text-white', 'shadow-lg', 'shadow-blue-500/20');

      const label = item.textContent.toLowerCase();
      if (label.includes('trading den')) {
        switchMainView('trading-den');
      } else if (label.includes('workspace')) {
        switchMainView('workspace');
      }
    });
  });
}

function initTradingDen() {
  // Back to workspace button
  const backBtn = document.getElementById('trading-den-back-workspace');
  if (backBtn) {
    backBtn.addEventListener('click', () => {
      switchMainView('workspace');
    });
  }

  // Open in IDE button
  const ideBtn = document.getElementById('trading-den-open-ide');
  if (ideBtn) {
    ideBtn.addEventListener('click', () => {
      switchMainView('workspace');
      if (window.dispatchEvent) {
        window.dispatchEvent(new CustomEvent('open-project-file', {
          detail: { path: 'trading_den/MMcloneLoopEngineering/app.py' }
        }));
      }
    });
  }

  // Trading Den Sub-tabs
  const tabs = [
    { btnId: 'td-tab-dashboard', contentId: 'td-content-dashboard' },
    { btnId: 'td-tab-loop', contentId: 'td-content-loop' },
    { btnId: 'td-tab-repo', contentId: 'td-content-repo' }
  ];

  tabs.forEach(tab => {
    const btn = document.getElementById(tab.btnId);
    if (!btn) return;
    btn.addEventListener('click', () => {
      tabs.forEach(t => {
        const b = document.getElementById(t.btnId);
        const c = document.getElementById(t.contentId);
        if (b) {
          b.classList.remove('bg-emerald-600/30', 'border-emerald-500/50', 'text-emerald-300');
          b.classList.add('text-slate-400', 'border-transparent');
        }
        if (c) c.classList.add('hidden');
      });

      btn.classList.remove('text-slate-400', 'border-transparent');
      btn.classList.add('bg-emerald-600/30', 'border-emerald-500/50', 'text-emerald-300');
      const targetContent = document.getElementById(tab.contentId);
      if (targetContent) targetContent.classList.remove('hidden');
    });
  });

  // DenMa backend + auto-loading embedded dashboard (its own origin => REST + WS work).
  const dot = document.getElementById('td-backend-dot');
  const stateEl = document.getElementById('td-backend-state');
  const urlEl = document.getElementById('td-backend-url');
  const errEl = document.getElementById('td-backend-error');
  const spinner = document.getElementById('td-backend-spinner');
  const overlay = document.getElementById('td-backend-overlay');
  const iframe = document.getElementById('td-iframe');
  const openBtn = document.getElementById('td-open-dashboard');
  const openTop = document.getElementById('td-open-dashboard-top');
  const restartBtn = document.getElementById('td-restart-backend');
  const dashTab = document.getElementById('td-tab-dashboard');
  // Served by the OS and proxied same-origin to the DenMa backend.
  const DENMA_PAGE = '/trading_den/MMcloneLoopEngineering/templates/index.html';
  let loadedUrl = null;

  function showOverlay(state, message, error) {
    if (overlay) overlay.classList.remove('hidden');
    if (dot) {
      dot.className = 'w-2.5 h-2.5 rounded-full ' +
        (state === 'starting' ? 'bg-amber-400' : 'bg-slate-500');
    }
    if (stateEl) {
      stateEl.textContent = message;
      stateEl.className = 'text-sm font-semibold ' +
        (state === 'starting' ? 'text-amber-300' : 'text-slate-300');
    }
    if (spinner) spinner.classList.toggle('hidden', state !== 'starting');
    if (errEl) {
      if (error) { errEl.textContent = error; errEl.classList.remove('hidden'); }
      else { errEl.textContent = ''; errEl.classList.add('hidden'); }
    }
  }

  function hideOverlay() {
    if (overlay) overlay.classList.add('hidden');
    if (spinner) spinner.classList.add('hidden');
    if (dot) dot.className = 'w-2.5 h-2.5 rounded-full bg-emerald-400';
    if (stateEl) stateEl.className = 'text-sm font-semibold text-emerald-300';
  }

  function applyBackendStatus(s) {
    if (!s) return;
    if (urlEl) urlEl.textContent = location.origin + DENMA_PAGE;
    if (openBtn) openBtn.href = DENMA_PAGE;
    if (openTop) openTop.href = DENMA_PAGE;

    if (s.running) {
      if (loadedUrl !== DENMA_PAGE && iframe) {
        iframe.src = DENMA_PAGE;
        loadedUrl = DENMA_PAGE;
      }
      hideOverlay();
      return;
    }
    if (iframe) { iframe.removeAttribute('src'); }
    loadedUrl = null;
    if (s.state === 'starting') {
      showOverlay('starting', 'Starting DenMa backend…', '');
    } else if (s.state === 'unavailable') {
      showOverlay('stopped', 'DenMa backend unavailable', s.error || '');
    } else {
      showOverlay('stopped', 'DenMa backend stopped', s.error || '');
    }
  }

  async function refreshBackendStatus() {
    try {
      const res = await fetch('/api/trading-den');
      applyBackendStatus(await res.json());
    } catch (e) {
      showOverlay('stopped', 'Backend status unavailable', '');
    }
  }

  if (restartBtn) {
    restartBtn.addEventListener('click', async () => {
      restartBtn.disabled = true;
      if (iframe) iframe.removeAttribute('src');
      loadedUrl = null;
      showOverlay('starting', 'Restarting DenMa backend…', '');
      try {
        const res = await fetch('/api/trading-den/restart', { method: 'POST' });
        applyBackendStatus(await res.json());
      } catch (e) { /* poll below will recover */ }
      restartBtn.disabled = false;
      refreshBackendStatus();
    });
  }
  if (dashTab) dashTab.addEventListener('click', refreshBackendStatus);

  refreshBackendStatus();
  setInterval(refreshBackendStatus, 5000);
}

/* ----------------------------------------------------
   4. DENEVO CHAT PANEL INTERACTIONS
---------------------------------------------------- */
let activeAgentState = {
  id: 'antigravity',
  name: 'Antigravity',
  version: 'v1.3.5'
};

let activeModelState = {
  id: 'gemini-2.5-flash',
  name: 'Gemini 2.5 Flash',
  provider: 'Google'
};

let allModelsCache = [];

function updateChatWelcomeState() {
  const welcomeEl = document.getElementById('chat-welcome-state');
  const messagesContainer = document.getElementById('chat-messages-container');
  const msgCount = messagesContainer ? messagesContainer.children.length : 0;
  if (welcomeEl) {
    if (msgCount === 0) {
      welcomeEl.classList.remove('hidden');
    } else {
      welcomeEl.classList.add('hidden');
    }
  }
}

function updateChatLabels() {
  const agentLabel = document.getElementById('active-agent-label');
  const modelLabel = document.getElementById('active-model-label');
  const welcomeAgent = document.getElementById('welcome-active-agent');
  const welcomeModel = document.getElementById('welcome-active-model');

  if (agentLabel) agentLabel.textContent = `Agent: ${activeAgentState.name}`;
  if (modelLabel) modelLabel.textContent = `Model: ${activeModelState.name}`;
  if (welcomeAgent) welcomeAgent.textContent = activeAgentState.name;
  if (welcomeModel) welcomeModel.textContent = activeModelState.name;
}

async function loadModelsIntoSwitcher(agentId) {
  const optionsContainer = document.getElementById('model-switcher-options');
  if (!optionsContainer) return;

  try {
    if (allModelsCache.length === 0) {
      const res = await fetch('/api/models');
      const data = await res.json();
      allModelsCache = data.models || data || [];
    }

    const colorMap = {
      blue: 'bg-blue-500', amber: 'bg-amber-500', emerald: 'bg-emerald-500',
      cyan: 'bg-cyan-500', purple: 'bg-purple-500', indigo: 'bg-indigo-500', red: 'bg-red-500'
    };

    // Normalize agent IDs so historical aliases (e.g. codex vs codex-cli,
    // custom-* vs custom-agent) still resolve to their recommended models.
    function modelAliases(id) {
      const aliases = [id];
      if (id.endsWith('-cli')) aliases.push(id.slice(0, -4));
      if (id === 'codex') aliases.push('codex-cli');
      if (id === 'custom-agent' || id.startsWith('custom-')) aliases.push('custom-agent');
      return [...new Set(aliases)];
    }
    const aliases = modelAliases(agentId);

    // Compatible models are those recommended for this agent (or an alias).
    let compatible = allModelsCache.filter(m =>
      Array.isArray(m.recommended_for) &&
      m.recommended_for.some(r => aliases.includes(r))
    );

    // Never leave the dropdown empty: fall back to the full catalog when an
    // agent has no explicitly recommended models (e.g. newly added agents).
    if (compatible.length === 0) {
      compatible = allModelsCache.slice();
    }

    const sorted = compatible;
    const recommended = compatible;
    const others = [];

    // Ensure the active model is compatible with the selected agent.
    if (sorted.length > 0 && !sorted.some(m => m.id === activeModelState.id)) {
      const firstCompatible = sorted[0];
      activeModelState = { id: firstCompatible.id, name: firstCompatible.name, provider: firstCompatible.provider };
      localStorage.setItem('dennisActiveModelId', firstCompatible.id);
      updateChatLabels();
    }



    let html = '';
    if (recommended.length > 0) {
      html += `<div class="text-[9px] text-purple-400/70 font-mono uppercase tracking-widest px-2 pt-1 pb-0.5">Recommended</div>`;
    }

    sorted.forEach((model, idx) => {
      if (idx === recommended.length && others.length > 0) {
        html += `<div class="text-[9px] text-slate-500 font-mono uppercase tracking-widest px-2 pt-1 pb-0.5">All Models</div>`;
      }
      const isActive = model.id === activeModelState.id;
      const dotColor = colorMap[model.color] || 'bg-slate-500';
      html += `
        <button class="model-switch-btn w-full px-2.5 py-1.5 rounded-lg text-left text-xs flex items-center justify-between hover:bg-slate-800 transition ${isActive ? 'bg-purple-900/40 text-purple-200 font-semibold' : 'text-slate-300'}" data-model-id="${escapeHtml(model.id)}">
          <div class="flex items-center gap-2">
            <span class="w-2 h-2 rounded-full ${dotColor} ${isActive ? 'shadow-sm shadow-purple-400' : ''}"></span>
            <span>${escapeHtml(model.name)}</span>
          </div>
          <div class="flex items-center gap-1.5">
            ${model.tier === 'free' ? '<span class="text-[8px] text-emerald-400 font-mono border border-emerald-600/40 px-1 rounded">FREE</span>' : ''}
            <span class="text-[9px] font-mono text-slate-500">${escapeHtml(model.context_window || '')}</span>
          </div>
        </button>
      `;
    });

    optionsContainer.innerHTML = html;

    // Attach click handlers
    optionsContainer.querySelectorAll('.model-switch-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const modelId = btn.getAttribute('data-model-id');
        const model = allModelsCache.find(m => m.id === modelId);
        if (model) {
          activeModelState = { id: model.id, name: model.name, provider: model.provider };
          localStorage.setItem('dennisActiveModelId', model.id);
          updateChatLabels();
          showToast(`Model switched to: ${model.name}`);
        }
        const menu = document.getElementById('model-switcher-menu');
        if (menu) menu.classList.add('hidden');
        // Re-render to update active highlight
        loadModelsIntoSwitcher(activeAgentState.id);
        // Re-launch the embedded TUI with the newly selected model.
        if (window.refreshAgentTerminal) window.refreshAgentTerminal();
      });
    });

  } catch (err) {
    console.error('Failed to load models:', err);
  }
}

async function loadChatHistory() {
  const chatMessages = document.getElementById('chat-messages-container');
  if (!chatMessages) return;

  try {
    const res = await fetch('/api/chat/history');
    const data = await res.json();
    // Server returns { history: [...] }, messages have .role and .text fields
    const history = data.history || data.messages || data || [];

    chatMessages.innerHTML = '';

    history.forEach(msg => {
      if (msg.role === 'user') {
        const el = document.createElement('div');
        el.className = 'flex justify-end animate-fadeIn';
        el.innerHTML = `
          <div class="bg-blue-600 text-white text-xs px-3.5 py-2 rounded-2xl max-w-[85%] shadow-md leading-relaxed">
            ${escapeHtml(msg.text || msg.content || msg.message || '')}
          </div>
        `;
        chatMessages.appendChild(el);
      } else if (msg.role === 'assistant') {
        const el = document.createElement('div');
        el.className = 'flex gap-3 text-xs leading-relaxed animate-fadeIn';
        el.innerHTML = `
          <div class="w-8 h-8 rounded-full bg-cyan-500/20 border border-cyan-400/40 flex items-center justify-center shrink-0 text-cyan-400">
            <i data-lucide="cpu" class="w-4 h-4"></i>
          </div>
          <div class="flex-1 space-y-2">
            <div class="flex items-center gap-2 flex-wrap">
              <span class="font-bold text-white text-sm">${escapeHtml(msg.agent || 'DENEVO')}</span>
              <span class="bg-slate-800/90 text-cyan-300 text-[10px] px-2 py-0.5 rounded-full border border-cyan-500/40 flex items-center gap-1 font-medium">
                <i data-lucide="sparkles" class="w-3 h-3 text-cyan-400"></i> ${escapeHtml(msg.model || activeModelState.name)}
              </span>
            </div>
            <p class="text-slate-300 leading-relaxed">${escapeHtml(msg.text || msg.content || msg.reply || '')}</p>
          </div>
        `;
        chatMessages.appendChild(el);
      }
    });

    if (window.lucide) lucide.createIcons();
    chatMessages.scrollTop = chatMessages.scrollHeight;
    updateChatWelcomeState();
  } catch (err) {
    console.error('Failed to load chat history:', err);
    updateChatWelcomeState();
  }
}

function initChat() {
  const chatInput = document.getElementById('chat-input');
  const sendBtn = document.getElementById('chat-send-btn');
  const chatMessages = document.getElementById('chat-messages-container');
  const taskAccordionBtn = document.getElementById('task-accordion-toggle');
  const taskAccordionBody = document.getElementById('task-accordion-content');
  const accordionChevron = document.getElementById('accordion-chevron');

  // Restore persisted selections
  const savedModelId = localStorage.getItem('dennisActiveModelId');
  const savedAgentId = localStorage.getItem('dennisActiveAgentId');

  // Agent Mode Dropdown Toggle
  const agentModeBtn = document.getElementById('agent-mode-dropdown-btn');
  const agentModeMenu = document.getElementById('agent-mode-menu');

  if (agentModeBtn && agentModeMenu) {
    agentModeBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      agentModeMenu.classList.toggle('hidden');
      const modelMenu = document.getElementById('model-switcher-menu');
      if (modelMenu) modelMenu.classList.add('hidden');
    });
  }

  // Model Switcher Dropdown Toggle
  const modelSwitcherBtn = document.getElementById('model-switcher-dropdown-btn');
  const modelSwitcherMenu = document.getElementById('model-switcher-menu');

  if (modelSwitcherBtn && modelSwitcherMenu) {
    modelSwitcherBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      modelSwitcherMenu.classList.toggle('hidden');
      if (agentModeMenu) agentModeMenu.classList.add('hidden');
      // Populate models when opened
      loadModelsIntoSwitcher(activeAgentState.id);
    });
  }

  // Close dropdowns on outside click
  document.addEventListener('click', () => {
    if (agentModeMenu) agentModeMenu.classList.add('hidden');
    if (modelSwitcherMenu) modelSwitcherMenu.classList.add('hidden');
  });

  // Accordion Expand/Collapse
  if (taskAccordionBtn && taskAccordionBody) {
    taskAccordionBtn.addEventListener('click', () => {
      const isHidden = taskAccordionBody.classList.contains('hidden');
      if (isHidden) {
        taskAccordionBody.classList.remove('hidden');
        if (accordionChevron) accordionChevron.classList.add('rotate-180');
      } else {
        taskAccordionBody.classList.add('hidden');
        if (accordionChevron) accordionChevron.classList.remove('rotate-180');
      }
    });
  }

  // Clear Chat Button
  const clearChatBtn = document.getElementById('btn-clear-chat');
  if (clearChatBtn) {
    clearChatBtn.addEventListener('click', async () => {
      if (!confirm('Clear all conversation history?')) return;
      try {
        await fetch('/api/chat/history', { method: 'DELETE' });
        if (chatMessages) chatMessages.innerHTML = '';
        updateChatWelcomeState();
        showToast('Conversation history cleared.');
      } catch (err) {
        showToast('Failed to clear history: ' + err.message);
      }
    });
  }

  // Quick prompt buttons (welcome state)
  document.querySelectorAll('.quick-prompt-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const prompt = btn.getAttribute('data-prompt') || btn.textContent.trim();
      if (chatInput) {
        chatInput.value = prompt;
        handleSendMessage();
      }
    });
  });

  // Send Chat Message
  async function handleSendMessage() {
    if (!chatInput) return;
    const text = chatInput.value.trim();
    if (!text) return;

    // Hide welcome state when first message sent
    const welcomeEl = document.getElementById('chat-welcome-state');
    if (welcomeEl) welcomeEl.classList.add('hidden');

    // Append User Message Bubble
    const userMsg = document.createElement('div');
    userMsg.className = 'flex justify-end animate-fadeIn group';
    userMsg.innerHTML = `
      <div class="relative bg-blue-600 text-white text-xs px-3.5 py-2 rounded-2xl max-w-[85%] shadow-md leading-relaxed">
        ${escapeHtml(text)}
        <div class="absolute -left-12 top-0 opacity-0 group-hover:opacity-100 transition-opacity flex gap-1">
          <button class="btn-copy-prompt p-1 rounded bg-slate-800 text-slate-300 hover:text-white transition" title="Copy Prompt">
            <i data-lucide="copy" class="w-3 h-3"></i>
          </button>
          <button class="btn-edit-prompt p-1 rounded bg-slate-800 text-slate-300 hover:text-white transition" title="Edit Prompt">
            <i data-lucide="pencil" class="w-3 h-3"></i>
          </button>
        </div>
      </div>
    `;
    chatMessages.appendChild(userMsg);

    // Add listeners for Copy/Edit
    const copyBtn = userMsg.querySelector('.btn-copy-prompt');
    if (copyBtn) {
      copyBtn.addEventListener('click', () => {
        navigator.clipboard.writeText(text);
        showToast('Prompt copied to clipboard!');
      });
    }
    const editBtn = userMsg.querySelector('.btn-edit-prompt');
    if (editBtn) {
      editBtn.addEventListener('click', () => {
        chatInput.value = text;
        chatInput.focus();
        showToast('Prompt loaded into input for editing');
      });
    }
    chatInput.value = '';
    chatMessages.scrollTop = chatMessages.scrollHeight;

    // Show typing indicator
    const typingEl = document.createElement('div');
    typingEl.id = 'typing-indicator';
    typingEl.className = 'flex gap-3 text-xs leading-relaxed animate-fadeIn';
    typingEl.innerHTML = `
      <div class="w-8 h-8 rounded-full bg-cyan-500/20 border border-cyan-400/40 flex items-center justify-center shrink-0 text-cyan-400">
        <i data-lucide="cpu" class="w-4 h-4"></i>
      </div>
      <div class="flex items-center gap-1.5 text-slate-400 text-[11px] py-2">
        <span class="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-bounce" style="animation-delay:0ms"></span>
        <span class="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-bounce" style="animation-delay:150ms"></span>
        <span class="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-bounce" style="animation-delay:300ms"></span>
        <span class="ml-1 text-cyan-400/60">${escapeHtml(activeAgentState.name)} is thinking...</span>
      </div>
    `;
    chatMessages.appendChild(typingEl);
    if (window.lucide) lucide.createIcons();
    chatMessages.scrollTop = chatMessages.scrollHeight;

    await simulateAgentResponse(text);
  }

  if (sendBtn) {
    sendBtn.addEventListener('click', handleSendMessage);
  }

  if (chatInput) {
    chatInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSendMessage();
      }
    });
  }

  async function simulateAgentResponse(query) {
    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: query,
          agent_id: activeAgentState.id,
          model_id: activeModelState.id
        })
      });
      const data = await res.json();

      // Remove typing indicator
      const typingEl = document.getElementById('typing-indicator');
      if (typingEl) typingEl.remove();

      const agentName = data.agent || data.assistant_message?.agent || activeAgentState.name;
      const modelName = data.model || data.assistant_message?.model || activeModelState.name;
      const replyText = data.reply || data.assistant_message?.text || data.assistant_message?.content || `Processing: "${query}"`;

      const agentMsg = document.createElement('div');
      agentMsg.className = 'flex gap-3 text-xs leading-relaxed animate-fadeIn';
      agentMsg.innerHTML = `
        <div class="w-8 h-8 rounded-full bg-cyan-500/20 border border-cyan-400/40 flex items-center justify-center shrink-0 text-cyan-400">
          <i data-lucide="cpu" class="w-4 h-4"></i>
        </div>
        <div class="flex-1 space-y-2">
          <div class="flex items-center gap-2 flex-wrap">
            <span class="font-bold text-white text-sm">${escapeHtml(agentName)}</span>
            <span class="bg-slate-800/90 text-cyan-300 text-[10px] px-2 py-0.5 rounded-full border border-cyan-500/40 flex items-center gap-1 font-medium">
              <i data-lucide="sparkles" class="w-3 h-3 text-cyan-400"></i> ${escapeHtml(modelName)}
            </span>
          </div>
          <p class="text-slate-300 leading-relaxed">${escapeHtml(replyText)}</p>
          <div class="bg-slate-900/80 border border-slate-800 rounded-lg p-2.5 text-[11px] text-slate-400 flex items-center justify-between">
            <div class="flex items-center gap-2 text-emerald-400">
              <i data-lucide="check-circle-2" class="w-3.5 h-3.5"></i>
              <span>Response generated via ${escapeHtml(agentName)} → ${escapeHtml(modelName)}</span>
            </div>
            <i data-lucide="chevron-down" class="w-3.5 h-3.5"></i>
          </div>
        </div>
      `;
      chatMessages.appendChild(agentMsg);
      if (window.lucide) lucide.createIcons();
      chatMessages.scrollTop = chatMessages.scrollHeight;
    } catch (err) {
      // Remove typing indicator on error too
      const typingEl = document.getElementById('typing-indicator');
      if (typingEl) typingEl.remove();
      console.error('Chat error:', err);
      showToast('Chat error: ' + err.message);
    }
  }

  // Load history & initial state
  loadChatHistory();

  // Restore persisted model from localStorage
  if (savedModelId && allModelsCache.length > 0) {
    const model = allModelsCache.find(m => m.id === savedModelId);
    if (model) {
      activeModelState = { id: model.id, name: model.name, provider: model.provider };
    }
  }
  updateChatLabels();
}

/* ----------------------------------------------------
   5. CODE EDITOR & FILE EXPLORER
---------------------------------------------------- */
const codeSamples = {
  'main.py': `from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from auth import router as auth_router
from routers import router as api_router

app = FastAPI(
    title="Full Stack App",
    description="AI Generated App",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/auth")
app.include_router(api_router, prefix="/api")

@app.get("/")
async def root():
    return {"message": "Hello from DENEVO"}`,

  'models.py': `from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime

class UserBase(BaseModel):
    email: EmailStr
    is_active: bool = True
    full_name: Optional[str] = None

class UserCreate(UserBase):
    password: str

class UserInDB(UserBase):
    id: int
    created_at: datetime = datetime.utcnow()

    class Config:
        orm_mode = True`,

  'routes.py': `from fastapi import APIRouter, Depends, HTTPException
from typing import List

router = APIRouter()

@router.get("/items")
async def read_items():
    return [
        {"id": 1, "name": "Neural Brain Agent", "status": "online"},
        {"id": 2, "name": "Vercel Deploy Runner", "status": "ready"}
    ]

@router.post("/items")
async def create_item(item_data: dict):
    return {"message": "Item registered successfully", "data": item_data}`,

  'auth.py': `from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

router = APIRouter()

class Token(BaseModel):
    access_token: str
    token_type: str

@router.post("/login", response_model=Token)
async def login_for_access_token():
    return {
        "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
        "token_type": "bearer"
    }`,

  'requirements.txt': `fastapi==0.111.0
uvicorn[standard]==0.30.1
pydantic==2.7.4
python-jose[cryptography]==3.3.0
passlib[bcrypt]==1.7.4
python-multipart==0.0.9`,

  'README.md': `# Full Stack AI App
Generated by **DENNIS Agentic OS** & **DENEVO Engine**.

### Architecture
- **Backend**: FastAPI + Uvicorn + JWT
- **Frontend**: Next.js App Router
- **Deployment**: Vercel & Render
- **Framework**: LangGraph Multi-Agent Workflows`
};

function initCodeEditor() {
  const codeContentEl = document.getElementById('code-content');
  const currentFileNameEl = document.getElementById('current-file-name');
  const fileTreeItems = document.querySelectorAll('.file-tree-item');

  function renderCode(fileName) {
    if (!codeContentEl) return;
    const rawCode = codeSamples[fileName] || `# File: ${fileName}\n# Content ready for editing...`;
    
    // Highlight lines and add line numbers
    const lines = rawCode.split('\n');
    let html = '';
    lines.forEach((line, index) => {
      const lineNum = index + 1;
      let highlighted = escapeHtml(line)
        .replace(/\b(from|import|as|return|def|async|class)\b/g, '<span class="syn-keyword">$1</span>')
        .replace(/(["'])(.*?)\1/g, '<span class="syn-string">$1$2$1</span>')
        .replace(/(@\w+)/g, '<span class="syn-def">$1</span>')
        .replace(/(#.*$)/g, '<span class="syn-comment">$1</span>');

      html += `<div class="flex hover:bg-slate-800/40 px-2 py-[1px]">
        <span class="w-6 text-right pr-3 select-none text-slate-600">${lineNum}</span>
        <span class="flex-1">${highlighted || ' '}</span>
      </div>`;
    });

    codeContentEl.innerHTML = html;
    if (currentFileNameEl) currentFileNameEl.textContent = fileName;
  }

  fileTreeItems.forEach(item => {
    item.addEventListener('click', (e) => {
      e.stopPropagation();
      fileTreeItems.forEach(f => f.classList.remove('bg-blue-900/30', 'text-cyan-400', 'font-medium'));
      item.classList.add('bg-blue-900/30', 'text-cyan-400', 'font-medium');
      const fileName = item.getAttribute('data-filename');
      if (fileName) renderCode(fileName);
    });
  });

  // Render initial code
  renderCode('main.py');
}

/* ----------------------------------------------------
   6. REGISTRIES & FILTERS (Model, MCP Connectors, Router)
---------------------------------------------------- */
function initRegistries() {
  // Model Registry Filter Tabs
  const modelFilters = document.querySelectorAll('.model-filter-btn');
  const modelItems = document.querySelectorAll('.model-item-row');

  modelFilters.forEach(btn => {
    btn.addEventListener('click', () => {
      modelFilters.forEach(b => {
        b.classList.remove('bg-blue-600', 'text-white');
        b.classList.add('text-slate-400', 'bg-slate-800/60');
      });
      btn.classList.remove('text-slate-400', 'bg-slate-800/60');
      btn.classList.add('bg-blue-600', 'text-white');

      const filter = btn.getAttribute('data-filter');
      modelItems.forEach(item => {
        const itemType = item.getAttribute('data-type');
        if (filter === 'all' || itemType === filter) {
          item.classList.remove('hidden');
        } else {
          item.classList.add('hidden');
        }
      });
    });
  });

  // Prefer Free Toggle
  const preferFreeToggle = document.getElementById('prefer-free-toggle');
  if (preferFreeToggle) {
    preferFreeToggle.addEventListener('change', (e) => {
      showToast(e.target.checked ? 'Prefer Free Models: ENABLED' : 'Prefer Free Models: DISABLED');
    });
  }
}

/* ----------------------------------------------------
   7. AGENT REGISTRY & IN-OS INSTALLATION WORKFLOW
---------------------------------------------------- */
let currentWizardStep = 1;
let currentConfigAgent = null;
let detectedAgentsList = [];
let catalogAgentsList = [];
let isDiagnosticVerified = false;

function initAgentRegistryAndWorkflows() {
  loadAgentRegistry();

  // Antigravity Modal Controls
  const antigravityModal = document.getElementById('antigravity-modal');
  const closeAntigravityBtn = document.getElementById('close-antigravity-modal');
  const runAntigravityDiagBtn = document.getElementById('btn-run-antigravity-diag');
  const antigravityDiagOutput = document.getElementById('antigravity-diag-output');

  if (closeAntigravityBtn && antigravityModal) {
    closeAntigravityBtn.addEventListener('click', () => {
      antigravityModal.classList.add('hidden');
    });
  }

  if (runAntigravityDiagBtn) {
    runAntigravityDiagBtn.addEventListener('click', async () => {
      antigravityDiagOutput.innerHTML = `<span class="text-cyan-400 animate-pulse">// Executing live pre-flight test on /Users/macbookpro/.local/bin/agy...</span>`;
      try {
        const res = await fetch('/api/agents/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ binary_path: '/Users/macbookpro/.local/bin/agy', args: ['--version'] })
        });
        const data = await res.json();
        if (data.success) {
          antigravityDiagOutput.innerHTML = `
            <div class="text-emerald-400 font-semibold">✓ Health Check Passed (Exit code: 0)</div>
            <div class="text-slate-300 font-mono">Output: ${escapeHtml(data.output)}</div>
            <div class="text-cyan-400/80 font-mono text-[10px]">Binary: ${escapeHtml(data.path)}</div>
          `;
        } else {
          antigravityDiagOutput.innerHTML = `
            <div class="text-rose-400 font-semibold">✗ Test Warning: ${escapeHtml(data.error || 'Failed')}</div>
          `;
        }
      } catch (err) {
        antigravityDiagOutput.innerHTML = `<div class="text-rose-400 font-mono">Error: ${escapeHtml(err.message)}</div>`;
      }
    });
  }

  // Add Agent Hub & Discovery Workflow
  const addAgentBtn = document.getElementById('btn-add-agent');
  const addAgentModal = document.getElementById('add-agent-modal');
  const closeAddAgentBtn = document.getElementById('close-add-agent-modal');
  const discoveryView = document.getElementById('discovery-screen-view');
  const wizardView = document.getElementById('setup-wizard-view');

  if (addAgentBtn && addAgentModal) {
    addAgentBtn.addEventListener('click', () => {
      openDiscoveryHub();
    });
  }

  if (closeAddAgentBtn && addAgentModal) {
    closeAddAgentBtn.addEventListener('click', () => {
      addAgentModal.classList.add('hidden');
    });
  }

  // Hub Tabs
  const tabDetected = document.getElementById('tab-detected-agents');
  const tabCatalog = document.getElementById('tab-catalog-agents');
  const tabCustom = document.getElementById('tab-custom-agent');
  const searchInput = document.getElementById('agent-search-input');

  let activeHubTab = 'detected';

  function updateHubTabs(selectedTab) {
    activeHubTab = selectedTab;
    [tabDetected, tabCatalog, tabCustom].forEach(btn => {
      if (!btn) return;
      btn.classList.remove('bg-blue-600', 'text-white');
      btn.classList.add('text-slate-400', 'hover:text-slate-200');
    });

    if (selectedTab === 'detected' && tabDetected) {
      tabDetected.classList.add('bg-blue-600', 'text-white');
      tabDetected.classList.remove('text-slate-400');
      renderDetectedCards();
    } else if (selectedTab === 'catalog' && tabCatalog) {
      tabCatalog.classList.add('bg-blue-600', 'text-white');
      tabCatalog.classList.remove('text-slate-400');
      renderCatalogCards();
    } else if (selectedTab === 'custom' && tabCustom) {
      tabCustom.classList.add('bg-blue-600', 'text-white');
      tabCustom.classList.remove('text-slate-400');
      startWizardWithAgent({
        id: 'custom-' + Date.now(),
        name: 'Custom Agent',
        provider: 'Custom / Local',
        binary_path: '',
        version: '1.0.0',
        type: 'Coding CLI',
        icon_char: '⚡',
        color_theme: 'cyan'
      });
    }
  }

  if (tabDetected) tabDetected.addEventListener('click', () => updateHubTabs('detected'));
  if (tabCatalog) tabCatalog.addEventListener('click', () => updateHubTabs('catalog'));
  if (tabCustom) tabCustom.addEventListener('click', () => updateHubTabs('custom'));

  if (searchInput) {
    searchInput.addEventListener('input', () => {
      const q = searchInput.value.toLowerCase().trim();
      if (activeHubTab === 'detected') {
        renderDetectedCards(q);
      } else {
        renderCatalogCards(q);
      }
    });
  }

  // Wizard Step Navigation
  const wizBtnBack = document.getElementById('wiz-btn-back');
  const wizBtnNext = document.getElementById('wiz-btn-next');
  const wizBtnCancel = document.getElementById('wiz-btn-cancel');
  const runWizardDiagBtn = document.getElementById('btn-run-wizard-diag');
  const wizardDiagOutput = document.getElementById('wizard-diag-output');
  const btnRunInstaller = document.getElementById('btn-run-installer');

  if (wizBtnCancel) {
    wizBtnCancel.addEventListener('click', () => {
      addAgentModal.classList.add('hidden');
    });
  }

  if (wizBtnBack) {
    wizBtnBack.addEventListener('click', () => {
      if (currentWizardStep > 1) {
        goToWizardStep(currentWizardStep - 1);
      } else {
        wizardView.classList.add('hidden');
        discoveryView.classList.remove('hidden');
      }
    });
  }

  if (wizBtnNext) {
    wizBtnNext.addEventListener('click', async () => {
      if (currentWizardStep < 4) {
        // Validation before advancing
        if (currentWizardStep === 1) {
          const name = document.getElementById('wiz-agent-name').value.trim();
          const path = document.getElementById('wiz-agent-path').value.trim();
          if (!name) {
            showToast('Please provide an Agent Name');
            return;
          }
          if (!path) {
            showToast('Please run installer or provide executable binary path');
            return;
          }
        }
        goToWizardStep(currentWizardStep + 1);
      } else {
        // Final Registration
        await finalizeAgentRegistration();
      }
    });
  }

  // Pre-flight Diagnostics Runner (Step 3)
  if (runWizardDiagBtn) {
    runWizardDiagBtn.addEventListener('click', async () => {
      const binaryPath = document.getElementById('wiz-agent-path').value.trim();
      wizardDiagOutput.innerHTML = `<span class="text-cyan-400 animate-pulse">// Testing binary execution: ${escapeHtml(binaryPath)} --version...</span>`;
      try {
        const res = await fetch('/api/agents/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ binary_path: binaryPath, args: ['--version'] })
        });
        const data = await res.json();
        if (data.success) {
          isDiagnosticVerified = true;
          wizardDiagOutput.innerHTML = `
            <div class="text-emerald-400 font-semibold flex items-center gap-1.5">
              <span>✓ Pre-Flight Verification Successful! (Exit code: 0)</span>
            </div>
            <div class="text-slate-200 font-mono mt-1">Output: ${escapeHtml(data.output)}</div>
            <div class="text-cyan-400/80 font-mono text-[10px] mt-0.5">Binary verified at: ${escapeHtml(data.path)}</div>
          `;
          showToast('Pre-flight test verified successfully!');
        } else {
          isDiagnosticVerified = false;
          wizardDiagOutput.innerHTML = `
            <div class="text-rose-400 font-semibold flex items-center gap-1.5">
              <span>✗ Verification Failed: Executable not responding</span>
            </div>
            <div class="text-slate-300 font-mono mt-1">${escapeHtml(data.error || 'Binary execution returned error')}</div>
          `;
          showToast('Diagnostic failed: Binary not found or not executable');
        }
      } catch (err) {
        isDiagnosticVerified = false;
        wizardDiagOutput.innerHTML = `<div class="text-rose-400 font-mono">Error: ${escapeHtml(err.message)}</div>`;
      }
    });
  }

  // Live In-OS Installer Runner (Step 1)
  if (btnRunInstaller) {
    btnRunInstaller.addEventListener('click', async () => {
      const installCmd = document.getElementById('wiz-install-cmd').value.trim();
      const terminalOut = document.getElementById('installer-terminal-output');
      const pathInput = document.getElementById('wiz-agent-path');
      const versionInput = document.getElementById('wiz-agent-version');
      const pathStatus = document.getElementById('wiz-path-status');

      if (!installCmd) {
        showToast('Please specify an installation command');
        return;
      }

      terminalOut.classList.remove('hidden');
      terminalOut.innerHTML = `
        <div class="text-cyan-400 animate-pulse">// Executing: ${escapeHtml(installCmd)}...</div>
        <div class="text-slate-400 text-[10px]">Downloading packages and resolving dependencies...</div>
      `;

      btnRunInstaller.disabled = true;
      btnRunInstaller.innerHTML = `<i data-lucide="loader" class="w-3.5 h-3.5 animate-spin"></i> Installing...`;
      if (window.lucide) lucide.createIcons();

      try {
        const res = await fetch('/api/agents/install', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            command: installCmd,
            binary_name: currentConfigAgent ? currentConfigAgent.binary : ''
          })
        });
        const data = await res.json();

        if (data.success) {
          terminalOut.innerHTML = `
            <div class="text-emerald-400 font-semibold">✓ Installation Completed Successfully!</div>
            <div class="text-slate-300 font-mono text-[10.5px] mt-1 whitespace-pre-wrap max-h-32 overflow-y-auto">${escapeHtml(data.output)}</div>
            <div class="text-cyan-400 font-mono text-[10.5px] mt-1">Binary resolved: ${escapeHtml(data.binary_path)}</div>
          `;
          if (pathInput) pathInput.value = data.binary_path;
          if (versionInput) versionInput.value = data.version;
          if (pathStatus) {
            pathStatus.textContent = 'Installed on system ✓';
            pathStatus.className = 'text-[10px] text-emerald-400 font-mono';
          }
          showToast('Agent successfully installed on host system!');
        } else {
          terminalOut.innerHTML = `
            <div class="text-rose-400 font-semibold">✗ Installation Error (${escapeHtml(data.error || 'Failed')}):</div>
            <div class="text-slate-300 font-mono text-[10.5px] mt-1 whitespace-pre-wrap">${escapeHtml(data.output || '')}</div>
          `;
          showToast('Installation failed. Check terminal logs.');
        }
      } catch (err) {
        terminalOut.innerHTML = `<div class="text-rose-400 font-mono">Error: ${escapeHtml(err.message)}</div>`;
      } finally {
        btnRunInstaller.disabled = false;
        btnRunInstaller.innerHTML = `<i data-lucide="play" class="w-3.5 h-3.5"></i> <span>Install Agent Now</span>`;
        if (window.lucide) lucide.createIcons();
      }
    });
  }
}

/* Load Registered Agents from Backend and Render List */
async function loadAgentRegistry() {
  const container = document.getElementById('agent-registry-list');
  const counterEl = document.getElementById('registered-agents-counter');
  const agentModeOptions = document.getElementById('agent-mode-options');
  const activeAgentLabel = document.getElementById('active-agent-label');

  if (!container) return;

  try {
    const res = await fetch('/api/agents');
    const data = await res.json();
    const agents = data.agents || [];
    const activeId = data.active_agent_id || 'antigravity';

    if (counterEl) counterEl.textContent = agents.length;

    let html = '';
    let dropdownHtml = '';

    agents.forEach(agent => {
      const isAntigravity = agent.id === 'antigravity';
      const isActive = agent.id === activeId;

      if (isActive) {
        activeAgentState = { id: agent.id, name: agent.name, version: agent.version };
        if (activeAgentLabel) activeAgentLabel.textContent = `Agent: ${agent.name}`;
      }

      let iconBg = 'bg-gradient-to-tr from-cyan-500 to-blue-600';
      if (agent.color_theme === 'amber') iconBg = 'bg-amber-600/30 text-amber-400 border border-amber-500/40';
      else if (agent.color_theme === 'blue') iconBg = 'bg-blue-600/30 text-blue-400 border border-blue-500/40';
      else if (agent.color_theme === 'emerald') iconBg = 'bg-emerald-600/30 text-emerald-400 border border-emerald-500/40';
      else if (agent.color_theme === 'purple') iconBg = 'bg-purple-600/30 text-purple-400 border border-purple-500/40';

      html += `
        <div class="agent-item-card flex items-center justify-between py-1.5 px-2 rounded-xl bg-slate-900/70 border ${isActive ? 'border-cyan-400/80 shadow-sm shadow-cyan-500/20' : (isAntigravity ? 'border-cyan-500/40' : 'border-slate-800')} hover:border-cyan-400/60 transition group cursor-pointer" data-agent-id="${escapeHtml(agent.id)}">
          <div class="flex items-center gap-2">
            <div class="w-6 h-6 rounded-lg ${iconBg} flex items-center justify-center font-bold text-[10px] shadow-sm">
              ${escapeHtml(agent.icon_char || agent.name.charAt(0))}
            </div>
            <div>
              <div class="font-semibold text-slate-100 text-[11px] flex items-center gap-1.5">
                <span>${escapeHtml(agent.name)}</span>
                ${isAntigravity ? '<span class="text-[8.5px] px-1.5 py-0.2 rounded bg-cyan-950 text-cyan-400 border border-cyan-500/40 font-mono font-normal">Core</span>' : ''}
                ${isActive ? '<span class="text-[8.5px] px-1.5 py-0.2 rounded bg-blue-900 text-blue-200 border border-blue-500/40 font-mono">Active</span>' : ''}
              </div>
              <div class="text-[9px] text-slate-400 font-mono truncate max-w-[130px]">${escapeHtml(agent.version || 'v1.0.0')} • ${escapeHtml(agent.provider || 'AI Engine')}</div>
            </div>
          </div>
          <div class="flex items-center gap-1">
            <!-- Quick Diagnostic Ping Button -->
            <button class="btn-ping-agent p-1 rounded-md hover:bg-slate-800 text-slate-400 hover:text-emerald-400 transition" title="Ping Test Diagnostic" data-agent-id="${escapeHtml(agent.id)}" data-binary="${escapeHtml(agent.binary_path || '')}">
              <i data-lucide="zap" class="w-3.5 h-3.5"></i>
            </button>

            <!-- Launch Agent in its own Terminal window -->
            <button class="btn-launch-agent p-1 rounded-md hover:bg-slate-800 text-slate-400 hover:text-cyan-400 transition" title="Launch in Terminal" data-agent-id="${escapeHtml(agent.id)}">
              <i data-lucide="terminal" class="w-3.5 h-3.5"></i>
            </button>

            ${isAntigravity ? `
              <button class="btn-inspect-antigravity p-1 rounded-md hover:bg-slate-800 text-slate-400 hover:text-cyan-300 transition" title="Inspect Antigravity Details">
                <i data-lucide="settings" class="w-3.5 h-3.5"></i>
              </button>
            ` : `
              <button class="btn-remove-agent p-1 rounded-md hover:bg-rose-950/60 text-slate-500 hover:text-rose-400 transition" title="Remove Agent" data-agent-id="${escapeHtml(agent.id)}">
                <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>
              </button>
            `}
          </div>
        </div>
      `;

      dropdownHtml += `
        <button class="agent-switch-btn w-full px-2.5 py-1.5 rounded-lg text-left text-xs flex items-center justify-between hover:bg-slate-800 transition ${isActive ? 'bg-blue-900/50 text-cyan-300 font-semibold' : 'text-slate-300'}" data-agent-id="${escapeHtml(agent.id)}">
          <div class="flex items-center gap-2">
            <span class="w-2 h-2 rounded-full ${isActive ? 'bg-cyan-400 shadow-sm shadow-cyan-400' : 'bg-slate-600'}"></span>
            <span>${escapeHtml(agent.name)}</span>
          </div>
          <span class="text-[9px] font-mono text-slate-500">${escapeHtml(agent.version || '')}</span>
        </button>
      `;
    });

    container.innerHTML = html;
    if (agentModeOptions) agentModeOptions.innerHTML = dropdownHtml;
    if (window.lucide) lucide.createIcons();

    // Attach Active Agent Switch listeners
    if (agentModeOptions) {
      agentModeOptions.querySelectorAll('.agent-switch-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
          const targetId = btn.getAttribute('data-agent-id');
          await setActiveAgent(targetId);
        });
      });
    }

    // Ping test handlers
    container.querySelectorAll('.btn-ping-agent').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const binaryPath = btn.getAttribute('data-binary');
        const agentId = btn.getAttribute('data-agent-id');
        btn.classList.add('text-cyan-400', 'animate-pulse');
        
        try {
          const res = await fetch('/api/agents/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ binary_path: binaryPath, args: ['--version'] })
          });
          const data = await res.json();
          btn.classList.remove('text-cyan-400', 'animate-pulse');
          if (data.success) {
            btn.classList.add('text-emerald-400');
            showToast(`✓ Ping success: ${agentId} responded with "${data.output}"`);
          } else {
            btn.classList.add('text-rose-400');
            showToast(`✗ Ping failed: ${data.error || 'Binary not responding'}`);
          }
        } catch (err) {
          btn.classList.remove('text-cyan-400', 'animate-pulse');
          btn.classList.add('text-rose-400');
          showToast(`Ping failed: ${err.message}`);
        }
      });
    });

    // Launch handlers (open agent in an independent Terminal window)
    container.querySelectorAll('.btn-launch-agent').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const agentId = btn.getAttribute('data-agent-id');
        btn.classList.add('text-cyan-400', 'animate-pulse');
        try {
          const res = await fetch('/api/agents/launch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ agent_id: agentId })
          });
          const data = await res.json();
          btn.classList.remove('text-cyan-400', 'animate-pulse');
          if (data.success) {
            btn.classList.add('text-emerald-400');
            showToast(`Launched ${agentId} in Terminal`);
          } else {
            btn.classList.add('text-rose-400');
            showToast(data.error || 'Failed to launch agent');
          }
        } catch (err) {
          btn.classList.remove('text-cyan-400', 'animate-pulse');
          btn.classList.add('text-rose-400');
          showToast('Launch failed: ' + err.message);
        }
      });
    });

    // Inspect Antigravity
    const inspectBtn = container.querySelector('.btn-inspect-antigravity');
    const antigravityCard = container.querySelector('[data-agent-id="antigravity"]');
    if (inspectBtn) {
      inspectBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        openAntigravityModal();
      });
    }
    if (antigravityCard) {
      antigravityCard.addEventListener('click', () => {
        openAntigravityModal();
      });
    }

    // Attach remove handlers
    const removeBtns = container.querySelectorAll('.btn-remove-agent');
    removeBtns.forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const agentId = btn.getAttribute('data-agent-id');
        if (confirm(`Remove agent '${agentId}' from the OS registry?`)) {
          await deleteAgent(agentId);
        }
      });
    });

    // Populate the model dropdown for the active agent on initial load.
    loadModelsIntoSwitcher(activeAgentState.id);

  } catch (err) {
    console.error('Failed to load agent registry:', err);
  }
}

async function setActiveAgent(agentId) {
  try {
    const res = await fetch('/api/agents/active', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ agent_id: agentId })
    });
    const data = await res.json();
    if (data.success) {
      // Update global state
      activeAgentState = {
        id: data.agent.id,
        name: data.agent.name,
        version: data.agent.version || 'v1.0.0'
      };
      localStorage.setItem('dennisActiveAgentId', data.agent.id);

      // Update recommended model for this agent if current model isn't compatible
      if (allModelsCache.length > 0) {
        const recommended = allModelsCache.find(m => m.recommended_for && m.recommended_for.includes(data.agent.id));
        const current = allModelsCache.find(m => m.id === activeModelState.id);
        const isCompatible = current && Array.isArray(current.recommended_for) && current.recommended_for.includes(data.agent.id);
        if (recommended && !isCompatible) {
          activeModelState = { id: recommended.id, name: recommended.name, provider: recommended.provider };
          localStorage.setItem('dennisActiveModelId', recommended.id);
        }
      }

      updateChatLabels();
      showToast(`Active OS Agent switched to: ${data.agent.name}`);
      loadAgentRegistry();

      // Refresh model switcher dropdown with recommendations for new agent
      allModelsCache = []; // force reload to re-sort
      loadModelsIntoSwitcher(data.agent.id);

      // Re-launch the embedded TUI with the newly selected agent.
      if (window.refreshAgentTerminal) window.refreshAgentTerminal();
    }
  } catch (err) {
    showToast('Failed to switch active agent');
  }
}

function openAntigravityModal() {
  const modal = document.getElementById('antigravity-modal');
  if (modal) modal.classList.remove('hidden');
}

async function deleteAgent(agentId) {
  try {
    const res = await fetch(`/api/agents?id=${encodeURIComponent(agentId)}`, { method: 'DELETE' });
    const data = await res.json();
    if (data.success) {
      showToast('Agent removed from registry');
      loadAgentRegistry();
    } else {
      showToast(data.error || 'Failed to remove agent');
    }
  } catch (err) {
    showToast('Error removing agent');
  }
}

/* Open Discovery Hub Modal */
async function openDiscoveryHub() {
  const modal = document.getElementById('add-agent-modal');
  const discoveryView = document.getElementById('discovery-screen-view');
  const wizardView = document.getElementById('setup-wizard-view');

  if (!modal) return;
  modal.classList.remove('hidden');
  discoveryView.classList.remove('hidden');
  wizardView.classList.add('hidden');

  try {
    const [detectRes, catalogRes] = await Promise.all([
      fetch('/api/agents/detect'),
      fetch('/api/agents/catalog')
    ]);
    const detectData = await detectRes.json();
    const catalogData = await catalogRes.json();

    detectedAgentsList = detectData.detected || [];
    catalogAgentsList = catalogData.catalog || [];

    const countBadge = document.getElementById('detected-agents-count');
    if (countBadge) countBadge.textContent = detectedAgentsList.length;

    renderDetectedCards();
  } catch (err) {
    console.error('Discovery fetch error:', err);
  }
}

function renderDetectedCards(query = '') {
  const container = document.getElementById('discovery-cards-container');
  if (!container) return;

  const filtered = detectedAgentsList.filter(a => {
    return !query || a.name.toLowerCase().includes(query) || a.provider.toLowerCase().includes(query);
  });

  if (filtered.length === 0) {
    container.innerHTML = `
      <div class="col-span-2 text-center py-8 text-slate-500 text-xs">
        No detected agents match "${escapeHtml(query)}". Try the Agent Catalog tab!
      </div>
    `;
    return;
  }

  let html = '';
  filtered.forEach(agent => {
    const isAdded = agent.is_already_added;
    html += `
      <div class="p-3.5 rounded-xl bg-slate-900/80 border border-slate-800 hover:border-cyan-500/40 flex flex-col justify-between transition space-y-3">
        <div class="flex items-start justify-between">
          <div class="flex items-center gap-2.5">
            <div class="w-9 h-9 rounded-xl bg-blue-600/20 border border-blue-500/40 text-cyan-400 flex items-center justify-center font-bold text-sm">
              ${escapeHtml(agent.icon_char || 'A')}
            </div>
            <div>
              <div class="flex items-center gap-1.5">
                <span class="font-bold text-white text-xs">${escapeHtml(agent.name)}</span>
                <span class="text-[9.5px] px-1.5 py-0.2 rounded bg-emerald-950 text-emerald-400 border border-emerald-500/30">Found on Host</span>
              </div>
              <div class="text-[10px] text-slate-400 font-mono">${escapeHtml(agent.provider)} • ${escapeHtml(agent.version)}</div>
            </div>
          </div>
        </div>

        <div class="p-2 rounded-lg bg-black/40 border border-slate-800/80 text-[10px] font-mono text-cyan-300 truncate">
          ${escapeHtml(agent.binary_path)}
        </div>

        <div class="flex items-center justify-between pt-1">
          <span class="text-[10px] text-slate-400">${escapeHtml(agent.category || 'Coding Agent')}</span>
          ${isAdded ? `
            <span class="text-xs font-medium text-emerald-400 px-3 py-1 rounded-lg bg-emerald-950/60 border border-emerald-500/30 flex items-center gap-1">
              <i data-lucide="check" class="w-3 h-3"></i> Added in OS
            </span>
          ` : `
            <button class="btn-setup-agent px-3 py-1.5 rounded-xl bg-blue-600 hover:bg-blue-500 text-white text-xs font-semibold shadow-md shadow-blue-600/30 transition flex items-center gap-1.5" data-agent-id="${escapeHtml(agent.id)}">
              <span>Set Up &amp; Add</span>
              <i data-lucide="arrow-right" class="w-3 h-3"></i>
            </button>
          `}
        </div>
      </div>
    `;
  });

  container.innerHTML = html;
  if (window.lucide) lucide.createIcons();

  container.querySelectorAll('.btn-setup-agent').forEach(btn => {
    btn.addEventListener('click', () => {
      const agentId = btn.getAttribute('data-agent-id');
      const agent = detectedAgentsList.find(a => a.id === agentId);
      if (agent) startWizardWithAgent(agent);
    });
  });
}

function renderCatalogCards(query = '') {
  const container = document.getElementById('discovery-cards-container');
  if (!container) return;

  const filtered = catalogAgentsList.filter(a => {
    return !query || a.name.toLowerCase().includes(query) || a.provider.toLowerCase().includes(query) || a.description.toLowerCase().includes(query);
  });

  if (filtered.length === 0) {
    container.innerHTML = `
      <div class="col-span-2 text-center py-8 text-slate-500 text-xs">
        No catalog agents match "${escapeHtml(query)}".
      </div>
    `;
    return;
  }

  let html = '';
  filtered.forEach(agent => {
    const isAdded = agent.is_already_added;
    const isInstalled = agent.is_installed;

    html += `
      <div class="p-3.5 rounded-xl bg-slate-900/80 border ${isInstalled ? 'border-blue-900/60' : 'border-slate-800'} hover:border-cyan-500/40 flex flex-col justify-between transition space-y-3">
        <div class="flex items-start justify-between">
          <div class="flex items-center gap-2.5">
            <div class="w-9 h-9 rounded-xl bg-purple-600/20 border border-purple-500/40 text-purple-300 flex items-center justify-center font-bold text-sm">
              ${escapeHtml(agent.icon_char || 'A')}
            </div>
            <div>
              <div class="flex items-center gap-1.5">
                <span class="font-bold text-white text-xs">${escapeHtml(agent.name)}</span>
                ${isInstalled ? `
                  <span class="text-[9px] px-1.5 py-0.2 rounded bg-emerald-950 text-emerald-400 border border-emerald-500/30">Installed</span>
                ` : `
                  <span class="text-[9px] px-1.5 py-0.2 rounded bg-amber-950/80 text-amber-400 border border-amber-500/30">Not on machine</span>
                `}
              </div>
              <div class="text-[10px] text-slate-400 font-mono">${escapeHtml(agent.provider)} • ${escapeHtml(agent.type)}</div>
            </div>
          </div>
          <span class="text-[9px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 font-mono">${escapeHtml(agent.package_manager || 'CLI')}</span>
        </div>

        <p class="text-[11px] text-slate-300 line-clamp-2 leading-relaxed">
          ${escapeHtml(agent.description)}
        </p>

        ${agent.install_command ? `
          <div class="px-2 py-1 rounded bg-black/50 border border-slate-800 text-[9.5px] font-mono text-cyan-400/90 truncate">
            $ ${escapeHtml(agent.install_command)}
          </div>
        ` : ''}

        <div class="flex items-center justify-between pt-1">
          <a href="${escapeHtml(agent.docs_url || '#')}" target="_blank" class="text-[10.5px] text-cyan-400 hover:underline flex items-center gap-1">
            <span>Docs</span>
            <i data-lucide="external-link" class="w-3 h-3"></i>
          </a>
          ${isAdded ? `
            <span class="text-xs font-semibold text-emerald-400 px-3 py-1 rounded-lg bg-emerald-950/60 border border-emerald-500/30 flex items-center gap-1">
              <i data-lucide="check" class="w-3 h-3"></i> Added to OS
            </span>
          ` : (isInstalled ? `
            <button class="btn-setup-catalog-agent px-3 py-1.5 rounded-xl bg-blue-600 hover:bg-blue-500 text-white text-xs font-semibold shadow-md shadow-blue-600/30 transition flex items-center gap-1.5" data-agent-id="${escapeHtml(agent.id)}">
              <span>Configure &amp; Add</span>
              <i data-lucide="arrow-right" class="w-3 h-3"></i>
            </button>
          ` : `
            <button class="btn-setup-catalog-agent px-3 py-1.5 rounded-xl bg-cyan-600 hover:bg-cyan-500 text-white text-xs font-semibold shadow-md shadow-cyan-600/30 transition flex items-center gap-1.5" data-agent-id="${escapeHtml(agent.id)}">
              <i data-lucide="download" class="w-3 h-3"></i>
              <span>Install &amp; Set Up</span>
            </button>
          `)}
        </div>
      </div>
    `;
  });

  container.innerHTML = html;
  if (window.lucide) lucide.createIcons();

  container.querySelectorAll('.btn-setup-catalog-agent').forEach(btn => {
    btn.addEventListener('click', () => {
      const agentId = btn.getAttribute('data-agent-id');
      const agent = catalogAgentsList.find(a => a.id === agentId);
      if (agent) startWizardWithAgent(agent);
    });
  });
}

/* Wizard Flow Transition */
function startWizardWithAgent(agent) {
  currentConfigAgent = agent;
  isDiagnosticVerified = false;

  const discoveryView = document.getElementById('discovery-screen-view');
  const wizardView = document.getElementById('setup-wizard-view');

  discoveryView.classList.add('hidden');
  wizardView.classList.remove('hidden');

  // Populate Step 1 fields
  const nameInput = document.getElementById('wiz-agent-name');
  const providerInput = document.getElementById('wiz-agent-provider');
  const pathInput = document.getElementById('wiz-agent-path');
  const versionInput = document.getElementById('wiz-agent-version');
  const pathStatus = document.getElementById('wiz-path-status');
  const installBox = document.getElementById('wiz-install-box');
  const installCmdInput = document.getElementById('wiz-install-cmd');
  const terminalOut = document.getElementById('installer-terminal-output');

  if (nameInput) nameInput.value = agent.name || '';
  if (providerInput) providerInput.value = agent.provider || '';
  if (pathInput) pathInput.value = agent.installed_path || agent.binary_path || '';
  if (versionInput) versionInput.value = agent.version || '1.0.0';

  // Toggle In-OS Installer box
  if (agent.install_command && !agent.is_installed && !agent.binary_path) {
    if (installBox) installBox.classList.remove('hidden');
    if (installCmdInput) installCmdInput.value = agent.install_command;
    if (terminalOut) terminalOut.classList.add('hidden');
    if (pathStatus) {
      pathStatus.textContent = 'Not yet installed on machine';
      pathStatus.className = 'text-[10px] text-amber-400 font-mono';
    }
  } else {
    if (installBox) installBox.classList.add('hidden');
    if (pathStatus) {
      pathStatus.textContent = (agent.installed_path || agent.binary_path) ? 'Found on system ✓' : 'Custom path';
      pathStatus.className = (agent.installed_path || agent.binary_path) ? 'text-[10px] text-emerald-400 font-mono' : 'text-[10px] text-slate-400 font-mono';
    }
  }

  goToWizardStep(1);
}

function goToWizardStep(stepNum) {
  currentWizardStep = stepNum;

  for (let i = 1; i <= 4; i++) {
    const stepEl = document.getElementById(`wizard-step-${i}`);
    const badgeEl = document.getElementById(`wizard-step-badge-${i}`);
    if (stepEl) {
      if (i === stepNum) stepEl.classList.add('active');
      else stepEl.classList.remove('active');
    }
    if (badgeEl) {
      if (i < stepNum) {
        badgeEl.className = 'step-indicator completed w-6 h-6 rounded-full flex items-center justify-center font-bold text-xs';
        badgeEl.innerHTML = '<i data-lucide="check" class="w-3.5 h-3.5"></i>';
      } else if (i === stepNum) {
        badgeEl.className = 'step-indicator active w-6 h-6 rounded-full flex items-center justify-center font-bold text-xs border border-blue-500';
        badgeEl.textContent = i;
      } else {
        badgeEl.className = 'step-indicator w-6 h-6 rounded-full bg-slate-900 text-slate-500 border border-slate-800 flex items-center justify-center font-bold text-xs';
        badgeEl.textContent = i;
      }
    }
  }

  // Update Next button label on Step 4
  const wizBtnNext = document.getElementById('wiz-btn-next');
  if (wizBtnNext) {
    if (stepNum === 4) {
      wizBtnNext.innerHTML = `<span>Register Agent into OS</span> <i data-lucide="check-circle" class="w-3.5 h-3.5"></i>`;
      wizBtnNext.className = 'px-4 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-semibold transition flex items-center gap-1.5 shadow-lg shadow-emerald-600/30';
      
      const confirmName = document.getElementById('wiz-confirm-name');
      const confirmPath = document.getElementById('wiz-confirm-path');
      const confirmStatus = document.getElementById('wiz-confirm-status');
      const nameInput = document.getElementById('wiz-agent-name');
      const pathInput = document.getElementById('wiz-agent-path');

      if (confirmName && nameInput) confirmName.textContent = nameInput.value;
      if (confirmPath && pathInput) confirmPath.textContent = pathInput.value || 'None specified';

      // Real status - no hardcoded misleading status!
      if (confirmStatus) {
        if (isDiagnosticVerified) {
          confirmStatus.innerHTML = `<span class="text-emerald-400 font-semibold">Verified &amp; Responsive ✓</span>`;
        } else {
          confirmStatus.innerHTML = `<span class="text-amber-400 font-semibold">⚠️ Unverified / Diagnostics Skipped</span>`;
        }
      }
    } else {
      wizBtnNext.innerHTML = `<span>Next Step</span> <i data-lucide="arrow-right" class="w-3.5 h-3.5"></i>`;
      wizBtnNext.className = 'px-4 py-2 rounded-xl bg-blue-600 hover:bg-blue-500 text-white font-semibold transition flex items-center gap-1.5 shadow-lg shadow-blue-600/30';
    }
  }

  // Update diagnostic command preview on Step 3
  if (stepNum === 3) {
    const pathInput = document.getElementById('wiz-agent-path');
    const cmdPreview = document.getElementById('diag-cmd-preview');
    if (pathInput && cmdPreview) {
      cmdPreview.textContent = `${pathInput.value || 'agent'} --version`;
    }
  }

  if (window.lucide) lucide.createIcons();
}

async function finalizeAgentRegistration() {
  const name = document.getElementById('wiz-agent-name').value.trim();
  const provider = document.getElementById('wiz-agent-provider').value.trim();
  const binary_path = document.getElementById('wiz-agent-path').value.trim();
  const version = document.getElementById('wiz-agent-version').value.trim();
  const type = document.getElementById('wiz-agent-type').value;
  const setActive = document.getElementById('wiz-set-active-checkbox')?.checked || false;

  const agentPayload = {
    id: name.toLowerCase().replace(/\s+/g, '-'),
    name: name,
    provider: provider || 'Autonomous AI',
    binary_path: binary_path,
    version: version || '1.0.0',
    type: type,
    status: 'Connected',
    color_theme: 'blue',
    icon_char: name.charAt(0).toUpperCase()
  };

  try {
    const res = await fetch('/api/agents', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ agent: agentPayload, set_active: setActive })
    });
    const data = await res.json();

    if (data.success) {
      showToast(`Agent '${name}' successfully registered into DENNIS Agentic OS!`);
      document.getElementById('add-agent-modal').classList.add('hidden');
      await loadAgentRegistry();
    } else {
      showToast(data.error || 'Registration failed');
    }
  } catch (err) {
    showToast('Failed to register agent: ' + err.message);
  }
}

/* ----------------------------------------------------
   8. QUICK TOOLS & MODALS
---------------------------------------------------- */
function initQuickToolsAndModals() {
  const quickTools = document.querySelectorAll('.quick-tool-item');
  quickTools.forEach(tool => {
    tool.addEventListener('click', () => {
      const toolName = tool.getAttribute('data-tool-name') || 'Tool';
      if (toolName === 'Terminal') {
        if (window.openAgentTerminalPanel) window.openAgentTerminalPanel();
        return;
      }
      showToast(`Launching ${toolName}...`);
    });
  });

  const modalTriggers = [
    { id: 'btn-add-provider', title: 'Add Model Provider', desc: 'Connect OpenAI, Anthropic, Google Gemini, Ollama, etc.' },
    { id: 'btn-config-router', title: 'Configure Framework Router', desc: 'Adjust LangChain, LangGraph, and LlamaIndex routing rules' }
  ];

  modalTriggers.forEach(m => {
    const el = document.getElementById(m.id);
    if (el) {
      el.addEventListener('click', () => {
        openModal(m.title, m.desc);
      });
    }
  });

  const modalCloseBtn = document.getElementById('modal-close-btn');
  const modalBackdrop = document.getElementById('generic-modal');
  if (modalCloseBtn && modalBackdrop) {
    modalCloseBtn.addEventListener('click', () => {
      modalBackdrop.classList.add('hidden');
    });
  }

  const refreshPreviewBtn = document.getElementById('refresh-preview-btn');
  const previewIframeWrap = document.getElementById('preview-mock-screen');
  if (refreshPreviewBtn && previewIframeWrap) {
    refreshPreviewBtn.addEventListener('click', () => {
      previewIframeWrap.classList.add('opacity-40');
      showToast('Reloading live app preview...');
      setTimeout(() => {
        previewIframeWrap.classList.remove('opacity-40');
      }, 400);
    });
  }
}

function openModal(title, description) {
  const modal = document.getElementById('generic-modal');
  const titleEl = document.getElementById('modal-title');
  const descEl = document.getElementById('modal-desc');

  if (modal && titleEl && descEl) {
    titleEl.textContent = title;
    descEl.textContent = description;
    modal.classList.remove('hidden');
  }
}

function showToast(message) {
  const toast = document.createElement('div');
  toast.className = 'fixed bottom-12 right-6 bg-slate-900/95 border border-cyan-500/50 text-cyan-300 text-xs px-4 py-2.5 rounded-lg shadow-2xl flex items-center gap-2 z-50 animate-bounce duration-300 backdrop-blur-md';
  toast.innerHTML = `<i data-lucide="info" class="w-4 h-4 text-cyan-400"></i> <span>${escapeHtml(message)}</span>`;
  document.body.appendChild(toast);
  if (window.lucide) lucide.createIcons();

  setTimeout(() => {
    toast.remove();
  }, 2800);
}

function escapeHtml(text) {
  const map = {
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#039;'
  };
  return text.replace(/[&<>"']/g, m => map[m]);
}

/* ------------------------------------------------------------------
   IN-PANEL INTERACTIVE TERMINAL (xterm.js + real PTY over SSE)
   Lives inside the DENEVO Agent Chat panel and runs the active agent's
   real TUI (e.g. opencode, where `/model` works), just like Terminal.
------------------------------------------------------------------ */
let agentTerm = null;       // xterm.Terminal instance
let agentTermEs = null;     // EventSource for the PTY stream
let agentTermStarted = false;
let agentTermSignature = ""; // agentId|modelId of the running session

function currentAgentSignature() {
  return `${activeAgentState.id || ''}|${activeModelState.id || ''}`;
}

function ensureXterm(cb) {
  if (window.Terminal) return cb();
  const existing = document.getElementById('xterm-script');
  if (existing) {
    if (existing.dataset.loaded) return cb();
    existing.addEventListener('load', cb);
    return;
  }
  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href = 'https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.css';
  document.head.appendChild(link);

  const script = document.createElement('script');
  script.id = 'xterm-script';
  script.src = 'https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.js';
  script.onload = () => { script.dataset.loaded = '1'; cb(); };
  document.head.appendChild(script);
}

function agentTermSize(host) {
  const cols = Math.max(40, Math.floor((host.clientWidth || 640) / 7.3));
  const rows = Math.max(12, Math.floor((host.clientHeight || 330) / 15));
  return { cols, rows };
}

function attachTerminalStream(term) {
  if (agentTermEs) { try { agentTermEs.close(); } catch (e) {} }
  const es = new EventSource('/api/terminal/stream');
  es.onmessage = (ev) => {
    try { term.write(JSON.parse(ev.data)); } catch (e) { term.write(ev.data); }
  };
  es.addEventListener('exit', () => { term.write('\r\n\x1b[33m[process exited]\x1b[0m\r\n'); });
  agentTermEs = es;
}

async function startAgentTerminal() {
  const host = document.getElementById('chat-terminal-host');
  if (!host) return;

  ensureXterm(async () => {
    if (!agentTerm) {
      agentTerm = new Terminal({
        theme: { background: '#000000', foreground: '#e2e8f0' },
        cursorBlink: true,
        fontSize: 12,
        fontFamily: 'Menlo, Monaco, "Courier New", monospace',
        scrollback: 5000
      });
      agentTerm.open(host);
      agentTerm.onData((d) => {
        fetch('/api/terminal/input', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ data: d })
        });
      });
      agentTerm.onResize(({ cols, rows }) => {
        fetch('/api/terminal/resize', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ cols, rows })
        });
      });
    }

    const size = agentTermSize(host);
    try { agentTerm.resize(size.cols, size.rows); } catch (e) {}

    const res = await fetch('/api/terminal/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        rows: size.rows,
        cols: size.cols,
        agent_id: activeAgentState.id,
        model_id: activeModelState.id
      })
    });
    const data = await res.json();
    if (!data.success) { showToast('Terminal: ' + (data.error || 'failed to start')); return; }

    agentTermStarted = true;
    agentTermSignature = currentAgentSignature();
    try { agentTerm.reset(); } catch (e) {}
    attachTerminalStream(agentTerm);
    agentTerm.focus();
  });
}

function setChatPanelTab(tab) {
  const chatBtn = document.getElementById('chat-tab-chat');
  const termBtn = document.getElementById('chat-tab-terminal');
  const messages = document.getElementById('chat-messages-container');
  const inputBar = document.getElementById('chat-input-bar');
  const termView = document.getElementById('chat-terminal-view');
  if (!chatBtn || !termBtn || !termView) return;

  const activeCls = ['bg-blue-900/40', 'text-blue-400', 'border', 'border-blue-700/50', 'font-semibold'];
  const idleCls = ['text-slate-400', 'hover:text-slate-200', 'hover:bg-slate-800/40', 'font-medium'];
  const activate = (btn, on) => {
    activeCls.forEach(c => btn.classList.toggle(c, on));
    idleCls.forEach(c => btn.classList.toggle(c, !on));
  };

  if (tab === 'terminal') {
    activate(chatBtn, false);
    activate(termBtn, true);
    if (messages) messages.classList.add('hidden');
    if (inputBar) inputBar.classList.add('hidden');
    termView.classList.remove('hidden');
    if (!agentTermStarted || agentTermSignature !== currentAgentSignature()) startAgentTerminal();
    else if (agentTerm) agentTerm.focus();
  } else {
    activate(chatBtn, true);
    activate(termBtn, false);
    if (messages) messages.classList.remove('hidden');
    if (inputBar) inputBar.classList.remove('hidden');
    termView.classList.add('hidden');
  }
}

function initChatPanelTerminal() {
  const chatBtn = document.getElementById('chat-tab-chat');
  const termBtn = document.getElementById('chat-tab-terminal');
  if (chatBtn) chatBtn.addEventListener('click', () => setChatPanelTab('chat'));
  if (termBtn) termBtn.addEventListener('click', () => setChatPanelTab('terminal'));

  // Keep the PTY sized to the panel when the window resizes.
  let resizeTimer;
  window.addEventListener('resize', () => {
    const view = document.getElementById('chat-terminal-view');
    if (!agentTerm || !view || view.classList.contains('hidden')) return;
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      const host = document.getElementById('chat-terminal-host');
      if (host) { const s = agentTermSize(host); agentTerm.resize(s.cols, s.rows); }
    }, 200);
  });
}

// Exposed for the Quick Tools "Run Terminal" action in the management grid.
window.openAgentTerminalPanel = () => {
  setChatPanelTab('terminal');
  const panel = document.getElementById('chat-messages-container');
  if (panel && panel.scrollIntoView) panel.scrollIntoView({ behavior: 'smooth', block: 'center' });
};

// Relaunch the TUI with the currently selected agent/model (used when the
// user switches Agent Mode or Model while the terminal tab is visible).
window.refreshAgentTerminal = () => {
  const view = document.getElementById('chat-terminal-view');
  if (!view || view.classList.contains('hidden')) return;
  startAgentTerminal();
};

