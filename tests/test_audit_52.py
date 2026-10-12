"""Регрессии по ревью среза 52: переименование курса, анкета модуля, главная."""

import logging

import pytest
from course_fixtures import WEB_T1 as T1

from llm_tutor.db import repos
from llm_tutor.db.connection import get_conn, migrate
from llm_tutor.student import survey
from llm_tutor.web import accounts
from llm_tutor.web.cli import main as cli_main
from llm_tutor.web.userdb import UserDBPool
from web_fakes import ScriptedTutor

COURSE = "mlcourse"
OLD = "mlcourse-topic01"
BASE = f"/api/courses/{COURSE}/lesson"


@pytest.fixture
def web_llm():
    return ScriptedTutor("Объясняю тему.")


@pytest.fixture
async def enrolled(student):
    response = await student.post(f"/api/courses/{COURSE}/enroll", json={})
    assert response.status_code == 200
    return student


def _answers(config) -> list[int]:
    progress, given = survey.Progress(config), []
    while (key := progress.next_key()) is not None:
        index = min(1, len(survey.options_for(config, key)) - 1)
        progress, given = progress.answer(index), [*given, index]
    return given


def _user_db(web_app):
    user = accounts.find_user(web_app.state.accounts, "ann@example.com")
    return web_app.state.userdbs.open(user.id, COURSE)


