"""Конспекты теории: миграция, загрузка, чтение, промпт (Срез 44)."""

import json
import sqlite3

from llm_tutor.core.context import build_context
from llm_tutor.course.seed import load_seed, load_seed_data, nodes_without_theory, theory_dir_for
from llm_tutor.db import repos
from llm_tutor.db.connection import MIGRATIONS_DIR, SCHEMA_VERSION, get_conn, migrate


def _seed_with_theory(tmp_path, theory: dict[str, str]):
    """Маленький seed из двух узлов и каталог конспектов рядом с ним."""
    seed = tmp_path / "seed_demo.json"
    seed.write_text(
        json.dumps(
            {
                "course": "demo",
                "nodes": [{"id": "a", "name": "Узел А"}, {"id": "b", "name": "Узел Б"}],
                "edges": [{"from_id": "a", "to_id": "b"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    directory = tmp_path / "theory" / "demo"
    directory.mkdir(parents=True)
    for node_id, text in theory.items():
        (directory / f"{node_id}.md").write_text(text, encoding="utf-8")
    return seed


def test_theory_dir_follows_seed_name(tmp_path) -> None:
    assert theory_dir_for(tmp_path / "seed_topic01.json") == tmp_path / "theory" / "topic01"


def test_migration_004_adds_columns_to_existing_db(tmp_path) -> None:
    """БД версии 3 с данными доходит до 4 без потерь."""
    path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(path)
    for name in ("001_init.sql", "002_seed_active.sql", "003_rubrics_active.sql"):
        conn.executescript((MIGRATIONS_DIR / name).read_text(encoding="utf-8"))
    conn.execute("PRAGMA user_version = 3")
    conn.execute("INSERT INTO concepts (id, name) VALUES ('x', 'Икс')")
    conn.commit()
    conn.close()

    upgraded = get_conn(str(path))
    migrate(upgraded)

    assert upgraded.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 4
    assert upgraded.execute("SELECT name, theory FROM concepts").fetchone()[:] == ("Икс", None)
    columns = {row[1] for row in upgraded.execute("PRAGMA table_info(items)")}
    assert {"starter", "setup", "tests"} <= columns
    upgraded.close()


def test_load_seed_reads_theory_files(conn, tmp_path) -> None:
    seed = _seed_with_theory(tmp_path, {"a": "# Узел А\n\nТекст конспекта."})

    load_seed(conn, seed)

    assert repos.get_theory(conn, "a") == "# Узел А\n\nТекст конспекта."
    assert repos.get_theory(conn, "b") is None
    assert repos.get_theory(conn, "nope") is None


def test_removed_theory_file_clears_text(conn, tmp_path) -> None:
    seed = _seed_with_theory(tmp_path, {"a": "старый текст"})
    load_seed(conn, seed)
    (tmp_path / "theory" / "demo" / "a.md").unlink()

    load_seed(conn, seed)

    assert repos.get_theory(conn, "a") is None


def test_nodes_without_theory(tmp_path) -> None:
    seed = _seed_with_theory(tmp_path, {"a": "текст"})

    assert nodes_without_theory(load_seed_data(seed)) == ["b"]


def test_theory_reaches_tutor_prompt(conn, tmp_path, settings) -> None:
    seed = _seed_with_theory(tmp_path, {"a": "Особая фраза из конспекта."})
    load_seed(conn, seed)
    session_id = repos.ensure_open_session(conn, 1.0)
    state = repos.get_session_state(conn, session_id).model_copy(update={"current_node_id": "a"})
    repos.update_session_state(conn, session_id, state)

    package = build_context(conn, session_id, "объясни", now=1.0, settings=settings)

    assert "Особая фраза из конспекта." in package.messages[0].content


def test_long_theory_is_trimmed_in_prompt(conn, tmp_path, settings) -> None:
    seed = _seed_with_theory(tmp_path, {"a": "слово " * 5000})
    load_seed(conn, seed)
    session_id = repos.ensure_open_session(conn, 1.0)
    state = repos.get_session_state(conn, session_id).model_copy(update={"current_node_id": "a"})
    repos.update_session_state(conn, session_id, state)

    package = build_context(conn, session_id, "объясни", now=1.0, settings=settings)

    assert len(package.messages[0].content) < 12000
