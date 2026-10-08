"""Анкета холодного старта в Telegram (Срез 4.7): инлайн-кнопки.

Ответы копятся в FSM, а результаты пишутся в БД только в конце — анкету можно
прервать на любом вопросе, ничего не сломав.
"""

from collections.abc import Mapping

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from llm_tutor.bot import render, start
from llm_tutor.config import Settings
from llm_tutor.llm.client import LLMClient
from llm_tutor.student import survey

CALLBACK_PREFIX = "survey"

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


GO = "go"
BACK = "back"
GO_DATA = f"{CALLBACK_PREFIX}:{GO}"
GO_LABEL = "▶️ Поехали"
BACK_LABEL = "‹ Назад"


def _button(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _data(step: int, choice: int | str) -> str:
    """``callback_data`` с номером шага: клик по старой клавиатуре узнаваем."""
    return f"{CALLBACK_PREFIX}:{step}:{choice}"


def intro_view() -> tuple[str, InlineKeyboardMarkup]:
    """Приветствие с единственной кнопкой «▶️ Поехали»."""
    return start.INTRO_TEXT, InlineKeyboardMarkup(
        inline_keyboard=[[_button(GO_LABEL, GO_DATA)]]
    )


def question_view(progress: survey.Progress) -> tuple[str, InlineKeyboardMarkup]:
    """Текущий вопрос (готовый HTML) и его клавиатура.

    Вызывать только на незаконченной анкете: у законченной вопроса нет.
    """
    key = progress.next_key()
    step = progress.step
    if key == survey.LEVEL_KEY:
        text = render.escape(survey.LEVEL_QUESTION)
        rows = [
            [_button(label, _data(step, index))]
            for index, label in enumerate(survey.LEVEL_OPTIONS)
        ]
    else:
        block = survey.block_by_key(key)
        number, total = progress.position()
        bar = "▰" * number + "▱" * (total - number)
        text = (
            f"Вопрос {number} из {total}  {bar}\n\n"
            f"<b>{render.escape(block.question)}</b>\n{render.escape(block.example)}"
        )
        buttons = [
            _button(label, _data(step, index))
            for index, label in enumerate(survey.SELF_LEVELS)
        ]
        rows = [buttons[:2], buttons[2:]]
    if step > 0:
        rows.append([_button(BACK_LABEL, _data(step, BACK))])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def summary_text(answers: Mapping[str, int]) -> str:
    """Сводка «как я тебя понял» (готовый HTML) — финал сообщения анкеты."""
    lines = [
        f"• {render.escape(block.title)} — {survey.SELF_LEVELS[answers[block.key]].lower()}"
        for block in survey.BLOCKS
    ]
    return "✅ Понял тебя:\n" + "\n".join(lines)


async def ask(message: Message, state: FSMContext, index: int = 0) -> None:
    """Задаёт вопрос анкеты и переводит FSM в ожидание ответа."""
    question = survey.SURVEY_QUESTIONS[index]
    await state.set_state(SurveyFlow.question)
    await state.update_data(index=index, answers={})
    await message.answer(question.text, reply_markup=_keyboard(question))


def make_survey_router(
    conn, settings: Settings, client: LLMClient, model: str
) -> Router:
    """Роутер анкеты: обработка нажатий на кнопки вариантов."""
    router = Router()

    @router.callback_query(SurveyFlow.question, F.data.startswith(f"{CALLBACK_PREFIX}:"))
    async def on_answer(callback: CallbackQuery, state: FSMContext) -> None:
        data = await state.get_data()
        index = data["index"]
        question = survey.SURVEY_QUESTIONS[index]
        # Данные колбэка подконтрольны клиенту: мусор и вариант вне списка не
        # должны ронять хендлер — иначе ученик не получит ни ответа, ни вопроса.
        try:
            choice = int((callback.data or "").split(":")[1])
        except (IndexError, ValueError):
            choice = -1
        if not 0 <= choice < len(question.options):
            await callback.message.answer(
                BUTTON_HINT_REPLY, reply_markup=_keyboard(question)
            )
            await callback.answer()
            return

        answers = dict(data.get("answers", {}))
        answers[question.key] = choice

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
        # Дальше не экран согласования, а список ближайших шагов и первый урок.
        await start.begin_lesson(
            callback.message, state, conn, client, model, settings
        )
        return

    @router.message(SurveyFlow.question, F.text, ~F.text.startswith("/"))
    async def on_text(message: Message) -> None:
        # Анкета принимает только нажатия: иначе напечатанный «1» пропадал
        # в тишину (тьютор-путь отсечён StateFilter(None)).
        await message.answer(BUTTON_HINT_REPLY)

    return router
