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
