"""Тесты анкеты холодного старта (Срез 4.7)."""

import pytest

from llm_tutor.course.seed import load_seed
from llm_tutor.db import repos
from llm_tutor.student import beta, self_report, survey

EXPERIENCE = survey.EXPERIENCE_KEY
GOAL = survey.GOAL_KEY
TIME_BUDGET = survey.TIME_BUDGET_KEY


def test_apply_answers_writes_facts(conn, settings) -> None:
    load_seed(conn)

    survey.apply_answers(conn, {EXPERIENCE: 1, TIME_BUDGET: 2}, now=0.0, settings=settings)

    assert repos.get_fact(conn, EXPERIENCE) == "Python знаю, pandas — нет"
    assert repos.get_fact(conn, TIME_BUDGET) == "3–7 часов"


def test_apply_answers_sets_goal_concept(conn, settings) -> None:
    load_seed(conn)

    survey.apply_answers(conn, {GOAL: 2}, now=0.0, settings=settings)

    assert repos.get_fact(conn, GOAL) == "Пройти тему 1 целиком"
    assert repos.get_fact(conn, survey.GOAL_CONCEPT_KEY) == "churn_eda_case"


def test_goal_concept_not_written_without_goal_answer(conn, settings) -> None:
    load_seed(conn)

    survey.apply_answers(conn, {EXPERIENCE: 0}, now=0.0, settings=settings)

    assert repos.get_fact(conn, survey.GOAL_CONCEPT_KEY) is None


def test_experience_prior_raises_foundation(conn, settings) -> None:
    """«Уверенно работаю с pandas» поднимает фундаментальные концепты."""
    load_seed(conn)

    survey.apply_answers(conn, {EXPERIENCE: 3}, now=0.0, settings=settings)

    assert beta.estimate(conn, "pandas_dataframe", now=0.0, settings=settings).mean > 0.5


def test_beginner_prior_lowers_python_basics(conn, settings) -> None:
    """«На Python не писал» опускает базу ниже априора и блокирует зависимые."""
    load_seed(conn)

    survey.apply_answers(conn, {EXPERIENCE: 0}, now=0.0, settings=settings)

    assert beta.estimate(conn, "python_basics", now=0.0, settings=settings).mean < 0.5


def test_prior_is_weak_evidence(conn, settings) -> None:
    """Самооценка не должна перевешивать реальные задания (малый вес)."""
    load_seed(conn)

    survey.apply_answers(conn, {EXPERIENCE: 3}, now=0.0, settings=settings)

    mastery = beta.estimate(conn, "pandas_dataframe", now=0.0, settings=settings)
    assert mastery.alpha <= 1.0 + settings.self_evidence_weight + 1e-9


def test_prior_events_are_labelled_as_self(conn, settings) -> None:
    load_seed(conn)

    survey.apply_answers(conn, {EXPERIENCE: 1}, now=0.0, settings=settings)

    events = repos.get_events(conn)
    assert events
    assert {event.source for event in events} == {"self"}
    assert all(event.weight == settings.self_evidence_weight for event in events)


def test_invalid_option_index_raises(conn, settings) -> None:
    load_seed(conn)

    with pytest.raises(ValueError, match="Нет варианта"):
        survey.apply_answers(conn, {EXPERIENCE: 99}, now=0.0, settings=settings)


def test_unknown_question_key_raises(conn, settings) -> None:
    load_seed(conn)

    with pytest.raises(KeyError):
        survey.apply_answers(conn, {"favourite_food": 0}, now=0.0, settings=settings)


def test_reapplying_survey_does_not_double_prior(conn, settings) -> None:
    """Повторный проход анкеты не должен накручивать самооценку дважды."""
    load_seed(conn)
    survey.apply_answers(conn, {EXPERIENCE: 3}, now=0.0, settings=settings)
    after_first = repos.get_mastery(conn, "pandas_dataframe")["alpha"]

    survey.apply_answers(conn, {EXPERIENCE: 3}, now=0.0, settings=settings)

    assert repos.get_mastery(conn, "pandas_dataframe")["alpha"] == after_first


def test_is_completed_reflects_experience_fact(conn, settings) -> None:
    load_seed(conn)
    assert survey.is_completed(conn) is False

    survey.apply_answers(conn, {EXPERIENCE: 2}, now=0.0, settings=settings)

    assert survey.is_completed(conn) is True


def test_onboarding_texts_exist() -> None:
    """Вход в курс объясняет, что будет происходить."""
    from llm_tutor.bot import survey as bot_survey

    assert "спрошу пару вопросов" in bot_survey.INTRO_TEXT
    assert "соберу маршрут" in bot_survey.INTRO_TEXT


def test_self_evidence_moves_mastery_weakly(conn, settings) -> None:
    """Самооценка — слабое свидетельство: владение растёт, но не до порога."""
    load_seed(conn)  # событие ссылается на концепт — он должен быть в графе
    self_report.apply(conn, "read_csv", correct=True, now=1.0, settings=settings)

    mastery = beta.estimate(conn, "read_csv", now=1.0, settings=settings)
    assert mastery.mean > 0.5
    assert mastery.mean < settings.mastery_verify_threshold
    assert [event.source for event in repos.get_events(conn)] == ["self"]


def test_survey_prior_uses_shared_self_evidence(conn, settings) -> None:
    """Анкета пишет тот же тип свидетельства, что и «я это знаю»."""
    load_seed(conn)
    survey.apply_answers(conn, {survey.EXPERIENCE_KEY: 1}, now=1.0, settings=settings)

    assert {event.source for event in repos.get_events(conn)} == {"self"}
