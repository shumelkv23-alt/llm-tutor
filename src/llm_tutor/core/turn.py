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
from collections.abc import Mapping
from dataclasses import dataclass

from llm_tutor.config import Settings, get_settings
from llm_tutor.core.context import build_context
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.grader import autocheck, rubric
from llm_tutor.llm.client import LLMClient, LLMError
from llm_tutor.llm.prompts import EMPTY_GRAPH_REPLY, LLM_FAILURE_REPLY
from llm_tutor.llm.schemas import TutorReply
from llm_tutor.schemas import Event, Item, SessionState
from llm_tutor.student import beta, diagnostic, guide, hints, route as route_mod

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
GRADING_FAILED_REPLY = (
    "Это задание не удалось проверить — снимаю его. Возьми новое: /task."
)
# По текущему узлу заданий в банке нет — ведём диалогом, узел не рвём.
NO_TASK_FOR_NODE_REPLY = (
    "По этому узлу новых заданий сейчас нет — свежие ты уже отвечал. "
    "Возьми ещё раз то же: /task, или спрашивай — объясню."
)
# Узел закрыт — объявляем следующий шаг (имя подставит вызывающий код).
STUCK_NOTE = "Ок, остаёмся на этом узле и разбираемся глубже."
# Маршрут пройден до конца: заданий больше нет, и это не ошибка.
ROUTE_DONE_REPLY = "Маршрут пройден до конца. Можно свериться: /plan."


