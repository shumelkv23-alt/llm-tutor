"""Модели сообщений для общения с LLM (OpenAI-совместимый формат OpenRouter)."""

from typing import Literal

from pydantic import BaseModel

Role = Literal["system", "user", "assistant"]


class ChatMessage(BaseModel):
    """Одно сообщение диалога.

    Сериализуется в ``{"role": ..., "content": ...}`` — ровно тот формат,
    который ожидает OpenRouter в поле ``messages``.
    """

    role: Role
    content: str
