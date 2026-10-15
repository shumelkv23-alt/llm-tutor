"""Задания с кодом модуля 2 курса ИСИТ (NumPy, pandas и данные).

Таблицы для `tests/test_isit_code_items.py`: эталон, верная альтернатива,
ловушки и падения на каждое задание занятия `arrays` (2001, 2003, 2005, 2007).
Модуль 1 остался в самом тестовом файле — его писали занятие за занятием и
ревью уже прошло; дальше каждый модуль живёт своим файлом, иначе общий файл
растёт на сотни строк на каждое занятие.

`WRONG` — по одной главной ловушке на задание (это словарь: вторая запись с тем
же id молча затрёт первую), остальные варианты — в `MORE_WRONG`, а те, что
падают ошибкой Python без подсказки, — в `CRASHES`.
"""

# Эталонные решения: и функция, и то, что в result лежит её результат.
SOLUTIONS = {
    # Пары индексов, а не подматрица: matrix[rows, cols].
    2001: "def construct_array(matrix, row_indices, col_indices):\n"
    '    """Элементы matrix по парам индексов."""\n'
    "    return matrix[row_indices, col_indices]\n\n\n"
    "result = (\n"
    "    list(construct_array(np.arange(42).reshape(7, 6), [3, 3, 0], [2, 1, 5])),\n"
    "    list(construct_array(np.arange(42).reshape(7, 6), [], [])),\n"
    ")",
    # Средние по каналам: ось канала остаётся, пространственные исчезают.
    2003: "def mean_channel(X):\n"
    '    """Средние значения по трём каналам картинки."""\n'
    "    return X.mean(axis=(0, 1))\n\n\n"
    "picture = np.arange(18).reshape(2, 3, 3)\n"
    "result = (list(mean_channel(picture)), picture.shape)",
    # Нули между элементами: заготовка нужной длины плюс присваивание срезу.
    2005: "def add_zeros(x):\n"
    '    """Массив с нулями между элементами."""\n'
    "    if len(x) == 0:\n"
    "        return x.copy()\n"
    "    out = np.zeros(2 * len(x) - 1, dtype=x.dtype)\n"
    "    out[::2] = x\n"
    "    return out\n\n\n"
    "sample = np.array([1, -5, 3])\n"
    "result = (\n"
    "    list(add_zeros(sample)),\n"
    "    list(add_zeros(np.array([7]))),\n"
    "    list(add_zeros(np.array([], dtype=int))),\n"
    "    add_zeros(sample).dtype == sample.dtype,\n"
    ")",
    # Итоговое задание: равенство с формой и матрица из двух векторов.
    2007: "def detect_identic(lhs_array, rhs_array):\n"
    '    """Совпадают ли массивы по форме и значениям."""\n'
    "    return np.array_equal(lhs_array, rhs_array)\n\n\n"
    "def construct_matrix(first_array, second_array):\n"
    '    """Матрица из двух векторов: они становятся столбцами."""\n'
    "    return np.stack([first_array, second_array], axis=1)\n\n\n"
    "result = (\n"
    "    detect_identic(np.array([1, 2]), np.array([1, 2.])),\n"
    "    detect_identic(np.array([1, 2]), np.array([[1, 2]])),\n"
    "    construct_matrix(np.array([1, 2, 3]), np.array([4, 5, 6])).tolist(),\n"
    ")",
}

# Верные альтернативы: другое решение той же задачи — проверки обязаны пропустить.
ALTERNATIVES = [
    # Отбор по парам можно собрать и поштучно: индексация матрицы тут не обязательна.
    (
        2001,
        "def construct_array(matrix, row_indices, col_indices):\n"
        '    """Тот же отбор, но элементы берутся по одному."""\n'
        "    picked = [matrix[row, col] for row, col in zip(row_indices, col_indices)]\n"
        "    return np.array(picked, dtype=matrix.dtype)\n\n\n"
        "result = (\n"
        "    list(construct_array(np.arange(42).reshape(7, 6), [3, 3, 0], [2, 1, 5])),\n"
        "    list(construct_array(np.arange(42).reshape(7, 6), [], [])),\n"
        ")",
    ),
    # Пространственные оси можно схлопнуть и вручную, переложив пиксели в строки.
    (
        2003,
        "def mean_channel(X):\n"
        '    """Средние по каналам через плоскую таблицу пикселей."""\n'
        "    return X.reshape(-1, X.shape[-1]).mean(axis=0)\n\n\n"
        "picture = np.arange(18).reshape(2, 3, 3)\n"
        "result = (list(mean_channel(picture)), picture.shape)",
    ),
    # insert делает ту же вставку без арифметики длины.
    (
        2005,
        "def add_zeros(x):\n"
        '    """Нули вставляются штатной функцией."""\n'
        "    return np.insert(x, np.arange(1, len(x)), 0)\n\n\n"
        "sample = np.array([1, -5, 3])\n"
        "result = (\n"
        "    list(add_zeros(sample)),\n"
        "    list(add_zeros(np.array([7]))),\n"
        "    list(add_zeros(np.array([], dtype=int))),\n"
        "    add_zeros(sample).dtype == sample.dtype,\n"
        ")",
    ),
    # column_stack — та же матрица «в столбцы», что и stack(axis=1).
    (
        2007,
        "def detect_identic(lhs_array, rhs_array):\n"
        '    """Равенство формы и значений без array_equal."""\n'
        "    return np.shape(lhs_array) == np.shape(rhs_array) and bool(\n"
        "        np.all(np.asarray(lhs_array) == np.asarray(rhs_array))\n"
        "    )\n\n\n"
        "def construct_matrix(first_array, second_array):\n"
        '    """Матрица из двух векторов."""\n'
        "    return np.column_stack([first_array, second_array])\n\n\n"
        "result = (\n"
        "    detect_identic(np.array([1, 2]), np.array([1, 2.])),\n"
        "    detect_identic(np.array([1, 2]), np.array([[1, 2]])),\n"
        "    construct_matrix(np.array([1, 2, 3]), np.array([4, 5, 6])).tolist(),\n"
        ")",
    ),
]

# Главная ловушка задания: решение выглядит разумным, но проверку не проходит.
WRONG = {
    # Подматрица вместо пар: строки и столбцы берутся все сразу.
    2001: "def construct_array(matrix, row_indices, col_indices):\n"
    '    """Строки и столбцы берутся все сразу."""\n'
    "    return matrix[row_indices][:, col_indices]\n\n\n"
    "result = (\n"
    "    list(construct_array(np.arange(42).reshape(7, 6), [3, 3, 0], [2, 1, 5])),\n"
    "    list(construct_array(np.arange(42).reshape(7, 6), [], [])),\n"
    ")",
    # Не та ось: усреднение по строкам оставляет матрицу, а не три числа.
    2003: "def mean_channel(X):\n"
    '    """Усреднение только по первой оси."""\n'
    "    return X.mean(axis=0)\n\n\n"
    "picture = np.arange(18).reshape(2, 3, 3)\n"
    "result = (list(mean_channel(picture)), picture.shape)",
    # Забыт dtype: заготовка дробная, тип входа не сохраняется.
    2005: "def add_zeros(x):\n"
    '    """Пустой вход обработан, а тип — нет."""\n'
    "    if len(x) == 0:\n"
    "        return x.copy()\n"
    "    out = np.zeros(2 * len(x) - 1)\n"
    "    out[::2] = x\n"
    "    return out\n\n\n"
    "sample = np.array([1, -5, 3])\n"
    "result = (\n"
    "    list(add_zeros(sample)),\n"
    "    list(add_zeros(np.array([7]))),\n"
    "    list(add_zeros(np.array([], dtype=int))),\n"
    "    add_zeros(sample).dtype == sample.dtype,\n"
    ")",
    # Равенство без формы: 1×2 и 2×1 с одинаковыми числами считаются равными.
    2007: "def detect_identic(lhs_array, rhs_array):\n"
    '    """Сравнение по числам, форма не проверяется."""\n'
    "    return np.array_equal(\n"
    "        np.asarray(lhs_array).ravel(), np.asarray(rhs_array).ravel()\n"
    "    )\n\n\n"
    "def construct_matrix(first_array, second_array):\n"
    '    """Матрица из двух векторов."""\n'
    "    return np.stack([first_array, second_array], axis=1)\n\n\n"
    "result = (\n"
    "    detect_identic(np.array([1, 2]), np.array([1, 2.])),\n"
    "    detect_identic(np.array([1, 2]), np.array([[1, 2]])),\n"
    "    construct_matrix(np.array([1, 2, 3]), np.array([4, 5, 6])).tolist(),\n"
    ")",
}

# Ловушки второго круга: похожи на верное решение, но проверку ломают.
MORE_WRONG = [
    # Ответ подобран под первый пример: result совпал, а функция не работает.
    (
        2001,
        "def construct_array(matrix, row_indices, col_indices):\n"
        '    """Ответ подобран под первую пару индексов."""\n'
        "    if len(row_indices) == 0:\n"
        "        return np.zeros(0, dtype=matrix.dtype)\n"
        "    return np.array([20, 19, 5])\n\n\n"
        "result = (\n"
        "    list(construct_array(np.arange(42).reshape(7, 6), [3, 3, 0], [2, 1, 5])),\n"
        "    list(construct_array(np.arange(42).reshape(7, 6), [], [])),\n"
        ")",
    ),
    # Та же ловушка «подматрица», но собранная срезом по столбцам.
    (
        2001,
        "def construct_array(matrix, row_indices, col_indices):\n"
        '    """Сначала строки, потом столбцы."""\n'
        "    rows = matrix[row_indices]\n"
        "    return rows[:, col_indices]\n\n\n"
        "result = (\n"
        "    list(construct_array(np.arange(42).reshape(7, 6), [3, 3, 0], [2, 1, 5])),\n"
        "    list(construct_array(np.arange(42).reshape(7, 6), [], [])),\n"
        ")",
    ),
    # Средняя яркость вместо средних по каналам: три числа есть, смысла нет.
    (
        2003,
        "def mean_channel(X):\n"
        '    """Одно среднее, размноженное на три канала."""\n'
        "    return np.full(3, X.mean())\n\n\n"
        "picture = np.arange(18).reshape(2, 3, 3)\n"
        "result = (list(mean_channel(picture)), picture.shape)",
    ),
    # Сумма вместо среднего: размер тот же, значения втрое больше.
    (
        2003,
        "def mean_channel(X):\n"
        '    """Сумма по пространственным осям."""\n'
        "    return X.sum(axis=(0, 1))\n\n\n"
        "picture = np.arange(18).reshape(2, 3, 3)\n"
        "result = (list(mean_channel(picture)), picture.shape)",
    ),
    # Лишний ноль сзади: числа сдвинуты на нечётные позиции.
    (
        2005,
        "def add_zeros(x):\n"
        '    """Числа встают на нечётные позиции."""\n'
        "    if len(x) == 0:\n"
        "        return x.copy()\n"
        "    out = np.zeros(2 * len(x), dtype=x.dtype)\n"
        "    out[1::2] = x\n"
        "    return out\n\n\n"
        "sample = np.array([1, -5, 3])\n"
        "result = (\n"
        "    list(add_zeros(sample)),\n"
        "    list(add_zeros(np.array([7]))),\n"
        "    list(add_zeros(np.array([], dtype=int))),\n"
        "    add_zeros(sample).dtype == sample.dtype,\n"
        ")",
    ),
    # Заготовка нужной длины, но числа так и не разложены по позициям.
    (
        2005,
        "def add_zeros(x):\n"
        '    """Готовая заготовка возвращается как есть."""\n'
        "    if len(x) == 0:\n"
        "        return x.copy()\n"
        "    return np.zeros(2 * len(x) - 1, dtype=x.dtype)\n\n\n"
        "sample = np.array([1, -5, 3])\n"
        "result = (\n"
        "    list(add_zeros(sample)),\n"
        "    list(add_zeros(np.array([7]))),\n"
        "    list(add_zeros(np.array([], dtype=int))),\n"
        "    add_zeros(sample).dtype == sample.dtype,\n"
        ")",
    ),
    # Векторы стали строками, а не столбцами.
    (
        2007,
        "def detect_identic(lhs_array, rhs_array):\n"
        '    """Совпадают ли массивы по форме и значениям."""\n'
        "    return np.array_equal(lhs_array, rhs_array)\n\n\n"
        "def construct_matrix(first_array, second_array):\n"
        '    """Векторы уложены строками."""\n'
        "    return np.array([first_array, second_array])\n\n\n"
        "result = (\n"
        "    detect_identic(np.array([1, 2]), np.array([1, 2.])),\n"
        "    detect_identic(np.array([1, 2]), np.array([[1, 2]])),\n"
        "    construct_matrix(np.array([1, 2, 3]), np.array([4, 5, 6])).tolist(),\n"
        ")",
    ),
    # Матрица собрана верно, но равенство решает одна форма: 1.0 и 1 равны.
    (
        2007,
        "def detect_identic(lhs_array, rhs_array):\n"
        '    """Сравнивается только форма."""\n'
        "    return np.shape(lhs_array) == np.shape(rhs_array)\n\n\n"
        "def construct_matrix(first_array, second_array):\n"
        '    """Матрица из двух векторов."""\n'
        "    return np.stack([first_array, second_array], axis=1)\n\n\n"
        "result = (\n"
        "    detect_identic(np.array([1, 2]), np.array([1, 2.])),\n"
        "    detect_identic(np.array([1, 2]), np.array([[1, 2]])),\n"
        "    construct_matrix(np.array([1, 2, 3]), np.array([4, 5, 6])).tolist(),\n"
        ")",
    ),
]

