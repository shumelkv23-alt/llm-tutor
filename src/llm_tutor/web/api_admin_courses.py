"""API админки: создание курса, источники, генерация, правка, публикация.

Доступ — только админам, и защита стоит на роутере (``admin_user``): новый
маршрут не может её забыть. Всё живёт под ``/api/admin``, поэтому не-админу
отвечает то же 404, что и несуществующая страница (``security.admin_cloak``).

Тяжёлое (сеть, диск) уходит в ``asyncio.to_thread``: в цикле событий идут ходы
учеников, и блокировать его нельзя. Генерация вообще не выполняется в запросе —
она запускается фоновой задачей (``web/jobs.py``), а интерфейс опрашивает
прогресс.
"""

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from llm_tutor.config import Settings
from llm_tutor.course import store
from llm_tutor.course.build import check, edit, sources, steps
from llm_tutor.course.build.schemas import CoursePlan
from llm_tutor.course.seed import MAX_LESSONS, SeedError
from llm_tutor.web import accounts, courses
from llm_tutor.web.accounts import User
from llm_tutor.web.security import accounts_db, admin_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", dependencies=[Depends(admin_user)])

NOT_FOUND = "Такого курса нет."
BUILTIN = "Встроенный курс не редактируется."
BUSY = "Генерация уже идёт — дождитесь конца или отмените её."
READ_ONLY_PUBLISHED = "Опубликованный курс нельзя удалить — сначала снимите с публикации."

# Журнал админки: имя действия → что это было (см. admin.ACTION_LABELS).
LOG_ACTIONS = {
    "create_course": "создан курс",
    "copy_course": "создана копия курса",
    "update_course": "правка курса",
    "delete_course": "удалён курс",
    "add_source": "добавлен источник",
    "remove_source": "убран источник",
    "generate_course": "запущена генерация",
    "cancel_generation": "генерация отменена",
    "publish_course": "курс опубликован",
    "unpublish_course": "курс снят с публикации",
}


class CreateBody(BaseModel):
    """Новый курс: название обязательно, остальное можно дополнить потом."""

    title: str = Field(max_length=200)
    subtitle: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=2000)
    source_url: str = Field(default="", max_length=500)
    course_id: str | None = Field(default=None, max_length=40)


