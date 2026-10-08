"""Доменные модели тьютора: события, наблюдения, вердикты, состояние сессии.

Отдельный от ``llm.schemas`` слой: здесь — предметная область (то, что живёт
в БД и коде), а там — транспортные модели запросов к провайдеру.
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Role = Literal["system", "user", "assistant"]

# Откуда пришло свидетельство об ученике (по убыванию надёжности).
EventSource = Literal["autotest", "checked", "rubric", "dialogue", "self"]

# Режим прохода узла (см. student/planner.py).
NodeMode = Literal["skip", "verify", "compressed", "full", "reinforce", "revisit", "review"]

# Фаза ведения занятия (см. student/guide.py).
GuidePhase = Literal["explain", "practice", "check"]

# Место узла в маршруте: закрыт, в работе сейчас или ещё впереди.
RouteStepStatus = Literal["closed", "current", "ahead"]

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
    # Убранный из seed узел не удаляется (на него ссылаются события), а гасится.
    active: bool = True


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
    # Убранное из seed задание не удаляется (на него ссылаются события), а гасится.
    active: bool = True

    @model_validator(mode="after")
    def _check_reference_answer(self) -> "Item":
        """Проверяемая связка тип ↔ варианты ↔ эталон.

        Битый эталон опаснее отсутствующего: без этой проверки задание с
        индексом вне диапазона молча писал бы result=0 за верный ответ и
        отравлял модель ученика.
        """
        if self.answer_type == "choice":
            if not self.options:
                raise ValueError(f"у choice-задания {self.id} должны быть варианты")
            index = (self.answer or "").strip()
            if not index.isdigit() or not 0 <= int(index) < len(self.options):
                raise ValueError(
                    f"эталон choice-задания {self.id} — индекс в 0..{len(self.options) - 1}"
                )
        elif self.answer_type == "short" and not (self.answer or "").strip():
            raise ValueError(f"у short-задания {self.id} должен быть непустой эталон")
        return self


class Rubric(BaseModel):
    """Рубрика: набор атомарных критериев для открытых и код-ответов."""

    id: int
    name: str
    active: bool = True


class Criterion(BaseModel):
    """Атомарный бинарный критерий рубрики с калибровочными примерами."""

    id: int
    rubric_id: int
    criterion: str
    weight: float = Field(default=1.0, ge=0.0)
    positive_example: str | None = None
    negative_example: str | None = None
    active: bool = True


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
    # id критерия рубрики (если результат получен по рубрике) — по нему
    # результаты сопоставляются с разметкой, а не по порядку списка.
    criterion_id: int | None = None


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


class RouteStep(BaseModel):
    """Один узел маршрута ученика."""

    concept_id: str
    mode: NodeMode
    status: RouteStepStatus


class Route(BaseModel):
    """Путь от текущего положения до цели со снимком состояния узлов."""

    goal_concept_id: str | None = None
    steps: list[RouteStep] = Field(default_factory=list)

    @property
    def closed_count(self) -> int:
        """Сколько узлов маршрута уже закрыто."""
        return sum(1 for step in self.steps if step.status == "closed")


class SessionState(BaseModel):
    """Состояние сессии (JSON в ``sessions.state``), меняется после каждого хода."""

    current_node_id: str | None = None
    mode: NodeMode | None = None
    hint_level: int = 0
    pending_item_id: int | None = None
    attempts: int = 0
    route: Route | None = None
    phase: GuidePhase = "explain"
    node_streak: int = 0
    # Ученик получил помощь по текущему заданию (спросил или попросил глубины):
    # ответ после этого чистым не считается, сколько бы ни говорила модель.
    task_hinted: bool = False
    # Задания, уже выданные в текущем проверочном проходе: одно и то же
    # задание в одном проходе не спрашивается дважды (защита от накрутки).
    verify_item_ids: list[int] = Field(default_factory=list)
    last_activity: float | None = None
