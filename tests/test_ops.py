"""Операции установки: бэкап/восстановление, экспорт и удаление аккаунта."""

import json
import shutil
from pathlib import Path

import pytest
from llm_tutor.db import repos
from llm_tutor.schemas import Event
from llm_tutor.web import accounts, courses, ops
from llm_tutor.web.userdb import UserDBPool

PASSWORD = "тестовый-пароль"


@pytest.fixture
def account(web_settings):
    courses.configure(web_settings.courses_dir)
    db = accounts.open_accounts(web_settings.accounts_db_path)
    try:
        user = accounts.create_user(
            db, email="ann@example.com", name="Аня", password=PASSWORD
        )
        accounts.enroll(db, user.id, "mlcourse")
    finally:
        db.close()
    pool = UserDBPool(
        web_settings.users_dir, web_settings.materials_db_path, settings=web_settings
    )
    try:
        conn = pool.open(user.id, "mlcourse")
        session_id = repos.ensure_open_session(conn, now=1.0)
        repos.add_message(conn, session_id, "user", "привет", ts=1.0)
        repos.add_event(
            conn,
            Event(
                source="checked",
                result=1.0,
                concept_id="pandas_structures",
                item_id=1,
                ts=2.0,
            ),
        )
        repos.add_note(
            conn,
            concept_id="pandas_structures",
            source="theory",
            quote="Series — это столбец таблицы",
            now=3.0,
        )
    finally:
        pool.close_all()
    return user


def _user_dir(settings, user) -> Path:
    return Path(settings.users_dir) / str(user.id)


def test_backup_and_restore(account, web_settings, tmp_path) -> None:
    target = ops.backup(web_settings, tmp_path / "backup")

    assert (target / "accounts.sqlite3").is_file()
    assert (target / "users" / str(account.id) / "mlcourse.sqlite3").is_file()
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["accounts_schema"] == accounts.SCHEMA_VERSION
    assert Path(str(account.id), "mlcourse.sqlite3").as_posix() in manifest["users"]

    # Ломаем установку: удаляем аккаунт и файлы.
    db = accounts.open_accounts(web_settings.accounts_db_path)
    db.execute("DELETE FROM users WHERE id = ?", (account.id,))
    db.commit()
    db.close()
    shutil.rmtree(_user_dir(web_settings, account))
    db = accounts.open_accounts(web_settings.accounts_db_path)
    try:
        assert accounts.find_user(db, "ann@example.com") is None
    finally:
        db.close()

    safety = ops.restore(web_settings, target, yes=True, force=True)

    assert safety.is_dir()
    db = accounts.open_accounts(web_settings.accounts_db_path)
    try:
        restored = accounts.find_user(db, "ann@example.com")
        assert restored is not None and restored.id == account.id
    finally:
        db.close()
    pool = UserDBPool(
        web_settings.users_dir, web_settings.materials_db_path, settings=web_settings
    )
    try:
        conn = pool.open(account.id, "mlcourse")
        assert [note.quote for note in repos.list_notes(conn)] == [
            "Series — это столбец таблицы"
        ]
        assert [event.item_id for event in repos.get_events(conn)] == [1]
    finally:
        pool.close_all()


def test_export_account_is_readable_json(account, web_settings, tmp_path) -> None:
    target = ops.export_account(web_settings, "ann@example.com", tmp_path / "ann.json")

    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["account"]["email"] == "ann@example.com"
    assert data["enrollments"] == ["mlcourse"]
    course = data["courses"]["mlcourse"]
    assert course["notes"][0]["quote"] == "Series — это столбец таблицы"
    assert course["events"][0]["item_id"] == 1
    assert course["messages"][0]["content"] == "привет"
    assert "password_hash" not in target.read_text(encoding="utf-8")


def test_delete_account_removes_files_and_rows(account, web_settings) -> None:
    result = ops.delete_account(web_settings, "ann@example.com", yes=True)

    assert result["user_id"] == account.id
    assert result["courses"] == ["mlcourse"]
    assert not _user_dir(web_settings, account).exists()
    db = accounts.open_accounts(web_settings.accounts_db_path)
    try:
        assert accounts.find_user(db, "ann@example.com") is None
        assert db.execute("SELECT COUNT(*) FROM enrollments WHERE user_id = ?", (account.id,)).fetchone()[0] == 0
    finally:
        db.close()


def test_delete_dry_run_keeps_account(account, web_settings) -> None:
    result = ops.delete_account(web_settings, "ann@example.com", dry_run=True)

    assert result["email"] == "ann@example.com"
    assert _user_dir(web_settings, account).exists()
    db = accounts.open_accounts(web_settings.accounts_db_path)
    try:
        assert accounts.find_user(db, "ann@example.com") is not None
    finally:
        db.close()


def test_last_admin_cannot_be_deleted(account, web_settings) -> None:
    db = accounts.open_accounts(web_settings.accounts_db_path)
    try:
        accounts.set_admin(db, account.id, True)
    finally:
        db.close()

    with pytest.raises(RuntimeError, match="последний админ"):
        ops.delete_account(web_settings, "ann@example.com", yes=True)


def test_check_smtp_without_settings(web_settings) -> None:
    ok, message = ops.check_smtp(web_settings)

    assert ok is False
    assert "не настроена" in message
