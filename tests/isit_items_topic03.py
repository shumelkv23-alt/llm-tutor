"""Задания с кодом модуля 3 курса ИСИТ (линейная регрессия).

Таблицы для `tests/test_isit_code_items.py`: эталон, верная альтернатива и
ловушки на каждое задание занятий `metrics` (3001, 3003, 3005, 3007, 3009),
`ols` (3011, 3013, 3015, 3017, 3019) и `regularization` (3021, 3023, 3024,
3026, 3027, 3029).

`WRONG` — по одной главной ловушке на задание (это словарь: вторая запись с
тем же id молча затрёт первую), остальные варианты — в `MORE_WRONG`, а те, что
падают ошибкой Python без подсказки, — в `CRASHES`. Все эталоны считаются на
данных из `setup` задания, а не на числах из ноутбука лабораторной.
"""

import numpy as np

SOLUTIONS = {
    # --- Занятие metrics: MSE, MAE и R² ---
    # Среднее квадратов, а не сумма: метрика не зависит от длины выборки.
    3001: "def mse(y_true, y_pred):\n"
    '    """Средний квадрат ошибки."""\n'
    "    y_true = np.asarray(y_true, dtype=float)\n"
    "    y_pred = np.asarray(y_pred, dtype=float)\n"
    "    return float(np.mean((y_true - y_pred) ** 2))\n\n\n"
    "result = (\n"
    "    round(float(mse([1.0, 2.0, 3.0], [1.1, 1.9, 3.2])), 4),\n"
    "    round(float(mse(y, np.full_like(y, y.mean()))), 4),\n"
    ")",
    # Модуль разности: ошибки не гасят друг друга и не растут как квадрат.
    3003: "def mae(y_true, y_pred):\n"
    '    """Средняя абсолютная ошибка."""\n'
    "    y_true = np.asarray(y_true, dtype=float)\n"
    "    y_pred = np.asarray(y_pred, dtype=float)\n"
    "    return float(np.mean(np.abs(y_true - y_pred)))\n\n\n"
    "result = (\n"
    "    round(float(mae([1.0, 2.0, 3.0], [1.1, 1.9, 3.2])), 4),\n"
    "    round(float(mae(y, y + 2.0)), 4),\n"
    "    round(float(mae(np.array([1.0, 2.0, 3.0, 100.0]), np.array([1.0, 2.0, 3.0, 60.0]))), 4),\n"
    ")",
    # R² = 1 − RSS/TSS: разброс цели считается вокруг её среднего.
    3005: "def r2(y_true, y_pred):\n"
    '    """Доля объяснённого разброса."""\n'
    "    y_true = np.asarray(y_true, dtype=float)\n"
    "    y_pred = np.asarray(y_pred, dtype=float)\n"
    "    rss = float(np.sum((y_true - y_pred) ** 2))\n"
    "    tss = float(np.sum((y_true - y_true.mean()) ** 2))\n"
    "    return float(1 - rss / tss)\n\n\n"
    "tiny = np.array([1.0, 2.0, 3.0])\n"
    "result = (\n"
    "    round(float(r2(tiny, tiny)), 4),\n"
    "    round(float(r2(tiny, np.full(3, 2.0))), 4),\n"
    "    round(float(r2(tiny, np.full(3, 3.0))), 4),\n"
    "    round(float(r2(y, predictions)), 4),\n"
    ")",
    # Базовая ошибка — дисперсия цели; R² через ошибки совпадает с формулой сумм.
    3007: "def baseline_mse(y_true):\n"
    '    """Ошибка прогноза средним."""\n'
    "    y_true = np.asarray(y_true, dtype=float)\n"
    "    return float(np.mean((y_true - y_true.mean()) ** 2))\n\n\n"
    "def r2_by_mse(y_true, y_pred):\n"
    '    """R² через MSE модели и MSE прогноза средним."""\n'
    "    y_true = np.asarray(y_true, dtype=float)\n"
    "    y_pred = np.asarray(y_pred, dtype=float)\n"
    "    model_mse = float(np.mean((y_true - y_pred) ** 2))\n"
    "    return float(1 - model_mse / baseline_mse(y_true))\n\n\n"
    "result = (\n"
    "    round(float(baseline_mse(y)), 4),\n"
    "    round(float(r2_by_mse(y, predictions)), 4),\n"
    "    round(float(r2_by_mse(y, np.full_like(y, y.mean()))), 4),\n"
    ")",
    # Три метрики считаются внутри функции по одним и тем же аргументам.
    3009: "def regression_report(y_true, y_pred):\n"
    '    """Словарь с mse, mae и r2."""\n'
    "    y_true = np.asarray(y_true, dtype=float)\n"
    "    y_pred = np.asarray(y_pred, dtype=float)\n"
    "    errors = y_true - y_pred\n"
    "    tss = float(np.sum((y_true - y_true.mean()) ** 2))\n"
    "    return {\n"
    '        "mse": float(np.mean(errors ** 2)),\n'
    '        "mae": float(np.mean(np.abs(errors))),\n'
    '        "r2": float(1 - np.sum(errors ** 2) / tss),\n'
    "    }\n\n\n"
    "def rounded(report):\n"
    '    """Тот же отчёт, округлённый до четырёх знаков."""\n'
    "    return {key: round(value, 4) for key, value in report.items()}\n\n\n"
    "result = (\n"
    "    rounded(regression_report(y, predictions)),\n"
    "    rounded(regression_report(np.array([1.0, 2.0, 3.0]), np.array([2.0, 2.0, 2.0]))),\n"
    ")",
    # --- Занятие ols: нормальное уравнение и своя модель ---
    # Столбец единиц — новый массив; одномерный вход сначала становится столбцом.
    3011: "def add_bias_column(X):\n"
    '    """Столбец единиц слева; исходный массив не меняется."""\n'
    "    X = np.asarray(X, dtype=float)\n"
    "    if X.ndim == 1:\n"
    "        X = X.reshape(-1, 1)\n"
    "    return np.hstack([np.ones((X.shape[0], 1)), X])\n\n\n"
    "result = (\n"
    "    np.round(add_bias_column(X[:2]), 4).tolist(),\n"
    "    add_bias_column(np.arange(5)).tolist(),\n"
    "    X.shape,\n"
    ")",
    # Нормальное уравнение на матрице со столбцом единиц; y приводится к столбцу.
    3013: "def fit_normal_equation(X, y):\n"
    '    """Веса линейной регрессии по нормальному уравнению."""\n'
    "    X = np.asarray(X, dtype=float)\n"
    "    y = np.asarray(y, dtype=float).reshape(-1, 1)\n"
    "    X_b = np.hstack([np.ones((X.shape[0], 1)), X])\n"
    "    return np.linalg.inv(X_b.T @ X_b) @ X_b.T @ y\n\n\n"
    "X_b = np.hstack([np.ones((X.shape[0], 1)), X])\n"
    "weights = fit_normal_equation(X, y)\n"
    "result = (\n"
    "    np.round(weights.ravel(), 4).tolist(),\n"
    "    round(float(np.mean((y - X_b @ weights) ** 2)), 4),\n"
    "    X_b.shape,\n"
    ")",
    # Своя модель: веса столбцом, predict до fit объясняет, что модель не обучена.
    3015: "class MyLinearRegression:\n"
    '    """Линейная регрессия, обученная нормальным уравнением."""\n'
    "\n"
    "    def __init__(self):\n"
    "        self.weights_ = None\n"
    "\n"
    "    def fit(self, X, y):\n"
    "        X = np.asarray(X, dtype=float)\n"
    "        if X.ndim == 1:\n"
    "            X = X.reshape(-1, 1)\n"
    "        y = np.asarray(y, dtype=float).reshape(-1, 1)\n"
    "        X_b = np.hstack([np.ones((X.shape[0], 1)), X])\n"
    "        self.weights_ = np.linalg.pinv(X_b.T @ X_b) @ X_b.T @ y\n"
    "        return self\n"
    "\n"
    "    def predict(self, X):\n"
    "        if self.weights_ is None:\n"
    '            raise ValueError("Сначала обучите модель: fit(X, y)")\n'
    "        # Одномерный вход — признаки одного объекта, а не столбец из строк.\n"
    "        X = np.atleast_2d(np.asarray(X, dtype=float))\n"
    "        X_b = np.hstack([np.ones((X.shape[0], 1)), X])\n"
    "        return X_b @ self.weights_\n\n\n"
    "model = MyLinearRegression().fit(X, y)\n"
    "result = (\n"
    "    np.round(model.weights_.ravel(), 4).tolist(),\n"
    "    np.round(model.predict(X[:3]).ravel(), 4).tolist(),\n"
    "    round(float(np.mean((y - model.predict(X)) ** 2)), 4),\n"
    ")",
    # pinv не падает на дубле столбца: сравнение со sklearn и проверка вырожденности.
    3017: "def weight_vectors(X, y):\n"
    '    """Пара векторов весов: свой из нормального уравнения и sklearn-вектор."""\n'
    "    X = np.asarray(X, dtype=float)\n"
    "    y = np.asarray(y, dtype=float).reshape(-1, 1)\n"
    "    X_b = np.hstack([np.ones((X.shape[0], 1)), X])\n"
    "    own = np.linalg.pinv(X_b.T @ X_b) @ X_b.T @ y\n"
    "    model = LinearRegression().fit(X, y)\n"
    "    sk = np.r_[np.ravel(model.intercept_), np.ravel(model.coef_)]\n"
    "    return own.ravel(), sk\n\n\n"
    "def cannot_be_inverted(A):\n"
    '    """True, если A.T @ A не обращается."""\n'
    "    A = np.asarray(A, dtype=float)\n"
    "    try:\n"
    "        np.linalg.inv(A.T @ A)\n"
    "    except np.linalg.LinAlgError:\n"
    "        return True\n"
    "    return False\n\n\n"
    "duplicated = np.hstack([X, X[:, :1]])\n"
    "result = (\n"
    "    bool(np.allclose(*weight_vectors(X, y), atol=1e-10)),\n"
    "    bool(np.allclose(*weight_vectors(duplicated, y), atol=1e-10)),\n"
    "    cannot_be_inverted(duplicated),\n"
    "    cannot_be_inverted(X),\n"
    ")",
    # Итоговое: веса только по обучающей части, ошибки на обеих выборках.
    3019: "def fit_and_test(X, y, n_train):\n"
    '    """Веса и две ошибки: на обучении и на тесте."""\n'
    "    X = np.asarray(X, dtype=float)\n"
    "    y = np.asarray(y, dtype=float).reshape(-1, 1)\n"
    "    X_train, y_train = X[:n_train], y[:n_train]\n"
    "    X_test, y_test = X[n_train:], y[n_train:]\n"
    "    X_b = np.hstack([np.ones((X_train.shape[0], 1)), X_train])\n"
    "    weights = np.linalg.inv(X_b.T @ X_b) @ X_b.T @ y_train\n"
    "    X_b_test = np.hstack([np.ones((X_test.shape[0], 1)), X_test])\n"
    "    train_mse = float(np.mean((y_train - X_b @ weights) ** 2))\n"
    "    test_mse = float(np.mean((y_test - X_b_test @ weights) ** 2))\n"
    "    return weights, train_mse, test_mse\n\n\n"
    "weights, train_mse, test_mse = fit_and_test(X, y, 35)\n"
    "result = (np.round(weights.ravel(), 4).tolist(), round(train_mse, 4), round(test_mse, 4))",
    # --- Занятие regularization: Ridge и Lasso ---
    # Штраф прибавляется к Xᵀ X; свободный член не штрафуется нулём в матрице.
    3021: "def ridge_weights(X, y, alpha):\n"
    '    """Веса Ridge: штраф alpha, свободный член не штрафуется."""\n'
    "    X = np.asarray(X, dtype=float)\n"
    "    y = np.asarray(y, dtype=float).reshape(-1, 1)\n"
    "    X_b = np.hstack([np.ones((X.shape[0], 1)), X])\n"
    "    penalty = alpha * np.eye(X_b.shape[1])\n"
    "    penalty[0, 0] = 0.0\n"
    "    return np.linalg.inv(X_b.T @ X_b + penalty) @ X_b.T @ y\n\n\n"
    "result = (\n"
    "    round(float(np.linalg.norm(ridge_weights(X_train, y_train, 1.0)[1:])), 4),\n"
    "    round(float(np.linalg.norm(ridge_weights(X_train, y_train, 1000.0)[1:])), 4),\n"
    "    round(float(ridge_weights(X_train, y_train, 1.0)[0, 0]), 4),\n"
    ")",
    # Лестница штрафов: alpha выбирается по ошибке на тестовой выборке.
    3023: "def best_alpha(X_train, y_train, X_test, y_test, alphas):\n"
    '    """Alpha с наименьшей ошибкой на тестовой выборке."""\n'
    "    chosen, best = None, None\n"
    "    for alpha in alphas:\n"
    "        model = Ridge(alpha=alpha).fit(X_train, y_train)\n"
    "        error = float(np.mean((y_test - model.predict(X_test)) ** 2))\n"
    "        if best is None or error < best:\n"
    "            chosen, best = alpha, error\n"
    "    return chosen, best\n\n\n"
    "ladder = [0.001, 1.0, 10.0, 1000.0]\n"
    "chosen, best = best_alpha(X_train, y_train, X_test, y_test, ladder)\n"
    "tail = float(\n"
    "    np.mean((y_test - Ridge(alpha=1000.0).fit(X_train, y_train).predict(X_test)) ** 2)\n"
    ")\n"
    "result = (chosen, round(best, 4), round(tail, 4))",
    # Lasso обнуляет шумовые признаки: индексы ненулевых коэффициентов.
    3024: "def nonzero_features(alpha):\n"
    '    """Индексы ненулевых коэффициентов Lasso по возрастанию."""\n'
    "    model = Lasso(alpha=alpha, max_iter=10000).fit(X_train, y_train)\n"
    "    return [int(index) for index in np.flatnonzero(model.coef_)]\n\n\n"
    "result = (nonzero_features(0.01), nonzero_features(0.5), nonzero_features(5.0))",
    # Лучший штраф Lasso снова ищется на тесте: сильный штраф душит признаки.
    3026: "def best_lasso(X_train, y_train, X_test, y_test, alphas):\n"
    '    """Alpha с наименьшей ошибкой на тестовой выборке."""\n'
    "    chosen, best = None, None\n"
    "    for alpha in alphas:\n"
    "        model = Lasso(alpha=alpha, max_iter=10000).fit(X_train, y_train)\n"
    "        error = float(np.mean((y_test - model.predict(X_test)) ** 2))\n"
    "        if best is None or error < best:\n"
    "            chosen, best = alpha, error\n"
    "    return chosen, best\n\n\n"
    "ladder = [0.01, 0.05, 0.1, 0.5, 1.0, 5.0]\n"
    "chosen, best = best_lasso(X_train, y_train, X_test, y_test, ladder)\n"
    "hard = float(\n"
    "    np.mean((y_test - Lasso(alpha=5.0, max_iter=10000).fit(X_train, y_train).predict(X_test)) ** 2)\n"
    ")\n"
    "result = (chosen, round(best, 4), round(hard, 4))",
    # Три модели на одном разбиении: ошибка на тесте и имя победителя.
    3027: "def compare_models(X_train, y_train, X_test, y_test):\n"
    '    """Список пар (имя модели, MSE на тестовой выборке)."""\n'
    "    prepared = [\n"
    '        ("ols", LinearRegression()),\n'
    '        ("ridge", Ridge(alpha=1.0)),\n'
    '        ("lasso", Lasso(alpha=1.0, max_iter=10000)),\n'
    "    ]\n"
    "    return [\n"
    "        (name, float(np.mean((y_test - model.fit(X_train, y_train).predict(X_test)) ** 2)))\n"
    "        for name, model in prepared\n"
    "    ]\n\n\n"
    "pairs = compare_models(X_train, y_train, X_test, y_test)\n"
    "result = (\n"
    "    [(name, round(value, 4)) for name, value in pairs],\n"
    "    min(pairs, key=lambda pair: pair[1])[0],\n"
    ")",
    # Итоговое: ошибка, норма и число ненулевых весов у каждой из трёх моделей.
    3029: "def regularization_report(X_train, y_train, X_test, y_test):\n"
    '    """Отчёт по трём моделям: mse, norm и nonzero у каждой."""\n'
    "    prepared = {\n"
    '        "ols": LinearRegression(),\n'
    '        "ridge": Ridge(alpha=1.0),\n'
    '        "lasso": Lasso(alpha=1.0, max_iter=10000),\n'
    "    }\n"
    "    report = {}\n"
    "    for name, model in prepared.items():\n"
    "        model.fit(X_train, y_train)\n"
    "        report[name] = {\n"
    '            "mse": float(np.mean((y_test - model.predict(X_test)) ** 2)),\n'
    '            "norm": float(np.linalg.norm(model.coef_)),\n'
    '            "nonzero": int(np.sum(model.coef_ != 0)),\n'
    "        }\n"
    "    return report\n\n\n"
    "report = regularization_report(X_train, y_train, X_test, y_test)\n"
    "result = {\n"
    "    name: {key: round(value, 4) for key, value in values.items()}\n"
    "    for name, values in report.items()\n"
    "}",
}

