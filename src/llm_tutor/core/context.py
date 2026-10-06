"""Сборка контекста хода для LLM (Срез 3, полный пакет — Срез 5).

Порядок блоков: правила → профиль → состояние занятия → срез модели ученика
→ материал курса → хвост диалога → вопрос ученика. При нехватке бюджета
первыми режутся материал и диалог: правила, профиль и состояние остаются.

Материал курса подаётся в СООБЩЕНИИ УЧЕНИКА как данные, а не в системный
промпт: он тянется из web и не должен попадать в самую привилегированную роль.
"""

import sqlite3
import time
from dataclasses import dataclass

from llm_tutor.config import (
    DEFAULT_CONTEXT_BUDGET_TOKENS,
    DEFAULT_DIALOG_TAIL,
    DEFAULT_RAG_TOP_K,
    Settings,
    get_settings,
)
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.llm.prompts import (
    format_course_block,
    format_mastery_block,
    format_profile_block,
    format_state_block,
    tutor_system_prompt,
)
from llm_tutor.llm.schemas import ChatMessage
from llm_tutor.rag.retriever import retrieve
from llm_tutor.schemas import Chunk, SessionState
from llm_tutor.student import beta
from llm_tutor.student.planner import MODE_LABELS

# Грубая оценка объёма без токенизатора: ~4 символа на токен.
CHARS_PER_TOKEN = 4

# Сколько соседних узлов показывать в срезе модели ученика.
_MASTERY_SLICE_SIZE = 6

# Факты профиля, которые не стоит показывать модели как «контекст темы».
_PROFILE_KEYS = ("pandas_experience", "goal", "time_budget")


@dataclass(frozen=True)
class ContextPackage:
    """Готовый пакет для LLM плюс найденные чанки (для решения «не нашёл»)."""

    messages: list[ChatMessage]
    chunks: list[Chunk]


def _mastery_slice(
    conn: sqlite3.Connection,
    graph: CourseGraph | None,
    state: SessionState,
    *,
    now: float,
    settings: Settings,
) -> list[tuple[str, float]]:
    """Оценки соседних узлов (текущий, его пререквизиты и зависимые)."""
    if graph is None or not state.current_node_id:
        return []
    try:
        graph.concept(state.current_node_id)
    except KeyError:
        return []

    neighbours = [
        state.current_node_id,
        *graph.prerequisites(state.current_node_id),
        *graph.dependents(state.current_node_id),
    ]
    return [
        (
            graph.concept(node_id).name,
            beta.estimate(conn, node_id, now=now, settings=settings).mean,
        )
        for node_id in neighbours[:_MASTERY_SLICE_SIZE]
    ]


def _system_prompt(
    conn: sqlite3.Connection,
    state: SessionState,
    graph: CourseGraph | None,
    *,
    has_material: bool,
    now: float,
    settings: Settings,
) -> str:
    """Правила + профиль + состояние занятия + срез модели ученика."""
    profile = format_profile_block(
        {
            key: value
            for key, value in repos.get_facts(conn).items()
            if key in _PROFILE_KEYS
        }
    )
    node_name = None
    if graph is not None and state.current_node_id:
        try:
            node_name = graph.concept(state.current_node_id).name
        except KeyError:
            node_name = None
    blocks = [
        tutor_system_prompt(state.hint_level, has_material=has_material),
        profile,
        format_state_block(
            node_name=node_name,
            mode_label=MODE_LABELS.get(state.mode) if state.mode else None,
            hint_level=state.hint_level,
        ),
        format_mastery_block(
            _mastery_slice(conn, graph, state, now=now, settings=settings)
        ),
    ]
    return "\n\n".join(block for block in blocks if block)


def _fit_budget(
    system: str,
    tail: list[ChatMessage],
    chunks: list[Chunk],
    question: str,
    budget_tokens: int,
) -> tuple[list[ChatMessage], list[Chunk]]:
    """Урезает материал, затем диалог, пока пакет не влезет в бюджет."""
    limit = budget_tokens * CHARS_PER_TOKEN

    def size() -> int:
        return (
            len(system)
            + len(question)
            + sum(len(message.content) for message in tail)
            + sum(len(chunk.content) for chunk in chunks)
        )

    tail, chunks = list(tail), list(chunks)
    while chunks and size() > limit:
        chunks.pop()  # наименее релевантные — в конце списка
    while tail and size() > limit:
        tail.pop(0)  # самый старый ход диалога
    return tail, chunks


def build_context(
    conn: sqlite3.Connection,
    session_id: int,
    user_message: str,
    *,
    state: SessionState | None = None,
    graph: CourseGraph | None = None,
    top_k: int = DEFAULT_RAG_TOP_K,
    dialog_tail: int = DEFAULT_DIALOG_TAIL,
    budget_tokens: int = DEFAULT_CONTEXT_BUDGET_TOKENS,
    now: float | None = None,
    settings: Settings | None = None,
) -> ContextPackage:
    """Собирает пакет: правила → профиль → состояние → модель → материал → вопрос."""
    s = settings or get_settings()
    stamp = time.time() if now is None else now
    session_state = state or repos.get_session_state(conn, session_id)

    # top_k <= 0 не должен «выключать» RAG и бота — падаем на дефолт.
    effective_k = top_k if top_k > 0 else DEFAULT_RAG_TOP_K
    chunks = retrieve(conn, user_message, k=effective_k)

    tail: list[ChatMessage] = []
    if dialog_tail > 0:
        tail = [
            ChatMessage(role=message.role, content=message.content)
            for message in repos.get_messages(conn, session_id)[-dialog_tail:]
            if message.role in ("user", "assistant")
        ]

    system = _system_prompt(
        conn,
        session_state,
        graph,
        has_material=bool(chunks),
        now=stamp,
        settings=s,
    )
    tail, chunks = _fit_budget(system, tail, chunks, user_message, budget_tokens)
    if not chunks:
        # Бюджет вытеснил весь материал — правила должны честно сказать об этом.
        system = _system_prompt(
            conn, session_state, graph, has_material=False, now=stamp, settings=s
        )
        tail, _ = _fit_budget(system, tail, [], user_message, budget_tokens)

    messages = [ChatMessage(role="system", content=system), *tail]
    block = format_course_block(chunks)
    final_content = (
        f"{block}\n\n---\n\nВопрос ученика: {user_message}" if block else user_message
    )
    messages.append(ChatMessage(role="user", content=final_content))
    return ContextPackage(messages=messages, chunks=chunks)
