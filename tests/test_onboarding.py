"""Тесты согласования маршрута на входе в курс (Срез 18)."""

from fakes import FakeCallback, FakeMessage, _fsm, _named

from llm_tutor.bot.onboarding import make_onboarding_router
from llm_tutor.course.seed import load_seed
from llm_tutor.db import repos
from llm_tutor.student import beta


def _state(conn):
    session_id = repos.ensure_open_session(conn, now=1.0)
    return repos.get_session_state(conn, session_id)


async def test_route_node_offers_three_actions(conn, settings) -> None:
    """Нажатие узла предлагает: знаю / не знаю / оставить."""
    load_seed(conn)
    router = make_onboarding_router(conn, settings)
    on_node = _named(router, "callback_query", "on_route_node")
    message = FakeMessage()

    await on_node(FakeCallback("route:node:read_csv", message))

    callbacks = [
        button.callback_data for row in message.sent[-1][1].inline_keyboard for button in row
    ]
    assert callbacks == ["route:know:read_csv", "route:unknown:read_csv", "route:leave"]


async def test_know_writes_weak_evidence_and_does_not_close(conn, settings) -> None:
    """«Я это знаю» двигает владение слабо и узел НЕ закрывает."""
    load_seed(conn)
    router = make_onboarding_router(conn, settings)
    on_know = _named(router, "callback_query", "on_route_know")

    await on_know(FakeCallback("route:know:read_csv", FakeMessage()), _fsm())

    mastery = beta.estimate(conn, "read_csv", now=1.0, settings=settings)
    assert mastery.mean > 0.5
    assert mastery.mean < settings.mastery_skip_threshold


async def test_unknown_lowers_priority(conn, settings) -> None:
    """«Я это не знаю» — свидетельство против владения."""
    load_seed(conn)
    router = make_onboarding_router(conn, settings)
    on_unknown = _named(router, "callback_query", "on_route_unknown")

    await on_unknown(FakeCallback("route:unknown:read_csv", FakeMessage()), _fsm())

    mastery = beta.estimate(conn, "read_csv", now=1.0, settings=settings)
    assert mastery.mean < 0.5


async def test_route_ok_fixes_route_and_offers_resume(conn, settings) -> None:
    """«Меня всё устраивает» фиксирует маршрут и даёт кнопку «Продолжить»."""
    load_seed(conn)
    router = make_onboarding_router(conn, settings)
    on_ok = _named(router, "callback_query", "on_route_ok")
    message = FakeMessage()

    await on_ok(FakeCallback("route:ok", message), _fsm())

    text, markup = message.sent[-1]
    assert "Маршрут зафиксирован" in text
    assert markup.inline_keyboard[0][0].callback_data == "menu:resume"


async def test_unknown_node_callback_is_refused(conn, settings) -> None:
    """Подделанный колбэк узла не роняет хендлер."""
    load_seed(conn)
    router = make_onboarding_router(conn, settings)
    on_node = _named(router, "callback_query", "on_route_node")
    message = FakeMessage()

    await on_node(FakeCallback("route:node:нет_такого", message))

    assert message.sent == []


async def test_route_review_text_gets_hint(conn, settings) -> None:
    """На экране согласования текст отвечает подсказкой, а не тьютором."""
    load_seed(conn)
    router = make_onboarding_router(conn, settings)
    on_text = _named(router, "message", "on_route_text")
    message = FakeMessage("я это знаю")

    await on_text(message)

    assert "кнопкой" in message.last_text
