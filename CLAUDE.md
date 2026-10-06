# AuraAgent — working notes for Claude Code

Operational quick-reference for working in this repo efficiently. For the full narrative
(why things are the way they are, the complete Epic-by-Epic history) see
`docs/ARCHITECTURE.md` / `docs/ARCHITECTURE-cn.md` — this file is deliberately terse and
only covers what saves re-exploration at the start of a session.

## What this is

A personal multi-agent assistant: one Leader ("orchestrator") delegates to Worker agents,
all built on a shared ReAct engine (`core/react_engine.py`). Runs as a CLI (`main.py`) or a
GUI (`gui/server.py` + a React frontend in `gui/frontend/`) — both are thin frontends over
the exact same composition root, `core/bootstrap.py::build_app_context()`.

## Directory map

- `core/` — `AsyncReActEngine` (the model-call loop), `AuraLogger`/`LogSink` (event fan-out),
  `bootstrap.py` (the one shared composition root both frontends call).
- `cli/` — `commands.py` (the `/` slash-command layer, CLI-only I/O), `service.py` (the
  shared query/action functions BOTH `cli/commands.py` and `gui/routes.py` call — this is
  the real "don't duplicate CLI/GUI logic" seam), `context.py` (`CLIContext` dataclass).
- `gui/` — FastAPI backend (`server.py`, `routes.py`, `auth.py`, `ws_log_sink.py`,
  `session_sink.py`, `ws_channel.py`) + `frontend/` (React+Vite; `npm run build` outputs to
  `gui/static/`, gitignored).
- `agents/` — `AgentRegistry`/`AgentDefinition`, `ScopedToolRegistryView` (per-agent tool
  visibility), `LeaderWorkerOrchestrator`, `delegate_tool.py`.
- `tools/` — every concrete tool, one subpackage per domain (`files/`, `notes/`, `calendar/`,
  `tasks/`, `scheduler/`, `projects/`, `sessions/`, `devices/`, `memory/`, `profile/`,
  `documents/`, `system/`, `self_extend/`, `web/`). `tools/registry.py` is the one shared
  `ToolRegistry`; `tools/sandbox_path.py::resolve_within_sandbox()` is the one shared
  filesystem boundary every tool must resolve paths through.
- `providers/` — `AnthropicProvider`/`OpenAIProvider` (+ OpenAI-compatible endpoints like
  DeepSeek) behind the common `LLMProvider` interface.
- `confirmation/` — the HITL abstraction (`ConfirmationChannel`); `TerminalConfirmationChannel`
  (CLI) / `WebSocketConfirmationChannel` (GUI) are the two concrete implementations.
- `mcp_integration/` — `MCPClientManager` (one dedicated `asyncio.Task` per connected MCP
  server, for its whole connect/reconnect/close lifecycle — see its own module docstring
  before touching this, it replaced a real concurrency bug).
- `skills/` — the self-extension "write yourself a new tool" machinery.
- `config/` — `settings.py` (pydantic-settings `Settings`), `agents.json` (team roster +
  capabilities allow-list), `mcp_servers.json`.
- `tests/` — one file per module under test, run with `pytest`.

## Design patterns that repeat — recognize them, don't reinvent

- **Swappable-indirection**: `SwappableWorkspaceRoot`, `SwappableProvider`,
  `SwappableCalendarProvider`, `SwappableConfirmationChannel` — same shape every time: one
  small mutable object, every consumer holds a reference to the SAME instance and reads
  `.current` fresh at call time (never caches it in a closure), so one `/command set` call
  repoints every consumer at once. If you need a new runtime-swappable value, follow this
  pattern — don't invent a differently-shaped one.
- **Narrow-interface JSON stores**: every `tools/*/[...]_store.py` (schedule, project,
  memory, device, session) is `__init__(file_path)` + `asyncio.Lock` + `_load()`/`_save()`
  private helpers + named public methods — never raw dict/file access from outside the
  class. This is what makes a future DB swap contained to one file each.
- **Sandbox boundary**: every filesystem tool resolves user/LLM-supplied paths through
  `tools/sandbox_path.py::resolve_within_sandbox(root, relative_path)` before touching disk.
  Never build a path by hand.
