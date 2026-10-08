"""Тесты FSM-потоков бота: анкета и диагностика (Срез 4.5)."""

import asyncio
from datetime import datetime

import pytest
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Chat, Message, Update, User

from fakes import FakeCallback, FakeMessage, NullSession, _fsm, _named

from llm_tutor.bot import handlers, menu
from llm_tutor.bot.diagnostic import DiagnosticFlow, make_diagnostic_router
from llm_tutor.bot.handlers import make_router
from llm_tutor.bot import start
from llm_tutor.bot import survey as survey_bot
from llm_tutor.bot.survey import SurveyFlow, start_survey
from llm_tutor.bot.survey import make_survey_router
from llm_tutor.core.turn import TurnReply
from llm_tutor.course.graph import CourseGraph
from llm_tutor.course.seed import load_seed
from llm_tutor.db import repos
from llm_tutor.llm.prompts import BUSY_REPLY
from llm_tutor.student import beta, survey


def _complete_survey(conn) -> None:
    """Профиль заполнен: без этого свободный текст упирается в приглашение."""
    for block in survey.BLOCKS:
        repos.set_fact(conn, block.key, survey.SELF_LEVELS[2], source="self")


def _handler(router, kind: str, index: int) -> object:
    """Достаёт callback зарегистрированного хендлера (фильтры обходим)."""
    return getattr(router, kind).handlers[index].callback


# --- анкета ---


async def _begin(conn, settings, client=None):
    """Приветствие анкеты и хендлер нажатий."""
    router = make_survey_router(conn, settings, client or _TutorClient(), "m")
    state = _fsm()
    intro = await start_survey(FakeMessage(), state)
    return intro, state, _named(router, "callback_query", "on_survey_click")


async def _tap(click, message, state, data: str) -> FakeCallback:
    callback = FakeCallback(data, message)
    await click(callback, state)
    return callback


def _data_of(message, label: str) -> str:
    """``callback_data`` кнопки с подписью ``label`` в клавиатуре сообщения."""
    for row in message.reply_markup.inline_keyboard:
        for button in row:
            if button.text == label:
                return button.callback_data
    raise AssertionError(f"нет кнопки {label!r}")


async def _press(click, message, state, label: str) -> FakeCallback:
    return await _tap(click, message, state, _data_of(message, label))


async def test_start_survey_sends_intro_with_go_button(conn, settings) -> None:
    message = FakeMessage()
    state = _fsm()

    await start_survey(message, state)

    text, markup = message.sent[-1]
    assert text == start.INTRO_TEXT
    assert markup.inline_keyboard[0][0].callback_data == survey_bot.GO_DATA
    assert await state.get_state() == SurveyFlow.question.state


async def test_go_turns_intro_into_level_question(conn, settings) -> None:
    """«Поехали» правит то же сообщение — нового не приходит."""
    load_seed(conn)
    intro, state, click = await _begin(conn, settings)

    await _press(click, intro, state, survey_bot.GO_LABEL)

    assert survey.LEVEL_QUESTION in intro.text
    assert intro.sent == []


async def test_answer_edits_same_message_to_next_question(conn, settings) -> None:
    load_seed(conn)
    intro, state, click = await _begin(conn, settings)
    await _press(click, intro, state, survey_bot.GO_LABEL)

    await _press(click, intro, state, survey.LEVEL_OPTIONS[survey.LEVEL_SOME])

    assert "Вопрос 1 из 5" in intro.text
    assert intro.sent == []


async def test_callback_is_answered_before_edit(conn, settings) -> None:
    """«Часики» гаснут сразу, правка сообщения — после."""
    load_seed(conn)
    intro, state, click = await _begin(conn, settings)
    intro.log.clear()

    await _press(click, intro, state, survey_bot.GO_LABEL)

    assert intro.log == ["callback", "edit"]


async def test_stale_step_click_writes_nothing(conn, settings) -> None:
    """Клик по клавиатуре прошлого шага — тост, ответ не записан."""
    load_seed(conn)
    intro, state, click = await _begin(conn, settings)
    await _press(click, intro, state, survey_bot.GO_LABEL)
    old = _data_of(intro, survey.LEVEL_OPTIONS[survey.LEVEL_SOME])
    await _tap(click, intro, state, old)

    callback = await _tap(click, intro, state, old)

    assert callback.answer_text == survey_bot.STALE_CLICK_TOAST
    assert (await state.get_data())["given"] == [[survey.LEVEL_KEY, survey.LEVEL_SOME]]


async def test_click_on_previous_survey_message_is_stale(conn, settings) -> None:
    """После повторного /start старое сообщение анкеты новое не сбивает."""
    load_seed(conn)
    intro, state, click = await _begin(conn, settings)
    await _press(click, intro, state, survey_bot.GO_LABEL)
    await start_survey(FakeMessage(), state)  # /start посреди анкеты

    callback = await _press(click, intro, state, survey.LEVEL_OPTIONS[0])

    assert callback.answer_text == survey_bot.STALE_CLICK_TOAST
    assert (await state.get_data())["given"] is None  # новая анкета не тронута
    assert survey.is_completed(conn) is False


