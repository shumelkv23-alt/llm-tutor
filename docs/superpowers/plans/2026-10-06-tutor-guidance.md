# План реализации: ведение ученика и дорожная карта

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Тьютор перестаёт быть реактивным: он ведёт ученика по маршруту к цели, сам закрывает узлы по решённым задачам, отступает, когда ученик просит глубины, и отвечает на вопросы рядом с курсом.

**Architecture:** Логика ведения вынесена в отдельный слой `student/guide.py` (машина состояний занятия), маршрут — в `student/route.py` (данные + снимок + пересмотр). `core/turn.py` остаётся транспортом: принял текст, спросил guide, записал результат одним коммитом.

**Tech Stack:** Python 3.12, aiogram 3, pydantic 2, SQLite, pytest + pytest-asyncio + respx. Запуск тестов: `uv run pytest`.

**Spec:** `docs/superpowers/specs/2026-10-06-tutor-guidance-design.md`

## Global Constraints

- Python 3.12; тесты только через `uv run pytest`; без сети (HTTP мокается respx).
- Покрытие проекта ≥ 80% (сейчас 95%) — новый код покрываем тестами наравне.
- Type hints на всех сигнатурах, docstrings у функций; комментарии по-русски, идентификаторы по-английски.
- Commit messages — по-русски, осмысленные, с трейлером `Co-Authored-By: Claude Code <noreply@anthropic.com>`.
- События (`events`) — источник истины; `mastery` — производная. Вердикты считает код, не модель.
- Материал курса никогда не попадает в `system`-сообщение.
- Правки схемы БД — только новой миграцией `NNN_*.sql` + `SCHEMA_VERSION`. Здесь миграций не нужно: состояние живёт в JSON.
- Существующие тесты, которые проверяют снятую политику, переписываются, а не удаляются молча.

## Файловая структура

| Файл | Ответственность |
|---|---|
| `src/llm_tutor/schemas.py` | доменные модели: `Route`, `RouteStep`, `GuidePhase`, правки `SessionState` |
| `src/llm_tutor/student/route.py` | **новый**: построение маршрута, дифф снимков, значимость, текст изменения |
| `src/llm_tutor/student/guide.py` | **новый**: фазы занятия, критерий закрытия узла, реакция на «застрял», переход к следующему |
| `src/llm_tutor/student/planner.py` | `mode_for_node()` — режим узла как отдельная функция (нужна маршруту) |
| `src/llm_tutor/llm/schemas.py` | флаг `student_stuck` в `TutorReply` |
| `src/llm_tutor/llm/prompts.py` | правила ведения, блок маршрута, новая политика «рядом с курсом» |
| `src/llm_tutor/core/context.py` | блок маршрута в пакете контекста |
| `src/llm_tutor/core/turn.py` | оркестрация хода через guide, `TurnReply` |
| `src/llm_tutor/rag/retriever.py` | удаление `has_searchable_content` (станет мёртвым) |
| `src/llm_tutor/bot/handlers.py` | `/plan` показывает маршрут; кнопки из `TurnReply` |
| `src/llm_tutor/config.py` | `guide_success_streak`, `route_min_significant_changes` |

---

## Срез 8. Вопросы рядом с курсом

### Task 1: Промпт «материала нет» разрешает ответ с пометкой

**Files:**
- Modify: `src/llm_tutor/llm/prompts.py` (константа `TUTOR_NO_MATERIAL_SYSTEM_PROMPT`)
- Test: `tests/test_prompts.py` (создать)

**Interfaces:**
- Consumes: ничего
- Produces: `TUTOR_NO_MATERIAL_SYSTEM_PROMPT: str` — текст правил на случай, когда фрагментов курса нет; используется в `tutor_system_prompt(hint_level, has_material=False)`

- [ ] **Step 1: Write the failing test**

```python
"""Тесты текстов промптов (правила, а не формулировки)."""

from llm_tutor.llm.prompts import TUTOR_NO_MATERIAL_SYSTEM_PROMPT, tutor_system_prompt


def test_no_material_prompt_allows_answering_but_marks_the_border() -> None:
    """Вопрос рядом с курсом должен получать ответ, а не отказ."""
    text = TUTOR_NO_MATERIAL_SYSTEM_PROMPT

    assert "за пределами" in text  # пометка границы
    assert "по своим знаниям" in text  # ответ разрешён


def test_no_material_prompt_forbids_passing_knowledge_as_course_material() -> None:
    text = TUTOR_NO_MATERIAL_SYSTEM_PROMPT

    assert "не приписывай курсу" in text.lower() or "не выдавай" in text.lower()


def test_tutor_system_prompt_picks_no_material_rules() -> None:
    assert TUTOR_NO_MATERIAL_SYSTEM_PROMPT in tutor_system_prompt(0, has_material=False)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_prompts.py -v`
Expected: FAIL — в текущем тексте нет «по своим знаниям» (промпт велит не отвечать по существу).

- [ ] **Step 3: Write minimal implementation**

Заменить в `src/llm_tutor/llm/prompts.py` константу целиком:

```python
# Когда материалов не нашлось: болтовню тьютор ведёт сам, а на содержательный
# вопрос отвечает по своим знаниям, ЯВНО помечая границу курса — иначе ученик
# примет ответ модели за материал темы (§7 архитектуры).
TUTOR_NO_MATERIAL_SYSTEM_PROMPT = (
    "Ты — тьютор по курсу машинного обучения mlcourse.ai, тема 1 «Pandas / EDA».\n"
    "Сейчас фрагментов материалов курса нет. Правила:\n"
    "1. Отвечай на русском, кратко и по-дружески.\n"
    "2. На приветствие, благодарность или короткую реплику ответь живо и естественно.\n"
    "3. На содержательный вопрос отвечай по своим знаниям, но честно помечай границу: "
    "«это уже за пределами темы 1». Не выдавай свои знания за материал курса.\n"
    "4. Где можешь — свяжи ответ с курсом: «в теме 1 это встречается, когда мы делаем вот это».\n"
    "5. Не приписывай курсу того, чего в нём нет: раз фрагментов нет, утверждать "
    "«в курсе сказано» нельзя.\n"
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_prompts.py -v`
Expected: PASS (3 теста)

- [ ] **Step 5: Commit**

```bash
git add tests/test_prompts.py src/llm_tutor/llm/prompts.py
git commit -m "Срез 8: промпт без материала разрешает ответ с пометкой границы"
```

---

### Task 2: Убрать детерминированный отказ, отдать вопрос модели

**Files:**
- Modify: `src/llm_tutor/core/turn.py` (`_tutor_branch`, импорты)
- Modify: `src/llm_tutor/llm/prompts.py` (удалить `NO_COURSE_ANSWER`)
- Modify: `src/llm_tutor/rag/retriever.py` (удалить `has_searchable_content`)
- Test: `tests/test_turn.py`, `tests/test_e2e_topic01.py` (переписать отказные тесты)

