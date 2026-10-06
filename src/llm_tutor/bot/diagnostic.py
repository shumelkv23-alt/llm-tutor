"""Поток диагностики в Telegram (Срез 4.9): «подбор маршрута», а не экзамен.

Спрашиваем по одному заданию, ответ проверяет код (autocheck), после каждого
ответа обновляем модель ученика и берём следующее задание. Заход короткий:
первый шире, последующие — по паре вопросов.
"""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from llm_tutor.config import Settings
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.schemas import Item
from llm_tutor.student import diagnostic

CALLBACK_PREFIX = "diag"

INTRO_REPLY = "Подберу маршрут: пара коротких вопросов, это не экзамен."
CORRECT_REPLY = "Верно ✓"
WRONG_REPLY = "Не совсем ✗ — ничего страшного, это и нужно было выяснить."
EMPTY_GRAPH_REPLY = "Граф курса пуст. Загрузи seed: python -m llm_tutor.course.seed"
NO_QUESTIONS_REPLY = "Спрашивать пока нечего — по всем доступным узлам картина уже есть."
BROKEN_STATE_REPLY = "Состояние захода потерялось — начнём заново, жми /diagnostic."
CHOOSE_BUTTON_REPLY = "Выбери, пожалуйста, один из вариантов кнопкой ниже 👇"
WRITE_TEXT_REPLY = "Здесь нужен короткий ответ текстом — напиши его сообщением."


class DiagnosticFlow(StatesGroup):
    """Ждём ответа на текущее задание."""

    answering = State()


def _choice_keyboard(item: Item) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=option, callback_data=f"{CALLBACK_PREFIX}:{index}")]
            for index, option in enumerate(item.options)
        ]
    )


async def start(message: Message, state: FSMContext, conn, settings: Settings) -> None:
    """Начинает заход диагностики."""
    graph = CourseGraph.load(conn)
    if not graph.node_ids:
        await message.answer(EMPTY_GRAPH_REPLY)
        return

    await state.set_state(DiagnosticFlow.answering)
    await state.update_data(
        left=diagnostic.questions_for_pass(conn, settings=settings),
        asked=[],
        correct=0,
        total=0,
    )
    await message.answer(INTRO_REPLY)
    await _ask_next(message, state, conn, settings)


async def _ask_next(target: Message, state: FSMContext, conn, settings: Settings) -> None:
    """Задаёт следующее задание или завершает заход."""
    data = await state.get_data()
    if data["left"] <= 0:
        await _finish(target, state, data)
        return

    graph = CourseGraph.load(conn)
    question = diagnostic.next_question(
        conn, graph, asked_item_ids=frozenset(data["asked"]), settings=settings
    )
    if question is None:
        await _finish(target, state, data)
        return

    await state.update_data(
        item_id=question.item.id,
        concept_id=question.concept_id,
        # Помним, какой ответ ждём: чужой тип ввода нельзя записывать ответом.
        expects_choice=bool(question.item.options),
    )
    keyboard = _choice_keyboard(question.item) if question.item.options else None
    await target.answer(question.item.prompt, reply_markup=keyboard)


def _mismatch_hint(data: dict, *, via_keyboard: bool) -> str | None:
    """Подсказка, если тип ввода не совпал с типом текущего задания.

    Состояние без текущего задания — не «не тот тип ввода», а потерянный
    заход: им занимается ``_record``.
    """
    if not data.get("item_id"):
        return None
    if data.get("expects_choice", False) == via_keyboard:
        return None
    return CHOOSE_BUTTON_REPLY if via_keyboard is False else WRITE_TEXT_REPLY


async def _record(
    target: Message, state: FSMContext, conn, settings: Settings, answer: str
) -> None:
    """Проверяет ответ, обновляет модель ученика и задаёт следующее задание."""
    data = await state.get_data()
    item = repos.get_item(conn, data.get("item_id", -1))
    if item is None:
        await state.clear()
        await target.answer(BROKEN_STATE_REPLY)
        return

    result = diagnostic.record_answer(
        conn,
        CourseGraph.load(conn),
        diagnostic.DiagnosticQuestion(item=item, concept_id=data["concept_id"]),
        answer,
        settings=settings,
    )
    passed = result.score >= diagnostic.SUCCESS_SCORE
    await state.update_data(
        asked=[*data["asked"], item.id],
        left=data["left"] - 1,
        correct=data["correct"] + int(passed),
        total=data["total"] + 1,
    )
    await target.answer(CORRECT_REPLY if passed else WRONG_REPLY)
    await _ask_next(target, state, conn, settings)


async def _finish(target: Message, state: FSMContext, data: dict) -> None:
    """Завершает заход и показывает краткий итог."""
    await state.clear()
    if data["total"] == 0:
        await target.answer(NO_QUESTIONS_REPLY)
        return
    await target.answer(
        f"Готово: {data['correct']} из {data['total']}. "
        "Маршрут обновлён — смотри /plan."
    )


def make_diagnostic_router(conn, settings: Settings) -> Router:
    """Роутер диагностики: команда, ответы кнопками и текстом."""
    router = Router()

    @router.message(Command("diagnostic"))
    async def on_diagnostic(message: Message, state: FSMContext) -> None:
        await start(message, state, conn, settings)

    @router.callback_query(
        DiagnosticFlow.answering, F.data.startswith(f"{CALLBACK_PREFIX}:")
    )
    async def on_choice(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        hint = _mismatch_hint(await state.get_data(), via_keyboard=True)
        if hint is not None:
            await callback.message.answer(hint)
            return
        await _record(
            callback.message, state, conn, settings, (callback.data or "").split(":")[1]
        )

    @router.message(DiagnosticFlow.answering, F.text, ~F.text.startswith("/"))
    async def on_text(message: Message, state: FSMContext) -> None:
        # Ответ есть ответ только на вопрос того же типа: иначе болтовня
        # засчиталась бы как неверный ответ и съела слот захода.
        hint = _mismatch_hint(await state.get_data(), via_keyboard=False)
        if hint is not None:
            await message.answer(hint)
            return
        await _record(message, state, conn, settings, message.text or "")

    return router
