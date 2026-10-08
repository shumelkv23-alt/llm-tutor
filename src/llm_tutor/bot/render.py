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


PLAN_WINDOW = 3
_PROGRESS_WIDTH = 15
_STATUS_MARKS = {"closed": "[x]", "current": "[>]", "ahead": "[ ]"}


def _progress_bar(closed: int, total: int, *, width: int = _PROGRESS_WIDTH) -> str:
    """Полоса прогресса из моноширинных блоков."""
    if total <= 0:
        return "░" * width
    filled = round(closed / total * width)
    return "█" * filled + "░" * (width - filled)


def _windowed(steps: list, window: int) -> list:
    """Окно вокруг текущего шага плюс начало и цель (без дублей)."""
    idx = next((i for i, s in enumerate(steps) if s.status == "current"), None)
    if idx is None or len(steps) <= window * 2 + 1:
        return steps
    start = max(0, idx - window)
    end = min(len(steps), idx + window + 1)
    chosen = list(steps[start:end])
    if start > 0 and chosen[0] is not steps[0]:
        chosen = [steps[0]] + chosen
    if end < len(steps):
        chosen = chosen + [steps[-1]]
    return chosen


def render_plan(
    conn: sqlite3.Connection,
    *,
    state: SessionState | None = None,
    now: float | None = None,
    settings: Settings | None = None,
) -> str:
    """Табличка маршрута: путь, прогресс и окно вокруг текущего узла."""
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

    goal_name = (
        graph.concept(route.goal_concept_id).name
        if route.goal_concept_id is not None
        else "вершина темы"
    )
    total = len(route.steps)
    shown = _windowed(route.steps, PLAN_WINDOW)

    # Имена экранируем: сообщение уходит с parse_mode=HTML, а имя — динамика.
    names = [escape(graph.concept(step.concept_id).name) for step in shown]
    name_w = max(len(name) for name in names)
    label_w = len("←сейчас")

    cells: list[str] = []
    for step, name in zip(shown, names):
        if step.concept_id == route.goal_concept_id:
            label = "цель"
        elif step.status == "current":
            label = "←сейчас"
        elif step.status == "closed":
            label = "усвоен"
        else:
            label = "впереди"
        cells.append(f"  {_STATUS_MARKS[step.status]}  {name.ljust(name_w)} {label.rjust(label_w)} ")

    bar = f" Пройдено  {_progress_bar(route.closed_count, total)}  {route.closed_count}/{total} "
    inner = max(len(bar), *(len(cell) for cell in cells))
    lines = [
        "┌" + "─" * inner + "┐",
        f"│{bar.ljust(inner)}│",
        "├" + "─" * inner + "┤",
        *(f"│{cell.ljust(inner)}│" for cell in cells),
        "└" + "─" * inner + "┘",
    ]
    header = (
        f"🗺 <b>Маршрут</b> — цель «{escape(goal_name)}», "
        f"пройдено {route.closed_count}/{total}"
    )
    return header + "\n<pre>" + "\n".join(lines) + "</pre>"


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
        lines.append("Ждёт ответа задание — ответь или жми ⏭ Пропустить.")
    else:
        lines.append("Задания нет — жми 🎯 Задание.")
    return "\n".join(lines)


# Пояснение к каждому действию для справки (синхронно с menu.ACTION_LABELS).
_HELP_LINES: dict[str, str] = {
    "status": "где ты сейчас и как идёт маршрут",
    "route": "путь к цели с прогрессом",
    "themes": "выбрать тему: вернуться или забежать вперёд",
    "close": "проверить тему и закрыть её, если знания подтвердятся",
    "task": "взять задание по текущей теме",
    "stuck": "не понял — объясню подробнее",
    "skip": "пропустить текущее задание",
    "help": "эта справка",
}


def render_help() -> str:
    """Справка по действиям меню (синхронна с ``menu.ACTION_LABELS``)."""
    lines = ["ℹ️ <b>Что умею</b>"]
    for action, label in menu.ACTION_LABELS.items():
        lines.append(f"{label} — {_HELP_LINES[action]}")
    return "\n".join(lines)
