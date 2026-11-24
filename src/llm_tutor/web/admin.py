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

import logging
import sqlite3
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from llm_tutor.config import Settings
from llm_tutor.course.seed import Course as CourseData
from llm_tutor.course.seed import main_concept
from llm_tutor.core import explain as core_explain
from llm_tutor.core.lesson import course_survey_done, is_closed
from llm_tutor.core.overview import topic_rows
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.schemas import SessionState
from llm_tutor.student import guide, planner
from llm_tutor.web import accounts, views
from llm_tutor.web.courses import Course

logger = logging.getLogger(__name__)

# Сколько снимок считается свежим.
SNAPSHOT_TTL = 60.0
# «Активен» в сводке — была активность за столько секунд.
ACTIVE_WINDOW = 7 * 24 * 3600.0
# Задание без движения столько — ученик «застрял» (для пометки в списке).
STUCK_AFTER = 12 * 3600.0
# Окно, за которое считаются неудачные попытки на текущем занятии.
FAILURE_WINDOW = 7 * 24 * 3600.0

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
class StudentData:
    """Всё, что снимок берёт из БД одного ученика за один проход."""

    survey_done: bool
    total: int
    closed: int
    weak: int
    last_activity: float | None
    lessons: dict[str, LessonMark]
    # Где ученик сейчас: занятие и висящее задание. Нужно списку, чтобы
    # показать «застрял», и профилю — чтобы открыть чужой экран.
    current_node_id: str | None = None
    pending_item_id: int | None = None
    pending_prompt: str | None = None
    # Когда ученик последний раз делал ход (``SessionState.last_activity``).
    # Это не то же, что ``last_activity``: там максимум по журналу и сообщениям.
    state_activity: float | None = None

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
    current_node_id: str | None = None
    pending_item_id: int | None = None
    pending_prompt: str | None = None
    state_activity: float | None = None

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
    pending = (
        repos.get_item(conn, state.pending_item_id)
        if state.pending_item_id is not None
        else None
    )
    return StudentData(
        survey_done=survey_done,
        total=len(rows),
        closed=len(closed),
        weak=sum(1 for row in rows if row.weak),
        last_activity=latest,
        lessons=lessons,
        current_node_id=state.current_node_id,
        pending_item_id=state.pending_item_id,
        pending_prompt=_preview(pending.prompt) if pending is not None else None,
        state_activity=state.last_activity,
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
        current_node_id=data.current_node_id,
        pending_item_id=data.pending_item_id,
        pending_prompt=data.pending_prompt,
        state_activity=data.state_activity,
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


def format_duration(seconds: float) -> str:
    """Сколько ждать/прошло — коротко: «2 дн. 3 ч.», «5 ч.», «12 мин.»."""
    seconds = max(0.0, seconds)
    days, rest = divmod(int(seconds), 86400)
    hours = rest // 3600
    if days:
        return f"{days} дн." + (f" {hours} ч." if hours else "")
    if hours:
        return f"{hours} ч."
    return f"{max(1, int(seconds // 60))} мин."


def stuck_note(row: StudentRow, *, now: float) -> str | None:
    """Пометка для списка: ученик висит на задании дольше ``STUCK_AFTER``.

    Коротко, одной строкой: номер задания, сколько ждёт и начало условия.
    """
    if row.pending_item_id is None:
        return None
    since = row.state_activity if row.state_activity is not None else row.last_activity
    if since is None:
        return f"Задание #{row.pending_item_id} без ответа"
    waited = now - since
    if waited < STUCK_AFTER:
        return None
    prompt = f" · {row.pending_prompt}" if row.pending_prompt else ""
    return f"Задание #{row.pending_item_id} · ждёт {format_duration(waited)}{prompt}"


# --- Пульс ученика ---

# Сколько недель показывать в календаре активности (как год в GitHub).
PULSE_WEEKS = 53
PULSE_HOURS = ((0, 6, "Ночь"), (6, 12, "Утро"), (12, 18, "День"), (18, 24, "Вечер"))
PULSE_WEEKDAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
PULSE_MONTHS = (
    "янв",
    "фев",
    "мар",
    "апр",
    "май",
    "июн",
    "июл",
    "авг",
    "сен",
    "окт",
    "ноя",
    "дек",
)


def day_key(ts: float) -> str | None:
    """Ключ дня ``YYYY-MM-DD`` по часам сервера или ``None`` для мусорной метки."""
    try:
        return datetime.fromtimestamp(ts).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


@dataclass(frozen=True)
class Pulse:
    """Агрегаты «Пульса ученика»: календарь, ритм, лента и подсказки."""

    weeks: list[list[dict]]
    month_labels: list[str]
    active_days: int
    active_30: int
    login_days: int
    solved_days: int
    streak: int
    longest_streak: int
    closed: int
    total: int
    percent: int
    answers_7: int
    accuracy_7: int | None
    hours: list[dict]
    weekdays: list[dict]
    best_hour: str | None
    timeline: list[dict]
    insights: list[dict]
    span_start: str
    span_end: str

    @property
    def has_activity(self) -> bool:
        return bool(self.active_days or self.login_days or self.solved_days)


def _pulse_timeline(conn: sqlite3.Connection, *, limit: int = 12) -> list[dict]:
    """Последние ответы на задания одной строкой: без реплик тьютора и чата."""
    graph = CourseGraph.load(conn)
    rows = conn.execute(
        "SELECT ts, item_id, concept_id, result FROM events "
        "WHERE item_id IS NOT NULL ORDER BY ts DESC, id DESC LIMIT ?",
        (limit * 4,),
    ).fetchall()
    seen: set[tuple[float, int]] = set()
    result = []
    for ts, item_id, concept_id, score in rows:
        key = (ts, item_id)
        if key in seen:
            continue
        seen.add(key)
        item = repos.get_item(conn, item_id)
        main = main_concept(item) if item is not None else None
        concept_id = main or concept_id
        lesson = (
            graph.concept(concept_id).name
            if concept_id and graph.has_node(concept_id)
            else None
        )
        result.append(
            {
                "ts": ts,
                "item_id": item_id,
                "lesson": lesson,
                "prompt": _preview(item.prompt) if item is not None else None,
                "result": _result_label(score),
                "right": score >= 0.5,
            }
        )
        if len(result) >= limit:
            break
    return result


def _pulse_insights(
    conn: sqlite3.Connection,
    *,
    data: StudentData,
    flow: dict | None,
    answers_7: int,
    accuracy_7: int | None,
    streak: int,
    last_answer: float | None,
    now: float,
) -> list[dict]:
    """Короткие выводы по правилам: без модели и без догадок."""
    insights = []
    for reason in (flow or {}).get("stuck", []):
        insights.append({"kind": "warning", "text": reason})
    if last_answer is not None and now - last_answer > 3 * 86400:
        idle = format_duration(now - last_answer)
        insights.append({"kind": "warning", "text": f"Нет ответов на задания {idle}"})
    elif last_answer is None and data.last_activity is not None:
        insights.append(
            {
                "kind": "info",
                "text": "Ответов на задания пока нет — начните с короткого задания.",
            }
        )
    if answers_7 >= 3 and accuracy_7 is not None and accuracy_7 < 60:
        insights.append(
            {
                "kind": "warning",
                "text": f"Точность за неделю {accuracy_7}% — стоит вернуться к слабым занятиям.",
            }
        )
    due = conn.execute(
        "SELECT COUNT(*) FROM review_schedule WHERE due_at <= ?", (now,)
    ).fetchone()[0]
    if due:
        insights.append({"kind": "info", "text": f"Пора повторить занятий: {due}."})
    if data.weak:
        insights.append({"kind": "info", "text": f"Занятий закрыто слабо: {data.weak}."})
    if streak >= 3:
        insights.append({"kind": "good", "text": f"Серия {streak} дн. — хороший ритм."})
    if not insights:
        insights.append(
            {
                "kind": "info",
                "text": "Пока мало данных: как появятся ответы, здесь будут подсказки.",
            }
        )
    return insights[:5]


def pulse_view(
    conn: sqlite3.Connection,
    *,
    data: StudentData,
    flow: dict | None,
    login_days: dict[str, int],
    now: float,
) -> Pulse:
    """Считает «Пульс» по журналу ученика и истории входов.

    Календарь — 53 недели по дням сервера. Ответы считаются по ``(ts, item_id)``:
    ядро пишет событие на каждый концепт задания, а ответ всё равно один.
    """
    today = datetime.fromtimestamp(now).date()
    end = today + timedelta(days=6 - today.weekday())
    start = end - timedelta(days=PULSE_WEEKS * 7 - 1)
    start_ts = datetime(start.year, start.month, start.day).timestamp()
    end_ts = datetime(end.year, end.month, end.day).timestamp() + 86400

    stats: dict[str, dict] = {}
    day = start
    while day <= end:
        stats[day.isoformat()] = {
            "logins": 0,
            "attempts": set(),
            "correct": set(),
        }
        day += timedelta(days=1)
    for key, count in login_days.items():
        if key in stats:
            stats[key]["logins"] = count

    learning_stamps: list[float] = []
    for ts, item_id, score in conn.execute(
        "SELECT ts, item_id, result FROM events "
        "WHERE item_id IS NOT NULL AND ts >= ? AND ts < ?",
        (start_ts, end_ts),
    ):
        key = day_key(ts)
        if key not in stats:
            continue
        attempt = (ts, item_id)
        if attempt not in stats[key]["attempts"]:
            learning_stamps.append(ts)
        stats[key]["attempts"].add(attempt)
        if score >= 0.5:
            stats[key]["correct"].add(attempt)

    last_answer = conn.execute(
        "SELECT MAX(ts) FROM events WHERE item_id IS NOT NULL"
    ).fetchone()[0]

    hours = [
        {"label": label, "range": f"{low}–{high}", "count": 0}
        for low, high, label in PULSE_HOURS
    ]
    weekdays = [{"label": label, "count": 0} for label in PULSE_WEEKDAYS]
    for ts in learning_stamps:
        try:
            moment = datetime.fromtimestamp(ts)
        except (OverflowError, OSError, ValueError):
            continue
        weekdays[moment.weekday()]["count"] += 1
        for index, (low, high, _) in enumerate(PULSE_HOURS):
            if low <= moment.hour < high:
                hours[index]["count"] += 1
                break
    hours_max = max((item["count"] for item in hours), default=0)
    for item in hours:
        item["percent"] = round(100 * item["count"] / hours_max) if hours_max else 0
    weekdays_max = max((item["count"] for item in weekdays), default=0)
    for item in weekdays:
        item["percent"] = round(100 * item["count"] / weekdays_max) if weekdays_max else 0
    best = max(hours, key=lambda item: item["count"])
    best_hour = best["label"] if best["count"] else None

    weeks: list[list[dict]] = []
    current: list[dict] = []
    active_keys: set[str] = set()
    for offset in range(PULSE_WEEKS * 7):
        day = start + timedelta(days=offset)
        key = day.isoformat()
        item = stats[key]
        answers = len(item["attempts"])
        correct = len(item["correct"])
        logins = item["logins"]
        activity = logins + answers
        if activity:
            active_keys.add(key)
        if activity == 0:
            level = 0
        elif activity == 1:
            level = 1
        elif activity <= 3:
            level = 2
        elif activity <= 6:
            level = 3
        else:
            level = 4
        parts = []
        if logins:
            parts.append(f"входов: {logins}")
        if answers:
            parts.append(f"ответов: {answers} (верно {correct})")
        title = (
            f"{day:%d.%m.%Y} — " + (", ".join(parts) if parts else "нет активности")
            if day <= today
            else f"{day:%d.%m.%Y} — ещё впереди"
        )
        current.append(
            {
                "key": key,
                "day": day,
                "level": level,
                "solved": answers > 0,
                "future": day > today,
                "title": title,
            }
        )
        if len(current) == 7:
            weeks.append(current)
            current = []

    month_labels = []
    last_month = None
    for week in weeks:
        month = week[0]["day"].month
        month_labels.append(PULSE_MONTHS[month - 1] if month != last_month else "")
        last_month = month

    def in_last(days: int) -> set[str]:
        first = today - timedelta(days=days - 1)
        return {key for key in active_keys if first.isoformat() <= key <= today.isoformat()}

    answers_7 = 0
    correct_7 = 0
    first_7 = today - timedelta(days=6)
    for key, item in stats.items():
        if first_7.isoformat() <= key <= today.isoformat():
            answers_7 += len(item["attempts"])
            correct_7 += len(item["correct"])
    accuracy_7 = round(100 * correct_7 / answers_7) if answers_7 else None

    streak = 0
    cursor = today
    if cursor.isoformat() not in active_keys:
        cursor -= timedelta(days=1)
    while cursor.isoformat() in active_keys:
        streak += 1
        cursor -= timedelta(days=1)
    longest = 0
    run = 0
    for offset in range(PULSE_WEEKS * 7):
        key = (start + timedelta(days=offset)).isoformat()
        if key in active_keys:
            run += 1
            longest = max(longest, run)
        else:
            run = 0

    closed = (flow or {}).get("closed", data.closed)
    total = (flow or {}).get("total", data.total)
    return Pulse(
        weeks=weeks,
        month_labels=month_labels,
        active_days=len(active_keys),
        active_30=len(in_last(30)),
        login_days=sum(1 for key in stats if stats[key]["logins"]),
        solved_days=sum(1 for key in stats if stats[key]["attempts"]),
        streak=streak,
        longest_streak=longest,
        closed=closed,
        total=total,
        percent=round(100 * closed / total) if total else 0,
        answers_7=answers_7,
        accuracy_7=accuracy_7,
        hours=hours,
        weekdays=weekdays,
        best_hour=best_hour,
        timeline=_pulse_timeline(conn),
        insights=_pulse_insights(
            conn,
            data=data,
            flow=flow,
            answers_7=answers_7,
            accuracy_7=accuracy_7,
            streak=streak,
            last_answer=last_answer,
            now=now,
        ),
        span_start=start.strftime("%d.%m.%Y"),
        span_end=today.strftime("%d.%m.%Y"),
    )


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
    "view_explain": "Просмотр сессии «Объясни новичку»",
    # Действия над курсами (преподаватель создаёт курс): в журнале видно, что
    # именно делали с курсом, — подробности в поле details.
    "create_course": "Создание курса",
    "copy_course": "Создание копии курса",
    "update_course": "Правка курса",
    "delete_course": "Удаление курса",
    "add_source": "Добавлен источник",
    "remove_source": "Убран источник",
    "generate_course": "Запуск генерации",
    "cancel_generation": "Отмена генерации",
    "publish_course": "Публикация курса",
    "unpublish_course": "Снятие с публикации",
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
    # Сессии «Объясни новичку» (срез 89); ``None`` — ученик не открыл доступ.
    explain: list[dict] | None = None


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


# Так ``code_message`` отделяет код от итога тестов (``web/api_lesson``).
TEST_MARK = "\n\nРезультат проверки:"


def _test_result(text: str | None) -> dict | None:
    """Разбор итога тестов из реплики ученика с кодом — или ``None``."""
    if not text or TEST_MARK not in text:
        return None
    code, _, verdict = text.rpartition(TEST_MARK)
    verdict = verdict.strip()
    passed = verdict.startswith("все проверки пройдены")
    report = ""
    if not passed:
        _, _, report = verdict.partition("—")
        report = report.strip() or verdict
    return {"code": code.strip(), "passed": passed, "report": report}


def _recent_answers(conn: sqlite3.Connection) -> list[dict]:
    """Последние ответы на задания: время, занятие, задание, итог и сам ответ.

    Ответ лежит в ``messages``: ход пишет реплику ученика и событие с одним
    ``ts``. Реплики без события (вопрос тьютору) сюда не попадают — карточка и
    профиль показывают только ответы на задания, а не переписку.
    """
    graph = CourseGraph.load(conn)
    rows = conn.execute(
        "SELECT ts, concept_id, item_id, source, result FROM events "
        "WHERE id IN (SELECT MIN(id) FROM events WHERE item_id IS NOT NULL "
        "GROUP BY item_id, ts, source) ORDER BY ts DESC, id DESC LIMIT ?",
        (RECENT_ANSWERS,),
    ).fetchall()
    texts: dict[float, str] = {}
    if rows:
        marks = ",".join("?" for _ in rows)
        texts = {
            ts: content
            for ts, content in conn.execute(
                f"SELECT ts, content FROM messages WHERE role = 'user' AND ts IN ({marks})",
                [row[0] for row in rows],
            )
        }
    result = []
    # Событие на концепт задания: один ответ — одна строка; занятие — по главному
    # концепту задания, а не по тому, чьё событие оказалось первым.
    for ts, concept_id, item_id, source, score in rows:
        item = repos.get_item(conn, item_id)
        main = main_concept(item) if item is not None else None
        concept_id = main or concept_id
        lesson = graph.concept(concept_id).name if concept_id and graph.has_node(concept_id) else None
        text = texts.get(ts)
        result.append(
            {
                "ts": ts,
                "lesson": lesson,
                "item_id": item_id,
                "prompt": _preview(item.prompt) if item is not None else None,
                "source": source,
                "result": _result_label(score),
                "right": score >= 0.5,
                "answer": text,
                "test": _test_result(text),
                "type": item.answer_type if item is not None else None,
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
        sessions = explain_sessions_for(conn)
    except UNREADABLE:
        logger.exception("БД ученика %s по курсу %s не открывается", account.id, course.id)
        return StudentCard(state="error", **base)
    return StudentCard(state="ok", learning=learning, answers=answers, explain=sessions, **base)


PHASE_LABELS = {"explain": "объяснение", "practice": "практика", "check": "проверка"}
REVIEW_KIND_LABELS = {"warmup": "разминка", "due": "срочное", "refresh": "освежить"}


@dataclass(frozen=True)
class StudentProfile:
    """Профиль ученика для админа: то же, что видит сам ученик, но только чтение.

    ``state`` — как у карточки: ``ok``, ``not_enrolled``, ``no_db``, ``error``,
    ``busy``. В карточку диалоги не попадают; в профиле — только ответы на
    задания (реплики с событием ``item_id``), а не переписка с тьютором.
    ``pulse`` — агрегаты входов и ответов за 53 недели, без текста чата.
    """

    account: accounts.AccountRow
    sessions: int
    state: str
    learning: dict | None = None
    flow: dict | None = None
    answers: list[dict] = field(default_factory=list)
    pulse: Pulse | None = None


def _flow_view(
    conn: sqlite3.Connection, *, data: StudentData, settings: Settings, now: float
) -> dict:
    """Где ученик сейчас: занятие, раздел, висящее задание и признаки застревания."""
    graph = CourseGraph.load(conn)
    session_id = repos.get_open_session(conn)
    state = repos.get_session_state(conn, session_id) if session_id else SessionState()
    node_id = (
        state.current_node_id
        if state.current_node_id and graph.has_node(state.current_node_id)
        else None
    )
    concept = graph.concept(node_id) if node_id is not None else None
    lesson = (
        views.lesson_view(conn, graph, state, node_id, settings=settings)
        if node_id is not None
        else None
    )
    section_title = None
    if concept is not None and concept.sections:
        section_id = guide.current_section(state, [section.id for section in concept.sections])
        section_title = next(
            (section.title for section in concept.sections if section.id == section_id), None
        )
    item = (
        repos.get_item(conn, state.pending_item_id)
        if state.pending_item_id is not None
        else None
    )
    review = state.review
    review_node = (
        graph.concept(review.node_id).name
        if review is not None and review.node_id and graph.has_node(review.node_id)
        else None
    )
    explain = core_explain.blocking(conn)
    failures = 0
    if node_id is not None:
        failures = conn.execute(
            "SELECT COUNT(DISTINCT ts) FROM events "
            "WHERE concept_id = ? AND result < 0.5 AND ts >= ?",
            (node_id, now - FAILURE_WINDOW),
        ).fetchone()[0]
    since = data.state_activity if data.state_activity is not None else data.last_activity
    waiting = now - since if item is not None and since is not None else None

    reasons = []
    if item is not None and waiting is not None and waiting >= STUCK_AFTER:
        reasons.append(f"Задание #{item.id} ждёт ответа {format_duration(waiting)}")
    if failures >= planner.STUCK_FAILURES:
        reasons.append(f"Неудачных попыток на занятии за неделю: {failures}")
    if node_id is not None and data.lessons.get(node_id) is not None and data.lessons[node_id].weak:
        reasons.append("Занятие закрыто слабо")
    if explain is not None:
        reasons.append(f"Ждёт «Объясни новичку»: {explain[1]}")
    if (
        data.status != "done"
        and data.last_activity is not None
        and now - data.last_activity > ACTIVE_WINDOW
    ):
        reasons.append(f"Нет активности {format_duration(now - data.last_activity)}")

    return {
        "node": {"id": concept.id, "name": concept.name} if concept is not None else None,
        "lesson": lesson,
        "section": section_title,
        "phase": PHASE_LABELS.get(state.phase, state.phase),
        "mode": planner.MODE_LABELS.get(state.mode) if state.mode else None,
        "pending_item": views.item_view(item, graph) if item is not None else None,
        "waiting": waiting,
        "waiting_label": format_duration(waiting) if waiting is not None else None,
        "review": (
            {"kind": REVIEW_KIND_LABELS.get(review.kind, review.kind), "node": review_node}
            if review is not None
            else None
        ),
        "explain": explain[1] if explain is not None else None,
        "failures": failures,
        "stuck": reasons,
        "last_activity": data.last_activity,
        "state_activity": data.state_activity,
        "survey_done": data.survey_done,
        "closed": data.closed,
        "total": data.total,
        "status": data.status,
    }


def student_profile(
    state, course: Course, account: accounts.AccountRow, *, now: float | None = None
) -> StudentProfile:
    """Профиль ученика без кэша; читает БД, только если ход не идёт (как карточка)."""
    stamp = time.time() if now is None else now
    base = {
        "account": account,
        "sessions": accounts.active_sessions(state.accounts, account.id, stamp),
    }
    if course.id not in accounts.enrolled_courses(state.accounts, account.id):
        return StudentProfile(state="not_enrolled", **base)
    pool = state.userdbs
    if not pool.exists(account.id, course.id):
        return StudentProfile(state="no_db", **base)
    if pool.lock(account.id, course.id).locked():
        return StudentProfile(state="busy", **base)
    try:
        conn = pool.open(account.id, course.id)
        data = read_student(conn, settings=state.settings)
        learning = views.learning_view(conn, settings=state.settings)
        flow = _flow_view(conn, data=data, settings=state.settings, now=stamp)
        answers = _recent_answers(conn)
        pulse = pulse_view(
            conn,
            data=data,
            flow=flow,
            login_days=accounts.login_day_counts(state.accounts, account.id),
            now=stamp,
        )
    except UNREADABLE:
        logger.exception("БД ученика %s по курсу %s не открывается", account.id, course.id)
        return StudentProfile(state="error", **base)
    return StudentProfile(
        state="ok", learning=learning, flow=flow, answers=answers, pulse=pulse, **base
    )


KIND_LABELS = {"required": "обязательное", "voluntary": "добровольное"}


def explain_sessions_for(conn: sqlite3.Connection) -> list[dict] | None:
    """Сессии «Объясни новичку» для карточки; без согласия ученика — ``None``.

    Читается синхронно, в той же ветке, что и остальная карточка (ход не идёт).
    """
    if not core_explain.share_enabled(conn):
        return None
    names = {concept.id: concept.name for concept in repos.get_concepts(conn)}
    rows = []
    for session in repos.list_explain_sessions(conn):
        report = session.report
        rows.append(
            {
                "id": session.id,
                "topic": session.topic_id,
                # Занятие сессии; старая модульная сессия (срез 85) — «Модуль N».
                "title": names.get(session.concept_id or "") or f"Модуль {session.topic_id}",
                "kind": KIND_LABELS[session.kind],
                "started_at": session.started_at,
                "ended_at": session.ended_at,
                "coverage": f"{report.required_done} из {report.required_total}" if report else "идёт",
            }
        )
    return rows


def explain_session(state, course: Course, user_id: int, session_id: int) -> tuple[str, dict | None]:
    """Сессия ученика для админа: ``("ok", вид)``, ``("busy", None)`` или ``("hidden", None)``.

    ``hidden`` — нет записи, БД, согласия или такой сессии: для админа это 404,
    как и несуществующая страница. Ученика с идущим ходом не читаем.
    """
    if course.id not in accounts.enrolled_courses(state.accounts, user_id):
        return "hidden", None
    pool = state.userdbs
    if not pool.exists(user_id, course.id):
        return "hidden", None
    if pool.lock(user_id, course.id).locked():
        return "busy", None
    conn = pool.open(user_id, course.id)
    if not core_explain.share_enabled(conn):
        return "hidden", None
    session = repos.get_explain_session(conn, session_id)
    if session is None:
        return "hidden", None
    names = {concept.id: concept.name for concept in repos.get_concepts(conn)}
    view = views.explain_session_view(
        session,
        repos.get_explain_turns(conn, session.id),
        repos.get_explain_configs(conn).get(session.concept_id or ""),
        title=names.get(session.concept_id or "") or f"Модуль {session.topic_id}",
        max_turns=state.explain_prompts.limits.max_student_turns,
        lesson_names=names,
    )
    return "ok", view


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
