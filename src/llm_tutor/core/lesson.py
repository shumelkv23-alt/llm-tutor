"""Занятие вне конкретного интерфейса: начало урока, статусы тем, переход.

Логика переехала сюда из бота (срез 40), а сам бот удалён в срезе 49:
слой ``core`` не знает про интерфейс, веб лишь показывает его ответы.
"""

import sqlite3
import time
from collections.abc import Collection
from dataclasses import dataclass
from typing import Literal

from llm_tutor.config import Settings, get_settings
from llm_tutor.core.turn import TurnReply, post_turn, reset_lesson, resume_reply
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.llm.client import LLMClient
from llm_tutor.schemas import RouteStep, SessionState, Topic
from llm_tutor.student import beta, survey
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
            # Урок частями и счёт ошибок (срез 28) — тоже про узел: иначе
            # ошибка прежней темы сделала бы первую ошибку новой «второй», а
            # кнопка «Дальше» показала бы части чужого урока (аудит среза 51, M1).
            "check_misses": 0,
            "lesson_parts": [],
            "lesson_part": 0,
            "close_requested": False,
        }
    new_state = state.model_copy(
        update={
            "current_node_id": node_id,
            "mode": None,
            # Переход на другую тему снимает проверочный проход (спека §5.2).
            "verify_item_ids": [],
            "last_activity": stamp,
            # Тема выбрана напрямую — прыжок через анкету больше не ждёт.
            "jump_node_id": None,
            **keep,
        }
    )
    fresh_route, _ = route_mod.refresh(conn, new_state, graph, now=stamp, settings=s)
    # Модуль работы с непройденной анкетой (прыжок туда и сразу назад) не
    # должен удерживать тему из другого модуля: любая реплика снова открыла бы
    # его анкету (финальное ревью ветки course-modules, F-2).
    working = repos.get_topic(conn, fresh_route.topic_id) if fresh_route.topic_id else None
    if working is not None and not survey.is_completed(conn, working.survey):
        fresh_route = fresh_route.model_copy(update={"topic_id": graph.topic_of(node_id)})
    text = SWITCH_TEMPLATE.format(name=name)
    post_turn(
        conn,
        session_id,
        user_text=f"Перейти к теме «{name}»",
        assistant_text=text,
        state=new_state.model_copy(update={"route": fresh_route}),
        now=stamp,
    )
    return text


# Прыжок в модуль, анкета которого не пройдена: сначала она (§5.2 спеки модулей).
MODULE_SURVEY_FIRST = (
    "«{name}» — из модуля {number} «{title}». Сначала пара вопросов о модуле — "
    "подберу, с чего начать."
)


def survey_first(conn: sqlite3.Connection, node_id: str) -> Topic | None:
    """Модуль темы, анкета которого не пройдена, иначе ``None``."""
    graph = CourseGraph.load(conn)
    topic = repos.get_topic(conn, graph.topic_of(node_id))
    if topic is None or survey.is_completed(conn, topic.survey):
        return None
    return topic


def enter_module(
    conn: sqlite3.Connection,
    node_id: str,
    *,
    now: float | None = None,
    settings: Settings | None = None,
) -> str:
    """Прыжок в модуль с непройденной анкетой: он становится модулем работы.

    Текущая тема и висящее задание прошлого модуля снимаются — как при
    обычном переходе к теме. Тему прыжка запоминаем в сессии
    (``jump_node_id``): урок после анкеты начнётся с неё (ветка
    course-modules, ревью 26-2 и F-3). Отдаёт реплику для чата.
    """
    s = settings or get_settings()
    stamp = time.time() if now is None else now
    session_id = repos.ensure_open_session(conn, stamp)
    state = repos.get_session_state(conn, session_id)
    graph = CourseGraph.load(conn)
    topic_id = graph.topic_of(node_id)
    topic = repos.get_topic(conn, topic_id)
    route = state.route or route_mod.build_route(
        conn, graph, goal_concept_id=route_mod.goal_for(conn, graph), now=stamp, settings=s
    )
    name = graph.concept(node_id).name
    text = MODULE_SURVEY_FIRST.format(
        name=name, number=topic_id, title=topic.title if topic else ""
    )
    post_turn(
        conn,
        session_id,
        user_text=f"Перейти к теме «{name}»",
        assistant_text=text,
        state=state.model_copy(
            update={
                "current_node_id": None,
                "pending_item_id": None,
                "mode": None,
                "phase": "explain",
                "node_streak": 0,
                "verify_item_ids": [],
                "lesson_item_ids": [],
                "check_misses": 0,
                "lesson_parts": [],
                "lesson_part": 0,
                "close_requested": False,
                "route": route_mod.release_current(route).model_copy(
                    update={"topic_id": topic_id}
                ),
                "last_activity": stamp,
                "jump_node_id": node_id,
            }
        ),
        now=stamp,
    )
    return text


