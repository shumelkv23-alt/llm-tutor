"""Задание «Объясни новичку» на границе занятий: ядро (срез 93)."""

from pathlib import Path

import pytest

from course_fixtures import correct_answer, finish_all_but, load_lessons
from llm_tutor.core import explain
from llm_tutor.core.turn import EXPLAIN_NOTE, handle_turn, start_practice_reply
from llm_tutor.core.verify import start_verification
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.llm.prompt_config import load_explain
from llm_tutor.llm.schemas import ExplainObservation, IdeaVerdict
from llm_tutor.student import explain as logic
from llm_tutor.student import review
from web_fakes import ExplainLLM, ScriptedTutor

PROMPTS = load_explain(Path(__file__).resolve().parent.parent / "data" / "prompts")
NOW = 1_000.0


@pytest.fixture
def db(conn):
    load_lessons(conn)
    # Занятия, закрытые раньше (finish_all_but ставит им closed_at=0.5), заданий не дают.
    logic.mark_since(conn, 1.0)
    return conn


def _state(conn):
    return repos.get_session_state(conn, repos.get_open_session(conn))


def _set(conn, **fields) -> None:
    sid = repos.get_open_session(conn)
    repos.update_session_state(conn, sid, repos.get_session_state(conn, sid).model_copy(update=fields))


def _final_of(conn, node: str):
    return next(i for i in repos.get_items(conn) if i.final and node in i.concept_weights)


async def _close_lesson(conn, settings, node: str, *, now: float = 100.0):
    """Занятие ``node`` закрывается верным ответом на итоговое задание."""
    finish_all_but(conn, node, topic_id=1)
    order = CourseGraph.load(conn).topo_order()
    # Занятия после ``node`` ещё впереди (finish_all_but закрывает весь модуль).
    steps = [
        step.model_copy(update={"status": "ahead", "closed_at": None})
        if order.index(step.concept_id) > order.index(node)
        else step
        for step in _state(conn).route.steps
    ]
    route = _state(conn).route.model_copy(update={"steps": steps})
    final = _final_of(conn, node)
    sections = [s.id for s in CourseGraph.load(conn).concept(node).sections]
    _set(conn, route=route, pending_item_id=final.id, passed_sections=sections, section_id=None,
         phase="practice")
    return await handle_turn(
        conn, ScriptedTutor("Разбираем."), "m", correct_answer(final),
        now=now, settings=settings, answer_mode="answer",
    )


def _required(conn, settings, *, now: float = NOW):
    return explain.ensure_required(conn, PROMPTS, now=now, settings=settings)


def _obs(*ideas: tuple[str, str]) -> ExplainObservation:
    return ExplainObservation(
        ideas=[IdeaVerdict(id=i, verdict="correct", quote=quote) for i, quote in ideas]
    )


async def _say(conn, llm, settings, session, text="loc берёт метки", *, to="novice", prompts=PROMPTS):
    return await explain.turn(
        conn, llm, prompts, session_id=session.id, text=text, to=to, now=NOW, settings=settings
    )


# --- Граница занятий в handle_turn ---


async def test_closing_important_lesson_holds_next(db, settings) -> None:
    reply = await _close_lesson(db, settings, "l1_select")

    assert EXPLAIN_NOTE.format(name="Выборка данных") in reply.text
    state = _state(db)
    assert state.lesson_parts == [] and state.pending_item_id is None
    assert reply.tail is None and reply.button is None
    assert explain.blocking(db)[0] == "l1_select"


async def test_verify_pass_also_triggers(db, settings) -> None:
    finish_all_but(db, "l1_select", topic_id=1)
    start_verification(db, now=2.0, settings=settings)
    for step in range(3):
        item = repos.get_item(db, _state(db).pending_item_id)
        reply = await handle_turn(
            db, ScriptedTutor("Разбираем."), "m", correct_answer(item),
            now=100.0 + step, settings=settings, answer_mode="answer",
        )
    route = _state(db).route
    assert logic.pending_lesson(db, route, CourseGraph.load(db).topo_order()) == "l1_select"
    assert "Перед тем как идти дальше" in reply.text
    assert _state(db).pending_item_id is None