async def test_double_tap_on_last_answer_starts_lesson_once(conn, settings) -> None:
    load_seed(conn)
    intro, state, click = await _begin(conn, settings)
    await _press(click, intro, state, survey_bot.GO_LABEL)
    data = _data_of(intro, survey.LEVEL_OPTIONS[survey.LEVEL_FROM_SCRATCH])
    first, second = FakeCallback(data, intro), FakeCallback(data, intro)

    await asyncio.gather(click(first, state), click(second, state))

    assert len([text for text, _ in intro.sent if text.startswith("📋")]) == 1
    assert first.answered and second.answered  # «часики» не висят ни у кого


async def test_back_returns_to_previous_question(conn, settings) -> None:
    load_seed(conn)
    intro, state, click = await _begin(conn, settings)
    await _press(click, intro, state, survey_bot.GO_LABEL)
    await _press(click, intro, state, survey.LEVEL_OPTIONS[survey.LEVEL_SOME])
    await _press(click, intro, state, survey.SELF_LEVELS[3])
    assert "Вопрос 2 из 5" in intro.text

    await _press(click, intro, state, survey_bot.BACK_LABEL)

    assert "Вопрос 1 из 5" in intro.text
    assert (await state.get_data())["given"] == [[survey.LEVEL_KEY, survey.LEVEL_SOME]]


async def test_garbage_choice_is_ignored(conn, settings) -> None:
    load_seed(conn)
    intro, state, click = await _begin(conn, settings)
    await _press(click, intro, state, survey_bot.GO_LABEL)

    callback = await _tap(click, intro, state, "survey:0:99")

    assert callback.answer_text == survey_bot.STALE_CLICK_TOAST
    assert (await state.get_data())["given"] == []


async def test_finish_writes_profile_shows_summary_and_starts_lesson(conn, settings) -> None:
    load_seed(conn)
    intro, state, click = await _begin(conn, settings)
    await _press(click, intro, state, survey_bot.GO_LABEL)
    await _press(click, intro, state, survey.LEVEL_OPTIONS[survey.LEVEL_CONFIDENT])
    await _press(click, intro, state, survey.SELF_LEVELS[2])
    await _press(click, intro, state, survey.SELF_LEVELS[1])

    assert repos.get_fact(conn, "block_python") == survey.SELF_LEVELS[3]
    assert repos.get_fact(conn, "block_analysis") == survey.SELF_LEVELS[1]
    assert repos.get_fact(conn, survey.LEVEL_KEY) == survey.LEVEL_OPTIONS[2]
    assert intro.text.startswith("✅ Понял тебя")
    assert intro.reply_markup is None  # у сводки кнопок нет
    assert any(text.startswith("📋") for text, _ in intro.sent)  # список шагов
    assert await state.get_state() is None


async def test_click_after_survey_done_toasts_and_drops_buttons(conn, settings) -> None:
    load_seed(conn)
    _complete_survey(conn)
    router = make_survey_router(conn, settings, _TutorClient(), "m")
    message = FakeMessage()
    message.reply_markup = survey_bot.intro_view()[1]

    callback = FakeCallback(survey_bot.GO_DATA, message)
    await _named(router, "callback_query", "on_survey_click")(callback, _fsm())

    assert callback.answer_text == survey_bot.DONE_TOAST
    assert message.reply_markup is None


async def test_click_after_lost_fsm_restarts_survey_in_place(conn, settings) -> None:
    """Рестарт бота потерял FSM: анкета начинается заново в этом же сообщении."""
    load_seed(conn)
    router = make_survey_router(conn, settings, _TutorClient(), "m")
    message = FakeMessage()
    state = _fsm()

    callback = FakeCallback("survey:3:1", message)
    await _named(router, "callback_query", "on_survey_click")(callback, state)

    assert callback.answered
    assert survey.LEVEL_QUESTION in message.text
    assert (await state.get_data())["message_id"] == message.message_id


async def test_click_during_other_flow_is_refused(conn, settings) -> None:
    load_seed(conn)
    router = make_survey_router(conn, settings, _TutorClient(), "m")
    state = _fsm()
    await state.set_state(DiagnosticFlow.answering)

    callback = FakeCallback(survey_bot.GO_DATA, FakeMessage())
    await _named(router, "callback_query", "on_survey_click")(callback, state)

    assert callback.answer_text == BUSY_REPLY
    assert await state.get_state() == DiagnosticFlow.answering.state


async def test_edit_failure_falls_back_to_new_message(conn, settings, monkeypatch) -> None:
    """Сообщение не править (старое/удалено) — вопрос приходит новым."""
    load_seed(conn)
    intro, state, click = await _begin(conn, settings)

    async def _cannot_edit(*args, **kwargs):
        raise TelegramBadRequest(method=None, message="Bad Request: message can't be edited")

    monkeypatch.setattr(intro, "edit_text", _cannot_edit)
    await _press(click, intro, state, survey_bot.GO_LABEL)

    assert survey.LEVEL_QUESTION in intro.last_text
    assert (await state.get_data())["message_id"] != intro.message_id


# --- диагностика ---


