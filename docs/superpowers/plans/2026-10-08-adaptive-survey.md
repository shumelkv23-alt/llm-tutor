# Адаптивная анкета в одном сообщении — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Вход нового ученика — одно сообщение, которое правится на месте
(приветствие → адаптивные вопросы → сводка), кнопки не зависают и не
засчитываются дважды, а ответы анкеты реально меняют урок: старт с первого
неуверенного блока, проверка «знакомого» в конце, профиль в промпте тьютора.

**Architecture:** Ветвление анкеты — чистый неизменяемый `survey.Progress` в
`student/survey.py` (без aiogram). `bot/survey.py` только рисует: один
callback-хендлер на все `survey:*`, привязка нажатия к `message_id` (FSM) и к
шагу (`callback_data`), решение принимается до первого сетевого `await`.
Влияние на урок — новый статус шага маршрута `claimed`: для готовности он
равен выполненному пререквизиту, а в конце маршрута такой узел входит
проверочным проходом (`mode="verify"`, механика среза 16).

**Tech Stack:** Python 3.12, aiogram 3.31, pydantic 2, pytest (asyncio_mode=auto),
SQLite.

**Spec:** `docs/superpowers/specs/2026-10-08-adaptive-survey-design.md`

## Global Constraints

- Python 3.12; тесты — pytest, асинхронные без декоратора (`asyncio_mode =
  "auto"`). Весь набор: `uv run pytest -q` (на старте — 518 passed).
- Type hints на всех сигнатурах; докстринги и комментарии — по-русски, как в
  проекте.
- **Конвенция вывода:** `core.*` и `bot/start.py` (функции текста) отдают
  **сырой** текст — хендлер шлёт `render.fit(render.escape(text))`;
  `bot/render.*` и `bot/survey.question_view/summary_text` отдают **готовый
  HTML** — шлются с `parse_mode=render.PARSE_MODE` без повторного `escape`.
- **Слоистость:** `bot → core/student/db`; `core/*` и `student/*` не
  импортируют `bot/*`.
- **Схема БД не меняется.** `claimed` живёт в JSON снимка маршрута.
- Подпись «Уверенно» (`SELF_LEVELS[3]`) — дословно та же, что раньше: по ней
  узнаются заявленные блоки, в т.ч. у учеников, прошедших старую анкету.
- Ключи фактов блоков (`block_python`, `block_tables`, `block_loading`,
  `block_selection`, `block_analysis`) не меняются.
- Инлайн-кнопки — только варианты ответа на задание и кнопки сообщения
  анкеты («Поехали», варианты, «Назад»).
- `callback.answer()` — первый сетевой вызов в каждой ветке хендлера анкеты и
  вызывается в каждой ветке.
- Коммиты по-русски: `Срез 22: …` / `Срез 23: …`. Ветка — `adaptive-survey`.
- После каждого среза — адверсариальное ревью fork-агентом и фиксы отдельным
  коммитом `Аудит среза N: …` (норма проекта).

## Review Focus

Ввод, который спека подразумевает, но легко пропустить. Тест каждой строки —
в задаче в скобках:

1. **Двойной тап на последнем ответе** — урок стартует ровно один раз, у
   второго нажатия «часики» гаснут (22.3).
2. **Клик по старому сообщению анкеты после повторного `/start`** — тост, ничего
   не пишется, новая анкета не сбита (22.3).
3. **Нажатие кнопки анкеты после рестарта бота (FSM пуст, анкета не пройдена)**
   — анкета начинается заново в этом же сообщении, а не молчит (22.3).
4. **Все блоки «Уверенно»** — незаявленных узлов нет: урок начинается сразу с
   проверки, а не с «маршрут пройден» (23.2, 23.3).
5. **Провал проверки заявленного узла** — узел уходит в обычный урок и больше не
   помечен 🔍 (23.2).

---

## Срез 22 — механика и логика анкеты

### Task 22.1: Логика адаптивной анкеты (чистые функции)

**Files:**
- Modify: `src/llm_tutor/student/survey.py` (весь файл)
- Test: `tests/test_survey.py`

**Interfaces:**
- Produces:
  - `SELF_LEVELS: tuple[str, ...]`, `PRIOR_LEVELS`, `UNKNOWN_INDEX = 0`,
    `CONFIDENT_INDEX = 3`
  - `LEVEL_KEY = "survey_level"`, `LEVEL_QUESTION: str`,
    `LEVEL_OPTIONS: tuple[str, ...]`, `LEVEL_FROM_SCRATCH, LEVEL_SOME,
    LEVEL_CONFIDENT = 0, 1, 2`
  - `@dataclass(frozen=True) Block(key, title, question, example, concepts)`,
    `BLOCKS: tuple[Block, ...]`, `ASSUMED_BY_CONFIDENT`, `FOUNDATION`
  - `block_by_key(key: str) -> Block`, `asked_blocks(level: int) -> tuple[Block, ...]`,
    `options_for(key: str) -> tuple[str, ...]`
  - `@dataclass(frozen=True) Progress(given: tuple[tuple[str, int], ...] = ())`
    с `step`, `level`, `answers`, `next_key()`, `answer(index)`, `back()`,
    `position()`, `final_answers()`
  - `apply_answers(conn, answers, *, level=None, now=None, settings=None)`
  - `is_completed(conn) -> bool` (без изменений по смыслу)
  - **временно** `SurveyOption`, `SurveyQuestion`, `SURVEY_QUESTIONS` — мост для
    старого потока бота; удаляется в 22.3.

- [ ] **Step 1: Переписать тесты `tests/test_survey.py`**

Заменить шапку и тесты, которые опираются на `SURVEY_QUESTIONS`/кортежи
`BLOCKS`, и добавить тесты ветвления. Итоговый файл:

```python
"""Тесты анкеты холодного старта (срез 22: адаптивная анкета)."""

import pytest

from llm_tutor.course.graph import CourseGraph
from llm_tutor.course.seed import load_seed
from llm_tutor.db import repos
from llm_tutor.student import beta, self_report, survey
from llm_tutor.student.survey import Progress

FIRST = survey.BLOCKS[0]
BLOCK_KEYS = [block.key for block in survey.BLOCKS]


# --- состав анкеты ---


def test_five_blocks_and_four_levels() -> None:
    assert len(survey.BLOCKS) == 5
    assert len(survey.SELF_LEVELS) == len(survey.PRIOR_LEVELS) == 4


def test_level_labels_are_short_and_neutral() -> None:
    """Подписи влезают в сетку 2×2 и не содержат «(а)»."""
    assert all("(" not in label and len(label) <= 14 for label in survey.SELF_LEVELS)


def test_confident_label_is_kept_verbatim() -> None:
    """По подписи «Уверенно» узнаются заявленные блоки — менять её нельзя."""
    assert survey.SELF_LEVELS[survey.CONFIDENT_INDEX] == "Уверенно"


def test_survey_blocks_cover_every_node(conn) -> None:
    """Блоки накрывают все узлы курса: априор достаётся каждому."""
    load_seed(conn)
    covered = {concept for block in survey.BLOCKS for concept in block.concepts}

    assert covered == set(CourseGraph.load(conn).node_ids)


def test_every_block_has_question_and_example() -> None:
    assert all(block.question.endswith("?") and block.example for block in survey.BLOCKS)


# --- ветвление ---


def _walk(progress: Progress, index: int) -> tuple[Progress, list[str]]:
    """Отвечает ``index`` на каждый вопрос по блокам, пока анкета не кончится."""
    asked = []
    while (key := progress.next_key()) is not None:
        asked.append(key)
        progress = progress.answer(index)
    return progress, asked


def test_survey_starts_with_level_question() -> None:
    assert Progress().next_key() == survey.LEVEL_KEY


def test_from_scratch_ends_after_one_answer() -> None:
    progress = Progress().answer(survey.LEVEL_FROM_SCRATCH)

    assert progress.next_key() is None
    assert progress.final_answers() == {key: survey.UNKNOWN_INDEX for key in BLOCK_KEYS}


def test_confident_asks_only_last_two_blocks() -> None:
    progress, asked = _walk(Progress().answer(survey.LEVEL_CONFIDENT), 1)

    assert asked == ["block_selection", "block_analysis"]
    final = progress.final_answers()
    assert all(final[key] == survey.CONFIDENT_INDEX for key in survey.ASSUMED_BY_CONFIDENT)
    assert final["block_selection"] == final["block_analysis"] == 1


def test_some_asks_every_block_in_order() -> None:
    _, asked = _walk(Progress().answer(survey.LEVEL_SOME), 2)

    assert asked == BLOCK_KEYS


@pytest.mark.parametrize("foundation", survey.FOUNDATION)
def test_unknown_foundation_ends_survey(foundation: str) -> None:
    """«Впервые вижу» на фундаменте — дальше всё незнакомо, анкета кончилась."""
    progress = Progress().answer(survey.LEVEL_SOME)
    while progress.next_key() != foundation:
        progress = progress.answer(2)
    progress = progress.answer(survey.UNKNOWN_INDEX)

    assert progress.next_key() is None
    final = progress.final_answers()
    after = BLOCK_KEYS[BLOCK_KEYS.index(foundation) + 1 :]
    assert all(final[key] == survey.UNKNOWN_INDEX for key in after)


def test_unknown_outside_foundation_keeps_asking() -> None:
    """Блоки 3–5 друг от друга не зависят: «Впервые вижу» анкету не обрывает."""
    progress = Progress().answer(survey.LEVEL_SOME).answer(2).answer(2)
    progress = progress.answer(survey.UNKNOWN_INDEX)  # block_loading

    assert progress.next_key() == "block_selection"


def test_back_removes_last_answer() -> None:
    progress = Progress().answer(survey.LEVEL_SOME).answer(3)

    back = progress.back()

    assert back.next_key() == "block_python"
    assert back.answers == {}
    assert back.back().next_key() == survey.LEVEL_KEY


def test_position_counts_block_questions_only() -> None:
    assert Progress().position() is None
    assert Progress().answer(survey.LEVEL_SOME).position() == (1, 5)
    assert Progress().answer(survey.LEVEL_CONFIDENT).answer(1).position() == (2, 2)


def test_answer_out_of_range_raises() -> None:
    with pytest.raises(ValueError):
        Progress().answer(len(survey.LEVEL_OPTIONS))


def test_answer_after_end_raises() -> None:
    with pytest.raises(ValueError):
        Progress().answer(survey.LEVEL_FROM_SCRATCH).answer(0)


def test_progress_is_immutable() -> None:
    progress = Progress()
    progress.answer(survey.LEVEL_SOME)

    assert progress.step == 0


# --- запись ---


def test_apply_answers_writes_facts(conn, settings) -> None:
    load_seed(conn)

    survey.apply_answers(conn, {FIRST.key: 1}, now=0.0, settings=settings)

    assert repos.get_fact(conn, FIRST.key) == survey.SELF_LEVELS[1]


def test_apply_answers_writes_level_fact(conn, settings) -> None:
    load_seed(conn)

    survey.apply_answers(
        conn, {FIRST.key: 1}, level=survey.LEVEL_SOME, now=0.0, settings=settings
    )

    assert repos.get_fact(conn, survey.LEVEL_KEY) == survey.LEVEL_OPTIONS[survey.LEVEL_SOME]


def test_prior_uses_levels(conn, settings) -> None:
    """Индекс ответа задаёт силу априора, а не только «знаю / не знаю»."""
    load_seed(conn)

    survey.apply_answers(conn, {FIRST.key: 1}, now=0.0, settings=settings)

    assert repos.get_events(conn)[0].result == pytest.approx(survey.PRIOR_LEVELS[1])


def test_prior_covers_every_node_of_block(conn, settings) -> None:
    """Один ответ накрывает весь блок узлов."""
    load_seed(conn)

    survey.apply_answers(conn, {FIRST.key: 3}, now=0.0, settings=settings)

    assert {event.concept_id for event in repos.get_events(conn)} == set(FIRST.concepts)


def test_level_fact_gives_no_prior(conn, settings) -> None:
    """Общий уровень — только факт: априор дают ответы по блокам."""
    load_seed(conn)

    survey.apply_answers(conn, {}, level=survey.LEVEL_CONFIDENT, now=0.0, settings=settings)

    assert repos.get_events(conn) == []


def test_prior_events_are_labelled_as_self(conn, settings) -> None:
    """Анкета пишет тот же тип свидетельства, что и «я это знаю»."""
    load_seed(conn)

    survey.apply_answers(conn, {FIRST.key: 1}, now=0.0, settings=settings)

    assert {event.source for event in repos.get_events(conn)} == {"self"}


def test_invalid_option_index_raises(conn, settings) -> None:
    load_seed(conn)

    with pytest.raises(ValueError):
        survey.apply_answers(conn, {FIRST.key: 99}, now=0.0, settings=settings)


def test_invalid_level_raises(conn, settings) -> None:
    load_seed(conn)

    with pytest.raises(ValueError):
        survey.apply_answers(conn, {}, level=99, now=0.0, settings=settings)


def test_unknown_question_key_raises(conn, settings) -> None:
    load_seed(conn)

    with pytest.raises(KeyError):
        survey.apply_answers(conn, {"нет_такого_блока": 0}, now=0.0, settings=settings)


def test_reapplying_survey_does_not_double_prior(conn, settings) -> None:
    """Повторное прохождение факты перезаписывает, априор — нет."""
    load_seed(conn)
    survey.apply_answers(conn, {FIRST.key: 3}, now=0.0, settings=settings)
    before = len(repos.get_events(conn))

    survey.apply_answers(conn, {FIRST.key: 3}, now=1.0, settings=settings)

    assert len(repos.get_events(conn)) == before


def test_is_completed_needs_every_answer(conn, settings) -> None:
    """Анкета считается пройденной только целиком."""
    load_seed(conn)

    assert survey.is_completed(conn) is False

    survey.apply_answers(conn, {FIRST.key: 2}, now=0.0, settings=settings)
    assert survey.is_completed(conn) is False

    survey.apply_answers(
        conn, Progress().answer(survey.LEVEL_FROM_SCRATCH).final_answers(), settings=settings
    )
    assert survey.is_completed(conn) is True


def test_self_evidence_moves_mastery_weakly(conn, settings) -> None:
    """Самооценка — слабое свидетельство: владение растёт, но не до порога."""
    load_seed(conn)  # событие ссылается на концепт — он должен быть в графе
    self_report.apply(conn, "read_csv", correct=True, now=1.0, settings=settings)

    mastery = beta.estimate(conn, "read_csv", now=1.0, settings=settings)
    assert mastery.mean > 0.5
    assert mastery.mean < settings.mastery_verify_threshold
    assert [event.source for event in repos.get_events(conn)] == ["self"]
```

