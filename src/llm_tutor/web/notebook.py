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
from functools import lru_cache

from markupsafe import Markup

from llm_tutor.course.graph import CourseGraph
from llm_tutor.course.seed import main_concept, section_texts
from llm_tutor.db import repos
from llm_tutor.schemas import Concept, Item, Note, Route, SessionState
from llm_tutor.student import explain as student_explain
from llm_tutor.student.diagnostic import SUCCESS_SCORE
from llm_tutor.web import notes as notes_feed
from llm_tutor.web.gist import clip_sentences, section_gist
from llm_tutor.web.markdown import render

STATUS_LABELS: dict[str, str] = {
    "passed": "пройдено",
    "weak": "пройдено с ошибками",
    "reopened": "открыто снова",
    "current": "идёт сейчас",
    "ahead": "впереди",
}

LOCKED_NOTE = "Откроется после объяснения новичку."

# Загвоздка «ошибка в задании»: условие, ответ ученика и как верно — коротко.
MISTAKE_PROMPT_CHARS = 120
MISTAKE_GIVEN_CHARS = 160
MISTAKE_CORRECT_CHARS = 200

# Материал ученика, который раскладывается по разделам и занятиям.
_MATERIAL = ("questions", "fragments", "mistakes")


def only_lesson(book: dict, node_id: str) -> dict:
    """Конспект одного занятия из уже собранного курса (фильтр «Тема»).

    Страница строит конспект курса один раз: чипам тем нужен весь курс, а
    повторная сборка ради одного занятия удваивала цену страницы (аудит ветки).
    """
    lessons = [lesson for lesson in book["lessons"] if lesson["node_id"] == node_id]
    return {
        **book,
        "lessons": lessons,
        "ahead": [],
        "outside": None,
        "count": sum(lesson["count"] for lesson in lessons),
        "hits": sum(lesson.get("hits", 0) for lesson in lessons),
    }


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
        "gist": None,
        "gist_html": None,
        "matched": False,
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
    gists = theory_gists(theory) if theory else {}
    sections = []
    for section in concept.sections:
        block = _empty_section(section.id, section.title)
        if section.id in passed:
            block["passed"] = True
            block["gist"], block["gist_html"] = gists.get(section.title, (None, None))
        sections.append(block)
    return sections


@lru_cache(maxsize=512)
def theory_gists(theory: str) -> dict[str, tuple[str | None, Markup | None]]:
    """Суть каждого раздела конспекта занятия: заголовок → (Markdown, HTML).

    Кэш по тексту конспекта: разбор Markdown — основная цена страницы (десятки
    занятий), а конспект курса между открытиями не меняется. Правка курса
    меняет текст — и ключ кэша. Результат не изменяют: он общий.
    """
    gists: dict[str, tuple[str | None, Markup | None]] = {}
    for title, body in section_texts(theory).items():
        gist = section_gist(body) if body else None
        gists[title] = (gist, render(gist) if gist else None)
    return gists


def fragment_view(note: Note) -> dict:
    """Сохранённый фрагмент для конспекта: цитата, комментарий, откуда взят."""
    return {
        "id": note.id,
        "quote": note.quote,
        "comment": (note.comment or "").strip() or None,
        "source": notes_feed.SOURCE_LABELS.get(note.source, note.source),
        "when": notes_feed._format_ts(note.created_at),
    }


def _short(text: str | None, limit: int) -> str | None:
    """Первая непустая строка текста, обрезанная по предложениям."""
    for line in (text or "").splitlines():
        if line.strip():
            return clip_sentences(line.strip(), limit)
    return None


def _correct_answer(item: Item) -> str | None:
    """Как верно: текст верного варианта, эталон открытого ответа; у кода — нет."""
    if item.runnable:
        return None
    if item.answer_type == "choice":
        try:
            return item.options[int(item.answer or "")]
        except (ValueError, IndexError):
            return None
    return item.answer or None


