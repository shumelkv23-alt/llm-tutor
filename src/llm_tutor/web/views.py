"""Данные страниц и API из ядра: словари для шаблонов и JSON.

HTML здесь появляется только из ``web.markdown.render`` (безопасный).
"""

import json
import logging
import sqlite3
import time
from collections.abc import Mapping, Sequence

from markupsafe import escape

from llm_tutor.config import Settings
from llm_tutor.core import explain as core_explain
from llm_tutor.core import guided
from llm_tutor.core.context import Focus, FocusSource, fence, focus_from_meta
from llm_tutor.core.lesson import (
    closed_from_route,
    course_survey_done,
    pending_survey_topic,
    survey_way_back,
)
from llm_tutor.core import project as core_project
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
from llm_tutor.llm.prompt_config import ExplainLimits, ExplainPrompts
from llm_tutor.schemas import (
    ExplainConfig,
    ExplainSession,
    ExplainTurn,
    Item,
    Project,
    ProjectBrief,
    SessionState,
    SurveyConfig,
    Topic,
)
from llm_tutor.student import diagnostic, guide, review, survey
from llm_tutor.student import explain as student_explain
from llm_tutor.student import project as student_project
from llm_tutor.web import accounts as web_accounts
from llm_tutor.web import courses as course_registry
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

# Курс не открывается по двум причинам: битая БД ученика или нечитаемые модули
# (нет каталога с seed-файлами, битый JSON). Второе приходит от `course_data` и
# раньше роняло страницу пятисоткой (аудит среза 1 курса ИСИТ): одна карточка
# не должна стоить ученику главной, профиля и страницы занятия.
COURSE_ERRORS = (sqlite3.DatabaseError, RuntimeError, SeedError, FileNotFoundError)


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
        # Повторение (срез 82): сколько занятий ждут и насколько самое просроченное
        # просрочено (секунды) — по нему «Повторить сегодня» упорядочивает курсы.
        "review_due": 0,
        "review_overdue": 0.0,
    }
    if enrolled:
        try:
            conn = pool.open(user_id, course.id)
            progress = course_progress(conn, settings=settings)
            card["modules"] = [
                module_view(module)
                for module in module_rows(conn, topic_rows(conn, settings=settings))
            ]
            card["review_due"], card["review_overdue"] = review_due_info(conn, settings=settings)
        except (*COURSE_ERRORS, OSError):
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
        elif progress.total and progress.closed >= progress.total:
            # «Пройден» — только когда в курсе есть что проходить: у курса из
            # одних неопубликованных модулей 0 из 0 — не «пройдено».
            card["status"] = "done"
        else:
            card["status"] = "in_progress"
    else:
        data, error = try_course_data(course)
        if error is not None:
            # Файлы курса не читаются (недописанный опубликованный курс): ученику
            # честнее показать «недоступен», чем пустой курс без занятий.
            logger.warning("Курс %s не читается: %s", course.id, error)
            card.update(status="error", status_label=STATUS_LABELS["error"], percent=0, topics=0)
            return card
        card["topics"] = _topic_count(data)
    card["status_label"] = STATUS_LABELS[card["status"]]
    card["percent"] = round(100 * card["closed"] / card["topics"]) if card["topics"] else 0
    return card


