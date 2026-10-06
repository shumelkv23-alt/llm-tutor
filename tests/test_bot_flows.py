"""Тесты FSM-потоков бота: анкета и диагностика (Срез 4.5)."""

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup

from llm_tutor.bot.diagnostic import DiagnosticFlow, make_diagnostic_router
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
