---
name: one-brain-agent-os
description: Use this skill when you want AI agents to share memory, work across devices, run continuously, accept voice commands, or improve your visibility in AI-powered search. Apply it when designing or upgrading a multi-agent system around one dashboard, one shared knowledge layer, and reusable task-specific agents.
---

# One-Brain Agent OS

Centralize agents, memory, tools, remote access, voice control, and AI-search workflows into one persistent operating system.

## When to use

Use this skill when:

- Agents forget user preferences, projects, or business context.
- Local notes are inaccessible to agents running on another machine.
- Work stops when a laptop sleeps or closes.
- You need to control agents from a phone or another device.
- You want voice-driven agent operations.
- Multiple agents repeat setup or context unnecessarily.
- You need content optimized for ChatGPT, Gemini, Google AI Overviews, or similar systems.

## Core methods

1. **Create one shared brain**

   Use a persistent, centralized memory layer that every agent can read and update. Store durable facts, preferences, projects, client context, workflows, and decisions there.

2. **Synchronize structured notes**

   Connect an Obsidian vault or equivalent Markdown knowledge base through MCP. Share only the folders agents need, such as:

   - `clients/`
   - `projects/`
   - `brand/`
   - `workflows/`

   Keep private journals, credentials, and unrelated personal data excluded.

3. **Separate device access from identity**

   Give each connected device its own token or key. Revoke a single device without invalidating the entire system when hardware is lost or retired.

4. **Run agents on an always-on host**

   Put the Agent OS on a computer or server that can remain available while personal devices sleep. Make the shared memory accessible to the host rather than leaving it locked on a laptop.

5. **Add a secure private network**

   Use a mesh VPN such as Tailscale/WireGuard so phones, laptops, desktops, and the host can reach the same dashboard without exposing services directly to the public internet.

6. **Add an ambient voice layer**

   Place a voice agent over the dashboard so it can:

   - Receive spoken requests.
   - Open applications or operate the computer.
   - Start workflows.
   - Answer questions using shared memory.
   - Remain available while switching between dashboard sections.

   Use an existing subscription-backed CLI integration where appropriate; use a dedicated voice provider only when custom voices or higher control are needed.

7. **Use task-specific pop-up agents**

   Create reusable agents for distinct jobs, such as:

   - Content analysis.
   - News and automation monitoring.
   - SEO research.
   - Image and video production.
   - Workflow building.
   - Reporting and quality control.

   Each agent should inherit shared context rather than starting from a blank chat.

8. **Optimize for AI search visibility**

   Build a Search OS that:

   - Imports Google Search Console data.
   - Identifies queries where the site is visible but lacks a strong answer.
   - Researches related query fan-outs.
   - Generates pages against a fixed quality checklist.
   - Publishes or distributes content across relevant sites.
   - Tracks mentions and visibility across AI answer systems.

9. **Use brand mentions as a distribution objective**

   Treat third-party references, consistent entity descriptions, useful citations, and authoritative mentions as first-class SEO outputs. Backlinks remain useful, but do not optimize only for links when the target is AI-generated answers.

## Workflow (default path)

1. **Inventory the system**

   List current agents, models, devices, note repositories, dashboards, credentials, and recurring workflows.

2. **Define the canonical memory**

   Decide which information belongs in shared memory and establish a clear folder or namespace structure.

3. **Connect the knowledge base**

   Configure MCP access to the selected Obsidian folders. Test reading and writing from both the local agent and the remote host.

4. **Deploy the always-on host**

   Install the dashboard and agents on a machine that can remain available independently of the user's primary laptop.

5. **Secure remote access**

   Add each approved device to the private mesh network. Verify dashboard access from a phone or secondary laptop, then record and protect device-specific credentials.

6. **Install voice control**

   Connect speech input and output to the shared agent layer. Test simple commands before allowing computer control or external actions.

7. **Create reusable agents**

   Define each pop-up agent with a narrow role, required inputs, expected outputs, available tools, and the shared memory it may access.

8. **Build the search workflow**

   Import Search Console data, select priority queries, identify content gaps and query fan-outs, generate drafts, and evaluate them against a 100-point quality checklist.

9. **Review before publishing**

   Check factual accuracy, brand voice, search intent, source quality, internal links, entity consistency, and whether the content genuinely answers the target question.

10. **Measure and improve**

    Track agent reliability, task completion, content performance, brand mentions, and AI-search visibility. Update shared memory with lessons that should benefit every agent.

## Decision framework

