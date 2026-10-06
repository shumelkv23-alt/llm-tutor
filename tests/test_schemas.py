"""Тесты доменных схем (сериализация и roundtrip)."""

import pytest
from pydantic import ValidationError

from llm_tutor.schemas import Event, Message, Route, RouteStep, SessionState


def test_session_state_roundtrip() -> None:
    """SessionState переживает JSON туда-обратно без потерь."""
    state = SessionState(
        current_node_id="pandas_dataframe",
        mode="full",
        hint_level=2,
        pending_item_id=7,
        attempts=1,
        route=Route(
            goal_concept_id="summary_tables",
            steps=[RouteStep(concept_id="x", mode="verify", status="current")],
        ),
        phase="practice",
        node_streak=1,
        last_activity=123.0,
    )
    assert SessionState.model_validate_json(state.model_dump_json()) == state


def test_route_roundtrip_and_closed_count() -> None:
    route = Route(
        goal_concept_id="churn_eda_case",
        steps=[
            RouteStep(concept_id="groupby", mode="full", status="closed"),
            RouteStep(concept_id="agg_functions", mode="compressed", status="current"),
            RouteStep(concept_id="summary_tables", mode="full", status="ahead"),
        ],
    )

    assert Route.model_validate_json(route.model_dump_json()) == route
    assert route.closed_count == 1


def test_session_state_defaults_for_guidance() -> None:
    state = SessionState()

    assert state.route is None
    assert state.phase == "explain"
    assert state.node_streak == 0


def test_old_state_with_plan_snapshot_is_still_readable() -> None:
    """Сохранённое состояние прошлой версии не должно ронять чтение."""
    old = '{"plan_snapshot": [{"concept_id": "x", "mode": "full", "priority": 1.0}]}'

    state = SessionState.model_validate_json(old)

    assert state.route is None


def test_message_roundtrip() -> None:
    msg = Message(role="user", content="привет", ts=1.0, session_id=3)
    assert Message.model_validate(msg.model_dump()) == msg


def test_message_rejects_unknown_role() -> None:
    with pytest.raises(ValidationError):
        Message(role="bot", content="x")


def test_event_roundtrip() -> None:
    event = Event(source="rubric", result=0.5, concept_id="c", weight=0.7)
    assert Event.model_validate(event.model_dump()) == event


def test_event_rejects_unknown_source() -> None:
    with pytest.raises(ValidationError):
        Event(source="telepathy", result=1.0)


def test_event_rejects_out_of_range_result() -> None:
    """result вне 0..1 (мусор в Beta-модель) отклоняется на границе."""
    with pytest.raises(ValidationError):
        Event(source="self", result=1.5)
    with pytest.raises(ValidationError):
        Event(source="self", result=-0.1)


def test_event_rejects_negative_hints() -> None:
    with pytest.raises(ValidationError):
        Event(source="self", result=0.5, hints_used=-1)


def test_message_id_and_meta_default_to_none() -> None:
    message = Message(role="user", content="x")
    assert message.id is None
    assert message.meta is None
