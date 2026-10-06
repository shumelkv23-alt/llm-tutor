"""Тесты текстов промптов (правила, а не формулировки)."""

from llm_tutor.llm.prompts import TUTOR_NO_MATERIAL_SYSTEM_PROMPT, tutor_system_prompt


def test_no_material_prompt_allows_answering_but_marks_the_border() -> None:
    """Вопрос рядом с курсом должен получать ответ, а не отказ."""
    text = TUTOR_NO_MATERIAL_SYSTEM_PROMPT

    assert "за пределами" in text  # пометка границы
    assert "по своим знаниям" in text  # ответ разрешён


def test_no_material_prompt_forbids_passing_knowledge_as_course_material() -> None:
    """Ответ модели не должен выглядеть как материал курса."""
    text = TUTOR_NO_MATERIAL_SYSTEM_PROMPT.lower()

    assert "не приписывай курсу" in text or "не выдавай" in text


def test_tutor_system_prompt_picks_no_material_rules() -> None:
    assert TUTOR_NO_MATERIAL_SYSTEM_PROMPT in tutor_system_prompt(0, has_material=False)
