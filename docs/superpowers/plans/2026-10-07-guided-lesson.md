# Ведомый урок — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Превратить набор команд в ведомый урок: меню из четырёх кнопок,
выходы («не понял», «пропусти», «закрой тему») ловятся из текста, а узел
закрывается проверкой, а не заявлением ученика.

**Architecture:** Намерение распознаётся в `core/intents.py` **до** развилки
«ответ / тьютор» внутри `core/turn.handle_turn` — иначе при висящем задании
фраза «закрой тему» попала бы в журнал как неверный ответ. Проверочный проход
(`core/verify.py`) выдаёт задания по узлу в режиме `verify`, а закрывает узел
существующий критерий `student/guide.is_node_closed`. Меню и онбординг —
слой `bot/`; машинерия ведения (`student/route.py`, `student/planner.py`,
`student/beta.py`) не меняется.

**Tech Stack:** Python 3.12, aiogram 3, pydantic 2, pytest (asyncio_mode=auto),
SQLite.

**Spec:** `docs/superpowers/specs/2026-10-07-guided-lesson-design.md`

## Global Constraints

- Python 3.12; тесты — pytest, асинхронные без декоратора (`asyncio_mode =
  "auto"`). Запуск всего набора: `uv run pytest -q` (на старте работы 400
  тестов).
- Стиль: black/isort/ruff; type hints на всех сигнатурах; докстринги и
  комментарии на русском.
- **Конвенция вывода (не путать):**
  - `core.turn.*` и `core.verify.*` возвращают **сырой** текст — хендлер
    экранирует и обрезает его: `render.fit(render.escape(text))`;
  - `bot.render.*` и `themes.switch_node` возвращают **готовый HTML**
    (динамика экранирована внутри) — шлются как есть с
    `parse_mode=render.PARSE_MODE`, **без** повторного `escape`.
- **Слоистость:** зависимости идут только `bot → core`/`student`/`db`.
  `core/*` не импортирует из `bot/*`. Поэтому модуль намерения —
  `core/intents.py`, а не `bot/intents.py`.
- **Схема БД не меняется.** Новые поля состояния — только в `SessionState`
  (JSON в `sessions.state`), с дефолтами: старые сессии читаются без миграции.
- Метки меню — единый источник истины в `bot/menu.py`; `render._HELP_LINES`
  обязан покрывать ровно действия из `ACTION_LABELS`.
- Коммиты по-русски в стиле проекта: `Срез N: …`. Ветка работы —
  `guided-lesson`.

## Review Focus

Проверки, которые спека подразумевает, но которые легко забыть. Каждая
привязана к задаче, где живёт её тест (в скобках):

1. **Короткая реплика, похожая и на ответ, и на команду** — ученик отвечает
   словом, которое есть в таблице фраз. Ожидание: длинный ответ не
   перехватывается, короткая команда — перехватывается (15.1, 15.2).
2. **Нажатие варианта ответа** — подпись кнопки не должна трактоваться как
   намерение. Ожидание: ветка ответа, свидетельство записано (15.3).
3. **Проверка на узле, где все задания уже отвечены** — ожидание: честный
   отказ, а не молчание и не зацикливание (16.2).
4. **`current_node_id` указывает на узел, убранный из seed** — ожидание: узел
   снимается и бот отвечает, а не падает `KeyError` каждый ход (16.3).
5. **Модель ставит `wants_close_topic` вместе с `student_stuck`** — ожидание:
   приоритет у «застрял», узел не закрывается без свидетельств (16.6).

---

## Срез 15. Слой намерения

### Task 15.1: Модуль распознавания намерения

**Files:**
- Create: `src/llm_tutor/core/intents.py`
- Test: `tests/test_intents.py`

**Interfaces:**
- Consumes: ничего (чистые функции, стандартная библиотека).
- Produces: `Intent = Literal["close_topic", "skip", "stuck"]`;
  `INTENT_MAX_WORDS: int = 6`; `normalize(text: str) -> str`;
  `detect(text: str) -> Intent | None`.

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_intents.py`:

```python
"""Тесты распознавания намерения из текста (Срез 15)."""

import pytest

from llm_tutor.core.intents import INTENT_MAX_WORDS, detect, normalize


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("закрой тему", "close_topic"),
        ("Закрой тему!", "close_topic"),
        ("закрой тему пожалуйста", "close_topic"),
        ("давай закроем", "close_topic"),
        ("тема закрыта", "close_topic"),
        ("я это знаю", "close_topic"),
        ("я это уже знаю", "close_topic"),
        ("Я всё знаю", "close_topic"),
        ("пропусти", "skip"),
        ("Пропусти это задание", "skip"),
        ("давай пропустим", "skip"),
        ("скип", "skip"),
        ("не понял", "stuck"),
        ("Не поняла(", "stuck"),
        ("не понимаю", "stuck"),
        ("ничего не понял", "stuck"),
        ("непонятно", "stuck"),
        ("объясни подробнее", "stuck"),
    ],
)
def test_detects_intent(text: str, expected: str) -> None:
    assert detect(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "2",
        "groupby группирует строки по ключу",
        "спасибо, дальше сам",
    ],
)
def test_ordinary_replies_have_no_intent(text: str) -> None:
    """Обычная короткая реплика — не команда."""
    assert detect(text) is None


def test_long_answer_with_intent_words_is_not_intercepted() -> None:
    """Длинный открытый ответ со словами-триггерами остаётся ответом."""
    text = "я не понял в прошлый раз, но сейчас вижу что groupby группирует строки"
    assert len(text.split()) > INTENT_MAX_WORDS
    assert detect(text) is None


def test_close_wins_over_stuck() -> None:
    """При нескольких совпадениях приоритет: close > skip > stuck."""
    assert detect("закрой тему, я не понял") == "close_topic"


@pytest.mark.parametrize(
    "text",
    [
        "я пропустил эту тему в курсе",
        "я пропустила эту лекцию",
        "мы пропустили этот шаг",
        "пропустив строки получаем ответ",
    ],
)
def test_word_forms_of_phrases_are_not_commands(text: str) -> None:
    """«пропустил» — не команда «пропусти»: фраза ищется целым словом."""
    assert detect(text) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("пропустите", "skip"),
        ("пропустите это задание", "skip"),
        ("пропустим это", "skip"),
        ("скипнем", "skip"),
        ("не поняли", "stuck"),
    ],
)
def test_imperative_forms_are_commands(text: str, expected: str) -> None:
    """Повелительные формы — команды: целое слово их не должно терять."""
    assert detect(text) == expected


def test_normalize_strips_case_punctuation_and_yo() -> None:
    assert normalize("  Закрой ТЕМУ!!!  ") == "закрой тему"
    assert normalize("я всё знаю") == "я все знаю"
    assert normalize("не   понял?") == "не понял"
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `uv run pytest tests/test_intents.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'llm_tutor.core.intents'`.

- [ ] **Step 3: Написать модуль**

Создать `src/llm_tutor/core/intents.py`:

```python
"""Распознавание намерения ученика в короткой реплике.

Пока в состоянии сессии висит задание, любой текст уходит в ветку ответа и
проверяется кодом — «закрой тему» или «не понял» попало бы в журнал как
неверный ответ. Поэтому короткие реплики разбираются на намерение ДО развилки.

Ловим только короткие сообщения: длинный открытый ответ со словом «не понял»
внутри — это ответ, а не просьба о помощи.
"""

import re
from typing import Literal

# Намерение ученика, распознаваемое из текста.
Intent = Literal["close_topic", "skip", "stuck"]

# Реплика длиннее — уже ответ, а не команда: не перехватываем.
INTENT_MAX_WORDS = 6

# Порядок задаёт приоритет при нескольких совпадениях: close > skip > stuck.
INTENT_PHRASES: tuple[tuple[Intent, tuple[str, ...]], ...] = (
    (
        "close_topic",
        (
            "закрой тему",
            "закрыть тему",
            "закрывай тему",
            "закрой узел",
            "закрыть узел",
            "закрывай узел",
            "можно закрыть тему",
            "давай закроем",
            "давай закроем тему",
            "тема закрыта",
            "я это знаю",
            "я это уже знаю",
            "это я знаю",
            "я все знаю",
            "я это все знаю",
        ),
    ),
    (
        "skip",
        (
            "пропусти",
            "пропустить",
            "пропустите",
            "пропустим",
            "пропусти задание",
            "пропусти это",
            "пропускаем",
            "давай пропустим",
            "скип",
            "скипнуть",
            "скипнем",
        ),
    ),
    (
        "stuck",
        (
            "не понял",
            "не поняла",
            "не поняли",
            "не понимаю",
            "непонятно",
            "не понятно",
            "не ясно",
            "неясно",
            "ничего не понял",
            "не догоняю",
            "объясни еще",
            "объясни подробнее",
            "объясни поподробнее",
        ),
    ),
)

# Всё, кроме букв, цифр и пробелов, — разделитель.
_NON_WORD = re.compile(r"[^0-9a-zа-я\s]+")
_WHITESPACE = re.compile(r"\s+")

# Фраза ищется целиком, а не подстрокой: иначе «я пропустил эту тему» ловилось
# бы как команда «пропусти», а «она пропустила» — так же. Фраза при этом
# остаётся вхождением, а не равенством: «закрой тему пожалуйста» ловится.
_INTENT_PATTERNS: tuple[tuple[Intent, tuple[re.Pattern[str], ...]], ...] = tuple(
    (intent, tuple(re.compile(rf"\b{re.escape(phrase)}\b") for phrase in phrases))
    for intent, phrases in INTENT_PHRASES
)


def normalize(text: str) -> str:
    """Реплика к сравнимому виду: регистр, «ё», пунктуация, лишние пробелы."""
    lowered = text.strip().lower().replace("ё", "е")
    return _WHITESPACE.sub(" ", _NON_WORD.sub(" ", lowered)).strip()


def detect(text: str) -> Intent | None:
    """Намерение короткой реплики или ``None``, если это не команда."""
    normalized = normalize(text)
    if not normalized or len(normalized.split()) > INTENT_MAX_WORDS:
        return None
    for intent, patterns in _INTENT_PATTERNS:
        if any(pattern.search(normalized) for pattern in patterns):
            return intent
    return None
```

- [ ] **Step 4: Запустить тесты модуля**

Run: `uv run pytest tests/test_intents.py -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add src/llm_tutor/core/intents.py tests/test_intents.py
git commit -m "Срез 15: распознавание намерения из текста"
```

---

### Task 15.2: Развилка намерения в ходе

**Files:**
- Modify: `src/llm_tutor/core/turn.py`
- Test: `tests/test_turn.py`

**Interfaces:**
- Consumes: `core.intents.detect` (15.1).
- Produces: `handle_turn(..., allow_intents: bool = True) -> TurnReply`;
  приватный `_skip_turn(conn, session_id, state, user_text, *, now) -> str`.
  Публичная `skip_pending(conn, *, now=None, settings=None) -> str` сохраняет
  сигнатуру и поведение (делегирует в `_skip_turn`).

- [ ] **Step 1: Написать падающие тесты**

Добавить в `tests/test_turn.py` (в блок импортов — `SKIP_REPLY` и `STUCK_NOTE`
из `llm_tutor.core.turn`):

```python
async def test_skip_phrase_clears_pending_item_without_evidence(conn, settings) -> None:
    """«пропусти» текстом работает как команда: свидетельство не пишется."""
    load_seed(conn)
    client = _FakeTutor("тьютор не должен вызываться")
    start_practice_reply(conn, now=1.0, settings=settings)

    reply = await handle_turn(conn, client, "m", "пропусти", now=2.0, settings=settings)

    assert reply.text == SKIP_REPLY
    assert _state(conn).pending_item_id is None
    assert repos.get_events(conn) == []
    assert client.calls == []


async def test_stuck_phrase_goes_to_tutor_even_with_pending_item(conn, settings) -> None:
    """«не понял» при висящем задании — просьба о помощи, а не ответ."""
    load_seed(conn)
    ingest_text(conn, "# T\n\n## Grouping\n\ngroupby aggregates rows\n", "u")
    _set_state(conn, pending_item_id=1, current_node_id="pandas_intro")
    client = _FakeTutor("Давай разберём")

    reply = await handle_turn(conn, client, "m", "не понял", now=1.0, settings=settings)

    assert "Давай разберём" in reply.text
    assert STUCK_NOTE in reply.text
    assert _state(conn).mode == "reinforce"
    assert _state(conn).pending_item_id == 1  # задание не тронуто
    assert client.calls  # модель спросили


async def test_intents_can_be_disabled(conn, settings) -> None:
    """Подпись варианта ответа не должна перехватываться как намерение."""
    load_seed(conn)
    _set_state(conn, pending_item_id=1, current_node_id="pandas_intro")

    reply = await handle_turn(
        conn, _FakeTutor(), "m", "пропусти", now=1.0, settings=settings, allow_intents=False
    )

    assert "Не совсем" in reply.text  # ушло в ветку ответа
    assert any(event.source == "checked" for event in repos.get_events(conn))
```

- [ ] **Step 2: Запустить тесты — убедиться, что падают**

Run: `uv run pytest tests/test_turn.py -q -k "intents_can_be_disabled or skip_phrase or stuck_phrase"`
Expected: FAIL — `TypeError: handle_turn() got an unexpected keyword argument
'allow_intents'`; `assert reply.text == SKIP_REPLY` не проходит.

- [ ] **Step 3: Вынести `_skip_turn` и добавить развилку**

В `src/llm_tutor/core/turn.py` добавить импорт:

```python
from llm_tutor.core import intents
```

Рядом с `skip_pending` добавить хелпер и переписать `skip_pending` через него
(поведение команды `/skip` сохраняется полностью, включая реплику-повод
`SKIP_KICKOFF_TEXT` в журнале):

