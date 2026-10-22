"""Серия повторения в ядре (срез 80): выдача, ответ, пропуск — на фикстуре занятий."""

import json

import pytest
from course_fixtures import correct_answer, load_lessons
from test_lesson_sections import _check, _enter, _wrong
from test_turn import _FakeTutor, _set_state, _state

from llm_tutor.core import review as core_review
from llm_tutor.core import turn
from llm_tutor.db import repos
from llm_tutor.schemas import Event, ReviewRow

DAY = 86_400.0
NOW = 100 * DAY + 3_600.0
GROUP_ITEMS = [3011, 3012, 3013, 3014]


def _close_in_route(conn, *concept_ids: str) -> None:
    """Занятия закрыты в снимке маршрута (шаги маршрута строятся ходом урока)."""
    route = _state(conn).route
    steps = [
        step.model_copy(update={"status": "closed", "closed_at": NOW - 10 * DAY})
        if step.concept_id in concept_ids
        else step
        for step in route.steps
    ]
    _set_state(conn, route=route.model_copy(update={"steps": steps}))


async def _lesson_with_due(conn, settings, *due: str) -> None:
    """Ученик посреди раздела занятия l1_frames, пройденные ``due`` просрочены."""
    load_lessons(conn)
    await _enter(conn, settings)
    _check(conn, settings)
    _close_in_route(conn, *due)
    for concept_id in due:
        repos.upsert_review(conn, concept_id, 1, NOW - DAY)


def _events(conn, concept_id: str) -> list:
    return conn.execute(
        "SELECT item_id, weight, source, result FROM events WHERE concept_id = ? ORDER BY id",
        (concept_id,),
    ).fetchall()


def _messages(conn) -> list:
    return conn.execute("SELECT role, content FROM messages ORDER BY id").fetchall()


async def test_start_due_issues_item_of_due_lesson(conn, settings) -> None:
    await _lesson_with_due(conn, settings, "l1_group")

    reply = await core_review.start(conn, _FakeTutor(), "m", kind="due", now=NOW, settings=settings)

    series = _state(conn).review
    assert series is not None and series.kind == "due" and series.node_id == "l1_group"
    assert reply.item_id == series.item_id and reply.item_id in GROUP_ITEMS
    assert reply.tail.startswith("🔁 Повторение · «Группировка» · 1 из 1")
    assert series.total == 1 and series.queue == []


async def test_answer_credits_item_lesson_not_current(conn, settings) -> None:
    await _lesson_with_due(conn, settings, "l1_group")
    current_before = len(_events(conn, "l1_frames"))
    await core_review.start(conn, _FakeTutor(), "m", kind="due", now=NOW, settings=settings)
    item = repos.get_item(conn, _state(conn).review.item_id)

    await core_review.answer(
        conn, _FakeTutor(), "m", correct_answer(item), now=NOW, settings=settings
    )

    rows = _events(conn, "l1_group")
    assert [r["item_id"] for r in rows] == [item.id]
    assert len(_events(conn, "l1_frames")) == current_before


async def test_lesson_fields_untouched(conn, settings) -> None:
    await _lesson_with_due(conn, settings, "l1_group")
    skip = {"review", "last_activity", "route"}
    before = _state(conn).model_dump(exclude=skip)

    await core_review.start(conn, _FakeTutor(), "m", kind="due", now=NOW, settings=settings)
    item = repos.get_item(conn, _state(conn).review.item_id)
    await core_review.answer(
        conn, _FakeTutor(), "m", correct_answer(item), now=NOW, settings=settings
    )

    after = _state(conn)
    assert after.review is None
    assert after.model_dump(exclude=skip) == before
    assert after.pending_item_id is not None  # задание урока ждёт, как ждало


async def test_correct_due_answer_moves_step(conn, settings) -> None:
    await _lesson_with_due(conn, settings, "l1_group")
    await core_review.start(conn, _FakeTutor(), "m", kind="due", now=NOW, settings=settings)
    item = repos.get_item(conn, _state(conn).review.item_id)

    reply = await core_review.answer(
        conn, _FakeTutor(), "m", correct_answer(item), now=NOW, settings=settings
    )

    assert repos.get_review_rows(conn)["l1_group"] == ReviewRow(
        concept_id="l1_group", step=2, due_at=NOW + 7 * DAY
    )
    assert reply.text.startswith("Верно ✓")