# Верные альтернативы: другой способ записи той же математики.
ALTERNATIVES = [
    # np.square вместо ** 2.
    (
        3001,
        "def mse(y_true, y_pred):\n"
        "    y_true = np.asarray(y_true, dtype=float)\n"
        "    y_pred = np.asarray(y_pred, dtype=float)\n"
        "    return float(np.square(y_true - y_pred).mean())\n\n\n"
        "result = (\n"
        "    round(float(mse([1.0, 2.0, 3.0], [1.1, 1.9, 3.2])), 4),\n"
        "    round(float(mse(y, np.full_like(y, y.mean()))), 4),\n"
        ")",
    ),
    # abs-массив и метод .mean().
    (
        3003,
        "def mae(y_true, y_pred):\n"
        "    diff = np.abs(np.asarray(y_true, dtype=float) - np.asarray(y_pred, dtype=float))\n"
        "    return float(diff.mean())\n\n\n"
        "result = (\n"
        "    round(float(mae([1.0, 2.0, 3.0], [1.1, 1.9, 3.2])), 4),\n"
        "    round(float(mae(y, y + 2.0)), 4),\n"
        "    round(float(mae(np.array([1.0, 2.0, 3.0, 100.0]), np.array([1.0, 2.0, 3.0, 60.0]))), 4),\n"
        ")",
    ),
    # R² через суммы квадратов, записанные методами массивов.
    (
        3005,
        "def r2(y_true, y_pred):\n"
        "    y_true = np.asarray(y_true, dtype=float)\n"
        "    y_pred = np.asarray(y_pred, dtype=float)\n"
        "    rss = float(np.square(y_true - y_pred).sum())\n"
        "    tss = float(np.square(y_true - y_true.mean()).sum())\n"
        "    return float(1.0 - rss / tss)\n\n\n"
        "tiny = np.array([1.0, 2.0, 3.0])\n"
        "result = (\n"
        "    round(float(r2(tiny, tiny)), 4),\n"
        "    round(float(r2(tiny, np.full(3, 2.0))), 4),\n"
        "    round(float(r2(tiny, np.full(3, 3.0))), 4),\n"
        "    round(float(r2(y, predictions)), 4),\n"
        ")",
    ),
    # Базовая ошибка — дисперсия цели; R² через ошибки считается по средним.
    (
        3007,
        "def baseline_mse(y_true):\n"
        "    return float(np.var(np.asarray(y_true, dtype=float)))\n\n\n"
        "def r2_by_mse(y_true, y_pred):\n"
        "    y_true = np.asarray(y_true, dtype=float)\n"
        "    y_pred = np.asarray(y_pred, dtype=float)\n"
        "    error = np.mean((y_true - y_pred) ** 2)\n"
        "    return float(1 - error / baseline_mse(y_true))\n\n\n"
        "result = (\n"
        "    round(float(baseline_mse(y)), 4),\n"
        "    round(float(r2_by_mse(y, predictions)), 4),\n"
        "    round(float(r2_by_mse(y, np.full_like(y, y.mean()))), 4),\n"
        ")",
    ),
    # Отчёт собирается одним словарём без вспомогательной функции.
    (
        3009,
        "def regression_report(y_true, y_pred):\n"
        "    y_true = np.asarray(y_true, dtype=float)\n"
        "    y_pred = np.asarray(y_pred, dtype=float)\n"
        "    return {\n"
        '        "mse": float(np.square(y_true - y_pred).mean()),\n'
        '        "mae": float(np.abs(y_true - y_pred).mean()),\n'
        '        "r2": float(1 - np.square(y_true - y_pred).mean() / np.square(y_true - y_true.mean()).mean()),\n'
        "    }\n\n\n"
        "result = (\n"
        "    {key: round(value, 4) for key, value in regression_report(y, predictions).items()},\n"
        "    {\n"
        "        key: round(value, 4)\n"
        "        for key, value in regression_report(\n"
        "            np.array([1.0, 2.0, 3.0]), np.array([2.0, 2.0, 2.0])\n"
        "        ).items()\n"
        "    },\n"
        ")",
    ),
    # column_stack вместо hstack.
    (
        3011,
        "def add_bias_column(X):\n"
        "    X = np.asarray(X, dtype=float)\n"
        "    if X.ndim == 1:\n"
        "        X = X.reshape(-1, 1)\n"
        "    return np.column_stack([np.ones(X.shape[0]), X])\n\n\n"
        "result = (\n"
        "    np.round(add_bias_column(X[:2]), 4).tolist(),\n"
        "    add_bias_column(np.arange(5)).tolist(),\n"
        "    X.shape,\n"
        ")",
    ),
    # solve вместо обращения матрицы.
    (
        3013,
        "def fit_normal_equation(X, y):\n"
        "    X = np.asarray(X, dtype=float)\n"
        "    y = np.asarray(y, dtype=float).reshape(-1, 1)\n"
        "    X_b = np.hstack([np.ones((X.shape[0], 1)), X])\n"
        "    return np.linalg.solve(X_b.T @ X_b, X_b.T @ y)\n\n\n"
        "X_b = np.hstack([np.ones((X.shape[0], 1)), X])\n"
        "weights = fit_normal_equation(X, y)\n"
        "result = (\n"
        "    np.round(weights.ravel(), 4).tolist(),\n"
        "    round(float(np.mean((y - X_b @ weights) ** 2)), 4),\n"
        "    X_b.shape,\n"
        ")",
    ),
    # Модель хранит веса из solve и проверяет обученность по None.
    (
        3015,
        "class MyLinearRegression:\n"
        "    def __init__(self):\n"
        "        self.weights_ = None\n"
        "\n"
        "    def fit(self, X, y):\n"
        "        X = np.asarray(X, dtype=float)\n"
        "        if X.ndim == 1:\n"
        "            X = X.reshape(-1, 1)\n"
        "        y = np.asarray(y, dtype=float).reshape(-1, 1)\n"
        "        X_b = np.hstack([np.ones((X.shape[0], 1)), X])\n"
        "        self.weights_ = np.linalg.solve(X_b.T @ X_b, X_b.T @ y)\n"
        "        return self\n"
        "\n"
        "    def predict(self, X):\n"
        "        if self.weights_ is None:\n"
        '            raise ValueError("Модель не обучена: сначала обучите её через fit(X, y)")\n'
        "        X = np.atleast_2d(np.asarray(X, dtype=float))\n"
        "        return np.hstack([np.ones((X.shape[0], 1)), X]) @ self.weights_\n\n\n"
        "model = MyLinearRegression().fit(X, y)\n"
        "result = (\n"
        "    np.round(model.weights_.ravel(), 4).tolist(),\n"
        "    np.round(model.predict(X[:3]).ravel(), 4).tolist(),\n"
        "    round(float(np.mean((y - model.predict(X)) ** 2)), 4),\n"
        ")",
    ),
    # lstsq вместо pinv: на вырожденных данных тоже решение наименьшей нормы.
    (
        3017,
        "def weight_vectors(X, y):\n"
        "    X = np.asarray(X, dtype=float)\n"
        "    y = np.asarray(y, dtype=float).reshape(-1, 1)\n"
        "    X_b = np.hstack([np.ones((X.shape[0], 1)), X])\n"
        "    own = np.linalg.lstsq(X_b, y, rcond=None)[0]\n"
        "    model = LinearRegression().fit(X, y)\n"
        "    sk = np.r_[np.ravel(model.intercept_), np.ravel(model.coef_)]\n"
        "    return own.ravel(), sk\n\n\n"
        "def cannot_be_inverted(A):\n"
        "    A = np.asarray(A, dtype=float)\n"
        "    try:\n"
        "        np.linalg.cholesky(A.T @ A)\n"
        "    except np.linalg.LinAlgError:\n"
        "        return True\n"
        "    return False\n\n\n"
        "duplicated = np.hstack([X, X[:, :1]])\n"
        "result = (\n"
        "    bool(np.allclose(*weight_vectors(X, y), atol=1e-10)),\n"
        "    bool(np.allclose(*weight_vectors(duplicated, y), atol=1e-10)),\n"
        "    cannot_be_inverted(duplicated),\n"
        "    cannot_be_inverted(X),\n"
        ")",
    ),
    # Срезы по маскам вместо самостоятельной сборки матрицы признаков.
    (
        3019,
        "def fit_and_test(X, y, n_train):\n"
        "    X = np.asarray(X, dtype=float)\n"
        "    y = np.asarray(y, dtype=float).reshape(-1, 1)\n"
        "    mask = np.arange(X.shape[0]) < n_train\n"
        "    weights = np.linalg.pinv(\n"
        "        np.hstack([np.ones((n_train, 1)), X[mask]]).T"
        " @ np.hstack([np.ones((n_train, 1)), X[mask]])\n"
        "    ) @ np.hstack([np.ones((n_train, 1)), X[mask]]).T @ y[mask]\n"
        "    rest = np.hstack([np.ones((X.shape[0] - n_train, 1)), X[~mask]]) @ weights\n"
        "    return (\n"
        "        weights,\n"
        "        float(np.mean((y[mask] - np.hstack([np.ones((n_train, 1)), X[mask]]) @ weights) ** 2)),\n"
        "        float(np.mean((y[~mask] - rest) ** 2)),\n"
        "    )\n\n\n"
        "weights, train_mse, test_mse = fit_and_test(X, y, 35)\n"
        "result = (np.round(weights.ravel(), 4).tolist(), round(train_mse, 4), round(test_mse, 4))",
    ),
    # solve вместо inv в ручном Ridge.
    (
        3021,
        "def ridge_weights(X, y, alpha):\n"
        "    X = np.asarray(X, dtype=float)\n"
        "    y = np.asarray(y, dtype=float).reshape(-1, 1)\n"
        "    X_b = np.hstack([np.ones((X.shape[0], 1)), X])\n"
        "    penalty = np.eye(X_b.shape[1]) * alpha\n"
        "    penalty[0, 0] = 0.0\n"
        "    return np.linalg.solve(X_b.T @ X_b + penalty, X_b.T @ y)\n\n\n"
        "result = (\n"
        "    round(float(np.linalg.norm(ridge_weights(X_train, y_train, 1.0)[1:])), 4),\n"
        "    round(float(np.linalg.norm(ridge_weights(X_train, y_train, 1000.0)[1:])), 4),\n"
        "    round(float(ridge_weights(X_train, y_train, 1.0)[0, 0]), 4),\n"
        ")",
    ),
    # Выбор alpha через min по списку пар.
    (
        3023,
        "def best_alpha(X_train, y_train, X_test, y_test, alphas):\n"
        "    def error(alpha):\n"
        "        model = Ridge(alpha=alpha).fit(X_train, y_train)\n"
        "        return float(np.mean((y_test - model.predict(X_test)) ** 2))\n\n"
        "    return min(((alpha, error(alpha)) for alpha in alphas), key=lambda pair: pair[1])\n\n\n"
        "ladder = [0.001, 1.0, 10.0, 1000.0]\n"
        "chosen, best = best_alpha(X_train, y_train, X_test, y_test, ladder)\n"
        "tail = float(\n"
        "    np.mean((y_test - Ridge(alpha=1000.0).fit(X_train, y_train).predict(X_test)) ** 2)\n"
        ")\n"
        "result = (chosen, round(best, 4), round(tail, 4))",
    ),
    # Индексы через enumerate вместо flatnonzero.
    (
        3024,
        "def nonzero_features(alpha):\n"
        "    model = Lasso(alpha=alpha, max_iter=10000).fit(X_train, y_train)\n"
        "    return [index for index, value in enumerate(model.coef_) if value != 0]\n\n\n"
        "result = (nonzero_features(0.01), nonzero_features(0.5), nonzero_features(5.0))",
    ),
    # Выбор alpha через min по списку пар.
    (
        3026,
        "def best_lasso(X_train, y_train, X_test, y_test, alphas):\n"
        "    def error(alpha):\n"
        "        model = Lasso(alpha=alpha, max_iter=10000).fit(X_train, y_train)\n"
        "        return float(np.mean((y_test - model.predict(X_test)) ** 2))\n\n"
        "    return min(((alpha, error(alpha)) for alpha in alphas), key=lambda pair: pair[1])\n\n\n"
        "ladder = [0.01, 0.05, 0.1, 0.5, 1.0, 5.0]\n"
        "chosen, best = best_lasso(X_train, y_train, X_test, y_test, ladder)\n"
        "hard = float(\n"
        "    np.mean((y_test - Lasso(alpha=5.0, max_iter=10000).fit(X_train, y_train).predict(X_test)) ** 2)\n"
        ")\n"
        "result = (chosen, round(best, 4), round(hard, 4))",
    ),
    # Сравнение через явный цикл и отдельный список результатов.
    (
        3027,
        "def compare_models(X_train, y_train, X_test, y_test):\n"
        "    pairs = []\n"
        '    for name, model in (\n'
        '        ("ols", LinearRegression()),\n'
        '        ("ridge", Ridge(alpha=1.0)),\n'
        '        ("lasso", Lasso(alpha=1.0, max_iter=10000)),\n'
        "    ):\n"
        "        model.fit(X_train, y_train)\n"
        "        pairs.append((name, float(np.mean((y_test - model.predict(X_test)) ** 2))))\n"
        "    return pairs\n\n\n"
        "pairs = compare_models(X_train, y_train, X_test, y_test)\n"
        "best = sorted(pairs, key=lambda pair: pair[1])[0][0]\n"
        "result = ([(name, round(value, 4)) for name, value in pairs], best)",
    ),
    # Отчёт через вспомогательную функцию на каждую модель.
    (
        3029,
        "def model_stats(model, X_test, y_test):\n"
        '    """Ошибка, норма и число ненулевых коэффициентов."""\n'
        "    return {\n"
        '        "mse": float(np.mean((y_test - model.predict(X_test)) ** 2)),\n'
        '        "norm": float(np.linalg.norm(model.coef_)),\n'
        '        "nonzero": int(np.count_nonzero(model.coef_)),\n'
        "    }\n\n\n"
        "def regularization_report(X_train, y_train, X_test, y_test):\n"
        "    report = {}\n"
        '    for name, model in (\n'
        '        ("ols", LinearRegression()),\n'
        '        ("ridge", Ridge(alpha=1.0)),\n'
        '        ("lasso", Lasso(alpha=1.0, max_iter=10000)),\n'
        "    ):\n"
        "        model.fit(X_train, y_train)\n"
        "        report[name] = model_stats(model, X_test, y_test)\n"
        "    return report\n\n\n"
        "report = regularization_report(X_train, y_train, X_test, y_test)\n"
        "result = {\n"
        "    name: {key: round(value, 4) for key, value in values.items()}\n"
        "    for name, values in report.items()\n"
        "}",
    ),
]

