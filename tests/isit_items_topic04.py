"""Задания с кодом модуля 4 курса ИСИТ (логистическая регрессия).

Таблицы для `tests/test_isit_code_items.py`: эталон, верная альтернатива и
ловушки на каждое задание занятий `sigmoid` (4001, 4002, 4004, 4005, 4007),
`gradient` и `boundary`.

`WRONG` — по одной главной ловушке на задание (это словарь: вторая запись с
тем же id молча затрёт первую), остальные варианты — в `MORE_WRONG`, а те, что
падают ошибкой Python без подсказки, — в `CRASHES`. Эталоны считаются по
переданным аргументам, а не по числам из ноутбука лабораторной.
"""

import numpy as np  # noqa: F401  (нужен решениям и ловушкам)

SOLUTIONS = {
    # --- Занятие sigmoid: сигмоида и логарифмическая потеря ---
    # Логистическая функция: 1 / (1 + exp(-z)), скаляр и массив одинаково.
    4001: "def sigmoid(z):\n"
    '    """Вероятность класса 1: 1 / (1 + exp(-z))."""\n'
    "    return 1.0 / (1.0 + np.exp(-np.asarray(z, dtype=float)))\n\n\n"
    "result = (\n"
    "    round(float(sigmoid(0.0)), 4),\n"
    "    round(float(sigmoid(1.0)), 6),\n"
    "    bool(np.allclose(sigmoid(np.array([-2.0, 0.0, 2.0])), [0.1192, 0.5, 0.8808], atol=1e-4)),\n"
    ")",
    # Производная через саму функцию: σ'(z) = σ(z)(1 − σ(z)).
    4002: "def sigmoid(z):\n"
    '    """Логистическая функция."""\n'
    "    return 1.0 / (1.0 + np.exp(-np.asarray(z, dtype=float)))\n\n\n"
    "def sigmoid_derivative(z):\n"
    '    """Производная сигмоиды через саму функцию."""\n'
    "    values = sigmoid(z)\n"
    "    return values * (1 - values)\n\n\n"
    "result = (\n"
    "    round(float(sigmoid_derivative(0.0)), 4),\n"
    "    round(float(sigmoid_derivative(5.0)), 6),\n"
    "    round(float(sigmoid_derivative(10.0)), 8),\n"
    "    round(float(np.max(sigmoid_derivative(np.linspace(-10, 10, 401)))), 4),\n"
    ")",
    # Потеря: −y ln h − (1 − y) ln(1 − h), среднее по объектам, с зажимом.
    4004: "def log_loss(y_true, proba):\n"
    '    """Средняя логарифмическая потеря."""\n'
    "    y_true = np.asarray(y_true, dtype=float)\n"
    "    proba = np.clip(np.asarray(proba, dtype=float), 1e-12, 1 - 1e-12)\n"
    "    return float(-np.mean(y_true * np.log(proba) + (1 - y_true) * np.log(1 - proba)))\n\n\n"
    "result = (\n"
    "    round(log_loss([1], [0.9]), 6),\n"
    "    round(log_loss([1], [0.1]), 6),\n"
    "    round(log_loss([1], [0.5]), 6),\n"
    "    round(log_loss([1, 0], [0.8, 0.3]), 6),\n"
    ")",
    # Потеря по логитам: logaddexp(0, z) − y z — без переполнения.
    4005: "def log_loss_from_logits(y_true, z):\n"
    '    """Логарифмическая потеря по логитам, устойчивая к большим числам."""\n'
    "    y_true = np.asarray(y_true, dtype=float)\n"
    "    z = np.asarray(z, dtype=float)\n"
    "    return float(np.mean(np.logaddexp(0.0, z) - y_true * z))\n\n\n"
    "result = (\n"
    "    round(log_loss_from_logits([1], [0.0]), 6),\n"
    "    round(log_loss_from_logits([1], [1000.0]), 8),\n"
    "    round(log_loss_from_logits([1], [-1000.0]), 6),\n"
    "    bool(np.isfinite(log_loss_from_logits([1, 0], [1000.0, -1000.0]))),\n"
    ")",
    # Итоговое занятие: вероятности по переданным весам и потеря по ним.
    4007: "def predict_proba(X, w, b):\n"
    '    """Вероятности класса 1 для строк X при весах w и сдвиге b."""\n'
    "    logits = np.asarray(X, dtype=float) @ np.asarray(w, dtype=float) + b\n"
    "    return 1.0 / (1.0 + np.exp(-logits))\n\n\n"
    "def mean_log_loss(y_true, proba):\n"
    '    """Средняя логарифмическая потеря с зажимом вероятностей."""\n'
    "    y_true = np.asarray(y_true, dtype=float)\n"
    "    proba = np.clip(np.asarray(proba, dtype=float), 1e-12, 1 - 1e-12)\n"
    "    return float(-np.mean(y_true * np.log(proba) + (1 - y_true) * np.log(1 - proba)))\n\n\n"
    "result = (\n"
    "    round(mean_log_loss(y, predict_proba(X, w, b)), 4),\n"
    "    round(float(np.mean((predict_proba(X, w, b) >= 0.5) == y)), 4),\n"
    "    round(mean_log_loss(y, predict_proba(X, w_other, b_other)), 4),\n"
    "    round(float(np.mean((predict_proba(X, w_other, b_other) >= 0.5) == y)), 4),\n"
    ")",
    # --- Занятие gradient: градиент потерь, свой спуск, порог и классы ---
    # Градиент средней логарифмической потери по весам и смещению.
    4008: "def logistic_gradient(X, y, w, b):\n"
    '    """Градиент потерь по весам и смещению."""\n'
    "    X = np.asarray(X, dtype=float)\n"
    "    y = np.asarray(y, dtype=float)\n"
    "    proba = 1.0 / (1.0 + np.exp(-(X @ w + b)))\n"
    "    return X.T @ (proba - y) / len(y), float(np.mean(proba - y))\n\n\n"
    "grad_w, grad_b = logistic_gradient(X, y, w, b)\n\n"
    "result = (\n"
    "    round(float(grad_w[0]), 8),\n"
    "    round(float(grad_w[1]), 8),\n"
    "    round(float(grad_b), 8),\n"
    ")",
    # Обучение спуском: цикл из прямого прохода, градиента и шага.
    4010: "def fit_logistic(X, y, learning_rate=0.1, n_iterations=2000):\n"
    '    """Обучение градиентным спуском: веса, смещение и история потерь."""\n'
    "    X = np.asarray(X, dtype=float)\n"
    "    y = np.asarray(y, dtype=float)\n"
    "    weights = np.zeros(X.shape[1])\n"
    "    bias = 0.0\n\n"
    "    def loss():\n"
    "        proba = np.clip(1.0 / (1.0 + np.exp(-(X @ weights + bias))), 1e-12, 1 - 1e-12)\n"
    "        return float(-np.mean(y * np.log(proba) + (1 - y) * np.log(1 - proba)))\n\n"
    "    history = [loss()]\n"
    "    for _ in range(n_iterations):\n"
    "        proba = 1.0 / (1.0 + np.exp(-(X @ weights + bias)))\n"
    "        weights = weights - learning_rate * (X.T @ (proba - y) / len(y))\n"
    "        bias = bias - learning_rate * float(np.mean(proba - y))\n"
    "        history.append(loss())\n"
    "    return weights, bias, history\n\n\n"
    "weights, bias, history = fit_logistic(X, y)\n\n"
    "result = (round(history[0], 6), round(history[-1], 6), len(history))",
    # Один шаг спуска: новые веса и смещение, исходные не тронуты.
    4011: "def descent_step(X, y, w, b, learning_rate):\n"
    '    """Один шаг градиентного спуска."""\n'
    "    X = np.asarray(X, dtype=float)\n"
    "    proba = 1.0 / (1.0 + np.exp(-(X @ w + b)))\n"
    "    grad_w = X.T @ (proba - y) / len(y)\n"
    "    grad_b = float(np.mean(proba - y))\n"
    "    return np.asarray(w, dtype=float) - learning_rate * grad_w, float(b) - learning_rate * grad_b\n\n\n"
    "new_w, new_b = descent_step(X, y, w, b, 0.1)\n\n"
    "result = (\n"
    "    round(float(new_w[0]), 6),\n"
    "    round(float(new_w[1]), 6),\n"
    "    round(float(new_b), 6),\n"
    ")",
    # Вероятности в классы сравнением с порогом.
    4013: "def predict_labels(proba, threshold=0.5):\n"
    '    """Вероятности в классы 0 и 1."""\n'
    "    proba = np.asarray(proba, dtype=float)\n"
    "    return (proba >= threshold).astype(int)\n\n\n"
    "result = (\n"
    "    predict_labels([0.9, 0.5, 0.499999], 0.5).tolist(),\n"
    "    predict_labels([0.5], 0.51).tolist(),\n"
    "    predict_labels([0.7]).tolist(),\n"
    ")",
    # Отчёт по порогам: сколько единиц и какая точность.
    4014: "def threshold_report(y_true, proba, thresholds):\n"
    '    """Для каждого порога: (порог, число единиц, точность)."""\n'
    "    y_true = np.asarray(y_true, dtype=float)\n"
    "    proba = np.asarray(proba, dtype=float)\n"
    "    report = []\n"
    "    for threshold in thresholds:\n"
    "        labels = (proba >= threshold).astype(int)\n"
    "        report.append(\n"
    "            (float(threshold), int(labels.sum()), float(np.mean(labels == y_true)))\n"
    "        )\n"
    "    return report\n\n\n"
    "report = threshold_report(y, proba, (0.3, 0.5, 0.7))\n\n"
    "result = [(float(t), int(n), round(float(a), 4)) for t, n, a in report]",
    # Итог занятия: свой класс логистической регрессии.
    4016: "class MyLogisticRegression:\n"
    "    def __init__(self, learning_rate=0.1, n_iterations=2000):\n"
    '        """Логистическая регрессия, обученная своим градиентным спуском."""\n'
    "        self.learning_rate = learning_rate\n"
    "        self.n_iterations = n_iterations\n"
    "        self.weights = None\n"
    "        self.bias = None\n\n"
    "    def fit(self, X, y):\n"
    '        """Обучает модель и возвращает self."""\n'
    "        X = np.asarray(X, dtype=float)\n"
    "        y = np.asarray(y, dtype=float)\n"
    "        n_samples = X.shape[0]\n"
    "        self.weights = np.zeros(X.shape[1])\n"
    "        self.bias = 0.0\n"
    "        for _ in range(self.n_iterations):\n"
    "            proba = 1.0 / (1.0 + np.exp(-(X @ self.weights + self.bias)))\n"
    "            self.weights = self.weights - self.learning_rate * (X.T @ (proba - y) / n_samples)\n"
    "            self.bias = self.bias - self.learning_rate * float(np.mean(proba - y))\n"
    "        return self\n\n"
    "    def predict_proba(self, X):\n"
    '        """Вероятности класса 1."""\n'
    "        logits = np.asarray(X, dtype=float) @ self.weights + self.bias\n"
    "        return 1.0 / (1.0 + np.exp(-logits))\n\n"
    "    def predict(self, X, threshold=0.5):\n"
    '        """Классы 0 и 1 по порогу."""\n'
    "        return (self.predict_proba(X) >= threshold).astype(int)\n\n\n"
    "model = MyLogisticRegression().fit(X, y)\n"
    "small = MyLogisticRegression(n_iterations=1000).fit(X_small, y_small)\n\n"
    "result = (\n"
    "    round(float(np.mean(model.predict(X) == y)), 4),\n"
    "    int(small.predict(X_small).sum()),\n"
    "    round(float(small.predict_proba(X_small)[0]), 4),\n"
    "    round(float(small.bias), 4),\n"
    ")",
}

