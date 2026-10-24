"""Сессия «Объясни новичку»: доступ, старт, наставник, завершение (срез 87)."""

import json
from pathlib import Path

import pytest

from course_fixtures import enter_module, load_lessons
from llm_tutor.core import explain
from llm_tutor.db import repos
from llm_tutor.llm.prompt_config import load_explain
from llm_tutor.llm.schemas import ExplainObservation, IdeaVerdict
from llm_tutor.schemas import ExplainState, IdeaProgress
from web_fakes import ExplainLLM

PROMPTS = load_explain(Path(__file__).resolve().parent.parent / "data" / "prompts")
NOW = 1_000_000.0
HOUR = 3600.0


@pytest.fixture
def db(conn):
    load_lessons(conn)
    enter_module(conn, 2)  # модуль 1 пройден, модуль 2 — нет
    return conn


def _start(db, settings, topic_id: int = 1, now: float = NOW):
    return explain.start(db, PROMPTS, topic_id=topic_id, now=now, settings=settings)


def _set_state(db, session_id: int, state: ExplainState, *, updated_at: float = NOW) -> None:
    repos.update_explain_session(db, session_id, state=state, updated_at=updated_at, commit=True)


def _reasons(db, settings) -> dict[int, str]:
    return {row.topic: row.reason for row in explain.availability(db, settings=settings)}


def test_availability(db, settings) -> None:
    rows = {row.topic: row for row in explain.availability(db, settings=settings)}
    assert (rows[1].available, rows[1].reason) == (True, "ok")
    assert (rows[2].available, rows[2].reason) == (False, "not_passed")
    assert rows[1].title == repos.get_topic(db, 1).title


def test_availability_no_reference(db, settings) -> None:
    db.execute("UPDATE topics SET explain = NULL WHERE id = 1")
    assert _reasons(db, settings)[1] == "no_reference"


def test_availability_soon(db, settings) -> None:
    db.execute("UPDATE topics SET published = 0 WHERE id = 2")
    assert _reasons(db, settings)[2] == "soon"


def test_start_requires_passed_module(db, settings) -> None:
    with pytest.raises(explain.ExplainError) as error:
        _start(db, settings, topic_id=2)
    assert error.value.code == "not_available"


def test_start_opening_without_llm(db, settings) -> None:
    session = _start(db, settings)
    turns = repos.get_explain_turns(db, session.id)
    title = repos.get_topic(db, 1).title
    assert [(t.role, t.content) for t in turns] == [
        ("novice", PROMPTS.novice.opening.format(title=title))
    ]
    assert session.status == "active"


def test_start_returns_active_same_module(db, settings) -> None:
    first = _start(db, settings)
    assert _start(db, settings).id == first.id
    assert len(repos.list_explain_sessions(db)) == 1


def test_start_other_module_conflict(db, settings) -> None:
    _start(db, settings)
    with pytest.raises(explain.ExplainError) as error:
        _start(db, settings, topic_id=2)
    assert error.value.code == "other_active"


async def test_module_reopened_mid_session(db, settings) -> None:
    session = _start(db, settings)
    state_id = repos.get_open_session(db)
    lesson_state = repos.get_session_state(db, state_id)
    steps = [
        step.model_copy(update={"status": "ahead"}) if step.concept_id == "l1_select" else step
        for step in lesson_state.route.steps
    ]
    route = lesson_state.route.model_copy(update={"steps": steps})
    repos.update_session_state(db, state_id, lesson_state.model_copy(update={"route": route}))
    after = await explain.turn(
        db, ExplainLLM(), PROMPTS, session_id=session.id, text="Series", now=NOW, settings=settings
    )
    assert after.state.student_turns == 1
    _set_state(db, session.id, after.state)
    explain.finish(db, session_id=session.id, now=NOW, settings=settings)
    with pytest.raises(explain.ExplainError) as error:
        _start(db, settings)
    assert error.value.code == "not_available"


async def _mentor_turn(db, settings, llm, text="Не понимаю loc"):
    session = _start(db, settings)
    explain.set_mentor(db, session_id=session.id, on=True, now=NOW)
    return session, await explain.turn(
        db, llm, PROMPTS, session_id=session.id, text=text, now=NOW, settings=settings
    )


async def test_mentor_turn(db, settings) -> None:
    obs = ExplainObservation(ideas=[IdeaVerdict(id="loc_vs_iloc", verdict="partial")])
    llm = ExplainLLM(observations=[obs], mentor=["loc — по меткам."])
    session, after = await _mentor_turn(db, settings, llm)
    assert llm.order == ["mentor", "evaluator"]
    assert llm.models["mentor"] == [settings.tutor_model]
    assert "наставника" in llm.calls["evaluator"][0][-1].content
    assert after.state.mode == "mentor"
    assert after.state.mentor_ideas == ["loc_vs_iloc"]
    assert (after.state.student_turns, after.state.mentor_turns) == (0, 1)
    turns = repos.get_explain_turns(db, session.id)[1:]
    assert [(t.role, t.content) for t in turns] == [
        ("student", "Не понимаю loc"),
        ("mentor", "loc — по меткам."),
    ]
    assert turns[0].meta == {"mentor": True}


async def test_mentor_messages_have_reference(db, settings) -> None:
    llm = ExplainLLM()
    await _mentor_turn(db, settings, llm)
    sent = "\n".join(m.content for m in llm.calls["mentor"][0])
    config = repos.get_topic(db, 1).explain
    assert config.ideas[0].text in sent


