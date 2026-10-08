"""Тесты обработчика хода (Срез 5.5)."""

import sqlite3

import pytest

from llm_tutor.core.turn import (
    NOTHING_TO_SKIP_REPLY,
    PENDING_ITEM_NOTE,
    RESUME_KICKOFF_TEXT,
    ROUTE_DONE_REPLY,
    SKIP_REPLY,
    STALE_ITEM_REPLY,
    STUCK_NOTE,
    VERIFY_EXHAUSTED_REPLY,
    VERIFY_EXPLAIN_PREFIX,
    handle_turn,
    post_turn,
    resume_reply,
    skip_pending,
    start_practice_reply,
)
from llm_tutor.course.ingest import ingest_text
from llm_tutor.course.seed import load_seed
from llm_tutor.db import repos
from llm_tutor.grader.rubric import CriterionVerdict, RubricVerdict
from llm_tutor.llm.client import LLMError
from llm_tutor.llm.prompts import LLM_FAILURE_REPLY
from llm_tutor.schemas import Route, RouteStep, SessionState
from llm_tutor.student import beta


class _FakeTutor:
    """Подставной клиент: отвечает заготовкой и задаёт уровень подсказки."""

    def __init__(
        self,
        reply: str = "Ответ тьютора",
        hint_level: int = 0,
        *,
        student_stuck: bool = False,
        wants_close_topic: bool = False,
    ) -> None:
        self.reply = reply
        self.hint_level = hint_level
        self.student_stuck = student_stuck
        self.wants_close_topic = wants_close_topic
        self.calls: list[list] = []

    async def chat_structured(self, messages, schema, *, model=None, temperature=None):
        self.calls.append(list(messages))
        return schema(
            reply=self.reply,
            hint_level=self.hint_level,
            student_stuck=self.student_stuck,
            wants_close_topic=self.wants_close_topic,
        )

    async def chat(self, messages, *, model=None, temperature=None) -> str:
        raise AssertionError("тьюторский ход должен идти через chat_structured")


class _FailingTutor:
    async def chat_structured(self, messages, schema, *, model=None, temperature=None):
        raise LLMError("сбой", retryable=False)


class _FakeGraderClient:
    """Подставной грейдер: возвращает заготовленный вердикт по рубрике."""

    def __init__(self, verdict: RubricVerdict) -> None:
        self.verdict = verdict
        self.calls: list[list] = []

    async def chat_structured(self, messages, schema, *, model=None, temperature=None):
        self.calls.append(list(messages))
        # Схему уважаем: закрытие узла делает ещё и тьюторский ход — на него
        # грейдерный вердикт не подходит.
        if schema is RubricVerdict:
            return self.verdict
        return schema(reply="Разбираем.", hint_level=0)


def _full_verdict(conn, item_id: int) -> RubricVerdict:
    """Вердикт «все критерии выполнены» с цитатой, которая есть в ответе."""
    item = repos.get_item(conn, item_id)
    return RubricVerdict(
        criteria=[
            CriterionVerdict(id=criterion.id, passed=True, quote="groupby")
            for criterion in repos.get_criteria(conn, item.rubric_id)
        ]
    )


def _state(conn) -> SessionState:
    session_id = repos.ensure_open_session(conn, now=1.0)
    return repos.get_session_state(conn, session_id)


def _set_state(conn, **patch) -> None:
    session_id = repos.ensure_open_session(conn, now=1.0)
    state = repos.get_session_state(conn, session_id)
    repos.update_session_state(conn, session_id, state.model_copy(update=patch))


# --- тьюторский путь ---


async def test_tutor_uses_material_and_persists_turn(conn, settings) -> None:
    ingest_text(conn, "# T\n\n## Grouping\n\ngroupby aggregates rows\n", "u")
    client = _FakeTutor("Смотри на groupby")

    reply = await handle_turn(conn, client, "m", "как работает groupby?", now=1.0, settings=settings)

    assert reply.text == "Смотри на groupby"
    material_in_prompt = any(
        "groupby aggregates rows" in message.content for message in client.calls[0]
    )
    assert material_in_prompt
    messages = repos.get_messages(conn, repos.get_open_session(conn))
    assert [m.role for m in messages] == ["user", "assistant"]


async def test_hint_level_rises_at_most_one_step(conn, settings) -> None:
    """Один ход не может выдать разбор: код держит шаг лестницы."""
    ingest_text(conn, "# T\n\n## S\n\ngroupby\n", "u")
    client = _FakeTutor(hint_level=4)

    # Текст со словами-триггерами, но проверяем не намерение, а лестницу.
    await handle_turn(
        conn, client, "m", "не понимаю groupby", now=1.0, settings=settings, allow_intents=False
    )

    assert _state(conn).hint_level == 1  # было 0