async def test_diagnostic_asks_question_and_records_answer(conn, settings) -> None:
    load_seed(conn)
    router = make_diagnostic_router(conn, settings)
    state = _fsm()
    message = FakeMessage()

    await _handler(router, "message", 0)(message, state)  # /diagnostic

    assert message.sent[0][0].startswith("Подберу маршрут")
    assert message.sent[1][1] is not None  # задание с вариантами
    item_id = (await state.get_data())["item_id"]

    await _handler(router, "callback_query", 0)(
        FakeCallback(f"diag:{item_id}:0", message), state
    )

    events = repos.get_events(conn)
    assert events  # у задания может быть несколько концептов — отсюда несколько событий
    assert {event.item_id for event in events} == {item_id}
    assert all(repos.get_mastery(conn, event.concept_id) for event in events)


async def test_diagnostic_checks_text_answer(conn, settings) -> None:
    """Короткий ответ приходит текстом — его ведёт отдельный хендлер."""
    load_seed(conn)
    router = make_diagnostic_router(conn, settings)
    state = _fsm()
    await state.set_state(DiagnosticFlow.answering)
    await state.update_data(
        item_id=2, concept_id="read_csv", asked=[], left=1, correct=0, total=0
    )

    await _handler(router, "message", 1)(FakeMessage("read_csv"), state)

    assert beta.estimate(conn, "read_csv", settings=settings).mean > 0.5


async def test_text_on_choice_question_is_not_recorded(conn, settings) -> None:
    """Болтовня на вопрос с кнопками не должна засчитаться неверным ответом."""
    load_seed(conn)
    router = make_diagnostic_router(conn, settings)
    state = _fsm()
    await _handler(router, "message", 0)(FakeMessage(), state)  # /diagnostic -> вопрос с кнопками
    reply = FakeMessage("не знаю, если честно")

    await _handler(router, "message", 1)(reply, state)

    assert repos.get_events(conn) == []
    assert "кнопкой" in reply.last_text


async def test_button_on_text_question_is_not_recorded(conn, settings) -> None:
    load_seed(conn)
    router = make_diagnostic_router(conn, settings)
    state = _fsm()
    await state.set_state(DiagnosticFlow.answering)
    await state.update_data(
        item_id=2, concept_id="read_csv", expects_choice=False, asked=[], left=1,
        correct=0, total=0,
    )
    message = FakeMessage()

    item_id = (await state.get_data())["item_id"]
    await _handler(router, "callback_query", 0)(
        FakeCallback(f"diag:{item_id}:0", message), state
    )

    assert repos.get_events(conn) == []
    assert "текстом" in message.last_text


async def test_survey_text_gets_button_hint(conn, settings) -> None:
    """Напечатанный вместо кнопки ответ не должен пропадать в тишину."""
    router = make_survey_router(conn, settings, _TutorClient(), "m")
    state = _fsm()
    await start_survey(FakeMessage(), state)
    message = FakeMessage()

    await _named(router, "message", "on_text")(message)

    assert "кнопкой" in message.last_text


async def test_diagnostic_skips_when_graph_empty(conn, settings) -> None:
    router = make_diagnostic_router(conn, settings)
    message = FakeMessage()

    await _handler(router, "message", 0)(message, _fsm())

    assert "seed" in message.last_text.lower()


async def test_diagnostic_survives_lost_state(conn, settings) -> None:
    load_seed(conn)
    router = make_diagnostic_router(conn, settings)
    state = _fsm()
    await state.update_data(item_id=None)  # как после рестарта

    message = FakeMessage()
    item_id = (await state.get_data())["item_id"]
    await _handler(router, "callback_query", 0)(
        FakeCallback(f"diag:{item_id}:0", message), state
    )

    assert "заново" in message.last_text


# --- практика: /task и ответ через состояние сессии ---


class _TutorClient:
    """Подставной клиент тьюторского хода (структурированный ответ)."""

    async def chat_structured(self, messages, schema, *, model=None, temperature=None):
        return schema(reply="ок", hint_level=0)

    async def chat(self, messages, *, model=None, temperature=None) -> str:
        return "ок"


def _state(conn):
    return repos.get_session_state(conn, repos.get_open_session(conn))


async def test_task_command_sends_question_with_buttons(conn, settings) -> None:
    load_seed(conn)
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    message = FakeMessage()

    await _named(router, "message", "on_task")(message, _fsm())

    assert message.sent[-1][1] is not None  # задание с вариантами
    state = _state(conn)
    assert state.pending_item_id is not None
    assert state.current_node_id is not None
    assert state.hint_level == 0


async def test_send_reply_sends_tail_as_second_message(conn, settings) -> None:
    """Ход с хвостом шлёт два сообщения: реплика и задание с кнопками."""
    load_seed(conn)
    message = FakeMessage()

    await handlers._send_reply(
        conn, message, TurnReply(text="объяснение", tail="задание", options=["а", "б"])
    )

    assert [text for text, _ in message.sent] == ["объяснение", "задание"]
    assert message.sent[1][1] is not None  # варианты ответа на задание


async def test_button_answer_goes_through_turn(conn, settings) -> None:
    load_seed(conn)
    _complete_survey(conn)
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    message = FakeMessage()
    await _named(router, "message", "on_task")(message, _fsm())
    pending = _state(conn).pending_item_id

    await _handler(router, "callback_query", 0)(
        FakeCallback(f"answer:{pending}:0", message)
    )

    assert repos.get_events(conn)  # ответ проверен кодом и записан
    assert _state(conn).pending_item_id != pending  # отвеченное задание снято


