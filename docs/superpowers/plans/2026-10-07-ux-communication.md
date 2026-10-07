# UX общения с ботом — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Сделать общение с тьютором понятным — короткие ответы, видимое
меню, аккуратная табличка маршрута и вежливый вход в курс.

**Architecture:** Слой представления выносится из `bot/handlers.py` в
`bot/render.py`; меню и навигация — отдельные модули `bot/menu.py` и
`bot/themes.py`. Ответы модели идут в Telegram с `parse_mode="HTML"` и
экранированием; наша разметка (табличка) формируется кодом. Машинерия ведения
(`student/guide.py`, `student/route.py`) не трогается — добавляется тонкий
слой навигации и представления поверх неё.

**Tech Stack:** Python 3.12, aiogram 3, pydantic 2, pytest (asyncio_mode=auto),
SQLite.

**Spec:** `docs/superpowers/specs/2026-10-07-ux-communication-design.md`

## Global Constraints

- Python 3.12; тесты — pytest, асинхронные тесты без декоратора
  (`asyncio_mode = "auto"`). Запуск: `uv run pytest -q`.
- Стиль: black/isort/ruff; type hints на всех сигнатурах; докстринги и
  комментарии на русском.
- **Конвенция вывода (важно, не путать):**
  - функции `core.turn.*`, возвращающие **сырой** текст (ответ модели, текст
    задания) — экранируются и обрезаются в хендлере:
    `render.fit(render.escape(reply.text))`;
  - функции `bot.render.*` и `themes.switch_node` возвращают **готовый
    HTML** (динамические вставки экранированы внутри) — шлются как есть с
    `parse_mode=render.PARSE_MODE`, **без** повторного `escape`.
- Тексты с разметкой (наши `render.*` и ответы модели) идут с
  `parse_mode="HTML"`. Тексты анкеты/диагностики разметки не содержат и
  `parse_mode` могут не задавать — это не нарушение.
- Импорт на каждом срезе должен быть самодостаточным: модуль создаётся
  **раньше**, чем на него появляется ссылка. `bot/render.py` (Срез 11) не
  импортирует `bot/menu.py` (появится в 12.1); `stuck_reply` импортируется
  только в 14.1.
- Метки меню — единый источник истины в `bot/menu.py`.
- Схема БД не меняется: состояние живёт в JSON-поле `sessions.state`.
- Коммиты по-русски в стиле проекта: `Срез N: …`.

---

### Task 11.1: Правила лаконичности в промпте тьютора

**Files:**
- Modify: `src/llm_tutor/llm/prompts.py`
- Test: `tests/test_prompts.py`

**Interfaces:**
- Consumes: существующую `tutor_system_prompt(hint_level, *, material=..., phase=..., route_block=...)`.
- Produces: константа `_BREVITY_RULES`; `tutor_system_prompt` включает её для
  всех состояний материала (`found`/`no_match`/`empty`).

- [ ] **Step 1: Написать падающий тест**

Добавить в `tests/test_prompts.py`:

```python
def test_tutor_prompt_demands_brevity_for_all_material_states() -> None:
    """Правила лаконичности действуют независимо от наличия материала курса."""
    for material in ("found", "no_match", "empty"):
        prompt = tutor_system_prompt(0, material=material)
        assert "2–4 предложения" in prompt
        assert "без вступлений" in prompt.lower()
        assert "Одна мысль за сообщение" in prompt
```

Убедиться, что `tutor_system_prompt` импортируется в этом файле (при
необходимости добавить в существующий блок импортов).

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `uv run pytest tests/test_prompts.py::test_tutor_prompt_demands_brevity_for_all_material_states -v`
Expected: FAIL — `assert "2–4 предложения" in prompt` не проходит.

- [ ] **Step 3: Добавить правила в промпт**

В `src/llm_tutor/llm/prompts.py` рядом с `_TUTOR_TURN_RULES` добавить:

```python
# Правила лаконичности: применяются во всех состояниях материала.
_BREVITY_RULES = (
    "Как говорить:\n"
    "- Отвечай коротко: 2–4 предложения по делу, без абзацев.\n"
    "- Без вступлений («Отличный вопрос!», «Конечно, давай разберём») — сразу суть.\n"
    "- Одна мысль за сообщение; нужно больше — ученик попросит.\n"
    "- Не пересказывай материал целиком — только нужное для текущего шага.\n"
    "- Заканчивай одним шагом или вопросом, а не списком всего подряд.\n"
)
```

В `tutor_system_prompt` включить блок в сборку частей — сразу после `base`:

```python
    parts = [base, _BREVITY_RULES, _TUTOR_TURN_RULES, _GUIDE_RULES]
```

- [ ] **Step 4: Запустить тесты промптов**

Run: `uv run pytest tests/test_prompts.py -q`
Expected: PASS (все тесты файла).

- [ ] **Step 5: Коммит**

```bash
git add src/llm_tutor/llm/prompts.py tests/test_prompts.py
git commit -m "Срез 11: правила лаконичности в промпте тьютора"
```

---

### Task 11.2: Модуль представления `bot/render.py` и HTML-оформление

**Files:**
- Create: `src/llm_tutor/bot/render.py`
- Modify: `src/llm_tutor/bot/handlers.py`
- Test: `tests/test_render.py`, `tests/test_handlers.py`

**Interfaces:**
- Consumes: `render_plan`, `_first_state` из `bot/handlers.py` (переносятся).
- Produces:
  - `render.escape(text: str) -> str`
  - `render.PARSE_MODE: str` (`"HTML"`)
  - `render._first_state(conn) -> SessionState`
  - `render.render_plan(conn, *, state=None, now=None, settings=None) -> str`
    (на этом срезе — поведение прежнее; форматирование меняется в 13.1)

- [ ] **Step 1: Написать падающий тест экранирования**

Создать `tests/test_render.py`:

```python
"""Тесты слоя представления бота."""

from llm_tutor.bot.render import MAX_MESSAGE, PARSE_MODE, escape, fit


def test_escape_neutralizes_model_markup() -> None:
    """Разметка из текста модели показывается буквально, а не как HTML."""
    assert escape("<b>x</b> & <i>y</i>") == "&lt;b&gt;x&lt;/b&gt; &amp; &lt;i&gt;y&lt;/i&gt;"


def test_parse_mode_is_html() -> None:
    assert PARSE_MODE == "HTML"


def test_fit_keeps_short_text_intact() -> None:
    assert fit("коротко") == "коротко"


def test_fit_does_not_break_html_entity() -> None:
    """Обрезка не рвёт сущность вида &amp; — иначе Telegram отвергнет HTML."""
    out = fit(escape("&" * 5000))  # после escape строка в разы длиннее лимита
    body = out.rstrip("…")
    assert len(out) <= MAX_MESSAGE + 1          # + многоточие
    assert body.count("&") == body.count(";")   # ни одной обрубленной сущности
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `uv run pytest tests/test_render.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'llm_tutor.bot.render'`.

- [ ] **Step 3: Создать `bot/render.py` и перенести `render_plan`**

Создать `src/llm_tutor/bot/render.py`:

```python
"""Слой представления бота: экранирование и тексты сообщений.

Всё, что бот отправляет с ``parse_mode=HTML``, формируется здесь. Тексты
модели и данные из БД перед вставкой экранируются (``escape``) — модель не
должна уметь вставлять разметку в свой ответ.
"""

