"""Второй курс «Машинное обучение для ИСИТ» — каркас (срез 1) и его наполнение.

Курс живёт рядом с mlcourse: свой каталог seed-файлов (`data/isit`), своя БД у
ученика, своя запись в реестре. Модуль 1 наполнялся занятие за занятием
(срезы 2–4) и с среза 4 опубликован целиком: у каждого занятия есть конспект,
задания разделов и итоговое.

Модуль 2 (`topic02`, NumPy и pandas) заведён срезом 5 и дописан занятие за
занятием: с конца среза он тоже опубликован. Модуль 3 (`topic03`, линейная
регрессия) прошёл тот же путь срезом 6. Модуль 4 (`topic04`, логистическая
регрессия) пишется срезом 7: занятия и разделы в seed уже объявлены, а
конспекты и задания приходят занятие за занятием — до публикации модуль
числится черновиком (`published: false`), и пустые занятия в нём допустимы.
Модуль-черновик держит занятия пустыми — но укомплектованное занятие обязано
быть укомплектовано целиком. Дальше по плану так же заводятся модули 5–9.

Курс скрыт из каталога вместе с mlcourse: его место заняли новые курсы. Файлы,
прогресс и прямые адреса занятий остаются рабочими; тесты содержимого включают
видимость точечно (фикстура ``visible_builtins``).
"""

import json
from dataclasses import replace

import pytest

from llm_tutor.course.graph import CourseGraph
from llm_tutor.course.seed import main_concept
from llm_tutor.web import courses as registry
from llm_tutor.web.courses import (
    COURSES,
    Course,
    course_data,
    get_course,
    visible_course,
    visible_courses,
)

ISIT = "isit-ml"
MLCOURSE = "mlcourse"
LESSONS = 9
SECTIONS = 23
# Модуль 2 (NumPy и pandas) дописан и опубликован в срезе 5.
MODULE2_LESSONS = 4
MODULE2_SECTIONS = 10
# Модуль 3 (линейная регрессия) дописан и опубликован в срезе 6.
MODULE3_LESSONS = 3
MODULE3_SECTIONS = 8
# Модули 1–3 — всё, что ученик видит сегодня: карточка курса считает занятия
# только опубликованных модулей (`views._topic_count`).
PUBLISHED_LESSONS = LESSONS + MODULE2_LESSONS + MODULE3_LESSONS
PUBLISHED_SECTIONS = SECTIONS + MODULE2_SECTIONS + MODULE3_SECTIONS
# Модуль 4 (логистическая регрессия) пишется срезом 7: занятия объявлены в
# seed, содержание приходит по мере готовности, поэтому в графе он уже есть.
MODULE4_LESSONS = 3
MODULE4_SECTIONS = 8
ALL_LESSONS = PUBLISHED_LESSONS + MODULE4_LESSONS
ALL_SECTIONS = PUBLISHED_SECTIONS + MODULE4_SECTIONS


def _isit() -> Course:
    course = get_course(ISIT)
    assert course is not None, "курс isit-ml не зарегистрирован"
    return course


def _hide(monkeypatch, course: Course) -> Course:
    """Прячет курс в реестре: ``visible=False`` — «черновик, который ещё пишется»."""
    draft = replace(course, title="Черновик курса ИСИТ", visible=False)
    monkeypatch.setattr(
        registry,
        "COURSES",
        tuple(draft if item.id == course.id else item for item in registry.COURSES),
    )
    monkeypatch.setitem(registry._BY_ID, draft.id, draft)
    return draft


@pytest.fixture
def visible_builtins(monkeypatch):
    """Показывает прежние курсы ученику для проверки их наполнения.

    Оба курса скрыты из каталога с появлением новых; тесты этого файла — про
    содержимое ИСИТ и его веб-страницы, поэтому включают видимость точечно.
    """
    shown = tuple(replace(course, visible=True) for course in registry.COURSES)
    monkeypatch.setattr(registry, "COURSES", shown)
    monkeypatch.setattr(registry, "_BY_ID", {course.id: course for course in shown})


