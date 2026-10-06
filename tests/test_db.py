"""Тесты схемы и слоя подключения к SQLite."""

import sqlite3
from pathlib import Path

import pytest

from llm_tutor.db import connection
from llm_tutor.db.connection import SCHEMA_VERSION, get_conn, migrate

EXPECTED_TABLES = {
    "concepts",
    "edges",
    "rubrics",
    "items",
    "criteria",
    "events",
    "mastery",
    "misconceptions",
    "sessions",
    "messages",
    "facts",
    "chunks",
}


def _object_names(conn) -> set[str]:
    return {r["name"] for r in conn.execute("SELECT name FROM sqlite_master")}


def test_migrate_creates_all_tables(conn) -> None:
    assert EXPECTED_TABLES <= _object_names(conn)


def test_migrate_creates_fts_and_triggers(conn) -> None:
    names = _object_names(conn)
    assert "chunks_fts" in names
    assert {"chunks_ai", "chunks_ad", "chunks_au"} <= names


def test_migrate_is_idempotent(conn) -> None:
    """Повторный migrate не падает и не сбрасывает версию."""
    migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_foreign_keys_enforced(conn) -> None:
    """Сообщение с несуществующей сессией отклоняется (FK ON)."""
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO messages (session_id, ts, role, content) VALUES (999, 0, 'user', 'x')"
        )


def test_chunk_insert_populates_fts(conn) -> None:
    """Триггер chunks_ai наполняет FTS-индекс при вставке."""
    conn.execute(
        "INSERT INTO chunks (source_url, seq, content, section) VALUES ('u', 1, 'pandas groupby', 'Введение')"
    )
    conn.commit()
    hits = conn.execute(
        "SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'pandas'"
    ).fetchone()[0]
    assert hits == 1


def test_chunk_update_syncs_fts(conn) -> None:
    """Триггер chunks_au убирает старый текст и добавляет новый."""
    cur = conn.execute(
        "INSERT INTO chunks (source_url, seq, content, section) VALUES ('u', 1, 'alpha', 's')"
    )
    chunk_id = cur.lastrowid
    conn.execute("UPDATE chunks SET content = 'beta' WHERE id = ?", (chunk_id,))
    conn.commit()

    assert conn.execute(
        "SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'alpha'"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'beta'"
    ).fetchone()[0] == 1


def test_chunk_delete_removes_from_fts(conn) -> None:
    """Триггер chunks_ad вычищает запись из FTS-индекса."""
    cur = conn.execute(
        "INSERT INTO chunks (source_url, seq, content, section) VALUES ('u', 1, 'gamma', 's')"
    )
    chunk_id = cur.lastrowid
    conn.execute("DELETE FROM chunks WHERE id = ?", (chunk_id,))
    conn.commit()

    assert conn.execute(
        "SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'gamma'"
    ).fetchone()[0] == 0


def _fts_count(conn, term: str) -> int:
    return conn.execute(
        "SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH ?", (term,)
    ).fetchone()[0]


def test_recursive_triggers_enabled(conn) -> None:
    """recursive_triggers включён — иначе INSERT OR REPLACE рассинхронит FTS."""
    assert conn.execute("PRAGMA recursive_triggers").fetchone()[0] == 1


def test_insert_or_replace_keeps_fts_consistent(conn) -> None:
    """INSERT OR REPLACE не оставляет призрачный терм в FTS-индексе."""
    conn.execute(
        "INSERT INTO chunks (id, source_url, seq, content) VALUES (1, 'u', 1, 'omega')"
    )
    conn.commit()
    conn.execute(
        "INSERT OR REPLACE INTO chunks (id, source_url, seq, content) "
        "VALUES (1, 'u', 1, 'psi')"
    )
    conn.commit()

    assert _fts_count(conn, "omega") == 0
    assert _fts_count(conn, "psi") == 1


def test_messages_foreign_key_declared(conn) -> None:
    """FK messages.session_id → sessions объявлен в схеме."""
    fks = conn.execute("PRAGMA foreign_key_list(messages)").fetchall()
    assert any(fk["table"] == "sessions" and fk["from"] == "session_id" for fk in fks)


def test_migrate_rejects_future_schema(conn) -> None:
    """БД от более новой версии приложения — явная ошибка, а не тихий no-op."""
    conn.execute("PRAGMA user_version = 999")
    with pytest.raises(RuntimeError, match="новее"):
        migrate(conn)


def test_migrate_missing_dir_raises_clear_error(monkeypatch) -> None:
    """Отсутствующий каталог миграций — понятная ошибка, а не сырой трейсбек."""
    monkeypatch.setattr(connection, "MIGRATIONS_DIR", Path("definitely_missing_dir_xyz"))
    fresh = get_conn(":memory:")
    try:
        with pytest.raises(RuntimeError, match="[Кк]аталог миграций"):
            migrate(fresh)
    finally:
        fresh.close()


def test_migrate_requires_file_for_each_version(monkeypatch) -> None:
    """SCHEMA_VERSION выше максимальной миграции → ошибка, а не ложная версия."""
    monkeypatch.setattr(connection, "SCHEMA_VERSION", 2)  # нет файла 002_*.sql
    fresh = get_conn(":memory:")
    try:
        with pytest.raises(RuntimeError, match="Нет миграций"):
            migrate(fresh)
    finally:
        fresh.close()


def test_get_conn_anchors_relative_path_to_project_root(monkeypatch, tmp_path) -> None:
    """Относительный путь БД резолвится от корня проекта, а не от cwd."""
    monkeypatch.setattr(connection, "_PROJECT_ROOT", tmp_path)
    conn = get_conn("sub/data.db")
    try:
        assert (tmp_path / "sub" / "data.db").exists()
    finally:
        conn.close()
