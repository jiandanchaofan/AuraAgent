"""Tests for tools/text_patch.py::apply_str_replace -- the shared matching
logic behind write_file/update_note's mode="str_replace" (N14).
"""
from __future__ import annotations

import pytest

from core.exceptions import ToolExecutionError
from tools.text_patch import apply_str_replace


def test_replaces_the_unique_match():
    result = apply_str_replace("one\ntwo\nthree", "two", "TWO")
    assert result == "one\nTWO\nthree"


def test_zero_matches_raises():
    with pytest.raises(ToolExecutionError, match="not found"):
        apply_str_replace("one\ntwo", "nope", "x")


def test_multiple_matches_raises():
    with pytest.raises(ToolExecutionError, match="matches 3 times"):
        apply_str_replace("a a a", "a", "b")


def test_replaces_only_the_single_occurrence_not_all():
    # count()==1 is the gate; replace(..., 1) as a defensive belt-and-braces
    # match of that same guarantee, not a second independent check.
    result = apply_str_replace("unique_token here", "unique_token", "X")
    assert result == "X here"