def _modules(course: Course) -> list[tuple[int, bool, set[str]]]:
    """Модули курса по файлам seed: (номер, опубликован ли, id его занятий).

    В ``course_data`` границы модулей не сохраняются — узлы идут подряд, а
    тестам нужно знать, к какому модулю относится занятие: пустые занятия
    допустимы только в модуле-черновике.
    """
    modules = []
    for path in sorted(course.data_dir.glob("seed_topic*.json")):
        seed = json.loads(path.read_text(encoding="utf-8"))
        modules.append(
            (
                seed["topic"]["number"],
                seed["topic"].get("published", False),
                {node["id"] for node in seed["nodes"]},
            )
        )
    return modules


def test_course_is_registered() -> None:
    course = _isit()

    assert course.title == "Python: от основ до машинного обучения"
    assert course.data_dir is not None
    assert course.data_dir.name == "isit"
    # Модули берутся из своего каталога, а не из общего glob по data/.
    assert course.seed_paths is None


def test_registry_keeps_both_courses() -> None:
    assert {course.id for course in COURSES} == {MLCOURSE, ISIT}


def test_both_courses_are_hidden_from_catalog() -> None:
    """Прежние курсы скрыты из списков, но остаются в реестре."""
    assert {course.id for course in COURSES} == {MLCOURSE, ISIT}
    assert visible_courses() == ()


def test_hidden_course_is_not_offered_to_students(monkeypatch) -> None:
    """``visible=False`` — запас на будущее: черновик открыт по адресу, но не в списках."""
    draft = _hide(monkeypatch, _isit())

    assert draft.visible is False
    assert draft.id not in {course.id for course in visible_courses()}
    assert visible_course(draft.id) is None
    # Прямой адрес по-прежнему ведёт в черновик: его находит ``get_course``.
    assert get_course(draft.id) is draft


def test_three_modules_are_published_and_the_fourth_is_a_draft() -> None:
    data = course_data(_isit())

    assert [topic.number for topic in data.topics] == [1, 2, 3, 4]
    assert [topic.published for topic in data.topics] == [True, True, True, False]
    assert len(data.nodes) == ALL_LESSONS
    assert sum(len(node.sections) for node in data.nodes) == ALL_SECTIONS


def test_content_arrives_lesson_by_lesson() -> None:
    """Занятие либо укомплектовано целиком, либо ещё пустое (срезы 2–4, 5–12).

    Полуготовых занятий быть не должно: ученик открывает конспект, отвечает на
    вопросы раздела и закрывает занятие итоговым заданием. Пустое занятие
    допустимо только в модуле-черновике: опубликованный модуль ученику уже
    показывают, и в нём дыр быть не может. У модулей 1–3 `published: true`,
    поэтому пустые занятия там запрещены, а у пишущегося модуля 4 — разрешены.
    """
    course = _isit()
    data = course_data(course)
    items_by_node: dict[str, list] = {node.id: [] for node in data.nodes}
    for item in data.items:
        concept = main_concept(item)
        assert concept in items_by_node, f"задание {item.id} метит в чужой концепт {concept!r}"
        items_by_node[concept].append(item)

    ready, empty = [], []
    for node in data.nodes:
        items = items_by_node[node.id]
        if not items:
            empty.append(node.id)
            continue
        ready.append(node.id)
        for section in node.sections:
            own = [item for item in items if item.section == section.id]
            assert len(own) >= 2, f"{node.id}/{section.id}: в разделе меньше двух заданий"
        assert [item for item in items if item.final], f"{node.id}: нет итогового задания"
        assert node.id in data.theory, f"{node.id}: есть задания, но нет конспекта"

    assert len(ready) + len(empty) == ALL_LESSONS
    drafts = {node_id for _, published, node_ids in _modules(course) if not published for node_id in node_ids}
    assert not set(empty) - drafts, f"пустые занятия в опубликованном модуле: {sorted(set(empty) - drafts)}"