async def test_hint_level_can_drop(conn, settings) -> None:
    ingest_text(conn, "# T\n\n## S\n\ngroupby\n", "u")
    _set_state(conn, hint_level=3)

    await handle_turn(conn, _FakeTutor(hint_level=0), "m", "groupby понял, дальше сам", now=1.0, settings=settings)

    assert _state(conn).hint_level == 0


async def test_offtopic_question_goes_to_model(conn, settings) -> None:
    """Вопрос рядом с курсом уходит модели, а не в детерминированный отказ."""
    ingest_text(conn, "# T\n\n## Grouping\n\ngroupby aggregates rows\n", "u")
    client = _FakeTutor("это за пределами темы 1, но вот как это работает")

    reply = await handle_turn(
        conn, client, "m", "how do I train a neural network?", now=1.0, settings=settings
    )

    assert reply.text == "это за пределами темы 1, но вот как это работает"
    assert len(client.calls) == 1  # модель спросили


async def test_llm_failure_is_reported_and_turn_persisted(conn, settings) -> None:
    ingest_text(conn, "# T\n\n## S\n\ngroupby\n", "u")

    reply = await handle_turn(conn, _FailingTutor(), "m", "groupby?", now=1.0, settings=settings)

    assert reply.text == LLM_FAILURE_REPLY
    assert len(repos.get_messages(conn, repos.get_open_session(conn))) == 2


# --- ответ на задание ---


async def test_answer_is_checked_without_llm(conn, settings) -> None:
    load_seed(conn)
    client = _FakeTutor("тьютор не должен вызываться")

    reply_ = start_practice_reply(conn, now=1.0, settings=settings)
    item = repos.get_item(conn, _state(conn).pending_item_id)
    reply = await handle_turn(conn, client, "m", item.options[0], now=2.0, settings=settings)

    assert reply_.text  # задание выдано
    assert "Верно" in reply.text
    assert client.calls == []  # проверял код, не модель
    assert _state(conn).pending_item_id != item.id  # отвеченное задание снято
    assert any(event.source == "checked" for event in repos.get_events(conn))


async def test_wrong_answer_shows_correct_option(conn, settings) -> None:
    load_seed(conn)
    _set_state(conn, pending_item_id=1, current_node_id="pandas_intro")
    item = repos.get_item(conn, 1)

    reply = await handle_turn(conn, _FakeTutor(), "m", item.options[1], now=1.0, settings=settings)

    assert "Не совсем" in reply.text
    assert item.options[0] in reply.text
    assert beta.estimate(conn, "pandas_intro", now=1.0, settings=settings).mean < 0.5


async def test_stale_pending_item_clears_state(conn, settings) -> None:
    _set_state(conn, pending_item_id=999)

    reply = await handle_turn(conn, _FakeTutor(), "m", "0", now=1.0, settings=settings)

    assert reply.text.startswith(STALE_ITEM_REPLY)
    assert _state(conn).pending_item_id != 999  # старое задание снято
    assert len(repos.get_messages(conn, repos.get_open_session(conn))) == 2


async def test_start_practice_sets_pending_item(conn, settings) -> None:
    load_seed(conn)

    reply_ = start_practice_reply(conn, now=1.0, settings=settings)

    state = _state(conn)
    assert state.pending_item_id is not None
    assert state.current_node_id
    assert state.hint_level == 0
    assert reply_.text


def test_start_practice_without_graph_hints_seed(conn, settings) -> None:
    assert "seed" in start_practice_reply(conn, now=1.0, settings=settings).text.lower()


# --- атомарность хода ---


def test_post_turn_rolls_back_everything_on_failure(conn, settings) -> None:
    """Сбой посреди записи не оставляет ни реплик, ни событий, ни состояния."""
    session_id = repos.ensure_open_session(conn, now=1.0)
    state = repos.get_session_state(conn, session_id)
    ghost = beta.MasteryUpdate("ghost", 1.0, 1.0, 0.0, None)  # нет такого концепта

    with pytest.raises(sqlite3.IntegrityError):
        post_turn(
            conn,
            session_id,
            user_text="вопрос",
            assistant_text="ответ",
            state=state.model_copy(update={"hint_level": 2}),
            mastery=[ghost],
            now=2.0,
        )

    assert repos.get_messages(conn, session_id) == []
    assert repos.get_session_state(conn, session_id).hint_level == 0