# Главные ловушки: ошибка в самой метрике или в формуле регуляризации.
WRONG = {
    # Сумма вместо среднего: метрика растёт с длиной выборки.
    3001: "def mse(y_true, y_pred):\n"
    "    y_true = np.asarray(y_true, dtype=float)\n"
    "    y_pred = np.asarray(y_pred, dtype=float)\n"
    "    return float(np.sum((y_true - y_pred) ** 2))\n\n\n"
    "result = (\n"
    "    round(float(mse([1.0, 2.0, 3.0], [1.1, 1.9, 3.2])), 4),\n"
    "    round(float(mse(y, np.full_like(y, y.mean()))), 4),\n"
    ")",
    # Квадрат вместо модуля: те же данные, другая метрика.
    3003: "def mae(y_true, y_pred):\n"
    "    y_true = np.asarray(y_true, dtype=float)\n"
    "    y_pred = np.asarray(y_pred, dtype=float)\n"
    "    return float(np.mean((y_true - y_pred) ** 2))\n\n\n"
    "result = (\n"
    "    round(float(mae([1.0, 2.0, 3.0], [1.1, 1.9, 3.2])), 4),\n"
    "    round(float(mae(y, y + 2.0)), 4),\n"
    "    round(float(mae(np.array([1.0, 2.0, 3.0, 100.0]), np.array([1.0, 2.0, 3.0, 60.0]))), 4),\n"
    ")",
    # Знак потерян: отношение ошибок вместо «единица минус отношение».
    3005: "def r2(y_true, y_pred):\n"
    "    y_true = np.asarray(y_true, dtype=float)\n"
    "    y_pred = np.asarray(y_pred, dtype=float)\n"
    "    rss = float(np.sum((y_true - y_pred) ** 2))\n"
    "    tss = float(np.sum((y_true - y_true.mean()) ** 2))\n"
    "    return float(rss / tss)\n\n\n"
    "tiny = np.array([1.0, 2.0, 3.0])\n"
    "result = (\n"
    "    round(float(r2(tiny, tiny)), 4),\n"
    "    round(float(r2(tiny, np.full(3, 2.0))), 4),\n"
    "    round(float(r2(tiny, np.full(3, 3.0))), 4),\n"
    "    round(float(r2(y, predictions)), 4),\n"
    ")",
    # Та же потеря знака в R² через ошибки.
    3007: "def baseline_mse(y_true):\n"
    "    y_true = np.asarray(y_true, dtype=float)\n"
    "    return float(np.mean((y_true - y_true.mean()) ** 2))\n\n\n"
    "def r2_by_mse(y_true, y_pred):\n"
    "    y_true = np.asarray(y_true, dtype=float)\n"
    "    y_pred = np.asarray(y_pred, dtype=float)\n"
    "    model_mse = float(np.mean((y_true - y_pred) ** 2))\n"
    "    return float(model_mse / baseline_mse(y_true))\n\n\n"
    "result = (\n"
    "    round(float(baseline_mse(y)), 4),\n"
    "    round(float(r2_by_mse(y, predictions)), 4),\n"
    "    round(float(r2_by_mse(y, np.full_like(y, y.mean()))), 4),\n"
    ")",
    # MSE посчитана суммой: отчёт расходится с прогнозом средним.
    3009: "def regression_report(y_true, y_pred):\n"
    "    y_true = np.asarray(y_true, dtype=float)\n"
    "    y_pred = np.asarray(y_pred, dtype=float)\n"
    "    errors = y_true - y_pred\n"
    "    tss = float(np.sum((y_true - y_true.mean()) ** 2))\n"
    "    return {\n"
    '        "mse": float(np.sum(errors ** 2)),\n'
    '        "mae": float(np.mean(np.abs(errors))),\n'
    '        "r2": float(1 - np.sum(errors ** 2) / tss),\n'
    "    }\n\n\n"
    "result = (\n"
    "    {key: round(value, 4) for key, value in regression_report(y, predictions).items()},\n"
    "    {\n"
    "        key: round(value, 4)\n"
    "        for key, value in regression_report(\n"
    "            np.array([1.0, 2.0, 3.0]), np.array([2.0, 2.0, 2.0])\n"
    "        ).items()\n"
    "    },\n"
    ")",
    # Единицы дописаны справа: вес свободного члена уезжает в конец.
    3011: "def add_bias_column(X):\n"
    "    X = np.asarray(X, dtype=float)\n"
    "    if X.ndim == 1:\n"
    "        X = X.reshape(-1, 1)\n"
    "    return np.hstack([X, np.ones((X.shape[0], 1))])\n\n\n"
    "result = (\n"
    "    np.round(add_bias_column(X[:2]), 4).tolist(),\n"
    "    add_bias_column(np.arange(5)).tolist(),\n"
    "    X.shape,\n"
    ")",
    # Забыт столбец единиц: прямая обязана пройти через ноль.
    3013: "def fit_normal_equation(X, y):\n"
    "    X = np.asarray(X, dtype=float)\n"
    "    y = np.asarray(y, dtype=float).reshape(-1, 1)\n"
    "    return np.linalg.inv(X.T @ X) @ X.T @ y\n\n\n"
    "X_b = np.hstack([np.ones((X.shape[0], 1)), X])\n"
    "weights = fit_normal_equation(X, y)\n"
    "result = (\n"
    "    np.round(weights.ravel(), 4).tolist(),\n"
    "    round(float(np.mean((y - X @ weights) ** 2)), 4),\n"
    "    X_b.shape,\n"
    ")",
    # predict считает без свободного члена: прогноз смещён на константу.
    3015: "class MyLinearRegression:\n"
    "    def __init__(self):\n"
    "        self.weights_ = None\n"
    "\n"
    "    def fit(self, X, y):\n"
    "        X = np.asarray(X, dtype=float)\n"
    "        if X.ndim == 1:\n"
    "            X = X.reshape(-1, 1)\n"
    "        y = np.asarray(y, dtype=float).reshape(-1, 1)\n"
    "        X_b = np.hstack([np.ones((X.shape[0], 1)), X])\n"
    "        self.weights_ = np.linalg.pinv(X_b.T @ X_b) @ X_b.T @ y\n"
    "        return self\n"
    "\n"
    "    def predict(self, X):\n"
    "        if self.weights_ is None:\n"
    '            raise ValueError("Сначала обучите модель: fit(X, y)")\n'
    "        X = np.atleast_2d(np.asarray(X, dtype=float))\n"
    "        return X @ self.weights_[1:]\n\n\n"
    "model = MyLinearRegression().fit(X, y)\n"
    "result = (\n"
    "    np.round(model.weights_.ravel(), 4).tolist(),\n"
    "    np.round(model.predict(X[:3]).ravel(), 4).tolist(),\n"
    "    round(float(np.mean((y - model.predict(X)) ** 2)), 4),\n"
    ")",
    # Свободный член в конце своего вектора: порядок весов не совпадает со sklearn.
    3017: "def weight_vectors(X, y):\n"
    "    X = np.asarray(X, dtype=float)\n"
    "    y = np.asarray(y, dtype=float).reshape(-1, 1)\n"
    "    X_b = np.hstack([X, np.ones((X.shape[0], 1))])\n"
    "    own = np.linalg.pinv(X_b.T @ X_b) @ X_b.T @ y\n"
    "    model = LinearRegression().fit(X, y)\n"
    "    sk = np.r_[np.ravel(model.intercept_), np.ravel(model.coef_)]\n"
    "    return own.ravel(), sk\n\n\n"
    "def cannot_be_inverted(A):\n"
    "    A = np.asarray(A, dtype=float)\n"
    "    try:\n"
    "        np.linalg.inv(A.T @ A)\n"
    "    except np.linalg.LinAlgError:\n"
    "        return True\n"
    "    return False\n\n\n"
    "duplicated = np.hstack([X, X[:, :1]])\n"
    "result = (\n"
    "    bool(np.allclose(*weight_vectors(X, y), atol=1e-10)),\n"
    "    bool(np.allclose(*weight_vectors(duplicated, y), atol=1e-10)),\n"
    "    cannot_be_inverted(duplicated),\n"
    "    cannot_be_inverted(X),\n"
    ")",
    # Модель обучается на всей выборке: тест перестаёт быть честным.
    3019: "def fit_and_test(X, y, n_train):\n"
    "    X = np.asarray(X, dtype=float)\n"
    "    y = np.asarray(y, dtype=float).reshape(-1, 1)\n"
    "    X_b = np.hstack([np.ones((X.shape[0], 1)), X])\n"
    "    weights = np.linalg.inv(X_b.T @ X_b) @ X_b.T @ y\n"
    "    train_mse = float(np.mean((y[:n_train] - X_b[:n_train] @ weights) ** 2))\n"
    "    test_mse = float(np.mean((y[n_train:] - X_b[n_train:] @ weights) ** 2))\n"
    "    return weights, train_mse, test_mse\n\n\n"
    "weights, train_mse, test_mse = fit_and_test(X, y, 35)\n"
    "result = (np.round(weights.ravel(), 4).tolist(), round(train_mse, 4), round(test_mse, 4))",
    # Штраф наложен на все веса, включая свободный член.
    3021: "def ridge_weights(X, y, alpha):\n"
    "    X = np.asarray(X, dtype=float)\n"
    "    y = np.asarray(y, dtype=float).reshape(-1, 1)\n"
    "    X_b = np.hstack([np.ones((X.shape[0], 1)), X])\n"
    "    penalty = alpha * np.eye(X_b.shape[1])\n"
    "    return np.linalg.inv(X_b.T @ X_b + penalty) @ X_b.T @ y\n\n\n"
    "result = (\n"
    "    round(float(np.linalg.norm(ridge_weights(X_train, y_train, 1.0)[1:])), 4),\n"
    "    round(float(np.linalg.norm(ridge_weights(X_train, y_train, 1000.0)[1:])), 4),\n"
    "    round(float(ridge_weights(X_train, y_train, 1.0)[0, 0]), 4),\n"
    ")",
    # alpha выбирается по ошибке на обучении: всегда побеждает минимальный штраф.
    3023: "def best_alpha(X_train, y_train, X_test, y_test, alphas):\n"
    "    chosen, best = None, None\n"
    "    for alpha in alphas:\n"
    "        model = Ridge(alpha=alpha).fit(X_train, y_train)\n"
    "        error = float(np.mean((y_train - model.predict(X_train)) ** 2))\n"
    "        if best is None or error < best:\n"
    "            chosen, best = alpha, error\n"
    "    return chosen, best\n\n\n"
    "ladder = [0.001, 1.0, 10.0, 1000.0]\n"
    "chosen, best = best_alpha(X_train, y_train, X_test, y_test, ladder)\n"
    "tail = float(\n"
    "    np.mean((y_test - Ridge(alpha=1000.0).fit(X_train, y_train).predict(X_test)) ** 2)\n"
    ")\n"
    "result = (chosen, round(best, 4), round(tail, 4))",
    # Ненулевые ищутся по знаку: отрицательные веса теряются.
    3024: "def nonzero_features(alpha):\n"
    "    model = Lasso(alpha=alpha, max_iter=10000).fit(X_train, y_train)\n"
    "    return [int(index) for index in np.flatnonzero(model.coef_ > 0)]\n\n\n"
    "result = (nonzero_features(0.01), nonzero_features(0.5), nonzero_features(5.0))",
    # Лучший штраф Lasso тоже выбран по обучению.
    3026: "def best_lasso(X_train, y_train, X_test, y_test, alphas):\n"
    "    chosen, best = None, None\n"
    "    for alpha in alphas:\n"
    "        model = Lasso(alpha=alpha, max_iter=10000).fit(X_train, y_train)\n"
    "        error = float(np.mean((y_train - model.predict(X_train)) ** 2))\n"
    "        if best is None or error < best:\n"
    "            chosen, best = alpha, error\n"
    "    return chosen, best\n\n\n"
    "ladder = [0.01, 0.05, 0.1, 0.5, 1.0, 5.0]\n"
    "chosen, best = best_lasso(X_train, y_train, X_test, y_test, ladder)\n"
    "hard = float(\n"
    "    np.mean((y_test - Lasso(alpha=5.0, max_iter=10000).fit(X_train, y_train).predict(X_test)) ** 2)\n"
    ")\n"
    "result = (chosen, round(best, 4), round(hard, 4))",
    # Ошибки посчитаны на обучении: сравнение моделей теряет смысл.
    3027: "def compare_models(X_train, y_train, X_test, y_test):\n"
    "    prepared = [\n"
    '        ("ols", LinearRegression()),\n'
    '        ("ridge", Ridge(alpha=1.0)),\n'
    '        ("lasso", Lasso(alpha=1.0, max_iter=10000)),\n'
    "    ]\n"
    "    return [\n"
    "        (name, float(np.mean((y_train - model.fit(X_train, y_train).predict(X_train)) ** 2)))\n"
    "        for name, model in prepared\n"
    "    ]\n\n\n"
    "pairs = compare_models(X_train, y_train, X_test, y_test)\n"
    "result = (\n"
    "    [(name, round(value, 4)) for name, value in pairs],\n"
    "    min(pairs, key=lambda pair: pair[1])[0],\n"
    ")",
    # Ненулевыми считаются все коэффициенты: у Lasso их десять из десяти.
    3029: "def regularization_report(X_train, y_train, X_test, y_test):\n"
    "    prepared = {\n"
    '        "ols": LinearRegression(),\n'
    '        "ridge": Ridge(alpha=1.0),\n'
    '        "lasso": Lasso(alpha=1.0, max_iter=10000),\n'
    "    }\n"
    "    report = {}\n"
    "    for name, model in prepared.items():\n"
    "        model.fit(X_train, y_train)\n"
    "        report[name] = {\n"
    '            "mse": float(np.mean((y_test - model.predict(X_test)) ** 2)),\n'
    '            "norm": float(np.linalg.norm(model.coef_)),\n'
    '            "nonzero": int(model.coef_.size),\n'
    "        }\n"
    "    return report\n\n\n"
    "report = regularization_report(X_train, y_train, X_test, y_test)\n"
    "result = {\n"
    "    name: {key: round(value, 4) for key, value in values.items()}\n"
    "    for name, values in report.items()\n"
    "}",
}

