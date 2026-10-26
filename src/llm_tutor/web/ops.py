"""Операции с данными установки: бэкап, восстановление, экспорт и удаление.

Всё это — отдельный процесс (CLI), а не маршрут веба: восстановление подменяет
SQLite-файлы, пока сервер держит их открытыми. Поэтому restore отказывается
работать, если порт веба слушается, и сначала делает страховочную копию.

Бэкап — не «копия файла»: SQLite в режиме WAL держит часть данных в ``-wal``.
Копируем через ``Connection.backup``, который видит согласованный снимок.
"""

from __future__ import annotations

import json
import logging
import shutil
import smtplib
import socket
import sqlite3
import time
from pathlib import Path

from llm_tutor.config import Settings, secret_value
from llm_tutor.db import repos
from llm_tutor.db.connection import SCHEMA_VERSION
from llm_tutor.web import accounts, courses
from llm_tutor.web.app import _project_path
from llm_tutor.web.userdb import UserDBPool

logger = logging.getLogger(__name__)

BACKUP_DIR = "backups"
EXPORT_DIR = "exports"


def _sqlite_copy(source: Path, target: Path) -> None:
    """Согласованная копия SQLite (учитывает WAL), без открытия на запись."""
    target.parent.mkdir(parents=True, exist_ok=True)
    # Старые -wal/-shm от прежней БД не должны примениться к восстановленной.
    for suffix in ("", "-wal", "-shm"):
        target.with_name(target.name + suffix).unlink(missing_ok=True)
    source_conn = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
    try:
        source_conn.execute("PRAGMA busy_timeout = 5000")
        target_conn = sqlite3.connect(target)
        try:
            source_conn.backup(target_conn)
        finally:
            target_conn.close()
    finally:
        source_conn.close()


def _non_empty(path: Path) -> bool:
    return path.exists() and any(path.iterdir())


def backup(
    settings: Settings,
    out: str | Path,
    *,
    with_courses: bool = False,
    force: bool = False,
    now: float | None = None,
) -> Path:
    """Копия аккаунтов и БД учеников (по желанию — курсы и материалы)."""
    target = Path(out).expanduser().resolve()
    if _non_empty(target) and not force:
        raise RuntimeError(f"Каталог {target} уже не пуст; укажи другой или --force")
    target.mkdir(parents=True, exist_ok=True)
    stamp = time.time() if now is None else now

    accounts_path = _project_path(settings.accounts_db_path)
    if accounts_path.is_file():
        _sqlite_copy(accounts_path, target / "accounts.sqlite3")

    users_source = _project_path(settings.users_dir)
    users: list[str] = []
    if users_source.is_dir():
        for source in sorted(users_source.rglob("*.sqlite3")):
            relative = source.relative_to(users_source)
            _sqlite_copy(source, target / "users" / relative)
            users.append(relative.as_posix())

    copied_courses = False
    materials = _project_path(settings.materials_db_path)
    if with_courses:
        courses_source = _project_path(settings.courses_dir)
        if courses_source.is_dir():
            shutil.copytree(courses_source, target / "courses", dirs_exist_ok=True)
            copied_courses = True
        if materials.is_file():
            _sqlite_copy(materials, target / "materials.sqlite3")

    manifest = {
        "created_at": stamp,
        "schema_version": SCHEMA_VERSION,
        "accounts_schema": accounts.SCHEMA_VERSION,
        "with_courses": copied_courses,
        "users": users,
    }
    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("Бэкап: %s (учеников: %d)", target, len(users))
    return target


def _port_busy(settings: Settings) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", settings.web_port), timeout=0.3):
            return True
    except OSError:
        return False


def restore(
    settings: Settings,
    source: str | Path,
    *,
    yes: bool = False,
    force: bool = False,
    now: float | None = None,
) -> Path:
    """Восстановить данные из бэкапа; возвращает путь страховочной копии."""
    if not yes:
        raise RuntimeError("Восстановление перезаписывает данные; добавь --yes")
    origin = Path(source).expanduser().resolve()
    manifest_path = origin / "manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError(f"Это не бэкап: нет {manifest_path}")
    if _port_busy(settings) and not force:
        raise RuntimeError(
            "Веб-сервер слушает порт: останови его перед восстановлением "
            "(или добавь --force, если точно знаешь, что делаешь)"
        )
    stamp = time.time() if now is None else now
    safety = backup(
        settings,
        origin.parent / f"pre-restore-{time.strftime('%Y%m%d-%H%M%S', time.gmtime(stamp))}",
        with_courses=(origin / "courses").is_dir(),
        force=True,
    )

    accounts_source = origin / "accounts.sqlite3"
    if accounts_source.is_file():
        _sqlite_copy(accounts_source, _project_path(settings.accounts_db_path))
    users_target = _project_path(settings.users_dir)
    if users_target.exists():
        shutil.rmtree(users_target)
    if (origin / "users").is_dir():
        shutil.copytree(origin / "users", users_target)
    else:
        users_target.mkdir(parents=True, exist_ok=True)

    # Курсы не удаляем, если их не было в бэкапе: это может быть более свежая
    # работа преподавателя, чем копия.
    if (origin / "courses").is_dir():
        courses_target = _project_path(settings.courses_dir)
        if courses_target.exists():
            shutil.rmtree(courses_target)
        shutil.copytree(origin / "courses", courses_target)
    materials_source = origin / "materials.sqlite3"
    if materials_source.is_file():
        _sqlite_copy(materials_source, _project_path(settings.materials_db_path))
    logger.info("Восстановлено из %s; страховочная копия: %s", origin, safety)
    return safety


