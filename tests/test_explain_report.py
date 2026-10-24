"""Отчёт, свидетельства и повторения «Объясни новичку» (срез 86)."""

from course_fixtures import LESSON_SEED_PATHS, finish_all_but, load_lessons
from llm_tutor.course.graph import CourseGraph
from llm_tutor.course.seed import load_course_data
from llm_tutor.db import repos
from llm_tutor.schemas import (
    Event,
    ExplainConfig,
    ExplainIdea,
    ExplainState,
    IdeaProgress,
    PitfallUse,
    ReviewRow,
)
from llm_tutor.student import explain
from llm_tutor.student import route as route_mod

CONFIG = load_course_data(LESSON_SEED_PATHS).topics[0].explain
DAY = 86_400.0
NOW = 1_000_000.0


def _state(**ideas) -> ExplainState:
    return ExplainState(
        ideas={
            key: value if isinstance(value, IdeaProgress) else IdeaProgress(state=value, quote="q")
            for key, value in ideas.items()
        }
    )


def _report(state: ExplainState, config: ExplainConfig = CONFIG):
    return explain.build_report(state, config, topic_id=1, end_reason="button")


def test_report_counts_and_reference() -> None:
    # Обязательные: series_vs_frame, loc_vs_iloc, split_apply_combine.
    report = _report(
        _state(series_vs_frame="correct", loc_vs_iloc="correct", split_apply_combine="wrong")
    )
    assert (report.required_done, report.required_total) == (2, 3)
    assert (report.ideas_done, report.ideas_total) == (2, 5)
    by_id = {idea.id: idea for idea in report.ideas}
    assert by_id["series_vs_frame"].reference is None
    texts = {idea.id: idea.text for idea in CONFIG.ideas}
    assert by_id["split_apply_combine"].reference == texts["split_apply_combine"]
    assert by_id["pivot_table"].state == "missing"
    assert by_id["pivot_table"].reference == texts["pivot_table"]
    assert [idea.id for idea in report.ideas] == [idea.id for idea in CONFIG.ideas]
    assert report.end_reason == "button"
    assert report.topic_id == 1


def test_report_pitfalls() -> None:
    state = ExplainState(
        pitfalls_named=["groupby_returns_df"],
        pitfalls_used=[PitfallUse(id="iloc_by_label", turn=3, fixed=False)],
    )
    report = _report(state)
    outcomes = {p.id: (p.outcome, p.fix) for p in report.pitfalls}
    fixes = {p.id: p.fix for p in CONFIG.pitfalls}
    assert outcomes == {
        "groupby_returns_df": ("named", None),
        "iloc_by_label": ("missed", fixes["iloc_by_label"]),
    }


def test_report_fixed_pitfall() -> None:
    state = ExplainState(pitfalls_used=[PitfallUse(id="iloc_by_label", turn=3, fixed=True)])
    assert [(p.id, p.outcome, p.fix) for p in _report(state).pitfalls] == [
        ("iloc_by_label", "fixed", None)
    ]


def test_report_skips_unused_pitfalls() -> None:
    assert _report(ExplainState()).pitfalls == []


def test_report_counters() -> None:
    state = ExplainState(student_turns=7, mentor_turns=2, pasted_turns=1)
    report = _report(state)
    assert (report.student_turns, report.mentor_used, report.pasted_turns) == (7, True, 1)
    assert _report(ExplainState()).mentor_used is False


def test_review_lessons_order() -> None:
    # l1_group — ошибка, l1_frames — пробел обязательной, l1_select — всё верно.
    state = _state(
        series_vs_frame="partial",
        loc_vs_iloc="correct",
        boolean_mask="correct",
        split_apply_combine="wrong",
        pivot_table="wrong",
    )
    assert _report(state).review_lessons == ["l1_group", "l1_frames"]


def test_review_lessons_missed_pitfall() -> None:
    state = _state(series_vs_frame="correct", loc_vs_iloc="correct", split_apply_combine="correct")
    state = state.model_copy(
        update={"pitfalls_used": [PitfallUse(id="iloc_by_label", turn=3, fixed=False)]}
    )
    assert _report(state).review_lessons == ["l1_select"]


def test_unknown_state_ids_ignored() -> None:
    state = _state(gone_idea="correct", series_vs_frame="correct")
    report = _report(state)
    assert "gone_idea" not in {idea.id for idea in report.ideas}
    extra = ExplainIdea(id="new_idea", lesson="l1_frames", text="Новая", expect="x")
    config = CONFIG.model_copy(update={"ideas": [*CONFIG.ideas, extra]})
    states = {idea.id: idea.state for idea in _report(state, config).ideas}
    assert states["new_idea"] == "missing"