# Ловушки, которые падают не `AssertionError`, а ошибкой Python: ученик видит
# трассировку. Тип ошибки тут — часть контракта задания: пустые индексы — это
# пустой ответ, а не исключение; массив нельзя вернуть вместо ответа «да/нет» —
# у него нет одного значения истинности; `dtype` берётся у массива, а не у списка.
CRASHES = [
    (
        2001,
        ValueError,
        "def construct_array(matrix, row_indices, col_indices):\n"
        '    """Пустые индексы считает ошибкой."""\n'
        "    if not row_indices:\n"
        '        raise ValueError("пустые индексы")\n'
        "    return matrix[row_indices, col_indices]\n\n\n"
        "result = (\n"
        "    list(construct_array(np.arange(42).reshape(7, 6), [3, 3, 0], [2, 1, 5])),\n"
        "    list(construct_array(np.arange(42).reshape(7, 6), [], [])),\n"
        ")",
    ),
    (
        2003,
        TypeError,
        "def mean_channel(X):\n"
        '    """Среднее по всей картинке вместо каналов."""\n'
        "    return X.mean()\n\n\n"
        "picture = np.arange(18).reshape(2, 3, 3)\n"
        "result = (list(mean_channel(picture)), picture.shape)",
    ),
    (
        2005,
        ValueError,
        "def add_zeros(x):\n"
        '    """Заготовка как у входа: числа не влезают на чётные позиции."""\n'
        "    out = np.zeros(len(x), dtype=x.dtype)\n"
        "    out[::2] = x\n"
        "    return out\n\n\n"
        "sample = np.array([1, -5, 3])\n"
        "result = (\n"
        "    list(add_zeros(sample)),\n"
        "    list(add_zeros(np.array([7]))),\n"
        "    list(add_zeros(np.array([], dtype=int))),\n"
        "    add_zeros(sample).dtype == sample.dtype,\n"
        ")",
    ),
    (
        2005,
        ValueError,
        "def add_zeros(x):\n"
        '    """Забыта проверка на пустой вход: длина выходит отрицательной."""\n'
        "    out = np.zeros(2 * len(x) - 1, dtype=x.dtype)\n"
        "    out[::2] = x\n"
        "    return out\n\n\n"
        "sample = np.array([1, -5, 3])\n"
        "result = (\n"
        "    list(add_zeros(sample)),\n"
        "    list(add_zeros(np.array([7]))),\n"
        "    list(add_zeros(np.array([], dtype=int))),\n"
        "    add_zeros(sample).dtype == sample.dtype,\n"
        ")",
    ),
    (
        2005,
        AttributeError,
        "def add_zeros(x):\n"
        '    """Результат собран списком."""\n'
        "    if len(x) == 0:\n"
        "        return []\n"
        "    out = [0] * (2 * len(x) - 1)\n"
        "    for index, value in enumerate(x):\n"
        "        out[2 * index] = value\n"
        "    return out\n\n\n"
        "sample = np.array([1, -5, 3])\n"
        "result = (\n"
        "    list(add_zeros(sample)),\n"
        "    list(add_zeros(np.array([7]))),\n"
        "    list(add_zeros(np.array([], dtype=int))),\n"
        "    add_zeros(sample).dtype == sample.dtype,\n"
        ")",
    ),
    (
        2007,
        ValueError,
        "def detect_identic(lhs_array, rhs_array):\n"
        '    """Возвращает массив сравнения вместо ответа."""\n'
        "    return lhs_array == rhs_array\n\n\n"
        "def construct_matrix(first_array, second_array):\n"
        '    """Матрица из двух векторов."""\n'
        "    return np.stack([first_array, second_array], axis=1)\n\n\n"
        "result = (\n"
        "    detect_identic(np.array([1, 2]), np.array([1, 2.])),\n"
        "    detect_identic(np.array([1, 2]), np.array([[1, 2]])),\n"
        "    construct_matrix(np.array([1, 2, 3]), np.array([4, 5, 6])).tolist(),\n"
        ")",
    ),
    (
        2007,
        TypeError,
        "def detect_identic(lhs_array, rhs_array):\n"
        '    """Сравнение через len: скаляры его ломают."""\n'
        "    return len(lhs_array) == len(rhs_array) and bool(\n"
        "        np.all(lhs_array == rhs_array)\n"
        "    )\n\n\n"
        "def construct_matrix(first_array, second_array):\n"
        '    """Матрица из двух векторов."""\n'
        "    return np.stack([first_array, second_array], axis=1)\n\n\n"
        "result = (\n"
        "    detect_identic(np.array([1, 2]), np.array([1, 2.])),\n"
        "    detect_identic(np.array([1, 2]), np.array([[1, 2]])),\n"
        "    construct_matrix(np.array([1, 2, 3]), np.array([4, 5, 6])).tolist(),\n"
        ")",
    ),
    (
        2007,
        AttributeError,
        "def detect_identic(lhs_array, rhs_array):\n"
        '    """Совпадают ли массивы по форме и значениям."""\n'
        "    return np.array_equal(lhs_array, rhs_array)\n\n\n"
        "def construct_matrix(first_array, second_array):\n"
        '    """Матрица собрана списком списков."""\n'
        "    return [[first, second] for first, second in zip(first_array, second_array)]\n\n\n"
        "result = (\n"
        "    detect_identic(np.array([1, 2]), np.array([1, 2.])),\n"
        "    detect_identic(np.array([1, 2]), np.array([[1, 2]])),\n"
        "    construct_matrix(np.array([1, 2, 3]), np.array([4, 5, 6])).tolist(),\n"
        ")",
    ),
]

# --- Занятие `missing` (2011, 2013, 2015) -----------------------------------
# Дальше таблицы занятия дописываются отдельным блоком: так видно, что именно
# принёс один срез, и словари не приходится править по строчке в пяти местах.

_MATRIX = "np.array([[np.nan, 1, 2, 3], [4, np.nan, 5, np.nan]])\n"

SOLUTIONS.update(
    {
        # Маска пропусков: np.isnan, а не сравнение с nan.
        2011: "def nan_mask(matrix):\n"
        '    """Булев массив: True на месте пропуска."""\n'
        "    return np.isnan(matrix)\n\n\n"
        f"matrix = {_MATRIX}"
        "result = (nan_mask(matrix).tolist(), nan_mask(matrix).sum(axis=1).tolist())",
        # Пропуски — на среднее по оставшимся числам, все пропуски — нули.
        2013: "def replace_nans(matrix):\n"
        '    """Пропуски — на среднее по остальным числам."""\n'
        "    mask = np.isnan(matrix)\n"
        "    if mask.all():\n"
        "        return np.zeros_like(matrix)\n"
        "    return np.where(mask, matrix[~mask].mean(), matrix)\n\n\n"
        f"matrix = {_MATRIX}"
        "result = (\n"
        "    replace_nans(matrix).tolist(),\n"
        "    replace_nans(np.array([[np.nan]])).tolist(),\n"
        "    replace_nans(np.array([[]])).tolist(),\n"
        ")",
        # Итоговое задание: среднее по своему столбцу, столбец из пропусков — нули.
        2015: "def fill_by_column(matrix):\n"
        '    """Пропуски — на среднее по своему столбцу."""\n'
        "    mask = np.isnan(matrix)\n"
        "    counts = (~mask).sum(axis=0)\n"
        "    totals = np.where(mask, 0.0, matrix).sum(axis=0)\n"
        "    means = np.divide(totals, counts, out=np.zeros_like(totals), where=counts > 0)\n"
        "    return np.where(mask, means, matrix)\n\n\n"
        f"matrix = {_MATRIX}"
        "result = (\n"
        "    fill_by_column(matrix).tolist(),\n"
        "    fill_by_column(np.array([[1.0, np.nan], [3.0, np.nan]])).tolist(),\n"
        ")",
    }
)

