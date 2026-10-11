"""Тесты маршрута: построение, снимок, пересмотр (Срез 9)."""

from course_fixtures import T1

from llm_tutor.course.graph import CourseGraph
from llm_tutor.course.seed import load_seed
from llm_tutor.db import repos
from llm_tutor.schemas import Concept, Edge, Event, Route, RouteStep, SessionState
from llm_tutor.db.connection import get_conn, migrate
from llm_tutor.student import route as route_mod
from llm_tutor.student import survey


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


def test_route_change_text_mentions_added_and_removed() -> None:
    changes = route_mod.RouteChanges(
        closed=(), added=("b",), removed=("c",), current_changed=False
    )

    text = route_mod.format_route_change(changes)

    assert "добавилось b" in text
    assert "ушло c" in text


# --- заявленное в анкете (срез 23) ---


def _claimed_route(*statuses: tuple[str, str]) -> Route:
    return Route(
        steps=[
            RouteStep(concept_id=concept_id, mode="full", status=status)
            for concept_id, status in statuses
        ]
    )


def test_claimed_comes_from_confident_survey_answer(conn, settings) -> None:
    load_seed(conn)
    survey.apply_answers(
        conn, T1, {"block_python": survey.CONFIDENT_INDEX}, now=0.0, settings=settings
    )

    route = route_mod.build_route(conn, CourseGraph.load(conn), now=0.0, settings=settings)

    statuses = _statuses(route)
    assert statuses["python_basics"] == statuses["numpy_basics"] == "claimed"
    assert statuses["pandas_intro"] == "ahead"


def test_claimed_kept_in_snapshot_until_node_becomes_current(conn, settings) -> None:
    graph = _graph(["a", "b"], [("a", "b")])
    previous = _claimed_route(("a", "claimed"), ("b", "ahead"))

    kept = route_mod.build_route(conn, graph, previous=previous, now=0.0, settings=settings)
    current = route_mod.build_route(
        conn, graph, current_node_id="a", previous=kept, now=0.0, settings=settings
    )
    after = route_mod.build_route(conn, graph, previous=current, now=0.0, settings=settings)

    assert _statuses(kept)["a"] == "claimed"
    assert _statuses(current)["a"] == "current"
    assert _statuses(after)["a"] == "ahead"  # заявка снята — обратно не возвращается


def test_next_node_treats_claimed_as_done_prerequisite(conn, settings) -> None:
    graph = _graph(["a", "b"], [("a", "b")])
    route = _claimed_route(("a", "claimed"), ("b", "ahead"))

    assert route_mod.next_node_id(conn, graph, route, now=0.0, settings=settings) == "b"


def test_next_node_checks_claimed_when_nothing_else_left(conn, settings) -> None:
    graph = _graph(["a", "b", "c"], [("a", "b")])
    route = _claimed_route(("a", "closed"), ("b", "claimed"), ("c", "claimed"))

    assert route_mod.next_node_id(conn, graph, route, now=0.0, settings=settings) == "b"


def test_first_node_is_never_claimed_while_unclaimed_remain(conn, settings) -> None:
    """По всем 32 наборам «уверенных» блоков старт — с незаявленного узла."""
    from itertools import combinations

    keys = [block.key for block in T1.blocks]
    for size in range(len(keys) + 1):
        for confident in combinations(keys, size):
            db = get_conn(":memory:")
            migrate(db)
            load_seed(db)
            answers = {key: (survey.CONFIDENT_INDEX if key in confident else 1) for key in keys}
            survey.apply_answers(db, T1, answers, now=0.0, settings=settings)
            graph = CourseGraph.load(db)
            route = route_mod.build_route(db, graph, now=0.0, settings=settings)
            first = route_mod.next_node_id(db, graph, route, now=0.0, settings=settings)
            statuses = _statuses(route)
            db.close()
            if size < len(keys):
                assert statuses[first] != "claimed", confident
            else:
                assert first == "python_basics"  # всё заявлено — проверка с начала


