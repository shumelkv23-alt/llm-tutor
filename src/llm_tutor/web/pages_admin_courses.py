"""Страницы создания курса: список, обзор курса, редактор модуля.

Доступ — только админам (``admin_page_user`` на роутере): гость уходит на вход,
остальным раздел отвечает 404. Данные страницы берут теми же функциями, что и
API (``api_admin_courses``), поэтому страница и API не расходятся.
"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from llm_tutor.course import store
from llm_tutor.course.build import edit, steps
from llm_tutor.web import courses
from llm_tutor.web.accounts import User
from llm_tutor.web.api_admin_courses import module_view, sources_view, warnings
from llm_tutor.web.pages import render
from llm_tutor.web.security import NOT_FOUND, admin_page_user

router = APIRouter(dependencies=[Depends(admin_page_user)])


def _course_or_404(course_id: str) -> tuple[courses.Course, Path]:
    """Курс и каталог его модулей; встроенный правится на месте, чужой id — 404."""
    course = courses.get_course(course_id)
    if course is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    try:
        return course, courses.edit_folder(course)
    except store.StoreError as error:
        raise HTTPException(status_code=404, detail=NOT_FOUND) from error


def _course_items(*, editable_only: bool = False) -> list[dict]:
    """Строки списка курсов: черновики и опубликованные, со счётчиком модулей."""
    items = []
    for course in courses.all_courses():
        if editable_only and course.root is None:
            continue
        meta = courses.meta_for(course)
        try:
            modules = len(edit.module_numbers(courses.folder(course)))
        except Exception:  # noqa: BLE001 — битый модуль не роняет список
            modules = 0
        items.append(
            {
                "id": course.id,
                "title": course.title,
                "subtitle": course.subtitle,
                "status": course.status,
                "origin": meta.origin if meta is not None else "manual",
                "modules": modules,
                "editable": True,
                "builtin": course.root is None,
            }
        )
    return items


def _editable_courses() -> list[courses.Course]:
    """Курсы, которые можно генерировать и править (встроенные — только код)."""
    return [course for course in courses.all_courses() if course.root is not None]


def _module_rows(root: Path) -> list[dict]:
    """Модули курса для таблицы; недособранный модуль показываем текстом."""
    rows = []
    for number in edit.module_numbers(root):
        try:
            rows.append(module_view(root, number))
        except Exception as error:  # noqa: BLE001 — недособранный модуль показываем текстом
            rows.append({"number": number, "error": str(error)})
    return rows


@router.get("/admin/courses", response_class=HTMLResponse)
async def course_list(request: Request, user: User = Depends(admin_page_user)) -> HTMLResponse:
    """Раздел «Изменение курсов»: список курсов и переход к правке."""
    return render(
        request,
        "admin_courses.html",
        "admin",
        user=user,
        items=_course_items(),
        tab="courses",
        admin_courses=None,
        admin_course=None,
        admin_action="/admin/courses",
    )


@router.get("/admin/generation", response_class=HTMLResponse)
async def generation_page(
    request: Request, course: str = "", user: User = Depends(admin_page_user)
) -> HTMLResponse:
    """Раздел «Генерация курса»: список курсов или рабочее место выбранного.

    Курс задаётся query-параметром ``?course=``: у генерации нет отдельного
    адреса на курс, поэтому переключаться между курсами можно тем же списком.
    Встроенный или несуществующий курс приводит сюда же — к списку.
    """
    chosen = courses.get_course(course) if course else None
    if chosen is not None and chosen.root is not None:
        root = chosen.root
        return render(
            request,
            "admin_generation.html",
            "admin",
            user=user,
            course=chosen,
            modules=_module_rows(root),
            # Структуру отдаём сразу в шаблон: строки рисует сервер.
            plan=steps.load_course_plan(root),
            sources=sources_view(root),
            broken=courses.try_course_data(chosen)[1],
            running=request.app.state.jobs.running(chosen.id) is not None,
            tab="generation",
            admin_courses=_editable_courses(),
            admin_course=chosen,
            admin_action="/admin/generation",
            admin_picker=True,
        )
    return render(
        request,
        "admin_generation_list.html",
        "admin",
        user=user,
        items=_course_items(editable_only=True),
        tab="generation",
        admin_courses=_editable_courses(),
        admin_course=None,
        admin_action="/admin/generation",
        admin_picker=bool(_editable_courses()),
    )


@router.get("/admin/courses/{course_id}", response_class=HTMLResponse)
async def course_page(
    request: Request, course_id: str, user: User = Depends(admin_page_user)
) -> HTMLResponse:
    """Раздел «Изменение курсов»: метаданные, модули, публикация."""
    course, root = _course_or_404(course_id)
    return render(
        request,
        "admin_course.html",
        "admin",
        user=user,
        course=course,
        modules=_module_rows(root),
        warnings=warnings(course_id),
        broken=courses.try_course_data(course)[1],
        tab="courses",
        admin_courses=courses.all_courses(),
        admin_course=course,
        admin_action=f"/admin/courses/{course_id}",
    )


@router.get("/admin/courses/{course_id}/modules/{number}", response_class=HTMLResponse)
async def module_page(
    request: Request, course_id: str, number: int, user: User = Depends(admin_page_user)
) -> HTMLResponse:
    """Редактор модуля: занятие за занятием, задания и конспект.

    Сам модуль страница не рендерит — его забирает и разбирает JS
    (``GET /api/admin/courses/{id}/modules/{number}``): правки идут целым
    модулем, а формы удобнее собирать в браузере.
    """
    course, root = _course_or_404(course_id)
    try:
        edit.read_module(root, number)
    except store.StoreError as error:
        raise HTTPException(status_code=404, detail=NOT_FOUND) from error
    return render(
        request,
        "admin_module.html",
        "admin",
        user=user,
        course=course,
        number=number,
        modules=edit.module_numbers(root),
        tab="courses",
        admin_courses=courses.all_courses(),
        admin_course=course,
        admin_action=f"/admin/courses/{course_id}",
    )