`test_onboarding_texts_exist` (про `start.INTRO_TEXT`) из этого файла
**переезжает** в 22.4 — здесь его не будет: тексты входа тестирует
`test_bot_flows.py`.

- [ ] **Step 2: Запустить — должно упасть**

Run: `uv run pytest tests/test_survey.py -q`
Expected: FAIL — `ImportError: cannot import name 'Progress'`.

- [ ] **Step 3: Переписать `src/llm_tutor/student/survey.py`**

```python
"""Анкета холодного старта: профиль в ``facts`` + слабый априор.

Модель ученика стартует с априора Beta(1,1) — «не знаем». Анкета не заменяет
диагностику, а даёт лишь слабые свидетельства ``source='self'``: их вес мал
(``self_evidence_weight``), чтобы самооценка не подменяла реальные задания.

Анкета адаптивная (срез 22): сначала общий уровень, потом только нужные
вопросы по блокам темы. Ветвление — чистые функции без aiogram: бот только
рисует то, что решено здесь.
"""

import sqlite3
import time
from dataclasses import dataclass

from llm_tutor.config import Settings, get_settings
from llm_tutor.db import repos
from llm_tutor.student import self_report

# Ключ факта цели: анкетой не заполняется, но маршрут его читает.
GOAL_CONCEPT_KEY = "goal_concept_id"

# Градации самооценки по блоку — общие для всех вопросов; короткие, чтобы
# влезать в сетку 2×2. Подпись «Уверенно» менять нельзя: по ней узнаются
# заявленные блоки (в т.ч. у прошедших анкету раньше).
SELF_LEVELS: tuple[str, ...] = (
    "Впервые вижу",
    "Знаю в теории",
    "С подсказками",
    "Уверенно",
)
# Во что превращается индекс ответа: 0 — «впервые вижу», 3 — «уверенно».
PRIOR_LEVELS: tuple[float, ...] = (0.0, 0.3, 0.65, 1.0)
UNKNOWN_INDEX = 0
CONFIDENT_INDEX = 3

# Вопрос об общем уровне: решает, какие вопросы по блокам задавать.
LEVEL_KEY = "survey_level"
LEVEL_QUESTION = "Для начала — как у тебя с Python и pandas?"
LEVEL_OPTIONS: tuple[str, ...] = ("С нуля", "Немного знаю", "Уверенно работаю с pandas")
LEVEL_FROM_SCRATCH, LEVEL_SOME, LEVEL_CONFIDENT = 0, 1, 2


@dataclass(frozen=True)
class Block:
    """Блок темы: ключ факта, имя для сводки, вопрос, пример API, узлы."""

    key: str
    title: str
    question: str
    example: str
    concepts: tuple[str, ...]


BLOCKS: tuple[Block, ...] = (
    Block(
        "block_python",
        "Python и NumPy",
        "Пишешь на Python и NumPy?",
        "списки, функции, np.array",
        ("python_basics", "numpy_basics"),
    ),
    Block(
        "block_tables",
        "Таблицы pandas",
        "Создаёшь Series и DataFrame?",
        "индекс, столбцы, pd.DataFrame",
        ("pandas_intro", "pandas_series", "pandas_dataframe"),
    ),
    Block(
        "block_loading",
        "Чтение и осмотр",
        "Загружаешь и осматриваешь данные?",
        "read_csv, info, describe, astype",
        ("read_csv", "df_inspect", "describe_stats", "dtype_conversion"),
    ),
    Block(
        "block_selection",
        "Выборка и сортировка",
        "Фильтруешь и сортируешь таблицы?",
        "loc / iloc, условия, sort_values, value_counts",
        (
            "indexing_loc_iloc",
            "boolean_indexing",
            "sorting",
            "value_counts",
            "df_transformations",
        ),
    ),
    Block(
        "block_analysis",
        "Группировки и EDA",
        "Группируешь и исследуешь данные?",
        "apply, groupby + agg, pivot_table, графики",
        (
            "apply_functions",
            "groupby",
            "agg_functions",
            "summary_tables",
            "visualization_basics",
            "eda_workflow",
            "churn_eda_case",
        ),
    ),
)

# «Уверенно работаю с pandas»: эти блоки знакомы без вопроса.
ASSUMED_BY_CONFIDENT: tuple[str, ...] = ("block_python", "block_tables", "block_loading")
# Фундамент: все узлы блоков 3–5 жёстко зависят от pandas_dataframe, а он — от
# Python. «Впервые вижу» здесь — дальше всё тоже незнакомо (ранний выход).
FOUNDATION: tuple[str, ...] = ("block_python", "block_tables")


def block_by_key(key: str) -> Block:
    """Блок по ключу факта (``KeyError``, если такого нет)."""
    for block in BLOCKS:
        if block.key == key:
            return block
    raise KeyError(f"Нет блока анкеты с ключом {key!r}")


def asked_blocks(level: int) -> tuple[Block, ...]:
    """Блоки, о которых спрашиваем на ветке общего уровня ``level``."""
    if level == LEVEL_FROM_SCRATCH:
        return ()
    if level == LEVEL_CONFIDENT:
        return tuple(block for block in BLOCKS if block.key not in ASSUMED_BY_CONFIDENT)
    return BLOCKS


def options_for(key: str) -> tuple[str, ...]:
    """Варианты ответа на вопрос с ключом ``key``."""
    return LEVEL_OPTIONS if key == LEVEL_KEY else SELF_LEVELS


@dataclass(frozen=True)
class Progress:
    """Ход анкеты: ответы в том порядке, в котором их дали (для «Назад»).

    Неизменяемый: каждый ответ и «Назад» дают новый объект — FSM хранит
    ``given``, а не мутирует общее состояние.
    """

    given: tuple[tuple[str, int], ...] = ()

    @property
    def step(self) -> int:
        """Сколько ответов дано — номер текущего вопроса (с нуля)."""
        return len(self.given)

    @property
    def level(self) -> int | None:
        """Ответ на вопрос об общем уровне (``None`` — ещё не дан)."""
        return dict(self.given).get(LEVEL_KEY)

    @property
    def answers(self) -> dict[str, int]:
        """Ответы по блокам, данные учеником (без достроенных)."""
        return {key: index for key, index in self.given if key != LEVEL_KEY}

    def next_key(self) -> str | None:
        """Ключ следующего вопроса; ``None`` — анкета кончилась."""
        level = self.level
        if level is None:
            return LEVEL_KEY
        answers = self.answers
        if any(answers.get(key) == UNKNOWN_INDEX for key in FOUNDATION):
            return None
        for block in asked_blocks(level):
            if block.key not in answers:
                return block.key
        return None

    def answer(self, index: int) -> "Progress":
        """Новый ход анкеты с ответом ``index`` на текущий вопрос."""
        key = self.next_key()
        if key is None:
            raise ValueError("Анкета уже кончилась — отвечать не на что")
        if not 0 <= index < len(options_for(key)):
            raise ValueError(f"Нет варианта {index} у вопроса {key!r}")
        return Progress(given=(*self.given, (key, index)))

    def back(self) -> "Progress":
        """Новый ход анкеты без последнего ответа."""
        return Progress(given=self.given[:-1])

    def position(self) -> tuple[int, int] | None:
        """(номер, всего) для вопроса по блоку; у вопроса об уровне — ``None``."""
        key = self.next_key()
        if key is None or key == LEVEL_KEY:
            return None
        return len(self.answers) + 1, len(asked_blocks(self.level))

    def final_answers(self) -> dict[str, int]:
        """Ответы на все пять блоков: данные учеником плюс достроенные правилом."""
        answers = self.answers
        result: dict[str, int] = {}
        for block in BLOCKS:
            if block.key in answers:
                result[block.key] = answers[block.key]
            elif self.level == LEVEL_CONFIDENT and block.key in ASSUMED_BY_CONFIDENT:
                result[block.key] = CONFIDENT_INDEX
            else:
                # «С нуля» или ранний выход на фундаменте — блок незнаком.
                result[block.key] = UNKNOWN_INDEX
        return result


def apply_answers(
    conn: sqlite3.Connection,
    answers: dict[str, int],
    *,
    level: int | None = None,
    now: float | None = None,
    settings: Settings | None = None,
) -> None:
    """Сохраняет ответы анкеты в ``facts`` и раздаёт слабый априор по блокам.

    ``answers`` — ключ блока → индекс в ``SELF_LEVELS``; ``level`` — ответ на
    вопрос об общем уровне (только факт, априора не даёт). Повторное
    применение факты перезаписывает, но априор **не** начисляет заново:
    иначе самооценка накрутила бы счётчики.
    """
    s = settings or get_settings()
    stamp = time.time() if now is None else now

    if level is not None:
        if not 0 <= level < len(LEVEL_OPTIONS):
            raise ValueError(f"Нет варианта {level} у вопроса об уровне")
        repos.set_fact(conn, LEVEL_KEY, LEVEL_OPTIONS[level], source="self")

    for key, index in answers.items():
        block = block_by_key(key)
        if not 0 <= index < len(SELF_LEVELS):
            raise ValueError(f"Нет варианта {index} у блока {key!r}")
        # Априор блока начисляем, только когда ответ на него звучит впервые:
        # иначе повторное прохождение анкеты накрутило бы самооценку.
        fresh = repos.get_fact(conn, key) is None
        repos.set_fact(conn, key, SELF_LEVELS[index], source="self")
        if fresh:
            for concept_id in block.concepts:
                self_report.apply(
                    conn,
                    concept_id,
                    correct=PRIOR_LEVELS[index],
                    now=stamp,
                    settings=s,
                    commit=False,
                )
    conn.commit()


def is_completed(conn: sqlite3.Connection) -> bool:
    """Прошёл ли ученик анкету: ответы есть на все блоки."""
    return all(repos.get_fact(conn, block.key) is not None for block in BLOCKS)


# --- Временный мост для старого потока бота (удаляется в задаче 22.3) ---


@dataclass(frozen=True)
class SurveyOption:
    """Вариант ответа анкеты (старый поток бота)."""

    label: str


@dataclass(frozen=True)
class SurveyQuestion:
    """Вопрос анкеты (старый поток бота)."""

    key: str
    text: str
    options: tuple[SurveyOption, ...]


SURVEY_QUESTIONS: tuple[SurveyQuestion, ...] = tuple(
    SurveyQuestion(
        key=block.key,
        text=block.question,
        options=tuple(SurveyOption(label) for label in SELF_LEVELS),
    )
    for block in BLOCKS
)
```

- [ ] **Step 4: Запустить набор**

Run: `uv run pytest -q`
Expected: всё зелёное. Если падает что-то вне `test_survey.py`, причина — кто-то
читал `survey.BLOCKS` как кортеж или `question_by_key`: поправить вызов на
`block.key` / `block.concepts` / `survey.block_by_key`.

- [ ] **Step 5: Commit**

```bash
git add src/llm_tutor/student/survey.py tests/test_survey.py
git commit -m "Срез 22: адаптивная анкета — ветвление чистыми функциями"
```

---

### Task 22.2: Фейки сообщений и вид сообщения анкеты

**Files:**
- Modify: `tests/fakes.py:24-48` (`FakeMessage`, `FakeCallback`)
- Modify: `src/llm_tutor/bot/survey.py` (добавить функции вида; старый поток
  пока не трогать)
- Create: `tests/test_survey_view.py`

**Interfaces:**
- Consumes: `survey.Progress`, `survey.LEVEL_*`, `survey.SELF_LEVELS`,
  `survey.block_by_key`, `survey.BLOCKS` (22.1); `start.INTRO_TEXT`.
- Produces (в `bot/survey.py`):
  - `GO = "go"`, `BACK = "back"`, `GO_DATA = "survey:go"`,
    `GO_LABEL = "▶️ Поехали"`, `BACK_LABEL = "‹ Назад"`
  - `intro_view() -> tuple[str, InlineKeyboardMarkup]`
  - `question_view(progress: survey.Progress) -> tuple[str, InlineKeyboardMarkup]`
    — готовый HTML
  - `summary_text(answers: Mapping[str, int]) -> str` — готовый HTML
- Produces (в `tests/fakes.py`): `FakeMessage.answer` возвращает новый
  `FakeMessage` (отправленное сообщение) со своим `message_id`;
  `FakeMessage.edit_text`, `.edit_reply_markup`, `.reply_markup`, `.edits`,
  `.log`, `.bot = None`, `.chat.id == 1`; `FakeCallback.answer(text=None,
  show_alert=False)` уступает цикл и пишет `answer_text`, а в
  `message.log` — `"callback"`.

- [ ] **Step 1: Обновить `tests/fakes.py`**

Заменить классы `FakeMessage` и `FakeCallback` (импорты дополнить
`asyncio`, `itertools`, `from types import SimpleNamespace`):

```python
class FakeMessage:
    """Подставное сообщение: помнит всё, что бот в него отправил или поправил.

    ``answer`` возвращает новое подставное сообщение — как настоящий
    ``Message.answer``: у отправленного свой ``message_id``, по нему анкета
    привязывает нажатия. ``log`` — порядок действий (``answer`` / ``edit`` /
    ``callback``), чтобы проверять, что «часики» гаснут раньше правки.
    """

    _ids = itertools.count(1)

    def __init__(self, text: str = "", *, log: list[str] | None = None) -> None:
        self.text = text
        self.message_id = next(FakeMessage._ids)
        self.sent: list[tuple[str, InlineKeyboardMarkup | None]] = []
        self.edits: list[tuple[str, InlineKeyboardMarkup | None]] = []
        self.reply_markup = None
        self.log: list[str] = [] if log is None else log
        self.bot = None
        self.chat = SimpleNamespace(id=1)

    async def answer(self, text: str, reply_markup=None, **kwargs) -> "FakeMessage":
        self.sent.append((text, reply_markup))
        self.log.append("answer")
        sent = FakeMessage(text, log=self.log)
        sent.reply_markup = reply_markup
        return sent

    async def edit_text(self, text: str, reply_markup=None, **kwargs) -> "FakeMessage":
        self.edits.append((text, reply_markup))
        self.log.append("edit")
        self.text = text
        self.reply_markup = reply_markup
        return self

    async def edit_reply_markup(self, reply_markup=None, **kwargs) -> "FakeMessage":
        self.reply_markup = reply_markup
        return self

    @property
    def last_text(self) -> str:
        return self.sent[-1][0]


class FakeCallback:
    """Подставное нажатие инлайн-кнопки."""

    def __init__(self, data: str, message: FakeMessage) -> None:
        self.data = data
        self.message = message
        self.answered = False
        self.answer_text: str | None = None

    async def answer(self, text: str | None = None, show_alert: bool = False, **kwargs) -> None:
        # Уступаем цикл, как настоящий сетевой вызов: тесты гонок проверяют,
        # что решение по нажатию принято ДО него.
        await asyncio.sleep(0)
        self.answered = True
        self.answer_text = text
        self.message.log.append("callback")
```

