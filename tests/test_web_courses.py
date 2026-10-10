"""Запись на курс и «Мои курсы» (Срез 39)."""

import sqlite3

import pytest

from llm_tutor.course.seed import load_seed
from llm_tutor.db import repos
from llm_tutor.db.connection import get_conn, migrate
from llm_tutor.core.overview import course_progress
from llm_tutor.web import accounts
from llm_tutor.web.cli import main as cli_main

COURSE = "mlcourse-topic01"
BOB = {"email": "bob@example.com", "name": "Боб", "password": "пароль-боба"}


async def _card(client) -> dict:
    courses = (await client.get("/api/courses")).json()["courses"]
    return next(card for card in courses if card["id"] == COURSE)


async def test_catalog_lists_the_course_not_enrolled(student) -> None:
    card = await _card(student)

    assert card["title"] == "Pandas и разведочный анализ данных"
    assert card["enrolled"] is False
    assert card["status"] == "available"
    assert card["topics"] == 21


async def test_catalog_requires_login(web) -> None:
    assert (await web.get("/api/courses")).status_code == 401


async def test_enroll_creates_db_and_is_idempotent(student, web_settings, tmp_path) -> None:
    first = await student.post(f"/api/courses/{COURSE}/enroll", json={})
    second = await student.post(f"/api/courses/{COURSE}/enroll", json={})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["course"]["enrolled"] is True
    assert first.json()["course"]["status"] == "survey"
    assert first.json()["course"]["href"] == f"/lesson/{COURSE}"
    files = list((tmp_path / "users").rglob("*.sqlite3"))
    assert len(files) == 1


async def test_enroll_unknown_course(student) -> None:
    response = await student.post("/api/courses/..%2F..%2Fetc/enroll", json={})

    assert response.status_code == 404


async def test_progress_counts_closed_topics(student, web_app) -> None:
    await student.post(f"/api/courses/{COURSE}/enroll", json={})
    user = accounts.authenticate(web_app.state.accounts, "ann@example.com", "секретный-пароль")
    conn = web_app.state.userdbs.open(user.id, COURSE)
    for key in ("block_python", "block_tables", "block_loading", "block_selection", "block_analysis"):
        repos.set_fact(conn, key, "Впервые вижу", source="self")
    conn.execute("UPDATE mastery SET alpha = 1, beta = 1")
    conn.execute(
        "INSERT OR REPLACE INTO mastery (concept_id, alpha, beta, last_seen) VALUES ('python_basics', 60, 1, ?)",
        (__import__("time").time(),),
    )
    conn.commit()

    card = await _card(student)

    assert card["status"] == "in_progress"
    assert card["closed"] == 1


async def test_students_do_not_see_each_other(student, web, web_app) -> None:
    await student.post(f"/api/courses/{COURSE}/enroll", json={})
    await web.post("/api/auth/logout", json={})
    await web.post("/api/auth/register", json=BOB)

    card = await _card(web)

    assert card["enrolled"] is False


async def test_courses_page_renders_cards(student) -> None:
    html = (await student.get("/courses")).text

    assert "Pandas и разведочный анализ данных" in html
    assert "Записаться" in html

    await student.post(f"/api/courses/{COURSE}/enroll", json={})
    html = (await student.get("/courses")).text
    assert "Мои курсы" in html
    assert f'href="/lesson/{COURSE}"' in html


async def test_lesson_without_course_goes_to_catalog(student) -> None:
    response = await student.get("/lesson")

    assert response.status_code == 303
    assert response.headers["location"] == "/courses"


async def test_lesson_redirects_to_enrolled_course(student) -> None:
    await student.post(f"/api/courses/{COURSE}/enroll", json={})

    response = await student.get("/lesson")

    assert response.headers["location"] == f"/lesson/{COURSE}"


async def test_lesson_page_offers_enrollment(student) -> None:
    response = await student.get(f"/lesson/{COURSE}")

    assert response.status_code == 200
    assert "Записаться" in response.text


async def test_lesson_page_for_unknown_course(student) -> None:
    assert (await student.get("/lesson/nope")).status_code == 404


async def test_lesson_page_requires_login(web) -> None:
    response = await web.get(f"/lesson/{COURSE}")

    assert response.status_code == 303


def test_course_progress_on_fresh_db(conn, settings) -> None:
    load_seed(conn)

    progress = course_progress(conn, settings=settings)

    assert progress.total == 21
    assert progress.closed == 0
    assert progress.survey_done is False
    assert progress.current_name is None


# --- Перенос старой БД бота ---


def _legacy(path) -> None:
    conn = get_conn(str(path))
    migrate(conn)
    load_seed(conn)
    repos.set_fact(conn, "legacy_marker", "да", source="self")
    conn.close()


def test_import_legacy(web_settings, tmp_path, capsys) -> None:
    legacy = tmp_path / "old.sqlite3"
    _legacy(legacy)
    db = accounts.open_accounts(web_settings.accounts_db_path)
    user = accounts.create_user(db, email="ann@example.com", name="Аня", password="секретный-пароль")
    db.close()

    code = cli_main(["import-legacy", "--email", "ANN@example.com", "--db", str(legacy)], settings=web_settings)

    assert code == 0
    target = tmp_path / "users" / str(user.id) / f"{COURSE}.sqlite3"
    copied = sqlite3.connect(target)
    assert copied.execute("SELECT value FROM facts WHERE key = 'legacy_marker'").fetchone()[0] == "да"
    copied.close()
    db = accounts.open_accounts(web_settings.accounts_db_path)
    assert db.execute("SELECT COUNT(*) FROM enrollments").fetchone()[0] == 1
    db.close()


def test_import_legacy_refuses_to_overwrite(web_settings, tmp_path) -> None:
    legacy = tmp_path / "old.sqlite3"
    _legacy(legacy)
    db = accounts.open_accounts(web_settings.accounts_db_path)
    accounts.create_user(db, email="ann@example.com", name="Аня", password="секретный-пароль")
    db.close()
    args = ["import-legacy", "--email", "ann@example.com", "--db", str(legacy)]

    assert cli_main(args, settings=web_settings) == 0
    assert cli_main(args, settings=web_settings) == 1


@pytest.mark.parametrize("email", ["nobody@example.com"])
def test_import_legacy_unknown_user(web_settings, tmp_path, email: str) -> None:
    legacy = tmp_path / "old.sqlite3"
    _legacy(legacy)

    assert cli_main(["import-legacy", "--email", email, "--db", str(legacy)], settings=web_settings) == 1
