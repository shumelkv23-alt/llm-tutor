"""Перенос прогресса со старых тем на занятия (срез 56, план §6).

БД в старом формате собирается из замороженных seed (``tests/fixtures/legacy_v1``),
затем курс перезагружается из data/ и прогресс переносится — как при первом
открытии БД ученика после выкатки.
"""

import json

import pytest
from course_fixtures import correct_answer
from legacy_course import load_course as load_old_course

from llm_tutor.config import Settings
from llm_tutor.core.overview import course_progress
from llm_tutor.core.turn import handle_turn, resume_reply
from llm_tutor.course import legacy as legacy_mod
from llm_tutor.course.graph import CourseGraph
from llm_tutor.course.seed import load_course, load_course_data
from llm_tutor.db import repos
from llm_tutor.schemas import Route, RouteStep, SessionState
from test_turn import _FakeTutor

NOW = 1_000_000.0


@pytest.fixture
def settings():
    return Settings(_env_file=None, openrouter_api_key="test-key")


def _old_db(conn, *, closed=(), weak=(), claimed=(), current=None, **state_patch):
    """БД ученика в старом формате: снимок маршрута по темам и состояние."""
    load_old_course(conn)
    graph = CourseGraph.load(conn)
    steps = []
    for node in graph.topo_order():
        if node in closed:
            steps.append(RouteStep(concept_id=node, mode="full", status="closed",
                                   closed_at=NOW - 100, weak=node in weak))
        elif node == current:
            steps.append(RouteStep(concept_id=node, mode="full", status="current"))
        elif node in claimed:
            steps.append(RouteStep(concept_id=node, mode="full", status="claimed"))
        else:
            steps.append(RouteStep(concept_id=node, mode="full", status="ahead"))
    session_id = repos.ensure_open_session(conn, now=NOW - 200)
    repos.update_session_state(
        conn,
        session_id,
        SessionState(
            current_node_id=current,
            route=Route(steps=steps, topic_id=1, goal_concept_id="churn_eda_case"),
            **state_patch,
        ),
    )


def _migrate(conn, settings):
    course = load_course(conn)
    return course, legacy_mod.remap_progress(conn, course.legacy, settings=settings, now=NOW)


def _state(conn) -> SessionState:
    return repos.get_session_state(conn, repos.get_open_session(conn))


def _step(conn, node):
    return next(step for step in _state(conn).route.steps if step.concept_id == node)


MODULE1_OLD = [
    "python_basics", "numpy_basics", "pandas_intro", "pandas_series", "pandas_dataframe",
    "read_csv", "df_inspect", "describe_stats", "dtype_conversion", "indexing_loc_iloc",
    "boolean_indexing", "sorting", "value_counts", "apply_functions", "groupby",
    "agg_functions", "summary_tables", "df_transformations", "visualization_basics",
    "eda_workflow", "churn_eda_case",
]


# --- Review Focus 3: ученик посреди старой темы ---


async def test_student_in_middle_of_old_topic_keeps_pending_item(conn, settings) -> None:
    _old_db(conn, closed=MODULE1_OLD[:14], current="groupby", pending_item_id=18,
            phase="practice", node_streak=1)

    _, moved = _migrate(conn, settings)

    state = _state(conn)
    assert moved
    assert state.current_node_id == "grouping" and state.section_id == "groupby"
    assert state.pending_item_id == 18 and state.phase == "practice"
    item = repos.get_item(conn, 18)
    assert item.active and item.section == "groupby"

    reply = await handle_turn(
        conn, _FakeTutor("A"), "m", correct_answer(item), now=NOW + 1, settings=settings,
        answer_mode="answer",
    )
    assert reply.text.startswith("Верно")
    assert _state(conn).passed_sections == ["groupby"]


# --- Review Focus 4: Python не считается пройденным занятием ---


def test_closed_python_does_not_close_a_lesson(conn, settings) -> None:
    _old_db(conn, closed=["python_basics", "numpy_basics"], current="pandas_intro")

    _migrate(conn, settings)

    progress = course_progress(conn, now=NOW, settings=settings)
    open_lessons = {t.number for t in load_course_data().topics if t.published}
    lessons = [n for n in load_course_data().nodes if n.topic_id in open_lessons]
    assert progress.closed == 0 and progress.total == len(lessons)
    assert _state(conn).current_node_id == "pandas_structures"


def test_current_python_topic_becomes_no_lesson(conn, settings) -> None:
    _old_db(conn, current="python_basics")

    _migrate(conn, settings)

    assert _state(conn).current_node_id is None


# --- Review Focus 5: повторный перенос ничего не меняет ---


