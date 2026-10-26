"""Служебные команды веба.

``uv run python -m llm_tutor.web.cli import-legacy --email ann@example.com
--db data/llm_tutor.sqlite3`` — перенести БД Telegram-бота (один ученик) в
аккаунт веба: копия становится БД ученика на курсе, ученик записывается на
курс. Аккаунт сначала регистрируется в браузере.

``make-admin --email ann@example.com`` выдаёт аккаунту роль админа (раздел
``/admin``), ``revoke-admin`` снимает её. Роль меняется только так, из веба —
никак: захват аккаунта ученика не должен давать права админа.
"""

import argparse
import asyncio
import os
import sqlite3
import time
from pathlib import Path

from llm_tutor.config import Settings, get_settings
from llm_tutor.db.connection import get_conn, migrate
from llm_tutor.web import accounts, courses, ops
from llm_tutor.web.app import _project_path
from llm_tutor.web.courses import get_course, published_courses
from llm_tutor.web.mailer import build_mailer
from llm_tutor.web.reminders import send_reminders
from llm_tutor.web.userdb import UserDBPool


def import_legacy(settings: Settings, email: str, db_path: str, course_id: str) -> str:
    """Копирует старую БД ученику. Возвращает путь копии; ошибка — ``RuntimeError``."""
    if get_course(course_id) is None:
        raise RuntimeError(f"Нет курса {course_id!r}")
    source = _project_path(db_path)
    if not source.is_file():
        raise RuntimeError(f"Нет файла {source}")
    db = accounts.open_accounts(settings.accounts_db_path)
    try:
        user = accounts.find_user(db, email)
        if user is None:
            raise RuntimeError(f"Нет аккаунта {email!r} — сначала зарегистрируйтесь в браузере")
        pool = UserDBPool(_project_path(settings.users_dir), _project_path(settings.materials_db_path))
        target = pool.path(user.id, course_id)
        # exists, а не path().exists(): БД веба под прежним id курса сначала
        # переезжает на новое имя, иначе перенос встал бы поверх неё (аудит 52, M1).
        if pool.exists(user.id, course_id):
            raise RuntimeError(f"У ученика уже есть БД курса: {target}. Перенос не перезаписывает её")
        target.parent.mkdir(parents=True, exist_ok=True)
        # Копия — во временный файл рядом; на место он встаёт, только когда
        # открылся и довёлся до текущей схемы. Сбой не оставит ученику
        # недоделанную БД, которая заблокирует повторный перенос.
        temporary = target.with_name(f".{target.name}.import")
        try:
            # backup, а не копия файла: в режиме WAL часть данных живёт в -wal.
            # as_uri: «#» и «?» в пути иначе обрезали бы имя файла.
            src = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
            dst = sqlite3.connect(temporary)
            try:
                src.backup(dst)
            finally:
                src.close()
                dst.close()
            check = get_conn(str(temporary))
            try:
                migrate(check)
            finally:
                check.close()
            os.replace(temporary, target)
        except (sqlite3.Error, RuntimeError) as exc:
            raise RuntimeError(f"Не удалось перенести {source}: {exc}") from exc
        finally:
            temporary.unlink(missing_ok=True)
        # Открытие доводит seed и материалы до текущих — как при обычном входе.
        pool.open(user.id, course_id)
        pool.close_all()
        accounts.enroll(db, user.id, course_id)
    finally:
        db.close()
    return str(target)


def set_admin_role(settings: Settings, email: str, *, admin: bool) -> str:
    """Выдаёт или снимает роль админа. Возвращает имя; нет аккаунта — ``RuntimeError``."""
    db = accounts.open_accounts(settings.accounts_db_path)
    try:
        user = accounts.find_user(db, email)
        if user is None:
            raise RuntimeError(f"Нет аккаунта {email!r} — сначала зарегистрируйтесь в браузере")
        accounts.set_admin(db, user.id, admin)
        return f"{user.name} <{user.email}>"
    finally:
        db.close()


