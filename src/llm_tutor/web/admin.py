"""Данные админки: снимок по всем ученикам курса, списки и сводки.

Снимок — один обход БД записанных учеников: из него считаются список
учеников и воронка по занятиям. Кэшируется на минуту в
``app.state.admin_cache`` (у каждого приложения свой).

Формул статусов здесь нет: прогресс и статусы тем берутся из
``core.overview`` и ``core.lesson`` (спека админки §3). Админка только читает.

Ученика с идущим ходом снимок не читает: ход держит его ``asyncio.Lock`` и
может не успеть закоммитить, а соединение общее — чтение увидело бы половину
изменений. Такой ученик берётся из прошлого снимка и помечается ``busy``.
Чтение синхронное, без ``await``: между проверкой замка и чтением ход
вклиниться не может.
"""

import json
import logging
import sqlite3
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path

from llm_tutor.config import Settings
from llm_tutor.course.seed import Course as CourseData
from llm_tutor.course.seed import main_concept
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

# Что значит «БД ученика не читается»: файл битый (``sqlite3.Error``), схема
# не та (``RuntimeError`` из ``migrate``) или в ней лежит неразбираемое
# состояние сессии (pydantic ``ValidationError`` — это ``ValueError``).
# ValueError — SeedError и битые состояния, OSError — пропавшие файлы курса.
UNREADABLE = (sqlite3.Error, RuntimeError, ValueError, KeyError, OSError)

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
class LessonMark:
    """Занятие глазами одного ученика (для воронки)."""

    reached: bool  # дошёл: занятие закрыто или текущее (после анкеты)
    closed: bool
    weak: bool  # закрыто после двух ошибок (⚠️)


@dataclass(frozen=True)
class StudentAnalytics:
    """Агрегаты одного ученика для страницы аналитики (читаются в том же проходе)."""

    items: dict[int, dict] = field(default_factory=dict)
    hints: dict[int, int] = field(default_factory=dict)
    review_due_at: tuple[float, ...] = ()
    review_answered: int = 0
    review_on_time: int = 0


@dataclass(frozen=True)
class StudentData:
    """Всё, что снимок берёт из БД одного ученика за один проход."""

    survey_done: bool
    total: int
    closed: int
    weak: int
    last_activity: float | None
    lessons: dict[str, LessonMark]
    analytics: StudentAnalytics = field(default_factory=StudentAnalytics)

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
    analytics: StudentAnalytics | None = None

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
    """Читает одну БД ученика: прогресс, статусы занятий, активность."""
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
            # Закрытое — всегда «дошёл» (иначе закрыли > дошли); текущее — только
            # у прошедшего анкету: до неё статусы занятий ничего не значат.
            reached=row.id in closed or (survey_done and row.status == "current"),
            closed=row.id in closed,
            weak=row.weak,
        )
        for row in rows
    }
    latest = conn.execute(
        "SELECT MAX(ts) FROM (SELECT MAX(ts) AS ts FROM events UNION ALL SELECT MAX(ts) FROM messages)"
    ).fetchone()[0]
    item_stats: dict[int, dict] = {}
    for row in conn.execute(
        "WITH answers AS ("
        " SELECT item_id, ts, source, MAX(result) AS result,"
        " MAX(hints_used) AS hints, MAX(time_spent) AS time_spent"
        " FROM events WHERE item_id IS NOT NULL"
        " GROUP BY item_id, ts, source"
        ") SELECT item_id, COUNT(*) AS attempts,"
        " SUM(CASE WHEN result >= 0.5 THEN 1 ELSE 0 END) AS correct,"
        " SUM(hints) AS hints_sum, SUM(time_spent) AS time_sum,"
        " COUNT(time_spent) AS time_n"
        " FROM answers GROUP BY item_id"
    ):
        item = repos.get_item(conn, row["item_id"])
        item_stats[row["item_id"]] = {
            "attempts": row["attempts"],
            "correct": row["correct"] or 0,
            "hints_sum": row["hints_sum"] or 0,
            "time_sum": row["time_sum"] or 0,
            "time_n": row["time_n"] or 0,
            "prompt": _preview(item.prompt) if item is not None else None,
        }
    hints = {
        row["hints"]: row["count"]
        for row in conn.execute(
            "SELECT hints, COUNT(*) AS count FROM ("
            " SELECT item_id, ts, source, MAX(hints_used) AS hints FROM events"
            " WHERE item_id IS NOT NULL GROUP BY item_id, ts, source"
            ") GROUP BY hints"
        )
    }
    review_due_at = tuple(
        row[0] for row in conn.execute("SELECT due_at FROM review_schedule")
    )
    review_answered, review_on_time = repos.review_stats(conn)
    return StudentData(
        survey_done=survey_done,
        total=len(rows),
        closed=len(closed),
        weak=sum(1 for row in rows if row.weak),
        last_activity=latest,
        lessons=lessons,
        analytics=StudentAnalytics(
            items=item_stats,
            hints=hints,
            review_due_at=review_due_at,
            review_answered=review_answered,
            review_on_time=review_on_time,
        ),
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
    except UNREADABLE:
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
        analytics=data.analytics,
    )