```python
def _skip_turn(
    conn: sqlite3.Connection,
    session_id: int,
    state: SessionState,
    user_text: str,
    *,
    now: float,
) -> str:
    """Снимает ожидание ответа, НЕ записывая свидетельство."""
    if state.pending_item_id is None:
        return NOTHING_TO_SKIP_REPLY
    post_turn(
        conn,
        session_id,
        user_text=user_text,
        assistant_text=SKIP_REPLY,
        state=state.model_copy(
            update={"pending_item_id": None, "phase": "explain", "last_activity": now}
        ),
        now=now,
    )
    return SKIP_REPLY


def skip_pending(
    conn: sqlite3.Connection, *, now: float | None = None, settings: Settings | None = None
) -> str:
    """Явный выход из задания командой `/skip`."""
    stamp = time.time() if now is None else now
    session_id = repos.ensure_open_session(conn, stamp)
    state = repos.get_session_state(conn, session_id)
    return _skip_turn(conn, session_id, state, SKIP_KICKOFF_TEXT, now=stamp)
```

В `handle_turn` добавить параметр:

```python
async def handle_turn(
    conn: sqlite3.Connection,
    client: LLMClient,
    model: str,
    user_text: str,
    *,
    now: float | None = None,
    settings: Settings | None = None,
    allow_intents: bool = True,
) -> TurnReply:
```

Тело — сразу после `graph = CourseGraph.load(conn)`:

```python
    # Короткая реплика-команда разбирается ДО развилки: иначе при висящем
    # задании «пропусти» ушло бы в ветку ответа и записалось как неверный ответ.
    intent = intents.detect(user_text) if allow_intents else None
    if intent == "skip":
        return TurnReply(text=_skip_turn(conn, session_id, state, user_text, now=stamp))
    force_stuck = intent == "stuck"
```

Условие ветки ответа получает `not force_stuck`:

```python
    if (
        state.pending_item_id is not None
        and not force_stuck
        and not _looks_like_question(user_text)
    ):
```

Вызов `_tutor_branch` в ветке `else` получает `force_stuck=force_stuck`, а
напоминание о висящем задании ставится только когда ученик **не** просил
глубины (при `force_stuck` `_tutor_branch` сам добавляет `STUCK_NOTE`):

```python
        reply, events, mastery, new_state = await _tutor_branch(
            conn,
            client,
            model,
            session_id,
            user_text,
            state,
            graph,
            now=stamp,
            settings=s,
            force_stuck=force_stuck,
        )
        if state.pending_item_id is not None and not force_stuck:
            reply = f"{reply}\n\n{PENDING_ITEM_NOTE}"
```

- [ ] **Step 4: Запустить тесты хода и смежные**

Run: `uv run pytest tests/test_turn.py tests/test_guide.py tests/test_e2e_topic01.py -q`
Expected: PASS (поведение `skip_pending` сохранено — старые тесты `/skip` целы).

- [ ] **Step 5: Коммит**

```bash
git add src/llm_tutor/core/turn.py tests/test_turn.py
git commit -m "Срез 15: «пропусти» и «не понял» текстом — до ветки ответа"
```

---

### Task 15.3: Нажатие варианта не перехватывается как намерение

**Files:**
- Modify: `src/llm_tutor/bot/handlers.py`
- Test: `tests/test_bot_flows.py`

**Interfaces:**
- Consumes: `handle_turn(..., allow_intents=...)` (15.2).
- Produces: `handlers._run_turn(..., allow_intents: bool = True)`; хендлер
  `on_answer` вызывает его с `allow_intents=False`.

- [ ] **Step 1: Написать падающий тест**

Добавить в `tests/test_bot_flows.py` — там уже есть `FakeMessage`,
`FakeCallback`, `_named`, `_TutorClient` и `_state`, их и используем:

```python
async def test_answer_callback_disables_intents(conn, settings, monkeypatch) -> None:
    """Нажатие варианта ответа не трактуется как намерение (Срез 15)."""
    load_seed(conn)
    session_id = repos.ensure_open_session(conn, now=1.0)
    state = repos.get_session_state(conn, session_id)
    repos.update_session_state(
        conn,
        session_id,
        state.model_copy(update={"pending_item_id": 1, "current_node_id": "pandas_intro"}),
    )
    seen: dict = {}

    async def spy(*args, **kwargs):
        seen.update(kwargs)
        return TurnReply(text="ок")

    monkeypatch.setattr(handlers, "handle_turn", spy)
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    on_answer = _named(router, "callback_query", "on_answer")

    await on_answer(FakeCallback("answer:0", FakeMessage()))

    assert seen.get("allow_intents") is False
```

В блок импортов `tests/test_bot_flows.py` добавить:

```python
from llm_tutor.bot import handlers
from llm_tutor.core.turn import TurnReply
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `uv run pytest tests/test_bot_flows.py::test_answer_callback_disables_intents -q`
Expected: FAIL — `assert None is False` (аргумент ещё не передаётся).

- [ ] **Step 3: Пробросить флаг в хендлер**

В `src/llm_tutor/bot/handlers.py`:

```python
async def _run_turn(
    conn: sqlite3.Connection,
    client: LLMClient,
    model: str,
    user_text: str,
    *,
    settings: Settings | None,
    allow_intents: bool = True,
) -> TurnReply:
    """Ход с подстраховкой: неожиданный сбой не оставит ученика без ответа."""
    try:
        return await handle_turn(
            conn, client, model, user_text, settings=settings, allow_intents=allow_intents
        )
    except Exception:  # noqa: BLE001 — бот не должен молчать
        logger.exception("Неожиданный сбой хода")
        return TurnReply(text=LLM_FAILURE_REPLY)
```

В хендлере `on_answer` вызов становится:

```python
        reply = await _run_turn(
            conn,
            client,
            model,
            item.options[index],
            settings=settings,
            allow_intents=False,
        )
```

- [ ] **Step 4: Запустить весь набор**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add src/llm_tutor/bot/handlers.py tests/test_bot_flows.py
git commit -m "Срез 15: нажатие варианта ответа не перехватывается как намерение"
```

---

## Срез 16. «Закрыть тему» — проверочный проход

### Task 16.1: Поле состояния и подбор задания прохода

**Files:**
- Modify: `src/llm_tutor/schemas.py`
- Modify: `src/llm_tutor/student/diagnostic.py`
- Test: `tests/test_diagnostic.py`

**Interfaces:**
- Consumes: `_available_items`, `_best_item` (существуют в `diagnostic.py`),
  `beta.estimate`.
- Produces: `SessionState.verify_item_ids: list[int]`;
  `diagnostic.verification_item(conn, node_id, *, used_item_ids=frozenset(),
  now=None, settings=None) -> DiagnosticQuestion | None`.

- [ ] **Step 1: Написать падающие тесты**

Добавить в `tests/test_diagnostic.py` (в импорты — `repos`, `Event`,
`SessionState`, если их там нет):

```python
def test_verification_item_prefers_rubric_item(conn, settings) -> None:
    """Объяснение идёт первым: рубричное задание приоритетнее автопроверки."""
    load_seed(conn)

    question = diagnostic.verification_item(
        conn, "groupby", used_item_ids=frozenset(), now=1.0, settings=settings
    )

    assert question is not None
    assert question.item.answer_type in diagnostic.RUBRIC_CHECKABLE


def test_verification_item_uses_repeat_if_nothing_fresh(conn, settings) -> None:
    """Пауза повтора в проходе не применяется: проверять иначе нечем."""
    load_seed(conn)
    item = repos.get_item(conn, 1)
    repos.add_event(
        conn,
        Event(source="checked", result=1.0, item_id=item.id, concept_id="pandas_intro", ts=1.0),
    )

    question = diagnostic.verification_item(
        conn, "pandas_intro", used_item_ids=frozenset(), now=1.0, settings=settings
    )

    assert question is not None
    assert question.item.id == item.id


def test_verification_item_skips_already_used(conn, settings) -> None:
    """В одном проходе одно задание не выдаётся дважды."""
    load_seed(conn)

    question = diagnostic.verification_item(
        conn, "pandas_intro", used_item_ids=frozenset({1}), now=1.0, settings=settings
    )

    assert question is None


def test_verification_item_without_items_for_node(conn, settings) -> None:
    """У узла без заданий подбирать нечего."""
    load_seed(conn)

    assert (
        diagnostic.verification_item(
            conn, "visualization_basics", used_item_ids=frozenset(), now=1.0, settings=settings
        )
        is None
    )


def test_verify_item_ids_defaults_to_empty() -> None:
    """Старая сессия без поля читается: дефолт пустой."""
    assert SessionState().verify_item_ids == []
```

- [ ] **Step 2: Запустить тесты — убедиться, что падают**

Run: `uv run pytest tests/test_diagnostic.py -q -k "verification_item or verify_item_ids"`
Expected: FAIL — `AttributeError: module 'llm_tutor.student.diagnostic' has no
attribute 'verification_item'`.

- [ ] **Step 3: Добавить поле и функцию**

В `src/llm_tutor/schemas.py` в `SessionState` после `task_hinted`:

```python
    # Задания, уже выданные в текущем проверочном проходе: одно и то же
    # задание в одном проходе не спрашивается дважды (защита от накрутки).
    verify_item_ids: list[int] = Field(default_factory=list)
```

В `src/llm_tutor/student/diagnostic.py` рядом с `question_for_node`:

```python
def verification_item(
    conn: sqlite3.Connection,
    node_id: str,
    *,
    used_item_ids: frozenset[int] = frozenset(),
    now: float | None = None,
    settings: Settings | None = None,
) -> DiagnosticQuestion | None:
    """Задание для проверочного прохода: объяснение приоритетнее автопроверки.

    Пауза ``item_repeat_cooldown_days`` здесь НЕ применяется: ученик явно
    просит проверить, а банк по большинству узлов содержит одно задание — с
    паузой проверять было бы нечем. От накрутки защищает ``used_item_ids``:
    внутри одного прохода задание не выдаётся дважды.
    """
    s = settings or get_settings()
    stamp = time.time() if now is None else now

    items = [
        item
        for item in _available_items(conn, include_rubric=True)
        if item.id not in used_item_ids and node_id in item.concept_weights
    ]
    if not items:
        return None

    target = beta.estimate(conn, node_id, now=stamp, settings=s).mean
    # Объяснение первым: оно несёт больше сигнала, чем выбор варианта.
    rubric_items = [item for item in items if item.answer_type in RUBRIC_CHECKABLE]
    candidate = _best_item(rubric_items or items, node_id, target_difficulty=target)
    if candidate is None:
        return None
    return DiagnosticQuestion(item=candidate, concept_id=node_id)
```

- [ ] **Step 4: Запустить тесты диагностики**

Run: `uv run pytest tests/test_diagnostic.py -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add src/llm_tutor/schemas.py src/llm_tutor/student/diagnostic.py tests/test_diagnostic.py
git commit -m "Срез 16: подбор задания проверочного прохода"
```

---

### Task 16.2: Выдача заданий прохода в `_issue_task`

**Files:**
- Modify: `src/llm_tutor/core/turn.py`
- Modify: `src/llm_tutor/bot/themes.py`
- Modify: `src/llm_tutor/student/guide.py`
- Test: `tests/test_turn.py`, `tests/test_themes.py`, `tests/test_guide.py`

**Interfaces:**
- Consumes: `diagnostic.verification_item`, `SessionState.verify_item_ids`
  (16.1).
- Produces: при `state.mode == "verify"` функция `_issue_task` берёт задание у
  `diagnostic.verification_item`, помечает выдачу шапкой и пополняет
  `verify_item_ids`. Новые константы `core/turn.py`: `VERIFY_STEP_TEMPLATE`,
  `VERIFY_EXPLAIN_PREFIX`, `VERIFY_EXHAUSTED_REPLY`, `VERIFY_NO_ITEMS_REPLY`.

- [ ] **Step 1: Написать падающие тесты**

Добавить в `tests/test_turn.py` (в импорты — `VERIFY_EXHAUSTED_REPLY`,
`VERIFY_EXPLAIN_PREFIX`):

```python
async def test_verify_mode_asks_explanation_first(conn, settings) -> None:
    """В режиме прохода шапка и «объясни своими словами» ставятся кодом."""
    load_seed(conn)
    _set_state(conn, current_node_id="groupby", mode="verify", phase="practice")

    reply = start_practice_reply(conn, now=1.0, settings=settings)

    assert "Проверка" in reply.text
    assert "шаг 1" in reply.text
    assert VERIFY_EXPLAIN_PREFIX in reply.text
    assert _state(conn).verify_item_ids == [_state(conn).pending_item_id]


async def test_verify_mode_does_not_repeat_item_in_one_pass(conn, settings) -> None:
    """Второй шаг прохода берёт другое задание, а не то же самое."""
    load_seed(conn)
    _set_state(
        conn, current_node_id="groupby", mode="verify", phase="practice", verify_item_ids=[9]
    )

    reply = start_practice_reply(conn, now=1.0, settings=settings)

    assert _state(conn).pending_item_id != 9
    assert "шаг 2" in reply.text


async def test_verify_mode_reports_exhausted_pass(conn, settings) -> None:
    """Задания узла кончились — честный отказ, режим снят."""
    load_seed(conn)
    _set_state(
        conn,
        current_node_id="pandas_intro",
        mode="verify",
        phase="practice",
        verify_item_ids=[1],
    )

    reply = start_practice_reply(conn, now=1.0, settings=settings)

    assert reply.text == VERIFY_EXHAUSTED_REPLY
    assert _state(conn).mode is None
    assert _state(conn).verify_item_ids == []


async def test_normal_task_is_not_marked_as_verification(conn, settings) -> None:
    """Обычная выдача задания не несёт шапки прохода."""
    load_seed(conn)

    reply = start_practice_reply(conn, now=1.0, settings=settings)

    assert "Проверка" not in reply.text
    assert _state(conn).verify_item_ids == []
```

Плюс два теста в свои файлы — спека §5.2 требует чистить список выданного
ещё при переходе на другую тему и при усиленном проходе.

В `tests/test_themes.py` (там уже есть `switch_node`, `load_seed`, `repos`):

