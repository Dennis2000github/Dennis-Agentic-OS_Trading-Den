# DENNIS Agentic OS

> **Your AI Brain. Your Tools. Your Universe.**  
> An Agentic Operating System frontend based on the `dennis_agentic_os_v5.png` architecture mockup.

---

## 🚀 Overview

**DENNIS Agentic OS** provides a cyber-futuristic dashboard interface for orchestrating autonomous coding agents, managing LLM model providers, tracking central credentials, and visualizing memory graphs.

### Key Components:
1. **Top Header & Navigation**:
   - Brand logo with cyan-to-magenta geometric wing emblem.
   - Module switchers: `Hermes Chat`, `Bots`, `Apollo`, `Hermes Oracle`, `Hermes Muse`, `Hermes Astros`, `Studio`, `Sessions`, `Outreach`, `Mixture`, `Workspace`, `MCP`.
   - User profile dropdown (`Dennis Manosa`) and plan status badge.
2. **Left Navigation & Hermes Core**:
   - Quick sections: `Workspace`, `Trading Den`, `Vault`, `Settings`.
   - **HERMES Apollo** widget with real-time reactive spinning galaxy vortex, voice wake command ("Hey Apollo"), and Jarvis-like voice assistant.
3. **Hero Dashboard (Top Banner)**:
   - Dynamic real-time digital clock and localized date greeting.
   - Interactive **Neural Brain Cosmos Canvas** with constellation nodes, synapsing electrical pulses, and pinned 3D document badges (`project-plan.md`, `research.md`, etc.).
4. **Middle Studio Tier**:
   - **DENEVO Agent Chat**: Multi-agent conversational panel, step-by-step task breakdown accordion, integration badges (Claude Code, GitHub, Node.js), message input, microphone, and orchestrator selectors.
   - **Project IDE & Live Preview**: File tree navigator with dynamic syntax-highlighted code editor and an in-app live browser preview screen.
5. **Bottom Management Grid**:
   - **Agent Registry**:
     - Dynamic discovery of host system agents (`Antigravity`, `Claude Code`, `OpenCode`, `Codex CLI`).
     - **In-OS Installer Engine**: Run installation commands directly within the OS terminal for uninstalled catalog agents (e.g. `pip install aider-chat`, `npm install -g @google/gemini-cli`).
     - **Pre-Flight Diagnostics**: Real-time executable testing (`--version`) with exit code checks to prevent false/misleading statuses.
     - **Active Agent Switching**: Dynamic binding with DENEVO Chat (`Agent Mode` switcher) allowing seamless handoffs between orchestrators.
   - **Model Registry**: Filterable by `All` / `Free` / `Paid` with a "Prefer Free" toggle.
   - **MCP Connectors**: discover servers from the official MCP Registry (online search) or add a custom connector, gated by an OS policy review and an OAuth 2.1 authorization workflow (RFC 9728/8414/7591 + PKCE).
   - **Framework Router**: LangChain, LangGraph, LlamaIndex, CrewAI.
   - **Quick Tools**: Shortcuts to Obsidian, Graphify, Repository Search, Terminal, and New Session.
6. **Footer Bar**:
   - System metadata architecture summary and memory layer links.

---

## 📁 File Structure

```
DENNIS Agentic OS/
├── index.html               # Main dashboard UI
├── css/
│   └── style.css            # Cyber-glassmorphism styles, glows, scrollbars & syntax theme
├── js/
│   ├── app.js               # Application state, clock, file tree switcher, chat & modals
│   ├── mcp.js               # MCP Connector Hub: registry discovery, custom servers, policy + OAuth workflow
│   ├── cosmos.js            # HTML5 Canvas neural brain constellation particle simulation
│   └── galaxy.js            # HTML5 Canvas Hermes galaxy vortex animation
├── server.py                # Local development server with mock API routes
├── backend/
│   └── mcp.py               # MCP discovery, policy gate and OAuth 2.1 authorization flow
└── dennis_agentic_os_v5.png # Reference architecture mockup
```

---

## ⚡ How to Run

You can open `index.html` directly in any web browser, or launch the local development server:

```bash
python3 server.py 8080
```

Open [http://localhost:8080](http://localhost:8080) in your browser.