async def test_typed_option_number_is_accepted(conn, settings) -> None:
    """Номер пункта из списка задания (нумерация с 1) — верный ответ."""
    load_seed(conn)
    client = _FakeTutor("тьютор не должен вызываться")
    start_practice_reply(conn, now=1.0, settings=settings)
    measured = _state(conn).current_node_id

    reply = await handle_turn(conn, client, "m", "1", now=2.0, settings=settings)

    assert "Верно" in reply.text
    assert client.calls == []
    assert repos.get_mastery(conn, measured)["alpha"] > 1.0


async def test_question_while_task_pending_keeps_task(conn, settings) -> None:
    """Вопрос вместо ответа не съедает задание и не пишет неверный ответ."""
    load_seed(conn)
    ingest_text(conn, "# T\n\n## Grouping\n\ngroupby aggregates rows\n", "u")
    start_practice_reply(conn, now=1.0, settings=settings)
    pending_before = _state(conn).pending_item_id

    reply = await handle_turn(
        conn, _FakeTutor("объясняю"), "m", "а что такое groupby?", now=2.0, settings=settings
    )

    assert "объясняю" in reply.text
    assert "ждёт ответа" in reply.text  # ученику сказали, что задание не сброшено
    assert _state(conn).pending_item_id == pending_before
    assert repos.get_events(conn) == []  # свидетельство не записано


def test_skip_pending_clears_without_evidence(conn, settings) -> None:
    load_seed(conn)
    start_practice_reply(conn, now=1.0, settings=settings)

    reply = skip_pending(conn, now=2.0, settings=settings)

    assert _state(conn).pending_item_id is None
    assert repos.get_events(conn) == []
    assert reply


def test_skip_without_task_is_harmless(conn, settings) -> None:
    assert skip_pending(conn, now=1.0, settings=settings) == NOTHING_TO_SKIP_REPLY


# --- рубричный грейдер ---


async def test_open_answer_is_graded_with_limited_weight(conn, settings) -> None:
    """Открытый ответ оценивает грейдер, событие идёт с ограниченным весом."""
    load_seed(conn)
    _set_state(conn, pending_item_id=9, current_node_id="groupby")
    item = repos.get_item(conn, 9)
    client = _FakeGraderClient(_full_verdict(conn, 9))

    reply = await handle_turn(
        conn, client, "m", "groupby разбивает строки по ключу", now=2.0, settings=settings
    )

    assert "Верно" in reply.text
    events = repos.get_events(conn)
    assert events
    assert {event.source for event in events} == {"rubric"}
    assert all(
        event.weight == item.concept_weights[event.concept_id] * settings.rubric_evidence_weight
        for event in events
    )
    assert _state(conn).pending_item_id != 9  # отвеченное задание снято


async def test_uncheckable_task_is_dropped_not_stuck(conn, settings) -> None:
    """Задание, которое нечем проверить, снимается, а не висит вечно."""
    load_seed(conn)
    conn.execute("UPDATE criteria SET active = 0 WHERE rubric_id = 1")
    conn.commit()
    _set_state(conn, pending_item_id=9, current_node_id="groupby")

    reply = await handle_turn(conn, _FakeTutor(), "m", "любой ответ", now=2.0, settings=settings)

    assert "не удалось проверить" in reply.text
    assert _state(conn).pending_item_id != 9  # сломанное задание снято
    assert len(repos.get_messages(conn, repos.get_open_session(conn))) == 2
    assert repos.get_events(conn) == []


async def test_open_answer_without_confirmed_quote_is_not_credited(conn, settings) -> None:
    """Модель «засчитала», но цитаты в ответе нет — балл нулевой."""
    load_seed(conn)
    _set_state(conn, pending_item_id=9, current_node_id="groupby")
    fake = RubricVerdict(
        criteria=[
            CriterionVerdict(id=criterion.id, passed=True, quote="цитата, которой нет")
            for criterion in repos.get_criteria(conn, 1)
        ]
    )

    reply = await handle_turn(
        conn, _FakeGraderClient(fake), "m", "совсем не про это", now=2.0, settings=settings
    )

    assert "Не совсем" in reply.text


async def test_turn_records_route_in_state(conn, settings) -> None:
    """После хода в состоянии сессии лежит снимок маршрута."""
    load_seed(conn)

    await handle_turn(conn, _FakeTutor(), "m", "привет!", now=1.0, settings=settings)

    state = _state(conn)
    assert state.route is not None
    assert state.route.steps


# --- ведение занятия (Срез 10) ---


