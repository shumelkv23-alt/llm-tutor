"""Разминка между занятиями (срез 81) — на фикстуре tests/fixtures/lessons."""

from course_fixtures import correct_answer, load_lessons
from test_lesson_interface import _unpublish
from test_lesson_sections import _answer, _check, _enter, _pending, _wrong
from test_turn import _FakeTutor, _set_state, _state

from llm_tutor.core import guided
from llm_tutor.core import review as core_review
from llm_tutor.core import turn
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.schemas import ReviewRow
from llm_tutor.student import review

DAY = 86_400.0
LATER = 5.0  # ходы после закрытия занятия идут на секунды позже: сроки строк — 0.5


def _mark(conn, status: str, *concept_ids: str) -> None:
    route = _state(conn).route
    steps = [
        step.model_copy(update={"status": status, "closed_at": 0.1})
        if step.concept_id in concept_ids
        else step
        for step in route.steps
    ]
    _set_state(conn, route=route.model_copy(update={"steps": steps}))


def _make_due(conn, *concept_ids: str, step: int = 1) -> None:
    for concept_id in concept_ids:
        repos.upsert_review(conn, concept_id, step, 0.5)


async def _setup(conn, settings, *due: str):
    """Ученик в первом занятии; ``due`` закрыты и просрочены."""
    load_lessons(conn)
    await _enter(conn, settings)
    _mark(conn, "closed", *due)
    _make_due(conn, *due)


async def _finish_lesson(conn, settings):
    """Два раздела и итоговое — ответами; возвращает ответ, закрывший занятие."""
    for _ in range(2):
        _check(conn, settings)
        await _answer(conn, settings, correct_answer(_pending(conn)))
    return await _answer(conn, settings, correct_answer(_pending(conn)), now=4.0)


async def test_warmup_instead_of_lesson_start(conn, settings) -> None:
    await _setup(conn, settings, "l1_group")

    reply = await _finish_lesson(conn, settings)

    state = _state(conn)
    assert state.current_node_id == "l1_select"
    assert turn.WARMUP_NOTE.format(next="Выборка данных") in reply.text
    assert "закрыто" in reply.text  # заметка закрытия идёт перед разминкой
    assert reply.tail.startswith("🔁 Повторение · «Группировка» · 1 из 1")
    assert state.review.kind == "warmup" and reply.item_id == state.review.item_id
    assert state.lesson_parts == [] and state.pending_item_id is None
    assert reply.button is None


async def test_no_warmup_when_disabled(conn, settings) -> None:
    await _setup(conn, settings, "l1_group")
    review.set_enabled(conn, False)

    reply = await _finish_lesson(conn, settings)

    state = _state(conn)
    assert state.review is None and state.lesson_parts and reply.button is not None
    assert turn.WARMUP_NOTE.split("«")[0] not in reply.text


async def test_no_warmup_when_nothing_due(conn, settings) -> None:
    await _setup(conn, settings)

    await _finish_lesson(conn, settings)

    assert _state(conn).review is None and _state(conn).lesson_parts


async def test_no_warmup_at_course_end(conn, settings) -> None:
    load_lessons(conn)
    _unpublish(conn, 2)
    await _enter(conn, settings)
    await _finish_lesson(conn, settings)
    await _finish_lesson(conn, settings)
    _make_due(conn, "l1_frames")

    reply = await _finish_lesson(conn, settings)  # l1_group — последнее открытое

    assert turn.MODULES_SOON_NOTE in reply.text
    assert _state(conn).review is None and reply.tail is None


async def test_no_warmup_before_module_survey(conn, settings) -> None:
    load_lessons(conn)
    await _enter(conn, settings)
    await _finish_lesson(conn, settings)
    await _finish_lesson(conn, settings)
    _make_due(conn, "l1_frames")

    reply = await _finish_lesson(conn, settings)  # дальше — анкета модуля 2

    state = _state(conn)
    assert state.current_node_id is None and state.review is None
    assert reply.tail is None and "сначала пара вопросов" in reply.text.lower()


async def test_warmup_at_most_two(conn, settings) -> None:
    await _setup(conn, settings, "l1_group", "l2_hist", "l2_box")

    await _finish_lesson(conn, settings)

    series = _state(conn).review
    assert series.total == 2 and len(series.queue) == 1


async def test_lesson_starts_after_warmup(conn, settings) -> None:
    await _setup(conn, settings, "l1_group")
    await _finish_lesson(conn, settings)
    item = repos.get_item(conn, _state(conn).review.item_id)

    reply = await core_review.answer(
        conn, _FakeTutor("A\n---\nB"), "m", correct_answer(item), now=LATER, settings=settings
    )

    state = _state(conn)
    assert state.review is None
    assert "верно 1 из 1" in reply.text and "A" in reply.text
    assert state.current_node_id == "l1_select" and state.lesson_part == 1
    assert reply.button is not None and reply.button[0] == guided.NEXT_LABEL
    assert reply.tail is None