import html
import sqlite3

from llm_tutor.config import Settings
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.llm.prompts import EMPTY_GRAPH_REPLY
from llm_tutor.schemas import SessionState
from llm_tutor.student import route as route_mod
from llm_tutor.student.planner import MODE_LABELS

PARSE_MODE = "HTML"
# Лимит Telegram 4096; держим запас.
MAX_MESSAGE = 4000


def escape(text: str) -> str:
    """Экранирует динамический текст для ``parse_mode=HTML``."""
    return html.escape(text, quote=False)


def fit(text: str) -> str:
    """Обрезает уже экранированный текст до лимита, не разрывая сущность.

    Экранировать нужно ДО обрезки: сущности длиннее исходных символов, и
    обрезка по «сырому» тексту пробила бы лимит Telegram. Но и резать по
    экранированному нельзя вслепую — можно оборвать ``&amp;`` на половине.
    """
    if len(text) <= MAX_MESSAGE:
        return text
    cut = text[:MAX_MESSAGE]
    amp = cut.rfind("&")
    if amp != -1 and ";" not in cut[amp:]:
        cut = cut[:amp]
    return cut + "…"


def _first_state(conn: sqlite3.Connection) -> SessionState:
    """Состояние открытой сессии (или пустое, если сессии ещё нет)."""
    session_id = repos.get_open_session(conn)
    return repos.get_session_state(conn, session_id) if session_id else SessionState()


def render_plan(
    conn: sqlite3.Connection,
    *,
    state: SessionState | None = None,
    now: float | None = None,
    settings: Settings | None = None,
) -> str:
    """Текст ``/plan``: маршрут к цели с прогрессом (перенос из handlers)."""
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
    if route.closed_count == len(route.steps):
        return "Всё доступное уже освоено — можно двигаться дальше или взять цель посложнее."

    goal_name = (
        graph.concept(route.goal_concept_id).name
        if route.goal_concept_id is not None
        else "вершина темы"
    )
    lines = [
        f"Маршрут: закрыто {route.closed_count} из {len(route.steps)}. Цель — {goal_name}."
    ]
    current = next((step for step in route.steps if step.status == "current"), None)
    if current is not None:
        lines.append(
            f"Сейчас: {graph.concept(current.concept_id).name} "
            f"({MODE_LABELS[current.mode]})."
        )
    ahead = [step for step in route.steps if step.status == "ahead"][:5]
    if ahead:
        names = ", ".join(
            f"{graph.concept(step.concept_id).name} ({MODE_LABELS[step.mode]})"
            for step in ahead
        )
        lines.append(f"Дальше: {names}.")
    return "\n".join(lines)
```

> Импорты в этом сниппете уже полные и самодостаточные: `menu` и `time`
> здесь не нужны и не импортируются (`menu.py` появится только в 12.1);
> `MODE_LABELS` подключён явно.

В `bot/handlers.py`:
- удалить функции `_first_state` и `render_plan` (перенесены);
- заменить импорт хендлера `/plan` на `from llm_tutor.bot import render`;
- в `on_plan` вызвать `render.render_plan(conn, settings=settings)`.

- [ ] **Step 4: Обновить импорты в тестах и добавить parse_mode**

В `tests/test_handlers.py` заменить импорт `render_plan` из
`llm_tutor.bot.handlers` на:

```python
from llm_tutor.bot.render import render_plan
```

В `bot/handlers.py` все отправки текста модели/кода перевести на
`render.fit(render.escape(...))` + `parse_mode=render.PARSE_MODE`
(**escape ДО fit** — см. конвенцию в Global Constraints), а наш HTML
(пока — только `render_plan`) слать как есть. Например `on_plan`:

```python
    @router.message(Command("plan"))
    async def on_plan(message: Message) -> None:
        try:
            text = render.render_plan(conn, settings=settings)
        except Exception:  # noqa: BLE001 — команда не должна отвечать молчанием
            logger.exception("Сбой построения маршрута")
            text = BOT_FAILURE_REPLY
        await message.answer(text, parse_mode=render.PARSE_MODE)
```

Аналогично метки-текстовые ответы (`on_text`, `on_answer`, `on_task`,
`on_skip`, `on_start`) шлют `render.fit(render.escape(reply.text))` и
`parse_mode=render.PARSE_MODE`.

Убрать из `bot/handlers.py` импорты, ставшие неиспользуемыми после переноса
`render_plan` (`EMPTY_GRAPH_REPLY`, `MODE_LABELS`, `route_mod` и прочие, что
больше не упоминаются). Не трогать то, что ещё используется.

- [ ] **Step 5: Запустить тесты**

Run: `uv run pytest tests/test_render.py tests/test_handlers.py tests/test_turn.py -q`
Expected: PASS.

- [ ] **Step 6: Коммит**

```bash
git add src/llm_tutor/bot/render.py src/llm_tutor/bot/handlers.py tests/test_render.py tests/test_handlers.py
git commit -m "Срез 11: слой представления и HTML-оформление ответов"
```

---

### Task 12.1: Модуль меню `bot/menu.py`

**Files:**
- Create: `src/llm_tutor/bot/menu.py`
- Test: `tests/test_menu.py`

**Interfaces:**
- Produces:
  - константы-метки `LABEL_STATUS`, `LABEL_ROUTE`, `LABEL_THEMES`,
    `LABEL_TASK`, `LABEL_STUCK`, `LABEL_SKIP`, `LABEL_HELP`;
  - `MENU_LABELS: tuple[str, ...]`;
  - `MENU_ACTIONS: dict[str, Action]`;
  - `main_menu() -> ReplyKeyboardMarkup`.

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_menu.py`:

```python
"""Тесты меню бота."""

from llm_tutor.bot import menu


def test_menu_labels_are_unique() -> None:
    assert len(set(menu.MENU_LABELS)) == len(menu.MENU_LABELS)


def test_action_map_covers_every_label() -> None:
    """Каждой метке соответствует ровно одно действие."""
    assert set(menu.MENU_ACTIONS) == set(menu.MENU_LABELS)


def test_main_menu_has_all_labels() -> None:
    kb = menu.main_menu()
    texts = [button.text for row in kb.keyboard for button in row]
    assert set(texts) == set(menu.MENU_LABELS)


def test_main_menu_is_persistent_and_resized() -> None:
    kb = menu.main_menu()
    assert kb.is_persistent is True
    assert kb.resize_keyboard is True
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `uv run pytest tests/test_menu.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'llm_tutor.bot.menu'`.

- [ ] **Step 3: Создать `bot/menu.py`**

```python
"""Постоянное меню бота: reply-клавиатура и карта действий.

Метки — единый источник истины: клавиатура строится из них, и по ним же
перехватывается нажатие (reply-кнопка отправляет свой текст сообщением).
Эмодзи-префикс отделяет нажатие от свободного вопроса ученика.
"""

