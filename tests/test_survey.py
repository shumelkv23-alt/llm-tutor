"""Тесты анкеты холодного старта (Срез 19: пять тематических вопросов)."""

import pytest

from llm_tutor.course.graph import CourseGraph
from llm_tutor.course.seed import load_seed
from llm_tutor.db import repos
from llm_tutor.student import beta, self_report, survey

FIRST = survey.SURVEY_QUESTIONS[0]
FIRST_BLOCK_NODES = survey.BLOCKS[0][2]


def test_survey_has_five_topical_questions() -> None:
    """Пять вопросов, у каждого четыре градации самооценки."""
    assert len(survey.SURVEY_QUESTIONS) == 5
    assert all(len(question.options) == 4 for question in survey.SURVEY_QUESTIONS)


def test_survey_blocks_cover_every_node(conn) -> None:
    """Блоки накрывают все узлы курса: априор достаётся каждому."""
    load_seed(conn)
    covered = {concept for _, _, concepts in survey.BLOCKS for concept in concepts}

    assert covered == set(CourseGraph.load(conn).node_ids)


def test_apply_answers_writes_facts(conn, settings) -> None:
    load_seed(conn)

    survey.apply_answers(conn, {FIRST.key: 1}, now=0.0, settings=settings)

    assert repos.get_fact(conn, FIRST.key) == survey.SELF_LEVELS[1]


def test_prior_uses_levels(conn, settings) -> None:
    """Индекс ответа задаёт силу априора, а не только «знаю / не знаю»."""
    load_seed(conn)

    survey.apply_answers(conn, {FIRST.key: 1}, now=0.0, settings=settings)

    assert repos.get_events(conn)[0].result == pytest.approx(survey.PRIOR_LEVELS[1])


def test_prior_covers_every_node_of_block(conn, settings) -> None:
    """Один ответ накрывает весь блок узлов."""
    load_seed(conn)

    survey.apply_answers(conn, {FIRST.key: 3}, now=0.0, settings=settings)

    assert {event.concept_id for event in repos.get_events(conn)} == set(FIRST_BLOCK_NODES)


def test_prior_events_are_labelled_as_self(conn, settings) -> None:
    """Анкета пишет тот же тип свидетельства, что и «я это знаю»."""
    load_seed(conn)

    survey.apply_answers(conn, {FIRST.key: 1}, now=0.0, settings=settings)

    assert {event.source for event in repos.get_events(conn)} == {"self"}


def test_invalid_option_index_raises(conn, settings) -> None:
    load_seed(conn)

    with pytest.raises(ValueError):
        survey.apply_answers(conn, {FIRST.key: 99}, now=0.0, settings=settings)


def test_unknown_question_key_raises(conn, settings) -> None:
    load_seed(conn)

    with pytest.raises(KeyError):
        survey.apply_answers(conn, {"нет_такого_блока": 0}, now=0.0, settings=settings)


def test_reapplying_survey_does_not_double_prior(conn, settings) -> None:
    """Повторное прохождение факты перезаписывает, априор — нет."""
    load_seed(conn)
    survey.apply_answers(conn, {FIRST.key: 3}, now=0.0, settings=settings)
    before = len(repos.get_events(conn))

    survey.apply_answers(conn, {FIRST.key: 3}, now=1.0, settings=settings)

    assert len(repos.get_events(conn)) == before


def test_is_completed_needs_every_answer(conn, settings) -> None:
    """Анкета считается пройденной только целиком."""
    load_seed(conn)

    assert survey.is_completed(conn) is False

    survey.apply_answers(conn, {FIRST.key: 2}, now=0.0, settings=settings)
    assert survey.is_completed(conn) is False


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
    survey.apply_answers(conn, {FIRST.key: 1}, now=1.0, settings=settings)

    assert {event.source for event in repos.get_events(conn)} == {"self"}


def test_onboarding_texts_exist() -> None:
    """Вход в курс объясняет, что будет происходить."""
    from llm_tutor.bot import start

    assert len([line for line in start.INTRO_TEXT.splitlines() if line.strip()]) == 3
    assert start.START_LABEL in start.INTRO_TEXT