async def test_two_clean_answers_close_node_and_move_on(conn, settings) -> None:
    """Два чистых ответа закрывают узел, и бот ведёт дальше сам."""
    load_seed(conn)
    item = repos.get_item(conn, 6)  # задание по python_basics, верный вариант первый

    for now in (1.0, 2.0):
        _set_state(conn, pending_item_id=item.id, current_node_id="python_basics")
        reply = await handle_turn(
            conn, _FakeTutor(), "m", item.options[0], now=now, settings=settings
        )

    state = _state(conn)
    assert state.current_node_id != "python_basics"  # ушли с закрытого узла
    assert state.phase == "practice"  # и сразу получили задание по новому узлу
    assert state.pending_item_id is not None
    assert "Тема «Основы Python» закрыта — идём дальше" in reply.text


async def test_stuck_keeps_student_on_the_same_node(conn, settings) -> None:
    """«Не понял» не пускает вперёд."""
    load_seed(conn)
    ingest_text(conn, "# T\n\n## Grouping\n\ngroupby aggregates rows\n", "u")
    _set_state(conn, current_node_id="groupby", node_streak=1)
    client = _FakeTutor("давай разберём подробнее", student_stuck=True)

    # Проверяем флаг модели student_stuck, а не распознавание фразы текстом.
    await handle_turn(
        conn, client, "m", "не понял groupby", now=1.0, settings=settings, allow_intents=False
    )

    state = _state(conn)
    assert state.mode == "reinforce"
    assert state.node_streak == 0
    assert state.current_node_id == "groupby"  # узел не сменился


async def test_stuck_reply_forces_reinforce_with_pending_task(conn, settings) -> None:
    """«Не понимаю» не съедает задание и включает усиленный проход."""
    from llm_tutor.core.turn import stuck_reply

    load_seed(conn)
    ingest_text(conn, "# T\n\n## Grouping\n\ngroupby aggregates rows\n", "u")
    item = repos.get_item(conn, 6)
    _set_state(conn, pending_item_id=item.id, current_node_id="python_basics", node_streak=1)
    client = _FakeTutor("объясняю подробнее")  # student_stuck по умолчанию False

    await stuck_reply(conn, client, "m", now=1.0, settings=settings)

    state = _state(conn)
    assert state.mode == "reinforce"
    assert state.node_streak == 0
    assert state.current_node_id == "python_basics"
    assert state.pending_item_id == item.id  # задание не потеряно
    assert state.task_hinted is True         # помощь оказана


def test_task_reply_carries_options_for_choice_task(conn, settings) -> None:
    """Задание с вариантами возвращается с подписями для кнопок."""
    load_seed(conn)
    _set_state(conn, current_node_id="python_basics")

    reply = start_practice_reply(conn, now=1.0, settings=settings)

    assert reply.options
    assert repos.get_item(conn, _state(conn).pending_item_id) is not None


async def test_node_without_items_says_so_honestly(conn, settings) -> None:
    """По узлу нет заданий — бот честно говорит об этом и не рвёт узел."""
    load_seed(conn)
    # Банк среза 20 покрывает все узлы: пустой узел делаем руками — случай
    # остаётся страховкой на случай правки seed.
    conn.execute(
        # GLOB, а не LIKE: в LIKE «_» — подстановочный знак, и шаблон погасил бы
        # ещё и чужие задания с похожим id узла.
        "UPDATE items SET active = 0 WHERE concept_weights GLOB '*describe_stats*'"
    )
    conn.commit()
    _set_state(conn, current_node_id="describe_stats", phase="practice")

    reply = start_practice_reply(conn, now=1.0, settings=settings)

    assert "заданий" in reply.text.lower()
    state = _state(conn)
    assert state.current_node_id == "describe_stats"  # узел не потерян
    assert state.pending_item_id is None
    assert state.phase == "explain"  # ведём диалогом


# --- фиксы ревью Срез 10 ---


async def test_answer_after_asking_is_not_clean_in_verify_pass(conn, settings) -> None:
    """В проходе спросил про задание — ответ уже не «без подсказок».

    В уроке это правило среза 21 отменено: там бот объясняет первым по своей
    схеме, и серию считают верные ответы (§5.2) — см. соседние тесты.
    """
    load_seed(conn)
    ingest_text(conn, "# T\n\n## Grouping\n\ngroupby aggregates rows\n", "u")
    item = repos.get_item(conn, 6)
    _set_state(
        conn,
        pending_item_id=item.id,
        current_node_id="python_basics",
        mode="verify",
    )

    # модель отдаёт разбор и при этом опускает уровень до нуля
    await handle_turn(
        conn, _FakeTutor("вот как это работает"), "m", "а как это работает?", now=1.0,
        settings=settings,
    )
    await handle_turn(conn, _FakeTutor(), "m", item.options[0], now=2.0, settings=settings)

    state = _state(conn)
    assert state.node_streak == 0  # помощь была — чистым ответ не считается


