"""Аккаунты: пользователи, пароли, веб-сессии (Срез 38)."""

import hashlib

import pytest

from llm_tutor.web import accounts
from llm_tutor.web.accounts import AccountError


@pytest.fixture
def db(tmp_path):
    connection = accounts.open_accounts(str(tmp_path / "accounts.sqlite3"))
    yield connection
    connection.close()


def _user(db, email: str = "Ann@Example.com", name: str = "Аня", password: str = "секретный-пароль"):
    return accounts.create_user(db, email=email, name=name, password=password, now=100.0)


def test_schema_has_its_own_version(db) -> None:
    assert db.execute("PRAGMA user_version").fetchone()[0] == accounts.SCHEMA_VERSION
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"users", "web_sessions", "enrollments"} <= tables
    # Схема ученика сюда не попадает: это другая БД.
    assert "events" not in tables


def test_open_is_idempotent(tmp_path) -> None:
    path = str(tmp_path / "accounts.sqlite3")
    first = accounts.open_accounts(path)
    _user(first)
    first.close()

    again = accounts.open_accounts(path)
    assert again.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    again.close()


def test_create_user_normalizes_email_and_name(db) -> None:
    user = _user(db, email="  Ann@Example.COM ", name="  Аня  ")

    assert user.email == "ann@example.com"
    assert user.name == "Аня"
    assert user.initial == "А"
    assert user.created_at == 100.0


def test_password_is_not_stored_in_plain_text(db) -> None:
    _user(db, password="секретный-пароль")

    stored = db.execute("SELECT password_hash FROM users").fetchone()[0]
    assert "секретный-пароль" not in stored
    assert stored.startswith("scrypt$")


def test_same_password_gets_different_salt(db) -> None:
    first = accounts.hash_password("одинаковый-пароль")
    second = accounts.hash_password("одинаковый-пароль")

    assert first != second
    assert accounts.verify_password("одинаковый-пароль", first)
    assert accounts.verify_password("одинаковый-пароль", second)
    assert not accounts.verify_password("другой-пароль", first)


@pytest.mark.parametrize("stored", ["", "plain", "scrypt$1$2", "md5$x$y$z$aa$bb", "scrypt$a$b$c$zz$yy"])
def test_verify_rejects_malformed_hash(stored: str) -> None:
    """Битая строка в БД — «не подошёл», а не исключение посреди входа."""
    assert not accounts.verify_password("что угодно", stored)


def test_duplicate_email_is_rejected_case_insensitively(db) -> None:
    _user(db, email="ann@example.com")

    with pytest.raises(AccountError) as exc_info:
        _user(db, email="ANN@example.com")

    assert exc_info.value.field == "email"


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"email": "без-собаки"}, "email"),
        ({"email": "a@b"}, "email"),
        ({"email": "a b@example.com"}, "email"),
        ({"email": "x" * 250 + "@example.com"}, "email"),
        ({"name": "   "}, "name"),
        ({"name": "я" * 61}, "name"),
        ({"password": "1234567"}, "password"),
        ({"password": "x" * 257}, "password"),
    ],
)
def test_invalid_input_names_the_field(db, overrides: dict, field: str) -> None:
    with pytest.raises(AccountError) as exc_info:
        _user(db, **overrides)

    assert exc_info.value.field == field
    assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_authenticate(db) -> None:
    user = _user(db, password="секретный-пароль")

    assert accounts.authenticate(db, "ANN@example.com", "секретный-пароль") == user
    assert accounts.authenticate(db, "ann@example.com", "не тот") is None
    assert accounts.authenticate(db, "nobody@example.com", "секретный-пароль") is None


def test_authenticate_unknown_email_still_hashes(db, monkeypatch) -> None:
    """Неизвестный email проверяется так же долго: по времени не отличить."""
    calls: list[str] = []
    real = accounts.verify_password
    monkeypatch.setattr(
        accounts, "verify_password", lambda password, stored: calls.append(stored) or real(password, stored)
    )

    accounts.authenticate(db, "nobody@example.com", "что-то")

    assert len(calls) == 1
    assert calls[0].startswith("scrypt$")


def test_session_roundtrip(db) -> None:
    user = _user(db)

    token = accounts.create_session(db, user.id, now=1000.0)

    assert len(token) >= 40
    assert accounts.resolve_session(db, token, now=1001.0) == user


