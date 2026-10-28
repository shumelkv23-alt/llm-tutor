"""API проекта по курсу (срез 99).

Все ходы идут под замком ученика (``UserDBPool.lock``): вторая вкладка дождётся
первого хода, а ход не по статусу или этапу отвечает 409 и ничего не меняет.
Замок «курс пройден» проверяется только при выборе описания (``start``).
"""

import time
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from llm_tutor.core import project as core
from llm_tutor.db import repos
from llm_tutor.llm.prompt_config import ProjectPrompts
from llm_tutor.llm.prompts import LLM_FAILURE_REPLY
from llm_tutor.schemas import ProjectFile
from llm_tutor.web import courses, views
from llm_tutor.web.accounts import User
from llm_tutor.web.api_lesson import LessonContext, course_or_404, lesson_context
from llm_tutor.web.security import require_user

router = APIRouter(prefix="/api")

PROJECT_NOT_FOUND = "У этого курса нет проекта."
UNKNOWN_BRIEF = "Такого описания проекта нет."
MAX_FIELDS = 20

PROJECT_ERRORS: dict[str, tuple[int, str]] = {
    "locked": (403, "Проект откроется после прохождения курса."),
    "active_exists": (409, "Проект уже идёт: продолжите его или начните заново."),
    "no_project": (409, "Сначала выберите описание проекта."),
    "wrong_status": (409, "Так сейчас нельзя: этап проекта уже сменился. Обновите страницу."),
    "wrong_stage": (409, "Этот этап уже не текущий. Обновите страницу."),
    "not_found": (404, "Такого предложения наставника нет."),
    "missing": (422, "Заполните обязательные поля."),
    "invalid": (422, "Поля заполнены неверно: проверьте названия и длину."),
    "llm": (502, LLM_FAILURE_REPLY),
    "keep_done": (
        409,
        "Готовый проект останется, пока курс не пройден снова: новый выбрать пока нельзя.",
    ),
}


def _fail(error: core.ProjectError) -> HTTPException:
    status, message = PROJECT_ERRORS[error.code]
    detail: dict = {"message": message}
    if error.missing:
        detail["missing"] = error.missing
    return HTTPException(status_code=status, detail=detail)


@dataclass
class ProjectContext:
    lesson: LessonContext
    file: ProjectFile
    prompts: ProjectPrompts


async def project_context(
    course_id: str, request: Request, user: User = Depends(require_user)
) -> ProjectContext:
    course = course_or_404(course_id, request)
    file = courses.project_file(course)
    if file is None:
        raise HTTPException(status_code=404, detail={"message": PROJECT_NOT_FOUND})
    ctx = await lesson_context(course_id, request, user)
    return ProjectContext(ctx, file, request.app.state.project_prompts)


def _names(ctx: ProjectContext) -> dict[str, str]:
    data, _ = courses.try_course_data(ctx.lesson.course)
    return {node.id: node.name for node in data.nodes} if data is not None else {}


def _briefs(ctx: ProjectContext) -> list[dict]:
    names = _names(ctx)
    return [views.brief_view(brief, names) for brief in ctx.file.briefs]


def _project_view(ctx: ProjectContext, project) -> dict:
    return views.project_view(ctx.lesson.conn, project)


