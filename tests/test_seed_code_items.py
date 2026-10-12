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

# Модуль 3 (срез 59): задания внутри занятий (3111–3117) и итоговые (3121–3127).
_ENTROPY = (
    "import numpy as np\n"
    "def entropy(labels):\n"
    "    _, counts = np.unique(labels, return_counts=True)\n"
    "    p = counts / counts.sum()\n"
    "    return float((p * np.log2(1 / p)).sum())\n"
)

SOLUTIONS.update({
    3111: (
        "from sklearn.model_selection import train_test_split\n"
        "from sklearn.tree import DecisionTreeClassifier\n"
        "a, b, c, d = train_test_split(X, y, test_size=0.25, random_state=17, stratify=y)\n"
        "model = DecisionTreeClassifier(max_depth=2, random_state=17).fit(a, c)\n"
        "result = model.score(b, d)"
    ),
    3112: _ENTROPY.replace("float((p", "float(-(p").replace("np.log2(1 / p)", "np.log2(p)")
    + "result = entropy(y)",
    3113: (
        "order = np.argsort(ages)\n"
        "a, c = ages[order], churn[order]\n"
        "result = [float((a[i] + a[i + 1]) / 2) for i in range(len(a) - 1) if c[i] != c[i + 1]]"
    ),
    3114: (
        "from sklearn.tree import DecisionTreeClassifier\n"
        "result = DecisionTreeClassifier(max_depth=3, min_samples_leaf=5, random_state=17)"
        ".fit(X_train, y_train)"
    ),
    3115: (
        "from sklearn.neighbors import KNeighborsClassifier\n"
        "from sklearn.pipeline import make_pipeline\n"
        "from sklearn.preprocessing import StandardScaler\n"
        "model = make_pipeline(StandardScaler(), KNeighborsClassifier(n_neighbors=3, weights='distance'))\n"
        "result = model.fit(X_train, y_train).score(X_test, y_test)"
    ),
    3116: (
        "from sklearn.model_selection import cross_val_score\n"
        "from sklearn.tree import DecisionTreeClassifier\n"
        "result = cross_val_score(DecisionTreeClassifier(max_depth=3, random_state=17), X, y, cv=5).mean()"
    ),
    3117: (
        "from sklearn.tree import DecisionTreeClassifier\n"
        "result = DecisionTreeClassifier(random_state=17).fit(X, y).get_n_leaves()"
    ),
    3121: (
        "from sklearn.dummy import DummyClassifier\n"
        "from sklearn.model_selection import train_test_split\n"
        "from sklearn.tree import DecisionTreeClassifier\n"
        "a, b, c, d = train_test_split(X, y, test_size=0.3, random_state=17, stratify=y)\n"
        "result = {\n"
        "    'baseline': DummyClassifier(strategy='most_frequent').fit(a, c).score(b, d),\n"
        "    'tree': DecisionTreeClassifier(max_depth=3, random_state=17).fit(a, c).score(b, d),\n"
        "}"
    ),
    3122: (
        _ENTROPY
        + "def information_gain(parent, groups):\n"
        "    n = len(parent)\n"
        "    return entropy(parent) - sum(len(g) / n * entropy(g) for g in groups)\n"
        "gains = {\n"
        "    name: information_gain(y, [y[(features[name] == v).values] for v in (0, 1)])\n"
        "    for name in features.columns\n"
        "}\n"
        "result = max(gains, key=gains.get)"
    ),
    3123: (
        _ENTROPY
        + "def best_threshold(x, y):\n"
        "    x, y = np.asarray(x), np.asarray(y)\n"
        "    order = np.argsort(x, kind='stable')\n"
        "    x, y = x[order], y[order]\n"
        "    best, best_gain = None, -1.0\n"
        "    for i in range(len(x) - 1):\n"
        "        if y[i] == y[i + 1]:\n"
        "            continue\n"
        "        t = (x[i] + x[i + 1]) / 2\n"
        "        left, right = y[x <= t], y[x > t]\n"
        "        gain = entropy(y) - len(left) / len(y) * entropy(left) - len(right) / len(y) * entropy(right)\n"
        "        if gain > best_gain:\n"
        "            best, best_gain = t, gain\n"
        "    return best\n"
        "result = best_threshold(ages, churn)"
    ),
    3124: (
        "import pandas as pd\n"
        "from sklearn.tree import DecisionTreeClassifier\n"
        "rows = []\n"
        "for depth in range(1, 9):\n"
        "    m = DecisionTreeClassifier(max_depth=depth, random_state=17).fit(X_train, y_train)\n"
        "    rows.append({'depth': depth, 'train': m.score(X_train, y_train), 'test': m.score(X_test, y_test)})\n"
        "result = pd.DataFrame(rows)\n"
        "best = int(result.loc[result['test'].idxmax(), 'depth'])"
    ),
    3125: (
        "from sklearn.model_selection import cross_val_score\n"
        "from sklearn.neighbors import KNeighborsClassifier\n"
        "from sklearn.pipeline import make_pipeline\n"
        "from sklearn.preprocessing import StandardScaler\n"
        "result = {\n"
        "    k: cross_val_score(make_pipeline(StandardScaler(), KNeighborsClassifier(n_neighbors=k)), X, y, cv=5).mean()\n"
        "    for k in [1, 3, 5, 9, 15]\n"
        "}\n"
        "best_k = max(result, key=lambda k: (result[k], -k))"
    ),
    3126: (
        "from sklearn.model_selection import GridSearchCV\n"
        "from sklearn.tree import DecisionTreeClassifier\n"
        "grid = GridSearchCV(DecisionTreeClassifier(random_state=17),\n"
        "                    {'max_depth': [1, 2, 3, 4, 5], 'min_samples_leaf': [1, 5, 10]}, cv=5)\n"
        "grid.fit(X_train, y_train)\n"
        "result = {'best_params': grid.best_params_, 'test_score': grid.score(X_test, y_test)}"
    ),
    3127: (
        "from sklearn.model_selection import cross_val_score\n"
        "from sklearn.neighbors import KNeighborsClassifier\n"
        "from sklearn.tree import DecisionTreeClassifier\n"
        "tree = cross_val_score(DecisionTreeClassifier(max_depth=8, random_state=17), X, y, cv=5).mean()\n"
        "knn = cross_val_score(KNeighborsClassifier(n_neighbors=3), X, y, cv=5).mean()\n"
        "result = {'tree': tree, 'knn': knn, 'winner': 'knn' if knn > tree else 'tree'}"
    ),
})

