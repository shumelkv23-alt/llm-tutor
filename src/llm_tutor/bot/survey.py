"""Анкета холодного старта в Telegram (Срез 4.7): инлайн-кнопки.

Ответы копятся в FSM, а результаты пишутся в БД только в конце — анкету можно
прервать на любом вопросе, ничего не сломав.
"""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from llm_tutor.config import Settings
from llm_tutor.student import survey

CALLBACK_PREFIX = "survey"

SURVEY_DONE_REPLY = (
    "Спасибо! Профиль заполнен.\n"
    "Могу подобрать маршрут — жми /diagnostic (это не экзамен, а пара коротких вопросов)."
)
BUTTON_HINT_REPLY = "Выбери, пожалуйста, один из вариантов кнопкой ниже 👇"


class SurveyFlow(StatesGroup):
    """Анкета: ждём нажатия кнопки на текущем вопросе."""

    question = State()


def _keyboard(question: survey.SurveyQuestion) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=option.label,
                    callback_data=f"{CALLBACK_PREFIX}:{index}",
                )
            ]
            for index, option in enumerate(question.options)
        ]
    )


async def ask(message: Message, state: FSMContext, index: int = 0) -> None:
    """Задаёт вопрос анкеты и переводит FSM в ожидание ответа."""
    question = survey.SURVEY_QUESTIONS[index]
    await state.set_state(SurveyFlow.question)
    await state.update_data(index=index, answers={})
    await message.answer(question.text, reply_markup=_keyboard(question))


def make_survey_router(conn, settings: Settings) -> Router:
    """Роутер анкеты: обработка нажатий на кнопки вариантов."""
    router = Router()

    @router.callback_query(SurveyFlow.question, F.data.startswith(f"{CALLBACK_PREFIX}:"))
    async def on_answer(callback: CallbackQuery, state: FSMContext) -> None:
        data = await state.get_data()
        index = data["index"]
        answers = dict(data.get("answers", {}))
        answers[survey.SURVEY_QUESTIONS[index].key] = int((callback.data or "").split(":")[1])

        if index + 1 < len(survey.SURVEY_QUESTIONS):
            await state.update_data(index=index + 1, answers=answers)
            next_question = survey.SURVEY_QUESTIONS[index + 1]
            await callback.message.answer(
                next_question.text, reply_markup=_keyboard(next_question)
            )
            await callback.answer()
            return

        survey.apply_answers(conn, answers, settings=settings)
        await state.clear()
        await callback.message.answer(SURVEY_DONE_REPLY)
        await callback.answer()

    @router.message(SurveyFlow.question, F.text, ~F.text.startswith("/"))
    async def on_text(message: Message) -> None:
        # Анкета принимает только нажатия: иначе напечатанный «1» пропадал
        # в тишину (тьютор-путь отсечён StateFilter(None)).
        await message.answer(BUTTON_HINT_REPLY)

    return router
