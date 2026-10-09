"""Загрузка материалов курса в чанки и FTS-индекс.

Источники: markdown (MyST) страницы mlcourse.ai и HTML-страницы
(конвертируются в markdown через bs4 + markdownify). Разбор учитывает
код-блоки: решётка ``#`` внутри ```- или ~~~-фенсов — это комментарий кода,
а не заголовок, иначе код-комментарии ложно становятся разделами.

CLI: ``python -m llm_tutor.course.ingest <url|file> --topic N [--source-url URL] [--db PATH]``.
"""

import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify

from llm_tutor.db.repos import replace_chunks
from llm_tutor.schemas import Chunk

# Бюджет чанка ~700 токенов: прокси по символам (~4 символа ≈ 1 токен).
MAX_CHUNK_CHARS = 3000

_HEADING_RE = re.compile(r"^(#{1,6})\s+(\S.*)$")
_MYST_LABEL_RE = re.compile(r"^\([^)]+\)=\s*$")
_FENCE_MARKERS = ("```", "~~~")
_SKIP_DIRECTIVES = ("{figure}", "{contents}", "{toctree}")
_HTML_START_RE = re.compile(r"^\s*(<!doctype\s+html|<html)", re.IGNORECASE)


@dataclass(frozen=True)
class _Item:
    """Элемент разбора: заголовок (с уровнем) или текстовый блок."""

    kind: str  # "heading" | "block"
    text: str
    level: int = 0


