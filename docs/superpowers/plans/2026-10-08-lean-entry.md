# Лаконичный вход и ведомый урок — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Убрать навигационный шум из интерфейса и сделать урок ведомым: одна
кнопка «▶️ Старт», приветствие из трёх предложений, анкета из пяти тематических
вопросов, список ближайших шагов вместо схемы, а дальше бот сам рассказывает,
показывает пример, даёт пару тестов и закрывает узел.

**Architecture:** Вход и анкета — слой `bot/` (новый `bot/start.py`, переписанный
`bot/survey.py`); экран согласования маршрута удаляется целиком. Урок ведёт
`core/turn.py`: вход в узел = тьюторское объяснение плюс первое задание узла,
закрытие узла = объявление плюс объяснение следующего узла в том же ходу.
`TurnReply` получает второе сообщение (`tail`), чтобы объяснение и тест не
слипались в стену. Машинерия ведения (`student/route.py`, `student/planner.py`,
`student/beta.py`) не меняется.

**Tech Stack:** Python 3.12, aiogram 3, pydantic 2, pytest (asyncio_mode=auto),
SQLite.

**Spec:** `docs/superpowers/specs/2026-10-08-lean-entry-design.md`

## Global Constraints

- Python 3.12; тесты — pytest, асинхронные без декоратора (`asyncio_mode =
  "auto"`). Запуск всего набора: `uv run pytest -q` (на старте работы 511 тестов).
- Стиль: black/isort; type hints на всех сигнатурах; докстринги и комментарии
  на русском.
- **Конвенция вывода (не путать):**
  - `core.turn.*`, `core.verify.*`, `bot/start.py` возвращают **сырой** текст —
    хендлер экранирует и обрезает его: `render.fit(render.escape(text))`;
  - `bot.render.*` и `themes.switch_node` возвращают **готовый HTML** — шлются
    как есть с `parse_mode=render.PARSE_MODE`, **без** повторного `escape`.
- **Слоистость:** зависимости идут только `bot → core`/`student`/`db`.
  `core/*` не импортирует из `bot/*`.
- **Схема БД не меняется.** Новые поля состояния — только в `SessionState`
  (JSON в `sessions.state`), с дефолтами: старые сессии читаются без миграции.
- Метки меню — единый источник истины в `bot/menu.py`; `render._HELP_LINES`
  обязан покрывать ровно действия из `ACTION_LABELS`.
- Инлайн-кнопки в интерфейсе только двух видов: варианты ответа на задание и
  варианты анкеты. Навигационных инлайн-кнопок нет.
- Коммиты по-русски в стиле проекта: `Срез N: …`. Ветка работы — `guided-lesson`.

## Review Focus

Проверки, которые спека подразумевает, но которые легко забыть. Каждая
привязана к задаче, где живёт её тест (в скобках):

1. **Узел, который нельзя закрыть** — банк узла кончился, серия не набрана.
   Ожидание: задания идут по второму кругу, повтор не закрывает узел (21.4).
2. **Свободный текст, который не должен двигать урок** — вопрос, намерение,
   ответ на висящее задание. Ожидание: занятие стоит (21.7).
3. **Ответ после подсказки** — в уроке серия не обнуляется, в проверочном
   проходе обнуляется. Ожидание: разное поведение в двух режимах (21.5).
4. **Реплика до анкеты** — ученик пишет текст, не пройдя опрос. Ожидание:
   приглашение нажать «▶️ Старт», а не тьюторский ход (19.3).
5. **Пустой и почти пустой маршрут** — незакрытых узлов меньше пяти, ноль.
   Ожидание: список без пустых строк, без падения (19.1, 19.5).

---

## Срез 19. Лаконичный вход

### Task 19.1: Список шагов вместо псевдографики

**Files:**
- Modify: `src/llm_tutor/bot/render.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `route_mod.build_route`, `route_mod.goal_for`, `_first_state`,
  `escape` (существуют).
- Produces: `render.render_steps(graph: CourseGraph, steps: list, *,
  limit: int | None = None, marks: bool = True) -> str` — нумерованный список
  шагов (граф нужен для имён узлов); `render.PLAN_STEPS = 5`.

- [ ] **Step 1: Написать падающие тесты**

Добавить в `tests/test_render.py`:

```python
def test_plan_is_a_numbered_list_without_art(conn) -> None:
    """Маршрут рисуется нумерованным списком, без псевдографики."""
    load_seed(conn)
    graph = CourseGraph.load(conn)

    text = render.render_plan(conn, now=1.0)

    assert "┌" not in text and "<pre>" not in text
    assert "1. " in text
    assert graph.concept("python_basics").name in text


def test_render_steps_limit_shows_only_first_steps(conn) -> None:
    """``limit`` оставляет только ближайшие шаги."""
    load_seed(conn)
    graph = CourseGraph.load(conn)
    route = route_mod.build_route(conn, graph, now=1.0)

    text = render.render_steps(graph, route.steps, limit=2)

    assert text.count(". ") == 2


