"""Данные страниц и API из ядра: словари для шаблонов и JSON.

HTML здесь появляется только из ``web.markdown.render`` (безопасный).
"""

import json
import logging
import sqlite3

from llm_tutor.config import Settings
from llm_tutor.core import guided
from llm_tutor.core.lesson import course_survey_done, pending_survey_topic, survey_way_back
from llm_tutor.core.overview import (
    ModuleRow,
    course_progress,
    module_rows,
    route_overview,
    topic_rows,
)
from llm_tutor.course.graph import CourseGraph
from llm_tutor.course.seed import Course as CourseData
from llm_tutor.course.seed import SeedError
from llm_tutor.db import repos
from llm_tutor.schemas import Item, SessionState, SurveyConfig, Topic
from llm_tutor.student import guide, survey
from llm_tutor.web import markdown

logger = logging.getLogger(__name__)
from llm_tutor.web.courses import Course, try_course_data
from llm_tutor.web.userdb import UserDBPool

STATUS_LABELS = {
    "error": "Недоступен",
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
        # Черновик виден только админу (предпросмотр) — страница его помечает.
        "published": course.status == "published",
        # Прогресс по модулям (срез 55): «Модуль N — закрыто k из m» / «скоро».
        "modules": [],
    }
    if enrolled:
        try:
            conn = pool.open(user_id, course.id)
            progress = course_progress(conn, settings=settings)
            card["modules"] = [
                module_view(module)
                for module in module_rows(conn, topic_rows(conn, settings=settings))
            ]
        except (sqlite3.DatabaseError, RuntimeError, SeedError, OSError):
            # Одна битая БД (или нечитаемый курс) не должна ронять каталог:
            # курс помечается недоступным, остальные показываются как обычно.
            logger.exception("БД ученика %s по курсу %s не открывается", user_id, course.id)
            card.update(status="error", status_label=STATUS_LABELS["error"], percent=0)
            data, _ = try_course_data(course)
            card["topics"] = _topic_count(data) if data is not None else 0
            return card
        card.update(topics=progress.total, closed=progress.closed, current_topic=progress.current_name)
        if not progress.survey_done:
            card["status"] = "survey"
        elif progress.closed >= progress.total:
            card["status"] = "done"
        else:
            card["status"] = "in_progress"
    else:
        data, error = try_course_data(course)
        if error is not None:
            # Файлы курса не читаются (недописанный опубликованный курс): ученику
            # честнее показать «недоступен», чем пустой курс без занятий.
            logger.warning("Курс %s не читается: %s", course.id, error)
            card.update(status="error", status_label=STATUS_LABELS["error"], percent=0)
            return card
        card["topics"] = _topic_count(data)
    card["status_label"] = STATUS_LABELS[card["status"]]
    card["percent"] = round(100 * card["closed"] / card["topics"]) if card["topics"] else 0
    return card


def _topic_count(data: CourseData) -> int:
    """Число занятий в открытых модулях курса — из seed, без БД ученика.

    Неопубликованные модули — «скоро» (срез 55): их занятий нет и в карточке
    записанного ученика, иначе числа разошлись бы.
    """
    published = {topic.number for topic in data.topics if topic.published}
    return sum(1 for node in data.nodes if node.topic_id in published)


# --- Анкета ---


def survey_progress(config: SurveyConfig, answers: list[int]) -> "survey.Progress":
    """Ход анкеты модуля из ответов клиента; недопустимая последовательность — ``ValueError``."""
    progress = survey.Progress(config)
    for index in answers:
        if isinstance(index, bool) or not isinstance(index, int):
            raise ValueError(f"Ответ анкеты — номер варианта, а не {index!r}")
        progress = progress.answer(index)
    return progress


def survey_question(progress: "survey.Progress") -> dict:
    """Текущий вопрос анкеты (вызывать на незаконченной анкете)."""
    config = progress.config
    key = progress.next_key()
    assert key is not None
    if key == config.level_key:
        question, example = config.level_question, None
    else:
        block = config.block(key)
        question, example = block.question, block.example
    position = progress.position()
    return {
        "key": key,
        "step": progress.step,
        "question": question,
        "example": example,
        "options": list(survey.options_for(config, key)),
        "number": position[0] if position else None,
        "total": position[1] if position else None,
        "can_back": progress.step > 0,
    }


def survey_summary(config: SurveyConfig, final: dict[str, int]) -> dict:
    """Сводка «как я тебя понял»: блок → ответ, и есть ли заявленное знакомым."""
    return {
        "items": [
            {"title": block.title, "answer": survey.SELF_LEVELS[final[block.key]]}
            for block in config.blocks
        ],
        "claimed": any(index == survey.CONFIDENT_INDEX for index in final.values()),
    }


