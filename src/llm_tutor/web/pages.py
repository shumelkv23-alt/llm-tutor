"""HTML-страницы: главная, мои курсы, занятие, профиль, вход и регистрация.

Страницы отдают оболочку и стартовые данные; дальше страница говорит с
``/api/*``. Тексты и данные экранирует Jinja2 (автоэкранирование включено).
Главная открыта гостю, остальные разделы — только вошедшему (``page_user``).
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from llm_tutor.core import project as core_project
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.web import accounts, notebook, reminders, courses as course_registry, notes as notes_feed
from llm_tutor.web.accounts import User
from llm_tutor.web.api_lesson import cards_for
from llm_tutor.web.courses import RENAMED_COURSES, published_courses, student_course
from llm_tutor.web.security import accounts_db, optional_user, page_user, safe_next
from llm_tutor.web.views import (
    COURSE_ERRORS,
    calibration_view,
    course_card,
    learning_view,
    note_view,
    search_course,
    weekly_plan,
)

router = APIRouter()
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NavItem:
    key: str
    href: str
    label: str
    icon: str


NAV: tuple[NavItem, ...] = (
    NavItem("home", "/", "Главная", "home"),
    NavItem("courses", "/courses", "Мои курсы", "book"),
    NavItem("lesson", "/lesson", "Занятие", "lesson"),
    NavItem("notes", "/notes", "Конспект", "note"),
    NavItem("project", "/project", "Проект", "spark"),
    NavItem("profile", "/profile", "Профиль", "user"),
)

# Пункты меню только для админов (срез 63): гостю и ученику они не рисуются.
# «Редактор курсов» — прямой вход в правку курсов: из «Админки» туда ведёт
# вкладка, но за курсом туда идут чаще, чем за списком учеников.
ADMIN_NAV: tuple[NavItem, ...] = (
    NavItem("admin", "/admin", "Админка", "shield"),
    NavItem("admin_courses", "/admin/courses", "Редактор курсов", "route"),
)

_TITLES = {item.key: item.label for item in (*NAV, *ADMIN_NAV)} | {
    "login": "Вход",
    "register": "Регистрация",
    "forgot": "Восстановление пароля",
    "reset": "Новый пароль",
    "unsubscribe": "Отписка от напоминаний",
}


def render(
    request: Request, template: str, active: str, *, user: User | None, **context: Any
) -> HTMLResponse:
    """Страница в общей оболочке с отмеченным пунктом навигации."""
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        template,
        {
            "nav": (*NAV, *ADMIN_NAV) if user is not None and user.is_admin else NAV,
            "active": active,
            "page_title": _TITLES[active],
            "user": user,
            "brand_caption": request.app.state.settings.app_brand,
            **context,
        },
    )


def _learning(request: Request, user: User) -> list[dict]:
    """Курсы ученика с карточкой и обучением (последний записанный — первым)."""
    pool = request.app.state.userdbs
    settings = request.app.state.settings
    result = []
    for course_id in accounts.enrolled_courses(accounts_db(request), user.id):
        course = student_course(course_id, include_drafts=user.is_admin)
        if course is None or not course.visible:
            continue
        card = course_card(course, user_id=user.id, enrolled=True, pool=pool, settings=settings)
        learning = None
        if card["status"] != "error":
            learning = learning_view(pool.open(user.id, course.id), settings=settings)
        result.append({"card": card, "learning": learning})
    return result


def _current_card(request: Request, user: User) -> dict | None:
    """Карточка курса, который ученик проходит сейчас (последний записанный)."""
    for course_id in accounts.enrolled_courses(accounts_db(request), user.id):
        course = student_course(course_id, include_drafts=user.is_admin)
        if course is not None and course.visible:
            return course_card(
                course,
                user_id=user.id,
                enrolled=True,
                pool=request.app.state.userdbs,
                settings=request.app.state.settings,
            )
    return None


@router.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    user = optional_user(request)
    if user is None:
        settings = request.app.state.settings
        cards = [
            course_card(course, user_id=0, enrolled=False, pool=request.app.state.userdbs, settings=settings)
            for course in published_courses()
        ]
        return render(request, "home.html", "home", user=None, cards=cards)
    return render(request, "home.html", "home", user=user, courses=_learning(request, user))


@router.get("/courses", response_class=HTMLResponse)
async def courses(request: Request, user: User = Depends(page_user)) -> HTMLResponse:
    cards = cards_for(request, user)
    return render(
        request,
        "courses.html",
        "courses",
        user=user,
        mine=[card for card in cards if card["enrolled"]],
        available=[card for card in cards if not card["enrolled"]],
    )


@router.get("/lesson")
async def lesson(request: Request, user: User = Depends(page_user)) -> Response:
    """«Занятие» в меню — последний курс ученика или каталог, если курсов нет."""
    enrolled = accounts.enrolled_courses(accounts_db(request), user.id)
    known = [
        course_id
        for course_id in enrolled
        if (course := student_course(course_id, include_drafts=user.is_admin)) is not None
        and course.visible
    ]
    target = f"/lesson/{known[0]}" if known else "/courses"
    return RedirectResponse(target, status_code=303)


@router.get("/lesson/{course_id}", response_class=HTMLResponse)
async def lesson_page(
    course_id: str, request: Request, node: str = "", user: User = Depends(page_user)
) -> Response:
    if course_id in RENAMED_COURSES:
        # Закладка на прежний адрес курса ведёт туда же (срез 52).
        # Query-строка (?tab=…) не теряется (аудит среза 52, L2).
        query = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(f"/lesson/{RENAMED_COURSES[course_id]}{query}", status_code=308)
    course = student_course(course_id, include_drafts=user.is_admin)
    if course is None:
        raise HTTPException(status_code=404, detail="Такого курса нет.")
    enrolled = course_id in accounts.enrolled_courses(accounts_db(request), user.id)
    card = course_card(
        course,
        user_id=user.id,
        enrolled=enrolled,
        pool=request.app.state.userdbs,
        settings=request.app.state.settings,
    )
    # Ссылка из заметки или поиска ведёт в своё занятие: страница запоминает
    # цель, а браузер переходит к ней после загрузки состояния (lesson.js).
    # Неизвестный id не ошибка — переход отклонит API или проигнорирует JS.
    return render(
        request,
        "lesson.html",
        "lesson",
        user=user,
        course=card,
        target_node=node.strip()[:200],
    )


def _format_time(ts: float | None) -> str:
    if ts is None:
        return "—"
    try:
        return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")
    except (OverflowError, OSError, ValueError):
        return "—"


# «Объясни новичку» переехало во вкладку занятия (срез 94): старые адреса ведут на занятие.
@router.get("/lesson/{course_id}/explain")
@router.get("/lesson/{course_id}/explain/{session_id}")
async def explain_moved(
    course_id: str, session_id: int | None = None, user: User = Depends(page_user)
) -> Response:
    return RedirectResponse(f"/lesson/{course_id}", status_code=303)


@router.get("/notes", response_class=HTMLResponse)
async def notes_page(
    request: Request,
    course: str | None = None,
    lesson: str | None = None,
    view: str | None = None,
    q: str | None = None,
    all: str | None = None,  # noqa: A002 — имя параметра адреса ``?all=1``
    user: User = Depends(page_user),
) -> HTMLResponse:
    """«Конспект» по скелету курса и «Выполненные задания».

    Журнал у каждого курса свой, поэтому страница проходит по записанным
    курсам. Нечитаемый курс не роняет страницу: он показывается строкой с
    ошибкой. ``?course=`` — курс; без него выбран текущий (последний
    записанный, как в профиле). ``?all=1`` — все курсы, но без скелета: только
    материал ученика. ``?lesson=`` — одно занятие выбранного курса; неизвестный
    или чужой id фильтр игнорирует.

    ``?view=solutions`` — «Выполненные задания»; ``notebook`` (по умолчанию) и
    старое ``questions`` — конспект. ``?q=`` — поиск (все слова, регистр не
    важен). Старое ``?kind=`` игнорируется — закладки страницу не ломают.
    """
    part = "solutions" if view == "solutions" else "notebook"
    query = q.strip()[: notes_feed.SEARCH_MAX_CHARS] if q and q.strip() else None
    every = all is not None and all not in ("", "0")
    pool = request.app.state.userdbs
    candidates = []
    for course_id in accounts.enrolled_courses(accounts_db(request), user.id):
        found = student_course(course_id, include_drafts=user.is_admin)
        if found is not None and found.visible:
            candidates.append(found)
    known = {item.id for item in candidates}
    selected = course if course in known else None
    if selected is None and not every and candidates:
        # Текущий курс (последний записанный) — его скелет, без лишнего клика.
        selected = candidates[0].id
    if selected is not None:
        every = False
    lesson_filters: list[dict] = []
    selected_lesson: str | None = None
    groups: list[dict] = []
    total = 0
    for item in candidates:
        if selected is not None and item.id != selected:
            continue
        try:
            conn = pool.open(user.id, item.id)
            # Чужой или неизвестный id темы игнорируется; известный — фильтр
            # ещё до поиска и лимита, иначе поиск в теме показывал бы чужие.
            lesson_id = (
                lesson
                if selected is not None and lesson and notes_feed.has_lesson(conn, lesson)
                else None
            )
            if part == "solutions":
                lessons = notes_feed.lesson_solutions(conn, query=query, node_id=lesson_id)
                book = None
                count = sum(entry["count"] for entry in lessons)
            else:
                book = notebook.course_notebook(
                    conn, skeleton=not every, query=query, node_id=lesson_id
                )
                lessons = book["lessons"]
                count = book["count"]
                # Чипы тем — все занятия курса, а не только отфильтрованное.
                lessons_for_chips = (
                    notebook.course_notebook(conn, query=query)["lessons"]
                    if lesson_id is not None
                    else lessons
                )
        except (*COURSE_ERRORS, OSError):
            logger.exception("Конспект: БД ученика %s по курсу %s не читается", user.id, item.id)
            groups.append(
                {"id": item.id, "title": item.title, "error": True, "lessons": [], "count": 0}
            )
            continue
        if selected is not None:
            selected_lesson = lesson_id
            source = lessons if part == "solutions" else lessons_for_chips
            lesson_filters = [
                {
                    "id": entry["node_id"],
                    "name": entry["name"],
                    "module": entry.get("module"),
                    "count": entry["count"],
                }
                for entry in source
                if entry.get("node_id") and (part == "solutions" or not entry.get("locked"))
            ]
        total += count
        groups.append(
            {
                "id": item.id,
                "title": item.title,
                "error": False,
                "lessons": lessons,
                "book": book,
                "count": count,
            }
        )
    return render(
        request,
        "notes.html",
        "notes",
        user=user,
        courses=groups,
        total=total,
        any_error=any(group["error"] for group in groups),
        selected=selected,
        every=every,
        selected_lesson=selected_lesson,
        lesson_filters=lesson_filters,
        filter_courses=[{"id": item.id, "title": item.title} for item in candidates],
        part=part,
        query=query,
        note_max=notes_feed.NOTE_MAX_CHARS,
        search_max=notes_feed.SEARCH_MAX_CHARS,
        locked_note=notebook.LOCKED_NOTE,
    )


@router.get("/project", response_class=HTMLResponse)
async def projects(request: Request, user: User = Depends(page_user)) -> HTMLResponse:
    """Курсы с проектной работой; данные страница берёт у ``/api/projects``."""
    return render(request, "projects.html", "project", user=user)


@router.get("/project/{course_id}", response_class=HTMLResponse)
async def project_page(
    course_id: str, request: Request, user: User = Depends(page_user)
) -> Response:
    """Рабочая страница проекта; у курса без проекта её нет, без записи и пока рано — на список."""
    course = student_course(course_id, include_drafts=user.is_admin)
    if course is None or course_registry.project_file(course) is None:
        raise HTTPException(status_code=404, detail="У этого курса нет проекта.")
    if course_id not in accounts.enrolled_courses(accounts_db(request), user.id):
        return RedirectResponse("/project", status_code=303)
    try:
        conn = request.app.state.userdbs.open(user.id, course.id)
        started = repos.current_project(conn) is not None
        passed = started or core_project.course_passed(
            conn, settings=request.app.state.settings
        )[2]
    except (*COURSE_ERRORS, OSError):
        logger.exception("БД ученика %s по курсу %s не открывается", user.id, course.id)
        return RedirectResponse("/project", status_code=303)
    if not passed:
        return RedirectResponse("/project", status_code=303)
    card = {"id": course.id, "title": course.title, "published": course.status == "published"}
    return render(request, "project.html", "project", user=user, course=card)


@router.get("/profile", response_class=HTMLResponse)
async def profile(
    request: Request, q: str = "", user: User = Depends(page_user)
) -> HTMLResponse:
    joined = datetime.fromtimestamp(user.created_at).strftime("%d.%m.%Y")
    card = _current_card(request, user)
    query = q.strip()[:100]
    notes: list[dict] = []
    search: list[dict] = []
    calibration: list[dict] = []
    plan: dict = {"steps": [], "total_minutes": 0}
    notes_error = False
    if card is not None and card["status"] != "error":
        pool = request.app.state.userdbs
        course_id = card["id"]
        if pool.exists(user.id, course_id):
            try:
                conn = pool.open(user.id, course_id)
                graph = CourseGraph.load(conn)
                calibration = calibration_view(conn, settings=request.app.state.settings)
                plan = weekly_plan(conn, settings=request.app.state.settings)
                if query:
                    notes = [
                        note_view(note, graph) for note in repos.search_notes(conn, query)
                    ]
                    search = search_course(conn, query, course_id=course_id)
                else:
                    notes = [note_view(note, graph) for note in repos.list_notes(conn)]
            except COURSE_ERRORS + (ValueError,):
                notes_error = True
    # Профиль — краткая сводка по текущему курсу; подробности (занятия,
    # анкета) — в маршруте на странице занятия. Заметки и поиск читаются из
    # БД текущего курса, если она открывается.
    return render(
        request,
        "profile.html",
        "profile",
        user=user,
        joined=joined,
        card=card,
        notes=notes,
        search=search,
        query=query,
        notes_error=notes_error,
        calibration=calibration,
        plan=plan,
        format_time=_format_time,
    )


def _auth_page(request: Request, template: str, active: str, next: str | None) -> Response:
    """Вход и регистрация: вошедшего сразу ведём дальше."""
    target = safe_next(next)
    if optional_user(request) is not None:
        return RedirectResponse(target, status_code=303)
    return render(
        request,
        template,
        active,
        user=None,
        next=target,
        registration_open=request.app.state.settings.registration_open,
    )


@router.get("/login", response_class=HTMLResponse)
async def login(request: Request, next: str | None = None) -> Response:
    return _auth_page(request, "login.html", "login", next)


@router.get("/register", response_class=HTMLResponse)
async def register(request: Request, next: str | None = None) -> Response:
    return _auth_page(request, "register.html", "register", next)


def _no_store(response: Response) -> Response:
    """Страница со ссылкой-секретом не кэшируется: «Назад» не должен её показать."""
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password(request: Request) -> Response:
    """Заявка письма со ссылкой. Вошедшему здесь нечего делать: пароль он помнит."""
    if optional_user(request) is not None:
        return RedirectResponse("/profile", status_code=303)
    return _no_store(
        render(
            request,
            "forgot_password.html",
            "forgot",
            user=None,
            # Почта не настроена — ученик должен знать, что письма не будет:
            # ссылка в этом случае печатается в журнале сервера (режим показа).
            mail_configured=request.app.state.settings.smtp_configured,
            # Срок жизни — из настроек, а не «час» в тексте: PASSWORD_RESET_TTL_MINUTES
            # меняется в .env, и обещание в шаблоне не должно разойтись с делом.
            ttl_minutes=request.app.state.settings.password_reset_ttl_minutes,
        )
    )


@router.get("/unsubscribe", response_class=HTMLResponse)
async def unsubscribe(request: Request, token: str = "") -> Response:
    """Страница отписки; сама отписка — POST из ``unsubscribe.js``.

    Ссылка из письма не должна отписывать при простом открытии: почтовые
    клиенты иногда подгружают ссылки заранее.
    """
    valid = bool(token) and reminders.verify_token(request.app.state.settings, token) is not None
    return _no_store(
        render(
            request,
            "unsubscribe.html",
            "unsubscribe",
            user=None,
            token=token,
            valid=valid,
        )
    )


@router.get("/reset-password", response_class=HTMLResponse)
async def reset_password(request: Request, token: str = "") -> Response:
    """Форма нового пароля; мёртвая ссылка — сообщение и путь за новым письмом.

    Годится ли ссылка, решает ``accounts.reset_token_user`` (только чтение) —
    страница ничего не сжигает: её мог открыть и поисковый робот.
    """
    user = accounts.reset_token_user(accounts_db(request), token)
    return _no_store(
        render(
            request,
            "reset_password.html",
            "reset",
            user=None,
            token=token,
            valid=user is not None,
            email=None if user is None else user.email,
            ttl_minutes=request.app.state.settings.password_reset_ttl_minutes,
        )
    )
