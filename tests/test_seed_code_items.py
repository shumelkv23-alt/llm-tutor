"""Задания с кодом: проверки работают в CPython (Срез 46).

В браузере те же setup → код → tests выполняет Pyodide. Здесь проверяем, что
эталонное решение проходит проверки, а заготовка — нет: иначе задание либо
не решить, либо его «решает» пустой редактор.
"""

import pytest

from llm_tutor.course.seed import load_seed_data

pytest.importorskip("pandas")

SOLUTIONS = {
    10: "result = df.groupby('city')['age'].mean()",
    44: "result = df[(df['Churn']) & (df['Customer service calls'] > 3)]",
    45: "result = df.sort_values('Total day charge', ascending=False).head(3)",
    46: "result = df['Churn'].value_counts(normalize=True)",
    47: (
        "df['Total calls'] = df['Total day calls'] + df['Total eve calls'] + df['Total night calls']\n"
        "result = df"
    ),
    48: "result = pd.crosstab(df['International plan'], df['Churn'], normalize='index')",
    49: "df['Churn'] = df['Churn'].astype('int64')\nresult = df['Churn']",
    50: "result = df.loc[df['Churn'], 'Total day minutes'].median()",
}

# Неверные решения, которые на данных задания совпадали с верным (аудит среза 46).
WRONG = {
    10: "result = df.groupby('city')['age'].median()",
    44: "result = df[df['Customer service calls'] > 3]",
    45: (
        "result = df.head(3).copy()\n"
        "result['Total day charge'] = df['Total day charge'].sort_values(ascending=False).head(3).values"
    ),
    46: "result = pd.Series({True: 0.375, 'мусор': 0.625})",
}

# Верные альтернативы, которые проверки отвергали.
ALTERNATIVES = [
    (
        45,
        "result = df.sort_values('Total day charge', ascending=False).head(3).reset_index(drop=True)",
    ),
    (46, "result = df['Churn'].astype(int).value_counts(normalize=True)"),
    (
        48,
        "result = pd.crosstab(df['International plan'], df['Churn'].astype(int), normalize='index')",
    ),
    (10, "result = df.groupby('city')['age'].agg('mean')"),
]

ITEMS = {item.id: item for item in load_seed_data().items if item.answer_type == "code"}


def _run(item, code: str) -> None:
    namespace: dict = {"__name__": "__task__"}
    exec(compile(item.setup or "", "setup", "exec"), namespace)  # noqa: S102
    exec(compile(code, "solution", "exec"), namespace)  # noqa: S102
    exec(compile(item.tests or "", "tests", "exec"), namespace)  # noqa: S102


def test_every_code_item_is_runnable_and_has_rubric() -> None:
    assert set(ITEMS) == set(SOLUTIONS)
    for item in ITEMS.values():
        assert item.runnable, item.id
        # Рубрика — для ответа текстом и для бота: тесты есть только в браузере.
        assert item.rubric_id is not None, item.id
        assert "result" in item.prompt, item.id


@pytest.mark.parametrize("item_id", sorted(SOLUTIONS))
def test_solution_passes(item_id: int) -> None:
    _run(ITEMS[item_id], SOLUTIONS[item_id])


@pytest.mark.parametrize("item_id", sorted(WRONG))
def test_wrong_solution_fails(item_id: int) -> None:
    with pytest.raises(AssertionError) as exc_info:
        _run(ITEMS[item_id], WRONG[item_id])
    assert str(exc_info.value)


@pytest.mark.parametrize(("item_id", "code"), ALTERNATIVES)
def test_correct_alternative_passes(item_id: int, code: str) -> None:
    _run(ITEMS[item_id], code)


@pytest.mark.parametrize("item_id", sorted(SOLUTIONS))
def test_starter_fails_with_hint(item_id: int) -> None:
    item = ITEMS[item_id]
    with pytest.raises(AssertionError) as exc_info:
        _run(item, item.starter or "")
    assert str(exc_info.value), "проверка должна объяснять, что не так"


@pytest.mark.parametrize("item_id", sorted(SOLUTIONS))
def test_missing_result_is_explained(item_id: int) -> None:
    with pytest.raises(AssertionError, match="result"):
        _run(ITEMS[item_id], "x = 1")


def test_runnable_items_reach_lessons_but_not_bot_diagnostic(conn) -> None:
    """Код с тестами выдаётся в уроке, но не в /diagnostic бота: тот не умеет его проверить."""
    from llm_tutor.course.seed import load_seed
    from llm_tutor.student import diagnostic

    load_seed(conn)
    plain = {
        item.id for item in diagnostic._available_items(conn, include_rubric=False)
    }
    lesson = {
        item.id
        for item in diagnostic._available_items(
            conn, include_rubric=False, include_runnable=True
        )
    }

    assert not plain & set(SOLUTIONS)
    assert set(SOLUTIONS) <= lesson
