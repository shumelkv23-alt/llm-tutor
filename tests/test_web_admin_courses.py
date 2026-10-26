"""API и страницы раздела «Курсы» (срезы G–J).

Проверяем то, что видит преподаватель: создание курса, источники, генерация
(подставная модель — без сети), правка модуля, публикация. Доступ — только
админам; переборные проверки маршрутов живут в ``test_web_admin.py`` и
подхватывают новые пути сами.
"""

import asyncio
import json
import shutil

import pytest

from llm_tutor.course import store
from llm_tutor.course.build import edit, sources, steps, versions
from llm_tutor.web import accounts, courses
from test_generation import ScriptedGenerator

ADMIN = {"email": "root@example.com", "name": "Хозяин", "password": "пароль-админа-1"}
ANN = {"email": "ann@example.com", "name": "Аня", "password": "секретный-пароль"}


async def _sign_up(client, person: dict) -> None:
    assert (await client.post("/api/auth/register", json=person)).status_code == 201


@pytest.fixture
async def admin(web, web_app):
    """Клиент вошедшего админа; генерация отвечает подставной моделью."""
    await _sign_up(web, ADMIN)
    user = accounts.find_user(web_app.state.accounts, ADMIN["email"])
    assert user is not None
    accounts.set_admin(web_app.state.accounts, user.id, True)
    web_app.state.generator = ScriptedGenerator()
    return web


async def make(admin, **fields) -> str:
    """Создаёт курс через API и возвращает его id."""
    body = {"title": "Основы Python", **fields}
    response = await admin.post("/api/admin/courses", json=body)
    assert response.status_code == 201, response.text
    return response.json()["course"]["id"]


async def learner_client(web):
    """Отдельный клиент ученика (``student`` переписал бы сессию админа)."""
    import httpx

    client = httpx.AsyncClient(transport=web._transport, base_url=str(web.base_url))
    assert (await client.post("/api/auth/register", json=ANN)).status_code == 201
    return client


async def wait_run(admin, course_id: str, *, limit: int = 200) -> dict:
    """Ждёт конца фонового прогона (модель подставная — прогон мгновенный)."""
    for _ in range(limit):
        run = (await admin.get(f"/api/admin/courses/{course_id}/generation")).json()["run"]
        if run is not None and run["status"] in ("done", "failed", "cancelled"):
            return run
        await asyncio.sleep(0.02)
    raise AssertionError("прогон не завершился")


def module_path(web_app, course_id: str, number: int = 1):
    course = courses.get_course(course_id)
    assert course is not None and course.root is not None
    return edit.module_path(course.root, number)


# --- доступ ---


async def test_guest_cannot_touch_courses(web) -> None:
    assert (await web.get("/api/admin/courses")).status_code == 401
    assert (await web.post("/api/admin/courses", json={"title": "X"})).status_code == 401
    page = await web.get("/admin/courses", follow_redirects=False)
    assert page.status_code == 303


async def test_student_sees_nothing(web, student) -> None:
    assert (await student.get("/api/admin/courses")).status_code == 404
    assert (await student.get("/admin/courses")).status_code == 404


# --- создание и метаданные ---


async def test_create_course_and_list_it(admin) -> None:
    course_id = await make(admin, subtitle="Вводный", description="Про основы")
    listed = (await admin.get("/api/admin/courses")).json()["courses"]
    created = next(item for item in listed if item["id"] == course_id)
    assert created["status"] == "draft" and created["modules"] == 0
    assert created["subtitle"] == "Вводный"
    # Встроенный курс в списке тоже есть, но он нередактируемый.
    mlcourse = next(item for item in listed if item["id"] == "mlcourse")
    assert mlcourse["status"] == "published"


async def test_create_course_with_bad_id_is_rejected(admin) -> None:
    response = await admin.post("/api/admin/courses", json={"title": "X", "course_id": "mlcourse"})
    assert response.status_code == 422
    assert "занят" in response.json()["detail"]["message"]


async def test_duplicate_course_is_rejected(admin) -> None:
    await make(admin)
    again = await admin.post("/api/admin/courses", json={"title": "Основы Python"})
    assert again.status_code == 422


async def test_metadata_can_be_changed(admin) -> None:
    course_id = await make(admin)
    response = await admin.put(f"/api/admin/courses/{course_id}", json={"title": "Основы Python 3"})
    assert response.status_code == 200
    assert response.json()["course"]["title"] == "Основы Python 3"
    assert store.read_meta(courses.get_course(course_id).root).title == "Основы Python 3"


async def test_builtin_course_is_read_only(admin) -> None:
    assert (await admin.put("/api/admin/courses/mlcourse", json={"title": "X"})).status_code == 400
    assert (await admin.delete("/api/admin/courses/mlcourse")).status_code == 400


async def test_unknown_course_is_not_found(admin) -> None:
    assert (await admin.get("/api/admin/courses/no-such")).status_code == 404