**Interfaces:**
- Consumes: `TUTOR_NO_MATERIAL_SYSTEM_PROMPT` из Task 1 (через `core/context.py` — уже подключено)
- Produces: тьюторский путь всегда вызывает модель; `NO_COURSE_ANSWER` и `has_searchable_content` больше не существуют

- [ ] **Step 1: Write the failing test**

В `tests/test_turn.py` заменить `test_offtopic_question_is_refused_without_llm` на:

```python
async def test_offtopic_question_goes_to_model(conn, settings) -> None:
    """Вопрос рядом с курсом уходит модели, а не в детерминированный отказ."""
    ingest_text(conn, "# T\n\n## Grouping\n\ngroupby aggregates rows\n", "u")
    client = _FakeTutor("это за пределами темы 1, но вот как это работает")

    reply = await handle_turn(
        conn, client, "m", "how do I train a neural network?", now=1.0, settings=settings
    )

    assert reply == "это за пределами темы 1, но вот как это работает"
    assert len(client.calls) == 1  # модель спросили
```

В `tests/test_e2e_topic01.py` заменить `test_without_material_refuses_without_calling_model` на:

```python
@respx.mock
async def test_without_material_still_answers_with_border_mark() -> None:
    """Материала нет — модель отвечает и помечает границу, отказ снят."""
    settings = Settings(
        _env_file=None, openrouter_api_key="test-key", telegram_bot_token="test-token"
    )
    conn = get_conn(":memory:")
    migrate(conn)
    load_seed(conn)  # материал специально не загружаем
    route = respx.post(OPENROUTER).mock(
        return_value=_completion(
            json.dumps({"reply": "Это за пределами темы 1", "hint_level": 0})
        )
    )
    client = _client(settings)
    try:
        reply = await handle_turn(
            conn, client, "m", "как работает groupby?", now=1.0, settings=settings
        )

        assert reply == "Это за пределами темы 1"
        assert route.called
    finally:
        await client.aclose()
        conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_turn.py::test_offtopic_question_goes_to_model tests/test_e2e_topic01.py::test_without_material_still_answers_with_border_mark -v`
Expected: FAIL — `handle_turn` возвращает `NO_COURSE_ANSWER`, модель не вызывается.

- [ ] **Step 3: Write minimal implementation**

В `src/llm_tutor/core/turn.py` удалить из `_tutor_branch` блок:

```python
    if not package.found_material and has_searchable_content(user_text):
        # Осмысленный вопрос, а материала нет вовсе — честный отказ без LLM.
        # (Материал, вытесненный бюджетом, сюда не попадает: см. found_material.)
        return NO_COURSE_ANSWER, [], [], idle_state
```

и убрать ставшие ненужными импорты `NO_COURSE_ANSWER`, `has_searchable_content`.
Из `src/llm_tutor/llm/prompts.py` удалить константу `NO_COURSE_ANSWER`.
Из `src/llm_tutor/rag/retriever.py` удалить `has_searchable_content`.

Проверить, что ничего не осталось:

```bash
grep -rn "NO_COURSE_ANSWER\|has_searchable_content" src/ tests/
```

Ожидаемо: пусто (или только в переписанных тестах — их тоже поправить).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: PASS, число тестов не меньше прежнего.

- [ ] **Step 5: Commit**

```bash
git add -A src/ tests/
git commit -m "Срез 8: снят детерминированный отказ «не нашёл в курсе»"
```

---

## Срез 9. Маршрут

### Task 3: Модели маршрута и поля состояния

**Files:**
- Modify: `src/llm_tutor/schemas.py`
- Test: `tests/test_schemas.py`

**Interfaces:**
- Consumes: `NodeMode` (уже есть)
- Produces:
  - `GuidePhase = Literal["explain", "practice", "check"]`
  - `RouteStep(concept_id: str, mode: NodeMode, status: Literal["closed", "current", "ahead"])`
  - `Route(goal_concept_id: str | None, steps: list[RouteStep], built_at: float | None)`, свойство `closed_count -> int`
  - `SessionState.route: Route | None`, `SessionState.phase: GuidePhase`, `SessionState.node_streak: int`

- [ ] **Step 1: Write the failing test**

Добавить в `tests/test_schemas.py`:

```python
def test_route_roundtrip_and_closed_count() -> None:
    route = Route(
        goal_concept_id="churn_eda_case",
        built_at=1.0,
        steps=[
            RouteStep(concept_id="groupby", mode="full", status="closed"),
            RouteStep(concept_id="agg_functions", mode="compressed", status="current"),
            RouteStep(concept_id="summary_tables", mode="full", status="ahead"),
        ],
    )

    assert Route.model_validate_json(route.model_dump_json()) == route
    assert route.closed_count == 1


def test_session_state_defaults_for_guidance() -> None:
    state = SessionState()

    assert state.route is None
    assert state.phase == "explain"
    assert state.node_streak == 0


def test_old_state_with_plan_snapshot_is_still_readable() -> None:
    """Сохранённое состояние прошлой версии не должно ронять чтение."""
    old = '{"plan_snapshot": [{"concept_id": "x", "mode": "full", "priority": 1.0}]}'

    state = SessionState.model_validate_json(old)

    assert state.route is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_schemas.py -v`
Expected: FAIL — `ImportError: cannot import name 'Route'`.

- [ ] **Step 3: Write minimal implementation**

В `src/llm_tutor/schemas.py` добавить после `NodeMode`:

```python
# Фаза ведения занятия (см. student/guide.py).
GuidePhase = Literal["explain", "practice", "check"]

# Состояние узла в маршруте: закрыт, в работе сейчас или ещё впереди.
RouteStepStatus = Literal["closed", "current", "ahead"]
```

и перед `SessionState`:

```python
class RouteStep(BaseModel):
    """Один узел в маршруте ученика."""

    concept_id: str
    mode: NodeMode
    status: RouteStepStatus


class Route(BaseModel):
    """Путь от текущего положения до цели со снимком состояния узлов."""

    goal_concept_id: str | None = None
    steps: list[RouteStep] = Field(default_factory=list)
    built_at: float | None = None

    @property
    def closed_count(self) -> int:
        """Сколько узлов маршрута закрыто."""
        return sum(1 for step in self.steps if step.status == "closed")
```

В `SessionState` заменить `plan_snapshot: list[PlannedNode] = Field(default_factory=list)` на:

```python
    route: Route | None = None
    phase: GuidePhase = "explain"
    node_streak: int = 0
```

Проверить, что `plan_snapshot` нигде не используется:

```bash
grep -rn "plan_snapshot" src/ tests/
```

