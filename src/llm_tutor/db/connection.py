"""Подключение к SQLite: pragma-настройки и идемпотентные миграции."""

import sqlite3
from pathlib import Path

# Корень проекта — не зависит от cwd (db/connection.py → src → корень).
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS_DIR = _PROJECT_ROOT / "migrations"

# Текущая (максимальная известная) версия схемы. Управляется номером файла
# миграции NNN_*.sql; здесь — ожидаемая по коду.
SCHEMA_VERSION = 3


def get_conn(db_path: str) -> sqlite3.Connection:
    """Открывает соединение с нужными pragma.

    ``db_path`` == ``":memory:"`` — БД в памяти (тесты). Относительный путь
    резолвится от корня проекта (не от cwd), родительский каталог создаётся.
    """
    if db_path == ":memory:":
        target = db_path
    else:
        path = Path(db_path)
        if not path.is_absolute():
            path = _PROJECT_ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        target = str(path)

    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    # INSERT OR REPLACE удаляет конфликтующую строку только при включённых
    # рекурсивных триггерах — иначе FTS-индекс рассинхронизируется.
    conn.execute("PRAGMA recursive_triggers = ON")
    return conn


def migrate(
    conn: sqlite3.Connection,
    *,
    migrations_dir: Path | None = None,
    schema_version: int | None = None,
) -> None:
    """Применяет миграции ``NNN_*.sql`` до версии схемы (идемпотентно).

    Версия берётся из ``PRAGMA user_version``; применяются файлы с номером
    строго больше текущей версии. По умолчанию — схема ученика
    (``MIGRATIONS_DIR``, ``SCHEMA_VERSION``); БД аккаунтов веба передаёт свои
    каталог и версию. Дефолты читаются при вызове: тесты подменяют константы
    модуля.
    """
    directory = MIGRATIONS_DIR if migrations_dir is None else migrations_dir
    target = SCHEMA_VERSION if schema_version is None else schema_version
    current = conn.execute("PRAGMA user_version").fetchone()[0]

    if current > target:
        raise RuntimeError(
            f"Схема БД новее приложения (user_version={current} > {target})."
            " Обнови приложение или укажи другую БД."
        )
    if current == target:
        return
    if not directory.is_dir():
        raise RuntimeError(f"Каталог миграций не найден: {directory}")

    for path in sorted(directory.glob("[0-9][0-9][0-9]_*.sql")):
        version = int(path.name[:3])
        if version <= current:
            continue
        if version > target:
            # Иначе код сам переведёт БД в состояние новее своего и «окирпичит» её.
            raise RuntimeError(
                f"Миграция {path.name} новее этой версии приложения "
                f"(SCHEMA_VERSION={target}). Обнови приложение."
            )
        script = path.read_text(encoding="utf-8")
        try:
            # PRAGMA user_version транзакционен: схема и её версия применяются
            # атомарно — сбой посередине скрипта не оставит половину DDL.
            conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {version};\nCOMMIT;")
        except sqlite3.Error as exc:
            conn.rollback()
            raise RuntimeError(f"Миграция {path.name} не применилась: {exc}") from exc
        current = version

    if current < target:
        raise RuntimeError(
            f"Нет миграций до SCHEMA_VERSION={target} (применено до {current})."
            f" Проверь {directory}."
        )
