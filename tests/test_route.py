"""Тесты маршрута: построение, снимок, пересмотр (Срез 9)."""

from llm_tutor.course.graph import CourseGraph
from llm_tutor.course.seed import load_seed
from llm_tutor.db import repos
from llm_tutor.schemas import Concept, Edge, Route, RouteStep, SessionState
from llm_tutor.student import route as route_mod


def _graph(concepts: list[str], edges: list[tuple[str, str]]) -> CourseGraph:
    return CourseGraph(
        [Concept(id=cid, name=cid) for cid in concepts],
        [Edge(from_id=src, to_id=dst, hard=True) for src, dst in edges],
    )


def _statuses(route: Route) -> dict[str, str]:
    return {step.concept_id: step.status for step in route.steps}


# --- построение ---


def test_route_covers_ancestors_of_goal_in_topological_order(conn, settings) -> None:
    graph = _graph(["a", "b", "c", "d"], [("a", "b"), ("b", "c")])

    result = route_mod.build_route(
        conn, graph, goal_concept_id="c", current_node_id="b", now=0.0, settings=settings
    )

    assert [step.concept_id for step in result.steps] == ["a", "b", "c"]  # d вне цели
    assert result.goal_concept_id == "c"


def test_current_node_is_marked_current_others_ahead(conn, settings) -> None:
    graph = _graph(["a", "b"], [("a", "b")])

    result = route_mod.build_route(
        conn, graph, current_node_id="a", now=0.0, settings=settings
    )

    assert _statuses(result) == {"a": "current", "b": "ahead"}


def test_confidently_mastered_node_is_closed(conn, settings) -> None:
    graph = _graph(["a", "b"], [("a", "b")])
    repos.upsert_concept(conn, Concept(id="a", name="a"))
    repos.upsert_concept(conn, Concept(id="b", name="b"))
    repos.upsert_mastery(conn, "a", alpha=38.0, beta=2.0, last_seen=0.0)

    result = route_mod.build_route(conn, graph, now=0.0, settings=settings)

    assert _statuses(result)["a"] == "closed"


def test_closed_status_survives_rebuild(conn, settings) -> None:
    """Узел, закрытый по задачам, не должен «раззакрыться» при пересчёте."""
    graph = _graph(["a", "b"], [("a", "b")])
    previous = Route(
        steps=[
            RouteStep(concept_id="a", mode="full", status="closed"),
            RouteStep(concept_id="b", mode="full", status="current"),
        ]
    )

    result = route_mod.build_route(
        conn, graph, current_node_id="b", previous=previous, now=0.0, settings=settings
    )

    assert _statuses(result)["a"] == "closed"


# --- пересмотр ---


def test_diff_reports_closed_added_removed_and_current() -> None:
    previous = Route(
        steps=[
            RouteStep(concept_id="a", mode="full", status="current"),
            RouteStep(concept_id="b", mode="full", status="ahead"),
        ]
    )
    current = Route(
        steps=[
            RouteStep(concept_id="a", mode="full", status="closed"),
            RouteStep(concept_id="c", mode="full", status="current"),
        ]
    )

    changes = route_mod.diff_routes(previous, current)

    assert changes.closed == ("a",)
    assert changes.added == ("c",)
    assert changes.removed == ("b",)
    assert changes.current_changed is True


def test_first_build_reports_everything_as_added(conn, settings) -> None:
    graph = _graph(["a"], [])

    current = route_mod.build_route(conn, graph, now=0.0, settings=settings)
    changes = route_mod.diff_routes(None, current)

    assert changes.added == ("a",)
    assert changes.closed == ()


def test_single_added_step_is_not_significant() -> None:
    """Один новый узел без закрытий — не повод тревожить ученика."""
    changes = route_mod.RouteChanges(
        closed=(), added=("b",), removed=(), current_changed=False
    )

    assert route_mod.is_significant(changes) is False


def test_two_changed_steps_are_significant() -> None:
    """Замена узла узлом — это уже два изменённых шага."""
    changes = route_mod.RouteChanges(
        closed=(), added=("b",), removed=("a",), current_changed=False
    )

    assert route_mod.is_significant(changes) is True


def test_threshold_is_configurable() -> None:
    changes = route_mod.RouteChanges(
        closed=(), added=("b",), removed=(), current_changed=False
    )

    assert route_mod.is_significant(changes, min_steps=1) is True


