"""Тесты FSM-потоков бота: анкета и диагностика (Срез 4.5)."""

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup

from llm_tutor.bot.diagnostic import DiagnosticFlow, make_diagnostic_router
from llm_tutor.bot.handlers import make_router
from llm_tutor.bot.survey import ask as ask_survey
from llm_tutor.bot.survey import make_survey_router
from llm_tutor.course.seed import load_seed
from llm_tutor.db import repos
from llm_tutor.student import beta, survey


class FakeMessage:
    """Подставное сообщение: помнит всё, что бот в него отправил."""

    def __init__(self, text: str = "") -> None:
        self.text = text
        self.sent: list[tuple[str, InlineKeyboardMarkup | None]] = []

    async def answer(self, text: str, reply_markup=None) -> None:
        self.sent.append((text, reply_markup))

    @property
    def last_text(self) -> str:
        return self.sent[-1][0]


class FakeCallback:
    """Подставное нажатие инлайн-кнопки."""

    def __init__(self, data: str, message: FakeMessage) -> None:
        self.data = data
        self.message = message
        self.answered = False

    async def answer(self, *args, **kwargs) -> None:
        self.answered = True


def _fsm() -> FSMContext:
    return FSMContext(
        storage=MemoryStorage(),
        key=StorageKey(bot_id=1, chat_id=1, user_id=1),
    )


def _handler(router, kind: str, index: int) -> object:
    """Достаёт callback зарегистрированного хендлера (фильтры обходим)."""
    return getattr(router, kind).handlers[index].callback


def _named(router, kind: str, name: str):
    """Находит хендлер по имени функции — не зависит от порядка регистрации."""
    for handler in getattr(router, kind).handlers:
        if handler.callback.__name__ == name:
            return handler.callback
    raise AssertionError(f"нет хендлера {name}")


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
    router = make_survey_router(conn, settings)
    state = _fsm()
    message = FakeMessage()
    await ask_survey(message, state)
    on_answer = _handler(router, "callback_query", 0)

    for _ in range(len(survey.SURVEY_QUESTIONS)):
        await on_answer(FakeCallback("survey:0", message), state)

    assert repos.get_fact(conn, survey.EXPERIENCE_KEY) == "На Python не писал"
    assert repos.get_fact(conn, survey.GOAL_CONCEPT_KEY) == "pandas_dataframe"
    assert await state.get_state() is None  # поток закрыт


async def test_survey_goes_through_all_questions(conn, settings) -> None:
    load_seed(conn)
    router = make_survey_router(conn, settings)
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

    await _handler(router, "callback_query", 0)(FakeCallback("diag:0", message), state)

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

    await _handler(router, "callback_query", 0)(FakeCallback("diag:0", message), state)

    assert repos.get_events(conn) == []
    assert "текстом" in message.last_text


async def test_survey_text_gets_button_hint(conn, settings) -> None:
    """Напечатанный вместо кнопки ответ не должен пропадать в тишину."""
    router = make_survey_router(conn, settings)
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
    await _handler(router, "callback_query", 0)(FakeCallback("diag:0", message), state)

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
    router = make_router(conn, _TutorClient(), "m", settings=settings)
    message = FakeMessage()
    await _named(router, "message", "on_task")(message, _fsm())

    await _handler(router, "callback_query", 0)(FakeCallback("answer:0", message))

    assert repos.get_events(conn)  # ответ проверен кодом и записан
    assert _state(conn).pending_item_id is None


async def test_text_answer_goes_through_turn(conn, settings) -> None:
    load_seed(conn)
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
