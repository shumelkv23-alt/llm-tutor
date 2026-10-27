"""Хранение сессий «Объясни новичку» (срез 85)."""

import pytest

from course_fixtures import load_lessons
from llm_tutor.db import repos
from llm_tutor.schemas import ExplainReport, ExplainState, IdeaProgress


@pytest.fixture
def db(conn):
    load_lessons(conn)
    return conn


def _report() -> ExplainReport:
    return ExplainReport(
        topic_id=1,
        required_done=1,
        required_total=3,
        ideas_done=1,
        ideas_total=5,
        end_reason="button",
    )


def test_create_and_get_session(db) -> None:
    session_id = repos.create_explain_session(db, 1, now=10.0)
    session = repos.get_explain_session(db, session_id)
    assert session is not None
    assert session.topic_id == 1
    assert session.status == "active"
    assert session.started_at == session.updated_at == 10.0
    assert session.state == ExplainState()
    assert session.report is None


def test_missing_session_is_none(db) -> None:
    assert repos.get_explain_session(db, 999) is None


def test_active_session_is_latest_active(db) -> None:
    assert repos.active_explain_session(db) is None
    done = repos.create_explain_session(db, 1, now=1.0)
    repos.update_explain_session(
        db, done, state=ExplainState(), updated_at=2.0, status="finished", commit=True
    )
    live = repos.create_explain_session(db, 2, now=3.0)
    assert repos.active_explain_session(db).id == live


def test_turns_in_order_with_meta(db) -> None:
    session_id = repos.create_explain_session(db, 1, now=1.0)
    repos.add_explain_turn(db, session_id, "novice", "Привет!", ts=1.0)
    repos.add_explain_turn(db, session_id, "student", "Смотри…", ts=2.0, meta={"pasted": True})
    repos.add_explain_turn(db, session_id, "novice", "А почему?", ts=3.0)
    db.commit()
    turns = repos.get_explain_turns(db, session_id)
    assert [t.role for t in turns] == ["novice", "student", "novice"]
    assert [t.content for t in turns] == ["Привет!", "Смотри…", "А почему?"]
    assert turns[0].meta == {}
    assert turns[1].meta == {"pasted": True}


def test_update_writes_state_report_and_status(db) -> None:
    session_id = repos.create_explain_session(db, 1, now=1.0)
    state = ExplainState(student_turns=2, ideas={"loc_vs_iloc": IdeaProgress(state="correct")})
    repos.update_explain_session(
        db,
        session_id,
        state=state,
        updated_at=5.0,
        status="finished",
        ended_at=5.0,
        end_reason="button",
        report=_report(),
        commit=True,
    )
    session = repos.get_explain_session(db, session_id)
    assert session.state == state
    assert session.status == "finished"
    assert session.ended_at == 5.0
    assert session.end_reason == "button"
    assert session.report == _report()


def test_update_without_status_keeps_it(db) -> None:
    session_id = repos.create_explain_session(db, 1, now=1.0)
    repos.update_explain_session(
        db, session_id, state=ExplainState(student_turns=1), updated_at=2.0, commit=True
    )
    session = repos.get_explain_session(db, session_id)
    assert session.status == "active"
    assert session.updated_at == 2.0
    assert session.state.student_turns == 1


def test_delete_cascades_turns(db) -> None:
    session_id = repos.create_explain_session(db, 1, now=1.0)
    repos.add_explain_turn(db, session_id, "novice", "Привет!", ts=1.0, commit=True)
    repos.delete_explain_session(db, session_id, commit=True)
    assert repos.get_explain_session(db, session_id) is None
    assert repos.get_explain_turns(db, session_id) == []


def test_list_sessions_newest_first(db) -> None:
    first = repos.create_explain_session(db, 1, now=1.0)
    second = repos.create_explain_session(db, 2, now=2.0)
    assert [s.id for s in repos.list_explain_sessions(db)] == [second, first]