# Эталон choice-задания — индекс верного варианта. Поведением такую опечатку не
# поймать: тесты не знают, какой вариант правильный, а ученик получил бы
# «неверно» за верную кнопку. Поэтому индекс пришпилен к фрагменту текста
# варианта: и подмена индекса, и правка самого варианта красят тест.
CHOICE_ANSWERS = {
    1002: "ничего не возвращает",
    1004: "известных входах",
    1012: "and b <= c",
    1014: "сверху вниз",
    1022: "флагом `-O`",
    1024: "уйдёт наверх",
    1032: "start=1",
    1034: "ValueError",
    1036: "is not in list",
    1042: "без региона пропущены",
    1044: "идти надо по записи",
    1052: "стек вызовов ограничен",
    1054: "стек живёт в куче",
    1062: "генератор исчерпан",
    1064: "останавливается на самой короткой строке",
    1072: "вызывается из него последним",
    1074: "ни в сравнении порядка, ни в равенстве",
    1076: "`Vector[str]` для mypy — ошибка",
    1078: "новый итератор, а итератор — сам себя",
    1082: "три `None`",
    1084: "вывод снова шёл в настоящую консоль",
    1086: "переносит на обёртку имя, докстринг",
    1088: "самой свежей",
    2002: "пары (0, 1) и (2, 3)",
    2004: "1 + 3 и 2 + 4",
    2006: "out[::2] = x",
    2012: "сравнение с `nan` пропусков не находит",
    2014: "пропуск портит сумму",
    2022: "у столбца нет одного значения истинности",
    2024: "по убыванию частоты",
    2028: "`382652`",
    2032: "строчными буквами",
    2034: "выбросы",
    2038: "по сумме рецензий всех фильмов жанра",
    3002: "среднее модулей ошибок",
    3004: "MSE = 16, MAE = 0.4",
    3006: "сильнее, чем прогноз средним",
    3008: "TSS = 0",
    3012: "Свободный член",
    3014: "Singular matrix",
    3016: "(50, 50)",
    3018: "через SVD",
    3022: "Норма весов падает",
    3025: "нет производной в нуле",
    3028: "он обнуляет лишние коэффициенты",
    4003: "умножаются на e^0.7 ≈ 2.01",
    4006: "MSE с сигмоидой невыпукла",
    4009: "пропорционален размеру выборки",
    4012: "σ′ не больше 0.25",
    4015: "включает границу",
}


def test_choice_answers_point_to_expected_option() -> None:
    data = course_data(_isit())
    choice = {item.id: item for item in data.items if item.answer_type == "choice"}

    assert set(choice) == set(CHOICE_ANSWERS), "choice-задание без эталона в наборе"
    for item_id, expected in CHOICE_ANSWERS.items():
        option = choice[item_id].options[int(choice[item_id].answer)]
        assert expected in option, (item_id, option)


def test_lessons_have_two_to_four_sections() -> None:
    data = course_data(_isit())

    assert all(2 <= len(node.sections) <= 4 for node in data.nodes)
    for node in data.nodes:
        ids = [section.id for section in node.sections]
        titles = [section.title for section in node.sections]
        assert len(set(ids)) == len(ids), node.id
        assert len(set(titles)) == len(titles), node.id


def test_survey_covers_every_lesson_of_its_module_once() -> None:
    """Анкета модуля спрашивает про все его занятия и только про них."""
    course = _isit()
    data = course_data(course)
    surveys = {topic.number: topic.survey for topic in data.topics}

    for number, _, node_ids in _modules(course):
        config = surveys[number]
        covered = [concept for block in config.blocks for concept in block.concepts]
        assert sorted(covered) == sorted(node_ids), f"модуль {number}"
        assert len(config.level_options) == 3, f"модуль {number}"


def test_edges_go_forward_within_the_module() -> None:
    data = course_data(_isit())
    order = {node.id: index for index, node in enumerate(data.nodes)}

    assert data.edges, "у каркаса нет рёбер — маршрут не выстроится"
    for edge in data.edges:
        assert order[edge.from_id] < order[edge.to_id], edge
    graph = CourseGraph(data.nodes, data.edges)
    assert set(graph.node_ids) == set(order)


def test_mlcourse_modules_do_not_leak_into_the_new_course() -> None:
    mlcourse = course_data(get_course(MLCOURSE))
    isit = course_data(_isit())

    assert {topic.number for topic in mlcourse.topics} == {1, 2, 3, 4, 5}
    assert {topic.number for topic in isit.topics} == {1, 2, 3, 4}
    assert not {node.id for node in mlcourse.nodes} & {node.id for node in isit.nodes}