def test_render_steps_says_so_when_nothing_left(conn) -> None:
    """Пустой список — вежливая строка, а не пустое сообщение."""
    load_seed(conn)

    assert render.render_steps(CourseGraph.load(conn), []).strip()
```

- [ ] **Step 2: Запустить тесты — убедиться, что падают**

Run: `uv run pytest tests/test_render.py -q -k steps`
Expected: FAIL — `AttributeError: module ... has no attribute 'render_steps'`.

- [ ] **Step 3: Реализовать список**

В `src/llm_tutor/bot/render.py` добавить `render_steps` и переписать
`render_plan` на него (убрать `<pre>`-таблицу, `_progress_bar`, `_STATUS_MARKS`,
`_windowed`, `route_window` — они больше не нужны никому, кроме этого места):

```python
PLAN_STEPS = 5
_STEP_MARKS = {"closed": "[x] ", "current": "[>] ", "ahead": ""}


def render_steps(
    graph: CourseGraph,
    steps: list,
    *,
    limit: int | None = None,
    marks: bool = True,
) -> str:
    """Шаги маршрута нумерованным списком.

    Без псевдографики: та же форма, что у списка ближайших шагов на входе в
    курс, — одна форма на весь интерфейс. ``limit`` режет список (ближайшие
    шаги), ``marks`` рисует пометки пройденного.
    """
    shown = steps[:limit] if limit is not None else steps
    if not shown:
        return "Пока нечего показывать — маршрут пуст."
    lines = []
    for index, step in enumerate(shown, start=1):
        name = escape(graph.concept(step.concept_id).name)
        mark = _STEP_MARKS[step.status] if marks else ""
        lines.append(f"{index}. {mark}{name}")
    return "\n".join(lines)


def render_plan(
    conn: sqlite3.Connection,
    *,
    state: SessionState | None = None,
    now: float | None = None,
    settings: Settings | None = None,
) -> str:
    """Маршрут целиком: нумерованный список с пометками пройденного."""
    graph = CourseGraph.load(conn)
    if not graph.node_ids:
        return EMPTY_GRAPH_REPLY
    session_state = state or _first_state(conn)
    route = route_mod.build_route(
        conn,
        graph,
        goal_concept_id=route_mod.goal_for(conn, graph),
        current_node_id=session_state.current_node_id,
        previous=session_state.route,
        now=now,
        settings=settings,
    )
    if not route.steps:
        return EMPTY_GRAPH_REPLY
    return "🗺 <b>Маршрут</b>\n" + render_steps(graph, route.steps)
```

- [ ] **Step 4: Запустить тесты представления**

Run: `uv run pytest tests/test_render.py -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add src/llm_tutor/bot/render.py tests/test_render.py
git commit -m "Срез 19: список шагов вместо псевдографики"
```

### Task 19.2: Меню из трёх действий, без навигационного инлайна

**Files:**
- Modify: `src/llm_tutor/bot/menu.py`, `src/llm_tutor/bot/handlers.py`,
  `src/llm_tutor/bot/render.py`
- Test: `tests/test_menu.py`, `tests/test_bot_flows.py`

**Interfaces:**
- Consumes: —
- Produces: `menu.Action = Literal["route", "themes", "close"]`;
  `menu.resume_keyboard` удалена; из хендлеров убраны `or menu.resume_keyboard()`.

- [ ] **Step 1: Написать падающие тесты**

```python
def test_menu_has_three_actions_without_resume() -> None:
    """В меню три действия: навигация больше не нужна."""
    assert set(menu.ACTION_LABELS) == {"route", "themes", "close"}
    assert not hasattr(menu, "resume_keyboard")


async def test_turn_tail_has_no_navigation_button(conn, settings) -> None:
    """Под ответом нет инлайн-кнопки «Продолжить»."""
    load_seed(conn)
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    message = FakeMessage()

    await _named(router, "message", "on_text")(message, _fsm())

    assert message.sent[-1][1] is None  # ни вариантов, ни кнопки
```

- [ ] **Step 2: Запустить — убедиться, что падают**

Run: `uv run pytest tests/test_menu.py tests/test_bot_flows.py -q -k "three_actions or navigation"`
Expected: FAIL.

- [ ] **Step 3: Ужать меню и убрать инлайн-навигацию**

`bot/menu.py`: `Action` из трёх значений, `ACTION_LABELS` из трёх записей,
`resume_keyboard` удалить. `bot/render.py`: `_HELP_LINES` — три записи.
`bot/handlers.py`: во всех хендлерах `_options_keyboard(conn, reply.options) or
menu.resume_keyboard()` → `_options_keyboard(conn, reply.options)`; в
`on_menu_action` убрать ветку `resume`; в ветке вернувшегося ученика `on_start`
вместо `menu.resume_keyboard()` — `menu.main_menu()`.

- [ ] **Step 4: Запустить набор**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add src/llm_tutor/bot/menu.py src/llm_tutor/bot/handlers.py src/llm_tutor/bot/render.py tests/test_menu.py tests/test_bot_flows.py
git commit -m "Срез 19: меню из трёх действий и никакого инлайна под ответом"
```

