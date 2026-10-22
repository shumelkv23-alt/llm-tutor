"""Серия повторения пройденного: разминка, «Срочное», «Освежить» (срез 80).

Ходы серии: выдать задание, принять ответ, пропустить. Расписание повторений
считает ``student/review``, а здесь оно только применяется: изменения строк
ложатся вместе с событиями и состоянием одним ``post_turn``.

Главное правило: ответ засчитывается **занятию задания** (``main_concept``), а не
текущему занятию ученика — разминка перед занятием 7 зачитывает занятию 3.
Поля урока (``pending_item_id``, ``section_id``, ``lesson_parts``…) серия не читает
и не пишет: после неё занятие продолжается ровно с того места.

``core/review`` импортирует ``core/turn``, обратного импорта нет (как у
``guided``/``lesson``): разминку в ``handle_turn`` собирает сам ``turn``.
"""

import logging
import sqlite3
import time
from collections.abc import Sequence
from typing import Literal

from llm_tutor.config import Settings, get_settings
from llm_tutor.core import turn
from llm_tutor.core.turn import TurnReply, post_turn
from llm_tutor.course.graph import CourseGraph
from llm_tutor.course.seed import main_concept
from llm_tutor.db import repos
from llm_tutor.grader import autocheck, rubric
from llm_tutor.llm.client import LLMClient
from llm_tutor.schemas import GradeResult, ReviewState, SessionState
from llm_tutor.student import diagnostic, review

logger = logging.getLogger(__name__)

NOTHING_TO_REVIEW_REPLY = "Повторять пока нечего — пройденное ещё свежо."
REVIEW_DONE_TEMPLATE = "Повторение закончено: верно {correct} из {answered}."
REVIEW_DONE_EMPTY = "Повторение закончено."
REVIEW_MISTAKE_NOTE = "Освежить: раздел «{section}» занятия «{name}»."
REVIEW_MISTAKE_NOTE_LESSON = "Освежить: занятие «{name}»."
REVIEW_SKIP_NOTE = "Пропускаю — это занятие вернётся завтра."

# Что «говорит» ученик, начиная серию кнопкой: нужен как реплика журнала.
START_TEXTS = {
    "due": "Начнём повторение срочного.",
    "refresh": "Освежим выбранное.",
}


def _session(conn: sqlite3.Connection, now: float) -> tuple[int, SessionState]:
    session_id = repos.ensure_open_session(conn, now)
    return session_id, repos.get_session_state(conn, session_id)


def done_text(series: ReviewState) -> str:
    """Итог серии для ученика."""
    if series.answered == 0:
        return REVIEW_DONE_EMPTY
    return REVIEW_DONE_TEMPLATE.format(correct=series.correct, answered=series.answered)


def _advance(
    conn: sqlite3.Connection,
    graph: CourseGraph,
    session_id: int,
    state: SessionState,
    *,
    user_text: str,
    lead: str,
    node_id: str,
    now: float,
    events: list | None = None,
    mastery: list | None = None,
    schedule: Sequence[review.ScheduleChange] = (),
    issued: tuple[SessionState, str | None, list[str] | None] | None = None,
) -> TurnReply:
    """Конец хода серии: следующее задание или итог — и запись хода одним коммитом.

    ``lead`` — то, что идёт перед заданием (разбор ответа, пояснение); ``node_id`` —
    занятие, о котором ход: им помечаются реплики журнала. ``issued`` — уже
    выданное ``turn.issue_review_item`` (начало серии проверяет, есть ли что
    спросить, до записи).
    """
    new_state, tail, options = issued or turn.issue_review_item(conn, graph, state, now=now)
    text = lead
    if tail is None:
        series = new_state.review
        summary = done_text(series) if series is not None else REVIEW_DONE_EMPTY
        text = f"{lead}\n\n{summary}" if lead else summary
        new_state = new_state.model_copy(update={"review": None})
    assistant_text = f"{text}\n\n{tail}" if tail and text else (tail or text)
    post_turn(
        conn,
        session_id,
        user_text=user_text,
        assistant_text=assistant_text,
        task_text=tail,
        state=new_state,
        events=events,
        mastery=mastery,
        schedule=schedule,
        review_node_id=node_id,
        now=now,
    )
    item_id = new_state.review.item_id if tail and new_state.review is not None else None
    return TurnReply(text=text, options=options, tail=tail, item_id=item_id)


def start(
    conn: sqlite3.Connection,
    *,
    kind: Literal["due", "refresh"],
    node_ids: Sequence[str] = (),
    now: float | None = None,
    settings: Settings | None = None,
) -> TurnReply:
    """Начинает серию: «Срочное» (до ``SERIES_SIZE`` занятий) или «Освежить».

    ``refresh`` берёт занятия из ``node_ids`` в их порядке; незакрытое или чужое
    занятие — ``ValueError``. Серия уже идёт — тоже ``ValueError``. Спрашивать
    нечего — ответ ``NOTHING_TO_REVIEW_REPLY`` без записи в журнал.
    """
    s = settings or get_settings()
    stamp = time.time() if now is None else now
    session_id, state = _session(conn, stamp)
    if state.review is not None:
        raise ValueError("серия повторения уже идёт")
    graph = CourseGraph.load(conn)
    if kind == "due":
        queue = review.due_lessons(conn, graph, state.route, now=stamp, settings=s)[
            : review.SERIES_SIZE
        ]
    else:
        wanted = list(dict.fromkeys(node_ids))
        closed = set(review.closed_lessons(graph, state.route))
        unknown = [node_id for node_id in wanted if node_id not in closed]
        if unknown:
            raise ValueError(f"занятия нельзя повторять: {', '.join(unknown)}")
        queue = wanted
    series = ReviewState(kind=kind, queue=queue, total=len(queue))
    issued = turn.issue_review_item(
        conn, graph, state.model_copy(update={"review": series}), now=stamp
    )
    new_state, tail, _ = issued
    if tail is None or new_state.review is None or new_state.review.node_id is None:
        return TurnReply(text=NOTHING_TO_REVIEW_REPLY)
    return _advance(
        conn, graph, session_id, new_state, user_text=START_TEXTS[kind], lead="",
        node_id=new_state.review.node_id, now=stamp, issued=issued,
    )


