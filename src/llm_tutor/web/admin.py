"""Данные админки: снимок по всем ученикам курса, списки и сводки.

Снимок — один обход БД записанных учеников: из него считаются список
учеников, воронка по занятиям и таблица трудных заданий. Кэшируется на
минуту в ``app.state.admin_cache`` (у каждого приложения свой).

Формул статусов здесь нет: прогресс и статусы тем берутся из
``core.overview`` и ``core.lesson`` (спека админки §3). Админка только читает.

Ученика с идущим ходом снимок не читает: ход держит его ``asyncio.Lock`` и
может не успеть закоммитить, а соединение общее — чтение увидело бы половину
изменений. Такой ученик берётся из прошлого снимка и помечается ``busy``.
Чтение синхронное, без ``await``: между проверкой замка и чтением ход
вклиниться не может.
"""

import logging
import sqlite3
import time
from dataclasses import dataclass, field, replace
from datetime import datetime

from llm_tutor.config import Settings
from llm_tutor.core.lesson import course_survey_done, is_closed
from llm_tutor.core.overview import topic_rows
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.schemas import SessionState
from llm_tutor.web import accounts, views
from llm_tutor.web.courses import Course

logger = logging.getLogger(__name__)

# Сколько снимок считается свежим.
SNAPSHOT_TTL = 60.0
# «Активен» в сводке — была активность за столько секунд.
ACTIVE_WINDOW = 7 * 24 * 3600.0
# События-ответы на задания; ``dialogue`` и ``self`` ответами не считаются.
ANSWER_SOURCES = ("autotest", "checked", "rubric")

STATUS_LABELS = {
    "survey": "Анкета",
    "in_progress": "В процессе",
    "done": "Пройден",
    "error": "Недоступен",
    "no_db": "Нет данных",
    "busy": "Отвечает",
    "not_enrolled": "Не записан",
}


@dataclass(frozen=True)
class Answer:
    """Ответ ученика на задание (строка журнала ``events``)."""

    id: int
    item_id: int
    result: float
    hints_used: int
    ts: float


@dataclass(frozen=True)
class LessonMark:
    """Занятие глазами одного ученика (для воронки)."""

    reached: bool  # дошёл: занятие закрыто или текущее (после анкеты)
    closed: bool
    weak: bool  # закрыто после двух ошибок (⚠️)


@dataclass(frozen=True)
class StudentData:
    """Всё, что снимок берёт из БД одного ученика за один проход."""

    survey_done: bool
    total: int
    closed: int
    weak: int
    last_activity: float | None
    lessons: dict[str, LessonMark]
    answers: tuple[Answer, ...]

    @property
    def status(self) -> str:
        # Как в карточке курса (``views.course_card``).
        if not self.survey_done:
            return "survey"
        return "done" if self.closed >= self.total else "in_progress"


@dataclass(frozen=True)
class StudentRow:
    """Ученик в снимке: итоги для списка и данные для аналитики."""

    user_id: int
    status: str
    closed: int | None = None
    total: int | None = None
    weak: int | None = None
    last_activity: float | None = None
    busy: bool = False
    lessons: dict[str, LessonMark] = field(default_factory=dict)
    answers: tuple[Answer, ...] = ()

    @property
    def percent(self) -> int:
        if self.closed is None or not self.total:
            return 0
        return round(100 * self.closed / self.total)


@dataclass(frozen=True)
class Snapshot:
    built_at: float
    rows: dict[int, StudentRow]


@dataclass(frozen=True)
class Summary:
    total: int
    active_week: int
    finished: int


def read_student(conn: sqlite3.Connection, *, settings: Settings) -> StudentData:
    """Читает одну БД ученика: прогресс, статусы занятий, активность, ответы."""
    rows = topic_rows(conn, settings=settings)
    session_id = repos.get_open_session(conn)
    state = repos.get_session_state(conn, session_id) if session_id else SessionState()
    survey_done = course_survey_done(conn)
    closed = {row.id for row in rows if row.status == "closed"}
    # Текущее занятие в списке тем «текущее», даже закрытое; карточка курса
    # считает его закрытым (``course_progress``) — снимок обязан совпадать.
    current = state.current_node_id
    if current in {row.id for row in rows} and current not in closed:
        if is_closed(conn, current, state, now=None, settings=settings):
            closed.add(current)
    lessons = {
        row.id: LessonMark(
            reached=survey_done and (row.id in closed or row.status == "current"),
            closed=row.id in closed,
            weak=row.weak,
        )
        for row in rows
    }
    latest = conn.execute(
        "SELECT MAX(ts) FROM (SELECT MAX(ts) AS ts FROM events UNION ALL SELECT MAX(ts) FROM messages)"
    ).fetchone()[0]
    placeholders = ", ".join("?" * len(ANSWER_SOURCES))
    answers = tuple(
        Answer(id=row[0], item_id=row[1], result=row[2], hints_used=row[3], ts=row[4])
        for row in conn.execute(
            "SELECT id, item_id, result, hints_used, ts FROM events "
            f"WHERE item_id IS NOT NULL AND source IN ({placeholders}) ORDER BY ts, id",
            ANSWER_SOURCES,
        )
    )
    return StudentData(
        survey_done=survey_done,
        total=len(rows),
        closed=len(closed),
        weak=sum(1 for row in rows if row.weak),
        last_activity=latest,
        lessons=lessons,
        answers=answers,
    )


