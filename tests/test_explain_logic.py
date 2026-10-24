"""Логика «Объясни новичку» без модели: покрытие, вставка, установка хода (срез 86)."""

import pytest

from course_fixtures import LESSON_SEED_PATHS
from llm_tutor.course.seed import load_course_data
from llm_tutor.llm.prompt_config import ExplainLimits
from llm_tutor.llm.schemas import ExplainObservation, IdeaVerdict
from llm_tutor.schemas import ExplainState, IdeaProgress, PitfallUse
from llm_tutor.student import explain

CONFIG = load_course_data(LESSON_SEED_PATHS).topics[0].explain
LESSONS = ["l1_frames", "l1_select", "l1_group"]
LIMITS = ExplainLimits()
REQUIRED = [idea.id for idea in CONFIG.ideas if idea.required]
OPTIONAL = [idea.id for idea in CONFIG.ideas if not idea.required]


def _obs(**fields) -> ExplainObservation:
    return ExplainObservation(**fields)


def _verdict(idea: str, verdict: str, quote: str | None = None) -> IdeaVerdict:
    return IdeaVerdict(id=idea, verdict=verdict, quote=quote)


def _state(**ideas: str) -> ExplainState:
    return ExplainState(ideas={key: IdeaProgress(state=value) for key, value in ideas.items()})


def _choose(state: ExplainState, obs: ExplainObservation | None = None, *, pasted: bool = False):
    return explain.choose_directive(
        state, obs or _obs(), CONFIG, pasted=pasted, limits=LIMITS, lesson_order=LESSONS
    )


# --- clean_observation ---


def test_quote_must_be_in_text() -> None:
    obs = _obs(ideas=[_verdict("loc_vs_iloc", "correct", "loc — по меткам")])
    cleaned = explain.clean_observation(obs, CONFIG, "iloc берёт позиции")
    assert cleaned.ideas[0].verdict == "partial"


def test_correct_without_quote_is_partial() -> None:
    obs = _obs(ideas=[_verdict("loc_vs_iloc", "correct")])
    assert explain.clean_observation(obs, CONFIG, "что угодно").ideas[0].verdict == "partial"


def test_quote_ignores_markdown_and_case() -> None:
    obs = _obs(ideas=[_verdict("split_apply_combine", "correct", "df.groupby('x')")])
    text = "Смотри: `DF.groupby('x')`\nвот так"
    assert explain.clean_observation(obs, CONFIG, text).ideas[0].verdict == "correct"


def test_quote_with_line_break_matches() -> None:
    obs = _obs(ideas=[_verdict("split_apply_combine", "correct", "делим на группы и считаем")])
    text = "Мы делим на\nгруппы   и считаем среднее"
    assert explain.clean_observation(obs, CONFIG, text).ideas[0].verdict == "correct"


def test_unknown_ids_dropped() -> None:
    obs = _obs(
        ideas=[_verdict("nope", "wrong"), _verdict("loc_vs_iloc", "wrong")],
        pitfalls_named=["nope", "iloc_by_label"],
    )
    cleaned = explain.clean_observation(obs, CONFIG, "текст")
    assert [v.id for v in cleaned.ideas] == ["loc_vs_iloc"]
    assert cleaned.pitfalls_named == ["iloc_by_label"]


def test_contradiction_trimmed_to_300() -> None:
    cleaned = explain.clean_observation(_obs(contradiction="я" * 500), CONFIG, "текст")
    assert len(cleaned.contradiction) == 300


# --- apply_observation ---


@pytest.mark.parametrize(
    ("before", "verdict", "after"),
    [
        ("missing", "partial", "partial"),
        ("missing", "correct", "correct"),
        ("missing", "wrong", "wrong"),
        ("partial", "wrong", "wrong"),
        ("partial", "correct", "correct"),
        ("wrong", "correct", "correct"),
        ("wrong", "partial", "wrong"),
        ("correct", "wrong", "wrong"),
        ("correct", "partial", "correct"),
    ],
)
def test_transitions(before: str, verdict: str, after: str) -> None:
    state = _state(loc_vs_iloc=before)
    obs = _obs(ideas=[_verdict("loc_vs_iloc", verdict, "q")])
    new = explain.apply_observation(state, obs, pasted=False)
    assert new.ideas["loc_vs_iloc"].state == after


