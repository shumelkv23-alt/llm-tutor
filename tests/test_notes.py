"""Заметки и поиск по курсу: репозиторий, API и страница профиля."""

import pytest
from legacy_course import load_seed
from test_web_lesson import COURSE, _finish, _user_db
from web_fakes import ScriptedTutor

from llm_tutor.db import repos


@pytest.fixture
def web_llm():
    return ScriptedTutor("Объясняю тему: groupby разбивает строки на группы.")


@pytest.fixture
async def enrolled(student):
    assert (await student.post(f"/api/courses/{COURSE}/enroll", json={})).status_code == 200
    await _finish(student)
    return student


def test_note_crud_and_search(conn) -> None:
    load_seed(conn)
    note_id = repos.add_note(
        conn, concept_id="groupby", source="theory", quote="groupby делит строки", now=1.0
    )

    notes = repos.list_notes(conn)
    assert [note.id for note in notes] == [note_id]
    assert notes[0].concept_id == "groupby"
    assert repos.search_notes(conn, "GROUPBY")[0].id == note_id
    assert repos.search_notes(conn, "нет такого") == []
    assert repos.delete_note(conn, note_id) is True
    assert repos.delete_note(conn, note_id) is False
    assert repos.list_notes(conn) == []


async def test_note_api_and_profile_search(enrolled, web_app) -> None:
    response = await enrolled.post(
        f"/api/courses/{COURSE}/notes",
        json={
            "quote": "groupby делит строки на группы",
            "source": "theory",
            "node_id": "grouping",
            "section_id": "groupby",
        },
    )

    assert response.status_code == 201, response.text
    note = response.json()["note"]
    assert note["lesson"]
    assert note["source"] == "theory"
    assert note["concept_id"] == "grouping"

    notes = repos.list_notes(_user_db(web_app))
    assert [item.id for item in notes] == [note["id"]]

    profile = await enrolled.get("/profile")
    assert profile.status_code == 200
    assert "groupby делит строки на группы" in profile.text
    assert "Мои заметки" in profile.text
    assert "На неделю" in profile.text
    assert "Калибровка" in profile.text
    # Ссылка из заметки открывает занятие заметки и её раздел.
    assert f'href="/lesson/{COURSE}?node=grouping#section-groupby"' in profile.text

    search = await enrolled.get("/profile", params={"q": "groupby"})
    assert search.status_code == 200
    assert "groupby делит строки на группы" in search.text
    assert "В конспектах" in search.text
    # Найденный конспект открывается на своём занятии, а не на текущем.
    assert f'href="/lesson/{COURSE}?node=grouping"' in search.text

    lesson = await enrolled.get(f"/lesson/{COURSE}", params={"node": "grouping"})
    assert lesson.status_code == 200
    assert 'data-target-node="grouping"' in lesson.text

    deleted = await enrolled.delete(f"/api/courses/{COURSE}/notes/{note['id']}")
    assert deleted.status_code == 200
    after = await enrolled.get("/profile")
    assert "groupby делит строки на группы" not in after.text


async def test_note_api_rejects_empty_quote(enrolled) -> None:
    response = await enrolled.post(
        f"/api/courses/{COURSE}/notes",
        json={"quote": "   ", "source": "theory", "node_id": "groupby"},
    )

    assert response.status_code == 422


def test_calibration_flags_overconfidence(conn, settings) -> None:
    from llm_tutor.schemas import Event
    from llm_tutor.web import views

    load_seed(conn)
    topic = repos.get_topics(conn)[0]
    block = topic.survey.blocks[0]
    repos.set_fact(conn, block.key, "Уверенно")
    repos.add_event(
        conn,
        Event(source="checked", result=0.0, concept_id=block.concepts[0], ts=1.0),
    )

    rows = views.calibration_view(conn, settings=settings)

    row = next(item for item in rows if item["title"] == block.title)
    assert row["self"] == "Уверенно"
    assert row["actual"] is not None and row["actual"] < 55
    assert row["note"] == "стоит проверить"


def test_weekly_plan_has_steps_and_estimate(conn, settings) -> None:
    from llm_tutor.web import views

    load_seed(conn)

    plan = views.weekly_plan(conn, settings=settings)

    assert plan["total_minutes"] == sum(step["minutes"] for step in plan["steps"])
    assert all(step["minutes"] == 25 for step in plan["steps"])


async def test_note_comment_and_search_by_it(enrolled) -> None:
    created = await enrolled.post(
        f"/api/courses/{COURSE}/notes",
        json={
            "quote": "groupby делит строки на группы",
            "source": "theory",
            "node_id": "groupby",
            "section_id": "groupby",
            "comment": "важно для EDA",
        },
    )
    assert created.status_code == 201
    note = created.json()["note"]
    assert note["comment"] == "важно для EDA"
    assert note["section_id"] == "groupby"

    search = await enrolled.get("/profile", params={"q": "EDA"})
    assert "важно для EDA" in search.text

    edited = await enrolled.put(
        f"/api/courses/{COURSE}/notes/{note['id']}",
        json={"comment": "перечитано перед практикой"},
    )
    assert edited.status_code == 200
    assert edited.json()["note"]["comment"] == "перечитано перед практикой"
    profile = await enrolled.get("/profile")
    assert "перечитано перед практикой" in profile.text


async def test_note_comment_repo_and_404(enrolled) -> None:
    created = await enrolled.post(
        f"/api/courses/{COURSE}/notes",
        json={"quote": "заметка", "source": "theory", "node_id": "groupby"},
    )
    note_id = created.json()["note"]["id"]
    missing = await enrolled.put(
        f"/api/courses/{COURSE}/notes/999999", json={"comment": "нет"}
    )
    assert missing.status_code == 404
