"""Ход занятия по разделам (срез 54) — на фикстуре tests/fixtures/lessons."""

from course_fixtures import correct_answer, load_lessons
from test_turn import _FailingTutor, _FakeTutor, _set_state, _state

from llm_tutor.core import guided
from llm_tutor.core.lesson import switch_node
from llm_tutor.core.turn import handle_turn, lesson_next_reply, stuck_reply
from llm_tutor.core.verify import start_verification
from llm_tutor.db import repos
from llm_tutor.schemas import Item
from llm_tutor.student import beta, guide


def _pending(conn) -> Item:
    item = repos.get_item(conn, _state(conn).pending_item_id)
    assert item is not None
    return item


def _wrong(item: Item) -> str:
    return "Неверно" if item.answer_type == "choice" else "не то"


async def _enter(conn, settings, client=None) -> None:
    """Вход в первое занятие: урок первого раздела частями."""
    await handle_turn(conn, client or _FakeTutor("A\n---\nB"), "m", "Поехали", now=1.0, settings=settings)


def _check(conn, settings, now: float = 2.0):
    """«Дальше» до конца частей и «Проверим»: вопрос раздела."""
    state = _state(conn)
    reply = None
    for part in range(state.lesson_part, len(state.lesson_parts) + 1):
        reply = lesson_next_reply(conn, state.current_node_id, part, now=now, settings=settings)
    return reply


async def _answer(conn, settings, text: str, client=None, now: float = 3.0):
    return await handle_turn(
        conn, client or _FakeTutor("A\n---\nB"), "m", text, now=now, settings=settings,
        answer_mode="answer",
    )


def _step(conn, node_id: str):
    return next(s for s in _state(conn).route.steps if s.concept_id == node_id)


async def test_entering_lesson_explains_first_section(conn, settings) -> None:
    load_lessons(conn)
    client = _FakeTutor("A\n---\nB")

    reply = await handle_turn(conn, client, "m", "Поехали", now=1.0, settings=settings)

    state = _state(conn)
    assert state.current_node_id == "l1_frames"
    assert state.section_id == "series"
    assert reply.text == "A" and reply.button[0] == guided.NEXT_LABEL
    kickoff = client.calls[-1][-1].content
    assert "«Series»" in kickoff and "«Series и DataFrame»" in kickoff


async def test_check_gives_question_of_current_section(conn, settings) -> None:
    load_lessons(conn)
    await _enter(conn, settings)

    _check(conn, settings)

    item = _pending(conn)
    assert item.section == "series" and not item.final


async def test_correct_answer_passes_section_and_explains_next(conn, settings) -> None:
    load_lessons(conn)
    await _enter(conn, settings)
    _check(conn, settings)

    reply = await _answer(conn, settings, correct_answer(_pending(conn)))

    state = _state(conn)
    assert state.passed_sections == ["series"] and state.section_id == "dataframe"
    assert state.pending_item_id is None and state.current_node_id == "l1_frames"
    assert "✅ Раздел «Series» пройден — дальше «DataFrame»." in reply.text
    assert reply.button is not None  # урок нового раздела частями


async def test_lesson_walks_sections_then_final_then_closes(conn, settings) -> None:
    load_lessons(conn)
    await _enter(conn, settings)
    for _ in range(2):
        _check(conn, settings)
        await _answer(conn, settings, correct_answer(_pending(conn)))

    final = _pending(conn)  # после последнего раздела — итоговое сразу
    assert final.final and _state(conn).section_id is None

    reply = await _answer(conn, settings, correct_answer(final), now=4.0)

    state = _state(conn)
    assert _step(conn, "l1_frames").status == "closed"
    assert not _step(conn, "l1_frames").weak
    assert state.current_node_id == "l1_select"
    assert state.section_id == "loc" and state.passed_sections == []
    assert not state.final_passed
    assert "закрыто" in reply.text


