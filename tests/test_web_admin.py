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


# --- Снимок и список учеников (Срез 64) ---

import logging
import sqlite3 as _sqlite3
import time

from course_fixtures import WEB_T1
from llm_tutor.db import repos
from llm_tutor.schemas import Event
from llm_tutor.student import survey
from llm_tutor.web import admin as admin_data
from llm_tutor.web.courses import COURSES

COURSE = COURSES[0].id
BASE = f"/api/courses/{COURSE}/lesson"
PAGE_SIZE = 50


def _answers() -> list[int]:
    progress, given = survey.Progress(WEB_T1), []
    while progress.next_key() is not None:
        progress, given = progress.answer(1), [*given, 1]
    return given


def _client(web_app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=web_app), base_url="http://testserver")


async def _learner(web_app, name: str, *, survey_done: bool = False, enroll: bool = True):
    """Ученик со своим клиентом: регистрация, запись на курс, при желании анкета."""
    client = _client(web_app)
    person = {"email": f"{name.lower()}@example.com", "name": name, "password": "секретный-пароль"}
    assert (await client.post("/api/auth/register", json=person)).status_code == 201
    user = accounts.find_user(web_app.state.accounts, person["email"])
    if enroll:
        assert (await client.post(f"/api/courses/{COURSE}/enroll", json={})).status_code == 200
    if survey_done:
        assert (await client.post(f"{BASE}/survey/finish", json={"answers": _answers()})).status_code == 200
    return client, user


def _db(web_app, user):
    return web_app.state.userdbs.open(user.id, COURSE)


def _close_lesson(web_app, user, node_id: str) -> None:
    conn = _db(web_app, user)
    conn.execute(
        "INSERT OR REPLACE INTO mastery (concept_id, alpha, beta, last_seen) VALUES (?, 60, 1, ?)",
        (node_id, time.time()),
    )
    conn.commit()


def _event(web_app, user, ts: float) -> None:
    repos.add_event(_db(web_app, user), Event(source="autotest", result=1.0, ts=ts))


def _snapshot(web_app, **kwargs):
    return admin_data.snapshot(web_app.state, COURSES[0], **kwargs)


async def test_snapshot_row_per_enrolled_student(web_app) -> None:
    ann, ann_user = await _learner(web_app, "Ann", survey_done=True)
    bob, bob_user = await _learner(web_app, "Bob")
    cat, cat_user = await _learner(web_app, "Cat", enroll=False)
    _close_lesson(web_app, ann_user, "pandas_structures")

    snapshot = _snapshot(web_app)

    assert set(snapshot.rows) == {ann_user.id, bob_user.id}
    ann_row, bob_row = snapshot.rows[ann_user.id], snapshot.rows[bob_user.id]
    assert (ann_row.status, ann_row.closed) == ("in_progress", 1)
    assert ann_row.total > 1
    assert (bob_row.status, bob_row.closed) == ("survey", 0)
    assert cat_user.id not in snapshot.rows
    for client in (ann, bob, cat):
        await client.aclose()


async def test_snapshot_progress_matches_the_course_card(web_app) -> None:
    from llm_tutor.core.overview import course_progress

    client, user = await _learner(web_app, "Ann", survey_done=True)
    _close_lesson(web_app, user, "pandas_structures")

    row = _snapshot(web_app).rows[user.id]

    progress = course_progress(_db(web_app, user), settings=web_app.state.settings)
    assert (row.closed, row.total) == (progress.closed, progress.total)
    await client.aclose()


async def test_snapshot_marks_weak_lessons(web_app) -> None:
    client, user = await _learner(web_app, "Ann", survey_done=True)
    conn = _db(web_app, user)
    session_id = repos.ensure_open_session(conn)
    state = repos.get_session_state(conn, session_id)
    assert state.route is not None
    # Текущее занятие слабым не считается (оно «текущее»), берём другое.
    other = next(step.concept_id for step in state.route.steps if step.concept_id != state.current_node_id)
    steps = [
        step.model_copy(update={"status": "closed", "weak": True}) if step.concept_id == other else step
        for step in state.route.steps
    ]
    repos.update_session_state(
        conn, session_id, state.model_copy(update={"route": state.route.model_copy(update={"steps": steps})})
    )

    row = _snapshot(web_app).rows[user.id]

    assert row.weak == 1
    assert row.closed >= 1
    await client.aclose()