def _topic_count(data: CourseData) -> int:
    """Число занятий в открытых модулях курса — из seed, без БД ученика.

    Неопубликованные модули — «скоро» (срез 55): их занятий нет и в карточке
    записанного ученика, иначе числа разошлись бы. Нечитаемые модули ловит
    ``try_course_data`` у вызывающего: карточка скажет «недоступен».
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


# Подпись плашки фрагмента в чате (срез 77).
QUOTE_LABELS: dict[FocusSource, str] = {
    "theory": "Из теории",
    "task": "Из задания",
    "code": "Из моего кода",
}


def quote_view(focus: Focus) -> dict:
    """Плашка фрагмента над репликой ученика: код — моноширинным блоком.

    Текст конспекта и задания показывается дословно: это уже отрисованная
    страница, и повторный разбор Markdown исказил бы цитату (``# …`` стал бы
    заголовком, ``__len__`` — жирным ``len``).
    """
    if focus.source == "code":
        mark = fence(focus.text)
        html = str(markdown.render(f"{mark}python\n{focus.text}\n{mark}"))
    else:
        # Переносы и отступы — текстом: плашка стоит с white-space: pre-wrap
        # (финальное ревью, M-3), <br> удвоил бы переносы.
        html = "<p>" + str(escape(focus.text)) + "</p>\n"
    return {"source": focus.source, "label": QUOTE_LABELS[focus.source], "html": html}


def message_view(
    role: str, text: str, *, ts: float | None = None, quote: Focus | None = None
) -> dict:
    return {
        "role": role,
        "html": str(markdown.render(text)),
        "ts": ts,
        "quote": quote_view(quote) if quote is not None else None,
    }


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
    conn: sqlite3.Connection,
    graph: CourseGraph,
    state: SessionState,
    node_id: str,
    *,
    settings: Settings | None = None,
) -> dict:
    """Где ученик в курсе (срез 55): модуль, занятие k из m, разделы и итоговое.

    Статус раздела: ``passed`` / ``weak`` (пройден после двух ошибок) /
    ``skipped`` (заданий по разделу ещё нет) / ``current`` / ``ahead``. У темы
    без разделов список пуст, ``final`` — None.
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
        # Раздел без заданий: спрашивать по нему нечем (недописанный урок). Это не
        # «пройден» — ученик ничего не доказывал, — и не «впереди».
        if section_id in state.skipped_sections:
            return "skipped"
        return "current" if section_id == current else "ahead"

    sections = [
        {"id": section.id, "title": section.title, "status": status(section.id)}
        for section in concept.sections
    ]
    final = None
    if ids:
        if here and state.final_passed:
            final_status = "weak" if state.final_weak else "passed"
        elif not diagnostic.has_lesson_item(conn, node_id, final=True, settings=settings):
            final_status = "skipped"
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


def review_due_info(
    conn: sqlite3.Connection, *, settings: Settings, now: float | None = None
) -> tuple[int, float]:
    """Сколько занятий ждут повторения и насколько просрочено самое срочное (сек).

    Повторение выключено — ``(0, 0.0)``: на главной курса с выключенным
    повторением нет, а вкладка занятия считает своё (``review_view``).
    """
    if not review.enabled(conn):
        return 0, 0.0
    stamp = time.time() if now is None else now
    session_id = repos.get_open_session(conn)
    route = repos.get_session_state(conn, session_id).route if session_id else None
    due = review.due_lessons(conn, CourseGraph.load(conn), route, now=stamp, settings=settings)
    if not due:
        return 0, 0.0
    return len(due), max(0.0, stamp - repos.get_review_rows(conn)[due[0]].due_at)


def review_view(
    conn: sqlite3.Connection,
    graph: CourseGraph,
    state: SessionState,
    *,
    settings: Settings,
    now: float | None = None,
) -> tuple[dict, set[str]]:
    """Блок «Повторение» состояния занятия и множество занятий «пора повторить».

    ``due`` считается и при выключенном повторении: переключатель гасит разминку
    и число на главной, а вкладка работает вручную. ``lessons`` — закрытые
    занятия для «Освежить».
    """
    stamp = time.time() if now is None else now
    due = review.due_lessons(conn, graph, state.route, now=stamp, settings=settings)
    series = state.review
    active = None
    if series is not None:
        shown = series.node_id or ""
        active = {
            "kind": series.kind,
            "node_name": graph.concept(shown).name if graph.has_node(shown) else shown,
            "position": series.total - len(series.queue),
            "total": series.total,
        }
    lessons = [
        {
            "module": graph.topic_of(node_id),
            "id": node_id,
            "name": graph.concept(node_id).name,
            "due": node_id in due,
        }
        for node_id in review.closed_lessons(graph, state.route)
    ]
    block = {"enabled": review.enabled(conn), "due": len(due), "active": active, "lessons": lessons}
    return block, set(due)