# Другие ловушки: каждая падает на своём утверждении.
MORE_WRONG = [
    # Прогноз средним по модулю: другая метрика вместо MSE.
    (
        3001,
        "def mse(y_true, y_pred):\n"
        "    y_true = np.asarray(y_true, dtype=float)\n"
        "    y_pred = np.asarray(y_pred, dtype=float)\n"
        "    return float(np.mean(np.abs(y_true - y_pred)))\n\n\n"
        "result = (\n"
        "    round(float(mse([1.0, 2.0, 3.0], [1.1, 1.9, 3.2])), 4),\n"
        "    round(float(mse(y, np.full_like(y, y.mean()))), 4),\n"
        ")",
    ),
    # Сначала среднее разностей, потом квадрат: знаки гасят друг друга.
    (
        3003,
        "def mae(y_true, y_pred):\n"
        "    y_true = np.asarray(y_true, dtype=float)\n"
        "    y_pred = np.asarray(y_pred, dtype=float)\n"
        "    return float(np.mean(y_true - y_pred))\n\n\n"
        "result = (\n"
        "    round(float(mae([1.0, 2.0, 3.0], [1.1, 1.9, 3.2])), 4),\n"
        "    round(float(mae(y, y + 2.0)), 4),\n"
        "    round(float(mae(np.array([1.0, 2.0, 3.0, 100.0]), np.array([1.0, 2.0, 3.0, 60.0]))), 4),\n"
        ")",
    ),
    # В знаменателе сумма квадратов самих значений: ноль так не получить.
    (
        3005,
        "def r2(y_true, y_pred):\n"
        "    y_true = np.asarray(y_true, dtype=float)\n"
        "    y_pred = np.asarray(y_pred, dtype=float)\n"
        "    rss = float(np.sum((y_true - y_pred) ** 2))\n"
        "    tss = float(np.sum(y_true ** 2))\n"
        "    return float(1 - rss / tss)\n\n\n"
        "tiny = np.array([1.0, 2.0, 3.0])\n"
        "result = (\n"
        "    round(float(r2(tiny, tiny)), 4),\n"
        "    round(float(r2(tiny, np.full(3, 2.0))), 4),\n"
        "    round(float(r2(tiny, np.full(3, 3.0))), 4),\n"
        "    round(float(r2(y, predictions)), 4),\n"
        ")",
    ),
    # Выборочная дисперсия (n − 1) вместо генеральной: базовая ошибка завышена.
    (
        3007,
        "def baseline_mse(y_true):\n"
        "    y_true = np.asarray(y_true, dtype=float)\n"
        "    return float(np.var(y_true, ddof=1))\n\n\n"
        "def r2_by_mse(y_true, y_pred):\n"
        "    y_true = np.asarray(y_true, dtype=float)\n"
        "    y_pred = np.asarray(y_pred, dtype=float)\n"
        "    model_mse = float(np.mean((y_true - y_pred) ** 2))\n"
        "    return float(1 - model_mse / baseline_mse(y_true))\n\n\n"
        "result = (\n"
        "    round(float(baseline_mse(y)), 4),\n"
        "    round(float(r2_by_mse(y, predictions)), 4),\n"
        "    round(float(r2_by_mse(y, np.full_like(y, y.mean()))), 4),\n"
        ")",
    ),
    # Знак потерян в R² отчёта.
    (
        3009,
        "def regression_report(y_true, y_pred):\n"
        "    y_true = np.asarray(y_true, dtype=float)\n"
        "    y_pred = np.asarray(y_pred, dtype=float)\n"
        "    errors = y_true - y_pred\n"
        "    tss = float(np.sum((y_true - y_true.mean()) ** 2))\n"
        "    return {\n"
        '        "mse": float(np.mean(errors ** 2)),\n'
        '        "mae": float(np.mean(np.abs(errors))),\n'
        '        "r2": float(np.sum(errors ** 2) / tss),\n'
        "    }\n\n\n"
        "result = (\n"
        "    {key: round(value, 4) for key, value in regression_report(y, predictions).items()},\n"
        "    {\n"
        "        key: round(value, 4)\n"
        "        for key, value in regression_report(\n"
        "            np.array([1.0, 2.0, 3.0]), np.array([2.0, 2.0, 2.0])\n"
        "        ).items()\n"
        "    },\n"
        ")",
    ),
    # Исходный массив правится на месте: проверка «X не меняется» ловит это.
    (
        3011,
        "def add_bias_column(X):\n"
        "    X = np.asarray(X, dtype=float)\n"
        "    if X.ndim == 1:\n"
        "        X = X.reshape(-1, 1)\n"
        "    X[:, 0] = 1.0\n"
        "    return X\n\n\n"
        "result = (\n"
        "    np.round(add_bias_column(X[:2]), 4).tolist(),\n"
        "    add_bias_column(np.arange(5)).tolist(),\n"
        "    X.shape,\n"
        ")",
    ),
    # Столбец единиц справа: веса выходят в другом порядке.
    (
        3013,
        "def fit_normal_equation(X, y):\n"
        "    X = np.asarray(X, dtype=float)\n"
        "    y = np.asarray(y, dtype=float).reshape(-1, 1)\n"
        "    X_b = np.hstack([X, np.ones((X.shape[0], 1))])\n"
        "    return np.linalg.inv(X_b.T @ X_b) @ X_b.T @ y\n\n\n"
        "X_b = np.hstack([np.ones((X.shape[0], 1)), X])\n"
        "weights = fit_normal_equation(X, y)\n"
        "result = (\n"
        "    np.round(weights.ravel(), 4).tolist(),\n"
        "    round(float(np.mean((y - X_b @ weights) ** 2)), 4),\n"
        "    X_b.shape,\n"
        ")",
    ),
    # fit не возвращает модель: цепочка вызовов ломается.
    (
        3015,
        "class MyLinearRegression:\n"
        "    def __init__(self):\n"
        "        self.weights_ = None\n"
        "\n"
        "    def fit(self, X, y):\n"
        "        X = np.asarray(X, dtype=float)\n"
        "        if X.ndim == 1:\n"
        "            X = X.reshape(-1, 1)\n"
        "        y = np.asarray(y, dtype=float).reshape(-1, 1)\n"
        "        X_b = np.hstack([np.ones((X.shape[0], 1)), X])\n"
        "        self.weights_ = np.linalg.pinv(X_b.T @ X_b) @ X_b.T @ y\n"
        "\n"
        "    def predict(self, X):\n"
        "        if self.weights_ is None:\n"
        '            raise ValueError("Сначала обучите модель: fit(X, y)")\n'
        "        X = np.asarray(X, dtype=float)\n"
        "        if X.ndim == 1:\n"
        "            X = X.reshape(-1, 1)\n"
        "        return np.hstack([np.ones((X.shape[0], 1)), X]) @ self.weights_\n\n\n"
        "model = MyLinearRegression()\n"
        "model.fit(X, y)\n"
        "result = (\n"
        "    np.round(model.weights_.ravel(), 4).tolist(),\n"
        "    np.round(model.predict(X[:3]).ravel(), 4).tolist(),\n"
        "    round(float(np.mean((y - model.predict(X)) ** 2)), 4),\n"
        ")",
    ),
    # predict до fit возвращает None вместо ошибки с объяснением.
    (
        3015,
        "class MyLinearRegression:\n"
        "    def __init__(self):\n"
        "        self.weights_ = None\n"
        "\n"
        "    def fit(self, X, y):\n"
        "        X = np.asarray(X, dtype=float)\n"
        "        if X.ndim == 1:\n"
        "            X = X.reshape(-1, 1)\n"
        "        y = np.asarray(y, dtype=float).reshape(-1, 1)\n"
        "        X_b = np.hstack([np.ones((X.shape[0], 1)), X])\n"
        "        self.weights_ = np.linalg.pinv(X_b.T @ X_b) @ X_b.T @ y\n"
        "        return self\n"
        "\n"
        "    def predict(self, X):\n"
        "        X = np.atleast_2d(np.asarray(X, dtype=float))\n"
        "        return np.hstack([np.ones((X.shape[0], 1)), X]) @ self.weights_\n\n\n"
        "model = MyLinearRegression().fit(X, y)\n"
        "result = (\n"
        "    np.round(model.weights_.ravel(), 4).tolist(),\n"
        "    np.round(model.predict(X[:3]).ravel(), 4).tolist(),\n"
        "    round(float(np.mean((y - model.predict(X)) ** 2)), 4),\n"
        ")",
    ),
    # Проверка вырожденности отвечает True всегда.
    (
        3017,
        "def weight_vectors(X, y):\n"
        "    X = np.asarray(X, dtype=float)\n"
        "    y = np.asarray(y, dtype=float).reshape(-1, 1)\n"
        "    X_b = np.hstack([np.ones((X.shape[0], 1)), X])\n"
        "    own = np.linalg.pinv(X_b.T @ X_b) @ X_b.T @ y\n"
        "    model = LinearRegression().fit(X, y)\n"
        "    sk = np.r_[np.ravel(model.intercept_), np.ravel(model.coef_)]\n"
        "    return own.ravel(), sk\n\n\n"
        "def cannot_be_inverted(A):\n"
        "    return True\n\n\n"
        "duplicated = np.hstack([X, X[:, :1]])\n"
        "result = (\n"
        "    bool(np.allclose(*weight_vectors(X, y), atol=1e-10)),\n"
        "    bool(np.allclose(*weight_vectors(duplicated, y), atol=1e-10)),\n"
        "    cannot_be_inverted(duplicated),\n"
        "    cannot_be_inverted(X),\n"
        ")",
    ),
    # Обучающая и тестовая части перепутаны местами.
    (
        3019,
        "def fit_and_test(X, y, n_train):\n"
        "    X = np.asarray(X, dtype=float)\n"
        "    y = np.asarray(y, dtype=float).reshape(-1, 1)\n"
        "    X_b = np.hstack([np.ones((X.shape[0], 1)), X])\n"
        "    weights = np.linalg.inv(X_b.T @ X_b) @ X_b.T @ y\n"
        "    train_mse = float(np.mean((y[n_train:] - X_b[n_train:] @ weights) ** 2))\n"
        "    test_mse = float(np.mean((y[:n_train] - X_b[:n_train] @ weights) ** 2))\n"
        "    return weights, train_mse, test_mse\n\n\n"
        "weights, train_mse, test_mse = fit_and_test(X, y, 35)\n"
        "result = (np.round(weights.ravel(), 4).tolist(), round(train_mse, 4), round(test_mse, 4))",
    ),
    # Штраф без alpha: регуляризация не зависит от аргумента.
    (
        3021,
        "def ridge_weights(X, y, alpha):\n"
        "    X = np.asarray(X, dtype=float)\n"
        "    y = np.asarray(y, dtype=float).reshape(-1, 1)\n"
        "    X_b = np.hstack([np.ones((X.shape[0], 1)), X])\n"
        "    penalty = np.eye(X_b.shape[1])\n"
        "    penalty[0, 0] = 0.0\n"
        "    return np.linalg.inv(X_b.T @ X_b + penalty) @ X_b.T @ y\n\n\n"
        "result = (\n"
        "    round(float(np.linalg.norm(ridge_weights(X_train, y_train, 1.0)[1:])), 4),\n"
        "    round(float(np.linalg.norm(ridge_weights(X_train, y_train, 1000.0)[1:])), 4),\n"
        "    round(float(ridge_weights(X_train, y_train, 1.0)[0, 0]), 4),\n"
        ")",
    ),
    # Возвращается первый alpha списка, а не лучший.
    (
        3023,
        "def best_alpha(X_train, y_train, X_test, y_test, alphas):\n"
        "    errors = [\n"
        "        float(\n"
        "            np.mean(\n"
        "                (y_test - Ridge(alpha=alpha).fit(X_train, y_train).predict(X_test)) ** 2\n"
        "            )\n"
        "        )\n"
        "        for alpha in alphas\n"
        "    ]\n"
        "    return alphas[0], errors[0]\n\n\n"
        "ladder = [0.001, 1.0, 10.0, 1000.0]\n"
        "chosen, best = best_alpha(X_train, y_train, X_test, y_test, ladder)\n"
        "tail = float(\n"
        "    np.mean((y_test - Ridge(alpha=1000.0).fit(X_train, y_train).predict(X_test)) ** 2)\n"
        ")\n"
        "result = (chosen, round(best, 4), round(tail, 4))",
    ),
    # Порог вместо строгого нуля: у Lasso теряются маленькие ненулевые веса.
    (
        3024,
        "def nonzero_features(alpha):\n"
        "    model = Lasso(alpha=alpha, max_iter=10000).fit(X_train, y_train)\n"
        "    return [int(index) for index in np.flatnonzero(np.abs(model.coef_) > 0.01)]\n\n\n"
        "result = (nonzero_features(0.01), nonzero_features(0.5), nonzero_features(5.0))",
    ),
    # Возвращается первый alpha списка, а не лучший.
    (
        3026,
        "def best_lasso(X_train, y_train, X_test, y_test, alphas):\n"
        "    errors = [\n"
        "        float(\n"
        "            np.mean(\n"
        "                (y_test - Lasso(alpha=alpha, max_iter=10000).fit(X_train, y_train).predict(X_test)) ** 2\n"
        "            )\n"
        "        )\n"
        "        for alpha in alphas\n"
        "    ]\n"
        "    return alphas[0], errors[0]\n\n\n"
        "ladder = [0.01, 0.05, 0.1, 0.5, 1.0, 5.0]\n"
        "chosen, best = best_lasso(X_train, y_train, X_test, y_test, ladder)\n"
        "hard = float(\n"
        "    np.mean((y_test - Lasso(alpha=5.0, max_iter=10000).fit(X_train, y_train).predict(X_test)) ** 2)\n"
        ")\n"
        "result = (chosen, round(best, 4), round(hard, 4))",
    ),
    # Победителем объявлена худшая модель.
    (
        3027,
        "def compare_models(X_train, y_train, X_test, y_test):\n"
        "    prepared = [\n"
        '        ("ols", LinearRegression()),\n'
        '        ("ridge", Ridge(alpha=1.0)),\n'
        '        ("lasso", Lasso(alpha=1.0, max_iter=10000)),\n'
        "    ]\n"
        "    return [\n"
        "        (name, float(np.mean((y_test - model.fit(X_train, y_train).predict(X_test)) ** 2)))\n"
        "        for name, model in prepared\n"
        "    ]\n\n\n"
        "pairs = compare_models(X_train, y_train, X_test, y_test)\n"
        "result = (\n"
        "    [(name, round(value, 4)) for name, value in pairs],\n"
        "    max(pairs, key=lambda pair: pair[1])[0],\n"
        ")",
    ),
    # Ошибка отчёта посчитана на обучении, а не на тесте.
    (
        3029,
        "def regularization_report(X_train, y_train, X_test, y_test):\n"
        "    prepared = {\n"
        '        "ols": LinearRegression(),\n'
        '        "ridge": Ridge(alpha=1.0),\n'
        '        "lasso": Lasso(alpha=1.0, max_iter=10000),\n'
        "    }\n"
        "    report = {}\n"
        "    for name, model in prepared.items():\n"
        "        model.fit(X_train, y_train)\n"
        "        report[name] = {\n"
        '            "mse": float(np.mean((y_train - model.predict(X_train)) ** 2)),\n'
        '            "norm": float(np.linalg.norm(model.coef_)),\n'
        '            "nonzero": int(np.sum(model.coef_ != 0)),\n'
        "        }\n"
        "    return report\n\n\n"
        "report = regularization_report(X_train, y_train, X_test, y_test)\n"
        "result = {\n"
        "    name: {key: round(value, 4) for key, value in values.items()}\n"
        "    for name, values in report.items()\n"
        "}",
    ),
]

