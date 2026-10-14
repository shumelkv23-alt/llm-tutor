"""Главная и профиль (Срез 47)."""

from course_fixtures import WEB_T1 as T1
import time

import pytest

from llm_tutor.student import survey
from llm_tutor.web import accounts
from web_fakes import ScriptedTutor

from llm_tutor.web.courses import COURSES as _COURSES, course_data as _course_data

# Занятий в открытых ученикам модулях (неопубликованные — «скоро», срез 55).
_PUBLISHED = {t.number for t in _course_data(_COURSES[0]).topics if t.published}
TOPICS = sum(1 for n in _course_data(_COURSES[0]).nodes if n.topic_id in _PUBLISHED)
TITLE = _COURSES[0].title


COURSE = "mlcourse"
BASE = f"/api/courses/{COURSE}/lesson"


@pytest.fixture
def web_llm():
    return ScriptedTutor("Объясняю.")


def _answers() -> list[int]:
    progress, given = survey.Progress(T1), []
    while progress.next_key() is not None:
        progress, given = progress.answer(1), [*given, 1]
    return given


async def test_guest_home_is_a_landing(web) -> None:
    html = (await web.get("/")).text

    assert "Начать учиться" in html
    assert 'href="/register"' in html
    assert "Как это работает" in html
    assert TITLE in html


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


async def test_profile_is_a_short_summary_of_the_current_course(student) -> None:
    """Срез 69: аккаунт и краткая сводка по курсу — без анкеты, тем и модулей."""
    await student.post(f"/api/courses/{COURSE}/enroll", json={})
    await student.post(f"{BASE}/survey/finish", json={"answers": _answers()})

    html = (await student.get("/profile")).text

    assert "ann@example.com" in html
    assert 'id="rename-form"' in html and 'id="password-form"' in html
    assert TITLE in html and "Сейчас:" in html
    assert f"0 из {TOPICS}" in html
    assert "Итог анкеты" not in html
    assert 'class="topic-row' not in html and 'class="module-row' not in html
    assert "Модуль 1" not in html


async def test_profile_before_survey_points_to_it(student) -> None:
    await student.post(f"/api/courses/{COURSE}/enroll", json={})

    html = (await student.get("/profile")).text

    assert TITLE in html and "анкет" in html


async def test_profile_of_two_students_differs(student, web, web_app) -> None:
    await student.post(f"/api/courses/{COURSE}/enroll", json={})
    await student.post(f"{BASE}/survey/finish", json={"answers": _answers()})
    ann = accounts.find_user(web_app.state.accounts, "ann@example.com")
    conn = web_app.state.userdbs.open(ann.id, COURSE)
    conn.execute(
        "INSERT OR REPLACE INTO mastery (concept_id, alpha, beta, last_seen) VALUES ('grouping', 80, 1, ?)",
        (time.time(),),
    )
    conn.commit()
    assert f"1 из {TOPICS}" in (await student.get("/profile")).text

    await web.post("/api/auth/logout", json={})
    await web.post("/api/auth/register", json={"email": "bob@example.com", "name": "Боб", "password": "пароль-боба"})
    html = (await web.get("/profile")).text

    assert "Курсов пока нет" in html
    assert "ann@example.com" not in html


# --- Срез 69: без фоновых надписей и «знакомых на проверку» ---


@pytest.mark.parametrize("path", ["/", "/courses", "/profile", "/login"])
async def test_no_footer_or_connection_badge(student, path) -> None:
    html = (await student.get(path)).text

    assert "Модель объясняет, код проверяет" not in html
    assert "по материалам mlcourse.ai" not in html
    assert 'id="connection"' not in html and "Подключение…" not in html


async def test_home_does_not_count_claimed_lessons(student, web_app) -> None:
    from llm_tutor.web.views import learning_view

    await student.post(f"/api/courses/{COURSE}/enroll", json={})
    progress, given = survey.Progress(T1), []
    while (key := progress.next_key()) is not None:  # самый уверенный вариант — будут знакомые
        index = len(survey.options_for(T1, key)) - 1
        progress, given = progress.answer(index), [*given, index]
    finish = await student.post(f"{BASE}/survey/finish", json={"answers": given})
    assert finish.status_code == 200
    ann = accounts.find_user(web_app.state.accounts, "ann@example.com")
    conn = web_app.state.userdbs.open(ann.id, COURSE)
    learning = learning_view(conn, settings=web_app.state.settings)
    assert any(topic["status"] == "claimed" for topic in learning["topics"])

    html = (await student.get("/")).text

    assert "Знакомых на проверку" not in html
    assert "Знакомая — проверим" not in html
    assert "Решено заданий" in html and "Занятий впереди" in html