def test_quote_kept_on_change() -> None:
    obs = _obs(ideas=[_verdict("loc_vs_iloc", "correct", "по меткам")])
    new = explain.apply_observation(ExplainState(), obs, pasted=False)
    assert new.ideas["loc_vs_iloc"].quote == "по меткам"


def test_counts_student_turn() -> None:
    new = explain.apply_observation(ExplainState(), _obs(), pasted=False)
    assert new.student_turns == 1
    assert new.pasted_turns == 0


def test_pasted_turn_changes_nothing() -> None:
    obs = _obs(ideas=[_verdict("loc_vs_iloc", "correct", "q")])
    new = explain.apply_observation(ExplainState(), obs, pasted=True)
    assert "loc_vs_iloc" not in new.ideas
    assert new.pasted_turns == 1
    assert new.student_turns == 1


def test_mentor_flag_on_correct() -> None:
    state = ExplainState(mentor_ideas=["loc_vs_iloc"])
    obs = _obs(ideas=[_verdict("loc_vs_iloc", "correct", "q")])
    assert explain.apply_observation(state, obs, pasted=False).ideas["loc_vs_iloc"].mentor


def test_no_mentor_flag_without_mentor() -> None:
    obs = _obs(ideas=[_verdict("loc_vs_iloc", "correct", "q")])
    assert not explain.apply_observation(ExplainState(), obs, pasted=False).ideas["loc_vs_iloc"].mentor


def test_pitfall_fixed_marks_last_use() -> None:
    state = ExplainState(
        student_turns=2,
        pitfalls_used=[PitfallUse(id="iloc_by_label", turn=2)],
        last_pitfall_turn=2,
    )
    new = explain.apply_observation(state, _obs(pitfall_fixed=True), pasted=False)
    assert new.pitfalls_used[0].fixed is True


def test_pitfall_fixed_ignored_for_old_use() -> None:
    state = ExplainState(student_turns=5, pitfalls_used=[PitfallUse(id="iloc_by_label", turn=2)])
    new = explain.apply_observation(state, _obs(pitfall_fixed=True), pasted=False)
    assert new.pitfalls_used[0].fixed is None


def test_named_pitfalls_accumulate_once() -> None:
    state = ExplainState(pitfalls_named=["iloc_by_label"])
    obs = _obs(pitfalls_named=["iloc_by_label", "groupby_returns_df"])
    new = explain.apply_observation(state, obs, pasted=False)
    assert new.pitfalls_named == ["iloc_by_label", "groupby_returns_df"]


def test_answer_requests_counted() -> None:
    new = explain.apply_observation(ExplainState(), _obs(asks_for_answer=True), pasted=False)
    assert new.answer_requests == 1


def test_stalled_turns() -> None:
    state = explain.apply_observation(ExplainState(), _obs(), pasted=False)
    state = explain.apply_observation(state, _obs(), pasted=False)
    assert state.stalled_turns == 2
    obs = _obs(ideas=[_verdict("loc_vs_iloc", "partial", "q")])
    assert explain.apply_observation(state, obs, pasted=False).stalled_turns == 0


# --- looks_pasted ---

THEORY = " ".join(f"слово{i} объясняет таблицу номер {i} подробно и понятно" for i in range(60))


def test_looks_pasted_copy() -> None:
    text = THEORY[:700]
    assert explain.looks_pasted(text, [THEORY], min_chars=600, overlap=0.5)


def test_looks_pasted_own_text() -> None:
    text = " ".join(f"моё{i} рассуждение про группы строк" for i in range(40))[:700]
    assert len(text) >= 600
    assert not explain.looks_pasted(text, [THEORY], min_chars=600, overlap=0.5)


def test_looks_pasted_short_copy() -> None:
    assert not explain.looks_pasted(THEORY[:300], [THEORY], min_chars=600, overlap=0.5)


# --- required_progress ---


def test_required_progress() -> None:
    state = _state(**{REQUIRED[0]: "correct", REQUIRED[1]: "partial", OPTIONAL[0]: "correct"})
    assert explain.required_progress(state, CONFIG) == (1, len(REQUIRED))


# --- choose_directive ---


def test_pasted_gives_own_words() -> None:
    plan, _ = _choose(ExplainState(student_turns=1), _obs(contradiction="x"), pasted=True)
    assert plan.directive == "own_words"


