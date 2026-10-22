"""Тесты таблицы расписания повторений и арифметики лестницы (срез 79)."""

import pytest
from course_fixtures import correct_answer, load_lessons
from test_lesson_sections import _answer as _answer_turn
from test_lesson_sections import _check, _enter, _pending
from test_turn import _state

from llm_tutor.course.graph import CourseGraph
from llm_tutor.core.turn import post_turn
from llm_tutor.db import repos
from llm_tutor.schemas import Event, ReviewRow, Route, RouteStep, SessionState
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


# --- строки при закрытии занятия ---


def _closure_route(before: dict[str, str], after: dict[str, tuple[str, str]]) -> tuple[Route, Route]:
    """Маршруты «до» (концепт → статус) и «после» (концепт → (статус, режим))."""
    old = Route(steps=[RouteStep(concept_id=c, mode="full", status=st) for c, st in before.items()])
    new = Route(
        steps=[RouteStep(concept_id=c, mode=mode, status=st) for c, (st, mode) in after.items()]
    )
    return old, new


def test_plan_closures_new_lesson_step_zero():
    old, new = _closure_route({"a": "current"}, {"a": ("closed", "full")})
    assert review.plan_closures({}, old, new, verify_node_id=None, now=NOW) == [
        review.ScheduleChange("a", 0, NOW + DAY)
    ]


def test_plan_closures_verify_step_two():
    old, new = _closure_route(
        {"a": "current", "b": "current"}, {"a": ("closed", "verify"), "b": ("closed", "skip")}
    )
    changes = review.plan_closures({}, old, new, verify_node_id="a", now=NOW)
    assert changes == [
        review.ScheduleChange("a", 2, NOW + 7 * DAY),
        review.ScheduleChange("b", 2, NOW + 7 * DAY),
    ]


def test_plan_closures_without_old_route_everything_is_new():
    _, new = _closure_route({}, {"a": ("closed", "full")})
    assert review.plan_closures({}, None, new, verify_node_id=None, now=NOW) == [
        review.ScheduleChange("a", 0, NOW + DAY)
    ]
    assert review.plan_closures({}, new, None, verify_node_id=None, now=NOW) == []


def test_plan_closures_existing_row_untouched():
    old, new = _closure_route({"a": "current"}, {"a": ("closed", "full")})
    rows = {"a": ReviewRow(concept_id="a", step=3, due_at=NOW + 9 * DAY)}
    assert review.plan_closures(rows, old, new, verify_node_id=None, now=NOW) == []


def test_plan_closures_already_closed_before_is_not_new():
    old, new = _closure_route({"a": "closed"}, {"a": ("closed", "full")})
    assert review.plan_closures({}, old, new, verify_node_id=None, now=NOW) == []


def test_plan_closures_eleven_in_a_day_spill_over():
    ids = [f"n{i}" for i in range(11)]
    old, new = _closure_route(
        {c: "current" for c in ids}, {c: ("closed", "full") for c in ids}
    )
    changes = review.plan_closures({}, old, new, verify_node_id=None, now=NOW)
    assert [c.due_at for c in changes] == [NOW + DAY] * 10 + [NOW + 2 * DAY]


async def test_closing_lesson_creates_row(conn, settings):
    load_lessons(conn)
    await _enter(conn, settings)
    for _ in range(2):
        _check(conn, settings)
        await _answer_turn(conn, settings, correct_answer(_pending(conn)))
    assert repos.get_review_rows(conn) == {}

    await _answer_turn(conn, settings, correct_answer(_pending(conn)), now=4.0)

    assert repos.get_review_rows(conn) == {
        "l1_frames": ReviewRow(concept_id="l1_frames", step=0, due_at=4.0 + DAY)
    }


def test_closure_rolls_back_with_turn(conn, monkeypatch):
    load_lessons(conn)
    session_id = repos.ensure_open_session(conn, now=1.0)
    old, new = _closure_route({"l1_frames": "current"}, {"l1_frames": ("closed", "full")})
    repos.update_session_state(conn, session_id, SessionState(route=old))

    def boom(*args, **kwargs):
        raise RuntimeError("сбой записи")

    monkeypatch.setattr(repos, "update_session_state", boom)
    with pytest.raises(RuntimeError):
        post_turn(
            conn, session_id, user_text="u", assistant_text="a",
            state=SessionState(route=new), now=NOW,
        )

    assert repos.get_review_rows(conn) == {}


def test_post_turn_writes_schedule_param_in_same_commit(conn):
    load_lessons(conn)
    session_id = repos.ensure_open_session(conn, now=1.0)
    post_turn(
        conn, session_id, user_text="u", assistant_text="a", state=SessionState(), now=NOW,
        schedule=[review.ScheduleChange("l1_frames", 1, NOW + DAY)],
    )
    assert repos.get_review_rows(conn)["l1_frames"].step == 1