def test_second_remap_changes_nothing(conn, settings) -> None:
    _old_db(conn, closed=MODULE1_OLD[:10], current="indexing_loc_iloc")
    repos.upsert_mastery(conn, "groupby", alpha=9.0, beta=1.0, last_seen=NOW - 10)
    course, first = _migrate(conn, settings)
    mastery = repos.get_mastery(conn, "grouping")
    state = _state(conn)

    second = legacy_mod.remap_progress(conn, course.legacy, settings=settings, now=NOW + 50)

    assert first and not second
    assert repos.get_mastery(conn, "grouping") == mastery
    assert _state(conn) == state


def test_fresh_db_is_not_marked(conn, settings) -> None:
    """Новая БД — переносить нечего, отметки нет (и повторный перенос безопасен)."""
    _, moved = _migrate(conn, settings)

    assert not moved
    assert repos.get_fact(conn, legacy_mod.LAYOUT_FACT) is None


# --- владение: среднее, а не сумма ---


def test_mastery_is_averaged_over_all_old_topics(conn, settings) -> None:
    _old_db(conn)
    repos.upsert_mastery(conn, "groupby", alpha=9.0, beta=3.0, last_seen=NOW - 10)

    _migrate(conn, settings)

    row = repos.get_mastery(conn, "grouping")
    # Три старые темы занятия: groupby, agg_functions, summary_tables.
    assert row["alpha"] == pytest.approx(1 + 8 / 3)
    assert row["beta"] == pytest.approx(1 + 2 / 3)
    assert row["last_seen"] == NOW - 10


# --- закрытые темы → разделы и занятия ---


def test_whole_module_closed_maps_to_lessons_and_passed_sections(conn, settings) -> None:
    _old_db(conn, closed=MODULE1_OLD, weak=["summary_tables"], current=None)

    _migrate(conn, settings)

    closed = {s.concept_id for s in _state(conn).route.steps if s.status == "closed"}
    # Занятия без разделов «нов.» закрыты; с ними — ждут уверенного владения.
    assert closed == {"pandas_structures", "types_stats", "selection", "transform", "grouping"}
    assert _step(conn, "grouping").weak
    passed = json.loads(repos.get_fact(conn, legacy_mod.PASSED_FACT))
    assert passed["load_inspect"] == {"passed": ["read", "inspect"], "weak": []}
    assert passed["eda_case"] == {"passed": ["workflow", "churn"], "weak": []}


def test_new_section_is_covered_by_confident_mastery(conn, settings) -> None:
    _old_db(conn, closed=MODULE1_OLD)
    for old in ("read_csv", "df_inspect"):
        repos.upsert_mastery(conn, old, alpha=200.0, beta=1.0, last_seen=NOW - 10)

    _migrate(conn, settings)

    assert _step(conn, "load_inspect").status == "closed"


async def test_lesson_entry_picks_up_sections_passed_before(conn, settings) -> None:
    _old_db(conn, closed=["read_csv"], current="df_inspect")

    _migrate(conn, settings)
    state = _state(conn)
    assert state.current_node_id == "load_inspect"
    assert state.passed_sections == ["read"] and state.section_id == "inspect"

    await resume_reply(conn, _FakeTutor("Часть"), "m", now=NOW + 1, settings=settings)

    assert _state(conn).passed_sections == ["read"]
    assert _state(conn).section_id == "inspect"


def test_partially_passed_lesson_keeps_progress_in_fact(conn, settings) -> None:
    _old_db(conn, closed=["groupby"], current="read_csv")

    _migrate(conn, settings)

    assert legacy_mod.legacy_passed(conn, "grouping") == (["groupby"], [])
    assert _step(conn, "grouping").status != "closed"


# --- проход и анкета ---


def test_verify_pass_on_old_topic_is_dropped(conn, settings) -> None:
    _old_db(conn, current="groupby", mode="verify", verify_item_ids=[18], close_requested=True)

    _migrate(conn, settings)

    state = _state(conn)
    assert state.mode is None and state.verify_item_ids == [] and not state.close_requested


def test_student_right_after_survey_keeps_claimed(conn, settings) -> None:
    _old_db(conn, claimed=["pandas_intro", "pandas_series", "pandas_dataframe", "read_csv"])

    _migrate(conn, settings)

    assert _step(conn, "pandas_structures").status == "claimed"
    # У load_inspect заявлена лишь часть тем — не заявлено.
    assert _step(conn, "load_inspect").status != "claimed"


def test_stale_pending_item_is_dropped(conn, settings) -> None:
    _old_db(conn, current="python_basics", pending_item_id=6, phase="practice")

    _migrate(conn, settings)

    assert _state(conn).pending_item_id is None


# --- БД бота с прогрессом в модулях 3 и 5 через import-legacy ---