```python
def test_switching_theme_clears_verification_pass(conn, settings) -> None:
    """Переход на другую тему снимает проход (спека §5.2)."""
    load_seed(conn)
    session_id = repos.ensure_open_session(conn, now=1.0)
    state = repos.get_session_state(conn, session_id)
    repos.update_session_state(
        conn,
        session_id,
        state.model_copy(
            update={"current_node_id": "groupby", "mode": "verify", "verify_item_ids": [9]}
        ),
    )

    switch_node(conn, "read_csv", now=2.0, settings=settings)

    updated = repos.get_session_state(conn, session_id)
    assert updated.verify_item_ids == []
    assert updated.mode is None
```

В `tests/test_guide.py` (нужны `guide` и `SessionState` в импортах):

```python
def test_stuck_clears_verification_pass() -> None:
    """Просьба о помощи снимает проход: помощь — не чистое свидетельство."""
    state = SessionState(current_node_id="groupby", mode="verify", verify_item_ids=[9])

    assert guide.on_student_stuck(state).verify_item_ids == []
```

- [ ] **Step 2: Запустить тесты — убедиться, что падают**

Run: `uv run pytest tests/test_turn.py -q -k "verify_mode or is_not_marked"`
Expected: FAIL — `ImportError: cannot import name 'VERIFY_EXHAUSTED_REPLY'`.

- [ ] **Step 3: Доработать `_issue_task`**

В `src/llm_tutor/core/turn.py` рядом с прочими текстами:

```python
# Шапка шага проверочного прохода. Номер без общего числа: после неудачного
# ответа серия обнуляется и шагов может стать больше двух.
VERIFY_STEP_TEMPLATE = "🔎 Проверка «{name}» — шаг {step}"
# Рубричные задания просим объяснить словами — это и есть суть проверки.
VERIFY_EXPLAIN_PREFIX = "Объясни своими словами:"
# Проход исчерпал задания узла, а серия ещё не набрана.
VERIFY_EXHAUSTED_REPLY = (
    "Проверить нечем: по этой теме мало заданий, и мы их уже прошли. "
    "Закрою её, когда владение подтвердится, — или идём дальше кнопкой ▶️."
)
# У узла нет ни одного задания.
VERIFY_NO_ITEMS_REPLY = (
    "По этой теме у меня нет проверочных заданий — "
    "закрою её, когда владение подтвердится по ходу."
)
```

В `_issue_task` заменить выбор задания:

```python
    if state.mode == "verify":
        # Проход: задание берём без паузы повтора, но не повторяем внутри прохода.
        question = diagnostic.verification_item(
            conn,
            node_id,
            used_item_ids=frozenset(state.verify_item_ids),
            now=now,
            settings=settings,
        )
    else:
        question = diagnostic.question_for_node(
            conn, node_id, asked_item_ids=exclude_item_ids, now=now, settings=settings
        )
```

Блок «задания нет» разводится на два случая:

```python
    if question is None:
        if state.mode == "verify":
            # Проход не может продолжаться: снимаем режим, иначе ученик залип
            # в «проверяю» на каждом следующем задании.
            text = VERIFY_EXHAUSTED_REPLY if state.verify_item_ids else VERIFY_NO_ITEMS_REPLY
            return (
                state.model_copy(
                    update={
                        "mode": None,
                        "verify_item_ids": [],
                        "phase": "explain",
                        "route": route,
                        "last_activity": now,
                    }
                ),
                text,
                None,
            )
        return (
            state.model_copy(update={"phase": "explain", "route": route, "last_activity": now}),
            NO_TASK_FOR_NODE_REPLY,
            None,
        )
```

Новый `new_state` пополняет список выданных, а текст получает шапку:

```python
    new_state = state.model_copy(
        update={
            "pending_item_id": question.item.id,
            "current_node_id": question.concept_id,
            "hint_level": 0,
            "task_hinted": False,
            "phase": "practice",
            "route": route,
            "last_activity": now,
            "verify_item_ids": (
                [*state.verify_item_ids, question.item.id]
                if state.mode == "verify"
                else state.verify_item_ids
            ),
        }
    )
    text = _render_item(question.item)
    if state.mode == "verify":
        header = VERIFY_STEP_TEMPLATE.format(
            name=graph.concept(node_id).name, step=len(state.verify_item_ids) + 1
        )
        if question.item.answer_type in diagnostic.RUBRIC_CHECKABLE:
            text = f"{header}\n\n{VERIFY_EXPLAIN_PREFIX}\n{text}"
        else:
            text = f"{header}\n\n{text}"
    return new_state, text, question.item.options or None
```

И две чистки состояния прохода — там, где узел меняет режим:

В `src/llm_tutor/bot/themes.py`, в `switch_node`, в `new_state.model_copy(update={...})`
добавить `"verify_item_ids": [],` — переход на другую тему снимает проход.

В `src/llm_tutor/student/guide.py`, в `on_student_stuck`, в
`state.model_copy(update={...})` добавить `"verify_item_ids": [],` — помощь по
узлу чистым свидетельством не является, проход обрывается.

- [ ] **Step 4: Запустить тесты хода, тем и ведения**

Run: `uv run pytest tests/test_turn.py tests/test_themes.py tests/test_guide.py -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add src/llm_tutor/core/turn.py src/llm_tutor/bot/themes.py src/llm_tutor/student/guide.py tests/test_turn.py tests/test_themes.py tests/test_guide.py
git commit -m "Срез 16: режим прохода — шапка шага, запрет повтора и снятие прохода"
```

---

### Task 16.3: Вход в проход и честные отказы

**Files:**
- Create: `src/llm_tutor/core/verify.py`
- Test: `tests/test_verify.py`

**Interfaces:**
- Consumes: `core.turn._issue_task`, `core.turn.post_turn`,
  `core.turn.VERIFY_NO_ITEMS_REPLY` (16.2), `CourseGraph.load`.
- Produces: `verify.start_verification(conn, *, now=None, settings=None) ->
  TurnReply`; константы `VERIFY_KICKOFF_TEXT`, `VERIFY_NO_NODE_REPLY`.

- [ ] **Step 1: Написать падающие тесты**

Создать `tests/test_verify.py`:

```python
"""Тесты проверочного прохода по узлу (Срез 16)."""

from llm_tutor.core import verify
from llm_tutor.core.turn import VERIFY_NO_ITEMS_REPLY
from llm_tutor.core.verify import VERIFY_NO_NODE_REPLY
from llm_tutor.course.seed import load_seed
from llm_tutor.db import repos


def _state(conn):
    session_id = repos.ensure_open_session(conn, now=1.0)
    return repos.get_session_state(conn, session_id)


def _set_state(conn, **patch) -> None:
    session_id = repos.ensure_open_session(conn, now=1.0)
    state = repos.get_session_state(conn, session_id)
    repos.update_session_state(conn, session_id, state.model_copy(update=patch))


def test_start_verification_marks_node_and_issues_task(conn, settings) -> None:
    """Проход помечает узел режимом verify и выдаёт задание с шапкой."""
    load_seed(conn)
    _set_state(conn, current_node_id="groupby")

    reply = verify.start_verification(conn, now=1.0, settings=settings)

    assert "Проверка" in reply.text
    assert _state(conn).mode == "verify"
    assert _state(conn).pending_item_id is not None
    assert _state(conn).node_streak == 0


def test_start_verification_without_node_answers_honestly(conn, settings) -> None:
    """Закрывать нечего — говорим об этом, состояние не трогаем."""
    load_seed(conn)

    reply = verify.start_verification(conn, now=1.0, settings=settings)

    assert reply.text == VERIFY_NO_NODE_REPLY
    assert _state(conn).mode is None


def test_start_verification_with_removed_node_answers_honestly(conn, settings) -> None:
    """Узел убрали из seed — не падаем KeyError, снимаем узел."""
    load_seed(conn)
    _set_state(conn, current_node_id="нет_такого_узла")

    reply = verify.start_verification(conn, now=1.0, settings=settings)

    assert reply.text == VERIFY_NO_NODE_REPLY
    assert _state(conn).current_node_id is None


def test_start_verification_without_items_answers_honestly(conn, settings) -> None:
    """У узла нет заданий — честный отказ, а не имитация проверки."""
    load_seed(conn)
    _set_state(conn, current_node_id="visualization_basics")

    reply = verify.start_verification(conn, now=1.0, settings=settings)

    assert reply.text == VERIFY_NO_ITEMS_REPLY
    assert _state(conn).mode is None


def test_restarting_pass_drops_pending_item_without_evidence(conn, settings) -> None:
    """Повторное «закрой тему» начинает проход заново, свидетельств не пишет."""
    load_seed(conn)
    _set_state(conn, current_node_id="groupby")
    verify.start_verification(conn, now=1.0, settings=settings)
    first = _state(conn).pending_item_id

    verify.start_verification(conn, now=2.0, settings=settings)

    assert _state(conn).pending_item_id != first
    assert _state(conn).verify_item_ids == [_state(conn).pending_item_id]
    assert repos.get_events(conn) == []
```

- [ ] **Step 2: Запустить тесты — убедиться, что падают**

Run: `uv run pytest tests/test_verify.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'llm_tutor.core.verify'`.

- [ ] **Step 3: Написать модуль**

Создать `src/llm_tutor/core/verify.py`:

```python
"""Проверочный проход по узлу: «закрой тему» с доказательствами.

Ученик утверждает, что тему знает; бот это проверяет — задание за заданием,
пока не сработает обычный критерий закрытия (``student/guide.is_node_closed``).
Закрывает узел код, а не заявление ученика и не вердикт грейдера (§9.2).
"""

import logging
import sqlite3
import time

from llm_tutor.config import Settings, get_settings
from llm_tutor.core.turn import TurnReply, _issue_task, post_turn
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.llm.prompts import EMPTY_GRAPH_REPLY

logger = logging.getLogger(__name__)

# Повод прохода: в журнале он виден как просьба ученика, а не как молчание.
VERIFY_KICKOFF_TEXT = "Закрой тему — я её уже знаю."
VERIFY_NO_NODE_REPLY = "Сейчас нечего закрывать — выбери тему: 🎚 Темы"


def start_verification(
    conn: sqlite3.Connection,
    *,
    now: float | None = None,
    settings: Settings | None = None,
) -> TurnReply:
    """Вход в проверочный проход по текущему узлу."""
    s = settings or get_settings()
    stamp = time.time() if now is None else now
    session_id = repos.ensure_open_session(conn, stamp)
    state = repos.get_session_state(conn, session_id)
    graph = CourseGraph.load(conn)

    if not graph.node_ids:
        return TurnReply(text=EMPTY_GRAPH_REPLY)

    node_id = state.current_node_id
    if node_id is None:
        return TurnReply(text=VERIFY_NO_NODE_REPLY)
    if not graph.has_node(node_id):
        # Узел убрали из графа (правка seed или БД руками) — иначе занятие
        # запиралось бы KeyError на каждом ходу.
        logger.warning("Узел %s больше не в графе, снимаю", node_id)
        post_turn(
            conn,
            session_id,
            user_text=VERIFY_KICKOFF_TEXT,
            assistant_text=VERIFY_NO_NODE_REPLY,
            state=state.model_copy(
                update={"current_node_id": None, "node_streak": 0, "last_activity": stamp}
            ),
            now=stamp,
        )
        return TurnReply(text=VERIFY_NO_NODE_REPLY)

    # Проход начинается заново: узел в режим «проверить и идти дальше», серия
    # обнулена — закрытие должно опираться на свидетельства ПРОХОДА.
    started = state.model_copy(
        update={
            "mode": "verify",
            "node_streak": 0,
            "hint_level": 0,
            "task_hinted": False,
            "pending_item_id": None,
            "phase": "explain",
            "verify_item_ids": [],
            "last_activity": stamp,
        }
    )
    new_state, text, options = _issue_task(conn, graph, started, now=stamp, settings=s)
    post_turn(
        conn,
        session_id,
        user_text=VERIFY_KICKOFF_TEXT,
        assistant_text=text,
        state=new_state,
        now=stamp,
    )
    return TurnReply(text=text, options=options)
```

- [ ] **Step 4: Запустить тесты прохода**

Run: `uv run pytest tests/test_verify.py -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add src/llm_tutor/core/verify.py tests/test_verify.py
git commit -m "Срез 16: вход в проверочный проход и честные отказы"
```

---

### Task 16.4: Исходы прохода — закрытие узла и обрыв на провале

**Files:**
- Create: `tests/fakes.py`
- Modify: `src/llm_tutor/core/turn.py`
- Modify: `src/llm_tutor/student/guide.py`
- Test: `tests/test_verify.py`

**Interfaces:**
- Consumes: `state.mode == "verify"` (16.3).
- Produces: `guide.on_verification_failed(state) -> SessionState`;
  `core.turn.VERIFY_FAILED_NOTE`; закрытие узла чистит `verify_item_ids`;
  `tests/fakes.py:GradingTutor` — общая тестовая заглушка (её же возьмёт
  сквозной тест 18.6).

- [ ] **Step 1: Написать падающие тесты**

Сначала создать общий модуль `tests/fakes.py` — заглушка понадобится и здесь,
и в сквозном тесте 18.6, а импортировать из соседнего тест-модуля не стоит:
это ломается при смене `--import-mode`.

```python
"""Общие подставные объекты для тестов."""

from llm_tutor.db import repos
from llm_tutor.grader.rubric import CriterionVerdict, RubricVerdict


class GradingTutor:
    """Отвечает и как тьютор, и как грейдер: вердикт задаётся параметром."""

    def __init__(self, conn, *, passed: bool) -> None:
        self.conn = conn
        self.passed = passed

    def _verdict(self) -> RubricVerdict:
        criteria = [
            CriterionVerdict(
                id=criterion.id,
                passed=self.passed,
                quote="groupby" if self.passed else None,
            )
            for rubric in repos.get_rubrics(self.conn)
            for criterion in repos.get_criteria(self.conn, rubric.id)
        ]
        return RubricVerdict(criteria=criteria)

    async def chat_structured(self, messages, schema, *, model=None, temperature=None):
        if schema is RubricVerdict:
            return self._verdict()
        return schema(reply="Разбираем.", hint_level=0)

    async def chat(self, messages, *, model=None, temperature=None) -> str:
        return "Разбираем."
```

