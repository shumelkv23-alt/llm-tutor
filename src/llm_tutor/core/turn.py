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
from llm_tutor.grader import autocheck, rubric
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
# Напоминание, что вопрос не сбросил выданное задание.
PENDING_ITEM_NOTE = (
    "Задание всё ещё ждёт ответа — ответь вариантом или пришли /skip."
)
SKIP_KICKOFF_TEXT = "Пропустить задание."
SKIP_REPLY = (
    "Пропустил — свидетельство не записано, вернёмся к этому узлу позже. "
    "Можно взять новое задание: /task."
)
NOTHING_TO_SKIP_REPLY = "Сейчас нет задания, которое нужно пропустить."


def _render_item(item: Item) -> str:
    """Задание как текст сообщения (варианты — нумерованным списком)."""
    if not item.options:
        return item.prompt
    options = "\n".join(
        f"{index + 1}. {option}" for index, option in enumerate(item.options)
    )
    return f"{item.prompt}\n\n{options}"


def _normalize_choice_answer(item: Item, text: str) -> str:
    """Номер варианта из списка (нумерация с 1) превращает в текст варианта.

    Задание показывается нумерованным списком, а ``autocheck`` ждёт либо текст
    варианта, либо 0-based индекс — набранная цифра без этой поправки
    засчитала бы верный ответ неверным.
    """
    stripped = text.strip()
    if item.answer_type != "choice" or not stripped.isdigit():
        return text
    number = int(stripped)
    if 1 <= number <= len(item.options):
        return item.options[number - 1]
    return text


def _looks_like_question(text: str) -> bool:
    """Ученик спрашивает, а не отвечает.

    Вопрос нельзя записывать в журнал как неверный ответ — иначе он теряется
    безвозвратно вместе с висящим заданием.
    """
    return text.strip().endswith("?")


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
        conn.commit()
    except Exception:
        conn.rollback()
        raise


async def _answer_branch(
    conn: sqlite3.Connection,
    client: LLMClient,
    model: str,
    graph: CourseGraph,
    user_text: str,
    state: SessionState,
    *,
    now: float,
    settings: Settings,
) -> tuple[str, list[Event], list[beta.MasteryUpdate], SessionState]:
    """Ученик отвечает на выданное задание.

    ``choice``/``short`` проверяет код, ``open``/``code`` — рубричный грейдер
    (его вердикт идёт в журнал с ограниченным весом).
    """
    item = (
        repos.get_item(conn, state.pending_item_id)
        if state.pending_item_id is not None
        else None
    )
    if item is None:
        return (
            STALE_ITEM_REPLY,
            [],
            [],
            state.model_copy(update={"pending_item_id": None}),
        )

    if item.answer_type in diagnostic.AUTO_CHECKABLE:
        result = autocheck.check(item, _normalize_choice_answer(item, user_text))
    elif item.answer_type in diagnostic.RUBRIC_CHECKABLE and item.rubric_id is not None:
        result = await rubric.grade(conn, client, model, item, user_text, settings=settings)
    else:
        return (
            STALE_ITEM_REPLY,
            [],
            [],
            state.model_copy(update={"pending_item_id": None}),
        )

    measured = state.current_node_id or next(iter(item.concept_weights), "")
    events, mastery = diagnostic.plan_evidence(
        conn,
        graph,
        item,
        measured,
        result.score,
        source="checked" if item.answer_type in diagnostic.AUTO_CHECKABLE else "rubric",
        weight_scale=(
            1.0
            if item.answer_type in diagnostic.AUTO_CHECKABLE
            else settings.rubric_evidence_weight
        ),
        now=now,
        settings=settings,
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

    if not package.found_material and has_searchable_content(user_text):
        # Осмысленный вопрос, а материала нет вовсе — честный отказ без LLM.
        # (Материал, вытесненный бюджетом, сюда не попадает: см. found_material.)
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

    if state.pending_item_id is not None and not _looks_like_question(user_text):
        reply, events, mastery, new_state = await _answer_branch(
            conn, client, model, graph, user_text, state, now=stamp, settings=s
        )
    else:
        reply, events, mastery, new_state = await _tutor_branch(
            conn, client, model, session_id, user_text, state, graph, now=stamp, settings=s
        )
        if state.pending_item_id is not None:
            # Вопрос при висящем задании: ответили тьютором, задание не тронули.
            reply = f"{reply}\n\n{PENDING_ITEM_NOTE}"

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
    # Практика берёт и задания с рубрикой: открытые ответы проверяет грейдер.
    question = diagnostic.next_question(
        conn, graph, include_rubric=True, now=stamp, settings=s
    )
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


def skip_pending(
    conn: sqlite3.Connection, *, now: float | None = None, settings: Settings | None = None
) -> str:
    """Снимает ожидание ответа, НЕ записывая свидетельство.

    Явный выход из задания: без него единственным способом выйти было
    ответить (и получить неверный ответ в журнал).
    """
    stamp = time.time() if now is None else now
    session_id = repos.ensure_open_session(conn, stamp)
    state = repos.get_session_state(conn, session_id)
    if state.pending_item_id is None:
        return NOTHING_TO_SKIP_REPLY

    post_turn(
        conn,
        session_id,
        user_text=SKIP_KICKOFF_TEXT,
        assistant_text=SKIP_REPLY,
        state=state.model_copy(update={"pending_item_id": None, "last_activity": stamp}),
        now=stamp,
    )
    return SKIP_REPLY
