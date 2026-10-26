"""Перенос прогресса учеников со старых тем на занятия (срез 56, план §6).

Курс перестроен: 103 темы стали 33 занятиями с разделами. Прогресс ученика не
обнуляется, а переносится по таблице ``legacy`` из seed (старая тема →
занятие и раздел):

* владение занятия — среднее свидетельств его старых тем: ``α = 1 + Σ(αᵢ−1)/n``
  (так же β), n — все старые темы занятия, включая нетронутые. Знал ``groupby``,
  но не видел ``pivot_table`` — занятие даром не получаешь;
* закрытая тема → пройденный раздел (слабая → слабый). Занятие закрыто, если
  пройдены все разделы со старыми темами, а разделы «нов.» прикрыты уверенным
  владением. Пройденное незакрытого занятия ждёт в факте
  ``legacy_passed_sections`` и подхватывается при входе в занятие;
* текущая тема → её занятие и раздел, убранная тема → ``None``; висящее
  задание остаётся, если оно ещё в банке; проверочный проход снимается;
* журнал (``events``, ``messages``) не трогается — источник истины.

Перенос — один раз: отметка — факт ``course_layout = "2"``. Его зовут
``web/userdb`` после загрузки курса (так же открывается и БД, перенесённая
``import-legacy``).
"""

import json
import logging
import sqlite3
import time
from collections.abc import Mapping

from llm_tutor.config import Settings
from llm_tutor.course.graph import CourseGraph
from llm_tutor.course.seed import LegacyTarget
from llm_tutor.db import repos
from llm_tutor.schemas import Concept, Route, RouteStep, SessionState
from llm_tutor.student import beta, guide
from llm_tutor.student import route as route_mod
from llm_tutor.student.planner import CONFIDENT_UNCERTAINTY

logger = logging.getLogger(__name__)

LAYOUT_FACT = "course_layout"
LAYOUT_VERSION = "2"
# Пройденные до перестройки разделы незакрытых занятий: {занятие: {passed, weak}}.
PASSED_FACT = "legacy_passed_sections"


def _old_by_lesson(legacy: Mapping[str, LegacyTarget | None]) -> dict[str, list[str]]:
    by_lesson: dict[str, list[str]] = {}
    for old, target in legacy.items():
        if target is not None:
            by_lesson.setdefault(target.node, []).append(old)
    return by_lesson


def _remap_mastery(
    conn: sqlite3.Connection, by_lesson: Mapping[str, list[str]]
) -> None:
    """Владение занятия — среднее свидетельств его старых тем (не сумма)."""
    for lesson, olds in by_lesson.items():
        rows = [repos.get_mastery(conn, old) for old in olds]
        touched = [row for row in rows if row is not None]
        if not touched or repos.get_mastery(conn, lesson) is not None:
            continue
        n = len(olds)
        alpha = 1.0 + sum(float(row["alpha"]) - 1.0 for row in touched) / n
        beta_ = 1.0 + sum(float(row["beta"]) - 1.0 for row in touched) / n
        seen = [row["last_seen"] for row in touched if row["last_seen"] is not None]
        reviews = [row["next_review"] for row in touched if row["next_review"] is not None]
        repos.upsert_mastery(
            conn,
            lesson,
            alpha=alpha,
            beta=beta_,
            last_seen=max(seen) if seen else None,
            next_review=min(reviews) if reviews else None,
            commit=False,
        )


def _confident(conn: sqlite3.Connection, lesson: str, *, settings: Settings) -> bool:
    """Уверенное владение по сырым счётчикам (без decay: перенос — момент, а не прогноз)."""
    row = repos.get_mastery(conn, lesson)
    if row is None:
        return False
    estimate = beta.to_mastery(lesson, float(row["alpha"]), float(row["beta"]), None, None)
    return (
        estimate.mean >= settings.mastery_skip_threshold
        and estimate.uncertainty <= CONFIDENT_UNCERTAINTY
    )