def _db_mtime(path: Path) -> float:
    """Время последней записи БД с учётом WAL: commit может не трогать main-файл."""
    mtimes = []
    for candidate in (path, path.with_name(path.name + "-wal")):
        try:
            mtimes.append(candidate.stat().st_mtime)
        except OSError:
            pass
    return max(mtimes, default=0.0)


def _data_from_payload(payload: dict) -> StudentData:
    """Восстановить ``StudentData`` из JSON материализованной сводки."""
    lessons = {
        key: LessonMark(**value)
        for key, value in (payload.get("lessons") or {}).items()
    }
    raw = payload.get("analytics") or {}
    analytics = StudentAnalytics(
        items={int(key): value for key, value in (raw.get("items") or {}).items()},
        hints={int(key): int(value) for key, value in (raw.get("hints") or {}).items()},
        review_due_at=tuple(float(value) for value in (raw.get("review_due_at") or ())),
        review_answered=int(raw.get("review_answered") or 0),
        review_on_time=int(raw.get("review_on_time") or 0),
    )
    return StudentData(
        survey_done=bool(payload.get("survey_done")),
        total=int(payload.get("total") or 0),
        closed=int(payload.get("closed") or 0),
        weak=int(payload.get("weak") or 0),
        last_activity=payload.get("last_activity"),
        lessons=lessons,
        analytics=analytics,
    )


def _row_from_data(user_id: int, data: StudentData, *, busy: bool = False) -> StudentRow:
    return StudentRow(
        user_id,
        data.status,
        closed=data.closed,
        total=data.total,
        weak=data.weak,
        last_activity=data.last_activity,
        busy=busy,
        lessons=data.lessons,
        analytics=data.analytics,
    )


def update_progress(state, course: Course, conn, user_id: int, *, now: float | None = None) -> None:
    """Обновить материализованную сводку ученика после хода или записи.

    Читает только БД этого ученика (соединение уже открыто) и пишет одну строку
    в общую БД аккаунтов. Админка потом не обходит все БД учеников.
    """
    try:
        data = read_student(conn, settings=state.settings)
    except UNREADABLE:
        logger.exception("Сводка: БД ученика %s по курсу %s не читается", user_id, course.id)
        return
    stamp = time.time() if now is None else now
    try:
        state.accounts.execute(
            "INSERT INTO course_progress (course_id, user_id, data, updated_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(course_id, user_id) DO UPDATE SET "
            "data = excluded.data, updated_at = excluded.updated_at",
            (course.id, user_id, json.dumps(asdict(data), ensure_ascii=False), stamp),
        )
        state.accounts.commit()
    except sqlite3.Error:
        logger.exception("Сводка: не удалось записать прогресс ученика %s", user_id)
        state.accounts.rollback()


