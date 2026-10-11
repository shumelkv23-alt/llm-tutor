"""Задания с кодом: проверки работают в CPython (Срез 46).

В браузере те же setup → код → tests выполняет Pyodide. Здесь проверяем, что
эталонное решение проходит проверки, а заготовка — нет: иначе задание либо
не решить, либо его «решает» пустой редактор.
"""

import pytest

from llm_tutor.course.seed import load_course_data

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
    # Итоговые задания модуля 1 (срез 56).
    1011: (
        "result = pd.DataFrame({'city': ['Казань', 'Москва', 'Сочи'], 'age': [20, 45, 52]},"
        " index=['a', 'b', 'c'])"
    ),
    1012: "df = pd.read_csv(io.StringIO(csv_text), sep=';')\nresult = df.isna().sum()",
    1013: "df['Churn'] = df['Churn'] == 'True'\nresult = df['Churn'].mean()",
    1014: "result = df.loc[df['State'] == 'OH', ['Total day minutes', 'Churn']]",
    1015: (
        "df['International plan'] = df['International plan'].map({'Yes': 1, 'No': 0})\n"
        "result = df.sort_values('Total day minutes', ascending=False).head(3)"
    ),
    1016: (
        "result = df.groupby('International plan')[['Churn', 'Customer service calls']].mean()"
    ),
    1017: (
        "df['pred'] = (df['International plan'] == 'Yes') | (df['Customer service calls'] >= 4)\n"
        "result = (df['pred'] == df['Churn']).mean()"
    ),
    # Задания с кодом внутри занятий модуля 1 (срез 57).
    1021: "result = day + eve",
    1022: "result = df.columns[df.dtypes == 'object'].tolist()",
    # Модуль 2 (срез 58): задания внутри занятий и итоговые.
    2111: "ax.hist(df['Total day minutes'], bins=6)\nax.set_title('Минуты днём')\nresult = ax",
    2112: (
        "top = df['State'].value_counts().head(3)\n"
        "top.plot(kind='bar', ax=ax)\n"
        "result = ax"
    ),
    2113: (
        "corr = df.corr(numeric_only=True).abs()\n"
        "pairs = corr.where(~np.eye(len(corr), dtype=bool)).stack()\n"
        "result = pairs.idxmax()"
    ),
    2114: (
        "g = df.groupby('Churn')['Total intl minutes']\n"
        "result = pd.DataFrame({'q1': g.quantile(0.25), 'median': g.median(), 'q3': g.quantile(0.75)})"
    ),
    2115: (
        "z = (df - df.mean()) / df.std()\n"
        "distance = ((z - z.loc[0]) ** 2).sum(axis=1) ** 0.5\n"
        "result = distance.drop(0).idxmin()"
    ),
    2116: (
        "numbers = df.select_dtypes('number')\n"
        "result = numbers.columns[numbers.nunique() <= 4].tolist()"
    ),
    2121: (
        "fig, axes = plt.subplots(1, 2, figsize=(9, 3))\n"
        "axes[0].hist(df['Total day minutes'], bins=8)\n"
        "axes[0].set_title('Минуты днём')\n"
        "df['International plan'].value_counts().plot(kind='bar', ax=axes[1])\n"
        "axes[1].set_title('Тарифы')\n"
        "result = fig"
    ),
    2122: (
        "m = df['Total day minutes']\n"
        "q1, q3 = m.quantile([0.25, 0.75])\n"
        "iqr = q3 - q1\n"
        "low, high = q1 - 1.5 * iqr, q3 + 1.5 * iqr\n"
        "outside = (m < low) | (m > high)\n"
        "result = {'low': low, 'high': high, 'n_outliers': int(outside.sum())}\n"
        "clean = df[~outside]"
    ),
    2123: (
        "for flag, group in df.groupby('Churn'):\n"
        "    ax.scatter(group['Total day minutes'], group['Customer service calls'], label=str(flag))\n"
        "corr = df['Total day minutes'].corr(df['Customer service calls'])\n"
        "result = ax"
    ),
    2124: (
        "table = df.pivot_table(values='Churn', index='International plan', columns='many_calls', aggfunc='mean')\n"
        "ax.imshow(table.values)\n"
        "result = ax"
    ),
    2125: (
        "cols = ['Total day minutes', 'Customer service calls']\n"
        "z = (df[cols] - df[cols].mean()) / df[cols].std()\n"
        "for flag in (False, True):\n"
        "    part = z[df['Churn'] == flag]\n"
        "    ax.scatter(part[cols[0]], part[cols[1]])\n"
        "result = ax"
    ),
    2126: (
        "groups = pd.cut(df['Customer service calls'], [-1, 1, 3, float('inf')], labels=['0-1', '2-3', '4+'])\n"
        "result = df.groupby(groups, observed=False)['Churn'].agg(share='mean', clients='size')\n"
        "result['share'].plot(kind='bar', ax=ax)"
    ),
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
    1011: "result = pd.DataFrame({'city': ['Казань', 'Москва', 'Сочи'], 'age': [20, 45, 52]})",
    1012: "df = pd.read_csv(io.StringIO(csv_text))\nresult = df.isna().sum()",
    1013: "df['Churn'] = df['Churn'].astype(bool)\nresult = df['Churn'].mean()",
    1014: "result = df[df['State'] == 'OH']",
    1015: "result = df.sort_values('Total day minutes', ascending=False).head(3)",
    1016: "result = df.groupby('International plan')['Churn'].mean()",
    1017: (
        "df['pred'] = (df['International plan'] == 'Yes') & (df['Customer service calls'] >= 4)\n"
        "result = (df['pred'] == df['Churn']).mean()"
    ),
    1021: "result = pd.Series(day.values + eve.values, index=day.index)",
    1022: "result = df.columns.tolist()",
}