def to_markdown(text: str) -> str:
    """Конвертирует HTML-страницу в markdown; markdown возвращает как есть."""
    text = text.lstrip("﻿")
    if _HTML_START_RE.match(text[:200]):
        soup = BeautifulSoup(text, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        return markdownify(str(soup), heading_style="ATX")
    return text


def _after_frontmatter(lines: list[str]) -> int:
    """Индекс первой строки после YAML-frontmatter (или 0, если его нет)."""
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return i + 1
    return 0


def _fence_marker(stripped: str) -> str | None:
    """Маркер открывающего фенса (`` ``` `` или ``~~~``), иначе None."""
    for marker in _FENCE_MARKERS:
        if stripped.startswith(marker):
            return marker
    return None


def _parse_markdown(text: str) -> list[_Item]:
    """Разбирает markdown в список заголовков и текстовых блоков."""
    lines = text.lstrip("﻿").splitlines()
    items: list[_Item] = []
    para: list[str] = []

    def flush_para() -> None:
        if para:
            joined = "\n".join(para).strip()
            if joined:
                items.append(_Item("block", joined))
            para.clear()

    i = _after_frontmatter(lines)
    while i < len(lines):
        stripped = lines[i].strip()

        marker = _fence_marker(stripped)
        if marker:
            flush_para()
            info = stripped[len(marker):].strip()
            i += 1
            body: list[str] = []
            while i < len(lines) and not lines[i].strip().startswith(marker):
                body.append(lines[i])
                i += 1
            i += 1  # закрывающий фенс
            if not info.startswith(_SKIP_DIRECTIVES):
                block = "\n".join(body).strip("\n")
                if block:
                    items.append(_Item("block", block))
            continue

        if _MYST_LABEL_RE.match(stripped):
            i += 1
            continue

        heading = _HEADING_RE.match(lines[i])
        if heading:
            flush_para()
            # Закрывающие решётки — синтаксис ATX, а не часть заголовка.
            title = heading.group(2).strip().rstrip("#").strip()
            items.append(_Item("heading", title, len(heading.group(1))))
            i += 1
            continue

        if not stripped:
            flush_para()
        else:
            para.append(lines[i])
        i += 1

    flush_para()
    return items


def _wrap_line(line: str, max_chars: int) -> list[str]:
    """Режет одну строку на куски ≤ ``max_chars``, не разрывая слова.

    Слово длиннее бюджета (URL, base64) режется жёстко по символам.
    """
    pieces: list[str] = []
    current: list[str] = []
    size = 0
    for word in line.split():
        while len(word) > max_chars:
            if current:
                pieces.append(" ".join(current))
                current, size = [], 0
            pieces.append(word[:max_chars])
            word = word[max_chars:]
        if current and size + len(word) + 1 > max_chars:
            pieces.append(" ".join(current))
            current, size = [], 0
        current.append(word)
        size += len(word) + 1
    if current:
        pieces.append(" ".join(current))
    return pieces


def _split_long(text: str, max_chars: int) -> list[str]:
    """Режет длинный блок на куски не длиннее ``max_chars``, сохраняя переводы строк.

    Режем по строкам, а не склейкой всего текста: код в материалах чувствителен
    к ``\\n`` и отступам — склейка через пробел превратила бы многострочный
    пример в неисполнимый однострочник.
    """
    pieces: list[str] = []
    buffer: list[str] = []
    size = 0
    for line in text.split("\n"):
        for chunk in _wrap_line(line, max_chars):
            if buffer and size + len(chunk) + 1 > max_chars:
                pieces.append("\n".join(buffer))
                buffer, size = [], 0
            buffer.append(chunk)
            size += len(chunk) + 1
    if buffer:
        pieces.append("\n".join(buffer))
    return [piece for piece in pieces if piece]


def chunk_markdown(
    text: str, source_url: str, *, max_chars: int = MAX_CHUNK_CHARS
) -> list[Chunk]:
    """Режет markdown на чанки, группируя блоки под ближайшим заголовком.

    Подпись секции — путь по уровням ≥2 (напр. ``Grouping > Sorting``);
    заголовок 1-го уровня считается названием документа и в путь не входит.
    """
    chunks: list[Chunk] = []
    heading_stack: list[tuple[int, str]] = []
    section: str | None = None
    buf: list[str] = []
    buf_len = 0

    def emit(content: str) -> None:
        content = content.strip()
        if content:
            chunks.append(
                Chunk(source_url=source_url, section=section, seq=len(chunks), content=content)
            )

    def flush() -> None:
        nonlocal buf, buf_len
        emit("\n\n".join(buf))
        buf = []
        buf_len = 0

    for item in _parse_markdown(text):
        if item.kind == "heading":
            if item.level == 1:
                heading_stack.clear()
            else:
                while heading_stack and heading_stack[-1][0] >= item.level:
                    heading_stack.pop()
                heading_stack.append((item.level, item.text))
            new_section = " > ".join(title for _, title in heading_stack) or None
            if new_section != section:
                flush()
                section = new_section
            continue

        # Блок длиннее бюджета режется принудительно, чтобы не раздувать чанк.
        if len(item.text) > max_chars:
            flush()
            for piece in _split_long(item.text, max_chars):
                emit(piece)
            continue

        if buf and buf_len + len(item.text) > max_chars:
            flush()
        buf.append(item.text)
        buf_len += len(item.text) + 2
    flush()
    return chunks


def ingest_text(
    conn: sqlite3.Connection,
    text: str,
    source_url: str,
    *,
    topic_id: int = 1,
    max_chars: int = MAX_CHUNK_CHARS,
) -> int:
    """Разбирает текст и записывает чанки (идемпотентно по ``source_url``).

    ``topic_id`` — модуль материала: RAG не подмешивает будущие модули.
    """
    chunks = [
        chunk.model_copy(update={"topic_id": topic_id})
        for chunk in chunk_markdown(to_markdown(text), source_url, max_chars=max_chars)
    ]
    return replace_chunks(conn, source_url, chunks)


def fetch_url(url: str, *, timeout: float = 30.0) -> str:
    """Скачивает страницу (markdown или HTML)."""
    response = httpx.get(url, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    return response.text


def ingest_url(
    conn: sqlite3.Connection,
    url: str,
    *,
    topic_id: int = 1,
    max_chars: int = MAX_CHUNK_CHARS,
) -> int:
    """Скачивает страницу по URL и загружает её чанки."""
    return ingest_text(conn, fetch_url(url), url, topic_id=topic_id, max_chars=max_chars)


def ingest_file(
    conn: sqlite3.Connection,
    path: str | Path,
    *,
    source_url: str | None = None,
    topic_id: int = 1,
    max_chars: int = MAX_CHUNK_CHARS,
) -> int:
    """Читает локальный файл и загружает его чанки.

    Ключом источника служит канонический абсолютный путь — иначе один и тот же
    файл под относительным и абсолютным путём давал бы дубли чанков.
    """
    resolved = Path(path).resolve()
    text = resolved.read_text(encoding="utf-8")
    return ingest_text(
        conn, text, source_url or str(resolved), topic_id=topic_id, max_chars=max_chars
    )


def main(argv: list[str] | None = None) -> int:
    """CLI: загрузить материал из URL или локального файла в БД."""
    import argparse

    from llm_tutor.db.connection import get_conn, migrate

    parser = argparse.ArgumentParser(description="Загрузка материалов курса в БД.")
    parser.add_argument("source", help="URL или путь к локальному markdown/HTML-файлу")
    parser.add_argument(
        "--topic", type=int, required=True, help="номер модуля материала (1–10)"
    )
    parser.add_argument(
        "--source-url", default=None, help="URL страницы для цитат (для файла)"
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("DB_PATH", "data/llm_tutor.sqlite3"),
        help="путь к БД (по умолчанию DB_PATH или data/llm_tutor.sqlite3)",
    )
    args = parser.parse_args(argv)

    conn = get_conn(args.db)
    try:
        migrate(conn)
        if args.source.startswith(("http://", "https://")):
            count = ingest_url(conn, args.source, topic_id=args.topic)
        else:
            count = ingest_file(
                conn, args.source, source_url=args.source_url, topic_id=args.topic
            )
    finally:
        conn.close()

    print(f"Загружено чанков: {count} из {args.source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