WRONG.update({
    3111: (
        "from sklearn.model_selection import train_test_split\n"
        "from sklearn.tree import DecisionTreeClassifier\n"
        "a, b, c, d = train_test_split(X, y, test_size=0.25, random_state=17, stratify=y)\n"
        "model = DecisionTreeClassifier(max_depth=2, random_state=17).fit(a, c)\n"
        "result = model.score(a, c)"
    ),
    3112: _ENTROPY.replace("float((p * np.log2(1 / p))", "float((p * np.log(1 / p))") + "result = entropy(y)",
    3113: (
        "a, c = ages, churn\n"
        "result = [float((a[i] + a[i + 1]) / 2) for i in range(len(a) - 1) if c[i] != c[i + 1]]"
    ),
    3114: (
        "from sklearn.tree import DecisionTreeClassifier\n"
        "result = DecisionTreeClassifier(max_depth=3, min_samples_split=5, random_state=17)"
        ".fit(X_train, y_train)"
    ),
    3115: (
        "from sklearn.neighbors import KNeighborsClassifier\n"
        "model = KNeighborsClassifier(n_neighbors=3, weights='distance')\n"
        "result = model.fit(X_train, y_train).score(X_test, y_test)"
    ),
    3116: (
        "from sklearn.model_selection import cross_val_score\n"
        "from sklearn.tree import DecisionTreeClassifier\n"
        "result = cross_val_score(DecisionTreeClassifier(max_depth=3, random_state=17), X, y, cv=3).mean()"
    ),
    3117: (
        "from sklearn.tree import DecisionTreeClassifier\n"
        "result = DecisionTreeClassifier(max_depth=3, random_state=17).fit(X, y).get_n_leaves()"
    ),
    3121: (
        "from sklearn.dummy import DummyClassifier\n"
        "from sklearn.model_selection import train_test_split\n"
        "from sklearn.tree import DecisionTreeClassifier\n"
        "a, b, c, d = train_test_split(X, y, test_size=0.3, random_state=17, stratify=y)\n"
        "result = {\n"
        "    'baseline': DummyClassifier(strategy='most_frequent').fit(a, c).score(a, c),\n"
        "    'tree': DecisionTreeClassifier(max_depth=3, random_state=17).fit(a, c).score(b, d),\n"
        "}"
    ),
    3122: (
        _ENTROPY
        + "def information_gain(parent, groups):\n"
        "    return entropy(parent) - sum(entropy(g) for g in groups) / len(groups)\n"
        "gains = {\n"
        "    name: information_gain(y, [y[(features[name] == v).values] for v in (0, 1)])\n"
        "    for name in features.columns\n"
        "}\n"
        "result = max(gains, key=gains.get)"
    ),
    3123: (
        _ENTROPY
        + "def best_threshold(x, y):\n"
        "    x, y = np.asarray(x), np.asarray(y)\n"
        "    order = np.argsort(x, kind='stable')\n"
        "    x, y = x[order], y[order]\n"
        "    for i in range(len(x) - 1):\n"
        "        if y[i] != y[i + 1]:\n"
        "            return (x[i] + x[i + 1]) / 2\n"
        "result = best_threshold(ages, churn)"
    ),
    3124: (
        "import pandas as pd\n"
        "from sklearn.tree import DecisionTreeClassifier\n"
        "rows = []\n"
        "for depth in range(1, 8):\n"
        "    m = DecisionTreeClassifier(max_depth=depth, random_state=17).fit(X_train, y_train)\n"
        "    rows.append({'depth': depth, 'train': m.score(X_train, y_train), 'test': m.score(X_test, y_test)})\n"
        "result = pd.DataFrame(rows)\n"
        "best = int(result.loc[result['test'].idxmax(), 'depth'])"
    ),
    3125: (
        "from sklearn.model_selection import cross_val_score\n"
        "from sklearn.neighbors import KNeighborsClassifier\n"
        "from sklearn.preprocessing import StandardScaler\n"
        "Z = StandardScaler().fit_transform(X)\n"
        "result = {k: cross_val_score(KNeighborsClassifier(n_neighbors=k), Z, y, cv=5).mean() for k in [1, 3, 5, 9, 15]}\n"
        "best_k = max(result, key=lambda k: (result[k], -k))"
    ),
    3126: (
        "from sklearn.model_selection import GridSearchCV\n"
        "from sklearn.tree import DecisionTreeClassifier\n"
        "grid = GridSearchCV(DecisionTreeClassifier(random_state=17),\n"
        "                    {'max_depth': [1, 2, 3, 4, 5], 'min_samples_leaf': [1, 5, 10]}, cv=3)\n"
        "grid.fit(X_train, y_train)\n"
        "result = {'best_params': grid.best_params_, 'test_score': grid.score(X_test, y_test)}"
    ),
    3127: (
        "from sklearn.model_selection import cross_val_score\n"
        "from sklearn.neighbors import KNeighborsClassifier\n"
        "from sklearn.tree import DecisionTreeClassifier\n"
        "tree = cross_val_score(DecisionTreeClassifier(random_state=17), X, y, cv=5).mean()\n"
        "knn = cross_val_score(KNeighborsClassifier(n_neighbors=3), X, y, cv=5).mean()\n"
        "result = {'tree': tree, 'knn': knn, 'winner': 'knn' if knn > tree else 'tree'}"
    ),
})