def test_closing_any_node_is_significant() -> None:
    changes = route_mod.RouteChanges(
        closed=("a",), added=(), removed=(), current_changed=False
    )

    assert route_mod.is_significant(changes) is True


def test_route_change_text_mentions_closed_node() -> None:
    changes = route_mod.RouteChanges(
        closed=("groupby",), added=(), removed=(), current_changed=False
    )

    assert "закрыт" in route_mod.format_route_change(changes).lower()


def test_refresh_returns_snapshot_and_message(conn, settings) -> None:
    """Пересчёт отдаёт новый снимок и текст о значимом изменении."""
    graph = _graph(["a", "b"], [("a", "b")])
    previous = Route(
        goal_concept_id=None,
        steps=[
            RouteStep(concept_id="a", mode="full", status="closed"),
            RouteStep(concept_id="b", mode="full", status="current"),
        ],
    )
    state = SessionState(current_node_id="b", route=previous)

    route, note = route_mod.refresh(conn, state, graph, now=0.0, settings=settings)

    assert _statuses(route)["a"] == "closed"
    assert note is None  # ничего значимого не поменялось


# --- реальный seed ---


def test_route_on_real_seed_covers_path_to_goal(conn, settings) -> None:
    load_seed(conn)
    graph = CourseGraph.load(conn)

    result = route_mod.build_route(
        conn, graph, goal_concept_id="summary_tables", now=0.0, settings=settings
    )

    ids = {step.concept_id for step in result.steps}
    assert "groupby" in ids and "summary_tables" in ids
    assert "churn_eda_case" not in ids  # это за целью


# --- фиксы ревью: просевший узел, снимок, молчание, приоритет ---


def test_decayed_node_leaves_closed_and_goes_for_review(conn, settings) -> None:
    """Забытый узел должен вернуться в маршрут (§6.4 — долгий перерыв)."""
    graph = _graph(["a"], [])
    repos.upsert_concept(conn, Concept(id="a", name="a"))
    # владение ещё выше порога «сжатого прохода», но повторение просрочено
    repos.upsert_mastery(conn, "a", alpha=3.0, beta=1.0, last_seen=0.0, next_review=-1000.0)
    previous = Route(steps=[RouteStep(concept_id="a", mode="skip", status="closed")])

    result = route_mod.build_route(
        conn, graph, previous=previous, now=0.0, settings=settings
    )

    assert _statuses(result)["a"] != "closed"


def test_freshly_closed_node_stays_closed(conn, settings) -> None:
    """Свежезакрытый узел не должен «раззакрываться» сам собой."""
    graph = _graph(["a"], [])
    repos.upsert_concept(conn, Concept(id="a", name="a"))
    repos.upsert_mastery(conn, "a", alpha=3.0, beta=1.0, last_seen=0.0, next_review=1e9)
    previous = Route(steps=[RouteStep(concept_id="a", mode="full", status="closed")])

    result = route_mod.build_route(
        conn, graph, previous=previous, now=0.0, settings=settings
    )

    assert _statuses(result)["a"] == "closed"


def test_current_change_alone_is_not_announced() -> None:
    """Смена текущего узла без закрытий — не повод писать «маршрут перестроен»."""
    changes = route_mod.RouteChanges(
        closed=(), added=(), removed=(), current_changed=True
    )

    assert route_mod.is_significant(changes) is False


def test_next_node_follows_priority(conn, settings) -> None:
    """Следующий узел выбирается по приоритету (§6.3), а не по порядку графа."""
    graph = _graph(["a", "b", "c", "d"], [("b", "c"), ("b", "d")])
    route = route_mod.build_route(conn, graph, now=0.0, settings=settings)

    assert route_mod.next_node_id(conn, graph, route, now=0.0, settings=settings) == "b"


def test_next_node_is_none_when_nothing_ahead(conn, settings) -> None:
    graph = _graph(["a", "b"], [("a", "b")])
    repos.upsert_concept(conn, Concept(id="a", name="a"))
    repos.upsert_concept(conn, Concept(id="b", name="b"))
    for node_id in ("a", "b"):
        repos.upsert_mastery(
            conn, node_id, alpha=38.0, beta=2.0, last_seen=0.0, next_review=1e9
        )
    route = route_mod.build_route(conn, graph, now=0.0, settings=settings)

    assert route_mod.next_node_id(conn, graph, route, now=0.0, settings=settings) is None
