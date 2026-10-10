"""Регрессии по ревью срезов 38–41."""

import sqlite3

import pytest

from llm_tutor.course.seed import load_seed
from llm_tutor.db import repos
from llm_tutor.db.connection import get_conn, migrate
from llm_tutor.student import survey
from llm_tutor.web import accounts, userdb
from llm_tutor.web.cli import main as cli_main
from llm_tutor.web.security import LoginLimiter
from llm_tutor.web.userdb import UserDBPool
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


@pytest.fixture
async def lesson(student):
    await student.post(f"/api/courses/{COURSE}/enroll", json={})
    await student.post(f"{BASE}/survey/finish", json={"answers": _answers()})
    return student


def _db(web_app):
    user = accounts.find_user(web_app.state.accounts, "ann@example.com")
    return web_app.state.userdbs.open(user.id, COURSE)


async def test_history_replaces_task_with_note(lesson) -> None:
    await lesson.post(f"{BASE}/skip", json={})
    live = (await lesson.post(f"{BASE}/task", json={})).json()
    prompt = live["state"]["item"]["prompt_html"]

    history = (await lesson.get(f"{BASE}/state")).json()["state"]["messages"]

    assert "Практика" in history[-1]["html"]
    assert prompt not in history[-1]["html"]


async def test_model_still_sees_the_task_in_journal(lesson, web_app) -> None:
    await lesson.post(f"{BASE}/skip", json={})
    state = (await lesson.post(f"{BASE}/task", json={})).json()["state"]
    conn = _db(web_app)
    item = repos.get_item(conn, state["item"]["id"])

    last = repos.get_messages(conn, repos.get_open_session(conn))[-1]

    assert item.prompt in last.content


async def test_closed_count_matches_course_card(lesson, web_app) -> None:
    import time

    conn = _db(web_app)
    conn.execute(
        "INSERT OR REPLACE INTO mastery (concept_id, alpha, beta, last_seen) VALUES ('groupby', 80, 1, ?)",
        (time.time(),),
    )
    conn.commit()
    await lesson.post(f"{BASE}/switch", json={"node_id": "groupby"})

    state = (await lesson.get(f"{BASE}/state")).json()["state"]
    card = (await lesson.get("/api/courses")).json()["courses"][0]

    assert state["closed"] == card["closed"] >= 1


async def test_unexpected_core_failure_is_a_reply(lesson, monkeypatch) -> None:
    """Сбой вне ядра (не перехваченный им) тоже даёт реплику, а не 500."""
    from llm_tutor.core import turn

    async def broken(*args, **kwargs):
        raise RuntimeError("всё сломалось")

    monkeypatch.setattr(turn, "handle_turn", broken)
    response = await lesson.post(f"{BASE}/chat", json={"text": "привет"})

    assert response.status_code == 200
    assert response.json()["messages"][-1]["role"] == "assistant"


async def test_broken_student_db(student, web_app, tmp_path) -> None:
    await student.post(f"/api/courses/{COURSE}/enroll", json={})
    user = accounts.find_user(web_app.state.accounts, "ann@example.com")
    pool = web_app.state.userdbs
    path = pool.path(user.id, COURSE)
    pool.close_all()
    path.write_bytes(b"not a database at all" * 100)

    page = await student.get("/courses")
    api = await student.get("/api/courses")
    state = await student.get(f"{BASE}/state")

    assert page.status_code == 200
    assert api.json()["courses"][0]["status"] == "error"
    assert state.status_code == 503
    assert state.json()["detail"]["message"]


def test_materials_with_unknown_concept_are_not_resynced(tmp_path) -> None:
    materials = tmp_path / "materials.sqlite3"
    conn = get_conn(str(materials))
    migrate(conn)
    conn.execute("INSERT INTO concepts (id, name) VALUES ('only_in_materials', 'x')")
    conn.execute(
        "INSERT INTO chunks (concept_id, source_url, section, seq, content) "
        "VALUES ('only_in_materials', 'u', 's', 0, 'про groupby')"
    )
    conn.commit()
    conn.close()
    student = get_conn(str(tmp_path / "student.sqlite3"))
    migrate(student)
    load_seed(student)

    assert userdb.sync_materials(student, materials) is True
    assert userdb.sync_materials(student, materials) is False
    student.close()


def test_limiter_forgets_old_emails() -> None:
    clock = {"now": 0.0}
    limiter = LoginLimiter(clock=lambda: clock["now"])
    limiter.PRUNE_AT = 50
    for index in range(50):
        limiter.failure(f"u{index}@x.io")
    clock["now"] += limiter.window + 1

    limiter.failure("fresh@x.io")

    assert len(limiter._failures) == 1


def _register(settings) -> accounts.User:
    db = accounts.open_accounts(settings.accounts_db_path)
    user = accounts.create_user(db, email="ann@example.com", name="Аня", password="секретный-пароль")
    db.close()
    return user


def test_import_legacy_bad_source_leaves_nothing_behind(web_settings, tmp_path) -> None:
    user = _register(web_settings)
    bad = tmp_path / "bad.sqlite3"
    bad.write_bytes(b"garbage" * 200)
    args = ["import-legacy", "--email", "ann@example.com", "--db", str(bad)]

    assert cli_main(args, settings=web_settings) == 1
    target = UserDBPool(tmp_path / "users", tmp_path / "m.sqlite3").path(user.id, COURSE)
    assert not target.exists()
    assert not list(target.parent.glob(".*.import")) if target.parent.exists() else True


@pytest.mark.parametrize("folder", ["a#b", "a?b"])
def test_import_legacy_path_with_uri_characters(web_settings, tmp_path, folder: str) -> None:
    user = _register(web_settings)
    source_dir = tmp_path / folder
    source_dir.mkdir()
    legacy = source_dir / "old.sqlite3"
    conn = get_conn(str(legacy))
    migrate(conn)
    load_seed(conn)
    repos.set_fact(conn, "legacy_marker", "да", source="self")
    conn.close()

    assert cli_main(["import-legacy", "--email", "ann@example.com", "--db", str(legacy)], settings=web_settings) == 0
    target = UserDBPool(tmp_path / "users", tmp_path / "m.sqlite3").path(user.id, COURSE)
    copied = sqlite3.connect(target)
    assert copied.execute("SELECT value FROM facts WHERE key = 'legacy_marker'").fetchone()[0] == "да"
    copied.close()