MORE_WRONG += [
    (
        3111,
        "from sklearn.model_selection import train_test_split\n"
        "from sklearn.tree import DecisionTreeClassifier\n"
        "a, b, c, d = train_test_split(X, y, test_size=0.25, random_state=17)\n"
        "result = DecisionTreeClassifier(max_depth=2, random_state=17).fit(a, c).score(b, d)",
    ),
    (
        3112,
        "import numpy as np\n"
        "def entropy(labels):\n"
        "    _, counts = np.unique(labels, return_counts=True)\n"
        "    p = counts / counts.sum()\n"
        "    return float(1 - (p ** 2).sum())\n"
        "result = entropy(y)",
    ),
    (
        3113,
        "order = np.argsort(ages)\n"
        "a = ages[order]\n"
        "result = [float((a[i] + a[i + 1]) / 2) for i in range(len(a) - 1)]",
    ),
    (
        3114,
        "from sklearn.tree import DecisionTreeClassifier\n"
        "result = DecisionTreeClassifier(max_depth=3, min_samples_leaf=5, random_state=17).fit(X, y)",
    ),
    (
        3115,
        "from sklearn.neighbors import KNeighborsClassifier\n"
        "from sklearn.pipeline import make_pipeline\n"
        "from sklearn.preprocessing import StandardScaler\n"
        "model = make_pipeline(StandardScaler(), KNeighborsClassifier(n_neighbors=3))\n"
        "result = model.fit(X_train, y_train).score(X_train, y_train)",
    ),
    (
        3116,
        "from sklearn.tree import DecisionTreeClassifier\n"
        "result = DecisionTreeClassifier(max_depth=3, random_state=17).fit(X, y).score(X, y)",
    ),
    (3117, "result = 2"),
    (
        3121,
        "from sklearn.dummy import DummyClassifier\n"
        "from sklearn.model_selection import train_test_split\n"
        "from sklearn.tree import DecisionTreeClassifier\n"
        "a, b, c, d = train_test_split(X, y, test_size=0.3, random_state=17)\n"
        "result = {\n"
        "    'baseline': DummyClassifier(strategy='most_frequent').fit(a, c).score(b, d),\n"
        "    'tree': DecisionTreeClassifier(max_depth=3, random_state=17).fit(a, c).score(b, d),\n"
        "}",
    ),
    (
        3122,
        _ENTROPY
        + "def information_gain(parent, groups):\n"
        "    n = len(parent)\n"
        "    return entropy(parent) - sum(len(g) / n * entropy(g) for g in groups)\n"
        "gains = {\n"
        "    name: information_gain(y, [y[(features[name] == v).values] for v in (0, 1)])\n"
        "    for name in features.columns\n"
        "}\n"
        "result = min(gains, key=gains.get)",
    ),
    (
        3123,
        _ENTROPY
        + "def best_threshold(x, y):\n"
        "    x, y = np.asarray(x), np.asarray(y)\n"
        "    order = np.argsort(x, kind='stable')\n"
        "    x, y = x[order], y[order]\n"
        "    best, best_gain = None, -1.0\n"
        "    for i in range(len(x) - 1):\n"
        "        if y[i] == y[i + 1]:\n"
        "            continue\n"
        "        t = (x[i] + x[i + 1]) / 2\n"
        "        left, right = y[x <= t], y[x > t]\n"
        "        gain = entropy(y) - (entropy(left) + entropy(right)) / 2\n"
        "        if gain > best_gain:\n"
        "            best, best_gain = t, gain\n"
        "    return best\n"
        "result = best_threshold(ages, churn)",
    ),
    (
        3124,
        "import pandas as pd\n"
        "from sklearn.tree import DecisionTreeClassifier\n"
        "rows = []\n"
        "for depth in range(1, 9):\n"
        "    m = DecisionTreeClassifier(max_depth=depth, random_state=17).fit(X_train, y_train)\n"
        "    rows.append({'depth': depth, 'train': m.score(X_train, y_train), 'test': m.score(X_test, y_test)})\n"
        "result = pd.DataFrame(rows)\n"
        "best = int(result.loc[result['train'].idxmax(), 'depth'])",
    ),
    (
        3125,
        "from sklearn.model_selection import cross_val_score\n"
        "from sklearn.neighbors import KNeighborsClassifier\n"
        "result = {k: cross_val_score(KNeighborsClassifier(n_neighbors=k), X, y, cv=5).mean() for k in [1, 3, 5, 9, 15]}\n"
        "best_k = max(result, key=lambda k: (result[k], -k))",
    ),
    (
        3126,
        "from sklearn.model_selection import GridSearchCV\n"
        "from sklearn.tree import DecisionTreeClassifier\n"
        "grid = GridSearchCV(DecisionTreeClassifier(random_state=17),\n"
        "                    {'max_depth': [3, 4, 5], 'min_samples_leaf': [1, 5, 10]}, cv=5)\n"
        "grid.fit(X_train, y_train)\n"
        "result = {'best_params': grid.best_params_, 'test_score': grid.score(X_test, y_test)}",
    ),
    (
        3127,
        "from sklearn.model_selection import cross_val_score\n"
        "from sklearn.neighbors import KNeighborsClassifier\n"
        "from sklearn.tree import DecisionTreeClassifier\n"
        "tree = cross_val_score(DecisionTreeClassifier(max_depth=8, random_state=17), X, y, cv=5).mean()\n"
        "knn = cross_val_score(KNeighborsClassifier(n_neighbors=3), X, y, cv=5).mean()\n"
        "result = {'tree': tree, 'knn': knn, 'winner': 'tree'}",
    ),
]

ALTERNATIVES += [
    (
        3111,
        "from sklearn.metrics import accuracy_score\n"
        "from sklearn.model_selection import train_test_split\n"
        "from sklearn.tree import DecisionTreeClassifier\n"
        "X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.25, random_state=17, stratify=y)\n"
        "model = DecisionTreeClassifier(max_depth=2, random_state=17)\n"
        "model.fit(X_tr, y_tr)\n"
        "result = accuracy_score(y_te, model.predict(X_te))",
    ),
    (
        3112,
        "import math\n"
        "def entropy(labels):\n"
        "    labels = list(labels)\n"
        "    total = 0.0\n"
        "    for c in set(labels):\n"
        "        p = labels.count(c) / len(labels)\n"
        "        total -= p * math.log2(p)\n"
        "    return total\n"
        "result = entropy(y)",
    ),
    (
        3113,
        "pairs = sorted(zip(ages.tolist(), churn.tolist()))\n"
        "result = [(pairs[i][0] + pairs[i + 1][0]) / 2 for i in range(len(pairs) - 1)"
        " if pairs[i][1] != pairs[i + 1][1]]",
    ),
    (
        3114,
        "from sklearn.tree import DecisionTreeClassifier\n"
        "result = DecisionTreeClassifier(random_state=17)\n"
        "result.set_params(max_depth=3, min_samples_leaf=5)\n"
        "result.fit(X_train, y_train)",
    ),
    (
        3115,
        "from sklearn.neighbors import KNeighborsClassifier\n"
        "from sklearn.pipeline import Pipeline\n"
        "from sklearn.preprocessing import StandardScaler\n"
        "pipe = Pipeline([('scale', StandardScaler()), ('knn', KNeighborsClassifier(n_neighbors=3, weights='distance'))])\n"
        "pipe.fit(X_train, y_train)\n"
        "result = (pipe.predict(X_test) == y_test).mean()",
    ),
    (
        3116,
        "from sklearn.model_selection import StratifiedKFold, cross_val_score\n"
        "from sklearn.tree import DecisionTreeClassifier\n"
        "scores = cross_val_score(DecisionTreeClassifier(max_depth=3, random_state=17), X, y,\n"
        "                         cv=StratifiedKFold(n_splits=5))\n"
        "result = sum(scores) / len(scores)",
    ),
    (
        3117,
        "from sklearn.tree import DecisionTreeClassifier\n"
        "tree = DecisionTreeClassifier(random_state=17).fit(X, y)\n"
        "result = int((tree.tree_.children_left == -1).sum())",
    ),
    (
        3121,
        "from sklearn.dummy import DummyClassifier\n"
        "from sklearn.metrics import accuracy_score\n"
        "from sklearn.model_selection import train_test_split\n"
        "from sklearn.tree import DecisionTreeClassifier\n"
        "X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.3, random_state=17, stratify=y)\n"
        "dummy = DummyClassifier(strategy='most_frequent').fit(X_tr, y_tr)\n"
        "tree = DecisionTreeClassifier(max_depth=3, random_state=17).fit(X_tr, y_tr)\n"
        "result = {'baseline': accuracy_score(y_te, dummy.predict(X_te)),\n"
        "          'tree': accuracy_score(y_te, tree.predict(X_te))}",
    ),
    (
        3122,
        "import numpy as np\n"
        "def entropy(labels):\n"
        "    counts = np.bincount(np.asarray(labels))\n"
        "    p = counts[counts > 0] / len(labels)\n"
        "    return float(-np.sum(p * np.log2(p)))\n"
        "def information_gain(parent, groups):\n"
        "    sizes = [len(g) for g in groups]\n"
        "    return entropy(parent) - float(np.average([entropy(g) for g in groups], weights=sizes))\n"
        "best, best_gain = None, -1\n"
        "for name in features.columns:\n"
        "    mask = (features[name] == 1).values\n"
        "    gain = information_gain(y, [y[~mask], y[mask]])\n"
        "    if gain > best_gain:\n"
        "        best, best_gain = name, gain\n"
        "result = best",
    ),
    (
        3126,
        "from sklearn.model_selection import GridSearchCV\n"
        "from sklearn.tree import DecisionTreeClassifier\n"
        "search = GridSearchCV(DecisionTreeClassifier(random_state=17),\n"
        "                      param_grid={'min_samples_leaf': [1, 5, 10], 'max_depth': list(range(1, 6))},\n"
        "                      cv=5, refit=True)\n"
        "search.fit(X_train, y_train)\n"
        "result = {'best_params': search.best_params_,\n"
        "          'test_score': search.best_estimator_.score(X_test, y_test)}",
    ),
]