from typing import Literal

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

Action = Literal["status", "route", "task", "skip", "help"]

LABEL_STATUS = "📚 Моё обучение"
LABEL_ROUTE = "🗺 Маршрут"
LABEL_TASK = "🎯 Задание"
LABEL_SKIP = "⏭ Пропустить"
LABEL_HELP = "ℹ️ Что умею"

MENU_LABELS: tuple[str, ...] = (
    LABEL_STATUS,
    LABEL_ROUTE,
    LABEL_TASK,
    LABEL_SKIP,
    LABEL_HELP,
)

MENU_ACTIONS: dict[str, Action] = {
    LABEL_STATUS: "status",
    LABEL_ROUTE: "route",
    LABEL_TASK: "task",
    LABEL_SKIP: "skip",
    LABEL_HELP: "help",
}


def main_menu() -> ReplyKeyboardMarkup:
    """Постоянная клавиатура меню (не сворачивается).

    `🎚 Темы` и `❓ Не понимаю` добавляются в 14.1/14.2 вместе со своими
    действиями: в срезе 12 кнопок без обработчика не бывает.
    """
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=LABEL_STATUS), KeyboardButton(text=LABEL_ROUTE)],
            [KeyboardButton(text=LABEL_TASK), KeyboardButton(text=LABEL_SKIP)],
            [KeyboardButton(text=LABEL_HELP)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )
```

- [ ] **Step 4: Запустить тесты**

Run: `uv run pytest tests/test_menu.py -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add src/llm_tutor/bot/menu.py tests/test_menu.py
git commit -m "Срез 12: метки и постоянная клавиатура меню"
```

---

### Task 12.2: Статус «Моё обучение» и справка «Что умею»

**Files:**
- Modify: `src/llm_tutor/bot/render.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `render._first_state`, `render.render_plan` (11.2).
- Produces:
  - `render.render_status(conn, *, state=None, now=None, settings=None) -> str`
  - `render.render_help() -> str`
  - `render.PHASE_LABELS: dict[str, str]`

- [ ] **Step 1: Написать падающий тест**

Добавить в `tests/test_render.py`:

```python
from llm_tutor.bot import menu
from llm_tutor.bot.render import render_help, render_status
from llm_tutor.course.seed import load_seed
from llm_tutor.db import repos
from llm_tutor.schemas import SessionState


def test_render_status_shows_current_node_and_phase(conn, settings) -> None:
    load_seed(conn)
    session_id = repos.ensure_open_session(conn, now=1.0)
    repos.update_session_state(
        conn,
        session_id,
        SessionState(current_node_id="groupby", phase="practice", hint_level=1),
    )

    text = render_status(conn, now=0.0, settings=settings)

    assert "groupby" in text
    assert "практика" in text
    assert "подсказк" in text.lower()


def test_render_help_lists_menu_labels() -> None:
    text = render_help()
    for label in menu.MENU_LABELS:
        assert label in text
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `uv run pytest tests/test_render.py -k "status or help" -v`
Expected: FAIL — `ImportError: cannot import name 'render_status'`.

- [ ] **Step 3: Реализовать `render_status` и `render_help`**

Добавить в импорты `src/llm_tutor/bot/render.py`:
`from llm_tutor.student.hints import HINT_LEVEL_NAMES`.

Добавить в `src/llm_tutor/bot/render.py`:

```python
PHASE_LABELS: dict[str, str] = {
    "explain": "объяснение",
    "practice": "практика",
    "check": "проверка",
}


def render_status(
    conn: sqlite3.Connection,
    *,
    state: SessionState | None = None,
    now: float | None = None,
    settings: Settings | None = None,
) -> str:
    """Дашборд ученика: где он, что делает и как идёт маршрут."""
    graph = CourseGraph.load(conn)
    if not graph.node_ids:
        return EMPTY_GRAPH_REPLY

    session_state = state or _first_state(conn)
    lines = ["📚 <b>Моё обучение</b>"]

    node_id = session_state.current_node_id
    if node_id is not None and graph.has_node(node_id):
        lines.append(f"Сейчас: <b>{escape(graph.concept(node_id).name)}</b>")
    else:
        lines.append("Сейчас: тема ещё не выбрана — жми 🗺 Маршрут.")
    lines.append(f"Фаза: {PHASE_LABELS.get(session_state.phase, session_state.phase)}")
    level = session_state.hint_level
    lines.append(
        f"Уровень подсказки: {level} "
        f"({HINT_LEVEL_NAMES.get(level, 'без подсказки')})"
    )

    route = route_mod.build_route(
        conn,
        graph,
        goal_concept_id=route_mod.goal_for(conn, graph),
        current_node_id=session_state.current_node_id,
        previous=session_state.route,
        now=now,
        settings=settings,
    )
    if route.steps:
        lines.append(f"Маршрут: пройдено {route.closed_count} из {len(route.steps)}")

    if session_state.pending_item_id is not None:
        lines.append("Ждёт ответа задание — ответь или жми ⏭ Пропустить.")
    else:
        lines.append("Задания нет — жми 🎯 Задание.")
    return "\n".join(lines)


def render_help() -> str:
    """Справка по кнопкам меню.

    Список синхронен с `menu.MENU_LABELS` текущего среза: на срезе 12 это
    пять кнопок; строки про `❓ Не понимаю` и `🎚 Темы` добавляют 14.1/14.2.
    """
    return (
        "ℹ️ <b>Что умею</b>\n"
        f"{menu.LABEL_STATUS} — где ты сейчас и как идёт маршрут\n"
        f"{menu.LABEL_ROUTE} — путь к цели с прогрессом\n"
        f"{menu.LABEL_TASK} — взять задание по текущей теме\n"
        f"{menu.LABEL_SKIP} — пропустить текущее задание\n"
        f"{menu.LABEL_HELP} — эта справка"
    )
```

Добавить в импорты `render.py` модуль меню: `from llm_tutor.bot import menu`.

- [ ] **Step 4: Запустить тесты**

Run: `uv run pytest tests/test_render.py -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add src/llm_tutor/bot/render.py tests/test_render.py
git commit -m "Срез 12: дашборд «Моё обучение» и справка «Что умею»"
```

---

### Task 12.3: Хендлер меню в роутере

**Files:**
- Modify: `src/llm_tutor/bot/handlers.py`
- Test: `tests/test_handlers.py`

**Interfaces:**
- Consumes: `menu.MENU_LABELS`, `menu.MENU_ACTIONS`, `menu.main_menu`,
  `render.render_status`, `render.render_help`, `render.render_plan`,
  `core.turn.start_practice_reply`, `core.turn.skip_pending`.

- [ ] **Step 1: Написать падающий тест**

Добавить в `tests/test_handlers.py`:

```python
def test_menu_handler_is_registered(conn) -> None:
    """Хендлер меню реально подключён к роутеру (а не потерялся)."""
    router = make_router(conn, _FakeClient(), "m")
    names = [h.callback.__name__ for h in router.message.handlers]
    assert "on_menu" in names