def _has_old_progress(conn: sqlite3.Connection, legacy: Mapping[str, LegacyTarget | None]) -> bool:
    """В БД есть следы старых тем: владение по ним или состояние сессии с ними."""
    old = set(legacy)
    marks = ",".join("?" for _ in old)
    if conn.execute(
        f"SELECT 1 FROM mastery WHERE concept_id IN ({marks}) LIMIT 1", tuple(old)
    ).fetchone():
        return True
    session_id = repos.get_open_session(conn)
    if session_id is None:
        return False
    state = repos.get_session_state(conn, session_id)
    ids = {state.current_node_id, state.jump_node_id}
    if state.route is not None:
        ids |= {step.concept_id for step in state.route.steps}
    return bool(ids & old)


def remap_progress(
    conn: sqlite3.Connection,
    legacy: Mapping[str, LegacyTarget | None],
    *,
    settings: Settings,
    now: float | None = None,
) -> bool:
    """Переносит прогресс со старых тем на занятия (один раз). ``True`` — перенёс."""
    if not legacy or repos.get_fact(conn, LAYOUT_FACT) == LAYOUT_VERSION:
        return False
    if not _has_old_progress(conn, legacy):
        # Новая БД (или БД без прогресса): переносить нечего, отметка не нужна —
        # старых тем в ней не появится.
        return False
    stamp = time.time() if now is None else now
    lessons = {c.id: c for c in repos.get_concepts(conn) if c.sections}
    by_lesson = {
        lesson: olds for lesson, olds in _old_by_lesson(legacy).items() if lesson in lessons
    }
    try:
        _remap_mastery(conn, by_lesson)
        session_id = repos.get_open_session(conn)
        if session_id is not None:
            state = repos.get_session_state(conn, session_id)
            new_state, passed = _remap_state(
                conn, state, legacy, lessons, by_lesson, now=stamp, settings=settings
            )
            repos.update_session_state(conn, session_id, new_state, commit=False)
            if passed:
                conn.execute(
                    "INSERT INTO facts (key, value, source) VALUES (?, ?, 'system') "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (PASSED_FACT, json.dumps(passed, ensure_ascii=False)),
                )
        conn.execute(
            "INSERT INTO facts (key, value, source) VALUES (?, ?, 'system') "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (LAYOUT_FACT, LAYOUT_VERSION),
        )
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    logger.info("Прогресс перенесён на занятия (%d занятий со старыми темами)", len(by_lesson))
    return True