def _db_with_fact(path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_conn(str(path))
    migrate(conn)
    repos.set_fact(conn, "secret", value, source="self")
    conn.close()


# --- M1: import-legacy поверх прогресса веба под прежним id ---


def test_import_legacy_refuses_when_web_progress_is_under_old_id(web_settings, tmp_path) -> None:
    db = accounts.open_accounts(web_settings.accounts_db_path)
    user = accounts.create_user(db, email="ann@example.com", name="Аня", password="секретный-пароль")
    db.close()
    users = tmp_path / "users"
    _db_with_fact(users / str(user.id) / f"{OLD}.sqlite3", "web-progress")
    legacy = tmp_path / "bot.sqlite3"
    _db_with_fact(legacy, "bot")

    code = cli_main(["import-legacy", "--email", "ann@example.com", "--db", str(legacy)], settings=web_settings)

    assert code == 1
    pool = UserDBPool(users, tmp_path / "materials.sqlite3")
    assert repos.get_fact(pool.open(user.id, COURSE), "secret") == "web-progress"
    pool.close_all()


# --- L4: два файла рядом и осиротевший WAL ---


def test_both_db_files_leave_a_warning(tmp_path, caplog) -> None:
    users = tmp_path / "users"
    _db_with_fact(users / "1" / f"{OLD}.sqlite3", "old")
    _db_with_fact(users / "1" / f"{COURSE}.sqlite3", "new")
    pool = UserDBPool(users, tmp_path / "materials.sqlite3")

    with caplog.at_level(logging.WARNING, logger="llm_tutor.web.userdb"):
        assert repos.get_fact(pool.open(1, COURSE), "secret") == "new"

    assert "старая не подхвачена" in caplog.text
    pool.close_all()


def test_orphan_wal_of_new_name_does_not_block_adoption(tmp_path) -> None:
    users = tmp_path / "users"
    _db_with_fact(users / "1" / f"{OLD}.sqlite3", "old")
    # Старый -shm переезжает на место осиротевшего нового: rename на Windows упал бы.
    (users / "1" / f"{OLD}.sqlite3-shm").write_bytes(b"")
    (users / "1" / f"{COURSE}.sqlite3-shm").write_bytes(b"")
    pool = UserDBPool(users, tmp_path / "materials.sqlite3")

    assert repos.get_fact(pool.open(1, COURSE), "secret") == "old"
    pool.close_all()


# --- M2: из анкеты модуля 2 можно вернуться в модуль 1 ---
# С среза 56 модули 2–5 настоящего курса ещё «скоро» — сценарии идут на
# курсе-фикстуре занятий, где открыты оба модуля.


@pytest.fixture
async def lessons(monkeypatch, student, web_app):
    from course_fixtures import use_lessons_course

    use_lessons_course(monkeypatch)
    response = await student.post(f"/api/courses/{COURSE}/enroll", json={})
    assert response.status_code == 200
    return student


async def _finish_module_1(client, web_app) -> None:
    config = repos.get_topic(_user_db(web_app), 1).survey
    response = await client.post(f"{BASE}/survey/finish", json={"answers": _answers(config)})
    assert response.status_code == 200, response.text


async def test_student_can_leave_module_survey_back_to_previous_module(lessons, web_app) -> None:
    enrolled = lessons
    await _finish_module_1(enrolled, web_app)
    state = (await enrolled.post(f"{BASE}/switch", json={"node_id": "l2_box"})).json()["state"]
    assert state["survey"] is True and state["survey_topic"]["number"] == 2
    back = state["survey_back"]
    assert back["module"] == 1
    conn = _user_db(web_app)
    assert next(c for c in repos.get_concepts(conn) if c.id == back["node_id"]).topic_id == 1

    response = await enrolled.post(f"{BASE}/switch", json={"node_id": back["node_id"]})

    assert response.status_code == 200, response.text
    state = response.json()["state"]
    assert state["survey"] is False and state["node"]["id"] == back["node_id"]
    chat = await enrolled.post(f"{BASE}/chat", json={"text": "привет"})
    assert chat.status_code == 200


async def test_module_survey_still_blocks_jump_to_another_unsurveyed_module(lessons, web_app) -> None:
    await _finish_module_1(lessons, web_app)
    await lessons.post(f"{BASE}/switch", json={"node_id": "l2_box"})

    # Занятие того же (не пройденного по анкете) модуля — только после анкеты.
    response = await lessons.post(f"{BASE}/switch", json={"node_id": "l2_corr"})

    assert response.status_code == 409


async def test_first_course_survey_has_no_way_back_and_blocks_switch(enrolled) -> None:
    state = (await enrolled.get(f"{BASE}/state")).json()["state"]
    assert state["survey"] is True and state["survey_back"] is None

    response = await enrolled.post(f"{BASE}/switch", json={"node_id": "grouping"})

    assert response.status_code == 409


# --- L1: анкета модуля 2 — не «начало курса» ---


async def test_home_continues_course_during_module_survey(lessons, web_app) -> None:
    enrolled = lessons
    await _finish_module_1(enrolled, web_app)
    state = (await enrolled.post(f"{BASE}/switch", json={"node_id": "l2_box"})).json()["state"]
    assert state["survey"] is True and state["survey_topic"]["number"] == 2

    html = (await enrolled.get("/")).text

    assert "Начать с анкеты" not in html
    assert "Начнём с короткой анкеты" not in html
    assert "Продолжить занятие" in html
    card = (await enrolled.get("/api/courses")).json()["courses"][0]
    assert card["status"] == "in_progress"


async def test_home_still_starts_with_first_survey(enrolled) -> None:
    html = (await enrolled.get("/")).text

    assert "Начать с анкеты" in html


# --- L2, L3: старый адрес курса ---


async def test_old_lesson_address_keeps_query(student) -> None:
    response = await student.get(f"/lesson/{OLD}?tab=practice")

    assert response.status_code == 308
    assert response.headers["location"] == f"/lesson/{COURSE}?tab=practice"


async def test_old_api_address_redirects_with_method(enrolled) -> None:
    response = await enrolled.get(f"/api/courses/{OLD}/lesson/state")
    assert response.status_code == 308
    assert response.headers["location"] == f"{BASE}/state"

    response = await enrolled.post(f"/api/courses/{OLD}/lesson/chat?x=1", json={"text": "привет"})
    assert response.status_code == 308
    assert response.headers["location"] == f"{BASE}/chat?x=1"