```

> Полнота карты «метка → действие» проверяется в
> `tests/test_menu.py::test_action_map_covers_every_label`.

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `uv run pytest tests/test_handlers.py::test_menu_handler_is_registered -v`
Expected: FAIL — хендлера `on_menu` в роутере пока нет.

- [ ] **Step 3: Добавить меню-хендлер и прикрепить клавиатуру**

В `bot/handlers.py` импортировать `from llm_tutor.bot import menu, render`
и в `make_router` **до** хендлера свободного текста (`on_text`) добавить:

```python
    @router.message(StateFilter(None), F.text.in_(frozenset(menu.MENU_LABELS)))
    async def on_menu(message: Message) -> None:
        action = menu.MENU_ACTIONS.get(message.text or "")
        if action == "status":
            text = render.render_status(conn, settings=settings)
            await message.answer(text, parse_mode=render.PARSE_MODE)
        elif action == "route":
            text = render.render_plan(conn, settings=settings)
            await message.answer(text, parse_mode=render.PARSE_MODE)
        elif action == "help":
            await message.answer(render.render_help(), parse_mode=render.PARSE_MODE)
        elif action == "task":
            reply = start_practice_reply(conn, settings=settings)
            await message.answer(
                render.fit(render.escape(reply.text)),
                parse_mode=render.PARSE_MODE,
                reply_markup=_options_keyboard(reply.options),
            )
        elif action == "skip":
            text = skip_pending(conn, settings=settings)
            await message.answer(
                render.fit(render.escape(text)),
                parse_mode=render.PARSE_MODE,
                reply_markup=menu.main_menu(),
            )
```

> Ветки `task`/`answer` шлют инлайн-кнопки вариантов — постоянное меню на них
> не помещается (одно поле `reply_markup`), но оно и так висит (persistent).
> К остальным текстовым ответам меню прикрепляется явно.

- в `on_start` (ветка «анкета заполнена») и в `on_text` прикрепить клавиатуру
  к обычным ответам: добавить `reply_markup=menu.main_menu()`.

- [ ] **Step 4: Запустить тесты**

Run: `uv run pytest tests/test_handlers.py tests/test_menu.py -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add src/llm_tutor/bot/handlers.py tests/test_handlers.py
git commit -m "Срез 12: меню-хендлер — статус, маршрут, задание, справка"
```

---

### Task 12.4: Онбординг — представление до анкеты

**Files:**
- Modify: `src/llm_tutor/bot/survey.py`, `src/llm_tutor/bot/handlers.py`
- Test: `tests/test_survey.py`

**Interfaces:**
- Consumes: `menu.main_menu`.
- Produces: константа `INTRO_TEXT` в `bot/survey.py`; обновлённый
  `SURVEY_DONE_REPLY`; `ask(message, state, index=0)` без изменений сигнатуры.

- [ ] **Step 1: Написать падающий тест**

Добавить в `tests/test_survey.py`:

```python
def test_onboarding_texts_exist() -> None:
    """Вход в курс объясняет, как учиться, и указывает на меню."""
    from llm_tutor.bot import survey as bot_survey

    assert "учиться" in bot_survey.INTRO_TEXT.lower()
    assert "меню" in bot_survey.INTRO_TEXT.lower()
    assert "маршрут" in bot_survey.SURVEY_DONE_REPLY.lower()
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `uv run pytest tests/test_survey.py::test_onboarding_texts_exist -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'INTRO_TEXT'`.

- [ ] **Step 3: Добавить тексты онбординга**

В `src/llm_tutor/bot/survey.py` заменить `SURVEY_DONE_REPLY` и добавить
`INTRO_TEXT`:

```python
INTRO_TEXT = (
    "👋 Привет! Я — твой тьютор по теме 1 курса mlcourse.ai: «Pandas / EDA».\n\n"
    "Как мы будем учиться:\n"
    "  1 · короткая анкета — что ты уже знаешь\n"
    "  2 · соберу маршрут под твою цель\n"
    "  3 · ведём по шагам: объясняю → даю задачу → проверяю\n\n"
    "Меню — на кнопках внизу. Застрял — жми ❓ Не понимаю.\n\n"
    "Начнём 👇"
)

SURVEY_DONE_REPLY = (
    "✅ Профиль заполнен. Можно посмотреть маршрут (🗺)\n"
    "или сразу взять первое задание (🎯).\n"
    "Я рядом — жми ❓, если что-то непонятно."
)
```

- [ ] **Step 4: Показать представление и вернуть меню**

В `bot/handlers.py`, в `on_start`, в ветке «анкета не заполнена» — сначала
отправть представление с меню, потом запустить анкету:

```python
    @router.message(CommandStart())
    async def on_start(message: Message, state: FSMContext) -> None:
        if not survey_completed(conn):
            from llm_tutor.bot.survey import INTRO_TEXT

            await message.answer(INTRO_TEXT, reply_markup=menu.main_menu())
            await ask_survey(message, state)
            return
        reply = await handle_start(conn, client, model, START_GREETING)
        await message.answer(
            render.escape(_truncate(reply)),
            parse_mode=render.PARSE_MODE,
            reply_markup=menu.main_menu(),
        )
```

(Импорт `INTRO_TEXT` поднять в общий блок импортов файла, а не держать
внутри функции, если так чище по стилю проекта.)

В `bot/survey.py`, в `make_survey_router`, к сообщению о завершении анкеты
прикрепить меню:

```python
        survey.apply_answers(conn, answers, settings=settings)
        await state.clear()
        await callback.message.answer(SURVEY_DONE_REPLY, reply_markup=menu.main_menu())
        await callback.answer()
```

Добавить в импорты `survey.py`: `from llm_tutor.bot import menu`.

> **Замечание по спеке §3.4.** Полностью скрыть reply-клавиатуру на время
> анкеты нельзя: `ReplyKeyboardRemove` и инлайн-кнопки вариантов — одно поле
> `reply_markup`, их не совместить в одном сообщении. Поэтому меню остаётся
> видимым, а нажатие метки во время анкеты ловит существующий `on_text`
> анкеты (`BUTTON_HINT_REPLY`). Это осознанное отклонение от буквы §3.4;
> при желании — обновим спеку.

- [ ] **Step 5: Запустить тесты**

Run: `uv run pytest tests/test_survey.py tests/test_handlers.py tests/test_bot_flows.py -q`
Expected: PASS.

- [ ] **Step 6: Коммит**

```bash
git add src/llm_tutor/bot/survey.py src/llm_tutor/bot/handlers.py tests/test_survey.py
git commit -m "Срез 12: онбординг — представление до анкеты и меню после"
```

