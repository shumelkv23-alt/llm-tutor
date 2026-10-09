"""Загрузка курса в БД: seed-файлы модулей (срез 4.1, модули — срез 24).

Курс — модули mlcourse.ai (сейчас пять, номера до 10), по seed-файлу на модуль
(``data/seed_topicNN.json``). В БД все файлы ложатся ОДНИМ проходом
``replace_seed``: по очереди нельзя — каждый следующий погасил бы предыдущий.
Перед записью курс валидируется целиком: ссылки между модулями, рёбра только
назад (``CourseGraph``), анкета модуля накрывает его темы, циклов нет.

CLI: ``python -m llm_tutor.course.seed [--seed PATH] [--db PATH]``.
"""

import json
import os
import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.schemas import Concept, Criterion, Edge, Item, Rubric, Topic

# Корень проекта (src/llm_tutor/course/seed.py → src → корень).
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = _PROJECT_ROOT / "data"
DEFAULT_SEED_PATH = DATA_DIR / "seed_topic01.json"
# Имя seed-файла модуля: seed_topicNN.json (суффикс — для тестовых модулей).
_SEED_NAME_RE = re.compile(r"seed_topic(\d{2})(?:_\w+)?\.json")
# В курс из data/ идут только файлы без суффикса: черновики и бэкапы
# (seed_topic02_draft.json) рядом с модулем в него не попадают.
_COURSE_NAME_RE = re.compile(r"seed_topic\d{2}\.json")


class Seed(BaseModel):
    """Seed-файл модуля: модуль, его граф, банк заданий и рубрики."""

    course: str
    topic: Topic
    nodes: list[Concept]
    edges: list[Edge]
    items: list[Item] = Field(default_factory=list)
    rubrics: list[Rubric] = Field(default_factory=list)
    criteria: list[Criterion] = Field(default_factory=list)


@dataclass(frozen=True)
class Course:
    """Курс целиком: модули и склеенные разделы их seed-файлов."""

    topics: tuple[Topic, ...]
    nodes: tuple[Concept, ...]
    edges: tuple[Edge, ...]
    items: tuple[Item, ...]
    rubrics: tuple[Rubric, ...]
    criteria: tuple[Criterion, ...]
    # Не ошибки, но автору стоит посмотреть: жёсткие межмодульные рёбра.
    warnings: tuple[str, ...] = ()


class SeedError(ValueError):
    """Курс внутренне несогласован: битые ссылки между разделами или модулями."""


def _check_unique(title: str, ids: Sequence) -> None:
    """Дубли id молча схлопнулись бы в upsert — содержимое разошлось бы с файлом."""
    if len(set(ids)) != len(ids):
        dups = sorted({value for value in ids if ids.count(value) > 1}, key=str)
        raise SeedError(f"Дубли id {title}: {dups}")


def _check_references(
    nodes: Sequence[Concept],
    items: Sequence[Item],
    rubrics: Sequence[Rubric],
    criteria: Sequence[Criterion],
) -> None:
    """Проверяет ссылки между разделами курса.

    Опечатка в id рубрики или концепта иначе всплыла бы сырым ``IntegrityError``
    на старте бота (или, хуже, падением при ответе ученика) — а не подсказкой
    автору, который правит банк руками.
    """
    for title, ids in (
        ("узлов", [node.id for node in nodes]),
        ("рубрик", [rubric.id for rubric in rubrics]),
        ("критериев", [criterion.id for criterion in criteria]),
        ("заданий", [item.id for item in items]),
    ):
        _check_unique(title, ids)

    rubric_ids = {rubric.id for rubric in rubrics}
    node_ids = {node.id for node in nodes}
    for criterion in criteria:
        if criterion.rubric_id not in rubric_ids:
            raise SeedError(
                f"Критерий {criterion.id} ссылается на неизвестную рубрику "
                f"{criterion.rubric_id}"
            )
    for item in items:
        if item.rubric_id is not None and item.rubric_id not in rubric_ids:
            raise SeedError(
                f"Задание {item.id} ссылается на неизвестную рубрику {item.rubric_id}"
            )
        unknown = sorted(set(item.concept_weights) - node_ids)
        if unknown:
            raise SeedError(
                f"Задание {item.id} ссылается на неизвестные концепты: {unknown}"
            )