async def test_wrong_answer_does_not_close_by_mastery(conn, settings) -> None:
    """«Не совсем верно» и «узел закрыт» в одном ходу противоречили бы друг другу."""
    load_seed(conn)
    repos.upsert_mastery(conn, "python_basics", alpha=38.0, beta=2.0, last_seen=0.0)
    item = repos.get_item(conn, 6)
    _set_state(conn, pending_item_id=item.id, current_node_id="python_basics")

    reply = await handle_turn(
        conn, _FakeTutor(), "m", item.options[1], now=1.0, settings=settings
    )

    assert "Не совсем" in reply.text
    assert _state(conn).current_node_id == "python_basics"  # узел не закрыт


async def test_node_missing_from_graph_does_not_lock_turn(conn, settings) -> None:
    """Узел убрали из графа — ход всё равно записывается, занятие не запирается."""
    load_seed(conn)
    item = repos.get_item(conn, 6)
    _set_state(conn, pending_item_id=item.id, current_node_id="ghost_node", node_streak=5)

    reply = await handle_turn(
        conn, _FakeTutor(), "m", item.options[0], now=1.0, settings=settings
    )

    assert reply.text
    assert _state(conn).current_node_id != "ghost_node"  # узел снят
    assert len(repos.get_messages(conn, repos.get_open_session(conn))) == 2  # ход записан


def test_route_done_hands_no_task(conn, settings) -> None:
    """Маршрут исчерпан — заданий больше нет, и это не ошибка."""
    load_seed(conn)
    _set_state(
        conn,
        route=Route(steps=[RouteStep(concept_id="python_basics", mode="full", status="closed")]),
    )

    reply = start_practice_reply(conn, now=1.0, settings=settings)

    assert "пройден" in reply.text.lower()
    assert _state(conn).pending_item_id is None


def test_skip_resets_phase(conn, settings) -> None:
    """После пропуска занятие не остаётся в фазе практики."""
    load_seed(conn)
    item = repos.get_item(conn, 6)
    _set_state(conn, pending_item_id=item.id, current_node_id="python_basics", phase="practice")

    skip_pending(conn, now=1.0, settings=settings)

    assert _state(conn).phase == "explain"


# --- намерение из текста (Срез 15) ---


async def test_skip_phrase_clears_pending_item_without_evidence(conn, settings) -> None:
    """«пропусти» текстом работает как команда: свидетельство не пишется."""
    load_seed(conn)
    client = _FakeTutor("тьютор не должен вызываться")
    start_practice_reply(conn, now=1.0, settings=settings)

    reply = await handle_turn(conn, client, "m", "пропусти", now=2.0, settings=settings)

    assert reply.text == SKIP_REPLY
    assert _state(conn).pending_item_id is None
    assert repos.get_events(conn) == []
    assert client.calls == []


async def test_stuck_phrase_goes_to_tutor_even_with_pending_item(conn, settings) -> None:
    """«не понял» при висящем задании — просьба о помощи, а не ответ."""
    load_seed(conn)
    ingest_text(conn, "# T\n\n## Grouping\n\ngroupby aggregates rows\n", "u")
    _set_state(conn, pending_item_id=1, current_node_id="pandas_intro")
    client = _FakeTutor("Давай разберём")

    reply = await handle_turn(conn, client, "m", "не понял", now=1.0, settings=settings)

    assert "Давай разберём" in reply.text
    assert STUCK_NOTE in reply.text
    assert _state(conn).mode == "reinforce"
    assert _state(conn).pending_item_id == 1  # задание не тронуто
    assert client.calls  # модель спросили


async def test_intents_can_be_disabled(conn, settings) -> None:
    """Подпись варианта ответа не должна перехватываться как намерение."""
    load_seed(conn)
    _set_state(conn, pending_item_id=1, current_node_id="pandas_intro")

    reply = await handle_turn(
        conn, _FakeTutor(), "m", "пропусти", now=1.0, settings=settings, allow_intents=False
    )

    assert "Не совсем" in reply.text  # ушло в ветку ответа
    assert any(event.source == "checked" for event in repos.get_events(conn))


# --- проверочный проход: выдача заданий (Срез 16) ---