async def test_snapshot_last_activity_is_latest_event_or_message(web_app) -> None:
    client, user = await _learner(web_app, "Ann")
    conn = _db(web_app, user)
    _event(web_app, user, 5000.0)
    repos.add_message(conn, repos.ensure_open_session(conn), "user", "привет", ts=7000.0)

    assert _snapshot(web_app).rows[user.id].last_activity == 7000.0

    _event(web_app, user, 9000.0)
    assert _snapshot(web_app, refresh=True).rows[user.id].last_activity == 9000.0
    await client.aclose()


async def test_snapshot_student_without_activity(web_app) -> None:
    client, user = await _learner(web_app, "Ann")

    assert _snapshot(web_app).rows[user.id].last_activity is None
    await client.aclose()


async def test_snapshot_student_without_db_file(web_app) -> None:
    client, user = await _learner(web_app, "Ann")
    path = web_app.state.userdbs.path(user.id, COURSE)
    web_app.state.userdbs.close_all()
    for suffix in ("", "-wal", "-shm"):
        path.with_name(path.name + suffix).unlink(missing_ok=True)

    row = _snapshot(web_app).rows[user.id]

    assert row.status == "no_db"
    assert row.closed is None
    assert not path.exists()  # снимок не создаёт БД
    await client.aclose()


async def test_snapshot_broken_db_does_not_break_the_rest(web_app, monkeypatch, caplog) -> None:
    ann, ann_user = await _learner(web_app, "Ann", survey_done=True)
    bob, bob_user = await _learner(web_app, "Bob", survey_done=True)
    pool = web_app.state.userdbs
    real_open = pool.open

    def open_(user_id, course_id):
        if user_id == bob_user.id:
            raise _sqlite3.DatabaseError("file is not a database")
        return real_open(user_id, course_id)

    monkeypatch.setattr(pool, "open", open_)

    with caplog.at_level(logging.ERROR):
        snapshot = _snapshot(web_app)

    assert snapshot.rows[bob_user.id].status == "error"
    assert snapshot.rows[ann_user.id].status == "in_progress"
    assert any("не открывается" in record.message for record in caplog.records)
    await ann.aclose()
    await bob.aclose()


async def test_snapshot_skips_student_whose_turn_is_running(web_app) -> None:
    client, user = await _learner(web_app, "Ann", survey_done=True)
    first = _snapshot(web_app)
    lock = web_app.state.userdbs.lock(user.id, COURSE)

    await lock.acquire()
    try:
        busy = _snapshot(web_app, refresh=True)
    finally:
        lock.release()

    row = busy.rows[user.id]
    assert row.busy
    # Данные берутся из прошлого снимка, а не из соединения посреди хода.
    assert (row.closed, row.total) == (first.rows[user.id].closed, first.rows[user.id].total)
    await client.aclose()


async def test_snapshot_busy_student_without_earlier_data(web_app) -> None:
    client, user = await _learner(web_app, "Ann", survey_done=True)
    lock = web_app.state.userdbs.lock(user.id, COURSE)

    await lock.acquire()
    try:
        row = _snapshot(web_app).rows[user.id]
    finally:
        lock.release()

    assert row.busy
    assert row.closed is None
    await client.aclose()


async def test_snapshot_is_cached_for_a_minute(web_app, monkeypatch) -> None:
    client, _ = await _learner(web_app, "Ann", survey_done=True)
    calls = []
    real_open = web_app.state.userdbs.open
    monkeypatch.setattr(web_app.state.userdbs, "open", lambda *a: calls.append(a) or real_open(*a))
    now = [1000.0]

    first = _snapshot(web_app, clock=lambda: now[0])
    opened = len(calls)
    assert opened > 0
    now[0] += 59
    assert _snapshot(web_app, clock=lambda: now[0]) is first
    assert len(calls) == opened

    assert _snapshot(web_app, refresh=True, clock=lambda: now[0]) is not first
    assert len(calls) > opened

    again = len(calls)
    now[0] += 61
    assert _snapshot(web_app, clock=lambda: now[0]).built_at == now[0]
    assert len(calls) > again
    await client.aclose()