Дальше — тесты в `tests/test_verify.py`. Узел берём `groupby`: это
**единственный** узел с более чем одним заданием в банке (id 4, 9, 10),
поэтому серия из двух чистых ответов достижима только на нём; задания 9 и 10
рубричные, отсюда грейдер в одной роли с тьютором. Импорты файла:
`from fakes import GradingTutor`, `handle_turn` и `VERIFY_FAILED_NOTE` из
`llm_tutor.core.turn`.

```python
async def test_pass_closes_node_after_two_clean_answers(conn, settings) -> None:
    """Проход закрывает узел обычным критерием — серией чистых ответов."""
    load_seed(conn)
    _set_state(conn, current_node_id="groupby")
    verify.start_verification(conn, now=1.0, settings=settings)
    client = GradingTutor(conn, passed=True)

    for ts in (2.0, 3.0):
        reply = await handle_turn(
            conn, client, "m", "groupby группирует строки по ключу", now=ts, settings=settings
        )

    assert "закрыт" in reply.text
    assert _state(conn).verify_item_ids == []
    assert _state(conn).mode is None


async def test_wrong_answer_drops_the_pass(conn, settings) -> None:
    """Неверный ответ обрывает проход — узел уходит в усиленный проход."""
    load_seed(conn)
    _set_state(conn, current_node_id="groupby")
    verify.start_verification(conn, now=1.0, settings=settings)
    client = GradingTutor(conn, passed=False)

    reply = await handle_turn(conn, client, "m", "не знаю", now=2.0, settings=settings)

    assert VERIFY_FAILED_NOTE in reply.text
    assert _state(conn).mode == "reinforce"
    assert _state(conn).verify_item_ids == []
    assert "Проверка" not in reply.text  # проход оборван, шагов больше нет
```

- [ ] **Step 2: Запустить тесты — убедиться, что падают**

Run: `uv run pytest tests/test_verify.py -q -k "closes_node or drops_the_pass"`
Expected: FAIL — `ImportError: cannot import name 'VERIFY_FAILED_NOTE'`.

- [ ] **Step 3: Реализовать исходы**

В `src/llm_tutor/student/guide.py` рядом с `on_student_stuck`:

```python
def on_verification_failed(state: SessionState) -> SessionState:
    """Проход не подтвердился: узел в усиленный проход, проход снят.

    Ученик заявил, что знает тему, но не подтвердил — значит тему надо
    разобрать, а не продолжать допрашивать.
    """
    return state.model_copy(
        update={
            "mode": "reinforce",
            "node_streak": 0,
            "verify_item_ids": [],
            "phase": "explain",
        }
    )
```

В `src/llm_tutor/core/turn.py` рядом с текстами прохода:

```python
# Проход не подтвердился — говорим честно и возвращаемся к разбору.
VERIFY_FAILED_NOTE = "Пока не подтвердилось — вернёмся к теме и разберёмся."
```

В `_close_node_if_ready` в `new_state.model_copy(update={...})` добавить
`"verify_item_ids": [],` — закрытый узел не тащит за собой проход.

В `handle_turn` в ветке ответа, между блоком `if passed:` и вызовом
`_issue_task`:

```python
        # Проход не подтвердился: обрываем его, узел уходит в разбор.
        if state.mode == "verify" and not passed:
            new_state = guide.on_verification_failed(new_state)
            reply = f"{reply}\n\n{VERIFY_FAILED_NOTE}"
```

- [ ] **Step 4: Запустить тесты прохода, хода и ведения**

Run: `uv run pytest tests/test_verify.py tests/test_turn.py tests/test_guide.py -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add src/llm_tutor/core/turn.py src/llm_tutor/student/guide.py tests/test_verify.py
git commit -m "Срез 16: исходы прохода — закрытие узла и обрыв на провале"
```

---

### Task 16.5: Кнопка «Закрыть тему» и команда `/close`

**Files:**
- Modify: `src/llm_tutor/bot/menu.py`
- Modify: `src/llm_tutor/bot/handlers.py`
- Modify: `src/llm_tutor/bot/render.py` (запись `close` в `_HELP_LINES`)
- Test: `tests/test_bot_flows.py`

**Interfaces:**
- Consumes: `core.verify.start_verification` (16.3).
- Produces: действие `close` в `menu.Action`/`ACTION_LABELS` (колбэк
  `menu:close`), команда `/close`.

- [ ] **Step 1: Написать падающий тест**

Добавить в `tests/test_bot_flows.py`:

```python
async def test_menu_close_starts_verification(conn, settings) -> None:
    """Нажатие «Закрыть тему» запускает проверочный проход."""
    load_seed(conn)
    session_id = repos.ensure_open_session(conn, now=1.0)
    state = repos.get_session_state(conn, session_id)
    repos.update_session_state(
        conn, session_id, state.model_copy(update={"current_node_id": "groupby"})
    )
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    on_menu_action = _named(router, "callback_query", "on_menu_action")
    message = FakeMessage()

    await on_menu_action(FakeCallback("menu:close", message))

    assert "Проверка" in message.last_text
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `uv run pytest tests/test_bot_flows.py::test_menu_close_starts_verification -q`
Expected: FAIL — `message.sent` пуст: действия `close` в меню нет.

- [ ] **Step 3: Добавить действие и команду**

В `src/llm_tutor/bot/menu.py` расширить литерал и таблицу (срез 17 ужмёт
набор до четырёх — `close` в него входит):

```python
Action = Literal["status", "route", "themes", "close", "task", "stuck", "skip", "help"]
```

```python
ACTION_LABELS: dict[Action, str] = {
    "status": "📚 Моё обучение",
    "route": "🗺 Маршрут",
    "themes": "🎚 Темы",
    "close": "✅ Закрыть тему",
    "task": "🎯 Задание",
    "stuck": "❓ Не понимаю",
    "skip": "⏭ Пропустить",
    "help": "ℹ️ Что умею",
}
```

Также в `src/llm_tutor/bot/render.py` добавить пояснение для справки, иначе
`render_help` упадёт `KeyError` на новом действии:

```python
    "close": "проверить тему и закрыть её, если знания подтвердятся",
```

В `src/llm_tutor/bot/handlers.py` добавить импорт
`from llm_tutor.core import verify`, ветку действия и команду:

```python
            elif action == "close":
                reply = verify.start_verification(conn, settings=settings)
                await callback.message.answer(
                    render.fit(render.escape(reply.text)),
                    parse_mode=render.PARSE_MODE,
                    reply_markup=_options_keyboard(reply.options),
                )
```

```python
    @router.message(Command("close"))
    async def on_close(message: Message) -> None:
        try:
            reply = verify.start_verification(conn, settings=settings)
        except Exception:  # noqa: BLE001 — команда не должна отвечать молчанием
            logger.exception("Сбой проверочного прохода")
            await message.answer(
                render.fit(render.escape(BOT_FAILURE_REPLY)), parse_mode=render.PARSE_MODE
            )
            return
        await message.answer(
            render.fit(render.escape(reply.text)),
            reply_markup=_options_keyboard(reply.options),
            parse_mode=render.PARSE_MODE,
        )
```

- [ ] **Step 4: Запустить тесты потоков, меню и представления**

Run: `uv run pytest tests/test_bot_flows.py tests/test_menu.py tests/test_render.py -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add src/llm_tutor/bot/menu.py src/llm_tutor/bot/handlers.py src/llm_tutor/bot/render.py tests/test_bot_flows.py
git commit -m "Срез 16: кнопка «Закрыть тему» и команда /close"
```

---

### Task 16.6: «Закрой тему» текстом и флаг модели

**Files:**
- Modify: `src/llm_tutor/llm/schemas.py`
- Modify: `src/llm_tutor/llm/prompts.py`
- Modify: `src/llm_tutor/core/turn.py`
- Test: `tests/test_turn.py`, `tests/test_prompts.py`

**Interfaces:**
- Consumes: `intents.detect` (15.1), `verify.start_verification` (16.3).
- Produces: `TutorReply.wants_close_topic: bool = False`; `_tutor_branch`
  возвращает **пять** значений (пятое — флаг просьбы закрыть тему).

- [ ] **Step 1: Написать падающие тесты**

В `tests/test_turn.py` расширить `_FakeTutor`:

```python
    def __init__(
        self,
        reply: str = "Ответ тьютора",
        hint_level: int = 0,
        *,
        student_stuck: bool = False,
        wants_close_topic: bool = False,
    ) -> None:
        self.reply = reply
        self.hint_level = hint_level
        self.student_stuck = student_stuck
        self.wants_close_topic = wants_close_topic
        self.calls: list[list] = []

    async def chat_structured(self, messages, schema, *, model=None, temperature=None):
        self.calls.append(list(messages))
        return schema(
            reply=self.reply,
            hint_level=self.hint_level,
            student_stuck=self.student_stuck,
            wants_close_topic=self.wants_close_topic,
        )
```

Добавить тесты:

```python
async def test_close_phrase_starts_verification_with_pending_item(conn, settings) -> None:
    """«закрой тему» при висящем задании идёт в проверку, а не в ответ."""
    load_seed(conn)
    _set_state(conn, current_node_id="groupby", pending_item_id=1)

    reply = await handle_turn(conn, _FakeTutor(), "m", "закрой тему", now=1.0, settings=settings)

    assert "Проверка" in reply.text
    assert _state(conn).mode == "verify"
    assert not any(event.source == "checked" for event in repos.get_events(conn))


async def test_model_flag_starts_verification(conn, settings) -> None:
    """Формулировку вне таблицы фраз ловит модель."""
    load_seed(conn)
    ingest_text(conn, "# T\n\n## Grouping\n\ngroupby aggregates rows\n", "u")
    _set_state(conn, current_node_id="groupby")
    client = _FakeTutor("Держишь тему уверенно", wants_close_topic=True)

    reply = await handle_turn(
        conn, client, "m", "давай закроем, я тут всё знаю уже", now=1.0, settings=settings
    )

    assert _state(conn).mode == "verify"
    assert "Проверка" in reply.text


async def test_close_flag_starts_pass_from_clean_state(conn, settings) -> None:
    """Проход начинается с нуля, а не с подсказок тьюторского хода."""
    load_seed(conn)
    ingest_text(conn, "# T\n\n## Grouping\n\ngroupby aggregates rows\n", "u")
    _set_state(conn, current_node_id="groupby", hint_level=3, node_streak=1)
    client = _FakeTutor("Держишь тему уверенно", wants_close_topic=True)

    await handle_turn(
        conn, client, "m", "давай закроем, я тут всё знаю уже", now=1.0, settings=settings
    )

    state = _state(conn)
    assert state.mode == "verify"
    assert state.hint_level == 0  # лестница подсказок сброшена — задумано
    assert state.node_streak == 0  # серия считается проходом, а не прежняя


async def test_stuck_flag_wins_over_close_flag(conn, settings) -> None:
    """«Застрял» приоритетнее: сначала разбираемся, потом проверяем."""
    load_seed(conn)
    ingest_text(conn, "# T\n\n## Grouping\n\ngroupby aggregates rows\n", "u")
    _set_state(conn, current_node_id="groupby")
    client = _FakeTutor("Разберём", student_stuck=True, wants_close_topic=True)

    await handle_turn(
        conn, client, "m", "закрой тему но я совсем запутался тут", now=1.0, settings=settings
    )

    assert _state(conn).mode == "reinforce"
```

В `tests/test_prompts.py`:

```python
def test_tutor_prompt_mentions_close_topic_flag() -> None:
    """Промпт знает про флаг просьбы закрыть тему."""
    assert "wants_close_topic" in tutor_system_prompt(0, material="found")
```

- [ ] **Step 2: Запустить тесты — убедиться, что падают**

Run: `uv run pytest tests/test_turn.py tests/test_prompts.py -q -k close`
Expected: FAIL — `TypeError: TutorReply() got an unexpected keyword argument
'wants_close_topic'`.

- [ ] **Step 3: Добавить флаг, правило промпта и ветки хода**

В `src/llm_tutor/llm/schemas.py` в `TutorReply`:

```python
    # Модель заметила просьбу закрыть текущую тему: код входит в проверочный
    # проход. Само заявление узел не закрывает — нужны свидетельства.
    wants_close_topic: bool = False
```

В `src/llm_tutor/llm/prompts.py` в `_GUIDE_RULES` — там, где живёт правило про
`student_stuck` (в `_TUTOR_TURN_RULES` его нет):

```python
    "- Если ученик просит закрыть текущую тему или утверждает, что уже её\n"
    "  знает, поставь wants_close_topic = true: тему проверит отдельный проход.\n"
```

И в `_TUTOR_TURN_CONTRACT` — иначе модель не узнает про новое поле
(там перечислен JSON-контракт ответа; тест `test_prompts.py` проверяет, что
`"student_stuck"` в промпте есть):

```python
    '{"reply": "<ответ ученику>", "hint_level": <число 0..%d>, '
    '"student_stuck": <true|false>, "wants_close_topic": <true|false>}\n'
    "hint_level — уровень, на котором ты ведёшь объяснение прямо сейчас; "
    "student_stuck — заметил ли ты, что ученик просит глубины; "
    "wants_close_topic — просит ли ученик закрыть текущую тему."
) % MAX_HINT_LEVEL

В `src/llm_tutor/core/turn.py` — импорт `intents` на уровне модуля (он от
`turn` не зависит) и ветка в `handle_turn` рядом с веткой skip:

```python
from llm_tutor.core import intents
```