# Модуль 4 (срез 60): задания внутри занятий (4111–4117) и итоговые (4121–4127).
_NE = "X1 = np.column_stack([np.ones(len(X)), X])\n"

SOLUTIONS.update({
    4111: _NE + "result = np.linalg.solve(X1.T @ X1, X1.T @ y)",
    4112: "result = ((predictions.mean() - truth) ** 2, predictions.var())",
    4113: (
        "def ridge_weights(X, y, lam):\n"
        "    A = X.T @ X + lam * np.eye(X.shape[1])\n"
        "    return np.linalg.solve(A, X.T @ y)\n"
        "result = ridge_weights(X, y, 10.0)"
    ),
    4114: "margin = y * z\nresult = np.log1p(np.exp(-margin)).mean()",
    4115: (
        "from sklearn.preprocessing import PolynomialFeatures\n"
        "result = PolynomialFeatures(degree=2, include_bias=False).fit_transform(X)"
    ),
    4116: (
        "from sklearn.feature_extraction.text import CountVectorizer\n"
        "from sklearn.linear_model import LogisticRegression\n"
        "from sklearn.pipeline import make_pipeline\n"
        "result = make_pipeline(CountVectorizer(), LogisticRegression(max_iter=1000)).fit(texts, labels)"
    ),
    4117: (
        "from sklearn.linear_model import Ridge\n"
        "from sklearn.model_selection import validation_curve\n"
        "train, valid = validation_curve(Ridge(), X, y, param_name='alpha', param_range=alphas, cv=5)\n"
        "result = alphas[int(valid.mean(axis=1).argmax())]"
    ),
    4121: (
        _NE
        + "w = np.linalg.solve(X1.T @ X1, X1.T @ y)\n"
        "result = {'w': w, 'mse': float(np.mean((y - X1 @ w) ** 2))}"
    ),
    4122: (
        "estimates = np.array([np.linalg.lstsq(*draw(), rcond=None)[0] for _ in range(500)])\n"
        "result = {'mean': estimates.mean(axis=0), 'std': estimates.std(axis=0)}"
    ),
    4123: (
        "from sklearn.linear_model import LinearRegression, Ridge\n"
        "ols = np.array([LinearRegression().fit(X[i], y[i]).coef_ for i in boot])\n"
        "ridge = np.array([Ridge(alpha=1.0).fit(X[i], y[i]).coef_ for i in boot])\n"
        "result = {'ols_std': ols.std(axis=0), 'ridge_std': ridge.std(axis=0)}"
    ),
    4124: (
        "from sklearn.linear_model import LogisticRegression\n"
        "model = LogisticRegression().fit(X_train, y_train)\n"
        "z = model.decision_function(X_test)\n"
        "margin = (2 * y_test - 1) * z\n"
        "result = {'proba': 1 / (1 + np.exp(-z)), 'logloss': float(np.log1p(np.exp(-margin)).mean())}"
    ),
    4125: (
        "from sklearn.linear_model import LogisticRegression\n"
        "from sklearn.model_selection import GridSearchCV\n"
        "from sklearn.pipeline import make_pipeline\n"
        "from sklearn.preprocessing import PolynomialFeatures\n"
        "linear = LogisticRegression().fit(X_train, y_train).score(X_test, y_test)\n"
        "grid = GridSearchCV(make_pipeline(PolynomialFeatures(degree=2), LogisticRegression()),\n"
        "                    {'logisticregression__C': [0.01, 0.1, 1, 10]}, cv=5).fit(X_train, y_train)\n"
        "result = {'linear_accuracy': linear, 'best_C': grid.best_params_['logisticregression__C'],\n"
        "          'poly_accuracy': grid.score(X_test, y_test)}"
    ),
    4126: (
        "from sklearn.feature_extraction.text import CountVectorizer\n"
        "from sklearn.linear_model import LogisticRegression\n"
        "from sklearn.model_selection import GridSearchCV, train_test_split\n"
        "from sklearn.pipeline import make_pipeline\n"
        "a, b, c, d = train_test_split(texts, labels, test_size=0.3, random_state=17, stratify=labels)\n"
        "grid = GridSearchCV(make_pipeline(CountVectorizer(), LogisticRegression(max_iter=1000)),\n"
        "                    {'logisticregression__C': [0.1, 1, 10],\n"
        "                     'countvectorizer__ngram_range': [(1, 1), (1, 2)]}, cv=5).fit(a, c)\n"
        "result = {'best_params': grid.best_params_, 'test_accuracy': grid.score(b, d)}"
    ),
    4127: (
        "from sklearn.linear_model import LogisticRegression\n"
        "from sklearn.model_selection import learning_curve\n"
        "from sklearn.pipeline import make_pipeline\n"
        "from sklearn.preprocessing import PolynomialFeatures, StandardScaler\n"
        "pipe = make_pipeline(PolynomialFeatures(degree=4), StandardScaler(),\n"
        "                     LogisticRegression(C=10, max_iter=5000))\n"
        "sizes, train, valid = learning_curve(pipe, X, y, train_sizes=[0.2, 0.5, 1.0], cv=5)\n"
        "result = {'sizes': sizes.tolist(), 'train': train.mean(axis=1).tolist(), 'valid': valid.mean(axis=1).tolist()}\n"
        "gap = float(train.mean(axis=1)[-1] - valid.mean(axis=1)[-1])"
    ),
})

