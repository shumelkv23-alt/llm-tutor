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
    MaterialState,
    format_course_block,
    format_mastery_block,
    format_profile_block,
    format_route_block,
    format_state_block,
    tutor_system_prompt,
)
from llm_tutor.llm.schemas import ChatMessage
from llm_tutor.rag.retriever import retrieve
from llm_tutor.schemas import Chunk, SessionState
from llm_tutor.student import beta, survey
from llm_tutor.student.planner import MODE_LABELS

# Грубая оценка объёма без токенизатора: ~4 символа на токен.
CHARS_PER_TOKEN = 4

# Сколько соседних узлов показывать в срезе модели ученика.
_MASTERY_SLICE_SIZE = 6


@dataclass(frozen=True)
class ContextPackage:
    """Готовый пакет для LLM плюс найденные чанки.

    ``material_state`` запоминает состояние материалов ДО урезания бюджетом
    (иначе вытесненный материал выглядел бы как ненайденный) и различает
    «не нашлось по словам» и «материалов нет вовсе» — от этого зависит
    промпт, а промах поиска НЕ означает, что тема вне курса.
    """

    messages: list[ChatMessage]
    chunks: list[Chunk]
    material_state: MaterialState = "empty"


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


def _material_state(conn: sqlite3.Connection, chunks: list[Chunk]) -> MaterialState:
    """Состояние материалов курса для промпта.

    Промах поиска НЕ значит «вне курса»: поиск ключевой, а материал
    англоязычный при русских вопросах. Различаем «не нашлось по словам» и
    «материалов нет вовсе» — иначе ученик услышит, что ключевая тема курса
    лежит за его пределами.
    """
    if chunks:
        return "found"
    return "empty" if repos.count_chunks(conn) == 0 else "no_match"


def _system_prompt(
    conn: sqlite3.Connection,
    state: SessionState,
    graph: CourseGraph | None,
    *,
    material: MaterialState,
    now: float,
    settings: Settings,
) -> str:
    """Правила + профиль + состояние занятия + срез модели ученика."""
    # Профиль — самооценка из анкеты по блокам (срез 23). Раньше фильтр ждал
    # ключи старой анкеты и молча отсекал всё.
    # Пока анкета модуля 1 (задача 16 возьмёт модуль занятия).
    profile = format_profile_block(
        survey.self_assessment(conn, cfg) if (cfg := survey.config_for(conn, 1)) else {}
    )
    node_name = None
    if graph is not None and state.current_node_id:
        try:
            node_name = graph.concept(state.current_node_id).name
        except KeyError:
            node_name = None
    route_block = None
    if state.route is not None and graph is not None:
        route_block = format_route_block(
            state.route,
            names={node_id: graph.concept(node_id).name for node_id in graph.node_ids},
        )
    blocks = [
        tutor_system_prompt(
            state.hint_level,
            material=material,
            phase=state.phase,
            route_block=route_block,
        ),
        profile,
        format_state_block(
            node_name=node_name,
            mode_label=MODE_LABELS.get(state.mode) if state.mode else None,
            hint_level=state.hint_level,
            self_level=(
                survey.block_level(conn, state.current_node_id)
                if state.current_node_id
                else None
            ),
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
    """Урезает материал, затем диалог, пока пакет не влезет в бюджет.

    Материал не вытесняется целиком: хотя бы самый релевантный чанк остаётся,
    иначе RAG молча превратился бы в обычный чат.
    """
    limit = budget_tokens * CHARS_PER_TOKEN

    def size() -> int:
        return (
            len(system)
            + len(question)
            + sum(len(message.content) for message in tail)
            + sum(len(chunk.content) for chunk in chunks)
        )

    tail, chunks = list(tail), list(chunks)
    while len(chunks) > 1 and size() > limit:
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

    material_state = _material_state(conn, chunks)
    system = _system_prompt(
        conn,
        session_state,
        graph,
        material=material_state,
        now=stamp,
        settings=s,
    )
    tail, chunks = _fit_budget(system, tail, chunks, user_message, budget_tokens)

    messages = [ChatMessage(role="system", content=system), *tail]
    block = format_course_block(chunks)
    final_content = (
        f"{block}\n\n---\n\nВопрос ученика: {user_message}" if block else user_message
    )
    messages.append(ChatMessage(role="user", content=final_content))
    return ContextPackage(messages=messages, chunks=chunks, material_state=material_state)