Ожидаемо: пусто.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_tutor/schemas.py tests/test_schemas.py
git commit -m "Срез 9: модели маршрута и поля состояния занятия"
```

---

### Task 4: Режим узла как отдельная функция планировщика

**Files:**
- Modify: `src/llm_tutor/student/planner.py`
- Test: `tests/test_planner.py`

**Interfaces:**
- Consumes: внутренние `_choose_mode`, `_recent_failures`, `_urgency`, `beta.estimate`
- Produces: `mode_for_node(conn, graph, concept_id, *, now=None, settings=None) -> NodeMode` — режим прохода узла; `ready_nodes` начинает пользоваться ею

- [ ] **Step 1: Write the failing test**

Добавить в `tests/test_planner.py`:

```python
def test_mode_for_node_matches_ready_nodes(conn, settings) -> None:
    """Режим узла считается одинаково в маршруте и в маршрутном списке."""
    graph = _graph(["a", "b"], [("a", "b", True)])
    _set_mastery(conn, "a", 0.8)

    nodes = {node.concept_id: node.mode for node in ready_nodes(conn, graph, now=0.0, settings=settings)}

    for concept_id, mode in nodes.items():
        assert mode_for_node(conn, graph, concept_id, now=0.0, settings=settings) == mode


def test_mode_for_node_is_skip_for_confident_mastery(conn, settings) -> None:
    graph = _graph(["a"], [])
    _set_mastery(conn, "a", 0.95, total=40.0)

    assert mode_for_node(conn, graph, "a", now=0.0, settings=settings) == "skip"
```

Добавить `mode_for_node` в импорт из `llm_tutor.student.planner` в шапке файла.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_planner.py -v -k mode_for_node`
Expected: FAIL — `ImportError: cannot import name 'mode_for_node'`.

- [ ] **Step 3: Write minimal implementation**

В `src/llm_tutor/student/planner.py` добавить публичную функцию (логика — ровно та же, что была в цикле `ready_nodes`):

```python
def mode_for_node(
    conn: sqlite3.Connection,
    graph: CourseGraph,
    concept_id: str,
    *,
    now: float | None = None,
    settings: Settings | None = None,
) -> NodeMode:
    """Режим прохода узла по текущей модели ученика.

    Тот же расчёт, что в ``ready_nodes``: вынесен отдельно, потому что режим
    нужен и маршруту (``student/route.py``).
    """
    s = settings or get_settings()
    stamp = time.time() if now is None else now
    mastery = beta.estimate(conn, concept_id, now=stamp, settings=s)
    failures = _recent_failures(conn, concept_id, stamp, FAILURE_WINDOW_DAYS)
    weak_soft = any(
        beta.estimate(conn, node_id, now=stamp, settings=s).mean
        < s.mastery_verify_threshold
        for node_id in graph.soft_prerequisites(concept_id)
    )
    return _choose_mode(
        mastery,
        weak_soft_prereq=weak_soft,
        overdue=mastery.next_review is not None and stamp >= mastery.next_review,
        stuck=failures >= STUCK_FAILURES,
        settings=s,
    )
```

В цикле `ready_nodes` заменить вычисление `mode` на вызов `mode_for_node(conn, graph, node_id, now=stamp, settings=s)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: PASS (поведение `ready_nodes` не изменилось)

- [ ] **Step 5: Commit**

```bash
git add src/llm_tutor/student/planner.py tests/test_planner.py
git commit -m "Срез 9: режим узла вынесен в mode_for_node"
```

---

### Task 5: Построение маршрута и сравнение снимков

**Files:**
- Create: `src/llm_tutor/student/route.py`
- Test: `tests/test_route.py`

**Interfaces:**
- Consumes: `CourseGraph`, `planner.mode_for_node`, `beta.estimate`, `repos.get_fact`, `GOAL_CONCEPT_KEY` из `student.survey`
- Produces:
  - `build_route(conn, graph, *, goal_concept_id=None, current_node_id=None, previous=None, now=None, settings=None) -> Route`
  - `RouteChanges` (frozen dataclass с полями `closed`, `added`, `removed`, `current_changed`)
  - `diff_routes(previous, current) -> RouteChanges`
  - `is_significant(changes, *, min_steps=2) -> bool`
  - `format_route_change(changes) -> str`
  - `refresh(conn, state, graph, *, now=None, settings=None) -> tuple[Route, str | None]`

- [ ] **Step 1: Write the failing test**

Создать `tests/test_route.py`:

```python
"""Тесты маршрута: построение, снимок, пересмотр (Срез 9)."""

from llm_tutor.course.graph import CourseGraph
from llm_tutor.course.seed import load_seed
from llm_tutor.db import repos
from llm_tutor.schemas import Concept, Edge, Route, RouteStep
from llm_tutor.student import route as route_mod


def _graph(concepts: list[str], edges: list[tuple[str, str]]) -> CourseGraph:
    return CourseGraph(
        [Concept(id=cid, name=cid) for cid in concepts],
        [Edge(from_id=src, to_id=dst, hard=True) for src, dst in edges],
    )


def test_route_covers_ancestors_of_goal_in_topological_order(conn, settings) -> None:
    graph = _graph(["a", "b", "c", "d"], [("a", "b"), ("b", "c")])

    result = route_mod.build_route(
        conn, graph, goal_concept_id="c", current_node_id="b", now=0.0, settings=settings
    )

    assert [step.concept_id for step in result.steps] == ["a", "b", "c"]  # d вне цели
    assert result.goal_concept_id == "c"


def test_current_node_is_marked_current_others_ahead(conn, settings) -> None:
    graph = _graph(["a", "b"], [("a", "b")])

    result = route_mod.build_route(
        conn, graph, current_node_id="a", now=0.0, settings=settings
    )

    statuses = {step.concept_id: step.status for step in result.steps}
    assert statuses == {"a": "current", "b": "ahead"}


def test_confidently_mastered_node_is_closed(conn, settings) -> None:
    graph = _graph(["a", "b"], [("a", "b")])
    repos.upsert_concept(conn, Concept(id="a", name="a"))
    repos.upsert_concept(conn, Concept(id="b", name="b"))
    repos.upsert_mastery(conn, "a", alpha=38.0, beta=2.0, last_seen=0.0)

    result = route_mod.build_route(conn, graph, now=0.0, settings=settings)

    assert {step.concept_id: step.status for step in result.steps}["a"] == "closed"


def test_closed_status_survives_rebuild(conn, settings) -> None:
    """Узел, закрытый по задачам, не должен «раззакрыться» при пересчёте."""
    graph = _graph(["a", "b"], [("a", "b")])
    previous = Route(
        steps=[
            RouteStep(concept_id="a", mode="full", status="closed"),
            RouteStep(concept_id="b", mode="full", status="current"),
        ]
    )

    result = route_mod.build_route(
        conn, graph, current_node_id="b", previous=previous, now=0.0, settings=settings
    )

    assert {step.concept_id: step.status for step in result.steps}["a"] == "closed"


def test_diff_reports_closed_added_removed_and_current() -> None:
    previous = Route(
        steps=[
            RouteStep(concept_id="a", mode="full", status="current"),
            RouteStep(concept_id="b", mode="full", status="ahead"),
        ]
    )
    current = Route(
        steps=[
            RouteStep(concept_id="a", mode="full", status="closed"),
            RouteStep(concept_id="c", mode="full", status="current"),
        ]
    )

    changes = route_mod.diff_routes(previous, current)

    assert changes.closed == ("a",)
    assert changes.added == ("c",)
    assert changes.removed == ("b",)
    assert changes.current_changed is True