### Task 19.3: Кнопка «▶️ Старт», приветствие, реплика до анкеты

**Files:**
- Create: `src/llm_tutor/bot/start.py`
- Modify: `src/llm_tutor/bot/handlers.py`, `src/llm_tutor/bot/survey.py`
- Test: `tests/test_bot_flows.py`

**Interfaces:**
- Produces: `start.START_LABEL = "▶️ Старт"`; `start.INTRO_TEXT`;
  `start.BEFORE_SURVEY_REPLY`; `start.start_keyboard() -> ReplyKeyboardMarkup`.

- [ ] **Step 1: Написать падающие тесты**

```python
async def test_start_before_survey_shows_start_button(conn, settings) -> None:
    """Первый вход: приветствие из трёх предложений и кнопка «▶️ Старт»."""
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    message = FakeMessage()

    await _named(router, "message", "on_start")(message, _fsm())

    text, markup = message.sent[0]
    assert text == start.INTRO_TEXT
    assert text.count(".") == 3  # ровно три предложения
    assert start.START_LABEL in text  # третье предложение называет кнопку
    assert markup.keyboard[0][0].text == start.START_LABEL


async def test_text_before_survey_is_not_a_tutor_turn(conn, settings) -> None:
    """До анкеты реплика отвечает приглашением, а не тьютором."""
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    message = FakeMessage("привет")

    await _named(router, "message", "on_text")(message, _fsm())

    assert message.last_text == start.BEFORE_SURVEY_REPLY
    assert repos.get_open_session(conn) is None  # в занятие не пошли
```

Импорты в `tests/test_bot_flows.py`: `from llm_tutor.bot import start`.

- [ ] **Step 2: Запустить — убедиться, что падают**

Run: `uv run pytest tests/test_bot_flows.py -q -k "before_survey"`
Expected: FAIL — модуля `bot.start` нет.

- [ ] **Step 3: Написать модуль и подключить**

```python
START_LABEL = "▶️ Старт"

INTRO_TEXT = (
    "👋 Привет! Я тьютор по теме 1 курса mlcourse.ai — «Pandas / EDA».\n"
    "Задам несколько коротких вопросов о тебе — это меньше минуты.\n"
    f"Нажми «{START_LABEL}», когда будешь готов пройти опрос."
)

BEFORE_SURVEY_REPLY = f"Сначала пару вопросов о тебе — нажми «{START_LABEL}»."
```

`start_keyboard()` — reply-клавиатура из одной кнопки `START_LABEL`
(`is_persistent=True`, `resize_keyboard=True`), как `menu.main_menu()`.
В `handlers.py`: `on_start` для непройденной анкеты отвечает `INTRO_TEXT` с
`start_keyboard()` и запускает анкету; фильтр кнопки
`@router.message(StateFilter(None), F.text == start.START_LABEL)` ведёт в тот же
путь; `on_text` при непройденной анкете отвечает `BEFORE_SURVEY_REPLY`.

- [ ] **Step 4: Запустить набор**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add src/llm_tutor/bot/start.py src/llm_tutor/bot/handlers.py src/llm_tutor/bot/survey.py tests/test_bot_flows.py
git commit -m "Срез 19: кнопка «Старт», приветствие в три предложения"
```

### Task 19.4: Анкета из пяти тематических вопросов

**Files:**
- Modify: `src/llm_tutor/student/survey.py`
- Test: `tests/test_survey.py`

**Interfaces:**
- Produces: `survey.SURVEY_QUESTIONS` из пяти вопросов;
  `survey.BLOCK_CONCEPTS: tuple[tuple[str, ...], ...]`;
  `survey.PRIOR_LEVELS = (0.0, 0.3, 0.65, 1.0)`; ключи фактов `BLOCK_KEY`.

- [ ] **Step 1: Написать падающие тесты**

```python
def test_survey_has_five_topical_questions() -> None:
    assert len(survey.SURVEY_QUESTIONS) == 5
    assert all(len(q.options) == 4 for q in survey.SURVEY_QUESTIONS)


def test_survey_prior_covers_every_node_of_block(conn, settings) -> None:
    """Ответ накрывает весь блок узлов слабым свидетельством."""
    load_seed(conn)
    answers = {q.key: 3 for q in survey.SURVEY_QUESTIONS}  # «уверенно» везде

    survey.apply_answers(conn, answers, now=1.0, settings=settings)

    covered = {event.concept_id for event in repos.get_events(conn)}
    assert len(covered) == len(CourseGraph.load(conn).node_ids)


