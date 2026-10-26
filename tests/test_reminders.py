"""Напоминания о повторении: письмо, день-отметка, opt-in и dry-run."""

import pytest
from legacy_course import load_seed
from web_fakes import RecordingMailer

from llm_tutor.db import repos
from llm_tutor.student import review
from llm_tutor.web import accounts, courses, reminders

NOW = 1_800_000_000.0
DAY = 86_400.0


def _account() -> accounts.User:
    return accounts.User(id=1, email="ann@example.com", name="Аня", created_at=0.0)


@pytest.fixture
def due_item(conn, monkeypatch):
    """Один концепт с просроченным повторением; банк и занятие — из seed."""
    load_seed(conn)
    monkeypatch.setattr(reminders.review, "due_lessons", lambda *args, **kwargs: ["groupby"])
    repos.upsert_review(conn, "groupby", 1, NOW - 2 * DAY)
    return conn


async def test_reminder_is_sent_once_per_day(due_item, settings) -> None:
    mailer = RecordingMailer()

    first = await reminders.send_for_student(
        due_item,
        courses.COURSES[0],
        _account(),
        mailer,
        now=NOW,
        base_url="https://tutor.example",
        settings=settings,
    )

    assert first is not None
    assert len(first.entries) == 1
    assert len(first.entries[0].lessons) == 1
    assert len(mailer.sent) == 1
    to, subject, text = mailer.sent[0]
    assert to == "ann@example.com"
    assert "повторить" in subject.lower()
    assert "https://tutor.example/lesson/mlcourse" in text
    assert first.entries[0].lessons[0] in text
    assert repos.get_fact(due_item, reminders.FACT) == reminders._day(NOW)

    again = await reminders.send_for_student(
        due_item,
        courses.COURSES[0],
        _account(),
        mailer,
        now=NOW + 3600,
        base_url="https://tutor.example",
        settings=settings,
    )

    assert again is None
    assert len(mailer.sent) == 1


async def test_reminders_respect_opt_out(due_item, settings) -> None:
    review.set_enabled(due_item, False)
    mailer = RecordingMailer()

    reminder = await reminders.send_for_student(
        due_item,
        courses.COURSES[0],
        _account(),
        mailer,
        now=NOW,
        base_url="https://tutor.example",
        settings=settings,
    )

    assert reminder is None
    assert mailer.sent == []
    assert repos.get_fact(due_item, reminders.FACT) is None


async def test_dry_run_does_not_send_or_mark(due_item, settings) -> None:
    mailer = RecordingMailer()

    reminder = await reminders.send_for_student(
        due_item,
        courses.COURSES[0],
        _account(),
        mailer,
        now=NOW,
        base_url="https://tutor.example",
        settings=settings,
        dry_run=True,
    )

    assert reminder is not None
    assert mailer.sent == []
    assert repos.get_fact(due_item, reminders.FACT) is None


def test_letter_lists_lessons_and_overdue() -> None:
    entry = reminders.ReminderEntry(
        course_id="mlcourse",
        course_title="Курс",
        lessons=("Series и DataFrame",),
        details=(
            "Series и DataFrame — " + reminders._overdue(NOW - 2 * DAY, NOW),
        ),
    )

    subject, text = reminders.letter_for(
        "Аня",
        [entry],
        base_url="https://tutor.example/",
        unsubscribe="https://tutor.example/unsubscribe?token=x",
    )

    assert "1 занятие" in subject
    assert "Series и DataFrame" in text
    assert "просрочено на 2 дня" in text
    assert "https://tutor.example/lesson/mlcourse" in text
    assert "https://tutor.example/unsubscribe?token=x" in text


def test_unsubscribe_token_roundtrip(settings) -> None:
    token = reminders.token_for(settings, 7)

    assert reminders.verify_token(settings, token) == 7
    assert reminders.verify_token(settings, token + "x") is None
    assert reminders.verify_token(settings, "не-токен") is None


async def test_unsubscribe_api_turns_emails_off(web_app, web_settings, student) -> None:
    course = courses.COURSES[0]
    assert (
        await student.post(f"/api/courses/{course.id}/enroll", json={})
    ).status_code == 200
    user = accounts.find_user(web_app.state.accounts, "ann@example.com")
    assert user is not None
    token = reminders.token_for(web_settings, user.id)

    response = await student.post("/api/unsubscribe", json={"token": token})
    assert response.status_code == 200

    conn = web_app.state.userdbs.open(user.id, course.id)
    assert reminders.reminders_enabled(conn) is False

    again = await student.post(
        "/api/unsubscribe", json={"token": token, "enabled": True}
    )
    assert again.status_code == 200
    assert reminders.reminders_enabled(conn) is True
    assert (
        await student.post("/api/unsubscribe", json={"token": "плохой.токен"})
    ).status_code == 404


async def test_send_reminders_walks_enrolled_students(
    web_app, web_settings, student, monkeypatch
) -> None:
    """Полный обход: аккаунты, запись, БД ученика и dry-run без отправки."""
    course = courses.COURSES[0]
    assert (
        await student.post(f"/api/courses/{course.id}/enroll", json={})
    ).status_code == 200
    user = accounts.find_user(web_app.state.accounts, "ann@example.com")
    assert user is not None
    conn = web_app.state.userdbs.open(user.id, course.id)
    monkeypatch.setattr(reminders.review, "due_lessons", lambda *args, **kwargs: ["grouping"])
    repos.upsert_review(conn, "grouping", 1, NOW - 2 * DAY)
    conn.commit()
    mailer = RecordingMailer()

    sent = await reminders.send_reminders(
        web_settings, mailer, now=NOW, base_url="https://tutor.example", dry_run=True
    )

    assert len(sent) == 1
    assert sent[0].email == "ann@example.com"
    assert mailer.sent == []



async def test_unsubscribe_page_shows_button_or_error(web_app, web_settings, student) -> None:
    user = accounts.find_user(web_app.state.accounts, "ann@example.com")
    assert user is not None
    token = reminders.token_for(web_settings, user.id)

    page = await student.get(f"/unsubscribe?token={token}")
    bad = await student.get("/unsubscribe?token=bad")

    assert page.status_code == 200
    assert "Отписаться" in page.text
    assert "Включить снова" in page.text
    assert bad.status_code == 200
    assert "недействительна" in bad.text
