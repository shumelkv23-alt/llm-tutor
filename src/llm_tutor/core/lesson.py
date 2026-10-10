"""Занятие вне конкретного интерфейса: начало урока, статусы тем, переход.

Логика переехала из ``bot/start.py`` и ``bot/themes.py``: она нужна и боту, и
вебу, а слой ``core`` не знает ни про Telegram, ни про браузер.
"""

import sqlite3
import time
from dataclasses import dataclass
from typing import Literal

from llm_tutor.config import Settings, get_settings
from llm_tutor.core.turn import TurnReply, post_turn, reset_lesson, resume_reply
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.llm.client import LLMClient
from llm_tutor.schemas import RouteStep, SessionState
from llm_tutor.student import beta
from llm_tutor.student import route as route_mod
from llm_tutor.student.planner import CONFIDENT_UNCERTAINTY

NodeStatus = Literal["closed", "current", "claimed", "available", "ahead"]

SWITCH_TEMPLATE = "Ок, тема — «{name}». Спроси, что непонятно, — объясню."


def closed_from_route(state: SessionState) -> set[str]:
    """Узлы, закрытые в снимке маршрута сессии — запись тьютора о закрытии.

    Ученик закрывает узел серией чистых ответов ИЛИ уверенным владением;
    снимок маршрута хранит оба случая, поэтому он — источник правды.
    """
    if state.route is None:
        return set()
    return {step.concept_id for step in state.route.steps if step.status == "closed"}


def claimed_from_route(state: SessionState) -> set[str]:
    """Узлы, заявленные в анкете и ещё не подтверждённые (снимок маршрута)."""
    if state.route is None:
        return set()
    return {step.concept_id for step in state.route.steps if step.status == "claimed"}


def is_closed(
    conn: sqlite3.Connection,
    node_id: str,
    state: SessionState,
    *,
    now: float | None,
    settings: Settings,
) -> bool:
    """Закрыт ли узел: по снимку маршрута или уверенным владением."""
    if node_id in closed_from_route(state):
        return True
    mastery = beta.estimate(conn, node_id, now=now, settings=settings)
    return (
        mastery.mean >= settings.mastery_skip_threshold
        and mastery.uncertainty <= CONFIDENT_UNCERTAINTY
    )


def node_status(
    conn: sqlite3.Connection,
    graph: CourseGraph,
    node_id: str,
    state: SessionState,
    *,
    now: float | None,
    settings: Settings,
) -> NodeStatus:
    """Статус узла для списка тем."""
    if node_id == state.current_node_id:
        return "current"
    if is_closed(conn, node_id, state, now=now, settings=settings):
        return "closed"
    claimed = claimed_from_route(state)
    if node_id in claimed:
        return "claimed"
    prereqs = graph.hard_prerequisites(node_id)
    if all(
        prereq in claimed or is_closed(conn, prereq, state, now=now, settings=settings)
        for prereq in prereqs
    ):
        return "available"
    return "ahead"


def switch_node(
    conn: sqlite3.Connection,
    node_id: str,
    *,
    now: float | None = None,
    settings: Settings | None = None,
) -> str:
    """Переход к узлу: смена текущей темы, снятие висящего задания (сырой текст)."""
    s = settings or get_settings()
    stamp = time.time() if now is None else now
    session_id = repos.ensure_open_session(conn, stamp)
    state = repos.get_session_state(conn, session_id)
    graph = CourseGraph.load(conn)
    name = graph.concept(node_id).name

    # Повторный выбор той же темы (ученик вернулся в список и ткнул в текущую)
    # не должен молча стирать набранную серию, лестницу подсказок и висящее
    # задание: тема та же, прогресс по ней никуда не делся.
    if state.current_node_id == node_id:
        keep = {
            "phase": state.phase,
            "node_streak": state.node_streak,
            "hint_level": state.hint_level,
            "pending_item_id": state.pending_item_id,
            "lesson_item_ids": state.lesson_item_ids,
        }
    else:
        keep = {
            "phase": "explain",
            "node_streak": 0,
            "hint_level": 0,
            "pending_item_id": None,
            # Список выданного в уроке — про узел: на новой теме он начинает
            # заход заново (спека §5.3).
            "lesson_item_ids": [],
        }
    new_state = state.model_copy(
        update={
            "current_node_id": node_id,
            "mode": None,
            # Переход на другую тему снимает проверочный проход (спека §5.2).
            "verify_item_ids": [],
            "last_activity": stamp,
            **keep,
        }
    )
    fresh_route, _ = route_mod.refresh(conn, new_state, graph, now=stamp, settings=s)
    text = SWITCH_TEMPLATE.format(name=name)
    post_turn(
        conn,
        session_id,
        user_text=f"Перейти к теме: {node_id}",
        assistant_text=text,
        state=new_state.model_copy(update={"route": fresh_route}),
        now=stamp,
    )
    return text


# Сколько ближайших шагов показывать перед первым уроком.
UPCOMING_STEPS = 5


@dataclass(frozen=True)
class LessonStart:
    """С чего начинается урок после анкеты: первый узел и ближайшие шаги."""

    first: str | None
    upcoming: list[RouteStep]

    @property
    def starts_with_check(self) -> bool:
        """Первый шаг — проверка знакомого (всё отмечено «Уверенно»)."""
        return bool(self.upcoming) and self.upcoming[0].status == "claimed"


def prepare_lesson(
    conn: sqlite3.Connection, *, settings: Settings, limit: int = UPCOMING_STEPS
) -> LessonStart:
    """Сброс захода и маршрут после анкеты (без модели).

    Первый шаг списка — ровно тот узел, с которого начнётся урок: его же
    передают в ``resume_reply``, иначе планировщик мог бы выбрать другой.
    """
    reset_lesson(conn)
    graph = CourseGraph.load(conn)
    route = route_mod.build_route(
        conn, graph, goal_concept_id=route_mod.goal_for(conn, graph), settings=settings
    )
    first = route_mod.next_node_id(conn, graph, route, settings=settings)
    return LessonStart(first=first, upcoming=route_mod.upcoming(route, first, limit=limit))


async def begin_lesson(
    conn: sqlite3.Connection,
    client: LLMClient,
    model: str,
    *,
    settings: Settings,
    limit: int = UPCOMING_STEPS,
) -> tuple[LessonStart, TurnReply | None]:
    """Подготовка и первый ход урока. ``None`` вместо хода — учить уже нечему."""
    start = prepare_lesson(conn, settings=settings, limit=limit)
    if not start.upcoming:
        return start, None
    reply = await resume_reply(conn, client, model, settings=settings, start_node_id=start.first)
    return start, reply
