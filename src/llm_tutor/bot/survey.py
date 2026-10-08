"""Анкета холодного старта в Telegram: одно сообщение, которое правится на месте.

Приветствие, вопросы и сводка — одно сообщение: нажатие правит его, а не
шлёт новое, поэтому в истории не копятся живые клавиатуры. Ответы копятся в
FSM, в БД пишутся только в финале — анкету можно прервать на любом шаге.

Нажатие привязано к сообщению (``message_id`` в FSM) и к шагу (номер в
``callback_data``): клик по старой клавиатуре и двойной тап ничего не пишут.
"""

import logging
from collections.abc import Mapping

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
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
from llm_tutor.llm.prompts import BUSY_REPLY
from llm_tutor.student import survey

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = "survey"
GO = "go"
BACK = "back"
GO_DATA = f"{CALLBACK_PREFIX}:{GO}"
GO_LABEL = "▶️ Поехали"
BACK_LABEL = "‹ Назад"

BUTTON_HINT_REPLY = "Ответь кнопкой в сообщении выше 👆"
STALE_CLICK_TOAST = "Этот вопрос уже позади"
DONE_TOAST = "Анкета уже пройдена"
CLAIMED_NOTE = "Знакомое не пропускаю — в конце проверим коротким тестом."


class SurveyFlow(StatesGroup):
    """Анкета: ждём нажатия кнопки в сообщении анкеты."""

    question = State()


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
    text = "✅ Понял тебя:\n" + "\n".join(lines)
    if any(index == survey.CONFIDENT_INDEX for index in answers.values()):
        text = f"{text}\n\n{CLAIMED_NOTE}"
    return text


def _progress(data: Mapping) -> survey.Progress | None:
    """Ход анкеты из FSM; ``None`` — приветствие показано, «Поехали» не нажато."""
    given = data.get("given")
    if given is None:
        return None
    return survey.Progress(given=tuple((key, index) for key, index in given))


def _stored(progress: survey.Progress) -> list[list]:
    """Ход анкеты в виде для FSM (списки — сериализуемы любым хранилищем)."""
    return [[key, index] for key, index in progress.given]


def _parse(data: str | None) -> tuple[str, str | None]:
    """``survey:go`` → (``go``, None); ``survey:<шаг>:<выбор>`` → (шаг, выбор)."""
    parts = (data or "").split(":")
    if parts[1:] == [GO]:
        return GO, None
    if len(parts) == 3:
        return parts[1], parts[2]
    return "", None


async def safe_edit(
    message: Message, text: str, markup: InlineKeyboardMarkup | None
) -> Message:
    """Правит сообщение; если править нельзя — шлёт новое с тем же содержимым.

    «Не изменилось» — не ошибка (повторная отрисовка того же шага). Остальные
    отказы (сообщение старое или удалено) не должны оставить ученика без
    вопроса: он приходит новым сообщением, и вызывающий перепривязывает FSM.
    """
    try:
        await message.edit_text(text, reply_markup=markup, parse_mode=render.PARSE_MODE)
    except TelegramBadRequest as error:
        if "message is not modified" in str(error):
            return message
        logger.info("Сообщение анкеты не править (%s) — шлю новое", error)
        return await message.answer(text, reply_markup=markup, parse_mode=render.PARSE_MODE)
    return message


async def _drop_keyboard(message: Message) -> None:
    """Снимает кнопки со старого сообщения анкеты."""
    try:
        await message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest as error:
        # Кнопки уже сняты или сообщение не править — тост ученик уже видел.
        logger.info("Кнопки анкеты не снять: %s", error)


async def start_survey(message: Message, state: FSMContext) -> Message:
    """Новое сообщение-приветствие с «Поехали»; анкета привязывается к нему.

    Повторный вызов (``/start`` посреди анкеты) перепривязывает FSM к новому
    сообщению — нажатия в старом упрутся в тост.
    """
    text, markup = intro_view()
    sent = await message.answer(text, reply_markup=markup, parse_mode=render.PARSE_MODE)
    await state.set_state(SurveyFlow.question)
    await state.set_data({"message_id": sent.message_id, "given": None})
    return sent