def test_survey_prior_uses_levels(conn, settings) -> None:
    """Индекс ответа задаёт силу априора, а не только «знаю/не знаю»."""
    load_seed(conn)
    first = survey.SURVEY_QUESTIONS[0]

    survey.apply_answers(conn, {first.key: 1}, now=1.0, settings=settings)

    event = repos.get_events(conn)[0]
    assert event.result == 0.3
```

- [ ] **Step 2: Запустить — убедиться, что падают**

Run: `uv run pytest tests/test_survey.py -q -k "five_topical or levels"`
Expected: FAIL.

- [ ] **Step 3: Переписать вопросы и априор**

В `student/survey.py`: пять вопросов с общими вариантами из `PRIOR_LEVELS`
(«Не знаю» · «Слышал(а), но не делал(а)» · «Делал(а), но с подсказками» ·
«Уверенно»), `BLOCK_CONCEPTS` с блоками из спеки §3.3, `_apply_prior` на каждый
узел блока со значением `PRIOR_LEVELS[index]`. Вопросы `GOAL_KEY` и
`TIME_BUDGET_KEY` удалить вместе с фактами.

- [ ] **Step 4: Запустить набор**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add src/llm_tutor/student/survey.py tests/test_survey.py
git commit -m "Срез 19: анкета из пяти вопросов по теме"
```

### Task 19.5: Список шагов вместо экрана согласования

**Files:**
- Modify: `src/llm_tutor/bot/survey.py`, `src/llm_tutor/bot/main.py`,
  `src/llm_tutor/bot/start.py`, `src/llm_tutor/bot/render.py`
- Delete: `src/llm_tutor/bot/onboarding.py`, `tests/test_onboarding.py`
- Test: `tests/test_bot_flows.py`

**Interfaces:**
- Consumes: `render.render_steps`, `core.turn.resume_reply`.
- Produces: `start.begin_lesson(message, state, conn, client, model, settings)`
  — список ближайших шагов и первый ход урока.

- [ ] **Step 1: Написать падающий тест**

```python
async def test_survey_finish_shows_steps_and_starts_lesson(conn, settings) -> None:
    """Финал анкеты: список ближайших шагов и сразу начало урока."""
    load_seed(conn)
    router = make_survey_router(conn, settings)
    ask_state = _fsm()
    await ask_survey(FakeMessage(), ask_state)
    on_answer = _handler(router, "callback_query", 0)
    message = FakeMessage()

    for _ in range(len(survey.SURVEY_QUESTIONS)):
        await on_answer(FakeCallback("survey:0", message), ask_state)

    steps_text = message.sent[-2][0]
    assert "Ближайшие 5 шагов" in steps_text
    assert "1. " in steps_text
    assert message.sent[-1][0]  # урок начался: объяснение первой темы
    assert await ask_state.get_state() is None  # FSM свободен
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_bot_flows.py -q -k "steps_and_starts"`
Expected: FAIL — сейчас приходит экран согласования.

- [ ] **Step 3: Заменить финал анкеты и удалить экран согласования**

`start.begin_lesson`: собрать маршрут, взять первые `render.PLAN_STEPS`
незакрытых шагов, отправить `render_steps(..., limit=...)` с приглашением
«Начинаем с «X» — сейчас коротко объясню и покажу пример.», затем вызвать
существующий `resume_reply` и отправить его текст с клавиатурой `menu.main_menu()`.
`bot/survey.py`: финал анкеты вызывает `start.begin_lesson`; `onboarding` из
импортов убрать. `bot/main.py`: роутер онбординга не подключать.
`bot/onboarding.py` и `tests/test_onboarding.py` удалить целиком (экран
согласования и колбэки `route:*` больше не существуют — спека §4).

- [ ] **Step 4: Запустить набор**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add -A src/llm_tutor/bot tests/test_bot_flows.py tests/test_onboarding.py docs/superpowers
git commit -m "Срез 19: список ближайших шагов вместо экрана согласования"
```

---

## Срез 20. Банк заданий

### Task 20.1: По два задания на каждый узел

**Files:**
- Modify: `data/seed_topic01.json`
- Create: `tests/test_seed.py`

**Interfaces:**
- Consumes: существующий формат seed (`prompt`, `answer_type`,
  `concept_weights`, `difficulty`, `options`, `answer`).
- Produces: банк, в котором у каждого из 21 узла ≥ 2 задания типа
  `choice`/`short`; итог — 42 задания с автопроверкой плюс существующие
  рубричные 9 и 10.

- [ ] **Step 1: Написать падающий тест банка**

```python
def test_every_node_has_two_auto_checked_items(conn, settings) -> None:
    """«Пара тестов» возможна на каждом узле: минимум два задания с автопроверкой."""
    load_seed(conn)
    graph = CourseGraph.load(conn)
    auto = diagnostic.AUTO_CHECKABLE

    for node_id in graph.node_ids:
        items = [
            item
            for item in repos.get_items(conn)
            if item.answer_type in auto and node_id in item.concept_weights
        ]
        assert len(items) >= 2, f"у узла {node_id} меньше двух заданий"