async def test_snapshot_cache_is_per_app(web_app, web_settings, web_llm) -> None:
    from llm_tutor.web.app import create_app

    client, _ = await _learner(web_app, "Ann", survey_done=True)
    other = create_app(web_settings, client=web_llm)
    try:
        assert web_app.state.admin_cache is not other.state.admin_cache
        _snapshot(web_app)
        assert web_app.state.admin_cache and not other.state.admin_cache
    finally:
        other.state.accounts.close()
    await client.aclose()


def test_account_search_escapes_like_wildcards(web_app) -> None:
    db = web_app.state.accounts
    for email, name in (
        ("a_b@example.com", "Под_черк"),
        ("axb@example.com", "Иксовый"),
        ("p@example.com", "100% учёбы"),
    ):
        accounts.create_user(db, email=email, name=name, password="секретный-пароль")

    assert [u.name for u in accounts.list_users(db, "_")] == ["Под_черк"]
    assert [u.name for u in accounts.list_users(db, "%")] == ["100% учёбы"]
    assert {u.name for u in accounts.list_users(db, "")} == {"Под_черк", "Иксовый", "100% учёбы"}
    assert accounts.list_users(db, "ПОД")[0].name == "Под_черк"  # без учёта регистра
    assert [u.email for u in accounts.list_users(db, "AXB@")] == ["axb@example.com"]


def test_active_sessions_count_only_live_ones(web_app) -> None:
    db = web_app.state.accounts
    user = accounts.create_user(db, email="a@example.com", name="А", password="секретный-пароль")
    accounts.create_session(db, user.id, now=100.0)
    accounts.create_session(db, user.id, now=100.0)

    assert accounts.active_sessions(db, user.id, 200.0) == 2
    assert accounts.active_sessions(db, user.id, 100.0 + accounts.SESSION_TTL + 1) == 0


async def _students_page(admin_client, **params) -> str:
    response = await admin_client.get("/admin/students", params=params)
    assert response.status_code == 200
    return response.text


async def test_students_page_lists_progress(admin, web_app) -> None:
    ann, ann_user = await _learner(web_app, "Ann", survey_done=True)
    _close_lesson(web_app, ann_user, "pandas_structures")
    bob, _ = await _learner(web_app, "Bob")

    html = await _students_page(admin)

    for text in ("Ann", "ann@example.com", "Bob", "bob@example.com", "Админ"):
        assert text in html
    assert "В процессе" in html and "Анкета" in html
    assert "1 из" in html
    await ann.aclose()
    await bob.aclose()


async def test_students_page_search(admin, web_app) -> None:
    clients = [(await _learner(web_app, name, enroll=False))[0] for name in ("Ann", "Bob", "Anna")]

    html = await _students_page(admin, q="ANN")

    assert "ann@example.com" in html and "anna@example.com" in html
    assert "bob@example.com" not in html
    assert "Ничего не найдено" in await _students_page(admin, q="нет-такого")
    # Символы LIKE ищутся буквально.
    assert "Ничего не найдено" in await _students_page(admin, q="%")
    assert "Ничего не найдено" in await _students_page(admin, q="_")
    for client in clients:
        await client.aclose()


async def test_students_page_sorted_by_last_activity(admin, web_app) -> None:
    clients = []
    for name, ts in (("Aaa", 1000.0), ("Bbb", 3000.0), ("Ccc", None), ("Ddd", 2000.0)):
        client, user = await _learner(web_app, name)
        clients.append(client)
        if ts is not None:
            _event(web_app, user, ts)

    html = await _students_page(admin, refresh=1)

    order = [html.index(f"{name.lower()}@example.com") for name in ("Bbb", "Ddd", "Aaa", "Ccc")]
    assert order == sorted(order)  # новые первыми, без активности — в конце
    for client in clients:
        await client.aclose()


async def test_students_page_pagination(admin, web_app) -> None:
    db = web_app.state.accounts
    for index in range(PAGE_SIZE + 5):
        accounts.create_user(
            db, email=f"u{index:03d}@example.com", name=f"Уч{index:03d}", password="секретный-пароль"
        )

    first = await _students_page(admin)
    second = await _students_page(admin, page=2)

    # Без активности — по дате регистрации, новые первыми; админ зарегистрирован раньше всех.
    assert "u054@example.com" in first and "u054@example.com" not in second
    assert "u000@example.com" in second and "u000@example.com" not in first
    assert "root@example.com" in second
    assert await _students_page(admin, page=99) == second  # за границей — последняя
    assert await _students_page(admin, page="abc") == first  # мусор — первая
    assert await _students_page(admin, page=0) == first