def test_post_turn_closure_via_state_change(conn):
    load_lessons(conn)
    session_id = repos.ensure_open_session(conn, now=1.0)
    old, new = _closure_route({"l1_frames": "current"}, {"l1_frames": ("closed", "verify")})
    repos.update_session_state(conn, session_id, SessionState(route=old, mode="verify", current_node_id="l1_frames"))
    post_turn(
        conn, session_id, user_text="u", assistant_text="a", state=SessionState(route=new), now=NOW
    )
    assert repos.get_review_rows(conn)["l1_frames"] == ReviewRow(
        concept_id="l1_frames", step=2, due_at=NOW + 7 * DAY
    )


# --- перенос старых учеников ---


def _aged_route(ages: dict[str, float | None]) -> Route:
    return Route(
        steps=[
            RouteStep(
                concept_id=cid, mode="full", status="closed",
                closed_at=None if age is None else NOW - age * DAY,
            )
            for cid, age in ages.items()
        ]
    )


def test_backfill_steps_by_age_and_once(conn):
    load_lessons(conn)
    route = _aged_route({"l1_frames": 1, "l1_select": 5, "l1_group": 10, "l2_hist": 40})

    review.backfill(conn, route, now=NOW)

    rows = repos.get_review_rows(conn)
    assert {cid: row.step for cid, row in rows.items()} == {
        "l1_frames": 0, "l1_select": 1, "l1_group": 2, "l2_hist": 3,
    }
    # Срок — не раньше завтра: 40 дней назад + 30 дней уже в прошлом.
    assert all(row.due_at >= NOW + DAY for row in rows.values())
    assert rows["l1_frames"].due_at == NOW + DAY  # (NOW-1д)+1д < NOW+1д
    assert repos.get_fact(conn, "review_backfill") == "1"

    repos.delete_review(conn, "l1_frames")
    review.backfill(conn, route, now=NOW)  # повтор — факт уже стоит
    assert "l1_frames" not in repos.get_review_rows(conn)


def test_backfill_fresh_lesson_due_tomorrow(conn):
    load_lessons(conn)
    review.backfill(conn, _aged_route({"l1_frames": 0.1}), now=NOW)
    # Закрыто 2.4 часа назад, ступень 0: closed_at + 1 день < now + 1 день.
    assert repos.get_review_rows(conn)["l1_frames"].due_at == NOW + DAY


def test_backfill_unknown_age_is_step_zero(conn):
    load_lessons(conn)
    review.backfill(conn, _aged_route({"l1_frames": None}), now=NOW)
    assert repos.get_review_rows(conn)["l1_frames"] == ReviewRow(
        concept_id="l1_frames", step=0, due_at=NOW + DAY
    )


def test_backfill_keeps_existing_rows_and_respects_limit(conn):
    load_lessons(conn)
    repos.upsert_review(conn, "l1_frames", 4, NOW + 20 * DAY)
    route = _aged_route({"l1_frames": 40, "l1_select": 40})
    review.backfill(conn, route, now=NOW)
    rows = repos.get_review_rows(conn)
    assert rows["l1_frames"].step == 4
    assert rows["l1_select"].step == 3


def test_backfill_without_route(conn):
    load_lessons(conn)
    review.backfill(conn, None, now=NOW)
    assert repos.get_review_rows(conn) == {}
    assert repos.get_fact(conn, "review_backfill") == "1"


# --- переключатель ---


def test_enabled_defaults_to_true_and_toggles(conn):
    assert review.enabled(conn) is True
    review.set_enabled(conn, False)
    assert review.enabled(conn) is False
    review.set_enabled(conn, True)
    assert review.enabled(conn) is True


def test_set_enabled_without_commit_rolls_back(conn):
    review.set_enabled(conn, False, commit=False)
    conn.rollback()
    assert review.enabled(conn) is True


async def test_rows_created_when_disabled(conn, settings):
    course = load_lessons(conn)
    graph = CourseGraph(course.nodes, course.edges)
    review.set_enabled(conn, False)
    await _enter(conn, settings)
    for _ in range(2):
        _check(conn, settings)
        await _answer_turn(conn, settings, correct_answer(_pending(conn)))
    await _answer_turn(conn, settings, correct_answer(_pending(conn)), now=4.0)
    assert "l1_frames" in repos.get_review_rows(conn)

    review.set_enabled(conn, True)
    due_at = repos.get_review_rows(conn)["l1_frames"].due_at
    assert "l1_frames" in review.due_lessons(
        conn, graph, _state(conn).route, now=due_at, settings=settings
    )