def task_mistakes(conn: sqlite3.Connection) -> list[dict]:
    """Ошибки в заданиях — по записи на задание, последняя неверная попытка.

    Источник — журнал: события с ``item_id`` (не «Объясни новичку»), свёрнутые по
    ``(item_id, ts)`` — ядро пишет событие на каждый концепт задания. Неверно —
    ``result < SUCCESS_SCORE``; верный ответ позже ставит «потом решено верно».
    Ответ ученика — его реплика с тем же ``ts``. Задание, которого больше нет в
    банке или которое погашено (убрано из курса), пропускается.
    """
    rows = conn.execute(
        "SELECT item_id, ts, result FROM events WHERE id IN ("
        "  SELECT MIN(id) FROM events WHERE item_id IS NOT NULL AND source != 'explain'"
        "  GROUP BY item_id, ts) "
        "ORDER BY ts, id"
    ).fetchall()
    last_wrong: dict[int, float] = {}
    solved: dict[int, bool] = {}
    for row in rows:
        if row["result"] < SUCCESS_SCORE:
            last_wrong[row["item_id"]] = row["ts"]
            solved[row["item_id"]] = False
        elif row["item_id"] in last_wrong:
            solved[row["item_id"]] = True
    mistakes = []
    for item_id, ts in sorted(last_wrong.items(), key=lambda pair: pair[1]):
        item = repos.get_item(conn, item_id)
        # Погашенное задание (убрано из seed) — больше не часть курса.
        if item is None or not item.active:
            continue
        message = conn.execute(
            "SELECT content FROM messages WHERE role = 'user' AND ts = ? ORDER BY id DESC LIMIT 1",
            (ts,),
        ).fetchone()
        text = (message["content"] if message is not None else "") or ""
        code = item.runnable or notes_feed.TEST_MARK in text
        mistakes.append(
            {
                "item_id": item.id,
                "node_id": main_concept(item),
                "section_id": None if item.final else item.section,
                "final": item.final,
                "prompt": _short(item.prompt, MISTAKE_PROMPT_CHARS) or "",
                "given": "код не прошёл проверку"
                if code
                else clip_sentences(" ".join(text.split()), MISTAKE_GIVEN_CHARS),
                "correct": None
                if code
                else (
                    clip_sentences(" ".join(answer.split()), MISTAKE_CORRECT_CHARS)
                    if (answer := _correct_answer(item))
                    else None
                ),
                "solved_later": solved[item_id],
                "ts": ts,
                "prompt_html": render(prompt) if (prompt := _short(item.prompt, MISTAKE_PROMPT_CHARS)) else None,
            }
        )
        # Вариант choice — текст курса в Markdown; эталон короткого ответа — имя или
        # число (``__getitem__``), Markdown превратил бы его в жирное «getitem».
        correct = mistakes[-1]["correct"]
        mistakes[-1]["correct_html"] = (
            render(correct) if correct and item.answer_type == "choice" else None
        )
    return mistakes


