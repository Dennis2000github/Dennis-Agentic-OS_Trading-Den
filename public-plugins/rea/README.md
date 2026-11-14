# REA — Reverse Engineer Anything

> Upstream: [morluto/rea](https://github.com/morluto/rea) · [rea.tools](https://rea.tools) · MIT · snapshot of `6.8.0` (`c3647ade1e55`, 2026-11-04)

One MCP for reverse engineering across binaries, applications, and runtime
behavior. See a feature you like? Understand how it works, down to the binary
level — REA inspects the target, returns findings with evidence, and your agent
explains the behavior or builds a version for your project.

## What it analyzes

| Target | Requirements |
|---|---|
| Native binaries (pseudocode, assembly, symbols) | Hopper, Ghidra, IDA or Kuna |
| JavaScript / Electron apps (modules, IPC, routes) | Node.js + npm |
| Websites (structure, scripts, network, screenshots) | Chrome-family browser |
| .NET assemblies, Android APKs, Flutter, firmware, GPU code, Go/Rust binaries, model artifacts (GGUF/safetensors) | Varies — see the bundled skill references |

Static analysis reads files without running anything; runtime capture uses your
user permissions. Analysis runs locally.

## Contents

```
plugin.json                            universal manifest (tier: public)
skills/reverse-engineer-anything/      official REA skill + 7 references
mcp/rea.mcp.json                       stdio MCP config (`npx rea-agents mcp`)
LICENSE                                MIT, from upstream
```

## Requirements

- Node.js 22.x (>= 22.23.4), 24.x (>= 24.22.1), or 26.12.1+, plus npm
- Native binary analysis additionally needs a Hopper, Ghidra, IDA or Kuna
  installation (setup can optionally install Hopper with approval); static
  JavaScript/Electron analysis needs no native engine

## Install

From the Plugins tab (Discover rail → Public → REA → Install), which uses the
agent-agnostic plugin manager (`backend/plugins.py`). The MCP server is
reported from `mcp/rea.mcp.json`, not run by the plugin manager — wire it into
your agent with the generated block for your client.

## Legal

REA is for lawful research, education, interoperability, software preservation,
and analysis of software you own or are authorized to examine. See
[DISCLAIMER.md](https://github.com/morluto/rea/blob/main/DISCLAIMER.md)
upstream. REA-authored code is MIT-licensed; you must hold valid licenses for
commercial analysis engines such as IDA Pro, Hopper, and JEB.