```python
    if intent == "close_topic":
        # Локальный импорт: turn и verify ссылаются друг на друга, а на уровне
        # модуля это цикл — verify не найдёт TurnReply в ещё не дочитанном turn.
        from llm_tutor.core import verify

        # Закрытие — отдельный проход; он сам запишет ход и состояние.
        return verify.start_verification(conn, now=stamp, settings=s)
```

**Почему именно локальный импорт, а не верхнеуровневый.** `core/verify.py`
импортирует из `core/turn.py` (`TurnReply`, `_issue_task`, `post_turn`), и
`TurnReply` определён уже ПОСЛЕ блока импортов `turn.py`. Если импортировать
`verify` в шапке `turn.py`, то при загрузке `turn` первым (а так и будет:
`bot/handlers.py` и `tests/test_turn.py` тянут именно его) `verify` попытается
взять из недочитанного модуля ещё не созданный `TurnReply` и упадёт
`ImportError: cannot import name 'TurnReply' from partially initialized module`.
Импорт внутри функции разрывает цикл: к моменту вызова оба модуля загружены.

Тот же локальный импорт нужен в ветке `wants_close` (шаг ниже) — она в этой же
функции, так что импорт в начале `handle_turn` покрывает оба случая.

В `_tutor_branch` флаг возвращается пятым элементом (приоритет у «застрял»):

```python
    if answer.student_stuck or force_stuck:
        stuck_state = guide.on_student_stuck(new_state).model_copy(
            update={"task_hinted": True}
        )
        return f"{answer.reply}\n\n{STUCK_NOTE}", [], [], stuck_state, False
    return answer.reply, [], [], new_state, answer.wants_close_topic
```

Обновить аннотацию возврата `_tutor_branch` и **оба** его вызова — в
`handle_turn` и в `stuck_reply` (там пятое значение не нужно: `_`). В
`handle_turn` ветка `else`:

```python
        reply, events, mastery, new_state, wants_close = await _tutor_branch(
            conn, client, model, session_id, user_text, state, graph,
            now=stamp, settings=s, force_stuck=force_stuck,
        )
        if wants_close:
            # Модель заметила просьбу закрыть тему: её реплику сохраняем, а
            # занятие уходит в проверочный проход.
            #
            # Состояние, посчитанное `_tutor_branch` (hint_level, phase,
            # task_hinted), СОЗНАТЕЛЬНО не переносится: проход начинается с
            # чистого листа — закрытие должно опираться на свидетельства
            # прохода, а не на прежнюю лестницу подсказок и серию. Потерять
            # при этом нечего: события и владение `_tutor_branch` всегда
            # возвращает пустыми, а всё остальное проход задаёт сам и пишет
            # своим `post_turn`.
            closing = verify.start_verification(conn, now=stamp, settings=s)
            # TurnReply — frozen dataclass, а не pydantic-модель: копия через
            # dataclasses.replace, не model_copy.
            return replace(closing, text=f"{reply}\n\n{closing.text}")
```

Импорт в `core/turn.py`: `from dataclasses import dataclass, replace`.

- [ ] **Step 4: Запустить весь набор**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add src/llm_tutor/llm/schemas.py src/llm_tutor/llm/prompts.py src/llm_tutor/core/turn.py tests/test_turn.py tests/test_prompts.py
git commit -m "Срез 16: «закрой тему» текстом и флаг модели"
```

## Срез 17. Меню из четырёх кнопок и «Продолжить обучение»

### Task 17.1: `core.turn.resume_reply` — умный resume

**Files:**
- Modify: `src/llm_tutor/core/turn.py`
- Test: `tests/test_turn.py`

**Interfaces:**
- Consumes: `_tutor_branch` (пять значений после 16.6), `start_practice_reply`,
  `route_mod.build_route`, `route_mod.next_node_id`, `route_mod.refresh`.
- Produces: `async resume_reply(conn, client, model, *, now=None, settings=None)
  -> TurnReply`; константа `RESUME_KICKOFF_TEXT`.

- [ ] **Step 1: Написать падающие тесты**

Добавить в `tests/test_turn.py` (в импорты — `resume_reply`,
`RESUME_KICKOFF_TEXT`, `PENDING_ITEM_NOTE`, `ROUTE_DONE_REPLY`):

```python
async def test_resume_keeps_pending_item(conn, settings) -> None:
    """Висящее задание не затирается — в этом смысл «продолжить»."""
    load_seed(conn)
    start_practice_reply(conn, now=1.0, settings=settings)
    pending = _state(conn).pending_item_id

    reply = await resume_reply(conn, _FakeTutor(), "m", now=2.0, settings=settings)

    assert reply.text == PENDING_ITEM_NOTE
    assert _state(conn).pending_item_id == pending


async def test_resume_explains_first_then_gives_task(conn, settings) -> None:
    """Фаза объяснения — тьюторский ход; дальше кнопка выдаёт задание."""
    load_seed(conn)
    ingest_text(conn, "# T\n\n## Grouping\n\ngroupby aggregates rows\n", "u")
    client = _FakeTutor("Смотри: groupby собирает строки в группы")

    first = await resume_reply(conn, client, "m", now=1.0, settings=settings)

    assert "groupby собирает строки" in first.text
    assert _state(conn).phase == "practice"

    second = await resume_reply(conn, client, "m", now=2.0, settings=settings)

    assert _state(conn).pending_item_id is not None
    assert second.options is not None


async def test_resume_reports_finished_route(conn, settings) -> None:
    """Маршрут исчерпан — честное сообщение, а не пустое задание."""
    load_seed(conn)
    route = Route(steps=[RouteStep(concept_id="pandas_intro", mode="skip", status="closed")])
    _set_state(conn, current_node_id=None, route=route, phase="practice")

    reply = await resume_reply(conn, _FakeTutor(), "m", now=1.0, settings=settings)

    assert reply.text == ROUTE_DONE_REPLY
```

- [ ] **Step 2: Запустить тесты — убедиться, что падают**

Run: `uv run pytest tests/test_turn.py -q -k resume`
Expected: FAIL — `ImportError: cannot import name 'resume_reply'`.

- [ ] **Step 3: Реализовать resume**

В `src/llm_tutor/core/turn.py` рядом с прочими текстами:

```python
# Повод хода «Продолжить обучение» — в журнале виден как реплика ученика.
RESUME_KICKOFF_TEXT = "Продолжаем занятие."
```

Рядом с `start_practice_reply`:

```python
async def resume_reply(
    conn: sqlite3.Connection,
    client: LLMClient,
    model: str,
    *,
    now: float | None = None,
    settings: Settings | None = None,
) -> TurnReply:
    """«Продолжить обучение»: занятие идёт с того места, где ученик остановился.

    Висящее задание не затирается — в этом отличие от `/task`, который молча
    выдаёт новое.
    """
    s = settings or get_settings()
    stamp = time.time() if now is None else now
    session_id = repos.ensure_open_session(conn, stamp)
    state = repos.get_session_state(conn, session_id)
    graph = CourseGraph.load(conn)
    if not graph.node_ids:
        return TurnReply(text=EMPTY_GRAPH_REPLY)

    pending = state.pending_item_id
    if pending is not None and repos.get_item(conn, pending) is not None:
        post_turn(
            conn,
            session_id,
            user_text=RESUME_KICKOFF_TEXT,
            assistant_text=PENDING_ITEM_NOTE,
            state=state.model_copy(update={"last_activity": stamp}),
            now=stamp,
        )
        return TurnReply(text=PENDING_ITEM_NOTE)

    route = state.route or route_mod.build_route(
        conn,
        graph,
        goal_concept_id=route_mod.goal_for(conn, graph),
        current_node_id=state.current_node_id,
        now=stamp,
        settings=s,
    )
    node_id = state.current_node_id or route_mod.next_node_id(
        conn, graph, route, now=stamp, settings=s
    )
    if node_id is None:
        post_turn(
            conn,
            session_id,
            user_text=RESUME_KICKOFF_TEXT,
            assistant_text=ROUTE_DONE_REPLY,
            state=state.model_copy(update={"route": route, "last_activity": stamp}),
            now=stamp,
        )
        return TurnReply(text=ROUTE_DONE_REPLY)

    if state.phase == "explain":
        # Обещанное в представлении «объясняю → даю задачу»: первое нажатие
        # объясняет, второе выдаёт задание (фаза уходит в practice).
        explaining = state.model_copy(update={"current_node_id": node_id})
        reply, events, mastery, new_state, _ = await _tutor_branch(
            conn, client, model, session_id, RESUME_KICKOFF_TEXT, explaining, graph,
            now=stamp, settings=s,
        )
        new_state = new_state.model_copy(update={"phase": "practice"})
        fresh_route, _ = route_mod.refresh(conn, new_state, graph, now=stamp, settings=s)
        post_turn(
            conn,
            session_id,
            user_text=RESUME_KICKOFF_TEXT,
            assistant_text=reply,
            state=new_state.model_copy(update={"route": fresh_route}),
            events=events,
            mastery=mastery,
            now=stamp,
        )
        return TurnReply(text=reply)

    return start_practice_reply(conn, now=stamp, settings=s)
```

- [ ] **Step 4: Запустить тесты хода**

Run: `uv run pytest tests/test_turn.py -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add src/llm_tutor/core/turn.py tests/test_turn.py
git commit -m "Срез 17: умный resume — висящее задание не затирается"
```

---

### Task 17.2: Меню из четырёх действий и слэш-команды

**Files:**
- Modify: `src/llm_tutor/bot/menu.py`
- Modify: `src/llm_tutor/bot/handlers.py`
- Test: `tests/test_menu.py`, `tests/test_bot_flows.py`

**Interfaces:**
- Consumes: `verify.start_verification` (16.3), `resume_reply` (17.1),
  `themes.themes_keyboard`, `render.render_status`, `render.render_help`.
- Produces: `Action = Literal["route", "themes", "close", "resume"]`; команды
  `/resume`, `/status`, `/help`, `/themes`.

- [ ] **Step 1: Написать падающие тесты**

Заменить в `tests/test_menu.py` тест набора действий:

```python
def test_actions_keyboard_lists_every_action() -> None:
    """В меню ровно четыре действия — по кнопке на каждое."""
    kb = menu.actions_keyboard()

    callbacks = [button.callback_data for row in kb.inline_keyboard for button in row]
    assert callbacks == [f"menu:{action}" for action in menu.ACTION_LABELS]
    assert set(menu.ACTION_LABELS) == {"route", "themes", "close", "resume"}
```

Добавить в `tests/test_bot_flows.py`:

```python
async def test_menu_resume_answers(conn, settings) -> None:
    """«Продолжить обучение» из меню ведёт занятие дальше."""
    load_seed(conn)
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    on_menu_action = _named(router, "callback_query", "on_menu_action")
    message = FakeMessage()

    await on_menu_action(FakeCallback("menu:resume", message))

    assert message.sent  # ученик получил ответ, а не тишину


async def test_removed_menu_action_answers_nothing(conn, settings) -> None:
    """Убранное действие больше не обрабатывается."""
    load_seed(conn)
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    on_menu_action = _named(router, "callback_query", "on_menu_action")
    message = FakeMessage()

    await on_menu_action(FakeCallback("menu:status", message))

    assert message.sent == []
```

Тесты используют существующий `_TutorClient` (отвечает `"ок"` на любой
структурированный запрос) — отдельная заглушка не нужна.

**Тут же правятся четыре существующих теста `tests/test_bot_flows.py`.** Они
жмут кнопки, которых после шага 3 не станет, и упадут: `message.sent == []`,
а `message.last_text` даст `IndexError`.

| Тест | Что с ним делать |
|---|---|
| `test_menu_action_sends_expected_text` | из параметров убрать `("status", "Моё обучение")` и `("help", "Что умею")`, добавить `("close", "Проверка")`; `("route", "Маршрут")` оставить |
| `test_menu_action_task_issues_task_with_options` | удалить: сценарий остался командой `/task`, но кнопки `🎯 Задание` больше нет |
| `test_menu_action_skip_clears_pending_item` | удалить: тот же сценарий уже покрыт `test_skip_command_clears_task_without_evidence` (команда `/skip` остаётся) |
| `test_menu_action_failure_is_reported_and_answered` | перевести на `menu:close`: мокать `verify.start_verification` вместо `start_practice_reply`, ожидание то же — сообщение вместо тишины и ответ на нажатие |

Новая параметризация первого теста:

```python
@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("route", "Маршрут"),
        ("close", "Проверка"),
    ],
)
async def test_menu_action_sends_expected_text(conn, settings, action, expected) -> None:
```

- [ ] **Step 2: Запустить тесты — убедиться, что падают**

Run: `uv run pytest tests/test_menu.py tests/test_bot_flows.py -q -k "menu"`
Expected: FAIL — в наборе действий всё ещё восемь записей.

- [ ] **Step 3: Ужать меню и развести действия**

В `src/llm_tutor/bot/menu.py`:

```python
Action = Literal["route", "themes", "close", "resume"]

ACTION_LABELS: dict[Action, str] = {
    "route": "🗺 Маршрут",
    "themes": "🎚 Темы",
    "close": "✅ Закрыть тему",
    "resume": "▶️ Продолжить обучение",
}
```

В `on_menu_action` (`src/llm_tutor/bot/handlers.py`) убрать ветки `status`,
`task`, `stuck`, `skip`, `help`; остаются четыре:

```python
            if action == "route":
                text = render.render_plan(conn, settings=settings)
                await callback.message.answer(text, parse_mode=render.PARSE_MODE)
            elif action == "themes":
                await callback.message.answer(
                    themes.THEMES_PROMPT,
                    parse_mode=render.PARSE_MODE,
                    reply_markup=themes.themes_keyboard(conn, settings=settings),
                )
            elif action == "close":
                reply = verify.start_verification(conn, settings=settings)
                await callback.message.answer(
                    render.fit(render.escape(reply.text)),
                    parse_mode=render.PARSE_MODE,
                    reply_markup=_options_keyboard(reply.options),
                )
            elif action == "resume":
                reply = await resume_reply(conn, client, model, settings=settings)
                await callback.message.answer(
                    render.fit(render.escape(reply.text)),
                    parse_mode=render.PARSE_MODE,
                    reply_markup=_options_keyboard(reply.options),
                )