def test_session_token_is_stored_hashed(db) -> None:
    user = _user(db)
    token = accounts.create_session(db, user.id, now=1000.0)

    stored = [row[0] for row in db.execute("SELECT token_hash FROM web_sessions")]
    assert token not in stored
    assert stored == [hashlib.sha256(token.encode()).hexdigest()]


def test_session_expires(db) -> None:
    user = _user(db)
    token = accounts.create_session(db, user.id, now=1000.0)

    assert accounts.resolve_session(db, token, now=1000.0 + accounts.SESSION_TTL + 1) is None
    # Протухшая сессия удаляется, а не копится.
    assert db.execute("SELECT COUNT(*) FROM web_sessions").fetchone()[0] == 0


def test_session_slides_when_half_used(db) -> None:
    user = _user(db)
    token = accounts.create_session(db, user.id, now=0.0)

    later = accounts.SESSION_TTL * 0.6
    assert accounts.resolve_session(db, token, now=later) == user
    # Продлили: через полный срок от создания сессия ещё жива.
    assert accounts.resolve_session(db, token, now=accounts.SESSION_TTL + 10) == user


@pytest.mark.parametrize("token", ["", "мусор", "a" * 500])
def test_unknown_token_resolves_to_nobody(db, token: str) -> None:
    assert accounts.resolve_session(db, token, now=1.0) is None


def test_delete_session(db) -> None:
    user = _user(db)
    token = accounts.create_session(db, user.id, now=1000.0)

    accounts.delete_session(db, token)

    assert accounts.resolve_session(db, token, now=1001.0) is None


def test_change_password_checks_current_and_drops_other_sessions(db) -> None:
    user = _user(db, password="старый-пароль")
    keep = accounts.create_session(db, user.id, now=1000.0)
    other = accounts.create_session(db, user.id, now=1000.0)

    with pytest.raises(AccountError) as exc_info:
        accounts.change_password(db, user.id, current="не тот", new="новый-пароль", keep_token=keep)
    assert exc_info.value.field == "current"

    accounts.change_password(db, user.id, current="старый-пароль", new="новый-пароль", keep_token=keep)

    assert accounts.authenticate(db, user.email, "новый-пароль") == user
    assert accounts.authenticate(db, user.email, "старый-пароль") is None
    assert accounts.resolve_session(db, keep, now=1001.0) == user
    assert accounts.resolve_session(db, other, now=1001.0) is None


def test_change_password_validates_new(db) -> None:
    user = _user(db, password="старый-пароль")

    with pytest.raises(AccountError) as exc_info:
        accounts.change_password(db, user.id, current="старый-пароль", new="short", keep_token=None)

    assert exc_info.value.field == "new"


def test_rename(db) -> None:
    user = _user(db)

    renamed = accounts.rename(db, user.id, "  Анна ")

    assert renamed.name == "Анна"
    assert accounts.get_user(db, user.id).name == "Анна"
    with pytest.raises(AccountError):
        accounts.rename(db, user.id, "")


def test_users_are_isolated(db) -> None:
    ann = _user(db, email="ann@example.com")
    bob = _user(db, email="bob@example.com", name="Боб")
    token = accounts.create_session(db, bob.id, now=1000.0)

    assert accounts.resolve_session(db, token, now=1001.0) == bob
    assert accounts.get_user(db, ann.id).email == "ann@example.com"


def test_enrollment_moves_to_renamed_course(tmp_path) -> None:
    """Миграция 002: запись на mlcourse-topic01 становится записью на mlcourse."""
    import sqlite3

    path = tmp_path / "accounts.sqlite3"
    old = sqlite3.connect(path)
    old.executescript((accounts.MIGRATIONS_DIR / "001_accounts.sql").read_text(encoding="utf-8"))
    old.execute("PRAGMA user_version = 1")
    old.execute(
        "INSERT INTO users (id, email, name, password_hash, created_at) "
        "VALUES (1, 'ann@example.com', 'Аня', 'x', 0)"
    )
    old.execute("INSERT INTO enrollments VALUES (1, 'mlcourse-topic01', 5.0)")
    old.commit()
    old.close()

    db = accounts.open_accounts(str(path))

    assert accounts.enrolled_courses(db, 1) == ["mlcourse"]
    db.close()


# --- Роль админа и последний вход (Срез 63) ---


