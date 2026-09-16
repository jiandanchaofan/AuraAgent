"""Unit tests for parse_skill_manifest — pure parsing logic, no
subprocess involved (see test_skill_loader.py for the real
subprocess-invocation tests).
"""
from __future__ import annotations

import pytest

from skills.skill_schema import SkillManifestError, parse_skill_manifest


def _write_skill(tmp_path, name="my_skill", front_matter="", with_run_py=True):
    skill_dir = tmp_path / name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(front_matter, encoding="utf-8")
    if with_run_py:
        (skill_dir / "run.py").write_text("print('hi')", encoding="utf-8")
    return skill_dir


def test_parses_valid_manifest_with_input_schema(tmp_path):
    skill_dir = _write_skill(
        tmp_path,
        front_matter=(
            "---\n"
            "name: reverse\n"
            "description: Reverses text\n"
            "input_schema:\n"
            "  type: object\n"
            "  properties:\n"
            "    text:\n"
            "      type: string\n"
            "  required:\n"
            "    - text\n"
            "---\n"
            "# reverse\n"
        ),
    )

    manifest = parse_skill_manifest(skill_dir)

    assert manifest.name == "reverse"
    assert manifest.description == "Reverses text"
    assert manifest.input_schema == {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
    assert manifest.entrypoint == skill_dir / "run.py"


def test_input_schema_defaults_to_empty_object_when_omitted(tmp_path):
    skill_dir = _write_skill(
        tmp_path, front_matter="---\nname: noargs\ndescription: takes nothing\n---\nbody\n"
    )

    manifest = parse_skill_manifest(skill_dir)

    assert manifest.input_schema == {"type": "object", "properties": {}}


def test_missing_skill_md_raises(tmp_path):
    skill_dir = tmp_path / "empty_skill"
    skill_dir.mkdir()

    with pytest.raises(SkillManifestError):
        parse_skill_manifest(skill_dir)


def test_missing_front_matter_delimiter_raises(tmp_path):
    skill_dir = _write_skill(tmp_path, front_matter="name: no_dashes\ndescription: oops\n")

    with pytest.raises(SkillManifestError):
        parse_skill_manifest(skill_dir)


def test_unclosed_front_matter_raises(tmp_path):
    skill_dir = _write_skill(tmp_path, front_matter="---\nname: unclosed\ndescription: oops\n")

    with pytest.raises(SkillManifestError):
        parse_skill_manifest(skill_dir)


def test_invalid_yaml_raises(tmp_path):
    skill_dir = _write_skill(tmp_path, front_matter="---\nname: [unbalanced\n---\nbody\n")

    with pytest.raises(SkillManifestError):
        parse_skill_manifest(skill_dir)


def test_missing_required_field_raises(tmp_path):
    skill_dir = _write_skill(tmp_path, front_matter="---\nname: only_name\n---\nbody\n")

    with pytest.raises(SkillManifestError):
        parse_skill_manifest(skill_dir)


def test_missing_run_py_raises(tmp_path):
    skill_dir = _write_skill(
        tmp_path,
        front_matter="---\nname: no_entrypoint\ndescription: oops\n---\nbody\n",
        with_run_py=False,
    )

    with pytest.raises(SkillManifestError):
        parse_skill_manifest(skill_dir)