def _check_topics(seeds: Sequence[Seed]) -> list[str]:
    """Правила модулей; отдаёт предупреждения (жёсткие межмодульные рёбра).

    Направление рёбер (только назад) проверяет ``CourseGraph`` — здесь его
    повторять незачем.
    """
    _check_unique("модулей", [seed.topic.number for seed in seeds])
    keys = [key for seed in seeds for key in seed.topic.survey.keys]
    if len(set(keys)) != len(keys):
        dups = sorted({key for key in keys if keys.count(key) > 1})
        raise SeedError(f"Дубли ключей анкеты между модулями: {dups}")

    topic_of = {node.id: node.topic_id for seed in seeds for node in seed.nodes}
    warnings: list[str] = []
    for seed in seeds:
        number = seed.topic.number
        if not seed.nodes:
            raise SeedError(f"В модуле {number} нет тем")
        for edge in seed.edges:
            src, dst = topic_of[edge.from_id], topic_of[edge.to_id]
            # Модуль отвечает только за входы в свои темы: иначе файл модуля 2
            # мог бы поменять порядок и блокировки модуля 1.
            if dst != number:
                raise SeedError(
                    f"Ребро {edge.from_id} -> {edge.to_id} в файле модуля {number} "
                    f"ведёт в тему чужого модуля {dst}"
                )
            if edge.type == "requires" and edge.hard and src < dst:
                warnings.append(
                    f"Жёсткое межмодульное ребро {edge.from_id} -> {edge.to_id} "
                    f"(модуль {src} -> {dst})"
                )
        own = {node.id for node in seed.nodes}
        covered = [c for block in seed.topic.survey.blocks for c in block.concepts]
        foreign = sorted(set(covered) - own)
        if foreign:
            raise SeedError(f"Анкета модуля {number} ссылается на чужие темы: {foreign}")
        missing = sorted(own - set(covered))
        if missing:
            raise SeedError(f"Анкета модуля {number} не накрывает темы: {missing}")
        twice = sorted({c for c in covered if covered.count(c) > 1})
        if twice:
            raise SeedError(f"Темы в нескольких блоках анкеты модуля {number}: {twice}")
    return warnings


