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
from llm_tutor.bot import start
from llm_tutor.bot.chat_action import typing_action
from llm_tutor.bot.survey import start_survey
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
from llm_tutor.llm.client import LLMClient
from llm_tutor.llm.prompts import (
    BOT_FAILURE_REPLY,
    BUSY_REPLY,
    LLM_FAILURE_REPLY,
)
from llm_tutor.schemas import Item
from llm_tutor.student.survey import is_completed as survey_completed

# /start — команда, а не реплика ученика: в диалог пишем приветствие,
# чтобы история не засорялась литералом "/start".
START_GREETING = "Привет! Хочу начать учиться."

ANSWER_CALLBACK_PREFIX = "answer"

logger = logging.getLogger(__name__)


def _persist_turn(
    conn: sqlite3.Connection, session_id: int, user_text: str, reply: str, ts: float
) -> None:
    """Пишет реплики хода и обновляет ``last_activity`` сессии."""
    add_message(conn, session_id, "user", user_text, ts=ts)
    add_message(conn, session_id, "assistant", reply, ts=ts)
    state = get_session_state(conn, session_id)
    update_session_state(conn, session_id, state.model_copy(update={"last_activity": ts}))


def handle_start(
    conn: sqlite3.Connection,
    user_text: str,
    *,
    now: float | None = None,
) -> str:
    """Полный ход на /start вернувшегося: сессия + журнал + «С возвращением».

    Модель не зовём: приветствие не несёт содержания, а LLM-вызов стоил бы
    секунд ожидания на каждый /start.
    """
    ts = time.time() if now is None else now
    reply = start.welcome_back_text(conn)
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


def _options_keyboard(
    conn: sqlite3.Connection, options: list[str] | None
) -> InlineKeyboardMarkup | None:
    """Кнопки вариантов по подписям (``None``, если вариантов нет).

    В колбэк кладём и id задания: сообщение остаётся в переписке, и клик по
    старой клавиатуре иначе засчитался бы как ответ на ТЕКУЩЕЕ задание —
    свидетельство за ответ, которого ученик не давал.
    """
    if not options:
        return None
    item = _pending_item(conn)
    item_id = item.id if item is not None else 0
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"{ANSWER_CALLBACK_PREFIX}:{item_id}:{index}",
                )
            ]
            for index, label in enumerate(options)
        ]
    )


