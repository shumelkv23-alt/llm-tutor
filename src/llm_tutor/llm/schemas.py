"""Модели сообщений для общения с LLM (OpenAI-совместимый формат OpenRouter)."""

from typing import Literal

from pydantic import BaseModel, Field

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
    # Модель заметила, что ученик просит глубины («не понял, объясни ещё»):
    # тогда код переводит узел в усиленный проход и вперёд не пускает.
    student_stuck: bool = False
    # Модель заметила просьбу закрыть текущую тему: код входит в проверочный
    # проход. Само заявление узел не закрывает — нужны свидетельства.
    wants_close_topic: bool = False


# --- «Объясни новичку» (срез 86) ---


class IdeaVerdict(BaseModel):
    """Вердикт оценщика по одной идее эталона в реплике ученика."""

    id: str
    verdict: Literal["correct", "partial", "wrong"]
    # Дословный кусок реплики — основание вердикта; без него «correct» не засчитывается.
    quote: str | None = None


class ExplainObservation(BaseModel):
    """Что скрытый оценщик увидел в одной реплике. Решения по нему принимает код."""

    ideas: list[IdeaVerdict] = Field(default_factory=list)
    pitfalls_named: list[str] = Field(default_factory=list)
    pitfall_fixed: bool | None = None
    pasted: bool = False
    contradiction: str | None = None
    asks_for_answer: bool = False
    frustrated: bool = False


class NoviceReply(BaseModel):
    """Реплика новичка."""

    reply: str
