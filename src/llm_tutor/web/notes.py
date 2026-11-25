"""Личный конспект ученика: пояснения, свои заметки и выполненные задания.

Источник — журнал ``messages``: вопрос помечается в ``meta`` реплики ученика
(``core.turn.post_turn(..., question=True)``), ответ тьютора — следующая за ней
реплика ``assistant``. Модуль читает и собирает данные для страницы «Конспект»
и хранит личные заметки ученика (таблица ``note_entries``, миграция 011); ходы
и состояние занятия не трогает.

Запись вопроса — вопрос-тема, коротко итог тьютора (``takeaway`` из ``meta``
его реплики, без него — первый абзац ответа; срез 108: конспект собирается
сам), полный ответ под спойлером и необязательная личная заметка ученика.
Раскладку по скелету курса делает ``web.notebook``.
Разбор кода в эту ленту не попадает: вопрос с выделенным кодом или с длинной
вставкой кода в тексте остаётся в чате, а длинный код в ответе сворачивается
под спойлер. Поиск (``query``) идёт по вопросу, фрагменту, ответу и заметке.

Вторая лента — «Выполненные задания»: ответы ученика на задания. Отдельной
пометки у них нет: ход пишет реплику ученика и событие ``events`` одним ``ts``
(так их и находит админка), поэтому ответ — реплика ``user``, совпавшая по
``ts`` с событием задания. Код из вкладки «Код» лежит там же текстом: его
собирает ``web.api_lesson.code_message``, а «Результат проверки:» отделяет код
от отчёта тестов. Условие задания показывается целиком (Markdown), ответ —
под ним; поиск идёт по условию и ответу.

Слои: ``web`` → ``core``/``course``/``student``/``db``. Рендер ответа —
``web.markdown`` (HTML в тексте модели запрещён, как и везде).
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from functools import lru_cache

from markupsafe import Markup

from llm_tutor.core.context import Focus, focus_from_meta
from llm_tutor.course.graph import CourseGraph
from llm_tutor.course.seed import main_concept
from llm_tutor.db import repos
from llm_tutor.llm.prompts import LLM_FAILURE_REPLY
from llm_tutor.student.diagnostic import SUCCESS_SCORE
from llm_tutor.web.gist import answer_gist
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

# «Выполненные задания»: сколько последних ответов на задания показывать.
# Условие задания выводится целиком — в конспекте это часть записи, а не
# подпись.
SOLUTION_LIMIT = 200

# Личная заметка ученика: предел на запись. Длиннее — это уже конспект, а не
# пометка на полях.
NOTE_MAX_CHARS = 2000

# Так ``web.api_lesson.code_message`` отделяет код от отчёта тестов; тем же
# способом его разбирает админка (``web.admin._test_result``).
TEST_MARK = "\n\nРезультат проверки:"

# Ограждение, которым ``core.context.fence`` окружает код в реплике ученика:
# серия ` не короче трёх, внутри неё `` ` `` не встречается.
CODE_FENCE = re.compile(r"^(?P<mark>`{3,})python\n(?P<code>.*)\n(?P=mark)$", re.S)

# Огорождённый блок кода в тексте вопроса или ответа (``` или ~~~).
# CommonMark разрешает отступ до трёх пробелов, а закрывающая серия может быть
# длиннее открывающей — оба случая иначе пропускали бы блок.
FENCE_BLOCK = re.compile(
    r"^[ \t]{0,3}(?P<mark>`{3,}|~{3,})[^\n]*\n"
    r"(?P<code>.*?)^[ \t]{0,3}(?P=mark)[`~]*[ \t]*$",
    re.S | re.M,
)
_TAG = re.compile(r"<[^>]+>")

# Сколько строк кода ещё считается «мелким кодом». Длиннее — это разбор кода:
# вопрос уходит из «Определений», а блок в ответе сворачивается.
MAX_CODE_LINES = 3

# Предел поискового запроса: GET-ссылка не должна расти без конца.
SEARCH_MAX_CHARS = 100

_PRE_BLOCK = re.compile(r"<pre>.*?</pre>", re.S)


def code_lines(text: str | None) -> int:
    """Самая длинная огорождённая вставка кода в тексте (0 — вставок нет)."""
    if not text:
        return 0
    longest = 0
    for match in FENCE_BLOCK.finditer(text):
        lines = [line for line in match.group("code").splitlines() if line.strip()]
        longest = max(longest, len(lines))
    return longest


def _collapse_code(html: Markup) -> Markup:
    """Длинный код в пояснении — под спойлер: в теории виден текст, не стены кода."""

    def replace(match: re.Match[str]) -> str:
        block = match.group(0)
        # Считаем непустые строки, как code_lines: пустые строки внутри кода
        # не делают пример «длинным».
        lines = [line for line in _TAG.sub("", block).splitlines() if line.strip()]
        if len(lines) <= MAX_CODE_LINES:
            return block
        return (
            '<details class="note-code"><summary>Показать код</summary>'
            f"{block}</details>"
        )

    return Markup(_PRE_BLOCK.sub(replace, str(html)))


def _matches(query: str | None, *parts: object) -> bool:
    """Все слова запроса есть в тексте (регистр и лишние пробелы не важны)."""
    if not query:
        return True
    haystack = " ".join(str(part) for part in parts if part).casefold()
    return all(word in haystack for word in query.casefold().split())


def question_entries(
    conn: sqlite3.Connection,
    *,
    limit: int = NOTE_LIMIT,
    query: str | None = None,
    node_id: str | None = None,
) -> list[dict]:
    """Вопросы ученика с ответами тьютора и личной заметкой — от новых к старым.

    Вопрос — реплика с ``meta.question`` (её пишет ``core.turn.post_turn``);
    ответ — следующая реплика ``assistant`` той же сессии (ход пишется одним
    коммитом, поэтому она идёт сразу за вопросом). Ответа может не быть —
    тогда запись показывается без разворота. Заметка приходит из
    ``note_entries`` (может отсутствовать).

    «Определения и пояснения» — не разбор кода: вопрос с выделенным кодом или
    длинной вставкой кода в самом тексте (больше ``MAX_CODE_LINES`` строк) в
    ленту не попадает. В истории чата он остаётся.

    ``query`` — поиск по вопросу, фрагменту, заметке и ответу тьютора: нужны
    все слова запроса, регистр не важен. ``node_id`` — только записи занятия
    (проверку, что занятие есть в курсе, делает страница). Лимит считается
    после всех отборов.
    """
    rows = conn.execute(
        "SELECT u.id, u.ts, u.content, u.meta, a.content AS answer, a.meta AS answer_meta, "
        "n.text AS note "
        "FROM messages u LEFT JOIN messages a ON a.id = ("
        "  SELECT a2.id FROM messages a2"
        "  WHERE a2.session_id = u.session_id AND a2.role = 'assistant'"
        "    AND a2.id > u.id AND a2.ts = u.ts"
        "  ORDER BY a2.id LIMIT 1) "
        "LEFT JOIN note_entries n ON n.message_id = u.id "
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
        # Разбор кода — не определение: вопрос с выделенным кодом или большой
        # вставкой кода в тексте остаётся в истории чата, а в «Определениях и
        # пояснениях» видны объяснения по теории/заданию и вопросы без
        # фрагмента. Мелкий код (до MAX_CODE_LINES строк) не мешает.
        if focus is not None and focus.source == "code":
            continue
        if code_lines(text) > MAX_CODE_LINES:
            continue
        node = meta.get("node")
        node_value = node if isinstance(node, str) and node else None
        if node_id is not None and node_value != node_id:
            continue
        # Поиск — до рендера полного ответа: несовпавшие записи не рендерим.
        if not _matches(
            query,
            text,
            focus.text if focus is not None else None,
            row["note"],
            row["answer"],
            _takeaway(row["answer_meta"]),
        ):
            continue
        entries.append(_entry(row, meta, text, focus))
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


@lru_cache(maxsize=4096)
def _answer_parts(answer: str) -> tuple[Markup, Markup | None]:
    """Отрендеренный полный ответ (код свёрнут) и его первый абзац.

    Кэш по тексту ответа: реплика после записи не меняется, а рендер сотен
    ответов — главная цена страницы «Конспект» (аудит ветки, срез 112).
    Результат общий и неизменяемый (``Markup`` — строка).
    """
    gist = answer_gist(answer)
    return _collapse_code(render(answer)), render(gist) if gist else None


def _takeaway(raw_meta: str | None) -> str | None:
    """Итог тьютора из ``meta`` его реплики (``core.turn.post_turn``)."""
    value = _meta(raw_meta).get("takeaway")
    return value.strip() or None if isinstance(value, str) else None


def _entry(row: sqlite3.Row, meta: dict, question: str, focus: Focus | None) -> dict:
    answer = (row["answer"] or "").strip()
    # Сбой модели — не ответ: заглушку в конспект не тащим, показываем пометку.
    failed = answer.startswith(LLM_FAILURE_REPLY)
    # Конспект собирается сам (срез 108): главный текст — итог тьютора, без него
    # — первый абзац ответа. Полный ответ остаётся под спойлером.
    answer_html, answer_gist_html = _answer_parts(answer) if answer and not failed else (None, None)
    takeaway = None if failed else _takeaway(row["answer_meta"])
    gist_html = render(takeaway) if takeaway else answer_gist_html
    ts = row["ts"]
    source = focus.source if focus is not None else None
    node_id = meta.get("node")
    section_id = meta.get("section")
    note = (row["note"] or "").strip()
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
        "note": note or None,
        "answer_html": answer_html,
        "gist_html": gist_html,
        "failed": failed,
    }


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


def save_note(
    conn: sqlite3.Connection,
    message_id: int,
    text: str,
    *,
    now: float,
) -> str:
    """Сохраняет заметку к записи конспекта; пустой текст удаляет её.

    Заметка есть только у вопроса тьютору: реплика без ``meta.question``
    адресом не становится — ``LookupError`` (API отвечает 404). Текст хранится
    как текст (края обрезаются); длиннее ``NOTE_MAX_CHARS`` — ``ValueError``.
    """
    note = text.strip()
    if len(note) > NOTE_MAX_CHARS:
        raise ValueError(f"Заметка длиннее {NOTE_MAX_CHARS} знаков.")
    try:
        row = conn.execute(
            "SELECT role, meta FROM messages WHERE id = ?", (message_id,)
        ).fetchone()
    except (OverflowError, ValueError):
        # id больше, чем влезает в INTEGER SQLite: такой записи нет.
        raise LookupError(message_id) from None
    if row is None or row["role"] != "user" or _meta(row["meta"]).get("question") is not True:
        raise LookupError(message_id)
    with conn:
        if note:
            conn.execute(
                "INSERT INTO note_entries(message_id, text, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(message_id) DO UPDATE SET "
                "text = excluded.text, updated_at = excluded.updated_at",
                (message_id, note, now),
            )
        else:
            conn.execute("DELETE FROM note_entries WHERE message_id = ?", (message_id,))
    return note


def has_lesson(conn: sqlite3.Connection, node_id: str | None) -> bool:
    """Есть ли занятие в графе курса (опубликованный модуль)."""
    return bool(node_id) and CourseGraph.load(conn).has_node(node_id)


def lesson_notes(
    conn: sqlite3.Connection,
    *,
    limit: int = NOTE_LIMIT,
    query: str | None = None,
    node_id: str | None = None,
) -> list[dict]:
    """Записи конспекта по занятиям одного курса.

    Занятия идут в порядке последнего вопроса (о чём спрашивали недавно — то
    выше), внутри — от новых к старым, а записи сгруппированы по разделам
    занятия. Имя занятия, модуль и название раздела берутся из графа курса;
    занятие, ушедшее из графа, показывается своим id. ``query`` — поиск,
    ``node_id`` — только одно занятие; пустые после отбора занятия не
    показываются.
    """
    entries = question_entries(conn, limit=limit, query=query, node_id=node_id)
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
        group["sections"] = _section_groups(group["entries"])
    return list(groups.values())


def _section_groups(entries: list[dict]) -> list[dict]:
    """Записи занятия по разделам: порядок разделов — по первому (новому) входу."""
    groups: list[dict] = []
    index: dict[str, dict] = {}
    for entry in entries:
        key = entry["section_id"] or ""
        group = index.get(key)
        if group is None:
            group = {"id": key, "name": entry["section"] or "", "entries": []}
            index[key] = group
            groups.append(group)
        group["entries"].append(entry)
    return groups


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


def solution_entries(
    conn: sqlite3.Connection,
    *,
    limit: int = SOLUTION_LIMIT,
    query: str | None = None,
    node_id: str | None = None,
) -> list[dict]:
    """Ответы ученика на задания — от новых к старым.

    Ответа не помечают: он находится по совпадению ``ts`` с событием задания.
    Ход пишет реплику ``user`` и все события одним ``stamp`` (``post_turn``),
    поэтому реплики без события (вопросы тьютору, команды) сюда не попадают.
    Событие на каждый концепт задания даёт несколько строк с одним ``ts`` —
    оставляем одну на ``(item_id, ts, source)``, как это делает админка.

    ``query`` — поиск по условию задания и ответу ученика, ``node_id`` — только
    одно занятие; лимит считается после всех отборов. Без запроса и фильтра
    SQL сразу берёт последние ``limit`` событий.
    """
    sql = (
        "SELECT e.ts, e.concept_id, e.item_id, e.source, e.result, m.content "
        "FROM events e JOIN messages m ON m.id = ("
        "  SELECT m2.id FROM messages m2 WHERE m2.role = 'user' AND m2.ts = e.ts"
        "  ORDER BY m2.id DESC LIMIT 1) "
        "WHERE e.id IN (SELECT MIN(id) FROM events WHERE item_id IS NOT NULL "
        "GROUP BY item_id, ts, source) "
        "ORDER BY e.ts DESC, e.id DESC"
    )
    rows = (
        conn.execute(sql + " LIMIT ?", (limit,)).fetchall()
        if query is None and node_id is None
        else conn.execute(sql).fetchall()
    )
    entries: list[dict] = []
    for row in rows:
        text = (row["content"] or "").strip()
        if not text:
            continue
        item = repos.get_item(conn, row["item_id"])
        node = main_concept(item) if item is not None else row["concept_id"]
        if node_id is not None and node != node_id:
            continue
        # Поиск — до рендера условия: несовпавшие записи не рендерим.
        if not _matches(query, item.prompt if item is not None else None, text):
            continue
        entries.append(_solution_entry(row, text, item))
        if len(entries) >= limit:
            break
    return entries


def _solution_entry(row: sqlite3.Row, text: str, item) -> dict:
    node_id = main_concept(item) if item is not None else row["concept_id"]
    code = _code_answer(text)
    score = row["result"]
    return {
        "item_id": row["item_id"],
        "ts": row["ts"],
        "when": _format_ts(row["ts"]),
        "iso": _iso(row["ts"]),
        "node_id": node_id if isinstance(node_id, str) and node_id else None,
        # Условие — часть записи конспекта, а не подпись: показываем целиком
        # и тем же безопасным Markdown, что «Практика» и вкладка «Код».
        "prompt_html": render(item.prompt) if item is not None else None,
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
    query: str | None = None,
    node_id: str | None = None,
) -> list[dict]:
    """Решения по занятиям.

    Группировка и порядок — как у вопросов: занятия по последнему ответу,
    внутри — от новых к старым. Показываются все решения, с вердиктом.
    ``query`` — поиск по условию и ответу, ``node_id`` — только одно занятие;
    пустые занятия не показываются.
    """
    entries = solution_entries(conn, limit=limit, query=query, node_id=node_id)
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