async def test_text_answer_goes_through_turn(conn, settings) -> None:
    load_seed(conn)
    _complete_survey(conn)
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    await _named(router, "message", "on_task")(FakeMessage(), _fsm())
    item = repos.get_item(conn, _state(conn).pending_item_id)
    answer = FakeMessage(item.options[0] if item.options else str(item.answer))

    await _named(router, "message", "on_text")(answer, _fsm())

    assert repos.get_events(conn)
    # Ход с заданием уходит двумя сообщениями: разбор — первым, задание —
    # вторым (Срез 21), поэтому «Верно» ищем среди отправленного, а не в хвосте.
    assert any("Верно" in text for text, _ in answer.sent)


async def test_answer_without_pending_item_is_reported(conn, settings) -> None:
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    message = FakeMessage()

    await _handler(router, "callback_query", 0)(FakeCallback("answer:0", message))

    assert "неактуально" in message.last_text


async def test_free_text_goes_to_tutor_turn(conn, settings) -> None:
    load_seed(conn)
    _complete_survey(conn)
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    message = FakeMessage("привет!")

    await _named(router, "message", "on_text")(message, _fsm())

    # Занятия ещё нет, поэтому реплика входит в узел (Срез 21): объяснение
    # первым сообщением, задание по первому шагу — вторым.
    assert message.sent[0][0] == "ок"
    assert message.sent[1][0]
    assert len(repos.get_messages(conn, repos.get_open_session(conn))) == 2


async def test_task_is_refused_during_flow(conn, settings) -> None:
    """Во время анкеты или подбора маршрута задание не выдаём."""
    load_seed(conn)
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    state = _fsm()
    await state.set_state(DiagnosticFlow.answering)
    message = FakeMessage()

    await _named(router, "message", "on_task")(message, state)

    assert "Сначала" in message.last_text
    session_id = repos.get_open_session(conn)
    assert session_id is None or repos.get_session_state(conn, session_id).pending_item_id is None


async def test_task_reports_failure_instead_of_silence(conn, settings, monkeypatch) -> None:
    """Сбой выдачи задания не должен оставлять ученика без ответа."""
    load_seed(conn)
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    monkeypatch.setattr(
        "llm_tutor.bot.handlers.start_practice_reply",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("сбой")),
    )
    message = FakeMessage()

    await _named(router, "message", "on_task")(message, _fsm())

    assert "пошло не так" in message.last_text


async def test_skip_command_clears_task_without_evidence(conn, settings) -> None:
    load_seed(conn)
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    await _named(router, "message", "on_task")(FakeMessage(), _fsm())
    message = FakeMessage()

    await _named(router, "message", "on_skip")(message)

    assert _state(conn).pending_item_id is None
    assert repos.get_events(conn) == []


async def test_menu_action_failure_is_reported_and_answered(conn, settings, monkeypatch) -> None:
    """Сбой действия меню не молчит и не оставляет «часик» висеть."""
    load_seed(conn)
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    monkeypatch.setattr(
        "llm_tutor.bot.handlers.verify.start_verification",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("сбой")),
    )
    message = FakeMessage()
    callback = FakeCallback("menu:close", message)

    await _named(router, "callback_query", "on_menu_action")(callback, _fsm())

    assert "пошло не так" in message.last_text
    assert callback.answered is True


# --- диспетчер меню: ветки действий ---


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("route", "Маршрут"),
        ("close", "Проверка"),
    ],
)
async def test_menu_action_sends_expected_text(conn, settings, action, expected) -> None:
    """route/close присылают свой экран и всегда отвечают на нажатие."""
    load_seed(conn)
    session_id = repos.ensure_open_session(conn, now=1.0)
    state = repos.get_session_state(conn, session_id)
    repos.update_session_state(
        conn, session_id, state.model_copy(update={"current_node_id": "groupby"})
    )
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    message = FakeMessage()
    callback = FakeCallback(f"menu:{action}", message)

    await _named(router, "callback_query", "on_menu_action")(callback, _fsm())

    assert expected in message.last_text
    assert callback.answered is True


async def test_removed_menu_action_answers_nothing(conn, settings) -> None:
    """Убранное действие больше не обрабатывается."""
    load_seed(conn)
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    on_menu_action = _named(router, "callback_query", "on_menu_action")
    message = FakeMessage()

    await on_menu_action(FakeCallback("menu:status", message), _fsm())

    assert message.sent == []


# --- онбординг: /start до анкеты ---


async def test_start_before_survey_sends_intro_with_go(conn, settings) -> None:
    """Первый /start: одно сообщение-приветствие с «▶️ Поехали»."""
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    message = FakeMessage()
    state = _fsm()

    await _named(router, "message", "on_start")(message, state)

    assert len(message.sent) == 1
    text, markup = message.sent[0]
    assert text == start.INTRO_TEXT
    assert markup.inline_keyboard[0][0].callback_data == survey_bot.GO_DATA


async def test_start_button_opens_survey(conn, settings) -> None:
    """Текст «▶️ Старт» (висит у старых учеников) — тот же вход, что /start."""
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    message = FakeMessage(start.START_LABEL)

    await _named(router, "message", "on_start_button")(message, _fsm())

    assert message.sent[0][0] == start.INTRO_TEXT


