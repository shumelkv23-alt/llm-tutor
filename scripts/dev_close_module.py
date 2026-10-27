"""Только для ручного теста: отмечает модуль пройденным в БД учеников.

Закрытое важное занятие даёт обязательное «Объясни новичку». Проходить ради
проверки семь занятий долго — скрипт закрывает их в снимке маршрута «сейчас»
(как это делает ход, когда занятие закрыто), и по важным занятиям модуля сразу
появляются обязательные задания. Свидетельств в журнал ``events`` не пишет:
владение и задания не трогаются.

Запуск (из папки проекта, сервер можно не останавливать):

    uv run python scripts/dev_close_module.py 1
    uv run python scripts/dev_close_module.py 2 --users-dir data/users

Не для учеников: на боевой БД так можно «пройти» модуль, ничего не решив.
"""

import argparse
import sqlite3
import time
from pathlib import Path

from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.db.connection import get_conn, migrate
from llm_tutor.student import explain as explain_logic


def close_module(conn: sqlite3.Connection, topic_id: int) -> int:
    """Закрывает занятия модуля в маршруте ученика; возвращает, сколько закрыто.

    Нет открытой сессии или маршрута (анкета ещё не пройдена) — ничего не делает.
    """
    session_id = repos.get_open_session(conn)
    state = repos.get_session_state(conn, session_id) if session_id else None
    if state is None or state.route is None:
        return 0
    graph = CourseGraph.load(conn)
    stamp = time.time()
    # БД, которую сервер ещё не открывал новой версией: занятия, закрытые скриптом
    # «сейчас», должны давать задание, а прежние — нет.
    explain_logic.mark_since(conn, stamp - 1.0)
    closed = 0
    steps = []
    for step in state.route.steps:
        if graph.has_node(step.concept_id) and graph.topic_of(step.concept_id) == topic_id:
            closed += 1
            was_closed = step.status == "closed" and step.closed_at is not None
            step = step.model_copy(
                update={"status": "closed", "closed_at": step.closed_at if was_closed else stamp}
            )
        steps.append(step)
    route = state.route.model_copy(update={"steps": steps})
    repos.update_session_state(conn, session_id, state.model_copy(update={"route": route}))
    return closed


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Отметить модуль пройденным (ручной тест).")
    parser.add_argument("topic", type=int, help="номер модуля, например 1")
    parser.add_argument("--users-dir", default="data/users", help="папка БД учеников")
    parser.add_argument("--course", default="mlcourse", help="id курса")
    args = parser.parse_args(argv)
    paths = sorted(Path(args.users_dir).glob(f"*/{args.course}.sqlite3"))
    if not paths:
        print(f"Не нашёл БД учеников в {args.users_dir} — сначала запишись на курс в браузере.")
        return
    for path in paths:
        conn = get_conn(str(path))
        try:
            # БД могла остаться от прошлой версии приложения: сервер мигрирует её
            # при открытии, а скрипт открывает файл сам.
            migrate(conn)
            closed = close_module(conn, args.topic)
        finally:
            conn.close()
        if closed:
            print(f"модуль {args.topic}: закрыто занятий — {closed} ({path})")
        else:
            print(f"пропускаю {path}: нет маршрута (пройди анкету) или в модуле нет занятий")


if __name__ == "__main__":
    main()