def test_choice_items_have_answer_among_options(conn, settings) -> None:
    load_seed(conn)

    for item in repos.get_items(conn):
        if item.answer_type == "choice":
            assert len(item.options) >= 3, item.id
            assert item.options[int(item.answer)] in item.options
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_seed.py -q`
Expected: FAIL — у 11 узлов заданий нет вовсе.

- [ ] **Step 3: Дописать задания**

Дописать в `data/seed_topic01.json` задания на все узлы, где их меньше двух:
`choice` (3–4 варианта, `answer` — индекс верного) или `short` (короткий
эталон), `concept_weights` = `{узел: 1.0}`, `difficulty` — как в узле.

Образец:

```json
{
  "id": 11,
  "prompt": "Что вернёт df.shape для таблицы с 5 строками и 3 столбцами?",
  "answer_type": "choice",
  "concept_weights": {"pandas_dataframe": 1.0},
  "difficulty": 0.3,
  "options": ["(3, 5)", "(5, 3)", "5", "8"],
  "answer": 1
}
```

- [ ] **Step 4: Запустить тесты банка и весь набор**

Run: `uv run pytest tests/test_seed.py -q && uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add data/seed_topic01.json tests/test_seed.py
git commit -m "Срез 20: по два задания на каждый узел"
```

---

## Срез 21. Ведомый урок

### Task 21.1: Промпт — сначала объясни, потом спрашивай

**Files:**
- Modify: `src/llm_tutor/llm/prompts.py`
- Test: `tests/test_prompts.py`

**Interfaces:**
- Produces: правило `_TUTOR_TURN_RULES` про объяснение первым и `phase`
  `explain` в `tutor_system_prompt`.

- [ ] **Step 1: Написать падающий тест**

```python
def test_tutor_prompt_explains_before_asking() -> None:
    """Сократовский диалог идёт ПОСЛЕ объяснения, а не вместо него."""
    text = tutor_system_prompt(0, material="found")

    assert "сначала" in text.lower() and "объясни" in text.lower()
    assert "пример" in text.lower()
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_prompts.py -q -k explains_before`
Expected: FAIL.

- [ ] **Step 3: Переписать правила**

В `_TUTOR_TURN_RULES` заменить установку «сначала вопросы, которые подводят
ученика к ответу» на: сначала коротко объясни (2–4 предложения), покажи
короткий пример и скажи, зачем это нужно; вопросы задавай после объяснения;
в фазе `explain` вопросов не задавай вовсе.

- [ ] **Step 4: Запустить тесты промптов**

Run: `uv run pytest tests/test_prompts.py -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add src/llm_tutor/llm/prompts.py tests/test_prompts.py
git commit -m "Срез 21: тьютор объясняет первым, спрашивает после"
```

### Task 21.2: Второе сообщение хода (`TurnReply.tail`)

**Files:**
- Modify: `src/llm_tutor/core/turn.py`, `src/llm_tutor/bot/handlers.py`
- Test: `tests/test_turn.py`, `tests/test_bot_flows.py`

**Interfaces:**
- Consumes: `TurnReply` (frozen dataclass), `_render_item`.
- Produces: `TurnReply(text: str, options: list[str] | None = None,
  tail: str | None = None)`; хендлеры шлют `tail` вторым сообщением с
  `_options_keyboard`.

- [ ] **Step 1: Написать падающий тест**

```python
async def test_send_reply_sends_tail_as_second_message(conn, settings) -> None:
    """Ход с хвостом шлёт два сообщения: реплика и задание с кнопками."""
    load_seed(conn)
    message = FakeMessage()

    await handlers._send_reply(
        conn, message, TurnReply(text="объяснение", tail="задание", options=["а", "б"])
    )

    assert [text for text, _ in message.sent] == ["объяснение", "задание"]
    assert message.sent[1][1] is not None  # варианты ответа на задание
```

Импорты: `from llm_tutor.bot import handlers` и `from llm_tutor.core.turn
import TurnReply` в `tests/test_bot_flows.py` уже есть.

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_turn.py -q -k two_messages`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'tail'`.

- [ ] **Step 3: Добавить поле и отправку**

`core/turn.py`: в `TurnReply` добавить `tail: str | None = None`; в
`handle_turn` вместо склейки `reply = f"{reply}\n\n{task_text}"` вернуть
`TurnReply(text=reply, options=options, tail=task_text)` (когда задание выдано).
`bot/handlers.py`: хелпер `_send_reply(message, reply)` — отправляет `text`, а
если `tail` есть, вторым сообщением `tail` с `_options_keyboard(conn, options)`.

- [ ] **Step 4: Запустить набор**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add src/llm_tutor/core/turn.py src/llm_tutor/bot/handlers.py tests/
git commit -m "Срез 21: задание идёт вторым сообщением хода"
```

