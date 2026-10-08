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

from llm_tutor.bot import onboarding
from llm_tutor.config import Settings
from llm_tutor.student import survey

CALLBACK_PREFIX = "survey"

INTRO_TEXT = (
    "👋 Привет! Я тьютор по теме 1 курса mlcourse.ai — «Pandas / EDA».\n\n"
    "Что будет:\n"
    "  1 · спрошу пару вопросов о тебе — это 30 секунд\n"
    "  2 · соберу маршрут под твою цель и покажу его\n"
    "  3 · дальше ведём по шагам: объясняю → даю задачу → проверяю\n\n"
    "Если что-то из темы уже знаешь — скажешь, перестрою.\n\n"
    "Поехали 👇"
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
        await callback.answer()
        # Анкета не закрывает поток, а открывает экран согласования маршрута:
        # ученик ещё может сказать, что часть тем уже знает (Срез 18).
        await onboarding.show_route_screen(callback.message, state, conn, settings)
        return

    @router.message(SurveyFlow.question, F.text, ~F.text.startswith("/"))
    async def on_text(message: Message) -> None:
        # Анкета принимает только нажатия: иначе напечатанный «1» пропадал
        # в тишину (тьютор-путь отсечён StateFilter(None)).
        await message.answer(BUTTON_HINT_REPLY)

    return router
