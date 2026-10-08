"""Анкета модуля: профиль в ``facts`` + слабый априор (срезы 22, 26).

Модель ученика стартует с априора Beta(1,1) — «не знаем». Анкета не заменяет
диагностику, а даёт лишь слабые свидетельства ``source='self'``: их вес мал
(``self_evidence_weight``), чтобы самооценка не подменяла реальные задания.

Анкета адаптивная (срез 22): сначала общий уровень, потом только нужные
вопросы по блокам. С среза 26 у каждого модуля своя анкета — вопросы и блоки
лежат в seed модуля (``schemas.SurveyConfig``); здесь только правила ветвления,
чистые функции без aiogram.
"""

import sqlite3
import time
from dataclasses import dataclass

from llm_tutor.config import Settings, get_settings
from llm_tutor.db import repos
from llm_tutor.schemas import SurveyBlock, SurveyConfig
from llm_tutor.student import self_report

# Ключ факта цели: анкетой не заполняется, но маршрут его читает.
GOAL_CONCEPT_KEY = "goal_concept_id"

# Градации самооценки по блоку — общие для всех модулей; короткие, чтобы
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

# Варианты вопроса об общем уровне — по индексу; тексты у каждого модуля свои.
LEVEL_FROM_SCRATCH, LEVEL_SOME, LEVEL_CONFIDENT = 0, 1, 2


def asked_blocks(config: SurveyConfig, level: int) -> tuple[SurveyBlock, ...]:
    """Блоки, о которых спрашиваем на ветке общего уровня ``level``."""
    if level == LEVEL_FROM_SCRATCH:
        return ()
    if level == LEVEL_CONFIDENT:
        return tuple(
            block for block in config.blocks if block.key not in config.assumed_by_confident
        )
    return config.blocks


def options_for(config: SurveyConfig, key: str) -> tuple[str, ...]:
    """Варианты ответа на вопрос с ключом ``key``."""
    return config.level_options if key == config.level_key else SELF_LEVELS


@dataclass(frozen=True)
class Progress:
    """Ход анкеты модуля: ответы в том порядке, в котором их дали (для «Назад»).

    Неизменяемый: каждый ответ и «Назад» дают новый объект — FSM хранит
    ``given``, а не мутирует общее состояние.
    """

    config: SurveyConfig
    given: tuple[tuple[str, int], ...] = ()

    @property
    def step(self) -> int:
        """Сколько ответов дано — номер текущего вопроса (с нуля)."""
        return len(self.given)

    @property
    def level(self) -> int | None:
        """Ответ на вопрос об общем уровне (``None`` — ещё не дан)."""
        return dict(self.given).get(self.config.level_key)

    @property
    def answers(self) -> dict[str, int]:
        """Ответы по блокам, данные учеником (без достроенных)."""
        return {key: index for key, index in self.given if key != self.config.level_key}

    def next_key(self) -> str | None:
        """Ключ следующего вопроса; ``None`` — анкета кончилась."""
        level = self.level
        if level is None:
            return self.config.level_key
        answers = self.answers
        if any(answers.get(key) == UNKNOWN_INDEX for key in self.config.foundation):
            return None
        for block in asked_blocks(self.config, level):
            if block.key not in answers:
                return block.key
        return None

    def answer(self, index: int) -> "Progress":
        """Новый ход анкеты с ответом ``index`` на текущий вопрос."""
        key = self.next_key()
        if key is None:
            raise ValueError("Анкета уже кончилась — отвечать не на что")
        if not 0 <= index < len(options_for(self.config, key)):
            raise ValueError(f"Нет варианта {index} у вопроса {key!r}")
        return Progress(self.config, (*self.given, (key, index)))

    def back(self) -> "Progress":
        """Новый ход анкеты без последнего ответа."""
        return Progress(self.config, self.given[:-1])

    def position(self) -> tuple[int, int] | None:
        """(номер, всего) для вопроса по блоку; у вопроса об уровне — ``None``."""
        key = self.next_key()
        if key is None or key == self.config.level_key:
            return None
        return len(self.answers) + 1, len(asked_blocks(self.config, self.level))

    def final_answers(self) -> dict[str, int]:
        """Ответы на все блоки модуля: данные учеником плюс достроенные правилом."""
        answers = self.answers
        result: dict[str, int] = {}
        for block in self.config.blocks:
            if block.key in answers:
                result[block.key] = answers[block.key]
            elif self.level == LEVEL_CONFIDENT and block.key in self.config.assumed_by_confident:
                result[block.key] = CONFIDENT_INDEX
            else:
                # «С нуля» или ранний выход на фундаменте — блок незнаком.
                result[block.key] = UNKNOWN_INDEX
        return result


def config_for(conn: sqlite3.Connection, topic_id: int) -> SurveyConfig | None:
    """Анкета модуля из БД (``None`` — такого модуля нет)."""
    topic = repos.get_topic(conn, topic_id)
    return topic.survey if topic is not None else None


def apply_answers(
    conn: sqlite3.Connection,
    config: SurveyConfig,
    answers: dict[str, int],
    *,
    level: int | None = None,
    now: float | None = None,
    settings: Settings | None = None,
) -> None:
    """Сохраняет ответы анкеты модуля в ``facts`` и раздаёт слабый априор.

    ``answers`` — ключ блока → индекс в ``SELF_LEVELS``; ``level`` — ответ на
    вопрос об общем уровне (только факт, априора не даёт). Повторное
    применение факты перезаписывает, но априор **не** начисляет заново:
    иначе самооценка накрутила бы счётчики.
    """
    s = settings or get_settings()
    stamp = time.time() if now is None else now

    if level is not None:
        if not 0 <= level < len(config.level_options):
            raise ValueError(f"Нет варианта {level} у вопроса об уровне")
        repos.set_fact(conn, config.level_key, config.level_options[level], source="self")

    for key, index in answers.items():
        block = config.block(key)
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


def is_completed(conn: sqlite3.Connection, config: SurveyConfig) -> bool:
    """Прошёл ли ученик анкету модуля: ответы есть на все её блоки."""
    return all(repos.get_fact(conn, block.key) is not None for block in config.blocks)


def claimed_concepts(
    conn: sqlite3.Connection, config: SurveyConfig | None = None
) -> frozenset[str]:
    """Темы блоков, которые ученик назвал знакомыми («Уверенно»).

    ``config`` — один модуль; ``None`` — все модули курса. Самооценка тему не
    закрывает: маршрут пропускает её вперёд и проверяет проходом (срез 23).
    """
    configs = (
        [config] if config is not None else [topic.survey for topic in repos.get_topics(conn)]
    )
    confident = SELF_LEVELS[CONFIDENT_INDEX]
    return frozenset(
        concept_id
        for cfg in configs
        for block in cfg.blocks
        if repos.get_fact(conn, block.key) == confident
        for concept_id in block.concepts
    )


def self_assessment(conn: sqlite3.Connection, config: SurveyConfig) -> dict[str, str]:
    """Самооценка по блокам модуля для промпта: название блока → ответ анкеты."""
    return {
        block.title: value
        for block in config.blocks
        if (value := repos.get_fact(conn, block.key)) is not None
    }


def block_level(conn: sqlite3.Connection, concept_id: str) -> str | None:
    """Ответ анкеты по блоку, в который входит тема (``None`` — не отвечал)."""
    for topic in repos.get_topics(conn):
        for block in topic.survey.blocks:
            if concept_id in block.concepts:
                return repos.get_fact(conn, block.key)
    return None
