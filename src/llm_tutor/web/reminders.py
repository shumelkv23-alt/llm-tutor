"""Напоминания о просроченном повторении по email.

Расписание повторений уже живёт в БД ученика (``student.review``); эта команда
раз в день собирает учеников, у которых есть просроченные занятия, и шлёт одно
письмо на ученика — пусть даже курсов несколько. Отправка идёт отдельным
процессом (cron / Планировщик задач): веб-процесс не держит фоновый цикл писем.

Согласие — существующий переключатель повторения (``review.enabled``) и
отдельная отметка ``review_reminders`` (её снимает ссылка «отписаться» из
письма). Факт ``review_reminder_sent`` хранит день последнего письма по курсу,
поэтому повторный запуск в тот же день молчит.

Подпись ссылки отписки — HMAC от id ученика на ``Settings.reminder_secret``
(по умолчанию — OPENROUTER_API_KEY): ссылка переживает перезапуск и не требует
входа в аккаунт.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from urllib.parse import quote

from llm_tutor.config import Settings
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.schemas import SessionState
from llm_tutor.student import review
from llm_tutor.web import accounts, courses
from llm_tutor.web.app import _project_path
from llm_tutor.web.courses import Course
from llm_tutor.web.mailer import Mailer
from llm_tutor.web.userdb import UserDBPool

logger = logging.getLogger(__name__)

FACT = "review_reminder_sent"
REMINDERS_FACT = "review_reminders"
_DAY = 86_400.0


@dataclass(frozen=True)
class ReminderEntry:
    """Один курс в письме: занятия и готовые строки для текста."""

    course_id: str
    course_title: str
    lessons: tuple[str, ...]
    details: tuple[str, ...]


@dataclass(frozen=True)
class Reminder:
    """Готовое письмо ученику (один адрес, один или несколько курсов)."""

    email: str
    name: str
    entries: tuple[ReminderEntry, ...]
    subject: str
    text: str


def _day(now: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(now))


def _plural(count: int, one: str, few: str, many: str) -> str:
    """Русская форма числа: 1 занятие, 2 занятия, 5 занятий."""
    if count % 10 == 1 and count % 100 != 11:
        return one
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return few
    return many


def _overdue(due_at: float, now: float) -> str:
    days = max(0, int((now - due_at) // _DAY))
    if days == 0:
        return "срок сегодня"
    return f"просрочено на {days} {_plural(days, 'день', 'дня', 'дней')}"


def reminders_enabled(conn) -> bool:
    """Не отписался ли ученик от писем (по умолчанию — да)."""
    return repos.get_fact(conn, REMINDERS_FACT) != "0"


def set_reminders_enabled(conn, flag: bool, *, commit: bool = True) -> None:
    """Включает/выключает письма, не трогая повторение в курсе."""
    repos.set_fact(conn, REMINDERS_FACT, "1" if flag else "0", source="self", commit=commit)


def token_for(settings: Settings, user_id: int) -> str:
    """Подписанный идентификатор ученика для ссылки отписки."""
    payload = str(int(user_id))
    signature = hmac.new(
        settings.reminder_secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:32]
    return f"{payload}.{signature}"


def verify_token(settings: Settings, token: str) -> int | None:
    """Id ученика из подписанной ссылки; ``None`` — ссылка битая/поддельная."""
    try:
        raw, signature = token.split(".", 1)
        user_id = int(raw)
    except (AttributeError, ValueError):
        return None
    if user_id <= 0:
        return None
    expected = hmac.new(
        settings.reminder_secret.encode("utf-8"),
        str(user_id).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:32]
    return user_id if hmac.compare_digest(signature, expected) else None


def unsubscribe_url(base_url: str, settings: Settings, user_id: int) -> str:
    return f"{base_url.rstrip('/')}/unsubscribe?token={quote(token_for(settings, user_id))}"


def letter_for(
    name: str,
    entries: list[ReminderEntry],
    *,
    base_url: str,
    unsubscribe: str,
) -> tuple[str, str]:
    """Тема и текст письма для одного или нескольких курсов."""
    total = sum(len(entry.lessons) for entry in entries)
    subject = f"Пора повторить: {total} {_plural(total, 'занятие', 'занятия', 'занятий')}"
    lines = [
        f"Здравствуйте, {name}!",
        "",
        f"Пора повторить {total} {_plural(total, 'занятие', 'занятия', 'занятий')}:",
    ]
    for entry in entries:
        lines.append("")
        lines.append(
            f"Курс «{entry.course_title}» — "
            f"{base_url.rstrip('/')}/lesson/{entry.course_id}"
        )
        lines.extend(f"• {detail}" for detail in entry.details)
    lines.extend(
        [
            "",
            "Повторение — короткая сессия на 5–10 минут: оно возвращает пройденное "
            "в память и подсказывает, где остались слабые места.",
            f"Отписаться от писем: {unsubscribe}",
            "Повторение в курсе при этом останется.",
        ]
    )
    return subject, "\n".join(lines)


def build_entry(
    conn, course: Course, *, now: float, settings: Settings
) -> ReminderEntry | None:
    """Курс для письма или ``None`` (нечего повторять / уже отправлено / отписан)."""
    if not review.enabled(conn) or not reminders_enabled(conn):
        return None
    if repos.get_fact(conn, FACT) == _day(now):
        return None
    graph = CourseGraph.load(conn)
    session_id = repos.get_open_session(conn)
    state = repos.get_session_state(conn, session_id) if session_id else SessionState()
    due = review.due_lessons(conn, graph, state.route, now=now, settings=settings)
    if not due:
        return None
    rows = repos.get_review_rows(conn)
    lessons: list[str] = []
    details: list[str] = []
    for concept_id in due:
        try:
            title = graph.concept(concept_id).name
        except KeyError:
            # Занятие ушло из опубликованного графа — не обещаем его в письме.
            continue
        lessons.append(title)
        details.append(
            f"{title} — "
            + (_overdue(rows[concept_id].due_at, now) if concept_id in rows else "ждёт повторения")
        )
    if not lessons:
        return None
    return ReminderEntry(
        course_id=course.id,
        course_title=course.title,
        lessons=tuple(lessons),
        details=tuple(details),
    )


def _compose(
    account,
    entries: list[tuple[ReminderEntry, object]],
    *,
    settings: Settings,
    base_url: str,
) -> Reminder:
    """Собрать письмо из готовых курсов; ``conn`` нужен только отправителю."""
    clean_entries = [entry for entry, _ in entries]
    subject, text = letter_for(
        account.name,
        clean_entries,
        base_url=base_url,
        unsubscribe=unsubscribe_url(base_url, settings, account.id),
    )
    return Reminder(
        email=account.email,
        name=account.name,
        entries=tuple(clean_entries),
        subject=subject,
        text=text,
    )


def build_for_user(
    conn, course: Course, account, *, now: float, base_url: str, settings: Settings
) -> Reminder | None:
    """Письмо по одному курсу (используется тестами и точечной отправкой)."""
    entry = build_entry(conn, course, now=now, settings=settings)
    if entry is None:
        return None
    return _compose(account, [(entry, conn)], settings=settings, base_url=base_url)


async def send_for_student(
    conn,
    course: Course,
    account,
    mailer: Mailer,
    *,
    now: float,
    base_url: str,
    settings: Settings,
    dry_run: bool = False,
) -> Reminder | None:
    """Собрать и отправить одно письмо по одному курсу; в dry-run только собрать."""
    reminder = build_for_user(
        conn, course, account, now=now, base_url=base_url, settings=settings
    )
    if reminder is None:
        return None
    if not dry_run:
        await mailer.send(to=reminder.email, subject=reminder.subject, text=reminder.text)
        repos.set_fact(conn, FACT, _day(now), source="system")
    return reminder


async def send_reminders(
    settings: Settings,
    mailer: Mailer,
    *,
    now: float | None = None,
    base_url: str | None = None,
    dry_run: bool = False,
) -> list[Reminder]:
    """Один проход по ученикам: одно письмо на ученика, один раз в день на курс."""
    stamp = time.time() if now is None else now
    base = base_url or settings.public_base_url
    db = accounts.open_accounts(settings.accounts_db_path)
    pool = UserDBPool(
        _project_path(settings.users_dir),
        _project_path(settings.materials_db_path),
        settings=settings,
    )
    published = {course.id: course for course in courses.published_courses()}
    sent: list[Reminder] = []
    try:
        for account in accounts.list_users(db):
            entries: list[tuple[ReminderEntry, object]] = []
            for course_id in accounts.enrolled_courses(db, account.id):
                course = published.get(course_id)
                if course is None or not pool.exists(account.id, course_id):
                    continue
                if pool.lock(account.id, course_id).locked():
                    continue
                try:
                    conn = pool.open(account.id, course_id)
                    entry = build_entry(conn, course, now=stamp, settings=settings)
                except Exception:  # noqa: BLE001 — одна битая БД не рвёт рассылку
                    logger.exception(
                        "Напоминание ученику %s по курсу %s не собрано",
                        account.id,
                        course_id,
                    )
                    continue
                if entry is not None:
                    entries.append((entry, conn))
            if not entries:
                continue
            reminder = _compose(account, entries, settings=settings, base_url=base)
            if not dry_run:
                try:
                    await mailer.send(
                        to=reminder.email, subject=reminder.subject, text=reminder.text
                    )
                except Exception:  # noqa: BLE001 — письмо не должно рвать остальную рассылку
                    logger.exception("Напоминание %s не отправлено", account.email)
                    continue
                for _entry, conn in entries:
                    repos.set_fact(conn, FACT, _day(stamp), source="system")
            logger.info(
                "Напоминание %s: %s",
                account.email,
                "; ".join(
                    f"{entry.course_title}: {len(entry.lessons)}" for entry, _ in entries
                ),
            )
            sent.append(reminder)
    finally:
        pool.close_all()
        db.close()
    return sent