ALTERNATIVES.extend(
    [
        # Пропуск — единственное значение, не равное самому себе.
        (
            2011,
            "def nan_mask(matrix):\n"
            '    """Маска через сравнение матрицы с собой."""\n'
            "    return np.not_equal(matrix, matrix)\n\n\n"
            f"matrix = {_MATRIX}"
            "result = (nan_mask(matrix).tolist(), nan_mask(matrix).sum(axis=1).tolist())",
        ),
        # Заполнение на копии: тот же ответ без np.where.
        (
            2013,
            "def replace_nans(matrix):\n"
            '    """Заполнение в копии матрицы."""\n'
            "    mask = np.isnan(matrix)\n"
            "    if mask.all():\n"
            "        return np.zeros_like(matrix)\n"
            "    filled = matrix.copy()\n"
            "    filled[mask] = matrix[~mask].mean()\n"
            "    return filled\n\n\n"
            f"matrix = {_MATRIX}"
            "result = (\n"
            "    replace_nans(matrix).tolist(),\n"
            "    replace_nans(np.array([[np.nan]])).tolist(),\n"
            "    replace_nans(np.array([[]])).tolist(),\n"
            ")",
        ),
        # Средние по столбцам через суммы и счётчики, деление защищено np.maximum.
        (
            2015,
            "def fill_by_column(matrix):\n"
            '    """Средние по столбцам без np.divide."""\n'
            "    mask = np.isnan(matrix)\n"
            "    totals = np.where(mask, 0.0, matrix).sum(axis=0)\n"
            "    counts = (~mask).sum(axis=0)\n"
            "    means = np.where(counts > 0, totals / np.maximum(counts, 1), 0.0)\n"
            "    return np.where(mask, means, matrix)\n\n\n"
            f"matrix = {_MATRIX}"
            "result = (\n"
            "    fill_by_column(matrix).tolist(),\n"
            "    fill_by_column(np.array([[1.0, np.nan], [3.0, np.nan]])).tolist(),\n"
            ")",
        ),
    ]
)

WRONG.update(
    {
        # Сравнение с nan: маска из одних False, пропусков «нет».
        2011: "def nan_mask(matrix):\n"
        '    """Пропуски ищутся сравнением."""\n'
        "    return matrix == np.nan\n\n\n"
        f"matrix = {_MATRIX}"
        "result = (nan_mask(matrix).tolist(), nan_mask(matrix).sum(axis=1).tolist())",
        # Среднее вместе с пропусками: ответ из nan.
        2013: "def replace_nans(matrix):\n"
        '    """Среднее считается вместе с пропусками."""\n'
        "    mask = np.isnan(matrix)\n"
        "    if mask.all():\n"
        "        return np.zeros_like(matrix)\n"
        "    return np.where(mask, matrix.mean(), matrix)\n\n\n"
        f"matrix = {_MATRIX}"
        "result = (\n"
        "    replace_nans(matrix).tolist(),\n"
        "    replace_nans(np.array([[np.nan]])).tolist(),\n"
        "    replace_nans(np.array([[]])).tolist(),\n"
        ")",
        # Одно среднее на всю матрицу вместо средних по столбцам.
        2015: "def fill_by_column(matrix):\n"
        '    """Одно среднее по всей матрице."""\n'
        "    mask = np.isnan(matrix)\n"
        "    if mask.all():\n"
        "        return np.zeros_like(matrix)\n"
        "    return np.where(mask, matrix[~mask].mean(), matrix)\n\n\n"
        f"matrix = {_MATRIX}"
        "result = (\n"
        "    fill_by_column(matrix).tolist(),\n"
        "    fill_by_column(np.array([[1.0, np.nan], [3.0, np.nan]])).tolist(),\n"
        ")",
    }
)

MORE_WRONG.extend(
    [
        # Маска из нулей и единиц: True/False подменены числами.
        (
            2011,
            "def nan_mask(matrix):\n"
            '    """Маска приводится к целым числам."""\n'
            "    return np.isnan(matrix).astype(int)\n\n\n"
            f"matrix = {_MATRIX}"
            "result = (nan_mask(matrix).tolist(), nan_mask(matrix).sum(axis=1).tolist())",
        ),
        # Вместо маски — координаты пропусков: форма потеряна.
        (
            2011,
            "def nan_mask(matrix):\n"
            '    """Координаты пропусков вместо маски."""\n'
            "    return np.argwhere(np.isnan(matrix))\n\n\n"
            f"matrix = {_MATRIX}"
            "result = (nan_mask(matrix).tolist(), nan_mask(matrix).sum(axis=1).tolist())",
        ),
        # Нет проверки «все пропуски»: среднее по пустому набору.
        (
            2013,
            "def replace_nans(matrix):\n"
            '    """Без проверки на все пропуски."""\n'
            "    mask = np.isnan(matrix)\n"
            "    return np.where(mask, matrix[~mask].mean(), matrix)\n\n\n"
            f"matrix = {_MATRIX}"
            "result = (\n"
            "    replace_nans(matrix).tolist(),\n"
            "    replace_nans(np.array([[np.nan]])).tolist(),\n"
            "    replace_nans(np.array([[]])).tolist(),\n"
            ")",
        ),
        # Среднее по строке вместо среднего по всей матрице.
        (
            2013,
            "def replace_nans(matrix):\n"
            '    """Среднее считается по строке."""\n'
            "    mask = np.isnan(matrix)\n"
            "    if mask.all():\n"
            "        return np.zeros_like(matrix)\n"
            "    numbers = np.where(mask, np.nan, matrix)\n"
            "    return np.where(mask, np.nanmean(numbers, axis=1)[:, None], matrix)\n\n\n"
            f"matrix = {_MATRIX}"
            "result = (\n"
            "    replace_nans(matrix).tolist(),\n"
            "    replace_nans(np.array([[np.nan]])).tolist(),\n"
            "    replace_nans(np.array([[]])).tolist(),\n"
            ")",
        ),
        # Заполнение прямо во входной матрице: вызывающий получает другие данные.
        (
            2013,
            "def replace_nans(matrix):\n"
            '    """Заполнение во входной матрице."""\n'
            "    mask = np.isnan(matrix)\n"
            "    if mask.all():\n"
            "        return np.zeros_like(matrix)\n"
            "    matrix[mask] = matrix[~mask].mean()\n"
            "    return matrix\n\n\n"
            f"matrix = {_MATRIX}"
            "result = (\n"
            "    replace_nans(matrix).tolist(),\n"
            "    replace_nans(np.array([[np.nan]])).tolist(),\n"
            "    replace_nans(np.array([[]])).tolist(),\n"
            ")",
        ),
        # Столбец из пропусков поделён на ноль: nan остаётся в ответе.
        (
            2015,
            "def fill_by_column(matrix):\n"
            '    """Деление без защиты от пустого столбца."""\n'
            "    mask = np.isnan(matrix)\n"
            "    totals = np.where(mask, 0.0, matrix).sum(axis=0)\n"
            "    counts = (~mask).sum(axis=0)\n"
            "    return np.where(mask, totals / counts, matrix)\n\n\n"
            f"matrix = {_MATRIX}"
            "result = (\n"
            "    fill_by_column(matrix).tolist(),\n"
            "    fill_by_column(np.array([[1.0, np.nan], [3.0, np.nan]])).tolist(),\n"
            ")",
        ),
        # Средние по строкам вместо столбцов.
        (
            2015,
            "def fill_by_column(matrix):\n"
            '    """Средние считаются по строкам."""\n'
            "    mask = np.isnan(matrix)\n"
            "    totals = np.where(mask, 0.0, matrix).sum(axis=1)\n"
            "    counts = (~mask).sum(axis=1)\n"
            "    means = np.where(counts > 0, totals / np.maximum(counts, 1), 0.0)\n"
            "    return np.where(mask, means[:, None], matrix)\n\n\n"
            f"matrix = {_MATRIX}"
            "result = (\n"
            "    fill_by_column(matrix).tolist(),\n"
            "    fill_by_column(np.array([[1.0, np.nan], [3.0, np.nan]])).tolist(),\n"
            ")",
        ),
        # Заполнение прямо во входной матрице.
        (
            2015,
            "def fill_by_column(matrix):\n"
            '    """Средние по столбцам, запись во вход."""\n'
            "    mask = np.isnan(matrix)\n"
            "    totals = np.where(mask, 0.0, matrix).sum(axis=0)\n"
            "    counts = (~mask).sum(axis=0)\n"
            "    means = np.where(counts > 0, totals / np.maximum(counts, 1), 0.0)\n"
            "    matrix[mask] = np.broadcast_to(means, matrix.shape)[mask]\n"
            "    return matrix\n\n\n"
            f"matrix = {_MATRIX}"
            "result = (\n"
            "    fill_by_column(matrix).tolist(),\n"
            "    fill_by_column(np.array([[1.0, np.nan], [3.0, np.nan]])).tolist(),\n"
            ")",
        ),
    ]
)

CRASHES.extend(
    [
        # Маска собрана списком: у списка нет ни sum(axis=1), ни tolist.
        (
            2011,
            AttributeError,
            "def nan_mask(matrix):\n"
            '    """Маска собрана списком списков."""\n'
            "    return [[value != value for value in row] for row in matrix]\n\n\n"
            f"matrix = {_MATRIX}"
            "result = (nan_mask(matrix).tolist(), nan_mask(matrix).sum(axis=1).tolist())",
        ),
        # Ответ собран списком: у него нет формы.
        (
            2015,
            AttributeError,
            "def fill_by_column(matrix):\n"
            '    """Средние по столбцам, ответ приведён к списку."""\n'
            "    mask = np.isnan(matrix)\n"
            "    totals = np.where(mask, 0.0, matrix).sum(axis=0)\n"
            "    counts = (~mask).sum(axis=0)\n"
            "    means = np.where(counts > 0, totals / np.maximum(counts, 1), 0.0)\n"
            "    return np.where(mask, means, matrix).tolist()\n\n\n"
            f"matrix = {_MATRIX}"
            "result = (\n"
            "    fill_by_column(matrix).tolist(),\n"
            "    fill_by_column(np.array([[1.0, np.nan], [3.0, np.nan]])).tolist(),\n"
            ")",
        ),
    ]
)

# --- Занятие `titanic` (2021–2029) -------------------------------------------
# Таблица «Титаника» встроена в setup задания (io.StringIO + sep="\t"), поэтому
# решения ниже берут df из setup и своей таблицы не заводят.