def lesson_state(
    conn: sqlite3.Connection, *, settings: Settings, prompts: ExplainPrompts | None = None
) -> dict:
    """Состояние занятия для страницы: всё, что рисуется слева и в чате.

    Сессию занятия не заводим. Задание берётся из состояния сессии, а не из
    текста ответа — это источник правды для «Практики». С ``prompts`` (под замком
    ученика) висящее «Объясни новичку» превращается в сессию (``ensure_required``);
    пока оно не выполнено, конспект и задание урока не отдаются: вместо них блок
    ``explain``, а в чате справа — ветка наставника. Задание важнее анкеты
    следующего модуля.
    """
    if prompts is not None:
        core_explain.ensure_required(conn, prompts, now=time.time(), settings=settings)
    held = core_explain.blocking(conn) is not None
    pending_topic = None if held else pending_survey_topic(conn)
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
            "review": None,
            "explain": None,
        }
    graph = CourseGraph.load(conn)
    session_id = repos.get_open_session(conn)
    state = repos.get_session_state(conn, session_id) if session_id else SessionState()
    limits = prompts.limits if prompts is not None else ExplainLimits()
    explain_block = explain_view(conn, graph, state, max_turns=limits.max_student_turns)
    mentor_chat = held
    node = None
    if not held and state.current_node_id and graph.has_node(state.current_node_id):
        concept = graph.concept(state.current_node_id)
        node = {
            "id": concept.id,
            "name": concept.name,
            "description": concept.description,
            "source_url": concept.source_url,
            "theory_html": str(markdown.render(repos.get_theory(conn, concept.id) or "")),
            "lesson": lesson_view(conn, graph, state, concept.id, settings=settings),
        }
    # Идущая серия повторения: в «Практике» её задание, а не урока.
    series = state.review
    shown_item = series.item_id if series is not None and series.item_id else state.pending_item_id
    item = repos.get_item(conn, shown_item) if shown_item and not held else None
    messages = repos.get_messages(conn, session_id)[-CHAT_HISTORY:] if session_id else []
    rows = topic_rows(conn, settings=settings)
    review_block, review_due = review_view(conn, graph, state, settings=settings)
    return {
        "survey": False,
        "review": review_block,
        "explain": explain_block,
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
                "review_due": row.id in review_due,
            }
            for row in rows
        ],
        "modules": [module_view(module) for module in module_rows(conn, rows)],
        "messages": (
            # Пока висит задание «Объясни новичку», чат справа — ветка наставника, а
            # не история урока. Добровольная сессия чат урока не забирает: её наставник
            # лежит в ``explain.active.mentor`` и открывается на вкладке «Объясни».
            (explain_block["active"] or {}).get("mentor", [])
            if mentor_chat
            else [
                message_view(
                    message.role,
                    _without_task(message.content, message.meta),
                    ts=message.ts,
                    quote=focus_from_meta(message.meta) if message.role == "user" else None,
                )
                for message in messages
                if message.role in ("user", "assistant")
            ]
        ),
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