def topic_view(topic: Topic) -> dict:
    """Модуль для страницы: номер, название, вводная."""
    return {"number": topic.number, "title": topic.title, "intro": topic.intro}


# --- Занятие ---

# Сколько последних реплик сессии отдавать в чат.
CHAT_HISTORY = 60


def message_view(role: str, text: str, *, ts: float | None = None) -> dict:
    return {"role": role, "html": str(markdown.render(text)), "ts": ts}


FINAL_LABEL = "Итоговое задание"


def item_part(item: Item, graph: CourseGraph | None) -> str | None:
    """Подпись задания в «Практике»: «Раздел j: …» или «Итоговое задание» (срез 55)."""
    if item.final:
        return FINAL_LABEL
    if item.section is None or graph is None:
        return None
    for node_id in item.concept_weights:
        if not graph.has_node(node_id):
            continue
        for number, section in enumerate(graph.concept(node_id).sections, 1):
            if section.id == item.section:
                return f"Раздел {number}: {section.title}"
    return None


def item_view(item: Item, graph: CourseGraph | None = None) -> dict:
    """Задание для «Практики». У задания с кодом — всё, что нужно браузеру для
    запуска: заготовка, подготовка данных и проверки (они видны ученику в
    исходниках страницы — цена проверки на его стороне, спека §8)."""
    view = {
        "id": item.id,
        "type": item.answer_type,
        "prompt_html": str(markdown.render(item.prompt)),
        "options": list(item.options),
        "runnable": item.runnable,
        "part": item_part(item, graph),
    }
    if item.runnable:
        view.update(starter=item.starter or "", setup=item.setup or "", tests=item.tests or "")
    return view


# Отметка вместо задания в чате: само задание — в «Практике».
NEW_TASK_NOTE = "📝 Задание — во вкладке «Практика»."


def _without_task(content: str, meta: str | None) -> str:
    """Реплика тьютора для истории чата: задание в конце заменено отметкой."""
    try:
        chars = int(json.loads(meta).get("task_chars", 0)) if meta else 0
    except (ValueError, TypeError, AttributeError):
        chars = 0
    if not 0 < chars <= len(content):
        return content
    lead = content[:-chars].rstrip()
    return f"{lead}\n\n{NEW_TASK_NOTE}" if lead else NEW_TASK_NOTE


def lesson_button(state: SessionState) -> dict | None:
    """Кнопка урока частями (срез 28): «Дальше» к следующей части или «Проверим».

    Берётся из состояния сессии, а не из ответа хода: переживает перезагрузку
    страницы. Нет урока частями или висит задание — кнопки нет.
    """
    if state.current_node_id is None or state.pending_item_id is not None:
        return None
    if not state.lesson_parts:
        return None
    last = state.lesson_part >= len(state.lesson_parts)
    return {
        "label": guided.CHECK_LABEL if last else guided.NEXT_LABEL,
        "check": last,
        "node_id": state.current_node_id,
        "part": state.lesson_part,
    }


def lesson_view(
    conn: sqlite3.Connection, graph: CourseGraph, state: SessionState, node_id: str
) -> dict:
    """Где ученик в курсе (срез 55): модуль, занятие k из m, разделы и итоговое.

    Статус раздела: ``passed`` / ``weak`` (пройден после двух ошибок) /
    ``current`` / ``ahead``. У темы без разделов список пуст, ``final`` — None.
    """
    module = graph.topic_of(node_id)
    topic = repos.get_topic(conn, module)
    lessons = [node for node in graph.topo_order() if graph.topic_of(node) == module]
    concept = graph.concept(node_id)
    ids = [section.id for section in concept.sections]
    here = state.current_node_id == node_id
    current = guide.current_section(state, ids) if ids and here else (ids[0] if ids else None)

    def status(section_id: str) -> str:
        if not here:
            return "current" if section_id == current else "ahead"
        if section_id in state.weak_sections:
            return "weak"
        if section_id in state.passed_sections:
            return "passed"
        return "current" if section_id == current else "ahead"

    sections = [
        {"id": section.id, "title": section.title, "status": status(section.id)}
        for section in concept.sections
    ]
    final = None
    if ids:
        if here and state.final_passed:
            final_status = "weak" if state.final_weak else "passed"
        else:
            final_status = "current" if here and current is None else "ahead"
        final = {"title": FINAL_LABEL, "status": final_status}
    return {
        "module": {"number": module, "title": topic.title if topic else f"Модуль {module}"},
        "index": lessons.index(node_id) + 1,
        "total": len(lessons),
        "sections": sections,
        "section_index": ids.index(current) + 1 if here and current in ids else None,
        "final": final,
    }