SOLUTIONS.update(
    {
        # Отбор строк и пропуски: pandas сам пропускает nan в среднем.
        2021: "def male_age(df):\n"
        '    """Средний возраст выживших мужчин из Саутгемптона с билетом дороже 30."""\n'
        '    chosen = df[(df["Sex"] == "male") & (df["Survived"] == 1)\n'
        '                & (df["Embarked"] == "S") & (df["Fare"] > 30)]\n'
        '    return chosen["Age"].mean()\n\n\n'
        "def nan_columns(df):\n"
        '    """Колонки, в которых есть пропуски."""\n'
        "    return df.columns[df.isna().any()].tolist()\n\n\n"
        "result = (male_age(df), sorted(nan_columns(df)))",
        # Доли классов: value_counts сортирует по частоте, порядок задаёт sort_index.
        2023: "def class_distribution(df):\n"
        '    """Доля пассажиров каждого класса."""\n'
        '    return df["Pclass"].value_counts(normalize=True).sort_index()\n\n\n'
        "def mean_price(df, tickets):\n"
        '    """Средняя цена билетов из списка."""\n'
        '    return df[df["Ticket"].isin(tickets)]["Fare"].mean()\n\n\n'
        "result = (\n"
        "    [int(index) for index in class_distribution(df).index],\n"
        "    [round(float(value), 6) for value in class_distribution(df)],\n"
        '    round(float(mean_price(df, df["Ticket"].unique())), 6),\n'
        ")",
        # Семьи: фамилия — часть Name до запятой, семьи считаются по фамилиям.
        2025: "def families_count(df, k):\n"
        '    """Сколько семей больше чем из k человек."""\n'
        '    surname = df["Name"].str.split(",").str[0]\n'
        "    return int((surname.value_counts() > k).sum())\n\n\n"
        "result = tuple(families_count(df, k) for k in range(5))",
        # Частое сочетание: размеры групп, а не мода по колонкам.
        2026: "def max_size_group(df, columns):\n"
        '    """Самое частое сочетание значений колонок."""\n'
        "    best = df.groupby(columns).size().idxmax()\n"
        "    return best if isinstance(best, tuple) else (best,)\n\n\n"
        "result = (\n"
        '    max_size_group(df, ["Survived", "Sex"]),\n'
        '    max_size_group(df, ["Embarked", "Pclass"]),\n'
        '    max_size_group(df, ["Survived", "Sex", "Cabin"]),\n'
        '    max_size_group(df, ["Age"]),\n'
        ")",
        # Счастливые билеты: сначала isdigit, потом арифметика; доля — внутри счастливчиков.
        2027: "def is_lucky(ticket):\n"
        '    """Счастливый ли номер билета."""\n'
        "    if not ticket.isdigit() or len(ticket) % 2:\n"
        "        return False\n"
        "    half = len(ticket) // 2\n"
        "    return sum(map(int, ticket[:half])) == sum(map(int, ticket[half:]))\n\n\n"
        "def dead_lucky(df):\n"
        '    """Доля утонувших среди пассажиров со счастливыми билетами."""\n'
        '    lucky = df[df["Ticket"].apply(is_lucky)]\n'
        '    return float((lucky["Survived"] == 0).mean())\n\n\n'
        'result = (is_lucky("2671"), is_lucky("123456"), round(float(dead_lucky(df)), 6))',
        # Итоговое задание: доля выживших внутри класса.
        2029: "def survival_by_class(df):\n"
        '    """Доля выживших в каждом классе."""\n'
        '    return df.groupby("Pclass")["Survived"].mean()\n\n\n'
        "result = (\n"
        "    [(int(index), round(float(value), 6)) for index, value in survival_by_class(df).items()],\n"
        "    int(survival_by_class(df).idxmax()),\n"
        ")",
    }
)

ALTERNATIVES.extend(
    [
        # query вместо маски: `and` внутри строки запроса законен.
        (
            2021,
            "def male_age(df):\n"
            '    """Средний возраст через query."""\n'
            "    chosen = df.query(\"Survived == 1 and Sex == 'male' and Embarked == 'S' and Fare > 30\")\n"
            '    return chosen["Age"].mean()\n\n\n'
            "def nan_columns(df):\n"
            '    """Колонки с пропусками списком."""\n'
            "    return [column for column in df.columns if df[column].isna().any()]\n\n\n"
            "result = (male_age(df), sorted(nan_columns(df)))",
        ),
        # Множество вместо списка: sorted в result всё приводит к списку.
        (
            2021,
            "def male_age(df):\n"
            '    """Средний возраст через loc."""\n'
            "    return df.loc[\n"
            '        (df["Survived"] == 1) & (df["Sex"] == "male")\n'
            '        & (df["Embarked"] == "S") & (df["Fare"] > 30),\n'
            '        "Age",\n'
            "    ].mean()\n\n\n"
            "def nan_columns(df):\n"
            '    """Колонки с пропусками множеством."""\n'
            "    return set(df.columns[df.isna().any()])\n\n\n"
            "result = (male_age(df), sorted(nan_columns(df)))",
        ),
        # Доли через размеры групп.
        (
            2023,
            "def class_distribution(df):\n"
            '    """Доли классов через groupby."""\n'
            "    return df.groupby(\"Pclass\").size() / len(df)\n\n\n"
            "def mean_price(df, tickets):\n"
            '    """Цена через apply по билетам."""\n'
            "    wanted = set(tickets)\n"
            '    return df[df["Ticket"].apply(lambda value: value in wanted)]["Fare"].mean()\n\n\n'
            "result = (\n"
            "    [int(index) for index in class_distribution(df).index],\n"
            "    [round(float(value), 6) for value in class_distribution(df)],\n"
            '    round(float(mean_price(df, df["Ticket"].unique())), 6),\n'
            ")",
        ),
        # value_counts без нормализации, деление вручную.
        (
            2023,
            "def class_distribution(df):\n"
            '    """Доли через counts и деление."""\n'
            '    return df["Pclass"].value_counts(sort=False).sort_index() / len(df)\n\n\n'
            "def mean_price(df, tickets):\n"
            '    """Цена через isin."""\n'
            '    return df[df["Ticket"].isin(list(tickets))]["Fare"].mean()\n\n\n'
            "result = (\n"
            "    [int(index) for index in class_distribution(df).index],\n"
            "    [round(float(value), 6) for value in class_distribution(df)],\n"
            '    round(float(mean_price(df, df["Ticket"].unique())), 6),\n'
            ")",
        ),
        # Фамилия через apply и подсчёт циклом.
        (
            2025,
            "def families_count(df, k):\n"
            '    """Семьи через apply."""\n'
            '    surname = df["Name"].apply(lambda name: name.split(",")[0])\n'
            "    return int(sum(1 for _, size in surname.value_counts().items() if size > k))\n\n\n"
            "result = tuple(families_count(df, k) for k in range(5))",
        ),
        # То же через groupby по фамилиям.
        (
            2025,
            "def families_count(df, k):\n"
            '    """Семьи через groupby."""\n'
            '    surname = df["Name"].str.split(",").str[0]\n'
            "    return int((surname.groupby(surname).size() > k).sum())\n\n\n"
            "result = tuple(families_count(df, k) for k in range(5))",
        ),
        # Сортировка размеров групп вместо idxmax.
        (
            2026,
            "def max_size_group(df, columns):\n"
            '    """Самое частое сочетание по отсортированным размерам."""\n'
            "    best = df.groupby(columns).size().sort_values(ascending=False).index[0]\n"
            "    return best if isinstance(best, tuple) else (best,)\n\n\n"
            "result = (\n"
            '    max_size_group(df, ["Survived", "Sex"]),\n'
            '    max_size_group(df, ["Embarked", "Pclass"]),\n'
            '    max_size_group(df, ["Survived", "Sex", "Cabin"]),\n'
            '    max_size_group(df, ["Age"]),\n'
            ")",
        ),
        # value_counts по таблице колонок.
        (
            2026,
            "def max_size_group(df, columns):\n"
            '    """Самое частое сочетание через value_counts."""\n'
            "    best = df[columns].value_counts().index[0]\n"
            "    return best if isinstance(best, tuple) else (best,)\n\n\n"
            "result = (\n"
            '    max_size_group(df, ["Survived", "Sex"]),\n'
            '    max_size_group(df, ["Embarked", "Pclass"]),\n'
            '    max_size_group(df, ["Survived", "Sex", "Cabin"]),\n'
            '    max_size_group(df, ["Age"]),\n'
            ")",
        ),
        # Суммы половин через список цифр и подсчёт без apply.
        (
            2027,
            "def is_lucky(ticket):\n"
            '    """Счастливый билет через список цифр."""\n'
            "    if not ticket.isdigit() or len(ticket) % 2:\n"
            "        return False\n"
            "    digits = [int(symbol) for symbol in ticket]\n"
            "    half = len(digits) // 2\n"
            "    return sum(digits[:half]) == sum(digits[half:])\n\n\n"
            "def dead_lucky(df):\n"
            '    """Доля утонувших среди счастливчиков без apply."""\n'
            '    tickets = list(df["Ticket"])\n'
            '    survived = list(df["Survived"])\n'
            "    died = sum(1 for ticket, value in zip(tickets, survived) if is_lucky(ticket) and value == 0)\n"
            "    total = sum(1 for ticket in tickets if is_lucky(ticket))\n"
            "    return float(died / total)\n\n\n"
            'result = (is_lucky("2671"), is_lucky("123456"), round(float(dead_lucky(df)), 6))',
        ),
        # Доля выживших через маску и суммы.
        (
            2029,
            "def survival_by_class(df):\n"
            '    """Доля выживших через маску."""\n'
            '    survived = df["Survived"] == 1\n'
            '    return survived.groupby(df["Pclass"]).mean()\n\n\n'
            "result = (\n"
            "    [(int(index), round(float(value), 6)) for index, value in survival_by_class(df).items()],\n"
            "    int(survival_by_class(df).idxmax()),\n"
            ")",
        ),
    ]
)

# Главная ловушка занятия — по одной на задание.
WRONG.update(
    {
        # Среднее через numpy: пропуск в Age отравляет ответ.
        2021: "def male_age(df):\n"
        '    """Средний возраст через numpy."""\n'
        '    chosen = df[(df["Sex"] == "male") & (df["Survived"] == 1)\n'
        '                & (df["Embarked"] == "S") & (df["Fare"] > 30)]\n'
        '    return chosen["Age"].to_numpy().mean()\n\n\n'
        "def nan_columns(df):\n"
        '    """Колонки с пропусками."""\n'
        "    return df.columns[df.isna().any()].tolist()\n\n\n"
        "result = (male_age(df), sorted(nan_columns(df)))",
        # value_counts без sort_index: классы идут 3, 1, 2.
        2023: "def class_distribution(df):\n"
        '    """Доли классов как отдал value_counts."""\n'
        '    return df["Pclass"].value_counts(normalize=True)\n\n\n'
        "def mean_price(df, tickets):\n"
        '    """Средняя цена билетов."""\n'
        '    return df[df["Ticket"].isin(tickets)]["Fare"].mean()\n\n\n'
        "result = (\n"
        "    [int(index) for index in class_distribution(df).index],\n"
        "    [round(float(value), 6) for value in class_distribution(df)],\n"
        '    round(float(mean_price(df, df["Ticket"].unique())), 6),\n'
        ")",
        # Семьи по полным именам: у каждого пассажира своё.
        2025: "def families_count(df, k):\n"
        '    """Семьи по полному имени."""\n'
        '    return int((df["Name"].value_counts() > k).sum())\n\n\n'
        "result = tuple(families_count(df, k) for k in range(5))",
        # Мода по колонкам вместо размеров групп.
        2026: "def max_size_group(df, columns):\n"
        '    """Самое частое сочетание через моду."""\n'
        "    return tuple(df[columns].mode().iloc[0])\n\n\n"
        "result = (\n"
        '    max_size_group(df, ["Survived", "Sex"]),\n'
        '    max_size_group(df, ["Embarked", "Pclass"]),\n'
        '    max_size_group(df, ["Survived", "Sex", "Cabin"]),\n'
        '    max_size_group(df, ["Age"]),\n'
        ")",
        # Из номера выбрасываются буквы — добавляется лишний пассажир.
        2027: "def is_lucky(ticket):\n"
        '    """Хитрый вариант: цифры выуживаются из любой строки."""\n'
        '    digits = "".join(symbol for symbol in str(ticket) if symbol.isdigit())\n'
        "    if not digits or len(digits) % 2:\n"
        "        return False\n"
        "    half = len(digits) // 2\n"
        "    return sum(map(int, digits[:half])) == sum(map(int, digits[half:]))\n\n\n"
        "def dead_lucky(df):\n"
        '    """Доля утонувших среди счастливчиков."""\n'
        '    lucky = df[df["Ticket"].apply(is_lucky)]\n'
        '    return float((lucky["Survived"] == 0).mean())\n\n\n'
        'result = (is_lucky("2671"), is_lucky("123456"), round(float(dead_lucky(df)), 6))',
        # Выжившие считаются от всех пассажиров, а не внутри класса.
        2029: "def survival_by_class(df):\n"
        '    """Выжившие класса, поделённые на всех пассажиров."""\n'
        '    return df.groupby("Pclass")["Survived"].sum() / len(df)\n\n\n'
        "result = (\n"
        "    [(int(index), round(float(value), 6)) for index, value in survival_by_class(df).items()],\n"
        "    int(survival_by_class(df).idxmax()),\n"
        ")",
    }
)

