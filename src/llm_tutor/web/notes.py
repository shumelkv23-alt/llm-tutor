"""Личный конспект ученика: вопросы тьютору и его решения, разложенные по занятиям.

Источник — журнал ``messages`` (миграций нет): вопрос помечается в ``meta``
реплики ученика (``core.turn.post_turn(..., question=True)``), ответ тьютора —
следующая за ней реплика ``assistant``. Модуль только читает и собирает
данные для страницы «Конспект»; ходы и состояние не трогает.

Запись конспекта — тема (вопрос) и краткая выжимка «что я узнал»: её пишет
модель в ``TutorReply.takeaway``, и ``post_turn`` кладёт её в ``meta`` реплики
тьютора. У старых записей итога нет — выжимкой становится первый абзац ответа.
Полный ответ страница оставляет под спойлером.

Вторая часть конспекта — «Мои решения»: ответы ученика на задания. Отдельной
пометки у них нет: ход пишет реплику ученика и событие ``events`` одним ``ts``
(так их и находит админка), поэтому ответ — реплика ``user``, совпавшая по
``ts`` с событием задания. Код из вкладки «Код» лежит там же текстом: его
собирает ``web.api_lesson.code_message``, а «Результат проверки:» отделяет код
от отчёта тестов. Записи делятся на текстовые и кодовые: код длинный, и его
удобнее смотреть отдельным фильтром.

Слои: ``web`` → ``core``/``course``/``student``/``db``. Рендер ответа —
``web.markdown`` (HTML в тексте модели запрещён, как и везде).
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime

from llm_tutor.core.context import Focus, focus_from_meta
from llm_tutor.course.graph import CourseGraph
from llm_tutor.course.seed import main_concept
from llm_tutor.db import repos
from llm_tutor.llm.prompts import LLM_FAILURE_REPLY
from llm_tutor.student.diagnostic import SUCCESS_SCORE
from llm_tutor.web.markdown import render

# Сколько последних вопросов курса попадает в конспект: страница личная, но
# за годы их может стать больше, чем стоит отдавать одним HTML.
NOTE_LIMIT = 300

# Источник выделенного фрагмента — подпись в записи (``FocusSource``).
SOURCE_LABELS: dict[str, str] = {
    "theory": "Теория",
    "task": "Задание",
    "code": "Код",
}

NO_LESSON = "Вне занятия"

# Выжимка в записи не длиннее этого: конспект читают глазами, а не стеной
# текста. Итог модели просим короче; запас — на старые записи и разошедшуюся
# модель.
BRIEF_CHARS = 320

# «Мои решения»: сколько последних ответов на задания показывать и какую часть
# условия задания оставить в строке-подписи (сам ответ показывается целиком).
SOLUTION_LIMIT = 200
PROMPT_CHARS = 200

# Так ``web.api_lesson.code_message`` отделяет код от отчёта тестов; тем же
# способом его разбирает админка (``web.admin._test_result``).
TEST_MARK = "\n\nРезультат проверки:"

# Ограждение, которым ``core.context.fence`` окружает код в реплике ученика:
# серия ` не короче трёх, внутри неё `` ` `` не встречается.
CODE_FENCE = re.compile(r"^(?P<mark>`{3,})python\n(?P<code>.*)\n(?P=mark)$", re.S)


def question_entries(
    conn: sqlite3.Connection,
    *,
    limit: int = NOTE_LIMIT,
    kind: str | None = None,
) -> list[dict]:
    """Вопросы ученика с ответами тьютора — от новых к старым.

    Вопрос — реплика с ``meta.question`` (её пишет ``core.turn.post_turn``);
    ответ — следующая реплика ``assistant`` той же сессии (ход пишется одним
    коммитом, поэтому она идёт сразу за вопросом). Ответа может не быть —
    тогда запись показывается без разворота.

    ``kind`` — ``code`` (вопрос по выделенному коду) или ``text`` (всё
    остальное): код длинный, и его удобнее смотреть отдельной лентой.
    """
    rows = conn.execute(
        "SELECT u.id, u.ts, u.content, u.meta, a.content AS answer, a.meta AS answer_meta "
        "FROM messages u LEFT JOIN messages a ON a.id = ("
        "  SELECT a2.id FROM messages a2"
        "  WHERE a2.session_id = u.session_id AND a2.role = 'assistant'"
        "    AND a2.id > u.id AND a2.ts = u.ts"
        "  ORDER BY a2.id LIMIT 1) "
        "WHERE u.role = 'user' "
        "ORDER BY u.ts DESC, u.id DESC"
    ).fetchall()
    entries: list[dict] = []
    for row in rows:
        meta = _meta(row["meta"])
        if meta.get("question") is not True:
            continue
        text = (row["content"] or "").strip()
        if not text:
            continue
        focus = focus_from_meta(row["meta"])
        entry = _entry(row, meta, text, focus)
        if kind == "code" and entry["source_key"] != "code":
            continue
        if kind == "text" and entry["source_key"] == "code":
            continue
        entries.append(entry)
        if len(entries) >= limit:
            break
    return entries


def _meta(raw: str | None) -> dict:
    """Разобранный ``messages.meta``; битый или не-объект — пустой словарь."""
    try:
        value = json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _entry(row: sqlite3.Row, meta: dict, question: str, focus: Focus | None) -> dict:
    answer = (row["answer"] or "").strip()
    # Сбой модели — не ответ: заглушку в конспект не тащим, показываем пометку.
    failed = answer.startswith(LLM_FAILURE_REPLY)
    ts = row["ts"]
    source = focus.source if focus is not None else None
    node_id = meta.get("node")
    section_id = meta.get("section")
    brief = None if failed else _brief(_meta(row["answer_meta"]).get("takeaway"), answer)
    return {
        "id": row["id"],
        "ts": ts,
        "when": _format_ts(ts),
        "iso": _iso(ts),
        "node_id": node_id if isinstance(node_id, str) and node_id else None,
        "section_id": section_id if isinstance(section_id, str) and section_id else None,
        "question": question,
        "quote": focus.text if focus is not None else None,
        "source": SOURCE_LABELS.get(source) if source is not None else None,
        "source_key": source,
        "is_code": source == "code",
        "brief_html": render(brief) if brief else None,
        "answer_html": render(answer) if answer and not failed else None,
        "failed": failed,
    }


def _brief(takeaway: object, answer: str) -> str | None:
    """Краткая выжимка записи: итог модели, а без него — первый абзац ответа.

    ``takeaway`` приходит из ``meta`` реплики тьютора и может отсутствовать
    (старые записи) или быть пустым (модель не заполнила): тогда конспект
    показывает первый абзац ответа — обычно это и есть определение по сути.
    """
    if isinstance(takeaway, str) and takeaway.strip():
        return _clip(takeaway.strip())
    return _clip(answer.strip().split("\n\n", 1)[0].strip())


def _clip(text: str, *, limit: int = BRIEF_CHARS) -> str | None:
    """Обрезка выжимки по границе предложения (или слова) с многоточием."""
    if not text:
        return None
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if end >= limit // 2:
        return cut[: end + 1].strip()
    space = cut.rfind(" ")
    return (cut[:space] if space > 0 else cut).rstrip(" ,;:—-") + "…"


def _format_ts(ts: float | int | None) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")
    except (OverflowError, OSError, TypeError, ValueError):
        return ""


def _iso(ts: float | int | None) -> str:
    try:
        return datetime.fromtimestamp(ts).isoformat(timespec="minutes")
    except (OverflowError, OSError, TypeError, ValueError):
        return ""


def lesson_notes(
    conn: sqlite3.Connection,
    *,
    limit: int = NOTE_LIMIT,
    kind: str | None = None,
) -> list[dict]:
    """Записи конспекта по занятиям одного курса.

    Занятия идут в порядке последнего вопроса (о чём спрашивали недавно — то
    выше), внутри — от новых к старым. Имя занятия, модуль и название раздела
    берутся из графа курса; занятие, ушедшее из графа, показывается своим id.
    """
    entries = question_entries(conn, limit=limit, kind=kind)
    if not entries:
        return []
    graph = CourseGraph.load(conn)
    topics = {topic.number: topic.title for topic in repos.get_topics(conn)}
    groups: dict[str, dict] = {}
    for entry in entries:
        node_id = entry["node_id"]
        key = node_id or ""
        group = groups.get(key)
        if group is None:
            group = _lesson_group(graph, topics, node_id)
            groups[key] = group
        section_id = entry["section_id"]
        entry["section"] = group["sections"].get(section_id) if section_id else None
        group["entries"].append(entry)
    for group in groups.values():
        group.pop("sections")
        group["count"] = len(group["entries"])
    return list(groups.values())


def _lesson_group(graph: CourseGraph, topics: dict[int, str], node_id: str | None) -> dict:
    concept = graph.concept(node_id) if node_id and graph.has_node(node_id) else None
    if concept is None:
        return {
            "node_id": node_id,
            "name": node_id or NO_LESSON,
            "module": None,
            "sections": {},
            "entries": [],
        }
    title = topics.get(concept.topic_id)
    module = f"Модуль {concept.topic_id}" + (f" · {title}" if title else "")
    return {
        "node_id": concept.id,
        "name": concept.name,
        "module": module,
        "sections": {section.id: section.title for section in concept.sections},
        "entries": [],
    }


# --- «Мои решения»: ответы ученика на задания ---


def solution_entries(conn: sqlite3.Connection, *, limit: int = SOLUTION_LIMIT) -> list[dict]:
    """Ответы ученика на задания — от новых к старым.

    Ответа не помечают: он находится по совпадению ``ts`` с событием задания.
    Ход пишет реплику ``user`` и все события одним ``stamp`` (``post_turn``),
    поэтому реплики без события (вопросы тьютору, команды) сюда не попадают.
    Событие на каждый концепт задания даёт несколько строк с одним ``ts`` —
    оставляем одну на ``(item_id, ts, source)``, как это делает админка.
    """
    rows = conn.execute(
        "SELECT ts, concept_id, item_id, source, result FROM events "
        "WHERE id IN (SELECT MIN(id) FROM events WHERE item_id IS NOT NULL "
        "GROUP BY item_id, ts, source) ORDER BY ts DESC, id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    if not rows:
        return []
    marks = ",".join("?" for _ in rows)
    texts: dict[float, str] = {
        ts: content
        for ts, content in conn.execute(
            f"SELECT ts, content FROM messages WHERE role = 'user' AND ts IN ({marks})",
            [row["ts"] for row in rows],
        )
    }
    entries: list[dict] = []
    for row in rows:
        text = texts.get(row["ts"])
        if not text or not text.strip():
            continue
        entries.append(_solution_entry(conn, row, text.strip()))
    return entries


def _solution_entry(conn: sqlite3.Connection, row: sqlite3.Row, text: str) -> dict:
    item = repos.get_item(conn, row["item_id"])
    node_id = main_concept(item) if item is not None else row["concept_id"]
    code = _code_answer(text)
    score = row["result"]
    return {
        "item_id": row["item_id"],
        "ts": row["ts"],
        "when": _format_ts(row["ts"]),
        "iso": _iso(row["ts"]),
        "node_id": node_id if isinstance(node_id, str) and node_id else None,
        "prompt": _prompt_preview(item.prompt) if item is not None else None,
        "is_code": code is not None,
        "code": code["code"] if code is not None else None,
        "passed": code["passed"] if code is not None else None,
        "report": code["report"] if code is not None else None,
        "right": score >= SUCCESS_SCORE,
        "verdict": _score_label(score),
        "answer_html": render(text) if code is None else None,
    }


def _code_answer(text: str) -> dict | None:
    """Разбор реплики с кодом: сам код (без ограждения) и итог проверок.

    ``None`` — реплика не от запуска кода: текстовый ответ остаётся текстом.
    """
    if TEST_MARK not in text:
        return None
    raw, _, verdict = text.rpartition(TEST_MARK)
    raw = raw.strip()
    verdict = verdict.strip()
    passed = verdict.startswith("все проверки пройдены")
    report = ""
    if not passed:
        _, _, report = verdict.partition("—")
        report = report.strip() or verdict
    match = CODE_FENCE.match(raw)
    code = match.group("code") if match is not None else raw
    return {"code": code.strip(), "passed": passed, "report": report}


def _prompt_preview(text: str) -> str:
    """Начало условия задания одной строкой — подпись решения, не сам ответ."""
    flat = " ".join(text.split())
    if len(flat) <= PROMPT_CHARS:
        return flat
    return flat[:PROMPT_CHARS].rstrip(" ,;:—-") + "…"


def _score_label(score: float) -> str:
    if score >= 1.0:
        return "Верно"
    if score <= 0.0:
        return "Неверно"
    return f"{round(score * 100)}%"


def lesson_solutions(
    conn: sqlite3.Connection,
    *,
    limit: int = SOLUTION_LIMIT,
    kind: str | None = None,
) -> list[dict]:
    """Решения по занятиям: ``kind`` — ``text`` или ``code`` (``None`` — все).

    Группировка и порядок — как у вопросов: занятия по последнему ответу,
    внутри — от новых к старым. Пустые после фильтра занятия не показываются.
    """
    entries = solution_entries(conn, limit=limit)
    if kind == "code":
        entries = [entry for entry in entries if entry["is_code"]]
    elif kind == "text":
        entries = [entry for entry in entries if not entry["is_code"]]
    if not entries:
        return []
    graph = CourseGraph.load(conn)
    topics = {topic.number: topic.title for topic in repos.get_topics(conn)}
    groups: dict[str, dict] = {}
    for entry in entries:
        node_id = entry["node_id"]
        key = node_id or ""
        group = groups.get(key)
        if group is None:
            group = _lesson_group(graph, topics, node_id)
            groups[key] = group
        group["entries"].append(entry)
    for group in groups.values():
        group.pop("sections")
        group["count"] = len(group["entries"])
    return list(groups.values())
