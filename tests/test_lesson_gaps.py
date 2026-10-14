"""Недописанное занятие не запирает ученика.

Курс пишется по частям: автор открывает модуль, когда часть заданий ещё не
написана. Раньше это запирало ученика навсегда — «По этому узлу новых заданий
сейчас нет» и ни кнопки, ни движения с места:

* нет итогового задания — занятие не закрывалось (`final_passed` не набрать);
* нет заданий у раздела — раздел не проходил, итоговое не наступало;
* нет заданий вовсе — то же самое с самого начала.

Правило: часть занятия, по которой в банке нет ни одного задания, проверить
нечем, и держать на ней ученика нельзя. Раздел помечается непроверяемым
(``skipped_sections``), итоговое задание, которого нет, не требуется.
"""

from course_fixtures import correct_answer, load_lessons
from test_lesson_sections import _answer, _check, _enter, _pending, _state, _step

from llm_tutor.core.turn import lesson_next_reply
from llm_tutor.schemas import SessionState
from llm_tutor.student import beta, guide

# Банк фикстуры: 3001/3002 — раздел series, 3003/3004 — dataframe, 3005 — итоговое.
SERIES, FRAME, FINAL = (3001, 3002), (3003, 3004), 3005


def _off(conn, *ids: int) -> None:
    """Гасит задания банка: так выглядит недописанный урок."""
    conn.execute(f"UPDATE items SET active = 0 WHERE id IN ({','.join('?' * len(ids))})", ids)
    conn.commit()


async def test_lesson_without_final_closes_after_its_sections(conn, settings) -> None:
    load_lessons(conn)
    _off(conn, FINAL)
    await _enter(conn, settings)
    for _ in range(2):
        _check(conn, settings)
        await _answer(conn, settings, correct_answer(_pending(conn)))

    state = _state(conn)

    assert _step(conn, "l1_frames").status == "closed", "занятие осталось незакрытым"
    assert state.current_node_id == "l1_select"
    assert not state.final_passed, "итоговое не сдавали — и не должны были"


async def test_section_without_items_is_skipped_and_lesson_closes(conn, settings) -> None:
    load_lessons(conn)
    _off(conn, *SERIES)
    await _enter(conn, settings)

    _check(conn, settings)

    state = _state(conn)
    assert state.skipped_sections == ["series"], "раздел без заданий не помечен"
    assert state.passed_sections == [], "непроверяемый раздел — не «пройден»"
    assert state.section_id == "dataframe"
    assert _pending(conn).section == "dataframe", "спросили не по тому разделу"

    await _answer(conn, settings, correct_answer(_pending(conn)))
    await _answer(conn, settings, correct_answer(_pending(conn)), now=5.0)

    assert _step(conn, "l1_frames").status == "closed"
    assert _state(conn).current_node_id == "l1_select"


async def test_lesson_without_items_closes_on_the_check(conn, settings) -> None:
    load_lessons(conn)
    _off(conn, *SERIES, *FRAME, FINAL)
    await _enter(conn, settings)

    reply = _check(conn, settings)

    assert "закрыто" in reply.text
    assert _step(conn, "l1_frames").status == "closed"
    assert _state(conn).current_node_id == "l1_select"


async def test_lesson_without_any_items_does_not_stall_the_next_lesson(conn, settings) -> None:
    """Занятие без заданий пропускает ученика, а не оставляет его на «нет заданий»."""
    load_lessons(conn)
    _off(conn, *SERIES, *FRAME, FINAL)

    await _enter(conn, settings)
    _check(conn, settings)

    state = _state(conn)
    assert state.current_node_id == "l1_select"
    # Следующее занятие начинается с обычного урока: ученик не в тупике.
    reply = lesson_next_reply(conn, "l1_select", 0, now=6.0, settings=settings)
    assert reply.button is not None or _state(conn).pending_item_id is not None


async def test_lesson_with_one_item_per_section_still_asks(conn, settings) -> None:
    """Раздел с одним заданием проверяем: пропускаем только пустые."""
    load_lessons(conn)
    _off(conn, SERIES[1], FRAME[1])
    await _enter(conn, settings)

    _check(conn, settings)

    state = _state(conn)
    assert state.skipped_sections == [], "раздел с заданием пропускать нельзя"
    assert _pending(conn).section == "series"


def test_criterion_does_not_demand_the_missing_final() -> None:
    weak = beta.to_mastery("a", alpha=1.0, beta=9.0, last_seen=0.0, next_review=None)
    done = SessionState(passed_sections=["a", "b"], final_passed=False)

    assert not guide.is_node_closed(done, weak, sections=("a", "b"))
    assert guide.is_node_closed(done, weak, sections=("a", "b"), final_required=False)
    assert not guide.is_node_closed(
        SessionState(passed_sections=["a"]), weak, sections=("a", "b"), final_required=False
    )


def test_skipped_section_counts_as_done_for_closure() -> None:
    state = SessionState(passed_sections=["a"], skipped_sections=["b"])

    assert guide.lesson_sections_done(state, ["a", "b"])
    assert guide.next_section(["a", "b", "c"], ["a"], ["b"]) == "c"
    assert guide.current_section(state, ["a", "b", "c"]) == "c"