async def test_mentor_failure_writes_nothing(db, settings) -> None:
    session = _start(db, settings)
    explain.set_mentor(db, session_id=session.id, on=True, now=NOW)
    with pytest.raises(explain.ExplainError) as error:
        await explain.turn(
            db, ExplainLLM(fail={"mentor"}), PROMPTS,
            session_id=session.id, text="?", now=NOW, settings=settings,
        )
    assert error.value.code == "llm"
    assert len(repos.get_explain_turns(db, session.id)) == 1


async def test_mentor_reply_hidden_from_novice(db, settings) -> None:
    llm = ExplainLLM(mentor=["Секрет наставника: loc — по меткам."])
    session, _ = await _mentor_turn(db, settings, llm)
    back = explain.set_mentor(db, session_id=session.id, on=False, now=NOW)
    assert back.state.mode == "novice"
    await explain.turn(db, llm, PROMPTS, session_id=session.id, text="Итак", now=NOW, settings=settings)
    sent = "\n".join(m.content for m in llm.calls["novice"][0])
    assert "Секрет наставника" not in sent
    assert "Не понимаю loc" not in sent


def _played(db, settings, **ideas) -> int:
    session = _start(db, settings)
    state = ExplainState(
        student_turns=2,
        ideas={key: IdeaProgress(state=value, quote="q") for key, value in ideas.items()},
    )
    _set_state(db, session.id, state)
    return session.id


def _explain_events(db) -> list:
    return [event for event in repos.get_events(db) if event.source == "explain"]


def test_finish_writes_events_and_schedule(db, settings) -> None:
    sid = _played(db, settings, series_vs_frame="correct", split_apply_combine="wrong")
    report = explain.finish(db, session_id=sid, now=NOW, settings=settings)
    events = _explain_events(db)
    assert {(e.concept_id, e.result) for e in events} == {("l1_frames", 1.0), ("l1_group", 0.0)}
    assert all(e.weight == settings.explain_evidence_weight for e in events)
    assert repos.get_mastery(db, "l1_frames") is not None
    rows = repos.get_review_rows(db)
    assert rows["l1_group"].step == 0 and rows["l1_group"].due_at == NOW + 86_400.0
    session = repos.get_explain_session(db, sid)
    assert (session.status, session.end_reason, session.ended_at) == ("finished", "button", NOW)
    assert session.report == report


def test_finish_idempotent(db, settings) -> None:
    sid = _played(db, settings, series_vs_frame="correct")
    first = explain.finish(db, session_id=sid, now=NOW, settings=settings)
    second = explain.finish(db, session_id=sid, now=NOW + 5, settings=settings)
    assert first == second
    assert len(_explain_events(db)) == 1


def test_finish_unknown_session(db, settings) -> None:
    with pytest.raises(explain.ExplainError) as error:
        explain.finish(db, session_id=999, now=NOW, settings=settings)
    assert error.value.code == "not_found"


def test_finish_empty_session_deleted(db, settings) -> None:
    session = _start(db, settings)
    assert explain.finish(db, session_id=session.id, now=NOW, settings=settings) is None
    assert repos.get_explain_session(db, session.id) is None
    assert _explain_events(db) == []


def test_finish_end_reason_coverage(db, settings) -> None:
    sid = _played(db, settings, series_vs_frame="correct")
    state = repos.get_explain_session(db, sid).state.model_copy(update={"wrap_up_offered": True})
    _set_state(db, sid, state)
    assert explain.finish(db, session_id=sid, now=NOW, settings=settings).end_reason == "coverage"


async def test_limit_finishes(db, settings) -> None:
    limits = PROMPTS.limits.model_copy(update={"max_student_turns": 2})
    prompts = PROMPTS.model_copy(update={"limits": limits})
    session = _start(db, settings)
    llm = ExplainLLM()
    for text in ("раз", "два"):
        after = await explain.turn(
            db, llm, prompts, session_id=session.id, text=text, now=NOW, settings=settings
        )
    assert (after.status, after.end_reason) == ("finished", "limit")
    assert after.report is not None


def test_stale_closed_on_start(db, settings) -> None:
    sid = _played(db, settings, series_vs_frame="correct")
    state = repos.get_explain_session(db, sid).state
    _set_state(db, sid, state, updated_at=NOW - 25 * HOUR)
    fresh = _start(db, settings)
    old = repos.get_explain_session(db, sid)
    assert fresh.id != sid
    assert (old.status, old.end_reason) == ("finished", "stale")
    assert old.report is not None


def test_recent_session_not_stale(db, settings) -> None:
    sid = _played(db, settings, series_vs_frame="correct")
    state = repos.get_explain_session(db, sid).state
    _set_state(db, sid, state, updated_at=NOW - 23 * HOUR)
    assert _start(db, settings).id == sid


def test_gone_lesson_skipped(db, settings) -> None:
    config = repos.get_topic(db, 1).explain.model_dump()
    config["ideas"][0]["lesson"] = "ghost_lesson"
    db.execute("UPDATE topics SET explain = ? WHERE id = 1", (json.dumps(config),))
    sid = _played(db, settings, series_vs_frame="wrong", loc_vs_iloc="correct")
    report = explain.finish(db, session_id=sid, now=NOW, settings=settings)
    assert "ghost_lesson" in report.review_lessons
    assert {e.concept_id for e in _explain_events(db)} == {"l1_select"}
    assert "ghost_lesson" not in repos.get_review_rows(db)