def explain_session_view(
    session: ExplainSession,
    turns: Sequence[ExplainTurn],
    config: ExplainConfig | None,
    *,
    title: str,
    max_turns: int,
    lesson_names: Mapping[str, str] | None = None,
) -> dict:
    """Сессия для вкладки «Объясни»: две ветки разговора и полоска без названий идей.

    ``messages`` — разговор с новичком (реплики ``student`` и ``novice``) плюс, для
    админки, всё остальное с пометкой ``thread``; ``mentor`` — ветка наставника
    в формате чата занятия (``user``/``assistant``). Эталон до конца сессии не уходит
    в браузер: только счёт обязательных идей. Отчёт (после завершения) — целиком,
    эталон в нём уже открыт.
    """
    done, total = (
        student_explain.required_progress(session.state, config) if config else (0, 0)
    )
    rows = [
        {
            "role": turn.role,
            "html": str(markdown.render(turn.content)),
            "ts": turn.ts,
            "thread": (
                "mentor" if turn.role == "mentor" or turn.meta.get("mentor") else "novice"
            ),
        }
        for turn in turns
    ]
    return {
        "id": session.id,
        "kind": session.kind,
        "node_id": session.concept_id,
        "topic": session.topic_id,
        "title": title,
        "status": session.status,
        "started_at": session.started_at,
        "messages": rows,
        "novice": [row for row in rows if row["thread"] == "novice"],
        "mentor": [
            {
                "role": "assistant" if row["role"] == "mentor" else "user",
                "html": row["html"],
                "ts": row["ts"],
                "quote": None,
            }
            for row in rows
            if row["thread"] == "mentor"
        ],
        "progress": {"done": done, "total": total},
        "turns_left": max(0, max_turns - session.state.student_turns),
        "report": session.report.model_dump() if session.report else None,
        # Названия занятий из отчёта (до завершения — пусто: отчёта нет).
        "lessons": (
            {
                lesson: (lesson_names or {}).get(lesson, lesson)
                for lesson in {idea.lesson for idea in session.report.ideas}
                | set(session.report.review_lessons)
                | {pitfall.lesson for pitfall in session.report.pitfalls}
            }
            if session.report
            else {}
        ),
    }


def explain_session_for(
    conn: sqlite3.Connection, session: ExplainSession, *, max_turns: int
) -> dict:
    """Вид сессии для вкладки: ``messages`` — только разговор с новичком, ``mentor`` — наставник."""
    names = {concept.id: concept.name for concept in repos.get_concepts(conn)}
    view = explain_session_view(
        session,
        repos.get_explain_turns(conn, session.id),
        repos.get_explain_configs(conn).get(session.concept_id or ""),
        title=names.get(session.concept_id or "") or f"Модуль {session.topic_id}",
        max_turns=max_turns,
        lesson_names=names if session.report else None,
    )
    view["messages"] = view.pop("novice")
    return view