def test_contradiction_gives_confused() -> None:
    plan, _ = _choose(ExplainState(student_turns=1), _obs(contradiction="то одно, то другое"))
    assert plan.directive == "confused"
    assert plan.contradiction == "то одно, то другое"


def test_frustrated_offers_mentor() -> None:
    plan, _ = _choose(ExplainState(student_turns=1), _obs(frustrated=True))
    assert plan.directive == "offer_mentor"


def test_first_answer_request_is_no_answer() -> None:
    state = ExplainState(student_turns=1, answer_requests=1)
    plan, _ = _choose(state, _obs(asks_for_answer=True))
    assert plan.directive == "no_answer"


def test_second_answer_request_offers_mentor() -> None:
    state = ExplainState(student_turns=2, answer_requests=2)
    plan, _ = _choose(state, _obs(asks_for_answer=True))
    assert plan.directive == "offer_mentor"


def test_stalled_offers_mentor_and_resets() -> None:
    plan, new = _choose(ExplainState(student_turns=3, stalled_turns=3))
    assert plan.directive == "offer_mentor"
    assert new.stalled_turns == 0


def test_wrap_up_on_coverage() -> None:
    state = _state(**{idea: "correct" for idea in REQUIRED})
    state = state.model_copy(update={"student_turns": 1})
    plan, new = _choose(state)
    assert plan.directive == "wrap_up"
    assert new.wrap_up_offered


def test_wrap_up_once() -> None:
    state = _state(**{idea: "correct" for idea in REQUIRED})
    state = state.model_copy(update={"student_turns": 1, "wrap_up_offered": True})
    plan, _ = _choose(state)
    assert plan.directive != "wrap_up"


def test_wrap_up_below_share() -> None:
    state = _state(**{REQUIRED[0]: "correct", REQUIRED[1]: "correct"})  # 2 из 3 < 0.8
    plan, _ = _choose(state.model_copy(update={"student_turns": 1}))
    assert plan.directive != "wrap_up"


def test_pitfall_after_lesson_spoken() -> None:
    state = _state(loc_vs_iloc="partial").model_copy(update={"student_turns": 3})
    plan, new = _choose(state)
    assert plan.directive == "pitfall"
    assert plan.pitfall.id == "iloc_by_label"
    assert plan.lesson == "l1_select"
    assert new.pitfalls_used == [PitfallUse(id="iloc_by_label", turn=3)]
    assert new.last_pitfall_turn == 3


def test_no_pitfall_too_early() -> None:
    state = _state(loc_vs_iloc="partial").model_copy(update={"student_turns": 2})
    assert _choose(state)[0].directive != "pitfall"


def test_pitfall_pacing() -> None:
    state = _state(loc_vs_iloc="partial", split_apply_combine="correct").model_copy(
        update={
            "student_turns": 5,
            "last_pitfall_turn": 3,
            "pitfalls_used": [PitfallUse(id="iloc_by_label", turn=3)],
        }
    )
    assert _choose(state)[0].directive != "pitfall"
    later = state.model_copy(update={"student_turns": 6})
    plan, _ = _choose(later)
    assert plan.directive == "pitfall"
    assert plan.pitfall.id == "groupby_returns_df"


def test_pitfall_max_per_session() -> None:
    limits = ExplainLimits(pitfall_max=1)
    state = _state(loc_vs_iloc="partial", split_apply_combine="correct").model_copy(
        update={
            "student_turns": 9,
            "last_pitfall_turn": 3,
            "pitfalls_used": [PitfallUse(id="iloc_by_label", turn=3)],
        }
    )
    plan, _ = explain.choose_directive(
        state, _obs(), CONFIG, pasted=False, limits=limits, lesson_order=LESSONS
    )
    assert plan.directive != "pitfall"


def test_named_pitfall_not_injected() -> None:
    state = _state(loc_vs_iloc="partial").model_copy(
        update={"student_turns": 3, "pitfalls_named": ["iloc_by_label"]}
    )
    assert _choose(state)[0].directive != "pitfall"


def test_ask_first_lesson_with_missing() -> None:
    state = _state(series_vs_frame="correct").model_copy(update={"student_turns": 1})
    plan, _ = _choose(state)
    assert plan.directive == "ask"
    assert plan.lesson == "l1_select"
    assert plan.pitfall is None


def test_ask_when_nothing_said() -> None:
    plan, _ = _choose(ExplainState(student_turns=1))
    assert (plan.directive, plan.lesson) == ("ask", "l1_frames")