async def test_mistake_drops_step_and_names_section(conn, settings) -> None:
    await _lesson_with_due(conn, settings, "l1_group")
    repos.upsert_review(conn, "l1_group", 3, NOW - DAY)
    await core_review.start(conn, _FakeTutor(), "m", kind="due", now=NOW, settings=settings)
    item = repos.get_item(conn, _state(conn).review.item_id)

    reply = await core_review.answer(
        conn, _FakeTutor(), "m", _wrong(item), now=NOW, settings=settings
    )

    assert repos.get_review_rows(conn)["l1_group"] == ReviewRow(
        concept_id="l1_group", step=1, due_at=NOW + DAY
    )
    titles = {"groupby": "groupby", "pivot": "pivot_table"}
    assert f"раздел «{titles[item.section]}» занятия «Группировка»" in reply.text
    assert "Не совсем ✗" in reply.text


async def test_repeat_item_weighted_as_lesson(conn, settings) -> None:
    await _lesson_with_due(conn, settings, "l1_group")
    for i, item_id in enumerate(GROUP_ITEMS):  # все отвечены давно: берётся самое давнее
        repos.add_event(
            conn,
            Event(source="checked", result=1.0, concept_id="l1_group", item_id=item_id,
                  ts=NOW - 5 * DAY + i),
        )
    await core_review.start(conn, _FakeTutor(), "m", kind="due", now=NOW, settings=settings)
    item = repos.get_item(conn, _state(conn).review.item_id)
    assert item.id == GROUP_ITEMS[0]

    await core_review.answer(
        conn, _FakeTutor(), "m", correct_answer(item), now=NOW, settings=settings
    )

    new = _events(conn, "l1_group")[-1]
    assert new["weight"] == pytest.approx(settings.repeat_evidence_weight)


async def test_new_item_has_full_weight(conn, settings) -> None:
    await _lesson_with_due(conn, settings, "l1_group")
    await core_review.start(conn, _FakeTutor(), "m", kind="due", now=NOW, settings=settings)
    item = repos.get_item(conn, _state(conn).review.item_id)
    await core_review.answer(
        conn, _FakeTutor(), "m", correct_answer(item), now=NOW, settings=settings
    )
    assert _events(conn, "l1_group")[-1]["weight"] == pytest.approx(1.0)


async def test_stale_item_taken_next(conn, settings) -> None:
    await _lesson_with_due(conn, settings, "l1_group", "l1_select")
    await core_review.start(conn, _FakeTutor(), "m", kind="due", now=NOW, settings=settings)
    first = _state(conn).review
    conn.execute("UPDATE items SET active = 0 WHERE id = ?", (first.item_id,))
    conn.commit()

    reply = await core_review.answer(conn, _FakeTutor(), "m", "что угодно", now=NOW, settings=settings)

    assert turn.STALE_ITEM_REPLY in reply.text
    second = _state(conn).review
    assert second.node_id != first.node_id and reply.item_id == second.item_id
    assert _events(conn, first.node_id) == []
    assert second.answered == 0


async def test_skip_postpones_day(conn, settings) -> None:
    await _lesson_with_due(conn, settings, "l1_group", "l1_select")
    await core_review.start(conn, _FakeTutor(), "m", kind="due", now=NOW, settings=settings)
    first = _state(conn).review

    reply = await core_review.skip(conn, _FakeTutor(), "m", now=NOW, settings=settings)

    assert _events(conn, first.node_id) == []
    assert repos.get_review_rows(conn)[first.node_id].due_at == NOW + DAY
    assert repos.get_review_rows(conn)[first.node_id].step == 1
    second = _state(conn).review
    assert second.node_id != first.node_id and reply.item_id == second.item_id
    assert second.answered == 0


async def test_series_done_summary(conn, settings) -> None:
    await _lesson_with_due(conn, settings, "l1_group")
    await core_review.start(conn, _FakeTutor(), "m", kind="due", now=NOW, settings=settings)
    item = repos.get_item(conn, _state(conn).review.item_id)

    reply = await core_review.answer(
        conn, _FakeTutor(), "m", correct_answer(item), now=NOW, settings=settings
    )

    assert "верно 1 из 1" in reply.text
    assert reply.tail is None and reply.item_id is None
    assert _state(conn).review is None


