"""Аккаунты учеников: пользователи, пароли, веб-сессии.

У каждого ученика своя БД курса (``web/userdb.py``), а здесь — общее для всех:
кто есть кто, как он входит и на что записан (спека веба §4). Схема —
``migrations_accounts/``.

Пароли — ``hashlib.scrypt`` с солью, сессии — случайный токен в cookie, а в
БД — только его sha256: утечка файла не даёт ни паролей, ни входа.
"""

import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from llm_tutor.db.connection import get_conn, migrate

# Корень проекта (web/accounts.py → src → корень) — не зависит от cwd.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS_DIR = _PROJECT_ROOT / "migrations_accounts"
SCHEMA_VERSION = 1

# Срок сессии; продлевается, когда прошло больше половины (скользящий срок).
SESSION_TTL = 30 * 24 * 3600.0

# scrypt n=2¹⁴, r=8, p=1: ~16 МБ памяти и десятки миллисекунд на проверку —
# дёшево для входа, дорого для перебора.
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
_SALT_BYTES = 16
_HASH_BYTES = 32

MIN_PASSWORD = 8
# Сверху пароль ограничен, чтобы мегабайтный «пароль» не грел процессор.
MAX_PASSWORD = 256
MAX_NAME = 60
MAX_EMAIL = 254
# Токен длиннее этого — точно не наш: в БД его не ищем.
_MAX_TOKEN = 200

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

EMAIL_INVALID = "Введите email в виде name@example.com."
EMAIL_TAKEN = "Этот email уже зарегистрирован — войдите или укажите другой."
NAME_INVALID = f"Укажите имя: от 1 до {MAX_NAME} символов."
PASSWORD_INVALID = f"Пароль — от {MIN_PASSWORD} до {MAX_PASSWORD} символов."
CURRENT_PASSWORD_WRONG = "Текущий пароль не подходит."


class AccountError(ValueError):
    """Ошибка ввода с полем формы, рядом с которым её показать."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message


class EmailTaken(AccountError):
    """Email уже зарегистрирован (конфликт, а не ошибка формата)."""

    def __init__(self) -> None:
        super().__init__("email", EMAIL_TAKEN)


@dataclass(frozen=True)
class User:
    id: int
    email: str
    name: str
    created_at: float

    @property
    def initial(self) -> str:
        """Буква для аватара."""
        return self.name[:1].upper() or "?"


def open_accounts(path: str) -> sqlite3.Connection:
    """Соединение с БД аккаунтов, схема доведена до текущей версии."""
    conn = get_conn(path)
    migrate(conn, migrations_dir=MIGRATIONS_DIR, schema_version=SCHEMA_VERSION)
    return conn


# --- Пароли ---


def hash_password(password: str) -> str:
    """Хеш пароля: ``scrypt$n$r$p$соль$хеш`` (соль и хеш — hex)."""
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=_HASH_BYTES,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Подходит ли пароль к хешу. Битая строка хеша — «не подходит», не исключение."""
    try:
        scheme, n, r, p, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, MemoryError):
        return False
    return bool(expected) and hmac.compare_digest(actual, expected)


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """Хеш, с которым сверяется вход по неизвестному email: время то же."""
    return hash_password(secrets.token_hex(16))


# --- Проверка ввода ---


def normalize_email(email: str) -> str:
    """Email в нижнем регистре без пробелов по краям или ``AccountError``."""
    value = email.strip().lower()
    if len(value) > MAX_EMAIL or not _EMAIL_RE.match(value):
        raise AccountError("email", EMAIL_INVALID)
    return value


def _clean_name(name: str) -> str:
    value = name.strip()
    if not value or len(value) > MAX_NAME:
        raise AccountError("name", NAME_INVALID)
    return value


def _check_password(password: str, *, field: str) -> None:
    if not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
        raise AccountError(field, PASSWORD_INVALID)


def _user(row: sqlite3.Row) -> User:
    return User(
        id=row["id"],
        email=row["email"],
        name=row["name"],
        created_at=row["created_at"],
    )


# --- Пользователи ---


def create_user(
    conn: sqlite3.Connection,
    *,
    email: str,
    name: str,
    password: str,
    now: float | None = None,
) -> User:
    """Регистрирует ученика. Ошибка ввода — ``AccountError`` с полем формы."""
    clean_email = normalize_email(email)
    clean_name = _clean_name(name)
    _check_password(password, field="password")
    stamp = time.time() if now is None else now
    try:
        cur = conn.execute(
            "INSERT INTO users (email, name, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (clean_email, clean_name, hash_password(password), stamp),
        )
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise EmailTaken() from exc
    conn.commit()
    return User(id=int(cur.lastrowid), email=clean_email, name=clean_name, created_at=stamp)