def main(argv: list[str] | None = None, *, settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    # Курсы, созданные преподавателем в админке, живут в каталоге из настроек:
    # без этого реестр знал бы только встроенный mlcourse.
    courses.configure(_project_path(settings.courses_dir))
    parser = argparse.ArgumentParser(prog="python -m llm_tutor.web.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    legacy = commands.add_parser("import-legacy", help="перенести БД Telegram-бота в аккаунт")
    legacy.add_argument("--email", required=True)
    legacy.add_argument("--db", default="data/llm_tutor.sqlite3")
    legacy.add_argument("--course", default=published_courses()[0].id)
    for name, help_text in (
        ("make-admin", "выдать аккаунту роль админа"),
        ("revoke-admin", "снять с аккаунта роль админа"),
    ):
        commands.add_parser(name, help=help_text).add_argument("--email", required=True)
    reminders = commands.add_parser(
        "send-review-reminders", help="письма о занятиях, готовых к повторению"
    )
    reminders.add_argument("--dry-run", action="store_true", help="только показать, не отправлять")
    reminders.add_argument("--base-url", default=None, help="внешний адрес в ссылке письма")

    backup = commands.add_parser("backup", help="копия аккаунтов и БД учеников")
    backup.add_argument("--out", default=None, help="каталог бэкапа")
    backup.add_argument("--with-courses", action="store_true", help="включить курсы и материалы")
    backup.add_argument("--force", action="store_true", help="писать в непустой каталог")
    restore = commands.add_parser("restore", help="восстановить данные из бэкапа")
    restore.add_argument("--from", dest="source", required=True, help="каталог бэкапа")
    restore.add_argument("--yes", action="store_true", help="подтвердить перезапись")
    restore.add_argument("--force", action="store_true", help="не проверять занятый порт")
    export_account = commands.add_parser("export-account", help="экспорт данных ученика в JSON")
    export_account.add_argument("--email", required=True)
    export_account.add_argument("--out", default=None)
    delete_account = commands.add_parser("delete-account", help="удалить аккаунт и его БД")
    delete_account.add_argument("--email", required=True)
    delete_account.add_argument("--yes", action="store_true")
    delete_account.add_argument("--dry-run", action="store_true")
    commands.add_parser("check-smtp", help="проверить вход в SMTP без отправки письма")
    args = parser.parse_args(argv)

    try:
        if args.command == "import-legacy":
            target = import_legacy(settings, args.email, args.db, args.course)
            print(f"Готово: {target}")
        elif args.command == "send-review-reminders":
            async def run():
                post = build_mailer(settings)
                try:
                    return await send_reminders(
                        settings, post, base_url=args.base_url, dry_run=args.dry_run
                    )
                finally:
                    await post.aclose()

            sent = asyncio.run(run())
            suffix = " (dry-run)" if args.dry_run else ""
            if not settings.smtp_configured:
                print("SMTP не настроен: письма печатаются в журнал сервера.")
            print(f"Готово: напоминаний — {len(sent)}{suffix}")
            for reminder in sent:
                course_lines = "; ".join(
                    f"{entry.course_title}: {', '.join(entry.lessons)}"
                    for entry in reminder.entries
                )
                print(f"  {reminder.email}: {course_lines}")
        elif args.command == "backup":
            out = args.out or (_project_path(ops.BACKUP_DIR) / time.strftime("%Y%m%d-%H%M%S"))
            target = ops.backup(
                settings, out, with_courses=args.with_courses, force=args.force
            )
            print(f"Готово: бэкап в {target}")
        elif args.command == "restore":
            safety = ops.restore(settings, args.source, yes=args.yes, force=args.force)
            print(f"Готово: восстановлено; страховочная копия — {safety}")
        elif args.command == "export-account":
            target = ops.export_account(settings, args.email, args.out)
            print(f"Готово: данные в {target}")
        elif args.command == "delete-account":
            result = ops.delete_account(
                settings, args.email, yes=args.yes, dry_run=args.dry_run
            )
            suffix = " (dry-run)" if args.dry_run else ""
            print(
                f"Готово: {result['email']} — курсов {len(result['courses'])}, "
                f"файлов {len(result['files'])}{suffix}"
            )
        elif args.command == "check-smtp":
            ok, message = ops.check_smtp(settings)
            print(message)
            return 0 if ok else 1
        else:
            admin = args.command == "make-admin"
            who = set_admin_role(settings, args.email, admin=admin)
            print(f"Готово: {who} — {'админ' if admin else 'не админ'}")
    except RuntimeError as exc:
        print(f"Ошибка: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
