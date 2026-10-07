"""Тесты навигации по узлам темы."""

from llm_tutor.bot import themes
from llm_tutor.course.graph import CourseGraph
from llm_tutor.course.seed import load_seed
from llm_tutor.db import repos
from llm_tutor.schemas import Route, RouteStep, SessionState


def test_node_status_marks_current(conn, settings) -> None:
    load_seed(conn)
    graph = CourseGraph.load(conn)
    state = SessionState(current_node_id="groupby")

    assert themes.node_status(conn, graph, "groupby", state, now=0.0, settings=settings) == "current"


def test_node_status_ahead_then_available(conn, settings) -> None:
    """Узел впереди, пока пререквизиты открыты; доступен, когда закрыты."""
    load_seed(conn)
    graph = CourseGraph.load(conn)
    node = next(n for n in graph.topo_order() if graph.hard_prerequisites(n))
    state = SessionState()

    assert themes.node_status(conn, graph, node, state, now=0.0, settings=settings) == "ahead"
    for prereq in graph.hard_prerequisites(node):
        repos.upsert_mastery(conn, prereq, alpha=38.0, beta=2.0, last_seen=0.0, next_review=1e9)
    assert themes.node_status(conn, graph, node, state, now=0.0, settings=settings) == "available"


def test_themes_keyboard_has_button_per_node(conn, settings) -> None:
    load_seed(conn)
    graph = CourseGraph.load(conn)

    kb = themes.themes_keyboard(conn, state=SessionState(), now=0.0, settings=settings)

    callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert len(callbacks) == len(graph.node_ids)
    assert all(cb.startswith("theme:") for cb in callbacks)


def test_node_status_reads_closure_from_route_snapshot(conn, settings) -> None:
    """Узел, закрытый серией в снимке маршрута, — closed и без мастерства."""
    load_seed(conn)
    graph = CourseGraph.load(conn)
    node = next(n for n in graph.topo_order() if graph.hard_prerequisites(n))
    state = SessionState(
        route=Route(steps=[RouteStep(concept_id=node, mode="full", status="closed")])
    )

    assert themes.node_status(conn, graph, node, state, now=0.0, settings=settings) == "closed"


def test_node_status_prereq_closed_by_route_opens_dependent(conn, settings) -> None:
    """Жёсткий пререквизит, закрытый в снимке, делает зависимый узел available."""
    load_seed(conn)
    graph = CourseGraph.load(conn)
    node = next(n for n in graph.topo_order() if graph.hard_prerequisites(n))
    closed = [
        RouteStep(concept_id=p, mode="full", status="closed")
        for p in graph.hard_prerequisites(node)
    ]
    state = SessionState(route=Route(steps=closed))

    status = themes.node_status(conn, graph, node, state, now=0.0, settings=settings)
    assert status == "available"


async def test_switch_node_changes_current_and_clears_pending(conn, settings) -> None:
    load_seed(conn)
    item = repos.get_item(conn, 6)
    session_id = repos.ensure_open_session(conn, now=1.0)
    repos.update_session_state(
        conn,
        session_id,
        SessionState(current_node_id="python_basics", pending_item_id=item.id),
    )

    themes.switch_node(conn, "groupby", now=2.0, settings=settings)

    state = repos.get_session_state(conn, session_id)
    assert state.current_node_id == "groupby"
    assert state.pending_item_id is None
    assert state.phase == "explain"


# --- роутер: подтверждение «вперёд», переход и отмена ---