# Падения без подсказки: ошибка Python вместо AssertionError.
CRASHES = [
    # Списки не приведены к массиву: вычитание списков не работает.
    (
        3001,
        TypeError,
        "def mse(y_true, y_pred):\n"
        "    return float(np.mean((y_true - y_pred) ** 2))\n\n\n"
        "result = (\n"
        "    round(float(mse([1.0, 2.0, 3.0], [1.1, 1.9, 3.2])), 4),\n"
        "    round(float(mse(y, np.full_like(y, y.mean()))), 4),\n"
        ")",
    ),
    # np.insert по оси 1 на одномерном входе: AxisError вместо обработки формы.
    (
        3011,
        np.exceptions.AxisError,
        "def add_bias_column(X):\n"
        "    X = np.asarray(X, dtype=float)\n"
        "    return np.insert(X, 0, 1, axis=1)\n\n\n"
        "result = (\n"
        "    np.round(add_bias_column(X[:2]), 4).tolist(),\n"
        "    add_bias_column(np.arange(5)).tolist(),\n"
        "    X.shape,\n"
        ")",
    ),
    # inv на данных со столбцом-дублем: LinAlgError прямо из numpy.
    (
        3017,
        np.linalg.LinAlgError,
        "def weight_vectors(X, y):\n"
        "    X = np.asarray(X, dtype=float)\n"
        "    y = np.asarray(y, dtype=float).reshape(-1, 1)\n"
        "    X_b = np.hstack([np.ones((X.shape[0], 1)), X])\n"
        "    own = np.linalg.inv(X_b.T @ X_b) @ X_b.T @ y\n"
        "    model = LinearRegression().fit(X, y)\n"
        "    sk = np.r_[np.ravel(model.intercept_), np.ravel(model.coef_)]\n"
        "    return own.ravel(), sk\n\n\n"
        "def cannot_be_inverted(A):\n"
        "    A = np.asarray(A, dtype=float)\n"
        "    try:\n"
        "        np.linalg.inv(A.T @ A)\n"
        "    except np.linalg.LinAlgError:\n"
        "        return True\n"
        "    return False\n\n\n"
        "duplicated = np.hstack([X, X[:, :1]])\n"
        "result = (\n"
        "    bool(np.allclose(*weight_vectors(X, y), atol=1e-10)),\n"
        "    bool(np.allclose(*weight_vectors(duplicated, y), atol=1e-10)),\n"
        "    cannot_be_inverted(duplicated),\n"
        "    cannot_be_inverted(X),\n"
        ")",
    ),
]