def explain_view(
    conn: sqlite3.Connection, graph: CourseGraph, state: SessionState, *, max_turns: int
) -> dict:
    """Блок ``explain`` состояния занятия (спека 2026-10-27).

    ``active`` — идущая сессия, а когда задание выполнено, но «Продолжить» ещё не
    нажато, — завершённая обязательная с отчётом (``ready``). ``blocked`` — курс
    стоит: задание висит или ждёт «Продолжить».
    """
    active_session = repos.active_explain_session(conn)
    waiting = core_explain.unproceeded(conn)
    shown = active_session or waiting
    pending = student_explain.pending_lesson(conn, state.route, graph.topo_order())
    names = {concept.id: concept.name for concept in repos.get_concepts(conn)}
    important = repos.get_explain_configs(conn)
    closed = closed_from_route(state)
    history = []
    for session in repos.list_explain_sessions(conn):
        if session.status != "finished":
            continue
        report = session.report
        history.append(
            {
                "id": session.id,
                "kind": session.kind,
                "node_id": session.concept_id,
                "name": names.get(session.concept_id or "") or f"Модуль {session.topic_id}",
                "started_at": session.started_at,
                "coverage": (
                    {"done": report.required_done, "total": report.required_total}
                    if report
                    else None
                ),
            }
        )
    return {
        "active": explain_session_for(conn, shown, max_turns=max_turns) if shown else None,
        "pending": pending,
        "ready": waiting is not None and active_session is None,
        "blocked": pending is not None or waiting is not None,
        "lessons": [
            {"node_id": node, "name": names[node]}
            for node in graph.topo_order()
            if node in important and node in closed
        ],
        "history": history,
        "share": core_explain.share_enabled(conn),
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


# --- Карта знаний (срез 74) ---


def graph_view(conn: sqlite3.Connection, *, settings: Settings) -> dict:
    """Граф курса для карты знаний в профиле: занятия со статусами и связи.

    Чтение, а не ход: сессию не заводит. Узлы — открытые модули
    (``CourseGraph.load``), статусы и владение — те же, что в таблице тем.
    """
    graph = CourseGraph.load(conn)
    rows = topic_rows(conn, settings=settings)
    session_id = repos.get_open_session(conn)
    state = repos.get_session_state(conn, session_id) if session_id else SessionState()
    current = state.current_node_id if state.current_node_id and graph.has_node(state.current_node_id) else None
    modules = {row.topic_id for row in rows}
    topics = repos.get_topics(conn)
    # Модули без пройденной анкеты: пока висит анкета другого модуля, переход
    # к их занятиям сервер отклонит (``/lesson/switch`` → 409), карта это знает.
    surveyed = {topic.number for topic in topics if survey.is_completed(conn, topic.survey)}
    pending = pending_survey_topic(conn)
    return {
        "current": current,
        "survey_done": course_survey_done(conn),
        "pending_item": state.pending_item_id is not None,
        "pending_survey": pending.number if pending is not None else None,
        "modules": [
            {"number": topic.number, "title": topic.title}
            for topic in topics
            if topic.number in modules
        ],
        "nodes": [
            {
                "id": row.id,
                "name": row.name,
                "module": row.topic_id,
                "status": row.status,
                "weak": row.weak,
                "mastery": round(row.mastery * 100),
                "confident": row.confident,
                "needs_survey": row.topic_id not in surveyed,
            }
            for row in rows
        ],
        "edges": [
            {"from": src, "to": dst, "hard": hard} for src, dst, hard in graph.prerequisite_edges()
        ],
    }


# --- Проект по курсу (срез 99) ---


def brief_view(brief: ProjectBrief, names: Mapping[str, str]) -> dict:
    """Описание проекта для экрана: занятия — названиями, ссылки — как есть (только http(s))."""
    return {
        "id": brief.id,
        "title": brief.title,
        "summary": brief.summary,
        "result": brief.result,
        "lessons": [names[node] for node in brief.lessons if node in names],
        "datasets": [{"title": ds.title, "url": ds.url} for ds in brief.datasets],
    }


def _field_view(field: student_project.Field, values: Mapping[str, str]) -> dict:
    """Поле формы: подпись, правила (для кнопок в браузере; решает всё равно сервер) и значение."""
    minimum = student_project.TITLE_MIN if field.key == "title" else student_project.MIN_TEXT
    return {
        "key": field.key,
        "label": field.label,
        "required": field.required,
        "min_chars": minimum if field.required else 0,
        "max_chars": field.max_chars,
        "value": values.get(field.key, ""),
    }


# Быстрая кнопка над полем ввода: что отправить наставнику по статусу проекта (срез 103).
QUICK_REPLIES = {
    "idea": "Предложи формулировки карточки",
    "build": "Подвести итог этапа",
}


def _proposal_view(
    template: student_project.ProjectTemplate, project: Project, turn
) -> dict | None:
    """Предложение наставника в реплике: карточка идеи или итог этапа (срез 103).

    ``meta.stage`` у реплики есть только в ``build``: по нему видно, что это итог
    этапа. ``actionable`` — предложение ещё можно применить: идея не утверждена
    или этап всё ещё текущий.
    """
    if turn.role != "mentor":
        return None
    raw = turn.meta.get("proposal")
    stage_id = turn.meta.get("stage")
    if stage_id is None:
        values = student_project.clean_proposal(template, raw)
        fields = [field for field in template.card if field.key in values]
        title, actionable, kind = "Предложение для карточки", project.status == "idea", "card"
    else:
        stage = next((item for item in template.stages if item.id == stage_id), None)
        if stage is None:
            return None
        values = student_project.clean_proposal(template, raw, stage=stage)
        fields = [field for field in stage.fields if field.key in values]
        title = f"Итог этапа «{stage.title}»"
        actionable = project.status == "build" and project.state.stage == stage_id
        kind = "stage"
    if not values:
        return None
    return {
        "kind": kind,
        "stage": stage_id,
        "title": title,
        "fields": [
            {"key": f.key, "label": f.label, "value": values[f.key], "required": f.required}
            for f in fields
        ],
        "actionable": actionable,
    }


def project_title(project: Project) -> str:
    return project.state.card.get("title") or project.brief.title


def project_view(conn: sqlite3.Connection, project: Project, *, max_turns: int = 200) -> dict:
    """Проект целиком: карточка, этапы с заметками, чат и что сейчас можно нажать.

    Описание берётся из копии проекта (``projects.brief``): правка ``project.json``
    начатый проект не меняет. Реплики наставника — HTML с сервера.
    """
    template = student_project.TEMPLATES[project.template]
    state = project.state
    graph = CourseGraph.load(conn)
    names = {node: graph.concept(node).name for node in graph.topo_order()}
    stages = []
    for stage in template.stages:
        notes = state.notes.get(stage.id, {})
        if stage.id in state.done_stages:
            status = "done"
        elif stage.id == state.stage:
            status = "current"
        else:
            status = "ahead"
        stages.append(
            {
                "id": stage.id,
                "title": stage.title,
                "goal": stage.goal,
                "checklist": list(stage.checklist),
                "status": status,
                "fields": [_field_view(field, notes) for field in stage.fields],
            }
        )
    messages = []
    for turn in repos.get_project_turns(conn, project.id)[-max_turns:]:
        messages.append(
            {
                "id": turn.id,
                "role": turn.role,
                "html": str(markdown.render(turn.content)),
                "ts": turn.ts,
                "proposal": _proposal_view(template, project, turn),
            }
        )
    current = next((stage for stage in template.stages if stage.id == state.stage), None)
    return {
        "id": project.id,
        "status": project.status,
        "title": project_title(project),
        "template": template.title,
        "brief": brief_view(project.brief, names),
        "card": [_field_view(field, state.card) for field in template.card],
        "stage": state.stage,
        "stages": stages,
        "messages": messages,
        "can_approve": project.status == "idea"
        and not student_project.card_missing(template, state.card),
        "can_complete": project.status == "build"
        and current is not None
        and not student_project.notes_missing(current, state.notes.get(current.id, {})),
        "done_at": project.done_at,
        "quick_reply": QUICK_REPLIES.get(project.status),
    }


def _project_summary(project: Project) -> dict:
    template = student_project.TEMPLATES[project.template]
    stage = next((s for s in template.stages if s.id == project.state.stage), None)
    return {
        "id": project.id,
        "status": project.status,
        "title": project_title(project),
        "stage": stage.id if stage else None,
        "stage_title": stage.title if stage else None,
    }


def projects_overview(app_state, user) -> list[dict]:
    """Курсы с проектной работой и состояние проекта ученика по каждому.

    ``not_enrolled`` — не записан; ``locked`` — курс не пройден; ``choose`` — можно
    выбрать описание; ``active`` — проект идёт; ``done`` — последний проект готов;
    ``error`` — БД ученика или курс не открываются.
    """
    enrolled = set(web_accounts.enrolled_courses(app_state.accounts, user.id))
    rows = []
    for course in course_registry.visible_courses():
        project_file = course_registry.project_file(course)
        if project_file is None:
            continue
        data, _ = try_course_data(course)
        names = {node.id: node.name for node in data.nodes} if data is not None else {}
        row = {
            "id": course.id,
            "title": course.title,
            "state": "not_enrolled",
            "progress": {"closed": 0, "total": 0},
            "briefs": [brief_view(brief, names) for brief in project_file.briefs],
            "project": None,
        }
        if course.id in enrolled:
            try:
                conn = app_state.userdbs.open(user.id, course.id)
                closed, total, passed = core_project.course_passed(
                    conn, settings=app_state.settings
                )
                project = repos.current_project(conn)
            except (*COURSE_ERRORS, OSError):
                logger.exception("БД ученика %s по курсу %s не открывается", user.id, course.id)
                row["state"] = "error"
            else:
                row["progress"] = {"closed": closed, "total": total}
                row["project"] = _project_summary(project) if project else None
                if project is not None:
                    row["state"] = "done" if project.status == "done" else "active"
                else:
                    row["state"] = "choose" if passed else "locked"
        rows.append(row)
    return rows
