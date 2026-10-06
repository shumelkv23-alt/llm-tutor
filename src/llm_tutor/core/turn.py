"""Обработчик хода: ответ тьютора или разбор ответа на задание (Срез 5.5).

Один ход = один коммит: реплики, события, модель ученика и состояние сессии
ложатся вместе — сбой посередине не оставит «сиротскую» реплику или событие
без обновления владения.

Разветвление: если в состоянии сессии стоит ``pending_item_id``, текст ученика
— это ответ на задание (проверяет код, без LLM); иначе идёт тьюторский путь с
лестницей подсказок.
"""

import logging
import sqlite3
import time

from llm_tutor.config import Settings, get_settings
from llm_tutor.core.context import build_context
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.grader import autocheck
from llm_tutor.llm.client import LLMClient, LLMError
from llm_tutor.llm.prompts import (
    EMPTY_GRAPH_REPLY,
    LLM_FAILURE_REPLY,
    NO_COURSE_ANSWER,
    NO_TASK_REPLY,
)
from llm_tutor.llm.schemas import TutorReply
from llm_tutor.rag.retriever import has_searchable_content
from llm_tutor.schemas import Event, Item, SessionState
from llm_tutor.student import beta, diagnostic, hints

logger = logging.getLogger(__name__)

# Задание уже неактуально (пропало из банка или не проверяется кодом).
STALE_ITEM_REPLY = "Это задание уже неактуально — жми /task, чтобы взять новое."
# Реплика «дай задание» попадает в журнал как обычная просьба ученика.
PRACTICE_KICKOFF_TEXT = "Давай задание по текущей теме."


def _render_item(item: Item) -> str:
    """Задание как текст сообщения (варианты — нумерованным списком)."""
    if not item.options:
        return item.prompt
    options = "\n".join(
        f"{index + 1}. {option}" for index, option in enumerate(item.options)
    )
    return f"{item.prompt}\n\n{options}"


def _feedback(item: Item, score: float) -> str:
    """Разбор ответа: вердикт и — при ошибке — верный вариант."""
    if score >= diagnostic.SUCCESS_SCORE:
        return "Верно ✓"
    if item.answer_type == "choice":
        return f"Не совсем ✗ — верный вариант: {item.options[int(item.answer or 0)]}"
    return f"Не совсем ✗ — ожидался ответ: {item.answer}"


def post_turn(
    conn: sqlite3.Connection,
    session_id: int,
    *,
    user_text: str,
    assistant_text: str,
    state: SessionState,
    events: list[Event] | None = None,
    mastery: list[beta.MasteryUpdate] | None = None,
    now: float | None = None,
) -> None:
    """Пишет ход одним коммитом; при сбое откатывает всё."""
    stamp = time.time() if now is None else now
    try:
        repos.add_message(conn, session_id, "user", user_text, ts=stamp, commit=False)
        repos.add_message(
            conn, session_id, "assistant", assistant_text, ts=stamp, commit=False
        )
        for event in events or []:
            repos.add_event(conn, event, stamp, commit=False)
        for change in mastery or []:
            beta.write_update(conn, change, commit=False)
        repos.update_session_state(conn, session_id, state, commit=False)
    except Exception:
        conn.rollback()
        raise
    conn.commit()


def _answer_branch(
    conn: sqlite3.Connection,
    graph: CourseGraph,
    user_text: str,
    state: SessionState,
    *,
    now: float,
    settings: Settings,
) -> tuple[str, list[Event], list[beta.MasteryUpdate], SessionState]:
    """Ученик отвечает на выданное задание: проверяет код, без LLM."""
    item = repos.get_item(conn, state.pending_item_id or -1)
    if item is None or item.answer_type not in diagnostic.AUTO_CHECKABLE:
        return (
            STALE_ITEM_REPLY,
            [],
            [],
            state.model_copy(update={"pending_item_id": None}),
        )

    result = autocheck.check(item, user_text)
    measured = state.current_node_id or next(iter(item.concept_weights), "")
    events, mastery = diagnostic.plan_evidence(
        conn, graph, item, measured, result.score, now=now, settings=settings
    )
    new_state = state.model_copy(
        update={
            "pending_item_id": None,
            "attempts": state.attempts + 1,
            "last_activity": now,
        }
    )
    return _feedback(item, result.score), events, mastery, new_state


