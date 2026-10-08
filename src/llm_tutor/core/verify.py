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

logger = logging.getLogger(__name__)

# Повод прохода: в журнале он виден как просьба ученика, а не как молчание.
VERIFY_KICKOFF_TEXT = "Закрой тему — я её уже знаю."
VERIFY_NO_NODE_REPLY = "Сейчас нечего закрывать — выбери тему: 🎚 Темы"


def start_verification(
    conn: sqlite3.Connection,
    *,
    now: float | None = None,
    settings: Settings | None = None,
) -> TurnReply:
    """Вход в проверочный проход по текущему узлу."""
    s = settings or get_settings()
    stamp = time.time() if now is None else now
    session_id = repos.ensure_open_session(conn, stamp)
    state = repos.get_session_state(conn, session_id)
    graph = CourseGraph.load(conn)

    if not graph.node_ids:
        return TurnReply(text=EMPTY_GRAPH_REPLY)

    node_id = state.current_node_id
    if node_id is None:
        return TurnReply(text=VERIFY_NO_NODE_REPLY)
    if not graph.has_node(node_id):
        # Узел убрали из графа (правка seed или БД руками) — иначе занятие
        # запиралось бы KeyError на каждом ходу.
        logger.warning("Узел %s больше не в графе, снимаю", node_id)
        post_turn(
            conn,
            session_id,
            user_text=VERIFY_KICKOFF_TEXT,
            assistant_text=VERIFY_NO_NODE_REPLY,
            state=state.model_copy(
                update={"current_node_id": None, "node_streak": 0, "last_activity": stamp}
            ),
            now=stamp,
        )
        return TurnReply(text=VERIFY_NO_NODE_REPLY)

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
            "last_activity": stamp,
        }
    )
    new_state, text, options = _issue_task(conn, graph, started, now=stamp, settings=s)
    post_turn(
        conn,
        session_id,
        user_text=VERIFY_KICKOFF_TEXT,
        assistant_text=text,
        state=new_state,
        now=stamp,
    )
    return TurnReply(text=text, options=options)