# Модуль 2: неверные решения. WRONG — главная ловушка задания, MORE_WRONG — другая ошибка.
WRONG.update({
    2111: "ax.hist(df['Total day minutes'], bins=5)\nax.set_title('Минуты днём')\nresult = ax",
    2112: "df['State'].value_counts(ascending=True).head(3).plot(kind='bar', ax=ax)\nresult = ax",
    2113: "result = tuple(df.corr(numeric_only=True).unstack().idxmax())",
    2114: (
        "g = df.groupby('Churn')['Total intl minutes']\n"
        "result = pd.DataFrame({'q1': g.quantile(0.25), 'median': g.mean(), 'q3': g.quantile(0.75)})"
    ),
    2115: (
        "distance = ((df - df.loc[0]) ** 2).sum(axis=1) ** 0.5\n"
        "result = distance.drop(0).idxmin()"
    ),
    2116: "result = df.columns[df.nunique() <= 4].tolist()",
    2121: (
        "fig, axes = plt.subplots(1, 2, figsize=(9, 3))\n"
        "axes[0].hist(df['Total day minutes'], bins=8)\n"
        "df['International plan'].value_counts().plot(kind='bar', ax=axes[1])\n"
        "axes[0].set_title('Минуты днём')\n"
        "result = fig"
    ),
    2122: (
        "m = df['Total day minutes']\n"
        "low, high = m.mean() - 3 * m.std(), m.mean() + 3 * m.std()\n"
        "outside = (m < low) | (m > high)\n"
        "result = {'low': low, 'high': high, 'n_outliers': int(outside.sum())}\n"
        "clean = df[~outside]"
    ),
    2123: (
        "ax.scatter(df['Total day minutes'], df['Customer service calls'])\n"
        "corr = df['Total day minutes'].corr(df['Customer service calls'])\n"
        "result = ax"
    ),
    2124: (
        "table = df.pivot_table(values='Churn', index='International plan', columns='many_calls', aggfunc='sum')\n"
        "ax.imshow(table.values)\n"
        "result = ax"
    ),
    2125: (
        "cols = ['Total day minutes', 'Customer service calls']\n"
        "z = df[cols] - df[cols].mean()\n"
        "for flag in (False, True):\n"
        "    part = z[df['Churn'] == flag]\n"
        "    ax.scatter(part[cols[0]], part[cols[1]])\n"
        "result = ax"
    ),
    2126: (
        "groups = pd.cut(df['Customer service calls'], [0, 1, 3, 10], labels=['0-1', '2-3', '4+'])\n"
        "result = df.groupby(groups, observed=False)['Churn'].agg(share='mean', clients='size')\n"
        "result['share'].plot(kind='bar', ax=ax)"
    ),
})