class FakeMessage:
    """Подставное сообщение: помнит всё, что бот в него отправил."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, object]] = []

    async def answer(self, text: str, reply_markup=None, **kwargs) -> None:
        self.sent.append((text, reply_markup))

    @property
    def last_text(self) -> str:
        return self.sent[-1][0]


class FakeCallback:
    """Подставное нажатие инлайн-кнопки."""

    def __init__(self, data: str, message: FakeMessage) -> None:
        self.data = data
        self.message = message
        self.answers: list[tuple] = []

    async def answer(self, *args, **kwargs) -> None:
        self.answers.append(args)


def _named(router, kind: str, name: str):
    """Находит хендлер по имени функции — не зависит от порядка регистрации."""
    for handler in getattr(router, kind).handlers:
        if handler.callback.__name__ == name:
            return handler.callback
    raise AssertionError(f"нет хендлера {name}")


def _ahead_node(graph: CourseGraph) -> str:
    """Первый узел топопорядка с жёстким пререквизитом (то есть «впереди»)."""
    return next(n for n in graph.topo_order() if graph.hard_prerequisites(n))


async def test_theme_ahead_asks_for_confirmation(conn, settings) -> None:
    """Прыжок вперёд — сначала предупреждение и кнопки, тема не меняется."""
    load_seed(conn)
    node = _ahead_node(CourseGraph.load(conn))
    session_id = repos.ensure_open_session(conn, now=1.0)
    repos.update_session_state(conn, session_id, SessionState(current_node_id="groupby"))
    router = themes.make_themes_router(conn, settings)
    message = FakeMessage()

    await _named(router, "callback_query", "on_theme")(
        FakeCallback(f"theme:{node}", message)
    )

    text, keyboard = message.sent[-1]
    assert "не закрыты" in text
    callbacks = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert callbacks == [f"theme_go:{node}", "theme_cancel"]
    # Это чтение, а не ход: текущая тема осталась прежней.
    assert repos.get_session_state(conn, session_id).current_node_id == "groupby"


async def test_theme_available_switches_node(conn, settings) -> None:
    """Доступный узел выбирается сразу: смена темы и снятие задания."""
    load_seed(conn)
    item = repos.get_item(conn, 6)
    session_id = repos.ensure_open_session(conn, now=1.0)
    repos.update_session_state(
        conn,
        session_id,
        SessionState(current_node_id="groupby", pending_item_id=item.id),
    )
    router = themes.make_themes_router(conn, settings)
    message = FakeMessage()

    await _named(router, "callback_query", "on_theme")(
        FakeCallback("theme:python_basics", message)
    )

    assert "Ок, тема" in message.last_text
    state = repos.get_session_state(conn, session_id)
    assert state.current_node_id == "python_basics"
    assert state.pending_item_id is None
    assert state.phase == "explain"


async def test_theme_go_switches_node(conn, settings) -> None:
    """Подтверждённый прыжок вперёд выполняет переход."""
    load_seed(conn)
    session_id = repos.ensure_open_session(conn, now=1.0)
    router = themes.make_themes_router(conn, settings)
    message = FakeMessage()
    callback = FakeCallback("theme_go:groupby", message)

    await _named(router, "callback_query", "on_theme_go")(callback)

    assert "Ок, тема" in message.last_text
    assert repos.get_session_state(conn, session_id).current_node_id == "groupby"
    assert callback.answers  # «часик» не зависает


async def test_theme_cancel_answers_without_change(conn, settings) -> None:
    """Отказ от прыжка: только ответ на нажатие, состояние не трогаем."""
    load_seed(conn)
    session_id = repos.ensure_open_session(conn, now=1.0)
    repos.update_session_state(conn, session_id, SessionState(current_node_id="groupby"))
    router = themes.make_themes_router(conn, settings)
    message = FakeMessage()
    callback = FakeCallback("theme_cancel", message)

    await _named(router, "callback_query", "on_theme_cancel")(callback)

    assert callback.answers[0][0] == "Остаёмся на месте"
    assert repos.get_session_state(conn, session_id).current_node_id == "groupby"


def test_themes_keyboard_without_session_does_not_open_one(conn, settings) -> None:
    """Список тем — чтение: сессию не заводит, даже если её ещё нет."""
    load_seed(conn)

    themes.themes_keyboard(conn, now=0.0, settings=settings)

    assert repos.get_open_session(conn) is None