def _parse_seed(path: str | Path) -> Seed:
    """Читает seed-файл модуля; темам проставляет модуль по файлу."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"Seed-файл не найден: {source}")
    try:
        seed = Seed.model_validate(json.loads(source.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, ValidationError) as error:
        # Без имени файла автор искал бы опечатку во всех десяти модулях.
        raise SeedError(f"{source.name}: {error}") from error
    number = seed.topic.number
    match = _SEED_NAME_RE.fullmatch(source.name)
    if match and int(match.group(1)) != number:
        raise SeedError(
            f"{source.name}: в файле модуль {number}, а в имени — {int(match.group(1))}"
        )
    # Модуль темы задаёт файл, а не поле темы: тема не «уедет» в чужой модуль.
    return seed.model_copy(
        update={"nodes": [node.model_copy(update={"topic_id": number}) for node in seed.nodes]}
    )


def build_course(seeds: Sequence[Seed]) -> Course:
    """Склеивает модули и валидирует курс целиком (``SeedError`` на ошибке)."""
    ordered = sorted(seeds, key=lambda seed: seed.topic.number)
    nodes = [node for seed in ordered for node in seed.nodes]
    edges = [edge for seed in ordered for edge in seed.edges]
    items = [item for seed in ordered for item in seed.items]
    rubrics = [rubric for seed in ordered for rubric in seed.rubrics]
    criteria = [criterion for seed in ordered for criterion in seed.criteria]
    _check_references(nodes, items, rubrics, criteria)
    CourseGraph(nodes, edges)  # цикл, битое ребро, ребро вперёд между модулями
    warnings = _check_topics(ordered)
    return Course(
        topics=tuple(seed.topic for seed in ordered),
        nodes=tuple(nodes),
        edges=tuple(edges),
        items=tuple(items),
        rubrics=tuple(rubrics),
        criteria=tuple(criteria),
        warnings=tuple(warnings),
    )


def course_paths(data_dir: Path | None = None) -> list[Path]:
    """Seed-файлы модулей курса (черновики и прочие файлы не берутся)."""
    folder = DATA_DIR if data_dir is None else data_dir
    return sorted(
        path for path in folder.glob("seed_topic*.json") if _COURSE_NAME_RE.fullmatch(path.name)
    )


def load_course_data(paths: Sequence[str | Path] | None = None) -> Course:
    """Читает и валидирует модули курса (без записи в БД)."""
    chosen = course_paths() if paths is None else [Path(path) for path in paths]
    if not chosen:
        raise FileNotFoundError(f"Нет seed-файлов модулей в {DATA_DIR}")
    return build_course([_parse_seed(path) for path in chosen])


def load_seed_data(path: str | Path = DEFAULT_SEED_PATH) -> Seed:
    """Читает и валидирует один seed-файл как курс из одного модуля."""
    seed = _parse_seed(path)
    build_course([seed])
    return seed


def _apply(conn: sqlite3.Connection, course: Course) -> None:
    repos.replace_seed(
        conn,
        course.nodes,
        course.edges,
        course.items,
        course.rubrics,
        course.criteria,
        topics=course.topics,
    )


def load_course(
    conn: sqlite3.Connection, paths: Sequence[str | Path] | None = None
) -> Course:
    """Идемпотентно приводит курс в БД к seed-файлам модулей (истина — seed)."""
    course = load_course_data(paths)
    _apply(conn, course)
    return course


def load_seed(conn: sqlite3.Connection, path: str | Path = DEFAULT_SEED_PATH) -> Seed:
    """Один модуль как весь курс — для тестов и отладки одного файла.

    Всё, чего нет в файле, гасится: в том числе другие модули.
    """
    seed = _parse_seed(path)
    _apply(conn, build_course([seed]))
    return seed


def items_without_rubric(seed: Seed | Course) -> list[int]:
    """Открытые и код-задания без рубрики: проверить их нечем, но они грузятся.

    Задание не выдаётся (рубрики нет), но автору об этом надо сказать вслух —
    иначе вопрос просто пропадёт из практики.
    """
    return sorted(
        item.id
        for item in seed.items
        if item.answer_type in ("open", "code") and item.rubric_id is None
    )


def nodes_without_items(seed: Seed | Course) -> list[str]:
    """«Несущие» узлы без задания в банке — диагностика их проверить не сможет.

    Возвращаются только узлы с зависимыми: непроверенная база запирает весь
    маршрут, тогда как лист без задания — терпимо.
    """
    graph = CourseGraph(seed.nodes, seed.edges)
    covered = {concept_id for item in seed.items for concept_id in item.concept_weights}
    return sorted(
        node_id
        for node_id in graph.node_ids
        if node_id not in covered and graph.dependents(node_id)
    )


def main(argv: list[str] | None = None) -> int:
    """CLI: загрузить курс (или один seed-файл модуля) в БД."""
    import argparse

    from llm_tutor.db.connection import get_conn, migrate

    parser = argparse.ArgumentParser(description="Загрузка курса в БД.")
    parser.add_argument(
        "--seed",
        default=None,
        help=(
            "один seed-файл модуля — только для отладки на отдельной БД: "
            "остальные модули в ней гаснут; по умолчанию — все модули из data/"
        ),
    )
    parser.add_argument(
        "--db",
        default=None,
        help="путь к БД (по умолчанию DB_PATH или data/llm_tutor.sqlite3)",
    )
    args = parser.parse_args(argv)
    if args.seed is not None and args.db is None:
        # Один файл — весь курс: в рабочей БД погасли бы остальные модули.
        parser.error("--seed гасит остальные модули — укажи отдельную БД через --db")
    db = args.db or os.environ.get("DB_PATH", "data/llm_tutor.sqlite3")

    conn = get_conn(db)
    try:
        migrate(conn)
        course = load_course(conn, None if args.seed is None else [args.seed])
    finally:
        conn.close()

    print(
        f"Загружено: {len(course.topics)} модулей, {len(course.nodes)} концептов, "
        f"{len(course.edges)} рёбер, {len(course.items)} заданий"
    )
    for warning in course.warnings:
        print(f"Предупреждение: {warning}")
    missing = nodes_without_items(course)
    if missing:
        print(f"Без заданий (диагностика их не возьмёт): {', '.join(missing)}")
    without_rubric = items_without_rubric(course)
    if without_rubric:
        print(f"Открытые задания без рубрики (проверить нечем): {without_rubric}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