---

### Task 13.1: Табличка маршрута в `render_plan`

**Files:**
- Modify: `src/llm_tutor/bot/render.py`
- Test: `tests/test_handlers.py` (тесты `render_plan` переезжают сюда из
  старого формата; фактически — правка существующих)

**Interfaces:**
- Consumes: `route_mod.build_route`, `graph.concept`.
- Produces: `render_plan` нового формата; константы `PLAN_WINDOW`,
  `_STATUS_MARKS`, `_PROGRESS_WIDTH`; хелперы `_progress_bar`, `_windowed`.

- [ ] **Step 1: Переписать тесты маршрута**

В `tests/test_handlers.py` заменить `test_render_plan_shows_route_progress` на:

```python
def test_render_plan_draws_table_with_progress(conn, settings) -> None:
    """Маршрут — табличка с прогрессом и текущим узлом."""
    load_seed(conn)
    repos.set_fact(conn, "goal_concept_id", "summary_tables")
    session_id = repos.ensure_open_session(conn, now=1.0)
    repos.update_session_state(
        conn, session_id, SessionState(current_node_id="groupby")
    )

    text = render_plan(conn, now=0.0, settings=settings)

    assert "<pre>" in text
    assert "Маршрут" in text
    assert "Пройдено" in text
    assert "[>]" in text            # метка текущего узла
    assert "groupby" in text
```

Добавить тест окна (импорт `PLAN_WINDOW` из `llm_tutor.bot.render`, а также
`CourseGraph` и `route_mod`):

```python
def test_render_plan_windows_long_route(conn, settings) -> None:
    """Длинный маршрут показывается окном, а не целиком."""
    from llm_tutor.bot.render import PLAN_WINDOW
    from llm_tutor.course.graph import CourseGraph
    from llm_tutor.student import route as route_mod

    load_seed(conn)
    repos.set_fact(conn, "goal_concept_id", "churn_eda_case")
    session_id = repos.ensure_open_session(conn, now=1.0)
    repos.update_session_state(
        conn, session_id, SessionState(current_node_id="read_csv")
    )

    text = render_plan(conn, now=0.0, settings=settings)

    graph = CourseGraph.load(conn)
    full_route = route_mod.build_route(
        conn,
        graph,
        goal_concept_id="churn_eda_case",
        current_node_id="read_csv",
        now=0.0,
        settings=settings,
    )
    marks = text.count("[x]") + text.count("[>]") + text.count("[ ]")
    assert marks < len(full_route.steps)     # показано меньше, чем весь путь
    assert marks <= PLAN_WINDOW * 2 + 3
```

Тесты `test_render_plan_reports_all_mastered` и
`test_render_plan_empty_graph_hints_seed` оставить без изменений.

- [ ] **Step 2: Запустить тесты — убедиться, что падают**

Run: `uv run pytest tests/test_handlers.py -k render_plan -v`
Expected: FAIL — в тексте нет `<pre>` (текущий формат — строки, а не табличка).

- [ ] **Step 3: Реализовать табличку**

В `src/llm_tutor/bot/render.py` заменить тело `render_plan` и добавить
хелперы:

```python
PLAN_WINDOW = 3
_PROGRESS_WIDTH = 15
_STATUS_MARKS = {"closed": "[x]", "current": "[>]", "ahead": "[ ]"}


def _progress_bar(closed: int, total: int, *, width: int = _PROGRESS_WIDTH) -> str:
    """Полоса прогресса из моноширинных блоков."""
    if total <= 0:
        return "░" * width
    filled = round(closed / total * width)
    return "█" * filled + "░" * (width - filled)


def _windowed(steps: list, window: int) -> list:
    """Окно вокруг текущего шага плюс начало и цель (без дублей)."""
    idx = next((i for i, s in enumerate(steps) if s.status == "current"), None)
    if idx is None or len(steps) <= window * 2 + 1:
        return steps
    start = max(0, idx - window)
    end = min(len(steps), idx + window + 1)
    chosen = list(steps[start:end])
    if start > 0 and chosen[0] is not steps[0]:
        chosen = [steps[0]] + chosen
    if end < len(steps):
        chosen = chosen + [steps[-1]]
    return chosen


def render_plan(
    conn: sqlite3.Connection,
    *,
    state: SessionState | None = None,
    now: float | None = None,
    settings: Settings | None = None,
) -> str:
    """Табличка маршрута: путь, прогресс и окно вокруг текущего узла."""
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
    if route.closed_count == len(route.steps):
        return "Всё доступное уже освоено — можно двигаться дальше или взять цель посложнее."

    goal_name = (
        graph.concept(route.goal_concept_id).name
        if route.goal_concept_id is not None
        else "вершина темы"
    )
    total = len(route.steps)
    shown = _windowed(route.steps, PLAN_WINDOW)

    name_w = max(len(graph.concept(step.concept_id).name) for step in shown)
    label_w = len("←сейчас")

    cells: list[str] = []
    for step in shown:
        name = graph.concept(step.concept_id).name
        if step.concept_id == route.goal_concept_id:
            label = "цель"
        elif step.status == "current":
            label = "←сейчас"
        elif step.status == "closed":
            label = "усвоен"
        else:
            label = "впереди"
        cells.append(f"  {_STATUS_MARKS[step.status]}  {name.ljust(name_w)} {label.rjust(label_w)} ")

    bar = f" Пройдено  {_progress_bar(route.closed_count, total)}  {route.closed_count}/{total} "
    inner = max(len(bar), *(len(cell) for cell in cells))
    lines = [
        "┌" + "─" * inner + "┐",
        f"│{bar.ljust(inner)}│",
        "├" + "─" * inner + "┤",
        *(f"│{cell.ljust(inner)}│" for cell in cells),
        "└" + "─" * inner + "┘",
    ]
    header = (
        f"🗺 <b>Маршрут</b> — цель «{escape(goal_name)}», "
        f"пройдено {route.closed_count}/{total}"
    )
    return header + "\n<pre>" + "\n".join(lines) + "</pre>"
```

- [ ] **Step 4: Запустить тесты**

Run: `uv run pytest tests/test_handlers.py -k render_plan -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add src/llm_tutor/bot/render.py tests/test_handlers.py
git commit -m "Срез 13: табличка маршрута вместо строк"
```

---

### Task 14.1: Усиленный проход — `stuck_reply`

**Files:**
- Modify: `src/llm_tutor/core/turn.py`
- Test: `tests/test_turn.py`

**Interfaces:**
- Consumes: `_tutor_branch`, `guide.on_student_stuck`, `post_turn`,
  `route_mod.refresh`.
- Produces: `core.turn.stuck_reply(conn, client, model, *, now=None, settings=None) -> TurnReply`;
  параметр `force_stuck: bool = False` у `_tutor_branch`.

- [ ] **Step 1: Написать падающий тест**

Добавить в `tests/test_turn.py`:

```python
async def test_stuck_reply_forces_reinforce_with_pending_task(conn, settings) -> None:
    """«Не понимаю» не съедает задание и включает усиленный проход."""
    from llm_tutor.core.turn import stuck_reply

    load_seed(conn)
    ingest_text(conn, "# T\n\n## Grouping\n\ngroupby aggregates rows\n", "u")
    item = repos.get_item(conn, 6)
    _set_state(conn, pending_item_id=item.id, current_node_id="python_basics", node_streak=1)
    client = _FakeTutor("объясняю подробнее")  # student_stuck по умолчанию False

    await stuck_reply(conn, client, "m", now=1.0, settings=settings)

    state = _state(conn)
    assert state.mode == "reinforce"
    assert state.node_streak == 0
    assert state.current_node_id == "python_basics"
    assert state.pending_item_id == item.id  # задание не потеряно
    assert state.task_hinted is True         # помощь оказана
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `uv run pytest tests/test_turn.py::test_stuck_reply_forces_reinforce_with_pending_task -v`
Expected: FAIL — `ImportError: cannot import name 'stuck_reply'`.

- [ ] **Step 3: Реализовать `stuck_reply` и `force_stuck`**

В `src/llm_tutor/core/turn.py`:
- добавить константу рядом с прочими:

```python
# Повод тьюторского хода при нажатии «Не понимаю».
STUCK_KICKOFF_TEXT = "Не понял, давай подробнее по текущей теме."
```

- в `_tutor_branch` добавить параметр и расширить условие перехода в усиленный
  проход:

```python
async def _tutor_branch(
    conn, client, model, session_id, user_text, state, graph, *,
    now, settings, force_stuck: bool = False,
) -> tuple[str, list[Event], list[beta.MasteryUpdate], SessionState]:
```

и ниже:

```python
    if answer.student_stuck or force_stuck:
        # Ученик просит глубины (или нажал «Не понимаю»): узел в усиленный
        # проход, вперёд не идём.
        stuck_state = guide.on_student_stuck(new_state).model_copy(
            update={"task_hinted": True}
        )
        return f"{answer.reply}\n\n{STUCK_NOTE}", [], [], stuck_state
```

- добавить публичную функцию рядом с `start_practice_reply`:

```python
async def stuck_reply(
    conn: sqlite3.Connection,
    client: LLMClient,
    model: str,
    *,
    now: float | None = None,
    settings: Settings | None = None,
) -> TurnReply:
    """Ученик нажал «Не понимаю»: углубляем текущий узел.

    Идёт тьюторским путём независимо от ``pending_item_id`` — иначе висящее
    задание приняло бы реплику за ответ.
    """
    s = settings or get_settings()
    stamp = time.time() if now is None else now
    session_id = repos.ensure_open_session(conn, stamp)
    state = repos.get_session_state(conn, session_id)
    graph = CourseGraph.load(conn)

    reply, events, mastery, new_state = await _tutor_branch(
        conn,
        client,
        model,
        session_id,
        STUCK_KICKOFF_TEXT,
        state,
        graph,
        now=stamp,
        settings=s,
        force_stuck=True,
    )
    fresh_route, _ = route_mod.refresh(conn, new_state, graph, now=stamp, settings=s)
    post_turn(
        conn,
        session_id,
        user_text=STUCK_KICKOFF_TEXT,
        assistant_text=reply,
        state=new_state.model_copy(update={"route": fresh_route}),
        events=events,
        mastery=mastery,
        now=stamp,
    )
    return TurnReply(text=reply)
```

- [ ] **Step 4: Добавить кнопку и подключить `stuck_reply`**

В `bot/menu.py` добавить метку, действие и кнопку:

```python
LABEL_STUCK = "❓ Не понимаю"      # рядом с прочими метками

Action = Literal["status", "route", "task", "stuck", "skip", "help"]
MENU_LABELS = (..., LABEL_STUCK, ...)
MENU_ACTIONS = {..., LABEL_STUCK: "stuck", ...}
```

В `main_menu()` второй ряд становится `[TASK, STUCK, SKIP]`:

```python
            [KeyboardButton(text=LABEL_TASK), KeyboardButton(text=LABEL_STUCK),
             KeyboardButton(text=LABEL_SKIP)],
```

В `bot/handlers.py`:
- импортировать `stuck_reply` из `llm_tutor.core.turn`;
- в меню-хендлере добавить ветку:

```python
        elif action == "stuck":
            reply = await stuck_reply(conn, client, model, settings=settings)
            await message.answer(
                render.fit(render.escape(reply.text)),
                parse_mode=render.PARSE_MODE,
                reply_markup=menu.main_menu(),
            )
```

Добавить в `render.render_help` строку про `menu.LABEL_STUCK`
(`— не понял: объясню подробнее`) — иначе справка разойдётся с меню, и
`test_render_help_lists_menu_labels` упадёт.

- [ ] **Step 5: Запустить тесты**

Run: `uv run pytest tests/test_turn.py tests/test_handlers.py -q`
Expected: PASS.

- [ ] **Step 6: Коммит**

```bash
git add src/llm_tutor/core/turn.py src/llm_tutor/bot/handlers.py tests/test_turn.py
git commit -m "Срез 14: кнопка «Не понимаю» — усиленный проход без потери задания"
```

---

### Task 14.2: Навигация «Темы» — выбор узла с предупреждением

**Files:**
- Create: `src/llm_tutor/bot/themes.py`
- Modify: `src/llm_tutor/bot/handlers.py`, `src/llm_tutor/bot/main.py`
- Test: `tests/test_themes.py`

**Interfaces:**
- Consumes: `beta.estimate`, `planner.CONFIDENT_UNCERTAINTY`,
  `graph.topo_order`, `graph.hard_prerequisites`, `route_mod.refresh`,
  `post_turn`.
- Produces:
  - `themes.node_status(conn, graph, node_id, state, *, now, settings) -> NodeStatus`
  - `themes.themes_keyboard(conn, *, state=None, now=None, settings=None) -> InlineKeyboardMarkup`
  - `themes.switch_node(conn, node_id, *, now=None, settings=None) -> str`
  - `themes.make_themes_router(conn, settings) -> Router`

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_themes.py`:

```python
"""Тесты навигации по узлам темы."""

from llm_tutor.bot import themes
from llm_tutor.course.graph import CourseGraph
from llm_tutor.course.seed import load_seed
from llm_tutor.db import repos
from llm_tutor.schemas import SessionState


def test_node_status_marks_current(conn, settings) -> None:
    load_seed(conn)
    graph = CourseGraph.load(conn)
    state = SessionState(current_node_id="groupby")

    assert themes.node_status(conn, graph, "groupby", state, now=0.0, settings=settings) == "current"


def test_node_status_ahead_then_available(conn, settings) -> None:
    """Узел впереди, пока пререквизиты открыты; доступен, когда закрыты."""
    load_seed(conn)
    graph = CourseGraph.load(conn)
    node = next(n for n in graph.topo_order() if graph.hard_prerequisites(n))
    state = SessionState()

    assert themes.node_status(conn, graph, node, state, now=0.0, settings=settings) == "ahead"
    for prereq in graph.hard_prerequisites(node):
        repos.upsert_mastery(conn, prereq, alpha=38.0, beta=2.0, last_seen=0.0, next_review=1e9)
    assert themes.node_status(conn, graph, node, state, now=0.0, settings=settings) == "available"


def test_themes_keyboard_has_button_per_node(conn, settings) -> None:
    load_seed(conn)
    graph = CourseGraph.load(conn)

    kb = themes.themes_keyboard(conn, state=SessionState(), now=0.0, settings=settings)

    callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert len(callbacks) == len(graph.node_ids)
    assert all(cb.startswith("theme:") for cb in callbacks)


async def test_switch_node_changes_current_and_clears_pending(conn, settings) -> None:
    load_seed(conn)
    item = repos.get_item(conn, 6)
    session_id = repos.ensure_open_session(conn, now=1.0)
    repos.update_session_state(
        conn,
        session_id,
        SessionState(current_node_id="python_basics", pending_item_id=item.id),
    )

    themes.switch_node(conn, "groupby", now=2.0, settings=settings)

    state = repos.get_session_state(conn, session_id)
    assert state.current_node_id == "groupby"
    assert state.pending_item_id is None
    assert state.phase == "explain"
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `uv run pytest tests/test_themes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'llm_tutor.bot.themes'`.

- [ ] **Step 3: Создать `bot/themes.py`**

```python
"""Навигация по узлам темы: список со статусами и переход к выбранному.

Прыжок вперёд (не закрыты жёсткие пререквизиты) требует подтверждения —
это предупреждение, а не запрет: ученик решает сам.
"""

import sqlite3
import time
from typing import Literal

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from llm_tutor.bot.render import PARSE_MODE, escape
from llm_tutor.config import Settings, get_settings
from llm_tutor.core.turn import post_turn
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.schemas import SessionState
from llm_tutor.student import beta, route as route_mod
from llm_tutor.student.planner import CONFIDENT_UNCERTAINTY

NodeStatus = Literal["closed", "current", "available", "ahead"]

STATUS_ICONS: dict[str, str] = {
    "closed": "✅",
    "current": "▶️",
    "available": "🟢",
    "ahead": "🔜",
}

THEMES_PROMPT = (
    "🎚 <b>Куда идём?</b>\n\n"
    "Можно вернуться к пройденному или заглянуть вперёд — "
    "про будущее предупрежу, там ещё не закрыты пререквизиты."
)


def _is_closed(
    conn: sqlite3.Connection, node_id: str, *, now: float | None, settings: Settings
) -> bool:
    mastery = beta.estimate(conn, node_id, now=now, settings=settings)
    return (
        mastery.mean >= settings.mastery_skip_threshold
        and mastery.uncertainty <= CONFIDENT_UNCERTAINTY
    )


def node_status(
    conn: sqlite3.Connection,
    graph: CourseGraph,
    node_id: str,
    state: SessionState,
    *,
    now: float | None,
    settings: Settings,
) -> NodeStatus:
    """Статус узла для списка тем."""
    if node_id == state.current_node_id:
        return "current"
    if _is_closed(conn, node_id, now=now, settings=settings):
        return "closed"
    prereqs = graph.hard_prerequisites(node_id)
    if all(_is_closed(conn, prereq, now=now, settings=settings) for prereq in prereqs):
        return "available"
    return "ahead"


def themes_keyboard(
    conn: sqlite3.Connection,
    *,
    state: SessionState | None = None,
    now: float | None = None,
    settings: Settings | None = None,
) -> InlineKeyboardMarkup:
    """Инлайн-список всех узлов темы со статусами (по 2 в ряду)."""
    s = settings or get_settings()
    graph = CourseGraph.load(conn)
    # Это чтение, а не ход: сессию НЕ заводим (как в ``_pending_item``).
    session_id = repos.get_open_session(conn)
    session_state = state or (
        repos.get_session_state(conn, session_id) if session_id else SessionState()
    )
    buttons: list[InlineKeyboardButton] = []
    for node_id in graph.topo_order():
        status = node_status(conn, graph, node_id, session_state, now=now, settings=s)
        label = f"{STATUS_ICONS[status]} {graph.concept(node_id).name}"
        buttons.append(
            InlineKeyboardButton(text=label, callback_data=f"theme:{node_id}")
        )
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def switch_node(
    conn: sqlite3.Connection,
    node_id: str,
    *,
    now: float | None = None,
    settings: Settings | None = None,
) -> str:
    """Переход к узлу: смена текущей темы, снятие висящего задания."""
    s = settings or get_settings()
    stamp = time.time() if now is None else now
    session_id = repos.ensure_open_session(conn, stamp)
    state = repos.get_session_state(conn, session_id)
    graph = CourseGraph.load(conn)
    name = graph.concept(node_id).name

    new_state = state.model_copy(
        update={
            "current_node_id": node_id,
            "phase": "explain",
            "mode": None,
            "node_streak": 0,
            "hint_level": 0,
            "pending_item_id": None,
            "last_activity": stamp,
        }
    )
    fresh_route, _ = route_mod.refresh(conn, new_state, graph, now=stamp, settings=s)
    text = (
        f"Ок, тема — <b>{escape(name)}</b>. "
        "Спроси, что непонятно, или жми 🎯 Задание."
    )
    post_turn(
        conn,
        session_id,
        user_text=f"Перейти к теме: {node_id}",
        assistant_text=text,
        state=new_state.model_copy(update={"route": fresh_route}),
        now=stamp,
    )
    return text


def make_themes_router(conn: sqlite3.Connection, settings: Settings) -> Router:
    """Роутер выбора темы: узел сразу либо с подтверждением для «вперёд»."""
    router = Router()

    def _ahead_prereq_names(graph: CourseGraph, node_id: str) -> list[str]:
        return [
            graph.concept(p).name
            for p in graph.hard_prerequisites(node_id)
            if not _is_closed(conn, p, now=time.time(), settings=settings)
        ]

    @router.callback_query(F.data.startswith("theme_go:"))
    async def on_theme_go(callback: CallbackQuery) -> None:
        node_id = (callback.data or "").split(":", 1)[1]
        text = switch_node(conn, node_id, settings=settings)
        await callback.message.answer(text, parse_mode=PARSE_MODE)
        await callback.answer()

    @router.callback_query(F.data.startswith("theme:"))
    async def on_theme(callback: CallbackQuery) -> None:
        node_id = (callback.data or "").split(":", 1)[1]
        graph = CourseGraph.load(conn)
        state = repos.get_session_state(conn, repos.ensure_open_session(conn, time.time()))
        status = node_status(conn, graph, node_id, state, now=time.time(), settings=settings)
        if status == "ahead":
            prereqs = ", ".join(_ahead_prereq_names(graph, node_id))
            text = (
                f"«{escape(graph.concept(node_id).name)}» — не закрыты "
                f"пререквизиты ({escape(prereqs)}). Всё равно идём?"
            )
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="🎯 Да", callback_data=f"theme_go:{node_id}"),
                        InlineKeyboardButton(text="↩️ Нет", callback_data="theme_cancel"),
                    ]
                ]
            )
            await callback.message.answer(text, parse_mode=PARSE_MODE, reply_markup=keyboard)
        else:
            text = switch_node(conn, node_id, settings=settings)
            await callback.message.answer(text, parse_mode=PARSE_MODE)
        await callback.answer()

    @router.callback_query(F.data == "theme_cancel")
    async def on_theme_cancel(callback: CallbackQuery) -> None:
        await callback.answer("Остаёмся на месте")

    return router