def _active_item(
    conn: sqlite3.Connection, state: SessionState
) -> tuple[ReviewState, str]:
    """Серия и занятие висящего задания; без них ход повторения невозможен."""
    series = state.review
    if series is None or series.item_id is None or series.node_id is None:
        raise ValueError("нет висящего задания повторения")
    return series, series.node_id


async def answer(
    conn: sqlite3.Connection,
    client: LLMClient,
    model: str,
    text: str,
    *,
    grade: GradeResult | None = None,
    now: float | None = None,
    settings: Settings | None = None,
) -> TurnReply:
    """Ответ на задание повторения.

    ``grade`` — готовый вердикт кода (тесты в браузере). Снятое с банка или
    непроверяемое задание уходит без свидетельств и расписания, ученику выдаётся
    следующее из очереди. Верный/неверный ответ даёт события занятию задания,
    а лестница двигается по ``review.plan_answer``.
    """
    s = settings or get_settings()
    stamp = time.time() if now is None else now
    session_id, state = _session(conn, stamp)
    series, node_id = _active_item(conn, state)
    graph = CourseGraph.load(conn)
    item = repos.get_item(conn, series.item_id)  # type: ignore[arg-type]
    cleared = state.model_copy(
        update={"review": series.model_copy(update={"item_id": None, "node_id": None})}
    )

    if item is None or not item.active:
        return _advance(
            conn, graph, session_id, cleared, user_text=text, lead=turn.STALE_ITEM_REPLY,
            node_id=node_id, now=stamp,
        )
    try:
        result = await turn.grade_item(conn, client, model, item, text, grade=grade, settings=s)
    except (rubric.RubricError, autocheck.AutoCheckError):
        logger.warning("Задание %s не проверяется, снимаю", item.id)
        return _advance(
            conn, graph, session_id, cleared, user_text=text, lead=turn.GRADING_FAILED_REPLY,
            node_id=node_id, now=stamp,
        )
    if result is None:
        return _advance(
            conn, graph, session_id, cleared, user_text=text, lead=turn.STALE_ITEM_REPLY,
            node_id=node_id, now=stamp,
        )

    # Засчитывается занятию задания, а не текущему занятию ученика.
    concept = main_concept(item) or node_id
    events, mastery = turn.item_evidence(
        conn, graph, item, concept, result, from_tests=grade is not None, now=stamp, settings=s
    )
    passed = result.score >= diagnostic.SUCCESS_SCORE
    row = repos.get_review_rows(conn).get(concept)
    change = review.plan_answer(
        row,
        concept,
        passed=passed,
        hinted=series.hinted,
        due=row is not None and row.due_at <= stamp,
        now=stamp,
    )
    lead = turn._feedback(item, result.score)
    if not passed:
        lead = f"{lead}\n\n{_mistake_note(graph, concept, item.section)}"
    updated = cleared.model_copy(
        update={
            "review": cleared.review.model_copy(  # type: ignore[union-attr]
                update={
                    "answered": series.answered + 1,
                    "correct": series.correct + (1 if passed else 0),
                }
            )
        }
    )
    return _advance(
        conn, graph, session_id, updated, user_text=text, lead=lead, node_id=node_id,
        now=stamp, events=events, mastery=mastery, schedule=[change] if change else (),
    )


def _mistake_note(graph: CourseGraph, node_id: str, section_id: str | None) -> str:
    """Что перечитать после ошибки: раздел конспекта занятия (ссылки нет)."""
    name = graph.concept(node_id).name
    if section_id is None:
        return REVIEW_MISTAKE_NOTE_LESSON.format(name=name)
    return REVIEW_MISTAKE_NOTE.format(
        section=turn._section_title(graph, node_id, section_id), name=name
    )


def skip(
    conn: sqlite3.Connection, *, now: float | None = None, settings: Settings | None = None
) -> TurnReply:
    """Пропускает задание повторения: без свидетельств, занятие откладывается на сутки."""
    stamp = time.time() if now is None else now
    session_id, state = _session(conn, stamp)
    series, node_id = _active_item(conn, state)
    graph = CourseGraph.load(conn)
    cleared = state.model_copy(
        update={"review": series.model_copy(update={"item_id": None, "node_id": None})}
    )
    postponed = review.plan_postpone(repos.get_review_rows(conn), [node_id], now=stamp)
    return _advance(
        conn, graph, session_id, cleared, user_text=turn.SKIP_KICKOFF_TEXT,
        lead=REVIEW_SKIP_NOTE, node_id=node_id, now=stamp, schedule=postponed,
    )


def abort(conn: sqlite3.Connection, *, now: float | None = None) -> None:
    """Снимает серию без реплики (одним коммитом состояния); расписание не трогает."""
    stamp = time.time() if now is None else now
    session_id, state = _session(conn, stamp)
    if state.review is None:
        return
    repos.update_session_state(
        conn,
        session_id,
        state.model_copy(update={"review": None, "last_activity": stamp}),
    )