async def test_summary_counts_active_and_finished(web_app) -> None:
    now = time.time()
    ann, ann_user = await _learner(web_app, "Ann")
    old, old_user = await _learner(web_app, "Old")
    _event(web_app, ann_user, now - 60)
    _event(web_app, old_user, now - 30 * 86400)

    summary = admin_data.summarize(_snapshot(web_app, refresh=True), total=2, now=now)

    assert (summary.total, summary.active_week, summary.finished) == (2, 1, 0)
    await ann.aclose()
    await old.aclose()


async def test_students_page_search_miss_has_a_message(admin) -> None:
    assert "Ничего не найдено" in await _students_page(admin, q="никто")


async def test_students_page_handles_broken_db(admin, web_app) -> None:
    ann, ann_user = await _learner(web_app, "Ann", survey_done=True)
    bob, bob_user = await _learner(web_app, "Bob", survey_done=True)
    web_app.state.userdbs.close_all()
    web_app.state.userdbs.path(bob_user.id, COURSE).write_bytes(b"not a database")

    html = await _students_page(admin, refresh=1)

    assert "Недоступен" in html
    assert "ann@example.com" in html and "bob@example.com" in html
    await ann.aclose()
    await bob.aclose()


async def test_students_page_escapes_names_and_hides_hashes(admin, web_app) -> None:
    accounts.create_user(
        web_app.state.accounts,
        email="x@example.com",
        name='<script>alert(1)</script>"><img src=x onerror=1>',
        password="секретный-пароль",
    )

    html = await _students_page(admin)

    assert "<script>alert(1)" not in html
    assert "&lt;script&gt;" in html
    assert "<img src=x" not in html
    assert "scrypt$" not in html and "password_hash" not in html


async def test_students_page_refresh_button_and_time(admin) -> None:
    html = await _students_page(admin)

    assert "Данные на" in html
    assert "refresh=1" in html


async def test_students_page_search_keeps_query_in_pagination_links(admin, web_app) -> None:
    db = web_app.state.accounts
    for index in range(PAGE_SIZE + 2):
        accounts.create_user(
            db, email=f"zz{index:03d}@example.com", name=f"Зз{index:03d}", password="секретный-пароль"
        )

    html = await _students_page(admin, q="zz")

    assert "q=zz" in html and "page=2" in html


# --- Карточка ученика (Срез 65) ---

from llm_tutor.web import views


def _items(web_app, user, count: int):
    return [item for item in repos.get_items(_db(web_app, user)) if not item.final][:count]


def _answer(web_app, user, item, ts: float, *, result: float = 1.0, source: str = "autotest", hints: int = 0, spent=None):
    repos.add_event(
        _db(web_app, user),
        Event(
            source=source,
            result=result,
            item_id=item.id,
            concept_id=next(iter(item.concept_weights), None),
            hints_used=hints,
            time_spent=spent,
            ts=ts,
        ),
    )


async def _card(admin_client, user_id: int):
    return await admin_client.get(f"/admin/students/{user_id}")