def make_survey_router(
    conn, settings: Settings, client: LLMClient, model: str
) -> Router:
    """Роутер анкеты: все нажатия ``survey:*`` и текст посреди анкеты."""
    router = Router()

    async def _finish(
        callback: CallbackQuery, state: FSMContext, progress: survey.Progress
    ) -> None:
        answers = progress.final_answers()
        # Запись и снятие FSM — ДО сетевых вызовов: второй параллельный тап
        # увидит пройденную анкету, а не запустит урок ещё раз.
        survey.apply_answers(conn, answers, level=progress.level, settings=settings)
        await state.clear()
        await callback.answer()
        await safe_edit(callback.message, summary_text(answers), None)
        await start.begin_lesson(callback.message, state, conn, client, model, settings)

    async def _orphan_click(callback: CallbackQuery, state: FSMContext) -> None:
        """Нажатие не в живом сообщении анкеты: тост или новый старт."""
        current = await state.get_state()
        if survey.is_completed(conn):
            await callback.answer(DONE_TOAST)
            await _drop_keyboard(callback.message)
        elif current == SurveyFlow.question.state:
            # Анкета идёт в другом сообщении — это старое.
            await callback.answer(STALE_CLICK_TOAST)
        elif current is not None:
            await callback.answer(BUSY_REPLY)
        else:
            # FSM потерян (рестарт бота), анкета не пройдена: начинаем заново
            # в этом же сообщении, а не молчим.
            await state.set_state(SurveyFlow.question)
            await state.set_data({"message_id": callback.message.message_id, "given": []})
            await callback.answer()
            text, markup = question_view(survey.Progress())
            await safe_edit(callback.message, text, markup)

    @router.callback_query(F.data.startswith(f"{CALLBACK_PREFIX}:"))
    async def on_survey_click(callback: CallbackQuery, state: FSMContext) -> None:
        # Всё до первого сетевого await — неделимая часть: MemoryStorage не
        # уступает цикл, поэтому второй параллельный тап (апдейты идут
        # задачами, handle_as_tasks=True) увидит уже продвинутый шаг. При смене
        # хранилища на сетевое — пересмотреть.
        message = callback.message
        data = await state.get_data()
        bound = (
            await state.get_state() == SurveyFlow.question.state
            and data.get("message_id") == message.message_id
        )
        if not bound:
            await _orphan_click(callback, state)
            return

        action, choice = _parse(callback.data)
        progress = _progress(data)
        if action == GO:
            if progress is not None:  # «Поехали» уже нажимали
                await callback.answer(STALE_CLICK_TOAST)
                return
            progress = survey.Progress()
        elif progress is None or action != str(progress.step):
            await callback.answer(STALE_CLICK_TOAST)
            return
        elif choice == BACK:
            progress = progress.back()
        else:
            try:
                progress = progress.answer(int(choice or ""))
            except ValueError:
                # Данные колбэка подконтрольны клиенту: мусор — не ответ.
                await callback.answer(STALE_CLICK_TOAST)
                return

        if progress.next_key() is None:
            await _finish(callback, state, progress)
            return
        await state.update_data(given=_stored(progress))
        await callback.answer()
        text, markup = question_view(progress)
        shown = await safe_edit(message, text, markup)
        if shown.message_id != message.message_id:
            await state.update_data(message_id=shown.message_id)

    @router.message(SurveyFlow.question, F.text, ~F.text.startswith("/"))
    async def on_text(message: Message) -> None:
        # Анкета принимает только нажатия: иначе напечатанное пропадало бы в
        # тишину (тьютор-путь отсечён StateFilter(None)).
        await message.answer(BUTTON_HINT_REPLY)

    return router
