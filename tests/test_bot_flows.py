"""Тесты FSM-потоков бота: анкета и диагностика (Срез 4.5)."""

from datetime import datetime

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import Chat, Message, Update, User

from fakes import FakeCallback, FakeMessage, NullSession, _fsm, _named

from llm_tutor.bot import handlers, menu
from llm_tutor.bot.diagnostic import DiagnosticFlow, make_diagnostic_router
from llm_tutor.bot.handlers import make_router
from llm_tutor.bot import start
from llm_tutor.bot.survey import ask as ask_survey
from llm_tutor.bot.survey import make_survey_router
from llm_tutor.core.turn import TurnReply
from llm_tutor.course.seed import load_seed
from llm_tutor.db import repos
from llm_tutor.student import beta, survey


def _complete_survey(conn) -> None:
    """Профиль заполнен: без этого свободный текст упирается в приглашение."""
    for question in survey.SURVEY_QUESTIONS:
        repos.set_fact(conn, question.key, survey.SELF_LEVELS[3], source="self")


def _handler(router, kind: str, index: int) -> object:
    """Достаёт callback зарегистрированного хендлера (фильтры обходим)."""
    return getattr(router, kind).handlers[index].callback


# --- анкета ---


async def test_survey_asks_first_question_with_buttons(conn, settings) -> None:
    state = _fsm()
    message = FakeMessage()

    await ask_survey(message, state)

    text, keyboard = message.sent[-1]
    assert text == survey.SURVEY_QUESTIONS[0].text
    assert len(keyboard.inline_keyboard) == len(survey.SURVEY_QUESTIONS[0].options)


async def test_survey_writes_profile_after_last_answer(conn, settings) -> None:
    load_seed(conn)
    router = make_survey_router(conn, settings, _TutorClient(), "m")
    state = _fsm()
    message = FakeMessage()
    await ask_survey(message, state)
    on_answer = _handler(router, "callback_query", 0)

    for _ in range(len(survey.SURVEY_QUESTIONS)):
        await on_answer(FakeCallback("survey:0", message), state)

    first = survey.SURVEY_QUESTIONS[0]
    assert repos.get_fact(conn, first.key) == first.options[0].label
    assert await state.get_state() is None  # анкета закрыта, урок начался


async def test_survey_goes_through_all_questions(conn, settings) -> None:
    load_seed(conn)
    router = make_survey_router(conn, settings, _TutorClient(), "m")
    state = _fsm()
    message = FakeMessage()
    await ask_survey(message, state)
    on_answer = _handler(router, "callback_query", 0)

    await on_answer(FakeCallback("survey:1", message), state)

    # Следующий вопрос — про цель, и он тоже с кнопками.
    assert message.last_text == survey.SURVEY_QUESTIONS[1].text
    assert message.sent[-1][1] is not None


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
    await ask_survey(FakeMessage(), state)
    message = FakeMessage()

    await _handler(router, "message", 0)(message)

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

    await _named(router, "message", "on_text")(answer)

    assert repos.get_events(conn)
    assert "Верно" in answer.last_text


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

    await _named(router, "message", "on_text")(message)

    assert message.last_text == "ок"
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


async def test_start_before_survey_sends_intro_then_question(conn, settings) -> None:
    """Первый /start: приветствие с кнопкой «▶️ Старт», потом вопрос анкеты."""
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    message = FakeMessage()
    state = _fsm()

    await _named(router, "message", "on_start")(message, state)

    intro_text, intro_markup = message.sent[0]
    assert intro_text == start.INTRO_TEXT
    assert intro_markup.keyboard[0][0].text == start.START_LABEL
    assert message.sent[1][0] == survey.SURVEY_QUESTIONS[0].text


async def test_start_button_opens_survey(conn, settings) -> None:
    """Кнопка «▶️ Старт» — тот же вход, что /start."""
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    message = FakeMessage(start.START_LABEL)
    state = _fsm()

    await _named(router, "message", "on_start_button")(message, state)

    assert message.sent[0][0] == start.INTRO_TEXT
    assert message.sent[1][0] == survey.SURVEY_QUESTIONS[0].text


async def test_text_before_survey_gets_invitation(conn, settings) -> None:
    """До анкеты реплика отвечает приглашением, а не тьютором."""
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    message = FakeMessage("привет")

    await _named(router, "message", "on_text")(message)

    assert message.last_text == start.BEFORE_SURVEY_REPLY
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


async def test_start_after_survey_offers_resume(conn, settings) -> None:
    """У вернувшегося ученика /start даёт кнопку «Продолжить обучение»."""
    load_seed(conn)
    survey.apply_answers(
        conn,
        {question.key: 1 for question in survey.SURVEY_QUESTIONS},
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


def test_intro_has_three_sentences_and_names_the_button() -> None:
    """Приветствие — три предложения, третье называет кнопку."""
    assert len([line for line in start.INTRO_TEXT.splitlines() if line.strip()]) == 3
    assert start.START_LABEL in start.INTRO_TEXT


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
        {question.key: 1 for question in survey.SURVEY_QUESTIONS},
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


async def test_survey_crafted_callback_is_answered(conn, settings) -> None:
    """Мусорный колбэк анкеты не роняет хендлер и не оставляет без ответа."""
    load_seed(conn)
    router = make_survey_router(conn, settings, _TutorClient(), "m")
    on_answer = _handler(router, "callback_query", 0)
    state = _fsm()
    message = FakeMessage()
    await ask_survey(message, state)
    callback = FakeCallback("survey:zzz", message)

    await on_answer(callback, state)

    assert callback.answered is True
    assert "кнопкой" in message.last_text


async def test_survey_out_of_range_callback_is_answered(conn, settings) -> None:
    """Вариант вне списка не проходит в анкету."""
    load_seed(conn)
    router = make_survey_router(conn, settings, _TutorClient(), "m")
    on_answer = _handler(router, "callback_query", 0)
    state = _fsm()
    message = FakeMessage()
    await ask_survey(message, state)
    callback = FakeCallback("survey:99", message)

    await on_answer(callback, state)

    assert callback.answered is True
    assert "кнопкой" in message.last_text


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
    """Финал анкеты: список ближайших шагов и сразу начало урока."""
    load_seed(conn)
    router = make_survey_router(conn, settings, _TutorClient(), "m")
    ask_state = _fsm()
    await ask_survey(FakeMessage(), ask_state)
    on_answer = _handler(router, "callback_query", 0)
    message = FakeMessage()

    for _ in range(len(survey.SURVEY_QUESTIONS)):
        await on_answer(FakeCallback("survey:0", message), ask_state)

    steps_text = message.sent[-2][0]
    assert "Ближайшие" in steps_text
    assert "1. " in steps_text
    assert "<pre>" not in steps_text  # схема больше не рисуется
    assert message.sent[-1][0]  # урок начался: объяснение первой темы
    assert await ask_state.get_state() is None