# Сколько ближайших шагов показывать перед первым уроком.
UPCOMING_STEPS = 5

# Подводка к первому уроку: что сейчас будет (имя подставит вызывающий код).
LESSON_LEAD = "Начинаем с «{name}» — сейчас коротко объясню и покажу пример."
CHECK_LEAD = "Всё отмечено знакомым — начнём с короткой проверки."


@dataclass(frozen=True)
class LessonStart:
    """С чего начинается урок после анкеты: первый узел и ближайшие шаги."""

    first: str | None
    upcoming: list[RouteStep]

    @property
    def starts_with_check(self) -> bool:
        """Первый шаг — проверка знакомого (всё отмечено «Уверенно»)."""
        return bool(self.upcoming) and self.upcoming[0].status == "claimed"


def pending_survey_topic(conn: sqlite3.Connection) -> Topic | None:
    """Модуль занятия, анкета которого ещё не пройдена (§5.2 спеки модулей).

    Это чтение, а не ход: сессию не заводим. ``None`` — анкету проходить не
    нужно (или курс пуст).
    """
    graph = CourseGraph.load(conn)
    if not graph.node_ids:
        return None
    session_id = repos.get_open_session(conn)
    state = repos.get_session_state(conn, session_id) if session_id else SessionState()
    topic_id = route_mod.topic_for(graph, state)
    topic = repos.get_topic(conn, topic_id) if topic_id is not None else None
    if topic is None or survey.is_completed(conn, topic.survey):
        return None
    return topic


def prepare_lesson(
    conn: sqlite3.Connection,
    *,
    settings: Settings,
    claimed: Collection[str] = (),
    limit: int = UPCOMING_STEPS,
) -> LessonStart:
    """Сброс захода и маршрут после анкеты модуля (без модели).

    ``claimed`` — темы, которые анкета модуля только что назвала знакомыми.
    Снимок маршрута не обнуляется: в нём закрытое прошлых модулей. Первый шаг
    списка — ровно тот узел, с которого начнётся урок: его же передают в
    ``resume_reply``, иначе планировщик мог бы выбрать другой. Тема прыжка в
    модуль (``jump_node_id``) — первая, если она есть.
    """
    session_id = repos.get_open_session(conn)
    jump = repos.get_session_state(conn, session_id).jump_node_id if session_id else None
    reset_lesson(conn, claimed=claimed)
    graph = CourseGraph.load(conn)
    previous = repos.get_session_state(conn, session_id).route if session_id else None
    route = route_mod.build_route(
        conn,
        graph,
        goal_concept_id=route_mod.goal_for(conn, graph),
        previous=previous,
        settings=settings,
    )
    first = (
        jump
        if jump is not None and graph.has_node(jump)
        else route_mod.next_node_id(conn, graph, route, settings=settings)
    )
    # Список — по участку модуля работы: первой темой может быть подтянутый
    # предок из прошлого модуля, и участок его модуля был бы не тот.
    topic = (
        route.topic_id
        if jump is None and route.topic_id is not None
        else graph.topic_of(first) if first is not None else None
    )
    section = route_mod.working_section(graph, route, topic) if first is not None else []
    return LessonStart(
        first=first, upcoming=route_mod.upcoming(route, first, limit=limit, section=section)
    )


async def begin_lesson(
    conn: sqlite3.Connection,
    client: LLMClient,
    model: str,
    *,
    settings: Settings,
    claimed: Collection[str] = (),
    limit: int = UPCOMING_STEPS,
) -> tuple[LessonStart, TurnReply | None]:
    """Подготовка и первый ход урока. ``None`` вместо хода — учить уже нечему."""
    start = prepare_lesson(conn, settings=settings, claimed=claimed, limit=limit)
    if not start.upcoming:
        return start, None
    reply = await resume_reply(conn, client, model, settings=settings, start_node_id=start.first)
    return start, reply
