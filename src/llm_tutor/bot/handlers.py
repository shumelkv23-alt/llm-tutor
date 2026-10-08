"""Telegram-хендлеры бота.

Зависимости (соединение с БД, LLM-клиент, модель) внедряются через фабрику
`make_router`, чтобы логику можно было тестировать без живого aiogram.

Свободный текст идёт в ``core.turn.handle_turn`` — единый ход диалога
(ответ на задание либо тьюторский путь). Задания выдаются командой ``/task``,
ответ на них принимается кнопкой или текстом через состояние сессии в БД,
а не память процесса: рестарт бота заход не теряет.
"""

import logging
import sqlite3
import time

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from llm_tutor.bot import menu, render, themes
from llm_tutor.bot.survey import INTRO_TEXT, ask as ask_survey
from llm_tutor.config import Settings
from llm_tutor.core import verify
from llm_tutor.core.turn import (
    STALE_ITEM_REPLY,
    TurnReply,
    handle_turn,
    resume_reply,
    skip_pending,
    start_practice_reply,
)
from llm_tutor.db import repos
from llm_tutor.db.repos import (
    add_message,
    ensure_open_session,
    get_fact,
    get_open_session,
    get_session_state,
    update_session_state,
)
from llm_tutor.llm.client import LLMClient, LLMError
from llm_tutor.llm.prompts import (
    BOT_FAILURE_REPLY,
    BUSY_REPLY,
    LLM_FAILURE_REPLY,
)
from llm_tutor.llm.schemas import ChatMessage
from llm_tutor.schemas import Item
from llm_tutor.student.survey import is_completed as survey_completed

START_SYSTEM_PROMPT = (
    "Ты — тьютор по курсу машинного обучения (mlcourse.ai), тема 1 «Pandas / EDA». "
    "Поздоровайся коротко и предложи задать вопрос по теме."
)

# /start — команда, а не реплика ученика: в диалог пишем приветствие,
# чтобы история не засорялась литералом "/start".
START_GREETING = "Привет! Хочу начать учиться."

# Telegram отклоняет сообщения длиннее 4096 символов — оставляем запас.
MAX_REPLY_LENGTH = 4000

ANSWER_CALLBACK_PREFIX = "answer"

logger = logging.getLogger(__name__)


def _truncate(reply: str) -> str:
    """Режет ответ до безопасной длины сообщения Telegram."""
    if len(reply) > MAX_REPLY_LENGTH:
        return reply[:MAX_REPLY_LENGTH] + "…"
    return reply


async def build_start_reply(client: LLMClient, model: str, user_text: str) -> str:
    """Ответ на /start: приветствие модели. Сбои LLM ловятся здесь."""
    messages = [
        ChatMessage(role="system", content=START_SYSTEM_PROMPT),
        ChatMessage(role="user", content=user_text or "Привет!"),
    ]
    try:
        answer = await client.chat(messages, model=model)
    except LLMError:
        return LLM_FAILURE_REPLY
    return _truncate(f"[модель: {model}]\n\n{answer}")


def _persist_turn(
    conn: sqlite3.Connection, session_id: int, user_text: str, reply: str, ts: float
) -> None:
    """Пишет реплики хода и обновляет ``last_activity`` сессии."""
    add_message(conn, session_id, "user", user_text, ts=ts)
    add_message(conn, session_id, "assistant", reply, ts=ts)
    state = get_session_state(conn, session_id)
    update_session_state(conn, session_id, state.model_copy(update={"last_activity": ts}))


async def handle_start(
    conn: sqlite3.Connection,
    client: LLMClient,
    model: str,
    user_text: str,
    *,
    now: float | None = None,
) -> str:
    """Полный ход на /start: сессия + запись реплик + приветствие модели."""
    ts = time.time() if now is None else now
    reply = await build_start_reply(client, model, user_text)

    session_id = ensure_open_session(conn, ts)
    _persist_turn(conn, session_id, user_text, reply, ts)
    return reply


def _pending_item(conn: sqlite3.Connection) -> Item | None:
    """Задание, ответа на которое сейчас ждём (из состояния сессии).

    Сессию НЕ создаёт: это чтение, а не ход.
    """
    session_id = get_open_session(conn)
    if session_id is None:
        return None
    state = get_session_state(conn, session_id)
    if state.pending_item_id is None:
        return None
    return repos.get_item(conn, state.pending_item_id)


async def _run_turn(
    conn: sqlite3.Connection,
    client: LLMClient,
    model: str,
    user_text: str,
    *,
    settings: Settings | None,
    allow_intents: bool = True,
) -> TurnReply:
    """Ход с подстраховкой: неожиданный сбой не оставит ученика без ответа."""
    try:
        return await handle_turn(
            conn, client, model, user_text, settings=settings, allow_intents=allow_intents
        )
    except Exception:  # noqa: BLE001 — бот не должен молчать
        logger.exception("Неожиданный сбой хода")
        return TurnReply(text=LLM_FAILURE_REPLY)


