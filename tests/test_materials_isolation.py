"""Материалы RAG у каждого курса свои (Срез C).

Общая БД материалов одна на приложение, а ``chunks.topic_id`` — номер модуля
**внутри** курса. Если её отдавать всем курсам, ученик нового курса получит
материалы mlcourse, и тьютор начнёт цитировать чужой курс. Поэтому у
созданного курса — свой ``materials.sqlite3``, а встроенный mlcourse
продолжает брать общий файл из настроек.
"""

from pathlib import Path

from llm_tutor.course.ingest import ingest_text
from llm_tutor.db.connection import get_conn, migrate
from llm_tutor.web import courses
from llm_tutor.web.userdb import UserDBPool


def write_materials(path: Path, text: str, source_url: str) -> None:
    """БД материалов с одним источником — как её готовит ``course.ingest``."""
    conn = get_conn(str(path))
    try:
        migrate(conn)
        ingest_text(conn, text, source_url, topic_id=1)
        conn.commit()
    finally:
        conn.close()


def sources(conn) -> set[str]:
    """Источники чанков в БД ученика."""
    return {row[0] for row in conn.execute("SELECT source_url FROM chunks")}


def test_generated_course_has_its_own_materials(make_course, tmp_path):
    root = make_course("python-basics")
    shared = tmp_path / "materials.sqlite3"
    write_materials(root.root / "materials.sqlite3", "# Основы\n\nПеременные и типы.", "python.md")
    write_materials(shared, "# Pandas\n\nБулевы маски.", "pandas.md")

    pool = UserDBPool(tmp_path / "users", shared)
    try:
        own = pool.open(1, "python-basics")
        assert sources(own) == {"python.md"}
        # Встроенный курс по-прежнему берёт общий файл материалов.
        builtin = pool.open(1, "mlcourse")
        assert sources(builtin) == {"pandas.md"}
    finally:
        pool.close_all()


def test_course_without_materials_has_empty_rag(make_course, tmp_path):
    """У курса без источников RAG пуст, а не «взят у соседа»."""
    make_course("python-basics")
    shared = tmp_path / "materials.sqlite3"
    write_materials(shared, "# Pandas\n\nБулевы маски.", "pandas.md")

    pool = UserDBPool(tmp_path / "users", shared)
    try:
        assert sources(pool.open(1, "python-basics")) == set()
    finally:
        pool.close_all()


def test_unknown_course_has_no_materials_path(courses_home, tmp_path):
    pool = UserDBPool(tmp_path / "users", tmp_path / "materials.sqlite3")
    try:
        try:
            pool.materials_path_for("no-such-course")
        except KeyError:
            pass
        else:  # pragma: no cover - ветка «ошибки не было» и есть дефект
            raise AssertionError("неизвестный курс должен давать KeyError")
    finally:
        pool.close_all()


def test_invalidate_course_reopens_student_db(make_course, tmp_path):
    """Правка курса должна доехать до уже открытого ученика.

    ``open`` применяет seed только при первом открытии, а соединения живут до
    конца процесса: без сброса кэша курса ученик остался бы со старым занятием.
    """
    make_course("python-basics")
    pool = UserDBPool(tmp_path / "users", tmp_path / "materials.sqlite3")
    try:
        first = pool.open(1, "python-basics")
        assert first is pool.open(1, "python-basics")  # соединение кэшируется

        pool.invalidate_course("python-basics")
        assert pool.open(1, "python-basics") is not first
    finally:
        pool.close_all()


def test_invalidate_course_keeps_other_courses(make_course, tmp_path):
    make_course("python-basics")
    pool = UserDBPool(tmp_path / "users", tmp_path / "materials.sqlite3")
    try:
        builtin = pool.open(1, "mlcourse")
        created = pool.open(1, "python-basics")
        pool.invalidate_course("python-basics")
        assert pool.open(1, "mlcourse") is builtin
        assert pool.open(1, "python-basics") is not created
    finally:
        pool.close_all()
