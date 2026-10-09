"""Тесты сборки контекста (Срез 3.3, полный пакет — Срез 5)."""

from course_fixtures import T2, finish_all_but, load_two_modules

from llm_tutor.core.context import build_context
from llm_tutor.course.graph import CourseGraph
from llm_tutor.course.ingest import ingest_text
from llm_tutor.course.seed import load_seed
from llm_tutor.db import repos
from llm_tutor.schemas import SessionState
from llm_tutor.student import route as route_mod
from llm_tutor.student import survey


def test_context_includes_retrieved_chunk_and_section(conn, settings) -> None:
    ingest_text(conn, "# T\n\n## Grouping\n\ngroupby aggregates rows\n", "u")
    session_id = repos.ensure_open_session(conn, now=1.0)

    package = build_context(conn, session_id, "как работает groupby?", settings=settings)

    assert package.chunks
    joined = "\n".join(m.content for m in package.messages)
    assert "groupby aggregates rows" in joined
    assert "Grouping" in joined  # подпись раздела


def test_context_does_not_crash_on_empty_db(conn, settings) -> None:
    session_id = repos.ensure_open_session(conn, now=1.0)

    package = build_context(conn, session_id, "что такое DataFrame?", settings=settings)

    assert package.chunks == []
    assert package.messages[0].role == "system"
    assert package.messages[-1].role == "user"
    assert package.messages[-1].content == "что такое DataFrame?"


def test_context_includes_dialog_tail(conn, settings) -> None:
    session_id = repos.ensure_open_session(conn, now=1.0)
    repos.add_message(conn, session_id, "user", "привет", ts=1.0)
    repos.add_message(conn, session_id, "assistant", "здравствуй", ts=2.0)

    package = build_context(conn, session_id, "новый вопрос", settings=settings)
    contents = [m.content for m in package.messages]

    assert "привет" in contents
    assert "здравствуй" in contents
    assert contents[-1] == "новый вопрос"


def test_context_limits_dialog_tail(conn, settings) -> None:
    session_id = repos.ensure_open_session(conn, now=1.0)
    for i in range(20):
        repos.add_message(conn, session_id, "user", f"m{i}", ts=float(i))

    package = build_context(conn, session_id, "q", dialog_tail=3, settings=settings)
    user_contents = [m.content for m in package.messages if m.role == "user"]

    assert user_contents == ["m17", "m18", "m19", "q"]


def test_dialog_tail_zero_means_no_history(conn, settings) -> None:
    """dialog_tail=0 — это «без истории», а не «вся история»."""
    session_id = repos.ensure_open_session(conn, now=1.0)
    repos.add_message(conn, session_id, "user", "старое", ts=1.0)

    package = build_context(conn, session_id, "q", dialog_tail=0, settings=settings)

    contents = [m.content for m in package.messages]
    assert "старое" not in contents
    assert len(package.messages) == 2  # только system + вопрос


def test_top_k_zero_falls_back_to_default(conn, settings) -> None:
    """top_k=0 не должен «выключать» RAG — падаем на дефолт."""
    ingest_text(conn, "# T\n\n## S\n\ngroupby\n", "u")
    session_id = repos.ensure_open_session(conn, now=1.0)

    package = build_context(conn, session_id, "groupby", top_k=0, settings=settings)

    assert package.chunks


def test_course_material_is_not_in_system_message(conn, settings) -> None:
    """Материал курса идёт сообщением ученика, а не в привилегированный system."""
    ingest_text(conn, "# T\n\n## Grouping\n\ngroupby aggregates rows\n", "u")
    session_id = repos.ensure_open_session(conn, now=1.0)

    package = build_context(conn, session_id, "groupby", settings=settings)

    system = next(m.content for m in package.messages if m.role == "system")
    assert "groupby aggregates rows" not in system


# --- полный пакет (Срез 5) ---


def test_system_prompt_carries_profile_state_and_mastery(conn, settings) -> None:
    """Правила, профиль и состояние ученика — в системном промпте."""
    load_seed(conn)
    repos.set_fact(conn, "block_analysis", "С подсказками")
    session_id = repos.ensure_open_session(conn, now=1.0)
    state = SessionState(current_node_id="groupby", hint_level=2)
    repos.update_session_state(conn, session_id, state)

    package = build_context(
        conn,
        session_id,
        "groupby",
        state=state,
        graph=CourseGraph.load(conn),
        settings=settings,
    )

    system = package.messages[0].content
    assert "Группировки и EDA: С подсказками" in system  # профиль из анкеты
    assert "самооценка по этой теме: С подсказками" in system
    assert "Группировка" in system  # текущая тема в состоянии
    assert "уровень подсказки: 2" in system
    assert "≈" in system  # срез модели ученика по соседним узлам
    assert "groupby aggregates" not in system  # материал в system не попадает


