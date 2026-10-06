"""Доменные модели тьютора: события, наблюдения, вердикты, состояние сессии.

Отдельный от ``llm.schemas`` слой: здесь — предметная область (то, что живёт
в БД и коде), а там — транспортные модели запросов к провайдеру.
"""

from typing import Literal

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant"]

# Откуда пришло свидетельство об ученике (по убыванию надёжности).
EventSource = Literal["autotest", "checked", "rubric", "dialogue", "self"]

# Режим прохода узла (см. student/planner.py).
NodeMode = Literal["skip", "verify", "compressed", "full", "reinforce", "revisit", "review"]


class Message(BaseModel):
    """Одна реплика диалога, сохраняемая в ``messages``."""

    role: Role
    content: str
    ts: float | None = None
    session_id: int | None = None
    id: int | None = None
    meta: str | None = None  # JSON: вердикт грейдера, время шага


class Event(BaseModel):
    """Атомарное свидетельство об ученике — запись в журнал ``events``."""

    source: EventSource
    result: float = Field(ge=0.0, le=1.0)  # 1 = верно, 0 = неверно
    concept_id: str | None = None
    item_id: int | None = None
    weight: float = Field(default=1.0, ge=0.0)
    citation: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    hints_used: int = Field(default=0, ge=0)
    time_spent: float | None = Field(default=None, ge=0.0)
    ts: float | None = None


class Observation(BaseModel):
    """Наблюдение, извлечённое из диалога (экстрактор, Фаза 2)."""

    concept_id: str
    correct: bool
    weight: float = Field(default=1.0, ge=0.0)
    misconception: str | None = None
    citation: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class CriterionResult(BaseModel):
    """Результат по одному бинарному критерию рубрики."""

    criterion: str
    passed: bool
    quote: str | None = None  # дословная цитата из ответа как основание


class GradeResult(BaseModel):
    """Вердикт грейдера по заданию."""

    criteria: list[CriterionResult]
    score: float = Field(ge=0.0, le=1.0)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class PlannedNode(BaseModel):
    """Узел в маршруте ученика, выданный планировщиком."""

    concept_id: str
    mode: NodeMode
    priority: float = Field(ge=0.0)


class Chunk(BaseModel):
    """Фрагмент материала курса для RAG-поиска."""

    source_url: str
    content: str
    seq: int
    id: int | None = None
    concept_id: str | None = None
    section: str | None = None


class SessionState(BaseModel):
    """Состояние сессии (JSON в ``sessions.state``), меняется после каждого хода."""

    current_node_id: str | None = None
    mode: NodeMode | None = None
    hint_level: int = 0
    pending_item_id: int | None = None
    attempts: int = 0
    plan_snapshot: list[PlannedNode] = Field(default_factory=list)
    last_activity: float | None = None
