"""Тесты сборки контекста (Срез 3.3)."""

from llm_tutor.core.context import build_context
from llm_tutor.course.ingest import ingest_text
from llm_tutor.db import repos


def test_context_includes_retrieved_chunk_and_section(conn) -> None:
    ingest_text(conn, "# T\n\n## Grouping\n\ngroupby aggregates rows\n", "u")
    session_id = repos.ensure_open_session(conn, now=1.0)

    package = build_context(conn, session_id, "как работает groupby?")

    assert package.chunks
    joined = "\n".join(m.content for m in package.messages)
    assert "groupby aggregates rows" in joined
    assert "Grouping" in joined  # подпись раздела


def test_context_does_not_crash_on_empty_db(conn) -> None:
    session_id = repos.ensure_open_session(conn, now=1.0)

    package = build_context(conn, session_id, "что такое DataFrame?")

    assert package.chunks == []
    assert package.messages[0].role == "system"
    assert package.messages[-1].role == "user"
    assert package.messages[-1].content == "что такое DataFrame?"


def test_context_includes_dialog_tail(conn) -> None:
    session_id = repos.ensure_open_session(conn, now=1.0)
    repos.add_message(conn, session_id, "user", "привет", ts=1.0)
    repos.add_message(conn, session_id, "assistant", "здравствуй", ts=2.0)

    package = build_context(conn, session_id, "новый вопрос")
    contents = [m.content for m in package.messages]

    assert "привет" in contents
    assert "здравствуй" in contents
    assert contents[-1] == "новый вопрос"


def test_context_limits_dialog_tail(conn) -> None:
    session_id = repos.ensure_open_session(conn, now=1.0)
    for i in range(20):
        repos.add_message(conn, session_id, "user", f"m{i}", ts=float(i))

    package = build_context(conn, session_id, "q", dialog_tail=3)
    user_contents = [m.content for m in package.messages if m.role == "user"]

    assert user_contents == ["m17", "m18", "m19", "q"]


def test_dialog_tail_zero_means_no_history(conn) -> None:
    """dialog_tail=0 — это «без истории», а не «вся история»."""
    session_id = repos.ensure_open_session(conn, now=1.0)
    repos.add_message(conn, session_id, "user", "старое", ts=1.0)

    package = build_context(conn, session_id, "q", dialog_tail=0)

    contents = [m.content for m in package.messages]
    assert "старое" not in contents
    assert len(package.messages) == 2  # только system + вопрос


def test_top_k_zero_falls_back_to_default(conn) -> None:
    """top_k=0 не должен «выключать» RAG — падаем на дефолт."""
    ingest_text(conn, "# T\n\n## S\n\ngroupby\n", "u")
    session_id = repos.ensure_open_session(conn, now=1.0)

    package = build_context(conn, session_id, "groupby", top_k=0)

    assert package.chunks


def test_course_material_is_not_in_system_message(conn) -> None:
    """Материал курса идёт сообщением ученика, а не в привилегированный system."""
    ingest_text(conn, "# T\n\n## Grouping\n\ngroupby aggregates rows\n", "u")
    session_id = repos.ensure_open_session(conn, now=1.0)

    package = build_context(conn, session_id, "groupby")

    system = next(m.content for m in package.messages if m.role == "system")
    assert "groupby aggregates rows" not in system
