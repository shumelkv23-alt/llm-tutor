"""Содержание конспектов topic01 (Срез 45).

Пример, который не запускается, хуже его отсутствия: ученик скопирует его в
редактор и получит ошибку. Поэтому каждый блок ```python выполняется сам по
себе, в чистом пространстве имён (у блока есть «Скопировать» и «Открыть в
редакторе»), а предупреждения pandas считаются ошибками — устаревший API
не должен попадать в конспект.
"""

import re
import warnings

import pytest

from llm_tutor.course.seed import DEFAULT_SEED_PATH, load_seed_data, theory_dir_for

pd = pytest.importorskip("pandas")
matplotlib = pytest.importorskip("matplotlib")
matplotlib.use("Agg")
plt = pytest.importorskip("matplotlib.pyplot")

SEED = load_seed_data(DEFAULT_SEED_PATH)
NODES = [node.id for node in SEED.nodes]
SECTIONS = ("## Зачем", "## Главное", "## Пример", "## Частые ошибки", "## Источник")
_BLOCK_RE = re.compile(r"```python\n(.*?)```", re.DOTALL)
# Любая метка блока кода: Python — только под меткой python, иначе блок не
# исполнится тестом, хотя сайт подсветит его как Python.
_FENCE_RE = re.compile(r"^```(\S*)", re.MULTILINE)


def test_every_node_has_theory() -> None:
    assert sorted(SEED.theory) == sorted(NODES)


def test_no_stray_theory_files() -> None:
    files = {path.stem for path in theory_dir_for(DEFAULT_SEED_PATH).glob("*.md")}
    assert files == set(NODES)


@pytest.mark.parametrize("node_id", NODES)
def test_theory_structure(node_id: str) -> None:
    text = SEED.theory[node_id]
    positions = [text.find(section) for section in SECTIONS]

    assert text.startswith("# "), node_id
    assert all(position >= 0 for position in positions), (node_id, positions)
    assert positions == sorted(positions), node_id
    words = len(re.findall(r"\w+", text))
    assert 180 <= words <= 1100, (node_id, words)
    assert "mlcourse.ai" in text[text.find("## Источник") :]


@pytest.mark.parametrize("node_id", NODES)
def test_theory_examples_run(node_id: str) -> None:
    blocks = _BLOCK_RE.findall(SEED.theory[node_id])
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


@pytest.mark.parametrize("node_id", NODES)
def test_python_blocks_use_python_label(node_id: str) -> None:
    labels = _FENCE_RE.findall(SEED.theory[node_id])
    opening = labels[0::2]  # метки открывающих строк (закрывающие — пустые)
    assert set(opening) <= {"python", ""}, (node_id, opening)