```

Добавить команды `/resume`, `/status`, `/help`, `/themes` рядом с `on_plan`,
с той же подстраховкой `try/except` + `BOT_FAILURE_REPLY`, что у `/plan`:

```python
    @router.message(Command("resume"))
    async def on_resume(message: Message) -> None:
        try:
            reply = await resume_reply(conn, client, model, settings=settings)
        except Exception:  # noqa: BLE001 — команда не должна отвечать молчанием
            logger.exception("Сбой продолжения занятия")
            await message.answer(
                render.fit(render.escape(BOT_FAILURE_REPLY)), parse_mode=render.PARSE_MODE
            )
            return
        await message.answer(
            render.fit(render.escape(reply.text)),
            reply_markup=_options_keyboard(reply.options),
            parse_mode=render.PARSE_MODE,
        )

    @router.message(Command("status"))
    async def on_status(message: Message) -> None:
        await message.answer(
            render.render_status(conn, settings=settings), parse_mode=render.PARSE_MODE
        )

    @router.message(Command("help"))
    async def on_help(message: Message) -> None:
        await message.answer(render.render_help(), parse_mode=render.PARSE_MODE)

    @router.message(Command("themes"))
    async def on_themes(message: Message) -> None:
        await message.answer(
            themes.THEMES_PROMPT,
            parse_mode=render.PARSE_MODE,
            reply_markup=themes.themes_keyboard(conn, settings=settings),
        )
```

Импорт в `handlers.py`:

```python
from llm_tutor.core.turn import resume_reply
```

И убрать из импортов `stuck_reply`: единственная ветка, которая его звала
(`elif action == "stuck"`), удалена, а команды `/stuck` нет — иначе мёртвый
импорт.





**Тут же правится `_HELP_LINES` в `src/llm_tutor/bot/render.py`.** `render_help`
идёт по `menu.ACTION_LABELS` и берёт пояснение из `_HELP_LINES` — без записи
для `resume` он падает `KeyError`, а существующий тест
`test_render_help_lists_menu_actions` (`tests/test_render.py`) вызывает
`render_help()` и покраснеет уже на шаге 4 задачи 17.3, где гоняется весь
набор. Заменить словарь целиком:

```python
# Пояснение к каждому действию для справки (синхронно с menu.ACTION_LABELS).
_HELP_LINES: dict[str, str] = {
    "route": "путь к цели с прогрессом; тут же правится, если что-то знаешь",
    "themes": "выбрать тему: вернуться назад или забежать вперёд",
    "close": "проверить тему и закрыть её, если знания подтвердятся",
    "resume": "продолжить занятие с того места, где остановился",
}
```

`render.py` попадает и в `git add` шага 5.

- [ ] **Step 4: Запустить тесты меню, хендлеров и потоков**

Run: `uv run pytest tests/test_menu.py tests/test_handlers.py tests/test_bot_flows.py -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add src/llm_tutor/bot/menu.py src/llm_tutor/bot/handlers.py src/llm_tutor/bot/render.py tests/test_menu.py tests/test_bot_flows.py
git commit -m "Срез 17: меню из четырёх кнопок и слэш-команды"
```

---

### Task 17.3: Инлайн-кнопка «Продолжить обучение» под ответом

**Files:**
- Modify: `src/llm_tutor/bot/menu.py`
- Modify: `src/llm_tutor/bot/handlers.py`
- Test: `tests/test_menu.py`

**Interfaces:**
- Consumes: `ACTION_LABELS["resume"]` (17.2).
- Produces: `menu.resume_keyboard() -> InlineKeyboardMarkup` (колбэк
  `menu:resume`).

- [ ] **Step 1: Написать падающий тест**

В `tests/test_menu.py`:

```python
def test_resume_keyboard_calls_resume_action() -> None:
    """Кнопка «Продолжить» несёт колбэк действия resume."""
    kb = menu.resume_keyboard()

    buttons = [button for row in kb.inline_keyboard for button in row]
    assert len(buttons) == 1
    assert buttons[0].callback_data == "menu:resume"
    assert buttons[0].text == menu.ACTION_LABELS["resume"]
```

Добавить в `tests/test_bot_flows.py` (спека §7.6 — повторный `/start`):

```python
async def test_start_after_survey_offers_resume(conn, settings) -> None:
    """У вернувшегося ученика /start даёт кнопку «Продолжить обучение»."""
    load_seed(conn)
    survey.apply_answers(conn, {survey.EXPERIENCE_KEY: 1}, now=1.0, settings=settings)
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    on_start = _named(router, "message", "on_start")
    message = FakeMessage()

    await on_start(message, _fsm())

    assert message.sent[-1][1].inline_keyboard[0][0].callback_data == "menu:resume"
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `uv run pytest tests/test_menu.py::test_resume_keyboard_calls_resume_action -q`
Expected: FAIL — `AttributeError: ... has no attribute 'resume_keyboard'`.

- [ ] **Step 3: Добавить кнопку и прикрепить её к хвосту хода**

В `src/llm_tutor/bot/menu.py`:

```python
def resume_keyboard() -> InlineKeyboardMarkup:
    """Инлайн-кнопка «Продолжить обучение» — главное действие в один тап.

    Постоянная клавиатура остаётся из одной кнопки «☰ Меню»: вторая
    reply-кнопка сломала бы решение среза 12.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=ACTION_LABELS["resume"], callback_data="menu:resume"
                )
            ]
        ]
    )
```

В `handlers.py` в хендлерах, завершающих ход (`on_answer`, `on_text`,
`on_task`, `on_close`, ветки `close`/`resume` действия меню) заменить
`reply_markup=_options_keyboard(reply.options)` на
`reply_markup=_options_keyboard(reply.options) or menu.resume_keyboard()`.
В `on_text` строка

```python
            reply_markup=_options_keyboard(reply.options) or menu.main_menu(),
```

становится

```python
            # Варианты ответа (инлайн) в приоритете; иначе — «Продолжить».
            # Постоянная клавиатура persistent, переприкреплять её не нужно.
            reply_markup=_options_keyboard(reply.options) or menu.resume_keyboard(),
```

И в `on_start` (ветка вернувшегося ученика, спека §7.6) — приветствие тоже
получает кнопку «Продолжить» вместо постоянной клавиатуры:

```python
        reply = await handle_start(conn, client, model, START_GREETING)
        await message.answer(
            render.fit(render.escape(reply)),
            reply_markup=menu.resume_keyboard(),
            parse_mode=render.PARSE_MODE,
        )
```

Ветка первого входа (`survey_completed(conn)` == False) не меняется: там
`menu.main_menu()` прикрепляется к представлению, и тест на это есть.

- [ ] **Step 4: Запустить весь набор**

Run: `uv run pytest -q`
Expected: PASS. Единственное место, где тест проверяет `menu.main_menu()` как
`reply_markup`, — `test_bot_flows.py` (клавиатура представления на `/start`);
она не меняется.

- [ ] **Step 5: Коммит**

```bash
git add src/llm_tutor/bot/menu.py src/llm_tutor/bot/handlers.py tests/test_menu.py
git commit -m "Срез 17: инлайн-кнопка «Продолжить обучение» под ответом"
```

---

### Task 17.4: Справка и дашборд под новое меню

**Files:**
- Modify: `src/llm_tutor/bot/render.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `menu.ACTION_LABELS` (17.2). `_HELP_LINES` под четыре действия
  уже приведён в 17.2 — здесь он только дополняется текстовыми подсказками.
- Produces: `render_help()` упоминает текстовые выходы и команды;
  `render_status()` не отправляет к убранным кнопкам.

- [ ] **Step 1: Написать падающие тесты**

В `tests/test_render.py`:

```python
def test_help_covers_every_menu_action() -> None:
    """Справка описывает ровно те действия, что есть в меню."""
    text = render_help()

    for label in menu.ACTION_LABELS.values():
        assert label in text
    assert "⏭ Пропустить" not in text


def test_help_mentions_text_exits_and_commands() -> None:
    """Справка объясняет, что выходы работают текстом."""
    text = render_help()

    assert "не понял" in text
    assert "пропусти" in text
    assert "закрой тему" in text
    assert "/status" in text


def test_status_does_not_point_to_removed_buttons(conn) -> None:
    """Дашборд не отправляет к убранным кнопкам."""
    load_seed(conn)

    text = render_status(conn)

    assert "⏭ Пропустить" not in text
    assert "🎯 Задание" not in text
```

- [ ] **Step 2: Запустить тесты — убедиться, что падают**

Run: `uv run pytest tests/test_render.py -q -k "help or removed_buttons"`
Expected: FAIL — справка всё ещё содержит «⏭ Пропустить».

- [ ] **Step 3: Переписать справку и подсказки дашборда**

В `src/llm_tutor/bot/render.py` дополнить `render_help` текстовыми
подсказками (`_HELP_LINES` уже приведён к четырём действиям в 17.2):

```python
# Выходы работают и текстом, а не только кнопками: см. core/intents.py.
_TEXT_HINTS = (
    "Просто напиши, если что-то не так:\n"
    "  «не понял» — объясню подробнее\n"
    "  «пропусти» — снять текущее задание\n"
    "  «закрой тему» — проверю и закрою, если знания подтвердятся"
)

_COMMANDS = (
    "Команды: /plan · /themes · /close · /resume · /status · /task · /skip · /help"
)


def render_help() -> str:
    """Справка по действиям меню (синхронна с ``menu.ACTION_LABELS``)."""
    lines = ["ℹ️ <b>Что умею</b>"]
    for action, label in menu.ACTION_LABELS.items():
        lines.append(f"{label} — {_HELP_LINES[action]}")
    lines.append("")
    lines.append(escape(_TEXT_HINTS))
    lines.append("")
    lines.append(escape(_COMMANDS))
    return "\n".join(lines)
```

В `render_status` заменить хвост:

```python
    if session_state.pending_item_id is not None:
        lines.append("Ждёт ответа задание — ответь на него или напиши «пропусти».")
    else:
        lines.append("Задания нет — жми ▶️ Продолжить обучение.")
```

- [ ] **Step 4: Запустить тесты представления**

Run: `uv run pytest tests/test_render.py -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add src/llm_tutor/bot/render.py tests/test_render.py
git commit -m "Срез 17: справка и дашборд под новое меню"
```

---

## Срез 18. Онбординг и согласование маршрута

### Task 18.1: Общий путь свидетельства самооценки

**Files:**
- Create: `src/llm_tutor/student/self_report.py`
- Modify: `src/llm_tutor/student/survey.py`
- Test: `tests/test_survey.py`

**Interfaces:**
- Consumes: `repos.add_event`, `beta.plan_update`, `beta.write_update`
  (**не** `beta.update` — тот всегда коммитит сам и не принимает `commit`).
- Produces: `self_report.apply(conn, concept_id, *, correct: bool, now: float,
  settings: Settings, commit: bool = True) -> None`.

- [ ] **Step 1: Написать падающие тесты**

Добавить в `tests/test_survey.py` (в импорты — `self_report`, `beta`,
`repos`):

```python
def test_self_evidence_moves_mastery_weakly(conn, settings) -> None:
    """Самооценка — слабое свидетельство: владение растёт, но не до порога."""
    self_report.apply(conn, "read_csv", correct=True, now=1.0, settings=settings)

    mastery = beta.estimate(conn, "read_csv", now=1.0, settings=settings)
    assert mastery.mean > 0.5
    assert mastery.mean < settings.mastery_verify_threshold
    assert [event.source for event in repos.get_events(conn)] == ["self"]


def test_survey_prior_uses_shared_self_evidence(conn, settings) -> None:
    """Анкета пишет тот же тип свидетельства, что и «я это знаю»."""
    survey.apply_answers(conn, {survey.EXPERIENCE_KEY: 1}, now=1.0, settings=settings)

    assert {event.source for event in repos.get_events(conn)} == {"self"}
```

- [ ] **Step 2: Запустить тесты — убедиться, что падают**

Run: `uv run pytest tests/test_survey.py -q -k self_evidence`
Expected: FAIL — `ModuleNotFoundError: No module named 'llm_tutor.student.self_report'`.

- [ ] **Step 3: Вынести свидетельство самооценки в модуль**

Создать `src/llm_tutor/student/self_report.py`:

```python
"""Слабое свидетельство самооценки: «я это знаю» / «я это не знаю».

Самооценка не заменяет проверку: вес свидетельства мал
(``self_evidence_weight``), чтобы заявление не подменяло реальные задания.
Один и тот же путь используют анкета холодного старта и правка маршрута.
"""

import sqlite3

from llm_tutor.config import Settings
from llm_tutor.db import repos
from llm_tutor.schemas import Event
from llm_tutor.student import beta


def apply(
    conn: sqlite3.Connection,
    concept_id: str,
    *,
    correct: bool,
    now: float,
    settings: Settings,
    commit: bool = True,
) -> None:
    """Пишет слабое свидетельство самооценки: событие + обновление Beta.

    ``beta.update`` не годится: он всегда коммитит сам и не принимает
    ``commit``, а нам нужна возможность писать в общей транзакции хода.
    """
    weight = settings.self_evidence_weight
    repos.add_event(
        conn,
        Event(source="self", result=correct, concept_id=concept_id, weight=weight, ts=now),
        commit=commit,
    )
    change = beta.plan_update(
        conn, concept_id, correct=correct, weight=weight, now=now, settings=settings
    )
    beta.write_update(conn, change, commit=commit)
```

В `src/llm_tutor/student/survey.py` заменить тело `_apply_prior` на
делегирование; из импортов убрать `Event` и `beta`, если они больше нигде в
модуле не используются:

```python
def _apply_prior(
    conn: sqlite3.Connection,
    concept_id: str,
    correct: float,
    *,
    now: float,
    settings: Settings,
) -> None:
    """Пишет слабое свидетельство самооценки из анкеты."""
    self_report.apply(conn, concept_id, correct=bool(correct), now=now, settings=settings)
