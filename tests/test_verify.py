"""Тесты проверочного прохода по узлу (Срез 16)."""

from llm_tutor.core import verify
from llm_tutor.core.turn import VERIFY_NO_ITEMS_REPLY
from llm_tutor.core.verify import VERIFY_NO_NODE_REPLY
from llm_tutor.course.seed import load_seed
from llm_tutor.db import repos


def _state(conn):
    session_id = repos.ensure_open_session(conn, now=1.0)
    return repos.get_session_state(conn, session_id)


def _set_state(conn, **patch) -> None:
    session_id = repos.ensure_open_session(conn, now=1.0)
    state = repos.get_session_state(conn, session_id)
    repos.update_session_state(conn, session_id, state.model_copy(update=patch))


def test_start_verification_marks_node_and_issues_task(conn, settings) -> None:
    """Проход помечает узел режимом verify и выдаёт задание с шапкой."""
    load_seed(conn)
    _set_state(conn, current_node_id="groupby")

    reply = verify.start_verification(conn, now=1.0, settings=settings)

    assert "Проверка" in reply.text
    assert _state(conn).mode == "verify"
    assert _state(conn).pending_item_id is not None
    assert _state(conn).node_streak == 0


def test_start_verification_without_node_answers_honestly(conn, settings) -> None:
    """Закрывать нечего — говорим об этом, состояние не трогаем."""
    load_seed(conn)

    reply = verify.start_verification(conn, now=1.0, settings=settings)

    assert reply.text == VERIFY_NO_NODE_REPLY
    assert _state(conn).mode is None


def test_start_verification_with_removed_node_answers_honestly(conn, settings) -> None:
    """Узел убрали из seed — не падаем KeyError, снимаем узел."""
    load_seed(conn)
    _set_state(conn, current_node_id="нет_такого_узла")

    reply = verify.start_verification(conn, now=1.0, settings=settings)

    assert reply.text == VERIFY_NO_NODE_REPLY
    assert _state(conn).current_node_id is None


def test_start_verification_without_items_answers_honestly(conn, settings) -> None:
    """У узла нет заданий — честный отказ, а не имитация проверки."""
    load_seed(conn)
    _set_state(conn, current_node_id="visualization_basics")

    reply = verify.start_verification(conn, now=1.0, settings=settings)

    assert reply.text == VERIFY_NO_ITEMS_REPLY
    assert _state(conn).mode is None


def test_restarting_pass_drops_pending_item_without_evidence(conn, settings) -> None:
    """Повторное «закрой тему» начинает проход заново, свидетельств не пишет."""
    load_seed(conn)
    _set_state(conn, current_node_id="groupby")
    verify.start_verification(conn, now=1.0, settings=settings)
    # Второй заход должен начать список выданного с нуля: без сброса он бы
    # счёл все три задания узла уже использованными и сдался.
    _set_state(conn, verify_item_ids=[4, 9, 10])

    verify.start_verification(conn, now=2.0, settings=settings)

    state = _state(conn)
    assert state.pending_item_id is not None
    assert state.verify_item_ids == [state.pending_item_id]
    assert repos.get_events(conn) == []