async def test_autoclose_without_items_triggers(db, settings) -> None:
    finish_all_but(db, "l1_select", topic_id=1)
    # В банке не осталось заданий узла: «Практика» закрывает занятие сама.
    db.execute("UPDATE items SET active = 0 WHERE concept_weights LIKE '%l1_select%'")
    db.commit()
    _set(db, pending_item_id=None, phase="explain", passed_sections=["loc", "mask"], section_id=None)

    reply = start_practice_reply(db, now=100.0, settings=settings)

    assert "Перед тем как идти дальше" in reply.text
    assert explain.blocking(db)[0] == "l1_select"


async def test_unimportant_lesson_no_task(db, settings) -> None:
    # l1_frames не важное: после его закрытия урок следующего занятия начинается, как раньше.
    finish_all_but(db, "l1_frames", topic_id=1)
    state = _state(db)
    steps = [
        step.model_copy(update={"status": "ahead", "closed_at": None})
        if step.concept_id in ("l1_select", "l1_group")
        else step
        for step in state.route.steps
    ]
    final = _final_of(db, "l1_frames")
    sections = [s.id for s in CourseGraph.load(db).concept("l1_frames").sections]
    _set(db, route=state.route.model_copy(update={"steps": steps}), pending_item_id=final.id,
         passed_sections=sections, section_id=None, phase="practice")

    reply = await handle_turn(
        db, ScriptedTutor("A\n---\nB"), "m", correct_answer(final),
        now=100.0, settings=settings, answer_mode="answer",
    )

    assert "Перед тем как идти дальше" not in reply.text
    assert _state(db).current_node_id == "l1_select" and _state(db).lesson_parts
    assert explain.blocking(db) is None


# --- Сессия задания ---


async def test_ensure_required_idempotent_and_opening(db, settings) -> None:
    await _close_lesson(db, settings, "l1_select")

    first = _required(db, settings)
    second = _required(db, settings)

    assert first.id == second.id and len(repos.list_explain_sessions(db)) == 1
    assert (first.kind, first.concept_id, first.status) == ("required", "l1_select", "active")
    turns = repos.get_explain_turns(db, first.id)
    title = CourseGraph.load(db).concept("l1_select").name
    assert [(t.role, t.content) for t in turns] == [
        ("novice", PROMPTS.novice.opening.format(title=title))
    ]


def test_ensure_required_nothing_pending(db, settings) -> None:
    finish_all_but(db, "l1_select", topic_id=1)
    assert _required(db, settings) is None


async def test_auto_finish_on_coverage(db, settings) -> None:
    await _close_lesson(db, settings, "l1_select")
    session = _required(db, settings)
    quote = "loc берёт метки, iloc позиции"
    llm = ExplainLLM(observations=[_obs(("loc_vs_iloc", quote))], replies=["Кажется, понял!"])

    after = await _say(db, llm, settings, session, quote)

    assert (after.status, after.end_reason) == ("finished", "coverage")
    assert after.report is not None and after.report.required_done == 1
    turns = repos.get_explain_turns(db, session.id)
    assert [t.role for t in turns] == ["novice", "student", "novice"]
    assert turns[-1].content == "Кажется, понял!"
    assert explain.blocking(db) is not None  # ждёт «Продолжить»


async def test_auto_finish_on_limit(db, settings) -> None:
    await _close_lesson(db, settings, "l1_select")
    session = _required(db, settings)
    llm = ExplainLLM()
    for index in range(PROMPTS.limits.max_student_turns):
        after = await _say(db, llm, settings, session, f"реплика {index}")
        if index < PROMPTS.limits.max_student_turns - 1:
            assert after.status == "active"
    assert PROMPTS.limits.max_student_turns == 10
    assert (after.status, after.end_reason) == ("finished", "limit")