WRONG.update({
    4111: "result = np.linalg.solve(X.T @ X, X.T @ y)",
    4112: "result = (predictions.mean() - truth, predictions.var())",
    4113: (
        "def ridge_weights(X, y, lam):\n"
        "    A = X.T @ X + lam\n"
        "    return np.linalg.solve(A, X.T @ y)\n"
        "result = ridge_weights(X, y, 10.0)"
    ),
    4114: "result = np.log(1 + np.exp(y * z)).mean()",
    4115: (
        "from sklearn.preprocessing import PolynomialFeatures\n"
        "result = PolynomialFeatures(degree=2).fit_transform(X)"
    ),
    4116: (
        "from sklearn.linear_model import LogisticRegression\n"
        "result = LogisticRegression(max_iter=1000)"
    ),
    4117: (
        "from sklearn.linear_model import Ridge\n"
        "from sklearn.model_selection import validation_curve\n"
        "train, valid = validation_curve(Ridge(), X, y, param_name='alpha', param_range=alphas, cv=5)\n"
        "result = alphas[int(train.mean(axis=1).argmax())]"
    ),
    4121: (
        _NE
        + "w = np.linalg.solve(X1.T @ X1, X1.T @ y)\n"
        "result = {'w': w, 'mse': float(np.sum((y - X1 @ w) ** 2))}"
    ),
    4122: (
        "estimates = np.array([np.linalg.lstsq(*draw(), rcond=None)[0] for _ in range(499)])\n"
        "result = {'mean': estimates.mean(axis=0), 'std': estimates.std(axis=0)}"
    ),
    4123: (
        "from sklearn.linear_model import LinearRegression, Ridge\n"
        "ols = np.array([LinearRegression().fit(X[i], y[i]).coef_ for i in boot])\n"
        "ridge = np.array([Ridge(alpha=0.01).fit(X[i], y[i]).coef_ for i in boot])\n"
        "result = {'ols_std': ols.std(axis=0), 'ridge_std': ridge.std(axis=0)}"
    ),
    4124: (
        "from sklearn.linear_model import LogisticRegression\n"
        "model = LogisticRegression().fit(X_train, y_train)\n"
        "z = model.decision_function(X_test)\n"
        "margin = y_test * z\n"
        "result = {'proba': 1 / (1 + np.exp(-z)), 'logloss': float(np.log1p(np.exp(-margin)).mean())}"
    ),
    4125: (
        "from sklearn.linear_model import LogisticRegression\n"
        "from sklearn.model_selection import GridSearchCV\n"
        "from sklearn.pipeline import make_pipeline\n"
        "from sklearn.preprocessing import PolynomialFeatures\n"
        "linear = LogisticRegression().fit(X_train, y_train).score(X_test, y_test)\n"
        "def pipe(C):\n"
        "    return make_pipeline(PolynomialFeatures(degree=2), LogisticRegression(C=C))\n"
        "scores = {C: pipe(C).fit(X_train, y_train).score(X_test, y_test) for C in [0.01, 0.1, 1, 10]}\n"
        "best = max(scores, key=scores.get)\n"
        "result = {'linear_accuracy': linear, 'best_C': best, 'poly_accuracy': scores[best]}"
    ),
    4126: (
        "from sklearn.feature_extraction.text import CountVectorizer\n"
        "from sklearn.linear_model import LogisticRegression\n"
        "from sklearn.model_selection import GridSearchCV, train_test_split\n"
        "from sklearn.pipeline import make_pipeline\n"
        "a, b, c, d = train_test_split(texts, labels, test_size=0.3, random_state=17)\n"
        "grid = GridSearchCV(make_pipeline(CountVectorizer(), LogisticRegression(max_iter=1000)),\n"
        "                    {'logisticregression__C': [0.1, 1, 10],\n"
        "                     'countvectorizer__ngram_range': [(1, 1), (1, 2)]}, cv=5).fit(a, c)\n"
        "result = {'best_params': grid.best_params_, 'test_accuracy': grid.score(b, d)}"
    ),
    4127: (
        "from sklearn.linear_model import LogisticRegression\n"
        "from sklearn.model_selection import learning_curve\n"
        "from sklearn.pipeline import make_pipeline\n"
        "from sklearn.preprocessing import PolynomialFeatures, StandardScaler\n"
        "pipe = make_pipeline(PolynomialFeatures(degree=4), StandardScaler(),\n"
        "                     LogisticRegression(C=0.001, max_iter=5000))\n"
        "sizes, train, valid = learning_curve(pipe, X, y, train_sizes=[0.2, 0.5, 1.0], cv=5)\n"
        "result = {'sizes': sizes.tolist(), 'train': train.mean(axis=1).tolist(), 'valid': valid.mean(axis=1).tolist()}\n"
        "gap = float(train.mean(axis=1)[-1] - valid.mean(axis=1)[-1])"
    ),
})

MORE_WRONG += [
    (
        4111,
        "X1 = np.column_stack([X, np.ones(len(X))])\n"
        "result = np.linalg.solve(X1.T @ X1, X1.T @ y)",
    ),
    (4112, "result = ((predictions.mean() - truth) ** 2, ((predictions - truth) ** 2).mean())"),
    (
        4113,
        "def ridge_weights(X, y, lam):\n"
        "    A = X.T @ X - lam * np.eye(X.shape[1])\n"
        "    return np.linalg.solve(A, X.T @ y)\n"
        "result = ridge_weights(X, y, 10.0)",
    ),
    (4114, "result = np.log1p(np.exp(-z)).mean()"),
    (
        4115,
        "from sklearn.preprocessing import PolynomialFeatures\n"
        "result = PolynomialFeatures(degree=3, include_bias=False).fit_transform(X)",
    ),
    (
        4116,
        "from sklearn.feature_extraction.text import CountVectorizer\n"
        "from sklearn.linear_model import LogisticRegression\n"
        "from sklearn.pipeline import make_pipeline\n"
        "result = make_pipeline(CountVectorizer(), LogisticRegression(max_iter=1000))",
    ),
    (4117, "result = alphas[0]"),
    (
        4121,
        "w = np.linalg.solve(X.T @ X, X.T @ y)\n"
        "result = {'w': w, 'mse': float(np.mean((y - X @ w) ** 2))}",
    ),
    (
        4122,
        "estimates = np.array([np.linalg.lstsq(*draw(), rcond=None)[0] for _ in range(500)])\n"
        "result = {'mean': estimates.mean(axis=0), 'std': estimates.std(axis=0, ddof=1)}",
    ),
    (
        4123,
        "from sklearn.linear_model import LinearRegression, Ridge\n"
        "ols = np.array([LinearRegression().fit(X, y).coef_ for i in boot])\n"
        "ridge = np.array([Ridge(alpha=1.0).fit(X, y).coef_ for i in boot])\n"
        "result = {'ols_std': ols.std(axis=0), 'ridge_std': ridge.std(axis=0)}",
    ),
    (
        4124,
        "from sklearn.linear_model import LogisticRegression\n"
        "model = LogisticRegression().fit(X_train, y_train)\n"
        "z = model.decision_function(X_test)\n"
        "margin = (2 * y_test - 1) * z\n"
        "result = {'proba': model.predict_proba(X_test)[:, 0], 'logloss': float(np.log1p(np.exp(-margin)).mean())}",
    ),
    (
        4125,
        "from sklearn.linear_model import LogisticRegression\n"
        "from sklearn.model_selection import GridSearchCV\n"
        "from sklearn.pipeline import make_pipeline\n"
        "linear = LogisticRegression().fit(X_train, y_train).score(X_test, y_test)\n"
        "grid = GridSearchCV(make_pipeline(LogisticRegression()),\n"
        "                    {'logisticregression__C': [0.01, 0.1, 1, 10]}, cv=5).fit(X_train, y_train)\n"
        "result = {'linear_accuracy': linear, 'best_C': grid.best_params_['logisticregression__C'],\n"
        "          'poly_accuracy': grid.score(X_test, y_test)}",
    ),
    (
        4126,
        "from sklearn.feature_extraction.text import CountVectorizer\n"
        "from sklearn.linear_model import LogisticRegression\n"
        "from sklearn.model_selection import GridSearchCV, train_test_split\n"
        "from sklearn.pipeline import make_pipeline\n"
        "a, b, c, d = train_test_split(texts, labels, test_size=0.3, random_state=17, stratify=labels)\n"
        "grid = GridSearchCV(make_pipeline(CountVectorizer(), LogisticRegression(max_iter=1000)),\n"
        "                    {'logisticregression__C': [0.1, 1, 10]}, cv=5).fit(a, c)\n"
        "result = {'best_params': grid.best_params_, 'test_accuracy': grid.score(b, d)}",
    ),
    (
        4127,
        "from sklearn.linear_model import LogisticRegression\n"
        "from sklearn.model_selection import learning_curve\n"
        "from sklearn.pipeline import make_pipeline\n"
        "from sklearn.preprocessing import PolynomialFeatures, StandardScaler\n"
        "pipe = make_pipeline(PolynomialFeatures(degree=4), StandardScaler(),\n"
        "                     LogisticRegression(C=10, max_iter=5000))\n"
        "sizes, train, valid = learning_curve(pipe, X, y, train_sizes=[0.2, 0.5, 1.0], cv=5)\n"
        "result = {'sizes': sizes.tolist(), 'train': train.mean(axis=1).tolist(), 'valid': valid.mean(axis=1).tolist()}\n"
        "gap = float(train.mean(axis=1)[0] - valid.mean(axis=1)[0])",
    ),
]