### Task 21.3: Вход в узел — объяснение и первый тест

**Files:**
- Modify: `src/llm_tutor/core/turn.py`
- Test: `tests/test_turn.py`

**Interfaces:**
- Produces: `handle_turn` при отсутствии текущего узла берёт узел маршрута,
  делает тьюторский ход и в том же ходу выдаёт первое задание узла
  (`tail` = текст задания, `options` = варианты).

- [ ] **Step 1: Написать падающий тест**

```python
async def test_entering_node_explains_and_gives_first_test(conn, settings) -> None:
    """Вход в узел: объяснение первым сообщением, задание — вторым."""
    load_seed(conn)
    ingest_text(conn, "# T\n\n## S\n\ngroupby\n", "u")
    client = _FakeTutor("Сейчас разберём Python")

    reply = await handle_turn(conn, client, "m", "давай учиться", now=1.0, settings=settings)

    assert reply.text == "Сейчас разберём Python"  # объяснение без задания
    assert reply.tail  # задание отдельным сообщением
    assert _state(conn).pending_item_id is not None
    assert _state(conn).phase == "practice"
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_turn.py -q -k explains_and_gives_first_test`
Expected: FAIL.

- [ ] **Step 3: Реализовать вход в узел**

В `handle_turn`: если `state.current_node_id is None` и `state.pending_item_id
is None` — взять узел правилом `route_mod.next_node_id`, сделать тьюторский ход
(`_tutor_branch` на состоянии с этим узлом), затем `_issue_task` и вернуть
`TurnReply(text=reply, tail=task_text, options=options)`.

- [ ] **Step 4: Запустить набор**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add src/llm_tutor/core/turn.py tests/test_turn.py
git commit -m "Срез 21: вход в узел — объяснение и первый тест"
```

### Task 21.4: Пара тестов и второй круг

**Files:**
- Modify: `src/llm_tutor/schemas.py`, `src/llm_tutor/core/turn.py`
- Test: `tests/test_turn.py`

**Interfaces:**
- Consumes: `diagnostic.verification_item`.
- Produces: `SessionState.lesson_item_ids: list[int] = []`; в уроке задания
  узла выдаются без суточной паузы, не повторяются подряд и идут по второму
  кругу, когда список исчерпан.

- [ ] **Step 1: Написать падающие тесты**

```python
async def test_lesson_second_test_differs_from_first(conn, settings) -> None:
    """Второй тест узла — другое задание, а не то же самое."""
    load_seed(conn)
    _set_state(conn, current_node_id="groupby", phase="practice")
    start_practice_reply(conn, now=1.0, settings=settings)
    first_id = _state(conn).pending_item_id

    reply = await handle_turn(conn, _FakeTutor(), "m", "мимо", now=2.0, settings=settings)

    assert reply.tail  # следующее задание выдано
    assert _state(conn).pending_item_id not in (None, first_id)


async def test_lesson_cycles_tests_when_exhausted(conn, settings) -> None:
    """Оба задания выданы, серия нулевая — идём по второму кругу, не залипаем."""
    load_seed(conn)
    _set_state(conn, current_node_id="groupby", phase="practice")
    start_practice_reply(conn, now=1.0, settings=settings)
    issued = {_state(conn).pending_item_id}

    for now in (2.0, 3.0, 4.0):
        reply = await handle_turn(
            conn, _FakeTutor(), "m", "мимо", now=now, settings=settings
        )
        issued.add(_state(conn).pending_item_id)

    assert reply.tail  # задание есть всегда: узел не залипает
    assert len(issued) < 4  # третий заход — повтор, а не четвёртое «новое» задание


def test_lesson_item_ids_defaults_to_empty() -> None:
    """Старая сессия без поля читается: дефолт пустой."""
    assert SessionState().lesson_item_ids == []
```

- [ ] **Step 2: Запустить — убедиться, что падают**

Run: `uv run pytest tests/test_turn.py -q -k "second_test or cycles"`
Expected: FAIL.

- [ ] **Step 3: Реализовать выдачу с циклом**

`schemas.py`: поле `lesson_item_ids: list[int] = Field(default_factory=list)`.
`core/turn.py`: в `_issue_task` для обычного режима урока брать задание
`diagnostic.verification_item(conn, node_id, used_item_ids=frozenset(state.lesson_item_ids), ...)`
(автопроверка: `include_rubric=False` — рубричные остаются проходу); если
вернулось `None`, а у узла задания есть — обнулить `lesson_item_ids` и взять
заново. Чистить `lesson_item_ids` при закрытии узла, смене темы и старте
прохода.

- [ ] **Step 4: Запустить набор**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add src/llm_tutor/schemas.py src/llm_tutor/core/turn.py tests/test_turn.py
git commit -m "Срез 21: пара тестов и второй круг заданий"
```

