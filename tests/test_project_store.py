"""Tests for tools/projects/project_store.py -- ProjectStore's registry
CRUD, ProjectSummary's replace-vs-dedup-append semantics, and the
size caps that keep a project's token cost bounded no matter how long
it's been worked on."""
from __future__ import annotations

import pytest

from tools.projects.project_store import ProjectStore


def _store(tmp_path, summary_max_chars=4_000, max_list_items=30) -> ProjectStore:
    return ProjectStore(
        registry_path=tmp_path / "project_meta" / "registry.json",
        meta_dir=tmp_path / "project_meta",
        summary_max_chars=summary_max_chars,
        max_list_items=max_list_items,
    )


@pytest.mark.asyncio
async def test_create_and_list_projects(tmp_path):
    store = _store(tmp_path)
    await store.create_project("ai_governance", "ai_governance", tmp_path / "ai_governance")

    projects = await store.list_projects()

    assert len(projects) == 1
    assert projects[0].slug == "ai_governance"
    assert projects[0].directory == tmp_path / "ai_governance"


@pytest.mark.asyncio
async def test_create_project_rejects_duplicate_slug(tmp_path):
    store = _store(tmp_path)
    await store.create_project("x", "x", tmp_path / "x")

    with pytest.raises(ValueError):
        await store.create_project("x", "x", tmp_path / "x2")


@pytest.mark.asyncio
async def test_create_project_rejects_invalid_slug(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        await store.create_project("has spaces", "has spaces", tmp_path / "y")


@pytest.mark.asyncio
async def test_get_project_returns_none_for_unknown_slug(tmp_path):
    store = _store(tmp_path)
    assert await store.get_project("missing") is None


@pytest.mark.asyncio
async def test_empty_summary_renders_to_empty_string(tmp_path):
    store = _store(tmp_path)
    summary = await store.get_summary("never_touched")
    assert summary.is_empty()
    assert summary.render() == ""


@pytest.mark.asyncio
async def test_current_state_is_replaced_not_appended(tmp_path):
    store = _store(tmp_path)
    await store.update_summary("p", current_state="first state")
    await store.update_summary("p", current_state="second state")

    summary = await store.get_summary("p")

    assert summary.current_state == "second state"
    assert "first state" not in summary.current_state


@pytest.mark.asyncio
async def test_outputs_and_open_questions_dedup_append(tmp_path):
    store = _store(tmp_path)
    await store.update_summary("p", outputs=["report.pdf -- initial draft"])
    await store.update_summary("p", outputs=["report.pdf -- initial draft", "slides.pptx -- summary deck"])

    summary = await store.get_summary("p")

    assert summary.outputs == ["report.pdf -- initial draft", "slides.pptx -- summary deck"]


@pytest.mark.asyncio
async def test_current_state_truncated_at_max_chars(tmp_path):
    store = _store(tmp_path, summary_max_chars=10)
    await store.update_summary("p", current_state="this is definitely longer than ten characters")

    summary = await store.get_summary("p")

    assert len(summary.current_state) == 10


@pytest.mark.asyncio
async def test_output_list_capped_keeping_most_recent(tmp_path):
    store = _store(tmp_path, max_list_items=3)
    await store.update_summary("p", outputs=["a", "b", "c", "d", "e"])

    summary = await store.get_summary("p")

    assert summary.outputs == ["c", "d", "e"]


@pytest.mark.asyncio
async def test_render_includes_all_populated_fields(tmp_path):
    store = _store(tmp_path)
    await store.update_summary(
        "p", current_state="researching EU AI Act", outputs=["notes.md -- summary"], open_questions=["scope of Annex III?"]
    )

    rendered = (await store.get_summary("p")).render()

    assert "researching EU AI Act" in rendered
    assert "notes.md -- summary" in rendered
    assert "scope of Annex III?" in rendered
