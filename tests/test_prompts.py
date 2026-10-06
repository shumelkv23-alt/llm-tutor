"""Тесты текстов промптов (правила, а не формулировки)."""

from llm_tutor.llm.prompts import (
    TUTOR_NO_MATCH_SYSTEM_PROMPT,
    TUTOR_NO_MATERIAL_SYSTEM_PROMPT,
    TUTOR_SYSTEM_PROMPT,
    tutor_system_prompt,
)

NO_FRAGMENTS_PROMPTS = (TUTOR_NO_MATCH_SYSTEM_PROMPT, TUTOR_NO_MATERIAL_SYSTEM_PROMPT)


def test_no_fragments_prompts_allow_answering() -> None:
    """Вопрос рядом с курсом должен получать ответ, а не отказ."""
    for prompt in NO_FRAGMENTS_PROMPTS:
        assert "по своим знаниям" in prompt


def test_no_fragments_prompts_mark_provenance_not_scope() -> None:
    """Промах поиска — не повод объявлять тему лежащей вне курса.

    Поиск ключевой, а материал англоязычный: русский вопрос про groupby
    промахивается, оставаясь при этом вопросом по теме.
    """
    for prompt in NO_FRAGMENTS_PROMPTS:
        assert "не из материалов курса" in prompt
        assert "за пределами темы" not in prompt  # регрессия Среза 8


def test_no_fragments_prompts_forbid_inventing_course_facts() -> None:
    """Нельзя приписывать курсу то, чего во фрагментах не было."""
    for prompt in NO_FRAGMENTS_PROMPTS:
        assert "в курсе сказано" in prompt  # именно это и запрещено


def test_no_match_prompt_asks_to_rephrase() -> None:
    """Материал есть, но не совпал — надо предложить термины из материала."""
    assert "добавить слова из материала" in TUTOR_NO_MATCH_SYSTEM_PROMPT


def test_tutor_system_prompt_selects_base_by_material_state() -> None:
    assert TUTOR_SYSTEM_PROMPT in tutor_system_prompt(0, material="found")
    assert TUTOR_NO_MATCH_SYSTEM_PROMPT in tutor_system_prompt(0, material="no_match")
    assert TUTOR_NO_MATERIAL_SYSTEM_PROMPT in tutor_system_prompt(0, material="empty")