```

- [ ] **Step 4: Запустить тесты анкеты и E2E**

Run: `uv run pytest tests/test_survey.py tests/test_e2e_topic01.py -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add src/llm_tutor/student/self_report.py src/llm_tutor/student/survey.py tests/test_survey.py
git commit -m "Срез 18: общий путь свидетельства самооценки"
```

---

### Task 18.2: Представление объясняет, что будет

**Files:**
- Modify: `src/llm_tutor/bot/survey.py`
- Test: `tests/test_bot_flows.py`

**Interfaces:**
- Consumes: ничего.
- Produces: новый текст `survey.INTRO_TEXT`.

- [ ] **Step 1: Написать падающий тест**

Добавить в `tests/test_bot_flows.py` (`INTRO_TEXT` там уже импортирован):

```python
def test_intro_explains_what_happens() -> None:
    """Представление объясняет, что будет происходить, до анкеты."""
    assert "спрошу пару вопросов" in INTRO_TEXT
    assert "соберу маршрут" in INTRO_TEXT
    assert "объясняю → даю задачу → проверяю" in INTRO_TEXT
    assert "перестрою" in INTRO_TEXT
```

И обновить проверку текстов в `tests/test_survey.py`: она держится за
формулировки, которых в новом представлении нет, и за `SURVEY_DONE_REPLY` —
а его удалит задача 18.5. Сейчас там:

```python
    assert "учиться" in bot_survey.INTRO_TEXT.lower()
    assert "меню" in bot_survey.INTRO_TEXT.lower()
    assert "маршрут" in bot_survey.SURVEY_DONE_REPLY.lower()
```

Заменить на проверку нового текста (одна строка, в том же тесте):

```python
    assert "спрошу пару вопросов" in bot_survey.INTRO_TEXT
    assert "соберу маршрут" in bot_survey.INTRO_TEXT
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `uv run pytest tests/test_bot_flows.py::test_intro_explains_what_happens -q`
Expected: FAIL — в текущем `INTRO_TEXT` этих строк нет.

- [ ] **Step 3: Переписать `INTRO_TEXT`**

В `src/llm_tutor/bot/survey.py`:

```python
INTRO_TEXT = (
    "👋 Привет! Я тьютор по теме 1 курса mlcourse.ai — «Pandas / EDA».\n\n"
    "Что будет:\n"
    "  1 · спрошу пару вопросов о тебе — это 30 секунд\n"
    "  2 · соберу маршрут под твою цель и покажу его\n"
    "  3 · дальше ведём по шагам: объясняю → даю задачу → проверяю\n\n"
    "Если что-то из темы уже знаешь — скажешь, перестрою.\n\n"
    "Поехали 👇"
)
```

- [ ] **Step 4: Запустить тесты потоков и анкеты**

Run: `uv run pytest tests/test_bot_flows.py tests/test_survey.py -q`
Expected: PASS. На шаге 3 правится только ветка первого `/start` — финал
анкеты ещё прежний (`SURVEY_DONE_REPLY`), его меняет 18.5.

- [ ] **Step 5: Коммит**

```bash
git add src/llm_tutor/bot/survey.py tests/test_bot_flows.py tests/test_survey.py
git commit -m "Срез 18: представление объясняет, что будет происходить"
```

---

### Task 18.3: Окно маршрута как переиспользуемая функция

**Files:**
- Modify: `src/llm_tutor/bot/render.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `route_mod.build_route`, `_windowed`, `PLAN_WINDOW`
  (существуют).
- Produces: `render.route_window(conn, *, state=None, now=None, settings=None)
  -> list[RouteStep]`.

- [ ] **Step 1: Написать падающие тесты**

В `tests/test_render.py` (в импорты — `CourseGraph`, если его там нет):

```python
def test_route_window_matches_rendered_plan(conn) -> None:
    """Окно маршрута — ровно те шаги, что попадают в табличку."""
    load_seed(conn)
    session_id = repos.ensure_open_session(conn, now=1.0)
    state = repos.get_session_state(conn, session_id)
    repos.update_session_state(
        conn, session_id, state.model_copy(update={"current_node_id": "groupby"})
    )

    window = render.route_window(conn, now=1.0)
    text = render.render_plan(conn, now=1.0)
    graph = CourseGraph.load(conn)

    assert window
    for step in window:
        assert graph.concept(step.concept_id).name in text


def test_route_window_includes_current_node(conn) -> None:
    """Текущий узел всегда в окне — иначе экран согласования не о том."""
    load_seed(conn)
    session_id = repos.ensure_open_session(conn, now=1.0)
    state = repos.get_session_state(conn, session_id)
    repos.update_session_state(
        conn, session_id, state.model_copy(update={"current_node_id": "groupby"})
    )

    window = render.route_window(conn, now=1.0)

    assert any(step.status == "current" for step in window)
```

- [ ] **Step 2: Запустить тесты — убедиться, что падают**

Run: `uv run pytest tests/test_render.py -q -k route_window`
Expected: FAIL — `AttributeError: module ... has no attribute 'route_window'`.

- [ ] **Step 3: Выделить окно в отдельную функцию**

В `src/llm_tutor/bot/render.py`:

```python
def route_window(
    conn: sqlite3.Connection,
    *,
    state: SessionState | None = None,
    now: float | None = None,
    settings: Settings | None = None,
) -> list:
    """Шаги маршрута в окне вокруг текущего — те же, что показывает табличка.

    Нужна экрану согласования маршрута: кнопки узлов должны соответствовать
    табличке, а не показывать все 21 узел стеной.
    """
    graph = CourseGraph.load(conn)
    if not graph.node_ids:
        return []
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
    return _windowed(route.steps, PLAN_WINDOW)
```

В `render_plan` строку

```python
    shown = _windowed(route.steps, PLAN_WINDOW)
```

заменить на

```python
    shown = route_window(conn, state=session_state, now=now, settings=settings)
```

- [ ] **Step 4: Запустить тесты представления**

Run: `uv run pytest tests/test_render.py -q`
Expected: PASS — табличка рисуется так же, тесты среза 13 не ломаются.

- [ ] **Step 5: Коммит**

```bash
git add src/llm_tutor/bot/render.py tests/test_render.py
git commit -m "Срез 18: окно маршрута — переиспользуемая функция"
```

---

### Task 18.4: Экран согласования маршрута

**Files:**
- Modify: `src/llm_tutor/bot/render.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `render_plan`, `escape` (существуют).
- Produces: `render.ROUTE_REVIEW_NOTE`; `render.render_route_screen(conn, *,
  state=None, now=None, settings=None) -> str` — готовый HTML.

- [ ] **Step 1: Написать падающий тест**

В `tests/test_render.py`:

```python
def test_route_screen_contains_plan_and_invitation(conn) -> None:
    """Экран согласования — та же табличка плюс приглашение поправить."""
    load_seed(conn)

    text = render.render_route_screen(conn, now=1.0)

    assert "Маршрут" in text
    assert "устраивает" in text
    assert "нажми узел" in text
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `uv run pytest tests/test_render.py::test_route_screen_contains_plan_and_invitation -q`
Expected: FAIL — `AttributeError: ... has no attribute 'render_route_screen'`.

- [ ] **Step 3: Добавить экран**

В `src/llm_tutor/bot/render.py` рядом с `render_plan`:

```python
# Приглашение поправить маршрут на входе в курс (готовый HTML: экранируем сами).
ROUTE_REVIEW_NOTE = (
    "Если что-то из этого ты уже знаешь — нажми узел и скажи.\n"
    "Или жми <b>✅ Меня всё устраивает</b>."
)


def render_route_screen(
    conn: sqlite3.Connection,
    *,
    state: SessionState | None = None,
    now: float | None = None,
    settings: Settings | None = None,
) -> str:
    """Экран согласования маршрута: табличка плюс приглашение поправить."""
    plan = render_plan(conn, state=state, now=now, settings=settings)
    return f"{plan}\n\n{ROUTE_REVIEW_NOTE}"
```

- [ ] **Step 4: Запустить тесты представления**

Run: `uv run pytest tests/test_render.py -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add src/llm_tutor/bot/render.py tests/test_render.py
git commit -m "Срез 18: экран согласования маршрута"
```

---

### Task 18.5: Роутер онбординга — правка узлов и фиксация маршрута

**Files:**
- Create: `src/llm_tutor/bot/onboarding.py`
- Modify: `tests/fakes.py` (заглушки бота добавляются к `GradingTutor` из 16.4)
- Modify: `src/llm_tutor/bot/survey.py`
- Modify: `src/llm_tutor/bot/main.py`
- Modify: `tests/test_bot_flows.py`
- Test: `tests/test_onboarding.py`

**Interfaces:**
- Consumes: `render.render_route_screen` (18.4), `render.route_window` (18.3),
  `self_report.apply` (18.1), `menu.resume_keyboard` (17.3).
- Produces: `OnboardingFlow.route_review`; `show_route_screen(message, state,
  conn, settings) -> None`; `make_onboarding_router(conn, settings) -> Router`;
  колбэки `route:node:<id>`, `route:know:<id>`, `route:unknown:<id>`,
  `route:leave`, `route:ok`; константы `ROUTE_ACTIONS_LABEL_OK`,
  `ROUTE_FIXED_TEMPLATE`, `ROUTE_HINT_REPLY`.

- [ ] **Step 1: Вынести тестовые заглушки в общий модуль**

Дописать в существующий `tests/fakes.py` (создан в 16.4) `FakeMessage`,
`FakeCallback`, `_fsm`, `_named` — перенеся их **как есть** из
`tests/test_bot_flows.py` (определения не менять, только перенести; их
импорты `aiogram.fsm.context`, `aiogram.fsm.storage.*`,
`aiogram.types.InlineKeyboardMarkup` переезжают вместе с ними). В
`tests/test_bot_flows.py` удалить определения и добавить импорт:

```python
from fakes import FakeCallback, FakeMessage, _fsm, _named
```

Импорт работает через `rootdir`-вставку pytest: файлы тестов лежат в `tests/`
без `__init__.py`, поэтому модуль импортируется по имени файла (проверено на
песочнице с той же раскладкой).

Проверка, что перенос ничего не сломал:

Run: `uv run pytest tests/test_bot_flows.py -q`
Expected: PASS.

- [ ] **Step 2: Написать падающие тесты**

Создать `tests/test_onboarding.py`:

```python
"""Тесты согласования маршрута на входе в курс (Срез 18)."""

from llm_tutor.bot.onboarding import make_onboarding_router
from llm_tutor.course.seed import load_seed
from llm_tutor.db import repos
from llm_tutor.student import beta

from fakes import FakeCallback, FakeMessage, _fsm, _named


def _state(conn):
    session_id = repos.ensure_open_session(conn, now=1.0)
    return repos.get_session_state(conn, session_id)


async def test_route_node_offers_three_actions(conn, settings) -> None:
    """Нажатие узла предлагает: знаю / не знаю / оставить."""
    load_seed(conn)
    router = make_onboarding_router(conn, settings)
    on_node = _named(router, "callback_query", "on_route_node")
    message = FakeMessage()

    await on_node(FakeCallback("route:node:read_csv", message))

    callbacks = [
        button.callback_data for row in message.sent[-1][1].inline_keyboard for button in row
    ]
    assert callbacks == ["route:know:read_csv", "route:unknown:read_csv", "route:leave"]


async def test_know_writes_weak_evidence_and_does_not_close(conn, settings) -> None:
    """«Я это знаю» двигает владение слабо и узел НЕ закрывает."""
    load_seed(conn)
    router = make_onboarding_router(conn, settings)
    on_know = _named(router, "callback_query", "on_route_know")

    await on_know(FakeCallback("route:know:read_csv", FakeMessage()), _fsm())

    mastery = beta.estimate(conn, "read_csv", now=1.0, settings=settings)
    assert mastery.mean > 0.5
    assert mastery.mean < settings.mastery_skip_threshold


async def test_unknown_lowers_priority(conn, settings) -> None:
    """«Я это не знаю» — свидетельство против владения."""
    load_seed(conn)
    router = make_onboarding_router(conn, settings)
    on_unknown = _named(router, "callback_query", "on_route_unknown")

    await on_unknown(FakeCallback("route:unknown:read_csv", FakeMessage()), _fsm())

    mastery = beta.estimate(conn, "read_csv", now=1.0, settings=settings)
    assert mastery.mean < 0.5


async def test_route_ok_fixes_route_and_offers_resume(conn, settings) -> None:
    """«Меня всё устраивает» фиксирует маршрут и даёт кнопку «Продолжить»."""
    load_seed(conn)
    router = make_onboarding_router(conn, settings)
    on_ok = _named(router, "callback_query", "on_route_ok")
    message = FakeMessage()

    await on_ok(FakeCallback("route:ok", message), _fsm())

    text, markup = message.sent[-1]
    assert "Маршрут зафиксирован" in text
    assert markup.inline_keyboard[0][0].callback_data == "menu:resume"


async def test_unknown_node_callback_is_refused(conn, settings) -> None:
    """Подделанный колбэк узла не роняет хендлер."""
    load_seed(conn)
    router = make_onboarding_router(conn, settings)
    on_node = _named(router, "callback_query", "on_route_node")
    message = FakeMessage()

    await on_node(FakeCallback("route:node:нет_такого", message))

    assert message.sent == []


async def test_route_review_text_gets_hint(conn, settings) -> None:
    """На экране согласования текст отвечает подсказкой, а не тьютором."""
    load_seed(conn)
    router = make_onboarding_router(conn, settings)
    on_text = _named(router, "message", "on_route_text")
    message = FakeMessage("я это знаю")

    await on_text(message)

    assert "кнопкой" in message.last_text
