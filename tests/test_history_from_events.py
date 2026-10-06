"""Test for cli/service.py::history_from_events -- moved out of
gui/server.py (where it was private) so the Telegram frontend
(telegram_bot.py) can reuse the exact same session-event -> chat-history
reconstruction without duplicating it. Behavior is unchanged by the move;
this is the first direct unit test it's had (previously only exercised
indirectly through GUI integration tests).
"""
from __future__ import annotations

from cli.service import history_from_events
from core.message_types import ConversationTurn


def test_keeps_only_the_leaders_own_user_input_and_final_answer_events():
    events = [
        {"agent_name": "Leader", "event_type": "user_input", "payload": {"text": "hi"}},
        {"agent_name": "Leader", "event_type": "thought", "payload": {"text": "thinking"}},
        {"agent_name": "Worker", "event_type": "user_input", "payload": {"text": "delegated sub-task"}},
        {"agent_name": "Worker", "event_type": "final_answer", "payload": {"text": "sub-task done"}},
        {"agent_name": "Leader", "event_type": "final_answer", "payload": {"text": "hello!"}},
    ]

    history = history_from_events(events, leader_name="Leader")

    assert history == [
        ConversationTurn(role="user", text="hi"),
        ConversationTurn(role="assistant", text="hello!"),
    ]


def test_empty_events_returns_empty_history():
    assert history_from_events([], leader_name="Leader") == []