ALTERNATIVES += [
    (4111, _NE + "result = np.linalg.inv(X1.T @ X1) @ X1.T @ y"),
    (4111, _NE + "result = np.linalg.lstsq(X1, y, rcond=None)[0]"),
    (
        4112,
        "bias = np.mean(predictions) - truth\n"
        "spread = np.mean((predictions - np.mean(predictions)) ** 2)\n"
        "result = [abs(bias) ** 2, spread]",
    ),
    (
        4113,
        "def ridge_weights(X, y, lam):\n"
        "    return np.linalg.inv(X.T @ X + lam * np.identity(X.shape[1])) @ (X.T @ y)\n"
        "result = ridge_weights(X, y, 10.0)",
    ),
    (4114, "result = float(np.mean(np.logaddexp(0.0, -y * z)))"),
    (
        4115,
        "result = np.column_stack([X[:, 0] ** 2, X[:, 0] * X[:, 1], X[:, 1] ** 2, X[:, 0], X[:, 1]])",
    ),
    (
        4116,
        "from sklearn.feature_extraction.text import CountVectorizer\n"
        "from sklearn.linear_model import LogisticRegression\n"
        "from sklearn.pipeline import Pipeline\n"
        "result = Pipeline([('countvectorizer', CountVectorizer()),\n"
        "                   ('logisticregression', LogisticRegression(max_iter=1000))])\n"
        "result.fit(texts, labels)",
    ),
    (
        4117,
        "from sklearn.linear_model import Ridge\n"
        "from sklearn.model_selection import cross_val_score\n"
        "scores = [cross_val_score(Ridge(alpha=a), X, y, cv=5).mean() for a in alphas]\n"
        "result = alphas[scores.index(max(scores))]",
    ),
    (
        4121,
        "from sklearn.metrics import mean_squared_error\n"
        "w = np.linalg.lstsq(np.c_[np.ones(len(X)), X], y, rcond=None)[0]\n"
        "pred = w[0] + X @ w[1:]\n"
        "result = {'w': list(w), 'mse': mean_squared_error(y, pred)}",
    ),
    (
        4122,
        "weights = []\n"
        "for _ in range(500):\n"
        "    X_, y_ = draw()\n"
        "    weights.append(np.linalg.solve(X_.T @ X_, X_.T @ y_))\n"
        "weights = np.array(weights)\n"
        "result = {'mean': np.mean(weights, axis=0), 'std': np.std(weights, axis=0)}",
    ),
    (
        4124,
        "from scipy.special import expit\n"
        "from sklearn.linear_model import LogisticRegression\n"
        "model = LogisticRegression().fit(X_train, y_train)\n"
        "z = model.decision_function(X_test)\n"
        "signed = np.where(y_test == 1, 1, -1)\n"
        "result = {'proba': expit(z), 'logloss': float(np.mean(np.logaddexp(0, -signed * z)))}",
    ),
    (
        4125,
        "from sklearn.linear_model import LogisticRegression\n"
        "from sklearn.model_selection import cross_val_score\n"
        "from sklearn.pipeline import make_pipeline\n"
        "from sklearn.preprocessing import PolynomialFeatures\n"
        "def pipe(C):\n"
        "    return make_pipeline(PolynomialFeatures(degree=2), LogisticRegression(C=C))\n"
        "Cs = [0.01, 0.1, 1, 10]\n"
        "best = max(Cs, key=lambda C: cross_val_score(pipe(C), X_train, y_train, cv=5).mean())\n"
        "result = {'linear_accuracy': LogisticRegression().fit(X_train, y_train).score(X_test, y_test),\n"
        "          'best_C': best,\n"
        "          'poly_accuracy': pipe(best).fit(X_train, y_train).score(X_test, y_test)}",
    ),
]

# Модуль 5 (срез 61): задания внутри занятий (5111–5116) и итоговые (5121–5126).
_IMP = (
    "from sklearn.ensemble import RandomForestClassifier\n"
    "from sklearn.inspection import permutation_importance\n"
)