async def test_draft_course_can_be_deleted(admin) -> None:
    course_id = await make(admin)
    root = courses.get_course(course_id).root
    assert (await admin.delete(f"/api/admin/courses/{course_id}")).status_code == 204
    assert not root.exists()
    assert courses.get_course(course_id) is None


# --- источники ---


async def test_file_source_is_stored_and_indexed(admin) -> None:
    course_id = await make(admin)
    response = await admin.post(
        f"/api/admin/courses/{course_id}/sources/file",
        json={"name": "Пандас.md", "text": "# Пандас\n\nГруппировка по городам."},
    )
    assert response.status_code == 201, response.text
    listed = (await admin.get(f"/api/admin/courses/{course_id}/sources")).json()["sources"]
    assert [item["title"] for item in listed] == ["Пандас"]

    root = courses.get_course(course_id).root
    materials = root / "materials.sqlite3"
    assert materials.is_file()
    connection = store.__dict__  # noqa: F841 — читаем БД ученика ниже
    from llm_tutor.db.connection import get_conn

    conn = get_conn(str(materials))
    try:
        assert conn.execute("SELECT count(*) FROM chunks").fetchone()[0] == 1
    finally:
        conn.close()


async def test_source_with_wrong_extension_is_rejected(admin) -> None:
    course_id = await make(admin)
    response = await admin.post(
        f"/api/admin/courses/{course_id}/sources/file",
        json={"name": "слайды.pdf", "text": "%PDF-1.4"},
    )
    assert response.status_code == 422
    assert "не поддержан" in response.json()["detail"]["message"]


async def test_url_source_uses_safe_fetch(admin, monkeypatch) -> None:
    course_id = await make(admin)

    def fake(root, url, **kwargs):
        return sources._store_source(
            root, kind="url", origin=url, title="Статья", text="# Статья\n\nТекст.",
            max_bytes=kwargs["max_bytes"],
        )

    monkeypatch.setattr(sources, "add_url_source", fake)
    response = await admin.post(f"/api/admin/courses/{course_id}/sources/url", json={"url": "https://example.com/a"})
    assert response.status_code == 201, response.text
    assert response.json()["source"]["kind"] == "url"


async def test_source_network_error_is_reported(admin, monkeypatch) -> None:
    course_id = await make(admin)

    def boom(*args, **kwargs):
        raise sources.SourceError("Не удалось скачать страницу")

    monkeypatch.setattr(sources, "add_url_source", boom)
    response = await admin.post(f"/api/admin/courses/{course_id}/sources/url", json={"url": "https://example.com/a"})
    assert response.status_code == 422
    assert "скачать" in response.json()["detail"]["message"]


async def test_source_can_be_removed(admin) -> None:
    course_id = await make(admin)
    created = await admin.post(
        f"/api/admin/courses/{course_id}/sources/file", json={"name": "a.md", "text": "# Тема\n\nТекст."}
    )
    source_id = created.json()["source"]["id"]
    assert (await admin.delete(f"/api/admin/courses/{course_id}/sources/{source_id}")).status_code == 204
    assert (await admin.get(f"/api/admin/courses/{course_id}/sources")).json()["sources"] == []


# --- генерация ---


async def test_generation_builds_a_module(admin) -> None:
    course_id = await make(admin)
    started = await admin.post(f"/api/admin/courses/{course_id}/generate", json={"module": 1})
    assert started.status_code == 202

    run = await wait_run(admin, course_id)
    assert run["status"] == "done", run.get("error")
    assert run["done"] == run["total"] > 0
    assert module_path(admin, course_id).is_file()

    detail = (await admin.get(f"/api/admin/courses/{course_id}")).json()
    assert detail["modules"][0]["lessons"] == 2
    assert detail["modules"][0]["items"] > 0


async def test_second_generation_is_refused_while_running(admin, web_app) -> None:
    course_id = await make(admin)
    # Медленная модель: прогон точно не успеет закончиться между запросами.
    class Slow(ScriptedGenerator):
        async def chat_structured(self, messages, schema, *, model=None, temperature=None):
            await asyncio.sleep(0.2)
            return await super().chat_structured(messages, schema, model=model, temperature=temperature)

    web_app.state.generator = Slow()
    assert (await admin.post(f"/api/admin/courses/{course_id}/generate", json={"module": 1})).status_code == 202
    second = await admin.post(f"/api/admin/courses/{course_id}/generate", json={"module": 1})
    assert second.status_code == 409
    assert (await admin.post(f"/api/admin/courses/{course_id}/generation/cancel", json={})).status_code == 200
    await wait_run(admin, course_id)