def test_upcoming_puts_first_then_ahead_then_claimed() -> None:
    route = _claimed_route(("a", "claimed"), ("b", "ahead"), ("c", "closed"), ("d", "ahead"))

    steps = route_mod.upcoming(route, "d", limit=5)

    assert [step.concept_id for step in steps] == ["d", "b", "a"]


def test_upcoming_respects_limit_and_empty_first() -> None:
    route = _claimed_route(("a", "ahead"), ("b", "ahead"), ("c", "ahead"))

    assert [step.concept_id for step in route_mod.upcoming(route, "a", limit=2)] == ["a", "b"]
    assert route_mod.upcoming(route, None, limit=5) == []


# --- модули курса (срез 25) ---


def _course(nodes: dict[str, int], edges: list[tuple[str, str]]) -> CourseGraph:
    """Граф из тем с модулями: {"a": 1, "x": 2}."""
    return CourseGraph(
        [Concept(id=node, name=node, topic_id=topic) for node, topic in nodes.items()],
        [Edge(from_id=src, to_id=dst, hard=True) for src, dst in edges],
    )


def _route(statuses: dict[str, str], **fields) -> Route:
    return Route(
        steps=[RouteStep(concept_id=n, mode="full", status=s) for n, s in statuses.items()],
        **fields,
    )


def test_working_section_is_module_plus_open_ancestors() -> None:
    graph = _course({"a": 1, "b": 1, "x": 2, "y": 2}, [("a", "x"), ("b", "y")])
    route = _route({"a": "closed", "b": "ahead", "x": "ahead", "y": "ahead"})

    assert route_mod.working_section(graph, route, 2) == ["b", "x", "y"]


def test_next_node_stays_inside_current_module(conn, settings) -> None:
    """Лёгкая тема модуля 2 не уводит из модуля 1, пока он не пройден."""
    graph = CourseGraph(
        [
            Concept(id="a", name="a", difficulty=0.9, topic_id=1),
            Concept(id="x", name="x", difficulty=0.1, topic_id=2),
        ],
        [],
    )
    route = _route({"a": "ahead", "x": "ahead"}, topic_id=1)

    assert route_mod.next_node_id(conn, graph, route, now=0.0, settings=settings) == "a"


def test_choose_topic_moves_on_when_module_finished() -> None:
    graph = _course({"a": 1, "x": 2}, [])
    route = _route({"a": "closed", "x": "ahead"})

    assert route_mod.choose_topic(graph, route, previous_topic=1) == 2


def test_choose_topic_keeps_module_for_pulled_in_ancestor() -> None:
    """Незакрытый предок из модуля 1 стал текущим — модуль работы всё ещё 2."""
    graph = _course({"a": 1, "x": 2}, [("a", "x")])
    route = _route({"a": "current", "x": "ahead"})

    assert route_mod.choose_topic(graph, route, previous_topic=2) == 2


def test_choose_topic_prefers_unfinished_module_over_completed_one() -> None:
    graph = _course({"a": 1, "x": 2}, [])
    route = _route({"a": "ahead", "x": "ahead"}, completed_topics=[1])

    assert route_mod.choose_topic(graph, route) == 2


def test_choose_topic_is_none_when_everything_closed() -> None:
    graph = _course({"a": 1}, [])

    assert route_mod.choose_topic(graph, _route({"a": "closed"})) is None


def test_build_route_carries_module_state(conn, settings) -> None:
    graph = _course({"a": 1, "x": 2}, [])
    previous = _route({"a": "closed", "x": "ahead"}, topic_id=2, completed_topics=[1])

    fresh = route_mod.build_route(conn, graph, previous=previous, now=0.0, settings=settings)

    assert fresh.topic_id == 2
    assert fresh.completed_topics == [1]


def test_legacy_snapshot_continues_in_first_module(conn, settings) -> None:
    """Снимок до модулей: только темы topic01, без topic_id — работа в модуле 1."""
    graph = _course({"a": 1, "b": 1, "x": 2}, [("a", "b")])
    legacy = _route({"a": "closed", "b": "current"})

    fresh = route_mod.build_route(
        conn, graph, current_node_id="b", previous=legacy, now=0.0, settings=settings
    )

    assert fresh.topic_id == 1
    assert {s.concept_id: s.status for s in fresh.steps}["a"] == "closed"