def test_evidence_weights() -> None:
    state = _state(
        series_vs_frame="correct",
        loc_vs_iloc=IdeaProgress(state="correct", quote="по меткам", mentor=True),
        split_apply_combine="wrong",
        boolean_mask="partial",
    )
    events = explain.plan_evidence(_report(state), weight=0.3, now=NOW)
    assert all(isinstance(event, Event) and event.source == "explain" for event in events)
    summary = {(e.concept_id, e.result, e.weight) for e in events}
    assert summary == {("l1_frames", 1.0, 0.3), ("l1_select", 1.0, 0.15), ("l1_group", 0.0, 0.3)}
    assert len(events) == 3
    assert all(event.ts == NOW for event in events)
    assert "loc_vs_iloc: по меткам" in {event.citation for event in events}


def test_no_evidence_without_verdicts() -> None:
    assert explain.plan_evidence(_report(ExplainState()), weight=0.3, now=NOW) == []


def _schedule(state: ExplainState, rows=None):
    return explain.plan_schedule(_report(state), CONFIG, rows or {}, now=NOW)


def test_schedule_wrong() -> None:
    state = _state(split_apply_combine="wrong")
    rows = {"l1_group": ReviewRow(concept_id="l1_group", step=4, due_at=NOW + 30 * DAY)}
    changes = {c.concept_id: c for c in _schedule(state, rows)}
    assert (changes["l1_group"].step, changes["l1_group"].due_at) == (2, NOW + DAY)
    fresh = {c.concept_id: c for c in _schedule(state)}
    assert (fresh["l1_group"].step, fresh["l1_group"].due_at) == (0, NOW + DAY)


def test_schedule_gap() -> None:
    state = _state(series_vs_frame="correct", loc_vs_iloc="partial", split_apply_combine="correct")
    rows = {"l1_select": ReviewRow(concept_id="l1_select", step=3, due_at=NOW + 9 * DAY)}
    changes = {c.concept_id: c for c in _schedule(state, rows)}
    assert set(changes) == {"l1_select"}
    assert (changes["l1_select"].step, changes["l1_select"].due_at) == (3, NOW + DAY)
    fresh = {c.concept_id: c for c in _schedule(state)}
    assert (fresh["l1_select"].step, fresh["l1_select"].due_at) == (0, NOW + DAY)


def test_schedule_missed_pitfall_is_gap() -> None:
    state = _state(series_vs_frame="correct", loc_vs_iloc="correct", split_apply_combine="correct")
    state = state.model_copy(
        update={"pitfalls_used": [PitfallUse(id="iloc_by_label", turn=3, fixed=False)]}
    )
    assert [c.concept_id for c in _schedule(state)] == ["l1_select"]


def test_schedule_all_correct_untouched() -> None:
    state = _state(**{idea.id: "correct" for idea in CONFIG.ideas})
    assert _schedule(state) == []


def test_one_change_per_lesson() -> None:
    state = _state(split_apply_combine="wrong", pivot_table="wrong")
    assert [c.concept_id for c in _schedule(state)].count("l1_group") == 1


def _failures_after_close(conn, settings, source: str):
    load_lessons(conn)
    finish_all_but(conn, "l1_group", topic_id=1)
    graph = CourseGraph.load(conn)
    previous = repos.get_session_state(conn, repos.get_open_session(conn)).route
    for ts in (10.0, 11.0):
        repos.add_event(
            conn, Event(source=source, result=0.0, concept_id="l1_select", weight=0.3), ts
        )
    rebuilt = route_mod.build_route(
        conn, graph, current_node_id="l1_group", previous=previous, now=20.0, settings=settings
    )
    return {step.concept_id: step.status for step in rebuilt.steps}["l1_select"]


def test_explain_failures_do_not_reopen(conn, settings) -> None:
    assert _failures_after_close(conn, settings, "explain") == "closed"


def test_dialogue_failures_reopen(conn, settings) -> None:
    # Контроль: те же провалы из диалога занятие открывают (REOPEN_FAILURES).
    assert _failures_after_close(conn, settings, "dialogue") != "closed"


def test_unclear_pitfall_not_scheduled() -> None:
    # Ученик на загвоздку не ответил ясно (fixed=None): не «пропустил ошибку».
    state = _state(**{idea.id: "correct" for idea in CONFIG.ideas})
    state = state.model_copy(update={"pitfalls_used": [PitfallUse(id="iloc_by_label", turn=3)]})
    report = _report(state)
    assert [(p.id, p.outcome) for p in report.pitfalls] == [("iloc_by_label", "unclear")]
    assert report.review_lessons == []
    assert _schedule(state) == []


def test_refuted_pitfall_is_missed() -> None:
    state = ExplainState(pitfalls_used=[PitfallUse(id="iloc_by_label", turn=3, fixed=False)])
    assert [(p.outcome, p.fix is not None) for p in _report(state).pitfalls] == [("missed", True)]