def test_small_reshuffle_is_not_significant() -> None:
    changes = route_mod.RouteChanges(closed=(), added=("b",), removed=("a",), current_changed=False)

    assert route_mod.is_significant(changes) is False


def test_closing_any_node_is_significant() -> None:
    changes = route_mod.RouteChanges(closed=("a",), added=(), removed=(), current_changed=False)

    assert route_mod.is_significant(changes) is True


def test_route_change_text_mentions_closed_node() -> None:
    changes = route_mod.RouteChanges(closed=("groupby",), added=(), removed=(), current_changed=False)

    assert "закрыт" in route_mod.format_route_change(changes).lower()


def test_route_on_real_seed_covers_path_to_goal(conn, settings) -> None:
    load_seed(conn)
    graph = CourseGraph.load(conn)

    result = route_mod.build_route(
        conn, graph, goal_concept_id="summary_tables", now=0.0, settings=settings
    )

    ids = {step.concept_id for step in result.steps}
    assert "groupby" in ids and "summary_tables" in ids
    assert "churn_eda_case" not in ids  # это за целью
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_route.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'llm_tutor.student.route'`.

- [ ] **Step 3: Write minimal implementation**

Создать `src/llm_tutor/student/route.py`:

```python
"""Маршрут: путь к цели, снимок состояния узлов и пересмотр (Срез 9).

Маршрут — это не список «что доступно сейчас», а путь до цели: предки цели
плюс сама цель (архитектура, §6.2). Снимок живёт в состоянии сессии, поэтому
видно, что изменилось, и можно не дёргать план по мелочам (§6.4).
"""

import sqlite3
import time
from dataclasses import dataclass

from llm_tutor.config import Settings, get_settings
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.schemas import Route, RouteStep
from llm_tutor.student import planner
from llm_tutor.student.survey import GOAL_CONCEPT_KEY


@dataclass(frozen=True)
class RouteChanges:
    """Что изменилось между снимком маршрута и новым расчётом."""

    closed: tuple[str, ...]
    added: tuple[str, ...]
    removed: tuple[str, ...]
    current_changed: bool


def _scope(graph: CourseGraph, goal_concept_id: str | None) -> list[str]:
    """Узлы маршрута в топопорядке: предки цели и сама цель."""
    if goal_concept_id is None:
        return graph.topo_order()
    graph.concept(goal_concept_id)  # падает, если цели нет в графе
    return [n for n in graph.topo_order() if n in graph.ancestors(goal_concept_id) | {goal_concept_id}]


def build_route(
    conn: sqlite3.Connection,
    graph: CourseGraph,
    *,
    goal_concept_id: str | None = None,
    current_node_id: str | None = None,
    previous: Route | None = None,
    now: float | None = None,
    settings: Settings | None = None,
) -> Route:
    """Строит маршрут; закрытые ранее узлы сохраняют свой статус."""
    s = settings or get_settings()
    stamp = time.time() if now is None else now
    closed_before = {step.concept_id for step in (previous.steps if previous else []) if step.status == "closed"}

    steps: list[RouteStep] = []
    for concept_id in _scope(graph, goal_concept_id):
        mode = planner.mode_for_node(conn, graph, concept_id, now=stamp, settings=s)
        if concept_id in closed_before or mode == "skip":
            status = "closed"
        elif concept_id == current_node_id:
            status = "current"
        else:
            status = "ahead"
        steps.append(RouteStep(concept_id=concept_id, mode=mode, status=status))
    return Route(goal_concept_id=goal_concept_id, steps=steps, built_at=stamp)


def diff_routes(previous: Route | None, current: Route) -> RouteChanges:
    """Сравнивает снимок с новым маршрутом."""
    old = {step.concept_id: step for step in (previous.steps if previous else [])}
    new = {step.concept_id: step for step in current.steps}
    return RouteChanges(
        closed=tuple(
            node_id
            for node_id, step in new.items()
            if step.status == "closed" and old.get(node_id, None) is not None and old[node_id].status != "closed"
        ),
        added=tuple(node_id for node_id in new if node_id not in old),
        removed=tuple(node_id for node_id in old if node_id not in new),
        current_changed=(
            next((step.concept_id for step in current.steps if step.status == "current"), None)
            != next((step.concept_id for step in (previous.steps if previous else []) if step.status == "current"), None)
        ),
    )


def is_significant(changes: RouteChanges, *, min_steps: int = 2) -> bool:
    """Стоит ли вообще говорить ученику о пересмотре (правило «малых различий»)."""
    return bool(changes.closed) or changes.current_changed or (
        len(changes.added) + len(changes.removed) >= min_steps
    )


def format_route_change(changes: RouteChanges) -> str:
    """Строка об изменении маршрута для ответа ученику."""
    parts = []
    if changes.closed:
        parts.append("закрыт " + ", ".join(changes.closed))
    if changes.added:
        parts.append("добавилось " + ", ".join(changes.added))
    return "Маршрут перестроен: " + "; ".join(parts) + "."


def goal_concept_id(conn: sqlite3.Connection) -> str | None:
    """Цель из анкеты (или ``None``, если ученик её не выбирал)."""
    return repos.get_fact(conn, GOAL_CONCEPT_KEY)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_route.py -v`
Expected: PASS (9 тестов)

- [ ] **Step 5: Commit**

```bash
git add src/llm_tutor/student/route.py tests/test_route.py
git commit -m "Срез 9: построение маршрута и сравнение снимков"
```

---

### Task 6: `/plan` показывает маршрут, ход сообщает о пересмотре

**Files:**
- Modify: `src/llm_tutor/bot/handlers.py` (`render_plan`)
- Modify: `src/llm_tutor/core/turn.py` (`handle_turn`: пересчёт маршрута после хода)
- Test: `tests/test_handlers.py`, `tests/test_turn.py`

**Interfaces:**
- Consumes: `route.build_route`, `route.diff_routes`, `route.is_significant`, `route.format_route_change`, `route.goal_concept_id`
- Produces:
  - `render_plan(conn, *, state=None, now=None, settings=None) -> str` — путь к цели вместо фронта
  - `handle_turn(...)` сохраняет маршрут в состоянии и дописывает сообщение о пересмотре

- [ ] **Step 1: Write the failing test**

В `tests/test_handlers.py` заменить `test_render_plan_lists_ready_node_with_mode` на:

```python
def test_render_plan_shows_route_progress(conn, settings) -> None:
    """Маршрут — это путь с прогрессом, а не список доступного."""
    load_seed(conn)
    repos.set_fact(conn, "goal_concept_id", "summary_tables")

    text = render_plan(conn, now=0.0, settings=settings)

    assert "из" in text  # «закрыто 0 из N»
    assert "Цель" in text
    assert "Сейчас" in text or "сейчас" in text