async def _tutor_branch(
    conn: sqlite3.Connection,
    client: LLMClient,
    model: str,
    session_id: int,
    user_text: str,
    state: SessionState,
    graph: CourseGraph,
    *,
    now: float,
    settings: Settings,
) -> tuple[str, list[Event], list[beta.MasteryUpdate], SessionState]:
    """Тьюторский путь: контекст → модель → новый уровень подсказки."""
    package = build_context(
        conn,
        session_id,
        user_text,
        state=state,
        graph=graph,
        top_k=settings.context_rag_top_k,
        dialog_tail=settings.context_dialog_tail,
        budget_tokens=settings.context_budget_tokens,
        now=now,
        settings=settings,
    )
    idle_state = state.model_copy(update={"last_activity": now})

    if not package.chunks and has_searchable_content(user_text):
        # Осмысленный вопрос вне материала — честный отказ без вызова LLM.
        return NO_COURSE_ANSWER, [], [], idle_state

    try:
        answer = await client.chat_structured(package.messages, TutorReply, model=model)
    except LLMError:
        return LLM_FAILURE_REPLY, [], [], idle_state
    except Exception:  # noqa: BLE001 — бот не должен молчать на неожиданный сбой
        logger.exception("Неожиданный сбой тьюторского хода")
        return LLM_FAILURE_REPLY, [], [], idle_state

    level = hints.next_hint_level(state.hint_level, answer.hint_level)
    return (
        answer.reply,
        [],
        [],
        state.model_copy(update={"hint_level": level, "last_activity": now}),
    )


async def handle_turn(
    conn: sqlite3.Connection,
    client: LLMClient,
    model: str,
    user_text: str,
    *,
    now: float | None = None,
    settings: Settings | None = None,
) -> str:
    """Один ход диалога: ответ на задание или реплика тьютору."""
    s = settings or get_settings()
    stamp = time.time() if now is None else now
    session_id = repos.ensure_open_session(conn, stamp)
    state = repos.get_session_state(conn, session_id)
    graph = CourseGraph.load(conn)

    if state.pending_item_id is not None:
        reply, events, mastery, new_state = _answer_branch(
            conn, graph, user_text, state, now=stamp, settings=s
        )
    else:
        reply, events, mastery, new_state = await _tutor_branch(
            conn, client, model, session_id, user_text, state, graph, now=stamp, settings=s
        )

    post_turn(
        conn,
        session_id,
        user_text=user_text,
        assistant_text=reply,
        state=new_state,
        events=events,
        mastery=mastery,
        now=stamp,
    )
    return reply


def start_practice(
    conn: sqlite3.Connection, *, now: float | None = None, settings: Settings | None = None
) -> str:
    """Выдаёт задание по текущему маршруту и ждёт ответа."""
    s = settings or get_settings()
    stamp = time.time() if now is None else now
    graph = CourseGraph.load(conn)
    if not graph.node_ids:
        return EMPTY_GRAPH_REPLY

    session_id = repos.ensure_open_session(conn, stamp)
    state = repos.get_session_state(conn, session_id)
    question = diagnostic.next_question(conn, graph, now=stamp, settings=s)
    if question is None:
        return NO_TASK_REPLY

    new_state = state.model_copy(
        update={
            "pending_item_id": question.item.id,
            "current_node_id": question.concept_id,
            "hint_level": 0,
            "last_activity": stamp,
        }
    )
    text = _render_item(question.item)
    post_turn(
        conn,
        session_id,
        user_text=PRACTICE_KICKOFF_TEXT,
        assistant_text=text,
        state=new_state,
        now=stamp,
    )
    return text
