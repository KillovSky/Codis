"""Testes para src.ia.actions (carregamento das Skills padrão)."""

from __future__ import annotations

from src.ia.actions import list_skills


def test_default_skills_loaded():
    assert len(list_skills()) == 15


def test_skill_ids_are_unique():
    ids = [s.id for s in list_skills()]
    assert len(ids) == len(set(ids))


def test_skills_have_prompts():
    for skill in list_skills():
        assert skill.system_prompt.strip()
        assert skill.display_name


def test_list_skills_is_stable():
    assert [s.id for s in list_skills()] == [s.id for s in list_skills()]