```

В `tests/test_turn.py` добавить:

```python
async def test_turn_records_route_in_state(conn, settings) -> None:
    """После хода в состоянии сессии лежит снимок маршрута."""
    load_seed(conn)
    await handle_turn(conn, _FakeTutor(), "m", "привет!", now=1.0, settings=settings)

    state = _state(conn)
    assert state.route is not None
    assert state.route.steps
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_handlers.py::test_render_plan_shows_route_progress tests/test_turn.py::test_turn_records_route_in_state -v`
Expected: FAIL — `render_plan` возвращает фронт («Что можно взять сейчас»), в состоянии нет `route`.

- [ ] **Step 3: Write minimal implementation**

В `src/llm_tutor/bot/handlers.py` переписать `render_plan`:

```python
def render_plan(
    conn: sqlite3.Connection,
    *,
    state: SessionState | None = None,
    now: float | None = None,
    settings: Settings | None = None,
) -> str:
    """Текст ``/plan``: маршрут к цели с прогрессом."""
    graph = CourseGraph.load(conn)
    if not graph.node_ids:
        return EMPTY_GRAPH_REPLY

    route = route_mod.build_route(
        conn,
        graph,
        goal_concept_id=route_mod.goal_concept_id(conn),
        current_node_id=(state or _first_state(conn)).current_node_id,
        now=now,
        settings=settings,
    )
    if not route.steps:
        return "Маршрут пуст — не выбрана цель."

    current = next((s for s in route.steps if s.status == "current"), None)
    ahead = [s for s in route.steps if s.status == "ahead"][:5]
    goal_name = (
        graph.concept(route.goal_concept_id).name if route.goal_concept_id else "вершина темы"
    )
    lines = [f"Маршрут: закрыто {route.closed_count} из {len(route.steps)}. Цель — {goal_name}."]
    if current is not None:
        lines.append(
            f"Сейчас: {graph.concept(current.concept_id).name} ({MODE_LABELS[current.mode]})."
        )
    if ahead:
        names = ", ".join(
            f"{graph.concept(step.concept_id).name} ({MODE_LABELS[step.mode]})" for step in ahead
        )
        lines.append(f"Дальше: {names}.")
    return "\n".join(lines)


def _first_state(conn: sqlite3.Connection) -> SessionState:
    """Состояние открытой сессии (или пустое, если сессии ещё нет)."""
    session_id = get_open_session(conn)
    return repos.get_session_state(conn, session_id) if session_id else SessionState()
```

В `src/llm_tutor/core/turn.py` в `handle_turn` перед `post_turn` добавить пересчёт маршрута:

```python
    route, route_note = route_mod.refresh(conn, new_state, graph, now=stamp, settings=s)
    new_state = new_state.model_copy(update={"route": route})
    if route_note:
        reply = f"{reply}\n\n{route_note}"
```

и в `src/llm_tutor/student/route.py` добавить:

```python
def refresh(
    conn: sqlite3.Connection,
    state: SessionState,
    graph: CourseGraph,
    *,
    now: float | None = None,
    settings: Settings | None = None,
) -> tuple[Route, str | None]:
    """Пересчитывает маршрут; возвращает снимок и сообщение о значимом изменении."""
    s = settings or get_settings()
    new = build_route(
        conn,
        graph,
        goal_concept_id=goal_concept_id(conn),
        current_node_id=state.current_node_id,
        previous=state.route,
        now=now,
        settings=s,
    )
    changes = diff_routes(state.route, new)
    note = (
        format_route_change(changes)
        if is_significant(changes, min_steps=s.route_min_significant_changes)
        else None
    )
    return new, note
```

В `src/llm_tutor/config.py` добавить в настройки:

```python
    # --- Ведение и маршрут, Срез 9–10 ---
    route_min_significant_changes: int = Field(default=2, ge=1)
    guide_success_streak: int = Field(default=2, ge=1)
```

и в `.env.example` — строки `ROUTE_MIN_SIGNIFICANT_CHANGES=2`, `GUIDE_SUCCESS_STREAK=2`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A src/ tests/ .env.example
git commit -m "Срез 9: маршрут в /plan и пересмотр в ходе"
```

---

## Срез 10. Ведение

### Task 7: Фазы занятия и критерий закрытия узла

**Files:**
- Create: `src/llm_tutor/student/guide.py`
- Test: `tests/test_guide.py`

**Interfaces:**
- Consumes: `SessionState` (`phase`, `node_streak`, `hint_level`, `current_node_id`), `beta.Mastery`, `Settings`
- Produces:
  - `register_answer(state, *, correct, hints_used, settings) -> SessionState` — обновляет счётчик и фазу
  - `is_node_closed(state, mastery, *, settings) -> bool` — критерий закрытия (оба пути)

- [ ] **Step 1: Write the failing test**

Создать `tests/test_guide.py`:

```python
"""Тесты ведения: фазы, критерий закрытия, «застрял» (Срез 10)."""

from llm_tutor.schemas import SessionState
from llm_tutor.student import beta, guide


def _mastery(mean: float, uncertainty: float) -> beta.Mastery:
    return beta.Mastery("x", 1.0, 1.0, mean, uncertainty, 0.0, None)


def test_two_correct_answers_without_hints_close_node(settings) -> None:
    state = SessionState(current_node_id="groupby")

    state = guide.register_answer(state, correct=True, hints_used=0, settings=settings)
    state = guide.register_answer(state, correct=True, hints_used=0, settings=settings)

    assert state.node_streak == 2
    assert guide.is_node_closed(state, _mastery(0.6, 0.2), settings=settings) is True


def test_answer_with_hints_resets_streak(settings) -> None:
    """Задача, решённая с подсказкой, доказательством не считается."""
    state = SessionState(current_node_id="groupby")

    state = guide.register_answer(state, correct=True, hints_used=0, settings=settings)
    state = guide.register_answer(state, correct=True, hints_used=2, settings=settings)

    assert state.node_streak == 0
    assert guide.is_node_closed(state, _mastery(0.6, 0.2), settings=settings) is False


def test_wrong_answer_resets_streak(settings) -> None:
    state = SessionState(current_node_id="groupby")
    state = guide.register_answer(state, correct=True, hints_used=0, settings=settings)

    state = guide.register_answer(state, correct=False, hints_used=0, settings=settings)

    assert state.node_streak == 0


def test_confident_mastery_closes_node_without_tasks(settings) -> None:
    """Второй путь критерия: узел можно закрыть по владению."""
    state = SessionState(current_node_id="groupby")

    assert guide.is_node_closed(state, _mastery(0.95, 0.03), settings=settings) is True


def test_uncertain_mastery_does_not_close_node(settings) -> None:
    """Высокая оценка без подтверждения узлом не считается (§6.1)."""
    state = SessionState(current_node_id="groupby")

    assert guide.is_node_closed(state, _mastery(0.95, 0.25), settings=settings) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_guide.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'llm_tutor.student.guide'`.

- [ ] **Step 3: Write minimal implementation**

Создать `src/llm_tutor/student/guide.py`:

```python
"""Ведение занятия: фазы, критерий закрытия узла, реакция на «застрял» (Срез 10).

Ведёт бот: он решает, что узел пройден, и объявляет следующий шаг. Но решает
это КОД по формальному критерию (архитектура, §6.1) — «понятно?» ответом не
считается, доказательство это решённые задачи.
"""

from llm_tutor.config import Settings, get_settings
from llm_tutor.schemas import SessionState
from llm_tutor.student import beta
from llm_tutor.student.planner import CONFIDENT_UNCERTAINTY


def register_answer(
    state: SessionState,
    *,
    correct: bool,
    hints_used: int,
    settings: Settings | None = None,
) -> SessionState:
    """Обновляет счётчик успехов и фазу после ответа на задание."""
    clean_success = correct and hints_used == 0
    streak = state.node_streak + 1 if clean_success else 0
    return state.model_copy(update={"node_streak": streak, "phase": "check"})


def is_node_closed(
    state: SessionState,
    mastery: beta.Mastery,
    *,
    settings: Settings | None = None,
) -> bool:
    """Пройден ли узел: серия чистых ответов ИЛИ уверенное владение (§6.1)."""
    s = settings or get_settings()
    by_streak = state.node_streak >= s.guide_success_streak
    by_mastery = (
        mastery.mean >= s.mastery_skip_threshold
        and mastery.uncertainty <= CONFIDENT_UNCERTAINTY
    )
    return by_streak or by_mastery
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_guide.py -v`
Expected: PASS (5 тестов)

- [ ] **Step 5: Commit**

```bash
git add src/llm_tutor/student/guide.py tests/test_guide.py
git commit -m "Срез 10: фазы занятия и критерий закрытия узла"
```

---

### Task 8: «Ученик застрял» — усиленный проход

**Files:**
- Modify: `src/llm_tutor/student/guide.py`
- Test: `tests/test_guide.py`

**Interfaces:**
- Consumes: `SessionState`
- Produces: `on_student_stuck(state) -> SessionState` — режим `reinforce`, счётчик успехов обнулён, уровень подсказки поднят на ступень

- [ ] **Step 1: Write the failing test**

Добавить в `tests/test_guide.py`:

```python
def test_stuck_switches_node_to_reinforce_and_resets_streak(settings) -> None:
    """«Не понял» не пускает вперёд: узел уходит в усиленный проход."""
    state = SessionState(current_node_id="groupby", node_streak=1, hint_level=0)

    state = guide.on_student_stuck(state)

    assert state.mode == "reinforce"
    assert state.node_streak == 0
    assert state.hint_level == 1  # ступень вверх, но не разбор


def test_stuck_does_not_jump_the_ladder(settings) -> None:
    state = SessionState(current_node_id="groupby", hint_level=2)

    state = guide.on_student_stuck(state)

    assert state.hint_level == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_guide.py -v -k stuck`
Expected: FAIL — `AttributeError: module 'llm_tutor.student.guide' has no attribute 'on_student_stuck'`.

- [ ] **Step 3: Write minimal implementation**

Добавить в `src/llm_tutor/student/guide.py`:

```python
from llm_tutor.student import hints


def on_student_stuck(state: SessionState) -> SessionState:
    """Ученик просит глубины: узел в усиленный проход, вперёд не идём.

    Счётчик успехов обнуляется — узел сейчас не закроется, значит и следующий
    не начнётся. Уровень подсказки поднимаем на ступень (не сразу разбор).
    """
    return state.model_copy(
        update={
            "mode": "reinforce",
            "node_streak": 0,
            "hint_level": hints.next_hint_level(state.hint_level, state.hint_level + 1),
            "phase": "explain",
        }
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_guide.py -v`
Expected: PASS (7 тестов)

- [ ] **Step 5: Commit**

```bash
git add src/llm_tutor/student/guide.py tests/test_guide.py
git commit -m "Срез 10: «ученик застрял» переводит узел в усиленный проход"
```

---

### Task 9: Модель сообщает «застрял» и «не могу — нет заданий»

**Files:**
- Modify: `src/llm_tutor/llm/schemas.py` (`TutorReply`)
- Modify: `src/llm_tutor/llm/prompts.py` (правила ведения и фазы)
- Modify: `src/llm_tutor/core/context.py` (блок маршрута и фазы в системном промпте)
- Test: `tests/test_context.py`, `tests/test_prompts.py`

**Interfaces:**
- Consumes: `Route`, `GuidePhase`, `MODE_LABELS`
- Produces:
  - `TutorReply.student_stuck: bool = False`
  - `format_route_block(route, *, concept_names=None) -> str` в `prompts.py`
  - `tutor_system_prompt(hint_level, *, has_material, phase=None, route_block=None) -> str`

- [ ] **Step 1: Write the failing test**

Добавить в `tests/test_prompts.py`:

```python
from llm_tutor.llm.prompts import format_route_block
from llm_tutor.schemas import Route, RouteStep


def test_route_block_lists_progress_and_current_node() -> None:
    route = Route(
        goal_concept_id="goal",
        steps=[
            RouteStep(concept_id="a", mode="full", status="closed"),
            RouteStep(concept_id="b", mode="full", status="current"),
            RouteStep(concept_id="goal", mode="full", status="ahead"),
        ],
    )

    block = format_route_block(route, names={"a": "Первый", "b": "Второй", "goal": "Цель"})

    assert "закрыто 1 из 3" in block.lower()
    assert "Второй" in block  # текущий узел назван
    assert "Цель" in block


def test_stuck_rule_is_in_tutor_prompt() -> None:
    text = tutor_system_prompt(0)

    assert "student_stuck" in text
    assert "не пускай вперёд" in text.lower() or "не иди вперёд" in text.lower()
```

Добавить в `tests/test_context.py`:

```python
def test_system_prompt_contains_route_block(conn, settings) -> None:
    load_seed(conn)
    session_id = repos.ensure_open_session(conn, now=1.0)
    state = SessionState(current_node_id="groupby", phase="practice")
    repos.update_session_state(conn, session_id, state)

    package = build_context(
        conn,
        session_id,
        "groupby",
        state=state,
        graph=CourseGraph.load(conn),
        settings=settings,
    )

    system = package.messages[0].content
    assert "Маршрут" in system
    assert "фаза" in system.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_prompts.py tests/test_context.py -v`
Expected: FAIL — `ImportError: cannot import name 'format_route_block'`.

- [ ] **Step 3: Write minimal implementation**

В `src/llm_tutor/llm/schemas.py` в `TutorReply` добавить поле:

```python
    # Модель заметила, что ученик просит глубины («не понял, объясни ещё»).
    student_stuck: bool = False
```

В `src/llm_tutor/llm/prompts.py` добавить:

```python
def format_route_block(route: Route, *, names: Mapping[str, str] | None = None) -> str:
    """Блок маршрута: где ученик и куда идём (§8.2 — состояние сессии)."""
    names = names or {}
    goal = names.get(route.goal_concept_id or "", route.goal_concept_id or "вершина темы")
    lines = [f"Маршрут: закрыто {route.closed_count} из {len(route.steps)}. Цель — {goal}."]
    current = next((step for step in route.steps if step.status == "current"), None)
    if current is not None:
        lines.append(f"Сейчас: {names.get(current.concept_id, current.concept_id)}.")
    return "\n".join(lines)
```

и в `_TUTOR_TURN_RULES` дописать правила ведения:

```python
    "Как ведёшь занятие:\n"
    "- Ты ведёшь ученика по маршруту: объявляешь, что делаем сейчас, и не "
    "перескакиваешь вперёд, пока текущий узел не закрыт.\n"
    "- Если ученик говорит, что не понял, просит подробнее или больше "
    "времени — верни student_stuck: true. Тогда узел уйдёт в усиленный проход: "
    "дай другое объяснение, разобранный пример, выдержку из материала. "
    "Вперёд не иди.\n"
    "- Решение задачи по-прежнему не выдаёшь, пока не пройдена лестница.\n"
```

и расширить сигнатуру:

```python
def tutor_system_prompt(
    hint_level: int,
    *,
    has_material: bool = True,
    phase: GuidePhase | None = None,
    route_block: str | None = None,
) -> str:
    """Системный промпт полного хода: правила, фаза и маршрут."""
    base = TUTOR_SYSTEM_PROMPT if has_material else TUTOR_NO_MATERIAL_SYSTEM_PROMPT
    level_name = HINT_LEVEL_NAMES.get(hint_level, "без подсказки")
    parts = [base, _TUTOR_TURN_RULES]
    if route_block:
        parts.append(route_block)
    if phase:
        parts.append(f"Фаза занятия: {phase}.")
    parts.append(f"Сейчас ты на уровне {hint_level} ({level_name}).")
    parts.append(_TUTOR_TURN_CONTRACT)
    return "\n\n".join(parts)
```

В `src/llm_tutor/core/context.py` в `_system_prompt` добавить блок маршрута:

```python
    route_block = None
    if state.route is not None and graph is not None:
        route_block = format_route_block(
            state.route,
            names={node_id: graph.concept(node_id).name for node_id in graph.node_ids},
        )
    blocks = [
        tutor_system_prompt(
            state.hint_level,
            has_material=has_material,
            phase=state.phase,
            route_block=route_block,
        ),
        profile,
        format_state_block(
            node_name=node_name,
            mode_label=MODE_LABELS.get(state.mode) if state.mode else None,
            hint_level=state.hint_level,
        ),
        format_mastery_block(
            _mastery_slice(conn, graph, state, now=now, settings=settings)
        ),
    ]
    return "\n\n".join(block for block in blocks if block)
```

и в `_TUTOR_TURN_CONTRACT` дописать поле `student_stuck` в контракт JSON:

```
'{"reply": "<ответ ученику>", "hint_level": <число 0..4>, "student_stuck": <true|false>}'
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A src/ tests/
git commit -m "Срез 10: маршрут и фаза в контексте, флаг «застрял» от модели"
```

---

### Task 10: Ход ведёт занятие: закрытие узла, следующий шаг, кнопки

**Files:**
- Modify: `src/llm_tutor/core/turn.py` (`TurnReply`, оркестрация через guide)
- Modify: `src/llm_tutor/bot/handlers.py` (кнопки из `TurnReply`, `/task` через guide)
- Test: `tests/test_turn.py`, `tests/test_bot_flows.py`

**Interfaces:**
- Consumes: `guide.register_answer`, `guide.is_node_closed`, `guide.on_student_stuck`, `route.refresh`
- Produces:
  - `TurnReply(text: str, options: list[str] | None = None)` в `core/turn.py`
  - `handle_turn(...) -> TurnReply` (было `-> str`)
  - `handle_turn` после закрытия узла ставит следующий узел в `current_node_id`, фазу `explain`, счётчик 0

- [ ] **Step 1: Write the failing test**

Добавить в `tests/test_turn.py`:

```python
async def test_two_clean_answers_close_node_and_announce_next(conn, settings) -> None:
    """Узел закрывается по двум чистым ответам, и бот ведёт дальше сам."""
    load_seed(conn)
    _set_state(conn, current_node_id="python_basics", phase="practice")
    item = repos.get_item(conn, 6)

    for now in (1.0, 2.0):
        _set_state(conn, pending_item_id=item.id, current_node_id="python_basics")
        reply = await handle_turn(conn, _FakeTutor(), "m", item.options[0], now=now, settings=settings)

    assert "закрыт" in reply.text.lower() or "дальше" in reply.text.lower()
    state = _state(conn)
    assert state.node_streak == 0          # счётчик обнулён под новый узел
    assert state.current_node_id != "python_basics"  # ушли с закрытого узла
    assert state.phase == "explain"


async def test_stuck_keeps_student_on_the_same_node(conn, settings) -> None:
    """«Не понял» не пускает вперёд."""
    load_seed(conn)
    ingest_text(conn, "# T\n\n## Grouping\n\ngroupby aggregates rows\n", "u")
    _set_state(conn, current_node_id="groupby", node_streak=1)
    client = _FakeTutor("давай разберём подробнее", student_stuck=True)

    await handle_turn(conn, client, "m", "не понял groupby", now=1.0, settings=settings)

    state = _state(conn)
    assert state.mode == "reinforce"
    assert state.node_streak == 0
    assert state.current_node_id == "groupby"
```

В `tests/test_bot_flows.py` добавить:

```python
async def test_task_reply_carries_buttons_for_choice_item(conn, settings) -> None:
    """Задание с вариантами отдаётся с кнопками — «за ручку» одним сообщением."""
    load_seed(conn)
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    await _named(router, "message", "on_task")(FakeMessage(), _fsm())

    message = FakeMessage()
    await _named(router, "message", "on_task")(message, _fsm())

    assert message.sent[-1][1] is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_turn.py -k "close_node or stuck" -v`
Expected: FAIL — `handle_turn` возвращает строку, а тест ждёт `reply.text`.

- [ ] **Step 3: Write minimal implementation**

В `src/llm_tutor/core/turn.py` (сокращённо — остальные ветки сохраняются как есть):

```python
@dataclass(frozen=True)
class TurnReply:
    """Ответ хода: текст и, если задание с вариантами, подписи для кнопок."""

    text: str
    options: list[str] | None = None


def _close_node_if_ready(
    conn: sqlite3.Connection,
    state: SessionState,
    graph: CourseGraph,
    *,
    now: float,
    settings: Settings,
) -> SessionState:
    """Если текущий узел пройден — объявляет следующий и обнуляет счётчик."""
    if state.current_node_id is None:
        return state
    mastery = beta.estimate(conn, state.current_node_id, now=now, settings=settings)
    if not guide.is_node_closed(state, mastery, settings=settings):
        return state
    route = state.route or route_mod.build_route(conn, graph, now=now, settings=settings)
    ahead = [
        step.concept_id for step in route.steps if step.status == "ahead"
    ]
    return state.model_copy(
        update={
            "current_node_id": ahead[0] if ahead else None,
            "node_streak": 0,
            "phase": "explain",
            "mode": None,
            "hint_level": 0,
        }
    )
```

В `_answer_branch` после вердикта:

```python
    state = guide.register_answer(
        state,
        correct=result.score >= diagnostic.SUCCESS_SCORE,
        hints_used=state.hint_level,
        settings=settings,
    )
    state = _close_node_if_ready(conn, state, graph, now=now, settings=settings)
```