# Верные альтернативы: другое решение той же задачи — проверки обязаны пропустить.
ALTERNATIVES = [
    # Через гиперболический тангенс: 0.5 * (1 + th(z / 2)) — та же сигмоида.
    (
        4001,
        "def sigmoid(z):\n"
        '    """Сигмоида через тангенс."""\n'
        "    z = np.asarray(z, dtype=float)\n"
        "    return 0.5 * (1.0 + np.tanh(z / 2.0))\n\n\n"
        "result = (\n"
        "    round(float(sigmoid(0.0)), 4),\n"
        "    round(float(sigmoid(1.0)), 6),\n"
        "    bool(np.allclose(sigmoid(np.array([-2.0, 0.0, 2.0])), [0.1192, 0.5, 0.8808], atol=1e-4)),\n"
        ")",
    ),
    # Через дополнение: 1 − 1 / (1 + e^z) — то же самое число.
    (
        4001,
        "def sigmoid(z):\n"
        '    """Сигмоида через дополнение."""\n'
        "    z = np.asarray(z, dtype=float)\n"
        "    return 1.0 - 1.0 / (1.0 + np.exp(z))\n\n\n"
        "result = (\n"
        "    round(float(sigmoid(0.0)), 4),\n"
        "    round(float(sigmoid(1.0)), 6),\n"
        "    bool(np.allclose(sigmoid(np.array([-2.0, 0.0, 2.0])), [0.1192, 0.5, 0.8808], atol=1e-4)),\n"
        ")",
    ),
    # Производная через экспоненту: e^(−z) / (1 + e^(−z))².
    (
        4002,
        "def sigmoid(z):\n"
        '    """Логистическая функция."""\n'
        "    return 1.0 / (1.0 + np.exp(-np.asarray(z, dtype=float)))\n\n\n"
        "def sigmoid_derivative(z):\n"
        '    """Производная через экспоненту."""\n'
        "    z = np.asarray(z, dtype=float)\n"
        "    exp = np.exp(-z)\n"
        "    return exp / (1.0 + exp) ** 2\n\n\n"
        "result = (\n"
        "    round(float(sigmoid_derivative(0.0)), 4),\n"
        "    round(float(sigmoid_derivative(5.0)), 6),\n"
        "    round(float(sigmoid_derivative(10.0)), 8),\n"
        "    round(float(np.max(sigmoid_derivative(np.linspace(-10, 10, 401)))), 4),\n"
        ")",
    ),
    # Цена по объектам считается ветвлением, а не одной формулой.
    (
        4004,
        "def log_loss(y_true, proba):\n"
        '    """Потеря как среднее цен по объектам."""\n'
        "    y_true = np.asarray(y_true, dtype=float)\n"
        "    proba = np.clip(np.asarray(proba, dtype=float), 1e-12, 1 - 1e-12)\n"
        "    costs = [-np.log(p) if label == 1 else -np.log(1 - p) for label, p in zip(y_true, proba)]\n"
        "    return float(np.mean(costs))\n\n\n"
        "result = (\n"
        "    round(log_loss([1], [0.9]), 6),\n"
        "    round(log_loss([1], [0.1]), 6),\n"
        "    round(log_loss([1], [0.5]), 6),\n"
        "    round(log_loss([1, 0], [0.8, 0.3]), 6),\n"
        ")",
    ),
    # Устойчивая запись через max и log1p — та же формула, другой вид.
    (
        4005,
        "def log_loss_from_logits(y_true, z):\n"
        '    """Потеря по логитам через max и log1p."""\n'
        "    y_true = np.asarray(y_true, dtype=float)\n"
        "    z = np.asarray(z, dtype=float)\n"
        "    stable = np.maximum(z, 0.0) + np.log1p(np.exp(-np.abs(z)))\n"
        "    return float(np.mean(stable - y_true * z))\n\n\n"
        "result = (\n"
        "    round(log_loss_from_logits([1], [0.0]), 6),\n"
        "    round(log_loss_from_logits([1], [1000.0]), 8),\n"
        "    round(log_loss_from_logits([1], [-1000.0]), 6),\n"
        "    bool(np.isfinite(log_loss_from_logits([1, 0], [1000.0, -1000.0]))),\n"
        ")",
    ),
    # Вероятности через softplus, потеря через np.where.
    (
        4007,
        "def predict_proba(X, w, b):\n"
        '    """Вероятности через softplus от −z."""\n'
        "    logits = np.asarray(X, dtype=float) @ np.asarray(w, dtype=float) + b\n"
        "    return np.exp(-np.logaddexp(0.0, -logits))\n\n\n"
        "def mean_log_loss(y_true, proba):\n"
        '    """Потеря через np.where."""\n'
        "    y_true = np.asarray(y_true, dtype=float)\n"
        "    proba = np.clip(np.asarray(proba, dtype=float), 1e-12, 1 - 1e-12)\n"
        "    return float(-np.mean(np.where(y_true == 1, np.log(proba), np.log(1 - proba))))\n\n\n"
        "result = (\n"
        "    round(mean_log_loss(y, predict_proba(X, w, b)), 4),\n"
        "    round(float(np.mean((predict_proba(X, w, b) >= 0.5) == y)), 4),\n"
        "    round(mean_log_loss(y, predict_proba(X, w_other, b_other)), 4),\n"
        "    round(float(np.mean((predict_proba(X, w_other, b_other) >= 0.5) == y)), 4),\n"
        ")",
    ),
    # Градиент по объектам, а не матричным умножением.
    (
        4008,
        "def logistic_gradient(X, y, w, b):\n"
        '    """Градиент суммой по объектам."""\n'
        "    X = np.asarray(X, dtype=float)\n"
        "    y = np.asarray(y, dtype=float)\n"
        "    parts = []\n"
        "    for row, label in zip(X, y):\n"
        "        proba = 1.0 / (1.0 + np.exp(-(row @ w + b)))\n"
        "        parts.append((proba - label) * row)\n"
        "    grad_w = np.mean(parts, axis=0)\n"
        "    grad_b = float(np.mean([1.0 / (1.0 + np.exp(-(row @ w + b))) - label for row, label in zip(X, y)]))\n"
        "    return grad_w, grad_b\n\n\n"
        "grad_w, grad_b = logistic_gradient(X, y, w, b)\n\n"
        "result = (\n"
        "    round(float(grad_w[0]), 8),\n"
        "    round(float(grad_w[1]), 8),\n"
        "    round(float(grad_b), 8),\n"
        ")",
    ),
    # Потеря истории считается по логитам, без явной сигмоиды.
    (
        4010,
        "def fit_logistic(X, y, learning_rate=0.1, n_iterations=2000):\n"
        '    """Спуск с устойчивой потерей по логитам."""\n'
        "    X = np.asarray(X, dtype=float)\n"
        "    y = np.asarray(y, dtype=float)\n"
        "    weights = np.zeros(X.shape[1])\n"
        "    bias = 0.0\n\n"
        "    def loss():\n"
        "        logits = X @ weights + bias\n"
        "        return float(np.mean(np.logaddexp(0.0, logits) - y * logits))\n\n"
        "    history = [loss()]\n"
        "    while len(history) <= n_iterations:\n"
        "        proba = 1.0 / (1.0 + np.exp(-(X @ weights + bias)))\n"
        "        weights = weights - learning_rate * (X.T @ (proba - y) / X.shape[0])\n"
        "        bias = bias - learning_rate * float(np.sum(proba - y) / X.shape[0])\n"
        "        history.append(loss())\n"
        "    return weights, bias, history\n\n\n"
        "weights, bias, history = fit_logistic(X, y)\n\n"
        "result = (round(history[0], 6), round(history[-1], 6), len(history))",
    ),
    # Шаг через уже написанный градиент и копии аргументов.
    (
        4011,
        "def logistic_gradient(X, y, w, b):\n"
        '    """Градиент потерь."""\n'
        "    X = np.asarray(X, dtype=float)\n"
        "    y = np.asarray(y, dtype=float)\n"
        "    proba = 1.0 / (1.0 + np.exp(-(X @ w + b)))\n"
        "    return X.T @ (proba - y) / len(y), float(np.mean(proba - y))\n\n\n"
        "def descent_step(X, y, w, b, learning_rate):\n"
        '    """Шаг по градиенту, без правки аргументов."""\n'
        "    grad_w, grad_b = logistic_gradient(X, y, w, b)\n"
        "    new_w = np.array(w, dtype=float)\n"
        "    return new_w - learning_rate * grad_w, float(b) - learning_rate * grad_b\n\n\n"
        "new_w, new_b = descent_step(X, y, w, b, 0.1)\n\n"
        "result = (\n"
        "    round(float(new_w[0]), 6),\n"
        "    round(float(new_w[1]), 6),\n"
        "    round(float(new_b), 6),\n"
        ")",
    ),
    # Классы через np.where.
    (
        4013,
        "def predict_labels(proba, threshold=0.5):\n"
        '    """Классы через np.where."""\n'
        "    proba = np.asarray(proba, dtype=float)\n"
        "    return np.where(proba >= threshold, 1, 0).astype(int)\n\n\n"
        "result = (\n"
        "    predict_labels([0.9, 0.5, 0.499999], 0.5).tolist(),\n"
        "    predict_labels([0.5], 0.51).tolist(),\n"
        "    predict_labels([0.7]).tolist(),\n"
        ")",
    ),
    # Отчёт собирается циклом, точность — отношение сумм.
    (
        4014,
        "def threshold_report(y_true, proba, thresholds):\n"
        '    """Отчёт циклом по порогам."""\n'
        "    y_true = np.asarray(y_true, dtype=float)\n"
        "    proba = np.asarray(proba, dtype=float)\n"
        "    report = []\n"
        "    for threshold in thresholds:\n"
        "        labels = np.where(proba >= float(threshold), 1, 0)\n"
        "        hits = float(np.sum(labels == y_true))\n"
        "        report.append((float(threshold), int(np.count_nonzero(labels)), hits / labels.size))\n"
        "    return report\n\n\n"
        "report = threshold_report(y, proba, (0.3, 0.5, 0.7))\n\n"
        "result = [(float(t), int(n), round(float(a), 4)) for t, n, a in report]",
    ),
    # Тот же класс через softplus и np.where.
    (
        4016,
        "class MyLogisticRegression:\n"
        "    def __init__(self, learning_rate=0.1, n_iterations=2000):\n"
        '        """Класс с сигмоидой через softplus."""\n'
        "        self.learning_rate = learning_rate\n"
        "        self.n_iterations = n_iterations\n"
        "        self.weights = None\n"
        "        self.bias = None\n\n"
        "    def fit(self, X, y):\n"
        '        """Обучает модель и возвращает self."""\n'
        "        X = np.asarray(X, dtype=float)\n"
        "        y = np.asarray(y, dtype=float)\n"
        "        self.weights = np.zeros(X.shape[1])\n"
        "        self.bias = 0.0\n"
        "        step = 0\n"
        "        while step < self.n_iterations:\n"
        "            logits = X @ self.weights + self.bias\n"
        "            proba = np.exp(-np.logaddexp(0.0, -logits))\n"
        "            self.weights = self.weights - self.learning_rate * (X.T @ (proba - y) / X.shape[0])\n"
        "            self.bias = self.bias - self.learning_rate * float(np.sum(proba - y) / X.shape[0])\n"
        "            step += 1\n"
        "        return self\n\n"
        "    def predict_proba(self, X):\n"
        '        """Вероятности через softplus от −z."""\n'
        "        logits = np.atleast_2d(np.asarray(X, dtype=float)) @ self.weights + self.bias\n"
        "        return np.exp(-np.logaddexp(0.0, -logits))\n\n"
        "    def predict(self, X, threshold=0.5):\n"
        '        """Классы через np.where."""\n'
        "        return np.where(self.predict_proba(X) >= threshold, 1, 0)\n\n\n"
        "model = MyLogisticRegression().fit(X, y)\n"
        "small = MyLogisticRegression(n_iterations=1000).fit(X_small, y_small)\n\n"
        "result = (\n"
        "    round(float(np.mean(model.predict(X) == y)), 4),\n"
        "    int(small.predict(X_small).sum()),\n"
        "    round(float(small.predict_proba(X_small)[0]), 4),\n"
        "    round(float(small.bias), 4),\n"
        ")",
    ),
]