async def test_first_mistake_gives_second_question_of_same_section(conn, settings) -> None:
    load_lessons(conn)
    await _enter(conn, settings)
    _check(conn, settings)
    first = _pending(conn)

    await _answer(conn, settings, _wrong(first))

    second = _pending(conn)
    state = _state(conn)
    assert second.id != first.id and second.section == "series"
    assert state.check_misses == 1 and state.passed_sections == []


async def test_two_mistakes_make_section_weak_and_lesson_goes_on(conn, settings) -> None:
    load_lessons(conn)
    await _enter(conn, settings)
    _check(conn, settings)
    await _answer(conn, settings, _wrong(_pending(conn)))

    reply = await _answer(conn, settings, _wrong(_pending(conn)), now=4.0)

    state = _state(conn)
    assert state.passed_sections == ["series"] and state.weak_sections == ["series"]
    assert state.section_id == "dataframe" and state.check_misses == 0
    assert "Раздел «Series» пока слабое место" in reply.text


async def test_weak_section_closes_lesson_with_warning(conn, settings) -> None:
    load_lessons(conn)
    await _enter(conn, settings)
    _check(conn, settings)
    await _answer(conn, settings, _wrong(_pending(conn)))
    await _answer(conn, settings, _wrong(_pending(conn)))
    _check(conn, settings)
    await _answer(conn, settings, correct_answer(_pending(conn)))

    await _answer(conn, settings, correct_answer(_pending(conn)), now=5.0)

    assert _step(conn, "l1_frames").status == "closed"
    assert _step(conn, "l1_frames").weak


async def test_two_mistakes_on_final_close_lesson_weak(conn, settings) -> None:
    load_lessons(conn)
    await _enter(conn, settings)
    for _ in range(2):
        _check(conn, settings)
        await _answer(conn, settings, correct_answer(_pending(conn)))
    await _answer(conn, settings, _wrong(_pending(conn)))
    assert _pending(conn).final  # второй шанс — снова итоговое

    reply = await _answer(conn, settings, _wrong(_pending(conn)), now=5.0)

    assert guided.FINAL_WEAK_NOTE in reply.text
    assert _step(conn, "l1_frames").status == "closed" and _step(conn, "l1_frames").weak


async def test_confident_mastery_closes_lesson_early(conn, settings) -> None:
    load_lessons(conn)
    await _enter(conn, settings)
    _check(conn, settings)
    repos.upsert_mastery(conn, "l1_frames", alpha=60.0, beta=1.0, last_seen=2.0)

    await _answer(conn, settings, correct_answer(_pending(conn)))

    assert _step(conn, "l1_frames").status == "closed"
    assert _state(conn).current_node_id == "l1_select"


async def test_stuck_keeps_passed_sections(conn, settings) -> None:
    load_lessons(conn)
    await _enter(conn, settings)
    _check(conn, settings)
    await _answer(conn, settings, correct_answer(_pending(conn)))

    await stuck_reply(conn, _FakeTutor("Иначе: …"), "m", now=4.0, settings=settings)

    state = _state(conn)
    assert state.passed_sections == ["series"] and state.section_id == "dataframe"


async def test_model_failure_shows_section_and_check_button(conn, settings) -> None:
    load_lessons(conn)

    reply = await handle_turn(conn, _FailingTutor(), "m", "Поехали", now=1.0, settings=settings)

    assert reply.text.startswith("📘 Series и DataFrame · Series")
    assert reply.button[0] == guided.CHECK_LABEL
    _check(conn, settings)
    assert _pending(conn).section == "series"


async def test_prompt_carries_only_current_section(conn, settings) -> None:
    load_lessons(conn)
    await _enter(conn, settings)
    _check(conn, settings)
    client = _FakeTutor("A\n---\nB")

    await _answer(conn, settings, correct_answer(_pending(conn)), client=client)

    system = client.calls[-1][0].content  # урок раздела DataFrame
    assert "Конспект текущего раздела «DataFrame»" in system
    assert "Раздел «DataFrame»" in system
    assert "Раздел «Series»" not in system
    assert "✓ Series" in system and "▶ DataFrame" in system


