"""Claude Code Templates integration for Agent Skill Builder.

Curated and modular templates sourced from davila7/claude-code-templates,
covering AI agents, slash commands, domain skills, and coding rules.
Allows agents and developers to adopt ready-to-use skills directly
into the Dennis Agentic OS Obsidian Vault.
"""

from __future__ import annotations

import re
import secrets
import time
from typing import Any, Dict, List, Optional

from . import config
from . import skill_builder
from . import vault as dennis_vault


CLAUDE_CODE_REPO = "https://github.com/davila7/claude-code-templates"

TEMPLATES: List[Dict[str, Any]] = [
    # -------------------------------------------------------------------------
    # AGENTS
    # -------------------------------------------------------------------------
    {
        "id": "agent-frontend-developer",
        "name": "claude-frontend-developer",
        "category": "agents",
        "title": "Frontend Developer (React / Next.js / Tailwind)",
        "description": "Specialist in modern web frontend architecture, React 19, Next.js App Router, Tailwind CSS, accessibility, and high-performance component state management.",
        "tags": ["react", "nextjs", "tailwind", "typescript", "ui", "agent"],
        "author": "davila7/claude-code-templates",
        "content": """---
name: claude-frontend-developer
description: Specialist in modern frontend development using React 19, Next.js App Router, Tailwind CSS, and TypeScript. Use when designing, building, or refactoring UI components and client-side application logic.
---

# Frontend Developer Specialist

Expert frontend engineering skill implementing modern component architecture, responsive styling, accessibility standards, and performant state synchronization.

## When to use

- Building new interactive UI views, widgets, or responsive dashboards.
- Refactoring legacy component state into modern React / Next.js patterns.
- Diagnosing and optimizing render bottlenecks, layout shifts, or bundle size bloat.
- Enforcing WCAG 2.1 AA accessibility guidelines across frontend controls.

Not for: low-level database tuning, raw backend microservices, or infrastructure automation.

## Core methods

1. **Component Atomicity** — Decompose complex interfaces into single-responsibility presentational and container components.
2. **Predictable State Flow** — Prefer local state (`useState`, `useReducer`) for view state; lift to shared contexts or atom stores only when cross-boundary synchronization is required.
3. **Responsive Design System** — Apply mobile-first utility classes with Tailwind, leveraging semantic color variables and clean glassmorphism tokens.
4. **Accessible Semantics** — Ensure ARIA roles, live regions, keyboard navigation (`tabindex`, focus rings), and contrast ratios comply with WCAG standards.
5. **Zero-Shift Asset Handling** — Reserve container dimensions for images, videos, and dynamic embeds to eliminate Cumulative Layout Shift (CLS).

## Workflow (default path)

1. **Inspect Requirements** — Identify layout constraints, responsive breakpoints, user interactions, and required API data bindings.
2. **Skeleton Definition** — Write semantic HTML / JSX markup with complete accessibility attributes and keyboard traps avoided.
3. **Styling & Tokens** — Apply Tailwind utility classes adhering to the OS visual theme.
4. **State & Effects** — Implement deterministic state handlers, memoize expensive calculations (`useMemo`), and clean up subscriptions in `useEffect`.
5. **Verification** — Test responsive behavior across viewport sizes and verify keyboard navigation.

## Decision framework

| If... | Then... | Because |
|---|---|---|
| State is only used by one component tree | Keep state local to the parent container | Avoids unnecessary re-renders of unrelated DOM nodes |
| UI requires dynamic media preview | Allocate aspect-ratio wrappers before asset load | Prevents Cumulative Layout Shift (CLS) |
| User action performs async network call | Disable trigger button and render explicit loading state | Prevents duplicate submissions and informs the user |

## Best practices

- Always add `aria-label` to icon-only buttons.
- Use CSS transitions for micro-interactions without blocking the main JavaScript thread.
- Isolate client components in Next.js using `"use client"` directives only where interactivity is required.

## Pitfalls

- Don't mutate state objects directly in place; always return fresh object/array copies.
- Don't use raw pixel widths for layout containers; use fluid percentages or `minmax()` grids.

## Sources

- Repository: https://github.com/davila7/claude-code-templates (frontend-developer agent)
"""
    },
    {
        "id": "agent-security-auditor",
        "name": "claude-security-auditor",
        "category": "agents",
        "title": "Application Security Auditor",
        "description": "Audits codebases for OWASP Top 10 vulnerabilities, unauthorized credential exposure, SSRF risks, and permission enforcement boundaries.",
        "tags": ["security", "owasp", "audit", "ssrf", "hardening", "agent"],
        "author": "davila7/claude-code-templates",
        "content": """---
name: claude-security-auditor
description: Audits codebases for OWASP Top 10 vulnerabilities, credential leakage, SSRF vectors, and broken access control. Use when reviewing code, reviewing dependencies, or validating endpoint protection.
---

# Security Auditor Specialist

Systematic application security analysis covering input sanitization, authentication verification, secret isolation, and defense-in-depth enforcement.

## When to use

- Reviewing new API endpoints, file upload handlers, or webhook receivers.
- Verifying protection against Server-Side Request Forgery (SSRF) and path traversal.
- Checking that environment variables and credentials are never logged or echoed to the client.
- Pre-release security verification and compliance checks.

Not for: routine code styling or aesthetic UI changes.

## Core methods

1. **SSRF Guarding** — Verify that external URL fetchers validate IP addresses against loopback (`127.0.0.1`), private RFC 1918 subnets, and cloud metadata endpoints (`169.254.169.254`).
2. **Path Confinement** — Ensure all file system reads and writes resolve absolute paths and verify they remain strictly within designated sandbox directories.
3. **Secret Isolation** — Enforce that API keys, bearer tokens, and credentials are read from server environment variables only and never emitted in client responses.
4. **Input Boundary Validation** — Validate incoming JSON payloads against strict schemas, enforcing length limits and type safety.
5. **Authentication Verification** — Confirm that modifying endpoints (POST, PUT, DELETE) require valid session tokens and reject cross-origin forgery (CSRF/Origin validation).

## Workflow (default path)

1. **Threat Surface Mapping** — Enumerate all public routes, input vectors, file operations, and external outbound connections.
2. **Vulnerability Inspection** — Trace untrusted user input from entry point to sink (database, shell, file system, or network request).
3. **Remediation Plan** — Identify root causes and formulate specific code fixes with defense-in-depth principles.
4. **Verification Test** — Create negative security tests demonstrating that invalid inputs or malicious payloads are safely rejected.

## Decision framework

| If... | Then... | Because |
|---|---|---|
| An endpoint fetches remote URLs | Resolve DNS and block private / loopback IP ranges | Prevents internal network reconnaissance (SSRF) |
| Path parameter is accepted from user | Call `os.path.realpath` and assert common prefix | Prevents directory traversal (`../../etc/passwd`) |
| Request modifies persistent state | Validate owner token and CSRF origin headers | Prevents unauthorized cross-site modification |

## Best practices

- Treat all external URLs, headers, and uploaded file contents as untrusted data.
- Use explicit allowlists for allowed MIME types rather than relying solely on file extensions.
- Sanitize error messages to avoid disclosing internal stack traces or directory structures.

## Pitfalls

- Don't rely on client-side validation alone; always re-validate on the server.
- Don't bypass security checks for local testing without strict environment guardrails.

## Sources

- Repository: https://github.com/davila7/claude-code-templates (security-auditor agent)
"""
    },
    {
        "id": "agent-database-architect",
        "name": "claude-database-architect",
        "category": "agents",
        "title": "Database Architect & Query Optimizer",
        "description": "Specialist in relational schema design, PostgreSQL / SQLite optimization, indexing strategies, atomic transactions, and migration safety.",
        "tags": ["database", "sql", "postgresql", "sqlite", "migrations", "agent"],
        "author": "davila7/claude-code-templates",
        "content": """---
name: claude-database-architect
description: Specialist in database schema design, indexing strategies, ACID transaction management, and query optimization for PostgreSQL and SQLite. Use when creating schemas, diagnosing slow queries, or writing migrations.
---

# Database Architect Specialist

Guides resilient schema evolution, index creation, locking reduction, and atomic data integrity across relational databases.

## When to use

- Designing new database tables, foreign key relationships, and indexes.
- Diagnosing slow `SELECT` queries with `EXPLAIN ANALYZE`.
- Writing safe, backward-compatible schema migrations without database locks.
- Designing atomic batch operations and audit log tables.

Not for: client-side UI rendering or CSS design.

## Core methods

1. **Index Optimization** — Design composite indexes tailored to frequent filter (`WHERE`), join (`JOIN`), and sort (`ORDER BY`) access patterns.
2. **ACID Transaction Scoping** — Keep transactions short and focused to prevent table lock contention and connection pool exhaustion.
3. **Safe Schema Migrations** — Execute column additions as nullable first, deploy backfills separately, and add NOT NULL constraints as a final step.
4. **Connection Pool Management** — Size connection pool workers according to CPU cores and memory limits; enforce query timeout deadlines.
5. **Data Confinement & Integrity** — Enforce foreign key constraints with explicit `ON DELETE CASCADE` or `RESTRICT` rules.

## Workflow (default path)

1. **Analyze Access Patterns** — Identify reads vs. writes, throughput expectations, and query frequency.
2. **Design Normalized Schema** — Define tables in 3NF, specifying primary keys, foreign keys, and default timestamps.
3. **Query Profiling** — Run `EXPLAIN (ANALYZE, BUFFERS)` to verify index usage and eliminate sequential scans on large tables.
4. **Migration Script** — Package changes into reversible migration steps with rollback provisions.

## Decision framework

| If... | Then... | Because |
|---|---|---|
| Query performs a Sequential Scan on >10k rows | Add an index matching the WHERE and ORDER BY columns | Eliminates expensive disk scans and CPU thrashing |
| Migration adds a default value to large table | Use default without lock (Postgres 11+) or backfill in chunks | Avoids locking the table during production traffic |
| Financial or audit data is stored | Use immutable append-only ledger tables | Ensures traceability and prevents accidental data tampering |

## Best practices

- Always use parameterized queries or ORM bindings; never concatenate raw SQL strings.
- Store timestamps in UTC format with timezone indicators (`TIMESTAMPTZ`).
- Add composite indexes ordered from highest cardinality to lowest cardinality.

## Pitfalls

- Don't over-index: every index increases write latency and storage overhead.
- Don't perform long-running network calls inside open database transactions.

## Sources

- Repository: https://github.com/davila7/claude-code-templates (database-architect agent)
"""
    },
    {
        "id": "agent-code-reviewer",
        "name": "claude-code-reviewer",
        "category": "agents",
        "title": "Senior Code Reviewer",
        "description": "Performs rigorous code reviews focusing on architectural integrity, maintainability, edge cases, error handling, and regression risks.",
        "tags": ["review", "quality", "clean-code", "refactoring", "agent"],
        "author": "davila7/claude-code-templates",
        "content": """---
name: claude-code-reviewer
description: Expert code reviewer analyzing pull requests and codebase diffs for maintainability, edge cases, silent failures, and architectural drift. Use before merging changes or during architectural reviews.
---

# Senior Code Reviewer Specialist

Provides structured, actionable code feedback that balances velocity, code clarity, defensive error handling, and system performance.

## When to use

- Reviewing pull requests and code modifications before merging to master.
- Auditing legacy code for dead logic, redundant complexity, and race conditions.
- Validating that new code includes regression tests and documentation.

Not for: writing initial code from scratch without context.

## Core methods

1. **Defensive Boundary Review** — Check that every external input is validated and that edge cases (null, empty strings, oversized inputs) are handled.
2. **Resource Leak Detection** — Ensure open files, network sockets, timers, and database connections are reliably closed using context managers or try/finally blocks.
3. **Idempotency & Concurrency** — Verify that repeated operations or concurrent calls cannot leave shared state in a corrupted condition.
4. **Clarity & Cognitive Load** — Suggest simplifications for deeply nested loops, convoluted conditionals, and redundant abstractions.
5. **Test Coverage Verification** — Confirm that bug fixes include tests proving the failure before the fix and success after.

## Decision framework

| If... | Then... | Because |
|---|---|---|
| Function exceeds 60 lines or 4 nesting levels | Recommend extracting cohesive helper functions | Reduces cognitive load and facilitates isolated testing |
| Async call lacks timeout or catch handler | Require explicit timeout and error fallback | Prevents hung workers and unhandled promise rejections |
| Error is caught and silenced without logging | Require structured error logging or re-raise | Prevents silent failures that make debugging impossible |

## Best practices

- Classify review comments into Blocking, Suggestion, and Nitpick.
- Praise elegant solutions and clear documentation.
- Verify that comments explain 'why', not merely restating 'what' the code does.

## Sources

- Repository: https://github.com/davila7/claude-code-templates (code-reviewer agent)
"""
    },
    {
        "id": "agent-mcp-expert",
        "name": "claude-mcp-expert",
        "category": "agents",
        "title": "Model Context Protocol (MCP) Specialist",
        "description": "Designs and implements MCP servers, client shims, tool schemas, and resource providers adhering to the Anthropic MCP specification.",
        "tags": ["mcp", "anthropic", "protocol", "tools", "integrations", "agent"],
        "author": "davila7/claude-code-templates",
        "content": """---
name: claude-mcp-expert
description: Specialist in Model Context Protocol (MCP) servers, tool definitions, resource providers, and client integrations. Use when building, debugging, or configuring MCP shims and agent tool interfaces.
---

# Model Context Protocol (MCP) Specialist

Architects standardized tool surfaces and contextual feeds connecting LLM agents to local files, external APIs, and execution environments.

## When to use

- Building new MCP servers over JSON-RPC 2.0 (stdio or SSE transport).
- Defining clean JSON Schema parameter contracts for agent tools.
- Implementing owner-approval gates for mutating or credit-consuming actions.
- Debugging MCP transport handshakes, message framing, and tool execution failures.

Not for: general frontend CSS styling.

## Core methods

1. **Strict Tool Schemas** — Define explicit `inputSchema` with type validations, required fields, and `additionalProperties: false`.
2. **Mutating Action Safeguards** — Clearly delineate read-only tools (auto-approved) from mutating tools requiring owner confirmation.
3. **Transport Confinement** — Ensure stdio processes communicate exclusively over standard streams and handle SIGTERM gracefully.
4. **Context Resource Routing** — Expose contextual feeds (logs, state, memory) via standardized MCP resource URIs.
5. **Detailed Error Payloads** — Return structured JSON-RPC error objects with standardized codes rather than unformatted tracebacks.

## Workflow (default path)

1. **Protocol Handshake** — Establish initialize negotiation, capabilities exchange, and protocol version agreement.
2. **Tool Registration** — Expose list of tools with human-readable descriptions and complete parameter schemas.
3. **Execution Handling** — Parse arguments, validate schema conformance, check approval state, and run worker logic.
4. **Result Packaging** — Format output as MCP Content objects (`text`, `image`, or `resource`).

## Best practices

- Keep tool descriptions concise, specifying concrete constraints and expected outcomes.
- Never write debug prints to stdout in stdio mode; route all diagnostic logs to stderr.

## Sources

- Repository: https://github.com/davila7/claude-code-templates (mcp-expert agent)
"""
    },
    {
        "id": "agent-deployer",
        "name": "claude-deployer",
        "category": "agents",
        "title": "DevOps & Deployment Specialist",
        "description": "Guides Docker containerization, multi-stage builds, CI/CD pipelines, reverse proxy configurations, and automated zero-downtime deploys.",
        "tags": ["devops", "docker", "deploy", "containers", "ci-cd", "agent"],
        "author": "davila7/claude-code-templates",
        "content": """---
name: claude-deployer
description: Specialist in Docker containerization, CI/CD pipelines, reverse proxies, and production deployment automation. Use when writing Dockerfiles, configuring environments, or automating deploy scripts.
---

# DevOps & Deployment Specialist

Automates container builds, environment isolation, health checks, and resilient application deployment workflows.

## When to use

- Creating or optimizing multi-stage Dockerfiles for minimal image sizes.
- Configuring reverse proxies (Nginx, Caddy) with TLS and WebSocket proxying.
- Writing CI/CD GitHub Actions workflows for automated test runs and releases.
- Setting up health probes, grace periods, and zero-downtime container rollouts.

## Core methods

1. **Multi-Stage Builds** — Separate compilation dependencies from runtime images to minimize attack surface and download size.
2. **Non-Root Execution** — Enforce unprivileged user accounts inside containers to mitigate container breakout risks.
3. **Robust Health Probing** — Implement lightweight HTTP `/health` or socket checks with configurable retry intervals.
4. **Environment Hardening** — Pass secrets via secure environment mounts or runtime secret managers; never bake keys into image layers.
5. **Graceful Signal Handling** — Forward `SIGTERM` and `SIGINT` signals properly so applications flush state before shutdown.

## Best practices

- Pin base image tags to specific minor versions or digests; avoid `:latest` in production.
- Use `.dockerignore` to exclude local caches, git histories, and sensitive environment files.

## Sources

- Repository: https://github.com/davila7/claude-code-templates (deployer agent)
"""
    },

    # -------------------------------------------------------------------------
    # COMMANDS
    # -------------------------------------------------------------------------
    {
        "id": "command-generate-tests",
        "name": "claude-generate-tests",
        "category": "commands",
        "title": "Generate Tests Slash Command (/generate-tests)",
        "description": "Automated workflow to generate comprehensive unit, boundary, and integration test suites for any source file or module.",
        "tags": ["testing", "unittest", "pytest", "jest", "commands"],
        "author": "davila7/claude-code-templates",
        "content": """---
name: claude-generate-tests
description: Automated test generation command. Inspects target source code, identifies edge cases and failure modes, and generates robust unit and integration tests.
---

# Generate Tests (/generate-tests)

Creates high-coverage, deterministic test suites with mocked external dependencies and boundary verification.

## When to use

- Adding test coverage to newly written functions or backend routes.
- Writing regression tests after diagnosing an edge case bug.
- Generating property-based or table-driven tests for pure algorithmic routines.

## Core methods

1. **Contract Analysis** — Read function signatures, parameter type constraints, and return contracts.
2. **Happy Path Tests** — Generate tests verifying expected behavior under standard inputs.
3. **Boundary & Edge Testing** — Exercise empty inputs, null pointers, unicode strings, oversized payloads, and invalid types.
4. **Failure & Exception Handling** — Verify that functions throw expected exceptions or return appropriate error structures on failure.
5. **Isolation & Mocking** — Mock network I/O, file system operations, and external API calls to keep tests deterministic and fast.

## Workflow

1. Select target file and function.
2. Run test generator with framework detection (pytest, unittest, jest).
3. Execute the generated tests to verify they pass against the implementation.
4. Verify tests fail when deliberate regressions are introduced.

## Best practices

- Name tests descriptively: `test_<function>_<scenario>_<expected_result>()`.
- Assert specific error messages or error codes, not merely general exception classes.

## Sources

- Repository: https://github.com/davila7/claude-code-templates (commands/generate-tests)
"""
    },
    {
        "id": "command-optimize-bundle",
        "name": "claude-optimize-bundle",
        "category": "commands",
        "title": "Optimize Bundle Slash Command (/optimize-bundle)",
        "description": "Analyzes JavaScript and CSS bundle sizes, detects unused dependencies, tree-shaking failures, and advises on code splitting.",
        "tags": ["performance", "bundle", "webpack", "vite", "commands"],
        "author": "davila7/claude-code-templates",
        "content": """---
name: claude-optimize-bundle
description: Bundle optimization command. Analyzes production build artifacts, uncovers bloated imports, configures dynamic code-splitting, and optimizes load times.
---

# Optimize Bundle (/optimize-bundle)

Detects and fixes frontend asset bloat, duplicate dependencies, and unminified bundles.

## When to use

- When production bundle exceeds target performance budgets (>200 KB initial JS).
- When third-party packages introduce large unneeded transitive dependencies.
- Configuring route-based and component-based lazy loading (`import()`).

## Core methods

1. **Visual Bundle Treemap** — Inspect webpack / vite bundle visualizer stats to pinpoint the largest contributors.
2. **Tree-Shaking Verification** — Replace whole-package imports with deep path imports or ES modules that tree-shake cleanly.
3. **Dynamic Import Splitting** — Wrap non-critical components (modals, heavy charting libraries) in dynamic loaders.
4. **Asset Compression** — Verify Brotli and Gzip compression headers and SVG optimization.

## Best practices

- Avoid importing entire utility libraries (e.g. `lodash`) when individual functions suffice.
- Check for duplicate versions of the same dependency in `package-lock.json`.

## Sources

- Repository: https://github.com/davila7/claude-code-templates (commands/optimize-bundle)
"""
    },
    {
        "id": "command-check-security",
        "name": "claude-check-security",
        "category": "commands",
        "title": "Check Security Slash Command (/check-security)",
        "description": "Performs fast static security analysis (SAST), secrets scanning, dependency vulnerability audits, and permission checks.",
        "tags": ["security", "audit", "sast", "secrets", "commands"],
        "author": "davila7/claude-code-templates",
        "content": """---
name: claude-check-security
description: Rapid security analysis command. Scans the workspace for hardcoded secrets, insecure deserialization, SQL injection patterns, and unpinned vulnerable dependencies.
---

# Check Security (/check-security)

Automated security verification script that inspects workspace files for compliance with security baselines.

## When to use

- Prior to committing code or pushing branches to remote repositories.
- Verifying that no `.env` files or API keys are staged in git index.
- Auditing dependencies with `npm audit` or `pip-audit`.

## Core methods

1. **Secret Scanning** — Search for regex signatures matching AWS keys, GitHub tokens, Gemini/Anthropic/OpenAI keys, and private SSH certificates.
2. **Insecure Function Auditing** — Flag dangerous functions like `eval()`, `exec()`, `shell=True` without sanitization, and raw SQL queries.
3. **CORS & Origin Checks** — Verify that wildcard CORS (`*`) is not combined with credentialed requests.

## Best practices

- Integrate secret scanning into git pre-commit hooks to catch secrets before they leave developer machines.
- Immediately revoke and rotate any token that was ever exposed to source control.

## Sources

- Repository: https://github.com/davila7/claude-code-templates (commands/check-security)
"""
    },

    # -------------------------------------------------------------------------
    # SKILLS
    # -------------------------------------------------------------------------
    {
        "id": "skill-owasp-security",
        "name": "claude-owasp-security",
        "category": "skills",
        "title": "OWASP Top 10 Security Hardening Skill",
        "description": "Comprehensive security remediation framework addressing Broken Access Control, Cryptographic Failures, Injection, and Insecure Design.",
        "tags": ["security", "owasp", "hardening", "web-security", "skills"],
        "author": "davila7/claude-code-templates",
        "content": """---
name: claude-owasp-security
description: Master skill for hardening web applications against OWASP Top 10 threats. Provides actionable remediation patterns for injection, SSRF, XSS, and broken access controls.
---

# OWASP Security Hardening Skill

Actionable defensive engineering patterns addressing the OWASP Top 10 web application vulnerabilities.

## When to use

- Designing or securing public web applications and REST/WebSocket APIs.
- Remedying high-severity vulnerability audit findings.
- Establishing security architecture guidelines for authentication and data storage.

## Core methods

1. **A01: Broken Access Control** — Enforce authorization checks at every business logic layer; verify that user identity matches resource ownership.
2. **A02: Cryptographic Failures** — Enforce TLS 1.3 in transit, bcrypt/argon2 for password hashes, and AES-256-GCM with unique nonces for sensitive data at rest.
3. **A03: Injection** — Always use parameterized queries for databases and structured argument lists (avoiding shell execution) for OS commands.
4. **A07: Identification & Auth Failures** — Implement rate limiting on login attempts, enforce session timeouts, and use cryptographically secure random session tokens.
5. **A10: Server-Side Request Forgery (SSRF)** — Revalidate every outbound URL against private, loopback, and metadata network ranges before dispatch.

## Best practices

- Fail closed: if an authorization check encounters an error, deny access by default.
- Set secure cookie attributes: `HttpOnly`, `Secure`, and `SameSite=Strict`.

## Sources

- Repository: https://github.com/davila7/claude-code-templates (skills/owasp-security)
"""
    },
    {
        "id": "skill-pdf-excel-automation",
        "name": "claude-pdf-excel-automation",
        "category": "skills",
        "title": "PDF & Excel Data Pipeline Automation",
        "description": "Production skill for extracting, transforming, and compiling tabular data from PDF documents, spreadsheets, and CSV exports.",
        "tags": ["data", "pdf", "excel", "spreadsheets", "automation", "skills"],
        "author": "davila7/claude-code-templates",
        "content": """---
name: claude-pdf-excel-automation
description: Automated pipeline skill for parsing, cleaning, and transforming tabular data from PDF files, Excel spreadsheets, and CSV streams.
---

# PDF & Excel Automation Skill

Reliable tabular data extraction, validation, and transformation pipeline for office documents and reporting workflows.

## When to use

- Extracting financial or operational tables from scanned or vector PDFs.
- Batch processing multiple Excel workbooks into unified normalized datasets.
- Generating formatted Excel reports with styled headers, conditional formulas, and freeze panes.

## Core methods

1. **Table Boundary Detection** — Accurately detect table headers, data rows, and footer totals in irregular document layouts.
2. **Type Coercion & Validation** — Coerce date strings, currency symbols, and percentages into standardized numeric types with null safety.
3. **Deduplication & Reconciliation** — Match records using composite keys and flag discrepancies between separate report sources.
4. **Stream Processing** — Process large multi-sheet workbooks in streaming chunks to keep memory usage under tight thresholds.

## Sources

- Repository: https://github.com/davila7/claude-code-templates (skills/pdf-excel-automation)
"""
    },
    {
        "id": "skill-prompt-engineer",
        "name": "claude-prompt-engineer",
        "category": "skills",
        "title": "Systematic Prompt Engineering & Evaluation",
        "description": "Methodology for crafting deterministic LLM system prompts, few-shot evaluations, output constraint enforcement, and reasoning chains.",
        "tags": ["prompting", "llm", "evals", "system-prompts", "skills"],
        "author": "davila7/claude-code-templates",
        "content": """---
name: claude-prompt-engineer
description: Systematic prompt engineering and evaluation skill. Structures system prompts, input boundaries, few-shot examples, and strict output formats.
---

# Systematic Prompt Engineering Skill

Framework for designing, testing, and optimizing production-grade prompts for autonomous agents and LLM tool-calling systems.

## When to use

- Writing system prompts for domain-specialized AI agents.
- Eliminating model hallucinations and enforcing structured JSON/Markdown outputs.
- Designing prompt evaluation rubrics to compare model versions.

## Core methods

1. **Clear Persona & Boundaries** — State agent identity, exact role scope, allowed tools, and explicit 'Do Not' boundary rules.
2. **Evidence-Based Reasoning** — Require models to cite observed evidence before drawing conclusions (`OBSERVED` vs `INFERRED`).
3. **Structured Few-Shot Examples** — Include paired positive and negative demonstrations to disambiguate edge case decisions.
4. **Constraint Enforcement** — Place formatting constraints (JSON schema, character limits) at the very end of the prompt for maximum attention weight.

## Sources

- Repository: https://github.com/davila7/claude-code-templates (skills/prompt-engineer)
"""
    },

    # -------------------------------------------------------------------------
    # RULES
    # -------------------------------------------------------------------------
    {
        "id": "rule-architecture-standards",
        "name": "claude-architecture-standards",
        "category": "rules",
        "title": "Modular Software Architecture Rules",
        "description": "Rules for separation of concerns, dependency injection, unidirectional data flow, and minimal cognitive complexity.",
        "tags": ["architecture", "standards", "clean-code", "rules"],
        "author": "davila7/claude-code-templates",
        "content": """---
name: claude-architecture-standards
description: Enforces modular software architecture, strict separation of concerns, and unidirectional data flow across backend and frontend codebases.
---

# Architecture Standards Rules

Rules governing codebase organization, module boundaries, and dependency management.

## Core Rules

1. **Single Responsibility** — Every module, class, and function must have exactly one reason to change.
2. **Boundary Confinement** — Infrastructure code (database, HTTP, file system) must be isolated behind clean interfaces; business logic must never call raw I/O directly.
3. **No Circular Dependencies** — Dependencies must flow in a single direction (e.g., UI -> State -> API -> Transport). Circular module imports are forbidden.
4. **Deterministic Side Effects** — Pure functions are preferred for business calculations; side effects must be localized and explicitly documented.

## Sources

- Repository: https://github.com/davila7/claude-code-templates (rules/architecture-standards)
"""
    },
    {
        "id": "rule-sandbox-security",
        "name": "claude-sandbox-security",
        "category": "rules",
        "title": "Safe Sandboxed Execution Rules",
        "description": "Rules governing execution confinement, resource ceilings, timeout deadlines, and access control integrity.",
        "tags": ["security", "sandbox", "confinement", "rules"],
        "author": "davila7/claude-code-templates",
        "content": """---
name: claude-sandbox-security
description: Rules for executing agent tools and child processes safely within confined sandbox boundaries and resource ceilings.
---

# Sandbox Security Rules

Strict security controls governing agent process execution and local file access.

## Core Rules

1. **Never Defeat Access Controls** — Under no circumstances may an agent bypass permissions, spoof authentication headers, or disable TLS verification.
2. **Path Confinement** — All read/write operations must resolve canonical realpaths within designated workspace or vault boundaries.
3. **Process Ceilings** — Child processes must have strict CPU timeouts, memory limits, and process table limits to prevent fork bombs and hanging tasks.
4. **Credential Redaction** — Environment variables containing tokens or passwords must never be logged, printed to stdout, or exported to child shells.

## Sources

- Repository: https://github.com/davila7/claude-code-templates (rules/sandbox-security)
"""
    }
]