async def test_text_before_survey_gets_intro(conn, settings) -> None:
    """До анкеты реплика получает приветствие с «Поехали», а не тьютора."""
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    message = FakeMessage("привет")
    state = _fsm()

    await _named(router, "message", "on_text")(message, state)

    assert message.last_text == start.INTRO_TEXT
    assert await state.get_state() == SurveyFlow.question.state
    assert repos.get_open_session(conn) is None  # в занятие не пошли


# --- намерение из текста (Срез 15) ---


async def test_answer_callback_disables_intents(conn, settings, monkeypatch) -> None:
    """Нажатие варианта ответа не трактуется как намерение (Срез 15)."""
    load_seed(conn)
    session_id = repos.ensure_open_session(conn, now=1.0)
    state = repos.get_session_state(conn, session_id)
    repos.update_session_state(
        conn,
        session_id,
        state.model_copy(update={"pending_item_id": 1, "current_node_id": "pandas_intro"}),
    )
    seen: dict = {}

    async def spy(*args, **kwargs):
        seen.update(kwargs)
        return TurnReply(text="ок")

    monkeypatch.setattr(handlers, "handle_turn", spy)
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    on_answer = _named(router, "callback_query", "on_answer")

    await on_answer(FakeCallback("answer:1:0", FakeMessage()))

    assert seen.get("allow_intents") is False


async def test_menu_close_starts_verification(conn, settings) -> None:
    """Нажатие «Закрыть тему» запускает проверочный проход."""
    load_seed(conn)
    session_id = repos.ensure_open_session(conn, now=1.0)
    state = repos.get_session_state(conn, session_id)
    repos.update_session_state(
        conn, session_id, state.model_copy(update={"current_node_id": "groupby"})
    )
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    on_menu_action = _named(router, "callback_query", "on_menu_action")
    message = FakeMessage()

    await on_menu_action(FakeCallback("menu:close", message), _fsm())

    assert "Проверка" in message.last_text


async def test_close_command_refuses_during_fsm_flow(conn, settings) -> None:
    """Посреди анкеты или подбора проверку не начинаем: состояние сессии поедет."""
    load_seed(conn)
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    state = _fsm()
    await state.set_state(DiagnosticFlow.answering)
    message = FakeMessage()

    await _named(router, "message", "on_close")(message, state)

    assert "Сначала закончим" in message.last_text
    assert repos.get_open_session(conn) is None  # сессию не тронули


async def test_menu_close_refuses_during_fsm_flow(conn, settings) -> None:
    """Та же защита у кнопки «Закрыть тему»."""
    load_seed(conn)
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    state = _fsm()
    await state.set_state(DiagnosticFlow.answering)
    message = FakeMessage()
    callback = FakeCallback("menu:close", message)

    await _named(router, "callback_query", "on_menu_action")(callback, state)

    assert "Сначала закончим" in message.last_text
    assert callback.answered is True
    assert repos.get_open_session(conn) is None  # сессию не тронули


async def test_start_after_survey_offers_menu(conn, settings) -> None:
    """У вернувшегося ученика /start даёт постоянную кнопку «☰ Меню»."""
    load_seed(conn)
    survey.apply_answers(
        conn,
        {block.key: 1 for block in survey.BLOCKS},
        now=1.0,
        settings=settings,
    )
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    on_start = _named(router, "message", "on_start")
    message = FakeMessage()

    await on_start(message, _fsm())

    assert message.sent[-1][1] == menu.main_menu()


async def test_resume_command_refuses_during_fsm_flow(conn, settings) -> None:
    """Посреди анкеты занятие не продолжаем: состояние сессии поедет."""
    load_seed(conn)
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    state = _fsm()
    await state.set_state(DiagnosticFlow.answering)
    message = FakeMessage()

    await _named(router, "message", "on_resume")(message, state)

    assert "Сначала закончим" in message.last_text
    assert repos.get_open_session(conn) is None  # сессию не тронули


def test_intro_is_two_sentences_without_reply_button() -> None:
    """Приветствие — два предложения; жать нужно инлайн-«Поехали» под ним."""
    lines = [line for line in start.INTRO_TEXT.splitlines() if line.strip()]
    assert len(lines) == 2
    assert start.START_LABEL not in start.INTRO_TEXT


async def test_start_after_survey_does_not_call_model(conn, settings) -> None:
    """Вернувшемуся — «С возвращением» без LLM и без служебной метки модели."""
    load_seed(conn)
    _complete_survey(conn)

    class _NoModel:
        async def chat(self, *args, **kwargs):
            raise AssertionError("повторный /start не зовёт модель")

        async def chat_structured(self, *args, **kwargs):
            raise AssertionError("повторный /start не зовёт модель")

    router = make_router(conn, _NoModel(), "m", settings=settings)
    message = FakeMessage()

    await _named(router, "message", "on_start")(message, _fsm())

    assert "С возвращением" in message.last_text
    assert "[модель:" not in message.last_text


# --- маршрутизация на уровне диспетчера (финал ветки) ---


def _update(text: str):
    """Настоящее обновление Telegram: прогоняем его через диспетчер как в бою."""
    return Update(
        update_id=1,
        message=Message(
            message_id=1,
            date=datetime.now(),
            chat=Chat(id=1, type="private"),
            from_user=User(id=1, is_bot=False, first_name="Ученик"),
            text=text,
        ),
    )


