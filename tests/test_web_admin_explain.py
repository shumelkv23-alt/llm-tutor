"""Сессии «Объясни новичку» в админке — только с согласия ученика (срез 89)."""

import time
from pathlib import Path

import pytest

from course_fixtures import enter_module, use_lessons_course
from llm_tutor.core import explain
from llm_tutor.db import repos
from llm_tutor.llm.prompt_config import load_explain
from llm_tutor.schemas import ExplainState, IdeaProgress
from llm_tutor.web import accounts

PROMPTS = load_explain(Path(__file__).resolve().parent.parent / "data" / "prompts")
ANN = {"email": "ann@example.com", "name": "Аня", "password": "секретный-пароль"}
ADMIN = {"email": "root@example.com", "name": "Хозяин", "password": "пароль-админа-1"}
SECRET_LINE = "Моё объяснение про Series с уникальной фразой"


async def _sign_up(client, person: dict) -> None:
    response = await client.post("/api/auth/register", json=person)
    assert response.status_code == 201


@pytest.fixture
async def setup(web, web_app, monkeypatch, settings):
    """Аня с одной завершённой сессией; клиент — уже админ."""
    use_lessons_course(monkeypatch)
    await _sign_up(web, ANN)
    assert (await web.post("/api/courses/mlcourse/enroll", json={})).status_code == 200
    ann = accounts.find_user(web_app.state.accounts, ANN["email"])
    conn = web_app.state.userdbs.open(ann.id, "mlcourse")
    enter_module(conn, 2)
    now = time.time()
    session = explain.start(conn, PROMPTS, topic_id=1, now=now, settings=settings)
    repos.add_explain_turn(conn, session.id, "student", SECRET_LINE, ts=now, commit=True)
    state = ExplainState(
        student_turns=1, ideas={"series_vs_frame": IdeaProgress(state="correct", quote="Series")}
    )
    repos.update_explain_session(conn, session.id, state=state, updated_at=now, commit=True)
    explain.finish(conn, session_id=session.id, now=now, settings=settings)
    await _sign_up(web, ADMIN)
    accounts.set_admin(web_app.state.accounts, accounts.find_user(web_app.state.accounts, ADMIN["email"]).id, True)
    return {"client": web, "ann": ann.id, "session": session.id, "conn": conn}


def _card(setup) -> str:
    return f"/admin/students/{setup['ann']}?course=mlcourse"


def _session_url(setup) -> str:
    return f"/admin/students/{setup['ann']}/explain/mlcourse/{setup['session']}"


def _share(setup, value: str) -> None:
    repos.set_fact(setup["conn"], "explain_share", value)


async def test_card_hidden_without_consent(setup) -> None:
    html = (await setup["client"].get(_card(setup))).text
    assert "Объясни новичку" in html
    assert "Ученик не открыл доступ" in html
    assert _session_url(setup) not in html


async def test_session_404_without_consent(setup) -> None:
    assert (await setup["client"].get(_session_url(setup))).status_code == 404


async def test_card_lists_sessions_with_consent(setup) -> None:
    _share(setup, "1")
    html = (await setup["client"].get(_card(setup))).text
    assert "Ученик не открыл доступ" not in html
    assert _session_url(setup) in html
    assert "1 из 3" in html


async def test_session_view_logs_without_text(setup, web_app) -> None:
    _share(setup, "1")
    response = await setup["client"].get(_session_url(setup))
    assert response.status_code == 200
    assert SECRET_LINE in response.text
    log = accounts.admin_log_for(web_app.state.accounts, setup["ann"])
    assert [row.action for row in log] == ["view_explain"]
    row = web_app.state.accounts.execute(
        "SELECT details FROM admin_log WHERE action = 'view_explain'"
    ).fetchone()
    assert SECRET_LINE not in row["details"]
    assert '"session"' in row["details"]


async def test_revoke_consent_immediate(setup) -> None:
    _share(setup, "1")
    assert (await setup["client"].get(_session_url(setup))).status_code == 200
    _share(setup, "0")
    assert (await setup["client"].get(_session_url(setup))).status_code == 404


async def test_unknown_session_404(setup) -> None:
    _share(setup, "1")
    url = f"/admin/students/{setup['ann']}/explain/mlcourse/999"
    assert (await setup["client"].get(url)).status_code == 404


async def test_unknown_course_404(setup) -> None:
    _share(setup, "1")
    url = f"/admin/students/{setup['ann']}/explain/nope/{setup['session']}"
    assert (await setup["client"].get(url)).status_code == 404


async def test_busy_student_not_read(setup, web_app) -> None:
    _share(setup, "1")
    lock = web_app.state.userdbs.lock(setup["ann"], "mlcourse")
    await lock.acquire()
    try:
        response = await setup["client"].get(_session_url(setup))
    finally:
        lock.release()
    assert response.status_code == 200
    assert SECRET_LINE not in response.text
    assert "обновите" in response.text


async def test_non_admin_404(setup, web_app) -> None:
    _share(setup, "1")
    accounts.set_admin(
        web_app.state.accounts, accounts.find_user(web_app.state.accounts, ADMIN["email"]).id, False
    )
    assert (await setup["client"].get(_session_url(setup))).status_code == 404