async def test_verify_pass_after_warmup(conn, settings) -> None:
    await _setup(conn, settings, "l1_group")
    _mark(conn, "claimed", "l1_select")
    await _finish_lesson(conn, settings)
    state = _state(conn)
    assert state.mode == "verify" and state.review is not None
    assert state.pending_item_id is None
    item = repos.get_item(conn, state.review.item_id)

    reply = await core_review.answer(
        conn, _FakeTutor(), "m", correct_answer(item), now=LATER, settings=settings
    )

    state = _state(conn)
    assert state.review is None and state.mode == "verify"
    assert state.pending_item_id is not None and reply.item_id == state.pending_item_id
    assert reply.tail.startswith("🔎 Проверка «Выборка данных»")


async def test_skip_last_warmup_item_starts_lesson(conn, settings) -> None:
    await _setup(conn, settings, "l1_group")
    await _finish_lesson(conn, settings)

    reply = await core_review.skip(conn, _FakeTutor("A\n---\nB"), "m", now=LATER, settings=settings)

    state = _state(conn)
    assert state.review is None and state.lesson_part == 1 and reply.button is not None
    assert repos.get_review_rows(conn)["l1_group"].due_at == LATER + DAY


async def test_stop_warmup_postpones_and_starts_lesson(conn, settings) -> None:
    await _setup(conn, settings, "l1_group", "l2_hist")
    await _finish_lesson(conn, settings)
    series = _state(conn).review
    assert series.total == 2

    reply = await core_review.stop(conn, _FakeTutor("A\n---\nB"), "m", now=LATER, settings=settings)

    state = _state(conn)
    rows = repos.get_review_rows(conn)
    assert state.review is None and state.lesson_part == 1 and reply.button is not None
    assert rows["l1_group"] == ReviewRow(concept_id="l1_group", step=1, due_at=LATER + DAY)
    assert rows["l2_hist"] == ReviewRow(concept_id="l2_hist", step=1, due_at=LATER + DAY)
    # В тот же день их не спросят снова.
    graph = CourseGraph.load(conn)
    assert review.due_lessons(conn, graph, state.route, now=LATER + 3_600, settings=settings) == []


async def test_stop_series_outside_warmup_only_summarizes(conn, settings) -> None:
    await _setup(conn, settings, "l1_group")
    await core_review.start(conn, _FakeTutor(), "m", kind="due", now=LATER, settings=settings)
    lesson_before = _state(conn).lesson_parts

    reply = await core_review.stop(conn, _FakeTutor(), "m", now=LATER, settings=settings)

    state = _state(conn)
    assert state.review is None and state.lesson_parts == lesson_before
    assert reply.text == core_review.REVIEW_DONE_EMPTY and reply.button is None
    assert repos.get_review_rows(conn)["l1_group"].due_at == 0.5  # «Закончить» не откладывает


async def test_warmup_wrong_answer_then_lesson(conn, settings) -> None:
    await _setup(conn, settings, "l1_group")
    await _finish_lesson(conn, settings)
    item = repos.get_item(conn, _state(conn).review.item_id)

    reply = await core_review.answer(
        conn, _FakeTutor("A\n---\nB"), "m", _wrong(item), now=LATER, settings=settings
    )

    assert "Освежить: раздел" in reply.text and "верно 0 из 1" in reply.text
    assert _state(conn).lesson_part == 1


# --- аудит среза 81 ---


async def test_warmup_does_not_ask_the_lesson_closed_this_turn(conn, settings) -> None:
    """Занятие, которое ученик только что закрыл заново, на разминку не идёт."""
    load_lessons(conn)
    await _enter(conn, settings)
    # l1_frames уже была на лестнице и просрочена, потом переоткрылась; закрываем её снова.
    _make_due(conn, "l1_frames")

    reply = await _finish_lesson(conn, settings)

    state = _state(conn)
    assert state.review is None, "разминка спросила только что закрытое занятие"
    assert state.lesson_part == 1 and reply.button is not None
    assert repos.get_review_rows(conn)["l1_frames"].due_at == 0.5  # строка не сброшена


async def test_warmup_other_due_lesson_still_asked_when_current_is_also_due(conn, settings) -> None:
    await _setup(conn, settings, "l1_group")
    _make_due(conn, "l1_frames")

    await _finish_lesson(conn, settings)

    assert _state(conn).review.node_id == "l1_group"
