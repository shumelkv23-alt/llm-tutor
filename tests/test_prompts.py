"""Тесты текстов промптов (правила, а не формулировки)."""

from course_fixtures import T2

from llm_tutor.schemas import Route, RouteStep, Topic

from llm_tutor.llm.prompts import (
    TUTOR_NO_MATCH_SYSTEM_PROMPT,
    format_route_block,
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


# --- блок маршрута и правила ведения (Срез 10) ---


def test_route_block_lists_progress_and_current_node() -> None:
    route = Route(
        goal_concept_id="goal",
        steps=[
            RouteStep(concept_id="a", mode="full", status="closed"),
            RouteStep(concept_id="b", mode="full", status="current"),
            RouteStep(concept_id="goal", mode="full", status="ahead"),
        ],
    )

    block = format_route_block(route, names={"a": "Первый", "b": "Второй", "goal": "Цель"})

    assert "закрыто 1 из 3" in block.lower()
    assert "Второй" in block  # текущий узел назван
    assert "Цель" in block


def test_stuck_rule_is_in_tutor_prompt() -> None:
    """Модель должна знать, как сообщить, что ученик застрял."""
    text = tutor_system_prompt(0)

    assert "student_stuck" in text


def test_contract_includes_stuck_flag() -> None:
    assert '"student_stuck"' in tutor_system_prompt(0)


def test_phase_is_rendered_in_prompt() -> None:
    assert "practice" in tutor_system_prompt(0, phase="practice")


# --- правила лаконичности (Срез 11) ---


def test_tutor_prompt_demands_brevity_for_all_material_states() -> None:
    """Правила лаконичности действуют независимо от наличия материала курса."""
    for material in ("found", "no_match", "empty"):
        prompt = tutor_system_prompt(0, material=material)
        assert "2–4 предложения" in prompt
        assert "без вступлений" in prompt.lower()
        assert "Одна мысль за сообщение" in prompt


def test_tutor_prompt_mentions_close_topic_flag() -> None:
    """Промпт знает про флаг просьбы закрыть тему."""
    assert "wants_close_topic" in tutor_system_prompt(0, material="found")


# --- ведомый урок (Срез 21) ---


def test_tutor_prompt_explains_before_asking() -> None:
    """Сократовский диалог идёт ПОСЛЕ объяснения, а не вместо него."""
    text = tutor_system_prompt(0, material="found")

    assert "сначала" in text.lower() and "объясни" in text.lower()
    assert "пример" in text.lower()
    assert "зачем" in text.lower()


def test_tutor_rules_tie_depth_to_self_assessment() -> None:
    assert "по самооценке" in tutor_system_prompt(0)


def test_confident_rule_has_no_false_premise() -> None:
    """«Уверенно» в промпте не утверждает провал проверки — его могло и не быть."""
    assert "не подтвердил проверку" not in tutor_system_prompt(0)


# --- модуль в промпте (срез 27) ---


def test_prompt_bases_have_no_hardcoded_module() -> None:
    for base in (TUTOR_SYSTEM_PROMPT, *NO_FRAGMENTS_PROMPTS):
        assert "Pandas / EDA" not in base and "groupby" not in base


def test_prompt_names_current_module() -> None:
    topic = Topic(number=2, title="Визуальный анализ", intro="…", survey=T2)

    assert "модуль 2 «Визуальный анализ»" in tutor_system_prompt(0, topic=topic)
    assert "mlcourse.ai" in tutor_system_prompt(0)