# Главная ловушка задания: решение выглядит разумным, но проверку не проходит.
WRONG = {
    # Знак в экспоненте потерян: это σ(−z), а не σ(z).
    4001: "def sigmoid(z):\n"
    '    """Сигмоида с потерянным знаком."""\n'
    "    return 1.0 / (1.0 + np.exp(np.asarray(z, dtype=float)))\n\n\n"
    "result = (\n"
    "    round(float(sigmoid(0.0)), 4),\n"
    "    round(float(sigmoid(1.0)), 6),\n"
    "    bool(np.allclose(sigmoid(np.array([-2.0, 0.0, 2.0])), [0.1192, 0.5, 0.8808], atol=1e-4)),\n"
    ")",
    # В производной плюс вместо минуса: в нуле выходит 0.75.
    4002: "def sigmoid(z):\n"
    '    """Логистическая функция."""\n'
    "    return 1.0 / (1.0 + np.exp(-np.asarray(z, dtype=float)))\n\n\n"
    "def sigmoid_derivative(z):\n"
    '    """Производная с плюсом внутри."""\n'
    "    values = sigmoid(z)\n"
    "    return values * (1 + values)\n\n\n"
    "result = (\n"
    "    round(float(sigmoid_derivative(0.0)), 4),\n"
    "    round(float(sigmoid_derivative(5.0)), 6),\n"
    "    round(float(sigmoid_derivative(10.0)), 8),\n"
    "    round(float(np.max(sigmoid_derivative(np.linspace(-10, 10, 401)))), 4),\n"
    ")",
    # Взято только первое слагаемое: объекты класса 0 перестали стоить.
    4004: "def log_loss(y_true, proba):\n"
    '    """Потеря только по первому слагаемому."""\n'
    "    y_true = np.asarray(y_true, dtype=float)\n"
    "    proba = np.clip(np.asarray(proba, dtype=float), 1e-12, 1 - 1e-12)\n"
    "    return float(-np.mean(y_true * np.log(proba)))\n\n\n"
    "result = (\n"
    "    round(log_loss([1], [0.9]), 6),\n"
    "    round(log_loss([1], [0.1]), 6),\n"
    "    round(log_loss([1], [0.5]), 6),\n"
    "    round(log_loss([1, 0], [0.8, 0.3]), 6),\n"
    ")",
    # Потеря считается через сигмоиду без зажима: на больших логитах nan.
    4005: "def log_loss_from_logits(y_true, z):\n"
    '    """Потеря через сигмоиду без зажима."""\n'
    "    y_true = np.asarray(y_true, dtype=float)\n"
    "    z = np.asarray(z, dtype=float)\n"
    "    proba = 1.0 / (1.0 + np.exp(-z))\n"
    "    return float(-np.mean(y_true * np.log(proba) + (1 - y_true) * np.log(1 - proba)))\n\n\n"
    "result = (\n"
    "    round(log_loss_from_logits([1], [0.0]), 6),\n"
    "    round(log_loss_from_logits([1], [1000.0]), 8),\n"
    "    round(log_loss_from_logits([1], [-1000.0]), 6),\n"
    "    bool(np.isfinite(log_loss_from_logits([1, 0], [1000.0, -1000.0]))),\n"
    ")",
    # Вернулись вероятности обученной модели: аргументы игнорируются.
    4007: "def predict_proba(X, w, b):\n"
    '    """Вероятности обученной модели вместо переданных весов."""\n'
    "    return proba\n\n\n"
    "def mean_log_loss(y_true, proba):\n"
    '    """Средняя логарифмическая потеря с зажимом."""\n'
    "    y_true = np.asarray(y_true, dtype=float)\n"
    "    proba = np.clip(np.asarray(proba, dtype=float), 1e-12, 1 - 1e-12)\n"
    "    return float(-np.mean(y_true * np.log(proba) + (1 - y_true) * np.log(1 - proba)))\n\n\n"
    "result = (\n"
    "    round(mean_log_loss(y, predict_proba(X, w, b)), 4),\n"
    "    round(float(np.mean((predict_proba(X, w, b) >= 0.5) == y)), 4),\n"
    "    round(mean_log_loss(y, predict_proba(X, w_other, b_other)), 4),\n"
    "    round(float(np.mean((predict_proba(X, w_other, b_other) >= 0.5) == y)), 4),\n"
    ")",
    # Знак градиента перепутан: шаг уводит в гору, потеря растёт.
    4008: "def logistic_gradient(X, y, w, b):\n"
    '    """Градиент со знаком (y − h)."""\n'
    "    X = np.asarray(X, dtype=float)\n"
    "    y = np.asarray(y, dtype=float)\n"
    "    proba = 1.0 / (1.0 + np.exp(-(X @ w + b)))\n"
    "    return X.T @ (y - proba) / len(y), float(np.mean(y - proba))\n\n\n"
    "grad_w, grad_b = logistic_gradient(X, y, w, b)\n\n"
    "result = (\n"
    "    round(float(grad_w[0]), 8),\n"
    "    round(float(grad_w[1]), 8),\n"
    "    round(float(grad_b), 8),\n"
    ")",
    # Множитель 1/m забыт: градиент в 200 раз больше, спуск расходится.
    4010: "def fit_logistic(X, y, learning_rate=0.1, n_iterations=2000):\n"
    '    """Спуск без деления на число объектов."""\n'
    "    X = np.asarray(X, dtype=float)\n"
    "    y = np.asarray(y, dtype=float)\n"
    "    weights = np.zeros(X.shape[1])\n"
    "    bias = 0.0\n\n"
    "    def loss():\n"
    "        proba = np.clip(1.0 / (1.0 + np.exp(-(X @ weights + bias))), 1e-12, 1 - 1e-12)\n"
    "        return float(-np.mean(y * np.log(proba) + (1 - y) * np.log(1 - proba)))\n\n"
    "    history = [loss()]\n"
    "    for _ in range(n_iterations):\n"
    "        proba = 1.0 / (1.0 + np.exp(-(X @ weights + bias)))\n"
    "        weights = weights - learning_rate * (X.T @ (proba - y))\n"
    "        bias = bias - learning_rate * float(np.sum(proba - y))\n"
    "        history.append(loss())\n"
    "    return weights, bias, history\n\n\n"
    "weights, bias, history = fit_logistic(X, y)\n\n"
    "result = (round(history[0], 6), round(history[-1], 6), len(history))",
    # Веса обновляются на месте: у вызывающей стороны испорчен исходный массив.
    4011: "def descent_step(X, y, w, b, learning_rate):\n"
    '    """Шаг с обновлением на месте."""\n'
    "    X = np.asarray(X, dtype=float)\n"
    "    proba = 1.0 / (1.0 + np.exp(-(X @ w + b)))\n"
    "    w -= learning_rate * (X.T @ (proba - y) / len(y))\n"
    "    b -= learning_rate * float(np.mean(proba - y))\n"
    "    return w, b\n\n\n"
    "new_w, new_b = descent_step(X, y, w, b, 0.1)\n\n"
    "result = (\n"
    "    round(float(new_w[0]), 6),\n"
    "    round(float(new_w[1]), 6),\n"
    "    round(float(new_b), 6),\n"
    ")",
    # Округление вместо порога: вероятность ровно 0.5 уезжает в класс 0.
    4013: "def predict_labels(proba, threshold=0.5):\n"
    '    """Классы округлением вероятности."""\n'
    "    return np.round(np.asarray(proba, dtype=float)).astype(int)\n\n\n"
    "result = (\n"
    "    predict_labels([0.9, 0.5, 0.499999], 0.5).tolist(),\n"
    "    predict_labels([0.5], 0.51).tolist(),\n"
    "    predict_labels([0.7]).tolist(),\n"
    ")",
    # Порог игнорируется: вероятности округляются, строки выходят одинаковыми.
    4014: "def threshold_report(y_true, proba, thresholds):\n"
    '    """Отчёт по округлённым вероятностям."""\n'
    "    y_true = np.asarray(y_true, dtype=float)\n"
    "    proba = np.asarray(proba, dtype=float)\n"
    "    report = []\n"
    "    for threshold in thresholds:\n"
    "        labels = np.round(proba).astype(int)\n"
    "        report.append(\n"
    "            (float(threshold), int(labels.sum()), float(np.mean(labels == y_true)))\n"
    "        )\n"
    "    return report\n\n\n"
    "report = threshold_report(y, proba, (0.3, 0.5, 0.7))\n\n"
    "result = [(float(t), int(n), round(float(a), 4)) for t, n, a in report]",
    # fit забыл вернуть self: цепочка model.fit(X, y).predict(X) ломается.
    4016: "class MyLogisticRegression:\n"
    "    def __init__(self, learning_rate=0.1, n_iterations=2000):\n"
    '        """Класс без возврата self из fit."""\n'
    "        self.learning_rate = learning_rate\n"
    "        self.n_iterations = n_iterations\n"
    "        self.weights = None\n"
    "        self.bias = None\n\n"
    "    def fit(self, X, y):\n"
    '        """Обучает модель, но ничего не возвращает."""\n'
    "        X = np.asarray(X, dtype=float)\n"
    "        y = np.asarray(y, dtype=float)\n"
    "        n_samples = X.shape[0]\n"
    "        self.weights = np.zeros(X.shape[1])\n"
    "        self.bias = 0.0\n"
    "        for _ in range(self.n_iterations):\n"
    "            proba = 1.0 / (1.0 + np.exp(-(X @ self.weights + self.bias)))\n"
    "            self.weights = self.weights - self.learning_rate * (X.T @ (proba - y) / n_samples)\n"
    "            self.bias = self.bias - self.learning_rate * float(np.mean(proba - y))\n\n"
    "    def predict_proba(self, X):\n"
    '        """Вероятности класса 1."""\n'
    "        logits = np.asarray(X, dtype=float) @ self.weights + self.bias\n"
    "        return 1.0 / (1.0 + np.exp(-logits))\n\n"
    "    def predict(self, X, threshold=0.5):\n"
    '        """Классы 0 и 1 по порогу."""\n'
    "        return (self.predict_proba(X) >= threshold).astype(int)\n\n\n"
    "model = MyLogisticRegression(learning_rate=0.1, n_iterations=2000)\n"
    "model.fit(X, y)\n"
    "result = round(float(np.mean(model.predict(X) == y)), 4)",
}

