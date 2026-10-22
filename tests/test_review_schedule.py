"""Тесты таблицы расписания повторений и арифметики лестницы (срез 79)."""

from course_fixtures import load_lessons

from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.schemas import Event, ReviewRow, Route, RouteStep
from llm_tutor.student import review

DAY = 86_400.0
NOW = 100 * DAY + 3_600.0  # 01:00 UTC сотого дня


def test_first_due_tomorrow_when_free():
    assert review.first_due({}, step=0, now=NOW) == NOW + DAY


def test_first_due_skips_full_day():
    rows = {f"n{i}": ReviewRow(concept_id=f"n{i}", step=0, due_at=NOW + DAY) for i in range(10)}
    assert review.first_due(rows, step=0, now=NOW) == NOW + 2 * DAY


def test_first_due_counts_pending():
    pending = [review.ScheduleChange(f"n{i}", 0, NOW + DAY) for i in range(10)]
    assert review.first_due({}, step=0, now=NOW, pending=pending) == NOW + 2 * DAY


def test_first_due_verify_step_starts_week_later():
    assert review.first_due({}, step=2, now=NOW) == NOW + 7 * DAY


def test_due_correct_moves_up():
    row = ReviewRow(concept_id="a", step=1, due_at=NOW - 1)
    assert review.plan_answer(row, "a", passed=True, hinted=False, due=True, now=NOW) == review.ScheduleChange("a", 2, NOW + 7 * DAY)


def test_correct_on_last_step_retires():
    row = ReviewRow(concept_id="a", step=5, due_at=NOW - 1)
    assert review.plan_answer(row, "a", passed=True, hinted=False, due=True, now=NOW) == review.ScheduleChange("a", None, None)


def test_mistake_drops_two_steps_floor_zero():
    for step, expected in ((4, 2), (1, 0), (0, 0)):
        row = ReviewRow(concept_id="a", step=step, due_at=NOW - 1)
        assert review.plan_answer(row, "a", passed=False, hinted=False, due=True, now=NOW) == review.ScheduleChange("a", expected, NOW + DAY)


def test_hinted_correct_keeps_step_tomorrow():
    row = ReviewRow(concept_id="a", step=3, due_at=NOW - 1)
    assert review.plan_answer(row, "a", passed=True, hinted=True, due=True, now=NOW) == review.ScheduleChange("a", 3, NOW + DAY)


def test_early_correct_changes_nothing():
    row = ReviewRow(concept_id="a", step=3, due_at=NOW + 5 * DAY)
    assert review.plan_answer(row, "a", passed=True, hinted=False, due=False, now=NOW) is None


def test_mistake_without_row_creates_step_zero():
    assert review.plan_answer(None, "a", passed=False, hinted=False, due=False, now=NOW) == review.ScheduleChange("a", 0, NOW + DAY)


def test_correct_without_row_changes_nothing():
    assert review.plan_answer(None, "a", passed=True, hinted=False, due=False, now=NOW) is None


def test_postpone_keeps_step_skips_missing():
    rows = {"a": ReviewRow(concept_id="a", step=3, due_at=NOW - 1)}
    assert review.plan_postpone(rows, ["a", "b"], now=NOW) == [review.ScheduleChange("a", 3, NOW + DAY)]


def test_write_changes_upsert_and_delete(conn):
    load_lessons(conn)
    review.write_changes(conn, [review.ScheduleChange("l1_frames", 0, NOW)])
    assert repos.get_review_rows(conn)["l1_frames"].step == 0
    review.write_changes(conn, [review.ScheduleChange("l1_frames", 1, NOW + DAY)])
    assert repos.get_review_rows(conn)["l1_frames"] == ReviewRow(concept_id="l1_frames", step=1, due_at=NOW + DAY)
    review.write_changes(conn, [review.ScheduleChange("l1_frames", None, None)])
    assert repos.get_review_rows(conn) == {}


# --- выбор занятий и заданий к повторению ---

ITEMS = {  # обычные задания занятий фикстуры; итоговое у каждого — последнее
    "l1_frames": [3001, 3002, 3003, 3004],
    "l1_select": [3006, 3007, 3008, 3009],
}
FINAL = {"l1_frames": 3005, "l1_select": 3010}


def _answer(conn, item_id: int, concept_id: str, ts: float) -> None:
    repos.add_event(
        conn, Event(source="autotest", result=1.0, concept_id=concept_id, item_id=item_id, ts=ts)
    )


def _graph_without(course, concept_id: str) -> CourseGraph:
    """Граф без занятия — так выглядит снятый с публикации модуль."""
    nodes = tuple(n for n in course.nodes if n.id != concept_id)
    edges = tuple(e for e in course.edges if concept_id not in (e.from_id, e.to_id))
    return CourseGraph(nodes, edges)


def _route(*concept_ids: str, status: str = "closed") -> Route:
    return Route(
        steps=[
            RouteStep(concept_id=cid, mode="full", status=status, closed_at=NOW - 10 * DAY)
            for cid in concept_ids
        ]
    )


