"""Статусы тем, переход и старт урока — ``core.lesson`` (Срез 49).

Раньше эти правила проверялись через бота (test_themes, test_bot_flows);
с удалением Telegram они проверяются напрямую.
"""

from web_fakes import ScriptedTutor

from llm_tutor.core import lesson, overview, turn
from llm_tutor.course.graph import CourseGraph
from llm_tutor.course.seed import load_seed
from llm_tutor.db import repos
from llm_tutor.schemas import Route, RouteStep, SessionState
from llm_tutor.student import survey


def _state(conn) -> SessionState:
    return repos.get_session_state(conn, repos.get_open_session(conn))


def _set_state(conn, **patch) -> None:
    session_id = repos.ensure_open_session(conn, now=1.0)
    state = repos.get_session_state(conn, session_id)
    repos.update_session_state(conn, session_id, state.model_copy(update=patch))


def _gated_node(graph: CourseGraph) -> str:
    return next(n for n in graph.topo_order() if graph.hard_prerequisites(n))


# --- статусы тем ---


def test_node_status_marks_current(conn, settings) -> None:
    load_seed(conn)
    graph = CourseGraph.load(conn)
    state = SessionState(current_node_id="groupby")

    assert lesson.node_status(conn, graph, "groupby", state, now=0.0, settings=settings) == "current"


def test_node_status_ahead_then_available(conn, settings) -> None:
    """Узел впереди, пока пререквизиты открыты; доступен, когда закрыты."""
    load_seed(conn)
    graph = CourseGraph.load(conn)
    node = _gated_node(graph)
    state = SessionState()

    assert lesson.node_status(conn, graph, node, state, now=0.0, settings=settings) == "ahead"
    for prereq in graph.hard_prerequisites(node):
        repos.upsert_mastery(conn, prereq, alpha=38.0, beta=2.0, last_seen=0.0, next_review=1e9)
    assert lesson.node_status(conn, graph, node, state, now=0.0, settings=settings) == "available"


def test_node_status_reads_closure_from_route_snapshot(conn, settings) -> None:
    """Узел, закрытый серией в снимке маршрута, — closed и без мастерства."""
    load_seed(conn)
    graph = CourseGraph.load(conn)
    node = _gated_node(graph)
    state = SessionState(route=Route(steps=[RouteStep(concept_id=node, mode="full", status="closed")]))

    assert lesson.node_status(conn, graph, node, state, now=0.0, settings=settings) == "closed"


def test_node_status_prereq_closed_by_route_opens_dependent(conn, settings) -> None:
    load_seed(conn)
    graph = CourseGraph.load(conn)
    node = _gated_node(graph)
    closed = [RouteStep(concept_id=p, mode="full", status="closed") for p in graph.hard_prerequisites(node)]

    status = lesson.node_status(conn, graph, node, SessionState(route=Route(steps=closed)), now=0.0, settings=settings)

    assert status == "available"


def test_node_status_claimed_and_opens_dependent(conn, settings) -> None:
    """Знакомое по анкете — claimed и, как закрытое, открывает зависимые узлы."""
    load_seed(conn)
    graph = CourseGraph.load(conn)
    node = _gated_node(graph)
    claimed = [RouteStep(concept_id=p, mode="full", status="claimed") for p in graph.hard_prerequisites(node)]
    state = SessionState(route=Route(steps=claimed))
    prereq = claimed[0].concept_id

    assert lesson.node_status(conn, graph, prereq, state, now=0.0, settings=settings) == "claimed"
    assert lesson.node_status(conn, graph, node, state, now=0.0, settings=settings) == "available"


def test_topic_list_does_not_open_session(conn, settings) -> None:
    load_seed(conn)

    overview.topic_rows(conn, settings=settings)

    assert repos.get_open_session(conn) is None


# --- переход к теме ---


def test_switch_to_other_theme_starts_over(conn, settings) -> None:
    """Новая тема: заход, серия, подсказки, задание и проверочный проход — с нуля."""
    load_seed(conn)
    _set_state(
        conn,
        current_node_id="python_basics",
        pending_item_id=6,
        lesson_item_ids=[1],
        node_streak=1,
        hint_level=2,
        mode="verify",
        verify_item_ids=[9],
        phase="check",
    )

    text = lesson.switch_node(conn, "groupby", now=2.0, settings=settings)

    state = _state(conn)
    assert state.current_node_id == "groupby"
    assert (state.pending_item_id, state.lesson_item_ids, state.verify_item_ids) == (None, [], [])
    assert (state.node_streak, state.hint_level, state.mode, state.phase) == (0, 0, None, "explain")
    name = CourseGraph.load(conn).concept("groupby").name
    assert text == lesson.SWITCH_TEMPLATE.format(name=name)


def test_reselecting_same_theme_keeps_progress(conn, settings) -> None:
    load_seed(conn)
    _set_state(conn, current_node_id="groupby", pending_item_id=6, lesson_item_ids=[1], node_streak=1, hint_level=2)

    lesson.switch_node(conn, "groupby", now=2.0, settings=settings)

    state = _state(conn)
    assert (state.pending_item_id, state.lesson_item_ids) == (6, [1])
    assert (state.node_streak, state.hint_level) == (1, 2)


# --- старт урока после анкеты ---


async def _finish(conn, settings, answers: list[int]):
    progress = survey.Progress()
    for index in answers:
        progress = progress.answer(index)
    assert progress.next_key() is None
    survey.apply_answers(conn, progress.final_answers(), level=progress.level, settings=settings)
    return await lesson.begin_lesson(conn, ScriptedTutor("Объясняю."), "m", settings=settings)


def _said(reply) -> str:
    return f"{reply.text}\n{reply.tail or ''}"


async def test_lesson_starts_where_steps_list_says(conn, settings) -> None:
    """Первый из «Ближайших шагов» — тот узел, с которого урок реально начался."""
    load_seed(conn)
    start, reply = await _finish(conn, settings, [survey.LEVEL_CONFIDENT, 1, 0])

    assert reply is not None
    assert start.upcoming[0].concept_id == start.first
    assert _state(conn).current_node_id == start.first
    assert start.first not in survey.claimed_concepts(conn)
    assert not start.starts_with_check


async def test_all_confident_starts_with_check(conn, settings) -> None:
    load_seed(conn)
    confident = survey.CONFIDENT_INDEX
    start, reply = await _finish(conn, settings, [survey.LEVEL_CONFIDENT, confident, confident])

    assert start.starts_with_check
    assert _state(conn).mode == "verify"
    assert reply.item_id is not None and "Проверка" in _said(reply)
    # Подводка к проверке звучит один раз и без счётчика «Осталось».
    assert _said(reply).count("пара быстрых вопросов") <= 1
    assert "Осталось" not in _said(reply)


async def test_practice_before_survey_does_not_disable_claimed(conn, settings) -> None:
    """Задание до анкеты (например, из перенесённой БД бота) не отменяет «знакомое»."""
    load_seed(conn)
    turn.start_practice_reply(conn, now=1.0, settings=settings)
    confident = survey.CONFIDENT_INDEX

    _, reply = await _finish(conn, settings, [survey.LEVEL_CONFIDENT, confident, confident])

    state = _state(conn)
    assert state.mode == "verify"
    assert "Проверка" in _said(reply)
    assert any(step.status == "claimed" for step in state.route.steps)
