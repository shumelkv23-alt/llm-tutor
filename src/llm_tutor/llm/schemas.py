"""Модели сообщений для общения с LLM (OpenAI-совместимый формат OpenRouter)."""

from pydantic import BaseModel

from llm_tutor.schemas import Role


class ChatMessage(BaseModel):
    """Одно сообщение диалога.

    Сериализуется в ``{"role": ..., "content": ...}`` — ровно тот формат,
    который ожидает OpenRouter в поле ``messages``.
    """

    role: Role
    content: str