def explain_snags(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """Промахи «Объясни новичку» по занятиям — из последней завершённой сессии.

    Занятие — у каждой идеи и загвоздки отчёта (``lesson``), а не у сессии:
    старые модульные сессии (``concept_id = NULL``) несут идеи разных занятий.
    Для занятия берётся самая поздняя завершённая сессия, где оно встречается.
    Неверная идея — «Ошибся, объясняя», обязательная пропущенная или неполная
    — «Упустил» (обязательность — по текущему эталону; идеи вне эталона не
    показываются), пропущенная ловушка — «Не заметил ловушку» с фразой новичка.
    """
    configs = repos.get_explain_configs(conn)
    seen: set[str] = set()
    snags: dict[str, list[dict]] = {}
    sessions = sorted(
        (s for s in repos.list_explain_sessions(conn) if s.status == "finished" and s.report),
        key=lambda s: (s.ended_at or s.updated_at, s.id),
        reverse=True,
    )
    for session in sessions:
        report = session.report
        lessons = {idea.lesson for idea in report.ideas} | {p.lesson for p in report.pitfalls}
        fresh = lessons - seen
        seen |= lessons
        for idea in report.ideas:
            if idea.lesson not in fresh:
                continue
            config = configs.get(idea.lesson)
            reference = next((i for i in config.ideas if i.id == idea.id), None) if config else None
            text = idea.reference or (reference.text if reference else None)
            if idea.state == "wrong" and text:
                snags.setdefault(idea.lesson, []).append(_snag("wrong", text))
            elif idea.state in ("missing", "partial") and reference is not None and reference.required:
                snags.setdefault(idea.lesson, []).append(_snag("missed", text or reference.text))
        for pitfall in report.pitfalls:
            if pitfall.lesson not in fresh or pitfall.outcome not in ("missed", "unclear"):
                continue
            config = configs.get(pitfall.lesson)
            known = next((p for p in config.pitfalls if p.id == pitfall.id), None) if config else None
            text = pitfall.fix or (known.fix if known else None)
            if text:
                snags.setdefault(pitfall.lesson, []).append(
                    _snag("pitfall", text, known.belief if known else None)
                )
    return snags


EXPLAIN_LABELS = {
    "wrong": "Ошибся, объясняя:",
    "missed": "Упустил:",
    "pitfall": "Не заметил ловушку:",
}


def _snag(kind: str, text: str, belief: str | None = None) -> dict:
    return {"kind": kind, "label": EXPLAIN_LABELS[kind], "text": text, "belief": belief}


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

    # Фрагменты (заметки ветки golos) — все, по порядку сохранения.
    for note in reversed(repos.list_notes(conn, limit=None)):
        if node_id is not None and note.concept_id != node_id:
            continue
        if not notes_feed._matches(query, note.quote, note.comment):
            continue
        entry = fragment_view(note)
        lesson = blocks.get(note.concept_id or "")
        if lesson is None:
            outside["fragments"].append(entry)
            outside["count"] += 1
        elif not lesson["locked"]:
            _place(lesson, "fragments", note.section_id, entry)

    # Загвоздки: ошибки в заданиях — в раздел задания, итоговое — на занятие.
    for mistake in task_mistakes(conn):
        if node_id is not None and mistake["node_id"] != node_id:
            continue
        if not notes_feed._matches(query, mistake["prompt"], mistake["given"], mistake["correct"]):
            continue
        lesson = blocks.get(mistake["node_id"] or "")
        if lesson is None:
            outside["mistakes"].append(mistake)
            outside["count"] += 1
        elif not lesson["locked"]:
            _place(lesson, "mistakes", mistake["section_id"], mistake)

    # Загвоздки «Объясни новичку» — на уровень занятия (идеи не делятся на разделы).
    for lesson_id, snags in explain_snags(conn).items():
        lesson = blocks.get(lesson_id)
        if lesson is None or lesson["locked"] or (node_id is not None and lesson_id != node_id):
            continue
        for snag in snags:
            if notes_feed._matches(query, snag["text"], snag["belief"]):
                lesson["explain"].append(snag)
                lesson["count"] += 1

    # Поиск по сути пройденных разделов: совпавший раздел показывается с сутью.
    hits = 0
    if query is not None:
        for lesson in blocks.values():
            for section in lesson["sections"]:
                if section["gist"] and notes_feed._matches(query, section["gist"]):
                    section["matched"] = True
                    lesson["hits"] = lesson.get("hits", 0) + 1

    lessons: list[dict] = []
    ahead: list[str] = []
    searching = query is not None or not skeleton
    for node in order:
        lesson = blocks[node]
        if node_id is not None and node != node_id:
            continue
        if searching:
            if lesson["count"] or lesson.get("hits"):
                lessons.append(lesson)
                hits += lesson.get("hits", 0)
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
        # Совпадения поиска по сути разделов (материалом ученика не считаются).
        "hits": hits,
        # Только занятия курса: в снимке маршрута остаются id ушедших из графа.
        "closed": sum(1 for node in order if statuses.get(node) in ("passed", "weak")),
        "total": len(order),
    }
