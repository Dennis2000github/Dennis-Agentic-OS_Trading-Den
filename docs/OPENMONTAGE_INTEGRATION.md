# OpenMontage in Studio

## Scope

Studio embeds the actual OpenMontage Backlot dashboard. DENNIS does not copy
or reimplement Backlot's project, library, board, or production views. The
OpenMontage checkout and virtual environment remain outside this repository:

`~/PycharmProjects/OpenMontage-Workspace/OpenMontage`

The checkout may be overridden with `DENNIS_OPENMONTAGE_ROOT`. The service
port defaults to `4750` and may be overridden with
`DENNIS_OPENMONTAGE_PORT`.

## Architecture

```text
DENNIS Agent OS :8080
  └─ Studio / OpenMontage tab
       └─ iframe → OpenMontage Backlot :4750 (loopback only)
            ├─ native Library and project board routes
            ├─ native SSE library updates
            └─ OpenMontage projects/assets and provider environment
```

`backend/openmontage.py` starts and stops only a process owned by DENNIS,
checks `GET /api/health`, captures a short redacted log tail, and exposes
safe status data to Studio. It never accepts arbitrary browser commands and it
does not stop an unrelated service occupying the port. The OpenMontage server
is bound to loopback by its native server configuration.

## Verified repository audit

The inspected OpenMontage revision is `195eab24478dc9363326aec437aefc3701635ddf`.

| Area | Verified result |
| --- | --- |
| Native dashboard | Backlot Library at `/`, project board at `/p/{project}`, UI assets under `/ui`, API under `/api` |
| Launch | `python -m backlot serve --port 4750` from the OpenMontage checkout |
| Health | `GET /api/health` returns a healthy Backlot response |
| Storage | Backlot reads the configured projects directory; DENNIS supplies `OPENMONTAGE_PROJECTS_DIR` for the isolated service |
| Routing | Native absolute `/api`, `/ui`, `/p`, `/media`, and `/thumb` paths are preserved by the loopback iframe |
| Live updates | Native `/api/library/events` SSE endpoint was observed during the smoke test |
| Providers | OpenMontage's registry/adapters use its documented environment/provider configuration; no duplicate browser credential form was added |
| License | Repository license is AGPL-3.0; keep the isolated checkout and notices intact when distributing it |
| Media prerequisites | FFmpeg was not available on this Mac during audit; rendering and some provider tests therefore remain limited until installed |

The OpenMontage repository's architecture and provider documentation remain
the source of truth for provider capabilities, model identifiers, pricing,
and credential configuration. A model is not considered available merely
because it appears in an example or provider list.

## Secure provider configuration

This integration deliberately does not move provider keys through Studio.
OpenMontage's existing server-side environment/provider mechanism remains the
configuration boundary. Do not put keys in browser storage, URLs, frontend
source, project files, or logs. DENNIS's OpenMontage status endpoint returns
only health/configuration metadata and a redacted log tail.

The current isolated checkout has no provider keys configured. The first
provider-dependent test must be performed by an authorized operator with
temporary/test credentials; automated tests use mocks and do not incur usage
charges.

## Setup and lifecycle

From the isolated OpenMontage checkout, use its supported setup:

```sh
make setup
make install-dev
```

If the shell already exports a different `VIRTUAL_ENV` or `CONDA_PREFIX`,
unset those two variables for setup so OpenMontage's own `.venv` is used.
The optional Piper installation may be unavailable on Python 3.14 Intel; it
is not required for the Backlot dashboard.

Start DENNIS using its normal command, then open **Studio → OpenMontage**.
The panel reports whether the isolated service is configured, running, or
blocked by a missing prerequisite. When configured, Studio makes one
automatic start attempt; the **Start service**, **Stop**, and **Refresh**
controls operate only the DENNIS-owned Backlot child process. The existing
Higgsfield generation console is kept separately under **Studio → Higgsfield
API**.

For a direct native smoke check, from the OpenMontage checkout:

```sh
OPENMONTAGE_PROJECTS_DIR=/private/tmp/openmontage-smoke-projects \
  .venv/bin/python -m backlot serve --port 4750
```

Use `Ctrl-C` to stop that independently started process. Do not expose port
4750 publicly without an approved authentication and network-security design.

## Tests and limitations

Focused DENNIS lifecycle tests are in `tests/test_openmontage.py`. The
OpenMontage contract suite was run in its isolated environment: `1257 passed`,
`7 skipped`, and `13 failed` due to the audit sandbox preventing socket/file
writes and the host lacking `ffprobe`; these failures were not caused by
changes to the OpenMontage checkout. The native Backlot health, Library,
project route, and SSE endpoints passed the manual smoke check.

Remaining limitations:

- No provider generation was run because no real credentials were requested or
  supplied.
- FFmpeg must be installed separately before local media rendering and
  ffprobe-dependent workflows can be validated.
- OpenMontage's environment-based provider configuration is retained; a new
  DENNIS credential-management UI was intentionally not introduced.
- The iframe is loopback-to-loopback and preserves native routing. A public,
  multi-user deployment would require a separate approved auth, TLS, and
  reverse-proxy design.

## Integration files

- `backend/openmontage.py` — isolated Backlot supervisor and safe status API.
- `app.py` — owner-authenticated OpenMontage status/start/restart/stop routes
  and clean shutdown registration.
- `index.html`, `js/studio.js`, `css/studio-v2.css` — Studio entry point,
  native dashboard frame, status/error states, and lifecycle controls.
- `tests/test_openmontage.py` — no-key lifecycle and redaction tests.

