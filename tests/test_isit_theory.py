"""Конспекты второго курса «Машинное обучение для ИСИТ».

Те же правила, что у mlcourse (`tests/test_theory_content.py`), но курс
читается из своего каталога: конспекты не публикуются вместе с модулем, и
проверять их нужно по мере написания — модуль 1 собирается срезами 2–4.
Отличие одно: в «Источнике» у этого курса ссылка на его лаборатории, а не на
mlcourse.ai.
"""

import re
import warnings

import pytest

from llm_tutor.web.courses import course_data, get_course

COURSE = course_data(get_course("isit-ml"))
NODES = {node.id: node for node in COURSE.nodes}
PUBLISHED = {topic.number for topic in COURSE.topics if topic.published}
WITH_THEORY = sorted(COURSE.theory)

MIN_WORDS, MAX_WORDS = 900, 2200
SECTION_PARTS = ("### Главное", "### Пример", "### Частые ошибки")
SOURCE = "github.com/Eldervald/isit_ml_labs"
_BLOCK_RE = re.compile(r"```python\n(.*?)```", re.DOTALL)
_FENCE_RE = re.compile(r"^```(\S*)", re.MULTILINE)

pd = pytest.importorskip("pandas")
plt = pytest.importorskip("matplotlib.pyplot")


def test_theory_exists() -> None:
    """Без конспектов этот файл молча прошёл бы на пустом курсе."""
    assert WITH_THEORY, "у курса ИСИТ нет ни одного конспекта"


def test_every_published_lesson_has_theory() -> None:
    missing = [
        node.id for node in COURSE.nodes if node.topic_id in PUBLISHED and node.id not in COURSE.theory
    ]
    assert missing == []


def test_no_stray_theory_files() -> None:
    from llm_tutor.course.seed import course_paths, theory_dir_for

    for path in course_paths(get_course("isit-ml").data_dir):
        folder = theory_dir_for(path)
        files = {p.stem for p in folder.glob("*.md")} if folder.is_dir() else set()
        assert files <= set(NODES), (path.name, sorted(files - set(NODES)))


@pytest.mark.parametrize("node_id", WITH_THEORY)
def test_theory_structure(node_id: str) -> None:
    from llm_tutor.course.seed import section_text, theory_headings

    text = COURSE.theory[node_id]
    node = NODES[node_id]
    headings = theory_headings(text)
    titles = [section.title for section in node.sections]

    assert text.startswith("# "), node_id
    assert headings[0] == "Зачем", (node_id, headings)
    assert headings[1:-2] == titles, (node_id, headings)
    assert headings[-2:] == ["Итоги занятия", "Источник"], (node_id, headings)
    for title in titles:
        part = section_text(text, title)
        positions = [part.find(marker) for marker in SECTION_PARTS]
        assert all(p >= 0 for p in positions), (node_id, title, positions)
        assert positions == sorted(positions), (node_id, title)
    words = len(re.findall(r"\w+", text))
    assert MIN_WORDS <= words <= MAX_WORDS, (node_id, words)
    assert SOURCE in section_text(text, "Источник"), node_id


@pytest.mark.parametrize("node_id", WITH_THEORY)
def test_theory_examples_run(node_id: str) -> None:
    blocks = _BLOCK_RE.findall(COURSE.theory[node_id])
    assert blocks, f"{node_id}: нет ни одного примера на Python"
    for index, block in enumerate(blocks, start=1):
        namespace: dict = {"__name__": "__theory__"}
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                exec(compile(block, f"{node_id}.md#{index}", "exec"), namespace)  # noqa: S102
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"{node_id}, пример {index}: {type(exc).__name__}: {exc}")
        finally:
            plt.close("all")


@pytest.mark.parametrize("node_id", WITH_THEORY)
def test_every_section_has_an_example(node_id: str) -> None:
    from llm_tutor.course.seed import section_text

    text = COURSE.theory[node_id]
    for section in NODES[node_id].sections:
        assert "```python" in section_text(text, section.title), (node_id, section.id)


@pytest.mark.parametrize("node_id", WITH_THEORY)
def test_python_blocks_use_python_label(node_id: str) -> None:
    labels = _FENCE_RE.findall(COURSE.theory[node_id])
    opening = labels[0::2]  # метки открывающих строк (закрывающие — пустые)
    # Пример без метки не исполнится `test_theory_examples_run` и не отрисуется
    # как код, но раньше оставался незамеченным: пустая метка считалась нормой.
    assert set(opening) <= {"python"}, (node_id, opening)