MORE_WRONG.extend(
    [
        # Условие по цене потеряно: 24.975714 вместо 30.0.
        (
            2021,
            "def male_age(df):\n"
            '    """Средний возраст без условия по цене."""\n'
            '    chosen = df[(df["Sex"] == "male") & (df["Survived"] == 1) & (df["Embarked"] == "S")]\n'
            '    return chosen["Age"].mean()\n\n\n'
            "def nan_columns(df):\n"
            '    """Колонки с пропусками."""\n'
            "    return df.columns[df.isna().any()].tolist()\n\n\n"
            "result = (male_age(df), sorted(nan_columns(df)))",
        ),
        # Среднее по всей таблице: 28.141508 вместо 30.0.
        (
            2021,
            "def male_age(df):\n"
            '    """Средний возраст всех пассажиров."""\n'
            '    return df["Age"].mean()\n\n\n'
            "def nan_columns(df):\n"
            '    """Колонки с пропусками."""\n'
            "    return df.columns[df.isna().any()].tolist()\n\n\n"
            "result = (male_age(df), sorted(nan_columns(df)))",
        ),
        # Готовое число вместо счёта: ловится проверкой на маленькой таблице.
        (
            2021,
            "def male_age(df):\n"
            '    """Ответ из лаборатории, посчитанный один раз."""\n'
            "    return 30.0\n\n\n"
            "def nan_columns(df):\n"
            '    """Колонки с пропусками."""\n'
            "    return df.columns[df.isna().any()].tolist()\n\n\n"
            "result = (male_age(df), sorted(nan_columns(df)))",
        ),
        # Средняя цена билетов вместо средней цены строк: 25.670832.
        (
            2023,
            "def class_distribution(df):\n"
            '    """Доли классов."""\n'
            '    return df["Pclass"].value_counts(normalize=True).sort_index()\n\n\n'
            "def mean_price(df, tickets):\n"
            '    """Среднее по средним билетов."""\n'
            '    chosen = df[df["Ticket"].isin(tickets)]\n'
            '    return chosen.groupby("Ticket")["Fare"].mean().mean()\n\n\n'
            "result = (\n"
            "    [int(index) for index in class_distribution(df).index],\n"
            "    [round(float(value), 6) for value in class_distribution(df)],\n"
            '    round(float(mean_price(df, df["Ticket"].unique())), 6),\n'
            ")",
        ),
        # Числа пассажиров вместо долей.
        (
            2023,
            "def class_distribution(df):\n"
            '    """Числа пассажиров вместо долей."""\n'
            '    return df["Pclass"].value_counts().sort_index()\n\n\n'
            "def mean_price(df, tickets):\n"
            '    """Средняя цена билетов."""\n'
            '    return df[df["Ticket"].isin(tickets)]["Fare"].mean()\n\n\n'
            "result = (\n"
            "    [int(index) for index in class_distribution(df).index],\n"
            "    [round(float(value), 6) for value in class_distribution(df)],\n"
            '    round(float(mean_price(df, df["Ticket"].unique())), 6),\n'
            ")",
        ),
        # Доли заучены числом: проверки зовут class_distribution и на маленьком фрейме.
        (
            2023,
            "def class_distribution(df):\n"
            '    """Заученные доли классов."""\n'
            "    return pd.Series({1: 30 / 156, 2: 30 / 156, 3: 96 / 156})\n\n\n"
            "def mean_price(df, tickets):\n"
            '    """Честная средняя цена."""\n'
            '    return float(df.loc[df["Ticket"].isin(tickets), "Fare"].mean())\n\n\n'
            "result = (\n"
            "    [1, 2, 3],\n"
            "    [0.192308, 0.192308, 0.615385],\n"
            '    round(float(mean_price(df, df["Ticket"].unique())), 6),\n'
            ")",
        ),
        # Строгое неравенство: при k = 1 все 141 семья проходят.
        (
            2025,
            "def families_count(df, k):\n"
            '    """Не меньше k человек вместо больше k."""\n'
            '    surname = df["Name"].str.split(",").str[0]\n'
            "    return int((surname.value_counts() >= k).sum())\n\n\n"
            "result = tuple(families_count(df, k) for k in range(5))",
        ),
        # Фильтр посчитан, но потерян: len по всей серии.
        (
            2025,
            "def families_count(df, k):\n"
            '    """Число всех фамилий вместо числа подходящих."""\n'
            '    surname = df["Name"].str.split(",").str[0]\n'
            "    return int(len(surname.value_counts() > k))\n\n\n"
            "result = tuple(families_count(df, k) for k in range(5))",
        ),
        # idxmax без обёртки: для одной колонки получается число, а не набор.
        (
            2026,
            "def max_size_group(df, columns):\n"
            '    """Самое частое сочетание без обёртки."""\n'
            "    return df.groupby(columns).size().idxmax()\n\n\n"
            "result = (\n"
            '    max_size_group(df, ["Survived", "Sex"]),\n'
            '    max_size_group(df, ["Embarked", "Pclass"]),\n'
            '    max_size_group(df, ["Survived", "Sex", "Cabin"]),\n'
            '    max_size_group(df, ["Age"]),\n'
            ")",
        ),
        # Частое значение первой колонки на всю длину набора.
        (
            2026,
            "def max_size_group(df, columns):\n"
            '    """Частое значение первой колонки, повторённое по длине."""\n'
            '    best = df[columns[0]].value_counts().idxmax()\n'
            "    return tuple(best for _ in columns)\n\n\n"
            "result = (\n"
            '    max_size_group(df, ["Survived", "Sex"]),\n'
            '    max_size_group(df, ["Embarked", "Pclass"]),\n'
            '    max_size_group(df, ["Survived", "Sex", "Cabin"]),\n'
            '    max_size_group(df, ["Age"]),\n'
            ")",
        ),
        # Доля счастливых билетов среди всех пассажиров: 0.025641.
        (
            2027,
            "def is_lucky(ticket):\n"
            '    """Счастливый ли номер билета."""\n'
            "    if not ticket.isdigit() or len(ticket) % 2:\n"
            "        return False\n"
            "    half = len(ticket) // 2\n"
            "    return sum(map(int, ticket[:half])) == sum(map(int, ticket[half:]))\n\n\n"
            "def dead_lucky(df):\n"
            '    """Доля счастливых пассажиров среди всех."""\n'
            '    lucky = df[df["Ticket"].apply(is_lucky)]\n'
            "    return float(len(lucky) / len(df))\n\n\n"
            'result = (is_lucky("2671"), is_lucky("123456"), round(float(dead_lucky(df)), 6))',
        ),
        # Погибшие счастливчики поделены на всех пассажиров.
        (
            2027,
            "def is_lucky(ticket):\n"
            '    """Счастливый ли номер билета."""\n'
            "    if not ticket.isdigit() or len(ticket) % 2:\n"
            "        return False\n"
            "    half = len(ticket) // 2\n"
            "    return sum(map(int, ticket[:half])) == sum(map(int, ticket[half:]))\n\n\n"
            "def dead_lucky(df):\n"
            '    """Погибшие счастливчики на всех пассажиров."""\n'
            '    lucky = df[df["Ticket"].apply(is_lucky)]\n'
            '    return float((lucky["Survived"] == 0).sum() / len(df))\n\n\n'
            'result = (is_lucky("2671"), is_lucky("123456"), round(float(dead_lucky(df)), 6))',
        ),
        # Выжившие класса поделены на всех пассажиров: 0.076923 и далее.
        (
            2029,
            "def survival_by_class(df):\n"
            '    """Выжившие класса на всех пассажиров."""\n'
            '    return df.groupby("Pclass")["Survived"].sum() / len(df)\n\n\n'
            "result = (\n"
            "    [(int(index), round(float(value), 6)) for index, value in survival_by_class(df).items()],\n"
            "    int(survival_by_class(df).idxmax()),\n"
            ")",
        ),
    ]
)

CRASHES.extend(
    [
        # `and` между условиями: True-значение Series неоднозначно.
        (
            2021,
            ValueError,
            "def male_age(df):\n"
            '    """Условия через and."""\n'
            '    chosen = df[(df["Sex"] == "male") and (df["Survived"] == 1)]\n'
            '    return chosen["Age"].mean()\n\n\n'
            "def nan_columns(df):\n"
            '    """Колонки с пропусками."""\n'
            "    return df.columns[df.isna().any()].tolist()\n\n\n"
            "result = (male_age(df), sorted(nan_columns(df)))",
        ),
        # Словарь в ответе: строка из условия задачи обращается к .index.
        (
            2023,
            AttributeError,
            "def class_distribution(df):\n"
            '    """Доли классов словарём."""\n'
            '    return df["Pclass"].value_counts(normalize=True).sort_index().to_dict()\n\n\n'
            "def mean_price(df, tickets):\n"
            '    """Средняя цена билетов."""\n'
            '    return df[df["Ticket"].isin(tickets)]["Fare"].mean()\n\n\n'
            "result = (\n"
            "    [int(index) for index in class_distribution(df).index],\n"
            "    [round(float(value), 6) for value in class_distribution(df)],\n"
            '    round(float(mean_price(df, df["Ticket"].unique())), 6),\n'
            ")",
        ),
        # Метод строки у столбца: забыт аксессор .str.
        (
            2025,
            AttributeError,
            "def families_count(df, k):\n"
            '    """Фамилия через метод строки у столбца."""\n'
            '    surname = df["Name"].split(",")[0]\n'
            "    return int((surname.value_counts() > k).sum())\n\n\n"
            "result = tuple(families_count(df, k) for k in range(5))",
        ),
        # tuple от скаляра: для одной колонки idxmax даёт число, а не набор.
        (
            2026,
            TypeError,
            "def max_size_group(df, columns):\n"
            '    """tuple прямо от idxmax."""\n'
            "    return tuple(df.groupby(columns).size().idxmax())\n\n\n"
            "result = (\n"
            '    max_size_group(df, ["Survived", "Sex"]),\n'
            '    max_size_group(df, ["Embarked", "Pclass"]),\n'
            '    max_size_group(df, ["Survived", "Sex", "Cabin"]),\n'
            '    max_size_group(df, ["Age"]),\n'
            ")",
        ),
        # int() от буквенного номера: проверка isdigit потеряна.
        (
            2027,
            ValueError,
            "def is_lucky(ticket):\n"
            '    """Без проверки на цифры."""\n'
            "    if len(ticket) % 2:\n"
            "        return False\n"
            "    half = len(ticket) // 2\n"
            "    return sum(map(int, ticket[:half])) == sum(map(int, ticket[half:]))\n\n\n"
            "def dead_lucky(df):\n"
            '    """Доля утонувших среди счастливчиков."""\n'
            '    lucky = df[df["Ticket"].apply(is_lucky)]\n'
            '    return float((lucky["Survived"] == 0).mean())\n\n\n'
            'result = (is_lucky("2671"), is_lucky("123456"), round(float(dead_lucky(df)), 6))',
        ),
        # Словарь в ответе: строка из условия задачи обращается к .idxmax.
        (
            2029,
            AttributeError,
            "def survival_by_class(df):\n"
            '    """Доли выживших словарём."""\n'
            '    return df.groupby("Pclass")["Survived"].mean().to_dict()\n\n\n'
            "result = (\n"
            "    [(int(index), round(float(value), 6)) for index, value in survival_by_class(df).items()],\n"
            "    int(survival_by_class(df).idxmax()),\n"
            ")",
        ),
        # Одно число вместо долей: строка из условия задачи ждёт Series.
        (
            2029,
            AttributeError,
            "def survival_by_class(df):\n"
            '    """Общая доля выживших."""\n'
            '    return df["Survived"].mean()\n\n\n'
            "result = (\n"
            "    [(int(index), round(float(value), 6)) for index, value in survival_by_class(df).items()],\n"
            "    int(survival_by_class(df).idxmax()),\n"
            ")",
        ),
    ]
)