- [ ] **Step 2: Написать тесты вида `tests/test_survey_view.py`**

```python
"""Вид сообщения анкеты: вопросы, сетка вариантов, «Назад», сводка."""

from llm_tutor.bot import start
from llm_tutor.bot import survey as survey_bot
from llm_tutor.student import survey
from llm_tutor.student.survey import Progress


def _labels(markup) -> list[list[str]]:
    return [[button.text for button in row] for row in markup.inline_keyboard]


def test_intro_view_has_single_go_button() -> None:
    text, markup = survey_bot.intro_view()

    assert text == start.INTRO_TEXT
    assert _labels(markup) == [[survey_bot.GO_LABEL]]
    assert markup.inline_keyboard[0][0].callback_data == survey_bot.GO_DATA


def test_level_question_has_one_option_per_row_and_no_back() -> None:
    text, markup = survey_bot.question_view(Progress())

    assert survey.LEVEL_QUESTION in text
    assert _labels(markup) == [[label] for label in survey.LEVEL_OPTIONS]


def test_block_question_is_two_by_two_grid_with_back() -> None:
    _, markup = survey_bot.question_view(Progress().answer(survey.LEVEL_SOME))

    assert _labels(markup) == [
        list(survey.SELF_LEVELS[:2]),
        list(survey.SELF_LEVELS[2:]),
        [survey_bot.BACK_LABEL],
    ]


def test_block_question_shows_progress_question_and_example() -> None:
    text, _ = survey_bot.question_view(Progress().answer(survey.LEVEL_SOME))

    first = survey.BLOCKS[0]
    assert "Вопрос 1 из 5" in text
    assert "▰▱▱▱▱" in text
    assert f"<b>{first.question}</b>" in text
    assert first.example in text


def test_callback_data_carries_step() -> None:
    progress = Progress().answer(survey.LEVEL_SOME).answer(2)
    _, markup = survey_bot.question_view(progress)

    data = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert all(item.startswith(f"survey:{progress.step}:") for item in data)
    assert data[-1] == f"survey:{progress.step}:{survey_bot.BACK}"


def test_summary_lists_every_block() -> None:
    answers = Progress().answer(survey.LEVEL_FROM_SCRATCH).final_answers()

    text = survey_bot.summary_text(answers)

    assert text.startswith("✅ Понял тебя")
    assert all(f"• {block.title} — впервые вижу" in text for block in survey.BLOCKS)
```

- [ ] **Step 3: Запустить — должно упасть**

Run: `uv run pytest tests/test_survey_view.py -q`
Expected: FAIL — `AttributeError: module 'llm_tutor.bot.survey' has no attribute 'intro_view'`.

- [ ] **Step 4: Добавить функции вида в `src/llm_tutor/bot/survey.py`**

После `BUTTON_HINT_REPLY` добавить константы, после `_keyboard` — функции
(импорты: `from collections.abc import Mapping`, `from llm_tutor.bot import
render, start`):

```python
GO = "go"
BACK = "back"
GO_DATA = f"{CALLBACK_PREFIX}:{GO}"
GO_LABEL = "▶️ Поехали"
BACK_LABEL = "‹ Назад"


def _button(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _data(step: int, choice: int | str) -> str:
    """``callback_data`` с номером шага: клик по старой клавиатуре узнаваем."""
    return f"{CALLBACK_PREFIX}:{step}:{choice}"


def intro_view() -> tuple[str, InlineKeyboardMarkup]:
    """Приветствие с единственной кнопкой «▶️ Поехали»."""
    return start.INTRO_TEXT, InlineKeyboardMarkup(
        inline_keyboard=[[_button(GO_LABEL, GO_DATA)]]
    )


def question_view(progress: survey.Progress) -> tuple[str, InlineKeyboardMarkup]:
    """Текущий вопрос (готовый HTML) и его клавиатура.

    Вызывать только на незаконченной анкете: у законченной вопроса нет.
    """
    key = progress.next_key()
    step = progress.step
    if key == survey.LEVEL_KEY:
        text = render.escape(survey.LEVEL_QUESTION)
        rows = [
            [_button(label, _data(step, index))]
            for index, label in enumerate(survey.LEVEL_OPTIONS)
        ]
    else:
        block = survey.block_by_key(key)
        number, total = progress.position()
        bar = "▰" * number + "▱" * (total - number)
        text = (
            f"Вопрос {number} из {total}  {bar}\n\n"
            f"<b>{render.escape(block.question)}</b>\n{render.escape(block.example)}"
        )
        buttons = [
            _button(label, _data(step, index))
            for index, label in enumerate(survey.SELF_LEVELS)
        ]
        rows = [buttons[:2], buttons[2:]]
    if step > 0:
        rows.append([_button(BACK_LABEL, _data(step, BACK))])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def summary_text(answers: Mapping[str, int]) -> str:
    """Сводка «как я тебя понял» (готовый HTML) — финал сообщения анкеты."""
    lines = [
        f"• {render.escape(block.title)} — {survey.SELF_LEVELS[answers[block.key]].lower()}"
        for block in survey.BLOCKS
    ]
    return "✅ Понял тебя:\n" + "\n".join(lines)
```

- [ ] **Step 5: Запустить набор**

Run: `uv run pytest -q`
Expected: всё зелёное (старые тесты пользуются `FakeMessage.sent`,
`FakeCallback.answered` — они не изменились).

- [ ] **Step 6: Commit**

```bash
git add tests/fakes.py tests/test_survey_view.py src/llm_tutor/bot/survey.py
git commit -m "Срез 22: вид сообщения анкеты — сетка 2×2, прогресс, «Назад», сводка"
```

---

### Task 22.3: Поток анкеты в одном сообщении

**Files:**
- Modify: `src/llm_tutor/bot/survey.py` (весь файл — итог ниже)
- Modify: `src/llm_tutor/student/survey.py` (удалить мост в конце файла)
- Modify: `src/llm_tutor/bot/handlers.py:28,213-219` (`ask_survey` →
  `start_survey`)
- Modify: `tests/test_bot_flows.py` (секция «анкета», онбординг, финал),
  `tests/test_e2e_topic01.py` (новый ученик)

**Interfaces:**
- Consumes: всё из 22.1–22.2; `start.begin_lesson(message, state, conn, client,
  model, settings)` (без изменений).
- Produces (в `bot/survey.py`):
  - `async start_survey(message: Message, state: FSMContext) -> Message` — шлёт
    приветствие, ставит `SurveyFlow.question`, FSM-данные
    `{"message_id": <id приветствия>, "given": None}`; возвращает отправленное
    сообщение
  - `async safe_edit(message, text, markup) -> Message`
  - хендлер `on_survey_click` (имя важно — тесты ищут его через `_named`)
  - `STALE_CLICK_TOAST = "Этот вопрос уже позади"`,
    `DONE_TOAST = "Анкета уже пройдена"`,
    `BUTTON_HINT_REPLY = "Ответь кнопкой в сообщении выше 👆"`
  - удалены `ask`, `_keyboard`

- [ ] **Step 1: Переписать тесты анкеты в `tests/test_bot_flows.py`**

Импорты: убрать `from llm_tutor.bot.survey import ask as ask_survey`,
добавить `import asyncio`, `from aiogram.exceptions import TelegramBadRequest`,
`from llm_tutor.bot import survey as survey_bot`,
`from llm_tutor.bot.survey import SurveyFlow, start_survey`,
`from llm_tutor.llm.prompts import BUSY_REPLY`.

`_complete_survey` (строки 23–26):

```python
def _complete_survey(conn) -> None:
    """Профиль заполнен: без этого свободный текст упирается в приглашение."""
    for block in survey.BLOCKS:
        repos.set_fact(conn, block.key, survey.SELF_LEVELS[3], source="self")
```

Во всех местах `{question.key: 1 for question in survey.SURVEY_QUESTIONS}`
(строки ~506–508, ~577–579) заменить на `{block.key: 1 for block in
survey.BLOCKS}`.

Тесты `test_survey_asks_first_question_with_buttons`,
`test_survey_writes_profile_after_last_answer`,
`test_survey_goes_through_all_questions` (строки 37–76) заменить на:

```python
async def _begin(conn, settings, client=None):
    """Приветствие анкеты и хендлер нажатий."""
    router = make_survey_router(conn, settings, client or _TutorClient(), "m")
    state = _fsm()
    intro = await start_survey(FakeMessage(), state)
    return intro, state, _named(router, "callback_query", "on_survey_click")


async def _tap(click, message, state, data: str) -> FakeCallback:
    callback = FakeCallback(data, message)
    await click(callback, state)
    return callback


def _data_of(message, label: str) -> str:
    """``callback_data`` кнопки с подписью ``label`` в клавиатуре сообщения."""
    for row in message.reply_markup.inline_keyboard:
        for button in row:
            if button.text == label:
                return button.callback_data
    raise AssertionError(f"нет кнопки {label!r}")


async def _press(click, message, state, label: str) -> FakeCallback:
    return await _tap(click, message, state, _data_of(message, label))


async def test_start_survey_sends_intro_with_go_button(conn, settings) -> None:
    message = FakeMessage()
    state = _fsm()

    await start_survey(message, state)

    text, markup = message.sent[-1]
    assert text == start.INTRO_TEXT
    assert markup.inline_keyboard[0][0].callback_data == survey_bot.GO_DATA
    assert await state.get_state() == SurveyFlow.question.state


async def test_go_turns_intro_into_level_question(conn, settings) -> None:
    """«Поехали» правит то же сообщение — нового не приходит."""
    load_seed(conn)
    intro, state, click = await _begin(conn, settings)

    await _press(click, intro, state, survey_bot.GO_LABEL)

    assert survey.LEVEL_QUESTION in intro.text
    assert intro.sent == []


async def test_answer_edits_same_message_to_next_question(conn, settings) -> None:
    load_seed(conn)
    intro, state, click = await _begin(conn, settings)
    await _press(click, intro, state, survey_bot.GO_LABEL)

    await _press(click, intro, state, survey.LEVEL_OPTIONS[survey.LEVEL_SOME])

    assert "Вопрос 1 из 5" in intro.text
    assert intro.sent == []


async def test_callback_is_answered_before_edit(conn, settings) -> None:
    """«Часики» гаснут сразу, правка сообщения — после."""
    load_seed(conn)
    intro, state, click = await _begin(conn, settings)
    intro.log.clear()

    await _press(click, intro, state, survey_bot.GO_LABEL)

    assert intro.log == ["callback", "edit"]


async def test_stale_step_click_writes_nothing(conn, settings) -> None:
    """Клик по клавиатуре прошлого шага — тост, ответ не записан."""
    load_seed(conn)
    intro, state, click = await _begin(conn, settings)
    await _press(click, intro, state, survey_bot.GO_LABEL)
    old = _data_of(intro, survey.LEVEL_OPTIONS[survey.LEVEL_SOME])
    await _tap(click, intro, state, old)

    callback = await _tap(click, intro, state, old)

    assert callback.answer_text == survey_bot.STALE_CLICK_TOAST
    assert (await state.get_data())["given"] == [[survey.LEVEL_KEY, survey.LEVEL_SOME]]


async def test_click_on_previous_survey_message_is_stale(conn, settings) -> None:
    """После повторного /start старое сообщение анкеты новое не сбивает."""
    load_seed(conn)
    intro, state, click = await _begin(conn, settings)
    await _press(click, intro, state, survey_bot.GO_LABEL)
    await start_survey(FakeMessage(), state)  # /start посреди анкеты

    callback = await _press(click, intro, state, survey.LEVEL_OPTIONS[0])

    assert callback.answer_text == survey_bot.STALE_CLICK_TOAST
    assert (await state.get_data())["given"] is None  # новая анкета не тронута
    assert survey.is_completed(conn) is False


async def test_double_tap_on_last_answer_starts_lesson_once(conn, settings) -> None:
    load_seed(conn)
    intro, state, click = await _begin(conn, settings)
    await _press(click, intro, state, survey_bot.GO_LABEL)
    data = _data_of(intro, survey.LEVEL_OPTIONS[survey.LEVEL_FROM_SCRATCH])
    first, second = FakeCallback(data, intro), FakeCallback(data, intro)

    await asyncio.gather(click(first, state), click(second, state))

    assert len([text for text, _ in intro.sent if text.startswith("📋")]) == 1
    assert first.answered and second.answered  # «часики» не висят ни у кого


async def test_back_returns_to_previous_question(conn, settings) -> None:
    load_seed(conn)
    intro, state, click = await _begin(conn, settings)
    await _press(click, intro, state, survey_bot.GO_LABEL)
    await _press(click, intro, state, survey.LEVEL_OPTIONS[survey.LEVEL_SOME])
    await _press(click, intro, state, survey.SELF_LEVELS[3])
    assert "Вопрос 2 из 5" in intro.text

    await _press(click, intro, state, survey_bot.BACK_LABEL)

    assert "Вопрос 1 из 5" in intro.text
    assert (await state.get_data())["given"] == [[survey.LEVEL_KEY, survey.LEVEL_SOME]]


async def test_garbage_choice_is_ignored(conn, settings) -> None:
    load_seed(conn)
    intro, state, click = await _begin(conn, settings)
    await _press(click, intro, state, survey_bot.GO_LABEL)

    callback = await _tap(click, intro, state, "survey:0:99")

    assert callback.answer_text == survey_bot.STALE_CLICK_TOAST
    assert (await state.get_data())["given"] == []


async def test_finish_writes_profile_shows_summary_and_starts_lesson(conn, settings) -> None:
    load_seed(conn)
    intro, state, click = await _begin(conn, settings)
    await _press(click, intro, state, survey_bot.GO_LABEL)
    await _press(click, intro, state, survey.LEVEL_OPTIONS[survey.LEVEL_CONFIDENT])
    await _press(click, intro, state, survey.SELF_LEVELS[2])
    await _press(click, intro, state, survey.SELF_LEVELS[1])

    assert repos.get_fact(conn, "block_python") == survey.SELF_LEVELS[3]
    assert repos.get_fact(conn, "block_analysis") == survey.SELF_LEVELS[1]
    assert repos.get_fact(conn, survey.LEVEL_KEY) == survey.LEVEL_OPTIONS[2]
    assert intro.text.startswith("✅ Понял тебя")
    assert intro.reply_markup is None  # у сводки кнопок нет
    assert any(text.startswith("📋") for text, _ in intro.sent)  # список шагов
    assert await state.get_state() is None


async def test_click_after_survey_done_toasts_and_drops_buttons(conn, settings) -> None:
    load_seed(conn)
    _complete_survey(conn)
    router = make_survey_router(conn, settings, _TutorClient(), "m")
    message = FakeMessage()
    message.reply_markup = survey_bot.intro_view()[1]

    callback = FakeCallback(survey_bot.GO_DATA, message)
    await _named(router, "callback_query", "on_survey_click")(callback, _fsm())

    assert callback.answer_text == survey_bot.DONE_TOAST
    assert message.reply_markup is None


async def test_click_after_lost_fsm_restarts_survey_in_place(conn, settings) -> None:
    """Рестарт бота потерял FSM: анкета начинается заново в этом же сообщении."""
    load_seed(conn)
    router = make_survey_router(conn, settings, _TutorClient(), "m")
    message = FakeMessage()
    state = _fsm()

    callback = FakeCallback("survey:3:1", message)
    await _named(router, "callback_query", "on_survey_click")(callback, state)

    assert callback.answered
    assert survey.LEVEL_QUESTION in message.text
    assert (await state.get_data())["message_id"] == message.message_id


async def test_click_during_other_flow_is_refused(conn, settings) -> None:
    load_seed(conn)
    router = make_survey_router(conn, settings, _TutorClient(), "m")
    state = _fsm()
    await state.set_state(DiagnosticFlow.answering)

    callback = FakeCallback(survey_bot.GO_DATA, FakeMessage())
    await _named(router, "callback_query", "on_survey_click")(callback, state)

    assert callback.answer_text == BUSY_REPLY
    assert await state.get_state() == DiagnosticFlow.answering.state


async def test_edit_failure_falls_back_to_new_message(conn, settings, monkeypatch) -> None:
    """Сообщение не править (старое/удалено) — вопрос приходит новым."""
    load_seed(conn)
    intro, state, click = await _begin(conn, settings)

    async def _cannot_edit(*args, **kwargs):
        raise TelegramBadRequest(method=None, message="Bad Request: message can't be edited")

    monkeypatch.setattr(intro, "edit_text", _cannot_edit)
    await _press(click, intro, state, survey_bot.GO_LABEL)

    assert survey.LEVEL_QUESTION in intro.last_text
    assert (await state.get_data())["message_id"] != intro.message_id
```

