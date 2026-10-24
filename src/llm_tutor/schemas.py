"""Доменные модели тьютора: события, наблюдения, вердикты, состояние сессии.

Отдельный от ``llm.schemas`` слой: здесь — предметная область (то, что живёт
в БД и коде), а там — транспортные модели запросов к провайдеру.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Role = Literal["system", "user", "assistant"]

# Откуда пришло свидетельство об ученике (по убыванию надёжности).
# ``explain`` — объяснение модуля новичку (срез 85): слабое, закрытые занятия не
# переоткрывает (``student/route._failures_since``).
EventSource = Literal["autotest", "checked", "rubric", "dialogue", "self", "explain"]

# Режим прохода узла (см. student/planner.py).
NodeMode = Literal["skip", "verify", "compressed", "full", "reinforce", "revisit", "review"]

# Фаза ведения занятия (см. student/guide.py).
GuidePhase = Literal["explain", "practice", "check"]

# Место узла в маршруте: закрыт, в работе сейчас или ещё впереди.
RouteStepStatus = Literal["closed", "current", "claimed", "ahead"]

# Тип связи между концептами графа курса.
EdgeType = Literal["requires", "part_of", "leads_to"]

# Тип задания: choice/short проверяются кодом (grader/autocheck.py),
# open/code — рубрикой (Срез 6).
AnswerType = Literal["open", "code", "choice", "short"]


class Section(BaseModel):
    """Раздел занятия (срез 53): шаг внутри узла со своим объяснением и заданиями.

    Своих рёбер и владения у раздела нет — это не узел графа.
    """

    # Пробелы по краям снимаются: «Series » иначе не нашлось бы в конспекте
    # заголовком «## Series» (аудит среза 53, L2).
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)


class Concept(BaseModel):
    """Узел графа курса (концепт); с разделами — занятие."""

    id: str
    name: str
    difficulty: float = Field(default=0.5, ge=0.0, le=1.0)
    description: str | None = None
    source_url: str | None = None
    # Модуль курса (1–10). Проставляет загрузчик по seed-файлу модуля (срез 24);
    # у узлов из тестов и данных до модулей — первый модуль.
    topic_id: int = Field(default=1, ge=1)
    # Убранный из seed узел не удаляется (на него ссылаются события), а гасится.
    active: bool = True
    # Разделы занятия по порядку; пусто — узел в старом формате (тема).
    sections: list[Section] = Field(default_factory=list)


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
    # Задание с кодом, который запускается в браузере (Pyodide): заготовка в
    # редакторе, подготовка данных до решения и проверки assert после него.
    starter: str | None = None
    setup: str | None = None
    tests: str | None = None
    # Эталонное решение: на нём прогоняются тесты задания (course.build.check).
    # В БД не хранится — это материал автора курса, а не ученика.
    solution: str | None = None
    # Убранное из seed задание не удаляется (на него ссылаются события), а гасится.
    active: bool = True
    # Раздел главного концепта задания (срез 53); у итогового задания занятия
    # (``final``) раздела нет.
    section: str | None = None
    final: bool = False

    @property
    def runnable(self) -> bool:
        """Можно ли проверить задание запуском тестов."""
        return self.answer_type == "code" and bool((self.tests or "").strip())

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
        if self.answer_type != "code" and (self.starter or self.setup or self.tests or self.solution):
            raise ValueError(
                f"starter/setup/tests/solution — только у code-задания (задание {self.id})"
            )
        if self.final and self.section is not None:
            raise ValueError(f"у итогового задания {self.id} не бывает раздела")
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


# Вопрос об общем уровне: «с нуля», «немного», «уверенно» (срез 22).
SURVEY_LEVEL_OPTIONS = 3


class SurveyBlock(BaseModel):
    """Блок анкеты модуля: ключ факта, имя для сводки, вопрос, пример API, темы."""

    model_config = ConfigDict(frozen=True)

    key: str
    title: str
    question: str
    example: str
    # Блок без тем ничего не говорит о маршруте — это ошибка seed.
    concepts: tuple[str, ...] = Field(min_length=1)


class SurveyConfig(BaseModel):
    """Анкета модуля: вопрос об общем уровне и вопросы по блокам (срез 26).

    Живёт в seed модуля: у каждого модуля свои блоки, а правила ветвления
    общие (``student/survey.py``).
    """

    model_config = ConfigDict(frozen=True)

    level_key: str
    level_question: str
    level_options: tuple[str, ...]
    blocks: tuple[SurveyBlock, ...]
    # «Уверенно» в вопросе об уровне: эти блоки знакомы без вопроса.
    assumed_by_confident: tuple[str, ...] = ()
    # Фундамент: «Впервые вижу» здесь — дальше всё тоже незнакомо.
    foundation: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> "SurveyConfig":
        """Анкета согласована сама с собой: иначе бот упал бы посреди вопросов."""
        if len(self.level_options) != SURVEY_LEVEL_OPTIONS:
            raise ValueError("в вопросе об уровне должно быть три варианта")
        if not self.blocks:
            raise ValueError("в анкете модуля нет блоков")
        keys = [self.level_key, *(block.key for block in self.blocks)]
        if len(set(keys)) != len(keys):
            raise ValueError(f"Дубли ключей анкеты: {keys}")
        unknown = sorted(
            (set(self.assumed_by_confident) | set(self.foundation))
            - {block.key for block in self.blocks}
        )
        if unknown:
            raise ValueError(f"анкета ссылается на неизвестные блоки: {unknown}")
        return self

    @property
    def keys(self) -> tuple[str, ...]:
        """Ключи фактов анкеты: вопрос об уровне и все блоки."""
        return (self.level_key, *(block.key for block in self.blocks))

    def block(self, key: str) -> SurveyBlock:
        """Блок по ключу факта (``KeyError``, если такого нет)."""
        for block in self.blocks:
            if block.key == key:
                return block
        raise KeyError(f"Нет блока анкеты с ключом {key!r}")


class ExplainIdea(BaseModel):
    """Ключевая идея модуля для «Объясни новичку» (срез 85).

    ``text`` и ``expect`` видят оценщик и наставник, ученик — только в отчёте;
    новичку они не передаются никогда.
    """

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    id: str = Field(min_length=1)
    lesson: str = Field(min_length=1)
    text: str = Field(min_length=1)
    required: bool = False
    # Что должен сказать ученик, чтобы идея считалась объяснённой (для оценщика).
    expect: str = Field(min_length=1)


class ExplainPitfall(BaseModel):
    """Типичная загвоздка модуля: новичок «понимает» с этой ошибкой.

    ``belief`` — фраза новичка (её он и получает), ``fix`` — почему это неверно:
    видят оценщик и наставник, ученик — в отчёте.
    """

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    id: str = Field(min_length=1)
    lesson: str = Field(min_length=1)
    belief: str = Field(min_length=1)
    fix: str = Field(min_length=1)


class ExplainConfig(BaseModel):
    """Эталон модуля для «Объясни новичку»: идеи и загвоздки.

    Числа (5–8 идей, обязательных ≥ 3, загвоздок ≥ 2) и принадлежность занятий
    модулю проверяет ``course.seed._check_explain``.
    """

    model_config = ConfigDict(frozen=True)

    ideas: list[ExplainIdea]
    pitfalls: list[ExplainPitfall]


class Topic(BaseModel):
    """Модуль курса: номер, название, вводная и анкета (срез 24)."""

    model_config = ConfigDict(frozen=True)

    number: int = Field(ge=1)
    title: str
    intro: str
    survey: SurveyConfig
    active: bool = True
    # Модуль открыт ученикам (срез 53): неопубликованный виден в маршруте как
    # «скоро», но войти в него нельзя.
    published: bool = True
    # Эталон «Объясни новичку» (срез 85); ``None`` — режим для модуля недоступен.
    explain: ExplainConfig | None = None


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


class ReviewRow(BaseModel):
    """Закрытое занятие на лестнице повторений (таблица ``review_schedule``)."""

    concept_id: str
    # Ступень лестницы: индекс в ``student.review.LADDER_DAYS`` (6 ступеней;
    # согласованность проверяет тест).
    step: int = Field(ge=0, le=5)
    # Срок повторения, unix-секунды UTC.
    due_at: float


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
    # Модуль, к которому относится материал: RAG не подмешивает будущие модули.
    topic_id: int = Field(default=1, ge=1)


class RouteStep(BaseModel):
    """Один узел маршрута ученика."""

    concept_id: str
    mode: NodeMode
    status: RouteStepStatus
    # Когда тема закрыта последний раз (unix time). Провалы ПОСЛЕ закрытия
    # открывают её снова, а у открывшейся время остаётся меткой «была закрыта»:
    # по ней тема прошлого модуля попадает в рабочий участок текущего.
    closed_at: float | None = None
    # Закрыта после второй ошибки на проверке урока, а не доказательством
    # (срез 28): в маршруте видна как ⚠️, пока владение не станет уверенным.
    weak: bool = False


class Route(BaseModel):
    """Путь от текущего положения до цели со снимком состояния узлов."""

    goal_concept_id: str | None = None
    steps: list[RouteStep] = Field(default_factory=list)
    # Модуль, над которым идёт работа (None — открытых тем нет нигде).
    topic_id: int | None = None
    # Модули, о прохождении которых ученику уже сказали «🎉»: возврат к их
    # темам не объявляет модуль пройденным повторно.
    completed_topics: list[int] = Field(default_factory=list)

    @property
    def closed_count(self) -> int:
        """Сколько узлов маршрута уже закрыто."""
        return sum(1 for step in self.steps if step.status == "closed")


class ReviewState(BaseModel):
    """Серия повторения пройденного (срез 80): разминка, «Срочное» или «Освежить».

    Живёт в ``SessionState.review`` и не трогает поля урока: после серии занятие
    продолжается ровно с того места, где остановилось.
    """

    kind: Literal["warmup", "due", "refresh"]
    # Занятия, которые ещё не спрошены (без висящего).
    queue: list[str] = Field(default_factory=list)
    # Занятие и задание, которые висят сейчас; ответ засчитывается этому занятию.
    node_id: str | None = None
    item_id: int | None = None
    # Ученик получил помощь по висящему заданию (спросил тьютора, «Застрял»).
    hinted: bool = False
    # Сколько занятий в серии (занятия без подходящих заданий не считаются).
    total: int = 0
    correct: int = 0
    answered: int = 0


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
    # Задания, уже выданные в текущем заходе по узлу урока: одно и то же
    # задание не идёт дважды подряд. Когда список исчерпан, он обнуляется и
    # задания идут по второму кругу (повтор — слабым свидетельством).
    lesson_item_ids: list[int] = Field(default_factory=list)
    last_activity: float | None = None
    # Урок за ручку (срез 28): части объяснения текущей темы и сколько из них
    # уже показано; ошибки на проверке темы (второй шанс, потом — дальше).
    lesson_parts: list[str] = Field(default_factory=list)
    lesson_part: int = 0
    check_misses: int = 0
    # Тема, к которой ученик прыгнул из меню в модуль с непройденной анкетой:
    # урок после анкеты начнётся с неё, даже если FSM потерян (/start, рестарт).
    jump_node_id: str | None = None
    # Проход начат просьбой «закрой тему», а не проверкой знакомого
    # по анкете: серия до закрытия у него длиннее (``close_success_streak``).
    close_requested: bool = False
    # Занятие с разделами (срез 53): текущий раздел, пройденные в этом заходе,
    # пройденные после двух ошибок и сдано ли итоговое задание. Вход в новое
    # занятие обнуляет все эти поля (``guide.SECTION_RESET``).
    section_id: str | None = None
    passed_sections: list[str] = Field(default_factory=list)
    weak_sections: list[str] = Field(default_factory=list)
    # Разделы, по которым в банке нет ни одного задания. Урок ещё
    # пишется: спрашивать по такому разделу нечем, и держать на нём занятие
    # нельзя — иначе ученик запирается в недописанном уроке. Это не «пройден»
    # (ученик ничего не доказал), но и не «впереди»: отдельный статус в полосе
    # разделов.
    skipped_sections: list[str] = Field(default_factory=list)
    final_passed: bool = False
    # Итоговое сдано после двух ошибок (срез 54): занятие закроется с ⚠️.
    final_weak: bool = False
    # Идущая серия повторения (срез 80); ``None`` — серии нет.
    review: ReviewState | None = None


# --- «Объясни новичку» (срез 85): состояние и итог сессии ---

IdeaState = Literal["missing", "partial", "correct", "wrong"]

# Почему сессия закончилась: кнопкой, после «кажется, понял», по лимиту реплик
# или брошенной (долго без ходов).
EndReason = Literal["button", "coverage", "limit", "stale"]


class IdeaProgress(BaseModel):
    """Где ученик сейчас с одной идеей эталона."""

    state: IdeaState = "missing"
    # Дословная цитата ученика, на которой оценщик основал вердикт.
    quote: str | None = None
    # Идея стала верной после того, как её затронул наставник: вес вдвое меньше.
    mentor: bool = False


class PitfallUse(BaseModel):
    """Загвоздка, которую новичок подбросил на ходу ``turn``."""

    id: str
    turn: int
    # Исправил ли ученик ошибку новичка (``None`` — ещё не ясно).
    fixed: bool | None = None


class ExplainState(BaseModel):
    """Живое состояние сессии (JSON в ``explain_sessions.state``)."""

    mode: Literal["novice", "mentor"] = "novice"
    ideas: dict[str, IdeaProgress] = Field(default_factory=dict)
    pitfalls_used: list[PitfallUse] = Field(default_factory=list)
    # Загвоздки, которые ученик назвал сам, без подсказки новичка.
    pitfalls_named: list[str] = Field(default_factory=list)
    # Идеи, которые затронул наставник.
    mentor_ideas: list[str] = Field(default_factory=list)
    student_turns: int = 0
    mentor_turns: int = 0
    pasted_turns: int = 0
    # Сколько раз ученик просил «просто дай ответ».
    answer_requests: int = 0
    # Ходов подряд без изменения покрытия.
    stalled_turns: int = 0
    last_pitfall_turn: int | None = None
    wrap_up_offered: bool = False
    # Какой запасной вопрос новичка следующий (по кругу).
    fallback_index: int = 0


class IdeaReport(BaseModel):
    """Идея в отчёте: состояние и — если не верно — эталон."""

    id: str
    lesson: str
    state: IdeaState
    quote: str | None = None
    mentor: bool = False
    reference: str | None = None


class PitfallReport(BaseModel):
    """Загвоздка в отчёте; ``fix`` — у пропущенной и у неразобранной (``unclear``)."""

    id: str
    lesson: str
    outcome: Literal["named", "fixed", "missed", "unclear"]
    fix: str | None = None


class ExplainReport(BaseModel):
    """Отчёт сессии (JSON в ``explain_sessions.report``); собирает код."""

    topic_id: int
    required_done: int
    required_total: int
    ideas_done: int
    ideas_total: int
    ideas: list[IdeaReport] = Field(default_factory=list)
    pitfalls: list[PitfallReport] = Field(default_factory=list)
    # Занятия «повторить»: сначала с ошибками, потом с пробелами.
    review_lessons: list[str] = Field(default_factory=list)
    student_turns: int = 0
    mentor_used: bool = False
    pasted_turns: int = 0
    end_reason: EndReason


class ExplainSession(BaseModel):
    """Строка ``explain_sessions``."""

    id: int
    topic_id: int
    started_at: float
    updated_at: float
    ended_at: float | None = None
    status: Literal["active", "finished"] = "active"
    end_reason: EndReason | None = None
    state: ExplainState = Field(default_factory=ExplainState)
    report: ExplainReport | None = None


class ExplainTurn(BaseModel):
    """Строка ``explain_turns``: реплика сессии."""

    id: int
    session_id: int
    ts: float
    role: Literal["student", "novice", "mentor"]
    content: str
    meta: dict = Field(default_factory=dict)