async def test_card_header(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann", survey_done=True)
    accounts.create_session(web_app.state.accounts, user.id)

    response = await _card(admin, user.id)

    assert response.status_code == 200
    html = response.text
    for text in ("Ann", "ann@example.com", "Активных сессий"):
        assert text in html
    assert "админ</span>" not in html
    root = accounts.find_user(web_app.state.accounts, ADMIN["email"])
    assert "админ</span>" in (await _card(admin, root.id)).text
    await client.aclose()


async def test_card_shows_active_session_count(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann")  # регистрация открыла одну сессию
    accounts.create_session(web_app.state.accounts, user.id)
    accounts.create_session(web_app.state.accounts, user.id, now=1.0)  # давно истекла

    html = (await _card(admin, user.id)).text

    assert 'data-sessions="2"' in html
    await client.aclose()


async def test_card_unknown_id_is_404(admin) -> None:
    assert (await _card(admin, 9999)).status_code == 404


async def test_card_non_numeric_id_does_not_crash(admin) -> None:
    response = await admin.get("/admin/students/abc")

    assert response.status_code in (404, 422)
    assert "Traceback" not in response.text


async def test_card_hidden_from_plain_student(student, web_app) -> None:
    user = accounts.find_user(web_app.state.accounts, ANN["email"])
    unknown = await student.get("/no-such-page")

    for path in (f"/admin/students/{user.id}", "/admin/students/9999", "/admin/students/abc"):
        response = await student.get(path)
        assert response.status_code == 404
        assert response.json() == unknown.json()


async def test_card_course_progress_topics_and_survey(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann", survey_done=True)
    _close_lesson(web_app, user, "pandas_structures")
    learning = views.learning_view(_db(web_app, user), settings=web_app.state.settings)

    html = (await _card(admin, user.id)).text

    assert "Модуль 1" in html
    first = next(topic for topic in learning["topics"] if topic["id"] == "pandas_structures")
    assert first["name"] in html
    assert f"{first['mastery']}%" in html
    assert first["label"] in html  # статус — как в профиле ученика
    assert "уверенно" in html or "мало данных" in html
    # Результаты анкеты — то же, что видит сам ученик в профиле.
    assert learning["self_assessment"]
    assert learning["self_assessment"][0]["title"] in html
    assert f"Решено заданий: {learning['solved']}" in html
    await client.aclose()


async def test_card_marks_weak_lessons(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann", survey_done=True)
    conn = _db(web_app, user)
    session_id = repos.ensure_open_session(conn)
    state = repos.get_session_state(conn, session_id)
    other = next(step.concept_id for step in state.route.steps if step.concept_id != state.current_node_id)
    steps = [
        step.model_copy(update={"status": "closed", "weak": True}) if step.concept_id == other else step
        for step in state.route.steps
    ]
    repos.update_session_state(
        conn, session_id, state.model_copy(update={"route": state.route.model_copy(update={"steps": steps})})
    )

    html = (await _card(admin, user.id)).text

    assert views.WEAK_LABEL in html
    await client.aclose()


async def test_card_recent_answers_are_the_latest_fifty(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann")
    items = _items(web_app, user, 3)
    for index in range(55):
        # Шаг в минуту: время в таблице выводится с точностью до минуты.
        _answer(web_app, user, items[index % 3], 100_000.0 + 60 * index, result=1.0 if index % 2 else 0.0)

    html = (await _card(admin, user.id)).text

    assert html.count('class="answer-row"') == 50
    # Новые первыми: последний ответ (54-й) выше первого из показанных (5-го).
    newest, oldest_shown, dropped = (admin_data.format_time(100_000.0 + 60 * n) for n in (54, 5, 4))
    assert html.index(newest) < html.index(oldest_shown)
    assert dropped not in html
    await client.aclose()


async def test_card_answer_row_details(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann")
    item, = _items(web_app, user, 1)
    conn = _db(web_app, user)
    conn.execute("UPDATE items SET prompt = ? WHERE id = ?", ("а" * 200, item.id))
    conn.commit()
    _answer(web_app, user, item, 2000.0, result=1.0, source="autotest", hints=2, spent=12.0)
    _answer(web_app, user, item, 2001.0, result=0.0, source="checked")
    _answer(web_app, user, item, 2002.0, result=0.4, source="rubric")

    html = (await _card(admin, user.id)).text

    assert "а" * 80 in html and "а" * 81 not in html  # условие обрезано до 80 символов
    assert f"#{item.id}" in html
    assert "верно" in html and "неверно" in html and "40%" in html
    for source in ("autotest", "checked", "rubric"):
        assert source in html
    assert "12 с" in html
    await client.aclose()


async def test_card_answer_for_missing_item_does_not_crash(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann")
    item, = _items(web_app, user, 1)
    conn = _db(web_app, user)
    _answer(web_app, user, item, 3000.0)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DELETE FROM items WHERE id = ?", (item.id,))
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")

    response = await _card(admin, user.id)

    assert response.status_code == 200
    assert "answer-row" in response.text
    await client.aclose()


async def test_card_never_shows_chat_messages(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann", survey_done=True)
    conn = _db(web_app, user)
    session_id = repos.ensure_open_session(conn)
    repos.add_message(conn, session_id, "user", "СЕКРЕТНАЯ-РЕПЛИКА-УЧЕНИКА", ts=1.0)
    repos.add_message(conn, session_id, "assistant", "СЕКРЕТНЫЙ-ОТВЕТ-ТЬЮТОРА", ts=2.0)

    html = (await _card(admin, user.id)).text

    assert "СЕКРЕТНАЯ-РЕПЛИКА-УЧЕНИКА" not in html
    assert "СЕКРЕТНЫЙ-ОТВЕТ-ТЬЮТОРА" not in html
    await client.aclose()


async def test_card_busy_student_shows_banner_and_no_course_data(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann", survey_done=True)
    lock = web_app.state.userdbs.lock(user.id, COURSE)

    await lock.acquire()
    try:
        response = await _card(admin, user.id)
    finally:
        lock.release()

    assert response.status_code == 200
    assert "сейчас отвечает" in response.text
    assert "Модуль 1" not in response.text
    assert "ann@example.com" in response.text  # шапка — из аккаунтов, она не зависит от хода
    await client.aclose()


async def test_card_student_not_enrolled(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann", enroll=False)

    html = (await _card(admin, user.id)).text

    assert "не записан" in html.lower()
    await client.aclose()


async def test_card_enrolled_without_db_file(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann")
    path = web_app.state.userdbs.path(user.id, COURSE)
    web_app.state.userdbs.close_all()
    for suffix in ("", "-wal", "-shm"):
        path.with_name(path.name + suffix).unlink(missing_ok=True)

    response = await _card(admin, user.id)

    assert response.status_code == 200
    assert "нет данных" in response.text.lower()
    assert not path.exists()
    await client.aclose()


async def test_card_broken_db(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann")
    web_app.state.userdbs.close_all()
    web_app.state.userdbs.path(user.id, COURSE).write_bytes(b"not a database")

    response = await _card(admin, user.id)

    assert response.status_code == 200
    assert "недоступны" in response.text.lower()
    await client.aclose()


async def test_card_admin_log_empty_and_filled(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann")
    root = accounts.find_user(web_app.state.accounts, ADMIN["email"])

    assert "Действий не было" in (await _card(admin, user.id)).text

    db = web_app.state.accounts
    db.execute(
        "INSERT INTO admin_log (ts, admin_id, action, target_id, details) VALUES (?, ?, ?, ?, ?)",
        (4000.0, root.id, "reset_password", user.id, None),
    )
    db.execute(
        "INSERT INTO admin_log (ts, admin_id, action, target_id, details) VALUES (?, ?, ?, ?, ?)",
        (4001.0, 9999, "close_sessions", user.id, None),  # админ уже удалён
    )
    db.execute(
        "INSERT INTO admin_log (ts, admin_id, action, target_id, details) VALUES (?, ?, ?, ?, ?)",
        (4002.0, root.id, "reset_password", root.id, None),  # над другим аккаунтом
    )
    db.commit()

    html = (await _card(admin, user.id)).text

    assert "Действий не было" not in html
    assert "Сброс пароля" in html and "Закрытие сессий" in html
    assert "Админ" in html  # имя админа из журнала
    assert html.count('class="log-row"') == 2
    await client.aclose()


async def test_card_admin_log_is_limited_to_twenty(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann")
    root = accounts.find_user(web_app.state.accounts, ADMIN["email"])
    for index in range(25):
        web_app.state.accounts.execute(
            "INSERT INTO admin_log (ts, admin_id, action, target_id) VALUES (?, ?, 'close_sessions', ?)",
            (5000.0 + index, root.id, user.id),
        )
    web_app.state.accounts.commit()

    html = (await _card(admin, user.id)).text

    assert html.count('class="log-row"') == 20
    await client.aclose()


async def test_card_escapes_text_and_hides_hashes(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann")
    web_app.state.accounts.execute(
        "UPDATE users SET name = ? WHERE id = ?", ('<img src=x onerror=alert(1)>', user.id)
    )
    web_app.state.accounts.commit()
    item, = _items(web_app, user, 1)
    conn = _db(web_app, user)
    conn.execute("UPDATE items SET prompt = ? WHERE id = ?", ("<script>alert(2)</script> условие", item.id))
    conn.commit()
    _answer(web_app, user, item, 6000.0)

    html = (await _card(admin, user.id)).text

    assert "<img src=x" not in html and "<script>alert(2)" not in html
    assert "&lt;img" in html and "&lt;script&gt;" in html
    assert "scrypt$" not in html and "password_hash" not in html
    await client.aclose()


async def test_students_list_links_to_cards(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann")

    assert f'href="/admin/students/{user.id}"' in await _students_page(admin)
    await client.aclose()
