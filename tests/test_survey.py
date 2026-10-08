"""Тесты анкеты холодного старта (срез 22: адаптивная анкета)."""

import pytest

from llm_tutor.course.graph import CourseGraph
from llm_tutor.course.seed import load_seed
from llm_tutor.db import repos
from llm_tutor.student import beta, self_report, survey
from llm_tutor.student.survey import Progress

FIRST = survey.BLOCKS[0]
BLOCK_KEYS = [block.key for block in survey.BLOCKS]


# --- состав анкеты ---


def test_five_blocks_and_four_levels() -> None:
    assert len(survey.BLOCKS) == 5
    assert len(survey.SELF_LEVELS) == len(survey.PRIOR_LEVELS) == 4


def test_level_labels_are_short_and_neutral() -> None:
    """Подписи влезают в сетку 2×2 и не содержат «(а)»."""
    assert all("(" not in label and len(label) <= 14 for label in survey.SELF_LEVELS)


def test_confident_label_is_kept_verbatim() -> None:
    """По подписи «Уверенно» узнаются заявленные блоки — менять её нельзя."""
    assert survey.SELF_LEVELS[survey.CONFIDENT_INDEX] == "Уверенно"


def test_survey_blocks_cover_every_node(conn) -> None:
    """Блоки накрывают все узлы курса: априор достаётся каждому."""
    load_seed(conn)
    covered = {concept for block in survey.BLOCKS for concept in block.concepts}

    assert covered == set(CourseGraph.load(conn).node_ids)


def test_every_block_has_question_and_example() -> None:
    assert all(block.question.endswith("?") and block.example for block in survey.BLOCKS)


# --- ветвление ---


def _walk(progress: Progress, index: int) -> tuple[Progress, list[str]]:
    """Отвечает ``index`` на каждый вопрос по блокам, пока анкета не кончится."""
    asked = []
    while (key := progress.next_key()) is not None:
        asked.append(key)
        progress = progress.answer(index)
    return progress, asked


def test_survey_starts_with_level_question() -> None:
    assert Progress().next_key() == survey.LEVEL_KEY


def test_from_scratch_ends_after_one_answer() -> None:
    progress = Progress().answer(survey.LEVEL_FROM_SCRATCH)

    assert progress.next_key() is None
    assert progress.final_answers() == {key: survey.UNKNOWN_INDEX for key in BLOCK_KEYS}


def test_confident_asks_only_last_two_blocks() -> None:
    progress, asked = _walk(Progress().answer(survey.LEVEL_CONFIDENT), 1)

    assert asked == ["block_selection", "block_analysis"]
    final = progress.final_answers()
    assert all(final[key] == survey.CONFIDENT_INDEX for key in survey.ASSUMED_BY_CONFIDENT)
    assert final["block_selection"] == final["block_analysis"] == 1


def test_some_asks_every_block_in_order() -> None:
    _, asked = _walk(Progress().answer(survey.LEVEL_SOME), 2)

    assert asked == BLOCK_KEYS


@pytest.mark.parametrize("foundation", survey.FOUNDATION)
def test_unknown_foundation_ends_survey(foundation: str) -> None:
    """«Впервые вижу» на фундаменте — дальше всё незнакомо, анкета кончилась."""
    progress = Progress().answer(survey.LEVEL_SOME)
    while progress.next_key() != foundation:
        progress = progress.answer(2)
    progress = progress.answer(survey.UNKNOWN_INDEX)

    assert progress.next_key() is None
    final = progress.final_answers()
    after = BLOCK_KEYS[BLOCK_KEYS.index(foundation) + 1 :]
    assert all(final[key] == survey.UNKNOWN_INDEX for key in after)


def test_unknown_outside_foundation_keeps_asking() -> None:
    """Блоки 3–5 друг от друга не зависят: «Впервые вижу» анкету не обрывает."""
    progress = Progress().answer(survey.LEVEL_SOME).answer(2).answer(2)
    progress = progress.answer(survey.UNKNOWN_INDEX)  # block_loading

    assert progress.next_key() == "block_selection"


def test_back_removes_last_answer() -> None:
    progress = Progress().answer(survey.LEVEL_SOME).answer(3)

    back = progress.back()

    assert back.next_key() == "block_python"
    assert back.answers == {}
    assert back.back().next_key() == survey.LEVEL_KEY


def test_position_counts_block_questions_only() -> None:
    assert Progress().position() is None
    assert Progress().answer(survey.LEVEL_SOME).position() == (1, 5)
    assert Progress().answer(survey.LEVEL_CONFIDENT).answer(1).position() == (2, 2)