```

- [ ] **Step 3: Запустить тесты — убедиться, что падают**

Run: `uv run pytest tests/test_onboarding.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'llm_tutor.bot.onboarding'`.

- [ ] **Step 4: Написать роутер и подключить его**

Создать `src/llm_tutor/bot/onboarding.py`:

```python
"""Онбординг: представление → анкета → согласование маршрута (Срез 18).

Экран согласования даёт ученику сказать «это я уже знаю» про конкретный узел.
Заявление пишет СЛАБОЕ свидетельство (``student/self_report``) и пересчитывает
маршрут; закрыть узел оно не может — закрытие только через проверку.
"""

import logging
import sqlite3
import time

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from llm_tutor.bot import menu, render
from llm_tutor.config import Settings
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.llm.prompts import BOT_FAILURE_REPLY
from llm_tutor.schemas import SessionState
from llm_tutor.student import route as route_mod
from llm_tutor.student import self_report

logger = logging.getLogger(__name__)

ROUTE_OK_LABEL = "✅ Меня всё устраивает"
ROUTE_ALL_THEMES_LABEL = "🎚 Все темы"
ROUTE_HINT_REPLY = "Выбери узел кнопкой или жми ✅ Меня всё устраивает."
ROUTE_FIXED_TEMPLATE = (
    "✅ Маршрут зафиксирован: {total} шагов, цель — «{goal}».\n\n"
    "Жми ▶️ — начнём с темы «{first}»."
)
ROUTE_FIXED_EMPTY = "✅ Маршрут зафиксирован. Жми ▶️ Продолжить обучение."


class OnboardingFlow(StatesGroup):
    """Согласование маршрута: ждём нажатия кнопки узла или подтверждения."""

    route_review = State()


def _node_keyboard(conn: sqlite3.Connection, *, now=None, settings=None) -> InlineKeyboardMarkup:
    """Кнопки узлов окна маршрута + полный список + подтверждение."""
    graph = CourseGraph.load(conn)
    buttons = [
        InlineKeyboardButton(
            text=graph.concept(step.concept_id).name,
            callback_data=f"route:node:{step.concept_id}",
        )
        for step in render.route_window(conn, now=now, settings=settings)
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    rows.append(
        [InlineKeyboardButton(text=ROUTE_ALL_THEMES_LABEL, callback_data="menu:themes")]
    )
    rows.append([InlineKeyboardButton(text=ROUTE_OK_LABEL, callback_data="route:ok")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _node_actions_keyboard(node_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Я это знаю", callback_data=f"route:know:{node_id}")],
            [
                InlineKeyboardButton(
                    text="❗ Я это не знаю", callback_data=f"route:unknown:{node_id}"
                )
            ],
            [InlineKeyboardButton(text="↩️ Оставить как есть", callback_data="route:leave")],
        ]
    )


async def show_route_screen(
    message: Message,
    state: FSMContext,
    conn: sqlite3.Connection,
    settings: Settings,
    *,
    note: str | None = None,
) -> None:
    """Показывает экран согласования и переводит FSM в ожидание выбора."""
    await state.set_state(OnboardingFlow.route_review)
    text = render.render_route_screen(conn, settings=settings)
    if note:
        text = f"{render.escape(note)}\n\n{text}"
    await message.answer(
        text,
        parse_mode=render.PARSE_MODE,
        reply_markup=_node_keyboard(conn, settings=settings),
    )


def make_onboarding_router(conn: sqlite3.Connection, settings: Settings) -> Router:
    """Роутер согласования маршрута."""
    router = Router()

    def _record(node_id: str, *, correct: bool) -> None:
        """Слабое свидетельство + пересчёт маршрута одним коммитом."""
        stamp = time.time()
        session_id = repos.ensure_open_session(conn, stamp)
        state = repos.get_session_state(conn, session_id)
        self_report.apply(conn, node_id, correct=correct, now=stamp, settings=settings)
        graph = CourseGraph.load(conn)
        fresh_route, _ = route_mod.refresh(conn, state, graph, now=stamp, settings=settings)
        repos.update_session_state(
            conn, session_id, state.model_copy(update={"route": fresh_route})
        )
        conn.commit()

    @router.callback_query(OnboardingFlow.route_review, F.data.startswith("route:node:"))
    async def on_route_node(callback: CallbackQuery) -> None:
        node_id = (callback.data or "").split(":", 2)[2]
        # Данные колбэка подконтрольны клиенту: неизвестный узел не должен
        # ронять хендлер (иначе у ученика зависает «часик»).
        if not CourseGraph.load(conn).has_node(node_id):
            await callback.answer("Тема недоступна")
            return
        await callback.message.answer(
            "Что с этой темой?", reply_markup=_node_actions_keyboard(node_id)
        )
        await callback.answer()

    @router.callback_query(OnboardingFlow.route_review, F.data.startswith("route:know:"))
    async def on_route_know(callback: CallbackQuery, state: FSMContext) -> None:
        node_id = (callback.data or "").split(":", 2)[2]
        try:
            if not CourseGraph.load(conn).has_node(node_id):
                await callback.answer("Тема недоступна")
                return
            _record(node_id, correct=True)
            await callback.answer()
            await show_route_screen(
                callback.message, state, conn, settings, note="Записал: знаешь эту тему."
            )
        except Exception:  # noqa: BLE001 — нажатие не должно отвечать молчанием
            logger.exception("Сбой правки маршрута: %s", node_id)
            await callback.message.answer(
                render.fit(render.escape(BOT_FAILURE_REPLY)), parse_mode=render.PARSE_MODE
            )
            await callback.answer()

    @router.callback_query(OnboardingFlow.route_review, F.data.startswith("route:unknown:"))
    async def on_route_unknown(callback: CallbackQuery, state: FSMContext) -> None:
        node_id = (callback.data or "").split(":", 2)[2]
        try:
            if not CourseGraph.load(conn).has_node(node_id):
                await callback.answer("Тема недоступна")
                return
            _record(node_id, correct=False)
            await callback.answer()
            await show_route_screen(
                callback.message, state, conn, settings, note="Записал: тема новая."
            )
        except Exception:  # noqa: BLE001 — нажатие не должно отвечать молчанием
            logger.exception("Сбой правки маршрута: %s", node_id)
            await callback.message.answer(
                render.fit(render.escape(BOT_FAILURE_REPLY)), parse_mode=render.PARSE_MODE
            )
            await callback.answer()

    @router.callback_query(OnboardingFlow.route_review, F.data == "route:leave")
    async def on_route_leave(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await show_route_screen(callback.message, state, conn, settings)

    @router.callback_query(OnboardingFlow.route_review, F.data == "route:ok")
    async def on_route_ok(callback: CallbackQuery, state: FSMContext) -> None:
        try:
            graph = CourseGraph.load(conn)
            session_id = repos.get_open_session(conn)
            current = (
                repos.get_session_state(conn, session_id) if session_id else SessionState()
            )
            route = current.route or route_mod.build_route(
                conn, graph, goal_concept_id=route_mod.goal_for(conn, graph), settings=settings
            )
            first = next(
                (step.concept_id for step in route.steps if step.status != "closed"), None
            )
            if first is None:
                text = ROUTE_FIXED_EMPTY
            else:
                goal_id = route.goal_concept_id
                text = ROUTE_FIXED_TEMPLATE.format(
                    total=len(route.steps),
                    goal=graph.concept(goal_id).name if goal_id else "вершина темы",
                    first=graph.concept(first).name,
                )
        except Exception:  # noqa: BLE001 — нажатие не должно отвечать молчанием
            logger.exception("Сбой фиксации маршрута")
            text = BOT_FAILURE_REPLY
        await state.clear()
        await callback.message.answer(
            render.escape(text),
            parse_mode=render.PARSE_MODE,
            reply_markup=menu.resume_keyboard(),
        )
        await callback.answer()

    @router.message(OnboardingFlow.route_review, F.text)
    async def on_route_text(message: Message) -> None:
        # Экран согласования принимает только кнопки: иначе реплика ушла бы в
        # тьюторский ход и узел выбрался бы «на слух».
        await message.answer(ROUTE_HINT_REPLY)

    return router
```

В `src/llm_tutor/bot/survey.py` заменить финал анкеты: вместо
`SURVEY_DONE_REPLY` — экран согласования (импорт
`from llm_tutor.bot import onboarding`):

```python
        survey.apply_answers(conn, answers, settings=settings)
        await callback.answer()
        await onboarding.show_route_screen(callback.message, state, conn, settings)
        return
```

Константа `SURVEY_DONE_REPLY` после этого не используется — удалить её.
Ссылок на неё в репозитории ровно три: определение, этот финал анкеты и тест
из `tests/test_survey.py`, который снят в 18.2; других не остаётся.

**И правится тест финала анкеты в `tests/test_bot_flows.py`.** Сейчас там
проверяется, что FSM-поток закрыт:

```python
    assert await state.get_state() is None  # поток закрыт
```

После этой задачи финал анкеты не закрывает поток, а открывает экран
согласования. Заменить на:

```python
    assert await state.get_state() == OnboardingFlow.route_review.state
```

и добавить в импорты файла `from llm_tutor.bot.onboarding import OnboardingFlow`.

В `src/llm_tutor/bot/main.py` подключить роутер (после диагностики, до
основного):

```python
from llm_tutor.bot.onboarding import make_onboarding_router
```

```python
        dispatcher.include_router(make_onboarding_router(conn, settings))
```

- [ ] **Step 5: Запустить тесты онбординга, потоков и анкеты**

Run: `uv run pytest tests/test_onboarding.py tests/test_bot_flows.py tests/test_survey.py -q`
Expected: PASS.

- [ ] **Step 6: Коммит**

```bash
git add src/llm_tutor/bot/onboarding.py src/llm_tutor/bot/survey.py src/llm_tutor/bot/main.py tests/fakes.py tests/test_bot_flows.py tests/test_onboarding.py
git commit -m "Срез 18: согласование маршрута на входе в курс"
```

---

### Task 18.6: Сквозной сценарий нового ученика

**Files:**
- Test: `tests/test_e2e_topic01.py`

**Interfaces:**
- Consumes: всё предыдущее.
- Produces: сквозной тест цепочки.

- [ ] **Step 1: Написать тест**

Добавить в `tests/test_e2e_topic01.py`:

```python
async def test_new_student_walks_the_whole_path(conn, settings) -> None:
    """Согласование маршрута → «закрой тему» текстом → проверка → узел закрыт.

    Проверяет, что куски срезов 15–18 сходятся: экран согласования пишет
    слабое свидетельство и НЕ закрывает узел, «закрой тему» текстом ведёт
    проверочный проход, а закрывает узел серия чистых ответов.
    """
    load_seed(conn)
    ingest_text(conn, "# T\n\n## Grouping\n\ngroupby aggregates rows\n", "u")

    # 1. Согласование маршрута: заявление о знании узел НЕ закрывает.
    onboarding_router = make_onboarding_router(conn, settings)
    on_know = _named(onboarding_router, "callback_query", "on_route_know")
    await on_know(FakeCallback("route:know:read_csv", FakeMessage()), _fsm())

    assert not _is_closed(conn, "read_csv")

    # 2. Переходим на groupby — единственный узел с несколькими заданиями —
    #    и просим закрыть тему текстом.
    session_id = repos.ensure_open_session(conn, now=1.0)
    state = repos.get_session_state(conn, session_id)
    repos.update_session_state(
        conn, session_id, state.model_copy(update={"current_node_id": "groupby"})
    )
    client = GradingTutor(conn, passed=True)

    reply = await handle_turn(conn, client, "m", "закрой тему", now=2.0, settings=settings)

    assert "Проверка" in reply.text
    assert repos.get_session_state(conn, session_id).mode == "verify"

    # 3. Два чистых ответа закрывают узел.
    for ts in (3.0, 4.0):
        await handle_turn(
            conn, client, "m", "groupby группирует строки по ключу", now=ts, settings=settings
        )

    assert _is_closed(conn, "groupby")
```

Заглушку не дублируем — берём общую из `tests/fakes.py`:

```python
from fakes import FakeCallback, FakeMessage, GradingTutor, _fsm, _named
```

Остальные недостающие импорты: `make_onboarding_router` из
`llm_tutor.bot.onboarding`; `handle_turn` из `llm_tutor.core.turn`. Хелпер:

```python
def _is_closed(conn, node_id: str) -> bool:
    """Закрыт ли узел — по снимку маршрута (его пишет код закрытия узла).

    Снимок, а не `beta.estimate`: закрытие по серии чистых ответов не обязано
    поднимать владение выше порога, а `build_route` сохраняет отметку закрытия
    из предыдущего снимка.
    """
    session_id = repos.get_open_session(conn)
    if session_id is None:
        return False
    state = repos.get_session_state(conn, session_id)
    if state.route is None:
        return False
    return any(
        step.concept_id == node_id and step.status == "closed" for step in state.route.steps
    )
```

Вызовы в тесте — `_is_closed(conn, "read_csv")` и `_is_closed(conn, "groupby")`.

- [ ] **Step 2: Запустить тест — убедиться, что он осмысленно проходит**

Run: `uv run pytest tests/test_e2e_topic01.py -q -k new_student`
Expected: PASS. Если падает — сначала проверить, что предыдущие задачи среза
18 закрыты: тест собирает их вместе.

- [ ] **Step 3: Запустить весь набор**

Run: `uv run pytest -q`
Expected: PASS — весь набор зелёный.

- [ ] **Step 4: Коммит**

```bash
git add tests/test_e2e_topic01.py
git commit -m "Срез 18: сквозной тест нового ученика"
```

---

## Итог

После всех задач:

- меню — четыре действия, у каждого есть обработчик и слэш-команда;
- «не понял», «пропусти», «закрой тему» работают текстом даже при висящем
  задании;
- «закрой тему» — проверочный проход, закрывающий узел только по
  свидетельствам;
- «продолжить обучение» не затирает висящее задание;
- новый ученик проходит представление → анкету → согласование маршрута → урок.

Ручная проверка на живом боте — по §13 спеки: понятна ли цепочка входа,
не раздражает ли экран согласования, ловят ли фразы намерение на живой речи.

