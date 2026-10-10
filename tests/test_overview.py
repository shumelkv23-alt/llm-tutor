"""Сводки ядра для экранов: темы и маршрут (Срез 40)."""

from llm_tutor.core.overview import route_overview, topic_rows
from llm_tutor.course.graph import CourseGraph
from llm_tutor.course.seed import load_seed
from llm_tutor.db import repos


def test_topic_rows_cover_graph_in_order(conn, settings) -> None:
    load_seed(conn)

    rows = topic_rows(conn, now=1.0, settings=settings)

    assert [row.id for row in rows] == CourseGraph.load(conn).topo_order()
    first = rows[0]
    assert first.name
    assert first.status == "available"
    assert 0.0 < first.mastery < 1.0
    assert first.confident is False
    assert any(row.status == "ahead" for row in rows)


def test_topic_rows_mark_current(conn, settings) -> None:
    load_seed(conn)
    session_id = repos.ensure_open_session(conn, 1.0)
    state = repos.get_session_state(conn, session_id)
    repos.update_session_state(conn, session_id, state.model_copy(update={"current_node_id": "groupby"}))

    rows = {row.id: row for row in topic_rows(conn, now=1.0, settings=settings)}

    assert rows["groupby"].status == "current"


def test_route_overview_reads_without_opening_session(conn, settings) -> None:
    load_seed(conn)

    overview = route_overview(conn, now=1.0, settings=settings)

    assert overview.total == len(overview.steps) > 0
    assert overview.closed == 0
    assert repos.get_open_session(conn) is None
