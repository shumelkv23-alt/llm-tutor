"""Проверка запуском: загвоздки «Объясни новичку» ложны, а их исправления верны (срез 90).

Загвоздка (belief) — ошибка, которую новичок «понимает» и которую ученик должен
заметить. Если она про поведение библиотеки, её проверяют кодом: трижды
«ложный» вариант заданий оказывался верным (CLAUDE.md). Каждая проверка ниже
утверждает исправление (fix) и тем самым опровергает загвоздку.

Запуск: uv run --with numpy,pandas,scikit-learn,matplotlib python scripts/check_explain_claims.py
"""

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from pandas.core.groupby import DataFrameGroupBy  # noqa: E402
from sklearn.datasets import make_classification  # noqa: E402
from sklearn.ensemble import RandomForestClassifier  # noqa: E402
from sklearn.linear_model import Lasso, LogisticRegression, Ridge  # noqa: E402
from sklearn.model_selection import train_test_split  # noqa: E402
from sklearn.neighbors import NearestNeighbors  # noqa: E402
from sklearn.preprocessing import StandardScaler  # noqa: E402
from sklearn.tree import DecisionTreeClassifier  # noqa: E402

CHECKS = []


def check(pitfall_id: str):
    def register(func):
        CHECKS.append((pitfall_id, func))
        return func

    return register


# --- Модуль 1: pandas ---


@check("iloc_by_label")
def _() -> None:
    df = pd.DataFrame({"x": [10, 20, 30, 40, 50, 60]}, index=[5, 4, 3, 2, 1, 0])
    assert df.iloc[5]["x"] == 60  # шестая позиция
    assert df.loc[5]["x"] == 10  # метка 5
    assert df.iloc[5]["x"] != df.loc[5]["x"]


@check("groupby_returns_table")
def _() -> None:
    df = pd.DataFrame({"churn": [0, 1, 0], "calls": [1, 5, 2]})
    grouped = df.groupby("churn")
    assert isinstance(grouped, DataFrameGroupBy)
    assert not isinstance(grouped, pd.DataFrame)
    assert isinstance(grouped.mean(), pd.DataFrame)


@check("loc_slice_exclusive")
def _() -> None:
    df = pd.DataFrame({"x": range(5)})
    assert list(df.loc[0:2].index) == [0, 1, 2]
    assert list(df.iloc[0:2].index) == [0, 1]


@check("describe_all_columns")
def _() -> None:
    df = pd.DataFrame({"age": [20, 30, 40], "city": ["a", "b", "a"]})
    assert list(df.describe().columns) == ["age"]
    assert "city" in df.describe(include="object").columns


# --- Модуль 2: визуализация ---


@check("corr_zero_no_link")
def _() -> None:
    x = np.linspace(-1, 1, 201)
    y = x**2  # полная, но нелинейная зависимость
    assert abs(np.corrcoef(x, y)[0, 1]) < 1e-9


@check("box_line_is_mean")
def _() -> None:
    data = np.array([1, 2, 3, 4, 100])
    parts = plt.boxplot(data)
    assert parts["means"] == []  # среднего по умолчанию нет
    assert parts["medians"][0].get_ydata()[0] == np.median(data) == 3
    assert np.mean(data) != np.median(data)
    plt.close("all")


# --- Модуль 3: деревья и kNN ---


@check("perfect_train_is_best")
def _() -> None:
    X, y = make_classification(n_samples=600, n_features=10, flip_y=0.2, random_state=0)
    X_train, X_test, y_train, y_test = train_test_split(X, y, random_state=0)
    tree = DecisionTreeClassifier(random_state=0).fit(X_train, y_train)
    assert tree.score(X_train, y_train) == 1.0
    assert tree.score(X_test, y_test) < 0.9


@check("knn_scale_irrelevant")
def _() -> None:
    rng = np.random.default_rng(0)
    X = np.column_stack([rng.normal(0, 1, 50), rng.normal(0, 1000, 50)])
    raw = NearestNeighbors(n_neighbors=5).fit(X).kneighbors(X[:1], return_distance=False)
    scaled_X = StandardScaler().fit_transform(X)
    scaled = NearestNeighbors(n_neighbors=5).fit(scaled_X).kneighbors(
        scaled_X[:1], return_distance=False
    )
    assert set(raw[0]) != set(scaled[0])


# --- Модуль 4: линейные модели ---


@check("bigger_c_stronger_reg")
def _() -> None:
    X, y = make_classification(n_samples=300, n_features=5, random_state=0)
    weak = LogisticRegression(C=100.0, max_iter=1000).fit(X, y)
    strong = LogisticRegression(C=0.01, max_iter=1000).fit(X, y)
    assert np.linalg.norm(weak.coef_) > np.linalg.norm(strong.coef_)


@check("logreg_predicts_number")
def _() -> None:
    X, y = make_classification(n_samples=200, n_features=4, random_state=0)
    model = LogisticRegression().fit(X, y)
    assert set(np.unique(model.predict(X))) <= {0, 1}
    proba = model.predict_proba(X[:5])
    assert np.allclose(proba.sum(axis=1), 1.0)


@check("ridge_zeroes_weights")
def _() -> None:
    rng = np.random.default_rng(0)
    X = rng.normal(size=(200, 10))
    y = X[:, 0] * 3 + rng.normal(size=200)
    assert np.count_nonzero(Ridge(alpha=10.0).fit(X, y).coef_ == 0) == 0
    assert np.count_nonzero(Lasso(alpha=0.5).fit(X, y).coef_ == 0) > 0


# --- Модуль 5: бэггинг и лес ---


@check("many_trees_overfit")
def _() -> None:
    X, y = make_classification(n_samples=600, n_features=10, flip_y=0.1, random_state=0)
    X_train, X_test, y_train, y_test = train_test_split(X, y, random_state=0)
    few = RandomForestClassifier(n_estimators=50, random_state=0).fit(X_train, y_train)
    many = RandomForestClassifier(n_estimators=500, random_state=0).fit(X_train, y_train)
    assert many.score(X_test, y_test) >= few.score(X_test, y_test) - 0.02


@check("bootstrap_is_shuffle")
def _() -> None:
    rng = np.random.default_rng(0)
    n = 100_000
    sample = rng.integers(0, n, size=n)
    share = len(np.unique(sample)) / n
    assert abs(share - (1 - np.exp(-1))) < 0.01  # ≈ 63% уникальных, ≈ 37% не попали


@check("forest_needs_scaling")
def _() -> None:
    X, y = make_classification(n_samples=300, n_features=6, random_state=0)
    scaled = StandardScaler().fit_transform(X)
    raw = RandomForestClassifier(n_estimators=50, random_state=0).fit(X, y).predict(X)
    after = RandomForestClassifier(n_estimators=50, random_state=0).fit(scaled, y).predict(scaled)
    assert np.mean(raw == after) > 0.99


def main() -> None:
    for pitfall_id, func in CHECKS:
        func()
        print(f"ok  {pitfall_id}")
    print(f"OK: {len(CHECKS)} утверждений")


if __name__ == "__main__":
    main()
