"""Tests for tools/workspace_root.py::SwappableWorkspaceRoot — mirrors
tests/test_swappable_provider.py's style, applied to the workspace root
indirection instead of the LLM provider indirection.
"""
from __future__ import annotations

from tools.workspace_root import SwappableWorkspaceRoot


def test_initial_value_is_current_and_gets_created(tmp_path):
    initial = tmp_path / "ws1"
    holder = SwappableWorkspaceRoot(initial)

    assert holder.current == initial.resolve()
    assert holder.current.is_dir()


def test_set_current_switches_and_creates_the_new_directory(tmp_path):
    holder = SwappableWorkspaceRoot(tmp_path / "ws1")
    new_root = tmp_path / "ws2"

    holder.set_current(new_root)

    assert holder.current == new_root.resolve()
    assert new_root.is_dir()


def test_two_consumers_sharing_one_instance_both_see_the_switch(tmp_path):
    # The whole point of this indirection: something that reads
    # `holder.current` fresh on every call (not a value captured once)
    # sees a change made through the SAME shared instance immediately.
    holder = SwappableWorkspaceRoot(tmp_path / "ws1")

    def consumer_a():
        return holder.current

    def consumer_b():
        return holder.current

    assert consumer_a() == consumer_b()

    holder.set_current(tmp_path / "ws2")

    assert consumer_a() == consumer_b() == (tmp_path / "ws2").resolve()