SOLUTIONS.update({
    5111: (
        "shares = []\n"
        "for _ in range(200):\n"
        "    idx = rng.choice(n, size=n, replace=True)\n"
        "    shares.append(len(np.unique(idx)) / n)\n"
        "result = float(np.mean(shares))"
    ),
    5112: (
        "from sklearn.ensemble import RandomForestClassifier\n"
        "forest = RandomForestClassifier(n_estimators=50, oob_score=True, random_state=17).fit(X_train, y_train)\n"
        "result = (forest.oob_score_, forest.score(X_test, y_test))"
    ),
    5113: (
        "from sklearn.ensemble import ExtraTreesClassifier, RandomForestClassifier\n"
        "from sklearn.model_selection import cross_val_score\n"
        "result = {\n"
        "    'forest': cross_val_score(RandomForestClassifier(n_estimators=50, random_state=17), X, y, cv=5).mean(),\n"
        "    'extra': cross_val_score(ExtraTreesClassifier(n_estimators=50, random_state=17), X, y, cv=5).mean(),\n"
        "}"
    ),
    5114: (
        "from sklearn.ensemble import RandomForestClassifier\n"
        "scores = {}\n"
        "for m in [1, 2, 4, 8]:\n"
        "    forest = RandomForestClassifier(n_estimators=50, oob_score=True, random_state=17, max_features=m)\n"
        "    scores[m] = forest.fit(X, y).oob_score_\n"
        "result = max(scores, key=scores.get)"
    ),
    5115: (
        "leaves = forest.apply(X)\n"
        "similarity = (leaves == leaves[0]).mean(axis=1)\n"
        "similarity[0] = -1\n"
        "result = int(similarity.argmax())"
    ),
    5116: "result = pd.Series(forest.feature_importances_, index=X.columns).sort_values(ascending=False)",
    5121: (
        "from sklearn.tree import DecisionTreeClassifier\n"
        "single = DecisionTreeClassifier(random_state=17).fit(X_train, y_train).score(X_test, y_test)\n"
        "votes = np.array([\n"
        "    DecisionTreeClassifier(random_state=17).fit(X_train[i], y_train[i]).predict(X_test) for i in boot\n"
        "])\n"
        "result = {'single': single, 'bagging': float(((votes.mean(axis=0) > 0.5).astype(int) == y_test).mean())}"
    ),
    5122: (
        "from sklearn.ensemble import RandomForestClassifier\n"
        "forest = RandomForestClassifier(n_estimators=60, max_features='sqrt', oob_score=True, random_state=17)\n"
        "forest.fit(X_train, y_train)\n"
        "result = {\n"
        "    'oob': forest.oob_score_,\n"
        "    'test': forest.score(X_test, y_test),\n"
        "    'm': forest.estimators_[0].max_features_,\n"
        "    'rule': int(np.sqrt(X_train.shape[1])),\n"
        "}"
    ),
    5123: (
        "from sklearn.ensemble import RandomForestClassifier\n"
        "sizes = [1, 5, 20, 60]\n"
        "result = [RandomForestClassifier(n_estimators=b, random_state=17).fit(X_train, y_train).score(X_test, y_test)\n"
        "          for b in sizes]\n"
        "plateau = next(b for b, v in zip(sizes, result) if v >= max(result) - 0.01)"
    ),
    5124: (
        "from sklearn.ensemble import RandomForestClassifier\n"
        "from sklearn.model_selection import GridSearchCV\n"
        "grid = GridSearchCV(RandomForestClassifier(n_estimators=30, random_state=17),\n"
        "                    {'max_features': [2, 8], 'min_samples_leaf': [1, 10]}, cv=3).fit(X_train, y_train)\n"
        "result = {'best_params': grid.best_params_, 'test_score': grid.score(X_test, y_test)}"
    ),
    5125: (
        "from sklearn.ensemble import RandomTreesEmbedding\n"
        "from sklearn.linear_model import LogisticRegression\n"
        "from sklearn.pipeline import make_pipeline\n"
        "linear = LogisticRegression(max_iter=1000).fit(X_train, y_train).score(X_test, y_test)\n"
        "stacked = make_pipeline(RandomTreesEmbedding(n_estimators=30, max_depth=3, random_state=17),\n"
        "                        LogisticRegression(max_iter=1000)).fit(X_train, y_train)\n"
        "result = {'linear': linear, 'embedding': stacked.score(X_test, y_test)}"
    ),
    5126: (
        _IMP
        + "forest = RandomForestClassifier(n_estimators=60, random_state=17).fit(X_train, y_train)\n"
        "perm = permutation_importance(forest, X_test, y_test, n_repeats=5, random_state=17)\n"
        "result = {\n"
        "    'sum': forest.feature_importances_.sum(),\n"
        "    'impurity_top': int(np.argmax(forest.feature_importances_)),\n"
        "    'permutation_top': int(np.argmax(perm.importances_mean)),\n"
        "}"
    ),
})

WRONG.update({
    5111: (
        "shares = []\n"
        "for _ in range(200):\n"
        "    idx = rng.choice(n, size=n, replace=False)\n"
        "    shares.append(len(np.unique(idx)) / n)\n"
        "result = float(np.mean(shares))"
    ),
    5112: (
        "from sklearn.ensemble import RandomForestClassifier\n"
        "forest = RandomForestClassifier(n_estimators=50, oob_score=True, random_state=17).fit(X_train, y_train)\n"
        "result = (forest.score(X_train, y_train), forest.score(X_test, y_test))"
    ),
    5113: (
        "from sklearn.ensemble import RandomForestClassifier\n"
        "from sklearn.model_selection import cross_val_score\n"
        "score = cross_val_score(RandomForestClassifier(n_estimators=50, random_state=17), X, y, cv=5).mean()\n"
        "result = {'forest': score, 'extra': score}"
    ),
    5114: (
        "from sklearn.ensemble import RandomForestClassifier\n"
        "scores = {}\n"
        "for m in [1, 2, 4, 8]:\n"
        "    forest = RandomForestClassifier(n_estimators=50, oob_score=True, random_state=17, max_features=m)\n"
        "    scores[m] = forest.fit(X, y).score(X, y)\n"
        "result = max(scores, key=scores.get)"
    ),
    5115: (
        "leaves = forest.apply(X)\n"
        "similarity = (leaves == leaves[0]).mean(axis=1)\n"
        "result = int(similarity.argmax())"
    ),
    5116: "result = pd.Series(forest.feature_importances_, index=X.columns)",
    5121: (
        "from sklearn.tree import DecisionTreeClassifier\n"
        "single = DecisionTreeClassifier(random_state=17).fit(X_train, y_train).score(X_test, y_test)\n"
        "votes = np.array([\n"
        "    DecisionTreeClassifier(random_state=17).fit(X_train, y_train).predict(X_test) for i in boot\n"
        "])\n"
        "result = {'single': single, 'bagging': float(((votes.mean(axis=0) > 0.5).astype(int) == y_test).mean())}"
    ),
    5122: (
        "from sklearn.ensemble import RandomForestClassifier\n"
        "forest = RandomForestClassifier(n_estimators=60, max_features='sqrt', oob_score=True, random_state=17)\n"
        "forest.fit(X_train, y_train)\n"
        "result = {\n"
        "    'oob': forest.oob_score_,\n"
        "    'test': forest.score(X_test, y_test),\n"
        "    'm': forest.estimators_[0].max_features_,\n"
        "    'rule': round(np.sqrt(X_train.shape[1])),\n"
        "}"
    ),
    5123: (
        "from sklearn.ensemble import RandomForestClassifier\n"
        "sizes = [1, 5, 20, 60]\n"
        "result = [RandomForestClassifier(n_estimators=b, random_state=17).fit(X_train, y_train).score(X_train, y_train)\n"
        "          for b in sizes]\n"
        "plateau = next(b for b, v in zip(sizes, result) if v >= max(result) - 0.01)"
    ),
    5124: (
        "from sklearn.ensemble import RandomForestClassifier\n"
        "from sklearn.model_selection import GridSearchCV\n"
        "grid = GridSearchCV(RandomForestClassifier(n_estimators=30, random_state=17),\n"
        "                    {'max_features': [2, 8], 'min_samples_leaf': [1, 10]}, cv=3).fit(X_train, y_train)\n"
        "result = {'best_params': grid.best_params_, 'test_score': grid.score(X_train, y_train)}"
    ),
    5125: (
        "from sklearn.ensemble import RandomTreesEmbedding\n"
        "from sklearn.linear_model import LogisticRegression\n"
        "from sklearn.pipeline import make_pipeline\n"
        "linear = LogisticRegression(max_iter=1000).fit(X_train, y_train).score(X_test, y_test)\n"
        "stacked = make_pipeline(RandomTreesEmbedding(n_estimators=30, max_depth=1, random_state=17),\n"
        "                        LogisticRegression(max_iter=1000)).fit(X_train, y_train)\n"
        "result = {'linear': linear, 'embedding': stacked.score(X_test, y_test)}"
    ),
    5126: (
        _IMP
        + "forest = RandomForestClassifier(n_estimators=60, random_state=17).fit(X_train, y_train)\n"
        "perm = permutation_importance(forest, X_train, y_train, n_repeats=5, random_state=17)\n"
        "result = {\n"
        "    'sum': forest.feature_importances_.sum(),\n"
        "    'impurity_top': int(np.argmax(forest.feature_importances_)),\n"
        "    'permutation_top': int(np.argmax(perm.importances_mean)),\n"
        "}"
    ),
})