def test_mark_claimed_touches_only_ahead() -> None:
    route = _route({"a": "closed", "b": "current", "c": "ahead"})

    marked = route_mod.mark_claimed(route, {"a", "b", "c"})

    assert _statuses(marked) == {"a": "closed", "b": "current", "c": "claimed"}


def test_release_current_makes_it_ahead() -> None:
    assert _statuses(route_mod.release_current(_route({"a": "current"}))) == {"a": "ahead"}


def test_upcoming_respects_section() -> None:
    route = _route({"a": "ahead", "x": "ahead"})

    assert [s.concept_id for s in route_mod.upcoming(route, "a", limit=5, section=["a"])] == ["a"]


def test_topic_for_new_student_is_first_module() -> None:
    graph = _course({"a": 1, "x": 2}, [])

    assert route_mod.topic_for(graph, SessionState()) == 1


def _closed_at(route: Route, node: str) -> float | None:
    return next(step.closed_at for step in route.steps if step.concept_id == node)


def test_closed_node_reopens_after_failures_since_closing(conn, settings) -> None:
    graph = _course({"a": 1}, [])
    repos.upsert_concept(conn, Concept(id="a", name="a"))
    previous = Route(
        steps=[RouteStep(concept_id="a", mode="full", status="closed", closed_at=10.0)]
    )
    for ts in (11.0, 12.0):
        repos.add_event(conn, Event(source="autotest", result=0.0, concept_id="a", ts=ts))

    fresh = route_mod.build_route(conn, graph, previous=previous, now=13.0, settings=settings)

    assert _statuses(fresh)["a"] != "closed"


def test_secondary_weight_failures_count_by_weight(conn, settings) -> None:
    """25-L5: провал по заданию, где тема лишь вторична, весит меньше прямого."""
    from llm_tutor.schemas import Item

    graph = _course({"a": 1, "b": 1}, [])
    repos.upsert_concept(conn, Concept(id="a", name="a"))
    repos.upsert_concept(conn, Concept(id="b", name="b"))
    repos.upsert_item(
        conn,
        Item(
            id=1,
            prompt="?",
            answer_type="short",
            answer="1",
            concept_weights={"b": 1.0, "a": 0.3},
        ),
    )
    previous = Route(
        steps=[RouteStep(concept_id="a", mode="full", status="closed", closed_at=10.0)]
    )
    for ts in (11.0, 12.0):
        repos.add_event(
            conn,
            Event(source="autotest", result=0.0, concept_id="a", item_id=1, weight=0.3, ts=ts),
        )

    fresh = route_mod.build_route(conn, graph, previous=previous, now=13.0, settings=settings)

    assert _statuses(fresh)["a"] == "closed"


def test_failures_before_closing_do_not_reopen(conn, settings) -> None:
    graph = _course({"a": 1}, [])
    repos.upsert_concept(conn, Concept(id="a", name="a"))
    previous = Route(
        steps=[RouteStep(concept_id="a", mode="full", status="closed", closed_at=10.0)]
    )
    for ts in (5.0, 6.0):
        repos.add_event(conn, Event(source="autotest", result=0.0, concept_id="a", ts=ts))

    fresh = route_mod.build_route(conn, graph, previous=previous, now=13.0, settings=settings)

    assert _statuses(fresh)["a"] == "closed"
    assert _closed_at(fresh, "a") == 10.0


def test_legacy_closed_step_gets_closed_at_now(conn, settings) -> None:
    """Снимок до среза 25: время закрытия неизвестно — отсчёт с пересчёта."""
    graph = _course({"a": 1}, [])
    repos.upsert_concept(conn, Concept(id="a", name="a"))
    repos.add_event(conn, Event(source="autotest", result=0.0, concept_id="a", ts=1.0))
    repos.add_event(conn, Event(source="autotest", result=0.0, concept_id="a", ts=2.0))
    previous = Route(steps=[RouteStep(concept_id="a", mode="full", status="closed")])

    fresh = route_mod.build_route(conn, graph, previous=previous, now=7.0, settings=settings)

    assert _statuses(fresh)["a"] == "closed"
    assert _closed_at(fresh, "a") == 7.0


