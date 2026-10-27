"""Сессия «Объясни новичку»: наставник, завершение, брошенные сессии (срезы 87, 93)."""

from pathlib import Path

import pytest

from course_fixtures import load_lessons
from llm_tutor.core import explain
from llm_tutor.db import repos
from llm_tutor.llm.prompt_config import load_explain
from llm_tutor.llm.schemas import ExplainObservation, IdeaVerdict
from llm_tutor.schemas import ExplainState, IdeaProgress
from llm_tutor.student import explain as logic
from web_fakes import ExplainLLM

PROMPTS = load_explain(Path(__file__).resolve().parent.parent / "data" / "prompts")
NOW = 1_000_000.0
HOUR = 3600.0


@pytest.fixture
def db(conn):
    load_lessons(conn)
    logic.mark_since(conn, 1.0)
    return conn


def _create(db, *, concept_id="l1_select", kind="voluntary", now: float = NOW) -> int:
    sid = repos.create_explain_session(db, 1, now=now, concept_id=concept_id, kind=kind)
    repos.add_explain_turn(db, sid, "novice", "Привет!", ts=now, commit=True)
    return sid


def _set_state(db, session_id: int, state: ExplainState, *, updated_at: float = NOW) -> None:
    repos.update_explain_session(db, session_id, state=state, updated_at=updated_at, commit=True)


async def _mentor(db, settings, llm, text="Не понимаю loc", sid: int | None = None):
    sid = sid or _create(db)
    return sid, await explain.turn(
        db, llm, PROMPTS, session_id=sid, text=text, to="mentor", now=NOW, settings=settings
    )


async def test_mentor_turn(db, settings) -> None:
    obs = ExplainObservation(ideas=[IdeaVerdict(id="loc_vs_iloc", verdict="partial")])
    llm = ExplainLLM(observations=[obs], mentor=["loc — по меткам."])
    sid, after = await _mentor(db, settings, llm)
    assert llm.order == ["mentor", "evaluator"]
    assert llm.models["mentor"] == [settings.tutor_model]
    assert "наставника" in llm.calls["evaluator"][0][-1].content
    assert after.state.mentor_ideas == ["loc_vs_iloc"]
    assert (after.state.student_turns, after.state.mentor_turns) == (0, 1)
    turns = repos.get_explain_turns(db, sid)[1:]
    assert [(t.role, t.content) for t in turns] == [
        ("student", "Не понимаю loc"),
        ("mentor", "loc — по меткам."),
    ]
    assert turns[0].meta == {"mentor": True}


async def test_mentor_messages_have_reference_and_theory(db, settings) -> None:
    llm = ExplainLLM()
    await _mentor(db, settings, llm)
    sent = "\n".join(m.content for m in llm.calls["mentor"][0])
    config = repos.get_explain_configs(db)["l1_select"]
    assert config.ideas[0].text in sent
    assert repos.get_theory(db, "l1_select")[:60] in sent


async def test_mentor_failure_writes_nothing(db, settings) -> None:
    sid = _create(db)
    with pytest.raises(explain.ExplainError) as error:
        await _mentor(db, settings, ExplainLLM(fail={"mentor"}), "?", sid)
    assert error.value.code == "llm"
    assert len(repos.get_explain_turns(db, sid)) == 1


async def test_mentor_reply_hidden_from_novice(db, settings) -> None:
    llm = ExplainLLM(mentor=["Секрет наставника: loc — по меткам."])
    sid, _ = await _mentor(db, settings, llm)
    await explain.turn(
        db, llm, PROMPTS, session_id=sid, text="Итак", to="novice", now=NOW, settings=settings
    )
    sent = "\n".join(m.content for m in llm.calls["novice"][0])
    assert "Секрет наставника" not in sent
    assert "Не понимаю loc" not in sent


def _played(db, **ideas) -> int:
    sid = _create(db)
    state = ExplainState(
        student_turns=2,
        ideas={key: IdeaProgress(state=value, quote="q") for key, value in ideas.items()},
    )
    _set_state(db, sid, state)
    return sid


def _explain_events(db) -> list:
    return [event for event in repos.get_events(db) if event.source == "explain"]


def test_finish_writes_events_and_schedule(db, settings) -> None:
    sid = _played(db, loc_vs_iloc="correct", boolean_mask="wrong")
    report = explain.finish(db, session_id=sid, now=NOW, settings=settings)
    events = _explain_events(db)
    assert {(e.concept_id, e.result) for e in events} == {("l1_select", 1.0), ("l1_select", 0.0)}
    assert all(e.weight == settings.explain_evidence_weight for e in events)
    assert repos.get_mastery(db, "l1_select") is not None
    rows = repos.get_review_rows(db)
    assert rows["l1_select"].step == 0 and rows["l1_select"].due_at == NOW + 86_400.0
    session = repos.get_explain_session(db, sid)
    assert (session.status, session.end_reason, session.ended_at) == ("finished", "button", NOW)
    assert session.report == report