async def test_series_of_two_counts_correct(conn, settings) -> None:
    await _lesson_with_due(conn, settings, "l1_group", "l1_select")
    await core_review.start(conn, _FakeTutor(), "m", kind="due", now=NOW, settings=settings)
    series = _state(conn).review
    assert series.total == 2 and len(series.queue) == 1
    first = repos.get_item(conn, series.item_id)
    mid = await core_review.answer(
        conn, _FakeTutor(), "m", correct_answer(first), now=NOW, settings=settings
    )
    assert "2 из 2" in mid.tail
    second = repos.get_item(conn, _state(conn).review.item_id)
    last = await core_review.answer(
        conn, _FakeTutor(), "m", _wrong(second), now=NOW, settings=settings
    )
    assert "верно 1 из 2" in last.text and _state(conn).review is None


async def test_refresh_rejects_unclosed(conn, settings) -> None:
    await _lesson_with_due(conn, settings, "l1_group")
    with pytest.raises(ValueError):
        await core_review.start(conn, _FakeTutor(), "m", kind="refresh", node_ids=["l1_select"], now=NOW, settings=settings)
    with pytest.raises(ValueError):
        await core_review.start(conn, _FakeTutor(), "m", kind="refresh", node_ids=["nope"], now=NOW, settings=settings)
    assert _state(conn).review is None


async def test_refresh_early_correct_keeps_schedule(conn, settings) -> None:
    """«Освежить» срок не двигает, если занятие ещё не просрочено."""
    await _lesson_with_due(conn, settings, "l1_group")
    repos.upsert_review(conn, "l1_group", 3, NOW + 5 * DAY)
    await core_review.start(conn, _FakeTutor(), "m", kind="refresh", node_ids=["l1_group"], now=NOW, settings=settings)
    item = repos.get_item(conn, _state(conn).review.item_id)

    await core_review.answer(
        conn, _FakeTutor(), "m", correct_answer(item), now=NOW, settings=settings
    )

    assert repos.get_review_rows(conn)["l1_group"] == ReviewRow(
        concept_id="l1_group", step=3, due_at=NOW + 5 * DAY
    )


async def test_nothing_to_review(conn, settings) -> None:
    await _lesson_with_due(conn, settings)  # просроченного нет
    before = len(_messages(conn))

    reply = await core_review.start(conn, _FakeTutor(), "m", kind="due", now=NOW, settings=settings)

    assert reply.text == core_review.NOTHING_TO_REVIEW_REPLY and reply.tail is None
    assert _state(conn).review is None
    assert len(_messages(conn)) == before


async def test_start_twice_is_rejected(conn, settings) -> None:
    await _lesson_with_due(conn, settings, "l1_group")
    await core_review.start(conn, _FakeTutor(), "m", kind="due", now=NOW, settings=settings)
    with pytest.raises(ValueError):
        await core_review.start(conn, _FakeTutor(), "m", kind="due", now=NOW, settings=settings)


async def test_lesson_without_items_is_dropped_from_series(conn, settings) -> None:
    """Занятие без подходящих заданий в серию не идёт и не считается в «из N»."""
    await _lesson_with_due(conn, settings, "l1_group", "l1_select")
    conn.execute("UPDATE items SET active = 0 WHERE final = 0 AND concept_weights LIKE '%l1_group%'")
    conn.commit()

    await core_review.start(conn, _FakeTutor(), "m", kind="due", now=NOW, settings=settings)

    series = _state(conn).review
    assert series.node_id == "l1_select" and series.total == 1
    assert "1 из 1" in _messages(conn)[-1]["content"]


async def test_review_turns_are_marked_in_journal(conn, settings) -> None:
    await _lesson_with_due(conn, settings, "l1_group")
    await core_review.start(conn, _FakeTutor(), "m", kind="due", now=NOW, settings=settings)
    item = repos.get_item(conn, _state(conn).review.item_id)
    await core_review.answer(
        conn, _FakeTutor(), "m", correct_answer(item), now=NOW, settings=settings
    )

    rows = conn.execute(
        "SELECT meta FROM messages WHERE meta IS NOT NULL ORDER BY id"
    ).fetchall()
    metas = [json.loads(r["meta"]) for r in rows]
    assert metas and all(m.get("review") == "l1_group" for m in metas)
    assert len(metas) == 4  # старт и ответ: по две реплики


async def test_abort_clears_series_without_touching_schedule(conn, settings) -> None:
    await _lesson_with_due(conn, settings, "l1_group")
    await core_review.start(conn, _FakeTutor(), "m", kind="due", now=NOW, settings=settings)
    rows = repos.get_review_rows(conn)

    core_review.abort(conn, now=NOW)

    assert _state(conn).review is None
    assert repos.get_review_rows(conn) == rows
    core_review.abort(conn, now=NOW)  # повтор — не ошибка