def _options_keyboard(options: list[str] | None) -> InlineKeyboardMarkup | None:
    """Кнопки вариантов по подписям (``None``, если вариантов нет)."""
    if not options:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"{ANSWER_CALLBACK_PREFIX}:{index}",
                )
            ]
            for index, label in enumerate(options)
        ]
    )


def make_router(
    conn: sqlite3.Connection,
    client: LLMClient,
    model: str,
    *,
    settings: Settings | None = None,
) -> Router:
    """Собирает роутер с внедрёнными зависимостями (conn, client, model)."""
    router = Router()

    @router.message(CommandStart())
    async def on_start(message: Message, state: FSMContext) -> None:
        # Пока профиль не заполнен — сначала представление с меню, потом
        # короткая анкета (Срез 4.7, 12.4).
        if not survey_completed(conn):
            await message.answer(INTRO_TEXT, reply_markup=menu.main_menu())
            await ask_survey(message, state)
            return
        reply = await handle_start(conn, client, model, START_GREETING)
        await message.answer(
            render.fit(render.escape(reply)),
            reply_markup=menu.main_menu(),
            parse_mode=render.PARSE_MODE,
        )

    @router.message(Command("plan"))
    async def on_plan(message: Message) -> None:
        try:
            text = render.render_plan(conn, settings=settings)
        except Exception:  # noqa: BLE001 — команда не должна отвечать молчанием
            logger.exception("Сбой построения маршрута")
            text = BOT_FAILURE_REPLY
        await message.answer(text, parse_mode=render.PARSE_MODE)

    @router.message(Command("task"))
    async def on_task(message: Message, state: FSMContext) -> None:
        # Посреди анкеты или подбора маршрута задание не выдаём: иначе в
        # состоянии повиснет pending_item_id, конфликтующий с FSM-потоком.
        if await state.get_state() is not None:
            await message.answer(
                render.fit(render.escape(BUSY_REPLY)), parse_mode=render.PARSE_MODE
            )
            return
        try:
            reply = start_practice_reply(conn, settings=settings)
        except Exception:  # noqa: BLE001 — лучше сообщение, чем тишина
            logger.exception("Сбой выдачи задания")
            reply = TurnReply(text=BOT_FAILURE_REPLY)
        await message.answer(
            render.fit(render.escape(reply.text)),
            reply_markup=_options_keyboard(reply.options),
            parse_mode=render.PARSE_MODE,
        )

    @router.message(Command("close"))
    async def on_close(message: Message, state: FSMContext) -> None:
        # Посреди анкеты или подбора маршрута проверку не начинаем: в состоянии
        # сессии повиснет pending_item_id, конфликтующий с FSM-потоком.
        if await state.get_state() is not None:
            await message.answer(
                render.fit(render.escape(BUSY_REPLY)), parse_mode=render.PARSE_MODE
            )
            return
        try:
            reply = verify.start_verification(conn, settings=settings)
        except Exception:  # noqa: BLE001 — команда не должна отвечать молчанием
            logger.exception("Сбой проверочного прохода")
            await message.answer(
                render.fit(render.escape(BOT_FAILURE_REPLY)), parse_mode=render.PARSE_MODE
            )
            return
        await message.answer(
            render.fit(render.escape(reply.text)),
            reply_markup=_options_keyboard(reply.options),
            parse_mode=render.PARSE_MODE,
        )

    @router.message(Command("skip"))
    async def on_skip(message: Message) -> None:
        await message.answer(
            render.fit(render.escape(skip_pending(conn, settings=settings))),
            parse_mode=render.PARSE_MODE,
        )

    @router.message(Command("resume"))
    async def on_resume(message: Message) -> None:
        try:
            reply = await resume_reply(conn, client, model, settings=settings)
        except Exception:  # noqa: BLE001 — команда не должна отвечать молчанием
            logger.exception("Сбой продолжения занятия")
            await message.answer(
                render.fit(render.escape(BOT_FAILURE_REPLY)), parse_mode=render.PARSE_MODE
            )
            return
        await message.answer(
            render.fit(render.escape(reply.text)),
            reply_markup=_options_keyboard(reply.options),
            parse_mode=render.PARSE_MODE,
        )

    @router.message(Command("status"))
    async def on_status(message: Message) -> None:
        await message.answer(
            render.render_status(conn, settings=settings), parse_mode=render.PARSE_MODE
        )

    @router.message(Command("help"))
    async def on_help(message: Message) -> None:
        await message.answer(render.render_help(), parse_mode=render.PARSE_MODE)

    @router.message(Command("themes"))
    async def on_themes(message: Message) -> None:
        await message.answer(
            themes.THEMES_PROMPT,
            parse_mode=render.PARSE_MODE,
            reply_markup=themes.themes_keyboard(conn, settings=settings),
        )

    @router.callback_query(F.data.startswith(f"{ANSWER_CALLBACK_PREFIX}:"))
    async def on_answer(callback: CallbackQuery) -> None:
        # Ответ приходит из состояния сессии в БД — рестарт процесса его не теряет.
        item = _pending_item(conn)
        index = int((callback.data or "").split(":")[1])
        if item is None or not 0 <= index < len(item.options):
            await callback.message.answer(
                render.fit(render.escape(STALE_ITEM_REPLY)),
                parse_mode=render.PARSE_MODE,
            )
            await callback.answer()
            return
        # Подпись варианта — это ответ, а не реплика ученика: намерение из неё
        # не ловим, иначе вариант «Пропустить» ушёл бы в /skip.
        reply = await _run_turn(
            conn,
            client,
            model,
            item.options[index],
            settings=settings,
            allow_intents=False,
        )
        await callback.message.answer(
            render.fit(render.escape(reply.text)),
            reply_markup=_options_keyboard(reply.options),
            parse_mode=render.PARSE_MODE,
        )
        await callback.answer()

    # Разбор действий из инлайн-списка меню. Регистрируется ПОСЛЕ on_answer:
    # тесты выбирают хендлер ответа по индексу callback_query[0].
    @router.callback_query(F.data.startswith("menu:"))
    async def on_menu_action(callback: CallbackQuery, state: FSMContext) -> None:
        action = (callback.data or "").split(":", 1)[1]
        # Любой сбой действия — сообщение вместо тишины; callback.answer()
        # вызывается всегда (ниже), иначе у ученика зависает «часик».
        try:
            if action == "route":
                text = render.render_plan(conn, settings=settings)
                await callback.message.answer(text, parse_mode=render.PARSE_MODE)
            elif action == "themes":
                await callback.message.answer(
                    themes.THEMES_PROMPT,
                    parse_mode=render.PARSE_MODE,
                    reply_markup=themes.themes_keyboard(conn, settings=settings),
                )
            elif action == "close":
                # Посреди анкеты или подбора маршрута не начинаем: в состоянии
                # повиснет pending_item_id, конфликтующий с FSM-потоком.
                if await state.get_state() is not None:
                    await callback.message.answer(
                        render.fit(render.escape(BUSY_REPLY)), parse_mode=render.PARSE_MODE
                    )
                else:
                    reply = verify.start_verification(conn, settings=settings)
                    await callback.message.answer(
                        render.fit(render.escape(reply.text)),
                        parse_mode=render.PARSE_MODE,
                        reply_markup=_options_keyboard(reply.options),
                    )
            elif action == "resume":
                reply = await resume_reply(conn, client, model, settings=settings)
                await callback.message.answer(
                    render.fit(render.escape(reply.text)),
                    parse_mode=render.PARSE_MODE,
                    reply_markup=_options_keyboard(reply.options),
                )
        except Exception:  # noqa: BLE001 — действие не должно отвечать молчанием
            logger.exception("Сбой действия меню: %s", action)
            await callback.message.answer(
                render.fit(render.escape(BOT_FAILURE_REPLY)),
                parse_mode=render.PARSE_MODE,
            )
        await callback.answer()

    # Кнопка меню открывает инлайн-список действий. Регистрируется ДО on_text,
    # иначе текст кнопки ушёл бы в тьюторский ход.
    @router.message(StateFilter(None), F.text == menu.LABEL_MENU)
    async def on_menu(message: Message) -> None:
        await message.answer(
            menu.MENU_TITLE,
            reply_markup=menu.actions_keyboard(),
        )

    # Обработчик свободного текста: не трогает команды (иначе CommandStop был
    # бы перехвачен) и не лезет в незавершённые FSM-потоки (анкета, диагностика) —
    # их шаги обрабатывают свои роутеры.
    @router.message(StateFilter(None), F.text & ~F.text.startswith("/"))
    async def on_text(message: Message) -> None:
        reply = await _run_turn(conn, client, model, message.text or "", settings=settings)
        await message.answer(
            render.fit(render.escape(reply.text)),
            # Варианты ответа (инлайн) в приоритете; иначе — постоянная кнопка меню.
            reply_markup=_options_keyboard(reply.options) or menu.main_menu(),
            parse_mode=render.PARSE_MODE,
        )

    return router
