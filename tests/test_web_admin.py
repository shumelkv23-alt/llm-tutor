"""Админка: роль, доступ, меню, CLI (Срез 63)."""

import sqlite3

import httpx
import pytest

from llm_tutor.web import accounts, api_admin, security
from llm_tutor.web.cli import main as cli_main

ANN = {"email": "ann@example.com", "name": "Аня", "password": "секретный-пароль"}
BOB = {"email": "bob@example.com", "name": "Боб", "password": "другой-пароль-1"}
ADMIN = {"email": "root@example.com", "name": "Админ", "password": "пароль-админа-1"}


def _set_admin(web_app, email: str, value: bool = True) -> None:
    db = web_app.state.accounts
    user = accounts.find_user(db, email)
    assert user is not None
    accounts.set_admin(db, user.id, value)


async def _sign_up(client, person: dict) -> None:
    response = await client.post("/api/auth/register", json=person)
    assert response.status_code == 201


@pytest.fixture
async def admin(web, web_app):
    """Клиент вошедшего админа."""
    await _sign_up(web, ADMIN)
    _set_admin(web_app, ADMIN["email"])
    return web


# --- Доступ ---


async def test_guest_is_sent_to_login(web) -> None:
    response = await web.get("/admin")

    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=%2Fadmin"


async def test_guest_on_students_page_is_sent_to_login(web) -> None:
    response = await web.get("/admin/students")

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login?next=")


async def test_plain_student_gets_404_like_an_unknown_page(student) -> None:
    unknown = await student.get("/no-such-page")

    for path in ("/admin", "/admin/students"):
        response = await student.get(path)
        assert response.status_code == 404
        # Тот же ответ, что у несуществующей страницы: раздел не выдаёт себя.
        assert response.json() == unknown.json()


async def test_admin_is_redirected_to_students(admin) -> None:
    response = await admin.get("/admin")

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/students"
    assert (await admin.get("/admin/students")).status_code == 200


async def test_revoked_admin_loses_access_without_relogin(admin, web_app) -> None:
    assert (await admin.get("/admin/students")).status_code == 200

    _set_admin(web_app, ADMIN["email"], False)

    assert (await admin.get("/admin/students")).status_code == 404


async def test_admin_role_is_not_given_by_the_web(student) -> None:
    """Роль выдаёт только CLI: регистрация и профиль её не принимают."""
    response = await student.post("/api/me", json={"name": "Аня", "is_admin": True})

    assert response.status_code == 200
    assert (await student.get("/admin/students")).status_code == 404


def _admin_endpoints(web_app) -> list[tuple[str, str]]:
    """Все (метод, путь) админки из схемы приложения.

    Не ``app.routes``: включённые роутеры там лежат лениво и путей не отдают.
    """
    endpoints = [
        (method.upper(), path.replace("{user_id}", "1"))
        for path, methods in web_app.openapi()["paths"].items()
        if path.startswith(("/admin", "/api/admin"))
        for method in methods
    ]
    assert ("GET", "/admin/students") in endpoints
    return endpoints


async def _call(client, method: str, path: str):
    return await client.request(method, path, json=None if method == "GET" else {})


async def test_every_admin_route_hides_from_plain_student(student, web_app) -> None:
    """Перебор маршрутов: ни один путь админки не отвечает не-админу иначе, чем 404."""
    for method, path in _admin_endpoints(web_app):
        response = await _call(student, method, path)
        assert response.status_code == 404, (method, path)


async def test_every_admin_route_asks_guest_to_log_in(web, web_app) -> None:
    for method, path in _admin_endpoints(web_app):
        response = await _call(web, method, path)
        assert response.status_code in (303, 401), (method, path)


def test_api_router_is_guarded_as_a_whole() -> None:
    """Защита на роутере, а не в каждой функции: новый маршрут её не забудет."""
    guards = [dependency.dependency for dependency in api_admin.router.dependencies]

    assert security.admin_user in guards


# --- Последний вход ---


def _last_login(web_app, email: str):
    row = web_app.state.accounts.execute(
        "SELECT last_login_at FROM users WHERE email = ?", (email,)
    ).fetchone()
    return row[0]


async def test_register_and_login_record_last_login(web, web_app) -> None:
    await _sign_up(web, ANN)
    registered = _last_login(web_app, ANN["email"])
    assert registered is not None

    await web.post("/api/auth/logout", json={})
    response = await web.post(
        "/api/auth/login", json={"email": ANN["email"], "password": ANN["password"]}
    )

    assert response.status_code == 200
    assert _last_login(web_app, ANN["email"]) >= registered


async def test_failed_login_does_not_touch_last_login(web, web_app) -> None:
    await _sign_up(web, ANN)
    before = _last_login(web_app, ANN["email"])
    await web.post("/api/auth/logout", json={})

    response = await web.post("/api/auth/login", json={"email": ANN["email"], "password": "не-тот-пароль"})

    assert response.status_code == 401
    assert _last_login(web_app, ANN["email"]) == before


# --- Меню ---


async def test_admin_menu_item_only_for_admins(admin, web_app) -> None:
    page = (await admin.get("/admin/students")).text
    home = (await admin.get("/")).text

    assert 'data-nav="admin"' in page
    assert 'href="/admin"' in home


async def test_plain_student_and_guest_have_no_admin_menu_item(student, web_app) -> None:
    assert 'href="/admin"' not in (await student.get("/")).text
    transport = httpx.ASGITransport(app=web_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as guest:
        assert 'href="/admin"' not in (await guest.get("/")).text


# --- CLI ---


def _user(web_settings, person: dict = ANN):
    db = accounts.open_accounts(web_settings.accounts_db_path)
    user = accounts.create_user(db, **person)
    db.close()
    return user


def _is_admin(web_settings, email: str) -> bool:
    db = accounts.open_accounts(web_settings.accounts_db_path)
    try:
        user = accounts.find_user(db, email)
        return user is not None and user.is_admin
    finally:
        db.close()


def test_make_and_revoke_admin(web_settings) -> None:
    _user(web_settings)

    assert cli_main(["make-admin", "--email", "ANN@example.com"], settings=web_settings) == 0
    assert _is_admin(web_settings, "ann@example.com")
    # Повторный запуск ничего не ломает.
    assert cli_main(["make-admin", "--email", "ann@example.com"], settings=web_settings) == 0
    assert _is_admin(web_settings, "ann@example.com")

    assert cli_main(["revoke-admin", "--email", "ann@example.com"], settings=web_settings) == 0
    assert not _is_admin(web_settings, "ann@example.com")
    assert cli_main(["revoke-admin", "--email", "ann@example.com"], settings=web_settings) == 0


@pytest.mark.parametrize("command", ["make-admin", "revoke-admin"])
def test_admin_commands_unknown_email(web_settings, capsys, command: str) -> None:
    code = cli_main([command, "--email", "nobody@example.com"], settings=web_settings)

    assert code == 1
    assert "зарегистр" in capsys.readouterr().out


def test_make_admin_does_not_touch_other_users(web_settings) -> None:
    _user(web_settings)
    _user(web_settings, BOB)

    cli_main(["make-admin", "--email", "ann@example.com"], settings=web_settings)

    assert not _is_admin(web_settings, "bob@example.com")


def test_cli_leaves_database_usable(web_settings) -> None:
    _user(web_settings)
    cli_main(["make-admin", "--email", "ann@example.com"], settings=web_settings)

    conn = sqlite3.connect(web_settings.accounts_db_path)
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    conn.close()