def test_pick_item_skips_final_and_recent(conn):
    load_lessons(conn)
    _answer(conn, 3001, "l1_frames", NOW - 3_600)
    for _ in range(10):
        item = review.pick_item(conn, "l1_frames", now=NOW)
        assert item is not None
        assert item.id != 3001
        assert not item.final
        assert item.id == 3002  # никогда не отвеченные — по id


def test_pick_item_respects_exclude(conn):
    load_lessons(conn)
    item = review.pick_item(conn, "l1_frames", now=NOW, exclude={3001, 3002})
    assert item is not None and item.id == 3003


def test_pick_item_prefers_never_answered_then_oldest(conn):
    load_lessons(conn)
    old = NOW - 2 * DAY
    _answer(conn, 3001, "l1_frames", old + 30)
    _answer(conn, 3002, "l1_frames", old)  # самый давний
    _answer(conn, 3003, "l1_frames", old + 20)
    # Один из четырёх не отвечен — он первый.
    assert review.pick_item(conn, "l1_frames", now=NOW).id == 3004
    _answer(conn, 3004, "l1_frames", old + 10)
    # Отвечены все: берётся отвеченное раньше всех.
    assert review.pick_item(conn, "l1_frames", now=NOW).id == 3002


def test_pick_item_none_when_only_final(conn):
    load_lessons(conn)
    for item_id in ITEMS["l1_frames"]:
        _answer(conn, item_id, "l1_frames", NOW - 60)  # все свежие
    assert review.pick_item(conn, "l1_frames", now=NOW) is None
    # Итоговое не берётся и тогда, когда свободно только оно.
    assert review.pick_item(conn, "l1_frames", now=NOW, exclude=set(ITEMS["l1_frames"])) is None


def test_due_lessons_order(conn, settings):
    course = load_lessons(conn)
    graph = CourseGraph(course.nodes, course.edges)
    route = _route("l1_frames", "l1_select", "l1_group")
    repos.upsert_review(conn, "l1_frames", 1, NOW - 1 * DAY)
    repos.upsert_review(conn, "l1_select", 1, NOW - 3 * DAY)
    repos.upsert_review(conn, "l1_group", 1, NOW + DAY)  # срок не наступил
    assert review.due_lessons(conn, graph, route, now=NOW, settings=settings) == [
        "l1_select",
        "l1_frames",
    ]


def test_due_lessons_tie_weaker_first(conn, settings):
    course = load_lessons(conn)
    graph = CourseGraph(course.nodes, course.edges)
    route = _route("l1_frames", "l1_select")
    for cid in ("l1_frames", "l1_select"):
        repos.upsert_review(conn, cid, 1, NOW - DAY)
    repos.upsert_mastery(conn, "l1_frames", alpha=9.0, beta=1.0, last_seen=NOW)
    repos.upsert_mastery(conn, "l1_select", alpha=1.0, beta=9.0, last_seen=NOW)
    assert review.due_lessons(conn, graph, route, now=NOW, settings=settings) == [
        "l1_select",
        "l1_frames",
    ]


def test_due_lessons_no_route(conn, settings):
    course = load_lessons(conn)
    graph = CourseGraph(course.nodes, course.edges)
    repos.upsert_review(conn, "l1_frames", 0, NOW - DAY)
    assert review.due_lessons(conn, graph, None, now=NOW, settings=settings) == []


def test_due_lessons_skips_not_closed_missing_and_empty(conn, settings):
    course = load_lessons(conn)
    graph = _graph_without(course, "l1_select")
    route = Route(
        steps=[
            RouteStep(concept_id="l1_frames", mode="full", status="current"),  # не закрыто
            RouteStep(concept_id="l1_select", mode="full", status="closed", closed_at=NOW - DAY),
            RouteStep(concept_id="l1_group", mode="full", status="closed", closed_at=NOW - DAY),
            RouteStep(concept_id="l2_hist", mode="full", status="closed", closed_at=NOW - DAY),
        ]
    )
    for cid in ("l1_frames", "l1_select", "l1_group", "l2_hist"):
        repos.upsert_review(conn, cid, 1, NOW - DAY)
    for item_id in (3011, 3012, 3013, 3014):  # у l1_group осталось только итоговое
        _answer(conn, item_id, "l1_group", NOW - 60)
    assert review.due_lessons(conn, graph, route, now=NOW, settings=settings) == ["l2_hist"]


def test_closed_lessons_in_route_order_and_in_graph(conn):
    course = load_lessons(conn)
    graph = _graph_without(course, "l1_select")
    route = Route(
        steps=[
            RouteStep(concept_id="l2_hist", mode="full", status="closed", closed_at=NOW - DAY),
            RouteStep(concept_id="l1_select", mode="full", status="closed", closed_at=NOW - DAY),
            RouteStep(concept_id="l1_frames", mode="full", status="current"),
            RouteStep(concept_id="l1_group", mode="full", status="closed", closed_at=NOW - DAY),
        ]
    )
    assert review.closed_lessons(graph, route) == ["l2_hist", "l1_group"]
    assert review.closed_lessons(graph, None) == []