async def test_verify_mode_asks_explanation_first(conn, settings) -> None:
    """В режиме прохода шапка и «объясни своими словами» ставятся кодом."""
    load_seed(conn)
    _set_state(conn, current_node_id="groupby", mode="verify", phase="practice")

    reply = start_practice_reply(conn, now=1.0, settings=settings)

    assert "Проверка" in reply.text
    assert "шаг 1" in reply.text
    assert VERIFY_EXPLAIN_PREFIX in reply.text
    assert _state(conn).verify_item_ids == [_state(conn).pending_item_id]


async def test_verify_mode_does_not_repeat_item_in_one_pass(conn, settings) -> None:
    """Второй шаг прохода берёт другое задание, а не то же самое."""
    load_seed(conn)
    _set_state(
        conn, current_node_id="groupby", mode="verify", phase="practice", verify_item_ids=[9]
    )

    reply = start_practice_reply(conn, now=1.0, settings=settings)

    assert _state(conn).pending_item_id != 9
    assert "шаг 2" in reply.text


async def test_verify_mode_reports_exhausted_pass(conn, settings) -> None:
    """Задания узла кончились — честный отказ, режим снят."""
    load_seed(conn)
    used = [
        item.id
        for item in repos.get_items(conn)
        if "pandas_intro" in item.concept_weights
    ]
    _set_state(
        conn,
        current_node_id="pandas_intro",
        mode="verify",
        phase="practice",
        verify_item_ids=used,
    )

    reply = start_practice_reply(conn, now=1.0, settings=settings)

    assert reply.text == VERIFY_EXHAUSTED_REPLY
    assert _state(conn).mode is None
    assert _state(conn).verify_item_ids == []


async def test_normal_task_is_not_marked_as_verification(conn, settings) -> None:
    """Обычная выдача задания не несёт шапки прохода."""
    load_seed(conn)

    reply = start_practice_reply(conn, now=1.0, settings=settings)

    assert "Проверка" not in reply.text
    assert _state(conn).verify_item_ids == []


# --- «закрой тему»: фраза и флаг модели (Срез 16.6) ---


async def test_close_phrase_starts_verification_with_pending_item(conn, settings) -> None:
    """«закрой тему» при висящем задании идёт в проверку, а не в ответ."""
    load_seed(conn)
    _set_state(conn, current_node_id="groupby", pending_item_id=1)

    reply = await handle_turn(conn, _FakeTutor(), "m", "закрой тему", now=1.0, settings=settings)

    assert "Проверка" in reply.text
    assert _state(conn).mode == "verify"
    assert not any(event.source == "checked" for event in repos.get_events(conn))


async def test_model_flag_starts_verification(conn, settings) -> None:
    """Формулировку вне таблицы фраз ловит модель."""
    load_seed(conn)
    ingest_text(conn, "# T\n\n## Grouping\n\ngroupby aggregates rows\n", "u")
    _set_state(conn, current_node_id="groupby")
    client = _FakeTutor("Держишь тему уверенно", wants_close_topic=True)

    reply = await handle_turn(
        conn, client, "m", "давай закроем, я тут всё знаю уже", now=1.0, settings=settings
    )

    assert _state(conn).mode == "verify"
    assert "Проверка" in reply.text


async def test_close_flag_starts_pass_from_clean_state(conn, settings) -> None:
    """Проход начинается с нуля, а не с подсказок тьюторского хода."""
    load_seed(conn)
    ingest_text(conn, "# T\n\n## Grouping\n\ngroupby aggregates rows\n", "u")
    _set_state(conn, current_node_id="groupby", hint_level=3, node_streak=1)
    client = _FakeTutor("Держишь тему уверенно", wants_close_topic=True)

    await handle_turn(
        conn, client, "m", "давай закроем, я тут всё знаю уже", now=1.0, settings=settings
    )

    state = _state(conn)
    assert state.mode == "verify"
    assert state.hint_level == 0  # лестница подсказок сброшена — задумано
    assert state.node_streak == 0  # серия считается проходом, а не прежняя


async def test_stuck_flag_wins_over_close_flag(conn, settings) -> None:
    """«Застрял» приоритетнее: сначала разбираемся, потом проверяем."""
    load_seed(conn)
    ingest_text(conn, "# T\n\n## Grouping\n\ngroupby aggregates rows\n", "u")
    _set_state(conn, current_node_id="groupby")
    client = _FakeTutor("Разберём", student_stuck=True, wants_close_topic=True)

    await handle_turn(
        conn, client, "m", "закрой тему но я совсем запутался тут", now=1.0, settings=settings
    )

    assert _state(conn).mode == "reinforce"


