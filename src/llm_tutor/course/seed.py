"""Загрузка seed-графа курса в БД (Срез 4.1).

Seed — источник графа концептов и рёбер темы (data/seed_topic01.json).
Перед записью граф валидируется (ссылки на существующие узлы + отсутствие
циклов), чтобы битый seed падал сразу, а не ломал планировщик позже.

CLI: ``python -m llm_tutor.course.seed [--seed PATH] [--db PATH]``.
"""

import json
import sqlite3
from pathlib import Path

from pydantic import BaseModel, Field

from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.schemas import Concept, Criterion, Edge, Item, Rubric

# Корень проекта (src/llm_tutor/course/seed.py → src → корень).
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SEED_PATH = _PROJECT_ROOT / "data" / "seed_topic01.json"


class Seed(BaseModel):
    """Содержимое seed-файла: граф курса, банк заданий и рубрики."""

    course: str
    nodes: list[Concept]
    edges: list[Edge]
    items: list[Item] = Field(default_factory=list)
    rubrics: list[Rubric] = Field(default_factory=list)
    criteria: list[Criterion] = Field(default_factory=list)
    # Конспекты узлов: не из JSON, а из файлов data/theory/<тема>/<узел>.md.
    theory: dict[str, str] = Field(default_factory=dict)


class SeedError(ValueError):
    """Seed внутренне несогласован: битые ссылки между его разделами."""


def _check_references(seed: Seed) -> None:
    """Проверяет ссылки между разделами seed.

    Опечатка в id рубрики или концепта иначе всплыла бы сырым ``IntegrityError``
    на старте бота (или, хуже, падением при ответе ученика) — а не подсказкой
    автору, который правит банк руками.
    """
    # Дубли id молча схлопнулись бы в upsert, и содержимое тихо разошлось бы
    # с файлом. Узлы и рёбра проверяет CourseGraph.
    for title, ids in (
        ("рубрик", [rubric.id for rubric in seed.rubrics]),
        ("критериев", [criterion.id for criterion in seed.criteria]),
        ("заданий", [item.id for item in seed.items]),
    ):
        if len(set(ids)) != len(ids):
            raise SeedError(f"Дубли id {title}: {sorted(ids)}")

    rubric_ids = {rubric.id for rubric in seed.rubrics}
    node_ids = {node.id for node in seed.nodes}

    for criterion in seed.criteria:
        if criterion.rubric_id not in rubric_ids:
            raise SeedError(
                f"Критерий {criterion.id} ссылается на неизвестную рубрику "
                f"{criterion.rubric_id}"
            )
    for item in seed.items:
        if item.rubric_id is not None and item.rubric_id not in rubric_ids:
            raise SeedError(
                f"Задание {item.id} ссылается на неизвестную рубрику {item.rubric_id}"
            )
        unknown = sorted(set(item.concept_weights) - node_ids)
        if unknown:
            raise SeedError(
                f"Задание {item.id} ссылается на неизвестные концепты: {unknown}"
            )


def theory_dir_for(seed_path: str | Path) -> Path:
    """Каталог конспектов seed-файла: ``seed_topic01.json`` → ``theory/topic01``."""
    source = Path(seed_path)
    return source.parent / "theory" / source.stem.removeprefix("seed_")


def _read_theory(seed_path: Path, node_ids: list[str]) -> dict[str, str]:
    """Конспекты узлов из файлов; файл без узла в графе не читается."""
    directory = theory_dir_for(seed_path)
    theory: dict[str, str] = {}
    for node_id in node_ids:
        path = directory / f"{node_id}.md"
        if path.is_file():
            text = path.read_text(encoding="utf-8").strip()
            if text:
                theory[node_id] = text
    return theory


def load_seed_data(path: str | Path = DEFAULT_SEED_PATH) -> Seed:
    """Читает и валидирует seed-файл с конспектами (без записи в БД)."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"Seed-файл не найден: {source}")
    raw = json.loads(source.read_text(encoding="utf-8"))
    seed = Seed.model_validate(raw)
    CourseGraph(seed.nodes, seed.edges)  # падает на цикле/дубле/битой ссылке
    _check_references(seed)
    seed.theory = _read_theory(source, [node.id for node in seed.nodes])
    return seed


def load_seed(conn: sqlite3.Connection, path: str | Path = DEFAULT_SEED_PATH) -> Seed:
    """Идемпотентно приводит граф в БД к seed-файлу (истина — seed)."""
    seed = load_seed_data(path)
    repos.replace_seed(
        conn, seed.nodes, seed.edges, seed.items, seed.rubrics, seed.criteria, seed.theory
    )
    return seed


def nodes_without_theory(seed: Seed) -> list[str]:
    """Узлы без конспекта — вкладка «Теория» у них пуста."""
    return [node.id for node in seed.nodes if node.id not in seed.theory]


def items_without_rubric(seed: Seed) -> list[int]:
    """Открытые и код-задания без рубрики: проверить их нечем, но они грузятся.

    Задание не выдаётся (рубрики нет), но автору об этом надо сказать вслух —
    иначе вопрос просто пропадёт из практики.
    """
    return sorted(
        item.id
        for item in seed.items
        if item.answer_type in ("open", "code") and item.rubric_id is None
    )


def nodes_without_items(seed: Seed) -> list[str]:
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
    """CLI: загрузить seed-граф в БД."""
    import argparse

    from llm_tutor.db.connection import get_conn, migrate

    parser = argparse.ArgumentParser(description="Загрузка seed-графа курса в БД.")
    parser.add_argument("--seed", default=str(DEFAULT_SEED_PATH), help="путь к seed-файлу")
    parser.add_argument(
        "--db",
        # Веб грузит seed в БД ученика сам; CLI — проверка seed (что без
        # заданий, рубрик, конспектов) или загрузка в указанную БД.
        default=":memory:",
        help="путь к БД (по умолчанию — в памяти: только проверка seed)",
    )
    args = parser.parse_args(argv)

    conn = get_conn(args.db)
    try:
        migrate(conn)
        seed = load_seed(conn, args.seed)
    finally:
        conn.close()

    print(
        f"Загружено: {len(seed.nodes)} концептов, {len(seed.edges)} рёбер, "
        f"{len(seed.items)} заданий ({seed.course})"
    )
    missing = nodes_without_items(seed)
    if missing:
        print(f"Без заданий (урок их не проверит): {', '.join(missing)}")
    without_rubric = items_without_rubric(seed)
    if without_rubric:
        print(f"Открытые задания без рубрики (проверить нечем): {without_rubric}")
    no_theory = nodes_without_theory(seed)
    if no_theory:
        print(f"Без конспекта (вкладка «Теория» пуста): {', '.join(no_theory)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
