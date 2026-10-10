"""Вход, регистрация, сессии и защита страниц (Срез 38)."""

import re

import httpx
import pytest

from llm_tutor.web.app import create_app
from llm_tutor.web.security import SESSION_COOKIE, safe_next

ANN = {"email": "ann@example.com", "name": "Аня", "password": "секретный-пароль"}


async def _register(web, **overrides) -> httpx.Response:
    return await web.post("/api/auth/register", json={**ANN, **overrides})


async def test_register_logs_in(web) -> None:
    response = await _register(web)

    assert response.status_code == 201
    assert response.json()["user"] == {"email": "ann@example.com", "name": "Аня", "initial": "А"}
    me = await web.get("/api/me")
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "ann@example.com"


async def test_session_cookie_flags(web) -> None:
    response = await _register(web)

    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/" in cookie
    assert "Secure" not in cookie  # COOKIE_SECURE=false по умолчанию


async def test_secure_cookie_when_configured(web_settings, web_llm) -> None:
    app = create_app(web_settings.model_copy(update={"cookie_secure": True}), client=web_llm)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://testserver"
    ) as client:
        response = await client.post("/api/auth/register", json=ANN)

    assert "Secure" in response.headers["set-cookie"]


async def test_register_errors_name_the_field(web) -> None:
    await _register(web)
    await web.post("/api/auth/logout", json={})

    duplicate = await _register(web, email="ANN@example.com")
    weak = await _register(web, email="bob@example.com", password="123")

    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["field"] == "email"
    assert weak.status_code == 422
    assert weak.json()["detail"]["field"] == "password"


async def test_malformed_body_is_a_field_error_not_a_stack(web) -> None:
    response = await web.post("/api/auth/register", json={"email": "ann@example.com"})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["message"]
    assert detail["field"] in {"name", "password"}


async def test_registration_can_be_closed(web_settings, web_llm) -> None:
    app = create_app(web_settings.model_copy(update={"registration_open": False}), client=web_llm)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post("/api/auth/register", json=ANN)
        page = await client.get("/register")

    assert response.status_code == 403
    assert "закрыта" in page.text


async def test_login_and_logout(web) -> None:
    await _register(web)
    await web.post("/api/auth/logout", json={})
    assert (await web.get("/api/me")).status_code == 401

    login = await web.post(
        "/api/auth/login", json={"email": " ANN@example.com ", "password": ANN["password"]}
    )

    assert login.status_code == 200
    assert (await web.get("/api/me")).status_code == 200


async def test_logout_kills_session_on_server(web, web_app) -> None:
    """Выход удаляет сессию в БД: старый cookie больше не пускает."""
    await _register(web)
    token = web.cookies[SESSION_COOKIE]

    await web.post("/api/auth/logout", json={})

    web.cookies.set(SESSION_COOKIE, token)
    assert (await web.get("/api/me")).status_code == 401


async def test_wrong_password(web) -> None:
    await _register(web)
    await web.post("/api/auth/logout", json={})

    response = await web.post("/api/auth/login", json={"email": ANN["email"], "password": "не тот"})

    assert response.status_code == 401
    assert response.json()["detail"]["message"] == "Неверный email или пароль."
    assert SESSION_COOKIE not in response.headers.get("set-cookie", "")


async def test_login_rate_limit(web, web_app) -> None:
    await _register(web)
    await web.post("/api/auth/logout", json={})
    clock = {"now": 1000.0}
    web_app.state.login_limiter.clock = lambda: clock["now"]
    wrong = {"email": ANN["email"], "password": "не тот"}

    for _ in range(5):
        assert (await web.post("/api/auth/login", json=wrong)).status_code == 401
    # Шестая попытка не проверяется даже с верным паролем.
    blocked = await web.post("/api/auth/login", json={"email": ANN["email"], "password": ANN["password"]})
    assert blocked.status_code == 429

    clock["now"] += 15 * 60 + 1
    allowed = await web.post("/api/auth/login", json={"email": ANN["email"], "password": ANN["password"]})
    assert allowed.status_code == 200


async def test_rate_limit_counts_email_case_insensitively(web, web_app) -> None:
    web_app.state.login_limiter.clock = lambda: 1000.0
    for variant in ("a@x.io", "A@x.io", " a@X.io", "a@x.IO", "A@X.IO"):
        await web.post("/api/auth/login", json={"email": variant, "password": "не тот"})

    assert (await web.post("/api/auth/login", json={"email": "a@x.io", "password": "x"})).status_code == 429


