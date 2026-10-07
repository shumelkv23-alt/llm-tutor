"""Тесты навигации по узлам темы."""

from llm_tutor.bot import themes
from llm_tutor.course.graph import CourseGraph
from llm_tutor.course.seed import load_seed
from llm_tutor.db import repos
from llm_tutor.schemas import SessionState


def test_node_status_marks_current(conn, settings) -> None:
    load_seed(conn)
    graph = CourseGraph.load(conn)
    state = SessionState(current_node_id="groupby")

    assert themes.node_status(conn, graph, "groupby", state, now=0.0, settings=settings) == "current"


def test_node_status_ahead_then_available(conn, settings) -> None:
    """Узел впереди, пока пререквизиты открыты; доступен, когда закрыты."""
    load_seed(conn)
    graph = CourseGraph.load(conn)
    node = next(n for n in graph.topo_order() if graph.hard_prerequisites(n))
    state = SessionState()

    assert themes.node_status(conn, graph, node, state, now=0.0, settings=settings) == "ahead"
    for prereq in graph.hard_prerequisites(node):
        repos.upsert_mastery(conn, prereq, alpha=38.0, beta=2.0, last_seen=0.0, next_review=1e9)
    assert themes.node_status(conn, graph, node, state, now=0.0, settings=settings) == "available"


def test_themes_keyboard_has_button_per_node(conn, settings) -> None:
    load_seed(conn)
    graph = CourseGraph.load(conn)

    kb = themes.themes_keyboard(conn, state=SessionState(), now=0.0, settings=settings)

    callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert len(callbacks) == len(graph.node_ids)
    assert all(cb.startswith("theme:") for cb in callbacks)


async def test_switch_node_changes_current_and_clears_pending(conn, settings) -> None:
    load_seed(conn)
    item = repos.get_item(conn, 6)
    session_id = repos.ensure_open_session(conn, now=1.0)
    repos.update_session_state(
        conn,
        session_id,
        SessionState(current_node_id="python_basics", pending_item_id=item.id),
    )

    themes.switch_node(conn, "groupby", now=2.0, settings=settings)

    state = repos.get_session_state(conn, session_id)
    assert state.current_node_id == "groupby"
    assert state.pending_item_id is None
    assert state.phase == "explain"