# Занятие `imdb`: выборка фильмов IMDB лежит в `setup`, жанровые колонки — в
# списке `GENRES`. Все числа посчитаны на этой выборке (см. `imdb_traps.py`).

SOLUTIONS.update(
    {
        2031: "def words_mode(df):\n"
        '    """Мода числа слов в названии."""\n'
        '    return df["wordsInTitle"].str.split().str.len().mode().iloc[0]\n\n\n'
        "def genre_films(df, genres):\n"
        '    """Число фильмов по жанрам, по убыванию."""\n'
        "    return df[genres].sum().sort_values(ascending=False)\n\n\n"
        "result = (\n"
        "    words_mode(df),\n"
        "    sorted(genre_films(df, GENRES).head(10).index),\n"
        '    int(genre_films(df, GENRES)["Comedy"]),\n'
        ")",
        2033: "def shorts_by_decade(df):\n"
        '    """Доля фильмов короче часа по десятилетиям."""\n'
        '    late = df.assign(decade=df["year"] // 10 * 10, short=df["duration"] < 3600)\n'
        '    return late.groupby("decade")["short"].mean()\n\n\n'
        "result = (\n"
        "    [(int(decade), round(float(share), 6)) for decade, share in shorts_by_decade(df).items()],\n"
        "    int(shorts_by_decade(df).idxmax()),\n"
        ")",
        2035: "def wins_correlation(df):\n"
        '    """Колонка, сильнее всего связанная с числом побед."""\n'
        '    numeric = df.select_dtypes(include="number")\n'
        '    return numeric.corrwith(df["nrOfWins"]).drop("nrOfWins").abs().idxmax()\n\n\n'
        "result = (\n"
        "    wins_correlation(df),\n"
        '    round(abs(float(df[wins_correlation(df)].corr(df["nrOfWins"]))), 6),\n'
        ")",
        2036: "def rating_by_decade(df):\n"
        '    """Тренд среднего рейтинга и лучшее десятилетие."""\n'
        '    means = df.assign(decade=df["year"] // 10 * 10).groupby("decade")["imdbRating"].mean()\n'
        '    trend = "ascending" if means.iloc[-1] > means.iloc[0] else "descending"\n'
        "    return (trend, means.idxmax())\n\n\n"
        "result = rating_by_decade(df)",
        2037: "def genre_ratings(df, genres):\n"
        '    """Жанр с лучшей медианой и жанр с самым широким IQR."""\n'
        "    top = df[genres].sum().sort_values(ascending=False).head(10).index\n"
        '    medians = {genre: df.loc[df[genre] == 1, "imdbRating"].median() for genre in top}\n'
        "    spreads = {\n"
        '        genre: df.loc[df[genre] == 1, "imdbRating"].quantile(0.75)\n'
        '        - df.loc[df[genre] == 1, "imdbRating"].quantile(0.25)\n'
        "        for genre in top\n"
        "    }\n"
        "    best_median = min(medians, key=lambda genre: (-medians[genre], genre))\n"
        "    best_spread = min(spreads, key=lambda genre: (-spreads[genre], genre))\n"
        "    return (best_median, best_spread)\n\n\n"
        "result = genre_ratings(df, GENRES)",
        2039: "def reviews_by_genre(df, genres):\n"
        '    """Сумма рецензий по жанрам."""\n'
        '    return pd.Series({genre: int(df.loc[df[genre] == 1, "nrOfUserReviews"].sum())\n'
        "                      for genre in genres})\n\n\n"
        "def mean_reviews_by_genre(df, genres):\n"
        '    """Среднее число рецензий на фильм жанра."""\n'
        '    return pd.Series({genre: float(df.loc[df[genre] == 1, "nrOfUserReviews"].mean())\n'
        "                      for genre in genres})\n\n\n"
        "result = (\n"
        "    reviews_by_genre(df, GENRES).idxmax(),\n"
        "    int(reviews_by_genre(df, GENRES).max()),\n"
        "    mean_reviews_by_genre(df, GENRES).idxmax(),\n"
        "    round(float(mean_reviews_by_genre(df, GENRES).max()), 1),\n"
        ")",
    }
)

ALTERNATIVES.extend(
    [
        # Слова считаются по названию без года, числа фильмов — словарём.
        (
            2031,
            "def words_mode(df):\n"
            '    """Слова в названии без года в скобках."""\n'
            '    titles = df["wordsInTitle"].str.replace(r"\\s*\\(\\d+\\)$", "", regex=True)\n'
            "    return int(titles.str.split().str.len().mode().iloc[0])\n\n\n"
            "def genre_films(df, genres):\n"
            '    """Число фильмов словарём."""\n'
            "    counts = {genre: int(df[genre].sum()) for genre in genres}\n"
            "    return pd.Series(counts).sort_values(ascending=False)\n\n\n"
            "result = (\n"
            "    words_mode(df),\n"
            "    sorted(genre_films(df, GENRES).head(10).index),\n"
            '    int(genre_films(df, GENRES)["Comedy"]),\n'
            ")",
        ),
        # Мода через value_counts, десять жанров через nlargest.
        (
            2031,
            "def words_mode(df):\n"
            '    """Мода через частоты значений."""\n'
            '    return int(df["wordsInTitle"].str.split().str.len().value_counts().idxmax())\n\n\n'
            "def genre_films(df, genres):\n"
            '    """Числа фильмов через nlargest."""\n'
            "    return df[genres].sum().nlargest(len(genres))\n\n\n"
            "result = (\n"
            "    words_mode(df),\n"
            "    sorted(genre_films(df, GENRES).head(10).index),\n"
            '    int(genre_films(df, GENRES)["Comedy"]),\n'
            ")",
        ),
        # Доли считаются у признака «короткий фильм», а не у длительности.
        (
            2033,
            "def shorts_by_decade(df):\n"
            '    """Доли по признаку короткого фильма."""\n'
            '    short = df["duration"] < 3600\n'
            '    decade = df["year"] // 10 * 10\n'
            "    return short.groupby(decade).mean()\n\n\n"
            "result = (\n"
            "    [(int(decade), round(float(share), 6)) for decade, share in shorts_by_decade(df).items()],\n"
            "    int(shorts_by_decade(df).idxmax()),\n"
            ")",
        ),
        # Связь ищется по матрице корреляций, а не через corrwith.
        (
            2035,
            "def wins_correlation(df):\n"
            '    """Через матрицу корреляций."""\n'
            '    matrix = df.select_dtypes(include="number").corr()\n'
            '    return matrix["nrOfWins"].drop("nrOfWins").abs().idxmax()\n\n\n'
            "result = (\n"
            "    wins_correlation(df),\n"
            '    round(abs(float(df[wins_correlation(df)].corr(df["nrOfWins"]))), 6),\n'
            ")",
        ),
        # Корреляции считаются по одной колонке за раз — без corrwith и матрицы.
        (
            2035,
            "def wins_correlation(df):\n"
            '    """Связь считается попарно, колонка за колонкой."""\n'
            '    numeric = df.select_dtypes(include="number").drop(columns=["nrOfWins"])\n'
            "    links = {column: float(df[column].corr(df[\"nrOfWins\"])) for column in numeric}\n"
            "    return max(links, key=lambda column: abs(links[column]))\n\n\n"
            "result = (\n"
            "    wins_correlation(df),\n"
            '    round(abs(float(df[wins_correlation(df)].corr(df["nrOfWins"]))), 6),\n'
            ")",
        ),
        # Тренд по знаку корреляции рейтинга с годом.
        (
            2036,
            "def rating_by_decade(df):\n"
            '    """Тренд по знаку связи рейтинга с годом."""\n'
            '    means = df.assign(decade=df["year"] // 10 * 10).groupby("decade")["imdbRating"].mean()\n'
            '    slope = df["imdbRating"].corr(df["year"])\n'
            '    return ("ascending" if slope > 0 else "descending", means.idxmax())\n\n\n'
            "result = rating_by_decade(df)",
        ),
        # Лучшее десятилетие — первый элемент сортировки по убыванию.
        (
            2036,
            "def rating_by_decade(df):\n"
            '    """Лучшее десятилетие сортировкой."""\n'
            '    means = df.assign(decade=df["year"] // 10 * 10).groupby("decade")["imdbRating"].mean()\n'
            "    ordered = means.sort_values(ascending=False)\n"
            '    trend = "ascending" if means.iloc[-1] > means.iloc[0] else "descending"\n'
            "    return (trend, ordered.index[0])\n\n\n"
            "result = rating_by_decade(df)",
        ),
        # Жанры выбираются сортировкой пар, а не min по словарю.
        (
            2037,
            "def genre_ratings(df, genres):\n"
            '    """Выбор сортировкой пар «жанр — значение»."""\n'
            "    top = list(df[genres].sum().sort_values(ascending=False).head(10).index)\n"
            "    medians = sorted(\n"
            '        ((genre, float(df.loc[df[genre] == 1, "imdbRating"].median())) for genre in top),\n'
            "        key=lambda pair: (-pair[1], pair[0]),\n"
            "    )\n"
            "    spreads = sorted(\n"
            '        ((genre, float(df.loc[df[genre] == 1, "imdbRating"].quantile(0.75)\n'
            '                       - df.loc[df[genre] == 1, "imdbRating"].quantile(0.25))) for genre in top),\n'
            "        key=lambda pair: (-pair[1], pair[0]),\n"
            "    )\n"
            "    return (medians[0][0], spreads[0][0])\n\n\n"
            "result = genre_ratings(df, GENRES)",
        ),
        # Перебор жанров по алфавиту: при равных значениях побеждает первый.
        (
            2037,
            "def genre_ratings(df, genres):\n"
            '    """Перебор жанров по алфавиту."""\n'
            "    top = list(df[genres].sum().sort_values(ascending=False).head(10).index)\n"
            "    best_median = best_spread = None\n"
            "    best_median_value = best_spread_value = -1.0\n"
            "    for genre in sorted(top):\n"
            '        ratings = df.loc[df[genre] == 1, "imdbRating"]\n'
            "        median = float(ratings.median())\n"
            "        spread = float(ratings.quantile(0.75) - ratings.quantile(0.25))\n"
            "        if median > best_median_value:\n"
            "            best_median, best_median_value = genre, median\n"
            "        if spread > best_spread_value:\n"
            "            best_spread, best_spread_value = genre, spread\n"
            "    return (best_median, best_spread)\n\n\n"
            "result = genre_ratings(df, GENRES)",
        ),
        # Среднее считается как сумма, поделённая на число фильмов жанра.
        (
            2039,
            "def reviews_by_genre(df, genres):\n"
            '    """Сумма рецензий по жанрам."""\n'
            '    return pd.Series({genre: df.loc[df[genre] == 1, "nrOfUserReviews"].sum()\n'
            "                      for genre in genres})\n\n\n"
            "def mean_reviews_by_genre(df, genres):\n"
            '    """Сумма, поделённая на число фильмов жанра."""\n'
            '    sums = {genre: df.loc[df[genre] == 1, "nrOfUserReviews"].sum() for genre in genres}\n'
            "    sizes = {genre: int((df[genre] == 1).sum()) for genre in genres}\n"
            "    return pd.Series({genre: sums[genre] / sizes[genre] for genre in genres})\n\n\n"
            "result = (\n"
            "    reviews_by_genre(df, GENRES).idxmax(),\n"
            "    int(reviews_by_genre(df, GENRES).max()),\n"
            "    mean_reviews_by_genre(df, GENRES).idxmax(),\n"
            "    round(float(mean_reviews_by_genre(df, GENRES).max()), 1),\n"
            ")",
        ),
        # Числа по жанрам считаются умножением матрицы флагов на столбец рецензий.
        (
            2039,
            "def reviews_by_genre(df, genres):\n"
            '    """Сумма через умножение матрицы флагов."""\n'
            "    flags = df[genres].to_numpy()\n"
            '    reviews = df["nrOfUserReviews"].to_numpy()\n'
            "    return pd.Series(flags.T @ reviews, index=genres)\n\n\n"
            "def mean_reviews_by_genre(df, genres):\n"
            '    """Среднее через ту же матрицу."""\n'
            "    flags = df[genres].to_numpy()\n"
            '    reviews = df["nrOfUserReviews"].to_numpy()\n'
            "    return pd.Series(flags.T @ reviews / flags.sum(axis=0), index=genres)\n\n\n"
            "result = (\n"
            "    reviews_by_genre(df, GENRES).idxmax(),\n"
            "    int(reviews_by_genre(df, GENRES).max()),\n"
            "    mean_reviews_by_genre(df, GENRES).idxmax(),\n"
            "    round(float(mean_reviews_by_genre(df, GENRES).max()), 1),\n"
            ")",
        ),
    ]
)