async def test_verify_pass_asks_each_section_then_final(conn, settings) -> None:
    load_lessons(conn)
    await _enter(conn, settings)

    start_verification(conn, now=2.0, settings=settings)
    asked = []
    for step in range(3):
        item = _pending(conn)
        asked.append("final" if item.final else item.section)
        await _answer(conn, settings, correct_answer(item), now=3.0 + step)

    assert asked == ["series", "dataframe", "final"]
    assert _step(conn, "l1_frames").status == "closed"


async def test_verify_failure_restarts_lesson_at_failed_section(conn, settings) -> None:
    load_lessons(conn)
    await _enter(conn, settings)
    start_verification(conn, now=2.0, settings=settings)
    await _answer(conn, settings, correct_answer(_pending(conn)), now=3.0)

    reply = await _answer(conn, settings, _wrong(_pending(conn)), now=4.0)

    state = _state(conn)
    assert state.mode != "verify"
    assert state.passed_sections == ["series"] and state.section_id == "dataframe"
    assert state.pending_item_id is None and reply.button is not None


async def test_switching_lesson_resets_section_progress(conn, settings) -> None:
    load_lessons(conn)
    await _enter(conn, settings)
    _check(conn, settings)
    await _answer(conn, settings, correct_answer(_pending(conn)))

    switch_node(conn, "l1_select", now=4.0, settings=settings)

    state = _state(conn)
    assert state.passed_sections == [] and state.section_id is None
    assert not state.final_passed


def test_lesson_closure_criterion() -> None:
    from llm_tutor.schemas import SessionState

    sections = ["a", "b"]
    weak = beta.to_mastery("x", 1.0, 1.0, None, None)
    done = SessionState(passed_sections=["a", "b"], final_passed=True)
    assert guide.is_node_closed(done, weak, sections=sections)
    assert not guide.is_node_closed(done.model_copy(update={"final_passed": False}), weak, sections=sections)
    # Серия ответов у занятия с разделами в уроке ничего не закрывает.
    assert not guide.is_node_closed(SessionState(node_streak=10), weak, sections=sections)
    # В проходе — по вопросу на раздел и итоговое.
    verify = SessionState(mode="verify", node_streak=3)
    assert guide.is_node_closed(verify, weak, sections=sections)
    assert not guide.is_node_closed(verify.model_copy(update={"node_streak": 2}), weak, sections=sections)


async def test_verify_failure_on_final_offers_final_again(conn, settings) -> None:
    load_lessons(conn)
    await _enter(conn, settings)
    start_verification(conn, now=2.0, settings=settings)
    for step in range(2):
        await _answer(conn, settings, correct_answer(_pending(conn)), now=3.0 + step)

    reply = await _answer(conn, settings, _wrong(_pending(conn)), now=6.0)

    state = _state(conn)
    assert state.passed_sections == ["series", "dataframe"] and state.section_id is None
    assert reply.button[0] == guided.CHECK_LABEL
    _check(conn, settings, now=7.0)
    assert _pending(conn).final


async def test_claimed_lesson_is_checked_section_by_section(conn, settings) -> None:
    """Заявленное в анкете (🔍): по вопросу на раздел и итоговое, без объяснения."""
    from llm_tutor.core.turn import resume_reply

    load_lessons(conn)
    await _enter(conn, settings)
    state = _state(conn)
    steps = [
        # Заявленное проверяется в конце модуля: заявлен весь модуль 1.
        s.model_copy(update={"status": "claimed"}) if s.concept_id.startswith("l1_") else s
        for s in state.route.steps
    ]
    _set_state(
        conn,
        current_node_id=None,
        route=state.route.model_copy(update={"steps": steps}),
        **guide.SECTION_RESET,
    )

    await resume_reply(conn, _FakeTutor(), "m", now=2.0, settings=settings)
    asked = []
    for step in range(3):
        item = _pending(conn)
        asked.append("final" if item.final else item.section)
        await _answer(conn, settings, correct_answer(item), now=3.0 + step)

    assert asked == ["series", "dataframe", "final"]
    assert _step(conn, "l1_frames").status == "closed"