def lesson_state(conn: sqlite3.Connection, *, settings: Settings) -> dict:
    """Состояние занятия для страницы: всё, что рисуется слева и в чате.

    Чтение, а не ход: сессию не заводим. Задание берётся из состояния сессии,
    а не из текста ответа — это источник правды для «Практики».
    """
    pending_topic = pending_survey_topic(conn)
    if pending_topic is not None:
        back = survey_way_back(conn)
        return {
            "survey": True,
            "survey_topic": topic_view(pending_topic),
            # Анкета модуля N > 1: путь назад к теме модуля с пройденной анкетой.
            "survey_back": {"node_id": back[0], "module": back[1].number} if back else None,
            "node": None,
            "item": None,
            "topics": [],
            "messages": [],
        }
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
            "lesson": lesson_view(conn, graph, state, concept.id),
        }
    item = repos.get_item(conn, state.pending_item_id) if state.pending_item_id else None
    messages = repos.get_messages(conn, session_id)[-CHAT_HISTORY:] if session_id else []
    rows = topic_rows(conn, settings=settings)
    return {
        "survey": False,
        "node": node,
        "item": item_view(item, graph) if item is not None else None,
        "mode": "verify" if state.mode == "verify" else "lesson",
        "phase": state.phase,
        "lesson_button": lesson_button(state),
        "streak": state.node_streak,
        # Та же цель, что у ядра: у прохода «закрой тему» серия длиннее (аудит среза 51, L1).
        "streak_target": guide.streak_target(
            state,
            settings,
            len(node["lesson"]["sections"]) if node is not None else 0,
        ),
        # Как в карточке курса: текущая тема тоже может быть закрытой.
        "closed": course_progress(conn, settings=settings).closed,
        "topics": [
            {
                "id": row.id,
                "name": row.name,
                "status": row.status,
                "module": row.topic_id,
                "weak": row.weak,
            }
            for row in rows
        ],
        "modules": [module_view(module) for module in module_rows(conn, rows)],
        "messages": [
            message_view(message.role, _without_task(message.content, message.meta), ts=message.ts)
            for message in messages
            if message.role in ("user", "assistant")
        ],
    }


# --- Главная и профиль ---

# Сколько ближайших шагов маршрута показывать.
NEXT_STEPS = 5

TOPIC_STATUS_LABELS = {
    "closed": "Закрыта",
    "current": "Текущая",
    "claimed": "Знакомая — проверим",
    "available": "Доступна",
    "ahead": "Впереди",
}


# Тема закрыта после двух ошибок (срез 28): засчитана, но к ней вернёмся.
WEAK_LABEL = "Слабое место"


def module_view(module: ModuleRow) -> dict:
    """Модуль для маршрута и профиля."""
    return {
        "number": module.number,
        "title": module.title,
        "closed": module.closed,
        "total": module.total,
        "survey_done": module.survey_done,
        "published": module.published,
    }


def solved_items(conn: sqlite3.Connection) -> int:
    """Сколько разных заданий решено верно (по журналу, а не по словам модели)."""
    row = conn.execute(
        "SELECT COUNT(DISTINCT item_id) FROM events WHERE item_id IS NOT NULL AND result >= 0.5"
    ).fetchone()
    return int(row[0])


def next_steps(conn: sqlite3.Connection, *, settings: Settings) -> list[dict]:
    """Ближайшие незакрытые шаги маршрута (текущий — первым)."""
    graph = CourseGraph.load(conn)
    route = route_overview(conn, settings=settings)
    pending = [step for step in route.steps if step.status != "closed"]
    pending.sort(key=lambda step: step.status != "current")
    return [
        {
            "id": step.concept_id,
            "name": graph.concept(step.concept_id).name,
            "status": step.status,
            "label": TOPIC_STATUS_LABELS.get(step.status, ""),
        }
        for step in pending[:NEXT_STEPS]
    ]


def learning_view(conn: sqlite3.Connection, *, settings: Settings) -> dict:
    """Обучение по курсу для главной и профиля: анкета, темы, шаги, итоги."""
    rows = topic_rows(conn, settings=settings)
    return {
        "survey_done": course_survey_done(conn),
        "self_assessment": [
            {"title": title, "answer": answer}
            for topic in repos.get_topics(conn)
            for title, answer in survey.self_assessment(conn, topic.survey).items()
        ],
        "topics": [
            {
                "id": row.id,
                "name": row.name,
                "status": row.status,
                "label": WEAK_LABEL if row.weak else TOPIC_STATUS_LABELS.get(row.status, ""),
                "weak": row.weak,
                "module": row.topic_id,
                "mastery": round(row.mastery * 100),
                "confidence": "уверенно" if row.confident else "мало данных",
            }
            for row in rows
        ],
        "modules": [module_view(module) for module in module_rows(conn, rows)],
        "next_steps": next_steps(conn, settings=settings),
        "solved": solved_items(conn),
    }