### Task 21.5: Чистота ответа зависит от режима

**Files:**
- Modify: `src/llm_tutor/core/turn.py`, `src/llm_tutor/student/guide.py`
- Test: `tests/test_turn.py`, `tests/test_verify.py`

**Interfaces:**
- Produces: в обычном уроке серия считается по верным ответам (подсказки её не
  обнуляют); в режиме `verify` — только чистые ответы.

- [ ] **Step 1: Написать падающие тесты**

```python
async def test_lesson_answer_after_help_still_counts(conn, settings) -> None:
    """В уроке бот сам объясняет первым — подсказка серию не обнуляет."""
    load_seed(conn)
    _set_state(conn, current_node_id="groupby", task_hinted=True, node_streak=1)
    client = _FakeGraderClient(_full_verdict(conn, 9))
    _set_state(conn, pending_item_id=9)

    await handle_turn(conn, client, "m", "groupby по ключу", now=1.0, settings=settings)

    assert _state(conn).node_streak == 2  # серия дошла до порога


async def test_verify_pass_answer_after_help_does_not_count(conn, settings) -> None:
    """В проходе подсказка по-прежнему обнуляет серию."""
    load_seed(conn)
    _set_state(
        conn, current_node_id="groupby", mode="verify", task_hinted=True, node_streak=1
    )
    client = _FakeGraderClient(_full_verdict(conn, 9))
    _set_state(conn, pending_item_id=9)

    await handle_turn(conn, client, "m", "groupby по ключу", now=1.0, settings=settings)

    assert _state(conn).node_streak == 0
```

- [ ] **Step 2: Запустить — убедиться, что падают**

Run: `uv run pytest tests/test_turn.py tests/test_verify.py -q -k "after_help"`
Expected: FAIL.

- [ ] **Step 3: Учесть режим**

В `_answer_branch` при вызове `guide.register_answer` передавать
`hinted=hinted and state.mode == "verify"`. Докстринг `guide.register_answer` —
уточнить, что чистота нужна проходу, а в уроке ответ считается верным.

- [ ] **Step 4: Запустить набор**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add src/llm_tutor/core/turn.py src/llm_tutor/student/guide.py tests/
git commit -m "Срез 21: в уроке серию считают верные ответы, в проходе — чистые"
```

### Task 21.6: Закрытие узла и переход к следующему

**Files:**
- Modify: `src/llm_tutor/core/turn.py`
- Test: `tests/test_turn.py`

**Interfaces:**
- Consumes: `_close_node_if_ready`, `_issue_task`, `_tutor_branch`.
- Produces: после закрытия узла ход отдаёт объявление, объяснение следующего
  узла (`text`) и его первый тест (`tail`).

- [ ] **Step 1: Написать падающий тест**

```python
async def test_closed_node_leads_into_next_lesson(conn, settings) -> None:
    """Узел закрылся — бот сам объясняет следующий и даёт его первый тест."""
    load_seed(conn)
    item = repos.get_item(conn, 6)  # python_basics
    client = _FakeTutor("Теперь про Series и DataFrame")
    for now in (1.0, 2.0):
        _set_state(conn, pending_item_id=item.id, current_node_id="python_basics")
        reply = await handle_turn(
            conn, client, "m", item.options[0], now=now, settings=settings
        )

    assert "закрыта" in reply.text
    assert "Теперь про Series и DataFrame" in reply.text  # объяснение следующего
    assert reply.tail  # и его первый тест
    assert _state(conn).current_node_id != "python_basics"
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_turn.py -q -k leads_into_next`
Expected: FAIL.

- [ ] **Step 3: Продолжить ход после закрытия**

В ветке ответа `handle_turn`: если `_close_node_if_ready` вернул объявление —
сделать тьюторский ход по новому узлу и выдать его первое задание:
`text = f"{closed_note}\n\n{explanation}"`, `tail = task_text`. Тьюторский ход
на объяснение следующего узла — второй вызов модели в этом ходу (осознанно:
сообщение должно быть не «тема закрыта», а началом следующей темы).

- [ ] **Step 4: Запустить набор**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add src/llm_tutor/core/turn.py tests/test_turn.py
git commit -m "Срез 21: закрытый узел ведёт в следующий урок"
```

### Task 21.7: Свободный текст ведёт занятие

**Files:**
- Modify: `src/llm_tutor/core/turn.py`
- Test: `tests/test_turn.py`

**Interfaces:**
- Consumes: `_looks_like_question`, `intents.detect`, `resume_reply`.
- Produces: реплика без висящего задания и без вопроса двигает занятие:
  фаза `explain` — объяснение текущего узла, иначе — следующее задание.

