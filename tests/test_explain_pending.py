"""Висящее задание «Объясни новичку» вычисляется по данным (срез 93)."""

from course_fixtures import load_lessons
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.schemas import ExplainState, Route, RouteStep
from llm_tutor.student import explain as logic
from llm_tutor.web.userdb import UserDBPool


def _route(**status: tuple[str, float | None]) -> Route:
    """Маршрут из шагов ``узел=(статус, closed_at)``."""
    return Route(
        steps=[
            RouteStep(concept_id=node, mode="full", status=state, closed_at=closed_at)
            for node, (state, closed_at) in status.items()
        ]
    )


def _setup(conn, since: float | None = 10.0) -> list[str]:
    load_lessons(conn)
    if since is not None:
        repos.set_fact(conn, logic.SINCE_FACT, str(since))
    return CourseGraph.load(conn).topo_order()


def _finish(conn, concept_id: str, kind: str) -> None:
    session_id = repos.create_explain_session(
        conn, 1, now=1.0, concept_id=concept_id, kind=kind
    )
    repos.update_explain_session(
        conn, session_id, state=ExplainState(), updated_at=2.0, status="finished", commit=True
    )


def test_pending_after_close(conn) -> None:
    order = _setup(conn)
    route = _route(l1_group=("closed", 20.0))
    assert logic.pending_lesson(conn, route, order) == "l1_group"


def test_closed_before_since_ignored(conn) -> None:
    order = _setup(conn)
    route = _route(l1_group=("closed", 5.0))
    assert logic.pending_lesson(conn, route, order) is None


def test_not_important_ignored(conn) -> None:
    order = _setup(conn)
    route = _route(l1_frames=("closed", 20.0))
    assert logic.pending_lesson(conn, route, order) is None


def test_finished_required_clears(conn) -> None:
    order = _setup(conn)
    route = _route(l1_group=("closed", 20.0))
    _finish(conn, "l1_group", "voluntary")
    assert logic.pending_lesson(conn, route, order) == "l1_group"
    _finish(conn, "l1_group", "required")
    assert logic.pending_lesson(conn, route, order) is None


def test_earliest_by_order(conn) -> None:
    order = _setup(conn)
    route = _route(l1_group=("closed", 20.0), l1_select=("closed", 21.0))
    assert logic.pending_lesson(conn, route, order) == "l1_select"


def test_no_fact_no_pending(conn) -> None:
    order = _setup(conn, since=None)
    route = _route(l1_group=("closed", 20.0))
    assert logic.pending_lesson(conn, route, order) is None


def test_reopened_lesson_not_pending(conn) -> None:
    order = _setup(conn)
    for state in ("current", "ahead", "claimed"):
        route = _route(l1_group=(state, 20.0))
        assert logic.pending_lesson(conn, route, order) is None


def test_no_route_no_pending(conn) -> None:
    order = _setup(conn)
    assert logic.pending_lesson(conn, None, order) is None


def test_closed_without_stamp_ignored(conn) -> None:
    order = _setup(conn)
    route = _route(l1_group=("closed", None))
    assert logic.pending_lesson(conn, route, order) is None


def test_mark_since_idempotent(conn) -> None:
    logic.mark_since(conn, 100.0)
    logic.mark_since(conn, 200.0)
    assert repos.get_fact(conn, logic.SINCE_FACT) == "100.0"


def test_userdb_open_marks_since(tmp_path) -> None:
    pool = UserDBPool(tmp_path / "users", tmp_path / "materials.sqlite3")
    conn = pool.open(1, "mlcourse")
    assert float(repos.get_fact(conn, logic.SINCE_FACT)) > 0
    pool.close_all()