@dataclass(frozen=True)
class TurnReply:
    """Ответ хода: текст и, если выпало задание с вариантами, подписи кнопок."""

    text: str
    options: list[str] | None = None


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
) -> tuple[str, list[Event], list[beta.MasteryUpdate], SessionState, bool]:
    """Ученик отвечает на выданное задание.

    ``choice``/``short`` проверяет код, ``open``/``code`` — рубричный грейдер
    (его вердикт идёт в журнал с ограниченным весом). Пятый элемент — был ли
    ответ верным: закрывать узел на неудачном ответе нельзя.
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
            False,
        )

    try:
        if item.answer_type in diagnostic.AUTO_CHECKABLE:
            result = autocheck.check(item, _normalize_choice_answer(item, user_text))
        elif item.answer_type in diagnostic.RUBRIC_CHECKABLE and item.rubric_id is not None:
            result = await rubric.grade(
                conn, client, model, item, user_text, settings=settings
            )
        else:
            return (
                STALE_ITEM_REPLY,
                [],
                [],
                state.model_copy(update={"pending_item_id": None}),
                False,
            )
    except (rubric.RubricError, autocheck.AutoCheckError):
        # Задание нельзя проверить (рубрику убрали, эталон битый) — снимаем его,
        # иначе оно залипнет и ученик не сможет выйти из него иначе как /skip.
        logger.warning("Задание %s не проверяется, снимаю", item.id)
        return (
            GRADING_FAILED_REPLY,
            [],
            [],
            state.model_copy(update={"pending_item_id": None}),
            False,
        )

    measured = state.current_node_id or next(iter(item.concept_weights), "")
    # Повтор того же вопроса — свидетельство слабее: знания могло и не прибавиться.
    first_time = not repos.item_was_answered(conn, item.id)
    evidence_scale = 1.0 if first_time else settings.repeat_evidence_weight
    events, mastery = diagnostic.plan_evidence(
        conn,
        graph,
        item,
        measured,
        result.score,
        source="checked" if item.answer_type in diagnostic.AUTO_CHECKABLE else "rubric",
        weight_scale=evidence_scale
        * (
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
    # Помощь по этому заданию — факт, а не выбранный моделью уровень: спросил
    # или попросил глубины, значит ответ не «без подсказок».
    hinted = state.task_hinted or state.hint_level > 0
    passed = result.score >= diagnostic.SUCCESS_SCORE
    new_state = guide.register_answer(
        new_state, correct=passed, hinted=hinted, settings=settings
    )
    return _feedback(item, result.score), events, mastery, new_state, passed


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

    try:
        answer = await client.chat_structured(package.messages, TutorReply, model=model)
    except LLMError:
        return LLM_FAILURE_REPLY, [], [], idle_state
    except Exception:  # noqa: BLE001 — бот не должен молчать на неожиданный сбой
        logger.exception("Неожиданный сбой тьюторского хода")
        return LLM_FAILURE_REPLY, [], [], idle_state

    level = hints.next_hint_level(state.hint_level, answer.hint_level)
    new_state = state.model_copy(update={"hint_level": level, "last_activity": now})
    if state.pending_item_id is not None:
        # Пока задание висело, ученик получил помощь: ответ уже не «без подсказок»,
        # даже если модель на этом ходу опустила уровень до нуля.
        new_state = new_state.model_copy(update={"task_hinted": True})
    if answer.student_stuck:
        # Ученик просит глубины: узел в усиленный проход, вперёд не идём.
        stuck_state = guide.on_student_stuck(new_state).model_copy(
            update={"task_hinted": True}
        )
        return f"{answer.reply}\n\n{STUCK_NOTE}", [], [], stuck_state
    return answer.reply, [], [], new_state


async def handle_turn(
    conn: sqlite3.Connection,
    client: LLMClient,
    model: str,
    user_text: str,
    *,
    now: float | None = None,
    settings: Settings | None = None,
) -> TurnReply:
    """Один ход диалога: ответ на задание или реплика тьютору."""
    s = settings or get_settings()
    stamp = time.time() if now is None else now
    session_id = repos.ensure_open_session(conn, stamp)
    state = repos.get_session_state(conn, session_id)
    graph = CourseGraph.load(conn)
    options: list[str] | None = None
    answered_item_id = state.pending_item_id

    if state.pending_item_id is not None and not _looks_like_question(user_text):
        reply, events, mastery, new_state, passed = await _answer_branch(
            conn, client, model, graph, user_text, state, now=stamp, settings=s
        )
        # Узел пройден? Только на ВЕРНОМ ответе: «не совсем верно» и «узёл
        # закрыт» в одном сообщении противоречат друг другу. Владение считаем
        # с учётом только что отвеченного задания, а не по старым данным.
        if passed:
            overrides = {
                change.concept_id: beta.to_mastery(
                    change.concept_id,
                    change.alpha,
                    change.beta,
                    change.last_seen,
                    change.next_review,
                )
                for change in mastery
            }
            new_state, closed_note = _close_node_if_ready(
                conn, graph, new_state, overrides=overrides, now=stamp, settings=s
            )
            if closed_note:
                reply = f"{reply}\n\n{closed_note}"
        # Ведём дальше: следующий узел после закрытия или ещё задание по этому.
        new_state, task_text, options = _issue_task(
            conn,
            graph,
            new_state,
            exclude_item_ids=(
                frozenset({answered_item_id})
                if answered_item_id is not None
                else frozenset()
            ),
            now=stamp,
            settings=s,
        )
        reply = f"{reply}\n\n{task_text}"
    else:
        reply, events, mastery, new_state = await _tutor_branch(
            conn, client, model, session_id, user_text, state, graph, now=stamp, settings=s
        )
        if state.pending_item_id is not None:
            # Вопрос при висящем задании: ответили тьютором, задание не тронули.
            reply = f"{reply}\n\n{PENDING_ITEM_NOTE}"

    # Маршрут пересчитывается на каждом ходу, но ученику сообщается только
    # о значимом изменении (§6.4 — «малые различия» не тревожат план).
    fresh_route, route_note = route_mod.refresh(conn, new_state, graph, now=stamp, settings=s)
    new_state = new_state.model_copy(update={"route": fresh_route})
    if route_note:
        reply = f"{reply}\n\n{route_note}"

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
    return TurnReply(text=reply, options=options)


def _close_node_if_ready(
    conn: sqlite3.Connection,
    graph: CourseGraph,
    state: SessionState,
    *,
    overrides: Mapping[str, beta.Mastery] | None = None,
    now: float,
    settings: Settings,
) -> tuple[SessionState, str | None]:
    """Закрывает пройденный узел и объявляет следующий.

    Критерий считает код (``student/guide.py``), не модель: «понятно?» ответом
    доказательством не считается. ``overrides`` — владение С УЧЁТОМ только что
    отвеченного задания: без них и закрытие, и выбор следующего узла шли бы по
    старым данным (узел закрыт, а следующий по нему ещё «не готов»).
    """
    node_id = state.current_node_id
    if node_id is None:
        return state, None
    if not graph.has_node(node_id):
        # Узел убрали из графа (правка seed или БД руками) — иначе занятие
        # запиралось бы KeyError на каждом ходу, не записывая ответы.
        logger.warning("Узел %s больше не в графе, снимаю", node_id)
        return (
            state.model_copy(update={"current_node_id": None, "node_streak": 0}),
            None,
        )

    current = (overrides or {}).get(node_id) or beta.estimate(
        conn, node_id, now=now, settings=settings
    )
    if not guide.is_node_closed(state, current, settings=settings):
        return state, None

    closed_name = graph.concept(node_id).name
    route = state.route or route_mod.build_route(conn, graph, now=now, settings=settings)
    route = route.model_copy(
        update={
            "steps": [
                step.model_copy(update={"status": "closed"})
                if step.concept_id == node_id
                else step
                for step in route.steps
            ]
        }
    )
    next_node_id = route_mod.next_node_id(
        conn, graph, route, now=now, settings=settings, mastery_overrides=overrides
    )
    new_state = state.model_copy(
        update={
            "current_node_id": next_node_id,
            "node_streak": 0,
            "phase": "explain",
            "mode": None,
            "hint_level": 0,
            "route": route,
        }
    )
    if next_node_id is None:
        return new_state, f"Узел «{closed_name}» закрыт — маршрут пройден до конца."
    next_name = graph.concept(next_node_id).name
    return new_state, f"Узел «{closed_name}» закрыт ✓ — идём дальше: {next_name}."


def _issue_task(
    conn: sqlite3.Connection,
    graph: CourseGraph,
    state: SessionState,
    *,
    exclude_item_ids: frozenset[int] = frozenset(),
    now: float,
    settings: Settings,
) -> tuple[SessionState, str, list[str] | None]:
    """Выдаёт задание по текущему узлу и переводит занятие в фазу практики.

    ``exclude_item_ids`` — задания, только что отвеченные в этом же ходу: их
    события ещё не записаны (запись идёт одним коммитом после), поэтому защита
    от повтора должна учитывать их явно.
    """
    route = state.route or route_mod.build_route(
        conn, graph, goal_concept_id=route_mod.goal_for(conn, graph), now=now, settings=settings
    )
    # Узел задан маршрутом: если его нет — берём следующий по приоритету.
    node_id = state.current_node_id or route_mod.next_node_id(
        conn, graph, route, now=now, settings=settings
    )
    if node_id is None:
        # Маршрут исчерпан — задания выдавать не по чему, и уходить в общий
        # подбор нельзя: он вернул бы узлы вне маршрута и сломал завершение.
        return (
            state.model_copy(update={"phase": "explain", "route": route, "last_activity": now}),
            ROUTE_DONE_REPLY,
            None,
        )

    question = diagnostic.question_for_node(
        conn, node_id, asked_item_ids=exclude_item_ids, now=now, settings=settings
    )
    if question is None:
        return (
            state.model_copy(update={"phase": "explain", "route": route, "last_activity": now}),
            NO_TASK_FOR_NODE_REPLY,
            None,
        )

    new_state = state.model_copy(
        update={
            "pending_item_id": question.item.id,
            "current_node_id": question.concept_id,
            "hint_level": 0,
            # Новое задание — помощь по нему ещё не оказана.
            "task_hinted": False,
            "phase": "practice",
            "route": route,
            "last_activity": now,
        }
    )
    return new_state, _render_item(question.item), question.item.options or None


def start_practice_reply(
    conn: sqlite3.Connection, *, now: float | None = None, settings: Settings | None = None
) -> TurnReply:
    """Выдаёт задание по текущему маршруту и ждёт ответа.

    Практика берёт и задания с рубрикой: открытые ответы проверяет грейдер.
    Если по текущему узлу заданий нет — говорим об этом честно, узел не рвём.
    """
    s = settings or get_settings()
    stamp = time.time() if now is None else now
    graph = CourseGraph.load(conn)
    if not graph.node_ids:
        return TurnReply(text=EMPTY_GRAPH_REPLY)

    session_id = repos.ensure_open_session(conn, stamp)
    state = repos.get_session_state(conn, session_id)
    new_state, text, options = _issue_task(conn, graph, state, now=stamp, settings=s)

    fresh_route, _ = route_mod.refresh(conn, new_state, graph, now=stamp, settings=s)
    post_turn(
        conn,
        session_id,
        user_text=PRACTICE_KICKOFF_TEXT,
        assistant_text=text,
        state=new_state.model_copy(update={"route": fresh_route}),
        now=stamp,
    )
    return TurnReply(text=text, options=options)


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
        state=state.model_copy(
            update={"pending_item_id": None, "phase": "explain", "last_activity": stamp}
        ),
        now=stamp,
    )
    return SKIP_REPLY
