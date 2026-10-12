"""Бюджет контекста для разделов занятий (срез 57, план 57.4).

В промпт идёт текст текущего раздела конспекта. Он не должен обрезаться
(``THEORY_PROMPT_CHARS``), а системный промпт с ним — вытеснять диалог из
бюджета контекста. Проверяется самый длинный раздел каждого опубликованного
модуля.
"""

import pytest

from llm_tutor.core.context import CHARS_PER_TOKEN, build_context
from llm_tutor.course.graph import CourseGraph
from llm_tutor.course.seed import load_course, load_course_data, section_text
from llm_tutor.db import repos
from llm_tutor.llm.prompts import THEORY_PROMPT_CHARS
from llm_tutor.schemas import SessionState
from llm_tutor.student import survey

COURSE = load_course_data()
PUBLISHED = {topic.number for topic in COURSE.topics if topic.published}
SECTIONS = [
    (node.id, section.id, section.title)
    for node in COURSE.nodes
    if node.topic_id in PUBLISHED and node.id in COURSE.theory
    for section in node.sections
]


@pytest.mark.parametrize(("node_id", "section_id", "title"), SECTIONS)
def test_section_fits_theory_limit(node_id: str, section_id: str, title: str) -> None:
    text = section_text(COURSE.theory[node_id], title)
    assert text is not None, (node_id, section_id)
    assert len(text) <= THEORY_PROMPT_CHARS, (node_id, section_id, len(text))


def _longest() -> tuple[str, str, str]:
    return max(SECTIONS, key=lambda s: len(section_text(COURSE.theory[s[0]], s[2]) or ""))


def test_longest_section_goes_into_prompt_whole_and_fits_budget(conn, settings) -> None:
    load_course(conn)
    for topic in repos.get_topics(conn):
        config = topic.survey
        survey.apply_answers(conn, config, {block.key: 0 for block in config.blocks}, now=1.0)
    node_id, section_id, title = _longest()
    session_id = repos.ensure_open_session(conn, now=1.0)
    for index in range(settings.context_dialog_tail):
        repos.add_message(conn, session_id, "user" if index % 2 == 0 else "assistant", "Реплика " * 40)
    state = SessionState(current_node_id=node_id, section_id=section_id)

    package = build_context(
        conn,
        session_id,
        "Объясни этот раздел ещё раз",
        state=state,
        graph=CourseGraph.load(conn),
        top_k=settings.context_rag_top_k,
        dialog_tail=settings.context_dialog_tail,
        budget_tokens=settings.context_budget_tokens,
        now=2.0,
        settings=settings,
    )

    system = package.messages[0].content
    text = section_text(COURSE.theory[node_id], title)
    assert text in system  # раздел целиком, без обрезки
    assert f"Конспект текущего раздела «{title}»" in system
    size = sum(len(message.content) for message in package.messages)
    assert size <= settings.context_budget_tokens * CHARS_PER_TOKEN
    # Диалог не вытеснен разделом: в пакете остались реплики хвоста.
    assert len(package.messages) > 2
