"""Backend package for DENNIS Agentic OS.

Modules are intentionally kept small and single-purpose:

- config          : paths + small IO helpers shared by every module
- auth            : local owner identity + session token
- projects        : allowed project roots + validated project folders
- session_store   : per-owner project session metadata (agent, project, panel state)
- agent_registry  : read access to the registered agent CLIs
- model_sources   : CLI-native model listing / selection (one module per agent)
- sandbox         : process runners (local PTY and Docker) behind one interface
- session_manager : lives sessions, streaming fan-out, idle timeout, status
- vault           : Obsidian-compatible markdown vault (Hermes second brain)
- hermes          : orchestrator agent - plan/route/delegate sub-agents, Kanban
- ws              : minimal RFC6455 WebSocket server for authenticated streaming
"""
