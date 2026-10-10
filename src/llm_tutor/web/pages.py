"""HTML-страницы: главная, мои курсы, занятие, профиль, вход и регистрация.

Страницы отдают оболочку и стартовые данные; дальше страница говорит с
``/api/*``. Тексты и данные экранирует Jinja2 (автоэкранирование включено).
Главная открыта гостю, остальные разделы — только вошедшему (``page_user``).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from llm_tutor.web import accounts
from llm_tutor.web.accounts import User
from llm_tutor.web.api_lesson import cards_for
from llm_tutor.web.courses import get_course
from llm_tutor.web.security import accounts_db, optional_user, page_user, safe_next
from llm_tutor.web.views import course_card, learning_view

router = APIRouter()


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
    NavItem("profile", "/profile", "Профиль", "user"),
)

_TITLES = {item.key: item.label for item in NAV} | {"login": "Вход", "register": "Регистрация"}


def render(
    request: Request, template: str, active: str, *, user: User | None, **context: Any
) -> HTMLResponse:
    """Страница в общей оболочке с отмеченным пунктом навигации."""
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        template,
        {
            "nav": NAV,
            "active": active,
            "page_title": _TITLES[active],
            "user": user,
            **context,
        },
    )


def _learning(request: Request, user: User) -> list[dict]:
    """Курсы ученика с карточкой и обучением (последний записанный — первым)."""
    pool = request.app.state.userdbs
    settings = request.app.state.settings
    result = []
    for course_id in accounts.enrolled_courses(accounts_db(request), user.id):
        course = get_course(course_id)
        if course is None:
            continue
        card = course_card(course, user_id=user.id, enrolled=True, pool=pool, settings=settings)
        learning = None
        if card["status"] != "error":
            learning = learning_view(pool.open(user.id, course.id), settings=settings)
        result.append({"card": card, "learning": learning})
    return result


@router.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    user = optional_user(request)
    if user is None:
        from llm_tutor.web.courses import COURSES

        settings = request.app.state.settings
        cards = [
            course_card(course, user_id=0, enrolled=False, pool=request.app.state.userdbs, settings=settings)
            for course in COURSES
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
    known = [course_id for course_id in enrolled if get_course(course_id) is not None]
    target = f"/lesson/{known[0]}" if known else "/courses"
    return RedirectResponse(target, status_code=303)


@router.get("/lesson/{course_id}", response_class=HTMLResponse)
async def lesson_page(
    course_id: str, request: Request, user: User = Depends(page_user)
) -> HTMLResponse:
    course = get_course(course_id)
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
    return render(request, "lesson.html", "lesson", user=user, course=card)


@router.get("/profile", response_class=HTMLResponse)
async def profile(request: Request, user: User = Depends(page_user)) -> HTMLResponse:
    joined = datetime.fromtimestamp(user.created_at).strftime("%d.%m.%Y")
    return render(
        request, "profile.html", "profile", user=user, joined=joined, courses=_learning(request, user)
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