def test_small_budget_trims_material_and_dialog(conn, settings) -> None:
    """При нехватке бюджета первыми режутся материал и хвост диалога."""
    ingest_text(conn, "# T\n\n## S\n\n" + "groupby " * 400, "u")
    session_id = repos.ensure_open_session(conn, now=1.0)
    for index in range(10):
        repos.add_message(conn, session_id, "user", "groupby " * 40, ts=float(index))

    big = build_context(conn, session_id, "groupby", settings=settings)
    small = build_context(conn, session_id, "groupby", budget_tokens=40, settings=settings)

    assert len(small.chunks) < len(big.chunks)
    assert len(small.messages) <= len(big.messages)
    assert "groupby" in small.messages[-1].content  # вопрос ученика не режется
    assert small.messages[0].role == "system"


def test_system_prompt_contains_route_block(conn, settings) -> None:
    """Маршрут и фаза занятия попадают в системный промпт (§8.2)."""
    load_seed(conn)
    session_id = repos.ensure_open_session(conn, now=1.0)
    graph = CourseGraph.load(conn)
    state = SessionState(
        current_node_id="groupby",
        phase="practice",
        route=route_mod.build_route(conn, graph, current_node_id="groupby", now=0.0, settings=settings),
    )
    repos.update_session_state(conn, session_id, state)

    package = build_context(
        conn, session_id, "groupby", state=state, graph=graph, settings=settings
    )

    system = package.messages[0].content
    assert "Маршрут" in system
    assert "Фаза занятия: practice" in system


def test_state_defaults_to_session_state(conn, settings) -> None:
    """Без явного state контекст берёт состояние сессии из БД."""
    session_id = repos.ensure_open_session(conn, now=1.0)
    repos.update_session_state(conn, session_id, SessionState(hint_level=3))

    package = build_context(conn, session_id, "привет", settings=settings)

    assert "уровень подсказки: 3" in package.messages[0].content


def test_evicted_material_is_not_reported_as_missing(conn, settings) -> None:
    """Материал, вытесненный бюджетом, не должен выглядеть как ненайденный."""
    ingest_text(conn, "# T\n\n## S\n\n" + "groupby " * 400, "u")
    session_id = repos.ensure_open_session(conn, now=1.0)

    package = build_context(conn, session_id, "groupby", budget_tokens=1, settings=settings)

    assert package.material_state == "found"
    assert len(package.chunks) == 1  # самый релевантный чанк всё равно остаётся


def test_empty_index_means_no_material(conn, settings) -> None:
    session_id = repos.ensure_open_session(conn, now=1.0)

    package = build_context(conn, session_id, "groupby", settings=settings)

    assert package.material_state == "empty"


def test_search_miss_on_loaded_material_is_not_emptiness(conn, settings) -> None:
    """Материал загружен, но по этим словам не совпал — это НЕ «нет материалов»."""
    ingest_text(conn, "# T\n\n## Grouping\n\ngroupby aggregates rows\n", "u")
    session_id = repos.ensure_open_session(conn, now=1.0)

    package = build_context(conn, session_id, "зюйдвест абракадабра", settings=settings)

    assert package.material_state == "no_match"
    assert "материалы курса загружены" in package.messages[0].content.lower()


# --- модуль занятия (срез 27) ---


def test_context_speaks_about_current_module(conn, settings) -> None:
    load_two_modules(conn)
    survey.apply_answers(conn, T2, {"t02_basics": 1, "t02_relations": 2}, settings=settings)
    finish_all_but(conn, "mini_hist", topic_id=2, completed=(1,))
    session_id = repos.get_open_session(conn)

    package = build_context(
        conn,
        session_id,
        "что такое bins?",
        graph=CourseGraph.load(conn),
        now=2.0,
        settings=settings,
    )

    system = package.messages[0].content
    assert "модуль 2" in system
    assert "Простые графики" in system  # профиль — анкета модуля 2
    assert "Группировки и EDA" not in system  # анкета модуля 1 в профиль не идёт


def test_material_state_counts_only_reachable_modules(conn, settings) -> None:
    """27-4: есть только материал будущего модуля — «материалов нет», а не «не нашлось»."""
    load_two_modules(conn)
    ingest_text(conn, "# T\n\n## S\n\nseaborn pairplot\n", "http://u2", topic_id=2)
    session_id = repos.ensure_open_session(conn, now=1.0)

    package = build_context(
        conn, session_id, "что такое groupby?", graph=CourseGraph.load(conn), now=2.0,
        settings=settings,
    )

    assert package.material_state == "empty"