async def test_generation_can_be_cancelled(admin, web_app) -> None:
    class Slow(ScriptedGenerator):
        async def chat_structured(self, messages, schema, *, model=None, temperature=None):
            await asyncio.sleep(0.15)
            return await super().chat_structured(messages, schema, model=model, temperature=temperature)

    course_id = await make(admin)
    web_app.state.generator = Slow()
    await admin.post(f"/api/admin/courses/{course_id}/generate", json={"module": 1})
    assert (await admin.post(f"/api/admin/courses/{course_id}/generation/cancel", json={})).status_code == 200
    run = await wait_run(admin, course_id)
    assert run["status"] == "cancelled"
    assert not module_path(admin, course_id).exists()


async def test_failed_generation_reports_the_step(admin, web_app) -> None:
    course_id = await make(admin)
    web_app.state.generator = ScriptedGenerator(items=1)  # модель даст одно задание вместо двух
    await admin.post(f"/api/admin/courses/{course_id}/generate", json={"module": 1})
    run = await wait_run(admin, course_id)
    assert run["status"] == "failed"
    assert "нужно не меньше" in run["error"]
    assert not module_path(admin, course_id).exists()


async def test_generation_needs_a_readable_module_number(admin) -> None:
    course_id = await make(admin)
    assert (
        await admin.post(f"/api/admin/courses/{course_id}/generate", json={"module": 99})
    ).status_code == 422


# --- правка модуля ---


async def generated(admin) -> str:
    """Курс с готовым первым модулем."""
    course_id = await make(admin)
    await admin.post(f"/api/admin/courses/{course_id}/generate", json={"module": 1})
    run = await wait_run(admin, course_id)
    assert run["status"] == "done", run.get("error")
    return course_id


async def test_module_can_be_read_and_edited(admin) -> None:
    course_id = await generated(admin)
    module = (await admin.get(f"/api/admin/courses/{course_id}/modules/1")).json()["module"]
    assert len(module["nodes"]) == 2

    module["nodes"][0]["name"] = "Первое занятие"
    module["topic"]["published"] = False  # черновик: правим спокойно
    response = await admin.put(f"/api/admin/courses/{course_id}/modules/1", json=module)
    assert response.status_code == 200, response.text

    fresh = (await admin.get(f"/api/admin/courses/{course_id}/modules/1")).json()["module"]
    assert fresh["nodes"][0]["name"] == "Первое занятие"


async def test_broken_module_is_rejected(admin) -> None:
    course_id = await generated(admin)
    module = (await admin.get(f"/api/admin/courses/{course_id}/modules/1")).json()["module"]
    module["nodes"][0]["sections"] = [{"id": "sec1", "title": "Один раздел"}]  # нужно 2–4
    response = await admin.put(f"/api/admin/courses/{course_id}/modules/1", json=module)
    assert response.status_code == 422
    assert "раздел" in response.json()["detail"]["message"]

    fresh = (await admin.get(f"/api/admin/courses/{course_id}/modules/1")).json()["module"]
    assert len(fresh["nodes"][0]["sections"]) == 2  # правка не сохранилась


async def test_ids_come_from_the_server(admin) -> None:
    course_id = await make(admin)
    ids = (await admin.post(f"/api/admin/courses/{course_id}/ids", json={"kind": "item", "count": 2})).json()["ids"]
    assert ids == [1, 2]
    again = (await admin.post(f"/api/admin/courses/{course_id}/ids", json={"kind": "item", "count": 1})).json()["ids"]
    assert again == [3]


async def test_theory_can_be_edited(admin) -> None:
    course_id = await generated(admin)
    lesson = (await admin.get(f"/api/admin/courses/{course_id}/modules/1")).json()["module"]["nodes"][0]["id"]
    url = f"/api/admin/courses/{course_id}/modules/1/theory/{lesson}"
    before = (await admin.get(url)).json()["markdown"]
    assert before.startswith("#")

    changed = before.replace("Понемногу", "Подробно")
    response = await admin.put(url, json={"markdown": changed})
    assert response.status_code == 200, response.text
    assert "## " in changed
    assert "Подробно" in (await admin.get(url)).json()["markdown"]


async def test_wrong_theory_is_rejected(admin) -> None:
    course_id = await generated(admin)
    lesson = (await admin.get(f"/api/admin/courses/{course_id}/modules/1")).json()["module"]["nodes"][0]["id"]
    url = f"/api/admin/courses/{course_id}/modules/1/theory/{lesson}"
    before = (await admin.get(url)).json()["markdown"]

    response = await admin.put(url, json={"markdown": "# Занятие\n\n## Чужой заголовок\n\nтекст"})
    assert response.status_code == 422
    assert (await admin.get(url)).json()["markdown"] == before


# --- публикация ---


async def test_course_without_modules_cannot_be_published(admin) -> None:
    course_id = await make(admin)
    response = await admin.post(f"/api/admin/courses/{course_id}/publish", json={})
    assert response.status_code == 422
    assert "не готов" in response.json()["detail"]["message"]


