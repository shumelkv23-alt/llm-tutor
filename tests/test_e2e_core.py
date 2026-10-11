"""Сквозной сценарий topic01 на моке OpenRouter (Срез 7.3, без бота — Срез 49).

Прогоняет весь путь через РЕАЛЬНЫЙ LLMClient: анкета → вопрос тьютору →
задание с автопроверкой → задание с рубрикой → маршрут. Сеть замокана respx,
БД в памяти; проверяем, что журнал событий, модель ученика и состояние сессии
сходятся.
"""

from course_fixtures import T1
import json

import httpx
import pytest
import respx
from web_fakes import GradingTutor

from llm_tutor.config import Settings
from llm_tutor.core import lesson, overview
from llm_tutor.core.turn import handle_turn, lesson_next_reply
from llm_tutor.course.ingest import ingest_text
from legacy_course import load_seed
from llm_tutor.db import repos
from llm_tutor.db.connection import get_conn, migrate
from llm_tutor.llm.client import LLMClient
from llm_tutor.llm.prompts import LLM_FAILURE_REPLY
from llm_tutor.schemas import SessionState
from llm_tutor.student import beta, survey

OPENROUTER = "https://openrouter.ai/api/v1/chat/completions"


def _completion(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def _set_pending(conn, item_id: int, concept_id: str) -> None:
    session_id = repos.ensure_open_session(conn, now=1.0)
    state = repos.get_session_state(conn, session_id)
    repos.update_session_state(
        conn, session_id, state.model_copy(update={"pending_item_id": item_id, "current_node_id": concept_id})
    )


def _client(settings: Settings) -> LLMClient:
    return LLMClient(
        base_url=settings.openrouter_base_url,
        api_key="test-key",
        default_model=settings.tutor_model,
        max_retries=0,
    )


@respx.mock
async def test_offtopic_question_still_gets_an_answer() -> None:
    """Вопрос вне темы получает ответ с пометкой происхождения, а не отказ."""
    settings = Settings(
        _env_file=None, openrouter_api_key="test-key"
    )
    conn = get_conn(":memory:")
    migrate(conn)
    load_seed(conn)  # материал специально не загружаем
    route = respx.post(OPENROUTER).mock(
        return_value=_completion(
            json.dumps({"reply": "Это за пределами темы 1", "hint_level": 0})
        )
    )
    client = _client(settings)
    try:
        reply = await handle_turn(
            conn, client, "m", "как обучают нейросети?", now=1.0, settings=settings
        )

        assert reply.text == "Это за пределами темы 1"
        assert route.called
    finally:
        await client.aclose()
        conn.close()


@respx.mock
async def test_provider_error_is_reported_and_turn_persisted() -> None:
    """Сбой провайдера не оставляет ученика без ответа, ход записан."""
    settings = Settings(
        _env_file=None, openrouter_api_key="test-key"
    )
    conn = get_conn(":memory:")
    migrate(conn)
    load_seed(conn)
    ingest_text(conn, "# T\n\n## Grouping\n\ngroupby aggregates rows\n", "u")
    respx.post(OPENROUTER).mock(return_value=httpx.Response(500, text="boom"))
    client = _client(settings)
    try:
        # Вход в тему без модели: урок не блокируется — описание темы из графа
        # и «Проверим» (срез 28).
        entering = await handle_turn(
            conn, client, "m", "как работает groupby?", now=1.0, settings=settings
        )
        assert entering.text.startswith("📘")
        assert entering.button is not None

        # Вопрос посреди урока без модели — честное «не смог получить ответ».
        reply = await handle_turn(
            conn, client, "m", "а зачем это нужно?", now=2.0, settings=settings
        )

        assert reply.text.startswith(LLM_FAILURE_REPLY)
        session_id = repos.get_open_session(conn)
        assert len(repos.get_messages(conn, session_id)) == 4
    finally:
        await client.aclose()
        conn.close()


@respx.mock
async def test_full_topic01_scenario() -> None:
    settings = Settings(
        _env_file=None,
        openrouter_api_key="test-key",
        guide_success_streak=2,  # сценарий проверяет конвейер, а не правило закрытия
    )
    conn = get_conn(":memory:")
    migrate(conn)
    load_seed(conn)
    ingest_text(conn, "# T\n\n## Grouping\n\ngroupby aggregates rows into groups\n", "u")

    queued = [
        _completion(json.dumps({"reply": "Смотри на groupby", "hint_level": 1})),
        _completion(
            json.dumps(
                {
                    "criteria": [
                        {"id": 1, "passed": True, "quote": "groupby"},
                        {"id": 2, "passed": True, "quote": "agg"},
                        {"id": 3, "passed": False, "quote": None},
                    ],
                    "confidence": 0.9,
                }
            )
        ),
        # Закрытие узла ведёт в следующий урок: объявление, объяснение нового
        # узла (второй вызов модели за ход) и его первое задание.
        _completion(json.dumps({"reply": "Дальше — выборка и сортировка", "hint_level": 0})),
    ]
    requests: list[dict] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return queued.pop(0)

    respx.post(OPENROUTER).mock(side_effect=_handler)

    client = _client(settings)
    try:
        # 1. Анкета: профиль и слабый априор, без обращения к модели
        survey.apply_answers(
            conn,
            T1,
            {block.key: 1 for block in T1.blocks},
            now=0.0,
            settings=settings,
        )
        assert survey.is_completed(conn, T1)
        assert repos.get_events(conn)  # самооценка записана в журнал

        # 2. Вопрос тьютору — структурированный ответ модели
        reply = await handle_turn(
            conn, client, "m", "как работает groupby?", now=1.0, settings=settings
        )
        assert reply.text == "Смотри на groupby"
        session_id = repos.get_open_session(conn)
        assert repos.get_session_state(conn, session_id).hint_level == 1
        assert len(requests) == 1

        # 3. Задание с автопроверкой — отвечает код, модель не вызывается
        _set_pending(conn, 6, "python_basics")
        answer = repos.get_item(conn, 6).options[0]
        reply = await handle_turn(conn, client, "m", answer, now=2.0, settings=settings)
        assert "Верно" in reply.text
        assert len(requests) == 1  # модель не спрашивали
        assert beta.estimate(conn, "python_basics", now=2.0, settings=settings).mean > 0.5

        # 4. Задание с рубрикой — оценивает грейдер, вес свидетельства ниже
        _set_pending(conn, 9, "groupby")
        reply = await handle_turn(
            conn, client, "m", "groupby разбивает строки, agg считает", now=3.0, settings=settings
        )
        assert "Верно" in reply.text  # 2 из 3 критериев — выше порога
        # грейдер плюс объяснение следующего узла: узел закрылся в этом ходу
        assert len(requests) == 3
        graded = [e for e in repos.get_events(conn) if e.source == "rubric"]
        assert graded
        assert all(e.result == pytest.approx(2 / 3) for e in graded)  # частичный балл
        weights = repos.get_item(conn, 9).concept_weights
        assert all(  # вес урезан до доли (§9.2: вердикт модели не решает в одиночку)
            e.weight == pytest.approx(weights[e.concept_id] * settings.rubric_evidence_weight)
            for e in graded
        )

        # 5. Маршрут считается: есть шаги, счётчик совпадает со списком
        plan = overview.route_overview(conn, now=3.0, settings=settings)
        assert plan.total == len(plan.steps) > 0

        # 6. Состояние сессии пережило все ходы и осталось согласованным
        state = repos.get_session_state(conn, session_id)
        assert state.pending_item_id != 9  # отвеченное задание снято
        assert state.attempts == 2
        messages = repos.get_messages(conn, session_id)
        assert [m.role for m in messages][:2] == ["user", "assistant"]
        assert len(messages) == 6  # три хода по две реплики
    finally:
        await client.aclose()
        conn.close()


def _state(conn) -> SessionState:
    """Состояние открытой сессии."""
    return repos.get_session_state(conn, repos.get_open_session(conn))


async def _finish_survey(conn, settings, answers: list[int]):
    """Анкета ответами ``answers`` и первый ход урока — как после «Начать урок»."""
    progress = survey.Progress(T1)
    for index in answers:
        progress = progress.answer(index)
    assert progress.next_key() is None
    survey.apply_answers(conn, T1, progress.final_answers(), level=progress.level, settings=settings)
    return await lesson.begin_lesson(
        conn, GradingTutor(conn, passed=True), "m", settings=settings,
        claimed=survey.claimed_concepts(conn, T1),
    )


async def test_new_student_goes_from_start_to_closed_node(conn, settings) -> None:
    """Анкета «С нуля» → шаги → объяснение → пара тестов → узел закрыт."""
    load_seed(conn)
    ingest_text(conn, "# T\n\n## Grouping\n\ngroupby aggregates rows\n", "u")
    start, reply = await _finish_survey(conn, settings, [survey.LEVEL_FROM_SCRATCH])

    assert start.upcoming and reply is not None
    assert reply.button is not None  # урок начался первой частью (срез 28)
    assert _state(conn).pending_item_id is None
    assert not _is_closed(conn, "python_basics")  # до ответов узел не закрыт

    # «Дальше» до «Проверим» — и вопрос по теме.
    task = await _press_until_task(conn, settings)
    assert task.item_id is not None

    # Верный ответ закрывает тему (срез 28: один вопрос на тему).
    client = GradingTutor(conn, passed=True)
    item = repos.get_item(conn, _state(conn).pending_item_id)
    assert "python_basics" in item.concept_weights
    answer = item.options[int(item.answer)] if item.options else str(item.answer)
    await handle_turn(
        conn, client, "m", answer, now=3.0, settings=settings,
        allow_intents=False, answer_mode="answer",
    )

    assert _is_closed(conn, "python_basics")
    assert _state(conn).current_node_id != "python_basics"  # урок пошёл дальше


async def _press_until_task(conn, settings):
    """Жмёт кнопку урока («Дальше», затем «Проверим»), пока не выдан вопрос."""
    for now in (2.0, 2.1, 2.2, 2.3):
        state = _state(conn)
        reply = lesson_next_reply(
            conn, state.current_node_id, state.lesson_part, now=now, settings=settings
        )
        if reply.item_id is not None:
            return reply
    raise AssertionError("кнопка урока так и не дала вопрос")


def _is_closed(conn, node_id: str) -> bool:
    """Закрыт ли узел — по снимку маршрута (его пишет код закрытия узла).

    Снимок, а не `beta.estimate`: закрытие по серии чистых ответов не обязано
    поднимать владение выше порога, а `build_route` сохраняет отметку закрытия
    из предыдущего снимка.
    """
    session_id = repos.get_open_session(conn)
    if session_id is None:
        return False
    state = repos.get_session_state(conn, session_id)
    if state.route is None:
        return False
    return any(
        step.concept_id == node_id and step.status == "closed" for step in state.route.steps
    )


async def test_confident_student_skips_known_blocks(conn, settings) -> None:
    """«Уверенно работаю с pandas» → два ответа → урок не с азов и не с проверки."""
    load_seed(conn)
    start, reply = await _finish_survey(conn, settings, [survey.LEVEL_CONFIDENT, 1, 0])

    assert reply is not None
    assert not start.starts_with_check  # незаявленное есть — начинаем с урока
    current = _state(conn).current_node_id
    assert current == start.first
    assert current not in survey.claimed_concepts(conn)
    assert reply.button is not None  # урок начался с первой части, а не с проверки
    assert _state(conn).mode != "verify"