def _version_2_db(path) -> None:
    """БД аккаунтов версии 2 с данными — как до миграции 003."""
    import sqlite3

    old = sqlite3.connect(path)
    for name in ("001_accounts.sql", "002_course_rename.sql"):
        old.executescript((accounts.MIGRATIONS_DIR / name).read_text(encoding="utf-8"))
    old.execute("PRAGMA user_version = 2")
    old.execute(
        "INSERT INTO users (id, email, name, password_hash, created_at) "
        "VALUES (1, 'ann@example.com', 'Аня', 'x', 7.0)"
    )
    old.execute("INSERT INTO web_sessions VALUES ('hash', 1, 1.0, 9999999999.0)")
    old.execute("INSERT INTO enrollments VALUES (1, 'mlcourse', 5.0)")
    old.commit()
    old.close()


def test_schema_has_admin_tables_and_columns(db) -> None:
    columns = {row[1] for row in db.execute("PRAGMA table_info(users)")}
    log = {row[1] for row in db.execute("PRAGMA table_info(admin_log)")}

    assert accounts.SCHEMA_VERSION == 3
    assert {"is_admin", "last_login_at"} <= columns
    assert {"ts", "admin_id", "action", "target_id", "details"} <= log


def test_migration_003_keeps_existing_data(tmp_path) -> None:
    path = tmp_path / "accounts.sqlite3"
    _version_2_db(path)

    db = accounts.open_accounts(str(path))

    assert db.execute("PRAGMA user_version").fetchone()[0] == 3
    user = accounts.get_user(db, 1)
    assert (user.email, user.name, user.created_at, user.is_admin) == ("ann@example.com", "Аня", 7.0, False)
    assert db.execute("SELECT last_login_at FROM users").fetchone()[0] is None
    assert db.execute("SELECT COUNT(*) FROM web_sessions").fetchone()[0] == 1
    assert accounts.enrolled_courses(db, 1) == ["mlcourse"]
    db.close()

    again = accounts.open_accounts(str(path))
    assert again.execute("PRAGMA user_version").fetchone()[0] == 3
    assert again.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    again.close()


def test_schema_newer_than_the_app_is_refused(tmp_path) -> None:
    path = tmp_path / "accounts.sqlite3"
    accounts.open_accounts(str(path)).close()
    import sqlite3

    newer = sqlite3.connect(path)
    newer.execute("PRAGMA user_version = 4")
    newer.commit()
    newer.close()

    with pytest.raises(RuntimeError):
        accounts.open_accounts(str(path))


def test_new_user_is_not_admin(db) -> None:
    user = _user(db)

    assert user.is_admin is False
    assert accounts.get_user(db, user.id).is_admin is False


def test_set_admin_is_idempotent_and_reversible(db) -> None:
    user = _user(db)

    accounts.set_admin(db, user.id, True)
    accounts.set_admin(db, user.id, True)
    assert accounts.get_user(db, user.id).is_admin is True

    accounts.set_admin(db, user.id, False)
    assert accounts.get_user(db, user.id).is_admin is False


def test_set_admin_unknown_user(db) -> None:
    with pytest.raises(LookupError):
        accounts.set_admin(db, 999, True)


def test_every_user_lookup_reads_the_role(db) -> None:
    """Роль приходит из всех выборок: иначе где-то админ остался бы «учеником»."""
    user = _user(db, email="root@example.com")
    accounts.set_admin(db, user.id, True)
    token = accounts.create_session(db, user.id, now=1000.0)

    assert accounts.get_user(db, user.id).is_admin
    assert accounts.find_user(db, "root@example.com").is_admin
    assert accounts.credentials(db, "root@example.com")[0].is_admin
    assert accounts.authenticate(db, "root@example.com", "секретный-пароль").is_admin
    assert accounts.resolve_session(db, token, now=1001.0).is_admin
    assert accounts.resolve_session_info(db, token, now=1001.0)[0].is_admin


def test_login_time_is_recorded_by_create_session(db) -> None:
    user = _user(db)
    assert db.execute("SELECT last_login_at FROM users").fetchone()[0] is None

    accounts.create_session(db, user.id, now=200.0)
    assert db.execute("SELECT last_login_at FROM users").fetchone()[0] == 200.0

    accounts.create_session(db, user.id, now=300.0)
    assert db.execute("SELECT last_login_at FROM users").fetchone()[0] == 300.0


def test_session_extension_is_not_a_login(db) -> None:
    user = _user(db)
    token = accounts.create_session(db, user.id, now=200.0)
    late = 200.0 + accounts.SESSION_TTL * 0.6  # осталось меньше половины срока

    _, extended = accounts.resolve_session_info(db, token, now=late)

    assert extended
    assert db.execute("SELECT last_login_at FROM users").fetchone()[0] == 200.0
