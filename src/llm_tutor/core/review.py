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
from llm_tutor.core import guided, intents, turn, verify
from llm_tutor.core.context import Focus
from llm_tutor.core.turn import TurnReply, post_turn
from llm_tutor.course.graph import CourseGraph
from llm_tutor.course.seed import main_concept
from llm_tutor.db import repos
from llm_tutor.grader import autocheck, rubric
from llm_tutor.llm.client import LLMClient
from llm_tutor.schemas import GradeResult, ReviewState, SessionState
from llm_tutor.student import diagnostic, review
from llm_tutor.student import route as route_mod

logger = logging.getLogger(__name__)

NOTHING_TO_REVIEW_REPLY = "Повторять пока нечего — пройденное ещё свежо."
REVIEW_DONE_TEMPLATE = "Повторение закончено: верно {correct} из {answered}."
REVIEW_DONE_EMPTY = "Повторение закончено."
REVIEW_MISTAKE_NOTE = "Освежить: раздел «{section}» занятия «{name}»."
REVIEW_MISTAKE_NOTE_LESSON = "Освежить: занятие «{name}»."
REVIEW_SKIP_NOTE = "Пропускаю — это занятие вернётся завтра."
WARMUP_SKIPPED_NOTE = "Разминку пропускаем — эти занятия вернутся завтра."
STOP_WARMUP_TEXT = "Пропустить разминку."
STOP_SERIES_TEXT = "Закончить повторение."

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


async def _begin_lesson(
    conn: sqlite3.Connection,
    client: LLMClient,
    model: str,
    graph: CourseGraph,
    session_id: int,
    state: SessionState,
    *,
    now: float,
    settings: Settings,
) -> tuple[str, str | None, list[str] | None, tuple[str, str] | None, SessionState]:
    """Начинает занятие, которое ждало конца разминки (срез 81).

    Заявленное в анкете (режим ``verify``) начинается заданием прохода, обычное —
    уроком частями. Возвращает текст, задание с вариантами, кнопку урока и
    состояние с обновлённым маршрутом.
    """
    if state.mode == "verify":
        new_state, task_text, task_options = turn._issue_task(
            conn, graph, state, now=now, settings=settings
        )
        issued = new_state.pending_item_id is not None
        # Задание — отдельным сообщением (tail); «заданий нет» остаётся текстом.
        text, tail, options, button = (
            ("", task_text, task_options, None) if issued else (task_text, None, None, None)
        )
    else:
        text, new_state = await turn._start_lesson(
            conn, client, model, session_id, state, graph, now=now, settings=settings
        )
        tail, options, button = None, None, guided.button(new_state)
    fresh_route, route_note = route_mod.refresh(conn, new_state, graph, now=now, settings=settings)
    if route_note:
        text = f"{text}\n\n{route_note}" if text else route_note
    return text, tail, options, button, new_state.model_copy(update={"route": fresh_route})


async def _advance(
    conn: sqlite3.Connection,
    client: LLMClient,
    model: str,
    graph: CourseGraph,
    session_id: int,
    state: SessionState,
    *,
    user_text: str,
    lead: str,
    node_id: str | None,
    now: float,
    settings: Settings,
    events: list | None = None,
    mastery: list | None = None,
    schedule: Sequence[review.ScheduleChange] = (),
    issued: tuple[SessionState, str | None, list[str] | None] | None = None,
) -> TurnReply:
    """Конец хода серии: следующее задание или итог — и запись хода одним коммитом.

    ``lead`` — то, что идёт перед заданием (разбор ответа, пояснение); ``node_id`` —
    занятие, о котором ход: им помечаются реплики журнала. ``issued`` — уже
    выданное ``turn.issue_review_item`` (начало серии проверяет, есть ли что
    спросить, до записи). Кончилась разминка — начинается занятие, которое она
    откладывала (``_begin_lesson``).
    """
    new_state, tail, options = issued or turn.issue_review_item(conn, graph, state, now=now)
    text = lead
    button = None
    if tail is None:
        series = new_state.review
        summary = done_text(series) if series is not None else REVIEW_DONE_EMPTY
        text = f"{lead}\n\n{summary}" if lead else summary
        new_state = new_state.model_copy(update={"review": None})
        if series is not None and series.kind == "warmup" and new_state.current_node_id:
            more, tail, options, button, new_state = await _begin_lesson(
                conn, client, model, graph, session_id, new_state, now=now, settings=settings
            )
            text = f"{text}\n\n{more}" if more else text
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
    shown = (
        new_state.review.item_id
        if new_state.review is not None and new_state.review.item_id is not None
        else new_state.pending_item_id
    )
    return TurnReply(
        text=text, options=options, tail=tail, item_id=shown if tail else None, button=button
    )