class UpdateBody(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    subtitle: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    source_url: str | None = Field(default=None, max_length=500)


class FileBody(BaseModel):
    name: str = Field(max_length=200)
    text: str = Field(max_length=2_000_000)


class UrlBody(BaseModel):
    url: str = Field(max_length=1000)
    title: str = Field(default="", max_length=200)


class GenerateBody(BaseModel):
    module: int = Field(ge=1, le=MAX_LESSONS)
    title: str = Field(default="", max_length=200)
    hints: str = Field(default="", max_length=2000)
    force: bool = False
    # Частичная переделка: «theory» — конспект занятия, «items» — задания
    # раздела, «final» — итоговое занятие, «plan»/«survey» — план и анкета.
    part: Literal["plan", "survey", "theory", "items", "final"] | None = None
    lesson: str = Field(default="", max_length=60)
    section: str = Field(default="", max_length=20)


class GenerateMissingBody(BaseModel):
    """Пакетная генерация: пожелания общие для всех недостающих модулей."""

    hints: str = Field(default="", max_length=2000)


class ModuleCreateBody(BaseModel):
    """Пустой модуль руками: место в структуре и сколько занятий завести."""

    module: int = Field(ge=1, le=MAX_LESSONS)
    title: str = Field(default="", max_length=200)
    lessons: int = Field(default=4, ge=2, le=MAX_LESSONS)
    intro: str = Field(default="", max_length=2000)


class StructureRequest(BaseModel):
    """Запрос структуры курса: только пожелания преподавателя."""

    hints: str = Field(default="", max_length=2000)


class SwapBody(BaseModel):
    """Обмен двух модулей местами (соседних или любых двух)."""

    a: int = Field(ge=1, le=MAX_LESSONS)
    b: int = Field(ge=1, le=MAX_LESSONS)


class StructureBody(BaseModel):
    """Структура курса, как её прислал редактор (правится целиком)."""

    plan: CoursePlan


class TheoryBody(BaseModel):
    markdown: str = Field(max_length=200_000)


class IdsBody(BaseModel):
    kind: Literal["item", "rubric", "criterion", "lesson"]
    count: int = Field(ge=1, le=100)


def _fail(status: int, message: str, field: str | None = None) -> HTTPException:
    detail = {"message": message} if field is None else {"field": field, "message": message}
    return HTTPException(status_code=status, detail=detail)


def _course(course_id: str) -> courses.Course:
    """Созданный курс (встроенный и черновик другого автора — не наша забота)."""
    course = courses.get_course(course_id)
    if course is None:
        raise _fail(404, NOT_FOUND)
    if course.root is None:
        raise _fail(400, BUILTIN)
    return course


def _root(course: courses.Course) -> Path:
    assert course.root is not None  # проверено в _course
    return course.root


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _log(request: Request, admin: User, action: str, course_id: str) -> None:
    """Действие над курсом — в журнал админки (секретов там нет)."""
    accounts.log_admin(
        accounts_db(request), admin.id, action, admin.id, details=f"курс {course_id}"
    )


def _refresh(request: Request, course_id: str) -> None:
    """После правки: перечитать разбор курса и закрыть открытые БД учеников.

    Без второго шага правка не доедет до учеников: их соединения кэшированы и
    применяют seed только при первом открытии.
    """
    courses.invalidate_course(course_id)
    request.app.state.userdbs.invalidate_course(course_id)
    request.app.state.admin_cache.clear()


def _meta(course: courses.Course, *, modules: int = 0, run: steps.RunState | None = None) -> dict:
    meta = courses.meta_for(course)
    return {
        "id": course.id,
        "title": course.title,
        "subtitle": course.subtitle,
        "description": course.description,
        "source_url": course.source_url,
        "status": course.status,
        "origin": meta.origin if meta is not None else "manual",
        "modules": modules,
        "generating": run is not None,
        "created_at": meta.created_at if meta is not None else 0.0,
        "updated_at": meta.updated_at if meta is not None else 0.0,
    }


def module_view(root: Path, number: int) -> dict:
    """Модуль для списка: без полного содержимого, только счётчики."""
    from llm_tutor.course.seed import _parse_seed

    seed = _parse_seed(edit.module_path(root, number))
    return {
        "number": number,
        "title": seed.topic.title,
        "published": seed.topic.published,
        "lessons": len(seed.nodes),
        "sections": sum(len(node.sections) for node in seed.nodes),
        "items": len(seed.items),
        "theory": sum(1 for node in seed.nodes if node.id in seed.theory),
    }


def warnings(course_id: str) -> list[str]:
    """Чего не хватает курсу до публикации — теми же функциями, что и seed CLI."""
    from llm_tutor.course.seed import (
        items_without_rubric,
        lessons_without_final,
        nodes_without_theory,
        thin_sections,
    )

    course = courses.get_course(course_id)
    if course is None:
        return [NOT_FOUND]
    data, _ = courses.try_course_data(course)
    if data is None:
        # Про нечитаемый курс уже сказано отдельной строкой (``broken``):
        # повторять ту же фразу в списке предупреждений незачем.
        return []
    parts: list[str] = []
    for title, missing in (
        ("Занятия без конспекта", nodes_without_theory(data)),
        ("Задания без рубрики", items_without_rubric(data)),
        ("Занятия без итогового задания", lessons_without_final(data)),
        ("Разделы с одним заданием", thin_sections(data)),
    ):
        if missing:
            parts.append(f"{title}: {', '.join(str(item) for item in missing)}")
    return parts


# --- курс ---


@router.get("/courses")
async def list_courses(request: Request) -> dict:
    """Все курсы, включая черновики: раздел админки видит всё."""
    out = []
    for course in courses.manageable_courses():
        modules = len(courses.course_data(course).topics) if course.root is None else 0
        if course.root is not None:
            try:
                modules = len(edit.module_numbers(course.root))
            except (store.StoreError, SeedError):
                modules = 0
        out.append(_meta(course, modules=modules))
    return {"courses": out}


@router.post("/courses", status_code=201)
async def create_course(
    body: CreateBody, request: Request, admin: User = Depends(admin_user)
) -> dict:
    try:
        meta = courses.create_course(
            title=body.title,
            course_id=body.course_id,
            subtitle=body.subtitle,
            description=body.description,
            source_url=body.source_url,
        )
    except store.StoreError as error:
        raise _fail(422, str(error), "title") from error
    _log(request, admin, "create_course", meta.id)
    course = courses.get_course(meta.id)
    assert course is not None
    return {"course": _meta(course)}


@router.post("/courses/{course_id}/copy", status_code=201)
async def copy_course(
    course_id: str, request: Request, admin: User = Depends(admin_user)
) -> dict:
    """Копия курса: встроенный курс правится только так.

    Копия — черновик: ученикам она не видна, пока её не опубликуют, и оригинал
    она не трогает.
    """
    if courses.get_course(course_id) is None:
        raise _fail(404, NOT_FOUND)
    try:
        meta = await asyncio.to_thread(courses.copy_course, course_id)
    except store.StoreError as error:
        raise _fail(422, str(error)) from error
    _log(request, admin, "copy_course", meta.id)
    _refresh(request, meta.id)
    course = courses.get_course(meta.id)
    assert course is not None and course.root is not None
    return {"course": _meta(course, modules=len(edit.module_numbers(course.root)))}


@router.get("/courses/{course_id}")
async def course_detail(course_id: str, request: Request) -> dict:
    course = _course(course_id)
    root = _root(course)
    numbers = edit.module_numbers(root)
    modules = []
    for number in numbers:
        try:
            modules.append(module_view(root, number))
        except (store.StoreError, SeedError) as error:
            modules.append({"number": number, "error": str(error)})
    plan = steps.load_course_plan(root)
    return {
        "course": _meta(course, modules=len(numbers)),
        "modules": modules,
        "sources": sources_view(root),
        "warnings": warnings(course_id),
        "run": _run(root, request, course_id),
        "plan": None if plan is None else plan.model_dump(),
    }


@router.put("/courses/{course_id}")
async def update_course(
    course_id: str, body: UpdateBody, request: Request, admin: User = Depends(admin_user)
) -> dict:
    course = _course(course_id)
    fields = {key: value for key, value in body.model_dump().items() if value is not None}
    try:
        courses.update_meta(course_id, **fields)
    except store.StoreError as error:
        raise _fail(422, str(error)) from error
    _log(request, admin, "update_course", course_id)
    _refresh(request, course_id)
    updated = courses.get_course(course_id)
    assert updated is not None
    return {"course": _meta(updated, modules=len(edit.module_numbers(_root(updated))))}


@router.delete("/courses/{course_id}", status_code=204)
async def delete_course(course_id: str, request: Request, admin: User = Depends(admin_user)) -> None:
    course = _course(course_id)
    if course.status == "published":
        raise _fail(409, READ_ONLY_PUBLISHED)
    if request.app.state.jobs.running(course_id) is not None:
        raise _fail(409, BUSY)
    # Сначала закрываем БД учеников: на Windows открытый materials.sqlite3
    # не даёт удалить каталог. Ошибку не глотаем — иначе курс останется, а
    # админка соврёт, что он удалён.
    request.app.state.userdbs.invalidate_course(course_id)
    try:
        await asyncio.to_thread(shutil.rmtree, _root(course))
    except OSError as error:
        raise _fail(500, f"Не удалось удалить каталог курса: {error}") from error
    _log(request, admin, "delete_course", course_id)
    courses.invalidate_course(course_id)
    request.app.state.admin_cache.clear()


# --- источники ---


def sources_view(root: Path) -> list[dict]:
    try:
        records = sources.read_manifest(root)
    except sources.SourceError:
        return []
    return [
        {
            "id": record.id,
            "kind": record.kind,
            "title": record.title,
            "origin": record.origin,
            "bytes": record.bytes,
            "fetched_at": record.fetched_at,
        }
        for record in records
    ]


def _source_limits(settings: Settings) -> dict:
    return {"max_bytes": settings.source_max_bytes, "max_count": settings.sources_max_count}


async def _store_source(request: Request, course_id: str, adder) -> dict:
    """Общий путь добавления источника: ошибки — 422 с понятным текстом."""
    course = _course(course_id)
    root = _root(course)
    try:
        record = await asyncio.to_thread(adder, root)
    except sources.SourceError as error:
        raise _fail(422, str(error)) from error
    await asyncio.to_thread(sources.ingest_sources, root, root / "materials.sqlite3")
    _refresh(request, course_id)
    return {"id": record.id, "kind": record.kind, "title": record.title, "bytes": record.bytes}


@router.get("/courses/{course_id}/sources")
async def list_sources(course_id: str) -> dict:
    return {"sources": sources_view(_root(_course(course_id)))}


@router.post("/courses/{course_id}/sources/file", status_code=201)
async def add_file_source(
    course_id: str, body: FileBody, request: Request, admin: User = Depends(admin_user)
) -> dict:
    """Файл приходит текстом: браузер читает его сам (multipart в CSP-проекте нет)."""
    settings = _settings(request)
    source = await _store_source(
        request,
        course_id,
        lambda root: sources.add_file_source(root, body.name, body.text, **_source_limits(settings)),
    )
    _log(request, admin, "add_source", course_id)
    return {"source": source}


@router.post("/courses/{course_id}/sources/url", status_code=201)
async def add_url_source(
    course_id: str, body: UrlBody, request: Request, admin: User = Depends(admin_user)
) -> dict:
    settings = _settings(request)
    source = await _store_source(
        request,
        course_id,
        lambda root: sources.add_url_source(
            root,
            body.url,
            title=body.title,
            timeout=settings.fetch_timeout,
            fetch_max_bytes=settings.fetch_max_bytes,
            **_source_limits(settings),
        ),
    )
    _log(request, admin, "add_source", course_id)
    return {"source": source}


@router.post("/courses/{course_id}/sources/github", status_code=201)
async def add_github_source(
    course_id: str, body: UrlBody, request: Request, admin: User = Depends(admin_user)
) -> dict:
    settings = _settings(request)
    course = _course(course_id)
    root = _root(course)
    try:
        records = await asyncio.to_thread(
            sources.add_github_source,
            root,
            body.url,
            timeout=settings.fetch_timeout,
            max_files=settings.github_max_files,
            **_source_limits(settings),
        )
    except sources.SourceError as error:
        raise _fail(422, str(error)) from error
    await asyncio.to_thread(sources.ingest_sources, root, root / "materials.sqlite3")
    _refresh(request, course_id)
    _log(request, admin, "add_source", course_id)
    return {"sources": [record.title for record in records], "count": len(records)}


@router.delete("/courses/{course_id}/sources/{source_id}", status_code=204)
async def remove_source(
    course_id: str, source_id: int, request: Request, admin: User = Depends(admin_user)
) -> None:
    course = _course(course_id)
    root = _root(course)
    try:
        await asyncio.to_thread(sources.remove_source, root, source_id)
    except sources.SourceError as error:
        raise _fail(404, str(error)) from error
    await asyncio.to_thread(sources.ingest_sources, root, root / "materials.sqlite3")
    _refresh(request, course_id)
    _log(request, admin, "remove_source", course_id)


# --- структура курса ---


def _module_count_problems(root: Path, plan: CoursePlan) -> list[str]:
    """Число занятий у собранного модуля берётся из файла, а не из структуры.

    Иначе структура врала бы, а перегенерация с ``expected`` из плана просила
    бы у модели не то число занятий.
    """
    problems: list[str] = []
    for outline in plan.modules:
        if not edit.module_path(root, outline.number).is_file():
            continue
        try:
            saved = edit.read_module(root, outline.number)
        except store.StoreError:
            continue  # о нечитаемом модуле скажет structure_problems
        actual = len(saved.get("nodes", []))
        if outline.lessons != actual:
            problems.append(
                f"под номером {outline.number} собрано занятий: {actual}, "
                f"а в структуре {outline.lessons}"
            )
    return problems


@router.get("/courses/{course_id}/structure")
async def read_structure(course_id: str) -> dict:
    """Структура курса: из каких модулей он состоит и что в них."""
    root = _root(_course(course_id))
    plan = await asyncio.to_thread(steps.load_course_plan, root)
    return {"plan": None if plan is None else plan.model_dump()}


@router.post("/courses/{course_id}/structure", status_code=201)
async def make_structure(
    course_id: str, body: StructureRequest, request: Request, admin: User = Depends(admin_user)
) -> dict:
    """Составляет структуру курса моделью — до генерации модулей.

    Один вызов: ответ короткий, поэтому ждём его прямо в запросе, без фоновой
    задачи и опроса.
    """
    course = _course(course_id)
    root = _root(course)
    settings = _settings(request)
    if request.app.state.jobs.running(course_id) is not None:
        raise _fail(409, BUSY)
    if await asyncio.to_thread(edit.module_numbers, root):
        # Модель переписала бы названия модулей, а собранные файлы остались бы
        # прежними: структура разошлась бы с курсом.
        raise _fail(
            409,
            "Модули уже собраны: структуру правьте строками, а не составляйте заново.",
        )
    try:
        plan = await steps.generate_course_plan(
            root,
            client=request.app.state.generator,
            model=settings.generator_model,
            course_title=course.title,
            course_description=course.description,
            hints=body.hints,
        )
    except steps.StepError as error:
        raise _fail(422, str(error)) from error
    except Exception as error:  # noqa: BLE001 — сеть и отказы провайдера
        logger.exception("Структура курса %s не составилась", course_id)
        raise _fail(502, f"Модель не составила структуру: {error}") from error
    _log(request, admin, "update_course", course_id)
    return {"plan": plan.model_dump()}


@router.put("/courses/{course_id}/structure")
async def write_structure(
    course_id: str, body: StructureBody, request: Request, admin: User = Depends(admin_user)
) -> dict:
    """Правка структуры: преподаватель решает, из каких модулей состоит курс."""
    course = _course(course_id)
    root = _root(course)
    try:
        steps.check_course_plan(body.plan)
    except steps.StepError as error:
        raise _fail(422, str(error), "modules") from error
    try:
        problems = await asyncio.to_thread(edit.structure_problems, root, body.plan)
    except (store.StoreError, SeedError) as error:
        raise _fail(422, f"Структура не проверяется: {error}", "modules") from error
    problems += await asyncio.to_thread(_module_count_problems, root, body.plan)
    if problems:
        # Собранный модуль (в нём правка преподавателя и прогресс учеников)
        # нельзя ни вычеркнуть, ни подменить: для перестановки есть отдельная
        # кнопка, которая двигает и файлы.
        raise _fail(422, "Структура не сходится с модулями: " + "; ".join(problems), "modules")
    ordered = body.plan.model_copy(
        update={"modules": sorted(body.plan.modules, key=lambda module: module.number)}
    )
    await asyncio.to_thread(steps.save_course_plan, root, ordered)
    body = body.model_copy(update={"plan": ordered})
    _log(request, admin, "update_course", course_id)
    return {"plan": body.plan.model_dump()}


@router.post("/courses/{course_id}/structure/swap")
async def swap_modules(
    course_id: str, body: SwapBody, request: Request, admin: User = Depends(admin_user)
) -> dict:
    """Меняет два модуля местами вместе с их файлами и конспектами."""
    course = _course(course_id)
    try:
        plan = await asyncio.to_thread(edit.swap_modules, _root(course), body.a, body.b)
    except store.StoreError as error:
        raise _fail(422, str(error)) from error
    _log(request, admin, "update_course", course_id)
    _refresh(request, course_id)
    return {"plan": plan.model_dump()}


# --- генерация ---


def _run(root: Path, request: Request, course_id: str) -> dict | None:
    """Прогресс: живое состояние задачи, а если её нет — последнее с диска."""
    job = request.app.state.jobs.running(course_id)
    if job is not None and job.state is not None:
        return job.state.view()
    state = steps.load_run(root)
    return None if state is None else state.view()


def _starting_run(module: int) -> dict:
    """Ответ на запуск: до первого шага у задачи ещё нет состояния."""
    return {
        "module": module,
        "status": "running",
        "steps": {},
        "current": "",
        "done": 0,
        "total": 0,
        "error": None,
    }


def _after_generation(request: Request, course_id: str, job) -> None:
    """Готовый модуль — новые файлы курса: перечитываем разбор и открытые БД."""
    def done(finished: asyncio.Task) -> None:
        _refresh(request, course_id)
        if finished.cancelled() or finished.exception() is not None:
            logger.warning("Генерация курса %s завершилась неудачно", course_id)

    assert job.task is not None
    job.task.add_done_callback(done)


@router.post("/courses/{course_id}/generate", status_code=202)
async def generate(
    course_id: str, body: GenerateBody, request: Request, admin: User = Depends(admin_user)
) -> dict:
    course = _course(course_id)
    root = _root(course)
    jobs = request.app.state.jobs
    if jobs.running(course_id) is not None:
        raise _fail(409, BUSY)
    settings = _settings(request)
    generator = request.app.state.generator
    plan = steps.load_course_plan(root)
    outline = next((item for item in (plan.modules if plan else []) if item.number == body.module), None)
    try:
        # Переделка куска не гоняет весь модуль: остальные шаги берутся готовыми.
        regenerate = (
            steps.regenerate_steps(
                root, body.module, part=body.part, lesson=body.lesson, section=body.section
            )
            if body.part
            else []
        )
    except steps.StepError as error:
        raise _fail(422, str(error)) from error
    # Название модуля задаёт структура курса; поле в форме — только перебивка.
    module_title = body.title or (
        outline.title if outline is not None else f"{course.title}, модуль {body.module}"
    )

    async def runner(cancelled, on_update) -> steps.RunState:
        return await steps.generate_module(
            root,
            module=body.module,
            client=generator,
            model=settings.generator_model,
            module_title=module_title,
            hints=body.hints,
            plan=plan,
            regenerate=regenerate,
            force=body.force,
            cancelled=cancelled,
            on_update=on_update,
            concurrency=settings.generation_concurrency,
        )

    job = jobs.start(course_id, body.module, runner)
    _log(request, admin, "generate_course", course_id)
    _after_generation(request, course_id, job)
    return {"run": _starting_run(body.module)}


@router.post("/courses/{course_id}/generate-missing", status_code=202)
async def generate_missing(
    course_id: str, body: GenerateMissingBody, request: Request,
    admin: User = Depends(admin_user),
) -> dict:
    """Собирает подряд все модули структуры, файлов которых ещё нет.

    Перезапуск после сбоя продолжает с первого недостающего: готовые модули
    пропускаются, а упавший модуль сам продолжается с сохранённых артефактов.
    """
    course = _course(course_id)
    root = _root(course)
    jobs = request.app.state.jobs
    if jobs.running(course_id) is not None:
        raise _fail(409, BUSY)
    plan = await asyncio.to_thread(steps.load_course_plan, root)
    if plan is None:
        raise _fail(422, "Сначала составьте структуру курса: генерация берёт модули из неё.")
    pending = [
        outline for outline in plan.modules
        if not edit.module_path(root, outline.number).is_file()
    ]
    if not pending:
        raise _fail(422, "Все модули структуры уже собраны.")
    try:
        steps.check_course_plan(plan)
    except steps.StepError as error:
        raise _fail(422, str(error)) from error

    settings = _settings(request)
    generator = request.app.state.generator

    async def runner(cancelled, on_update) -> steps.RunState:
        last: steps.RunState | None = None
        for index, outline in enumerate(pending):
            if index and cancelled():
                # Остановились между модулями: показываем отмену, а не «готово»
                # от предыдущего — иначе интерфейс решит, что пакет закончен.
                state = steps.RunState(module=outline.number, status="cancelled")
                steps.save_run(root, state)
                on_update(state)
                return state
            last = await steps.generate_module(
                root,
                module=outline.number,
                client=generator,
                model=settings.generator_model,
                module_title=outline.title,
                hints=body.hints,
                plan=plan,
                force=False,
                cancelled=cancelled,
                on_update=on_update,
                concurrency=settings.generation_concurrency,
            )
            if last.status != "done":
                return last
        assert last is not None  # pending не пуст — проверено выше
        return last

    job = jobs.start(course_id, pending[0].number, runner)
    _log(request, admin, "generate_course", course_id)
    _after_generation(request, course_id, job)
    return {
        "run": _starting_run(pending[0].number),
        "modules": [outline.number for outline in pending],
    }


@router.get("/courses/{course_id}/generation")
async def generation(course_id: str, request: Request) -> dict:
    course = _course(course_id)
    run = _run(_root(course), request, course_id)
    return {"run": run, "generating": request.app.state.jobs.running(course_id) is not None}


@router.post("/courses/{course_id}/generation/cancel")
async def cancel_generation(course_id: str, request: Request, admin: User = Depends(admin_user)) -> dict:
    course = _course(course_id)
    if not request.app.state.jobs.cancel(course_id):
        raise _fail(409, "Генерация сейчас не идёт.")
    _log(request, admin, "cancel_generation", course_id)
    return {"run": _run(_root(course), request, course_id)}


@router.post("/courses/{course_id}/ids")
async def allocate_ids(
    course_id: str, body: IdsBody, request: Request, admin: User = Depends(admin_user)
) -> dict:
    """Новые id для редактора: их выдаёт сервер, счётчики курса — монотонные.

    Иначе редактор придумывал бы id сам, а повтор id переписал бы записи
    журнала ученика (``replace_seed`` делает upsert по id).
    """
    _course(course_id)
    try:
        ids = await asyncio.to_thread(courses.allocate_ids, course_id, body.kind, body.count)
    except store.StoreError as error:
        raise _fail(422, str(error)) from error
    return {"ids": ids}


# --- правка модулей ---


async def _read_module_or_404(root: Path, number: int) -> dict:
    try:
        return await asyncio.to_thread(edit.read_module, root, number)
    except store.StoreError as error:
        raise _fail(404, str(error)) from error


def _sync_plan(root: Path, module: int) -> None:
    """Название и число занятий в структуре — из сохранённого модуля.

    Редактор меняет их в seed; без синхронизации структура врала бы, а её
    сохранение падало бы на сверке названий собранных модулей.
    """
    plan = steps.load_course_plan(root)
    if plan is None:
        return
    saved = edit.read_module(root, module)
    title = str(saved.get("topic", {}).get("title", "")).strip()
    lessons = len(saved.get("nodes", []))
    modules = []
    changed = False
    for outline in plan.modules:
        if outline.number == module and (outline.title != title or outline.lessons != lessons):
            outline = outline.model_copy(update={"title": title, "lessons": lessons})
            changed = True
        modules.append(outline)
    if changed:
        steps.save_course_plan(root, plan.model_copy(update={"modules": modules}))


def _write_module_and_sync(root: Path, number: int, payload: dict[str, Any]) -> None:
    edit.write_module(root, number, payload)
    _sync_plan(root, number)


@router.post("/courses/{course_id}/modules", status_code=201)
async def create_module(
    course_id: str, body: ModuleCreateBody, request: Request, admin: User = Depends(admin_user)
) -> dict:
    """Пустой модуль без модели: занятия-заготовки, дальше — редактор.

    Модуль должен стоять в сохранённой структуре курса: номер — это его место,
    а название и число занятий структура показывает в списке. Иначе файл
    оказался бы вне структуры, и её сохранение потом бы не сошлось.
    """
    course = _course(course_id)
    root = _root(course)
    if request.app.state.jobs.running(course_id) is not None:
        raise _fail(409, BUSY)
    plan = await asyncio.to_thread(steps.load_course_plan, root)
    outline = next(
        (item for item in (plan.modules if plan is not None else []) if item.number == body.module),
        None,
    )
    if outline is None:
        raise _fail(422, "Сначала добавьте модуль в структуру курса и сохраните её.")
    try:
        await asyncio.to_thread(
            edit.create_module,
            root,
            body.module,
            title=body.title.strip() or outline.title,
            lessons=body.lessons,
            course_title=plan.title if plan is not None else course.title,
            intro=body.intro,
        )
        await asyncio.to_thread(_sync_plan, root, body.module)
    except store.StoreError as error:
        raise _fail(422, str(error)) from error
    except SeedError as error:
        raise _fail(422, f"Курс не соберётся: {error}") from error
    _log(request, admin, "update_course", course_id)
    _refresh(request, course_id)
    return {"number": body.module, "module": module_view(root, body.module)}


@router.get("/courses/{course_id}/modules/{number}")
async def read_module(course_id: str, number: int) -> dict:
    root = _root(_course(course_id))
    return {"module": await _read_module_or_404(root, number)}


@router.put("/courses/{course_id}/modules/{number}")
async def write_module(
    course_id: str, number: int, payload: dict[str, Any], request: Request,
    admin: User = Depends(admin_user),
) -> dict:
    """Модуль целиком (формы редактора собирают его в браузере)."""
    root = _root(_course(course_id))
    try:
        await asyncio.to_thread(_write_module_and_sync, root, number, payload)
    except store.StoreError as error:
        raise _fail(422, str(error)) from error
    except SeedError as error:
        raise _fail(422, f"Курс не соберётся: {error}") from error
    _log(request, admin, "update_course", course_id)
    _refresh(request, course_id)
    return {"warnings": warnings(course_id)}


@router.delete("/courses/{course_id}/modules/{number}", status_code=204)
async def delete_module(
    course_id: str, number: int, request: Request, admin: User = Depends(admin_user)
) -> None:
    """Убирает модуль из курса (файлы и строку структуры); остальные сдвигаются."""
    course = _course(course_id)
    if request.app.state.jobs.running(course_id) is not None:
        raise _fail(409, BUSY)
    try:
        await asyncio.to_thread(edit.delete_module, _root(course), number)
    except store.StoreError as error:
        raise _fail(404, str(error)) from error
    _log(request, admin, "update_course", course_id)
    _refresh(request, course_id)


@router.get("/courses/{course_id}/modules/{number}/check")
async def check_module(course_id: str, number: int) -> dict:
    """Проверка код-заданий модуля запуском: тесты должны работать.

    Долгий запрос (по заданию на прогон), но без модели: считает сервер.
    """
    root = _root(_course(course_id))
    await _read_module_or_404(root, number)
    problems = await asyncio.to_thread(check.check_module, root, number)
    return {
        "problems": [
            {
                "item_id": problem.item_id,
                "prompt": problem.prompt[:200],
                "kind": problem.kind,
                "detail": problem.detail,
            }
            for problem in problems
        ]
    }


@router.get("/courses/{course_id}/modules/{number}/theory/{lesson_id}")
async def read_theory(course_id: str, number: int, lesson_id: str) -> dict:
    """Конспект занятия; нет такого модуля или занятия — 404, а не пустая строка."""
    root = _root(_course(course_id))
    module = await _read_module_or_404(root, number)
    if lesson_id not in {node["id"] for node in module.get("nodes", [])}:
        raise _fail(404, f"В модуле {number} нет занятия {lesson_id!r}")
    return {"markdown": await asyncio.to_thread(edit.read_theory, root, number, lesson_id)}


@router.put("/courses/{course_id}/modules/{number}/theory/{lesson_id}")
async def write_theory(
    course_id: str, number: int, lesson_id: str, body: TheoryBody, request: Request,
    admin: User = Depends(admin_user),
) -> dict:
    root = _root(_course(course_id))
    try:
        headings = await asyncio.to_thread(edit.write_theory, root, number, lesson_id, body.markdown)
    except (store.StoreError, SeedError) as error:
        raise _fail(422, str(error)) from error
    _log(request, admin, "update_course", course_id)
    _refresh(request, course_id)
    return {"headings": headings}


# --- публикация ---


@router.post("/courses/{course_id}/publish")
async def publish(course_id: str, request: Request, admin: User = Depends(admin_user)) -> dict:
    """Публикация: строгая проверка всего курса, потом флаг и видимость ученикам."""
    course = _course(course_id)
    root = _root(course)
    try:
        await asyncio.to_thread(edit.publish_course, root)
        courses.update_meta(course_id, status="published")
    except (SeedError, store.StoreError) as error:
        raise _fail(422, f"Курс не готов к публикации: {error}") from error
    _log(request, admin, "publish_course", course_id)
    _refresh(request, course_id)
    updated = courses.get_course(course_id)
    assert updated is not None
    return {"course": _meta(updated, modules=len(edit.module_numbers(root)))}


@router.post("/courses/{course_id}/unpublish")
async def unpublish(course_id: str, request: Request, admin: User = Depends(admin_user)) -> dict:
    """Снятие с публикации: ученики теряют доступ, их БД и прогресс остаются."""
    course = _course(course_id)
    try:
        await asyncio.to_thread(edit.unpublish_course, _root(course))
        courses.update_meta(course_id, status="draft")
    except (store.StoreError, SeedError) as error:
        raise _fail(422, f"Курс не снимается с публикации: {error}") from error
    _log(request, admin, "unpublish_course", course_id)
    _refresh(request, course_id)
    updated = courses.get_course(course_id)
    assert updated is not None
    return {"course": _meta(updated, modules=len(edit.module_numbers(_root(updated))))}