# --- чат и «Застрял» во время серии ---


def _system(client) -> str:
    return client.calls[-1][0].content


async def test_chat_context_uses_review_section(conn, settings) -> None:
    await _lesson_with_due(conn, settings, "l1_group")
    await core_review.start(conn, _FakeTutor(), "m", kind="due", now=NOW, settings=settings)
    item = repos.get_item(conn, _state(conn).review.item_id)
    client = _FakeTutor("Разъяснение")

    reply = await core_review.chat(conn, client, "m", "Что тут спрашивают?", now=NOW, settings=settings)

    system = _system(client)
    assert f"Конспект текущего раздела «{item.section}»" in system
    assert "Группировка" in system
    assert "Конспект текущего раздела «Series»" not in system
    assert reply.text.startswith("Разъяснение") and reply.text.endswith(turn.PENDING_ITEM_NOTE)


async def test_chat_marks_hinted_keeps_step(conn, settings) -> None:
    await _lesson_with_due(conn, settings, "l1_group")
    await core_review.start(conn, _FakeTutor(), "m", kind="due", now=NOW, settings=settings)
    await core_review.chat(conn, _FakeTutor(), "m", "Подскажи", now=NOW, settings=settings)
    assert _state(conn).review.hinted is True
    item = repos.get_item(conn, _state(conn).review.item_id)

    await core_review.answer(
        conn, _FakeTutor(), "m", correct_answer(item), now=NOW, settings=settings
    )

    assert repos.get_review_rows(conn)["l1_group"] == ReviewRow(
        concept_id="l1_group", step=1, due_at=NOW + DAY
    )


async def test_stuck_marks_hinted_without_lesson_side_effects(conn, settings) -> None:
    await _lesson_with_due(conn, settings, "l1_group")
    await core_review.start(conn, _FakeTutor(), "m", kind="due", now=NOW, settings=settings)
    skip = {"review", "last_activity", "route"}
    before = _state(conn).model_dump(exclude=skip)

    reply = await core_review.chat(
        conn, _FakeTutor("Подсказка", hint_level=2), "m", turn.STUCK_KICKOFF_TEXT,
        stuck=True, now=NOW, settings=settings,
    )

    state = _state(conn)
    assert state.review.hinted is True
    assert state.model_dump(exclude=skip) == before  # усиленный проход урока не включился
    assert turn.STUCK_NOTE not in reply.text and "Подсказка" in reply.text


async def test_chat_skip_intent_skips_review_item(conn, settings) -> None:
    await _lesson_with_due(conn, settings, "l1_group", "l1_select")
    await core_review.start(conn, _FakeTutor(), "m", kind="due", now=NOW, settings=settings)
    first = _state(conn).review
    client = _FakeTutor()

    reply = await core_review.chat(conn, client, "m", "пропусти", now=NOW, settings=settings)

    assert client.calls == []  # команда, а не вопрос тьютору
    assert repos.get_review_rows(conn)[first.node_id].due_at == NOW + DAY
    assert _events(conn, first.node_id) == []
    assert _state(conn).review.node_id != first.node_id and reply.item_id is not None


async def test_chat_close_topic_aborts_series_and_starts_verification(conn, settings) -> None:
    await _lesson_with_due(conn, settings, "l1_group")
    await core_review.start(conn, _FakeTutor(), "m", kind="due", now=NOW, settings=settings)

    await core_review.chat(
        conn, _FakeTutor("ок", wants_close_topic=True), "m", "я это знаю", now=NOW, settings=settings
    )

    state = _state(conn)
    assert state.review is None
    assert state.mode == "verify" and state.current_node_id == "l1_frames"


async def test_chat_llm_failure_does_not_mark_hinted(conn, settings) -> None:
    from test_turn import _FailingTutor

    await _lesson_with_due(conn, settings, "l1_group")
    await core_review.start(conn, _FakeTutor(), "m", kind="due", now=NOW, settings=settings)

    await core_review.chat(conn, _FailingTutor(), "m", "вопрос", now=NOW, settings=settings)

    assert _state(conn).review.hinted is False


