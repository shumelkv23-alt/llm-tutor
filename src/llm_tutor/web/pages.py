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
from llm_tutor.web.courses import RENAMED_COURSES, published_courses, student_course
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

# Пункт меню только для админов (срез 63): гостю и ученику он не рисуется.
ADMIN_NAV = NavItem("admin", "/admin", "Админка", "shield")

_TITLES = {item.key: item.label for item in (*NAV, ADMIN_NAV)} | {
    "login": "Вход",
    "register": "Регистрация",
    "forgot": "Восстановление пароля",
    "reset": "Новый пароль",
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
            "nav": (*NAV, ADMIN_NAV) if user is not None and user.is_admin else NAV,
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
    course_id: str, request: Request, user: User = Depends(page_user)
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
    return render(request, "lesson.html", "lesson", user=user, course=card)


def _explain_page(
    request: Request, user: User, course_id: str, *, topic: str | None, session_id: int | None
) -> Response:
    """Страница «Объясни новичку»: проверки — как у страницы занятия."""
    course = student_course(course_id, include_drafts=user.is_admin)
    if course is None:
        raise HTTPException(status_code=404, detail="Такого курса нет.")
    if course_id not in accounts.enrolled_courses(accounts_db(request), user.id):
        return RedirectResponse(f"/lesson/{course_id}", status_code=303)
    chosen = topic if topic and topic.isdigit() else ""
    return render(
        request,
        "explain.html",
        "lesson",
        user=user,
        course=course,
        topic=chosen,
        session_id=session_id or "",
    )


@router.get("/lesson/{course_id}/explain", response_class=HTMLResponse)
async def explain_page(
    course_id: str, request: Request, topic: str | None = None, user: User = Depends(page_user)
) -> Response:
    return _explain_page(request, user, course_id, topic=topic, session_id=None)


@router.get("/lesson/{course_id}/explain/{session_id}", response_class=HTMLResponse)
async def explain_history_page(
    course_id: str, session_id: int, request: Request, user: User = Depends(page_user)
) -> Response:
    return _explain_page(request, user, course_id, topic=None, session_id=session_id)


@router.get("/profile", response_class=HTMLResponse)
async def profile(request: Request, user: User = Depends(page_user)) -> HTMLResponse:
    joined = datetime.fromtimestamp(user.created_at).strftime("%d.%m.%Y")
    # Профиль — только краткая сводка по текущему курсу; подробности (занятия,
    # анкета) — в маршруте на странице занятия.
    return render(
        request, "profile.html", "profile", user=user, joined=joined, card=_current_card(request, user)
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