async def _dispatch(conn, settings, state_name: str, text: str):
    """Прогоняет текст через боевой диспетчер, стоя в заданном FSM-состоянии."""
    bot = Bot(token="42:TEST", session=NullSession())
    dispatcher = Dispatcher()
    # Порядок как в bot/main.py: потоки бота идут раньше основного роутера.
    dispatcher.include_router(make_router(conn, _TutorClient(), "m", settings=settings))
    ctx = dispatcher.fsm.get_context(bot=bot, chat_id=1, user_id=1)
    await ctx.set_state(state_name)

    await dispatcher.feed_update(bot, _update(text))

    state = await ctx.get_state()
    await bot.session.close()
    return state


async def test_command_on_route_screen_reaches_its_handler(conn, settings) -> None:
    """На экране согласования команда доходит до хендлера, а не в подсказку."""
    load_seed(conn)
    survey.apply_answers(
        conn,
        {block.key: 1 for block in survey.BLOCKS},
        now=1.0,
        settings=settings,
    )

    state = await _dispatch(conn, settings, DiagnosticFlow.answering.state, "/start")

    assert state is None  # /start отработал и снял поток


async def test_text_on_route_screen_stays_in_hint(conn, settings) -> None:
    """Свободный текст по-прежнему остаётся подсказкой экрана."""
    load_seed(conn)

    state = await _dispatch(conn, settings, DiagnosticFlow.answering.state, "groupby")

    assert state == DiagnosticFlow.answering.state  # подбор идёт своим ходом


async def test_status_command_reports_failure_instead_of_silence(
    conn, settings, monkeypatch
) -> None:
    """Сбой дашборда — сообщение, а не тишина."""
    load_seed(conn)
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    monkeypatch.setattr(
        "llm_tutor.bot.handlers.render.render_status",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("сбой")),
    )
    message = FakeMessage()

    await _named(router, "message", "on_status")(message)

    assert "пошло не так" in message.last_text


async def test_themes_command_reports_failure_instead_of_silence(
    conn, settings, monkeypatch
) -> None:
    """Сбой списка тем — сообщение, а не тишина."""
    load_seed(conn)
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    monkeypatch.setattr(
        "llm_tutor.bot.handlers.themes.themes_keyboard",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("сбой")),
    )
    message = FakeMessage()

    await _named(router, "message", "on_themes")(message)

    assert "пошло не так" in message.last_text


async def test_answer_from_stale_keyboard_is_refused(conn, settings) -> None:
    """Клик по старой клавиатуре не отвечает за текущее задание.

    Индекс варианта применялся бы к тому заданию, что висит сейчас, и в журнал
    ушло бы свидетельство за ответ, которого ученик не давал.
    """
    load_seed(conn)
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    on_answer = _handler(router, "callback_query", 0)
    await _named(router, "message", "on_task")(FakeMessage(), _fsm())
    answered = _state(conn).pending_item_id
    await on_answer(FakeCallback(f"answer:{answered}:0", FakeMessage()))
    events_before = len(repos.get_events(conn))
    current = _state(conn).pending_item_id
    message = FakeMessage()

    await on_answer(FakeCallback(f"answer:{answered}:0", message))

    assert "неактуально" in message.last_text
    assert len(repos.get_events(conn)) == events_before
    assert _state(conn).pending_item_id == current


def _lesson_node(conn) -> str:
    return repos.get_session_state(conn, repos.get_open_session(conn)).current_node_id


async def test_lesson_starts_where_steps_list_says(conn, settings) -> None:
    """Первый шаг списка — тот узел, с которого реально начался урок."""
    load_seed(conn)
    intro, state, click = await _begin(conn, settings)
    await _press(click, intro, state, survey_bot.GO_LABEL)
    await _press(click, intro, state, survey.LEVEL_OPTIONS[survey.LEVEL_CONFIDENT])
    await _press(click, intro, state, survey.SELF_LEVELS[1])
    await _press(click, intro, state, survey.SELF_LEVELS[0])

    steps_text = next(text for text, _ in intro.sent if text.startswith("📋"))
    first_line = next(line for line in steps_text.splitlines() if line.startswith("1. "))
    node = CourseGraph.load(conn).concept(_lesson_node(conn))
    assert node.name in first_line
    assert _lesson_node(conn) not in survey.claimed_concepts(conn)


async def test_all_confident_starts_with_check(conn, settings) -> None:
    load_seed(conn)
    intro, state, click = await _begin(conn, settings)
    await _press(click, intro, state, survey_bot.GO_LABEL)
    await _press(click, intro, state, survey.LEVEL_OPTIONS[survey.LEVEL_CONFIDENT])
    await _press(click, intro, state, survey.SELF_LEVELS[3])
    await _press(click, intro, state, survey.SELF_LEVELS[3])

    steps_text = next(text for text, _ in intro.sent if text.startswith("📋"))
    assert "начнём с короткой проверки" in steps_text
    assert "🔍" in steps_text
    assert "Проверка" in intro.sent[-1][0]  # первое задание прохода