def snapshot(state, course: Course, *, refresh: bool = False, clock=time.time) -> Snapshot:
    """Снимок курса из материализованной сводки; недостающих добирает из БД.

    ``state`` — ``app.state``. Функция синхронная и без ``await``: два запроса
    подряд не построят снимок дважды. Сводка обновляется после каждого хода;
    старый ученик без строки читается один раз и сразу попадает в сводку.
    """
    now = clock()
    cached = state.admin_cache.get(course.id)
    if cached is not None and not refresh and now - cached.built_at < SNAPSHOT_TTL:
        return cached
    earlier = cached.rows if cached is not None else {}
    stored: dict[int, tuple[str, float]] = {}
    try:
        if refresh:
            # Явное «Обновить» — доверять только БД, а не сводке.
            raise LookupError
        for row in state.accounts.execute(
            "SELECT user_id, data, updated_at FROM course_progress WHERE course_id = ?",
            (course.id,),
        ):
            stored[int(row["user_id"])] = (row["data"], float(row["updated_at"]))
    except (LookupError, sqlite3.Error):
        if not refresh:
            logger.exception("Сводка курса %s не читается, падаем на обход БД", course.id)
        stored = {}
    rows: dict[int, StudentRow] = {}
    for user_id in accounts.enrolled_user_ids(state.accounts, course.id):
        busy = state.userdbs.lock(user_id, course.id).locked()
        stored_row = stored.get(user_id)
        if stored_row is not None:
            payload, updated_at = stored_row
            # БД изменили мимо хода (тест, перенос, восстановление): сводка
            # устарела. Сравнение mtime — один stat на ученика, без чтения БД.
            try:
                path = state.userdbs.path(user_id, course.id)
                modified = _db_mtime(path)
                exists = path.is_file()
            except (KeyError, OSError):
                modified = 0.0
                exists = False
            if not busy and exists and modified <= updated_at:
                try:
                    rows[user_id] = _row_from_data(
                        user_id, _data_from_payload(json.loads(payload)), busy=busy
                    )
                    continue
                except (KeyError, TypeError, ValueError):
                    logger.warning("Сводка ученика %s битая, читаю БД", user_id)
        row = _row(state, course, user_id, earlier.get(user_id))
        rows[user_id] = row
        if row.status not in ("no_db", "error", "busy"):
            # Новый/старый ученик без строки: один раз читаем и фиксируем.
            update_progress(
                state,
                course,
                state.userdbs.open(user_id, course.id),
                user_id,
                now=now,
            )
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
    """Дата и время для таблиц; нет значения или метка не разбирается — прочерк.

    Время — по часам сервера. Метка в ``events`` бывает мусорной (``inf``, «из
    будущего» за пределом платформы): такая строка не должна ронять страницу.
    """
    if ts is None:
        return "—"
    try:
        return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")
    except (OverflowError, OSError, ValueError):
        return "—"


# --- Карточка ученика ---

RECENT_ANSWERS = 50
LOG_LIMIT = 20
# Сколько символов условия задания показывать в таблице ответов.
PROMPT_PREVIEW = 80

