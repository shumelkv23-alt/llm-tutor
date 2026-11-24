"""Скрипт подготовки показа: курс проходится целиком через API, с событиями в журнале."""

import argparse
import importlib.util
from pathlib import Path

import httpx
import pytest

from llm_tutor.config import Settings
from llm_tutor.db.connection import get_conn
from llm_tutor.web.app import create_app

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "pass_course.py"
spec = importlib.util.spec_from_file_location("pass_course", SCRIPT)
pass_course = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pass_course)

DEMO = "trees-demo"


async def _run(tmp_path: Path, **options) -> dict:
    settings = Settings(
        _env_file=None,
        openrouter_api_key="test-key",
        accounts_db_path=str(tmp_path / "accounts.sqlite3"),
        users_dir=str(tmp_path / "users"),
        materials_db_path=str(tmp_path / "materials.sqlite3"),
        courses_dir=str(tmp_path / "courses"),
    )
    stub = pass_course.StubLLM()
    app = create_app(settings, client=stub, mailer=pass_course.NullMailer())
    args = argparse.Namespace(
        email="ann@example.com", password="секретный-пароль", name="Аня", register=True,
        course=DEMO, wrong_every=0, survey_answer=1,
    )
    vars(args).update(options)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
            await pass_course._sign_in(client, args)
            await pass_course._enroll(client, DEMO)
            return await pass_course.pass_course(client, stub, DEMO, args)
    finally:
        app.state.accounts.close()


def _events(tmp_path: Path) -> dict[str, int]:
    conn = get_conn(str(next((tmp_path / "users").glob(f"*/{DEMO}.sqlite3"))))
    try:
        return dict(conn.execute("SELECT source, count(*) FROM events GROUP BY source").fetchall())
    finally:
        conn.close()


async def test_whole_course_is_passed_with_real_events(tmp_path) -> None:
    result = await _run(tmp_path)

    assert result["closed"] == result["total"] == 5
    assert result["surveys"] == 1 and result["wrong"] == 0
    # Три важных занятия демо-курса — три объяснения новичку.
    assert result["explained"] == 3
    events = _events(tmp_path)
    # В отличие от close-module, в журнале — настоящие свидетельства всех видов.
    assert {"self", "checked", "autotest", "explain"} <= set(events)


@pytest.mark.parametrize("every", [3])
async def test_wrong_every_leaves_mistakes_but_course_closes(tmp_path, every) -> None:
    result = await _run(tmp_path, wrong_every=every)

    assert result["closed"] == result["total"] == 5
    assert result["wrong"] >= 1
