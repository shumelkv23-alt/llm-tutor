"""Скрипт ручного теста: модуль отмечается пройденным в БД ученика."""

import importlib.util
from pathlib import Path

from course_fixtures import load_lessons
from llm_tutor.core import explain
from llm_tutor.db import repos
from llm_tutor.db.connection import get_conn, migrate
from llm_tutor.schemas import Route, RouteStep, SessionState

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "dev_close_module.py"
spec = importlib.util.spec_from_file_location("dev_close_module", SCRIPT)
dev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dev)


def _student_db(path: Path):
    conn = get_conn(str(path))
    migrate(conn)
    load_lessons(conn)
    session_id = repos.ensure_open_session(conn, now=1.0)
    steps = [
        RouteStep(concept_id=node, mode="full", status="current" if index == 0 else "ahead")
        for index, node in enumerate(["l1_frames", "l1_select", "l1_group", "l2_hist", "l2_box", "l2_corr"])
    ]
    state = SessionState(current_node_id="l1_frames", route=Route(steps=steps, topic_id=1))
    repos.update_session_state(conn, session_id, state)
    return conn


def test_close_module_marks_only_that_module(tmp_path, settings) -> None:
    conn = _student_db(tmp_path / "mlcourse.sqlite3")
    assert dev.close_module(conn, 1) == 3
    route = repos.get_session_state(conn, repos.get_open_session(conn)).route
    status = {step.concept_id: step.status for step in route.steps}
    assert [status[n] for n in ("l1_frames", "l1_select", "l1_group")] == ["closed"] * 3
    assert [status[n] for n in ("l2_hist", "l2_box", "l2_corr")] == ["ahead"] * 3
    rows = {row.topic: row.reason for row in explain.availability(conn, settings=settings)}
    assert rows[1] == "ok"


def test_close_module_without_route(tmp_path) -> None:
    conn = get_conn(str(tmp_path / "empty.sqlite3"))
    migrate(conn)
    load_lessons(conn)
    assert dev.close_module(conn, 1) == 0


def test_main_walks_users_dir(tmp_path, capsys) -> None:
    users = tmp_path / "users"
    (users / "7").mkdir(parents=True)
    _student_db(users / "7" / "mlcourse.sqlite3").close()
    dev.main(["2", "--users-dir", str(users)])
    assert "модуль 2" in capsys.readouterr().out