async def test_mentor_turn_keeps_coverage(db, settings) -> None:
    await _close_lesson(db, settings, "l1_select")
    session = _required(db, settings)
    obs = ExplainObservation(ideas=[IdeaVerdict(id="loc_vs_iloc", verdict="partial")])
    llm = ExplainLLM(observations=[obs], mentor=["loc — по меткам."])

    after = await _say(db, llm, settings, session, "Не понимаю loc", to="mentor")

    assert llm.order == ["mentor", "evaluator"]
    assert after.state.student_turns == 0 and after.state.mentor_turns == 1
    assert after.state.mentor_ideas == ["loc_vs_iloc"]
    assert after.state.ideas == {} and after.status == "active"
    roles = [t.role for t in repos.get_explain_turns(db, session.id)]
    assert roles == ["novice", "student", "mentor"]


async def test_stop_required_refused(db, settings) -> None:
    await _close_lesson(db, settings, "l1_select")
    session = _required(db, settings)
    with pytest.raises(explain.ExplainError) as error:
        explain.stop(db, session_id=session.id, now=NOW, settings=settings)
    assert error.value.code == "required"
    assert repos.get_explain_session(db, session.id).status == "active"


async def test_stop_voluntary_reports(db, settings) -> None:
    await _close_lesson(db, settings, "l1_select")
    required = _required(db, settings)
    await _say(db, ExplainLLM(observations=[_obs(("loc_vs_iloc", "loc"))]), settings, required, "loc")
    await explain.proceed(db, ScriptedTutor("A\n---\nB"), PROMPTS, now=NOW, settings=settings)

    voluntary = explain.start_voluntary(db, PROMPTS, node_id="l1_select", now=NOW, settings=settings)
    await _say(db, ExplainLLM(), settings, voluntary, "что-то про loc")
    report = explain.stop(db, session_id=voluntary.id, now=NOW, settings=settings)

    assert report is not None and report.end_reason == "button"
    assert repos.get_explain_session(db, voluntary.id).status == "finished"


# --- «Продолжить» ---


async def test_proceed_before_done_refused(db, settings) -> None:
    await _close_lesson(db, settings, "l1_select")
    _required(db, settings)
    with pytest.raises(explain.ExplainError) as error:
        await explain.proceed(db, ScriptedTutor(), PROMPTS, now=NOW, settings=settings)
    assert error.value.code == "not_done"


async def _finish_required(db, settings, quote="loc берёт метки"):
    session = _required(db, settings)
    llm = ExplainLLM(observations=[_obs(("loc_vs_iloc", quote))])
    return await _say(db, llm, settings, session, quote)


async def test_proceed_starts_lesson(db, settings) -> None:
    await _close_lesson(db, settings, "l1_select")
    await _finish_required(db, settings)

    reply = await explain.proceed(
        db, ScriptedTutor("Группировка\n---\nвторая часть"), PROMPTS, now=NOW, settings=settings
    )

    state = _state(db)
    assert state.current_node_id == "l1_group" and state.lesson_parts
    assert reply.button is not None
    assert explain.blocking(db) is None
    # Повторный «Продолжить» — ошибка: задание уже пройдено.
    with pytest.raises(explain.ExplainError):
        await explain.proceed(db, ScriptedTutor(), PROMPTS, now=NOW, settings=settings)


async def test_proceed_starts_warmup_first(db, settings) -> None:
    await _close_lesson(db, settings, "l1_select")
    await _finish_required(db, settings)
    review.write_changes(db, [review.ScheduleChange("l1_frames", 1, 10.0)])

    reply = await explain.proceed(db, ScriptedTutor("A\n---\nB"), PROMPTS, now=NOW, settings=settings)

    state = _state(db)
    assert state.review is not None and state.review.kind == "warmup"
    assert reply.tail is not None