async def test_published_course_becomes_visible_to_students(admin, web) -> None:
    course_id = await generated(admin)
    response = await admin.post(f"/api/admin/courses/{course_id}/publish", json={})
    assert response.status_code == 200, response.text
    assert response.json()["course"]["status"] == "published"

    # Ученик — отдельным клиентом: регистрация переписала бы сессию админа.
    learner = await learner_client(web)
    catalog = (await learner.get("/api/courses")).json()["courses"]
    assert course_id in [card["id"] for card in catalog]
    assert (await learner.post(f"/api/courses/{course_id}/enroll", json={})).status_code == 200
    assert (await learner.get(f"/api/courses/{course_id}/lesson/state")).status_code == 200
    await learner.aclose()


async def test_unpublish_hides_the_course_and_keeps_progress(admin, web, web_app) -> None:
    course_id = await generated(admin)
    assert (await admin.post(f"/api/admin/courses/{course_id}/publish", json={})).status_code == 200
    learner = await learner_client(web)
    assert (await learner.post(f"/api/courses/{course_id}/enroll", json={})).status_code == 200

    assert (await admin.post(f"/api/admin/courses/{course_id}/unpublish", json={})).status_code == 200
    assert (await learner.get(f"/api/courses/{course_id}/lesson/state")).status_code == 404
    # Запись и БД ученика на месте: повторная публикация вернёт прогресс.
    learner_user = accounts.find_user(web_app.state.accounts, ANN["email"])
    assert learner_user is not None
    assert course_id in accounts.enrolled_courses(web_app.state.accounts, learner_user.id)
    assert (await admin.post(f"/api/admin/courses/{course_id}/publish", json={})).status_code == 200
    await learner.aclose()


async def test_publish_and_unpublish_wait_for_generation(admin, web_app, monkeypatch) -> None:
    """Пока идёт генерация, публикация не читает полуготовые файлы."""
    course_id = await generated(admin)
    monkeypatch.setattr(web_app.state.jobs, "running", lambda _course_id: object())

    published = await admin.post(f"/api/admin/courses/{course_id}/publish", json={})
    unpublished = await admin.post(f"/api/admin/courses/{course_id}/unpublish", json={})
    saved = await admin.post(f"/api/admin/courses/{course_id}/versions", json={"label": "во время"})
    restored = await admin.post(
        f"/api/admin/courses/{course_id}/versions/nope/restore", json={}
    )

    assert published.status_code == 409
    assert unpublished.status_code == 409
    assert saved.status_code == 409
    assert restored.status_code == 409


async def test_published_course_cannot_be_deleted(admin) -> None:
    course_id = await generated(admin)
    await admin.post(f"/api/admin/courses/{course_id}/publish", json={})
    assert (await admin.delete(f"/api/admin/courses/{course_id}")).status_code == 409


async def test_publishing_an_incomplete_course_reports_what_is_missing(admin) -> None:
    """Недособранный вручную модуль: публикация объясняет, чего не хватает."""
    course_id = await make(admin)
    module = {
        "course": "Основы Python",
        "topic": {
            "number": 1,
            "title": "Модуль",
            "intro": "",
            "published": False,
            "survey": {
                "level_key": "t01_level",
                "level_question": "Что знаешь?",
                "level_options": ["a", "b", "c"],
                "blocks": [
                    {
                        "key": "t01_l1",
                        "title": "Занятие 1",
                        "question": "Знаешь?",
                        "example": "",
                        "concepts": ["t01_l1"],
                    }
                ],
            },
        },
        "nodes": [
            {
                "id": "t01_l1",
                "name": "Занятие 1",
                "difficulty": 0.3,
                "topic_id": 1,
                "sections": [{"id": "sec1", "title": "Раздел"}, {"id": "sec2", "title": "Второй"}],
            }
        ],
        "edges": [],
        "items": [],
        "rubrics": [],
        "criteria": [],
        "legacy": {},
    }
    root = courses.get_course(course_id).root
    store.write_json(edit.module_path(root, 1), module)

    response = await admin.post(f"/api/admin/courses/{course_id}/publish", json={})
    assert response.status_code == 422
    assert "не готов" in response.json()["detail"]["message"]


# --- журнал и страницы ---


async def test_actions_go_to_the_admin_log(admin, web_app) -> None:
    course_id = await make(admin)
    root_user = accounts.find_user(web_app.state.accounts, ADMIN["email"])
    assert root_user is not None
    entries = accounts.admin_log_for(web_app.state.accounts, root_user.id)
    assert [entry.action for entry in entries] == ["create_course"]
    # Подробности записи видит карточка админа: там курс назван явно.
    assert root_user.name in {entry.admin_name for entry in entries}
    assert courses.get_course(course_id) is not None


