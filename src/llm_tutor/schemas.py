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

# Тип связи между концептами графа курса.
EdgeType = Literal["requires", "part_of", "leads_to"]

# Тип задания: choice/short проверяются кодом (grader/autocheck.py),
# open/code — рубрикой (Срез 6).
AnswerType = Literal["open", "code", "choice", "short"]


class Concept(BaseModel):
    """Узел графа курса (концепт)."""

    id: str
    name: str
    difficulty: float = Field(default=0.5, ge=0.0, le=1.0)
    description: str | None = None
    source_url: str | None = None


class Edge(BaseModel):
    """Ребро графа курса: ``from_id`` требуется для ``to_id``.

    ``hard=True`` — без пререквизита узел заблокирован; ``hard=False`` —
    мягкий пререквизит (штраф к приоритету, не блокировка).
    """

    from_id: str
    to_id: str
    type: EdgeType = "requires"
    hard: bool = True
    weight: float = Field(default=1.0, ge=0.0)


class Message(BaseModel):
    """Одна реплика диалога, сохраняемая в ``messages``."""

    role: Role
    content: str
    ts: float | None = None
    session_id: int | None = None
    id: int | None = None
    meta: str | None = None  # JSON: вердикт грейдера, время шага


class Item(BaseModel):
    """Задание из банка: что спрашиваем, как проверяем и какой концепт меряем."""

    id: int
    prompt: str
    answer_type: AnswerType = "open"
    # Веса концептов, на которые начисляется свидетельство: {"groupby": 1.0}.
    concept_weights: dict[str, float] = Field(default_factory=dict)
    difficulty: float = Field(default=0.5, ge=0.0, le=1.0)
    options: list[str] = Field(default_factory=list)  # варианты для choice
    answer: str | None = None  # эталон: для choice — индекс варианта, иначе текст
    rubric_id: int | None = None


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
