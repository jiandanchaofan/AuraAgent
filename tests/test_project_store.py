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
async def test_rename_project_changes_name_not_slug(tmp_path):
    store = _store(tmp_path)
    await store.create_project("ai_governance", "ai_governance", tmp_path / "ai_governance")

    updated = await store.rename_project("ai_governance", "  AI Governance Research  ")
    assert updated.slug == "ai_governance"
    assert updated.name == "AI Governance Research"

    reloaded = await store.get_project("ai_governance")
    assert reloaded.name == "AI Governance Research"


@pytest.mark.asyncio
async def test_rename_project_rejects_empty_name(tmp_path):
    store = _store(tmp_path)
    await store.create_project("x", "x", tmp_path / "x")
    with pytest.raises(ValueError, match="empty"):
        await store.rename_project("x", "   ")


@pytest.mark.asyncio
async def test_rename_project_unknown_slug_raises(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="No project named"):
        await store.rename_project("missing", "New name")


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
async def test_open_questions_dedup_append(tmp_path):
    store = _store(tmp_path)
    await store.update_summary("p", open_questions=["scope of Annex III?"])
    await store.update_summary("p", open_questions=["scope of Annex III?", "who owns the EU filing?"])

    summary = await store.get_summary("p")

    assert summary.open_questions == ["scope of Annex III?", "who owns the EU filing?"]


@pytest.mark.asyncio
async def test_current_state_truncated_at_max_chars(tmp_path):
    store = _store(tmp_path, summary_max_chars=10)
    await store.update_summary("p", current_state="this is definitely longer than ten characters")

    summary = await store.get_summary("p")

    assert len(summary.current_state) == 10


@pytest.mark.asyncio
async def test_open_questions_capped_keeping_most_recent(tmp_path):
    store = _store(tmp_path, max_list_items=3)
    await store.update_summary("p", open_questions=["a", "b", "c", "d", "e"])

    summary = await store.get_summary("p")

    assert summary.open_questions == ["c", "d", "e"]


@pytest.mark.asyncio
async def test_render_includes_all_populated_fields(tmp_path):
    store = _store(tmp_path)
    await store.update_summary("p", current_state="researching EU AI Act", open_questions=["scope of Annex III?"])

    rendered = (await store.get_summary("p")).render()

    assert "researching EU AI Act" in rendered
    assert "scope of Annex III?" in rendered


@pytest.mark.asyncio
async def test_outputs_field_no_longer_exists(tmp_path):
    """Regression guard: `outputs` was retired (folded into current_state,
    see project_store.py's module docstring) -- a stray `outputs` key in
    an old summary.json must be silently ignored, never crash, and never
    resurrect an `outputs` attribute on ProjectSummary."""
    store = _store(tmp_path)
    summary_file = tmp_path / "project_meta" / "p" / "summary.json"
    summary_file.parent.mkdir(parents=True)
    summary_file.write_text('{"current_state": "x", "outputs": ["stale.pdf -- old"], "open_questions": []}', encoding="utf-8")

    summary = await store.get_summary("p")

    assert summary.current_state == "x"
    assert not hasattr(summary, "outputs")


@pytest.mark.asyncio
async def test_role_is_replaced_not_appended(tmp_path):
    store = _store(tmp_path)
    await store.update_summary("p", role="first role")
    await store.update_summary("p", role="second role")

    summary = await store.get_summary("p")

    assert summary.role == "second role"
    assert "first role" not in summary.role


@pytest.mark.asyncio
async def test_role_truncated_at_max_chars(tmp_path):
    store = _store(tmp_path, summary_max_chars=10)
    await store.update_summary("p", role="this is definitely longer than ten characters")

    summary = await store.get_summary("p")

    assert len(summary.role) == 10


@pytest.mark.asyncio
async def test_role_persists_independently_of_current_state(tmp_path):
    store = _store(tmp_path)
    await store.update_summary("p", role="Act as a compliance assistant")
    await store.update_summary("p", current_state="drafting the first report")

    summary = await store.get_summary("p")

    assert summary.role == "Act as a compliance assistant"
    assert summary.current_state == "drafting the first report"


@pytest.mark.asyncio
async def test_render_puts_role_first(tmp_path):
    store = _store(tmp_path)
    await store.update_summary("p", role="Act as X", current_state="doing Y")

    rendered = (await store.get_summary("p")).render()

    assert rendered.index("Act as X") < rendered.index("doing Y")


@pytest.mark.asyncio
async def test_memory_store_for_returns_a_usable_independent_store(tmp_path):
    store = _store(tmp_path)
    await store.create_project("p", "p", tmp_path / "p")

    memory_a = store.memory_store_for("p")
    await memory_a.add_fact("remembered fact")

    # A fresh call to memory_store_for() must see the same underlying
    # file -- it's constructed on demand, not cached, so this also
    # verifies it isn't accidentally pointed at a per-call tmp location.
    memory_b = store.memory_store_for("p")
    facts = await memory_b.search_facts("")
    assert [f.content for f in facts] == ["remembered fact"]


@pytest.mark.asyncio
async def test_memory_store_for_is_isolated_per_project(tmp_path):
    store = _store(tmp_path)
    await store.memory_store_for("a").add_fact("fact for a")
    await store.memory_store_for("b").add_fact("fact for b")

    facts_a = await store.memory_store_for("a").search_facts("")
    facts_b = await store.memory_store_for("b").search_facts("")

    assert [f.content for f in facts_a] == ["fact for a"]
    assert [f.content for f in facts_b] == ["fact for b"]


@pytest.mark.asyncio
async def test_new_project_starts_with_no_enabled_tools(tmp_path):
    store = _store(tmp_path)
    project = await store.create_project("p", "p", tmp_path / "p")

    assert project.enabled_tools == []
    assert (await store.get_project("p")).enabled_tools == []


@pytest.mark.asyncio
async def test_set_enabled_tools_replaces_wholesale_and_persists(tmp_path):
    store = _store(tmp_path)
    await store.create_project("p", "p", tmp_path / "p")

    await store.set_enabled_tools("p", ["mcp_example_*"])
    updated = await store.set_enabled_tools("p", ["mcp_example_*", "make_pptx"])

    assert updated.enabled_tools == ["mcp_example_*", "make_pptx"]
    assert (await store.get_project("p")).enabled_tools == ["mcp_example_*", "make_pptx"]


@pytest.mark.asyncio
async def test_set_enabled_tools_unknown_slug_raises(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="No project named"):
        await store.set_enabled_tools("missing", ["mcp_example_*"])
