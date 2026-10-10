"""БД ученика: одна SQLite на пару (ученик, курс).

Ядро построено на инварианте «одна БД — один ученик» (спека веба §5): веб
открывает ученику его файл и передаёт соединение в ``core`` как есть.

Открытие: ``migrate`` → ``load_seed`` (идемпотентный upsert графа и банка) →
сверка материалов RAG с общей БД материалов. Соединения кэшируются в
процессе; изменяющие запросы занятия идут под ``lock`` ученика.
"""

import asyncio
import hashlib
import logging
import sqlite3
from pathlib import Path

from llm_tutor.course.seed import load_seed
from llm_tutor.db.connection import get_conn, migrate
from llm_tutor.web.courses import get_course

logger = logging.getLogger(__name__)

_CHUNK_COLUMNS = "concept_id, source_url, section, seq, content"


def _chunks_revision(conn: sqlite3.Connection, schema: str = "main") -> str:
    """Отпечаток чанков: совпал — копировать нечего."""
    digest = hashlib.sha256()
    for row in conn.execute(
        f"SELECT {_CHUNK_COLUMNS} FROM {schema}.chunks ORDER BY source_url, seq, id"
    ):
        digest.update(repr(tuple(row)).encode("utf-8"))
    return digest.hexdigest()


def sync_materials(conn: sqlite3.Connection, materials_path: Path) -> bool:
    """Приводит чанки БД ученика к общей БД материалов. ``True`` — перезалили.

    Нет файла материалов — RAG у ученика пуст, файл не создаём. ``concept_id``
    узла, которого нет в графе ученика, обнуляется: иначе вставка упала бы на
    внешнем ключе.
    """
    if not materials_path.is_file():
        return False
    conn.execute("ATTACH DATABASE ? AS materials", (str(materials_path),))
    try:
        if _chunks_revision(conn, "materials") == _chunks_revision(conn):
            return False
        try:
            conn.execute("DELETE FROM main.chunks")
            conn.execute(
                f"INSERT INTO main.chunks ({_CHUNK_COLUMNS}) "
                "SELECT CASE WHEN m.concept_id IN (SELECT id FROM main.concepts) "
                "THEN m.concept_id END, m.source_url, m.section, m.seq, m.content "
                "FROM materials.chunks m ORDER BY m.id"
            )
        except sqlite3.Error:
            conn.rollback()
            raise
        conn.commit()
        return True
    finally:
        conn.execute("DETACH DATABASE materials")


class UserDBPool:
    """Открытые БД учеников и их блокировки (на процесс)."""

    def __init__(self, users_dir: str | Path, materials_path: str | Path) -> None:
        self._users_dir = Path(users_dir)
        self._materials_path = Path(materials_path)
        self._conns: dict[tuple[int, str], sqlite3.Connection] = {}
        self._locks: dict[tuple[int, str], asyncio.Lock] = {}

    def _key(self, user_id: int, course_id: str) -> tuple[int, str]:
        # bool — подкласс int: True не должен стать учеником №1.
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
            raise ValueError(f"Некорректный id ученика: {user_id!r}")
        if get_course(course_id) is None:
            raise KeyError(course_id)
        return user_id, course_id

    def path(self, user_id: int, course_id: str) -> Path:
        """Файл БД ученика. ``course_id`` — только из реестра: путь не уйдёт наружу."""
        uid, cid = self._key(user_id, course_id)
        return self._users_dir / str(uid) / f"{cid}.sqlite3"

    def exists(self, user_id: int, course_id: str) -> bool:
        return self.path(user_id, course_id).is_file()

    def open(self, user_id: int, course_id: str) -> sqlite3.Connection:
        """Соединение с БД ученика; первый вход создаёт её и грузит курс."""
        key = self._key(user_id, course_id)
        conn = self._conns.get(key)
        if conn is not None:
            return conn
        course = get_course(course_id)
        assert course is not None  # проверено в _key
        conn = get_conn(str(self.path(user_id, course_id)), check_same_thread=False)
        try:
            migrate(conn)
            load_seed(conn, course.seed_path)
            if sync_materials(conn, self._materials_path):
                logger.info("Материалы обновлены у ученика %s (%s)", user_id, course_id)
        except Exception:
            conn.close()
            raise
        self._conns[key] = conn
        return conn

    def lock(self, user_id: int, course_id: str) -> asyncio.Lock:
        """Блокировка занятия ученика: ходы одного ученика идут по очереди."""
        key = self._key(user_id, course_id)
        lock = self._locks.get(key)
        if lock is None:
            lock = self._locks[key] = asyncio.Lock()
        return lock

    def close_all(self) -> None:
        for conn in self._conns.values():
            conn.close()
        self._conns.clear()
