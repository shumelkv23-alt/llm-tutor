"""Тесты проверочного прохода по узлу (Срез 16)."""

from web_fakes import GradingTutor

from llm_tutor.core import verify
from llm_tutor.core.turn import VERIFY_FAILED_NOTE, VERIFY_NO_ITEMS_REPLY, handle_turn
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

    assert "Проверка" in reply.tail
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
    # Банк среза 20 покрывает все узлы: пустой узел делаем руками — случай
    # остаётся страховкой на случай правки seed.
    conn.execute(
        # GLOB, а не LIKE: в LIKE «_» — подстановочный знак, и шаблон погасил бы
        # ещё и чужие задания с похожим id узла.
        "UPDATE items SET active = 0 WHERE concept_weights GLOB '*visualization_basics*'"
    )
    conn.commit()
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


# --- исходы прохода (Срез 16.4) ---
#
# Узел берём `groupby`: это единственный узел с более чем одним заданием в
# банке (id 4, 9, 10), поэтому серия из двух чистых ответов достижима только
# на нём; задания 9 и 10 рубричные, отсюда грейдер в одной роли с тьютором.


async def test_pass_closes_node_after_two_clean_answers(conn, settings) -> None:
    """Проход закрывает узел обычным критерием — серией чистых ответов."""
    load_seed(conn)
    _set_state(conn, current_node_id="groupby")
    verify.start_verification(conn, now=1.0, settings=settings)
    client = GradingTutor(conn, passed=True)

    for ts in (2.0, 3.0):
        reply = await handle_turn(
            conn, client, "m", "groupby группирует строки по ключу", now=ts, settings=settings
        )

    assert "закрыт" in reply.text
    assert _state(conn).verify_item_ids == []
    assert _state(conn).mode is None


async def test_wrong_answer_drops_the_pass(conn, settings) -> None:
    """Неверный ответ обрывает проход — узел уходит в усиленный проход."""
    load_seed(conn)
    _set_state(conn, current_node_id="groupby")
    verify.start_verification(conn, now=1.0, settings=settings)
    client = GradingTutor(conn, passed=False)

    reply = await handle_turn(conn, client, "m", "не знаю", now=2.0, settings=settings)

    assert VERIFY_FAILED_NOTE in reply.text
    assert _state(conn).mode == "reinforce"
    assert _state(conn).verify_item_ids == []
    assert "Проверка" not in reply.text  # проход оборван, шагов больше нет


async def test_ungradable_item_does_not_drop_the_pass(conn, settings) -> None:
    """Задание, которое не удалось проверить, — сбой, а не провал ученика."""
    load_seed(conn)
    _set_state(conn, current_node_id="groupby", mode="verify", phase="practice")
    _set_state(conn, pending_item_id=999)  # задание пропало из банка
    client = GradingTutor(conn, passed=False)

    reply = await handle_turn(
        conn, client, "m", "groupby группирует строки по ключу", now=2.0, settings=settings
    )

    assert VERIFY_FAILED_NOTE not in reply.text
    assert _state(conn).mode == "verify"


async def test_item_without_rubric_does_not_drop_the_pass(conn, settings) -> None:
    """Задание, которое код не умеет проверить, — сбой, а не провал ученика."""
    load_seed(conn)
    conn.execute("UPDATE items SET answer_type = 'open', rubric_id = NULL WHERE id = 9")
    conn.commit()
    _set_state(
        conn, current_node_id="groupby", mode="verify", phase="practice", pending_item_id=9
    )
    client = GradingTutor(conn, passed=False)

    reply = await handle_turn(
        conn, client, "m", "groupby группирует строки", now=2.0, settings=settings
    )

    assert VERIFY_FAILED_NOTE not in reply.text
    assert _state(conn).mode == "verify"
