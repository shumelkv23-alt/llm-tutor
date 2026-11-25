"""Конспект по скелету курса (срезы 107–112): собирается сам при открытии страницы.

Скелет — граф курса: занятия в порядке ``topo_order`` (как маршрут и карта
знаний), у занятия — его разделы. В пройденном разделе — суть из конспекта
курса (``web.gist``), рядом — материал ученика: вопросы тьютору, сохранённые
фрагменты, загвоздки. Ничего не хранится отдельно: всё читается из БД ученика
(журнал — источник истины), поэтому конспект не расходится с ходами.

Пройдено: раздел занятия, которое **когда-либо закрывалось** (закрыто или
переоткрыто провалами, ``closed_at`` остался), а у текущего — пройденные и
текущий раздел. Пока по занятию висит «Объясни новичку», его блок закрыт: сервер
прячет конспект занятия и на странице занятия.

Слои: ``web`` → ``core``/``course``/``student``/``db``.
"""

from __future__ import annotations

import sqlite3

from llm_tutor.course.graph import CourseGraph
from llm_tutor.course.seed import section_text
from llm_tutor.db import repos
from llm_tutor.schemas import Concept, Route, SessionState
from llm_tutor.student import explain as student_explain
from llm_tutor.web import notes as notes_feed
from llm_tutor.web.gist import section_gist
from llm_tutor.web.markdown import render

STATUS_LABELS: dict[str, str] = {
    "passed": "пройдено",
    "weak": "пройдено с ошибками",
    "reopened": "открыто снова",
    "current": "идёт сейчас",
    "ahead": "впереди",
}

LOCKED_NOTE = "Откроется после объяснения новичку."

# Материал ученика, который раскладывается по разделам и занятиям.
_MATERIAL = ("questions", "fragments", "mistakes")


def lesson_statuses(route: Route | None, current: str | None) -> dict[str, str]:
    """Статус занятия для конспекта: закрыто > идёт сейчас > открыто снова.

    Занятий без записи в словаре нет — они «впереди».
    """
    statuses: dict[str, str] = {}
    for step in route.steps if route is not None else []:
        if step.status == "closed":
            statuses[step.concept_id] = "weak" if step.weak else "passed"
        elif step.closed_at is not None:
            statuses[step.concept_id] = "reopened"
    if current is not None and statuses.get(current) not in ("passed", "weak"):
        statuses[current] = "current"
    return statuses


def _state(conn: sqlite3.Connection) -> SessionState:
    session_id = repos.get_open_session(conn)
    return repos.get_session_state(conn, session_id) if session_id is not None else SessionState()


def _ever_closed(route: Route | None) -> set[str]:
    if route is None:
        return set()
    return {
        step.concept_id
        for step in route.steps
        if step.status == "closed" or step.closed_at is not None
    }


def _empty_section(section_id: str, title: str) -> dict:
    return {
        "id": section_id,
        "title": title,
        "passed": False,
        "gist_html": None,
        **{key: [] for key in _MATERIAL},
    }


def _lesson(concept: Concept, status: str, topics: dict[int, str]) -> dict:
    title = topics.get(concept.topic_id)
    return {
        "node_id": concept.id,
        "name": concept.name,
        "module": f"Модуль {concept.topic_id}" + (f" · {title}" if title else ""),
        "status": status,
        "status_label": STATUS_LABELS[status],
        "locked": False,
        "sections": [],
        "explain": [],
        **{key: [] for key in _MATERIAL},
        "count": 0,
    }


def _outside() -> dict:
    return {
        "node_id": None,
        "name": notes_feed.NO_LESSON,
        "module": None,
        "status": "ahead",
        "status_label": "",
        "locked": False,
        "sections": [],
        "explain": [],
        **{key: [] for key in _MATERIAL},
        "count": 0,
    }


def _sections(
    conn: sqlite3.Connection,
    concept: Concept,
    passed: set[str],
) -> list[dict]:
    theory = repos.get_theory(conn, concept.id) if passed else None
    sections = []
    for section in concept.sections:
        block = _empty_section(section.id, section.title)
        if section.id in passed:
            block["passed"] = True
            body = section_text(theory, section.title) if theory else None
            gist = section_gist(body) if body else None
            block["gist_html"] = render(gist) if gist else None
        sections.append(block)
    return sections


def _place(lesson: dict, kind: str, section_id: str | None, entry: dict) -> None:
    """Кладёт запись в раздел занятия, а без раздела (или с чужим) — в блок занятия."""
    target = next((s for s in lesson["sections"] if s["id"] == section_id), None)
    (target or lesson)[kind].append(entry)
    lesson["count"] += 1


def course_notebook(
    conn: sqlite3.Connection,
    *,
    skeleton: bool = True,
    query: str | None = None,
    node_id: str | None = None,
) -> dict:
    """Конспект одного курса ученика.

    ``skeleton=False`` — только занятия, где есть материал ученика (режим «Все
    курсы»): без строк «впереди». ``query`` — поиск, ``node_id`` — одно занятие.
    """
    graph = CourseGraph.load(conn)
    order = graph.topo_order()
    topics = {topic.number: topic.title for topic in repos.get_topics(conn)}
    state = _state(conn)
    statuses = lesson_statuses(state.route, state.current_node_id)
    ever = _ever_closed(state.route)
    locked = student_explain.pending_lesson(conn, state.route, order)

    blocks: dict[str, dict] = {}
    for node in order:
        concept = graph.concept(node)
        lesson = _lesson(concept, statuses.get(node, "ahead"), topics)
        if node == locked:
            lesson["locked"] = True
        else:
            if node in ever:
                passed = {section.id for section in concept.sections}
            elif node == state.current_node_id:
                passed = {*state.passed_sections, *state.weak_sections}
                if state.section_id:
                    passed.add(state.section_id)
            else:
                passed = set()
            lesson["sections"] = _sections(conn, concept, passed)
        blocks[node] = lesson

    outside = _outside()
    entries = notes_feed.question_entries(conn, query=query, node_id=node_id)
    # Конспект читается сверху вниз: внутри раздела — по порядку вопросов.
    for entry in reversed(entries):
        lesson = blocks.get(entry["node_id"] or "")
        if lesson is None:
            outside["questions"].append(entry)
            outside["count"] += 1
            continue
        if lesson["locked"]:
            continue
        _place(lesson, "questions", entry["section_id"], entry)

    lessons: list[dict] = []
    ahead: list[str] = []
    searching = query is not None or not skeleton
    for node in order:
        lesson = blocks[node]
        if node_id is not None and node != node_id:
            continue
        if searching:
            if lesson["count"]:
                lessons.append(lesson)
            continue
        if lesson["status"] == "ahead" and not lesson["count"] and not lesson["locked"]:
            ahead.append(lesson["name"])
            continue
        lessons.append(lesson)
    show_outside = outside["count"] and node_id is None
    return {
        # Скелет (суть, «ещё впереди», «Впереди») — только без поиска и не в «Все курсы».
        "skeleton": not searching,
        "lessons": lessons,
        "ahead": ahead,
        "outside": outside if show_outside else None,
        "count": sum(lesson["count"] for lesson in lessons) + (outside["count"] if show_outside else 0),
        "closed": sum(1 for status in statuses.values() if status in ("passed", "weak")),
        "total": len(order),
    }