MORE_WRONG = [
    (2111, "ax.hist(df['Total day minutes'], bins=6)\nresult = ax"),
    (2112, "ax.bar([0, 1, 2], [6, 4, 3])\nresult = ax"),
    (2113, "result = ('Total day minutes', 'Total eve minutes')"),
    (2114, "result = df.groupby('Churn')['Total intl minutes'].describe()"),
    (
        2115,
        "z = (df - df.mean()) / df.std()\n"
        "result = ((z - z.loc[0]) ** 2).sum(axis=1).idxmin()",
    ),
    (
        2116,
        "numbers = df.select_dtypes('number')\n"
        "result = numbers.columns[numbers.nunique() < 4].tolist()",
    ),
    (
        2121,
        "fig, axes = plt.subplots(1, 2, figsize=(9, 3))\n"
        "axes[0].hist(df['Total day minutes'], bins=10)\n"
        "df['International plan'].value_counts().plot(kind='bar', ax=axes[1])\n"
        "axes[0].set_title('a')\naxes[1].set_title('b')\nresult = fig",
    ),
    (
        2122,
        "m = df['Total day minutes']\n"
        "q1, q3 = m.quantile([0.25, 0.75])\n"
        "high = q3 + 1.5 * (q3 - q1)\nlow = q1 - 1.5 * (q3 - q1)\n"
        "outside = m > high\n"
        "result = {'low': low, 'high': high, 'n_outliers': int(outside.sum())}\n"
        "clean = df[~outside]",
    ),
    (
        2123,
        "for flag, group in df.groupby('Churn'):\n"
        "    ax.scatter(group['Total day minutes'], group['Customer service calls'])\n"
        "corr = df['Total day minutes'].corr(df['Customer service calls'], method='spearman')\n"
        "result = ax",
    ),
    (
        2124,
        "table = df.pivot_table(values='Churn', index='many_calls', columns='International plan', aggfunc='mean')\n"
        "ax.imshow(table.values)\n"
        "result = ax",
    ),
    (
        2125,
        "cols = ['Total day minutes', 'Customer service calls']\n"
        "z = (df[cols] - df[cols].mean()) / df[cols].std()\n"
        "ax.scatter(z[cols[0]], z[cols[1]])\n"
        "result = ax",
    ),
    (
        2126,
        "groups = pd.cut(df['Customer service calls'], [-1, 1, 3, float('inf')], labels=['0-1', '2-3', '4+'])\n"
        "result = df.groupby(groups, observed=False)['Churn'].agg(share='sum', clients='size')\n"
        "result['share'].plot(kind='bar', ax=ax)",
    ),
]

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
    (
        1011,
        "result = pd.DataFrame([['Казань', 20], ['Москва', 45], ['Сочи', 52]],"
        " columns=['city', 'age'], index=['a', 'b', 'c'])",
    ),
    (1013, "df['Churn'] = df['Churn'].map({'True': True, 'False': False})\nresult = df['Churn'].mean()"),
    (
        1015,
        "df['International plan'] = df['International plan'].replace({'Yes': 1, 'No': 0})\n"
        "result = df.nlargest(3, 'Total day minutes')",
    ),
    (
        1016,
        "result = df.groupby('International plan').agg("
        "{'Churn': 'mean', 'Customer service calls': 'mean'})",
    ),
    (
        1017,
        "df['pred'] = df['International plan'].eq('Yes') | df['Customer service calls'].ge(4)\n"
        "result = df['pred'].eq(df['Churn']).sum() / len(df)",
    ),
    (1021, "result = day.add(eve)"),
    (1022, "result = list(df.select_dtypes(include='object').columns)"),
    # Модуль 2.
    (2111, "result = df['Total day minutes'].plot(kind='hist', bins=6, title='Минуты днём')"),
    (2112, "top = df['State'].value_counts().head(3)\nax.bar(top.index, top.values)\nresult = ax"),
    (
        2113,
        "corr = df.corr(numeric_only=True).abs()\n"
        "best = max(((a, b) for a in corr for b in corr if a < b), key=lambda p: corr.loc[p])\n"
        "result = list(best)",
    ),
    (
        2114,
        "result = df.groupby('Churn')['Total intl minutes'].agg(\n"
        "    q1=lambda s: s.quantile(0.25), median='median', q3=lambda s: s.quantile(0.75))",
    ),
    (
        2115,
        "z = (df - df.mean()) / df.std(ddof=0)\n"
        "result = int(np.sqrt(((z.drop(0) - z.loc[0]) ** 2).sum(axis=1)).idxmin())",
    ),
    (2116, "result = [c for c in df.select_dtypes('number') if df[c].nunique() <= 4]"),
    (
        2121,
        "fig, axes = plt.subplots(1, 2, figsize=(9, 3))\n"
        "df['Total day minutes'].plot(kind='hist', bins=8, ax=axes[0], title='Минуты днём')\n"
        "counts = df['International plan'].value_counts()\n"
        "axes[1].bar(counts.index, counts.values)\n"
        "axes[1].set_title('Тарифы')\n"
        "result = fig",
    ),
    (
        2122,
        "m = df['Total day minutes']\n"
        "q1, q3 = np.percentile(m, [25, 75])\n"
        "low, high = q1 - 1.5 * (q3 - q1), q3 + 1.5 * (q3 - q1)\n"
        "keep = m.between(low, high)\n"
        "result = {'low': low, 'high': high, 'n_outliers': int((~keep).sum())}\n"
        "clean = df.loc[keep]",
    ),
    (
        2123,
        "ax.scatter(df.loc[df['Churn'], 'Total day minutes'], df.loc[df['Churn'], 'Customer service calls'])\n"
        "ax.scatter(df.loc[~df['Churn'], 'Total day minutes'], df.loc[~df['Churn'], 'Customer service calls'])\n"
        "corr = np.corrcoef(df['Total day minutes'], df['Customer service calls'])[0, 1]\n"
        "result = ax",
    ),
    (
        2124,
        "table = df.groupby(['International plan', 'many_calls'])['Churn'].mean().unstack()\n"
        "ax.imshow(table)\n"
        "result = ax",
    ),
    (
        2125,
        "cols = ['Total day minutes', 'Customer service calls']\n"
        "z = (df[cols] - df[cols].mean()) / df[cols].std(ddof=0)\n"
        "ax.scatter(z.loc[df['Churn'], cols[0]], z.loc[df['Churn'], cols[1]])\n"
        "ax.scatter(z.loc[~df['Churn'], cols[0]], z.loc[~df['Churn'], cols[1]])\n"
        "result = ax",
    ),
    (
        2126,
        "bins = pd.cut(df['Customer service calls'], bins=[-1, 1, 3, 100], labels=['0-1', '2-3', '4+'])\n"
        "result = pd.DataFrame({'share': df.groupby(bins, observed=False)['Churn'].mean(),\n"
        "                       'clients': df.groupby(bins, observed=False)['Churn'].size()})\n"
        "ax.bar(range(3), result['share'])",
    ),
]

ITEMS = {item.id: item for item in load_course_data().items if item.answer_type == "code"}


def _run(item, code: str) -> None:
    namespace: dict = {"__name__": "__task__"}
    exec(compile(item.setup or "", "setup", "exec"), namespace)  # noqa: S102
    exec(compile(code, "solution", "exec"), namespace)  # noqa: S102
    try:
        exec(compile(item.tests or "", "tests", "exec"), namespace)  # noqa: S102
    finally:
        import matplotlib.pyplot as plt

        plt.close("all")


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


@pytest.mark.parametrize(
    ("item_id", "code"),
    [*WRONG.items(), *MORE_WRONG],
    ids=lambda value: str(value)[:12],
)
def test_wrong_solution_fails(item_id: int, code: str) -> None:
    with pytest.raises(AssertionError) as exc_info:
        _run(ITEMS[item_id], code)
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
    from llm_tutor.course.seed import load_course
    from llm_tutor.student import diagnostic

    load_course(conn)
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