MORE_WRONG += [
    (
        5111,
        "shares = []\n"
        "for _ in range(200):\n"
        "    idx = rng.choice(n, size=n // 2, replace=True)\n"
        "    shares.append(len(np.unique(idx)) / n)\n"
        "result = float(np.mean(shares))",
    ),
    (5111, "result = 0.632"),
    (
        5112,
        "from sklearn.ensemble import RandomForestClassifier\n"
        "forest = RandomForestClassifier(n_estimators=100, oob_score=True, random_state=17).fit(X_train, y_train)\n"
        "result = (forest.oob_score_, forest.score(X_test, y_test))",
    ),
    (
        5113,
        "from sklearn.ensemble import ExtraTreesClassifier, RandomForestClassifier\n"
        "from sklearn.model_selection import cross_val_score\n"
        "result = {\n"
        "    'forest': cross_val_score(RandomForestClassifier(n_estimators=50, random_state=17), X, y, cv=3).mean(),\n"
        "    'extra': cross_val_score(ExtraTreesClassifier(n_estimators=50, random_state=17), X, y, cv=3).mean(),\n"
        "}",
    ),
    (
        5114,
        "from sklearn.ensemble import RandomForestClassifier\n"
        "scores = {}\n"
        "for m in [1, 2, 4, 8]:\n"
        "    forest = RandomForestClassifier(n_estimators=50, oob_score=True, random_state=17, max_features=m)\n"
        "    scores[m] = forest.fit(X, y).oob_score_\n"
        "result = min(scores, key=scores.get)",
    ),
    (
        5115,
        "import numpy as np\n"
        "result = int(np.argsort(np.linalg.norm(X - X[0], axis=1))[1])",
    ),
    (5116, "result = pd.Series(forest.feature_importances_, index=X.columns).sort_values()"),
    (
        5121,
        "from sklearn.tree import DecisionTreeClassifier\n"
        "single = DecisionTreeClassifier(random_state=17).fit(X_train, y_train).score(X_test, y_test)\n"
        "votes = np.array([\n"
        "    DecisionTreeClassifier(random_state=17).fit(X_train[i], y_train[i]).predict(X_test) for i in boot\n"
        "])\n"
        "result = {'single': single, 'bagging': float((votes.max(axis=0) == y_test).mean())}",
    ),
    (
        5122,
        "from sklearn.ensemble import RandomForestClassifier\n"
        "forest = RandomForestClassifier(n_estimators=60, max_features=None, oob_score=True, random_state=17)\n"
        "forest.fit(X_train, y_train)\n"
        "result = {\n"
        "    'oob': forest.oob_score_,\n"
        "    'test': forest.score(X_test, y_test),\n"
        "    'm': forest.estimators_[0].max_features_,\n"
        "    'rule': int(np.sqrt(X_train.shape[1])),\n"
        "}",
    ),
    (
        5123,
        "from sklearn.ensemble import RandomForestClassifier\n"
        "sizes = [1, 5, 20, 60]\n"
        "result = [RandomForestClassifier(n_estimators=b, random_state=17).fit(X_train, y_train).score(X_test, y_test)\n"
        "          for b in sizes]\n"
        "plateau = sizes[-1]",
    ),
    (
        5124,
        "from sklearn.ensemble import RandomForestClassifier\n"
        "from sklearn.model_selection import GridSearchCV\n"
        "grid = GridSearchCV(RandomForestClassifier(n_estimators=30, random_state=17),\n"
        "                    {'max_features': [2, 8], 'min_samples_leaf': [1, 10]}, cv=3).fit(X_train, y_train)\n"
        "result = {'best_params': grid.best_params_, 'test_score': grid.best_score_}",
    ),
    (
        5125,
        "from sklearn.ensemble import RandomTreesEmbedding\n"
        "from sklearn.linear_model import LogisticRegression\n"
        "from sklearn.pipeline import make_pipeline\n"
        "linear = LogisticRegression(max_iter=1000).fit(X_train, y_train).score(X_train, y_train)\n"
        "stacked = make_pipeline(RandomTreesEmbedding(n_estimators=30, max_depth=3, random_state=17),\n"
        "                        LogisticRegression(max_iter=1000)).fit(X_train, y_train)\n"
        "result = {'linear': linear, 'embedding': stacked.score(X_test, y_test)}",
    ),
    (
        5126,
        _IMP
        + "forest = RandomForestClassifier(n_estimators=60, random_state=17).fit(X_train, y_train)\n"
        "perm = permutation_importance(forest, X_test, y_test, n_repeats=5, random_state=17)\n"
        "result = {\n"
        "    'sum': perm.importances_mean.sum(),\n"
        "    'impurity_top': int(np.argmax(forest.feature_importances_)),\n"
        "    'permutation_top': int(np.argmax(perm.importances_mean)),\n"
        "}",
    ),
]

ALTERNATIVES += [
    (
        5111,
        "shares = [len(set(rng.integers(0, n, n).tolist())) / n for _ in range(200)]\n"
        "result = sum(shares) / len(shares)",
    ),
    (
        5112,
        "from sklearn.ensemble import RandomForestClassifier\n"
        "from sklearn.metrics import accuracy_score\n"
        "forest = RandomForestClassifier(n_estimators=50, oob_score=True, random_state=17)\n"
        "forest.fit(X_train, y_train)\n"
        "oob = forest.oob_score_\n"
        "test = accuracy_score(y_test, forest.predict(X_test))\n"
        "result = (oob, test)",
    ),
    (
        5114,
        "from sklearn.ensemble import RandomForestClassifier\n"
        "def oob(m):\n"
        "    return RandomForestClassifier(n_estimators=50, oob_score=True, random_state=17,\n"
        "                                  max_features=m).fit(X, y).oob_score_\n"
        "result = max([1, 2, 4, 8], key=oob)",
    ),
    (
        5115,
        "import numpy as np\n"
        "leaves = forest.apply(X)\n"
        "similarity = np.array([(leaves[i] == leaves[0]).mean() for i in range(len(X))])\n"
        "result = int(np.argsort(-similarity)[1])",
    ),
    (
        5116,
        "importances = dict(zip(X.columns, forest.feature_importances_))\n"
        "ordered = sorted(importances, key=importances.get, reverse=True)\n"
        "result = pd.Series([importances[c] for c in ordered], index=ordered)",
    ),
    (
        5121,
        "from sklearn.tree import DecisionTreeClassifier\n"
        "single = DecisionTreeClassifier(random_state=17).fit(X_train, y_train).score(X_test, y_test)\n"
        "trees = [DecisionTreeClassifier(random_state=17).fit(X_train[i], y_train[i]) for i in boot]\n"
        "ensemble = (np.sum([t.predict(X_test) for t in trees], axis=0) > len(trees) / 2).astype(int)\n"
        "result = {'single': single, 'bagging': float(np.mean(ensemble == y_test))}",
    ),
    (
        5123,
        "from sklearn.ensemble import RandomForestClassifier\n"
        "result = []\n"
        "for b in [1, 5, 20, 60]:\n"
        "    result.append(RandomForestClassifier(n_estimators=b, random_state=17).fit(X_train, y_train).score(X_test, y_test))\n"
        "limit = max(result) - 0.01\n"
        "plateau = [1, 5, 20, 60][[v >= limit for v in result].index(True)]",
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
