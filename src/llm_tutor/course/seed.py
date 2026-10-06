"""Загрузка seed-графа курса в БД (Срез 4.1).

Seed — источник графа концептов и рёбер темы (data/seed_topic01.json).
Перед записью граф валидируется (ссылки на существующие узлы + отсутствие
циклов), чтобы битый seed падал сразу, а не ломал планировщик позже.

CLI: ``python -m llm_tutor.course.seed [--seed PATH] [--db PATH]``.
"""

import json
import os
import sqlite3
from pathlib import Path

from pydantic import BaseModel

from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.schemas import Concept, Edge

# Корень проекта (src/llm_tutor/course/seed.py → src → корень).
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SEED_PATH = _PROJECT_ROOT / "data" / "seed_topic01.json"


class Seed(BaseModel):
    """Содержимое seed-файла: узлы и рёбра графа курса."""

    course: str
    nodes: list[Concept]
    edges: list[Edge]


def load_seed_data(path: str | Path = DEFAULT_SEED_PATH) -> Seed:
    """Читает и валидирует seed-файл (без записи в БД)."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"Seed-файл не найден: {source}")
    raw = json.loads(source.read_text(encoding="utf-8"))
    seed = Seed.model_validate(raw)
    CourseGraph(seed.nodes, seed.edges)  # падает на цикле/дубле/битой ссылке
    return seed


def load_seed(conn: sqlite3.Connection, path: str | Path = DEFAULT_SEED_PATH) -> Seed:
    """Идемпотентно приводит граф в БД к seed-файлу (истина — seed)."""
    seed = load_seed_data(path)
    repos.replace_graph(conn, seed.nodes, seed.edges)
    return seed


def main(argv: list[str] | None = None) -> int:
    """CLI: загрузить seed-граф в БД."""
    import argparse

    from llm_tutor.db.connection import get_conn, migrate

    parser = argparse.ArgumentParser(description="Загрузка seed-графа курса в БД.")
    parser.add_argument("--seed", default=str(DEFAULT_SEED_PATH), help="путь к seed-файлу")
    parser.add_argument(
        "--db",
        default=os.environ.get("DB_PATH", "data/llm_tutor.sqlite3"),
        help="путь к БД (по умолчанию DB_PATH или data/llm_tutor.sqlite3)",
    )
    args = parser.parse_args(argv)

    conn = get_conn(args.db)
    try:
        migrate(conn)
        seed = load_seed(conn, args.seed)
    finally:
        conn.close()

    print(f"Загружено: {len(seed.nodes)} концептов, {len(seed.edges)} рёбер ({seed.course})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