WRONG.update(
    {
        # Жанры отсортированы по возрастанию: в ответе редкие.
        2031: "def words_mode(df):\n"
        '    """Мода числа слов в названии."""\n'
        '    return df["wordsInTitle"].str.split().str.len().mode().iloc[0]\n\n\n'
        "def genre_films(df, genres):\n"
        '    """Число фильмов по возрастанию."""\n'
        "    return df[genres].sum().sort_values()\n\n\n"
        "result = (\n"
        "    words_mode(df),\n"
        "    sorted(genre_films(df, GENRES).head(10).index),\n"
        '    int(genre_films(df, GENRES)["Comedy"]),\n'
        ")",
        # Длительность сравнивается с часом в минутах: коротких нет нигде.
        2033: "def shorts_by_decade(df):\n"
        '    """Порог короткого фильма в минутах."""\n'
        '    late = df.assign(decade=df["year"] // 10 * 10, short=df["duration"] < 60)\n'
        '    return late.groupby("decade")["short"].mean()\n\n\n'
        "result = (\n"
        "    [(int(decade), round(float(share), 6)) for decade, share in shorts_by_decade(df).items()],\n"
        "    int(shorts_by_decade(df).idxmax()),\n"
        ")",
        # Победы остались в сравнении: корреляция с собой равна единице.
        2035: "def wins_correlation(df):\n"
        '    """Победы сравниваются сами с собой."""\n'
        '    return df.select_dtypes(include="number").corrwith(df["nrOfWins"]).abs().idxmax()\n\n\n'
        "result = (\n"
        "    wins_correlation(df),\n"
        '    round(abs(float(df[wins_correlation(df)].corr(df["nrOfWins"]))), 6),\n'
        ")",
        # Лучшее десятилетие — самое многолюдное, а не самое высокооценённое.
        2036: "def rating_by_decade(df):\n"
        '    """Лучшее десятилетие — по числу фильмов."""\n'
        '    late = df.assign(decade=df["year"] // 10 * 10)\n'
        '    means = late.groupby("decade")["imdbRating"].mean()\n'
        '    sizes = late.groupby("decade").size()\n'
        '    trend = "ascending" if means.iloc[-1] > means.iloc[0] else "descending"\n'
        "    return (trend, sizes.idxmax())\n\n\n"
        "result = rating_by_decade(df)",
        # Среднее вместо медианы: впереди Drama, а не Crime.
        2037: "def genre_ratings(df, genres):\n"
        '    """Среднее вместо медианы."""\n'
        "    top = df[genres].sum().sort_values(ascending=False).head(10).index\n"
        '    averages = {genre: df.loc[df[genre] == 1, "imdbRating"].mean() for genre in top}\n'
        "    spreads = {\n"
        '        genre: df.loc[df[genre] == 1, "imdbRating"].quantile(0.75)\n'
        '        - df.loc[df[genre] == 1, "imdbRating"].quantile(0.25)\n'
        "        for genre in top\n"
        "    }\n"
        "    best_mean = min(averages, key=lambda genre: (-averages[genre], genre))\n"
        "    best_spread = min(spreads, key=lambda genre: (-spreads[genre], genre))\n"
        "    return (best_mean, best_spread)\n\n\n"
        "result = genre_ratings(df, GENRES)",
        # Метрики перепутаны: суммой отвечают на вопрос о среднем.
        2039: "def reviews_by_genre(df, genres):\n"
        '    """Среднее там, где нужна сумма."""\n'
        '    return pd.Series({genre: float(df.loc[df[genre] == 1, "nrOfUserReviews"].mean())\n'
        "                      for genre in genres})\n\n\n"
        "def mean_reviews_by_genre(df, genres):\n"
        '    """Сумма там, где нужно среднее."""\n'
        '    return pd.Series({genre: int(df.loc[df[genre] == 1, "nrOfUserReviews"].sum())\n'
        "                      for genre in genres})\n\n\n"
        "result = (\n"
        "    reviews_by_genre(df, GENRES).idxmax(),\n"
        "    int(reviews_by_genre(df, GENRES).max()),\n"
        "    mean_reviews_by_genre(df, GENRES).idxmax(),\n"
        "    round(float(mean_reviews_by_genre(df, GENRES).max()), 1),\n"
        ")",
    }
)