def test_course_without_seed_files_fails_with_the_folder_name(tmp_path) -> None:
    draft = Course(
        id="draft",
        title="Черновик",
        subtitle="",
        description="",
        source_url="",
        data_dir=tmp_path / "isit",
    )

    with pytest.raises(FileNotFoundError, match="isit"):
        course_data(draft)


# --- Веб ---


async def test_the_new_course_is_in_the_catalog(visible_builtins, student) -> None:
    """Курс ИСИТ виден ученику; карточка считает занятия открытых модулей."""
    cards = {card["id"]: card for card in (await student.get("/api/courses")).json()["courses"]}

    assert set(cards) == {MLCOURSE, ISIT}
    assert cards[ISIT]["enrolled"] is False
    assert cards[ISIT]["topics"] == PUBLISHED_LESSONS


async def test_the_new_course_offers_its_lessons(visible_builtins, student) -> None:
    """Опубликованные модули открыты: карточка считает занятия, страница открывает урок."""
    catalog = (await student.get("/courses")).text
    assert "Python: от основ до машинного обучения" in catalog
    assert f"{PUBLISHED_LESSONS} занятий" in catalog
    assert "Занятия готовятся — модуль выйдет скоро" not in catalog

    assert (await student.post(f"/api/courses/{ISIT}/enroll", json={})).status_code == 200
    page = (await student.get(f"/lesson/{ISIT}")).text
    assert "Загружаем занятие…" in page
    assert "Занятия этого курса готовятся" not in page


async def test_enrolling_in_the_new_course_writes_its_own_db(student, tmp_path) -> None:
    response = await student.post(f"/api/courses/{ISIT}/enroll", json={})

    assert response.status_code == 200
    card = response.json()["course"]
    assert card["id"] == ISIT
    assert card["enrolled"] is True
    # 16 занятий трёх опубликованных модулей впереди, ни одно не закрыто:
    # курс записан, но не пройден.
    assert card["topics"] == PUBLISHED_LESSONS
    assert card["status"] != "done"
    assert [path.name for path in (tmp_path / "users").rglob("*.sqlite3")] == [f"{ISIT}.sqlite3"]


async def test_empty_course_does_not_displace_the_real_one(
    monkeypatch, visible_builtins, student
) -> None:
    """Записи на недописанный курс не вытесняют mlcourse с главной, профиля и «Занятия» (аудит 1)."""
    draft = _hide(monkeypatch, _isit())
    await student.post(f"/api/courses/{MLCOURSE}/enroll", json={})
    await student.post(f"/api/courses/{draft.id}/enroll", json={})

    home = (await student.get("/")).text
    assert "Машинное обучение: mlcourse.ai" in home
    assert draft.title not in home
    assert (await student.get("/lesson")).headers["location"] == f"/lesson/{MLCOURSE}"
    assert (await student.get("/profile")).text.count(draft.title) == 0
    cards = (await student.get("/api/courses")).json()["courses"]
    assert {card["id"] for card in cards} == {MLCOURSE}


async def test_unreadable_course_modules_do_not_break_the_pages(
    monkeypatch, student, tmp_path
) -> None:
    """Каталог модулей курса пропал: страницы отвечают, а запись — понятной ошибкой.

    Раньше ``course_data`` бросал из ``views._topic_count`` мимо ``try`` карточки,
    и ученик получал 500 на главной, в профиле и на странице занятия (аудит 1).
    """
    from llm_tutor.web import api_lesson

    broken = replace(
        registry.COURSES[0],
        id="broken",
        seed_paths=None,
        data_dir=tmp_path / "нет-такого",
        visible=True,
    )
    monkeypatch.setattr(registry, "COURSES", (broken,))
    monkeypatch.setitem(registry._BY_ID, broken.id, broken)

    assert (await student.get("/")).status_code == 200
    assert (await student.get("/lesson/broken")).status_code == 200
    cards = (await student.get("/api/courses")).json()["courses"]
    assert cards[0]["status"] == "error"
    enroll = await student.post("/api/courses/broken/enroll", json={})
    assert enroll.status_code == 503
    assert enroll.json()["detail"]["message"] == api_lesson.COURSE_DB_BROKEN
