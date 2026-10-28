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
    """Созданный курс; встроенный и чужой id одинаково недоступны."""
    course = courses.get_course(course_id)
    if course is None or course.root is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return course, course.root


@router.get("/admin/courses", response_class=HTMLResponse)
async def course_list(request: Request, user: User = Depends(admin_page_user)) -> HTMLResponse:
    """Список курсов: черновики и опубликованные, со счётчиком модулей."""
    items = []
    for course in courses.manageable_courses():
        meta = courses.meta_for(course)
        modules = 0
        if course.root is not None:
            try:
                modules = len(edit.module_numbers(course.root))
            except Exception:  # noqa: BLE001 — битый модуль не роняет список
                modules = 0
        else:
            # У встроенного курса папки нет, модули читаются из seed-файлов:
            # без этого в списке стоял бы ноль, хотя курс полный.
            try:
                modules = len(courses.course_data(course).topics)
            except Exception:  # noqa: BLE001
                modules = 0
        items.append(
            {
                "id": course.id,
                "title": course.title,
                "subtitle": course.subtitle,
                "status": course.status,
                "origin": meta.origin if meta is not None else "manual",
                "modules": modules,
                "editable": course.root is not None,
            }
        )
    return render(
        request,
        "admin_courses.html",
        "admin_courses",
        user=user,
        items=items,
    )


@router.get("/admin/courses/{course_id}", response_class=HTMLResponse)
async def course_page(
    request: Request, course_id: str, user: User = Depends(admin_page_user)
) -> HTMLResponse:
    """Обзор курса: метаданные, источники, генерация, модули, публикация."""
    course, root = _course_or_404(course_id)
    numbers = edit.module_numbers(root)
    modules = []
    for number in numbers:
        try:
            modules.append(module_view(root, number))
        except Exception as error:  # noqa: BLE001 — недособранный модуль показываем текстом
            modules.append({"number": number, "error": str(error)})
    built = {module["number"]: module for module in modules if "error" not in module}
    return render(
        request,
        "admin_course.html",
        "admin_courses",
        user=user,
        course=course,
        modules=modules,
        # У собранных модулей название и число занятий — из seed: структура
        # показывает их только для чтения.
        built_numbers=sorted(built),
        built_titles={number: item["title"] for number, item in built.items()},
        built_lessons={number: item["lessons"] for number, item in built.items()},
        # Структуру отдаём сразу в шаблон: строки рисует сервер.
        plan=steps.load_course_plan(root),
        sources=sources_view(root),
        warnings=warnings(course_id),
        broken=courses.try_course_data(course)[1],
        running=request.app.state.jobs.running(course_id) is not None,
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
        "admin_courses",
        user=user,
        course=course,
        number=number,
        modules=edit.module_numbers(root),
        # Переделка кусков берёт остальные шаги из артефактов прогона: у
        # собранного вручную модуля их нет, и кнопки были бы ложными.
        parts=steps.plan_artifact(root, number) is not None,
    )