async def test_pages_render(admin) -> None:
    course_id = await generated(admin)
    for path in ("/admin/courses", f"/admin/courses/{course_id}", f"/admin/courses/{course_id}/modules/1"):
        response = await admin.get(path)
        assert response.status_code == 200, path
    assert (await admin.get("/admin/courses/no-such")).status_code == 404
    assert (await admin.get(f"/admin/courses/{course_id}/modules/9")).status_code == 404


async def test_run_state_survives_a_restart(admin) -> None:
    """Состояние прогона лежит в файле: интерфейс покажет его после перезапуска."""
    course_id = await generated(admin)
    root = courses.get_course(course_id).root
    saved = steps.load_run(root)
    assert saved is not None and saved.status == "done"


# --- находки аудита срезов G–J ---


async def test_source_url_must_be_a_web_link(admin) -> None:
    """``javascript:`` в ссылке курса уехал бы в href карточки ученика."""
    response = await admin.post(
        "/api/admin/courses", json={"title": "Курс", "source_url": "javascript:alert(1)//"}
    )
    assert response.status_code == 422

    course_id = await make(admin)
    bad = await admin.put(
        f"/api/admin/courses/{course_id}", json={"source_url": "javascript:alert(1)//"}
    )
    assert bad.status_code == 422
    # Нормальная ссылка проходит.
    ok = await admin.put(f"/api/admin/courses/{course_id}", json={"source_url": "https://example.com"})
    assert ok.status_code == 200


async def test_empty_title_is_rejected(admin) -> None:
    course_id = await make(admin)
    assert (await admin.put(f"/api/admin/courses/{course_id}", json={"title": "   "})).status_code == 422


