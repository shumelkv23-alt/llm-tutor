"""Лестница повторений закрытых занятий (срез 79).

Чистая арифметика расписания: когда занятию возвращаться и как меняется
ступень после ответа. Выбор занятий к повторению и хуки закрытия живут в
других модулях; здесь — только ступени, сроки и запись изменений в БД.
Время — unix-секунды UTC, «день» — календарный день UTC.
"""

import sqlite3
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass

from llm_tutor.config import Settings
from llm_tutor.course.graph import CourseGraph
from llm_tutor.course.seed import main_concept
from llm_tutor.db import repos
from llm_tutor.schemas import Item, ReviewRow, Route
from llm_tutor.student import beta, diagnostic

# Через сколько дней после закрытия (ступень 0) и после верного повторения
# (ступени 1…5) занятию возвращаться.
LADDER_DAYS = (1.0, 3.0, 7.0, 14.0, 30.0, 60.0)
# Не больше стольких сроков на один день UTC: хвост уезжает на следующий день.
DAILY_LIMIT = 10
# С этой ступени повторение — проверка (после разминки), а не просто напоминание.
VERIFY_START_STEP = 2
# Задание, решённое меньше суток назад, для повторения не берётся.
RECENT_ITEM_SECONDS = 86_400.0
# Размер разминки между занятиями и серии на вкладке «Повторение».
WARMUP_SIZE = 2
SERIES_SIZE = 5

_DAY = 86_400.0


@dataclass(frozen=True)
class ScheduleChange:
    """Изменение строки расписания; ``step is None`` — удалить строку."""

    concept_id: str
    step: int | None
    due_at: float | None


def utc_day(ts: float) -> int:
    """Номер календарного дня UTC (с 1970-01-01)."""
    return int(ts // _DAY)


def first_due(
    rows: Mapping[str, ReviewRow],
    *,
    step: int,
    now: float,
    pending: Sequence[ScheduleChange] = (),
) -> float:
    """Срок для занятия на ступени ``step``: первый день с запасом по лимиту.

    Отсчёт — от ``utc_day(now) + LADDER_DAYS[step]``; сроки в ``rows`` и в ещё
    не записанных ``pending`` считаются вместе, день с ``DAILY_LIMIT`` сроков
    пропускается. Время суток — как у ``now``.
    """
    load: dict[int, int] = {}
    for row in rows.values():
        day = utc_day(row.due_at)
        load[day] = load.get(day, 0) + 1
    for change in pending:
        if change.due_at is not None:
            day = utc_day(change.due_at)
            load[day] = load.get(day, 0) + 1

    day = utc_day(now) + int(LADDER_DAYS[step])
    while load.get(day, 0) >= DAILY_LIMIT:
        day += 1
    return day * _DAY + (now - utc_day(now) * _DAY)


def plan_answer(
    row: ReviewRow | None,
    concept_id: str,
    *,
    passed: bool,
    hinted: bool,
    due: bool,
    now: float,
) -> ScheduleChange | None:
    """Что делать с расписанием после ответа по занятию.

    Нет строки: ошибка заводит ступень 0 на завтра, верный ответ ничего не
    меняет. Ошибка — на две ступени вниз (не ниже 0), завтра. Верно с
    подсказкой — ступень та же, завтра. Верно в срок — ступень вверх; после
    последней занятие уходит с лестницы. Верно раньше срока — без изменений.
    """
    if row is None:
        if passed:
            return None
        return ScheduleChange(concept_id, 0, now + _DAY)
    if not passed:
        return ScheduleChange(concept_id, max(0, row.step - 2), now + _DAY)
    if hinted:
        return ScheduleChange(concept_id, row.step, now + _DAY)
    if not due:
        return None
    nxt = row.step + 1
    if nxt >= len(LADDER_DAYS):
        return ScheduleChange(concept_id, None, None)
    return ScheduleChange(concept_id, nxt, now + LADDER_DAYS[nxt] * _DAY)


def plan_postpone(
    rows: Mapping[str, ReviewRow],
    concept_ids: Iterable[str],
    *,
    now: float,
) -> list[ScheduleChange]:
    """Откладывает занятия на день: ступень та же, срок ``now + день``.

    Занятия без строки в расписании пропускаются.
    """
    return [
        ScheduleChange(cid, rows[cid].step, now + _DAY) for cid in concept_ids if cid in rows
    ]


def write_changes(
    conn: sqlite3.Connection, changes: Iterable[ScheduleChange], *, commit: bool = True
) -> None:
    """Применяет изменения расписания: ``step is None`` — удалить строку."""
    for change in changes:
        if change.step is None or change.due_at is None:
            repos.delete_review(conn, change.concept_id, commit=False)
        else:
            repos.upsert_review(conn, change.concept_id, change.step, change.due_at, commit=False)
    if commit:
        conn.commit()


def pick_item(
    conn: sqlite3.Connection,
    concept_id: str,
    *,
    now: float,
    exclude: Collection[int] = (),
) -> Item | None:
    """Задание для повторения занятия или ``None``, если подходящих нет.

    Берутся задания, главный концепт которых — это занятие, кроме итогового,
    ``exclude`` и решённых за ``RECENT_ITEM_SECONDS``. Сначала ни разу не
    отвеченные (по id), затем те, на которые отвечали раньше всех.
    """
    answered = repos.last_answer_ts(conn)
    candidates = [
        item
        for item in diagnostic._available_items(conn, include_rubric=True, include_runnable=True)
        if main_concept(item) == concept_id
        and not item.final
        and item.id not in exclude
        and (item.id not in answered or now - answered[item.id] >= RECENT_ITEM_SECONDS)
    ]
    candidates.sort(key=lambda item: (item.id in answered, answered.get(item.id, 0.0), item.id))
    return candidates[0] if candidates else None


def closed_lessons(graph: CourseGraph, route: Route | None) -> list[str]:
    """Закрытые занятия маршрута, которые есть в графе (порядок маршрута)."""
    if route is None:
        return []
    return [
        step.concept_id
        for step in route.steps
        if step.status == "closed" and graph.has_node(step.concept_id)
    ]


def due_lessons(
    conn: sqlite3.Connection,
    graph: CourseGraph,
    route: Route | None,
    *,
    now: float,
    settings: Settings,
) -> list[str]:
    """Занятия, готовые к повторению сейчас.

    Занятие закрыто, есть в графе (модуль не снят с публикации), срок в
    расписании наступил и есть чем спросить (``pick_item``). Сначала самые
    просроченные, при равной просрочке — слабее владение.
    """
    rows = repos.get_review_rows(conn)
    due = [
        (cid, rows[cid].due_at)
        for cid in closed_lessons(graph, route)
        if cid in rows and rows[cid].due_at <= now and pick_item(conn, cid, now=now) is not None
    ]
    due.sort(
        key=lambda pair: (
            pair[1],  # меньший срок — большая просрочка
            beta.estimate(conn, pair[0], now=now, settings=settings).mean,
        )
    )
    return [cid for cid, _ in due]