def get_user(conn: sqlite3.Connection, user_id: int) -> User | None:
    row = conn.execute(
        "SELECT id, email, name, created_at FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    return None if row is None else _user(row)


def authenticate(conn: sqlite3.Connection, email: str, password: str) -> User | None:
    """Ученик по email и паролю или ``None``.

    Неизвестный email сверяется с фиктивным хешем: по времени ответа нельзя
    узнать, зарегистрирован ли адрес.
    """
    if len(password) > MAX_PASSWORD:
        return None
    try:
        clean_email = normalize_email(email)
    except AccountError:
        clean_email = None
    row = (
        conn.execute(
            "SELECT id, email, name, created_at, password_hash FROM users WHERE email = ?",
            (clean_email,),
        ).fetchone()
        if clean_email is not None
        else None
    )
    stored = row["password_hash"] if row is not None else _dummy_hash()
    matches = verify_password(password, stored)
    if row is None or not matches:
        return None
    return _user(row)


def rename(conn: sqlite3.Connection, user_id: int, name: str) -> User:
    clean_name = _clean_name(name)
    conn.execute("UPDATE users SET name = ? WHERE id = ?", (clean_name, user_id))
    conn.commit()
    user = get_user(conn, user_id)
    if user is None:
        raise LookupError(f"Нет пользователя {user_id}")
    return user


def change_password(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    current: str,
    new: str,
    keep_token: str | None,
) -> None:
    """Меняет пароль после проверки текущего и закрывает прочие сессии.

    Смена пароля — обычно реакция на утечку: чужой вход с другого устройства
    должен перестать работать. Сессия ``keep_token`` (та, из которой меняют)
    остаётся.
    """
    row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None or not verify_password(current, row["password_hash"]):
        raise AccountError("current", CURRENT_PASSWORD_WRONG)
    _check_password(new, field="new")
    conn.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(new), user_id)
    )
    keep = _token_hash(keep_token) if keep_token else ""
    conn.execute(
        "DELETE FROM web_sessions WHERE user_id = ? AND token_hash != ?", (user_id, keep)
    )
    conn.commit()


# --- Веб-сессии ---


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(conn: sqlite3.Connection, user_id: int, *, now: float | None = None) -> str:
    """Новая сессия: возвращает токен для cookie (в БД — только его хеш)."""
    stamp = time.time() if now is None else now
    token = secrets.token_urlsafe(32)
    # Попутно чистим протухшие: иначе таблица растёт с каждым входом.
    conn.execute("DELETE FROM web_sessions WHERE expires_at <= ?", (stamp,))
    conn.execute(
        "INSERT INTO web_sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (_token_hash(token), user_id, stamp, stamp + SESSION_TTL),
    )
    conn.commit()
    return token


def resolve_session(
    conn: sqlite3.Connection, token: str, *, now: float | None = None
) -> User | None:
    """Ученик по токену сессии или ``None`` (нет, протухла, мусор)."""
    if not token or len(token) > _MAX_TOKEN:
        return None
    stamp = time.time() if now is None else now
    digest = _token_hash(token)
    row = conn.execute(
        "SELECT s.expires_at, u.id, u.email, u.name, u.created_at "
        "FROM web_sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
        (digest,),
    ).fetchone()
    if row is None:
        return None
    if row["expires_at"] <= stamp:
        conn.execute("DELETE FROM web_sessions WHERE token_hash = ?", (digest,))
        conn.commit()
        return None
    if row["expires_at"] - stamp < SESSION_TTL / 2:
        conn.execute(
            "UPDATE web_sessions SET expires_at = ? WHERE token_hash = ?",
            (stamp + SESSION_TTL, digest),
        )
        conn.commit()
    return _user(row)


def delete_session(conn: sqlite3.Connection, token: str) -> None:
    conn.execute("DELETE FROM web_sessions WHERE token_hash = ?", (_token_hash(token),))
    conn.commit()


# --- Записи на курсы ---


def enroll(
    conn: sqlite3.Connection, user_id: int, course_id: str, *, now: float | None = None
) -> None:
    """Записывает ученика на курс (повторная запись ничего не меняет)."""
    stamp = time.time() if now is None else now
    conn.execute(
        "INSERT OR IGNORE INTO enrollments (user_id, course_id, enrolled_at) VALUES (?, ?, ?)",
        (user_id, course_id, stamp),
    )
    conn.commit()


def enrolled_courses(conn: sqlite3.Connection, user_id: int) -> list[str]:
    """Курсы ученика, последний записанный — первым."""
    rows = conn.execute(
        "SELECT course_id FROM enrollments WHERE user_id = ? ORDER BY enrolled_at DESC, course_id",
        (user_id,),
    ).fetchall()
    return [row["course_id"] for row in rows]


def find_user(conn: sqlite3.Connection, email: str) -> User | None:
    """Ученик по email (для CLI); некорректный email — ``None``."""
    try:
        clean = normalize_email(email)
    except AccountError:
        return None
    row = conn.execute(
        "SELECT id, email, name, created_at FROM users WHERE email = ?", (clean,)
    ).fetchone()
    return None if row is None else _user(row)