async def test_chat_lesson_fields_untouched_and_journal_marked(conn, settings) -> None:
    await _lesson_with_due(conn, settings, "l1_group")
    await core_review.start(conn, _FakeTutor(), "m", kind="due", now=NOW, settings=settings)
    skip = {"review", "last_activity", "route"}
    before = _state(conn).model_dump(exclude=skip)

    await core_review.chat(conn, _FakeTutor(), "m", "Вопрос", now=NOW, settings=settings)

    assert _state(conn).model_dump(exclude=skip) == before
    last = conn.execute("SELECT meta FROM messages ORDER BY id DESC LIMIT 2").fetchall()
    assert [json.loads(r["meta"]) for r in last] == [{"review": "l1_group"}] * 2


async def test_lesson_tail_labels_review_messages(conn, settings) -> None:
    """Ход урока после серии видит её реплики с пометкой, а не как вопросы по занятию."""
    from llm_tutor.core.context import REVIEW_TAIL_LABEL

    await _lesson_with_due(conn, settings, "l1_group")
    await core_review.start(conn, _FakeTutor(), "m", kind="due", now=NOW, settings=settings)
    await core_review.chat(conn, _FakeTutor(), "m", "Чем это поможет?", now=NOW, settings=settings)
    core_review.abort(conn, now=NOW)
    client = _FakeTutor()

    await turn.handle_turn(
        conn, client, "m", "Расскажи про Series", now=NOW + 1, settings=settings,
        answer_mode="tutor",
    )

    label = REVIEW_TAIL_LABEL.format(name="Группировка")
    contents = [m.content for m in client.calls[-1] if m.role in ("user", "assistant")]
    assert any(c.startswith(label + "Чем это поможет?") for c in contents)
    assert any(c.startswith(label + "🔁 Повторение") for c in contents)


# --- аудит среза 80 ---


async def test_lesson_removed_from_graph_mid_series_does_not_crash(conn, settings) -> None:
    """Занятие следующее в очереди ушло из графа: серия идёт дальше (случай 5 спеки)."""
    await _lesson_with_due(conn, settings, "l1_group", "l1_select")
    await core_review.start(conn, _FakeTutor(), "m", kind="due", now=NOW, settings=settings)
    series = _state(conn).review
    gone = series.queue[0]
    conn.execute("UPDATE concepts SET active = 0 WHERE id = ?", (gone,))
    conn.commit()
    item = repos.get_item(conn, series.item_id)

    reply = await core_review.answer(
        conn, _FakeTutor(), "m", correct_answer(item), now=NOW, settings=settings
    )

    assert "верно 1 из 1" in reply.text and _state(conn).review is None
    assert repos.get_review_rows(conn)[gone].step == 1  # строка осталась


async def test_mistake_on_lesson_removed_from_graph_does_not_crash(conn, settings) -> None:
    await _lesson_with_due(conn, settings, "l1_group")
    await core_review.start(conn, _FakeTutor(), "m", kind="due", now=NOW, settings=settings)
    series = _state(conn).review
    conn.execute("UPDATE concepts SET active = 0 WHERE id = ?", (series.node_id,))
    conn.commit()
    item = repos.get_item(conn, series.item_id)

    reply = await core_review.answer(
        conn, _FakeTutor(), "m", _wrong(item), now=NOW, settings=settings
    )

    assert "Не совсем ✗" in reply.text and _state(conn).review is None


async def test_refresh_deduplicates_lessons(conn, settings) -> None:
    await _lesson_with_due(conn, settings, "l1_group")
    await core_review.start(
        conn, _FakeTutor(), "m", kind="refresh", node_ids=["l1_group", "l1_group"],
        now=NOW, settings=settings,
    )
    assert _state(conn).review.total == 1


async def test_chat_with_fragment_keeps_quote_and_review_marks(conn, settings) -> None:
    from llm_tutor.core.context import Focus, focus_from_meta

    await _lesson_with_due(conn, settings, "l1_group")
    await core_review.start(conn, _FakeTutor(), "m", kind="due", now=NOW, settings=settings)

    await core_review.chat(
        conn, _FakeTutor(), "m", "Объясни этот фрагмент подробнее.",
        focus=Focus("task", "df.groupby('a')"), now=NOW, settings=settings,
    )

    user_meta = conn.execute(
        "SELECT meta FROM messages WHERE role = 'user' ORDER BY id DESC LIMIT 1"
    ).fetchone()["meta"]
    assert focus_from_meta(user_meta) == Focus("task", "df.groupby('a')")
    assert json.loads(user_meta)["review"] == "l1_group"