- [ ] **Step 1: Написать падающие тесты**

```python
async def test_plain_reply_moves_lesson_forward(conn, settings) -> None:
    """Ремарка без задания ведёт занятие дальше — кнопки для этого нет."""
    load_seed(conn)
    _set_state(conn, current_node_id="groupby", phase="practice")

    reply = await handle_turn(
        conn, _FakeTutor(), "m", "ага, понятно", now=1.0, settings=settings
    )

    assert reply.tail  # выдали следующее задание
    assert _state(conn).pending_item_id is not None


async def test_question_does_not_move_lesson(conn, settings) -> None:
    """Вопрос оставляет занятие на месте: сначала отвечаем."""
    load_seed(conn)
    _set_state(conn, current_node_id="groupby", phase="practice")

    reply = await handle_turn(
        conn, _FakeTutor("Отвечаю на вопрос"), "m", "а что такое ключ?", now=1.0, settings=settings
    )

    assert reply.tail is None
    assert _state(conn).pending_item_id is None
```

- [ ] **Step 2: Запустить — убедиться, что падают**

Run: `uv run pytest tests/test_turn.py -q -k "moves_lesson or does_not_move"`
Expected: FAIL.

- [ ] **Step 3: Реализовать продолжение занятия**

В `handle_turn` в ветке `else` (задания нет, намерения нет): если реплика не
вопрос — вести занятие дальше: фаза `explain` → тьюторский ход по текущему узлу
и выдача задания (`tail`), иначе → `_issue_task` и `tail` с заданием.

- [ ] **Step 4: Запустить набор**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add src/llm_tutor/core/turn.py tests/test_turn.py
git commit -m "Срез 21: реплика ученика ведёт занятие дальше"
```

### Task 21.8: Сквозной сценарий нового ученика

**Files:**
- Test: `tests/test_e2e_topic01.py`

- [ ] **Step 1: Написать тест**

```python
async def test_new_student_goes_from_start_to_closed_node(conn, settings) -> None:
    """«▶️ Старт» → анкета → список шагов → объяснение → пара тестов → узел закрыт."""
    load_seed(conn)
    ingest_text(conn, "# T\n\n## Grouping\n\ngroupby aggregates rows\n", "u")
    router = make_survey_router(conn, settings)
    state = _fsm()
    await ask_survey(FakeMessage(), state)
    on_answer = _handler(router, "callback_query", 0)
    message = FakeMessage()
    for _ in range(len(survey.SURVEY_QUESTIONS)):
        await on_answer(FakeCallback("survey:0", message), state)

    assert "Ближайшие 5 шагов" in message.sent[0][0]
    assert message.sent[-1][0]  # урок начался
    assert not _is_closed(conn, "python_basics")  # до ответов узел не закрыт

    # Отвечаем верно на первый тест узла, за ним на второй — и узел закрывается.
    client = GradingTutor(conn, passed=True)
    for now in (2.0, 3.0):
        item = repos.get_item(conn, _state(conn).pending_item_id)
        assert item is not None
        answer = item.options[int(item.answer)] if item.options else str(item.answer)
        await handle_turn(conn, client, "m", answer, now=now, settings=settings)

    assert _is_closed(conn, "python_basics")
    assert _state(conn).current_node_id != "python_basics"  # урок пошёл дальше
```

Импорты: `from llm_tutor.bot import survey as bot_survey` не нужен —
`SurveyFlow` и `survey` в файле уже импортированы; нужны `make_survey_router`,
`ask as ask_survey`, `FakeCallback`, `FakeMessage`, `_fsm`, `_handler`,
`GradingTutor`.

- [ ] **Step 2: Запустить тест**

Run: `uv run pytest tests/test_e2e_topic01.py -q -k new_student_goes`
Expected: PASS. Если падает — проверить, что задачи 19–21 закрыты.

- [ ] **Step 3: Запустить весь набор**

Run: `uv run pytest -q`
Expected: PASS — весь набор зелёный.

- [ ] **Step 4: Коммит**

```bash
git add tests/test_e2e_topic01.py
git commit -m "Срез 21: сквозной сценарий от «Старт» до закрытого узла"
```

---

## Итог

После всех задач:

- новый ученик видит одну кнопку «▶️ Старт», три предложения приветствия и
  анкету из пяти тематических вопросов;
- после анкеты — список ближайших пяти шагов и сразу первый урок, без схемы и
  без кнопки согласования;
- урок ведёт бот: объяснение с примером, пара тестов, закрытие узла и переход
  к следующему без единой навигационной кнопки;
- под сообщениями остались только варианты ответа; в меню три действия;
- в банке по два задания на каждый узел.

Ручная проверка на живом боте — по §12 спеки: не тараторит ли бот, понятно ли,
что делать после объяснения, не выглядит ли список шагов рекламой.
