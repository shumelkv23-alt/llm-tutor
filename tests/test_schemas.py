"""Тесты доменных схем (сериализация и roundtrip)."""

import pytest
from pydantic import ValidationError

from llm_tutor.schemas import Event, Message, PlannedNode, SessionState


def test_session_state_roundtrip() -> None:
    """SessionState переживает JSON туда-обратно без потерь."""
    state = SessionState(
        current_node_id="pandas_dataframe",
        mode="full",
        hint_level=2,
        pending_item_id=7,
        attempts=1,
        plan_snapshot=[PlannedNode(concept_id="x", mode="verify", priority=0.5)],
        last_activity=123.0,
    )
    assert SessionState.model_validate_json(state.model_dump_json()) == state


def test_session_state_defaults() -> None:
    """Пустое состояние — валидный дефолт."""
    state = SessionState()
    assert state.hint_level == 0
    assert state.plan_snapshot == []
    assert state.current_node_id is None


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