| If | Then | Because |
|---|---|---|
| Agents repeatedly ask for the same context | Move that context into shared persistent memory | Repeated prompts indicate fragmented knowledge |
| A remote agent needs local notes | Expose selected folders through MCP | The agent cannot use files it cannot reach |
| The laptop must be closed while work continues | Run the Agent OS on an always-on host | Local sleep interrupts execution and context access |
| Users need access from phones or multiple networks | Use a private mesh VPN | Device-local dashboards are otherwise difficult to reach securely |
| The same task is performed frequently | Turn it into a reusable pop-up agent | Standardized agents reduce setup and improve consistency |
| Voice is mainly for commands and navigation | Use a subscription-backed CLI or existing voice layer | A separate real-time API may be unnecessary |
| A custom voice or advanced audio control is required | Add a dedicated voice provider | Specialized providers offer more voice customization |
| The target is visibility in AI answers | Research query fan-outs and entity mentions | AI systems synthesize multiple related searches and sources |
| Search Console shows impressions but weak coverage | Build pages for the missing answer gaps | Existing demand provides a validated content opportunity |
| Content is generated automatically | Require checklist-based quality control and human review | Automation without verification creates inaccurate or generic output |
| A device is lost or compromised | Revoke only that device's token | Per-device credentials limit blast radius |
| Sensitive notes are mixed with business notes | Share specific folders instead of the entire vault | Least-privilege access reduces privacy and security risk |

## Best practices

- Treat shared memory as the system's source of truth.
- Store durable facts in structured, searchable Markdown rather than chat history alone.
- Use explicit folder permissions and least-privilege access.
- Give every agent a narrow job and a defined output format.
- Keep agent prompts focused on decisions and actions, not repeated background.
- Make voice commands confirm destructive, financial, or externally visible actions.
- Keep the always-on host separate from sensitive personal data where possible.
- Use device-specific tokens and rotate credentials periodically.
- Maintain a human approval step before publishing or sending content.
- Evaluate AI-search content for usefulness, not keyword density.
- Track brand mentions, citations, and entity consistency across the web.
- Design the system so models can be replaced without rebuilding memory and workflows.
- Document setup, recovery, device revocation, and backup procedures.

## Pitfalls

- **Fragmented memory:** Agents use separate notes and produce inconsistent answers.
- **Laptop dependency:** Background work stops when the primary computer sleeps.
- **Over-sharing the vault:** Agents gain access to private material they do not need.
- **Single shared credential:** One compromised token exposes every device.
- **Voice without safeguards:** Spoken ambiguity can trigger unwanted actions.
- **Agent sprawl:** Too many overlapping agents make ownership and debugging unclear.
- **Unverified automation:** Generated content may be inaccurate, repetitive, or off-brand.
- **Chasing backlinks only:** AI-search visibility also depends on broader brand and entity mentions.
- **Ignoring query fan-outs:** A page may target the obvious keyword while missing the related questions AI systems assemble.
- **Publishing everywhere indiscriminately:** Low-quality distribution can damage trust and create inconsistent brand information.
- **Hidden installation assumptions:** A dashboard demo may omit dependencies, ports, transport settings, or authentication details.
- **Overclaiming results:** Rankings and correlations shown in a demonstration are evidence of a setup, not a guarantee of future performance.

## Vocabulary

- **Agent OS:** A dashboard and orchestration layer for managing multiple AI agents, tools, memory, and workflows.
- **One brain:** A single shared memory layer available to all authorized agents and devices.
- **MCP:** Model Context Protocol, a standard mechanism for connecting agents to external tools and data.
- **Obsidian Sync MCP:** An MCP connection that exposes selected Obsidian notes to remote agents.
- **Always-on host:** A computer or server that remains available for background agent execution.
- **Mesh VPN:** A private network connecting approved devices across different locations.
- **Tailscale:** A mesh VPN service commonly used to connect devices through WireGuard.
- **Pop-up agent:** A ready-to-use, task-specific agent launched from a shared dashboard.
- **GEO:** Generative Engine Optimization; improving the chance that AI systems mention a business or source.
- **AIO:** AI Optimization, commonly referring to visibility in AI-generated search summaries and answers.
- **Search OS:** A workflow that combines search data, content-gap research, generation, quality control, publishing, and visibility tracking.
- **Query fan-out:** The set of related searches an AI system may perform to construct one answer.
- **Brand mention:** An external reference to a business, product, or entity, whether or not it includes a backlink.
- **Shared memory:** Persistent facts, notes, decisions, preferences, and project context available across agents.
- **Device token:** A credential assigned to one device so its access can be revoked independently.

## Sources

- [Hermes Agent OS Just Got 5 INSANE Upgrades!](https://www.youtube.com/watch?v=Nm6rVRw8hAU) — Shared “One Brain” architecture, Obsidian Sync MCP, voice agent integration, remote access through Tailscale/WireGuard, reusable pop-up agents, and Search OS/GEO workflows using Search Console data, query fan-outs, quality checklists, and brand mentions.