def test_answer_out_of_range_raises() -> None:
    with pytest.raises(ValueError):
        Progress().answer(len(survey.LEVEL_OPTIONS))


def test_answer_after_end_raises() -> None:
    with pytest.raises(ValueError):
        Progress().answer(survey.LEVEL_FROM_SCRATCH).answer(0)


def test_progress_is_immutable() -> None:
    progress = Progress()
    progress.answer(survey.LEVEL_SOME)

    assert progress.step == 0


# --- запись ---


def test_apply_answers_writes_facts(conn, settings) -> None:
    load_seed(conn)

    survey.apply_answers(conn, {FIRST.key: 1}, now=0.0, settings=settings)

    assert repos.get_fact(conn, FIRST.key) == survey.SELF_LEVELS[1]


def test_apply_answers_writes_level_fact(conn, settings) -> None:
    load_seed(conn)

    survey.apply_answers(
        conn, {FIRST.key: 1}, level=survey.LEVEL_SOME, now=0.0, settings=settings
    )

    assert repos.get_fact(conn, survey.LEVEL_KEY) == survey.LEVEL_OPTIONS[survey.LEVEL_SOME]


def test_prior_uses_levels(conn, settings) -> None:
    """Индекс ответа задаёт силу априора, а не только «знаю / не знаю»."""
    load_seed(conn)

    survey.apply_answers(conn, {FIRST.key: 1}, now=0.0, settings=settings)

    assert repos.get_events(conn)[0].result == pytest.approx(survey.PRIOR_LEVELS[1])


def test_prior_covers_every_node_of_block(conn, settings) -> None:
    """Один ответ накрывает весь блок узлов."""
    load_seed(conn)

    survey.apply_answers(conn, {FIRST.key: 3}, now=0.0, settings=settings)

    assert {event.concept_id for event in repos.get_events(conn)} == set(FIRST.concepts)


def test_level_fact_gives_no_prior(conn, settings) -> None:
    """Общий уровень — только факт: априор дают ответы по блокам."""
    load_seed(conn)

    survey.apply_answers(conn, {}, level=survey.LEVEL_CONFIDENT, now=0.0, settings=settings)

    assert repos.get_events(conn) == []


def test_prior_events_are_labelled_as_self(conn, settings) -> None:
    """Анкета пишет тот же тип свидетельства, что и «я это знаю»."""
    load_seed(conn)

    survey.apply_answers(conn, {FIRST.key: 1}, now=0.0, settings=settings)

    assert {event.source for event in repos.get_events(conn)} == {"self"}


def test_invalid_option_index_raises(conn, settings) -> None:
    load_seed(conn)

    with pytest.raises(ValueError):
        survey.apply_answers(conn, {FIRST.key: 99}, now=0.0, settings=settings)


def test_invalid_level_raises(conn, settings) -> None:
    load_seed(conn)

    with pytest.raises(ValueError):
        survey.apply_answers(conn, {}, level=99, now=0.0, settings=settings)


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

    survey.apply_answers(
        conn, Progress().answer(survey.LEVEL_FROM_SCRATCH).final_answers(), settings=settings
    )
    assert survey.is_completed(conn) is True


def test_self_evidence_moves_mastery_weakly(conn, settings) -> None:
    """Самооценка — слабое свидетельство: владение растёт, но не до порога."""
    load_seed(conn)  # событие ссылается на концепт — он должен быть в графе
    self_report.apply(conn, "read_csv", correct=True, now=1.0, settings=settings)

    mastery = beta.estimate(conn, "read_csv", now=1.0, settings=settings)
    assert mastery.mean > 0.5
    assert mastery.mean < settings.mastery_verify_threshold
    assert [event.source for event in repos.get_events(conn)] == ["self"]


def test_self_assessment_uses_block_titles(conn, settings) -> None:
    load_seed(conn)
    survey.apply_answers(conn, {FIRST.key: 2}, now=0.0, settings=settings)

    assert survey.self_assessment(conn) == {FIRST.title: survey.SELF_LEVELS[2]}


def test_block_level_of_concept(conn, settings) -> None:
    load_seed(conn)
    survey.apply_answers(conn, {"block_analysis": 1}, now=0.0, settings=settings)

    assert survey.block_level(conn, "groupby") == survey.SELF_LEVELS[1]
    assert survey.block_level(conn, "python_basics") is None
