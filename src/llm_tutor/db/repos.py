"""Тонкий репозиторий поверх SQLite: CRUD без ORM.

Все запросы параметризованы (значения — только через плейсхолдеры, не
f-строки). Соединение приходит снаружи, что убирает глобальное состояние
и упрощает тесты (см. ``db.connection``).
"""

import json
import sqlite3
import time
from typing import Sequence, get_args

from llm_tutor.schemas import (
    Chunk,
    Concept,
    Edge,
    Event,
    Item,
    Message,
    Role,
    SessionState,
)

# Роли, допустимые в messages — те же, что в доменной модели Role.
_VALID_ROLES = frozenset(get_args(Role))


# --- sessions ---


def create_session(conn: sqlite3.Connection, now: float | None = None) -> int:
    """Создаёт новую сессию и возвращает её id."""
    ts = time.time() if now is None else now
    cur = conn.execute("INSERT INTO sessions (started_at) VALUES (?)", (ts,))
    conn.commit()
    return int(cur.lastrowid)


def get_open_session(conn: sqlite3.Connection) -> int | None:
    """id последней незакрытой сессии (``ended_at IS NULL``), иначе None."""
    row = conn.execute(
        "SELECT id FROM sessions WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return int(row["id"]) if row else None


def ensure_open_session(conn: sqlite3.Connection, now: float | None = None) -> int:
    """Открытая сессия, а если её нет — только что созданная."""
    session_id = get_open_session(conn)
    if session_id is None:
        session_id = create_session(conn, now)
    return session_id


def end_session(conn: sqlite3.Connection, session_id: int, now: float | None = None) -> None:
    """Проставляет ``ended_at`` (закрывает сессию).

    Идемпотентно: уже закрытая сессия не перезаписывается; несуществующая —
    ``KeyError``.
    """
    row = conn.execute(
        "SELECT ended_at FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"сессия {session_id} не найдена")
    if row["ended_at"] is not None:
        return

    ts = time.time() if now is None else now
    conn.execute("UPDATE sessions SET ended_at = ? WHERE id = ?", (ts, session_id))
    conn.commit()


def get_session_state(conn: sqlite3.Connection, session_id: int) -> SessionState:
    """Читает и разбирает JSON-состояние сессии."""
    row = conn.execute(
        "SELECT state FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"сессия {session_id} не найдена")
    return SessionState.model_validate_json(row["state"])


def update_session_state(
    conn: sqlite3.Connection, session_id: int, state: SessionState
) -> None:
    """Сохраняет состояние сессии (перезапись JSON целиком).

    Несуществующая сессия — ``KeyError``: молчаливый no-op терял бы состояние.
    """
    cur = conn.execute(
        "UPDATE sessions SET state = ? WHERE id = ?",
        (state.model_dump_json(), session_id),
    )
    if cur.rowcount == 0:
        raise KeyError(f"сессия {session_id} не найдена")
    conn.commit()


# --- messages ---


def add_message(
    conn: sqlite3.Connection,
    session_id: int,
    role: Role,
    content: str,
    *,
    ts: float | None = None,
    meta: str | None = None,
) -> int:
    """Пишет реплику в ``messages`` и возвращает её id.

    Роль валидируется на входе: мусорная роль на записи сломала бы чтение
    всей сессии позже (``Message.role`` — ``Literal``).
    """
    if role not in _VALID_ROLES:
        raise ValueError(f"Недопустимая роль сообщения: {role!r}")
    stamp = time.time() if ts is None else ts
    cur = conn.execute(
        "INSERT INTO messages (session_id, ts, role, content, meta) VALUES (?, ?, ?, ?, ?)",
        (session_id, stamp, role, content, meta),
    )
    conn.commit()
    return int(cur.lastrowid)


def get_messages(conn: sqlite3.Connection, session_id: int) -> list[Message]:
    """Все реплики сессии в хронологическом порядке."""
    rows = conn.execute(
        "SELECT id, role, content, ts, session_id, meta FROM messages "
        "WHERE session_id = ? ORDER BY ts, id",
        (session_id,),
    ).fetchall()
    return [
        Message(
            id=r["id"],
            role=r["role"],
            content=r["content"],
            ts=r["ts"],
            session_id=r["session_id"],
            meta=r["meta"],
        )
        for r in rows
    ]


# --- events ---


def add_event(conn: sqlite3.Connection, event: Event, now: float | None = None) -> int:
    """Пишет событие в журнал и возвращает его id."""
    ts = event.ts if event.ts is not None else (time.time() if now is None else now)
    cur = conn.execute(
        "INSERT INTO events "
        "(ts, concept_id, item_id, source, result, weight, citation, confidence,"
        " hints_used, time_spent) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ts,
            event.concept_id,
            event.item_id,
            event.source,
            event.result,
            event.weight,
            event.citation,
            event.confidence,
            event.hints_used,
            event.time_spent,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def get_events(conn: sqlite3.Connection, concept_id: str | None = None) -> list[Event]:
    """События журнала (опционально — только по одному концепту)."""
    if concept_id is None:
        rows = conn.execute("SELECT * FROM events ORDER BY ts, id").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM events WHERE concept_id = ? ORDER BY ts, id",
            (concept_id,),
        ).fetchall()
    return [
        Event(
            source=r["source"],
            result=r["result"],
            concept_id=r["concept_id"],
            item_id=r["item_id"],
            weight=r["weight"],
            citation=r["citation"],
            confidence=r["confidence"],
            hints_used=r["hints_used"],
            time_spent=r["time_spent"],
            ts=r["ts"],
        )
        for r in rows
    ]


# --- concepts / edges (граф курса, Срез 4) ---


def _write_concept(conn: sqlite3.Connection, concept: Concept) -> None:
    """Upsert концепта без коммита (для вызова внутри чужой транзакции)."""
    conn.execute(
        "INSERT INTO concepts (id, name, difficulty, description, source_url, active) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "name = excluded.name, difficulty = excluded.difficulty, "
        "description = excluded.description, source_url = excluded.source_url, "
        # Вернувшийся в seed узел снова активен.
        "active = 1",
        (
            concept.id,
            concept.name,
            concept.difficulty,
            concept.description,
            concept.source_url,
            int(concept.active),
        ),
    )


def _write_edge(conn: sqlite3.Connection, edge: Edge) -> None:
    """Upsert ребра без коммита (по тройке from/to/type)."""
    conn.execute(
        "INSERT INTO edges (from_id, to_id, type, hard, weight) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(from_id, to_id, type) DO UPDATE SET "
        "hard = excluded.hard, weight = excluded.weight",
        (edge.from_id, edge.to_id, edge.type, int(edge.hard), edge.weight),
    )


def _write_item(conn: sqlite3.Connection, item: Item) -> None:
    """Upsert задания банка без коммита."""
    conn.execute(
        "INSERT INTO items "
        "(id, concept_weights, difficulty, answer_type, prompt, options, answer,"
        " rubric_id, active) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "concept_weights = excluded.concept_weights, difficulty = excluded.difficulty, "
        "answer_type = excluded.answer_type, prompt = excluded.prompt, "
        "options = excluded.options, answer = excluded.answer, rubric_id = excluded.rubric_id, "
        # Вернувшееся в seed задание снова активно.
        "active = 1",
        (
            item.id,
            json.dumps(item.concept_weights, ensure_ascii=False),
            item.difficulty,
            item.answer_type,
            item.prompt,
            json.dumps(item.options, ensure_ascii=False),
            item.answer,
            item.rubric_id,
            int(item.active),
        ),
    )


def upsert_concept(conn: sqlite3.Connection, concept: Concept) -> None:
    """Создаёт или обновляет концепт графа."""
    _write_concept(conn, concept)
    conn.commit()


_ITEM_COLUMNS = (
    "id, concept_weights, difficulty, answer_type, prompt, options, answer, rubric_id, active"
)


def _row_to_item(row: sqlite3.Row) -> Item:
    return Item(
        id=row["id"],
        prompt=row["prompt"],
        answer_type=row["answer_type"],
        concept_weights=json.loads(row["concept_weights"]),
        difficulty=row["difficulty"],
        options=json.loads(row["options"]),
        answer=row["answer"],
        rubric_id=row["rubric_id"],
        active=bool(row["active"]),
    )


def upsert_item(conn: sqlite3.Connection, item: Item) -> None:
    """Создаёт или обновляет задание банка (путь авторской правки банка)."""
    _write_item(conn, item)
    conn.commit()


def get_items(conn: sqlite3.Connection) -> list[Item]:
    """Активные задания банка (порядок — по id, детерминированный)."""
    rows = conn.execute(
        f"SELECT {_ITEM_COLUMNS} FROM items WHERE active = 1 ORDER BY id"
    ).fetchall()
    return [_row_to_item(row) for row in rows]


def get_item(conn: sqlite3.Connection, item_id: int) -> Item | None:
    """Задание по id (в том числе погашенное) или ``None``."""
    row = conn.execute(
        f"SELECT {_ITEM_COLUMNS} FROM items WHERE id = ?", (item_id,)
    ).fetchone()
    return _row_to_item(row) if row else None


def replace_seed(
    conn: sqlite3.Connection,
    concepts: Sequence[Concept],
    edges: Sequence[Edge],
    items: Sequence[Item],
) -> None:
    """Атомарно приводит граф и банк заданий в БД к содержимому seed.

    Seed — источник истины: его узлы/рёбра/задания upsert-ятся, а всё, чего
    в нём больше нет, гасится (``active = 0``) — физически удалять нельзя,
    на узлы ссылаются события, чанки и mastery, а на задания — события.
    Погашенное не попадает в граф и банк, но журнал ученика остаётся целым.

    Рёбра удаляются по-настоящему: на них никто не ссылается.
    """
    node_ids = {concept.id for concept in concepts}
    edge_keys = {(edge.from_id, edge.to_id, edge.type) for edge in edges}
    item_ids = {item.id for item in items}
    try:
        for concept in concepts:
            _write_concept(conn, concept)
        for edge in edges:
            _write_edge(conn, edge)
        for item in items:
            _write_item(conn, item)
        for row in conn.execute("SELECT from_id, to_id, type FROM edges").fetchall():
            key = (row["from_id"], row["to_id"], row["type"])
            if key not in edge_keys:
                conn.execute(
                    "DELETE FROM edges WHERE from_id = ? AND to_id = ? AND type = ?", key
                )
        for row in conn.execute("SELECT id FROM concepts WHERE active = 1").fetchall():
            if row["id"] not in node_ids:
                conn.execute("UPDATE concepts SET active = 0 WHERE id = ?", (row["id"],))
        for row in conn.execute("SELECT id FROM items WHERE active = 1").fetchall():
            if row["id"] not in item_ids:
                conn.execute("UPDATE items SET active = 0 WHERE id = ?", (row["id"],))
    except Exception:
        conn.rollback()
        raise
    conn.commit()


def get_concepts(conn: sqlite3.Connection) -> list[Concept]:
    """Активные концепты графа (порядок — по id, детерминированный)."""
    rows = conn.execute(
        "SELECT id, name, difficulty, description, source_url, active "
        "FROM concepts WHERE active = 1 ORDER BY id"
    ).fetchall()
    return [
        Concept(
            id=r["id"],
            name=r["name"],
            difficulty=r["difficulty"],
            description=r["description"],
            source_url=r["source_url"],
            active=bool(r["active"]),
        )
        for r in rows
    ]


def get_edges(conn: sqlite3.Connection) -> list[Edge]:
    """Рёбра между активными концептами (порядок детерминированный)."""
    rows = conn.execute(
        "SELECT from_id, to_id, type, hard, weight FROM edges "
        "WHERE from_id IN (SELECT id FROM concepts WHERE active = 1) "
        "AND to_id IN (SELECT id FROM concepts WHERE active = 1) "
        "ORDER BY from_id, to_id, type"
    ).fetchall()
    return [
        Edge(
            from_id=r["from_id"],
            to_id=r["to_id"],
            type=r["type"],
            hard=bool(r["hard"]),
            weight=r["weight"],
        )
        for r in rows
    ]


# --- mastery (производная модель ученика, Срез 4) ---


def get_mastery(conn: sqlite3.Connection, concept_id: str) -> dict | None:
    """Текущие Beta-счётчики концепта или None, если не заведены."""
    row = conn.execute(
        "SELECT alpha, beta, last_seen, next_review FROM mastery WHERE concept_id = ?",
        (concept_id,),
    ).fetchone()
    return dict(row) if row else None


def upsert_mastery(
    conn: sqlite3.Connection,
    concept_id: str,
    *,
    alpha: float,
    beta: float,
    last_seen: float | None = None,
    next_review: float | None = None,
) -> None:
    """Создаёт или обновляет Beta-счётчики концепта."""
    conn.execute(
        "INSERT INTO mastery (concept_id, alpha, beta, last_seen, next_review) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(concept_id) DO UPDATE SET "
        "alpha = excluded.alpha, beta = excluded.beta, "
        # Не переданные (None) last_seen/next_review сохраняют прежние значения.
        "last_seen = COALESCE(excluded.last_seen, mastery.last_seen), "
        "next_review = COALESCE(excluded.next_review, mastery.next_review)",
        (concept_id, alpha, beta, last_seen, next_review),
    )
    conn.commit()


# --- facts (профиль/предпочтения, Срез 4.5) ---


def get_fact(conn: sqlite3.Connection, key: str) -> str | None:
    """Значение факта профиля или None."""
    row = conn.execute("SELECT value FROM facts WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_fact(
    conn: sqlite3.Connection,
    key: str,
    value: str,
    *,
    source: str | None = None,
    confidence: float | None = None,
) -> None:
    """Создаёт или перезаписывает факт профиля."""
    conn.execute(
        "INSERT INTO facts (key, value, source, confidence) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET "
        "value = excluded.value, "
        # Не переданные (None) source/confidence сохраняют прежние значения.
        "source = COALESCE(excluded.source, facts.source), "
        "confidence = COALESCE(excluded.confidence, facts.confidence)",
        (key, value, source, confidence),
    )
    conn.commit()


# --- chunks (материалы курса для RAG, Срез 3) ---


def replace_chunks(
    conn: sqlite3.Connection, source_url: str, chunks: Sequence[Chunk]
) -> int:
    """Атомарно заменяет чанки источника: delete + insert в одной транзакции.

    Отдельные коммиты на удаление и вставку оставили бы источник без чанков
    при сбое между ними. FTS наполняется триггерами. Возвращает число чанков.
    """
    try:
        conn.execute("DELETE FROM chunks WHERE source_url = ?", (source_url,))
        for chunk in chunks:
            conn.execute(
                "INSERT INTO chunks (concept_id, source_url, section, seq, content) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    chunk.concept_id,
                    chunk.source_url,
                    chunk.section,
                    chunk.seq,
                    chunk.content,
                ),
            )
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    return len(chunks)
