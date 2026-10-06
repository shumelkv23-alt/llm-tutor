"""Поиск релевантных чанков курса через FTS5 с ранжированием BM25."""

import re
import sqlite3

from llm_tutor.schemas import Chunk

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)

# Минимальная длина значимого токена: односимвольные («a», «и») слишком шумны.
_MIN_TOKEN_LEN = 2

# Стоп-слова (англ. + рус.): сами по себе они матчат почти любой чанк
# и рушат точность OR-запроса. Материал англоязычный, поэтому особенно
# важны английские, но русские тоже отсекаем — вреда нет.
_STOPWORDS = frozenset(
    """
    a an the is are was were be been being do does did of to in on at by for with
    and or not no it its this that these those as from but if then than so such
    how what when where which who why i you we they he she can could should would
    will shall may might must have has had
    и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по
    только ее мне было вот от меня еще нет о из ему теперь когда даже ну ли чтобы
    там потом это этот эта эти или при для до после
    """.split()
)


def build_fts_query(user_query: str) -> str:
    """Строит безопасный FTS5-запрос: значимые токены в кавычках через ``OR``.

    Кавычки нейтрализуют операторы FTS5 (``*``, ``-``, ``"``, ``:``, ``^`` и
    т.п.), а стоп-слова и короткие токены отсекаются, чтобы запрос не матчил
    всё подряд. Пустой результат означает «искать нечего».
    """
    seen: set[str] = set()
    unique: list[str] = []
    for token in _TOKEN_RE.findall(user_query):
        low = token.lower()
        if len(token) < _MIN_TOKEN_LEN or low in _STOPWORDS or low in seen:
            continue
        seen.add(low)
        unique.append(token)
    return " OR ".join(f'"{token}"' for token in unique)


def retrieve(
    conn: sqlite3.Connection,
    query: str,
    *,
    concept_id: str | None = None,
    k: int = 4,
) -> list[Chunk]:
    """Возвращает топ-``k`` чанков по BM25; пустой запрос → ``[]``."""
    match = build_fts_query(query)
    if not match or k <= 0:
        return []

    sql = (
        "SELECT c.id, c.concept_id, c.source_url, c.section, c.seq, c.content "
        "FROM chunks_fts "
        "JOIN chunks c ON c.id = chunks_fts.rowid "
        "WHERE chunks_fts MATCH ?"
    )
    params: list[object] = [match]
    if concept_id is not None:
        sql += " AND c.concept_id = ?"
        params.append(concept_id)
    sql += " ORDER BY bm25(chunks_fts) LIMIT ?"
    params.append(k)

    rows = conn.execute(sql, params).fetchall()
    return [
        Chunk(
            id=row["id"],
            concept_id=row["concept_id"],
            source_url=row["source_url"],
            section=row["section"],
            seq=row["seq"],
            content=row["content"],
        )
        for row in rows
    ]