`test_survey_text_gets_button_hint` (строки ~153–162): заменить
`await ask_survey(FakeMessage(), state)` на `await start_survey(FakeMessage(),
state)`; хендлер текста брать через `_named(router, "message", "on_text")`.

`test_start_before_survey_sends_intro_then_question` и
`test_start_button_opens_survey` (строки ~391–414) заменить на:

```python
async def test_start_before_survey_sends_intro_with_go(conn, settings) -> None:
    """Первый /start: одно сообщение-приветствие с «▶️ Поехали»."""
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    message = FakeMessage()
    state = _fsm()

    await _named(router, "message", "on_start")(message, state)

    assert len(message.sent) == 1
    text, markup = message.sent[0]
    assert text == start.INTRO_TEXT
    assert markup.inline_keyboard[0][0].callback_data == survey_bot.GO_DATA


async def test_start_button_opens_survey(conn, settings) -> None:
    """Текст «▶️ Старт» (висит у старых учеников) — тот же вход, что /start."""
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    message = FakeMessage(start.START_LABEL)

    await _named(router, "message", "on_start_button")(message, _fsm())

    assert message.sent[0][0] == start.INTRO_TEXT
```

`test_survey_finish_shows_steps_and_starts_lesson` (строки ~738–760)
заменить на:

```python
async def test_survey_finish_shows_steps_and_starts_lesson(conn, settings) -> None:
    """Финал анкеты: сводка в сообщении анкеты, список шагов и сразу урок."""
    load_seed(conn)
    intro, state, click = await _begin(conn, settings)
    await _press(click, intro, state, survey_bot.GO_LABEL)

    await _press(click, intro, state, survey.LEVEL_OPTIONS[survey.LEVEL_FROM_SCRATCH])

    steps_text = next(text for text, _ in intro.sent if "Ближайшие" in text)
    assert "1. " in steps_text
    assert intro.sent[-2][0]  # урок начался: объяснение первой темы
    assert intro.sent[-1][0]  # и сразу первое задание
    assert await state.get_state() is None
```

- [ ] **Step 2: Обновить E2E нового ученика (`tests/test_e2e_topic01.py`)**

Импорт `from llm_tutor.bot.survey import ask as ask_survey` заменить на
`from llm_tutor.bot.survey import GO_DATA, start_survey`. В
`test_e2e_full_cycle` (строка ~146) `{question.key: 1 for question in
survey.SURVEY_QUESTIONS}` → `{block.key: 1 for block in survey.BLOCKS}`.
Начало `test_new_student_goes_from_start_to_closed_node` (до строки `steps =
…`) заменить на:

```python
async def test_new_student_goes_from_start_to_closed_node(conn, settings) -> None:
    """/start → «Поехали» → «С нуля» → список шагов → объяснение → пара тестов → узел закрыт."""
    load_seed(conn)
    ingest_text(conn, "# T\n\n## Grouping\n\ngroupby aggregates rows\n", "u")
    router = make_survey_router(conn, settings, GradingTutor(conn, passed=True), "m")
    state = _fsm()
    message = await start_survey(FakeMessage(), state)
    click = _named(router, "callback_query", "on_survey_click")
    await click(FakeCallback(GO_DATA, message), state)
    from_scratch = message.reply_markup.inline_keyboard[0][0].callback_data
    await click(FakeCallback(from_scratch, message), state)
```

Остаток теста (`steps = next(...)` и дальше) не меняется: сообщения урока
уходят через `message.answer` того же сообщения анкеты.

- [ ] **Step 3: Запустить — должно упасть**

Run: `uv run pytest tests/test_bot_flows.py tests/test_e2e_topic01.py -q`
Expected: FAIL — `ImportError: cannot import name 'start_survey'`.

- [ ] **Step 4: Переписать `src/llm_tutor/bot/survey.py` целиком**

```python
"""Анкета холодного старта в Telegram: одно сообщение, которое правится на месте.

Приветствие, вопросы и сводка — одно сообщение: нажатие правит его, а не
шлёт новое, поэтому в истории не копятся живые клавиатуры. Ответы копятся в
FSM, в БД пишутся только в финале — анкету можно прервать на любом шаге.

Нажатие привязано к сообщению (``message_id`` в FSM) и к шагу (номер в
``callback_data``): клик по старой клавиатуре и двойной тап ничего не пишут.
"""

import logging
from collections.abc import Mapping

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from llm_tutor.bot import render, start
from llm_tutor.config import Settings
from llm_tutor.llm.client import LLMClient
from llm_tutor.llm.prompts import BUSY_REPLY
from llm_tutor.student import survey

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = "survey"
GO = "go"
BACK = "back"
GO_DATA = f"{CALLBACK_PREFIX}:{GO}"
GO_LABEL = "▶️ Поехали"
BACK_LABEL = "‹ Назад"

BUTTON_HINT_REPLY = "Ответь кнопкой в сообщении выше 👆"
STALE_CLICK_TOAST = "Этот вопрос уже позади"
DONE_TOAST = "Анкета уже пройдена"


class SurveyFlow(StatesGroup):
    """Анкета: ждём нажатия кнопки в сообщении анкеты."""

    question = State()


def _button(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _data(step: int, choice: int | str) -> str:
    """``callback_data`` с номером шага: клик по старой клавиатуре узнаваем."""
    return f"{CALLBACK_PREFIX}:{step}:{choice}"


def intro_view() -> tuple[str, InlineKeyboardMarkup]:
    """Приветствие с единственной кнопкой «▶️ Поехали»."""
    return start.INTRO_TEXT, InlineKeyboardMarkup(
        inline_keyboard=[[_button(GO_LABEL, GO_DATA)]]
    )


def question_view(progress: survey.Progress) -> tuple[str, InlineKeyboardMarkup]:
    """Текущий вопрос (готовый HTML) и его клавиатура.

    Вызывать только на незаконченной анкете: у законченной вопроса нет.
    """
    key = progress.next_key()
    step = progress.step
    if key == survey.LEVEL_KEY:
        text = render.escape(survey.LEVEL_QUESTION)
        rows = [
            [_button(label, _data(step, index))]
            for index, label in enumerate(survey.LEVEL_OPTIONS)
        ]
    else:
        block = survey.block_by_key(key)
        number, total = progress.position()
        bar = "▰" * number + "▱" * (total - number)
        text = (
            f"Вопрос {number} из {total}  {bar}\n\n"
            f"<b>{render.escape(block.question)}</b>\n{render.escape(block.example)}"
        )
        buttons = [
            _button(label, _data(step, index))
            for index, label in enumerate(survey.SELF_LEVELS)
        ]
        rows = [buttons[:2], buttons[2:]]
    if step > 0:
        rows.append([_button(BACK_LABEL, _data(step, BACK))])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def summary_text(answers: Mapping[str, int]) -> str:
    """Сводка «как я тебя понял» (готовый HTML) — финал сообщения анкеты."""
    lines = [
        f"• {render.escape(block.title)} — {survey.SELF_LEVELS[answers[block.key]].lower()}"
        for block in survey.BLOCKS
    ]
    return "✅ Понял тебя:\n" + "\n".join(lines)


def _progress(data: Mapping) -> survey.Progress | None:
    """Ход анкеты из FSM; ``None`` — приветствие показано, «Поехали» не нажато."""
    given = data.get("given")
    if given is None:
        return None
    return survey.Progress(given=tuple((key, index) for key, index in given))


def _stored(progress: survey.Progress) -> list[list]:
    """Ход анкеты в виде для FSM (списки — сериализуемы любым хранилищем)."""
    return [[key, index] for key, index in progress.given]


def _parse(data: str | None) -> tuple[str, str | None]:
    """``survey:go`` → (``go``, None); ``survey:<шаг>:<выбор>`` → (шаг, выбор)."""
    parts = (data or "").split(":")
    if parts[1:] == [GO]:
        return GO, None
    if len(parts) == 3:
        return parts[1], parts[2]
    return "", None


async def safe_edit(
    message: Message, text: str, markup: InlineKeyboardMarkup | None
) -> Message:
    """Правит сообщение; если править нельзя — шлёт новое с тем же содержимым.

    «Не изменилось» — не ошибка (повторная отрисовка того же шага). Остальные
    отказы (сообщение старое или удалено) не должны оставить ученика без
    вопроса: он приходит новым сообщением, и вызывающий перепривязывает FSM.
    """
    try:
        await message.edit_text(text, reply_markup=markup, parse_mode=render.PARSE_MODE)
    except TelegramBadRequest as error:
        if "message is not modified" in str(error):
            return message
        logger.info("Сообщение анкеты не править (%s) — шлю новое", error)
        return await message.answer(text, reply_markup=markup, parse_mode=render.PARSE_MODE)
    return message


async def _drop_keyboard(message: Message) -> None:
    """Снимает кнопки со старого сообщения анкеты."""
    try:
        await message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest as error:
        # Кнопки уже сняты или сообщение не править — тост ученик уже видел.
        logger.info("Кнопки анкеты не снять: %s", error)


async def start_survey(message: Message, state: FSMContext) -> Message:
    """Новое сообщение-приветствие с «Поехали»; анкета привязывается к нему.

    Повторный вызов (``/start`` посреди анкеты) перепривязывает FSM к новому
    сообщению — нажатия в старом упрутся в тост.
    """
    text, markup = intro_view()
    sent = await message.answer(text, reply_markup=markup, parse_mode=render.PARSE_MODE)
    await state.set_state(SurveyFlow.question)
    await state.set_data({"message_id": sent.message_id, "given": None})
    return sent


def make_survey_router(
    conn, settings: Settings, client: LLMClient, model: str
) -> Router:
    """Роутер анкеты: все нажатия ``survey:*`` и текст посреди анкеты."""
    router = Router()

    async def _finish(
        callback: CallbackQuery, state: FSMContext, progress: survey.Progress
    ) -> None:
        answers = progress.final_answers()
        # Запись и снятие FSM — ДО сетевых вызовов: второй параллельный тап
        # увидит пройденную анкету, а не запустит урок ещё раз.
        survey.apply_answers(conn, answers, level=progress.level, settings=settings)
        await state.clear()
        await callback.answer()
        await safe_edit(callback.message, summary_text(answers), None)
        await start.begin_lesson(callback.message, state, conn, client, model, settings)

    async def _orphan_click(callback: CallbackQuery, state: FSMContext) -> None:
        """Нажатие не в живом сообщении анкеты: тост или новый старт."""
        current = await state.get_state()
        if survey.is_completed(conn):
            await callback.answer(DONE_TOAST)
            await _drop_keyboard(callback.message)
        elif current == SurveyFlow.question.state:
            # Анкета идёт в другом сообщении — это старое.
            await callback.answer(STALE_CLICK_TOAST)
        elif current is not None:
            await callback.answer(BUSY_REPLY)
        else:
            # FSM потерян (рестарт бота), анкета не пройдена: начинаем заново
            # в этом же сообщении, а не молчим.
            await state.set_state(SurveyFlow.question)
            await state.set_data({"message_id": callback.message.message_id, "given": []})
            await callback.answer()
            text, markup = question_view(survey.Progress())
            await safe_edit(callback.message, text, markup)

    @router.callback_query(F.data.startswith(f"{CALLBACK_PREFIX}:"))
    async def on_survey_click(callback: CallbackQuery, state: FSMContext) -> None:
        # Всё до первого сетевого await — неделимая часть: MemoryStorage не
        # уступает цикл, поэтому второй параллельный тап (апдейты идут
        # задачами, handle_as_tasks=True) увидит уже продвинутый шаг. При смене
        # хранилища на сетевое — пересмотреть.
        message = callback.message
        data = await state.get_data()
        bound = (
            await state.get_state() == SurveyFlow.question.state
            and data.get("message_id") == message.message_id
        )
        if not bound:
            await _orphan_click(callback, state)
            return

        action, choice = _parse(callback.data)
        progress = _progress(data)
        if action == GO:
            if progress is not None:  # «Поехали» уже нажимали
                await callback.answer(STALE_CLICK_TOAST)
                return
            progress = survey.Progress()
        elif progress is None or action != str(progress.step):
            await callback.answer(STALE_CLICK_TOAST)
            return
        elif choice == BACK:
            progress = progress.back()
        else:
            try:
                progress = progress.answer(int(choice or ""))
            except ValueError:
                # Данные колбэка подконтрольны клиенту: мусор — не ответ.
                await callback.answer(STALE_CLICK_TOAST)
                return

        if progress.next_key() is None:
            await _finish(callback, state, progress)
            return
        await state.update_data(given=_stored(progress))
        await callback.answer()
        text, markup = question_view(progress)
        shown = await safe_edit(message, text, markup)
        if shown.message_id != message.message_id:
            await state.update_data(message_id=shown.message_id)

    @router.message(SurveyFlow.question, F.text, ~F.text.startswith("/"))
    async def on_text(message: Message) -> None:
        # Анкета принимает только нажатия: иначе напечатанное пропадало бы в
        # тишину (тьютор-путь отсечён StateFilter(None)).
        await message.answer(BUTTON_HINT_REPLY)

    return router
```