def export_account(
    settings: Settings, email: str, out: str | Path | None = None, *, now: float | None = None
) -> Path:
    """Экспорт данных ученика в JSON (без хеша пароля и без секретов)."""
    db = accounts.open_accounts(settings.accounts_db_path)
    pool = UserDBPool(
        _project_path(settings.users_dir),
        _project_path(settings.materials_db_path),
        settings=settings,
    )
    try:
        user = accounts.find_user(db, email)
        if user is None:
            raise RuntimeError(f"Нет аккаунта {email!r}")
        account = accounts.get_account(db, user.id)
        assert account is not None
        course_ids = accounts.enrolled_courses(db, user.id)
        data: dict = {
            "exported_at": time.time() if now is None else now,
            "account": {
                "id": account.id,
                "email": account.email,
                "name": account.name,
                "created_at": account.created_at,
                "is_admin": account.is_admin,
            },
            "enrollments": course_ids,
            "courses": {},
        }
        for course_id in course_ids:
            if not pool.exists(user.id, course_id):
                continue
            conn = pool.open(user.id, course_id)
            session_id = repos.get_open_session(conn)
            data["courses"][course_id] = {
                "events": [event.model_dump() for event in repos.get_events(conn)],
                "messages": [
                    dict(row)
                    for row in conn.execute(
                        "SELECT id, session_id, role, content, ts, meta FROM messages "
                        "ORDER BY ts, id"
                    )
                ],
                "notes": [note.model_dump() for note in repos.list_notes(conn)],
                "facts": repos.get_facts(conn),
                "review": {
                    concept_id: row.model_dump()
                    for concept_id, row in repos.get_review_rows(conn).items()
                },
                "state": (
                    repos.get_session_state(conn, session_id).model_dump()
                    if session_id is not None
                    else None
                ),
            }
        if out is None:
            stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(data["exported_at"]))
            target = _project_path(EXPORT_DIR) / f"account-{user.id}-{stamp}.json"
        else:
            target = Path(out).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return target
    finally:
        pool.close_all()
        db.close()


def delete_account(
    settings: Settings, email: str, *, yes: bool = False, dry_run: bool = False
) -> dict:
    """Удалить аккаунт, записи, сессии и файлы курсов. Курсы не трогает."""
    db = accounts.open_accounts(settings.accounts_db_path)
    try:
        user = accounts.find_user(db, email)
        if user is None:
            raise RuntimeError(f"Нет аккаунта {email!r}")
        account = accounts.get_account(db, user.id)
        assert account is not None
        if account.is_admin:
            admins = db.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1").fetchone()[0]
            if admins <= 1:
                raise RuntimeError(
                    "Это последний админ: удаление оставит установку без админки"
                )
        courses_list = accounts.enrolled_courses(db, user.id)
        user_dir = _project_path(settings.users_dir) / str(user.id)
        files = [str(path) for path in user_dir.rglob("*") if path.is_file()] if user_dir.is_dir() else []
        result = {
            "email": account.email,
            "user_id": account.id,
            "courses": courses_list,
            "files": files,
        }
        if dry_run:
            return result
        if not yes:
            raise RuntimeError("Удаление необратимо; добавь --yes")
        db.execute("DELETE FROM admin_log WHERE target_id = ? OR admin_id = ?", (user.id, user.id))
        db.execute("DELETE FROM users WHERE id = ?", (user.id,))
        db.commit()
        if user_dir.exists():
            shutil.rmtree(user_dir)
        logger.info("Аккаунт %s удалён", account.email)
        return result
    finally:
        db.close()


def check_smtp(settings: Settings) -> tuple[bool, str]:
    """Проверить вход в SMTP, не отправляя письмо."""
    if not settings.smtp_configured:
        # Без стрелок и длинных подсказок: консоль Windows в cp1251 не умеет
        # часть символов, а падать из-за сообщения об ошибке — обидно.
        return False, "Почта не настроена: заполни SMTP_USER и SMTP_PASSWORD в .env."
    assert settings.smtp_user is not None
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout) as smtp:
            smtp.ehlo()
            if settings.smtp_starttls:
                smtp.starttls()
                smtp.ehlo()
            smtp.login(settings.smtp_user, secret_value(settings.smtp_password))
        return True, "SMTP: вход выполнен, письма можно отправлять."
    except smtplib.SMTPAuthenticationError as exc:
        return False, (
            f"SMTP: не принял логин (код {exc.smtp_code}). Нужен пароль приложения "
            "Google из 16 символов, а не пароль аккаунта."
        )
    except Exception as exc:  # noqa: BLE001 — CLI должен показать причину, а не стек
        return False, f"SMTP: {type(exc).__name__}: {exc}"
