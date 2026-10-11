"""Тесты FTS5-ретривера (Срез 3.2)."""

from llm_tutor.course.ingest import ingest_text
from llm_tutor.db import repos
from llm_tutor.rag.retriever import build_fts_query, retrieve
from llm_tutor.schemas import Chunk


# --- построение запроса ---


def test_build_fts_query_quotes_and_ors_tokens() -> None:
    assert build_fts_query('groupby "drop" table') == '"groupby" OR "drop" OR "table"'


def test_build_fts_query_dedupes_case_insensitively() -> None:
    assert build_fts_query("Pandas pandas PANDAS") == '"Pandas"'


def test_build_fts_query_empty_for_punctuation_only() -> None:
    assert build_fts_query("  !!! ???  ") == ""


def test_build_fts_query_drops_stopwords_and_short_tokens() -> None:
    """Стоп-слова и односимвольные токены не попадают в запрос (точность)."""
    assert build_fts_query("how do I train a neural network") == (
        '"train" OR "neural" OR "network"'
    )


def test_build_fts_query_empty_when_only_stopwords() -> None:
    assert build_fts_query("the of and a") == ""


# --- поиск ---


def test_retrieve_empty_query_returns_empty(conn) -> None:
    ingest_text(conn, "# T\n\n## S\n\npandas groupby\n", "u")
    assert retrieve(conn, "   ") == []


def test_retrieve_on_empty_db_returns_empty(conn) -> None:
    assert retrieve(conn, "pandas") == []


def test_retrieve_stopword_only_query_returns_empty(conn) -> None:
    ingest_text(conn, "# T\n\n## S\n\nabout the data\n", "u")
    assert retrieve(conn, "the of and") == []


def test_retrieve_ranks_relevant_chunk_first(conn) -> None:
    ingest_text(conn, "# T\n\n## A\n\ngroupby aggregation of a DataFrame column\n", "u1")
    ingest_text(conn, "# T\n\n## B\n\nunrelated text about plotting and colors\n", "u2")

    results = retrieve(conn, "groupby DataFrame", k=2)
    assert results
    assert results[0].section == "A"
    assert "groupby" in results[0].content


def test_retrieve_special_characters_do_not_break(conn) -> None:
    """Операторы FTS5 в запросе ученика не ломают синтаксис."""
    ingest_text(conn, "# T\n\n## S\n\nalpha beta gamma\n", "u")
    results = retrieve(conn, 'alpha* OR "beta" -gamma (x:y) ^z')
    assert results


def test_retrieve_respects_k(conn) -> None:
    for i in range(5):
        ingest_text(conn, f"# T\n\n## S{i}\n\nalpha content number {i}\n", f"u{i}")
    assert len(retrieve(conn, "alpha", k=2)) == 2


def test_retrieve_filters_by_concept(conn) -> None:
    conn.execute("INSERT INTO concepts (id, name) VALUES ('c1', 'c1')")
    conn.execute(
        "INSERT INTO chunks (concept_id, source_url, section, seq, content) "
        "VALUES ('c1', 'u', 'S', 0, 'alpha c1')"
    )
    conn.execute(
        "INSERT INTO chunks (concept_id, source_url, section, seq, content) "
        "VALUES (NULL, 'u', 'S', 1, 'alpha other')"
    )
    conn.commit()

    results = retrieve(conn, "alpha", concept_id="c1")
    assert len(results) == 1
    assert results[0].concept_id == "c1"


def test_retrieve_skips_future_modules(conn) -> None:
    repos.replace_chunks(
        conn, "u1", [Chunk(source_url="u1", content="gradient boosting basics", seq=0, topic_id=1)]
    )
    repos.replace_chunks(
        conn, "u3", [Chunk(source_url="u3", content="gradient boosting trees", seq=0, topic_id=3)]
    )

    found = retrieve(conn, "gradient boosting", max_topic=2)

    assert [chunk.source_url for chunk in found] == ["u1"]
    assert found[0].topic_id == 1
