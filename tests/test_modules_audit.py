"""Аудит среза 25: регрессии находок ревью маршрута по модулям."""

from course_fixtures import load_two_modules
from fakes import GradingTutor

from llm_tutor.bot.render import MAX_MESSAGE, render_plan
from llm_tutor.course.graph import CourseGraph
from llm_tutor.core.turn import (
    ROUTE_DONE_REPLY,
    handle_turn,
    resume_reply,
    start_practice_reply,
)
from llm_tutor.db import repos
from llm_tutor.schemas import Concept, Edge, Route, RouteStep, SessionState
from llm_tutor.student import route as route_mod


def _state(conn) -> SessionState:
    return repos.get_session_state(conn, repos.get_open_session(conn))


def _module_one_done_before_growth(conn) -> None:
    """Снимок ученика, прошедшего модуль 1 до появления модуля 2 (H1)."""
    load_two_modules(conn)
    for key in ("t02_level", "t02_basics", "t02_relations"):
        repos.set_fact(conn, key, "Знаю в теории", source="self")
    graph = CourseGraph.load(conn)
    steps = [
        RouteStep(concept_id=node, mode="full", status="closed")
        for node in graph.topo_order()
        if graph.topic_of(node) == 1
    ]
    session_id = repos.ensure_open_session(conn, now=1.0)
    repos.update_session_state(conn, session_id, SessionState(route=Route(steps=steps)))


# H1: курс вырос после того, как всё прежнее закрыто.


def test_task_after_course_growth_starts_new_module(conn, settings) -> None:
    _module_one_done_before_growth(conn)

    reply = start_practice_reply(conn, now=2.0, settings=settings)

    assert reply.text != ROUTE_DONE_REPLY
    assert _state(conn).current_node_id.startswith("mini_")


async def test_resume_after_course_growth_is_not_course_done(conn, settings) -> None:
    _module_one_done_before_growth(conn)

    reply = await resume_reply(conn, GradingTutor(conn, passed=True), "m", now=2.0, settings=settings)

    assert ROUTE_DONE_REPLY not in reply.text
    assert any(s.concept_id.startswith("mini_") for s in _state(conn).route.steps)


async def test_free_text_after_course_growth_enters_new_module_silently(conn, settings) -> None:
    _module_one_done_before_growth(conn)

    reply = await handle_turn(
        conn, GradingTutor(conn, passed=True), "m", "привет", now=2.0, settings=settings
    )

    assert "Маршрут перестроен" not in reply.text
    state = _state(conn)
    assert state.current_node_id == "mini_plots"
    assert state.route.topic_id == 2
    assert 1 in state.route.completed_topics


# M1: переоткрытая тема прошлого модуля тянет в участок свои открытые пререквизиты.


def test_reopened_theme_pulls_its_open_prerequisites_into_section(conn, settings) -> None:
    graph = CourseGraph(
        [
            Concept(id="g", name="g", topic_id=1),
            Concept(id="agg", name="agg", topic_id=1),
            Concept(id="x", name="x", topic_id=2),
        ],
        [Edge(from_id="g", to_id="agg", hard=True)],
    )
    route = Route(
        steps=[
            RouteStep(concept_id="g", mode="full", status="ahead"),
            RouteStep(concept_id="agg", mode="full", status="ahead", closed_at=10.0),
            RouteStep(concept_id="x", mode="full", status="closed", closed_at=11.0),
        ],
        topic_id=2,
    )

    assert route_mod.working_section(graph, route, 2) == ["g", "agg", "x"]
    assert route_mod.next_node_id(conn, graph, route, now=20.0, settings=settings) == "g"


# L3: /plan не длиннее лимита Telegram.


def test_long_plan_fits_telegram_limit(conn, settings) -> None:
    nodes = [Concept(id=f"n{i:03d}", name="очень длинное название темы " * 2) for i in range(300)]
    repos.replace_seed(conn, nodes, [], [])
    repos.ensure_open_session(conn, now=1.0)

    assert len(render_plan(conn, now=0.0, settings=settings)) <= MAX_MESSAGE + 1
