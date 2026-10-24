"""БД ученика: открытие, seed, материалы RAG, блокировка (Срез 39)."""

import asyncio

import pytest

from llm_tutor.course.ingest import ingest_text
from llm_tutor.db import repos
from llm_tutor.db.connection import get_conn, migrate
from llm_tutor.rag.retriever import retrieve
from llm_tutor.web.courses import COURSES, get_course
from llm_tutor.web.userdb import UserDBPool

from llm_tutor.web.courses import COURSES as _COURSES, course_data as _course_data

TOPICS = len(_course_data(_COURSES[0]).nodes)  # тем во всех модулях курса
TITLE = _COURSES[0].title


COURSE = COURSES[0].id


def _materials(path, text: str) -> None:
    conn = get_conn(str(path))
    migrate(conn)
    conn.execute("DELETE FROM chunks")
    conn.commit()
    ingest_text(conn, text, "https://mlcourse.ai/topic01")
    conn.close()


@pytest.fixture
def pool(tmp_path):
    instance = UserDBPool(tmp_path / "users", tmp_path / "materials.sqlite3")
    yield instance
    instance.close_all()


def test_registry_has_the_mlcourse_of_five_modules() -> None:
    course = get_course("mlcourse")

    assert course is not None
    assert [topic.number for topic in _course_data(course).topics] == [1, 2, 3, 4, 5]
    assert get_course("nope") is None
    assert get_course("mlcourse-topic01") is None  # старый id — только переадресация


def test_first_open_creates_db_with_course_graph(pool, tmp_path) -> None:
    conn = pool.open(1, COURSE)

    assert (tmp_path / "users" / "1" / f"{COURSE}.sqlite3").is_file()
    assert conn.execute("SELECT COUNT(*) FROM concepts").fetchone()[0] == TOPICS
    assert pool.exists(1, COURSE)
    assert not pool.exists(2, COURSE)


def test_open_is_cached_and_idempotent(pool, tmp_path) -> None:
    first = pool.open(1, COURSE)
    repos.set_fact(first, "goal", "x", source="self")
    assert pool.open(1, COURSE) is first

    pool.close_all()
    reopened = UserDBPool(tmp_path / "users", tmp_path / "materials.sqlite3").open(1, COURSE)
    assert reopened.execute("SELECT COUNT(*) FROM concepts").fetchone()[0] == TOPICS
    assert repos.get_fact(reopened, "goal") == "x"
    reopened.close()


def test_unknown_course_creates_nothing(pool, tmp_path) -> None:
    with pytest.raises(KeyError):
        pool.open(1, "../../etc")

    assert not (tmp_path / "users").exists()


@pytest.mark.parametrize("user_id", [0, -1, True])
def test_bad_user_id_is_rejected(pool, user_id) -> None:
    with pytest.raises(ValueError):
        pool.open(user_id, COURSE)


def test_students_have_separate_files(pool) -> None:
    ann = pool.open(1, COURSE)
    bob = pool.open(2, COURSE)
    repos.set_fact(ann, "secret", "ann", source="self")

    assert ann is not bob
    assert repos.get_fact(bob, "secret") is None


def test_materials_are_copied_and_searchable(pool, tmp_path) -> None:
    _materials(tmp_path / "materials.sqlite3", "# Groupby\n\nМетод groupby группирует строки.")

    conn = pool.open(1, COURSE)

    assert repos.count_chunks(conn) > 0
    assert retrieve(conn, "groupby", k=3)


def test_copied_materials_keep_their_module(pool, tmp_path) -> None:
    """Материал модуля 2 и в БД ученика — модуля 2: RAG фильтрует по модулю."""
    path = tmp_path / "materials.sqlite3"
    conn = get_conn(str(path))
    migrate(conn)
    ingest_text(conn, "# Графики\n\nseaborn pairplot рисует пары.", "https://u/2", topic_id=2)
    conn.close()

    student = pool.open(1, COURSE)

    assert {row[0] for row in student.execute("SELECT topic_id FROM chunks")} == {2}


def test_materials_of_old_schema_are_module_one(pool, tmp_path) -> None:
    """Аудит среза 51, H1: общая БД материалов, созданная до модулей (без
    chunks.topic_id), не роняет открытие БД ученика — её чанки — модуль 1."""
    import sqlite3

    from llm_tutor.db.connection import MIGRATIONS_DIR

    path = tmp_path / "materials.sqlite3"
    old = sqlite3.connect(path)
    for name in ("001_init.sql", "002_seed_active.sql", "003_rubrics_active.sql", "004_web.sql"):
        old.executescript((MIGRATIONS_DIR / name).read_text(encoding="utf-8"))
    old.execute("PRAGMA user_version = 4")
    old.execute(
        "INSERT INTO chunks (source_url, section, seq, content) "
        "VALUES ('https://u', 'Grouping', 0, 'Метод groupby группирует строки.')"
    )
    old.commit()
    old.close()

    conn = pool.open(1, COURSE)

    assert {row[0] for row in conn.execute("SELECT topic_id FROM chunks")} == {1}
    assert retrieve(conn, "groupby", k=3)


