"""Содержание конспектов занятий (срез 45, структура занятий — срез 57).

Пример, который не запускается, хуже его отсутствия: ученик скопирует его в
редактор и получит ошибку. Поэтому каждый блок ```python выполняется сам по
себе, в чистом пространстве имён (у блока есть «Скопировать» и «Открыть в
редакторе»), а предупреждения pandas считаются ошибками — устаревший API
не должен попадать в конспект.

Структура конспекта занятия: ``# Название``, ``## Зачем``, по разделу
``## <название раздела>`` с ``### Главное``, ``### Пример``, ``### Частые
ошибки`` — в порядке seed, затем ``## Итоги занятия`` и ``## Источник``.
Проверяются конспекты всех модулей, у которых они есть.
"""

import re
import warnings

import pytest

from llm_tutor.course.seed import load_course_data, section_text, theory_headings

pd = pytest.importorskip("pandas")
matplotlib = pytest.importorskip("matplotlib")
matplotlib.use("Agg")
plt = pytest.importorskip("matplotlib.pyplot")

COURSE = load_course_data()
NODES = {node.id: node for node in COURSE.nodes}
PUBLISHED = {topic.number for topic in COURSE.topics if topic.published}
WITH_THEORY = sorted(COURSE.theory)
MIN_WORDS, MAX_WORDS = 900, 2200
SECTION_PARTS = ("### Главное", "### Пример", "### Частые ошибки")
_BLOCK_RE = re.compile(r"```python\n(.*?)```", re.DOTALL)
# Любая метка блока кода: Python — только под меткой python, иначе блок не
# исполнится тестом, хотя сайт подсветит его как Python.
_FENCE_RE = re.compile(r"^```(\S*)", re.MULTILINE)
# seaborn и plotly в тестовом окружении могут отсутствовать — такие примеры
# пропускаются, если пакета нет (в Pyodide их нет, «Открыть в редакторе» скрыт).
_OPTIONAL = {"seaborn": "seaborn", "plotly": "plotly"}


def test_every_published_lesson_has_theory() -> None:
    missing = [n.id for n in COURSE.nodes if n.topic_id in PUBLISHED and n.id not in COURSE.theory]
    assert missing == []


def test_no_stray_theory_files() -> None:
    from llm_tutor.course.seed import course_paths, theory_dir_for

    for path in course_paths():
        files = {p.stem for p in theory_dir_for(path).glob("*.md")} if theory_dir_for(path).is_dir() else set()
        assert files <= set(NODES), (path.name, sorted(files - set(NODES)))


@pytest.mark.parametrize("node_id", WITH_THEORY)
def test_theory_structure(node_id: str) -> None:
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
    assert "mlcourse.ai" in section_text(text, "Источник")


def _needs_missing_package(block: str) -> str | None:
    for module, name in _OPTIONAL.items():
        if re.search(rf"^\s*(import|from)\s+{module}\b", block, re.MULTILINE):
            try:
                __import__(module)
            except ImportError:
                return name
    return None


@pytest.mark.parametrize("node_id", WITH_THEORY)
def test_theory_examples_run(node_id: str) -> None:
    blocks = _BLOCK_RE.findall(COURSE.theory[node_id])
    assert blocks, f"{node_id}: нет ни одного примера на Python"
    for index, block in enumerate(blocks, start=1):
        if _needs_missing_package(block):
            continue
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
