"""Данные страниц и API из ядра: словари для шаблонов и JSON.

HTML здесь появляется только из ``web.markdown.render`` (безопасный).
"""

import sqlite3

from llm_tutor.config import Settings
from llm_tutor.core.overview import course_progress, topic_rows
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.schemas import Item, SessionState
from llm_tutor.student import survey
from llm_tutor.web import markdown
from llm_tutor.web.courses import Course
from llm_tutor.web.userdb import UserDBPool

STATUS_LABELS = {
    "available": "Не начат",
    "survey": "Анкета",
    "in_progress": "В процессе",
    "done": "Пройден",
}


def course_card(
    course: Course,
    *,
    user_id: int,
    enrolled: bool,
    pool: UserDBPool,
    settings: Settings,
) -> dict:
    """Карточка курса: прогресс читается из БД ученика, только если он записан."""
    card = {
        "id": course.id,
        "title": course.title,
        "subtitle": course.subtitle,
        "description": course.description,
        "source_url": course.source_url,
        "href": f"/lesson/{course.id}",
        "enrolled": enrolled,
        "topics": None,
        "closed": 0,
        "current_topic": None,
        "status": "available",
    }
    if enrolled:
        progress = course_progress(pool.open(user_id, course.id), settings=settings)
        card.update(topics=progress.total, closed=progress.closed, current_topic=progress.current_name)
        if not progress.survey_done:
            card["status"] = "survey"
        elif progress.closed >= progress.total:
            card["status"] = "done"
        else:
            card["status"] = "in_progress"
    else:
        card["topics"] = _topic_count(course)
    card["status_label"] = STATUS_LABELS[card["status"]]
    card["percent"] = round(100 * card["closed"] / card["topics"]) if card["topics"] else 0
    return card


def _topic_count(course: Course) -> int:
    """Число тем курса из seed — без создания БД ученика."""
    from llm_tutor.course.seed import load_seed_data

    return len(load_seed_data(course.seed_path).nodes)


# --- Анкета ---


def survey_progress(answers: list[int]) -> "survey.Progress":
    """Ход анкеты из ответов клиента; недопустимая последовательность — ``ValueError``."""
    progress = survey.Progress()
    for index in answers:
        if isinstance(index, bool) or not isinstance(index, int):
            raise ValueError(f"Ответ анкеты — номер варианта, а не {index!r}")
        progress = progress.answer(index)
    return progress


def survey_question(progress: "survey.Progress") -> dict:
    """Текущий вопрос анкеты (вызывать на незаконченной анкете)."""
    key = progress.next_key()
    assert key is not None
    if key == survey.LEVEL_KEY:
        question, example = survey.LEVEL_QUESTION, None
    else:
        block = survey.block_by_key(key)
        question, example = block.question, block.example
    position = progress.position()
    return {
        "key": key,
        "step": progress.step,
        "question": question,
        "example": example,
        "options": list(survey.options_for(key)),
        "number": position[0] if position else None,
        "total": position[1] if position else None,
        "can_back": progress.step > 0,
    }


def survey_summary(final: dict[str, int]) -> dict:
    """Сводка «как я тебя понял»: блок → ответ, и есть ли заявленное знакомым."""
    return {
        "items": [
            {"title": block.title, "answer": survey.SELF_LEVELS[final[block.key]]}
            for block in survey.BLOCKS
        ],
        "claimed": any(index == survey.CONFIDENT_INDEX for index in final.values()),
    }


# --- Занятие ---

# Сколько последних реплик сессии отдавать в чат.
CHAT_HISTORY = 60


def message_view(role: str, text: str, *, ts: float | None = None) -> dict:
    return {"role": role, "html": str(markdown.render(text)), "ts": ts}


def item_view(item: Item) -> dict:
    """Задание для «Практики». У задания с кодом — всё, что нужно браузеру для
    запуска: заготовка, подготовка данных и проверки (они видны ученику в
    исходниках страницы — цена проверки на его стороне, спека §8)."""
    view = {
        "id": item.id,
        "type": item.answer_type,
        "prompt_html": str(markdown.render(item.prompt)),
        "options": list(item.options),
        "runnable": item.runnable,
    }
    if item.runnable:
        view.update(starter=item.starter or "", setup=item.setup or "", tests=item.tests or "")
    return view


def lesson_state(conn: sqlite3.Connection, *, settings: Settings) -> dict:
    """Состояние занятия для страницы: всё, что рисуется слева и в чате.

    Чтение, а не ход: сессию не заводим. Задание берётся из состояния сессии,
    а не из текста ответа — это источник правды для «Практики».
    """
    if not survey.is_completed(conn):
        return {"survey": True, "node": None, "item": None, "topics": [], "messages": []}
    graph = CourseGraph.load(conn)
    session_id = repos.get_open_session(conn)
    state = repos.get_session_state(conn, session_id) if session_id else SessionState()
    node = None
    if state.current_node_id and graph.has_node(state.current_node_id):
        concept = graph.concept(state.current_node_id)
        node = {
            "id": concept.id,
            "name": concept.name,
            "description": concept.description,
            "source_url": concept.source_url,
            "theory_html": str(markdown.render(repos.get_theory(conn, concept.id) or "")),
        }
    item = repos.get_item(conn, state.pending_item_id) if state.pending_item_id else None
    messages = repos.get_messages(conn, session_id)[-CHAT_HISTORY:] if session_id else []
    rows = topic_rows(conn, settings=settings)
    return {
        "survey": False,
        "node": node,
        "item": item_view(item) if item is not None else None,
        "mode": "verify" if state.mode == "verify" else "lesson",
        "phase": state.phase,
        "streak": state.node_streak,
        "streak_target": settings.guide_success_streak,
        "closed": sum(1 for row in rows if row.status == "closed"),
        "topics": [{"id": row.id, "name": row.name, "status": row.status} for row in rows],
        "messages": [
            message_view(message.role, message.content, ts=message.ts)
            for message in messages
            if message.role in ("user", "assistant")
        ],
    }