def test_changed_materials_replace_old_chunks(tmp_path) -> None:
    materials = tmp_path / "materials.sqlite3"
    _materials(materials, "# Старое\n\nПро merge и join.")
    first = UserDBPool(tmp_path / "users", materials)
    first.open(1, COURSE)
    first.close_all()

    _materials(materials, "# Новое\n\nПро pivot_table и crosstab.")
    second = UserDBPool(tmp_path / "users", materials)
    conn = second.open(1, COURSE)

    assert retrieve(conn, "crosstab", k=3)
    assert not retrieve(conn, "merge", k=3)
    second.close_all()


def test_missing_materials_are_not_created(pool, tmp_path) -> None:
    conn = pool.open(1, COURSE)

    assert repos.count_chunks(conn) == 0
    assert not (tmp_path / "materials.sqlite3").exists()


async def test_lock_serializes_one_student(pool) -> None:
    order: list[str] = []

    async def turn(name: str) -> None:
        async with pool.lock(1, COURSE):
            order.append(f"{name}:start")
            await asyncio.sleep(0.01)
            order.append(f"{name}:end")

    await asyncio.gather(turn("a"), turn("b"))

    assert order == ["a:start", "a:end", "b:start", "b:end"]


def test_locks_are_per_student(pool) -> None:
    assert pool.lock(1, COURSE) is pool.lock(1, COURSE)
    assert pool.lock(1, COURSE) is not pool.lock(2, COURSE)


def test_connection_works_from_another_thread(pool) -> None:
    """Фабрика открывает БД в своём потоке, запросы идут из потока цикла событий."""
    import threading

    conn = pool.open(1, COURSE)
    result: list[int] = []
    worker = threading.Thread(
        target=lambda: result.append(conn.execute("SELECT COUNT(*) FROM concepts").fetchone()[0])
    )
    worker.start()
    worker.join()

    assert result == [TOPICS]


# --- курс mlcourse-topic01 стал mlcourse (срез 52) ---


def test_db_of_renamed_course_is_adopted(tmp_path) -> None:
    """Файл БД под прежним id курса переименовывается, прогресс на месте."""
    users = tmp_path / "users"
    old = users / "1" / "mlcourse-topic01.sqlite3"
    old.parent.mkdir(parents=True)
    conn = get_conn(str(old))
    migrate(conn)
    repos.set_fact(conn, "secret", "ann", source="self")
    conn.close()
    pool = UserDBPool(users, tmp_path / "materials.sqlite3")

    assert pool.exists(1, "mlcourse")
    student = pool.open(1, "mlcourse")

    assert repos.get_fact(student, "secret") == "ann"
    assert not old.exists()
    pool.close_all()


def test_existing_db_of_new_course_is_not_overwritten(tmp_path) -> None:
    users = tmp_path / "users"
    (users / "1").mkdir(parents=True)
    for name, value in (("mlcourse-topic01", "old"), ("mlcourse", "new")):
        conn = get_conn(str(users / "1" / f"{name}.sqlite3"))
        migrate(conn)
        repos.set_fact(conn, "secret", value, source="self")
        conn.close()
    pool = UserDBPool(users, tmp_path / "materials.sqlite3")

    assert repos.get_fact(pool.open(1, "mlcourse"), "secret") == "new"
    assert (users / "1" / "mlcourse-topic01.sqlite3").exists()
    pool.close_all()


def test_userdb_open_runs_backfill(tmp_path) -> None:
    """Снимок маршрута с закрытым занятием → при открытии появляется строка расписания."""
    from llm_tutor.schemas import Route, RouteStep, SessionState

    users = tmp_path / "users"
    path = users / "1" / "mlcourse.sqlite3"
    path.parent.mkdir(parents=True)
    conn = get_conn(str(path))
    migrate(conn)
    session_id = repos.ensure_open_session(conn, now=1.0)
    route = Route(steps=[RouteStep(concept_id="pandas_structures", mode="full", status="closed", closed_at=1.0)])
    repos.update_session_state(conn, session_id, SessionState(route=route))
    conn.close()
    pool = UserDBPool(users, tmp_path / "materials.sqlite3")

    student = pool.open(1, "mlcourse")

    assert list(repos.get_review_rows(student)) == ["pandas_structures"]
    assert repos.get_fact(student, "review_backfill") == "1"
    pool.close_all()


def test_userdb_open_without_session_sets_backfill_fact(pool) -> None:
    student = pool.open(1, COURSE)
    assert repos.get_review_rows(student) == {}
    assert repos.get_fact(student, "review_backfill") == "1"


def test_userdb_open_survives_unknown_concept_in_route_snapshot(tmp_path) -> None:
    """Призрак в снимке маршрута (id вне курса) не должен ронять открытие БД навсегда."""
    from llm_tutor.schemas import Route, RouteStep, SessionState

    users = tmp_path / "users"
    path = users / "1" / "mlcourse.sqlite3"
    path.parent.mkdir(parents=True)
    conn = get_conn(str(path))
    migrate(conn)
    session_id = repos.ensure_open_session(conn, now=1.0)
    route = Route(
        steps=[
            RouteStep(concept_id="old_topic_ghost", mode="full", status="closed", closed_at=1.0),
            RouteStep(concept_id="pandas_structures", mode="full", status="closed", closed_at=1.0),
        ]
    )
    repos.update_session_state(conn, session_id, SessionState(route=route))
    conn.close()
    pool = UserDBPool(users, tmp_path / "materials.sqlite3")

    student = pool.open(1, "mlcourse")  # раньше — IntegrityError при каждом открытии

    assert list(repos.get_review_rows(student)) == ["pandas_structures"]
    pool.close_all()
