"""Слабое свидетельство самооценки: «я это знаю» / «я это не знаю».

Самооценка не заменяет проверку: вес свидетельства мал
(``self_evidence_weight``), чтобы заявление не подменяло реальные задания.
Один и тот же путь используют анкета холодного старта и правка маршрута.
"""

import sqlite3

from llm_tutor.config import Settings
from llm_tutor.db import repos
from llm_tutor.schemas import Event
from llm_tutor.student import beta


def apply(
    conn: sqlite3.Connection,
    concept_id: str,
    *,
    correct: bool,
    now: float,
    settings: Settings,
    commit: bool = True,
) -> None:
    """Пишет слабое свидетельство самооценки: событие + обновление Beta.

    ``beta.update`` не годится: он всегда коммитит сам и не принимает
    ``commit``, а нам нужна возможность писать в общей транзакции хода.
    """
    weight = settings.self_evidence_weight
    repos.add_event(
        conn,
        Event(source="self", result=correct, concept_id=concept_id, weight=weight, ts=now),
        commit=commit,
    )
    change = beta.plan_update(
        conn, concept_id, correct=correct, weight=weight, now=now, settings=settings
    )
    beta.write_update(conn, change, commit=commit)
