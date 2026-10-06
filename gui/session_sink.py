"""SessionSink -- the LogSink that persists every white-box event into the
currently-active chat session's file (tools/sessions/session_store.py),
so a conversation survives a server restart and can be replayed/resumed.

`active_session_id` is a plain mutable attribute, not fixed at construction
-- the same connection switches sessions over its lifetime ("New chat",
picking an old one from the sidebar), so gui/server.py just reassigns it
whenever that happens. This mirrors tools/workspace_root.py's
SwappableWorkspaceRoot indirection pattern: one small object other code
repoints, rather than threading a session id through every call site.

Like every other LogSink, write() must never await/block (core/logger.py's
module docstring) -- it delegates straight to
ChatSessionStore.append_event_sync(), which is plain synchronous file I/O
for exactly that reason.
"""
from __future__ import annotations

from typing import Any

from core.logger import LogSink
from tools.sessions.session_store import ChatSessionStore


class SessionSink(LogSink):
    def __init__(self, store: ChatSessionStore) -> None:
        self._store = store
        self.active_session_id: str | None = None

    def write(self, event: dict[str, Any]) -> None:
        if self.active_session_id is None:
            return
        self._store.append_event_sync(self.active_session_id, event)