Заметка исполнителю: «Назад» с шага 0 невозможен (кнопки нет), а подделанный
`survey:0:back` упрётся в `action != str(progress.step)` только на шаге ≠ 0;
на шаге 0 `progress.back()` вернёт тот же пустой ход — безвредно, отдельной
ветки не нужно.

- [ ] **Step 5: Удалить мост из `src/llm_tutor/student/survey.py`**

Удалить блок от комментария `# --- Временный мост для старого потока бота`
до конца файла (`SurveyOption`, `SurveyQuestion`, `SURVEY_QUESTIONS`).

- [ ] **Step 6: `src/llm_tutor/bot/handlers.py` — вход через `start_survey`**

Строку 28 `from llm_tutor.bot.survey import ask as ask_survey` заменить на
`from llm_tutor.bot.survey import start_survey`. В `on_start` ветку до анкеты
(строки 215–220) заменить на:

```python
        # Пока профиль не заполнен — одно сообщение-приветствие с «Поехали»:
        # дальше анкета живёт в нём же.
        if not survey_completed(conn):
            await start_survey(message, state)
            return
```

- [ ] **Step 7: Проверить, что ничего не ссылается на удалённое**

Run: `uv run python -c "import llm_tutor.bot.main"` и
`git grep -n "SURVEY_QUESTIONS\|ask_survey\|question_by_key\|SurveyQuestion" -- src tests`
Expected: импорт без ошибок; grep пуст.

- [ ] **Step 8: Запустить набор**

Run: `uv run pytest -q`
Expected: всё зелёное.

- [ ] **Step 9: Commit**

```bash
git add src/llm_tutor/bot/survey.py src/llm_tutor/student/survey.py src/llm_tutor/bot/handlers.py tests/test_bot_flows.py tests/test_e2e_topic01.py
git commit -m "Срез 22: анкета в одном сообщении — привязка нажатий, «Назад», без зависших кнопок"
```

---

### Task 22.4: Вход и повторный /start без LLM

**Files:**
- Modify: `src/llm_tutor/bot/start.py:1-43` (тексты, удалить reply-клавиатуру,
  `welcome_back_text`)
- Modify: `src/llm_tutor/bot/handlers.py:48-119` (`build_start_reply`,
  `START_SYSTEM_PROMPT`, `_truncate`, `handle_start`), `213-232` (`on_start`),
  `405-434` (`on_start_button`, `on_text`)
- Modify: `tests/test_handlers.py:1-140`, `tests/test_bot_flows.py`
  (`test_text_before_survey_gets_invitation`,
  `test_intro_has_three_sentences_and_names_the_button`,
  `test_start_after_survey_offers_menu`)

**Interfaces:**
- Consumes: `start_survey` (22.3).
- Produces:
  - `start.INTRO_TEXT` — два предложения, без имени reply-кнопки
  - `start.START_LABEL = "▶️ Старт"` — оставлен только для старых учеников
  - `start.welcome_back_text(conn) -> str` — сырой текст
  - `handlers.handle_start(conn, user_text, *, now=None) -> str` — **синхронная**,
    без клиента и модели
  - удалены: `start.start_keyboard`, `start.BEFORE_SURVEY_REPLY`,
    `handlers.build_start_reply`, `handlers.START_SYSTEM_PROMPT`,
    `handlers._truncate`

- [ ] **Step 1: Тесты в `tests/test_handlers.py`**

Импорт `build_start_reply` убрать. Удалить тесты
`test_build_start_reply_*` (4 шт.) и
`test_handle_start_leaves_no_orphan_on_unexpected_error`, классы
`_FailingClient`, `_CrashingClient`, если больше не используются
(`git grep` по файлу). Тесты `handle_start` переписать:

```python
def test_handle_start_persists_session_and_messages(conn) -> None:
    """handle_start заводит сессию и пишет обе реплики в хронологии."""
    reply = handle_start(conn, "привет", now=123.0)

    session_id = repos.get_open_session(conn)
    messages = repos.get_messages(conn, session_id)
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[0].content == "привет"
    assert messages[1].content == reply


def test_handle_start_updates_last_activity(conn) -> None:
    handle_start(conn, "привет", now=123.0)
    session_id = repos.get_open_session(conn)
    assert repos.get_session_state(conn, session_id).last_activity == 123.0


def test_handle_start_reuses_single_session(conn) -> None:
    """Повторный /start не плодит сессии — открытая переиспользуется."""
    handle_start(conn, "раз", now=1.0)
    handle_start(conn, "два", now=2.0)

    assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1
    session_id = repos.get_open_session(conn)
    assert len(repos.get_messages(conn, session_id)) == 4


def test_welcome_back_names_current_node_without_model_tag(conn) -> None:
    load_seed(conn)
    session_id = repos.ensure_open_session(conn, now=1.0)
    repos.update_session_state(conn, session_id, SessionState(current_node_id="groupby"))

    reply = handle_start(conn, "привет", now=2.0)

    assert "С возвращением" in reply
    assert "Группировка" in reply
    assert "[модель:" not in reply


def test_welcome_back_without_node_invites_to_write(conn) -> None:
    load_seed(conn)

    reply = handle_start(conn, "привет", now=2.0)

    assert reply == start.WELCOME_BACK_IDLE
```

(в импорты: `from llm_tutor.bot import start`). В
`test_state_survives_restart` вызов
`await handle_start(first_conn, _FakeClient(), "m", "привет", now=10.0)`
заменить на `handle_start(first_conn, "привет", now=10.0)` (тест остаётся
`async` или становится обычным — не важно; сделать обычным `def`).

- [ ] **Step 2: Тесты входа в `tests/test_bot_flows.py`**

`test_text_before_survey_gets_invitation` заменить:

```python
async def test_text_before_survey_gets_intro(conn, settings) -> None:
    """До анкеты реплика получает приветствие с «Поехали», а не тьютора."""
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    message = FakeMessage("привет")
    state = _fsm()

    await _named(router, "message", "on_text")(message, state)

    assert message.last_text == start.INTRO_TEXT
    assert await state.get_state() == SurveyFlow.question.state
    assert repos.get_open_session(conn) is None  # в занятие не пошли
```

`test_intro_has_three_sentences_and_names_the_button` заменить:

```python
def test_intro_is_two_sentences_without_reply_button() -> None:
    """Приветствие — два предложения; жать нужно инлайн-«Поехали» под ним."""
    lines = [line for line in start.INTRO_TEXT.splitlines() if line.strip()]
    assert len(lines) == 2
    assert start.START_LABEL not in start.INTRO_TEXT


async def test_start_after_survey_does_not_call_model(conn, settings) -> None:
    """Вернувшемуся — «С возвращением» без LLM и без служебной метки модели."""
    load_seed(conn)
    _complete_survey(conn)

    class _NoModel:
        async def chat(self, *args, **kwargs):
            raise AssertionError("повторный /start не зовёт модель")

        async def chat_structured(self, *args, **kwargs):
            raise AssertionError("повторный /start не зовёт модель")

    router = make_router(conn, _NoModel(), "m", settings=settings)
    message = FakeMessage()

    await _named(router, "message", "on_start")(message, _fsm())

    assert "С возвращением" in message.last_text
    assert "[модель:" not in message.last_text
```

`test_start_after_survey_offers_menu` — не меняется (только словарь ответов
уже поправлен в 22.3).

`on_text` теперь принимает `state` (Step 5): **все** вызовы
`_named(router, "message", "on_text")(message)` в тестах дополнить вторым
аргументом `_fsm()`. Найти: `git grep -n '"on_text")(' -- tests`.

- [ ] **Step 3: Запустить — должно упасть**

Run: `uv run pytest tests/test_handlers.py tests/test_bot_flows.py -q`
Expected: FAIL — `TypeError` в `handle_start` (лишние аргументы) и
`AttributeError: WELCOME_BACK_IDLE`.

- [ ] **Step 4: `src/llm_tutor/bot/start.py`**

Докстринг модуля, константы и клавиатуру (строки 1–43) заменить; импорт
`KeyboardButton, ReplyKeyboardMarkup` убрать:

```python
"""Вход в курс: приветствие, «С возвращением» и начало урока.

Нового ученика встречает одно сообщение с кнопкой «▶️ Поехали» — анкета живёт
в нём же (``bot/survey.py``). После анкеты бот показывает ближайшие шаги
списком и сразу начинает урок по первому из них.
"""

import logging
import sqlite3

from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from llm_tutor.bot import menu, render
from llm_tutor.config import Settings
from llm_tutor.core.turn import resume_reply
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.llm.client import LLMClient
from llm_tutor.student import route as route_mod

logger = logging.getLogger(__name__)

# Текст reply-кнопки прошлой версии: у старых учеников она ещё висит внизу, и
# её нажатие должно вести на вход, а не в тьюторский ход.
START_LABEL = "▶️ Старт"

# Приветствие — два предложения: кто я и что сейчас будет. Что нажать, видно
# по единственной кнопке под ним.
INTRO_TEXT = (
    "👋 Привет! Я тьютор по теме «Pandas / EDA» курса mlcourse.ai.\n"
    "Пара коротких вопросов — и подберу, с чего начать."
)

WELCOME_BACK_TEMPLATE = "👋 С возвращением! Продолжаем «{name}»."
WELCOME_BACK_IDLE = "👋 С возвращением! Напиши что угодно — продолжим."


def welcome_back_text(conn: sqlite3.Connection) -> str:
    """«С возвращением» с текущей темой — без модели (сырой текст).

    Это чтение, а не ход: сессию не заводим (её заведёт ``handle_start``).
    """
    session_id = repos.get_open_session(conn)
    if session_id is None:
        return WELCOME_BACK_IDLE
    node_id = repos.get_session_state(conn, session_id).current_node_id
    graph = CourseGraph.load(conn)
    if node_id is None or not graph.has_node(node_id):
        return WELCOME_BACK_IDLE
    return WELCOME_BACK_TEMPLATE.format(name=graph.concept(node_id).name)
```

(`begin_lesson` ниже не трогать.)

- [ ] **Step 5: `src/llm_tutor/bot/handlers.py`**

Удалить `START_SYSTEM_PROMPT` (строки 58–61), `_truncate` (75–79),
`build_start_reply` (82–92); убрать ставшие неиспользуемыми импорты
`LLMError`, `ChatMessage` (проверить `git grep -n "LLMError\|ChatMessage"
src/llm_tutor/bot/handlers.py`). `MAX_REPLY_LENGTH` удалить, если больше не
используется. `handle_start` заменить:

```python
def handle_start(
    conn: sqlite3.Connection,
    user_text: str,
    *,
    now: float | None = None,
) -> str:
    """Полный ход на /start вернувшегося: сессия + журнал + «С возвращением».

    Модель не зовём: приветствие не несёт содержания, а LLM-вызов стоил бы
    секунд ожидания на каждый /start.
    """
    ts = time.time() if now is None else now
    reply = start.welcome_back_text(conn)
    session_id = ensure_open_session(conn, ts)
    _persist_turn(conn, session_id, user_text, reply, ts)
    return reply
```

В `on_start` строку `reply = await handle_start(conn, client, model,
START_GREETING)` заменить на `reply = handle_start(conn, START_GREETING)`;
комментарий про «экран согласования маршрута» поправить на «из незакрытого
потока (анкета, диагностика)».

`on_text` (строки 423–434) — принимает `state` и до анкеты шлёт приветствие:

```python
    @router.message(StateFilter(None), F.text & ~F.text.startswith("/"))
    async def on_text(message: Message, state: FSMContext) -> None:
        # Анкета не пройдена — маршрута ещё нет: вести занятие не по чему.
        # Вместо отказа — то же приветствие с «Поехали», что и на /start.
        if not survey_completed(conn):
            await start_survey(message, state)
            return
        reply = await _run_turn(conn, client, model, message.text or "", settings=settings)
        await _send_reply(conn, message, reply)
```

Комментарий над `on_start_button` поправить: «Текст «▶️ Старт» — reply-кнопка
прошлой версии, у старых учеников она ещё висит: ведёт на вход, как /start».

- [ ] **Step 6: Проверить хвосты**

Run: `git grep -n "BEFORE_SURVEY_REPLY\|start_keyboard\|build_start_reply\|START_SYSTEM_PROMPT" -- src tests`
Expected: пусто.

