"""Сборка контекста хода для LLM (минимальная версия, Срез 3).

Полный контекст (профиль, срез модели ученика, лестница подсказок) — Срез 5.
Здесь: системные правила → хвост диалога → (материал курса) + вопрос ученика.

Материал курса подаётся в СООБЩЕНИИ УЧЕНИКА как данные, а не в системный
промпт: он тянется из web и не должен попадать в самую привилегированную роль.
"""

import sqlite3
from dataclasses import dataclass

from llm_tutor.config import DEFAULT_DIALOG_TAIL, DEFAULT_RAG_TOP_K
from llm_tutor.db.repos import get_messages
from llm_tutor.llm.prompts import (
    TUTOR_NO_MATERIAL_SYSTEM_PROMPT,
    TUTOR_SYSTEM_PROMPT,
    format_course_block,
)
from llm_tutor.llm.schemas import ChatMessage
from llm_tutor.rag.retriever import retrieve
from llm_tutor.schemas import Chunk


@dataclass(frozen=True)
class ContextPackage:
    """Готовый пакет для LLM плюс найденные чанки (для решения «не нашёл»)."""

    messages: list[ChatMessage]
    chunks: list[Chunk]


def build_context(
    conn: sqlite3.Connection,
    session_id: int,
    user_message: str,
    *,
    top_k: int = DEFAULT_RAG_TOP_K,
    dialog_tail: int = DEFAULT_DIALOG_TAIL,
) -> ContextPackage:
    """Собирает пакет: правила → хвост диалога → (материал) + вопрос ученика."""
    # top_k <= 0 не должен «выключать» RAG и бота — падаем на дефолт.
    effective_k = top_k if top_k > 0 else DEFAULT_RAG_TOP_K
    chunks = retrieve(conn, user_message, k=effective_k)

    system_prompt = TUTOR_SYSTEM_PROMPT if chunks else TUTOR_NO_MATERIAL_SYSTEM_PROMPT
    messages = [ChatMessage(role="system", content=system_prompt)]

    if dialog_tail > 0:
        tail = get_messages(conn, session_id)[-dialog_tail:]
        for message in tail:
            if message.role in ("user", "assistant"):
                messages.append(ChatMessage(role=message.role, content=message.content))

    block = format_course_block(chunks)
    final_content = (
        f"{block}\n\n---\n\nВопрос ученика: {user_message}" if block else user_message
    )
    messages.append(ChatMessage(role="user", content=final_content))
    return ContextPackage(messages=messages, chunks=chunks)