def _row(state, course: Course, user_id: int, previous: StudentRow | None) -> StudentRow:
    pool = state.userdbs
    if not pool.exists(user_id, course.id):
        return StudentRow(user_id, "no_db")
    if pool.lock(user_id, course.id).locked():
        # Идёт ход ученика: читать нельзя, берём прошлые данные.
        if previous is not None:
            return replace(previous, busy=True)
        return StudentRow(user_id, "busy", busy=True)
    try:
        data = read_student(pool.open(user_id, course.id), settings=state.settings)
    except (sqlite3.DatabaseError, RuntimeError):
        # Одна битая БД не должна ронять админку (как в ``views.course_card``).
        logger.exception("БД ученика %s по курсу %s не открывается", user_id, course.id)
        return StudentRow(user_id, "error")
    return StudentRow(
        user_id,
        data.status,
        closed=data.closed,
        total=data.total,
        weak=data.weak,
        last_activity=data.last_activity,
        lessons=data.lessons,
        answers=data.answers,
    )


def snapshot(state, course: Course, *, refresh: bool = False, clock=time.time) -> Snapshot:
    """Снимок курса: свежий из кэша или пересчитанный.

    ``state`` — ``app.state``. Функция синхронная и без ``await``: два запроса
    подряд не построят снимок дважды.
    """
    now = clock()
    cached = state.admin_cache.get(course.id)
    if cached is not None and not refresh and now - cached.built_at < SNAPSHOT_TTL:
        return cached
    earlier = cached.rows if cached is not None else {}
    rows = {
        user_id: _row(state, course, user_id, earlier.get(user_id))
        for user_id in accounts.enrolled_user_ids(state.accounts, course.id)
    }
    fresh = Snapshot(built_at=now, rows=rows)
    state.admin_cache[course.id] = fresh
    return fresh


def summarize(snap: Snapshot, *, total: int, now: float) -> Summary:
    """Сводка над списком: всего аккаунтов, активных за неделю, закончивших курс."""
    active = sum(
        1
        for row in snap.rows.values()
        if row.last_activity is not None and now - row.last_activity <= ACTIVE_WINDOW
    )
    finished = sum(1 for row in snap.rows.values() if row.status == "done")
    return Summary(total=total, active_week=active, finished=finished)


def format_time(ts: float | None) -> str:
    """Дата и время для таблиц; нет значения — прочерк."""
    return "—" if ts is None else datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")


# --- Карточка ученика ---

RECENT_ANSWERS = 50
LOG_LIMIT = 20
# Сколько символов условия задания показывать в таблице ответов.
PROMPT_PREVIEW = 80

ACTION_LABELS = {
    "reset_password": "Сброс пароля",
    "close_sessions": "Закрытие сессий",
}


@dataclass(frozen=True)
class StudentCard:
    """Карточка ученика: шапка из аккаунтов и, если можно прочесть, данные курса.

    ``state``: ``ok`` — данные курса есть; ``not_enrolled``, ``no_db``,
    ``error`` (БД не открывается), ``busy`` (идёт ход ученика — читать нельзя).
    Диалоги (``messages``) в карточку не попадают никогда.
    """

    account: accounts.AccountRow
    sessions: int
    state: str
    learning: dict | None = None
    answers: list[dict] = field(default_factory=list)
    log: list[dict] = field(default_factory=list)


def _preview(text: str) -> str:
    """Начало условия одной строкой: без переводов строк, не длиннее ``PROMPT_PREVIEW``."""
    flat = " ".join(text.split())
    return flat if len(flat) <= PROMPT_PREVIEW else flat[:PROMPT_PREVIEW] + "…"


def _result_label(result: float) -> str:
    if result >= 1.0:
        return "верно"
    if result <= 0.0:
        return "неверно"
    return f"{round(result * 100)}%"


def _recent_answers(conn: sqlite3.Connection) -> list[dict]:
    """Последние события по заданиям: время, занятие, задание, источник, итог."""
    graph = CourseGraph.load(conn)
    result = []
    for ts, concept_id, item_id, source, score, hints, spent in conn.execute(
        "SELECT ts, concept_id, item_id, source, result, hints_used, time_spent FROM events "
        "WHERE item_id IS NOT NULL ORDER BY ts DESC, id DESC LIMIT ?",
        (RECENT_ANSWERS,),
    ):
        item = repos.get_item(conn, item_id)
        lesson = graph.concept(concept_id).name if concept_id and graph.has_node(concept_id) else None
        result.append(
            {
                "ts": ts,
                "lesson": lesson,
                "item_id": item_id,
                "prompt": _preview(item.prompt) if item is not None else None,
                "source": source,
                "result": _result_label(score),
                "right": score >= 0.5,
                "hints": hints,
                "spent": None if spent is None else f"{round(spent)} с",
            }
        )
    return result


def student_card(
    state, course: Course, account: accounts.AccountRow, *, now: float | None = None
) -> StudentCard:
    """Карточка ученика; без кэша. Читает БД ученика, только если ход не идёт."""
    db = state.accounts
    base = {
        "account": account,
        "sessions": accounts.active_sessions(db, account.id, now),
        "log": [
            {"ts": row.ts, "label": ACTION_LABELS.get(row.action, row.action), "admin": row.admin_name}
            for row in accounts.admin_log_for(db, account.id, LOG_LIMIT)
        ],
    }
    if course.id not in accounts.enrolled_courses(db, account.id):
        return StudentCard(state="not_enrolled", **base)
    pool = state.userdbs
    if not pool.exists(account.id, course.id):
        return StudentCard(state="no_db", **base)
    if pool.lock(account.id, course.id).locked():
        return StudentCard(state="busy", **base)
    try:
        conn = pool.open(account.id, course.id)
        learning = views.learning_view(conn, settings=state.settings)
        answers = _recent_answers(conn)
    except (sqlite3.DatabaseError, RuntimeError):
        logger.exception("БД ученика %s по курсу %s не открывается", account.id, course.id)
        return StudentCard(state="error", **base)
    return StudentCard(state="ok", learning=learning, answers=answers, **base)