async def test_survey_crafted_callback_is_answered(conn, settings) -> None:
    """Мусорный колбэк анкеты не роняет хендлер и не оставляет без ответа."""
    load_seed(conn)
    intro, state, click = await _begin(conn, settings)
    await _press(click, intro, state, survey_bot.GO_LABEL)

    callback = await _tap(click, intro, state, "survey:zzz")

    assert callback.answered is True
    assert callback.answer_text == survey_bot.STALE_CLICK_TOAST
    assert (await state.get_data())["given"] == []


async def test_survey_out_of_range_callback_is_answered(conn, settings) -> None:
    """Вариант вне списка не проходит в анкету."""
    load_seed(conn)
    intro, state, click = await _begin(conn, settings)
    await _press(click, intro, state, survey_bot.GO_LABEL)

    callback = await _tap(click, intro, state, "survey:0:-1")

    assert callback.answered is True
    assert callback.answer_text == survey_bot.STALE_CLICK_TOAST
    assert (await state.get_data())["given"] == []


async def test_options_keyboard_binds_answer_to_item(conn, settings) -> None:
    """Колбэк варианта несёт id задания — иначе старый клик не отличить."""
    load_seed(conn)
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    message = FakeMessage()
    await _named(router, "message", "on_task")(message, _fsm())
    pending = _state(conn).pending_item_id

    callbacks = [
        button.callback_data for row in message.sent[-1][1].inline_keyboard for button in row
    ]

    assert callbacks[0] == f"answer:{pending}:0"
    assert len(callbacks) == len(set(callbacks))


async def test_skip_command_reports_failure(conn, settings, monkeypatch) -> None:
    """Сбой пропуска задания — сообщение, а не тишина."""
    load_seed(conn)
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    monkeypatch.setattr(
        "llm_tutor.bot.handlers.skip_pending",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("сбой")),
    )
    message = FakeMessage()

    await _named(router, "message", "on_skip")(message)

    assert "пошло не так" in message.last_text


async def test_diagnostic_stale_keyboard_is_refused(conn, settings) -> None:
    """Клик по клавиатуре прошлого задания не отвечает за текущее."""
    load_seed(conn)
    router = make_diagnostic_router(conn, settings)
    state = _fsm()
    message = FakeMessage()
    await _handler(router, "message", 0)(message, state)  # /diagnostic
    first = (await state.get_data())["item_id"]
    on_choice = _handler(router, "callback_query", 0)
    await on_choice(FakeCallback(f"diag:{first}:0", message), state)
    events_before = len(repos.get_events(conn))
    assert (await state.get_data())["item_id"] != first  # заход пошёл дальше
    stale = FakeMessage()

    await on_choice(FakeCallback(f"diag:{first}:0", stale), state)

    assert "прошлого задания" in stale.last_text
    assert len(repos.get_events(conn)) == events_before


async def test_survey_finish_shows_steps_and_starts_lesson(conn, settings) -> None:
    """Финал анкеты: сводка в сообщении анкеты, список шагов и сразу урок."""
    load_seed(conn)
    intro, state, click = await _begin(conn, settings)
    await _press(click, intro, state, survey_bot.GO_LABEL)

    await _press(click, intro, state, survey.LEVEL_OPTIONS[survey.LEVEL_FROM_SCRATCH])

    steps_text = next(text for text, _ in intro.sent if "Ближайшие" in text)
    assert "1. " in steps_text
    assert intro.sent[-2][0]  # урок начался: объяснение первой темы
    assert intro.sent[-1][0]  # и сразу первое задание
    assert await state.get_state() is None




# --- аудит среза 22 ---


class _Inaccessible:
    """Как InaccessibleMessage: писать в чат можно, править нельзя."""

    def __init__(self, message_id: int) -> None:
        self.message_id = message_id
        self.sent: list = []
        self.log: list[str] = []

    async def answer(self, text: str, reply_markup=None, **kwargs) -> FakeMessage:
        self.sent.append((text, reply_markup))
        sent = FakeMessage(text, log=self.log)
        sent.reply_markup = reply_markup
        return sent


async def test_inaccessible_survey_message_gets_question_anew(conn, settings) -> None:
    """Старое (недоступное для правки) сообщение анкеты — вопрос приходит новым."""
    load_seed(conn)
    intro, state, click = await _begin(conn, settings)
    old = _Inaccessible(intro.message_id)

    await _tap(click, old, state, survey_bot.GO_DATA)

    assert survey.LEVEL_QUESTION in old.sent[-1][0]
    assert (await state.get_data())["message_id"] != intro.message_id  # перепривязали


async def test_inaccessible_message_on_last_answer_still_starts_lesson(conn, settings) -> None:
    load_seed(conn)
    intro, state, click = await _begin(conn, settings)
    await _press(click, intro, state, survey_bot.GO_LABEL)
    data = _data_of(intro, survey.LEVEL_OPTIONS[survey.LEVEL_FROM_SCRATCH])

    old = _Inaccessible(intro.message_id)

    await _tap(click, old, state, data)

    assert survey.is_completed(conn)
    texts = [text for text, _ in old.sent]
    assert any(text.startswith("✅ Понял тебя") for text in texts)  # сводка — новым
    assert any(text.startswith("📋") for text in texts)  # и урок начался


