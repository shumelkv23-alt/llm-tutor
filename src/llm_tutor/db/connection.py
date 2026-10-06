"""Подключение к SQLite: pragma-настройки и идемпотентные миграции."""

import sqlite3
from pathlib import Path

# Корень проекта — не зависит от cwd (db/connection.py → src → корень).
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS_DIR = _PROJECT_ROOT / "migrations"

# Текущая (максимальная известная) версия схемы. Управляется номером файла
# миграции NNN_*.sql; здесь — ожидаемая по коду.
SCHEMA_VERSION = 1


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


def migrate(conn: sqlite3.Connection) -> None:
    """Применяет миграции ``NNN_*.sql`` до ``SCHEMA_VERSION`` (идемпотентно).

    Версия берётся из ``PRAGMA user_version``; применяются файлы с номером
    строго больше текущей версии.
    """
    current = conn.execute("PRAGMA user_version").fetchone()[0]

    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"Схема БД новее приложения (user_version={current} > {SCHEMA_VERSION})."
            " Обнови приложение или укажи другую БД."
        )
    if current == SCHEMA_VERSION:
        return
    if not MIGRATIONS_DIR.is_dir():
        raise RuntimeError(f"Каталог миграций не найден: {MIGRATIONS_DIR}")

    for path in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql")):
        version = int(path.name[:3])
        if version <= current:
            continue
        conn.executescript(path.read_text(encoding="utf-8"))
        # PRAGMA не принимает плейсхолдеры; version — целое из имени файла.
        conn.execute(f"PRAGMA user_version = {version}")
        conn.commit()
        current = version

    if current < SCHEMA_VERSION:
        raise RuntimeError(
            f"Нет миграций до SCHEMA_VERSION={SCHEMA_VERSION} (применено до {current})."
            f" Проверь {MIGRATIONS_DIR}."
        )
