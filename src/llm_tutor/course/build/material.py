"""Материал источников для генерации: поиск нужного куска под тему шага.

Источники курса лежат файлами; чтобы дать модели именно то, что относится к
занятию или разделу, их чанки складываются в БД в памяти и ищутся тем же
BM25-поиском, что и RAG занятия (``rag.retriever``).
"""

import sqlite3

from llm_tutor.course.build import sources as sources_mod
from llm_tutor.course.ingest import chunk_markdown
from llm_tutor.db import repos
from llm_tutor.db.connection import get_conn, migrate
from llm_tutor.rag.retriever import retrieve
from llm_tutor.schemas import Chunk

# Сколько материала отдаём в один запрос к модели и сколько чанков ищем.
MATERIAL_CHARS = 6000
MATERIAL_TOP_K = 6


class Material:
    """Источники курса в памяти: ``for_query`` отдаёт материал под запрос."""

    def __init__(self, conn: sqlite3.Connection, count: int) -> None:
        self._conn = conn
        self.count = count

    @classmethod
    def load(cls, root, *, max_chars: int = 3000) -> "Material | None":
        """Готовит материал курса; источников нет — ``None``."""
        records = sources_mod.read_manifest(root)
        if not records:
            return None
        conn = get_conn(":memory:")
        migrate(conn)
        for record in records:
            key = f"{record.kind}:{record.origin}"
            text = sources_mod.source_text(root, record)
            repos.replace_chunks(conn, key, chunk_markdown(text, key, max_chars=max_chars))
        conn.commit()
        return cls(conn, len(records))

    def for_query(self, query: str, *, limit_chars: int = MATERIAL_CHARS) -> str:
        """Материал под запрос: найденные чанки, а если не нашлось — первые по порядку.

        Пустая выдача поиска не должна оставлять шаг без материала вовсе:
        тема могла быть сформулирована словами, которых нет в источниках.
        """
        found = retrieve(self._conn, query, k=MATERIAL_TOP_K)
        if not found:
            found = self._first_chunks()
        parts: list[str] = []
        used = 0
        for chunk in found:
            head = f"### {chunk.section}\n" if chunk.section else ""
            body = f"{head}{chunk.content}\n"
            if used + len(body) > limit_chars:
                body = body[: max(0, limit_chars - used)]
            if not body.strip():
                continue
            parts.append(body)
            used += len(body)
            if used >= limit_chars:
                break
        return "".join(parts).strip()

    def _first_chunks(self) -> list[Chunk]:
        """Первые чанки по порядку — запасной вариант, когда поиск ничего не нашёл."""
        rows = self._conn.execute(
            "SELECT concept_id, source_url, section, seq, content, topic_id"
            " FROM chunks ORDER BY source_url, seq LIMIT ?",
            (MATERIAL_TOP_K,),
        )
        return [
            Chunk(
                concept_id=row[0],
                source_url=row[1],
                section=row[2],
                seq=row[3],
                content=row[4],
                topic_id=row[5],
            )
            for row in rows
        ]

    def close(self) -> None:
        self._conn.close()