async def test_close_flag_journals_tutor_reply(conn, settings) -> None:
    """Реплика тьютора и слова ученика попадают в журнал вместе с проходом.

    Хвост диалога для модели собирается из журнала: если реплики там нет,
    следующий ход модели не увидит того, что ученик только что прочитал.
    """
    load_seed(conn)
    ingest_text(conn, "# T\n\n## Grouping\n\ngroupby aggregates rows\n", "u")
    _set_state(conn, current_node_id="groupby")
    client = _FakeTutor("Держишь тему уверенно", wants_close_topic=True)

    reply = await handle_turn(
        conn, client, "m", "давай закроем, я тут всё знаю уже", now=1.0, settings=settings
    )

    messages = repos.get_messages(conn, repos.get_open_session(conn))
    assert messages[-2].content == "давай закроем, я тут всё знаю уже"
    assert reply.text == messages[-1].content
    assert "Держишь тему уверенно" in messages[-1].content
    assert "Проверка" in messages[-1].content


# --- «Продолжить обучение» (Срез 17.1) ---


async def test_resume_keeps_pending_item(conn, settings) -> None:
    """Висящее задание не затирается — в этом смысл «продолжить»."""
    load_seed(conn)
    start_practice_reply(conn, now=1.0, settings=settings)
    pending = _state(conn).pending_item_id

    reply = await resume_reply(conn, _FakeTutor(), "m", now=2.0, settings=settings)

    assert reply.text == PENDING_ITEM_NOTE
    assert _state(conn).pending_item_id == pending
    messages = repos.get_messages(conn, repos.get_open_session(conn))
    assert messages[-2].content == RESUME_KICKOFF_TEXT


async def test_resume_explains_first_then_gives_task(conn, settings) -> None:
    """Вход в узел: объяснение первым сообщением, задание — вторым."""
    load_seed(conn)
    ingest_text(conn, "# T\n\n## Grouping\n\ngroupby aggregates rows\n", "u")
    client = _FakeTutor("Смотри: groupby собирает строки в группы")

    reply = await resume_reply(conn, client, "m", now=1.0, settings=settings)

    assert "groupby собирает строки" in reply.text  # объяснение без задания
    assert reply.tail  # задание отдельным сообщением
    assert _state(conn).phase == "practice"
    assert _state(conn).pending_item_id is not None
    assert reply.options is not None


async def test_resume_reports_finished_route(conn, settings) -> None:
    """Маршрут исчерпан — честное сообщение, а не пустое задание."""
    load_seed(conn)
    route = Route(steps=[RouteStep(concept_id="pandas_intro", mode="skip", status="closed")])
    _set_state(conn, current_node_id=None, route=route, phase="practice")

    reply = await resume_reply(conn, _FakeTutor(), "m", now=1.0, settings=settings)

    assert reply.text == ROUTE_DONE_REPLY


async def test_close_phrase_keeps_students_words_in_journal(conn, settings) -> None:
    """В журнал идут слова ученика, а не синтетический повод прохода."""
    load_seed(conn)
    _set_state(conn, current_node_id="groupby")

    await handle_turn(conn, _FakeTutor(), "m", "закрой тему", now=1.0, settings=settings)

    messages = repos.get_messages(conn, repos.get_open_session(conn))
    assert messages[-2].content == "закрой тему"


# --- ведомый урок: вход в узел (Срез 21) ---


async def test_entering_node_explains_and_gives_first_test(conn, settings) -> None:
    """Вход в узел: объяснение первым сообщением, задание — вторым."""
    load_seed(conn)
    ingest_text(conn, "# T\n\n## S\n\ngroupby\n", "u")
    client = _FakeTutor("Сейчас разберём Python")

    reply = await handle_turn(conn, client, "m", "давай учиться", now=1.0, settings=settings)

    assert reply.text == "Сейчас разберём Python"  # объяснение без задания
    assert reply.tail  # задание отдельным сообщением
    assert _state(conn).pending_item_id is not None
    assert _state(conn).phase == "practice"


async def test_plain_reply_moves_lesson_forward(conn, settings) -> None:
    """Ремарка без задания ведёт занятие дальше — кнопки для этого нет."""
    load_seed(conn)
    _set_state(conn, current_node_id="groupby", phase="practice")

    reply = await handle_turn(
        conn, _FakeTutor(), "m", "ага, понятно", now=1.0, settings=settings
    )

    assert reply.tail  # выдали следующее задание
    assert _state(conn).pending_item_id is not None