async def test_stale_step_in_bound_message_redraws_current_question(conn, settings, monkeypatch) -> None:
    """Правка сорвалась (сеть), шаг уже сдвинут — следующее нажатие перерисует вопрос."""
    from aiogram.exceptions import TelegramNetworkError

    load_seed(conn)
    intro, state, click = await _begin(conn, settings)
    await _press(click, intro, state, survey_bot.GO_LABEL)
    level_some = _data_of(intro, survey.LEVEL_OPTIONS[survey.LEVEL_SOME])
    real_edit = intro.edit_text

    async def _network_down(*args, **kwargs):
        raise TelegramNetworkError(method=None, message="timeout")

    monkeypatch.setattr(intro, "edit_text", _network_down)
    with pytest.raises(TelegramNetworkError):
        await _tap(click, intro, state, level_some)
    monkeypatch.setattr(intro, "edit_text", real_edit)

    callback = await _tap(click, intro, state, level_some)  # кнопки на экране старые

    assert callback.answer_text == survey_bot.STALE_CLICK_TOAST
    assert "Вопрос 1 из 5" in intro.text  # экран догнал состояние


async def test_restart_fallback_rebinds_survey_to_new_message(conn, settings, monkeypatch) -> None:
    load_seed(conn)
    router = make_survey_router(conn, settings, _TutorClient(), "m")
    message = FakeMessage()
    state = _fsm()

    async def _cannot_edit(*args, **kwargs):
        raise TelegramBadRequest(method=None, message="Bad Request: message can't be edited")

    monkeypatch.setattr(message, "edit_text", _cannot_edit)
    await _named(router, "callback_query", "on_survey_click")(
        FakeCallback(survey_bot.GO_DATA, message), state
    )

    assert (await state.get_data())["message_id"] != message.message_id


async def test_restart_keeps_answer_to_level_question(conn, settings) -> None:
    """После рестарта нажатие на вопросе об уровне не теряется."""
    load_seed(conn)
    router = make_survey_router(conn, settings, _TutorClient(), "m")
    message = FakeMessage()
    state = _fsm()

    await _named(router, "callback_query", "on_survey_click")(
        FakeCallback(f"survey:0:{survey.LEVEL_SOME}", message), state
    )

    assert "Вопрос 1 из 5" in message.text
    assert (await state.get_data())["given"] == [[survey.LEVEL_KEY, survey.LEVEL_SOME]]


async def test_start_survey_binds_state_before_sending(conn, settings) -> None:
    """Состояние анкеты ставится до отправки: вторая реплика пачкой уйдёт в анкету."""
    state = _fsm()
    seen: list = []

    class _Probe(FakeMessage):
        async def answer(self, text, reply_markup=None, **kwargs):
            seen.append(await state.get_state())
            return await super().answer(text, reply_markup=reply_markup, **kwargs)

    await start_survey(_Probe(), state)

    assert seen == [SurveyFlow.question.state]


async def test_lesson_failure_after_survey_is_reported(conn, settings, monkeypatch) -> None:
    """Сбой первого хода урока не оставляет ученика в тишине после «сейчас объясню»."""
    from llm_tutor.llm.prompts import BOT_FAILURE_REPLY

    load_seed(conn)
    intro, state, click = await _begin(conn, settings)
    await _press(click, intro, state, survey_bot.GO_LABEL)

    async def _boom(*args, **kwargs):
        raise RuntimeError("сбой")

    monkeypatch.setattr(start, "resume_reply", _boom)
    await _press(click, intro, state, survey.LEVEL_OPTIONS[survey.LEVEL_FROM_SCRATCH])

    assert BOT_FAILURE_REPLY in intro.sent[-1][0]


# --- аудит среза 23 ---


async def test_command_before_survey_does_not_disable_claimed(conn, settings) -> None:
    """/task до анкеты не отменяет «знакомое»: после анкеты урок с чистого листа."""
    load_seed(conn)
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    await _named(router, "message", "on_task")(FakeMessage(), _fsm())  # задание до анкеты
    intro, state, click = await _begin(conn, settings)
    await _press(click, intro, state, survey_bot.GO_LABEL)
    await _press(click, intro, state, survey.LEVEL_OPTIONS[survey.LEVEL_CONFIDENT])
    await _press(click, intro, state, survey.SELF_LEVELS[3])
    await _press(click, intro, state, survey.SELF_LEVELS[3])

    lesson = repos.get_session_state(conn, repos.get_open_session(conn))
    assert lesson.mode == "verify"
    assert "Проверка" in intro.sent[-1][0]  # пришло задание проверки, а не «задание ждёт»
    assert any(step.status == "claimed" for step in lesson.route.steps)


async def test_check_start_does_not_repeat_itself(conn, settings) -> None:
    """Старт с проверки: «пара быстрых вопросов» звучит один раз, без «Осталось»."""
    load_seed(conn)
    intro, state, click = await _begin(conn, settings)
    await _press(click, intro, state, survey_bot.GO_LABEL)
    await _press(click, intro, state, survey.LEVEL_OPTIONS[survey.LEVEL_CONFIDENT])
    await _press(click, intro, state, survey.SELF_LEVELS[3])
    await _press(click, intro, state, survey.SELF_LEVELS[3])

    said = " ".join(text for text, _ in intro.sent)
    assert said.count("пара быстрых вопросов") == 1
    assert "Осталось" not in said
