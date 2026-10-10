"""API курсов и занятия."""

from fastapi import APIRouter, Depends, HTTPException, Request

from llm_tutor.web import accounts
from llm_tutor.web.accounts import User
from llm_tutor.web.courses import COURSES, Course, get_course
from llm_tutor.web.security import accounts_db, require_user
from llm_tutor.web.views import course_card

router = APIRouter(prefix="/api")

COURSE_NOT_FOUND = "Такого курса нет."


def course_or_404(course_id: str) -> Course:
    course = get_course(course_id)
    if course is None:
        raise HTTPException(status_code=404, detail={"message": COURSE_NOT_FOUND})
    return course


def cards_for(request: Request, user: User) -> list[dict]:
    enrolled = set(accounts.enrolled_courses(accounts_db(request), user.id))
    return [
        course_card(
            course,
            user_id=user.id,
            enrolled=course.id in enrolled,
            pool=request.app.state.userdbs,
            settings=request.app.state.settings,
        )
        for course in COURSES
    ]


@router.get("/courses")
async def list_courses(request: Request, user: User = Depends(require_user)) -> dict:
    return {"courses": cards_for(request, user)}


@router.post("/courses/{course_id}/enroll")
async def enroll(course_id: str, request: Request, user: User = Depends(require_user)) -> dict:
    course = course_or_404(course_id)
    pool = request.app.state.userdbs
    async with pool.lock(user.id, course.id):
        # Сначала БД ученика: запись без БД оставила бы курс, который не открыть.
        pool.open(user.id, course.id)
        accounts.enroll(accounts_db(request), user.id, course.id)
    card = course_card(
        course, user_id=user.id, enrolled=True, pool=pool, settings=request.app.state.settings
    )
    return {"course": card}