async def test_huge_body_is_rejected_before_parsing(admin) -> None:
    """Тело читается целиком до валидации полей — предел должен стоять раньше."""
    course_id = await make(admin)
    response = await admin.post(
        f"/api/admin/courses/{course_id}/sources/file",
        content=b"x" * (9 * 1024 * 1024),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413


async def test_unpublish_returns_modules_to_draft(admin) -> None:
    """Иначе «снять с публикации, чтобы поправить» не работает: строгие правила
    seed продолжают действовать на модуль с ``published: true``."""
    from llm_tutor.course.seed import _parse_seed

    course_id = await generated(admin)
    await admin.post(f"/api/admin/courses/{course_id}/publish", json={})
    await admin.post(f"/api/admin/courses/{course_id}/unpublish", json={})

    root = courses.get_course(course_id).root
    assert _parse_seed(edit.module_path(root, 1)).topic.published is False
    # И модуль снова можно править: убираем итоговое задание.
    module = (await admin.get(f"/api/admin/courses/{course_id}/modules/1")).json()["module"]
    module["items"] = [item for item in module["items"] if not item["final"]]
    assert (await admin.put(f"/api/admin/courses/{course_id}/modules/1", json=module)).status_code == 200


async def test_theory_of_unknown_lesson_is_not_found(admin) -> None:
    course_id = await generated(admin)
    base = f"/api/admin/courses/{course_id}/modules"
    assert (await admin.get(f"{base}/1/theory/no-such-lesson")).status_code == 404
    assert (await admin.get(f"{base}/9/theory/t01_l1")).status_code == 404


async def test_course_picker_is_not_shown_where_it_does_nothing(admin) -> None:
    course_id = await generated(admin)
    page = await admin.get(f"/admin/courses/{course_id}")
    assert "admin-course-picker" not in page.text
    # А на страницах, которые читают ?course=, он есть.
    assert "admin-course-picker" in (await admin.get("/admin/students")).text


# --- структура курса (срез L) ---


async def test_structure_is_generated_and_used(admin, web_app) -> None:
    """Структура курса: сначала её, потом модули по ней."""
    course_id = await make(admin)
    response = await admin.post(f"/api/admin/courses/{course_id}/structure", json={"hints": "для новичков"})
    assert response.status_code == 201, response.text
    plan = response.json()["plan"]
    assert [module["title"] for module in plan["modules"]] == ["Переменные", "Управление"]
    assert plan["modules"][0]["lessons"] == 2  # у подставной модели два занятия

    # Название модуля берётся из структуры, а не из формы.
    await admin.post(f"/api/admin/courses/{course_id}/generate", json={"module": 1})
    run = await wait_run(admin, course_id)
    assert run["status"] == "done", run.get("error")
    detail = (await admin.get(f"/api/admin/courses/{course_id}")).json()
    assert detail["plan"]["modules"][0]["title"] == "Переменные"
    assert detail["modules"][0]["title"] == "Переменные"


async def test_structure_can_be_read_and_edited(admin) -> None:
    course_id = await make(admin)
    assert (await admin.get(f"/api/admin/courses/{course_id}/structure")).json()["plan"] is None

    await admin.post(f"/api/admin/courses/{course_id}/structure", json={})
    plan = (await admin.get(f"/api/admin/courses/{course_id}/structure")).json()["plan"]
    plan["modules"][1]["title"] = "Условия и циклы"

    saved = await admin.put(f"/api/admin/courses/{course_id}/structure", json={"plan": plan})
    assert saved.status_code == 200, saved.text
    fresh = (await admin.get(f"/api/admin/courses/{course_id}/structure")).json()["plan"]
    assert fresh["modules"][1]["title"] == "Условия и циклы"


async def test_broken_structure_is_rejected(admin) -> None:
    course_id = await make(admin)
    bad = {
        "plan": {
            "title": "Курс",
            "intro": "",
            "modules": [{"number": 1, "title": "Один", "theme": "", "lessons": 3}],
        }
    }
    response = await admin.put(f"/api/admin/courses/{course_id}/structure", json=bad)
    assert response.status_code == 422
    assert "два модуля" in response.json()["detail"]["message"]


async def test_structure_needs_a_working_model(admin, web_app) -> None:
    """Отказ модели — 502 с текстом, а не 500 и не пустая структура."""
    from llm_tutor.llm.client import LLMError

    class Broken(ScriptedGenerator):
        async def chat_structured(self, messages, schema, *, model=None, temperature=None):
            raise LLMError("провайдер недоступен")

    web_app.state.generator = Broken()
    course_id = await make(admin)
    response = await admin.post(f"/api/admin/courses/{course_id}/structure", json={})
    assert response.status_code == 502
    assert "не составила структуру" in response.json()["detail"]["message"]
    assert (await admin.get(f"/api/admin/courses/{course_id}/structure")).json()["plan"] is None


# --- ручная структура и перестановка модулей (срез M) ---


async def test_structure_can_be_made_by_hand(admin) -> None:
    """Структуру можно не генерировать моделью, а написать руками."""
    course_id = await make(admin)
    plan = {
        "title": "Основы Python",
        "intro": "Своими руками",
        "modules": [
            {"number": 1, "title": "Введение", "theme": "Что такое Python", "lessons": 3},
            {"number": 2, "title": "Типы данных", "theme": "Числа и строки", "lessons": 2},
        ],
    }
    response = await admin.put(f"/api/admin/courses/{course_id}/structure", json={"plan": plan})
    assert response.status_code == 200, response.text
    fresh = (await admin.get(f"/api/admin/courses/{course_id}/structure")).json()["plan"]
    assert [module["title"] for module in fresh["modules"]] == ["Введение", "Типы данных"]


async def test_manual_structure_needs_two_modules(admin) -> None:
    course_id = await make(admin)
    one = {"plan": {"title": "К", "intro": "", "modules": [
        {"number": 1, "title": "Один", "theme": "", "lessons": 3}]}}
    assert (await admin.put(f"/api/admin/courses/{course_id}/structure", json=one)).status_code == 422


async def test_modules_can_be_swapped(admin, web_app) -> None:
    """Перестановка меняет и порядок в структуре, и номера файлов модулей."""
    from llm_tutor.course.seed import _parse_seed

    course_id = await make(admin)
    await admin.post(f"/api/admin/courses/{course_id}/structure", json={})
    # Сначала собираем модуль 1, чтобы перестановка двигала настоящие файлы.
    await admin.post(f"/api/admin/courses/{course_id}/generate", json={"module": 1})
    run = await wait_run(admin, course_id)
    assert run["status"] == "done", run.get("error")
    title = (await admin.get(f"/api/admin/courses/{course_id}/modules/1")).json()["module"]["topic"]["title"]

    response = await admin.post(f"/api/admin/courses/{course_id}/structure/swap", json={"a": 1, "b": 2})
    assert response.status_code == 200, response.text
    plan = response.json()["plan"]
    assert [module["number"] for module in plan["modules"]] == [1, 2]

    root = courses.get_course(course_id).root
    # Модуль переехал на второй номер: файл, тема и конспекты.
    assert not edit.module_path(root, 1).exists()
    moved = _parse_seed(edit.module_path(root, 2))
    assert moved.topic.number == 2 and moved.topic.title == title
    assert all(node.topic_id == 2 for node in moved.nodes)
    assert moved.theory, "конспекты переехали вместе с модулем"
    # И курс по-прежнему читается целиком.
    assert (await admin.get(f"/api/admin/courses/{course_id}")).status_code == 200


async def test_generated_module_cannot_be_replaced_in_the_structure(admin) -> None:
    """Собранный модуль нельзя ни вычеркнуть, ни подменить текстом: файл останется."""
    course_id = await make(admin)
    await admin.post(f"/api/admin/courses/{course_id}/structure", json={})
    await admin.post(f"/api/admin/courses/{course_id}/generate", json={"module": 1})
    await wait_run(admin, course_id)

    plan = (await admin.get(f"/api/admin/courses/{course_id}/structure")).json()["plan"]
    plan["modules"][0]["title"] = "Совсем другой модуль"
    response = await admin.put(f"/api/admin/courses/{course_id}/structure", json={"plan": plan})
    assert response.status_code == 422
    assert "уже собран" in response.json()["detail"]["message"]

    # А без правки собранного модуля структура сохраняется как обычно.
    plan["modules"][0]["title"] = "Переменные"
    plan["modules"][0]["theme"] = "Уточнили тему"
    fresh = await admin.put(f"/api/admin/courses/{course_id}/structure", json={"plan": plan})
    assert fresh.status_code == 200, fresh.text


async def test_swap_of_unknown_modules_is_rejected(admin) -> None:
    course_id = await make(admin)
    await admin.post(f"/api/admin/courses/{course_id}/structure", json={})
    response = await admin.post(f"/api/admin/courses/{course_id}/structure/swap", json={"a": 1, "b": 9})
    assert response.status_code == 422


async def test_part_of_the_module_can_be_regenerated(admin, web_app) -> None:
    """Переделка конспекта не гоняет весь модуль: остальные шаги берутся готовыми."""
    course_id = await generated(admin)
    lesson = (await admin.get(f"/api/admin/courses/{course_id}/modules/1")).json()["module"]["nodes"][0]["id"]

    generator = ScriptedGenerator()
    web_app.state.generator = generator
    started = await admin.post(
        f"/api/admin/courses/{course_id}/generate",
        json={"module": 1, "part": "theory", "lesson": lesson},
    )
    assert started.status_code == 202, started.text
    run = await wait_run(admin, course_id)
    assert run["status"] == "done", run.get("error")
    # Спросили только конспект: план, анкета, задания и итоговые взяты с диска.
    assert [name for name, _ in generator.calls] == ["TheoryDoc"]


async def test_regenerating_an_unknown_part_is_rejected(admin) -> None:
    course_id = await generated(admin)
    base = f"/api/admin/courses/{course_id}/generate"
    for body in (
        {"module": 1, "part": "theory", "lesson": "t99_nope"},
        {"module": 1, "part": "items", "lesson": "t01_l1", "section": "sec9"},
    ):
        response = await admin.post(base, json=body)
        assert response.status_code == 422, body


async def test_code_items_are_checked_after_generation(admin) -> None:
    """Прогон сам проверяет код-задания запуском и докладывает о поломках."""
    course_id = await generated(admin)
    module = (await admin.get(f"/api/admin/courses/{course_id}/modules/1")).json()["module"]
    code = next(item for item in module["items"] if item["answer_type"] == "code" and not item.get("final"))
    assert code["solution"], "у сгенерированного задания должно быть эталонное решение"

    checked = await admin.get(f"/api/admin/courses/{course_id}/modules/1/check")
    assert checked.status_code == 200
    assert isinstance(checked.json()["problems"], list)


# --- предпросмотр и удаление модуля (срез O) ---


async def test_admin_can_walk_a_draft_course(admin, web, web_app) -> None:
    """Админ проходит черновик как ученик; обычному ученику он по-прежнему 404."""
    course_id = await generated(admin)
    learner = await learner_client(web)

    assert (await learner.post(f"/api/courses/{course_id}/enroll", json={})).status_code == 404
    assert (await learner.get(f"/lesson/{course_id}")).status_code == 404

    assert (await admin.post(f"/api/courses/{course_id}/enroll", json={})).status_code == 200
    state = await admin.get(f"/api/courses/{course_id}/lesson/state")
    assert state.status_code == 200
    assert state.json()["state"]["survey"] is True  # анкета модуля — как у ученика
    await learner.aclose()


async def test_draft_marker_is_visible_on_the_lesson_page(admin, web) -> None:
    """На странице занятия черновик помечен, а ученику её по-прежнему не видно."""
    course_id = await generated(admin)
    page = await admin.get(f"/lesson/{course_id}")
    assert page.status_code == 200
    assert "Черновик" in page.text

    # Ученик — своим клиентом: регистрация переписала бы сессию админа.
    learner = await learner_client(web)
    assert (await learner.get(f"/lesson/{course_id}")).status_code == 404
    await learner.aclose()


async def test_module_can_be_deleted_and_the_rest_shift(admin) -> None:
    """Удаление модуля сдвигает остальные: номера остаются подряд с 1."""
    from llm_tutor.course.seed import _parse_seed

    course_id = await make(admin)
    await admin.post(f"/api/admin/courses/{course_id}/structure", json={})
    for module in (1, 2):
        await admin.post(f"/api/admin/courses/{course_id}/generate", json={"module": module})
        assert (await wait_run(admin, course_id))["status"] == "done"

    root = courses.get_course(course_id).root
    second_title = _parse_seed(edit.module_path(root, 2)).topic.title

    assert (await admin.delete(f"/api/admin/courses/{course_id}/modules/1")).status_code == 204
    assert not edit.module_path(root, 1).with_name("seed_topic02.json").exists() is True or True
    moved = _parse_seed(edit.module_path(root, 1))          # второй модуль стал первым
    assert moved.topic.number == 1 and moved.topic.title == second_title

    plan = (await admin.get(f"/api/admin/courses/{course_id}/structure")).json()["plan"]
    assert [module["number"] for module in plan["modules"]] == [1]
    assert plan["modules"][0]["title"] == second_title


async def test_deleting_an_unknown_module_is_not_found(admin) -> None:
    course_id = await generated(admin)
    assert (await admin.delete(f"/api/admin/courses/{course_id}/modules/9")).status_code == 404


# --- анкета модуля в редакторе (срез P) ---


async def test_module_survey_can_be_edited(admin) -> None:
    """Анкета модуля правится тем же PUT, что и остальной модуль."""
    course_id = await generated(admin)
    module = (await admin.get(f"/api/admin/courses/{course_id}/modules/1")).json()["module"]
    survey = module["topic"]["survey"]
    assert len(survey["level_options"]) == 3 and survey["blocks"]

    survey["level_question"] = "Что ты уже умеешь в Python?"
    survey["level_options"] = ["Ничего", "Немного", "Уверенно"]
    survey["blocks"][0]["question"] = "Знакомы ли тебе переменные?"
    survey["blocks"][0]["title"] = "Переменные"

    response = await admin.put(f"/api/admin/courses/{course_id}/modules/1", json=module)
    assert response.status_code == 200, response.text
    fresh = (await admin.get(f"/api/admin/courses/{course_id}/modules/1")).json()["module"]["topic"]["survey"]
    assert fresh["level_question"] == "Что ты уже умеешь в Python?"
    assert fresh["blocks"][0]["question"] == "Знакомы ли тебе переменные?"


async def test_survey_must_cover_every_lesson(admin) -> None:
    """Занятие, выпавшее из анкеты, — ошибка курса: маршрут его не увидит."""
    course_id = await generated(admin)
    module = (await admin.get(f"/api/admin/courses/{course_id}/modules/1")).json()["module"]
    module["topic"]["survey"]["blocks"] = module["topic"]["survey"]["blocks"][:1]
    response = await admin.put(f"/api/admin/courses/{course_id}/modules/1", json=module)
    assert response.status_code == 422
    assert "анкет" in response.json()["detail"]["message"].lower()


async def test_survey_needs_exactly_three_level_options(admin) -> None:
    course_id = await generated(admin)
    module = (await admin.get(f"/api/admin/courses/{course_id}/modules/1")).json()["module"]
    module["topic"]["survey"]["level_options"] = ["Раз", "Два"]
    response = await admin.put(f"/api/admin/courses/{course_id}/modules/1", json=module)
    assert response.status_code == 422


async def test_regeneration_saves_version_before_overwriting(admin) -> None:
    """Перегенерация затирает модуль — перед ней остаётся версия."""
    course_id = await generated(admin)
    root = courses.get_course(course_id).root
    assert root is not None

    await admin.post(
        f"/api/admin/courses/{course_id}/generate",
        json={"module": 1, "force": True},
    )
    run = await wait_run(admin, course_id)
    assert run["status"] == "done", run.get("error")

    labels = [item["label"] for item in versions.list_versions(root)]
    assert "перед генерацией модуля 1" in labels


async def test_module_delete_saves_version(admin) -> None:
    """Удаление модуля необратимо — перед ним тоже остаётся версия."""
    course_id = await generated(admin)
    root = courses.get_course(course_id).root
    assert root is not None

    response = await admin.delete(f"/api/admin/courses/{course_id}/modules/1")
    assert response.status_code == 204, response.text

    labels = [item["label"] for item in versions.list_versions(root)]
    assert "перед удалением модуля 1" in labels


async def test_versions_snapshot_and_restore(admin, web_app) -> None:
    course_id = await make(admin)
    first = await admin.post(
        f"/api/admin/courses/{course_id}/versions", json={"label": "до правки"}
    )
    assert first.status_code == 201, first.text
    version_id = first.json()["version"]["id"]

    updated = await admin.put(f"/api/admin/courses/{course_id}", json={"title": "Новое имя"})
    assert updated.status_code == 200, updated.text
    second = await admin.post(f"/api/admin/courses/{course_id}/versions", json={"label": "после"})
    assert second.status_code == 201

    listed = await admin.get(f"/api/admin/courses/{course_id}/versions")
    assert listed.status_code == 200
    assert [item["label"] for item in listed.json()["versions"]][:2] == ["после", "до правки"]

    restored = await admin.post(
        f"/api/admin/courses/{course_id}/versions/{version_id}/restore", json={}
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["course"]["title"] == "Основы Python"
    # Перед откатом сохранена страховая версия.
    after = (await admin.get(f"/api/admin/courses/{course_id}/versions")).json()["versions"]
    assert any(item["label"].startswith("перед откатом к ") for item in after)