- [ ] **Step 7: Запустить набор**

Run: `uv run pytest -q`
Expected: всё зелёное.

- [ ] **Step 8: Commit**

```bash
git add src/llm_tutor/bot/start.py src/llm_tutor/bot/handlers.py tests/test_handlers.py tests/test_bot_flows.py
git commit -m "Срез 22: приветствие с «Поехали», повторный /start без модели"
```

---

### Task 22.5: «Печатает…» на время ответа модели

**Files:**
- Create: `src/llm_tutor/bot/chat_action.py`
- Modify: `src/llm_tutor/bot/handlers.py` (`on_text`, `on_answer`, `on_resume`),
  `src/llm_tutor/bot/start.py` (`begin_lesson`)
- Create: `tests/test_chat_action.py`

**Interfaces:**
- Produces: `chat_action.typing_action(message) -> AbstractAsyncContextManager`
  — `ChatActionSender.typing`, если у сообщения есть `bot`, иначе
  `nullcontext()`.

- [ ] **Step 1: Тесты `tests/test_chat_action.py`**

```python
"""«Печатает…», пока бот ждёт модель."""

import asyncio

from fakes import FakeMessage, _fsm, _named

from llm_tutor.bot.chat_action import typing_action
from llm_tutor.bot.handlers import make_router
from llm_tutor.course.seed import load_seed
from llm_tutor.db import repos
from llm_tutor.student import survey


class _RecordingBot:
    """Бот-заглушка: помнит отправленные chat action."""

    id = 42

    def __init__(self) -> None:
        self.actions: list[tuple[int, str]] = []

    async def send_chat_action(self, chat_id, action, message_thread_id=None, **kwargs):
        self.actions.append((chat_id, action))
        return True


class _SlowTutor:
    async def chat_structured(self, messages, schema, *, model=None, temperature=None):
        await asyncio.sleep(0.01)
        return schema(reply="ок", hint_level=0)


async def test_typing_action_sends_typing_while_waiting() -> None:
    message = FakeMessage()
    message.bot = _RecordingBot()

    async with typing_action(message):
        await asyncio.sleep(0.01)

    assert message.bot.actions == [(1, "typing")]


async def test_typing_action_is_noop_without_bot() -> None:
    async with typing_action(FakeMessage()):
        pass


async def test_free_text_shows_typing(conn, settings) -> None:
    load_seed(conn)
    for block in survey.BLOCKS:
        repos.set_fact(conn, block.key, survey.SELF_LEVELS[1], source="self")
    router = make_router(conn, _SlowTutor(), "m", settings=settings)
    message = FakeMessage("как работает groupby?")
    message.bot = _RecordingBot()

    await _named(router, "message", "on_text")(message, _fsm())

    assert (1, "typing") in message.bot.actions
```

- [ ] **Step 2: Запустить — должно упасть**

Run: `uv run pytest tests/test_chat_action.py -q`
Expected: FAIL — `ModuleNotFoundError: llm_tutor.bot.chat_action`.

- [ ] **Step 3: Создать `src/llm_tutor/bot/chat_action.py`**

```python
"""«Печатает…» на время долгого ответа: ученик видит, что бот не завис."""

from contextlib import AbstractAsyncContextManager, nullcontext

from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender


def typing_action(message: Message) -> AbstractAsyncContextManager:
    """Контекст «печатает…» в чате сообщения.

    ``ChatActionSender`` повторяет действие каждые 5 секунд, пока ждём модель.
    Без бота (сообщение не привязано к нему — например, в тестах) — пустой
    контекст: индикатор не важнее ответа.
    """
    bot = getattr(message, "bot", None)
    if bot is None:
        return nullcontext()
    return ChatActionSender.typing(bot=bot, chat_id=message.chat.id)
```

- [ ] **Step 4: Обернуть ожидание модели**

`src/llm_tutor/bot/handlers.py` (импорт `from llm_tutor.bot.chat_action import
typing_action`):

- `on_text`: `reply = await _run_turn(...)` →
  ```python
        async with typing_action(message):
            reply = await _run_turn(conn, client, model, message.text or "", settings=settings)
  ```
- `on_answer`: вызов `_run_turn(...)` обернуть в
  `async with typing_action(callback.message):`; `callback.answer()` перенести
  **перед** этим блоком (сразу после проверки актуальности задания) — «часики»
  гаснут до ожидания модели. Нижний `await callback.answer()` удалить.
- `on_resume`: `reply = await resume_reply(...)` внутри `try` обернуть в
  `async with typing_action(message):`.

`src/llm_tutor/bot/start.py`, `begin_lesson`: вызов
`reply = await resume_reply(conn, client, model, settings=settings)` обернуть в
`async with typing_action(message):` (импорт `from llm_tutor.bot.chat_action
import typing_action`).

- [ ] **Step 5: Запустить набор**

Run: `uv run pytest -q`
Expected: всё зелёное. Если упал тест, проверявший `callback.answered` после
`on_answer`, — он по-прежнему должен быть `True` (ответ на колбэк теперь
раньше, но есть).

- [ ] **Step 6: Commit**

```bash
git add src/llm_tutor/bot/chat_action.py src/llm_tutor/bot/handlers.py src/llm_tutor/bot/start.py tests/test_chat_action.py
git commit -m "Срез 22: «печатает…», пока бот ждёт модель"
```

---

### Task 22.6: Адверсариальное ревью среза 22

- [ ] **Step 1:** `uv run pytest -q` — зелёное; записать число тестов в
  `.superpowers/sdd/2026-10-08-adaptive-survey/progress.md`.
- [ ] **Step 2:** Запустить fork-агента (`subagent_type: "fork"`, максимальное
  усердие) с ТЗ: сфера — `bot/survey.py`, `bot/start.py`, `bot/handlers.py`
  (вход), `student/survey.py`; правила — ничего не редактировать, каждую
  находку воспроизводить запуском кода (пробы в scratchpad), отчёт по
  серьёзности с конкретным сценарием отказа. Фокус: Review Focus 1–3, порядок
  `callback.answer()`, перепривязка FSM после `safe_edit`, ввод мусорных
  `callback_data`, `/start`/команды посреди анкеты через боевой `Dispatcher`.
- [ ] **Step 3:** Триаж находок (принять / размен / дефект спеки), фиксы с
  регрессионными тестами RED→GREEN, `uv run pytest -q`.
- [ ] **Step 4:** Commit `Аудит среза 22: …`, запись в progress.md.

---

## Срез 23 — анкета влияет на урок

### Task 23.1: Статус шага `claimed`

**Files:**
- Modify: `src/llm_tutor/schemas.py:23` (`RouteStepStatus`)
- Modify: `src/llm_tutor/student/survey.py` (добавить `claimed_concepts`)
- Modify: `src/llm_tutor/student/route.py:18,50-82,112-145` (импорт,
  `build_route`, `next_node_id`, новая `upcoming`)
- Modify: `src/llm_tutor/bot/render.py:53` (`_STEP_MARKS`)
- Modify: `src/llm_tutor/bot/themes.py:27-90` (`NodeStatus`, `STATUS_ICONS`,
  `node_status`)
- Test: `tests/test_route.py`, `tests/test_render.py`, `tests/test_themes.py`

**Interfaces:**
- Produces:
  - `RouteStepStatus = Literal["closed", "current", "claimed", "ahead"]`
  - `survey.claimed_concepts(conn) -> frozenset[str]`
  - `build_route(...)`: `claimed` из фактов (нет снимка) или из снимка
  - `next_node_id(...)`: `claimed` = выполненный пререквизит; выбирается, только
    когда незаявленных нет (первый по порядку маршрута)
  - `route.upcoming(route: Route, first: str | None, *, limit: int) -> list[RouteStep]`
  - `render._STEP_MARKS["claimed"] == "🔍 "`, `themes.STATUS_ICONS["claimed"] == "🔍"`

- [ ] **Step 1: Тесты маршрута (`tests/test_route.py`, в конец)**

Импорты дополнить: `from llm_tutor.student import survey`.

```python
# --- заявленное в анкете (срез 23) ---


def _claimed_route(*statuses: tuple[str, str]) -> Route:
    return Route(
        steps=[
            RouteStep(concept_id=concept_id, mode="full", status=status)
            for concept_id, status in statuses
        ]
    )


def test_claimed_comes_from_confident_survey_answer(conn, settings) -> None:
    load_seed(conn)
    survey.apply_answers(
        conn, {"block_python": survey.CONFIDENT_INDEX}, now=0.0, settings=settings
    )

    route = route_mod.build_route(conn, CourseGraph.load(conn), now=0.0, settings=settings)

    statuses = _statuses(route)
    assert statuses["python_basics"] == statuses["numpy_basics"] == "claimed"
    assert statuses["pandas_intro"] == "ahead"


def test_claimed_kept_in_snapshot_until_node_becomes_current(conn, settings) -> None:
    graph = _graph(["a", "b"], [("a", "b")])
    previous = _claimed_route(("a", "claimed"), ("b", "ahead"))

    kept = route_mod.build_route(conn, graph, previous=previous, now=0.0, settings=settings)
    current = route_mod.build_route(
        conn, graph, current_node_id="a", previous=kept, now=0.0, settings=settings
    )
    after = route_mod.build_route(conn, graph, previous=current, now=0.0, settings=settings)

    assert _statuses(kept)["a"] == "claimed"
    assert _statuses(current)["a"] == "current"
    assert _statuses(after)["a"] == "ahead"  # заявка снята — обратно не возвращается


def test_next_node_treats_claimed_as_done_prerequisite(conn, settings) -> None:
    graph = _graph(["a", "b"], [("a", "b")])
    route = _claimed_route(("a", "claimed"), ("b", "ahead"))

    assert route_mod.next_node_id(conn, graph, route, now=0.0, settings=settings) == "b"


def test_next_node_checks_claimed_when_nothing_else_left(conn, settings) -> None:
    graph = _graph(["a", "b", "c"], [("a", "b")])
    route = _claimed_route(("a", "closed"), ("b", "claimed"), ("c", "claimed"))

    assert route_mod.next_node_id(conn, graph, route, now=0.0, settings=settings) == "b"


def test_first_node_is_never_claimed_while_unclaimed_remain(conn, settings) -> None:
    """По всем 32 наборам «уверенных» блоков старт — с незаявленного узла."""
    from itertools import combinations

    keys = [block.key for block in survey.BLOCKS]
    for size in range(len(keys) + 1):
        for confident in combinations(keys, size):
            db = get_conn(":memory:")
            migrate(db)
            load_seed(db)
            answers = {key: (survey.CONFIDENT_INDEX if key in confident else 1) for key in keys}
            survey.apply_answers(db, answers, now=0.0, settings=settings)
            graph = CourseGraph.load(db)
            route = route_mod.build_route(db, graph, now=0.0, settings=settings)
            first = route_mod.next_node_id(db, graph, route, now=0.0, settings=settings)
            statuses = _statuses(route)
            db.close()
            if size < len(keys):
                assert statuses[first] != "claimed", confident
            else:
                assert first == "python_basics"  # всё заявлено — проверка с начала


def test_upcoming_puts_first_then_ahead_then_claimed() -> None:
    route = _claimed_route(("a", "claimed"), ("b", "ahead"), ("c", "closed"), ("d", "ahead"))

    steps = route_mod.upcoming(route, "d", limit=5)

    assert [step.concept_id for step in steps] == ["d", "b", "a"]


def test_upcoming_respects_limit_and_empty_first() -> None:
    route = _claimed_route(("a", "ahead"), ("b", "ahead"), ("c", "ahead"))

    assert [step.concept_id for step in route_mod.upcoming(route, "a", limit=2)] == ["a", "b"]
    assert route_mod.upcoming(route, None, limit=5) == []
```

(импорты для последнего: `from llm_tutor.db.connection import get_conn, migrate`.)

- [ ] **Step 2: Тесты отображения**

`tests/test_render.py` (в конец; при необходимости импортировать `RouteStep`,
`CourseGraph`, `load_seed`):

```python
def test_render_steps_marks_claimed(conn) -> None:
    load_seed(conn)
    graph = CourseGraph.load(conn)
    steps = [RouteStep(concept_id="python_basics", mode="full", status="claimed")]

    assert "🔍 " in render.render_steps(graph, steps)
```

`tests/test_themes.py` (в конец):

```python
def test_node_status_claimed_and_opens_dependent(conn, settings) -> None:
    """Заявленный узел помечен 🔍, а зависимый от него доступен."""
    load_seed(conn)
    graph = CourseGraph.load(conn)
    state = SessionState(
        route=Route(steps=[RouteStep(concept_id="python_basics", mode="full", status="claimed")])
    )

    assert themes.node_status(conn, graph, "python_basics", state, now=0.0, settings=settings) == "claimed"
    assert themes.node_status(conn, graph, "numpy_basics", state, now=0.0, settings=settings) == "available"
    assert themes.STATUS_ICONS["claimed"] == "🔍"
```

- [ ] **Step 3: Запустить — должно упасть**

Run: `uv run pytest tests/test_route.py tests/test_render.py tests/test_themes.py -q`
Expected: FAIL — pydantic отвергает `status="claimed"`.

- [ ] **Step 4: Реализация**

`src/llm_tutor/schemas.py:23`:

```python
RouteStepStatus = Literal["closed", "current", "claimed", "ahead"]
```

`src/llm_tutor/student/survey.py` — после `is_completed`:

```python
def claimed_concepts(conn: sqlite3.Connection) -> frozenset[str]:
    """Узлы блоков, которые ученик назвал знакомыми («Уверенно»).

    Самооценка узел не закрывает: такие узлы маршрут пропускает вперёд и
    проверяет проходом в конце (срез 23).
    """
    confident = SELF_LEVELS[CONFIDENT_INDEX]
    return frozenset(
        concept_id
        for block in BLOCKS
        if repos.get_fact(conn, block.key) == confident
        for concept_id in block.concepts
    )
```

`src/llm_tutor/student/route.py`: импорт
`from llm_tutor.student.survey import GOAL_CONCEPT_KEY, claimed_concepts`.
В `build_route` после `closed_before`:

```python
    # Заявленное в анкете: без снимка — из фактов, со снимком — из него. Узел,
    # переставший быть заявленным (стал текущим, проход не подтвердился),
    # обратно не возвращается.
    claimed_before = (
        {step.concept_id for step in previous.steps if step.status == "claimed"}
        if previous is not None
        else set(claimed_concepts(conn))
    )
```

и в цикле статусов между `current` и `ahead`:

```python
        elif concept_id == current_node_id:
            status = "current"
        elif concept_id in claimed_before:
            status = "claimed"
        else:
            status = "ahead"
```

`next_node_id` — тело после `in_route`:

```python
    closed = {step.concept_id for step in route.steps if step.status == "closed"}
    claimed = {step.concept_id for step in route.steps if step.status == "claimed"}
    # Заявленное — выполненный пререквизит: урок начинается с первого
    # неуверенного блока, а не с азов, которые ученик назвал знакомыми.
    ready = planner.ready_nodes(
        conn,
        graph,
        goal_concept_id=route.goal_concept_id,
        limit=max(len(route.steps), 1),
        now=stamp,
        settings=s,
        mastery_overrides=mastery_overrides,
        completed_ids=frozenset(closed | claimed),
    )
    for node in ready:
        if node.concept_id in in_route and node.concept_id not in closed | claimed:
            return node.concept_id
    # Незаявленное кончилось — пора подтвердить заявленное, по порядку маршрута.
    return next(
        (step.concept_id for step in route.steps if step.status == "claimed"), None
    )
```

Новая функция после `next_node_id`:

```python
def upcoming(route: Route, first: str | None, *, limit: int) -> list[RouteStep]:
    """Ближайшие шаги для списка: с какого начнём, потом незакрытые по порядку
    маршрута, в хвосте — заявленные (их проверим в конце)."""
    if first is None:
        return []
    head = [step for step in route.steps if step.concept_id == first]
    rest = [
        step
        for step in route.steps
        if step.status != "closed" and step.concept_id != first
    ]
    ahead = [step for step in rest if step.status != "claimed"]
    claimed = [step for step in rest if step.status == "claimed"]
    return (head + ahead + claimed)[:limit]
```

`src/llm_tutor/bot/render.py:53`:

```python
_STEP_MARKS = {"closed": "[x] ", "current": "[>] ", "claimed": "🔍 ", "ahead": ""}
```

`src/llm_tutor/bot/themes.py`:

```python
NodeStatus = Literal["closed", "current", "claimed", "available", "ahead"]

STATUS_ICONS: dict[str, str] = {
    "closed": "✅",
    "current": "▶️",
    "claimed": "🔍",
    "available": "🟢",
    "ahead": "🔜",
}
```

после `_closed_from_route`:

```python
def _claimed_from_route(state: SessionState) -> set[str]:
    """Узлы, заявленные в анкете и ещё не подтверждённые (снимок маршрута)."""
    if state.route is None:
        return set()
    return {step.concept_id for step in state.route.steps if step.status == "claimed"}
```

`node_status`:

```python
    if node_id == state.current_node_id:
        return "current"
    if _is_closed(conn, node_id, state, now=now, settings=settings):
        return "closed"
    claimed = _claimed_from_route(state)
    if node_id in claimed:
        return "claimed"
    prereqs = graph.hard_prerequisites(node_id)
    if all(
        prereq in claimed or _is_closed(conn, prereq, state, now=now, settings=settings)
        for prereq in prereqs
    ):
        return "available"
    return "ahead"
```

`tests/test_bot_flows.py`, `_complete_survey`: уровень `survey.SELF_LEVELS[3]`
заменить на `survey.SELF_LEVELS[2]` («С подсказками»). Иначе все узлы станут
`claimed`, и тесты тьюторского хода получат проверку вместо объяснения. Цель
хелпера — «профиль заполнен», а не «всё знакомо».

- [ ] **Step 5: Запустить набор**

Run: `uv run pytest -q`
Expected: всё зелёное. Если упал тест, который сам ставит «Уверенно» на все
блоки и ждёт объяснения узла, — это то же следствие: поменять в нём уровень,
а не код.

- [ ] **Step 6: Commit**

```bash
git add src/llm_tutor/schemas.py src/llm_tutor/student/survey.py src/llm_tutor/student/route.py src/llm_tutor/bot/render.py src/llm_tutor/bot/themes.py tests/test_route.py tests/test_render.py tests/test_themes.py tests/test_bot_flows.py
git commit -m "Срез 23: заявленное в анкете — статус 🔍, урок начинается с неуверенного"
```

---

### Task 23.2: Вход в заявленный узел — проверочный проход

**Files:**
- Modify: `src/llm_tutor/core/turn.py` (константа, `_is_claimed`,
  `_checking`, `_enter_claimed`, `handle_turn`, `_close_node_if_ready`,
  `resume_reply`)
- Test: `tests/test_turn.py`

**Interfaces:**
- Consumes: статус `claimed`, `next_node_id` (23.1).
- Produces:
  - `CLAIMED_CHECK_NOTE = "🔍 Осталось подтвердить знакомое: «{name}» — пара быстрых вопросов."`
  - `resume_reply(conn, client, model, *, now=None, settings=None, start_node_id: str | None = None)`
    — `start_node_id` берётся, когда в состоянии нет текущего узла
  - вход в `claimed` узел: `mode="verify"`, без вызова модели, ответ
    `TurnReply(text=<CLAIMED_CHECK_NOTE>, tail=<задание>, options=...)`

- [ ] **Step 1: Тесты (`tests/test_turn.py`, в конец)**

Импорты дополнить: `from llm_tutor.course.graph import CourseGraph`,
`from llm_tutor.student import route as route_mod, survey`,
`from llm_tutor.core.turn import resume_reply` (если ещё не импортирован).

```python
# --- заявленное в анкете (срез 23) ---


def _claim(conn, settings, *keys: str) -> None:
    survey.apply_answers(
        conn, {key: survey.CONFIDENT_INDEX for key in keys}, now=0.0, settings=settings
    )


ALL_BLOCKS = tuple(block.key for block in survey.BLOCKS)


def _wrong(item) -> str:
    if item.options:
        return item.options[(int(item.answer) + 1) % len(item.options)]
    return "заведомо неверно"


async def test_entering_lesson_skips_claimed_block(conn, settings) -> None:
    load_seed(conn)
    _claim(conn, settings, "block_python")

    await handle_turn(conn, _FakeTutor(), "m", "привет", now=1.0, settings=settings)

    assert _state(conn).current_node_id not in {"python_basics", "numpy_basics"}


async def test_claimed_node_is_entered_as_check_without_explanation(conn, settings) -> None:
    load_seed(conn)
    _claim(conn, settings, *ALL_BLOCKS)
    tutor = _FakeTutor()

    reply = await handle_turn(conn, tutor, "m", "привет", now=1.0, settings=settings)

    state = _state(conn)
    assert state.mode == "verify"
    assert state.current_node_id == "python_basics"
    assert state.pending_item_id is not None
    assert tutor.calls == []  # без объяснения модели
    assert "Осталось подтвердить знакомое" in reply.text
    assert reply.tail and "Проверка" in reply.tail


async def test_resume_enters_claimed_node_as_check(conn, settings) -> None:
    load_seed(conn)
    _claim(conn, settings, *ALL_BLOCKS)

    reply = await resume_reply(conn, _FakeTutor(), "m", now=1.0, settings=settings)

    assert _state(conn).mode == "verify"
    assert "Осталось подтвердить знакомое" in reply.text


async def test_resume_starts_from_given_node(conn, settings) -> None:
    load_seed(conn)

    await resume_reply(
        conn, _FakeTutor(), "m", now=1.0, settings=settings, start_node_id="numpy_basics"
    )

    assert _state(conn).current_node_id == "numpy_basics"


async def test_closing_last_unclaimed_node_moves_to_claimed_check(conn, settings) -> None:
    """Незаявленное кончилось — бот сам ведёт в проверку заявленного."""
    load_seed(conn)
    _claim(conn, settings, *ALL_BLOCKS)
    graph = CourseGraph.load(conn)
    route = route_mod.build_route(
        conn, graph, current_node_id="python_basics", now=0.0, settings=settings
    )
    # Урок по python_basics идёт обычным порядком (заявка снята — стал текущим).
    item = repos.get_item(conn, 6)  # задание по python_basics, верный вариант первый
    tutor = _FakeTutor()

    for now in (1.0, 2.0):
        _set_state(conn, pending_item_id=item.id, current_node_id="python_basics", route=route)
        reply = await handle_turn(conn, tutor, "m", item.options[0], now=now, settings=settings)
        route = _state(conn).route

    state = _state(conn)
    assert state.current_node_id == "numpy_basics"
    assert state.mode == "verify"
    assert "Осталось подтвердить знакомое" in reply.text
    assert tutor.calls == []  # следующий узел не объясняется — проверяется


async def test_failed_claimed_check_turns_into_lesson(conn, settings) -> None:
    load_seed(conn)
    _claim(conn, settings, *ALL_BLOCKS)
    await handle_turn(conn, _FakeTutor(), "m", "привет", now=1.0, settings=settings)
    item = repos.get_item(conn, _state(conn).pending_item_id)

    await handle_turn(conn, _FakeTutor(), "m", _wrong(item), now=2.0, settings=settings)

    state = _state(conn)
    assert state.mode == "reinforce"
    statuses = {step.concept_id: step.status for step in state.route.steps}
    assert statuses["python_basics"] != "claimed"
```

- [ ] **Step 2: Запустить — должно упасть**

Run: `uv run pytest tests/test_turn.py -q -k "claimed or given_node"`
Expected: FAIL — `TypeError: resume_reply() got an unexpected keyword argument 'start_node_id'`
и/или узел входит объяснением.

- [ ] **Step 3: Реализация в `src/llm_tutor/core/turn.py`**

Импорт: `from llm_tutor.schemas import Event, Item, Route, SessionState`.
Константа рядом с `VERIFY_FAILED_NOTE`:

```python
# Вход в заявленный в анкете узел: самооценку подтверждаем проходом (срез 23).
CLAIMED_CHECK_NOTE = "🔍 Осталось подтвердить знакомое: «{name}» — пара быстрых вопросов."
```

Хелперы перед `handle_turn`:

```python
def _is_claimed(route: Route | None, node_id: str) -> bool:
    """Заявлен ли узел в анкете и ещё не подтверждён (по снимку маршрута)."""
    return route is not None and any(
        step.concept_id == node_id and step.status == "claimed" for step in route.steps
    )


def _checking(state: SessionState, node_id: str) -> SessionState:
    """Состояние входа в проверочный проход по заявленному узлу."""
    return state.model_copy(
        update={
            "current_node_id": node_id,
            "mode": "verify",
            "node_streak": 0,
            "hint_level": 0,
            "task_hinted": False,
            "pending_item_id": None,
            "phase": "explain",
            "verify_item_ids": [],
            "lesson_item_ids": [],
        }
    )


def _enter_claimed(
    conn: sqlite3.Connection,
    session_id: int,
    graph: CourseGraph,
    state: SessionState,
    route: Route,
    node_id: str,
    *,
    user_text: str,
    now: float,
    settings: Settings,
) -> TurnReply:
    """Ход входа в заявленный узел: объявление и первое задание прохода.

    Модель не зовём: ученик сказал, что тему знает, — объяснять нечего, нужно
    подтвердить. Провал прохода сам уведёт узел в обычный урок.
    """
    checking = _checking(state.model_copy(update={"route": route}), node_id)
    new_state, task_text, options = _issue_task(
        conn, graph, checking, now=now, settings=settings
    )
    note = CLAIMED_CHECK_NOTE.format(name=graph.concept(node_id).name)
    tail = task_text if new_state.pending_item_id is not None else None
    text = note if tail else f"{note}\n\n{task_text}"
    fresh_route, _ = route_mod.refresh(conn, new_state, graph, now=now, settings=settings)
    post_turn(
        conn,
        session_id,
        user_text=user_text,
        assistant_text=f"{text}\n\n{tail}" if tail else text,
        state=new_state.model_copy(update={"route": fresh_route}),
        now=now,
    )
    return TurnReply(text=text, options=options if tail else None, tail=tail)
```

`handle_turn`, ветка входа (сейчас `if node_id is None: entering = False else:
state = …`):

```python
            node_id = route_mod.next_node_id(conn, graph, route, now=stamp, settings=s)
            if node_id is None:
                entering = False  # маршрут исчерпан — входить некуда
            elif _is_claimed(route, node_id):
                # Заявленное в анкете не объясняем — подтверждаем проходом.
                return _enter_claimed(
                    conn, session_id, graph, state, route, node_id,
                    user_text=user_text, now=stamp, settings=s,
                )
            else:
                state = state.model_copy(
                    update={"current_node_id": node_id, "route": route}
                )
```

`handle_turn`, после закрытия узла: условие объяснения следующего узла

```python
                if new_state.current_node_id is not None and new_state.mode != "verify":
```

(комментарий добавить: «следующий узел — заявленный: его не объясняем, а
проверяем; задание выдаст общий `_issue_task` ниже»).

`_close_node_if_ready` — конец функции:

```python
    if next_node_id is None:
        return new_state, f"✅ Тема «{closed_name}» закрыта — маршрут пройден до конца."
    next_name = graph.concept(next_node_id).name
    if _is_claimed(route, next_node_id):
        return (
            _checking(new_state, next_node_id),
            f"✅ Тема «{closed_name}» закрыта.\n\n"
            + CLAIMED_CHECK_NOTE.format(name=next_name),
        )
    return new_state, f"✅ Тема «{closed_name}» закрыта — идём дальше: {next_name}."
```

`resume_reply`: сигнатура `…, settings: Settings | None = None,
start_node_id: str | None = None,`; докстринг дополнить строкой
«``start_node_id`` — с какого узла начать, если текущего ещё нет (первый урок
после анкеты: тот, что объявлен в списке шагов).» Выбор узла:

```python
    node_id = (
        state.current_node_id
        or start_node_id
        or route_mod.next_node_id(conn, graph, route, now=stamp, settings=s)
    )
```

и сразу после ветки `if node_id is None: … return`:

```python
    if state.mode != "verify" and _is_claimed(route, node_id):
        return _enter_claimed(
            conn, session_id, graph, state, route, node_id,
            user_text=RESUME_KICKOFF_TEXT, now=stamp, settings=s,
        )
```

- [ ] **Step 4: Запустить набор**