MORE_WRONG = [
    # Комплемент вместо вероятности класса 1.
    (
        4001,
        "def sigmoid(z):\n"
        '    """1 − σ(z) вместо σ(z)."""\n'
        "    z = np.asarray(z, dtype=float)\n"
        "    return np.exp(-z) / (1.0 + np.exp(-z))\n\n\n"
        "result = (\n"
        "    round(float(sigmoid(0.0)), 4),\n"
        "    round(float(sigmoid(1.0)), 6),\n"
        "    bool(np.allclose(sigmoid(np.array([-2.0, 0.0, 2.0])), [0.1192, 0.5, 0.8808], atol=1e-4)),\n"
        ")",
    ),
    # Производная не посчитана: возвращается сама сигмоида.
    (
        4002,
        "def sigmoid(z):\n"
        '    """Логистическая функция."""\n'
        "    return 1.0 / (1.0 + np.exp(-np.asarray(z, dtype=float)))\n\n\n"
        "def sigmoid_derivative(z):\n"
        '    """Возвращает саму сигмоиду."""\n'
        "    return sigmoid(z)\n\n\n"
        "result = (\n"
        "    round(float(sigmoid_derivative(0.0)), 4),\n"
        "    round(float(sigmoid_derivative(5.0)), 6),\n"
        "    round(float(sigmoid_derivative(10.0)), 8),\n"
        "    round(float(np.max(sigmoid_derivative(np.linspace(-10, 10, 401)))), 4),\n"
        ")",
    ),
    # Сумма вместо среднего: потеря растёт с длиной выборки.
    (
        4004,
        "def log_loss(y_true, proba):\n"
        '    """Сумма цен вместо среднего."""\n'
        "    y_true = np.asarray(y_true, dtype=float)\n"
        "    proba = np.clip(np.asarray(proba, dtype=float), 1e-12, 1 - 1e-12)\n"
        "    return float(-np.sum(y_true * np.log(proba) + (1 - y_true) * np.log(1 - proba)))\n\n\n"
        "result = (\n"
        "    round(log_loss([1], [0.9]), 6),\n"
        "    round(log_loss([1], [0.1]), 6),\n"
        "    round(log_loss([1], [0.5]), 6),\n"
        "    round(log_loss([1, 0], [0.8, 0.3]), 6),\n"
        ")",
    ),
    # Знак второго слагаемого перепутан: y z прибавляется, а не вычитается.
    (
        4005,
        "def log_loss_from_logits(y_true, z):\n"
        '    """Знак у произведения перепутан."""\n'
        "    y_true = np.asarray(y_true, dtype=float)\n"
        "    z = np.asarray(z, dtype=float)\n"
        "    return float(np.mean(np.logaddexp(0.0, z) + y_true * z))\n\n\n"
        "result = (\n"
        "    round(log_loss_from_logits([1], [0.0]), 6),\n"
        "    round(log_loss_from_logits([1], [1000.0]), 8),\n"
        "    round(log_loss_from_logits([1], [-1000.0]), 6),\n"
        "    bool(np.isfinite(log_loss_from_logits([1, 0], [1000.0, -1000.0]))),\n"
        ")",
    ),
    # Возвращаются классы вместо вероятностей.
    (
        4007,
        "def predict_proba(X, w, b):\n"
        '    """Классы вместо вероятностей."""\n'
        "    logits = np.asarray(X, dtype=float) @ np.asarray(w, dtype=float) + b\n"
        "    return logits >= 0\n\n\n"
        "def mean_log_loss(y_true, proba):\n"
        '    """Средняя логарифмическая потеря с зажимом."""\n'
        "    y_true = np.asarray(y_true, dtype=float)\n"
        "    proba = np.clip(np.asarray(proba, dtype=float), 1e-12, 1 - 1e-12)\n"
        "    return float(-np.mean(y_true * np.log(proba) + (1 - y_true) * np.log(1 - proba)))\n\n\n"
        "result = (\n"
        "    round(mean_log_loss(y, predict_proba(X, w, b)), 4),\n"
        "    round(float(np.mean((predict_proba(X, w, b) >= 0.5) == y)), 4),\n"
        "    round(mean_log_loss(y, predict_proba(X, w_other, b_other)), 4),\n"
        "    round(float(np.mean((predict_proba(X, w_other, b_other) >= 0.5) == y)), 4),\n"
        ")",
    ),
    # Градиент по смещению просуммирован, а не усреднён.
    (
        4008,
        "def logistic_gradient(X, y, w, b):\n"
        '    """Градиент по смещению без деления на m."""\n'
        "    X = np.asarray(X, dtype=float)\n"
        "    y = np.asarray(y, dtype=float)\n"
        "    proba = 1.0 / (1.0 + np.exp(-(X @ w + b)))\n"
        "    return X.T @ (proba - y) / len(y), float(np.sum(proba - y))\n\n\n"
        "grad_w, grad_b = logistic_gradient(X, y, w, b)\n\n"
        "result = (\n"
        "    round(float(grad_w[0]), 8),\n"
        "    round(float(grad_w[1]), 8),\n"
        "    round(float(grad_b), 8),\n"
        ")",
    ),
    # История потерь без начального значения: длина на единицу меньше.
    (
        4010,
        "def fit_logistic(X, y, learning_rate=0.1, n_iterations=2000):\n"
        '    """Спуск, который записывает потерю только после шага."""\n'
        "    X = np.asarray(X, dtype=float)\n"
        "    y = np.asarray(y, dtype=float)\n"
        "    weights = np.zeros(X.shape[1])\n"
        "    bias = 0.0\n"
        "    history = []\n"
        "    for _ in range(n_iterations):\n"
        "        proba = 1.0 / (1.0 + np.exp(-(X @ weights + bias)))\n"
        "        weights = weights - learning_rate * (X.T @ (proba - y) / len(y))\n"
        "        bias = bias - learning_rate * float(np.mean(proba - y))\n"
        "        loss = np.clip(1.0 / (1.0 + np.exp(-(X @ weights + bias))), 1e-12, 1 - 1e-12)\n"
        "        history.append(float(-np.mean(y * np.log(loss) + (1 - y) * np.log(1 - loss))))\n"
        "    return weights, bias, history\n\n\n"
        "weights, bias, history = fit_logistic(X, y)\n\n"
        "result = (round(history[0], 6), round(history[-1], 6), len(history))",
    ),
    # Шаг забыл множитель 1/m: сдвиг в 200 раз больше градиента.
    (
        4011,
        "def descent_step(X, y, w, b, learning_rate):\n"
        '    """Шаг по неусреднённому градиенту."""\n'
        "    X = np.asarray(X, dtype=float)\n"
        "    proba = 1.0 / (1.0 + np.exp(-(X @ w + b)))\n"
        "    grad_w = X.T @ (proba - y)\n"
        "    grad_b = float(np.sum(proba - y))\n"
        "    return np.asarray(w, dtype=float) - learning_rate * grad_w, float(b) - learning_rate * grad_b\n\n\n"
        "new_w, new_b = descent_step(X, y, w, b, 0.1)\n\n"
        "result = (\n"
        "    round(float(new_w[0]), 6),\n"
        "    round(float(new_w[1]), 6),\n"
        "    round(float(new_b), 6),\n"
        ")",
    ),
    # Сравнение строгое: вероятность ровно 0.5 уезжает в класс 0.
    (
        4013,
        "def predict_labels(proba, threshold=0.5):\n"
        '    """Классы строгим сравнением."""\n'
        "    return (np.asarray(proba, dtype=float) > threshold).astype(int)\n\n\n"
        "result = (\n"
        "    predict_labels([0.9, 0.5, 0.499999], 0.5).tolist(),\n"
        "    predict_labels([0.5], 0.51).tolist(),\n"
        "    predict_labels([0.7]).tolist(),\n"
        ")",
    ),
    # Логические значения вместо целых классов.
    (
        4013,
        "def predict_labels(proba, threshold=0.5):\n"
        '    """Массив True/False вместо 0 и 1."""\n'
        "    return np.asarray(proba, dtype=float) >= threshold\n\n\n"
        "result = (\n"
        "    predict_labels([0.9, 0.5, 0.499999], 0.5).tolist(),\n"
        "    predict_labels([0.5], 0.51).tolist(),\n"
        "    predict_labels([0.7]).tolist(),\n"
        ")",
    ),
    # Точность в процентах: 86.5 вместо 0.865.
    (
        4014,
        "def threshold_report(y_true, proba, thresholds):\n"
        '    """Отчёт с точностью в процентах."""\n'
        "    y_true = np.asarray(y_true, dtype=float)\n"
        "    proba = np.asarray(proba, dtype=float)\n"
        "    report = []\n"
        "    for threshold in thresholds:\n"
        "        labels = (proba >= threshold).astype(int)\n"
        "        report.append(\n"
        "            (float(threshold), int(labels.sum()), 100.0 * float(np.mean(labels == y_true)))\n"
        "        )\n"
        "    return report\n\n\n"
        "report = threshold_report(y, proba, (0.3, 0.5, 0.7))\n\n"
        "result = [(float(t), int(n), round(float(a), 4)) for t, n, a in report]",
    ),
    # Логиты вместо вероятностей: predict_proba без сигмоиды.
    (
        4016,
        "class MyLogisticRegression:\n"
        "    def __init__(self, learning_rate=0.1, n_iterations=2000):\n"
        '        """Класс, у которого predict_proba возвращает логиты."""\n'
        "        self.learning_rate = learning_rate\n"
        "        self.n_iterations = n_iterations\n"
        "        self.weights = None\n"
        "        self.bias = None\n\n"
        "    def fit(self, X, y):\n"
        '        """Обучает модель и возвращает self."""\n'
        "        X = np.asarray(X, dtype=float)\n"
        "        y = np.asarray(y, dtype=float)\n"
        "        n_samples = X.shape[0]\n"
        "        self.weights = np.zeros(X.shape[1])\n"
        "        self.bias = 0.0\n"
        "        for _ in range(self.n_iterations):\n"
        "            proba = 1.0 / (1.0 + np.exp(-(X @ self.weights + self.bias)))\n"
        "            self.weights = self.weights - self.learning_rate * (X.T @ (proba - y) / n_samples)\n"
        "            self.bias = self.bias - self.learning_rate * float(np.mean(proba - y))\n"
        "        return self\n\n"
        "    def predict_proba(self, X):\n"
        '        """Логиты вместо вероятностей."""\n'
        "        return np.asarray(X, dtype=float) @ self.weights + self.bias\n\n"
        "    def predict(self, X, threshold=0.5):\n"
        '        """Классы 0 и 1 по порогу."""\n'
        "        return (self.predict_proba(X) >= threshold).astype(int)\n\n\n"
        "model = MyLogisticRegression(learning_rate=0.1, n_iterations=2000).fit(X, y)\n"
        "result = round(float(np.mean(model.predict(X) == y)), 4)",
    ),
]

