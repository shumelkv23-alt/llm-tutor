"""Слой представления бота: экранирование и тексты сообщений.

Всё, что бот отправляет с ``parse_mode=HTML``, формируется здесь. Тексты
модели и данные из БД перед вставкой экранируются (``escape``) — модель не
должна уметь вставлять разметку в свой ответ.
"""

import html
import sqlite3

from llm_tutor.bot import menu
from llm_tutor.config import Settings
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.llm.prompts import EMPTY_GRAPH_REPLY
from llm_tutor.schemas import SessionState
from llm_tutor.student import route as route_mod
from llm_tutor.student.hints import HINT_LEVEL_NAMES

PARSE_MODE = "HTML"
# Лимит Telegram 4096; держим запас.
MAX_MESSAGE = 4000


def escape(text: str) -> str:
    """Экранирует динамический текст для ``parse_mode=HTML``."""
    return html.escape(text, quote=False)


def fit(text: str) -> str:
    """Обрезает уже экранированный текст до лимита, не разрывая сущность.

    Экранировать нужно ДО обрезки: сущности длиннее исходных символов, и
    обрезка по «сырому» тексту пробила бы лимит Telegram. Но и резать по
    экранированному нельзя вслепую — можно оборвать ``&amp;`` на половине.
    """
    if len(text) <= MAX_MESSAGE:
        return text
    cut = text[:MAX_MESSAGE]
    amp = cut.rfind("&")
    if amp != -1 and ";" not in cut[amp:]:
        cut = cut[:amp]
    return cut + "…"


def _first_state(conn: sqlite3.Connection) -> SessionState:
    """Состояние открытой сессии (или пустое, если сессии ещё нет)."""
    session_id = repos.get_open_session(conn)
    return repos.get_session_state(conn, session_id) if session_id else SessionState()


PLAN_STEPS = 5
_STEP_MARKS = {"closed": "[x] ", "current": "[>] ", "claimed": "🔍 ", "ahead": ""}


def render_steps(
    graph: CourseGraph,
    steps: list,
    *,
    limit: int | None = None,
    marks: bool = True,
) -> str:
    """Шаги маршрута нумерованным списком.

    Без псевдографики: та же форма, что у списка ближайших шагов на входе в
    курс, — одна форма на весь интерфейс. ``limit`` режет список (ближайшие
    шаги), ``marks`` рисует пометки пройденного.
    """
    shown = steps[:limit] if limit is not None else steps
    if not shown:
        return "Пока нечего показывать — маршрут пуст."
    lines = [
        f"{index}. {_STEP_MARKS[step.status] if marks else ''}"
        f"{escape(graph.concept(step.concept_id).name)}"
        for index, step in enumerate(shown, start=1)
    ]
    return "\n".join(lines)


def render_plan(
    conn: sqlite3.Connection,
    *,
    state: SessionState | None = None,
    now: float | None = None,
    settings: Settings | None = None,
) -> str:
    """Маршрут целиком: нумерованный список с пометками пройденного."""
    graph = CourseGraph.load(conn)
    if not graph.node_ids:
        return EMPTY_GRAPH_REPLY

    session_state = state or _first_state(conn)
    route = route_mod.build_route(
        conn,
        graph,
        goal_concept_id=route_mod.goal_for(conn, graph),
        current_node_id=session_state.current_node_id,
        previous=session_state.route,
        now=now,
        settings=settings,
    )
    if not route.steps:
        return EMPTY_GRAPH_REPLY
    if route.closed_count == len(route.steps):
        return "Всё доступное уже освоено — можно двигаться дальше или взять цель посложнее."
    header = f"🗺 <b>Маршрут</b> — пройдено {route.closed_count} из {len(route.steps)}"
    return f"{header}\n{render_steps(graph, route.steps)}"


PHASE_LABELS: dict[str, str] = {
    "explain": "объяснение",
    "practice": "практика",
    "check": "проверка",
}


def render_status(
    conn: sqlite3.Connection,
    *,
    state: SessionState | None = None,
    now: float | None = None,
    settings: Settings | None = None,
) -> str:
    """Дашборд ученика: где он, что делает и как идёт маршрут."""
    graph = CourseGraph.load(conn)
    if not graph.node_ids:
        return EMPTY_GRAPH_REPLY

    session_state = state or _first_state(conn)
    lines = ["📚 <b>Моё обучение</b>"]

    node_id = session_state.current_node_id
    if node_id is not None and graph.has_node(node_id):
        lines.append(f"Сейчас: <b>{escape(graph.concept(node_id).name)}</b>")
    else:
        lines.append("Сейчас: тема ещё не выбрана — жми 🗺 Маршрут.")
    lines.append(f"Фаза: {PHASE_LABELS.get(session_state.phase, session_state.phase)}")
    level = session_state.hint_level
    lines.append(
        f"Уровень подсказки: {level} "
        f"({HINT_LEVEL_NAMES.get(level, 'без подсказки')})"
    )

    route = route_mod.build_route(
        conn,
        graph,
        goal_concept_id=route_mod.goal_for(conn, graph),
        current_node_id=session_state.current_node_id,
        previous=session_state.route,
        now=now,
        settings=settings,
    )
    if route.steps:
        lines.append(f"Маршрут: пройдено {route.closed_count} из {len(route.steps)}")

    if session_state.pending_item_id is not None:
        lines.append("Ждёт ответа задание — ответь на него или напиши «пропусти».")
    else:
        lines.append("Задания нет — просто напиши, и продолжим.")
    return "\n".join(lines)


# Пояснение к каждому действию для справки (синхронно с menu.ACTION_LABELS).
_HELP_LINES: dict[str, str] = {
    "route": "путь к цели с прогрессом; тут же правится, если что-то знаешь",
    "themes": "выбрать тему: вернуться назад или забежать вперёд",
    "close": "проверить тему и закрыть её, если знания подтвердятся",
}


# Выходы работают и текстом, а не только кнопками: см. core/intents.py.
_TEXT_HINTS = (
    "Просто напиши, если что-то не так:\n"
    "  «не понял» — объясню подробнее\n"
    "  «пропусти» — снять текущее задание\n"
    "  «закрой тему» — проверю и закрою, если знания подтвердятся"
)

_COMMANDS = "Команды: /plan · /themes · /close · /resume · /status · /task · /skip · /help"


def render_help() -> str:
    """Справка по действиям меню (синхронна с ``menu.ACTION_LABELS``)."""
    lines = ["ℹ️ <b>Что умею</b>"]
    for action, label in menu.ACTION_LABELS.items():
        lines.append(f"{label} — {_HELP_LINES[action]}")
    lines.append("")
    lines.append(escape(_TEXT_HINTS))
    lines.append("")
    lines.append(escape(_COMMANDS))
    return "\n".join(lines)
