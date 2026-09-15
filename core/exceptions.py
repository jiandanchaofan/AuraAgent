"""AuraAgent's exception hierarchy.

core/react_engine.py only ever catches ToolExecutionError — every other
error type is allowed to propagate and crash the current run() call
loudly, because silently swallowing an unexpected error would hide bugs
during the "white-box learning" phase this project is built for.
"""
from __future__ import annotations


class AuraAgentError(Exception):
    """Base class for all AuraAgent-raised errors."""


class ToolExecutionError(AuraAgentError):
    """A registered tool handler raised, or the requested tool name is unknown.

    Caught by the ReAct engine and turned into an `is_error` Observation
    fed back to the LLM — this is what lets the loop keep going instead of
    crashing whenever a tool call fails.
    """


class ConfirmationDeniedError(AuraAgentError):
    """Raised by a tool handler when a human declines a HITL confirmation.

    Tool handlers should generally catch this internally and return a
    plain-text Observation instead of letting it propagate, so the LLM can
    reason about the decline like any other Observation rather than seeing
    a hard failure.
    """


class MaxTurnsExceededError(AuraAgentError):
    """The ReAct loop hit its configured turn limit without reaching end_turn."""

    def __init__(self, max_turns: int) -> None:
        super().__init__(f"ReAct loop exceeded max_turns={max_turns} without reaching end_turn")
        self.max_turns = max_turns


class SandboxPathError(AuraAgentError):
    """A tool attempted to access a path outside its configured sandbox root."""
