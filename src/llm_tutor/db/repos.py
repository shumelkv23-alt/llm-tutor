"""Тонкий репозиторий поверх SQLite: CRUD без ORM.

Все запросы параметризованы (значения — только через плейсхолдеры, не
f-строки). Соединение приходит снаружи, что убирает глобальное состояние
и упрощает тесты (см. ``db.connection``).
"""

import sqlite3
import time
from typing import Sequence

from llm_tutor.schemas import Chunk, Event, Message, SessionState


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
    """Сохраняет состояние сессии (перезапись JSON целиком)."""
    conn.execute(
        "UPDATE sessions SET state = ? WHERE id = ?",
        (state.model_dump_json(), session_id),
    )
    conn.commit()


# --- messages ---


def add_message(
    conn: sqlite3.Connection,
    session_id: int,
    role: str,
    content: str,
    *,
    ts: float | None = None,
    meta: str | None = None,
) -> int:
    """Пишет реплику в ``messages`` и возвращает её id."""
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


def delete_chunks_by_source(conn: sqlite3.Connection, source_url: str) -> int:
    """Удаляет чанки источника (для идемпотентного повторного ingest)."""
    cur = conn.execute("DELETE FROM chunks WHERE source_url = ?", (source_url,))
    conn.commit()
    return cur.rowcount


def add_chunks(conn: sqlite3.Connection, chunks: Sequence[Chunk]) -> int:
    """Вставляет чанки пачкой (один коммит), FTS наполняется триггерами."""
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
    conn.commit()
    return len(chunks)