# Решения, падающие ошибкой Python: подсказки в них нет, но и ответа тоже.
CRASHES = [
    # math.exp на массиве: скалярная экспонента с массивом не работает.
    (
        4001,
        TypeError,
        "import math\n\n\n"
        "def sigmoid(z):\n"
        '    """Сигмоида через math.exp."""\n'
        "    return 1.0 / (1.0 + math.exp(-z))\n\n\n"
        "result = (\n"
        "    round(float(sigmoid(0.0)), 4),\n"
        "    round(float(sigmoid(1.0)), 6),\n"
        "    bool(np.allclose(sigmoid(np.array([-2.0, 0.0, 2.0])), [0.1192, 0.5, 0.8808], atol=1e-4)),\n"
        ")",
    ),
    # math.log от нулевой вероятности: домен логарифма кончился.
    (
        4004,
        ValueError,
        "import math\n\n\n"
        "def log_loss(y_true, proba):\n"
        '    """Потеря через math.log без зажима."""\n'
        "    total = 0.0\n"
        "    for label, p in zip(y_true, proba):\n"
        "        total += -(label * math.log(p) + (1 - label) * math.log(1 - p))\n"
        "    return total / len(y_true)\n\n\n"
        "result = (\n"
        "    round(log_loss([1], [0.9]), 6),\n"
        "    round(log_loss([1], [0.1]), 6),\n"
        "    round(log_loss([1], [0.5]), 6),\n"
        "    round(log_loss([1, 0], [0.8, 0.3]), 6),\n"
        ")",
    ),
    # math.exp переполняется на логите в тысячу.
    (
        4005,
        OverflowError,
        "import math\n\n\n"
        "def log_loss_from_logits(y_true, z):\n"
        '    """Потеря через math.exp: на больших логитах переполнение."""\n'
        "    total = 0.0\n"
        "    for label, value in zip(y_true, z):\n"
        "        total += math.log(1.0 + math.exp(-label * value))\n"
        "    return total / len(y_true)\n\n\n"
        "result = (\n"
        "    round(log_loss_from_logits([1], [0.0]), 6),\n"
        "    round(log_loss_from_logits([1], [1000.0]), 8),\n"
        "    round(log_loss_from_logits([1], [-1000.0]), 6),\n"
        "    bool(np.isfinite(log_loss_from_logits([1, 0], [1000.0, -1000.0]))),\n"
        ")",
    ),
    # math.exp на матрице прямого прохода.
    (
        4008,
        TypeError,
        "import math\n\n\n"
        "def logistic_gradient(X, y, w, b):\n"
        '    """Градиент со скалярной экспонентой."""\n'
        "    proba = 1.0 / (1.0 + math.exp(-(X @ w + b)))\n"
        "    return X.T @ (proba - y) / len(y), float(np.mean(proba - y))\n\n\n"
        "grad_w, grad_b = logistic_gradient(X, y, w, b)\n\n"
        "result = (\n"
        "    round(float(grad_w[0]), 8),\n"
        "    round(float(grad_w[1]), 8),\n"
        "    round(float(grad_b), 8),\n"
        ")",
    ),
    # История заведена как список и заполняется по индексу.
    (
        4010,
        IndexError,
        "def fit_logistic(X, y, learning_rate=0.1, n_iterations=2000):\n"
        '    """Спуск с историей по индексу."""\n'
        "    X = np.asarray(X, dtype=float)\n"
        "    y = np.asarray(y, dtype=float)\n"
        "    weights = np.zeros(X.shape[1])\n"
        "    bias = 0.0\n"
        "    history = []\n"
        "    for index in range(n_iterations + 1):\n"
        "        proba = np.clip(1.0 / (1.0 + np.exp(-(X @ weights + bias))), 1e-12, 1 - 1e-12)\n"
        "        history[index] = float(-np.mean(y * np.log(proba) + (1 - y) * np.log(1 - proba)))\n"
        "        if index < n_iterations:\n"
        "            weights = weights - learning_rate * (X.T @ (proba - y) / len(y))\n"
        "            bias = bias - learning_rate * float(np.mean(proba - y))\n"
        "    return weights, bias, history\n\n\n"
        "weights, bias, history = fit_logistic(X, y)\n\n"
        "result = (round(history[0], 6), round(history[-1], 6), len(history))",
    ),
    # Скалярная экспонента на векторе вероятностей.
    (
        4011,
        TypeError,
        "import math\n\n\n"
        "def descent_step(X, y, w, b, learning_rate):\n"
        '    """Шаг со скалярной экспонентой."""\n'
        "    proba = 1.0 / (1.0 + math.exp(-(X @ w + b)))\n"
        "    grad_w = X.T @ (proba - y) / len(y)\n"
        "    grad_b = float(np.mean(proba - y))\n"
        "    return np.asarray(w, dtype=float) - learning_rate * grad_w, float(b) - learning_rate * grad_b\n\n\n"
        "new_w, new_b = descent_step(X, y, w, b, 0.1)\n\n"
        "result = (\n"
        "    round(float(new_w[0]), 6),\n"
        "    round(float(new_w[1]), 6),\n"
        "    round(float(new_b), 6),\n"
        ")",
    ),
    # Список сравнивается с порогом напрямую: приведение к массиву забыто.
    (
        4013,
        TypeError,
        "def predict_labels(proba, threshold=0.5):\n"
        '    """Классы без приведения к массиву."""\n'
        "    return (proba >= threshold).astype(int)\n\n\n"
        "result = (\n"
        "    predict_labels([0.9, 0.5, 0.499999], 0.5).tolist(),\n"
        "    predict_labels([0.5], 0.51).tolist(),\n"
        "    predict_labels([0.7]).tolist(),\n"
        ")",
    ),
    # Вероятности сравниваются сразу со всеми порогами: формы не совпадают.
    (
        4014,
        ValueError,
        "def threshold_report(y_true, proba, thresholds):\n"
        '    """Пороги применяются одним сравнением со всеми сразу."""\n'
        "    y_true = np.asarray(y_true, dtype=float)\n"
        "    proba = np.asarray(proba, dtype=float)\n"
        "    labels = (proba >= thresholds).astype(int)\n"
        "    return [\n"
        "        (float(threshold), int(labels.sum()), float(np.mean(labels == y_true)))\n"
        "        for threshold in thresholds\n"
        "    ]\n\n\n"
        "report = threshold_report(y, proba, (0.3, 0.5, 0.7))\n\n"
        "result = [(float(t), int(n), round(float(a), 4)) for t, n, a in report]",
    ),
    # Конструктор без значений по умолчанию: вызов только с n_iterations падает.
    (
        4016,
        TypeError,
        "class MyLogisticRegression:\n"
        "    def __init__(self, learning_rate, n_iterations):\n"
        '        """Класс без значений по умолчанию у конструктора."""\n'
        "        self.learning_rate = learning_rate\n"
        "        self.n_iterations = n_iterations\n"
        "        self.weights = None\n"
        "        self.bias = None\n\n"
        "    def fit(self, X, y):\n"
        '        """Обучает модель и возвращает self."""\n'
        "        X = np.asarray(X, dtype=float)\n"
        "        y = np.asarray(y, dtype=float)\n"
        "        n_samples = X.shape[0]\n"
        "        self.weights = np.zeros(X.shape[1])\n"
        "        self.bias = 0.0\n"
        "        for _ in range(self.n_iterations):\n"
        "            proba = 1.0 / (1.0 + np.exp(-(X @ self.weights + self.bias)))\n"
        "            self.weights = self.weights - self.learning_rate * (X.T @ (proba - y) / n_samples)\n"
        "            self.bias = self.bias - self.learning_rate * float(np.mean(proba - y))\n"
        "        return self\n\n"
        "    def predict_proba(self, X):\n"
        '        """Вероятности класса 1."""\n'
        "        logits = np.asarray(X, dtype=float) @ self.weights + self.bias\n"
        "        return 1.0 / (1.0 + np.exp(-logits))\n\n"
        "    def predict(self, X, threshold=0.5):\n"
        '        """Классы 0 и 1 по порогу."""\n'
        "        return (self.predict_proba(X) >= threshold).astype(int)\n\n\n"
        "model = MyLogisticRegression(0.1, 2000).fit(X, y)\n"
        "result = round(float(np.mean(model.predict(X) == y)), 4)",
    ),
]