Run: `uv run pytest -q`
Expected: всё зелёное.

- [ ] **Step 5: Commit**

```bash
git add src/llm_tutor/core/turn.py tests/test_turn.py
git commit -m "Срез 23: знакомое не пропускается — проверка заявленных узлов в конце"
```

---

### Task 23.3: Список шагов от фактического первого узла и сводка

**Files:**
- Modify: `src/llm_tutor/bot/start.py` (`begin_lesson`)
- Modify: `src/llm_tutor/bot/survey.py` (`summary_text` — строка про проверку)
- Test: `tests/test_bot_flows.py`, `tests/test_survey_view.py`

**Interfaces:**
- Consumes: `route_mod.upcoming`, `route_mod.next_node_id` (23.1),
  `resume_reply(..., start_node_id=...)` (23.2).
- Produces: `start.LESSON_LEAD`, `start.CHECK_LEAD`; `survey_bot.CLAIMED_NOTE`.

- [ ] **Step 1: Тесты**

`tests/test_survey_view.py`:

```python
def test_summary_promises_check_when_something_is_confident() -> None:
    answers = Progress().answer(survey.LEVEL_CONFIDENT).answer(1).answer(1).final_answers()

    assert survey_bot.CLAIMED_NOTE in survey_bot.summary_text(answers)


def test_summary_has_no_check_note_without_confident_blocks() -> None:
    answers = Progress().answer(survey.LEVEL_FROM_SCRATCH).final_answers()

    assert survey_bot.CLAIMED_NOTE not in survey_bot.summary_text(answers)
```

`tests/test_bot_flows.py` (рядом с тестами анкеты):

```python
def _lesson_node(conn) -> str:
    return repos.get_session_state(conn, repos.get_open_session(conn)).current_node_id


async def test_lesson_starts_where_steps_list_says(conn, settings) -> None:
    """Первый шаг списка — тот узел, с которого реально начался урок."""
    load_seed(conn)
    intro, state, click = await _begin(conn, settings)
    await _press(click, intro, state, survey_bot.GO_LABEL)
    await _press(click, intro, state, survey.LEVEL_OPTIONS[survey.LEVEL_CONFIDENT])
    await _press(click, intro, state, survey.SELF_LEVELS[1])
    await _press(click, intro, state, survey.SELF_LEVELS[0])

    steps_text = next(text for text, _ in intro.sent if text.startswith("📋"))
    first_line = next(line for line in steps_text.splitlines() if line.startswith("1. "))
    node = CourseGraph.load(conn).concept(_lesson_node(conn))
    assert node.name in first_line
    assert _lesson_node(conn) not in survey.claimed_concepts(conn)


async def test_all_confident_starts_with_check(conn, settings) -> None:
    load_seed(conn)
    intro, state, click = await _begin(conn, settings)
    await _press(click, intro, state, survey_bot.GO_LABEL)
    await _press(click, intro, state, survey.LEVEL_OPTIONS[survey.LEVEL_CONFIDENT])
    await _press(click, intro, state, survey.SELF_LEVELS[3])
    await _press(click, intro, state, survey.SELF_LEVELS[3])

    steps_text = next(text for text, _ in intro.sent if text.startswith("📋"))
    assert "Начинаем с проверки" in steps_text
    assert "🔍" in steps_text
    assert "Проверка" in intro.sent[-1][0]  # первое задание прохода
```

(импорт `from llm_tutor.course.graph import CourseGraph`, если его нет.)

- [ ] **Step 2: Запустить — должно упасть**

Run: `uv run pytest tests/test_survey_view.py tests/test_bot_flows.py -q -k "summary or steps_list or confident"`
Expected: FAIL — нет `CLAIMED_NOTE`; «Начинаем с проверки» не найдено.

- [ ] **Step 3: `src/llm_tutor/bot/survey.py`**

```python
CLAIMED_NOTE = "Знакомое не пропускаю — в конце проверим коротким тестом."
```

в конце `summary_text`:

```python
    text = "✅ Понял тебя:\n" + "\n".join(lines)
    if any(index == survey.CONFIDENT_INDEX for index in answers.values()):
        text = f"{text}\n\n{CLAIMED_NOTE}"
    return text
```

- [ ] **Step 4: `src/llm_tutor/bot/start.py`, `begin_lesson`**

Константы рядом с `INTRO_TEXT`:

```python
LESSON_LEAD = "Начинаем с «{name}» — сейчас коротко объясню и покажу пример."
CHECK_LEAD = "Начинаем с проверки «{name}» — пара быстрых вопросов."
```

Тело `begin_lesson` после `await state.clear()`:

```python
    graph = CourseGraph.load(conn)
    route = route_mod.build_route(
        conn,
        graph,
        goal_concept_id=route_mod.goal_for(conn, graph),
        settings=settings,
    )
    # Первый шаг списка — ровно тот узел, с которого начнётся урок: его же
    # передаём в resume_reply, иначе планировщик мог бы выбрать другой.
    first = route_mod.next_node_id(conn, graph, route, settings=settings)
    upcoming = route_mod.upcoming(route, first, limit=render.PLAN_STEPS)
    if not upcoming:
        await message.answer(
            "Всё доступное уже освоено — можно свериться: /plan.",
            reply_markup=menu.main_menu(),
        )
        return

    head = (
        f"📋 Ближайшие {len(upcoming)} шагов:"
        if len(upcoming) == render.PLAN_STEPS
        else "📋 Что впереди:"
    )
    name = render.escape(graph.concept(first).name)
    lead = (CHECK_LEAD if upcoming[0].status == "claimed" else LESSON_LEAD).format(name=name)
    await message.answer(
        f"{head}\n\n{render.render_steps(graph, upcoming)}\n\n{lead}",
        parse_mode=render.PARSE_MODE,
        reply_markup=menu.main_menu(),
    )
    from llm_tutor.bot.handlers import _send_reply

    async with typing_action(message):
        reply = await resume_reply(
            conn, client, model, settings=settings, start_node_id=first
        )
    await _send_reply(conn, message, reply)
```

(`render_steps` теперь с пометками по умолчанию: у незаявленных шагов
пометки нет, у заявленных — «🔍 ».)

- [ ] **Step 5: Запустить набор**

Run: `uv run pytest -q`
Expected: всё зелёное.

- [ ] **Step 6: Commit**

```bash
git add src/llm_tutor/bot/start.py src/llm_tutor/bot/survey.py tests/test_bot_flows.py tests/test_survey_view.py
git commit -m "Срез 23: список шагов от фактического первого узла, 🔍 для знакомого"
```

---

### Task 23.4: Профиль и глубина объяснения в промпте

**Files:**
- Modify: `src/llm_tutor/student/survey.py` (`self_assessment`, `block_level`)
- Modify: `src/llm_tutor/core/context.py:44-46,116-150` (`_PROFILE_KEYS`,
  `_system_prompt`)
- Modify: `src/llm_tutor/llm/prompts.py:73-91,169-187`
  (`_TUTOR_TURN_RULES`, `format_profile_block`, `format_state_block`)
- Test: `tests/test_survey.py`, `tests/test_context.py`, `tests/test_prompts.py`

**Interfaces:**
- Produces:
  - `survey.self_assessment(conn) -> dict[str, str]` — `{название блока: ответ}`
  - `survey.block_level(conn, concept_id: str) -> str | None`
  - `format_profile_block(facts)` — заголовок «Самооценка ученика (анкета):»
  - `format_state_block(*, node_name, mode_label, hint_level, self_level: str | None = None)`

- [ ] **Step 1: Тесты**

`tests/test_survey.py`:

```python
def test_self_assessment_uses_block_titles(conn, settings) -> None:
    load_seed(conn)
    survey.apply_answers(conn, {FIRST.key: 2}, now=0.0, settings=settings)

    assert survey.self_assessment(conn) == {FIRST.title: survey.SELF_LEVELS[2]}


def test_block_level_of_concept(conn, settings) -> None:
    load_seed(conn)
    survey.apply_answers(conn, {"block_analysis": 1}, now=0.0, settings=settings)

    assert survey.block_level(conn, "groupby") == survey.SELF_LEVELS[1]
    assert survey.block_level(conn, "python_basics") is None
```

`tests/test_context.py` — в `test_system_prompt_carries_profile_state_and_mastery`
строку `repos.set_fact(conn, "goal", "пройти тему 1")` заменить на
`repos.set_fact(conn, "block_analysis", "С подсказками")`, а
`assert "пройти тему 1" in system` — на:

```python
    assert "Группировки и EDA: С подсказками" in system  # профиль из анкеты
    assert "самооценка по этой теме: С подсказками" in system
```

`tests/test_prompts.py` (в конец; импорт `tutor_system_prompt` уже есть или
добавить `from llm_tutor.llm.prompts import tutor_system_prompt`):

```python
def test_tutor_rules_tie_depth_to_self_assessment() -> None:
    assert "по самооценке" in tutor_system_prompt(0)
```

- [ ] **Step 2: Запустить — должно упасть**

Run: `uv run pytest tests/test_survey.py tests/test_context.py tests/test_prompts.py -q`
Expected: FAIL — нет `self_assessment`; профиль в промпте отсутствует.

- [ ] **Step 3: Реализация**

`src/llm_tutor/student/survey.py` — после `claimed_concepts`:

```python
def self_assessment(conn: sqlite3.Connection) -> dict[str, str]:
    """Самооценка по блокам для промпта: название блока → ответ анкеты."""
    return {
        block.title: value
        for block in BLOCKS
        if (value := repos.get_fact(conn, block.key)) is not None
    }


def block_level(conn: sqlite3.Connection, concept_id: str) -> str | None:
    """Ответ анкеты по блоку, в который входит узел (``None`` — не отвечал)."""
    for block in BLOCKS:
        if concept_id in block.concepts:
            return repos.get_fact(conn, block.key)
    return None
```

`src/llm_tutor/llm/prompts.py`:

- в `_TUTOR_TURN_RULES` после строки «Проверяй понимание задачей…» добавить:
  ```python
    "- Глубину объяснения подбирай по самооценке темы: «Впервые вижу» и «Знаю "
    "в теории» — с азов и с аналогией; «С подсказками» — коротко, без азов, с "
    "акцентом на тонкостях; «Уверенно» — ученик не подтвердил проверку: "
    "разбирай именно то, на чём он споткнулся.\n"
  ```
- `format_profile_block`:
  ```python
  def format_profile_block(facts: Mapping[str, str]) -> str:
      """Самооценка ученика из анкеты: блок темы → ответ."""
      if not facts:
          return ""
      lines = [f"- {key}: {value}" for key, value in facts.items()]
      return "Самооценка ученика (анкета):\n" + "\n".join(lines)
  ```
- `format_state_block` — параметр `self_level: str | None = None` и строка
  после режима:
  ```python
      if self_level:
          lines.append(f"- самооценка по этой теме: {self_level}")
  ```

`src/llm_tutor/core/context.py`: удалить `_PROFILE_KEYS` (строки 44–46);
импорт `from llm_tutor.student import beta, survey`; в `_system_prompt`:

```python
    # Профиль — самооценка из анкеты по блокам (срез 23). Раньше фильтр ждал
    # ключи старой анкеты и молча отсекал всё.
    profile = format_profile_block(survey.self_assessment(conn))
```

и в вызове `format_state_block` добавить
`self_level=survey.block_level(conn, state.current_node_id) if state.current_node_id else None,`.

- [ ] **Step 4: Запустить набор**

Run: `uv run pytest -q`
Expected: всё зелёное.

- [ ] **Step 5: Commit**

```bash
git add src/llm_tutor/student/survey.py src/llm_tutor/core/context.py src/llm_tutor/llm/prompts.py tests/test_survey.py tests/test_context.py tests/test_prompts.py
git commit -m "Срез 23: самооценка из анкеты доходит до тьютора и задаёт глубину"
```

---

### Task 23.5: E2E уверенного ученика и ревью среза 23

**Files:**
- Modify: `tests/test_e2e_topic01.py`

- [ ] **Step 1: E2E (в конец файла)**

```python
async def test_confident_student_skips_known_blocks(conn, settings) -> None:
    """/start → «Уверенно работаю с pandas» → два ответа → урок не с азов."""
    load_seed(conn)
    router = make_survey_router(conn, settings, GradingTutor(conn, passed=True), "m")
    state = _fsm()
    message = await start_survey(FakeMessage(), state)
    click = _named(router, "callback_query", "on_survey_click")

    def data_of(label: str) -> str:
        return next(
            button.callback_data
            for row in message.reply_markup.inline_keyboard
            for button in row
            if button.text == label
        )

    await click(FakeCallback(GO_DATA, message), state)
    await click(FakeCallback(data_of(survey.LEVEL_OPTIONS[survey.LEVEL_CONFIDENT]), message), state)
    await click(FakeCallback(data_of(survey.SELF_LEVELS[1]), message), state)
    await click(FakeCallback(data_of(survey.SELF_LEVELS[0]), message), state)

    assert message.text.startswith("✅ Понял тебя")
    steps = next(text for text, _ in message.sent if text.startswith("📋"))
    assert "🔍" in steps  # знакомое — в хвосте списка, на проверку
    current = _state(conn).current_node_id
    assert current not in survey.claimed_concepts(conn)
    assert _state(conn).pending_item_id is not None  # урок начался с задания
```

- [ ] **Step 2: Запустить**

Run: `uv run pytest tests/test_e2e_topic01.py -q`
Expected: PASS (все части уже реализованы; если падает — это дефект 23.1–23.3,
чинить там с отдельным тестом).

- [ ] **Step 3: Commit**

```bash
git add tests/test_e2e_topic01.py
git commit -m "Срез 23: сквозной сценарий уверенного ученика"
```

- [ ] **Step 4: Адверсариальное ревью среза 23** — как 22.6. Сфера:
  `student/route.py`, `core/turn.py` (вход в `claimed`, закрытие с переходом в
  проверку), `bot/start.py`, `bot/themes.py`, `core/context.py`. Фокус: Review
  Focus 4–5; переход «заявленный → провал → урок → закрытие»; выбор заявленной
  темы вручную через 🎚 Темы; «закрой тему» текстом на заявленном узле;
  потеря снимка маршрута (новая сессия); сверка «объявленный первый шаг =
  начатый» по всем путям анкеты. Фиксы — коммит `Аудит среза 23: …`.
