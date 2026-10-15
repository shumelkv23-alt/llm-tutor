"""Курс проходится целиком — даже пока в нём написаны не все модули.

Курс пишется модуль за модулем: сегодня ученику открыты модули 1–3, модуль 4
объявлен черновиком (`published: false`), модулей 5–9 ещё нет. Тест ведёт
ученика тем же путём, что и браузер (анкета модуля → разделы занятия →
итоговое), и требует, чтобы он прошёл **всё опубликованное** и упёрся в
честный конец: «курс пройден», а не в пустую страницу, тупик или пятисотку.

Вместо Pyodide вердикт по код-заданию приходит как ``passed`` — проверка
самого прогона кода живёт в браузерных сценариях (`tests/e2e`), здесь важен
ход занятия.
"""

import json
import shutil
from pathlib import Path

import pytest

from llm_tutor.course.seed import Course as CourseData
from llm_tutor.student import survey
from llm_tutor.web import courses
from llm_tutor.web.courses import course_data, get_course

ISIT = "isit-ml"
BASE = f"/api/courses/{ISIT}/lesson"

# Опубликованное сегодня: модули 1–3. Растёт вместе с курсом — тест обязан
# замечать, что новый модуль не доходится, а не молча пропускать его.
PUBLISHED_LESSONS = 16


def _course() -> CourseData:
    course = get_course(ISIT)
    assert course is not None, "курс isit-ml не зарегистрирован"
    return course_data(course)


def _reference_choice_answers() -> dict[int, int]:
    """id choice-задания → индекс верного варианта (эталон из seed-файлов)."""
    course = get_course(ISIT)
    assert course is not None and course.data_dir is not None
    answers: dict[int, int] = {}
    for path in sorted(course.data_dir.glob("seed_topic*.json")):
        seed = json.loads(path.read_text(encoding="utf-8"))
        for item in seed.get("items", []):
            if item.get("answer_type") == "choice":
                answers[item["id"]] = int(item["answer"])
    assert answers, "в банке заданий нет ни одного choice-задания"
    return answers


def _survey_answers(topic) -> list[int]:
    """Полная анкета модуля: на каждый вопрос — первый вариант («с нуля»)."""
    progress = survey.Progress(topic.survey)
    given: list[int] = []
    while (key := progress.next_key()) is not None:
        index = 0
        assert index < len(survey.options_for(topic.survey, key))
        progress = progress.answer(index)
        given.append(index)
    return given


async def _finish_survey(client, topic_by_number: dict, view: dict) -> None:
    """Проходит анкету модуля, которую просит страница занятия."""
    number = view["id"] if "id" in view else view["number"]
    topic = topic_by_number[number]
    response = await client.post(
        f"{BASE}/survey/finish", json={"answers": _survey_answers(topic)}
    )
    assert response.status_code == 200, response.text


async def _answer(client, item: dict, answers: dict[int, int]) -> None:
    """Отвечает на задание так, как это сделал бы ученик: верно."""
    if item["runnable"]:
        response = await client.post(
            f"{BASE}/code-result",
            json={
                "item_id": item["id"],
                "code": "result = 42",
                "passed": True,
                "report": "",
            },
        )
    else:
        assert item["type"] == "choice", f"задание {item['id']} без типа {item['type']}"
        response = await client.post(
            f"{BASE}/answer",
            json={"item_id": item["id"], "answer": answers[item["id"]]},
        )
    assert response.status_code == 200, response.text
    verdict = response.json()["verdict"]
    assert verdict["correct"] is True, f"верный ответ не зачтён: {verdict}"


async def _walk(client) -> dict:
    """Ведёт ученика до конца опубликованного курса; отдаёт последнее состояние."""
    topics = {topic.number: topic for topic in _course().topics}
    answers = _reference_choice_answers()
    visited: list[str] = []
    for step in range(800):
        state = (await client.get(f"{BASE}/state")).json()["state"]
        if state["survey"]:
            await _finish_survey(client, topics, state["survey_topic"])
            continue
        node = state["node"]
        if node is None:
            return state
        if not visited or visited[-1] != node["id"]:
            visited.append(node["id"])
        if state["item"] is not None:
            await _answer(client, state["item"], answers)
            continue
        button = state["lesson_button"]
        assert button is not None, (
            f"тупик на шаге {step}: занятие «{node['name']}» без задания и без кнопки урока"
        )
        response = await client.post(
            f"{BASE}/next",
            json={"node_id": button["node_id"], "part": button["part"]},
        )
        assert response.status_code == 200, response.text
    raise AssertionError(f"курс не кончился за 800 шагов; пройдено занятий: {len(visited)}")


@pytest.fixture
async def enrolled(student):
    response = await student.post(f"/api/courses/{ISIT}/enroll", json={})
    assert response.status_code == 200, response.text
    return student