MORE_WRONG.extend(
    [
        # Слова считаются по названию с годом в скобках: мода сдвигается на 3.
        (
            2031,
            "def words_mode(df):\n"
            '    """Слова вместе с годом в скобках."""\n'
            '    return df["title"].str.split().str.len().mode().iloc[0]\n\n\n'
            "def genre_films(df, genres):\n"
            '    """Число фильмов по убыванию."""\n'
            "    return df[genres].sum().sort_values(ascending=False)\n\n\n"
            "result = (\n"
            "    words_mode(df),\n"
            "    sorted(genre_films(df, GENRES).head(10).index),\n"
            '    int(genre_films(df, GENRES)["Comedy"]),\n'
            ")",
        ),
        # count() считает строки, а не единицы: у каждого жанра их 159.
        (
            2031,
            "def words_mode(df):\n"
            '    """Мода числа слов в названии."""\n'
            '    return df["wordsInTitle"].str.split().str.len().mode().iloc[0]\n\n\n'
            "def genre_films(df, genres):\n"
            '    """count вместо sum: посчитаны строки."""\n'
            "    return df[genres].count().sort_values(ascending=False)\n\n\n"
            "result = (\n"
            "    words_mode(df),\n"
            "    sorted(genre_films(df, GENRES).head(10).index),\n"
            '    int(genre_films(df, GENRES)["Comedy"]),\n'
            ")",
        ),
        # Срез сделан до сортировки: в ответе случайные десять жанров.
        (
            2031,
            "def words_mode(df):\n"
            '    """Мода числа слов в названии."""\n'
            '    return df["wordsInTitle"].str.split().str.len().mode().iloc[0]\n\n\n'
            "def genre_films(df, genres):\n"
            '    """Первые десять жанровых колонок, потом сортировка."""\n'
            "    return df[genres].sum().head(10).sort_values(ascending=False)\n\n\n"
            "result = (\n"
            "    words_mode(df),\n"
            "    sorted(genre_films(df, GENRES).head(10).index),\n"
            '    int(genre_films(df, GENRES)["Comedy"]),\n'
            ")",
        ),
        # Год поделён на десять без умножения: индекс — десятки лет.
        (
            2033,
            "def shorts_by_decade(df):\n"
            '    """Десятилетие как год // 10."""\n'
            '    late = df.assign(decade=df["year"] // 10, short=df["duration"] < 3600)\n'
            '    return late.groupby("decade")["short"].mean()\n\n\n'
            "result = (\n"
            "    [(int(decade), round(float(share), 6)) for decade, share in shorts_by_decade(df).items()],\n"
            "    int(shorts_by_decade(df).idxmax()),\n"
            ")",
        ),
        # Порог строгий: фильмы ровно на 3600 секунд попадают в короткие.
        (
            2033,
            "def shorts_by_decade(df):\n"
            '    """Короче или равно часу."""\n'
            '    late = df.assign(decade=df["year"] // 10 * 10, short=df["duration"] <= 3600)\n'
            '    return late.groupby("decade")["short"].mean()\n\n\n'
            "result = (\n"
            "    [(int(decade), round(float(share), 6)) for decade, share in shorts_by_decade(df).items()],\n"
            "    int(shorts_by_decade(df).idxmax()),\n"
            ")",
        ),
        # Считается доля длинных фильмов вместо коротких.
        (
            2033,
            "def shorts_by_decade(df):\n"
            '    """Доля фильмов от часа и длиннее."""\n'
            '    late = df.assign(decade=df["year"] // 10 * 10, long=df["duration"] >= 3600)\n'
            '    return late.groupby("decade")["long"].mean()\n\n\n'
            "result = (\n"
            "    [(int(decade), round(float(share), 6)) for decade, share in shorts_by_decade(df).items()],\n"
            "    int(shorts_by_decade(df).idxmax()),\n"
            ")",
        ),
        # Связь подменена суммой значений: побеждает самая большая колонка.
        (
            2035,
            "def wins_correlation(df):\n"
            '    """Самая большая сумма вместо связи."""\n'
            '    return df.select_dtypes(include="number").sum().idxmax()\n\n\n'
            "result = (\n"
            "    wins_correlation(df),\n"
            '    round(abs(float(df[wins_correlation(df)].corr(df["nrOfWins"]))), 6),\n'
            ")",
        ),
        # Тренд считается по двум последним десятилетиям: он восходящий.
        (
            2036,
            "def rating_by_decade(df):\n"
            '    """Тренд по двум последним десятилетиям."""\n'
            '    means = df.assign(decade=df["year"] // 10 * 10).groupby("decade")["imdbRating"].mean()\n'
            '    trend = "ascending" if means.iloc[-1] > means.iloc[-2] else "descending"\n'
            "    return (trend, means.idxmax())\n\n\n"
            "result = rating_by_decade(df)",
        ),
        # Лучшее десятилетие — по сумме рейтингов, то есть самое многолюдное.
        (
            2036,
            "def rating_by_decade(df):\n"
            '    """Лучшее десятилетие по сумме рейтингов."""\n'
            '    late = df.assign(decade=df["year"] // 10 * 10)\n'
            '    means = late.groupby("decade")["imdbRating"].mean()\n'
            '    totals = late.groupby("decade")["imdbRating"].sum()\n'
            '    trend = "ascending" if means.iloc[-1] > means.iloc[0] else "descending"\n'
            "    return (trend, totals.idxmax())\n\n\n"
            "result = rating_by_decade(df)",
        ),
        # Медианы считаются по всем жанрам: впереди Documentary с малым числом фильмов.
        (
            2037,
            "def genre_ratings(df, genres):\n"
            '    """Медианы по всем жанрам, без отбора популярных."""\n'
            '    medians = {genre: df.loc[df[genre] == 1, "imdbRating"].median() for genre in genres}\n'
            "    spreads = {\n"
            '        genre: df.loc[df[genre] == 1, "imdbRating"].quantile(0.75)\n'
            '        - df.loc[df[genre] == 1, "imdbRating"].quantile(0.25)\n'
            "        for genre in genres\n"
            "    }\n"
            "    best_median = min(medians, key=lambda genre: (-medians[genre], genre))\n"
            "    best_spread = min(spreads, key=lambda genre: (-spreads[genre], genre))\n"
            "    return (best_median, best_spread)\n\n\n"
            "result = genre_ratings(df, GENRES)",
        ),
        # Разброс измерен стандартным отклонением: впереди Fantasy.
        (
            2037,
            "def genre_ratings(df, genres):\n"
            '    """Разброс через стандартное отклонение."""\n'
            "    top = df[genres].sum().sort_values(ascending=False).head(10).index\n"
            '    medians = {genre: df.loc[df[genre] == 1, "imdbRating"].median() for genre in top}\n'
            '    spreads = {genre: df.loc[df[genre] == 1, "imdbRating"].std() for genre in top}\n'
            "    best_median = min(medians, key=lambda genre: (-medians[genre], genre))\n"
            "    best_spread = min(spreads, key=lambda genre: (-spreads[genre], genre))\n"
            "    return (best_median, best_spread)\n\n\n"
            "result = genre_ratings(df, GENRES)",
        ),
        # Медиана вместо среднего: побеждает жанр с малой парой фильмов.
        (
            2039,
            "def reviews_by_genre(df, genres):\n"
            '    """Сумма рецензий по жанрам."""\n'
            '    return pd.Series({genre: int(df.loc[df[genre] == 1, "nrOfUserReviews"].sum())\n'
            "                      for genre in genres})\n\n\n"
            "def mean_reviews_by_genre(df, genres):\n"
            '    """Медиана вместо среднего."""\n'
            '    return pd.Series({genre: float(df.loc[df[genre] == 1, "nrOfUserReviews"].median())\n'
            "                      for genre in genres})\n\n\n"
            "result = (\n"
            "    reviews_by_genre(df, GENRES).idxmax(),\n"
            "    int(reviews_by_genre(df, GENRES).max()),\n"
            "    mean_reviews_by_genre(df, GENRES).idxmax(),\n"
            "    round(float(mean_reviews_by_genre(df, GENRES).max()), 1),\n"
            ")",
        ),
        # В сумме оказалось число фильмов жанра, а не рецензии.
        (
            2039,
            "def reviews_by_genre(df, genres):\n"
            '    """Число фильмов вместо суммы рецензий."""\n'
            "    return pd.Series({genre: int((df[genre] == 1).sum()) for genre in genres})\n\n\n"
            "def mean_reviews_by_genre(df, genres):\n"
            '    """Среднее число рецензий на фильм жанра."""\n'
            '    return pd.Series({genre: float(df.loc[df[genre] == 1, "nrOfUserReviews"].mean())\n'
            "                      for genre in genres})\n\n\n"
            "result = (\n"
            "    reviews_by_genre(df, GENRES).idxmax(),\n"
            "    int(reviews_by_genre(df, GENRES).max()),\n"
            "    mean_reviews_by_genre(df, GENRES).idxmax(),\n"
            "    round(float(mean_reviews_by_genre(df, GENRES).max()), 1),\n"
            ")",
        ),
        # Среднее делится на все фильмы выборки, а не на фильмы жанра.
        (
            2039,
            "def reviews_by_genre(df, genres):\n"
            '    """Сумма рецензий по жанрам."""\n'
            '    return pd.Series({genre: int(df.loc[df[genre] == 1, "nrOfUserReviews"].sum())\n'
            "                      for genre in genres})\n\n\n"
            "def mean_reviews_by_genre(df, genres):\n"
            '    """Среднее на все фильмы выборки."""\n'
            '    sums = {genre: df.loc[df[genre] == 1, "nrOfUserReviews"].sum() for genre in genres}\n'
            "    return pd.Series({genre: sums[genre] / len(df) for genre in genres})\n\n\n"
            "result = (\n"
            "    reviews_by_genre(df, GENRES).idxmax(),\n"
            "    int(reviews_by_genre(df, GENRES).max()),\n"
            "    mean_reviews_by_genre(df, GENRES).idxmax(),\n"
            "    round(float(mean_reviews_by_genre(df, GENRES).max()), 1),\n"
            ")",
        ),
    ]
)

CRASHES.extend(
    [
        # Среднее по текстовой колонке — не число слов.
        (
            2031,
            TypeError,
            "def words_mode(df):\n"
            '    """Среднее по колонке с названиями."""\n'
            '    return df["wordsInTitle"].mean()\n\n\n'
            "def genre_films(df, genres):\n"
            '    """Число фильмов по убыванию."""\n'
            "    return df[genres].sum().sort_values(ascending=False)\n\n\n"
            "result = (\n"
            "    words_mode(df),\n"
            "    sorted(genre_films(df, GENRES).head(10).index),\n"
            '    int(genre_films(df, GENRES)["Comedy"]),\n'
            ")",
        ),
        # Признак «короткий фильм» не создан, а колонка уже нужна.
        (
            2033,
            KeyError,
            "def shorts_by_decade(df):\n"
            '    """Колонка short не создана."""\n'
            '    return df.groupby(df["year"] // 10 * 10)["short"].mean()\n\n\n'
            "result = (\n"
            "    [(int(decade), round(float(share), 6)) for decade, share in shorts_by_decade(df).items()],\n"
            "    int(shorts_by_decade(df).idxmax()),\n"
            ")",
        ),
        # drop уже удалённой колонки: в наборе её больше нет.
        (
            2035,
            KeyError,
            "def wins_correlation(df):\n"
            '    """Лишний drop: колонка побегов уже выброшена."""\n'
            '    numeric = df.select_dtypes(include="number").drop(columns=["nrOfWins"])\n'
            '    return numeric.corrwith(df["nrOfWins"]).drop("nrOfWins").abs().idxmax()\n\n\n'
            "result = (\n"
            "    wins_correlation(df),\n"
            '    round(abs(float(df[wins_correlation(df)].corr(df["nrOfWins"]))), 6),\n'
            ")",
        ),
        # Функция вернула число: по нему нельзя взять колонку.
        (
            2035,
            KeyError,
            "def wins_correlation(df):\n"
            '    """Возвращает саму корреляцию."""\n'
            '    numeric = df.select_dtypes(include="number")\n'
            '    return float(numeric.corrwith(df["nrOfWins"]).drop("nrOfWins").abs().max())\n\n\n'
            "result = (\n"
            "    wins_correlation(df),\n"
            '    round(abs(float(df[wins_correlation(df)].corr(df["nrOfWins"]))), 6),\n'
            ")",
        ),
        # Корреляция по всей таблице: в ней есть текстовые колонки.
        (
            2035,
            ValueError,
            "def wins_correlation(df):\n"
            '    """Матрица корреляций по всей таблице."""\n'
            '    return df.corr()["nrOfWins"].drop("nrOfWins").abs().idxmax()\n\n\n'
            "result = (\n"
            "    wins_correlation(df),\n"
            '    round(abs(float(df[wins_correlation(df)].corr(df["nrOfWins"]))), 6),\n'
            ")",
        ),
        # Ответ словарём: строка из условия задачи ждёт пару.
        (
            2036,
            KeyError,
            "def rating_by_decade(df):\n"
            '    """Ответ словарём."""\n'
            '    means = df.assign(decade=df["year"] // 10 * 10).groupby("decade")["imdbRating"].mean()\n'
            '    trend = "ascending" if means.iloc[-1] > means.iloc[0] else "descending"\n'
            '    return {"trend": trend, "decade": means.idxmax()}\n\n\n'
            "result = rating_by_decade(df)",
        ),
        # Опечатка в имени колонки рейтинга.
        (
            2037,
            KeyError,
            "def genre_ratings(df, genres):\n"
            '    """Колонка рейтинга названа с подчёркиванием."""\n'
            "    top = df[genres].sum().sort_values(ascending=False).head(10).index\n"
            '    medians = {genre: df.loc[df[genre] == 1, "imdb_rating"].median() for genre in top}\n'
            "    spreads = {genre: float(medians[genre]) for genre in top}\n"
            "    best_median = min(medians, key=lambda genre: (-medians[genre], genre))\n"
            "    best_spread = min(spreads, key=lambda genre: (-spreads[genre], genre))\n"
            "    return (best_median, best_spread)\n\n\n"
            "result = genre_ratings(df, GENRES)",
        ),
        # Ответ словарями: строка из условия задачи обращается к idxmax.
        (
            2039,
            AttributeError,
            "def reviews_by_genre(df, genres):\n"
            '    """Сумма рецензий словарём."""\n'
            '    return {genre: int(df.loc[df[genre] == 1, "nrOfUserReviews"].sum()) for genre in genres}\n\n\n'
            "def mean_reviews_by_genre(df, genres):\n"
            '    """Среднее словарём."""\n'
            '    return {genre: float(df.loc[df[genre] == 1, "nrOfUserReviews"].mean()) for genre in genres}\n\n\n'
            "result = (\n"
            "    reviews_by_genre(df, GENRES).idxmax(),\n"
            "    int(reviews_by_genre(df, GENRES).max()),\n"
            "    mean_reviews_by_genre(df, GENRES).idxmax(),\n"
            "    round(float(mean_reviews_by_genre(df, GENRES).max()), 1),\n"
            ")",
        ),
    ]
)