async def test_question_does_not_move_lesson(conn, settings) -> None:
    """Вопрос оставляет занятие на месте: сначала отвечаем."""
    load_seed(conn)
    _set_state(conn, current_node_id="groupby", phase="practice")

    reply = await handle_turn(
        conn,
        _FakeTutor("Отвечаю на вопрос"),
        "m",
        "а что такое ключ?",
        now=1.0,
        settings=settings,
    )

    assert reply.tail is None
    assert _state(conn).pending_item_id is None


async def test_closed_node_leads_into_next_lesson(conn, settings) -> None:
    """Узел закрылся — бот сам объясняет следующий и даёт его первый тест."""
    load_seed(conn)
    item = repos.get_item(conn, 6)  # python_basics
    client = _FakeTutor("Теперь про Series и DataFrame")
    for now in (1.0, 2.0):
        _set_state(conn, pending_item_id=item.id, current_node_id="python_basics")
        reply = await handle_turn(
            conn, client, "m", item.options[0], now=now, settings=settings
        )

    assert "закрыта" in reply.text
    assert "Теперь про Series и DataFrame" in reply.text  # объяснение следующего
    assert reply.tail  # и его первый тест
    assert _state(conn).current_node_id != "python_basics"


async def test_lesson_answer_after_help_still_counts(conn, settings) -> None:
    """В уроке бот сам объясняет первым — подсказка серию не обнуляет."""
    load_seed(conn)
    _set_state(conn, current_node_id="groupby", task_hinted=True, node_streak=1)
    _set_state(conn, pending_item_id=9)
    client = _FakeGraderClient(_full_verdict(conn, 9))

    reply = await handle_turn(
        conn, client, "m", "groupby по ключу", now=1.0, settings=settings
    )

    # Ответ после помощи дошёл до порога серии и закрыл узел: в уроке серию
    # считают верные ответы, а не только чистые.
    assert "закрыта" in reply.text
    assert _state(conn).current_node_id != "groupby"


async def test_verify_pass_answer_after_help_does_not_count(conn, settings) -> None:
    """В проходе подсказка по-прежнему обнуляет серию."""
    load_seed(conn)
    _set_state(
        conn, current_node_id="groupby", mode="verify", task_hinted=True, node_streak=1
    )
    _set_state(conn, pending_item_id=9)
    client = _FakeGraderClient(_full_verdict(conn, 9))

    reply = await handle_turn(
        conn, client, "m", "groupby по ключу", now=1.0, settings=settings
    )

    assert _state(conn).node_streak == 0
    assert "закрыта" not in reply.text
    assert _state(conn).current_node_id == "groupby"


async def test_lesson_second_test_differs_from_first(conn, settings) -> None:
    """Второй тест узла — другое задание, а не то же самое."""
    load_seed(conn)
    _set_state(conn, current_node_id="groupby", phase="practice")
    start_practice_reply(conn, now=1.0, settings=settings)
    first_id = _state(conn).pending_item_id

    reply = await handle_turn(conn, _FakeTutor(), "m", "мимо", now=2.0, settings=settings)

    assert reply.tail  # следующее задание выдано
    assert _state(conn).pending_item_id not in (None, first_id)


async def test_lesson_cycles_tests_when_exhausted(conn, settings) -> None:
    """Оба задания выданы, серия нулевая — идём по второму кругу, не залипаем."""
    load_seed(conn)
    _set_state(conn, current_node_id="groupby", phase="practice")
    start_practice_reply(conn, now=1.0, settings=settings)
    issued = {_state(conn).pending_item_id}

    for now in (2.0, 3.0, 4.0):
        reply = await handle_turn(
            conn, _FakeTutor(), "m", "мимо", now=now, settings=settings
        )
        issued.add(_state(conn).pending_item_id)

    assert reply.tail  # задание есть всегда: узел не залипает
    assert len(issued) < 4  # третий заход — повтор, а не четвёртое «новое» задание


def test_lesson_item_ids_defaults_to_empty() -> None:
    """Старая сессия без поля читается: дефолт пустой."""
    assert SessionState().lesson_item_ids == []


@pytest.mark.parametrize("model_level", [0, 4])
async def test_stuck_raises_hint_level_by_one_step(conn, settings, model_level) -> None:
    """«не понял» поднимает лестницу на одну ступень, а не на две."""
    load_seed(conn)
    ingest_text(conn, "# T\n\n## S\n\ngroupby\n", "u")
    client = _FakeTutor(hint_level=model_level)

    await handle_turn(conn, client, "m", "не понял", now=1.0, settings=settings)

    assert _state(conn).hint_level == 1
