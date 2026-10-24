"""Страница «Объясни новичку» (срез 88)."""

import re

import pytest

from course_fixtures import use_lessons_course

PAGE = "/lesson/mlcourse/explain"


@pytest.fixture
async def enrolled(monkeypatch, student):
    use_lessons_course(monkeypatch)
    response = await student.post("/api/courses/mlcourse/enroll", json={})
    assert response.status_code == 200
    return student


async def test_page_renders_for_enrolled(enrolled) -> None:
    response = await enrolled.get(PAGE)
    assert response.status_code == 200
    html = response.text
    assert "Объясни новичку" in html
    assert "Сохраняются реплики сессии, время ходов и отчёт." in html
    assert "Показывать мои сессии преподавателю" in html
    assert 'data-course="mlcourse"' in html
    assert "js/explain.js" in html


async def test_page_topic_from_query(enrolled) -> None:
    html = (await enrolled.get(f"{PAGE}?topic=2")).text
    assert 'data-topic="2"' in html


async def test_bad_topic_query_ignored(enrolled) -> None:
    response = await enrolled.get(f"{PAGE}?topic=abc")
    assert response.status_code == 200
    assert 'data-topic=""' in response.text


async def test_history_page(enrolled) -> None:
    html = (await enrolled.get(f"{PAGE}/5")).text
    assert 'data-session="5"' in html


async def test_page_requires_login(web, monkeypatch) -> None:
    use_lessons_course(monkeypatch)
    response = await web.get(PAGE)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


async def test_page_not_enrolled_redirects_to_course(student, monkeypatch) -> None:
    use_lessons_course(monkeypatch)
    response = await student.get(PAGE)
    assert response.status_code == 303
    assert response.headers["location"] == "/lesson/mlcourse"


async def test_unknown_course_404(student) -> None:
    assert (await student.get("/lesson/nope/explain")).status_code == 404


async def test_no_inline_script_or_style(enrolled) -> None:
    html = (await enrolled.get(PAGE)).text
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html)
    assert 'style="' not in html


async def test_lesson_page_links_to_explain(enrolled) -> None:
    html = (await enrolled.get("/lesson/mlcourse")).text
    assert 'href="/lesson/mlcourse/explain"' in html
