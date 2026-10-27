"""Вкладка «Объясни» на странице занятия (срез 95)."""

import re
from pathlib import Path

import pytest

from course_fixtures import use_lessons_course

STATIC = Path(__file__).resolve().parent.parent / "src" / "llm_tutor" / "web" / "static"
PAGE = "/lesson/mlcourse"


@pytest.fixture
async def page(monkeypatch, student) -> str:
    use_lessons_course(monkeypatch)
    response = await student.post("/api/courses/mlcourse/enroll", json={})
    assert response.status_code == 200
    return (await student.get(PAGE)).text


async def test_explain_tab_present(page) -> None:
    assert 'id="tab-btn-explain"' in page and 'id="tab-explain"' in page
    assert 'aria-controls="tab-explain"' in page
    assert "js/explain.js" in page and "css/explain.css" in page


async def test_no_separate_explain_link_in_header(page) -> None:
    assert 'href="/lesson/mlcourse/explain' not in page


async def test_tabs_carry_lock_hint(page) -> None:
    # Закрытые на время задания вкладки подписаны «откроется после объяснения».
    assert page.count("откроется после объяснения") == 4


async def test_chat_head_is_switchable(page) -> None:
    # Правый чат на время задания становится наставником: заголовок меняет JS.
    assert 'id="chat-title"' in page and 'id="chat-subtitle"' in page
    assert 'id="view-chat-label"' in page


async def test_no_inline_script_or_style(page) -> None:
    scripts = re.findall(r"<script\b[^>]*>", page)
    assert scripts and all("src=" in tag for tag in scripts)
    assert 'style="' not in page


def test_explain_js_is_self_contained() -> None:
    source = (STATIC / "js" / "explain.js").read_text(encoding="utf-8")
    # Свои ходы идут через общий ход занятия; отдельного API у вкладки нет.
    for call in ("explain/turn", "explain/start", "explain/stop", "explain/continue",
                 "explain/settings", "explain/sessions"):
        assert call in source, call
    assert "Сохраняются реплики сессии, время ходов и отчёт." in source
    assert "Показывать мои сессии преподавателю" in source
    # CSP без inline-стилей: ширину полоски ставят через CSSOM, а не атрибутом.
    assert 'setAttribute("style"' not in source and "cssText" not in source


def test_route_js_has_no_explain_link() -> None:
    assert "/explain" not in (STATIC / "js" / "route.js").read_text(encoding="utf-8")


def test_chat_js_knows_mentor_mode() -> None:
    source = (STATIC / "js" / "chat.js").read_text(encoding="utf-8")
    assert "Наставник" in source and "Спроси наставника" in source
