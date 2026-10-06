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


class TutorReply(BaseModel):
    """Ответ тьютора за ход: текст ученику и выбранный уровень подсказки."""

    reply: str
    # Уровень решает модель (она судит, застрял ли ученик), границы держит код.
    hint_level: int = 0