async def start(
    conn: sqlite3.Connection,
    client: LLMClient,
    model: str,
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
    return await _advance(
        conn, client, model, graph, session_id, new_state, user_text=START_TEXTS[kind],
        lead="", node_id=new_state.review.node_id, now=stamp, settings=s, issued=issued,
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
        return await _advance(
            conn, client, model, graph, session_id, cleared, user_text=text,
            lead=turn.STALE_ITEM_REPLY, node_id=node_id, now=stamp, settings=s,
        )
    try:
        result = await turn.grade_item(conn, client, model, item, text, grade=grade, settings=s)
    except (rubric.RubricError, autocheck.AutoCheckError):
        logger.warning("Задание %s не проверяется, снимаю", item.id)
        return await _advance(
            conn, client, model, graph, session_id, cleared, user_text=text,
            lead=turn.GRADING_FAILED_REPLY, node_id=node_id, now=stamp, settings=s,
        )
    if result is None:
        return await _advance(
            conn, client, model, graph, session_id, cleared, user_text=text,
            lead=turn.STALE_ITEM_REPLY, node_id=node_id, now=stamp, settings=s,
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
    return await _advance(
        conn, client, model, graph, session_id, updated, user_text=text, lead=lead,
        node_id=node_id, now=stamp, settings=s, events=events, mastery=mastery,
        schedule=[change] if change else (),
    )


def _mistake_note(graph: CourseGraph, node_id: str, section_id: str | None) -> str:
    """Что перечитать после ошибки: раздел конспекта занятия (ссылки нет)."""
    if not graph.has_node(node_id):  # занятие ушло из графа, пока висело задание
        return REVIEW_MISTAKE_NOTE_LESSON.format(name=node_id)
    name = graph.concept(node_id).name
    if section_id is None:
        return REVIEW_MISTAKE_NOTE_LESSON.format(name=name)
    return REVIEW_MISTAKE_NOTE.format(
        section=turn._section_title(graph, node_id, section_id), name=name
    )


async def skip(
    conn: sqlite3.Connection,
    client: LLMClient,
    model: str,
    *,
    now: float | None = None,
    settings: Settings | None = None,
) -> TurnReply:
    """Пропускает задание повторения: без свидетельств, занятие откладывается на сутки.

    ``client`` нужен, потому что пропуск последнего задания разминки начинает
    занятие (урок зовёт модель).
    """
    s = settings or get_settings()
    stamp = time.time() if now is None else now
    session_id, state = _session(conn, stamp)
    series, node_id = _active_item(conn, state)
    graph = CourseGraph.load(conn)
    cleared = state.model_copy(
        update={"review": series.model_copy(update={"item_id": None, "node_id": None})}
    )
    postponed = review.plan_postpone(repos.get_review_rows(conn), [node_id], now=stamp)
    return await _advance(
        conn, client, model, graph, session_id, cleared, user_text=turn.SKIP_KICKOFF_TEXT,
        lead=REVIEW_SKIP_NOTE, node_id=node_id, now=stamp, settings=s, schedule=postponed,
    )


async def stop(
    conn: sqlite3.Connection,
    client: LLMClient,
    model: str,
    *,
    now: float | None = None,
    settings: Settings | None = None,
) -> TurnReply:
    """«Закончить» / «Пропустить разминку».

    Разминка: оставшиеся занятия (и висящее) откладываются на сутки, начинается
    занятие, которое она задерживала. «Срочное» и «Освежить»: оставшееся остаётся
    в очереди без отсрочки, ответом идёт итог.
    """
    s = settings or get_settings()
    stamp = time.time() if now is None else now
    session_id, state = _session(conn, stamp)
    series = state.review
    if series is None:
        raise ValueError("серия повторения не идёт")
    graph = CourseGraph.load(conn)
    cleared = state.model_copy(update={"review": None, "last_activity": stamp})
    if series.kind != "warmup":
        text = done_text(series)
        post_turn(
            conn, session_id, user_text=STOP_SERIES_TEXT, assistant_text=text, state=cleared,
            review_node_id=series.node_id, now=stamp,
        )
        return TurnReply(text=text)

    waiting = [*([series.node_id] if series.node_id and series.item_id else []), *series.queue]
    postponed = review.plan_postpone(repos.get_review_rows(conn), waiting, now=stamp)
    text, tail, options, button = WARMUP_SKIPPED_NOTE, None, None, None
    if cleared.current_node_id:
        more, tail, options, button, cleared = await _begin_lesson(
            conn, client, model, graph, session_id, cleared, now=stamp, settings=s
        )
        text = f"{text}\n\n{more}" if more else text
    post_turn(
        conn, session_id, user_text=STOP_WARMUP_TEXT,
        assistant_text=f"{text}\n\n{tail}" if tail else text, task_text=tail,
        state=cleared, schedule=postponed, review_node_id=series.node_id, now=stamp,
    )
    item_id = cleared.pending_item_id if tail else None
    return TurnReply(text=text, options=options, tail=tail, item_id=item_id, button=button)


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


def _view(state: SessionState, series: ReviewState, item_section: str | None) -> SessionState:
    """Состояние глазами тьютора: занятие, раздел и задание повторения.

    Строится только для запроса модели и в БД не пишется: поля урока остаются
    такими, какими были. Пройденные разделы и режим урока в виде не нужны —
    они относятся к другому занятию.
    """
    return state.model_copy(
        update={
            "current_node_id": series.node_id,
            "section_id": item_section,
            "passed_sections": [],
            "weak_sections": [],
            "skipped_sections": [],
            "mode": None,
            "hint_level": 0,
            "pending_item_id": series.item_id,
            "task_hinted": series.hinted,
            "phase": "practice",
        }
    )


async def chat(
    conn: sqlite3.Connection,
    client: LLMClient,
    model: str,
    text: str,
    *,
    stuck: bool = False,
    focus: Focus | None = None,
    now: float | None = None,
    settings: Settings | None = None,
) -> TurnReply:
    """Реплика ученика тьютору посреди серии: вопрос по заданию повторения.

    Контекст — по занятию и разделу задания повторения. Вопрос (и «Застрял»)
    ставит ``hinted``: ответ после помощи двигает лестницу слабее. Короткие
    команды: «пропусти» пропускает задание, «закрой тему» снимает серию и
    ведёт в проверочный проход по текущему занятию. ``focus`` — выделенный
    фрагмент: команды разбираются только по ``text``.
    """
    s = settings or get_settings()
    stamp = time.time() if now is None else now
    session_id, state = _session(conn, stamp)
    series, node_id = _active_item(conn, state)

    intent = None if stuck else intents.detect(text)
    if intent == "skip":
        return await skip(conn, client, model, now=stamp, settings=s)
    if intent == "close_topic":
        abort(conn, now=stamp)
        return verify.start_verification(
            conn, now=stamp, settings=s, user_text=text, focus=focus
        )
    stuck = stuck or intent == "stuck"

    graph = CourseGraph.load(conn)
    item = repos.get_item(conn, series.item_id)  # type: ignore[arg-type]
    view = _view(state, series, item.section if item is not None else None)
    reply, _, _, new_view, wants_close = await turn._tutor_branch(
        conn, client, model, session_id, text, view, graph,
        now=stamp, settings=s, force_stuck=stuck, allow_stuck=False, focus=focus,
    )
    if wants_close:
        abort(conn, now=stamp)
        return verify.start_verification(
            conn, now=stamp, settings=s, user_text=text, tutor_reply=reply, focus=focus
        )
    # «Застрял» в уроке уводит узел в усиленный проход; в повторении это лишь
    # просьба о помощи, и фраза про «остаёмся на этом узле» ученику не нужна.
    reply = reply.removesuffix(f"\n\n{turn.STUCK_NOTE}")
    reply = f"{reply}\n\n{turn.PENDING_ITEM_NOTE}"
    new_series = series.model_copy(update={"hinted": series.hinted or new_view.task_hinted})
    post_turn(
        conn,
        session_id,
        user_text=text,
        assistant_text=reply,
        state=state.model_copy(update={"review": new_series, "last_activity": stamp}),
        review_node_id=node_id,
        focus=focus,
        now=stamp,
    )
    return TurnReply(text=reply)
