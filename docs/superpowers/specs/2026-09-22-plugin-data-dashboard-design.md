# Plugin-Data Dashboard via MCP — Design Specification

Date: 2026-09-22  
Status: Approved design; implementation not started

## Purpose

Make the Laya review dashboard a lifecycle-managed capability of the installed
plugin. The prompt hook, MCP worker, dashboard API, and labeling UI must use
the one SQLite database at the Codex-provided `PLUGIN_DATA/triage.sqlite3`
path. This removes the current split between the hook's plugin data directory
and a repository-local dashboard database.

## Scope

Included:

- A package-owned loopback HTTP dashboard server and bundled HTML resource.
- An MCP tool named `triage_dashboard` that starts the dashboard on demand and
  returns its local URL.
- Shared use of the MCP server's `Storage` instance for dashboard reads and
  labels.
- Fixed binding to `127.0.0.1:11020`.
- Tests for the shared storage path, idempotent startup, label persistence,
  and occupied-port behavior.

Excluded:

- Public/LAN binding, authentication, remote access, or proxying.
- Changes to shadow-mode routing, inference behavior, or retention policy.
- Automatic browser launching; Codex or the user opens the returned URL.

## Architecture

```text
Codex UserPromptSubmit hook
        |
        v
PLUGIN_DATA/triage.sqlite3 <--- MCP Storage ---> background Laya worker
        ^                              |
        |                              v
        +---------------- DashboardController <- triage_dashboard MCP tool
                                          |
                                          v
                               http://127.0.0.1:11020
```

`DashboardController` owns one `HTTPServer` thread for the lifetime of the
MCP process. It receives the already-open `Storage` instance rather than
opening a second database connection or deriving any path. The dashboard HTTP
handler reads and writes only through that storage object.

The dashboard HTML becomes a package resource, so the installed plugin cache
contains the full server and UI. The standalone `scripts/label_server.py`
entry point is removed or reduced to a development-only wrapper that starts
the same package controller; it must not implement a separate data-path policy.

## MCP contract

`triage_dashboard()` has no parameters and returns:

```json
{
  "url": "http://127.0.0.1:11020",
  "started": true
}
```

The first call starts the server and returns `started: true`; later calls from
the same MCP process return the same URL with `started: false`.

If another process already owns port 11020, the tool fails with an explicit
port-in-use error. It never kills, adopts, or sends requests to that process.

## Data and security boundaries

- The hook wrapper preserves Codex's `PLUGIN_DATA` environment value.
- The MCP launcher and worker preserve the same value; no component falls back
  to `repo/data` when running as an installed plugin.
- The HTTP server binds only to `127.0.0.1`; no configuration option widens
  that binding in this change.
- Existing prompt-redaction, database permissions, and 30-day retention rules
  remain unchanged.
- The dashboard continues to expose prompt text only locally, to the user who
  can access the host loopback interface.

## HTTP behavior

The existing dashboard routes are retained:

- `GET /` serves the bundled dashboard HTML.
- `GET /api/captures` returns captures, including pending rows and status.
- `GET /api/health` returns queue and record counts.
- `POST /api/label` validates and saves a human label.
- `POST /api/suggest` retains its current local LLM-assisted suggestion
  behavior and error handling.

All routes are reachable only through the loopback listener. Existing CORS
behavior is retained only for loopback usage; the server does not listen on
any public interface.

## Failure behavior

- Starting the dashboard never interrupts the MCP worker or prompt capture.
- A port collision returns an MCP tool error with the port number and no side
  effects.
- A dashboard request that encounters a storage error returns a bounded HTTP
  error response; it does not close the shared MCP storage.
- Calling `triage_dashboard` after a server startup failure permits a later
  retry after the conflicting process exits.

## Verification

1. A hook payload captured with a supplied `PLUGIN_DATA` path is visible via
   both `triage_recent` and `GET /api/captures` from the MCP-started dashboard.
2. Calling `triage_dashboard` twice creates exactly one listening server and
   reports the same URL.
3. `POST /api/label` persists a label visible to `triage_recent`.
4. A pre-bound `127.0.0.1:11020` makes `triage_dashboard` return the defined
   port-in-use error without terminating the occupying process.
5. Focused dashboard/MCP tests, then the full `pytest`, `ruff`, and `mypy`
   suite pass before release.

## Rollout

The release updates the local plugin cache with a new cachebuster and reinstalls
`knt-laya@personal`. Because plugin lifecycle hooks and MCP servers are loaded
per task, validation occurs in a new Codex task. The installed plugin must use
the Codex-issued `PLUGIN_DATA` directory, not the repository checkout's
`data/` directory.