async def test_two_tasks_in_a_row(db, settings) -> None:
    finish_all_but(db, "l1_group", topic_id=1)
    # Закрыты сразу два важных занятия (проход знакомого): l1_select и l1_group.
    state = _state(db)
    steps = [
        s.model_copy(update={"status": "closed", "closed_at": 20.0 + i})
        if s.concept_id in ("l1_select", "l1_group")
        else s
        for i, s in enumerate(state.route.steps)
    ]
    _set(db, route=state.route.model_copy(update={"steps": steps}), current_node_id=None,
         pending_item_id=None)

    first = _required(db, settings)
    assert first.concept_id == "l1_select"
    llm = ExplainLLM(observations=[_obs(("loc_vs_iloc", "loc берёт метки"))])
    await _say(db, llm, settings, first, "loc берёт метки")

    reply = await explain.proceed(db, ScriptedTutor("A\n---\nB"), PROMPTS, now=NOW, settings=settings)

    second = repos.active_explain_session(db)
    assert second is not None and (second.kind, second.concept_id) == ("required", "l1_group")
    assert "Перед тем как идти дальше" in reply.text
    assert _state(db).lesson_parts == []
    assert explain.blocking(db)[0] == "l1_group"


async def test_required_displaces_voluntary(db, settings) -> None:
    await _close_lesson(db, settings, "l1_select")
    await _finish_required(db, settings)
    await explain.proceed(db, ScriptedTutor("A\n---\nB"), PROMPTS, now=NOW, settings=settings)
    voluntary = explain.start_voluntary(db, PROMPTS, node_id="l1_select", now=NOW, settings=settings)
    await _say(db, ExplainLLM(), settings, voluntary, "про loc")
    # Пока идёт добровольная, закрылось ещё одно важное занятие.
    await _close_lesson(db, settings, "l1_group", now=200.0)

    session = _required(db, settings, now=300.0)

    old = repos.get_explain_session(db, voluntary.id)
    assert (old.status, old.end_reason) == ("finished", "button") and old.report is not None
    assert (session.kind, session.concept_id, session.status) == ("required", "l1_group", "active")
    assert repos.active_explain_session(db).id == session.id


async def test_voluntary_rules(db, settings) -> None:
    finish_all_but(db, "l1_select", topic_id=1)
    for node in ("l1_select", "l1_frames", "no_such"):  # не закрыто / не важное / нет такого
        with pytest.raises(explain.ExplainError) as error:
            explain.start_voluntary(db, PROMPTS, node_id=node, now=NOW, settings=settings)
        assert error.value.code == "not_available"
    await _close_lesson(db, settings, "l1_select")
    _required(db, settings)
    with pytest.raises(explain.ExplainError) as error:
        explain.start_voluntary(db, PROMPTS, node_id="l1_select", now=NOW, settings=settings)
    assert error.value.code == "other_active"


async def test_novice_sees_lesson_only(db, settings) -> None:
    await _close_lesson(db, settings, "l1_select")
    session = _required(db, settings)
    llm = ExplainLLM()

    await _say(db, llm, settings, session, "loc берёт метки")

    sent = "\n".join(m.content for call in llm.calls["novice"] for m in call)
    graph = CourseGraph.load(db)
    assert graph.concept("l1_select").name in sent
    assert graph.concept("l1_frames").name in sent  # закрытое раньше — база новичка
    assert graph.concept("l1_group").name not in sent  # следующее занятие новичку неизвестно
    config = repos.get_explain_configs(db)["l1_select"]
    for line in [*(i.text for i in config.ideas), *(i.expect for i in config.ideas)]:
        assert line not in sent
    for pitfall in config.pitfalls:
        assert pitfall.fix not in sent


async def test_stale_active_required_closed_when_not_pending(db, settings) -> None:
    await _close_lesson(db, settings, "l1_select")
    session = _required(db, settings)
    # Эталон убрали из seed: задание исчезает, сессия закрывается как «stale».
    db.execute("UPDATE concepts SET explain = NULL WHERE id = 'l1_select'")
    db.commit()

    assert _required(db, settings) is None

    old = repos.get_explain_session(db, session.id)
    assert old is None or (old.status, old.end_reason) == ("finished", "stale")
    assert explain.blocking(db) is None