def _remap_state(
    conn: sqlite3.Connection,
    state: SessionState,
    legacy: Mapping[str, LegacyTarget | None],
    lessons: Mapping[str, Concept],
    by_lesson: Mapping[str, list[str]],
    *,
    now: float,
    settings: Settings,
) -> tuple[SessionState, dict[str, dict[str, list[str]]]]:
    """Состояние сессии на занятиях и пройденные разделы незакрытых занятий.

    Занятия неопубликованных модулей в граф ученика не входят: снимок маршрута
    их не удержит, поэтому их пройденное (даже целиком) уходит в факт и
    подхватится, когда модуль откроют. Текущим может быть только видимое
    занятие — иначе «Продолжить» звало бы урок по узлу вне графа.
    """
    old_steps = {step.concept_id: step for step in (state.route.steps if state.route else [])}
    visible = set(CourseGraph.load(conn).node_ids)

    def target(old: str | None) -> LegacyTarget | None:
        return legacy.get(old) if old is not None else None

    closed: set[str] = set()
    weak_lessons: set[str] = set()
    closed_at: dict[str, float] = {}
    passed: dict[str, dict[str, list[str]]] = {}
    claimed: set[str] = set()
    for lesson, olds in by_lesson.items():
        concept = lessons[lesson]
        steps = {old: old_steps.get(old) for old in olds}
        done = {
            legacy[old].section: step
            for old, step in steps.items()
            if step is not None and step.status == "closed"
        }
        weak = sorted(section for section, step in done.items() if step.weak)
        with_old = {legacy[old].section for old in olds}
        fresh = [s.id for s in concept.sections if s.id not in with_old]
        is_closed = bool(done) and with_old <= set(done) and (
            not fresh or _confident(conn, lesson, settings=settings)
        )
        if is_closed and lesson not in visible:
            order = [s.id for s in concept.sections]
            passed[lesson] = {"passed": [s for s in order if s in done], "weak": weak}
        elif is_closed:
            closed.add(lesson)
            closed_at[lesson] = max(
                (step.closed_at for step in done.values() if step.closed_at is not None),
                default=now,
            )
            if weak:
                weak_lessons.add(lesson)
        elif done:
            order = [s.id for s in concept.sections]
            passed[lesson] = {
                "passed": [s for s in order if s in done],
                "weak": weak,
            }
        statuses = {step.status for step in steps.values() if step is not None}
        if not is_closed and "claimed" in statuses and statuses <= {"claimed", "closed"}:
            claimed.add(lesson)

    current_target = target(state.current_node_id)
    current = current_target.node if current_target and current_target.node in visible else None
    jump = target(state.jump_node_id)
    goal_target = target(state.route.goal_concept_id if state.route else None)

    route = None
    if state.route is not None:
        previous = Route(
            goal_concept_id=goal_target.node if goal_target else None,
            steps=[
                RouteStep(
                    concept_id=lesson,
                    mode="full",
                    status=(
                        "closed"
                        if lesson in closed
                        else "current"
                        if lesson == current
                        else "claimed"
                        if lesson in claimed
                        else "ahead"
                    ),
                    closed_at=closed_at.get(lesson),
                    weak=lesson in weak_lessons,
                )
                for lesson in lessons
            ],
            topic_id=state.route.topic_id,
            completed_topics=state.route.completed_topics,
        )
        graph = CourseGraph.load(conn)
        route = route_mod.build_route(
            conn,
            graph,
            goal_concept_id=previous.goal_concept_id if previous.goal_concept_id in graph.node_ids else None,
            current_node_id=current,
            previous=previous,
            now=now,
            settings=settings,
        ).model_copy(
            update={"topic_id": state.route.topic_id, "completed_topics": state.route.completed_topics}
        )

    pending = state.pending_item_id
    if pending is not None:
        item = repos.get_item(conn, pending)
        if item is None or not item.active:
            pending = None
    progress = passed.get(current, {"passed": [], "weak": []}) if current else {"passed": [], "weak": []}
    section = current_target.section if current_target and current else None
    if section in progress["passed"]:
        section = None
    new_state = state.model_copy(
        update={
            "current_node_id": current,
            "pending_item_id": pending,
            "item_issued_at": None,
            "phase": "practice" if pending is not None else "explain",
            "mode": None,
            "verify_item_ids": [],
            "lesson_item_ids": [],
            "close_requested": False,
            "node_streak": 0,
            "check_misses": 0,
            "lesson_parts": [],
            "lesson_part": 0,
            "jump_node_id": jump.node if jump and jump.node in visible else None,
            "route": route,
            **guide.SECTION_RESET,
            "section_id": section,
            "passed_sections": progress["passed"],
            "weak_sections": progress["weak"],
        }
    )
    return new_state, passed


def legacy_passed(conn: sqlite3.Connection, lesson: str) -> tuple[list[str], list[str]]:
    """Пройденные до перестройки разделы занятия: (пройденные, слабые)."""
    raw = repos.get_fact(conn, PASSED_FACT)
    if not raw:
        return [], []
    try:
        entry = json.loads(raw).get(lesson) or {}
    except (ValueError, AttributeError):
        return [], []
    return list(entry.get("passed", [])), list(entry.get("weak", []))