В `_tutor_branch` при `answer.student_stuck`:

```python
    if answer.student_stuck:
        new_state = guide.on_student_stuck(new_state)
        note = "Ок, остаёмся на этом узле и разбираемся глубже."
        reply = f"{answer.reply}\n\n{note}"
```

`handle_turn` возвращает `TurnReply(text=reply, options=options)`, где `options`
заполняется только там, где ход отдаёт задание с вариантами: в
`start_practice_reply` (Task 11) и в `_close_node_if_ready`, когда следующий
узел сразу получает задание. Во всех остальных ветках `options=None`.

В `src/llm_tutor/bot/handlers.py` заменить работу со строкой на `TurnReply`:

```python
def _options_keyboard(options: list[str] | None) -> InlineKeyboardMarkup | None:
    """Кнопки вариантов по подписям (None, если вариантов нет)."""
    if not options:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=f"{ANSWER_CALLBACK_PREFIX}:{index}")]
            for index, label in enumerate(options)
        ]
    )
```

и вызывать `await message.answer(_truncate(reply.text), reply_markup=_options_keyboard(reply.options))`.
Существующая `_answer_keyboard(item)` заменяется на `_options_keyboard(item.options)`.

Импорты в `core/turn.py`: `from llm_tutor.student import beta, diagnostic, guide, hints, route as route_mod`
и `from llm_tutor.student.route import build_route` (если используется напрямую).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A src/ tests/
git commit -m "Срез 10: ход ведёт занятие — закрытие узла и следующий шаг"
```

---

### Task 11: Узел без заданий — честное сообщение вместо пустоты

**Files:**
- Modify: `src/llm_tutor/core/turn.py` (`start_practice`, `_close_node_if_ready`)
- Test: `tests/test_turn.py`

**Interfaces:**
- Consumes: `diagnostic.next_question(..., include_rubric=True)`
- Produces: `NO_TASK_FOR_NODE_REPLY` — если у текущего узла заданий нет, бот говорит об этом и продолжает диалог; фаза остаётся `explain`

- [ ] **Step 1: Write the failing test**

Добавить в `tests/test_turn.py`:

```python
async def test_node_without_items_says_so_honestly(conn, settings) -> None:
    """У узла нет заданий — бот говорит об этом, а не молчит."""
    load_seed(conn)
    # у describe_stats заданий в банке нет
    _set_state(conn, current_node_id="describe_stats", phase="practice")

    reply = await start_practice_reply(conn, now=1.0, settings=settings)

    assert "заданий" in reply.text.lower()
    assert _state(conn).current_node_id == "describe_stats"  # узел не потерян
```

где `start_practice_reply` — тонкая обёртка над `start_practice`, возвращающая `TurnReply` (её и добавляем в реализации).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_turn.py::test_node_without_items_says_so_honestly -v`
Expected: FAIL — `ImportError: cannot import name 'start_practice_reply'`.

- [ ] **Step 3: Write minimal implementation**

В `src/llm_tutor/core/turn.py` добавить:

```python
NO_TASK_FOR_NODE_REPLY = (
    "По этому узлу заданий у меня нет — идём разговором. "
    "Спрашивай, объясню; закроем узел, когда разберёмся."
)


def start_practice_reply(
    conn: sqlite3.Connection,
    *,
    now: float | None = None,
    settings: Settings | None = None,
) -> TurnReply:
    """Задание по текущему маршруту; если заданий нет — честное сообщение."""
    s = settings or get_settings()
    stamp = time.time() if now is None else now
    graph = CourseGraph.load(conn)
    if not graph.node_ids:
        return TurnReply(text=EMPTY_GRAPH_REPLY)

    session_id = repos.ensure_open_session(conn, stamp)
    state = repos.get_session_state(conn, session_id)
    question = diagnostic.next_question(
        conn, graph, include_rubric=True, now=stamp, settings=s
    )
    if question is None:
        # Заданий по доступным узлам нет: узел остаётся, ведём диалогом.
        post_turn(
            conn,
            session_id,
            user_text=PRACTICE_KICKOFF_TEXT,
            assistant_text=NO_TASK_FOR_NODE_REPLY,
            state=state.model_copy(update={"last_activity": stamp, "phase": "explain"}),
            now=stamp,
        )
        return TurnReply(text=NO_TASK_FOR_NODE_REPLY)

    new_state = state.model_copy(
        update={
            "pending_item_id": question.item.id,
            "current_node_id": question.concept_id,
            "hint_level": 0,
            "phase": "practice",
            "last_activity": stamp,
        }
    )
    text = _render_item(question.item)
    post_turn(
        conn,
        session_id,
        user_text=PRACTICE_KICKOFF_TEXT,
        assistant_text=text,
        state=new_state,
        now=stamp,
    )
    return TurnReply(text=text, options=question.item.options or None)
```

Существующий `start_practice` заменяется этой функцией (возвращаемый тип меняется
со `str` на `TurnReply`); вызов в `bot/handlers.py` обновляется вместе с Task 10.

Сообщение «заданий нет» ученик увидит в двух случаях: по `/task`, когда у банка
нет вопросов по доступным узлам, и сразу после закрытия узла — там
`_close_node_if_ready` ставит следующий узел, а текст шага добавляет
`handle_turn`, вызывая `start_practice_reply` для нового узла. Если заданий нет,
в ответе появится `NO_TASK_FOR_NODE_REPLY`, а фаза останется `explain`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A src/ tests/
git commit -m "Срез 10: узел без заданий ведём диалогом с честным сообщением"
```

---

## Самопроверка плана

**Покрытие спеки:**

| Требование спеки | Задача |
|---|---|
| §3 Маршрут: структура, снимок, подграф цели | Task 3, Task 5 |
| §3.3 Пересчёт по событиям, значимость, правило тишины | Task 5, Task 6 |
| §3.4 `/plan` показывает путь | Task 6 |
| §4.1 Слой ведения, фазы | Task 7 |
| §4.2 Критерий закрытия (оба пути) | Task 7 |
| §4.2.1 Новые параметры конфига | Task 6 |
| §4.3 «Ученик застрял» | Task 8, Task 9, Task 10 |
| §4.4 Узлы без заданий | Task 11 |
| §4.5 `TurnReply` и кнопки | Task 10 |
| §5 Вопросы рядом с курсом | Task 1, Task 2 |
| §6 Изменения состояния и схемы | Task 3 |
| §7 Раскладка по коду | все задачи |
| §8 Тестирование | тест в каждой задаче |

**Порядок:** Task 1–2 (срез 8) независимы и идут первыми; Task 3–6 (срез 9) дают
маршрут; Task 7–11 (срез 10) надстраивают ведение.

**Известные зависимости между задачами:** Task 5 использует `mode_for_node` из
Task 4; Task 6 использует `refresh` из Task 5; Task 10 использует guide из
Task 7–8. Задачи внутри среза выполняются по порядку.

**Что осталось за планом** (в спеке §10): резюме сессий, определение концепта
вопроса, детектор буксовки, backfill привязки материалов, проактивные сообщения.