async def _send_reply(
    conn: sqlite3.Connection, message: Message, reply: TurnReply
) -> None:
    """Отправляет ответ хода: текст, а следом — задание отдельным сообщением.

    Объяснение и тест в одном сообщении читаются стеной, поэтому ход с
    ``tail`` уходит двумя сообщениями; варианты ответа прикрепляются к тому из
    них, которое несёт задание.
    """
    await message.answer(
        render.fit(render.escape(reply.text)),
        reply_markup=None if reply.tail else _options_keyboard(conn, reply.options),
        parse_mode=render.PARSE_MODE,
    )
    if reply.tail:
        await message.answer(
            render.fit(render.escape(reply.tail)),
            reply_markup=_options_keyboard(conn, reply.options),
            parse_mode=render.PARSE_MODE,
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
        # Пока профиль не заполнен — одно сообщение-приветствие с «Поехали»:
        # дальше анкета живёт в нём же.
        if not survey_completed(conn):
            await start_survey(message, state)
            return
        reply = handle_start(conn, START_GREETING)
        # Повторный /start — надёжный выход из незакрытого потока (анкета,
        # диагностика): иначе ученик остался бы в нём, а
        # «Продолжить» отвечала бы «сначала закончим текущий шаг».
        await state.clear()
        # Вернувшемуся ученику — главное действие в один тап; постоянная
        # клавиатура и так висит, переприкреплять её не нужно.
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
        await _send_reply(conn, message, reply)

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
        await _send_reply(conn, message, reply)

    @router.message(Command("skip"))
    async def on_skip(message: Message) -> None:
        try:
            text = skip_pending(conn, settings=settings)
        except Exception:  # noqa: BLE001 — команда не должна отвечать молчанием
            logger.exception("Сбой пропуска задания")
            text = BOT_FAILURE_REPLY
        await message.answer(
            render.fit(render.escape(text)),
            reply_markup=menu.main_menu(),
            parse_mode=render.PARSE_MODE,
        )

    @router.message(Command("resume"))
    async def on_resume(message: Message, state: FSMContext) -> None:
        # Посреди анкеты или подбора маршрута занятие не продолжаем: в состоянии
        # сессии повиснет pending_item_id, конфликтующий с FSM-потоком.
        if await state.get_state() is not None:
            await message.answer(
                render.fit(render.escape(BUSY_REPLY)), parse_mode=render.PARSE_MODE
            )
            return
        try:
            async with typing_action(message):
                reply = await resume_reply(conn, client, model, settings=settings)
        except Exception:  # noqa: BLE001 — команда не должна отвечать молчанием
            logger.exception("Сбой продолжения занятия")
            await message.answer(
                render.fit(render.escape(BOT_FAILURE_REPLY)), parse_mode=render.PARSE_MODE
            )
            return
        await _send_reply(conn, message, reply)

    @router.message(Command("status"))
    async def on_status(message: Message) -> None:
        try:
            text = render.render_status(conn, settings=settings)
        except Exception:  # noqa: BLE001 — команда не должна отвечать молчанием
            logger.exception("Сбой дашборда")
            text = BOT_FAILURE_REPLY
        await message.answer(text, parse_mode=render.PARSE_MODE)

    @router.message(Command("help"))
    async def on_help(message: Message) -> None:
        await message.answer(render.render_help(), parse_mode=render.PARSE_MODE)

    @router.message(Command("themes"))
    async def on_themes(message: Message) -> None:
        try:
            keyboard = themes.themes_keyboard(conn, settings=settings)
        except Exception:  # noqa: BLE001 — команда не должна отвечать молчанием
            logger.exception("Сбой списка тем")
            await message.answer(
                render.fit(render.escape(BOT_FAILURE_REPLY)), parse_mode=render.PARSE_MODE
            )
            return
        await message.answer(
            themes.THEMES_PROMPT,
            parse_mode=render.PARSE_MODE,
            reply_markup=keyboard,
        )

    @router.callback_query(F.data.startswith(f"{ANSWER_CALLBACK_PREFIX}:"))
    async def on_answer(callback: CallbackQuery) -> None:
        # Ответ приходит из состояния сессии в БД — рестарт процесса его не теряет.
        item = _pending_item(conn)
        # Данные колбэка подконтрольны клиенту: мусор и клик по старой
        # клавиатуре должны упираться в «задание неактуально», а не отвечать
        # за текущее задание.
        try:
            item_id, index = (int(part) for part in (callback.data or "").split(":")[1:3])
        except ValueError:
            item_id, index = -1, -1
        if item is None or item.id != item_id or not 0 <= index < len(item.options):
            await callback.message.answer(
                render.fit(render.escape(STALE_ITEM_REPLY)),
                parse_mode=render.PARSE_MODE,
            )
            await callback.answer()
            return
        # «Часики» гасим сразу: дальше ждём модель, и ученик видит «печатает…».
        await callback.answer()
        # Подпись варианта — это ответ, а не реплика ученика: намерение из неё
        # не ловим, иначе вариант «Пропустить» ушёл бы в /skip.
        async with typing_action(callback.message):
            reply = await _run_turn(
                conn,
                client,
                model,
                item.options[index],
                settings=settings,
                allow_intents=False,
            )
        await _send_reply(conn, callback.message, reply)

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
                    await _send_reply(conn, callback.message, reply)
        except Exception:  # noqa: BLE001 — действие не должно отвечать молчанием
            logger.exception("Сбой действия меню: %s", action)
            await callback.message.answer(
                render.fit(render.escape(BOT_FAILURE_REPLY)),
                parse_mode=render.PARSE_MODE,
            )
        await callback.answer()

    # Текст «▶️ Старт» — reply-кнопка прошлой версии, у старых учеников она
    # ещё висит: ведёт на вход, как /start.
    @router.message(StateFilter(None), F.text == start.START_LABEL)
    async def on_start_button(message: Message, state: FSMContext) -> None:
        await on_start(message, state)

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
    async def on_text(message: Message, state: FSMContext) -> None:
        # Анкета не пройдена — маршрута ещё нет: вести занятие не по чему.
        # Вместо отказа — то же приветствие с «Поехали», что и на /start.
        if not survey_completed(conn):
            await start_survey(message, state)
            return
        async with typing_action(message):
            reply = await _run_turn(conn, client, model, message.text or "", settings=settings)
        await _send_reply(conn, message, reply)

    return router