@pytest.fixture
def module5_soon(monkeypatch, tmp_path):
    """Настоящий курс, где модуль 5 снова «скоро» (с среза 61 он опубликован).

    Копия seed-файлов и конспектов во временной папке: переносу прогресса нужен
    курс с неопубликованным модулем, а в data/ таких больше нет.
    """
    import shutil
    from dataclasses import replace

    from llm_tutor.course.seed import course_paths
    from llm_tutor.web import api_lesson, courses

    source = course_paths()[0].parent
    root = tmp_path / "course"
    root.mkdir()
    shutil.copytree(source / "theory", root / "theory")
    paths = []
    for path in course_paths():
        text = path.read_text(encoding="utf-8")
        if path.name == "seed_topic05.json":
            assert '"published": true' in text
            text = text.replace('"published": true', '"published": false')
        target = root / path.name
        target.write_text(text, encoding="utf-8")
        paths.append(target)
    course = replace(courses.COURSES[0], seed_paths=tuple(paths))
    monkeypatch.setattr(courses, "COURSES", (course,))
    monkeypatch.setitem(courses._BY_ID, course.id, course)
    monkeypatch.setattr(api_lesson, "COURSES", (course,))


def _import_bot_db(web_settings, tmp_path, *, closed, current, mastery):
    """БД бота с прогрессом → import-legacy → открытая БД ученика (и пул)."""
    from llm_tutor.db.connection import get_conn, migrate
    from llm_tutor.web import accounts
    from llm_tutor.web.cli import main as cli_main
    from llm_tutor.web.userdb import UserDBPool

    db = accounts.open_accounts(web_settings.accounts_db_path)
    user = accounts.create_user(db, email="ann@example.com", name="Аня", password="секретный-пароль")
    db.close()
    bot = tmp_path / "bot.sqlite3"
    conn = get_conn(str(bot))
    migrate(conn)
    _old_db(conn, closed=MODULE1_OLD + closed, current=current)
    repos.upsert_mastery(conn, mastery, alpha=30.0, beta=2.0, last_seen=NOW)
    conn.close()

    args = ["import-legacy", "--email", "ann@example.com", "--db", str(bot)]
    assert cli_main(args, settings=web_settings) == 0
    pool = UserDBPool(tmp_path / "users", tmp_path / "materials.sqlite3", settings=web_settings)
    return pool.open(user.id, "mlcourse"), pool


async def test_bot_db_with_module5_progress_via_import_legacy(
    module5_soon, web_settings, tmp_path
) -> None:
    module5 = ["ensembles", "bootstrapping", "bagging", "bagging_variance",
               "rf_variance_decorrelation", "rf_bias"]
    student, pool = _import_bot_db(
        web_settings, tmp_path, closed=module5, current="random_subspace", mastery="rf_bias"
    )

    state = repos.get_session_state(student, repos.get_open_session(student))
    # Модуль 5 пока «скоро»: его занятие не может быть текущим…
    assert state.current_node_id is None
    assert repos.get_mastery(student, "rf_theory") is not None
    # …а пройденное в нём (и закрытое целиком) ждёт в факте, пока модуль не откроют.
    assert legacy_mod.legacy_passed(student, "bagging_basics") == (
        ["crowd", "bootstrap", "bagging", "variance"], []
    )
    assert legacy_mod.legacy_passed(student, "rf_theory") == (["decorrelation", "bias"], [])
    assert repos.get_fact(student, legacy_mod.LAYOUT_FACT) == legacy_mod.LAYOUT_VERSION
    # Модуль 1 закрыт по-прежнему (кроме занятий с разделами «нов.»).
    closed = {s.concept_id for s in state.route.steps if s.status == "closed"}
    assert {"pandas_structures", "grouping"} <= closed
    # «Продолжить» не падает на узле вне графа.
    reply = await resume_reply(student, _FakeTutor("Часть"), "m", now=NOW + 1, settings=web_settings)
    assert reply.text
    pool.close_all()


async def test_bot_db_with_module3_progress_continues_in_module_3(web_settings, tmp_path) -> None:
    """Модуль 3 открыт (срез 59): текущее занятие бота становится текущим в вебе."""
    module3 = ["ml_task_types", "supervised_learning", "accuracy_metric",
               "decision_tree_idea", "entropy", "information_gain", "gini_impurity"]
    student, pool = _import_bot_db(
        web_settings, tmp_path, closed=module3, current="greedy_tree_building", mastery="entropy"
    )

    state = repos.get_session_state(student, repos.get_open_session(student))
    assert state.current_node_id == "tree_building"
    assert repos.get_mastery(student, "tree_criteria") is not None
    closed = {s.concept_id for s in state.route.steps if s.status == "closed"}
    assert "pandas_structures" in closed
    assert "ml_basics" not in closed  # итоговое задание занятия ещё впереди
    pool.close_all()
