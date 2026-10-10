"""БД ученика: открытие, seed, материалы RAG, блокировка (Срез 39)."""

import asyncio

import pytest

from llm_tutor.course.ingest import ingest_text
from llm_tutor.db import repos
from llm_tutor.db.connection import get_conn, migrate
from llm_tutor.rag.retriever import retrieve
from llm_tutor.web.courses import COURSES, get_course
from llm_tutor.web.userdb import UserDBPool

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


def test_registry_has_the_pandas_course() -> None:
    course = get_course("mlcourse-topic01")

    assert course is not None
    assert course.seed_path.is_file()
    assert get_course("nope") is None


def test_first_open_creates_db_with_course_graph(pool, tmp_path) -> None:
    conn = pool.open(1, COURSE)

    assert (tmp_path / "users" / "1" / f"{COURSE}.sqlite3").is_file()
    assert conn.execute("SELECT COUNT(*) FROM concepts").fetchone()[0] == 21
    assert pool.exists(1, COURSE)
    assert not pool.exists(2, COURSE)


def test_open_is_cached_and_idempotent(pool, tmp_path) -> None:
    first = pool.open(1, COURSE)
    repos.set_fact(first, "goal", "x", source="self")
    assert pool.open(1, COURSE) is first

    pool.close_all()
    reopened = UserDBPool(tmp_path / "users", tmp_path / "materials.sqlite3").open(1, COURSE)
    assert reopened.execute("SELECT COUNT(*) FROM concepts").fetchone()[0] == 21
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