ACTION_LABELS = {
    "reset_password": "Сброс пароля",
    "close_sessions": "Закрытие сессий",
    "grant_admin": "Выдана роль админа",
    "revoke_admin": "Снята роль админа",
    # Действия над курсами (преподаватель создаёт курс): в журнале видно, что
    # именно делали с курсом, — подробности в поле details.
    "create_course": "Создание курса",
    "update_course": "Правка курса",
    "delete_course": "Удаление курса",
    "add_source": "Добавлен источник",
    "remove_source": "Убран источник",
    "generate_course": "Запуск генерации",
    "cancel_generation": "Отмена генерации",
    "publish_course": "Публикация курса",
    "unpublish_course": "Снятие с публикации",
    "save_version": "Сохранена версия курса",
    "restore_version": "Курс возвращён к версии",
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
    # Событие на концепт задания: один ответ — одна строка; занятие — по главному
    # концепту задания, а не по тому, чьё событие оказалось первым.
    for ts, concept_id, item_id, source, score, hints, spent in conn.execute(
        "SELECT ts, concept_id, item_id, source, result, hints_used, time_spent FROM events "
        "WHERE id IN (SELECT MIN(id) FROM events WHERE item_id IS NOT NULL "
        "GROUP BY item_id, ts, source) ORDER BY ts DESC, id DESC LIMIT ?",
        (RECENT_ANSWERS,),
    ):
        item = repos.get_item(conn, item_id)
        main = main_concept(item) if item is not None else None
        concept_id = main or concept_id
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
                "time_spent": format_duration(spent),
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
    except UNREADABLE:
        logger.exception("БД ученика %s по курсу %s не открывается", account.id, course.id)
        return StudentCard(state="error", **base)
    return StudentCard(state="ok", learning=learning, answers=answers, **base)


# --- Аналитика курса ---


@dataclass(frozen=True)
class LessonFunnel:
    id: str
    name: str
    reached: int  # учеников, дошедших до занятия
    closed: int
    weak: int  # из закрывших — закрыли после двух ошибок

    @property
    def rate(self) -> float | None:
        """Доля закрытия среди дошедших; до занятия никто не дошёл — ``None``."""
        return self.closed / self.reached if self.reached else None


@dataclass(frozen=True)
class ModuleFunnel:
    """Модуль в воронке: строка-сводка (видна свёрнутой) и занятия внутри."""

    number: int
    title: str
    lessons: tuple[LessonFunnel, ...]
    started: int  # учеников, дошедших хотя бы до одного занятия модуля
    finished: int  # учеников, закрывших все занятия модуля


def format_duration(seconds: float | None) -> str:
    """Длительность для таблиц: секунды, потом минуты и секунды."""
    if seconds is None:
        return "—"
    total = max(0, round(seconds))
    if total < 60:
        return f"{total} с"
    return f"{total // 60} мин {total % 60} с"


@dataclass(frozen=True)
class ItemStat:
    """Задание в аналитике курса: попытки, верные, подсказки и время."""

    item_id: int
    prompt: str
    attempts: int
    correct: int
    avg_hints: float
    avg_time: float | None
    # Корреляция «верно ли задание у ученика» с его общим результатом:
    # на малой выборке или без разброса не считается.
    discrimination: float | None = None

    @property
    def correct_rate(self) -> float | None:
        return self.correct / self.attempts if self.attempts else None

    @property
    def difficulty(self) -> float | None:
        """Сложность задания: доля неверных ответов (1 − доля верных)."""
        rate = self.correct_rate
        return None if rate is None else 1.0 - rate

    @property
    def difficulty_label(self) -> str:
        return "—" if self.difficulty is None else f"{round(self.difficulty * 100)}%"

    @property
    def hints_label(self) -> str:
        return f"{self.avg_hints:.1f}"

    @property
    def time_label(self) -> str:
        return format_duration(self.avg_time)

    @property
    def discrimination_label(self) -> str:
        return "—" if self.discrimination is None else f"{self.discrimination:.2f}"


@dataclass(frozen=True)
class GroupStat:
    """Группа учеников по статусу: сколько и насколько далеко прошли."""

    label: str
    count: int
    avg_percent: float
    avg_closed: float
    avg_weak: float = 0.0


@dataclass(frozen=True)
class CourseAnalytics:
    """Сводка по заданиям, подсказкам и расписанию повторений."""

    built_at: float
    items: tuple[ItemStat, ...]
    hints: tuple[tuple[int, int], ...]  # (hints_used, ответов)
    review_total: int
    review_due: int
    review_overdue: int
    review_answered: int
    review_on_time: int
    groups: tuple[GroupStat, ...] = ()

    @property
    def review_adherence(self) -> float | None:
        if not self.review_answered:
            return None
        return self.review_on_time / self.review_answered


# Сколько строк заданий показывать: длинный список не читается.
ANALYTICS_ITEMS = 40
# Просрочка «больше суток» — отдельно от «готово сейчас».
OVERDUE_AFTER = 86_400.0


def _pearson(pairs: list[tuple[float, float]]) -> float | None:
    """Корреляция Пирсона; меньше трёх точек или нулевой разброс — ``None``."""
    if len(pairs) < 3:
        return None
    mean_x = sum(x for x, _ in pairs) / len(pairs)
    mean_y = sum(y for _, y in pairs) / len(pairs)
    var_x = sum((x - mean_x) ** 2 for x, _ in pairs)
    var_y = sum((y - mean_y) ** 2 for _, y in pairs)
    if var_x <= 1e-12 or var_y <= 1e-12:
        return None
    cov = sum((x - mean_x) * (y - mean_y) for x, y in pairs)
    return cov / (var_x * var_y) ** 0.5


def analytics(snap: Snapshot, *, now: float | None = None) -> CourseAnalytics:
    """Аналитика поверх готового снимка: ученик уже прочитан одним проходом.

    Страницы админки используют один снимок, поэтому второй обход БД здесь
    запрещён: иначе список учеников и аналитика читали бы каждую БД дважды.
    """
    stamp = time.time() if now is None else now
    totals: dict[int, dict] = {}
    hints: dict[int, int] = {}
    per_item: dict[int, list[tuple[float, float]]] = {}
    review_total = review_due = review_overdue = 0
    review_answered = review_on_time = 0
    for row in snap.rows.values():
        data = row.analytics
        if data is None:
            continue
        student_attempts = sum(bucket["attempts"] for bucket in data.items.values())
        student_correct = sum(bucket["correct"] for bucket in data.items.values())
        overall = student_correct / student_attempts if student_attempts else None
        for item_id, bucket in data.items.items():
            if overall is not None and bucket["attempts"]:
                per_item.setdefault(item_id, []).append(
                    (bucket["correct"] / bucket["attempts"], overall)
                )
            target = totals.setdefault(
                item_id,
                {
                    "attempts": 0,
                    "correct": 0,
                    "hints_sum": 0.0,
                    "time_sum": 0.0,
                    "time_n": 0,
                    "prompt": bucket.get("prompt"),
                },
            )
            target["attempts"] += bucket["attempts"]
            target["correct"] += bucket["correct"]
            target["hints_sum"] += bucket["hints_sum"]
            target["time_sum"] += bucket["time_sum"]
            target["time_n"] += bucket["time_n"]
            if not target["prompt"] and bucket.get("prompt"):
                target["prompt"] = bucket["prompt"]
        for hints_used, count in data.hints.items():
            hints[hints_used] = hints.get(hints_used, 0) + count
        for due_at in data.review_due_at:
            review_total += 1
            if due_at <= stamp:
                review_due += 1
            if due_at <= stamp - OVERDUE_AFTER:
                review_overdue += 1
        review_answered += data.review_answered
        review_on_time += data.review_on_time
    items = []
    for item_id, bucket in sorted(
        totals.items(), key=lambda pair: (-pair[1]["attempts"], pair[0])
    )[:ANALYTICS_ITEMS]:
        attempts = bucket["attempts"]
        items.append(
            ItemStat(
                item_id=item_id,
                prompt=bucket.get("prompt") or "—",
                attempts=attempts,
                correct=bucket["correct"],
                avg_hints=(bucket["hints_sum"] / attempts) if attempts else 0.0,
                avg_time=(bucket["time_sum"] / bucket["time_n"]) if bucket["time_n"] else None,
                discrimination=_pearson(per_item.get(item_id, [])),
            )
        )
    grouped: dict[str, list[StudentRow]] = {}
    for row in snap.rows.values():
        grouped.setdefault(row.status, []).append(row)
    groups = []
    for status in ("done", "in_progress", "survey", "error", "no_db", "busy"):
        rows = grouped.get(status)
        if not rows:
            continue
        groups.append(
            GroupStat(
                label=STATUS_LABELS.get(status, status),
                count=len(rows),
                avg_percent=sum(row.percent for row in rows) / len(rows),
                avg_closed=sum(row.closed or 0 for row in rows) / len(rows),
                avg_weak=sum(row.weak or 0 for row in rows) / len(rows),
            )
        )
    return CourseAnalytics(
        built_at=snap.built_at,
        items=tuple(items),
        hints=tuple(sorted(hints.items())),
        review_total=review_total,
        review_due=review_due,
        review_overdue=review_overdue,
        review_answered=review_answered,
        review_on_time=review_on_time,
        groups=tuple(groups),
    )


def _published(data: CourseData):
    return [topic for topic in data.topics if topic.published and topic.active]


def funnel(snap: Snapshot, data: CourseData) -> list[ModuleFunnel]:
    """Воронка по занятиям в порядке курса; неопубликованные модули не показываются.

    Занятия, до которых никто не дошёл, остаются — нулями. Ученики без данных
    (нет БД, ошибка, ход без прежнего снимка) в подсчёт не входят.
    """
    marks = [row.lessons for row in snap.rows.values() if row.lessons]
    result = []
    names = {node.id: node.name for node in data.nodes}
    for topic in _published(data):
        ids = [node.id for node in data.nodes if node.topic_id == topic.number]
        lessons = []
        for node_id in ids:
            own = [lessons_of[node_id] for lessons_of in marks if node_id in lessons_of]
            lessons.append(
                LessonFunnel(
                    id=node_id,
                    name=names[node_id],
                    reached=sum(1 for mark in own if mark.reached),
                    closed=sum(1 for mark in own if mark.closed),
                    weak=sum(1 for mark in own if mark.closed and mark.weak),
                )
            )
        own_module = [[lessons_of[i] for i in ids if i in lessons_of] for lessons_of in marks]
        result.append(
            ModuleFunnel(
                number=topic.number,
                title=topic.title,
                lessons=tuple(lessons),
                started=sum(1 for module in own_module if any(mark.reached for mark in module)),
                finished=sum(
                    1 for module in own_module if len(module) == len(ids) and all(m.closed for m in module)
                ),
            )
        )
    return result