class _Fields(BaseModel):
    fields: dict[str, str]

    @field_validator("fields")
    @classmethod
    def _few(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > MAX_FIELDS:
            raise ValueError("слишком много полей")
        return value


class StartBody(BaseModel):
    brief_id: str = Field(min_length=1, max_length=40)


class ChatBody(BaseModel):
    text: str = Field(min_length=1, max_length=4000)

    @field_validator("text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        # Реплика из одних пробелов — не ход: вызов модели впустую.
        if not value.strip():
            raise ValueError("пустая реплика")
        return value


class TakeBody(BaseModel):
    turn_id: int


class CardBody(_Fields):
    pass


class NotesBody(_Fields):
    stage: str = Field(min_length=1, max_length=40)


class CompleteBody(BaseModel):
    stage: str = Field(min_length=1, max_length=40)


@router.get("/projects")
async def projects(request: Request, user: User = Depends(require_user)) -> dict:
    return {"courses": views.projects_overview(request.app.state, user)}


@router.get("/courses/{course_id}/project")
async def project_state(ctx: ProjectContext = Depends(project_context)) -> dict:
    async with ctx.lesson.lock:
        project = repos.current_project(ctx.lesson.conn)
        _, _, passed = core.course_passed(ctx.lesson.conn, settings=ctx.lesson.settings)
        return {
            "project": _project_view(ctx, project) if project else None,
            "briefs": _briefs(ctx),
            "unlocked": passed,
        }


@router.post("/courses/{course_id}/project/start")
async def project_start(body: StartBody, ctx: ProjectContext = Depends(project_context)) -> dict:
    brief = next((item for item in ctx.file.briefs if item.id == body.brief_id), None)
    if brief is None:
        raise HTTPException(status_code=400, detail={"message": UNKNOWN_BRIEF})
    async with ctx.lesson.lock:
        try:
            project = core.start(
                ctx.lesson.conn, ctx.prompts, brief=brief, template=ctx.file.template,
                now=time.time(), settings=ctx.lesson.settings,
            )
        except core.ProjectError as error:
            raise _fail(error) from error
        return {"project": _project_view(ctx, project)}


@router.post("/courses/{course_id}/project/chat")
async def project_chat(body: ChatBody, ctx: ProjectContext = Depends(project_context)) -> dict:
    lesson = ctx.lesson
    async with lesson.lock:
        try:
            student, mentor = await core.chat(
                lesson.conn, lesson.client, ctx.prompts, text=body.text,
                course_title=lesson.course.title, now=time.time(), settings=lesson.settings,
            )
        except core.ProjectError as error:
            raise _fail(error) from error
        project = repos.current_project(lesson.conn)
        view = _project_view(ctx, project)
        sent = {message["id"]: message for message in view["messages"]}
        return {
            "messages": [sent[student.id], sent[mentor.id]],
            "project": view,
        }


@router.put("/courses/{course_id}/project/card")
async def project_card(body: CardBody, ctx: ProjectContext = Depends(project_context)) -> dict:
    async with ctx.lesson.lock:
        try:
            project = core.update_card(ctx.lesson.conn, fields=body.fields, now=time.time())
        except core.ProjectError as error:
            raise _fail(error) from error
        return {"project": _project_view(ctx, project)}


@router.post("/courses/{course_id}/project/take")
async def project_take(body: TakeBody, ctx: ProjectContext = Depends(project_context)) -> dict:
    async with ctx.lesson.lock:
        try:
            project = core.take_proposal(ctx.lesson.conn, turn_id=body.turn_id, now=time.time())
        except core.ProjectError as error:
            raise _fail(error) from error
        return {"project": _project_view(ctx, project)}


@router.post("/courses/{course_id}/project/accept")
async def project_accept(body: TakeBody, ctx: ProjectContext = Depends(project_context)) -> dict:
    """«Подтвердить и дальше»: итог этапа из сохранённой реплики наставника (срез 103)."""
    async with ctx.lesson.lock:
        try:
            project = core.accept_stage(ctx.lesson.conn, turn_id=body.turn_id, now=time.time())
        except core.ProjectError as error:
            raise _fail(error) from error
        return {"project": _project_view(ctx, project)}


@router.post("/courses/{course_id}/project/approve")
async def project_approve(ctx: ProjectContext = Depends(project_context)) -> dict:
    async with ctx.lesson.lock:
        try:
            project = core.approve(ctx.lesson.conn, now=time.time())
        except core.ProjectError as error:
            raise _fail(error) from error
        return {"project": _project_view(ctx, project)}


@router.put("/courses/{course_id}/project/notes")
async def project_notes(body: NotesBody, ctx: ProjectContext = Depends(project_context)) -> dict:
    async with ctx.lesson.lock:
        try:
            project = core.save_notes(
                ctx.lesson.conn, stage=body.stage, fields=body.fields, now=time.time()
            )
        except core.ProjectError as error:
            raise _fail(error) from error
        return {"project": _project_view(ctx, project)}


@router.post("/courses/{course_id}/project/complete")
async def project_complete(
    body: CompleteBody, ctx: ProjectContext = Depends(project_context)
) -> dict:
    async with ctx.lesson.lock:
        try:
            project = core.complete_stage(ctx.lesson.conn, stage=body.stage, now=time.time())
        except core.ProjectError as error:
            raise _fail(error) from error
        return {"project": _project_view(ctx, project)}


@router.post("/courses/{course_id}/project/archive")
async def project_archive(ctx: ProjectContext = Depends(project_context)) -> dict:
    async with ctx.lesson.lock:
        try:
            core.archive(ctx.lesson.conn, now=time.time(), settings=ctx.lesson.settings)
        except core.ProjectError as error:
            raise _fail(error) from error
        return {"project": None, "briefs": _briefs(ctx)}