def list_templates(category: Optional[str] = None, query: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return filtered templates matching optional category and text query."""
    results = TEMPLATES
    if category and category.lower() != "all":
        cat = category.strip().lower()
        results = [t for t in results if t.get("category", "").lower() == cat]

    if query and query.strip():
        terms = query.strip().lower().split()
        filtered = []
        for t in results:
            haystack = (
                t.get("title", "") + " " +
                t.get("name", "") + " " +
                t.get("description", "") + " " +
                " ".join(t.get("tags", []))
            ).lower()
            if all(term in haystack for term in terms):
                filtered.append(t)
        results = filtered

    # Return summary representations without full markdown body to save bandwidth
    summaries = []
    for t in results:
        summaries.append({
            "id": t["id"],
            "name": t["name"],
            "category": t["category"],
            "title": t["title"],
            "description": t["description"],
            "tags": t["tags"],
            "author": t["author"],
            "content_length": len(t.get("content", ""))
        })
    return summaries


def get_template(template_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve the full template including its Markdown content."""
    for t in TEMPLATES:
        if t["id"] == template_id or t["name"] == template_id:
            return t
    return None


def adopt_template(template_id: str) -> Dict[str, Any]:
    """Adopt a Claude Code template by creating a reviewable draft in Agent Skill Builder."""
    tmpl = get_template(template_id)
    if not tmpl:
        raise ValueError(f"Template '{template_id}' not found.")

    name = tmpl["name"]
    description = tmpl["description"]
    content = tmpl["content"]
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # Check for conflict with existing Vault skills
    conflict = skill_builder._conflict(name)

    draft = {
        "id": "sbd_" + secrets.token_hex(10),
        "name": name,
        "description": description,
        "content": content,
        "mode": "templates",
        "objective": "template",
        "author": tmpl.get("author", "davila7/claude-code-templates"),
        "agent_error": "",
        "character_image_url": "",
        "sources": [{
            "kind": "repository",
            "title": "davila7/claude-code-templates",
            "url": CLAUDE_CODE_REPO,
            "category": tmpl.get("category", "template")
        }],
        "warnings": [],
        "timings": {"adoption_seconds": 0.05},
        "prompt_chars": len(content),
        "source_chars": len(content),
        "conflict": conflict,
        "target": skill_builder._vault_skill_path(name),
        "vault_dir": dennis_vault.VAULT_DIR,
        "created_at": now,
        "updated_at": now,
        "status": "review",
    }

    skill_builder._save_draft(draft)
    return draft