async def test_expired_session(web, web_app) -> None:
    await _register(web)
    web_app.state.accounts.execute("UPDATE web_sessions SET expires_at = 0")
    web_app.state.accounts.commit()

    assert (await web.get("/api/me")).status_code == 401
    page = await web.get("/profile")
    assert page.status_code == 303


async def test_rename_and_change_password(web) -> None:
    await _register(web)

    renamed = await web.post("/api/me", json={"name": "Анна"})
    wrong = await web.post("/api/me/password", json={"current": "не тот", "new": "новый-пароль"})
    changed = await web.post(
        "/api/me/password", json={"current": ANN["password"], "new": "новый-пароль"}
    )

    assert renamed.json()["user"]["name"] == "Анна"
    assert wrong.status_code == 422
    assert wrong.json()["detail"]["field"] == "current"
    assert changed.status_code == 200
    # Текущая сессия переживает смену пароля.
    assert (await web.get("/api/me")).status_code == 200
    await web.post("/api/auth/logout", json={})
    relogin = await web.post("/api/auth/login", json={"email": ANN["email"], "password": "новый-пароль"})
    assert relogin.status_code == 200


# --- CSRF: только JSON и только со своего сайта ---


async def test_form_post_is_rejected(web) -> None:
    response = await web.post("/api/auth/login", data={"email": "a@b.io", "password": "x"})

    assert response.status_code == 415


async def test_post_without_body_is_rejected(web) -> None:
    assert (await web.post("/api/auth/logout")).status_code == 415


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.example"},
        {"Origin": "null"},
        {"Origin": "http://testserver.evil.example"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
    ],
)
async def test_cross_site_post_is_rejected(web, headers: dict) -> None:
    response = await web.post("/api/auth/register", json=ANN, headers=headers)

    assert response.status_code == 403
    assert SESSION_COOKIE not in response.headers.get("set-cookie", "")


async def test_same_origin_post_is_accepted(web) -> None:
    response = await web.post(
        "/api/auth/register",
        json=ANN,
        headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
    )

    assert response.status_code == 201


async def test_get_api_is_not_blocked_by_content_type(web) -> None:
    assert (await web.get("/api/me")).status_code == 401


# --- Страницы ---


@pytest.mark.parametrize("path", ["/courses", "/lesson", "/profile"])
async def test_private_pages_redirect_guest_to_login(web, path: str) -> None:
    response = await web.get(path)

    assert response.status_code == 303
    assert response.headers["location"] == f"/login?next={path.replace('/', '%2F')}"


async def test_home_is_open_to_guest(web) -> None:
    response = await web.get("/")

    assert response.status_code == 200
    assert 'href="/login"' in response.text


async def test_private_page_shows_user_after_login(web) -> None:
    await _register(web)

    response = await web.get("/profile")

    assert response.status_code == 200
    assert "ann@example.com" in response.text
    assert 'id="logout"' in response.text


async def test_user_name_is_escaped(web) -> None:
    await _register(web, name="<img src=x onerror=alert(1)>")

    html = (await web.get("/profile")).text

    assert "<img src=x" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


@pytest.mark.parametrize("path", ["/login", "/register"])
async def test_auth_pages_are_open_to_guest(web, path: str) -> None:
    response = await web.get(path)

    assert response.status_code == 200
    assert "<form" in response.text


@pytest.mark.parametrize("path", ["/login", "/register"])
async def test_auth_pages_send_logged_in_user_on(web, path: str) -> None:
    await _register(web)

    response = await web.get(f"{path}?next=/profile")

    assert response.status_code == 303
    assert response.headers["location"] == "/profile"


@pytest.mark.parametrize(
    "value",
    [
        "https://evil.example",
        "//evil.example",
        "/\\evil.example",
        "\\\\evil.example",
        "javascript:alert(1)",
        "evil.example",
        "",
        # Браузер выбрасывает из URL табы и переводы строк: «/\t/x» станет «//x».
        "/\t/evil.example",
        "/\n/evil.example",
        " /profile",
    ],
)
def test_safe_next_rejects_foreign_targets(value: str) -> None:
    assert safe_next(value) == "/"


@pytest.mark.parametrize("value", ["/", "/profile", "/lesson/mlcourse-topic01?tab=code"])
def test_safe_next_keeps_own_paths(value: str) -> None:
    assert safe_next(value) == value


async def test_login_page_sanitizes_next(web) -> None:
    html = (await web.get("/login?next=//evil.example")).text

    assert re.search(r'data-next="/"', html)
    assert "evil.example" not in html


async def test_logged_in_user_with_foreign_next_goes_home(web) -> None:
    await _register(web)

    response = await web.get("/login?next=https://evil.example")

    assert response.headers["location"] == "/"
