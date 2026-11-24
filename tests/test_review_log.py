"""Журнал повторений: ответы и соблюдение срока."""

from llm_tutor.db import repos


def test_review_log_counts_answered_and_on_time(conn) -> None:
    repos.add_review_log(
        conn, None, due_at=100.0, answered_at=90.0, passed=True, step=1
    )
    repos.add_review_log(
        conn, None, due_at=100.0, answered_at=200.0, passed=False, step=2
    )
    repos.add_review_log(
        conn, None, due_at=None, answered_at=300.0, passed=True, step=None
    )

    answered, on_time = repos.review_stats(conn)

    assert answered == 3
    assert on_time == 2