def test_reopened_ancestor_joins_next_module_section(conn, settings) -> None:
    graph = _course({"a": 1, "x": 2}, [("a", "x")])
    repos.upsert_concept(conn, Concept(id="a", name="a"))
    previous = Route(
        steps=[
            RouteStep(concept_id="a", mode="full", status="closed", closed_at=10.0),
            RouteStep(concept_id="x", mode="full", status="ahead"),
        ],
        topic_id=2,
        completed_topics=[1],
    )
    for ts in (11.0, 12.0):
        repos.add_event(conn, Event(source="autotest", result=0.0, concept_id="a", ts=ts))

    fresh = route_mod.build_route(conn, graph, previous=previous, now=13.0, settings=settings)

    assert route_mod.working_section(graph, fresh, 2) == ["a", "x"]


def test_reopened_theme_without_edge_joins_current_section(conn, settings) -> None:
    """Провалы по заданию модуля 2 со вторичным весом на «a» — без ребра a → x."""
    graph = _course({"a": 1, "x": 2}, [])
    repos.upsert_concept(conn, Concept(id="a", name="a"))
    previous = Route(
        steps=[
            RouteStep(concept_id="a", mode="full", status="closed", closed_at=10.0),
            RouteStep(concept_id="x", mode="full", status="current"),
        ],
        topic_id=2,
        completed_topics=[1],
    )
    for ts in (11.0, 12.0):
        repos.add_event(conn, Event(source="autotest", result=0.0, concept_id="a", ts=ts))

    fresh = route_mod.build_route(
        conn, graph, current_node_id="x", previous=previous, now=13.0, settings=settings
    )

    assert _closed_at(fresh, "a") == 10.0  # метка «была закрыта» осталась
    assert route_mod.working_section(graph, fresh, 2) == ["a", "x"]
    assert fresh.topic_id == 2


def test_never_closed_theme_of_earlier_module_stays_out(conn, settings) -> None:
    """Прыжок вперёд: незакрытое прошлого модуля без ребра и без метки — не в участке."""
    graph = _course({"a": 1, "x": 2}, [])

    fresh = route_mod.build_route(conn, graph, current_node_id="x", now=0.0, settings=settings)

    assert route_mod.working_section(graph, fresh, 2) == ["x"]


def test_course_growth_is_not_reported_as_route_change(conn, settings) -> None:
    """Снимок topic01 + новые модули в графе — ученику сообщать не о чем."""
    graph = _course({"a": 1, "b": 1, "x": 2, "y": 2}, [("a", "b")])
    legacy = _route({"a": "closed", "b": "current"})
    state = SessionState(current_node_id="b", route=legacy)

    _, note = route_mod.refresh(conn, state, graph, now=0.0, settings=settings)

    assert note is None


def test_module_finished_by_mastery_alone_is_celebrated(conn, settings) -> None:
    """25-L1: последнюю тему модуля закрыло владение, а не урок, — «🎉» всё равно есть."""
    from course_fixtures import load_two_modules

    load_two_modules(conn)
    graph = CourseGraph.load(conn)
    last = graph.topic_nodes(1)[-1]
    steps = [
        RouteStep(
            concept_id=node,
            mode="full",
            status="ahead" if node == last or graph.topic_of(node) == 2 else "closed",
            closed_at=None if node == last or graph.topic_of(node) == 2 else 0.5,
        )
        for node in graph.topo_order()
    ]
    repos.upsert_mastery(conn, last, alpha=38.0, beta=2.0, last_seen=0.0)
    state = SessionState(route=Route(steps=steps, topic_id=1))

    fresh, note = route_mod.refresh(conn, state, graph, now=0.0, settings=settings)

    assert 1 in fresh.completed_topics
    assert note is not None and "🎉 Модуль 1" in note
    # Повторный пересчёт не празднует ещё раз.
    _, again = route_mod.refresh(
        conn, state.model_copy(update={"route": fresh}), graph, now=0.0, settings=settings
    )
    assert again is None or "🎉" not in again
