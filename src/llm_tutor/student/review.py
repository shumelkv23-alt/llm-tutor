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
from llm_tutor.schemas import Item, ReviewRow, Route, RouteStep
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

BACKFILL_FACT = "review_backfill"
ENABLED_FACT = "review_enabled"


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
    day = utc_day(now) + int(LADDER_DAYS[step])
    return _spill(rows, pending, day * _DAY + (now - utc_day(now) * _DAY))


def _spill(
    rows: Mapping[str, ReviewRow], pending: Sequence[ScheduleChange], target: float
) -> float:
    """Первое время суток ``target`` в день, где сроков меньше ``DAILY_LIMIT``."""
    load: dict[int, int] = {}
    for row in rows.values():
        day = utc_day(row.due_at)
        load[day] = load.get(day, 0) + 1
    for change in pending:
        if change.due_at is not None:
            day = utc_day(change.due_at)
            load[day] = load.get(day, 0) + 1

    day = utc_day(target)
    while load.get(day, 0) >= DAILY_LIMIT:
        day += 1
    return day * _DAY + (target - utc_day(target) * _DAY)


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
    меняет. Ошибка — на две ступени вниз (не ниже 0), завтра. Верно раньше
    срока — без изменений, с подсказкой или без: «Освежить» интервал не
    сокращает. Верно в срок с подсказкой — ступень та же, завтра; без неё —
    ступень вверх, после последней занятие уходит с лестницы.
    """
    if row is None:
        if passed:
            return None
        return ScheduleChange(concept_id, 0, now + _DAY)
    if not passed:
        return ScheduleChange(concept_id, max(0, row.step - 2), now + _DAY)
    if not due:
        return None
    if hinted:
        return ScheduleChange(concept_id, row.step, now + _DAY)
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


def _bank(conn: sqlite3.Connection) -> list[Item]:
    """Задания банка, пригодные для повторения (ещё без отбора по занятию)."""
    return [
        item
        for item in diagnostic._available_items(conn, include_rubric=True, include_runnable=True)
        if not item.final
    ]


def _pick(
    bank: Sequence[Item],
    answered: Mapping[int, float],
    concept_id: str,
    *,
    now: float,
    exclude: Collection[int],
) -> Item | None:
    """``pick_item`` по уже прочитанным банку и журналу ответов."""
    candidates = [
        item
        for item in bank
        if main_concept(item) == concept_id
        and item.id not in exclude
        and (item.id not in answered or now - answered[item.id] >= RECENT_ITEM_SECONDS)
    ]
    candidates.sort(key=lambda item: (item.id in answered, answered.get(item.id, 0.0), item.id))
    return candidates[0] if candidates else None


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
    return _pick(_bank(conn), repos.last_answer_ts(conn), concept_id, now=now, exclude=exclude)


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
    candidates = [
        cid for cid in closed_lessons(graph, route) if cid in rows and rows[cid].due_at <= now
    ]
    if not candidates:
        return []
    # Банк и журнал читаются один раз на все занятия: вызов идёт с каждым
    # состоянием занятия и с главной.
    bank, answered = _bank(conn), repos.last_answer_ts(conn)
    due = [
        (cid, rows[cid].due_at)
        for cid in candidates
        if _pick(bank, answered, cid, now=now, exclude=()) is not None
    ]
    due.sort(
        key=lambda pair: (
            pair[1],  # меньший срок — большая просрочка
            beta.estimate(conn, pair[0], now=now, settings=settings).mean,
        )
    )
    return [cid for cid, _ in due]


def _step_for_closure(step: RouteStep, verify_node_id: str | None) -> int:
    """Ступень новой строки: сразу проверка (2) для проверенного и пропущенного занятия."""
    if step.concept_id == verify_node_id or step.mode == "skip":
        return VERIFY_START_STEP
    return 0


def plan_closures(
    rows: Mapping[str, ReviewRow],
    before: Route | None,
    after: Route | None,
    *,
    verify_node_id: str | None,
    now: float,
) -> list[ScheduleChange]:
    """Строки для занятий, закрытых этим ходом: закрыто в ``after``, не в ``before``.

    Строка заводится только тем, у кого её ещё нет: повторное закрытие
    расписание не сбрасывает. Занятие, которое ученик уже знает (проверка или
    пропуск), начинает с ступени ``VERIFY_START_STEP``, остальные — с нуля.
    Сроки считаются с учётом уже набранных в этом вызове изменений.
    """
    if after is None:
        return []
    closed_before = (
        {s.concept_id for s in before.steps if s.status == "closed"} if before else set()
    )
    changes: list[ScheduleChange] = []
    for step in after.steps:
        if step.status != "closed" or step.concept_id in closed_before or step.concept_id in rows:
            continue
        ladder = _step_for_closure(step, verify_node_id)
        due = first_due(rows, step=ladder, now=now, pending=changes)
        changes.append(ScheduleChange(step.concept_id, ladder, due))
    return changes


def _backfill_step(closed_at: float | None, now: float) -> int:
    """Ступень по давности закрытия: чем старше занятие, тем выше."""
    age = 0.0 if closed_at is None else max(0.0, now - closed_at)
    for step, days in enumerate((3.0, 7.0, 14.0)):
        if age < days * _DAY:
            return step
    return 3


def backfill(conn: sqlite3.Connection, route: Route | None, *, now: float) -> None:
    """Один раз заводит строки расписания закрытым занятиям старого ученика.

    Ступень — по давности ``closed_at``, срок — не раньше завтра и не раньше
    ``closed_at`` + лестница; сроки раскладываются по дню с лимитом. Шаги с id,
    которого нет среди концептов курса, пропускаются. Отметка —
    факт ``review_backfill``; без маршрута строк нет, отметка всё равно ставится.
    Всё одним коммитом.
    """
    if repos.get_fact(conn, BACKFILL_FACT) is not None:
        return
    rows = repos.get_review_rows(conn)
    known = repos.get_concept_ids(conn)
    changes: list[ScheduleChange] = []
    if route is not None:
        for step in route.steps:
            # Шаг старого снимка с id вне курса: на концепт ссылается внешний
            # ключ, и строка уронила бы открытие БД ученика насовсем.
            if step.status != "closed" or step.concept_id in rows or step.concept_id not in known:
                continue
            ladder = _backfill_step(step.closed_at, now)
            closed_at = now if step.closed_at is None else step.closed_at
            target = max(closed_at + LADDER_DAYS[ladder] * _DAY, now + _DAY)
            changes.append(ScheduleChange(step.concept_id, ladder, _spill(rows, changes, target)))
    try:
        write_changes(conn, changes, commit=False)
        repos.set_fact(conn, BACKFILL_FACT, "1", source="system", commit=False)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def enabled(conn: sqlite3.Connection) -> bool:
    """Включено ли повторение (разминка и вкладка); по умолчанию — да."""
    return repos.get_fact(conn, ENABLED_FACT) != "0"


def set_enabled(conn: sqlite3.Connection, flag: bool, *, commit: bool = True) -> None:
    """Включает или выключает повторение; строки расписания при этом не трогаются."""
    repos.set_fact(conn, ENABLED_FACT, "1" if flag else "0", source="self", commit=commit)