@pytest.fixture
def open_module4(monkeypatch, tmp_path: Path) -> None:
    """Курс ИСИТ, у которого модуль 4 открыт как есть — с недописанным занятием.

    Копия каталога курса во временном каталоге: править ``data/`` тест не
    вправе. Подменяется реестр курсов — его читают по имени модуля, а
    ``course_data`` кэширует по объекту курса.
    """
    from dataclasses import replace

    source = courses.COURSES[1].data_dir
    assert source is not None
    target = tmp_path / "isit"
    shutil.copytree(source, target)
    path = target / "seed_topic04.json"
    seed = json.loads(path.read_text(encoding="utf-8"))
    seed["topic"]["published"] = True
    path.write_text(json.dumps(seed, ensure_ascii=False, indent=2), encoding="utf-8")

    course = replace(courses.COURSES[1], data_dir=target)
    monkeypatch.setattr(courses, "COURSES", (*courses.COURSES[:1], course))
    monkeypatch.setitem(courses._BY_ID, course.id, course)


@pytest.fixture
async def module4_student(student, open_module4):
    """Ученик, записанный на курс **после** открытия модуля 4.

    БД ученика собирается из курса при записи: подмена реестра обязана
    случиться раньше, иначе модуля 4 в ней просто не будет.
    """
    response = await student.post(f"/api/courses/{ISIT}/enroll", json={})
    assert response.status_code == 200, response.text
    return student


async def test_course_is_walked_to_the_end(enrolled) -> None:
    """Ученик проходит все опубликованные занятия и видит конец курса."""
    final = await _walk(enrolled)

    assert final["node"] is None, "курс не закончился: занятие всё ещё открыто"
    assert final["lesson_button"] is None, "в конце курса осталась кнопка урока"

    closed = [row for row in final["topics"] if row["status"] == "closed"]
    assert len(closed) == PUBLISHED_LESSONS, (
        f"закрыто занятий {len(closed)}, а опубликовано {PUBLISHED_LESSONS}: "
        f"открытыми остались {[row['id'] for row in final['topics'] if row['status'] != 'closed']}"
    )


async def test_card_closes_the_course_after_the_last_lesson(enrolled) -> None:
    """Карточка курса после последнего занятия — «Пройден», а не «0 из 0»."""
    await _walk(enrolled)

    card = next(
        card for card in (await enrolled.get("/api/courses")).json()["courses"]
        if card["id"] == ISIT
    )

    assert card["topics"] == PUBLISHED_LESSONS
    assert card["closed"] == PUBLISHED_LESSONS
    assert card["status"] == "done"


async def test_unwritten_modules_are_shown_as_soon_not_as_gaps(enrolled) -> None:
    """Недописанные модули — «скоро», и они не попадают в счёт занятий.

    Модуль 4 объявлен в seed черновиком: в маршруте он есть («скоро»), а его
    занятия в граф ученика не входят и счёт курса не раздувают.
    """
    await _walk(enrolled)

    card = next(
        card for card in (await enrolled.get("/api/courses")).json()["courses"]
        if card["id"] == ISIT
    )
    modules = {module["number"]: module for module in card["modules"]}

    assert [module["published"] for module in modules.values()] == [True, True, True, False]
    assert sum(module["total"] for module in modules.values()) == PUBLISHED_LESSONS
    assert modules[4]["total"] == 0, "черновик не должен приносить занятия в курс"


async def test_pages_survive_the_end_of_the_course(enrolled) -> None:
    """Страницы после последнего урока отдаются, а не падают.

    Конец курса — не ошибка: занятие, главная и профиль показывают итог.
    """
    await _walk(enrolled)

    for path in (f"/lesson/{ISIT}", "/", "/profile"):
        page = await enrolled.get(path)
        assert page.status_code == 200, f"{path}: {page.status_code}"


async def test_module_is_walked_before_its_items_are_written(module4_student) -> None:
    """Модуль можно открыть недописанным: занятие без заданий не запирает ученика.

    В модуле 4 недописано занятие «Граница решения»: конспект есть, а в банке
    по нему нет ни одного задания и нет итогового. Раньше такой модуль нельзя
    было даже объявить опубликованным (валидатор отказывал в загрузке курса),
    а занятие без заданий оставляло ученика на «По этому узлу новых заданий
    сейчас нет» — с концом курса вместо продолжения.

    Модуль 4 открыт в копии курса (``open_module4``) — тем же способом его
    откроет автор, когда дописаны конспекты.
    """
    final = await _walk(module4_student)

    assert final["node"] is None, "курс не закончился из-за недописанного занятия"
    assert final["lesson_button"] is None, "в конце курса осталась кнопка урока"

    opened = [row["id"] for row in final["topics"] if row["status"] != "closed"]
    assert opened == [], f"не закрылись занятия: {opened}"
    assert "boundary" in {row["id"] for row in final["topics"]}, (
        "недописанное занятие модуля 4 не попало в маршрут — тест ничего не проверил"
    )
