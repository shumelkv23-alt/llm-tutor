"""Проверочный проход по узлу: «закрой тему» с доказательствами.

Ученик утверждает, что тему знает; бот это проверяет — задание за заданием,
пока не сработает обычный критерий закрытия (``student/guide.is_node_closed``).
Закрывает узел код, а не заявление ученика и не вердикт грейдера (§9.2).
"""

import logging
import sqlite3
import time

from llm_tutor.config import Settings, get_settings
from llm_tutor.core.turn import TurnReply, _issue_task, post_turn
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.llm.prompts import EMPTY_GRAPH_REPLY
from llm_tutor.schemas import SessionState

logger = logging.getLogger(__name__)

# Повод прохода: в журнале он виден как просьба ученика, а не как молчание.
VERIFY_KICKOFF_TEXT = "Закрой тему — я её уже знаю."
VERIFY_NO_NODE_REPLY = "Сейчас нечего закрывать — сначала выбери тему в маршруте."


def _post_reply(
    conn: sqlite3.Connection,
    session_id: int,
    *,
    state: SessionState,
    text: str,
    user_text: str,
    tutor_reply: str | None,
    now: float,
    options: list[str] | None = None,
    issued: bool = False,
) -> TurnReply:
    """Пишет ход прохода одним коммитом и отдаёт ответ.

    ``tutor_reply`` — реплика, с которой тьюторский ход вошёл в проход (флаг
    модели): ученик её увидел, значит и ответ, и журнал должны её содержать —
    ``core/context`` собирает хвост диалога из журнала, и без неё модель на
    следующем ходу не увидит того, что ученик только что прочитал.
    """
    full = f"{tutor_reply}\n\n{text}" if tutor_reply else text
    post_turn(
        conn,
        session_id,
        user_text=user_text,
        assistant_text=full,
        task_text=text if issued and state.pending_item_id is not None else None,
        state=state,
        now=now,
    )
    if issued and state.pending_item_id is not None:
        # Задание выдано: см. инвариант TurnReply — задание в ``tail``.
        return TurnReply(
            text=tutor_reply or "", options=options, tail=text, item_id=state.pending_item_id
        )
    return TurnReply(text=full, options=options)


def start_verification(
    conn: sqlite3.Connection,
    *,
    now: float | None = None,
    settings: Settings | None = None,
    user_text: str = VERIFY_KICKOFF_TEXT,
    tutor_reply: str | None = None,
) -> TurnReply:
    """Вход в проверочный проход по текущему узлу.

    ``user_text`` и ``tutor_reply`` переопределяют повод и начало ответа —
    так в проход входит тьюторский ход, поймавший просьбу закрыть тему: в
    журнал ложатся слова ученика, а не синтетический повод кнопки.
    """
    s = settings or get_settings()
    stamp = time.time() if now is None else now
    session_id = repos.ensure_open_session(conn, stamp)
    state = repos.get_session_state(conn, session_id)
    graph = CourseGraph.load(conn)

    if not graph.node_ids:
        return _post_reply(
            conn,
            session_id,
            state=state,
            text=EMPTY_GRAPH_REPLY,
            user_text=user_text,
            tutor_reply=tutor_reply,
            now=stamp,
        )

    node_id = state.current_node_id
    if node_id is None:
        return _post_reply(
            conn,
            session_id,
            state=state,
            text=VERIFY_NO_NODE_REPLY,
            user_text=user_text,
            tutor_reply=tutor_reply,
            now=stamp,
        )
    if not graph.has_node(node_id):
        # Узел убрали из графа (правка seed или БД руками) — иначе занятие
        # запиралось бы KeyError на каждом ходу.
        logger.warning("Узел %s больше не в графе, снимаю", node_id)
        return _post_reply(
            conn,
            session_id,
            state=state.model_copy(
                update={"current_node_id": None, "node_streak": 0, "last_activity": stamp}
            ),
            text=VERIFY_NO_NODE_REPLY,
            user_text=user_text,
            tutor_reply=tutor_reply,
            now=stamp,
        )

    # Проход начинается заново: узел в режим «проверить и идти дальше», серия
    # обнулена — закрытие должно опираться на свидетельства ПРОХОДА.
    started = state.model_copy(
        update={
            "mode": "verify",
            "node_streak": 0,
            "hint_level": 0,
            "task_hinted": False,
            "pending_item_id": None,
            "phase": "explain",
            "verify_item_ids": [],
            # Список выданного в уроке про заход урока: у прохода свой.
            "lesson_item_ids": [],
            "last_activity": stamp,
        }
    )
    new_state, text, options = _issue_task(conn, graph, started, now=stamp, settings=s)
    return _post_reply(
        conn,
        session_id,
        state=new_state,
        text=text,
        user_text=user_text,
        tutor_reply=tutor_reply,
        now=stamp,
        options=options,
        issued=True,
    )