- **CLI/GUI parity via `cli/service.py`**: a GUI REST handler (`gui/routes.py`) and its `/`
  command counterpart (`cli/commands.py`) always call the SAME `cli/service.py` function —
  if you're adding a capability reachable from both, put the logic there once.
- **Deferred/zero-arg-callable reads**: when something needs to read a value that doesn't
  exist yet at registration time (e.g. `tools/notes/notes_tool.py::register_quick_note_tool`'s
  `get_quick_notes_subdir`), pass a zero-arg lambda closing over the enclosing scope, not a
  captured value — Python closures resolve free variables at CALL time, so this is safe even
  when the referenced variable is assigned later in the same function.

## A real gotcha: pydantic-settings aliases

`config/settings.py`'s `Settings` has `model_config = SettingsConfigDict(..., extra="ignore")`
with NO `populate_by_name=True`. Any field declared with `alias="AURA_..."` (most of them)
**must be constructed using the alias name**, not the Python field name, when building a
`Settings(...)` directly in code (tests, scripts) — e.g. `Settings(AURA_REQUIRE_AUTH=True)`,
not `Settings(require_auth=True)`. Passing the field name silently does nothing (swallowed by
`extra="ignore"`) rather than raising — this caused a real, silent test failure once (see
`tests/test_gui_auth.py`'s history). Fields with NO alias (e.g. `devices_file`,
`quick_notes_subdir`) take their plain Python name as normal.

## Testing

- `pytest tests/` — asyncio mode is `auto` (no `@pytest.mark.asyncio` boilerplate needed,
  though the existing tests still carry it from before that was set). ~830 tests.
- **Judge test coverage and full-suite runs by the actual risk of the change, not
  reflexively** — a small additive change (a new config field, a simple wrapper endpoint)
  needs 1-2 tests (happy path + the one real failure mode), not exhaustive edge-case coverage
  across unit + integration layers. Run just the targeted test file(s) while iterating; save
  a full-suite run for when several changes have accumulated or something cross-cutting
  (shared state, a widely-used helper, config loading) was touched — not as the default
  closing step of one small feature. This is a solo project with no CI counterparty paying
  down the cost of maximal-rigor-by-default.
- `tests/test_bootstrap.py` is slow (6 tests, ~94s total, one single test ~44s) because it
  calls the real `build_app_context()` — real MCP server subprocesses, real Skill scanning.
  Skip it (`--ignore=tests/test_bootstrap.py`) when your change doesn't touch
  `core/bootstrap.py`, MCP, or Skill loading.
- When checking a background test run's output, grep/tail for the summary line
  (`passed|failed|error`) rather than pulling a large tail by default — only pull a bigger
  window when actually diagnosing a failure.
- No `pytest-xdist` parallelization is configured yet by default — install it
  (`pip install pytest-xdist`, already in requirements.txt's dev section) and run
  `pytest tests/ -n auto` for a real full-suite run to cut wall-clock time.

## Research approach

Prefer direct `Grep`/`Read` over spawning an Explore subagent when the scope is already
reasonably well understood or confined to known files — an agent starts cold and has to
re-derive context already established in the session, which costs more, not less. Reserve
parallel Explore agents for genuinely large-unknown-scope investigations (e.g. "what would
it take to support N concurrent connections" across a dozen files with unclear interactions)
where the breadth justifies it.

## Documentation conventions

- `README.md` (EN, primary) and `README-cn.md` (ZH) should stay in sync, but **scale the
  update to the feature's actual significance** — a small additive change (a new config
  field, an internal helper) doesn't need a README mention; a new user-facing capability
  does. Same for `docs/ARCHITECTURE.md`/`docs/ARCHITECTURE-cn.md`'s Epic table — add an entry
  for something a future session would need the backstory on, not for every small change.
- The architecture docs' Epic table (search for `| N13` or `| M4` style rows) is the project's
  changelog — each row is one self-contained paragraph covering what changed, why, and what
  real bug (if any) was found along the way. Match that density for a genuinely new Epic;
  don't pad a small addition to match it.

## Memory

Durable preferences/decisions/history for this project live in Claude's own memory system
(not in this file) — check there for things like "server deployment was shelved," "the
user's real Obsidian Thino format," or standing feedback about process. This file is for
codebase/operational facts that don't change conversation-to-conversation; memory is for
project history and user preferences that do.