def test_finish_idempotent(db, settings) -> None:
    sid = _played(db, loc_vs_iloc="correct")
    first = explain.finish(db, session_id=sid, now=NOW, settings=settings)
    second = explain.finish(db, session_id=sid, now=NOW + 5, settings=settings)
    assert first == second
    assert len(_explain_events(db)) == 1


def test_finish_unknown_session(db, settings) -> None:
    with pytest.raises(explain.ExplainError) as error:
        explain.finish(db, session_id=999, now=NOW, settings=settings)
    assert error.value.code == "not_found"


def test_finish_empty_session_deleted(db, settings) -> None:
    sid = _create(db)
    assert explain.finish(db, session_id=sid, now=NOW, settings=settings) is None
    assert repos.get_explain_session(db, sid) is None
    assert _explain_events(db) == []


def test_finish_end_reason_coverage(db, settings) -> None:
    sid = _played(db, loc_vs_iloc="correct")
    state = repos.get_explain_session(db, sid).state.model_copy(update={"wrap_up_offered": True})
    _set_state(db, sid, state)
    assert explain.finish(db, session_id=sid, now=NOW, settings=settings).end_reason == "coverage"


async def test_limit_finishes(db, settings) -> None:
    limits = PROMPTS.limits.model_copy(update={"max_student_turns": 2})
    prompts = PROMPTS.model_copy(update={"limits": limits})
    sid = _create(db, kind="required")
    llm = ExplainLLM()
    for text in ("раз", "два"):
        after = await explain.turn(
            db, llm, prompts, session_id=sid, text=text, to="novice", now=NOW, settings=settings
        )
    assert (after.status, after.end_reason) == ("finished", "limit")
    assert after.report is not None


def test_stale_voluntary_closed(db, settings) -> None:
    _closed(db)
    sid = _played(db, loc_vs_iloc="correct")
    state = repos.get_explain_session(db, sid).state
    _set_state(db, sid, state, updated_at=NOW - 25 * HOUR)
    fresh = explain.start_voluntary(db, PROMPTS, node_id="l1_select", now=NOW, settings=settings)
    old = repos.get_explain_session(db, sid)
    assert fresh.id != sid
    assert (old.status, old.end_reason) == ("finished", "stale")
    assert old.report is not None


def _closed(db) -> None:
    """Ученик закрыл l1_select: добровольная сессия по нему возможна."""
    from llm_tutor.schemas import Route, RouteStep, SessionState

    steps = [RouteStep(concept_id="l1_select", mode="full", status="closed", closed_at=0.5)]
    sid = repos.ensure_open_session(db, now=1.0)
    repos.update_session_state(db, sid, SessionState(route=Route(steps=steps)))


def test_recent_voluntary_not_stale(db, settings) -> None:
    _closed(db)
    sid = _played(db, loc_vs_iloc="correct")
    state = repos.get_explain_session(db, sid).state
    _set_state(db, sid, state, updated_at=NOW - 23 * HOUR)
    assert explain.start_voluntary(db, PROMPTS, node_id="l1_select", now=NOW, settings=settings).id == sid


def test_required_never_stale_by_time(db, settings) -> None:
    sid = _create(db, kind="required")
    _set_state(db, sid, ExplainState(student_turns=1), updated_at=NOW - 100 * HOUR)
    explain.close_stale(db, PROMPTS, now=NOW, settings=settings)
    assert repos.get_explain_session(db, sid).status == "active"


def test_legacy_module_session_closed(db, settings) -> None:
    sid = repos.create_explain_session(db, 1, now=NOW)  # без занятия: старая модульная
    _set_state(db, sid, ExplainState(student_turns=1))
    explain.close_stale(db, PROMPTS, now=NOW, settings=settings)
    old = repos.get_explain_session(db, sid)
    assert (old.status, old.end_reason) == ("finished", "stale")


def test_gone_lesson_skipped(db, settings) -> None:
    # Идея сослалась на занятие, которого нет в графе: в отчёте остаётся, событий нет.
    sid = _played(db, loc_vs_iloc="correct", boolean_mask="wrong")
    db.execute("UPDATE concepts SET active = 0 WHERE id = 'l1_select'")
    db.commit()
    explain.finish(db, session_id=sid, now=NOW, settings=settings)
    assert _explain_events(db) == []
    assert "l1_select" not in repos.get_review_rows(db)


def test_finish_failure_rolls_back(db, settings, monkeypatch) -> None:
    # Сбой не из sqlite внутри завершения не оставляет полузаписи на общем соединении.
    sid = _played(db, loc_vs_iloc="correct")

    def boom(*args, **kwargs):
        raise ValueError("сбой")

    monkeypatch.setattr(explain.logic, "plan_schedule", boom)
    with pytest.raises(ValueError):
        explain.finish(db, session_id=sid, now=NOW, settings=settings)
    assert not db.in_transaction
    assert _explain_events(db) == []
    assert repos.get_explain_session(db, sid).status == "active"
