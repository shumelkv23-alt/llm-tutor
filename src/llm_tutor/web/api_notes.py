"""API личных заметок конспекта.

Заметка — текст ученика к записи «Определений и пояснений» (реплике-вопросу
тьютору). Пишется под замком ученика, как ходы занятия: страница могла
открыться, пока идёт ход, и запись не должна перемешаться с ним.
"""

import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from llm_tutor.web import notes
from llm_tutor.web.api_lesson import LessonContext, lesson_context

router = APIRouter(prefix="/api")

NOTE_MISSING = "Такой записи в конспекте нет."


class NoteBody(BaseModel):
    # Пустая строка допустима: она удаляет заметку.
    text: str = Field(max_length=notes.NOTE_MAX_CHARS)


@router.put("/courses/{course_id}/notes/{message_id}")
async def save_note(
    message_id: int,
    body: NoteBody,
    ctx: LessonContext = Depends(lesson_context),
) -> dict:
    """Сохраняет заметку к записи конспекта; пустой текст удаляет её.

    Записи нет, это не вопрос тьютору или чужая реплика — 404. Текст длиннее
    ``notes.NOTE_MAX_CHARS`` отбивает pydantic (422) до записи.
    """
    async with ctx.lock:
        try:
            text = notes.save_note(ctx.conn, message_id, body.text, now=time.time())
        except LookupError as exc:
            raise HTTPException(status_code=404, detail={"message": NOTE_MISSING}) from exc
    return {"text": text}
