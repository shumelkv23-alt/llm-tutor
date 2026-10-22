"""Тесты таблицы расписания повторений и арифметики лестницы (срез 79)."""

from course_fixtures import load_lessons

from llm_tutor.db import repos
from llm_tutor.schemas import ReviewRow
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
