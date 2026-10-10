"""Главная и профиль (Срез 47)."""

import time

import pytest

from llm_tutor.student import survey
from llm_tutor.web import accounts
from web_fakes import ScriptedTutor

COURSE = "mlcourse-topic01"
BASE = f"/api/courses/{COURSE}/lesson"


@pytest.fixture
def web_llm():
    return ScriptedTutor("Объясняю.")


def _answers() -> list[int]:
    progress, given = survey.Progress(), []
    while progress.next_key() is not None:
        progress, given = progress.answer(1), [*given, 1]
    return given


async def test_guest_home_is_a_landing(web) -> None:
    html = (await web.get("/")).text

    assert "Начать учиться" in html
    assert 'href="/register"' in html
    assert "Как это работает" in html
    assert "Pandas и разведочный анализ данных" in html


async def test_student_without_courses_is_sent_to_catalog(student) -> None:
    html = (await student.get("/")).text

    assert "Привет, Аня!" in html
    assert 'href="/courses"' in html
    assert "Продолжить занятие" not in html


async def test_student_home_shows_continue_and_steps(student) -> None:
    await student.post(f"/api/courses/{COURSE}/enroll", json={})
    assert "Начать с анкеты" in (await student.get("/")).text

    await student.post(f"{BASE}/survey/finish", json={"answers": _answers()})
    html = (await student.get("/")).text

    assert "Продолжить занятие" in html
    assert "Сейчас:" in html
    assert "Ближайшие шаги" in html
    assert "Решено заданий" in html


async def test_profile_shows_account_survey_and_topics(student) -> None:
    await student.post(f"/api/courses/{COURSE}/enroll", json={})
    await student.post(f"{BASE}/survey/finish", json={"answers": _answers()})

    html = (await student.get("/profile")).text

    assert "ann@example.com" in html
    assert 'id="rename-form"' in html and 'id="password-form"' in html
    assert "Итог анкеты" in html
    assert html.count('<tr class="') == 21
    assert "Текущая" in html


async def test_profile_of_two_students_differs(student, web, web_app) -> None:
    await student.post(f"/api/courses/{COURSE}/enroll", json={})
    await student.post(f"{BASE}/survey/finish", json={"answers": _answers()})
    ann = accounts.find_user(web_app.state.accounts, "ann@example.com")
    conn = web_app.state.userdbs.open(ann.id, COURSE)
    conn.execute(
        "INSERT OR REPLACE INTO mastery (concept_id, alpha, beta, last_seen) VALUES ('groupby', 80, 1, ?)",
        (time.time(),),
    )
    conn.commit()
    assert "Закрыта" in (await student.get("/profile")).text

    await web.post("/api/auth/logout", json={})
    await web.post("/api/auth/register", json={"email": "bob@example.com", "name": "Боб", "password": "пароль-боба"})
    html = (await web.get("/profile")).text

    assert "Курсов пока нет" in html
    assert "ann@example.com" not in html