```

- [ ] **Step 4: Добавить кнопку «Темы», подключить роутер и обработчик**

В `bot/menu.py` добавить метку, действие и кнопку:

```python
LABEL_THEMES = "🎚 Темы"
Action = Literal["status", "route", "themes", "task", "stuck", "skip", "help"]
MENU_LABELS = (..., LABEL_THEMES, ...)
MENU_ACTIONS = {..., LABEL_THEMES: "themes", ...}
```
первый ряд `main_menu()` становится `[STATUS, ROUTE, THEMES]`.

В `bot/handlers.py` в ветке `action == "themes"` меню-хендлера:

```python
        elif action == "themes":
            await message.answer(
                themes.THEMES_PROMPT,
                parse_mode=render.PARSE_MODE,
                reply_markup=themes.themes_keyboard(conn, settings=settings),
            )
```

Добавить импорт `from llm_tutor.bot import themes`. Добавить в
`render.render_help` строку про `menu.LABEL_THEMES`
(`— выбрать тему: вернуться или забежать вперёд`), чтобы справка осталась
синхронной с меню.

В `bot/main.py` подключить роутер (до основного, порядок не критичен — префикс
`theme:` уникален):

```python
        dispatcher.include_router(make_themes_router(conn, settings))
```

Добавить импорт `from llm_tutor.bot.themes import make_themes_router`.

- [ ] **Step 5: Запустить тесты**

Run: `uv run pytest tests/test_themes.py tests/test_handlers.py tests/test_turn.py -q`
Expected: PASS.

- [ ] **Step 6: Коммит**

```bash
git add src/llm_tutor/bot/themes.py src/llm_tutor/bot/handlers.py src/llm_tutor/bot/main.py tests/test_themes.py
git commit -m "Срез 14: навигация «Темы» — выбор узла с предупреждением"
```

---

### Task 15: Сквозная проверка и прогон

**Files:**
- Test: `tests/test_e2e_topic01.py` (дополнить) или ручной прогон

- [ ] **Step 1: Полный прогон тестов с покрытием**

Run: `uv run pytest -q --cov=src --cov-report=term-missing`
Expected: PASS, покрытие новых модулей (`bot/render.py`, `bot/menu.py`,
`bot/themes.py`) — не ниже 80% по ключевым путям.

- [ ] **Step 2: Живой прогон в Telegram**

Проверить вручную (то, что тестами не покрывается):
- ответы тьютора стали короткими;
- табличка `/plan` и «🗺 Маршрут» ровно выравнивается на телефоне и десктопе;
- постоянное меню видно; нажатие каждой кнопки делает ожидаемое;
- «🎚 Темы»: переход в пройденный узел и предупреждение при прыжке вперёд;
- «❓ Не понимаю» углубляет текущий узел, не теряя висящее задание;
- онбординг: представление → анкета → меню.

- [ ] **Step 3: Финальный коммит**

```bash
git add -A
git commit -m "Срез 14: сквозная проверка UX-общения"
```

---

## Self-Review

**1. Покрытие спеки:**
- §3 меню (7 кнопок) → 12.1/12.3 (первые 5: статус, маршрут, задание, пропуск,
  справка) + 14.1 (❓ Не понимаю) + 14.2 (🎚 Темы). Кнопок без обработчика нет
  ни на одном срезе. ✅
- §3.1 «уровень подсказки» в статусе → 12.2. ✅
- §4 действия → 12.2 (status/help), 12.3 (task/skip), 14.1 (stuck), 14.2 (themes). ✅
- §5 лаконичность + HTML → 11.1, 11.2. ✅
- §6 табличка → 13.1. ✅
- §7 «Не понимаю» → 14.1. ✅
- §8 «Темы» → 14.2. ✅
- §9 онбординг → 12.4. ✅
- §3.4 FSM-меню → отклонение зафиксировано в 12.4 (Telegram-ограничение). ⚠️
- §8 «+объяснение» при переходе → подтверждение без LLM-разбора (разбор придёт
  следующим ходом). Отклонение — см. ниже.
- §11 тесты → покрыты по задачам + Task 15. ✅

**2. Плейсхолдеры:** не найдено.

**3. Согласованность типов:** `render.escape`/`render.PARSE_MODE`
используются одинаково во всех задачах; `menu.MENU_LABELS`/`MENU_ACTIONS` —
один источник; `NodeStatus` и `STATUS_ICONS` согласованы; `switch_node`
возвращает HTML-безопасный текст (имя экранировано) — отправляется с
`parse_mode`.

**Отклонения от спеки (нужны ваш ок):**
1. §3.4 (скрытие меню на время анкеты) — технически невозможно совместить с
   инлайн-кнопками вариантов в одном сообщении; меню остаётся видимым, а
   нажатие ловит существующий хинт анкеты.
2. §8 (объяснение нового узла сразу при переходе) — переход даёт
   подтверждение без LLM-разбора; тьютор объяснит следующим ходом.

**Правки по адверсариальному ревью (отдельный агент, 2026-10-07):**
- C1/C3/H1 — импорты сниппетов сделаны самодостаточными: `bot/render.py`
  (Срез 11) больше не импортирует `menu`/`time`; `stuck_reply` не
  импортируется до 14.1; `MODE_LABELS` подключён явно в 11.2.
- C2 — в `render_status` добавлена строка уровня подсказки (по §3.1).
- H2 — порядок `escape` → `fit`; добавлен `render.fit`, не рвущий HTML-сущности.
- H3 — конвенция «сырой текст vs готовый HTML» вынесена в Global Constraints.
- M1 — тест 12.3 стал настоящим падающим (`on_menu` зарегистрирован).
- M2 — срез 12 несёт только работающие кнопки; «Темы»/«Не понимаю» — в 14.x.
- M3 — `themes_keyboard` не заводит сессию на чтении (`get_open_session`).
- M4 — тест окна сравнивает показ с полной длиной маршрута, а не с границей.
- M5 — меню прикрепляется единообразно ко всем текстовым ответам.
- L1/L2 — неиспользуемые импорты (после переноса `render_plan`; `menu` в
  `themes.py`) убираются в соответствующих задачах.
- L3 — формулировка про `parse_mode` смягчена (анкета/диагностика без разметки).
