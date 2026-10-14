"""Задания с кодом второго курса: проверки работают в CPython.

Правила те же, что у mlcourse (`tests/test_seed_code_items.py`): эталон
проходит проверки, заготовка падает **с AssertionError** и объясняет, что не
так, а неверные решения — ловушки задания — не проходят. Отдельный файл, а не
общий словарь: id заданий уникальны внутри курса, и у двух курсов они
пересекаются (1001 у ИСИТ — первая функция, у mlcourse — итоговое задание
модуля 1).
"""

import contextlib

import pytest

import isit_items_topic02
import isit_items_topic03
import isit_items_topic04
from llm_tutor.web.courses import course_data, get_course

pytest.importorskip("pandas")

ISIT = "isit-ml"

# Эталонные решения. Полный текст, а не одна строка: задание проверяет и
# функцию, и то, что в result лежит её результат.
SOLUTIONS = {
    1001: 'def get_hello_world() -> str:\n'
    '    """\n    :return: hello world string\n    """\n'
    '    return "Hello world!"\n\n\n'
    "result = get_hello_world()",
    1003: "def sum_two_numbers(a: int, b: int) -> int:\n"
    '    """\n    :param a: first integer\n    :param b: second integer\n'
    '    :return: sum of a and b\n    """\n'
    "    return a + b\n\n\n"
    "result = sum_two_numbers(10, 5)",
    1005: 'def greet(name: str) -> str:\n    """Приветствие по имени."""\n'
    '    return f"Привет, {name}!"\n\n\n'
    "def add(a: int, b: int) -> int:\n"
    '    """Сумма двух чисел."""\n'
    "    return a + b\n\n\n"
    'result = (greet("Аня"), add(2, 3))',
    1011: "def get_middle_value(a: int, b: int, c: int) -> int:\n"
    "    return a + b + c - min(a, b, c) - max(a, b, c)\n\n\n"
    "result = get_middle_value(100, -10, -100)",
    1013: "def get_fizz_buzz(n: int) -> list[int | str]:\n"
    "    values: list[int | str] = []\n"
    "    for value in range(1, n + 1):\n"
    '        if value % 15 == 0:\n            values.append("FizzBuzz")\n'
    '        elif value % 3 == 0:\n            values.append("Fizz")\n'
    '        elif value % 5 == 0:\n            values.append("Buzz")\n'
    "        else:\n            values.append(value)\n"
    "    return values\n\n\n"
    "result = get_fizz_buzz(15)",
    1015: "def get_middle_value(a: int, b: int, c: int) -> int:\n"
    "    return a + b + c - min(a, b, c) - max(a, b, c)\n\n\n"
    "def get_fizz_buzz(n: int) -> list[int | str]:\n"
    "    values: list[int | str] = []\n"
    "    for value in range(1, n + 1):\n"
    '        if value % 15 == 0:\n            values.append("FizzBuzz")\n'
    '        elif value % 3 == 0:\n            values.append("Fizz")\n'
    '        elif value % 5 == 0:\n            values.append("Buzz")\n'
    "        else:\n            values.append(value)\n"
    "    return values\n\n\n"
    "result = (get_middle_value(3, 1, 2), get_fizz_buzz(5))",
    1021: "def check_ctr(clicks: int, shows: int, expected: float) -> None:\n"
    "    if ctr(clicks, shows) != expected:\n"
    '        raise AssertionError("Wrong ctr calculation")\n\n\n'
    "try:\n    check_ctr(1, 2, 0.5)\n    result = \"ошибки не было\"\n"
    "except AssertionError as exc:\n    result = str(exc)",
    1023: "def safe_ctr(clicks: int, shows: int, log: list[str]) -> float:\n"
    "    try:\n"
    '        assert clicks >= 0, "Clicks must be non-negative"\n'
    "        return float(ctr(clicks, shows))\n"
    "    except ValueError:\n        return -1.0\n"
    "    except ZeroDivisionError:\n        return 0.0\n"
    "    finally:\n        log.append(\"done\")\n\n\n"
    "log: list[str] = []\n"
    "result = [safe_ctr(2, 2, log), safe_ctr(5, -1, log), safe_ctr(10, 0, log)]",
    1025: "def safe_divide(a: float, b: float, log: list[str]) -> float:\n"
    "    try:\n"
    '        assert a >= 0, "a must be non-negative"\n'
    "        if b == 0:\n"
    '            log.append("zero")\n'
    "            return 0.0\n"
    '        log.append("ok")\n'
    "        return a / b\n"
    "    finally:\n        log.append(\"done\")\n\n\n"
    "log: list[str] = []\n"
    "result = (safe_divide(10, 2, log), safe_divide(1, 0, log), log)",
    1031: "def get_every_second_element(elements: list[int]) -> list[int]:\n"
    '    """Каждый второй элемент списка."""\n'
    "    return elements[1::2]\n\n\n"
    "result = get_every_second_element([-2, 0, 5, 2, 3, 4, 3])",
    1033: "def get_min_max(elements: list[int], default: int | None) -> tuple[int | None, int | None]:\n"
    '    """Минимум и максимум; на пустом списке — default."""\n'
    "    if not elements:\n        return default, default\n"
    "    return min(elements), max(elements)\n\n\n"
    "result = get_min_max([3, 0, 5, 2, 1, 4, 6], 0)",
    1035: "def get_last_three_index(elements: list[int]) -> int | None:\n"
    '    """Индекс последней тройки или None."""\n'
    "    if 3 not in elements:\n        return None\n"
    "    return len(elements) - 1 - elements[::-1].index(3)\n\n\n"
    "result = get_last_three_index([-2, 0, 5, 2, 3, 4, 3])",
    1037: "def get_squares(elements: list[int]) -> list[int]:\n"
    '    """Квадраты значений."""\n'
    "    return [value**2 for value in elements]\n\n\n"
    "def get_max_element_index(elements: list[int]) -> int | None:\n"
    '    """Индекс первого максимального элемента."""\n'
    "    if not elements:\n        return None\n"
    "    return max(range(len(elements)), key=lambda index: elements[index])\n\n\n"
    "def get_by_index(elements: list[int], index: int, boundary: int) -> int | None:\n"
    '    """Элемент по индексу, если он больше границы."""\n'
    "    return value if (value := elements[index]) > boundary else None\n\n\n"
    "result = (\n"
    "    get_squares([-2, 0, 5]),\n"
    "    get_max_element_index([-2, 0, 5, 2, 3, 4, 3]),\n"
    "    get_by_index([-2, 0, 5, 2, 3, 4, 3], 5, 3),\n"
    ")",
    1041: "def get_events_by_device_type(records: list[dict], device_type: str) -> list[dict]:\n"
    '    """Записи с указанным устройством, в порядке лога."""\n'
    '    return [r for r in records if r["DeviceType"] == device_type]\n\n\n'
    'result = get_events_by_device_type(records, "Safari")',
    1043: "def get_record_with_none_if_key_not_in_keys(record: dict, keys: set[str]) -> dict:\n"
    '    """Все ключи записи; чужие значения заменены на None."""\n'
    "    return {key: value if key in keys else None for key, value in record.items()}\n\n\n"
    'result = get_record_with_none_if_key_not_in_keys(record, {"EventID", "UserID"})',
    1045: "def get_unique_page_ids(records: list[dict]) -> set[int]:\n"
    '    """Уникальные страницы лога."""\n'
    '    return {r["PageID"] for r in records}\n\n\n'
    "def get_region_id_if_not_none(records: list[dict]) -> list[int]:\n"
    '    """Регионы записей, где регион определён."""\n'
    '    return [r["RegionID"] for r in records if r["RegionID"] is not None]\n\n\n'
    "def get_keys_where_value_is_not_none(record: dict) -> list[str]:\n"
    '    """Ключи записи, у которых значение не None."""\n'
    "    return [key for key, value in record.items() if value is not None]\n\n\n"
    "result = (\n"
    "    sorted(get_unique_page_ids(records)),\n"
    "    get_region_id_if_not_none(records),\n"
    "    get_keys_where_value_is_not_none(records[0]),\n"
    ")",
    1051: "def traverse_dictionary_immutable(dct: dict, prefix: str = '') -> list[tuple[str, int]]:\n"
    '    """Пары «путь через точку — значение»."""\n'
    "    result = []\n"
    "    for key, value in dct.items():\n"
    '        path = f"{prefix}.{key}" if prefix else key\n'
    "        if isinstance(value, dict):\n"
    "            result.extend(traverse_dictionary_immutable(value, path))\n"
    "        else:\n"
    "            result.append((path, value))\n"
    "    return result\n\n\n"
    'result = traverse_dictionary_immutable({"a": 1, "b": {"c": 2, "d": {"e": 4}}})',
    1053: "def traverse_dictionary_iterative(dct: dict) -> list[tuple[str, int]]:\n"
    '    """Обход явным стеком."""\n'
    "    result = []\n"
    '    stack = [(dct, "")]\n'
    "    while stack:\n"
    "        current, prefix = stack.pop()\n"
    "        for key, value in current.items():\n"
    '            path = f"{prefix}.{key}" if prefix else key\n'
    "            if isinstance(value, dict):\n"
    "                stack.append((value, path))\n"
    "            else:\n"
    "                result.append((path, value))\n"
    "    return result\n\n\n"
    'result = sorted(traverse_dictionary_iterative({"a": 1, "b": {"c": 2, "d": {"e": 4}}}))',
    1055: "def traverse_dictionary_mutable(\n"
    "    dct: dict, result: list[tuple[str, int]], prefix: str = ''\n"
    ") -> None:\n"
    '    """Рекурсивный обход: пары дописываются в result."""\n'
    "    for key, value in dct.items():\n"
    '        path = f"{prefix}.{key}" if prefix else key\n'
    "        if isinstance(value, dict):\n"
    "            traverse_dictionary_mutable(value, result, path)\n"
    "        else:\n"
    "            result.append((path, value))\n\n\n"
    "def traverse_dictionary_iterative(dct: dict) -> list[tuple[str, int]]:\n"
    '    """Итеративный обход со стеком."""\n'
    "    result = []\n"
    '    stack = [(dct, "")]\n'
    "    while stack:\n"
    "        current, prefix = stack.pop()\n"
    "        for key, value in current.items():\n"
    '            path = f"{prefix}.{key}" if prefix else key\n'
    "            if isinstance(value, dict):\n"
    "                stack.append((value, path))\n"
    "            else:\n"
    "                result.append((path, value))\n"
    "    return result\n\n\n"
    'dct = {"a": 1, "b": {"c": 2, "d": {"e": 4}}}\n'
    "collected = []\n"
    "traverse_dictionary_mutable(dct, collected)\n"
    "result = (sorted(collected), sorted(traverse_dictionary_iterative(dct)))",
    1061: "from collections.abc import Iterator\n\n\n"
    "def uniq(sequence: list[int]) -> Iterator[int]:\n"
    '    """Отдаёт элементы без повторов, в порядке входа."""\n'
    "    seen: set[int] = set()\n"
    "    for value in sequence:\n"
    "        if value not in seen:\n"
    "            seen.add(value)\n"
    "            yield value\n\n\n"
    "result = (list(uniq([1, 2, 3, 3, 1, 7])), list(uniq([])))",
    1063: "def transpose(matrix: list[list[int]]) -> list[list[int]]:\n"
    '    """Строки становятся столбцами; вход не меняется."""\n'
    "    return [list(row) for row in zip(*matrix)]\n\n\n"
    "result = (transpose([[1, 2], [3, 4], [5, 6]]), transpose([]))",
    1065: "from collections.abc import Iterator\n"
    "from operator import mul\n\n\n"
    "def uniq(sequence: list[int]) -> Iterator[int]:\n"
    '    """Элементы без повторов, в порядке входа."""\n'
    "    seen: set[int] = set()\n"
    "    for value in sequence:\n"
    "        if value not in seen:\n"
    "            seen.add(value)\n"
    "            yield value\n\n\n"
    "def dict_merge(*dicts: dict[int, int]) -> dict[int, int]:\n"
    '    """Объединение плоских словарей: поздний аргумент перекрывает ранний."""\n'
    "    merged: dict[int, int] = {}\n"
    "    for mapping in dicts:\n"
    "        merged.update(mapping)\n"
    "    return merged\n\n\n"
    "def product(lhs: list[int], rhs: list[int]) -> int:\n"
    '    """Скалярное произведение."""\n'
    "    return sum(map(mul, lhs, rhs))\n\n\n"
    "result = (\n"
    "    list(uniq([2, 1, 2, 3])),\n"
    "    dict_merge({1: 2}, {2: 2}, {1: 1}),\n"
    "    product([1, 2, 3], [4, 5, 6]),\n"
    ")",
    1071: "from dataclasses import dataclass\n\n\n"
    "@dataclass(frozen=True)\n"
    "class Item:\n"
    '    """Товар: название непустое, цена положительная, поля не меняются."""\n\n'
    "    title: str\n"
    "    cost: int\n\n"
    "    def __post_init__(self) -> None:\n"
    '        """Проверяет инварианты товара."""\n'
    '        assert self.title, "У товара должно быть название."\n'
    '        assert self.cost > 0, "Цена товара должна быть положительной."\n\n\n'
    'result = (Item("Ложка", 25) == Item("Ложка", 25), Item("Ложка", 25).cost)',
    1073: "from dataclasses import dataclass, field\n\n\n"
    "@dataclass(order=True)\n"
    "class Item:\n"
    '    """Товар: сравнение по названию, затем по цене."""\n\n'
    "    title: str\n"
    "    cost: int\n"
    "    item_id: int = field(compare=False, default=0)\n\n\n"
    "items = [\n"
    '    Item("Thing", 44, 9),\n'
    '    Item("Note", 8, 0),\n'
    '    Item("Things", 100, 1),\n'
    '    Item("Unity", 5, 8),\n'
    '    Item("Things", 44, 11),\n'
    '    Item("Note", 64, 15),\n'
    "]\n\n"
    "result = [item.item_id for item in sorted(items)]",
    1075: "class Vector:\n"
    '    """Контейнер чисел одного типа."""\n\n'
    "    def __init__(self, elements: list[int]) -> None:\n"
    '        """Запоминает элементы вектора."""\n'
    "        self._elements = list(elements)\n\n"
    "    def __len__(self) -> int:\n"
    '        """Число элементов."""\n'
    "        return len(self._elements)\n\n"
    "    def __getitem__(self, index: int) -> int:\n"
    '        """Элемент по индексу."""\n'
    "        return self._elements[index]\n\n"
    "    def append(self, element: int) -> None:\n"
    '        """Дописывает элемент в конец."""\n'
    "        self._elements.append(element)\n\n"
    "    def extend(self, elements: list[int]) -> None:\n"
    '        """Дописывает несколько элементов."""\n'
    "        self._elements.extend(elements)\n\n"
    "    def __iter__(self):\n"
    '        """Идёт по элементам по порядку."""\n'
    "        return iter(self._elements)\n\n"
    "    def __str__(self) -> str:\n"
    '        """Читаемое представление."""\n'
    '        return f"Vector({self._elements})"\n\n'
    "    def __repr__(self) -> str:\n"
    '        """Представление для отладки."""\n'
    '        return f"Vector({self._elements!r})"\n\n'
    '    def __add__(self, other: "Vector") -> "Vector":\n'
    '        """Поэлементная сумма."""\n'
    "        return Vector([left + right for left, right in zip(self._elements, other._elements)])\n\n"
    '    def __sub__(self, other: "Vector") -> "Vector":\n'
    '        """Поэлементная разность."""\n'
    "        return Vector([left - right for left, right in zip(self._elements, other._elements)])\n\n"
    '    def __mul__(self, scalar: int) -> "Vector":\n'
    '        """Умножение на скаляр."""\n'
    "        return Vector([element * scalar for element in self._elements])\n\n"
    '    def __rmul__(self, scalar: int) -> "Vector":\n'
    '        """Умножение скаляра на вектор."""\n'
    "        return self * scalar\n\n"
    '    def dot(self, other: "Vector") -> int:\n'
    '        """Скалярное произведение."""\n'
    "        return sum(left * right for left, right in zip(self._elements, other._elements))\n\n\n"
    "one = Vector([1, 2, 3])\n"
    "two = Vector([4, 5, 6])\n\n"
    "result = (list(one + two), list(two - one), list(one * 2), list(3 * two), one.dot(two))",
    1077: "class VectorIterator:\n"
    '    """Итератор вектора: хранит данные и текущую позицию."""\n\n'
    "    def __init__(self, elements: list[int]) -> None:\n"
    '        """Запоминает элементы и начинает с нулевой позиции."""\n'
    "        self._elements = elements\n"
    "        self._index = 0\n\n"
    '    def __iter__(self) -> "VectorIterator":\n'
    '        """Итератор возвращает сам себя."""\n'
    "        return self\n\n"
    "    def __next__(self) -> int:\n"
    '        """Следующий элемент или StopIteration."""\n'
    "        if self._index >= len(self._elements):\n"
    "            raise StopIteration\n"
    "        value = self._elements[self._index]\n"
    "        self._index += 1\n"
    "        return value\n\n\n"
    "class Vector:\n"
    '    """Контейнер, по которому можно пройти несколько раз."""\n\n'
    "    def __init__(self, elements: list[int]) -> None:\n"
    "        self._elements = list(elements)\n\n"
    "    def __iter__(self) -> VectorIterator:\n"
    "        return VectorIterator(self._elements)\n\n\n"
    "vector = Vector([1, 2, 3])\n"
    "first = iter(vector)\n"
    "second = iter(vector)\n\n"
    "result = (next(first), next(first), next(second), list(vector))",
    1079: "from dataclasses import dataclass, field\n\n\n"
    "@dataclass(frozen=True, order=True)\n"
    "class Item:\n"
    '    """Товар: неизменяемый, сравнивается по названию и цене."""\n\n'
    "    title: str\n"
    "    cost: int\n"
    "    item_id: int = field(compare=False, default=0)\n\n"
    "    def __post_init__(self) -> None:\n"
    '        """Проверяет инварианты товара."""\n'
    '        assert self.title, "У товара должно быть название."\n'
    '        assert self.cost > 0, "Цена товара должна быть положительной."\n\n\n'
    "class Vector:\n"
    '    """Контейнер чисел одного типа."""\n\n'
    "    def __init__(self, elements: list[int]) -> None:\n"
    '        """Запоминает элементы."""\n'
    "        self._elements = list(elements)\n\n"
    "    def __len__(self) -> int:\n"
    '        """Число элементов."""\n'
    "        return len(self._elements)\n\n"
    "    def __getitem__(self, index: int) -> int:\n"
    '        """Элемент по индексу."""\n'
    "        return self._elements[index]\n\n"
    "    def __iter__(self):\n"
    '        """Идёт по элементам по порядку."""\n'
    "        return iter(self._elements)\n\n"
    '    def __add__(self, other: "Vector") -> "Vector":\n'
    '        """Поэлементная сумма."""\n'
    "        return Vector([left + right for left, right in zip(self._elements, other._elements)])\n\n"
    '    def __mul__(self, scalar: int) -> "Vector":\n'
    '        """Умножение на скаляр."""\n'
    "        return Vector([element * scalar for element in self._elements])\n\n"
    '    def dot(self, other: "Vector") -> int:\n'
    '        """Скалярное произведение."""\n'
    "        return sum(left * right for left, right in zip(self._elements, other._elements))\n\n\n"
    "items = [\n"
    '    Item("Thing", 44, 9),\n'
    '    Item("Note", 8, 0),\n'
    '    Item("Unity", 5, 8),\n'
    "]\n\n"
    "result = (\n"
    "    sorted(items)[0].item_id,\n"
    "    list(Vector([1, 2, 3]) + Vector([4, 5, 6])),\n"
    "    Vector([1, 2, 3]).dot(Vector([4, 5, 6])),\n"
    ")",
    1081: "import time\n\n\n"
    "class Timer:\n"
    '    """Замеряет время выполнения блока `with`."""\n\n'
    "    def __init__(self) -> None:\n"
    '        """Готовит пустой замер."""\n'
    "        self.elapsed = 0.0\n"
    "        self._start = 0.0\n\n"
    '    def __enter__(self) -> "Timer":\n'
    '        """Запускает отсчёт и отдаёт менеджер в `as`."""\n'
    "        self._start = time.perf_counter()\n"
    "        return self\n\n"
    "    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:\n"
    '        """Останавливает отсчёт; исключение не подавляет."""\n'
    "        self.elapsed = time.perf_counter() - self._start\n"
    "        return False\n\n\n"
    "with Timer() as timer:\n"
    "    for _ in range(1000):\n"
    "        pass\n\n"
    "result = (isinstance(timer, Timer), timer.elapsed >= 0.0)",
    1083: "class FileManager:\n"
    '    """Открывает файл при входе в блок и закрывает при выходе."""\n\n'
    '    def __init__(self, filename: str, mode: str = "r") -> None:\n'
    '        """Запоминает имя файла и режим."""\n'
    "        self.filename = filename\n"
    "        self.mode = mode\n"
    "        self.file = None\n\n"
    "    def __enter__(self):\n"
    '        """Открывает файл и отдаёт его в `as`."""\n'
    "        self.file = open(self.filename, self.mode)\n"
    "        return self.file\n\n"
    "    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:\n"
    '        """Закрывает файл; исключение не подавляет."""\n'
    "        if self.file is not None:\n"
    "            self.file.close()\n"
    "        return False\n\n\n"
    'with FileManager("isit_demo.txt", "w") as stream:\n'
    '    stream.write("строка\\n")\n\n'
    'with FileManager("isit_demo.txt") as reader:\n'
    "    content = reader.read()\n\n"
    "result = (stream.closed, content)",
    1085: "import functools\n\n\n"
    "def traced(function):\n"
    '    """Печатает `вызов: <имя>` и возвращает результат функции."""\n\n'
    "    @functools.wraps(function)\n"
    "    def wrapper(*args, **kwargs):\n"
    '        print(f"вызов: {function.__name__}")\n'
    "        return function(*args, **kwargs)\n\n"
    "    return wrapper\n\n\n"
    "@traced\n"
    "def add(a: int, b: int = 1) -> int:\n"
    '    """Складывает два числа."""\n'
    "    return a + b\n\n\n"
    "result = (add(2, 3), add(2, b=5), add.__name__, add.__doc__)",
    1087: "import functools\n"
    "import inspect\n"
    "from collections import OrderedDict\n\n\n"
    "def cache(max_size: int):\n"
    '    """Хранит результат для `max_size` самых свежих наборов аргументов."""\n\n'
    "    def decorator(function):\n"
    "        cached: OrderedDict[tuple, object] = OrderedDict()\n"
    "        signature = inspect.signature(function)\n\n"
    "        @functools.wraps(function)\n"
    "        def wrapper(*args, **kwargs):\n"
    "            bound = signature.bind(*args, **kwargs)\n"
    "            bound.apply_defaults()\n"
    "            key = tuple(bound.arguments.items())\n"
    "            if key in cached:\n"
    "                cached.move_to_end(key)\n"
    "                return cached[key]\n"
    "            value = function(*args, **kwargs)\n"
    "            if max_size > 0:\n"
    "                cached[key] = value\n"
    "                if len(cached) > max_size:\n"
    "                    cached.popitem(last=False)\n"
    "            return value\n\n"
    "        return wrapper\n\n"
    "    return decorator\n\n\n"
    "calls: list[int] = []\n\n\n"
    "@cache(2)\n"
    "def identity(value: int) -> int:\n"
    '    """Считает настоящие вычисления."""\n'
    "    calls.append(value)\n"
    "    return value\n\n\n"
    "first = identity(1)\n"
    "second = identity(2)\n"
    "again = identity(1)\n"
    "third = identity(3)\n"
    "evicted = identity(2)\n\n"
    "result = (first, second, again, third, evicted, len(calls))",
    1089: "import functools\n"
    "import io\n"
    "import sys\n\n\n"
    "class OutputCapture:\n"
    '    """Перехватывает stdout и stderr внутри блока `with`."""\n\n'
    "    def __init__(self) -> None:\n"
    '        """Готовит пустые места под перехваченный вывод."""\n'
    '        self.stdout = ""\n'
    '        self.stderr = ""\n'
    "        self._old_stdout = None\n"
    "        self._old_stderr = None\n\n"
    '    def __enter__(self) -> "OutputCapture":\n'
    '        """Подменяет потоки и отдаёт менеджер в `as`."""\n'
    "        self._old_stdout = sys.stdout\n"
    "        self._old_stderr = sys.stderr\n"
    "        sys.stdout = io.StringIO()\n"
    "        sys.stderr = io.StringIO()\n"
    "        return self\n\n"
    "    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:\n"
    '        """Возвращает потоки на место; исключение не подавляет."""\n'
    "        self.stdout = sys.stdout.getvalue()\n"
    "        self.stderr = sys.stderr.getvalue()\n"
    "        sys.stdout = self._old_stdout\n"
    "        sys.stderr = self._old_stderr\n"
    "        return False\n\n\n"
    "def count_calls(function):\n"
    '    """Считает вызовы функции в атрибуте `calls`."""\n\n'
    "    @functools.wraps(function)\n"
    "    def wrapper(*args, **kwargs):\n"
    "        wrapper.calls += 1\n"
    "        return function(*args, **kwargs)\n\n"
    "    wrapper.calls = 0\n"
    "    return wrapper\n\n\n"
    "original = sys.stdout\n"
    "with OutputCapture() as captured:\n"
    '    print("отчёт")\n'
    '    print("ошибка", file=sys.stderr)\n\n\n'
    "@count_calls\n"
    "def doubled(value: int) -> int:\n"
    '    """Удваивает значение."""\n'
    "    return value * 2\n\n\n"
    "doubled(1)\n"
    "doubled(2)\n\n"
    "result = (captured.stdout, captured.stderr, sys.stdout is original, doubled.calls)",
}

# Верные альтернативы: другое решение той же задачи — проверки обязаны пропустить.
ALTERNATIVES = [
    (1001, 'get_hello_world = lambda: "Hello world!"\nresult = get_hello_world()'),
    (
        1003,
        "def sum_two_numbers(a: int, b: int) -> int:\n"
        '    """Сложить два числа."""\n'
        "    numbers = [a, b]\n"
        "    total = 0\n"
        "    for number in numbers:\n        total += number\n"
        "    return total\n\n\n"
        "result = sum_two_numbers(10, 5)",
    ),
    # Встроенная sum в задании не запрещена — считается поведение функции.
    (
        1003,
        "def sum_two_numbers(a: int, b: int) -> int:\n"
        '    """Сумма двух чисел."""\n'
        "    return sum([a, b])\n\n\n"
        "result = sum_two_numbers(10, 5)",
    ),
    # Запись в две строки вместо `return a + b` — тоже верное решение.
    (
        1003,
        "def sum_two_numbers(a: int, b: int) -> int:\n"
        '    """Сумма двух чисел."""\n'
        "    result = a + b\n"
        "    return result\n\n\n"
        "result = sum_two_numbers(10, 5)",
    ),
    (
        1005,
        'def greet(name: str) -> str:\n    return "Привет, " + name + "!"\n\n\n'
        "def add(a: int, b: int) -> int:\n    return int(a) + int(b)\n\n\n"
        'result = (greet("Аня"), add(2, 3))',
    ),
    (
        1011,
        "def get_middle_value(a: int, b: int, c: int) -> int:\n"
        "    if a <= b <= c or c <= b <= a:\n        return b\n"
        "    if b <= a <= c or c <= a <= b:\n        return a\n"
        "    return c\n\n\n"
        "result = get_middle_value(100, -10, -100)",
    ),
    (
        1013,
        "def get_fizz_buzz(n: int) -> list[int | str]:\n"
        "    values: list[int | str] = []\n"
        "    for value in range(1, n + 1):\n"
        "        if value % 3 == 0 and value % 5 == 0:\n"
        '            values.append("FizzBuzz")\n'
        "        elif value % 3 == 0:\n"
        '            values.append("Fizz")\n'
        "        elif value % 5 == 0:\n"
        '            values.append("Buzz")\n'
        "        else:\n            values.append(value)\n"
        "    return values\n\n\n"
        "result = get_fizz_buzz(15)",
    ),
    (
        1015,
        "def get_middle_value(a: int, b: int, c: int) -> int:\n"
        "    if a <= b <= c or c <= b <= a:\n        return b\n"
        "    if b <= a <= c or c <= a <= b:\n        return a\n"
        "    return c\n\n\n"
        "def get_fizz_buzz(n: int) -> list[int | str]:\n"
        "    values: list[int | str] = []\n"
        "    for value in range(1, n + 1):\n"
        "        if value % 15 == 0:\n"
        '            values.append("FizzBuzz")\n'
        "        elif value % 3 == 0:\n"
        '            values.append("Fizz")\n'
        "        elif value % 5 == 0:\n"
        '            values.append("Buzz")\n'
        "        else:\n            values.append(value)\n"
        "    return values\n\n\n"
        "result = (get_middle_value(3, 1, 2), get_fizz_buzz(5))",
    ),
    (
        1021,
        "def check_ctr(clicks: int, shows: int, expected: float) -> None:\n"
        '    assert ctr(clicks, shows) == expected, "Wrong ctr calculation"\n\n\n'
        "try:\n    check_ctr(1, 2, 0.5)\n    result = \"ошибки не было\"\n"
        "except AssertionError as exc:\n    result = str(exc)",
    ),
    (
        1023,
        "def safe_ctr(clicks: int, shows: int, log: list[str]) -> float:\n"
        "    try:\n"
        "        if clicks < 0:\n"
        '            raise AssertionError("Clicks must be non-negative")\n'
        "        return float(ctr(clicks, shows))\n"
        "    except ValueError:\n        return -1.0\n"
        "    except ZeroDivisionError:\n        return 0.0\n"
        "    finally:\n        log.append(\"done\")\n\n\n"
        "log: list[str] = []\n"
        "result = [safe_ctr(2, 2, log), safe_ctr(5, -1, log), safe_ctr(10, 0, log)]",
    ),
    (
        1025,
        "def safe_divide(a: float, b: float, log: list[str]) -> float:\n"
        "    try:\n"
        '        assert a >= 0, "a must be non-negative"\n'
        "        try:\n"
        "            answer = a / b\n"
        "        except ZeroDivisionError:\n"
        '            log.append("zero")\n'
        "            return 0.0\n"
        '        log.append("ok")\n'
        "        return answer\n"
        "    finally:\n        log.append(\"done\")\n\n\n"
        "log: list[str] = []\n"
        "result = (safe_divide(10, 2, log), safe_divide(1, 0, log), log)",
    ),
    (
        1031,
        "def get_every_second_element(elements: list[int]) -> list[int]:\n"
        '    """Каждый второй через индексы."""\n'
        "    result = []\n"
        "    for index in range(1, len(elements), 2):\n"
        "        result.append(elements[index])\n"
        "    return result\n\n\n"
        "result = get_every_second_element([-2, 0, 5, 2, 3, 4, 3])",
    ),
    (
        1031,
        "def get_every_second_element(elements: list[int]) -> list[int]:\n"
        '    """Каждый второй через enumerate."""\n'
        "    return [value for index, value in enumerate(elements) if index % 2]\n\n\n"
        "result = get_every_second_element([-2, 0, 5, 2, 3, 4, 3])",
    ),
    (
        1033,
        "def get_min_max(elements: list[int], default: int | None) -> tuple[int | None, int | None]:\n"
        '    """Крайние значения через sorted."""\n'
        "    if not elements:\n        return default, default\n"
        "    ordered = sorted(elements)\n"
        "    return ordered[0], ordered[-1]\n\n\n"
        "result = get_min_max([3, 0, 5, 2, 1, 4, 6], 0)",
    ),
    (
        1033,
        "def get_min_max(elements: list[int], default: int | None) -> tuple[int | None, int | None]:\n"
        '    """Крайние значения ручным перебором."""\n'
        "    if not elements:\n        return default, default\n"
        "    minimum = maximum = elements[0]\n"
        "    for value in elements[1:]:\n"
        "        if value < minimum:\n            minimum = value\n"
        "        if value > maximum:\n            maximum = value\n"
        "    return minimum, maximum\n\n\n"
        "result = get_min_max([3, 0, 5, 2, 1, 4, 6], 0)",
    ),
    (
        1035,
        "def get_last_three_index(elements: list[int]) -> int | None:\n"
        '    """Последняя тройка перебором."""\n'
        "    found = None\n"
        "    for index, value in enumerate(elements):\n"
        "        if value == 3:\n"
        "            found = index\n"
        "    return found\n\n\n"
        "result = get_last_three_index([-2, 0, 5, 2, 3, 4, 3])",
    ),
    (
        1035,
        "def get_last_three_index(elements: list[int]) -> int | None:\n"
        '    """Последняя тройка как максимум подходящих индексов."""\n'
        "    indices = [index for index, value in enumerate(elements) if value == 3]\n"
        "    return max(indices) if indices else None\n\n\n"
        "result = get_last_three_index([-2, 0, 5, 2, 3, 4, 3])",
    ),
    (
        1037,
        "def get_squares(elements: list[int]) -> list[int]:\n"
        '    """Квадраты циклом."""\n'
        "    squares = []\n"
        "    for value in elements:\n"
        "        squares.append(value * value)\n"
        "    return squares\n\n\n"
        "def get_max_element_index(elements: list[int]) -> int | None:\n"
        '    """Индекс максимума перебором."""\n'
        "    if not elements:\n        return None\n"
        "    best = 0\n"
        "    for index, value in enumerate(elements):\n"
        "        if value > elements[best]:\n            best = index\n"
        "    return best\n\n\n"
        "def get_by_index(elements: list[int], index: int, boundary: int) -> int | None:\n"
        '    """Элемент по индексу без моржового оператора."""\n'
        "    value = elements[index]\n"
        "    return value if value > boundary else None\n\n\n"
        "result = (\n"
        "    get_squares([-2, 0, 5]),\n"
        "    get_max_element_index([-2, 0, 5, 2, 3, 4, 3]),\n"
        "    get_by_index([-2, 0, 5, 2, 3, 4, 3], 5, 3),\n"
        ")",
    ),
    (
        1041,
        "def get_events_by_device_type(records: list[dict], device_type: str) -> list[dict]:\n"
        '    """Фильтр циклом, порядок сохраняется."""\n'
        "    result = []\n"
        "    for record in records:\n"
        '        if record["DeviceType"] == device_type:\n'
        "            result.append(record)\n"
        "    return result\n\n\n"
        'result = get_events_by_device_type(records, "Safari")',
    ),
    (
        1043,
        "def get_record_with_none_if_key_not_in_keys(record: dict, keys: set[str]) -> dict:\n"
        '    """Через fromkeys и update."""\n'
        "    result = dict.fromkeys(record)\n"
        "    for key in record:\n"
        "        if key in keys:\n"
        "            result[key] = record[key]\n"
        "    return result\n\n\n"
        'result = get_record_with_none_if_key_not_in_keys(record, {"EventID", "UserID"})',
    ),
    (
        1045,
        "def get_unique_page_ids(records: list[dict]) -> set[int]:\n"
        '    """Уникальные страницы циклом."""\n'
        "    pages = set()\n"
        "    for record in records:\n"
        '        pages.add(record["PageID"])\n'
        "    return pages\n\n\n"
        "def get_region_id_if_not_none(records: list[dict]) -> list[int]:\n"
        '    """Регионы циклом."""\n'
        "    regions = []\n"
        "    for record in records:\n"
        '        if record["RegionID"] is not None:\n'
        '            regions.append(record["RegionID"])\n'
        "    return regions\n\n\n"
        "def get_keys_where_value_is_not_none(record: dict) -> list[str]:\n"
        '    """Ключи циклом."""\n'
        "    keys = []\n"
        "    for key, value in record.items():\n"
        "        if value is not None:\n"
        "            keys.append(key)\n"
        "    return keys\n\n\n"
        "result = (\n"
        "    sorted(get_unique_page_ids(records)),\n"
        "    get_region_id_if_not_none(records),\n"
        "    get_keys_where_value_is_not_none(records[0]),\n"
        ")",
    ),
    # Склейка через `+` вместо `extend` — тот же ответ дороже по памяти.
    (
        1051,
        "def traverse_dictionary_immutable(dct: dict, prefix: str = '') -> list[tuple[str, int]]:\n"
        '    """Склейка через +."""\n'
        "    result: list[tuple[str, int]] = []\n"
        "    for key, value in dct.items():\n"
        '        path = f"{prefix}.{key}" if prefix else key\n'
        "        if isinstance(value, dict):\n"
        "            result = result + traverse_dictionary_immutable(value, path)\n"
        "        else:\n"
        "            result = result + [(path, value)]\n"
        "    return result\n\n\n"
        'result = traverse_dictionary_immutable({"a": 1, "b": {"c": 2, "d": {"e": 4}}})',
    ),
    # Очередь вместо стека: порядок пар другой — тесты его и не требуют.
    (
        1053,
        "from collections import deque\n\n\n"
        "def traverse_dictionary_iterative(dct: dict) -> list[tuple[str, int]]:\n"
        '    """Обход очередью: порядок другой, результат тот же."""\n'
        "    result = []\n"
        '    queue = deque([(dct, "")])\n'
        "    while queue:\n"
        "        current, prefix = queue.popleft()\n"
        "        for key, value in current.items():\n"
        '            path = f"{prefix}.{key}" if prefix else key\n'
        "            if isinstance(value, dict):\n"
        "                queue.append((value, path))\n"
        "            else:\n"
        "                result.append((path, value))\n"
        "    return result\n\n\n"
        'result = sorted(traverse_dictionary_iterative({"a": 1, "b": {"c": 2, "d": {"e": 4}}}))',
    ),
    (
        1055,
        "def traverse_dictionary_mutable(\n"
        "    dct: dict, result: list[tuple[str, int]], prefix: str = ''\n"
        ") -> None:\n"
        '    """Рекурсивный обход с локальным префиксом."""\n'
        "    for key, value in dct.items():\n"
        '        path = f"{prefix}.{key}" if prefix else key\n'
        "        if isinstance(value, dict):\n"
        "            traverse_dictionary_mutable(value, result, path)\n"
        "        else:\n"
        "            result.append((path, value))\n\n\n"
        "def traverse_dictionary_iterative(dct: dict) -> list[tuple[str, int]]:\n"
        '    """Обход стеком, дети кладутся в обратном порядке."""\n'
        "    result = []\n"
        "    stack = [(dct, '')]\n"
        "    while stack:\n"
        "        current, prefix = stack.pop()\n"
        "        for key in reversed(list(current)):\n"
        "            value = current[key]\n"
        '            path = f"{prefix}.{key}" if prefix else key\n'
        "            if isinstance(value, dict):\n"
        "                stack.append((value, path))\n"
        "            else:\n"
        "                result.append((path, value))\n"
        "    return result\n\n\n"
        'dct = {"a": 1, "b": {"c": 2, "d": {"e": 4}}}\n'
        "collected = []\n"
        "traverse_dictionary_mutable(dct, collected)\n"
        "result = (sorted(collected), sorted(traverse_dictionary_iterative(dct)))",
    ),
    # Ленивое решение через filter: генератор не обязан быть функцией с `yield`.
    (
        1061,
        "from collections.abc import Iterator\n\n\n"
        "def uniq(sequence: list[int]) -> Iterator[int]:\n"
        '    """Через замыкание и filter."""\n'
        "    seen: set[int] = set()\n\n"
        "    def first_time(value: int) -> bool:\n"
        "        if value in seen:\n"
        "            return False\n"
        "        seen.add(value)\n"
        "        return True\n\n"
        "    return filter(first_time, sequence)\n\n\n"
        "result = (list(uniq([1, 2, 3, 3, 1, 7])), list(uniq([])))",
    ),
    (
        1061,
        "from collections.abc import Iterator\n\n\n"
        "def uniq(sequence: list[int]) -> Iterator[int]:\n"
        '    """Генераторное выражение с множеством-побочным эффектом."""\n'
        "    seen: set[int] = set()\n"
        "    return (value for value in sequence if not (value in seen or seen.add(value)))\n\n\n"
        "result = (list(uniq([1, 2, 3, 3, 1, 7])), list(uniq([])))",
    ),
    # Без импорта `Iterator` и без аннотаций: проверка обязана объяснять ленивость
    # сама, а не за счёт имён, принесённых решением (аудит среза 4, 1061).
    (
        1061,
        "def uniq(sequence: list[int]):\n"
        '    """Множество-накопитель плюс yield."""\n'
        "    seen = set()\n"
        "    for value in sequence:\n"
        "        if value not in seen:\n"
        "            seen.add(value)\n"
        "            yield value\n\n\n"
        "result = (list(uniq([1, 2, 3, 3, 1, 7])), list(uniq([])))",
    ),
    # Транспонирование по индексам: пустая матрица проверяется отдельно.
    (
        1063,
        "def transpose(matrix: list[list[int]]) -> list[list[int]]:\n"
        '    """По индексам."""\n'
        "    if not matrix:\n"
        "        return []\n"
        "    return [[row[column] for row in matrix] for column in range(len(matrix[0]))]\n\n\n"
        "result = (transpose([[1, 2], [3, 4], [5, 6]]), transpose([]))",
    ),
    (
        1063,
        "def transpose(matrix: list[list[int]]) -> list[list[int]]:\n"
        '    """Через map и zip."""\n'
        "    return list(map(list, zip(*matrix)))\n\n\n"
        "result = (transpose([[1, 2], [3, 4], [5, 6]]), transpose([]))",
    ),
    (
        1065,
        "import functools\n\n\n"
        "def uniq(sequence: list[int]):\n"
        '    """Ленивая дедупликация через filter."""\n'
        "    seen: set[int] = set()\n\n"
        "    def first_time(value: int) -> bool:\n"
        "        if value in seen:\n"
        "            return False\n"
        "        seen.add(value)\n"
        "        return True\n\n"
        "    return filter(first_time, sequence)\n\n\n"
        "def dict_merge(*dicts: dict[int, int]) -> dict[int, int]:\n"
        '    """Через dict и chain: поздние пары перекрывают ранние."""\n'
        "    return dict(functools.reduce(lambda left, right: left + list(right.items()), dicts, []))\n\n\n"
        "def product(lhs: list[int], rhs: list[int]) -> int:\n"
        '    """Циклом."""\n'
        "    total = 0\n"
        "    for left, right in zip(lhs, rhs):\n"
        "        total += left * right\n"
        "    return total\n\n\n"
        "result = (\n"
        "    list(uniq([2, 1, 2, 3])),\n"
        "    dict_merge({1: 2}, {2: 2}, {1: 1}),\n"
        "    product([1, 2, 3], [4, 5, 6]),\n"
        ")",
    ),
    (
        1071,
        "from dataclasses import dataclass\n\n\n"
        "def check(item: \"Item\") -> None:\n"
        '    """Проверки инвариантов отдельной функцией."""\n'
        '    assert item.title != "", "Название не может быть пустым."\n'
        '    assert item.cost > 0, "Цена должна быть положительной."\n\n\n'
        "@dataclass(frozen=True)\n"
        "class Item:\n"
        '    """Товар с проверками в отдельной функции."""\n\n'
        "    title: str\n"
        "    cost: int\n\n"
        "    def __post_init__(self) -> None:\n"
        '        """Вызывает проверки."""\n'
        "        check(self)\n\n\n"
        'result = (Item("Ложка", 25) == Item("Ложка", 25), Item("Ложка", 25).cost)',
    ),
    (
        1071,
        "from dataclasses import dataclass\n\n\n"
        "@dataclass(frozen=True)\n"
        "class Item:\n"
        '    """Товар с одной проверкой на два поля."""\n\n'
        "    title: str\n"
        "    cost: int\n\n"
        "    def __post_init__(self) -> None:\n"
        '        """Проверяет оба поля одним условием."""\n'
        '        assert self.title and self.cost > 0, "Название и цена должны быть заполнены."\n\n\n'
        'result = (Item("Ложка", 25) == Item("Ложка", 25), Item("Ложка", 25).cost)',
    ),
    # Сравнение задано вручную: order=True не обязателен.
    (
        1073,
        "from dataclasses import dataclass\n\n\n"
        "@dataclass\n"
        "class Item:\n"
        '    """Сравнение задано вручную."""\n\n'
        "    title: str\n"
        "    cost: int\n"
        "    item_id: int = 0\n\n"
        "    def __eq__(self, other: object) -> bool:\n"
        '        """Равенство по названию и цене."""\n'
        "        if not isinstance(other, Item):\n"
        "            return NotImplemented\n"
        "        return (self.title, self.cost) == (other.title, other.cost)\n\n"
        '    def __lt__(self, other: "Item") -> bool:\n'
        '        """Порядок по названию, затем по цене."""\n'
        "        return (self.title, self.cost) < (other.title, other.cost)\n\n\n"
        "items = [\n"
        '    Item("Thing", 44, 9),\n'
        '    Item("Note", 8, 0),\n'
        '    Item("Things", 100, 1),\n'
        '    Item("Unity", 5, 8),\n'
        '    Item("Things", 44, 11),\n'
        '    Item("Note", 64, 15),\n'
        "]\n\n"
        "result = [item.item_id for item in sorted(items)]",
    ),
    (
        1073,
        "from dataclasses import dataclass, field\n\n\n"
        "@dataclass(order=True)\n"
        "class Item:\n"
        '    """item_id исключён, порядок задан описанием полей."""\n\n'
        "    title: str\n"
        "    cost: int\n"
        "    item_id: int = field(default=0, compare=False)\n\n\n"
        "items = [\n"
        '    Item("Thing", 44, 9),\n'
        '    Item("Note", 8, 0),\n'
        '    Item("Things", 100, 1),\n'
        '    Item("Unity", 5, 8),\n'
        '    Item("Things", 44, 11),\n'
        '    Item("Note", 64, 15),\n'
        "]\n\n"
        "result = [item.item_id for item in sorted(items)]",
    ),
    (
        1075,
        "from operator import add, mul, sub\n\n\n"
        "class Vector:\n"
        '    """Арифметика через map и operator."""\n\n'
        "    def __init__(self, elements: list[int]) -> None:\n"
        '        """Запоминает элементы."""\n'
        "        self._elements = list(elements)\n\n"
        "    def __len__(self) -> int:\n"
        '        """Число элементов."""\n'
        "        return len(self._elements)\n\n"
        "    def __getitem__(self, index: int) -> int:\n"
        '        """Элемент по индексу."""\n'
        "        return self._elements[index]\n\n"
        "    def append(self, element: int) -> None:\n"
        '        """Дописывает элемент."""\n'
        "        self._elements.append(element)\n\n"
        "    def extend(self, elements: list[int]) -> None:\n"
        '        """Дописывает несколько элементов."""\n'
        "        self._elements.extend(elements)\n\n"
        "    def __iter__(self):\n"
        '        """Идёт по элементам."""\n'
        "        return iter(self._elements)\n\n"
        "    def __str__(self) -> str:\n"
        '        """Читаемое представление."""\n'
        '        return f"Vector({self._elements})"\n\n'
        "    def __repr__(self) -> str:\n"
        '        """Представление для отладки."""\n'
        '        return f"Vector({self._elements!r})"\n\n'
        '    def __add__(self, other: "Vector") -> "Vector":\n'
        '        """Поэлементная сумма."""\n'
        "        return Vector(list(map(add, self._elements, other._elements)))\n\n"
        '    def __sub__(self, other: "Vector") -> "Vector":\n'
        '        """Поэлементная разность."""\n'
        "        return Vector(list(map(sub, self._elements, other._elements)))\n\n"
        '    def __mul__(self, scalar: int) -> "Vector":\n'
        '        """Умножение на скаляр."""\n'
        "        return Vector([element * scalar for element in self._elements])\n\n"
        "    __rmul__ = __mul__\n\n"
        '    def dot(self, other: "Vector") -> int:\n'
        '        """Скалярное произведение."""\n'
        "        return sum(map(mul, self._elements, other._elements))\n\n\n"
        "one = Vector([1, 2, 3])\n"
        "two = Vector([4, 5, 6])\n\n"
        "result = (list(one + two), list(two - one), list(one * 2), list(3 * two), one.dot(two))",
    ),
    (
        1077,
        "class VectorIterator:\n"
        '    """Итератор поверх встроенного итератора списка."""\n\n'
        "    def __init__(self, elements: list[int]) -> None:\n"
        '        """Берёт готовый итератор списка."""\n'
        "        self._iterator = iter(elements)\n\n"
        "    def __iter__(self):\n"
        '        """Возвращает сам себя."""\n'
        "        return self\n\n"
        "    def __next__(self) -> int:\n"
        '        """Отдаёт следующий элемент списка."""\n'
        "        return next(self._iterator)\n\n\n"
        "class Vector:\n"
        '    """Контейнер, по которому можно пройти несколько раз."""\n\n'
        "    def __init__(self, elements: list[int]) -> None:\n"
        "        self._elements = list(elements)\n\n"
        "    def __iter__(self):\n"
        "        return VectorIterator(self._elements)\n\n\n"
        "vector = Vector([1, 2, 3])\n"
        "first = iter(vector)\n"
        "second = iter(vector)\n\n"
        "result = (next(first), next(first), next(second), list(vector))",
    ),
    (
        1077,
        "class VectorIterator:\n"
        '    """Итератор, который забирает элементы из своего списка."""\n\n'
        "    def __init__(self, elements: list[int]) -> None:\n"
        '        """Копирует элементы себе."""\n'
        "        self._remaining = list(elements)\n\n"
        "    def __iter__(self):\n"
        '        """Возвращает сам себя."""\n'
        "        return self\n\n"
        "    def __next__(self) -> int:\n"
        '        """Отдаёт первый из оставшихся."""\n'
        "        if not self._remaining:\n"
        "            raise StopIteration\n"
        "        return self._remaining.pop(0)\n\n\n"
        "class Vector:\n"
        '    """Контейнер, по которому можно пройти несколько раз."""\n\n'
        "    def __init__(self, elements: list[int]) -> None:\n"
        "        self._elements = list(elements)\n\n"
        "    def __iter__(self):\n"
        "        return VectorIterator(self._elements)\n\n\n"
        "vector = Vector([1, 2, 3])\n"
        "first = iter(vector)\n"
        "second = iter(vector)\n\n"
        "result = (next(first), next(first), next(second), list(vector))",
    ),
    (
        1079,
        "from dataclasses import dataclass, field\n"
        "from operator import add, mul\n\n\n"
        "@dataclass(frozen=True)\n"
        "class Item:\n"
        '    """Сравнение по названию и цене задано вручную."""\n\n'
        "    title: str\n"
        "    cost: int\n"
        "    item_id: int = field(compare=False, default=0)\n\n"
        "    def __post_init__(self) -> None:\n"
        '        """Проверяет инварианты."""\n'
        '        assert self.title, "У товара должно быть название."\n'
        '        assert self.cost > 0, "Цена товара должна быть положительной."\n\n'
        '    def __lt__(self, other: "Item") -> bool:\n'
        '        """Порядок по названию и цене."""\n'
        "        return (self.title, self.cost) < (other.title, other.cost)\n\n\n"
        "class Vector:\n"
        '    """Контейнер чисел одного типа."""\n\n'
        "    def __init__(self, elements: list[int]) -> None:\n"
        '        """Запоминает элементы."""\n'
        "        self._elements = list(elements)\n\n"
        "    def __len__(self) -> int:\n"
        '        """Число элементов."""\n'
        "        return len(self._elements)\n\n"
        "    def __getitem__(self, index: int) -> int:\n"
        '        """Элемент по индексу."""\n'
        "        return self._elements[index]\n\n"
        "    def __iter__(self):\n"
        '        """Идёт по элементам."""\n'
        "        return iter(self._elements)\n\n"
        '    def __add__(self, other: "Vector") -> "Vector":\n'
        '        """Поэлементная сумма."""\n'
        "        return Vector(list(map(add, self._elements, other._elements)))\n\n"
        '    def __mul__(self, scalar: int) -> "Vector":\n'
        '        """Умножение на скаляр."""\n'
        "        return Vector([element * scalar for element in self._elements])\n\n"
        '    def dot(self, other: "Vector") -> int:\n'
        '        """Скалярное произведение."""\n'
        "        return sum(map(mul, self._elements, other._elements))\n\n\n"
        "items = [\n"
        '    Item("Thing", 44, 9),\n'
        '    Item("Note", 8, 0),\n'
        '    Item("Unity", 5, 8),\n'
        "]\n\n"
        "result = (\n"
        "    sorted(items)[0].item_id,\n"
        "    list(Vector([1, 2, 3]) + Vector([4, 5, 6])),\n"
        "    Vector([1, 2, 3]).dot(Vector([4, 5, 6])),\n"
        ")",
    ),
    (
        1081,
        "import time\n\n\n"
        "class Timer:\n"
        '    """Через monotonic и с `return None` из __exit__."""\n\n'
        "    def __init__(self) -> None:\n"
        '        """Готовит пустой замер."""\n'
        "        self.elapsed = 0.0\n\n"
        '    def __enter__(self) -> "Timer":\n'
        '        """Запускает отсчёт."""\n'
        "        self._start = time.monotonic()\n"
        "        return self\n\n"
        "    def __exit__(self, exc_type, exc_val, exc_tb) -> None:\n"
        '        """Останавливает отсчёт; ничего не возвращает."""\n'
        "        self.elapsed = time.monotonic() - self._start\n\n\n"
        "with Timer() as timer:\n"
        "    for _ in range(1000):\n"
        "        pass\n\n"
        "result = (isinstance(timer, Timer), timer.elapsed >= 0.0)",
    ),
    # Сверка длин в арифметике: правило конспекта, проверке оно не мешает
    # (аудит среза 4, own_types.md).
    (
        1075,
        "class Vector:\n"
        '    """Арифметика со сверкой длин."""\n\n'
        "    def __init__(self, elements: list[int]) -> None:\n"
        '        """Запоминает элементы."""\n'
        "        self._elements = list(elements)\n\n"
        "    def __len__(self) -> int:\n"
        '        """Число элементов."""\n'
        "        return len(self._elements)\n\n"
        "    def __getitem__(self, index: int) -> int:\n"
        '        """Элемент по индексу."""\n'
        "        return self._elements[index]\n\n"
        "    def append(self, element: int) -> None:\n"
        '        """Дописывает элемент."""\n'
        "        self._elements.append(element)\n\n"
        "    def extend(self, elements: list[int]) -> None:\n"
        '        """Дописывает несколько элементов."""\n'
        "        self._elements.extend(elements)\n\n"
        "    def __iter__(self):\n"
        '        """Идёт по элементам."""\n'
        "        return iter(self._elements)\n\n"
        "    def _pair(self, other: \"Vector\") -> list[tuple[int, int]]:\n"
        '        """Пары элементов; разная длина — ошибка."""\n'
        "        if len(self) != len(other):\n"
        '            raise ValueError("длины векторов не совпадают")\n'
        "        return list(zip(self._elements, other._elements))\n\n"
        "    def __str__(self) -> str:\n"
        '        """Читаемое представление."""\n'
        '        return "Vector(" + ", ".join(str(x) for x in self._elements) + ")"\n\n'
        "    def __repr__(self) -> str:\n"
        '        """Представление для отладки."""\n'
        '        return f"Vector({self._elements!r})"\n\n'
        "    def __add__(self, other: \"Vector\") -> \"Vector\":\n"
        '        """Поэлементная сумма."""\n'
        "        return Vector([left + right for left, right in self._pair(other)])\n\n"
        "    def __sub__(self, other: \"Vector\") -> \"Vector\":\n"
        '        """Поэлементная разность."""\n'
        "        return Vector([left - right for left, right in self._pair(other)])\n\n"
        "    def __mul__(self, scalar: int) -> \"Vector\":\n"
        '        """Умножение на скаляр."""\n'
        "        return Vector([element * scalar for element in self._elements])\n\n"
        "    def __rmul__(self, scalar: int) -> \"Vector\":\n"
        '        """Скаляр слева."""\n'
        "        return self * scalar\n\n"
        "    def dot(self, other: \"Vector\") -> int:\n"
        '        """Скалярное произведение."""\n'
        "        return sum(left * right for left, right in self._pair(other))\n\n\n"
        "v1 = Vector([1, 2, 3])\n"
        "v2 = Vector([4, 5, 6])\n"
        "result = (list(v1 + v2), list(v2 - v1), list(v1 * 2), list(3 * v2), v1.dot(v2))",
    ),
    (
        1083,
        "class FileManager:\n"
        '    """Открывает файл при входе и закрывает при выходе."""\n\n'
        '    def __init__(self, filename: str, mode: str = "r") -> None:\n'
        '        """Запоминает имя файла и режим."""\n'
        "        self.filename = filename\n"
        "        self.mode = mode\n"
        "        self.file = None\n\n"
        "    def __enter__(self):\n"
        '        """Открывает файл."""\n'
        "        self.file = open(self.filename, self.mode)\n"
        "        return self.file\n\n"
        "    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:\n"
        '        """Закрывает файл, если он открыт."""\n'
        "        if self.file:\n"
        "            self.file.close()\n"
        "        return None\n\n\n"
        'with FileManager("isit_demo.txt", "w") as stream:\n'
        '    stream.write("строка\\n")\n\n'
        'with FileManager("isit_demo.txt") as reader:\n'
        "    content = reader.read()\n\n"
        "result = (stream.closed, content)",
    ),
    (
        1085,
        "import functools\n\n\n"
        "def traced(function):\n"
        '    """Через update_wrapper и явную склейку строк."""\n\n'
        "    def wrapper(*args, **kwargs):\n"
        '        print("вызов: " + function.__name__)\n'
        "        return function(*args, **kwargs)\n\n"
        "    return functools.update_wrapper(wrapper, function)\n\n\n"
        "@traced\n"
        "def add(a: int, b: int = 1) -> int:\n"
        '    """Складывает два числа."""\n'
        "    return a + b\n\n\n"
        "result = (add(2, 3), add(2, b=5), add.__name__, add.__doc__)",
    ),
    (
        1087,
        "import functools\n"
        "import inspect\n\n\n"
        "def cache(max_size: int):\n"
        '    """Свежесть держится удалением и повторной вставкой в обычный dict."""\n\n'
        "    def decorator(function):\n"
        "        cached: dict[tuple, object] = {}\n"
        "        signature = inspect.signature(function)\n\n"
        "        @functools.wraps(function)\n"
        "        def wrapper(*args, **kwargs):\n"
        "            bound = signature.bind(*args, **kwargs)\n"
        "            bound.apply_defaults()\n"
        "            key = tuple(bound.arguments.items())\n"
        "            if key in cached:\n"
        "                value = cached.pop(key)\n"
        "                cached[key] = value\n"
        "                return value\n"
        "            value = function(*args, **kwargs)\n"
        "            if max_size:\n"
        "                cached[key] = value\n"
        "                while len(cached) > max_size:\n"
        "                    del cached[next(iter(cached))]\n"
        "            return value\n\n"
        "        return wrapper\n\n"
        "    return decorator\n\n\n"
        "calls: list[int] = []\n\n\n"
        "@cache(2)\n"
        "def identity(value: int) -> int:\n"
        '    """Считает настоящие вычисления."""\n'
        "    calls.append(value)\n"
        "    return value\n\n\n"
        "first = identity(1)\n"
        "second = identity(2)\n"
        "again = identity(1)\n"
        "third = identity(3)\n"
        "evicted = identity(2)\n\n"
        "result = (first, second, again, third, evicted, len(calls))",
    ),
    (
        1089,
        "import functools\n"
        "import io\n"
        "import sys\n\n\n"
        "class OutputCapture:\n"
        '    """Потоки сохраняются кортежем."""\n\n'
        "    def __init__(self) -> None:\n"
        '        """Готовит пустые места под перехваченный вывод."""\n'
        '        self.stdout = ""\n'
        '        self.stderr = ""\n'
        "        self._saved = (sys.stdout, sys.stderr)\n\n"
        '    def __enter__(self) -> "OutputCapture":\n'
        '        """Подменяет оба потока."""\n'
        "        self._saved = (sys.stdout, sys.stderr)\n"
        "        sys.stdout, sys.stderr = io.StringIO(), io.StringIO()\n"
        "        return self\n\n"
        "    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:\n"
        '        """Возвращает потоки на место."""\n'
        "        self.stdout = sys.stdout.getvalue()\n"
        "        self.stderr = sys.stderr.getvalue()\n"
        "        sys.stdout, sys.stderr = self._saved\n"
        "        return False\n\n\n"
        "def count_calls(function):\n"
        '    """Счётчик в словаре, атрибут обновляется на каждом вызове."""\n\n'
        "    seen = {\"calls\": 0}\n\n"
        "    @functools.wraps(function)\n"
        "    def wrapper(*args, **kwargs):\n"
        "        seen[\"calls\"] += 1\n"
        "        wrapper.calls = seen[\"calls\"]\n"
        "        return function(*args, **kwargs)\n\n"
        "    wrapper.calls = 0\n"
        "    return wrapper\n\n\n"
        "original = sys.stdout\n"
        "with OutputCapture() as captured:\n"
        '    print("отчёт")\n'
        '    print("ошибка", file=sys.stderr)\n\n\n'
        "@count_calls\n"
        "def doubled(value: int) -> int:\n"
        '    """Удваивает значение."""\n'
        "    return value * 2\n\n\n"
        "doubled(1)\n"
        "doubled(2)\n\n"
        "result = (captured.stdout, captured.stderr, sys.stdout is original, doubled.calls)",
    ),
]

# Главная ловушка задания: решение выглядит разумным, но проверку не проходит.
WRONG = {
    1001: 'def get_hello_world() -> str:\n    print("Hello world!")\n\n\n'
    'result = "Hello world!"',
    1003: "def sum_two_numbers(a: int, b: int) -> int:\n"
    '    """\n    :return: sum of a and b\n    """\n'
    "    return a - b\n\n\n"
    "result = sum_two_numbers(10, 5)",
    1005: 'def greet(name: str) -> str:\n    return "Привет, Аня!"\n\n\n'
    "def add(a: int, b: int) -> int:\n    return a + b\n\n\n"
    'result = (greet("Аня"), add(2, 3))',
    1011: "def get_middle_value(a: int, b: int, c: int) -> int:\n"
    "    return max(a, b, c)\n\n\n"
    "result = get_middle_value(100, -10, -100)",
    1013: "def get_fizz_buzz(n: int) -> list[int | str]:\n"
    "    values: list[int | str] = []\n"
    "    for value in range(1, n + 1):\n"
    "        if value % 3 == 0:\n"
    '            values.append("Fizz")\n'
    "        elif value % 5 == 0:\n"
    '            values.append("Buzz")\n'
    "        elif value % 15 == 0:\n"
    '            values.append("FizzBuzz")\n'
    "        else:\n            values.append(value)\n"
    "    return values\n\n\n"
    "result = get_fizz_buzz(15)",
    1015: "def get_middle_value(a: int, b: int, c: int) -> int:\n"
    "    return max(a, b, c)\n\n\n"
    "def get_fizz_buzz(n: int) -> list[int | str]:\n"
    "    values: list[int | str] = []\n"
    "    for value in range(1, n + 1):\n"
    "        if value % 15 == 0:\n"
    '            values.append("FizzBuzz")\n'
    "        elif value % 3 == 0:\n"
    '            values.append("Fizz")\n'
    "        elif value % 5 == 0:\n"
    '            values.append("Buzz")\n'
    "        else:\n            values.append(value)\n"
    "    return values\n\n\n"
    "result = (get_middle_value(3, 1, 2), get_fizz_buzz(5))",
    1021: "def check_ctr(clicks: int, shows: int, expected: float) -> None:\n"
    "    assert ctr(clicks, shows) == expected\n\n\n"
    "try:\n    check_ctr(1, 2, 0.5)\n    result = \"ошибки не было\"\n"
    "except AssertionError as exc:\n    result = str(exc)",
    1023: "def safe_ctr(clicks: int, shows: int, log: list[str]) -> float:\n"
    "    try:\n"
    '        assert clicks >= 0, "Clicks must be non-negative"\n'
    "        return float(ctr(clicks, shows))\n"
    "    except (ValueError, ZeroDivisionError):\n        return -1.0\n"
    "    finally:\n        log.append(\"done\")\n\n\n"
    "log: list[str] = []\n"
    "result = [safe_ctr(2, 2, log), safe_ctr(5, -1, log), safe_ctr(10, 0, log)]",
    # Главная ловушка: деление на ноль обработано, но в журнал не попало «zero»
    # — по журналу видно и порядок записей, и что ветка вообще выполнялась.
    1025: "def safe_divide(a: float, b: float, log: list[str]) -> float:\n"
    "    try:\n"
    '        assert a >= 0, "a must be non-negative"\n'
    "        if b == 0:\n"
    "            return 0.0\n"
    '        log.append("ok")\n'
    "        return a / b\n"
    "    finally:\n        log.append(\"done\")\n\n\n"
    "log: list[str] = []\n"
    "result = (safe_divide(10, 2, log), safe_divide(1, 0, log), log)",
    1031: "def get_every_second_element(elements: list[int]) -> list[int]:\n"
    '    """Каждый второй — думаем, что с нулевого."""\n'
    "    return elements[::2]\n\n\n"
    "result = get_every_second_element([-2, 0, 5, 2, 3, 4, 3])",
    1035: "def get_last_three_index(elements: list[int]) -> int | None:\n"
    '    """Первая тройка вместо последней."""\n'
    "    if 3 not in elements:\n        return None\n"
    "    return elements.index(3)\n\n\n"
    "result = get_last_three_index([-2, 0, 5, 2, 3, 4, 3])",
    1043: "def get_record_with_none_if_key_not_in_keys(record: dict, keys: set[str]) -> dict:\n"
    '    """Фильтр вместо замены: ключи потеряны."""\n'
    "    return {key: value for key, value in record.items() if key in keys}\n\n\n"
    'result = get_record_with_none_if_key_not_in_keys(record, {"EventID", "UserID"})',
    1051: "def traverse_dictionary_immutable(dct: dict) -> list[tuple[str, int]]:\n"
    '    """Путь без префикса: вложенные ключи теряют родителя."""\n'
    "    result = []\n"
    "    for key, value in dct.items():\n"
    "        if isinstance(value, dict):\n"
    "            result.extend(traverse_dictionary_immutable(value))\n"
    "        else:\n"
    "            result.append((key, value))\n"
    "    return result\n\n\n"
    'result = traverse_dictionary_immutable({"a": 1, "b": {"c": 2, "d": {"e": 4}}})',
    1053: "def traverse_dictionary_iterative(dct: dict) -> list[tuple[str, int]]:\n"
    '    """Рекурсия вместо стека, да ещё и без накопленного пути."""\n'
    "    result = []\n"
    "    for key, value in dct.items():\n"
    "        if isinstance(value, dict):\n"
    "            result.extend(traverse_dictionary_iterative(value))\n"
    "        else:\n"
    "            result.append((key, value))\n"
    "    return result\n\n\n"
    'result = sorted(traverse_dictionary_iterative({"a": 1, "b": {"c": 2, "d": {"e": 4}}}))',
    1061: "from collections.abc import Iterator\n\n\n"
    "def uniq(sequence: list[int]) -> Iterator[int]:\n"
    '    """Отдаёт всё как есть: повторы остаются на месте."""\n'
    "    for value in sequence:\n"
    "        yield value\n\n\n"
    "result = (list(uniq([1, 2, 3, 3, 1, 7])), list(uniq([])))",
    1063: "def transpose(matrix: list[list[int]]) -> list[list[int]]:\n"
    '    """Забыл транспонировать: строки остались строками."""\n'
    "    return [row[:] for row in matrix]\n\n\n"
    "result = (transpose([[1, 2], [3, 4], [5, 6]]), transpose([]))",
    1065: "from collections.abc import Iterator\n\n\n"
    "def uniq(sequence: list[int]) -> Iterator[int]:\n"
    '    """Элементы без повторов, в порядке входа."""\n'
    "    seen: set[int] = set()\n"
    "    for value in sequence:\n"
    "        if value not in seen:\n"
    "            seen.add(value)\n"
    "            yield value\n\n\n"
    "def dict_merge(*dicts: dict[int, int]) -> dict[int, int]:\n"
    '    """Объединение плоских словарей: поздний аргумент перекрывает ранний."""\n'
    "    merged: dict[int, int] = {}\n"
    "    for mapping in dicts:\n"
    "        merged.update(mapping)\n"
    "    return merged\n\n\n"
    "def product(lhs: list[int], rhs: list[int]) -> int:\n"
    '    """Произведение сумм вместо суммы произведений."""\n'
    "    return sum(lhs) * sum(rhs)\n\n\n"
    "result = (\n"
    "    list(uniq([2, 1, 2, 3])),\n"
    "    dict_merge({1: 2}, {2: 2}, {1: 1}),\n"
    "    product([1, 2, 3], [4, 5, 6]),\n"
    ")",
    1071: "from dataclasses import dataclass\n\n\n"
    "@dataclass\n"
    "class Item:\n"
    '    """Товар без frozen: поля меняются уже после создания."""\n\n'
    "    title: str\n"
    "    cost: int\n\n"
    "    def __post_init__(self) -> None:\n"
    '        """Проверяет инварианты товара."""\n'
    '        assert self.title, "У товара должно быть название."\n'
    '        assert self.cost > 0, "Цена товара должна быть положительной."\n\n\n'
    'result = (Item("Ложка", 25) == Item("Ложка", 25), Item("Ложка", 25).cost)',
    1073: "from dataclasses import dataclass\n\n\n"
    "@dataclass(order=True)\n"
    "class Item:\n"
    '    """item_id участвует в сравнении наравне с остальными полями."""\n\n'
    "    title: str\n"
    "    cost: int\n"
    "    item_id: int = 0\n\n\n"
    "items = [\n"
    '    Item("Thing", 44, 9),\n'
    '    Item("Note", 8, 0),\n'
    '    Item("Things", 100, 1),\n'
    '    Item("Unity", 5, 8),\n'
    '    Item("Things", 44, 11),\n'
    '    Item("Note", 64, 15),\n'
    "]\n\n"
    "result = [item.item_id for item in sorted(items)]",
    1075: "class Vector:\n"
    '    """Умножение повторяет список вместо умножения элементов."""\n\n'
    "    def __init__(self, elements: list[int]) -> None:\n"
    '        """Запоминает элементы вектора."""\n'
    "        self._elements = list(elements)\n\n"
    "    def __len__(self) -> int:\n"
    '        """Число элементов."""\n'
    "        return len(self._elements)\n\n"
    "    def __getitem__(self, index: int) -> int:\n"
    '        """Элемент по индексу."""\n'
    "        return self._elements[index]\n\n"
    "    def append(self, element: int) -> None:\n"
    '        """Дописывает элемент в конец."""\n'
    "        self._elements.append(element)\n\n"
    "    def extend(self, elements: list[int]) -> None:\n"
    '        """Дописывает несколько элементов."""\n'
    "        self._elements.extend(elements)\n\n"
    "    def __iter__(self):\n"
    '        """Идёт по элементам по порядку."""\n'
    "        return iter(self._elements)\n\n"
    "    def __str__(self) -> str:\n"
    '        """Читаемое представление."""\n'
    '        return f"Vector({self._elements})"\n\n'
    "    def __repr__(self) -> str:\n"
    '        """Представление для отладки."""\n'
    '        return f"Vector({self._elements!r})"\n\n'
    '    def __add__(self, other: "Vector") -> "Vector":\n'
    '        """Поэлементная сумма."""\n'
    "        return Vector([left + right for left, right in zip(self._elements, other._elements)])\n\n"
    '    def __sub__(self, other: "Vector") -> "Vector":\n'
    '        """Поэлементная разность."""\n'
    "        return Vector([left - right for left, right in zip(self._elements, other._elements)])\n\n"
    '    def __mul__(self, scalar: int) -> "Vector":\n'
    '        """Повторение списка вместо умножения каждого элемента."""\n'
    "        return Vector(self._elements * scalar)\n\n"
    '    def __rmul__(self, scalar: int) -> "Vector":\n'
    '        """Умножение скаляра на вектор."""\n'
    "        return self * scalar\n\n"
    '    def dot(self, other: "Vector") -> int:\n'
    '        """Скалярное произведение."""\n'
    "        return sum(left * right for left, right in zip(self._elements, other._elements))\n\n\n"
    "one = Vector([1, 2, 3])\n"
    "two = Vector([4, 5, 6])\n\n"
    "result = (list(one + two), list(two - one), list(one * 2), list(3 * two), one.dot(two))",
    1077: "class VectorIterator:\n"
    '    """__iter__ отдаёт новый итератор: обход начинается заново."""\n\n'
    "    def __init__(self, elements: list[int]) -> None:\n"
    '        """Запоминает элементы и начинает с нулевой позиции."""\n'
    "        self._elements = elements\n"
    "        self._index = 0\n\n"
    "    def __iter__(self):\n"
    '        """Новый обход вместо самого себя."""\n'
    "        return VectorIterator(self._elements)\n\n"
    "    def __next__(self) -> int:\n"
    '        """Следующий элемент или StopIteration."""\n'
    "        if self._index >= len(self._elements):\n"
    "            raise StopIteration\n"
    "        value = self._elements[self._index]\n"
    "        self._index += 1\n"
    "        return value\n\n\n"
    "class Vector:\n"
    '    """Контейнер, по которому можно пройти несколько раз."""\n\n'
    "    def __init__(self, elements: list[int]) -> None:\n"
    "        self._elements = list(elements)\n\n"
    "    def __iter__(self) -> VectorIterator:\n"
    "        return VectorIterator(self._elements)\n\n\n"
    "vector = Vector([1, 2, 3])\n"
    "first = iter(vector)\n"
    "second = iter(vector)\n\n"
    "result = (next(first), next(first), next(second), list(vector))",
    1079: "from dataclasses import dataclass, field\n\n\n"
    "@dataclass(order=True)\n"
    "class Item:\n"
    '    """Товар без frozen: поля меняются уже после создания."""\n\n'
    "    title: str\n"
    "    cost: int\n"
    "    item_id: int = field(compare=False, default=0)\n\n"
    "    def __post_init__(self) -> None:\n"
    '        """Проверяет инварианты товара."""\n'
    '        assert self.title, "У товара должно быть название."\n'
    '        assert self.cost > 0, "Цена товара должна быть положительной."\n\n\n'
    "class Vector:\n"
    '    """Контейнер чисел одного типа."""\n\n'
    "    def __init__(self, elements: list[int]) -> None:\n"
    '        """Запоминает элементы."""\n'
    "        self._elements = list(elements)\n\n"
    "    def __len__(self) -> int:\n"
    '        """Число элементов."""\n'
    "        return len(self._elements)\n\n"
    "    def __getitem__(self, index: int) -> int:\n"
    '        """Элемент по индексу."""\n'
    "        return self._elements[index]\n\n"
    "    def __iter__(self):\n"
    '        """Идёт по элементам по порядку."""\n'
    "        return iter(self._elements)\n\n"
    '    def __add__(self, other: "Vector") -> "Vector":\n'
    '        """Поэлементная сумма."""\n'
    "        return Vector([left + right for left, right in zip(self._elements, other._elements)])\n\n"
    '    def __mul__(self, scalar: int) -> "Vector":\n'
    '        """Умножение на скаляр."""\n'
    "        return Vector([element * scalar for element in self._elements])\n\n"
    '    def dot(self, other: "Vector") -> int:\n'
    '        """Скалярное произведение."""\n'
    "        return sum(left * right for left, right in zip(self._elements, other._elements))\n\n\n"
    "items = [\n"
    '    Item("Thing", 44, 9),\n'
    '    Item("Note", 8, 0),\n'
    '    Item("Unity", 5, 8),\n'
    "]\n\n"
    "result = (\n"
    "    sorted(items)[0].item_id,\n"
    "    list(Vector([1, 2, 3]) + Vector([4, 5, 6])),\n"
    "    Vector([1, 2, 3]).dot(Vector([4, 5, 6])),\n"
    ")",
    1081: "import time\n\n\n"
    "class Timer:\n"
    '    """__exit__ возвращает True и глушит исключение блока."""\n\n'
    "    def __init__(self) -> None:\n"
    '        """Готовит пустой замер."""\n'
    "        self.elapsed = 0.0\n\n"
    '    def __enter__(self) -> "Timer":\n'
    '        """Запускает отсчёт и отдаёт менеджер в `as`."""\n'
    "        self._start = time.perf_counter()\n"
    "        return self\n\n"
    "    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:\n"
    '        """Останавливает отсчёт и подавляет исключение блока."""\n'
    "        self.elapsed = time.perf_counter() - self._start\n"
    "        return True\n\n\n"
    "with Timer() as timer:\n"
    "    for _ in range(1000):\n"
    "        pass\n\n"
    "result = (isinstance(timer, Timer), timer.elapsed >= 0.0)",
    1083: "class FileManager:\n"
    '    """Открывает файл, но не закрывает: надежда на сборщик мусора."""\n\n'
    '    def __init__(self, filename: str, mode: str = "r") -> None:\n'
    '        """Запоминает имя файла и режим."""\n'
    "        self.filename = filename\n"
    "        self.mode = mode\n"
    "        self.file = None\n\n"
    "    def __enter__(self):\n"
    '        """Открывает файл и отдаёт его в `as`."""\n'
    "        self.file = open(self.filename, self.mode)\n"
    "        return self.file\n\n"
    "    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:\n"
    '        """Ничего не делает: файл остаётся открытым."""\n'
    "        return False\n\n\n"
    'with FileManager("isit_demo.txt", "w") as stream:\n'
    '    stream.write("строка\\n")\n\n'
    'with FileManager("isit_demo.txt") as reader:\n'
    "    content = reader.read()\n\n"
    "result = (stream.closed, content)",
    1085: "import functools\n\n\n"
    "def traced(function):\n"
    '    """Обёртка без functools.wraps: имя и докстринг теряются."""\n\n'
    "    def wrapper(*args, **kwargs):\n"
    '        print(f"вызов: {function.__name__}")\n'
    "        return function(*args, **kwargs)\n\n"
    "    return wrapper\n\n\n"
    "@traced\n"
    "def add(a: int, b: int = 1) -> int:\n"
    '    """Складывает два числа."""\n'
    "    return a + b\n\n\n"
    "result = (add(2, 3), add(2, b=5), add.__name__, add.__doc__)",
    1087: "import functools\n"
    "import inspect\n"
    "from collections import OrderedDict\n\n\n"
    "def cache(max_size: int):\n"
    '    """Хранит результат для `max_size` самых свежих наборов аргументов."""\n\n'
    "    def decorator(function):\n"
    "        cached: OrderedDict[tuple, object] = OrderedDict()\n"
    "        signature = inspect.signature(function)\n\n"
    "        @functools.wraps(function)\n"
    "        def wrapper(*args, **kwargs):\n"
    "            bound = signature.bind(*args, **kwargs)\n"
    "            bound.apply_defaults()\n"
    "            key = tuple(bound.arguments.items())\n"
    "            if key in cached:\n"
    "                return cached[key]\n"
    "            value = function(*args, **kwargs)\n"
    "            if max_size > 0:\n"
    "                cached[key] = value\n"
    "                if len(cached) > max_size:\n"
    "                    cached.popitem(last=False)\n"
    "            return value\n\n"
    "        return wrapper\n\n"
    "    return decorator\n\n\n"
    "calls: list[int] = []\n\n\n"
    "@cache(2)\n"
    "def identity(value: int) -> int:\n"
    '    """Считает настоящие вычисления."""\n'
    "    calls.append(value)\n"
    "    return value\n\n\n"
    "first = identity(1)\n"
    "second = identity(2)\n"
    "again = identity(1)\n"
    "third = identity(3)\n"
    "evicted = identity(2)\n\n"
    "result = (first, second, again, third, evicted, len(calls))",
    1089: "import functools\n"
    "import io\n"
    "import sys\n\n\n"
    "class OutputCapture:\n"
    '    """Потоки не возвращаются на место: захват остаётся навсегда."""\n\n'
    "    def __init__(self) -> None:\n"
    '        """Готовит пустые места под перехваченный вывод."""\n'
    '        self.stdout = ""\n'
    '        self.stderr = ""\n\n'
    '    def __enter__(self) -> "OutputCapture":\n'
    '        """Подменяет потоки и отдаёт менеджер в `as`."""\n'
    "        sys.stdout = io.StringIO()\n"
    "        sys.stderr = io.StringIO()\n"
    "        return self\n\n"
    "    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:\n"
    '        """Забирает перехваченное и уходит, не вернув потоки."""\n'
    "        self.stdout = sys.stdout.getvalue()\n"
    "        self.stderr = sys.stderr.getvalue()\n"
    "        return False\n\n\n"
    "def count_calls(function):\n"
    '    """Считает вызовы функции в атрибуте `calls`."""\n\n'
    "    @functools.wraps(function)\n"
    "    def wrapper(*args, **kwargs):\n"
    "        wrapper.calls += 1\n"
    "        return function(*args, **kwargs)\n\n"
    "    wrapper.calls = 0\n"
    "    return wrapper\n\n\n"
    "original = sys.stdout\n"
    "with OutputCapture() as captured:\n"
    '    print("отчёт")\n'
    '    print("ошибка", file=sys.stderr)\n\n\n'
    "@count_calls\n"
    "def doubled(value: int) -> int:\n"
    '    """Удваивает значение."""\n'
    "    return value * 2\n\n\n"
    "doubled(1)\n"
    "doubled(2)\n\n"
    "sys.stdout = original\n"
    "result = (captured.stdout, captured.stderr, sys.stdout is original, doubled.calls)",
}


MORE_WRONG = [
    (
        1001,
        'result = "Hello world!"\n\n\ndef get_hello_world() -> str:\n'
        '    """Возвращает приветствие."""\n'
        '    return "Hello world"\n',
    ),
    # Докстринг из заготовки — часть договора, его отсутствие ловит проверка.
    (
        1003,
        "def sum_two_numbers(a: int, b: int) -> int:\n    return a + b\n\n\n"
        "result = sum_two_numbers(10, 5)",
    ),
    (
        1005,
        'def greet(name: str) -> str:\n    return f"Привет, {name}!"\n\n\n'
        "def add(a: int, b: int) -> int:\n    return a + b\n\n\n"
        'result = greet("Аня")',
    ),
    (
        1011,
        "def get_middle_value(a: int, b: int, c: int) -> int:\n"
        "    return (a + b + c) // 3\n\n\n"
        "result = get_middle_value(100, -10, -100)",
    ),
    (
        1013,
        "def get_fizz_buzz(n: int) -> list[int | str]:\n"
        "    values: list[int | str] = []\n"
        "    for value in range(n):\n"
        "        if value % 15 == 0:\n"
        '            values.append("FizzBuzz")\n'
        "        elif value % 3 == 0:\n"
        '            values.append("Fizz")\n'
        "        elif value % 5 == 0:\n"
        '            values.append("Buzz")\n'
        "        else:\n            values.append(value)\n"
        "    return values\n\n\n"
        "result = get_fizz_buzz(15)",
    ),
    (
        1015,
        "def get_middle_value(a: int, b: int, c: int) -> int:\n"
        "    return a + b + c - min(a, b, c) - max(a, b, c)\n\n\n"
        "def get_fizz_buzz(n: int) -> list[int | str]:\n"
        "    values: list[int | str] = []\n"
        "    for value in range(1, n + 1):\n"
        "        if value % 15 == 0:\n"
        '            values.append("FizzBuzz")\n'
        "        elif value % 3 == 0:\n"
        '            values.append("Fizz")\n'
        "        elif value % 5 == 0:\n"
        '            values.append("Buzz")\n'
        "        else:\n            values.append(value)\n"
        "    return values\n\n\n"
        "result = (get_middle_value(3, 1, 2), get_fizz_buzz(3))",
    ),
    (
        1021,
        "def check_ctr(clicks: int, shows: int, expected: float) -> None:\n"
        "    if ctr(clicks, shows) != expected:\n"
        '        raise AssertionError("wrong ctr calculation")\n\n\n'
        "try:\n    check_ctr(1, 2, 0.5)\n    result = \"ошибки не было\"\n"
        "except AssertionError as exc:\n    result = str(exc)",
    ),
    (
        1023,
        "def safe_ctr(clicks: int, shows: int, log: list[str]) -> float:\n"
        "    try:\n"
        '        assert clicks >= 0, "Clicks must be non-negative"\n'
        "        value = float(ctr(clicks, shows))\n"
        "    except ValueError:\n"
        '        log.append("done")\n        return -1.0\n'
        "    except ZeroDivisionError:\n"
        '        log.append("done")\n        return 0.0\n'
        '    log.append("done")\n'
        "    return value\n\n\n"
        "log: list[str] = []\n"
        "result = [safe_ctr(2, 2, log), safe_ctr(5, -1, log), safe_ctr(10, 0, log)]",
    ),
    (
        1025,
        "def safe_divide(a: float, b: float, log: list[str]) -> float:\n"
        "    try:\n"
        '        assert a >= 0, "a must be positive"\n'
        "        if b == 0:\n"
        '            log.append("zero")\n'
        "            return 0.0\n"
        '        log.append("ok")\n'
        "        return a / b\n"
        "    finally:\n        log.append(\"done\")\n\n\n"
        "log: list[str] = []\n"
        "result = (safe_divide(10, 2, log), safe_divide(1, 0, log), log)",
    ),
    # Деление на ноль в журнал записано, но вместо 0.0 вернулся None.
    (
        1025,
        "def safe_divide(a: float, b: float, log: list[str]) -> float:\n"
        "    try:\n"
        '        assert a >= 0, "a must be non-negative"\n'
        "        if b == 0:\n"
        '            log.append("zero")\n'
        "            return None\n"
        '        log.append("ok")\n'
        "        return a / b\n"
        "    finally:\n        log.append(\"done\")\n\n\n"
        "log: list[str] = []\n"
        "result = (safe_divide(10, 2, log), safe_divide(1, 0, log), log)",
    ),
    # Второй аргумент совпадает с медианой во всех прежних наборах — поэтому у
    # задания есть случаи, где середина не второй аргумент.
    (
        1011,
        "def get_middle_value(a: int, b: int, c: int) -> int:\n"
        "    return b\n\n\n"
        "result = get_middle_value(100, -10, -100)",
    ),
    # Длина зашита числом 15: проверка обязана спросить задание и про другие n.
    (
        1013,
        "def get_fizz_buzz(n: int) -> list[int | str]:\n"
        "    values: list[int | str] = []\n"
        "    for value in range(1, 16):\n"
        "        if value % 15 == 0:\n"
        '            values.append("FizzBuzz")\n'
        "        elif value % 3 == 0:\n"
        '            values.append("Fizz")\n'
        "        elif value % 5 == 0:\n"
        '            values.append("Buzz")\n'
        "        else:\n            values.append(value)\n"
        "    return values\n\n\n"
        "result = get_fizz_buzz(15)",
    ),
    # 1031: индексы вместо значений
    (
        1031,
        "def get_every_second_element(elements: list[int]) -> list[int]:\n"
        '    """Возвращаем индексы, а не элементы."""\n'
        "    return list(range(1, len(elements), 2))\n\n\n"
        "result = get_every_second_element([-2, 0, 5, 2, 3, 4, 3])",
    ),
    # 1031: порча входа
    (
        1031,
        "def get_every_second_element(elements: list[int]) -> list[int]:\n"
        '    """Портит входной список."""\n'
        "    second = elements[1::2]\n"
        "    elements.clear()\n"
        "    return second\n\n\n"
        "result = get_every_second_element([-2, 0, 5, 2, 3, 4, 3])",
    ),
    # 1033: перепутанный порядок
    (
        1033,
        "def get_min_max(elements: list[int], default: int | None) -> tuple[int | None, int | None]:\n"
        '    """Максимум и минимум — в обратном порядке."""\n'
        "    if not elements:\n        return default, default\n"
        "    return max(elements), min(elements)\n\n\n"
        "result = get_min_max([3, 0, 5, 2, 1, 4, 6], 0)",
    ),
    # 1033: default вместо ответа
    (
        1033,
        "def get_min_max(elements: list[int], default: int | None) -> tuple[int | None, int | None]:\n"
        '    """Всегда возвращает default."""\n'
        "    return default, default\n\n\n"
        "result = get_min_max([3, 0, 5, 2, 1, 4, 6], 0)",
    ),
    # 1035: -1 как признак «не нашли»
    (
        1035,
        "def get_last_three_index(elements: list[int]) -> int | None:\n"
        '    """-1 вместо None."""\n'
        "    if 3 not in elements:\n        return -1\n"
        "    return len(elements) - 1 - elements[::-1].index(3)\n\n\n"
        "result = get_last_three_index([-2, 0, 5, 2, 3, 4, 3])",
    ),
    # 1035: индекс из развёрнутого списка, без пересчёта
    (
        1035,
        "def get_last_three_index(elements: list[int]) -> int | None:\n"
        '    """Индекс в развёрнутом списке выдаётся как есть."""\n'
        "    if 3 not in elements:\n        return None\n"
        "    return elements[::-1].index(3)\n\n\n"
        "result = get_last_three_index([-2, 0, 5, 2, 3, 4, 3])",
    ),
    # 1037: квадраты отсортированы
    (
        1037,
        "def get_squares(elements: list[int]) -> list[int]:\n"
        '    """Квадраты отсортированы по возрастанию."""\n'
        "    return sorted(value**2 for value in elements)\n\n\n"
        "def get_max_element_index(elements: list[int]) -> int | None:\n"
        '    """Пустой список — None."""\n'
        "    if not elements:\n        return None\n"
        "    return max(range(len(elements)), key=lambda index: elements[index])\n\n\n"
        "def get_by_index(elements: list[int], index: int, boundary: int) -> int | None:\n"
        '    """Элемент по индексу."""\n'
        "    return value if (value := elements[index]) > boundary else None\n\n\n"
        "result = (\n"
        "    get_squares([-2, 0, 5]),\n"
        "    get_max_element_index([-2, 0, 5, 2, 3, 4, 3]),\n"
        "    get_by_index([-2, 0, 5, 2, 3, 4, 3], 5, 3),\n"
        ")",
    ),
    # 1037: значение максимума вместо индекса и нестрогое сравнение с границей
    (
        1037,
        "def get_squares(elements: list[int]) -> list[int]:\n"
        '    """Квадраты."""\n'
        "    return [value**2 for value in elements]\n\n\n"
        "def get_max_element_index(elements: list[int]) -> int | None:\n"
        '    """Отдаёт само максимальное значение."""\n'
        "    if not elements:\n        return None\n"
        "    return max(elements)\n\n\n"
        "def get_by_index(elements: list[int], index: int, boundary: int) -> int | None:\n"
        '    """Строгое сравнение забыто."""\n'
        "    return value if (value := elements[index]) >= boundary else None\n\n\n"
        "result = (\n"
        "    get_squares([-2, 0, 5]),\n"
        "    get_max_element_index([-2, 0, 5, 2, 3, 4, 3]),\n"
        "    get_by_index([-2, 0, 5, 2, 3, 4, 3], 5, 3),\n"
        ")",
    ),
    # 1041: порядок лога сломан сортировкой
    (
        1041,
        "def get_events_by_device_type(records: list[dict], device_type: str) -> list[dict]:\n"
        '    """Результат отсортирован — порядок лога потерян."""\n'
        '    filtered = [r for r in records if r["DeviceType"] == device_type]\n'
        '    return sorted(filtered, key=lambda r: r["EventID"], reverse=True)\n\n\n'
        'result = get_events_by_device_type(records, "Safari")',
    ),
    # 1041: множество вместо списка записей
    (
        1041,
        "def get_events_by_device_type(records: list[dict], device_type: str) -> list[dict]:\n"
        '    """В множество словари не положить — соберём идентификаторы."""\n'
        '    return {r["EventID"] for r in records if r["DeviceType"] == device_type}\n\n\n'
        'result = get_events_by_device_type(records, "Safari")',
    ),
    # 1043: порядок ключей берётся из набора, а не из записи
    (
        1043,
        "def get_record_with_none_if_key_not_in_keys(record: dict, keys: set[str]) -> dict:\n"
        '    """Замена на None, но обход идёт по набору ключей."""\n'
        "    return {key: record.get(key) for key in keys}\n\n\n"
        'result = get_record_with_none_if_key_not_in_keys(record, {"EventID", "UserID"})',
    ),
    # 1045: проверка значения через истинность — теряет регион 0
    (
        1045,
        "def get_unique_page_ids(records: list[dict]) -> set[int]:\n"
        '    """Уникальные страницы."""\n'
        '    return {r["PageID"] for r in records}\n\n\n'
        "def get_region_id_if_not_none(records: list[dict]) -> list[int]:\n"
        '    """Проверка через истинность — теряет регион 0."""\n'
        '    return [r["RegionID"] for r in records if r["RegionID"]]\n\n\n'
        "def get_keys_where_value_is_not_none(record: dict) -> list[str]:\n"
        '    """Проверка через истинность."""\n'
        "    return [key for key, value in record.items() if value]\n\n\n"
        "result = (\n"
        "    sorted(get_unique_page_ids(records)),\n"
        "    get_region_id_if_not_none(records),\n"
        "    get_keys_where_value_is_not_none(records[0]),\n"
        ")",
    ),
    # 1045: список вместо множества — повторы остаются
    (
        1045,
        "def get_unique_page_ids(records: list[dict]) -> set[int]:\n"
        '    """Список вместо множества."""\n'
        '    return [r["PageID"] for r in records]\n\n\n'
        "def get_region_id_if_not_none(records: list[dict]) -> list[int]:\n"
        '    """Регионы."""\n'
        '    return [r["RegionID"] for r in records if r["RegionID"] is not None]\n\n\n'
        "def get_keys_where_value_is_not_none(record: dict) -> list[str]:\n"
        '    """Ключи."""\n'
        "    return [key for key, value in record.items() if value is not None]\n\n\n"
        "result = (\n"
        "    sorted(get_unique_page_ids(records)),\n"
        "    get_region_id_if_not_none(records),\n"
        "    get_keys_where_value_is_not_none(records[0]),\n"
        ")",
    ),
    # 1051: точка ставится всегда — верхний ключ получает ведущую точку
    (
        1051,
        "def traverse_dictionary_immutable(dct: dict, prefix: str = '') -> list[tuple[str, int]]:\n"
        '    """Точка ставится всегда."""\n'
        "    result = []\n"
        "    for key, value in dct.items():\n"
        '        path = f"{prefix}.{key}"\n'
        "        if isinstance(value, dict):\n"
        "            result.extend(traverse_dictionary_immutable(value, path))\n"
        "        else:\n"
        "            result.append((path, value))\n"
        "    return result\n\n\n"
        'result = traverse_dictionary_immutable({"a": 1, "b": {"c": 2, "d": {"e": 4}}})',
    ),
    # 1053: дети кладутся в стек без накопленного пути
    (
        1053,
        "def traverse_dictionary_iterative(dct: dict) -> list[tuple[str, int]]:\n"
        '    """Дети кладутся без накопленного пути."""\n'
        "    result = []\n"
        "    stack = [(dct, '')]\n"
        "    while stack:\n"
        "        current, prefix = stack.pop()\n"
        "        for key, value in current.items():\n"
        "            if isinstance(value, dict):\n"
        "                stack.append((value, key))\n"
        "            else:\n"
        "                result.append((key, value))\n"
        "    return result\n\n\n"
        'result = sorted(traverse_dictionary_iterative({"a": 1, "b": {"c": 2, "d": {"e": 4}}}))',
    ),
    # 1055: мутирующая версия ещё и возвращает список — два источника правды
    (
        1055,
        "def traverse_dictionary_mutable(\n"
        "    dct: dict, result: list[tuple[str, int]], prefix: str = ''\n"
        "):\n"
        '    """Возвращает result — два источника правды."""\n'
        "    for key, value in dct.items():\n"
        '        path = f"{prefix}.{key}" if prefix else key\n'
        "        if isinstance(value, dict):\n"
        "            traverse_dictionary_mutable(value, result, path)\n"
        "        else:\n"
        "            result.append((path, value))\n"
        "    return result\n\n\n"
        "def traverse_dictionary_iterative(dct: dict) -> list[tuple[str, int]]:\n"
        '    """Обход стеком."""\n'
        "    result = []\n"
        "    stack = [(dct, '')]\n"
        "    while stack:\n"
        "        current, prefix = stack.pop()\n"
        "        for key, value in current.items():\n"
        '            path = f"{prefix}.{key}" if prefix else key\n'
        "            if isinstance(value, dict):\n"
        "                stack.append((value, path))\n"
        "            else:\n"
        "                result.append((path, value))\n"
        "    return result\n\n\n"
        'dct = {"a": 1, "b": {"c": 2, "d": {"e": 4}}}\n'
        "collected = []\n"
        "traverse_dictionary_mutable(dct, collected)\n"
        "result = (sorted(collected), sorted(traverse_dictionary_iterative(dct)))",
    ),
    # Регрессии ревью среза 3: решения, которые проходили проверки, пока в
    # данных не было различающего случая.
    # 1031: «каждые вторые» отсортированы — на исходном входе порядок совпадал.
    (
        1031,
        "def get_every_second_element(elements: list[int]) -> list[int]:\n"
        '    """Результат отсортирован."""\n'
        "    return sorted(elements[1::2])\n\n\n"
        "result = get_every_second_element([-2, 0, 5, 2, 3, 4, 3])",
    ),
    # 1037: индекс последнего максимума вместо первого.
    (
        1037,
        "def get_squares(elements: list[int]) -> list[int]:\n"
        '    """Квадраты."""\n'
        "    return [value**2 for value in elements]\n\n\n"
        "def get_max_element_index(elements: list[int]) -> int | None:\n"
        '    """Индекс последнего максимума."""\n'
        "    if not elements:\n        return None\n"
        "    return len(elements) - 1 - elements[::-1].index(max(elements))\n\n\n"
        "def get_by_index(elements: list[int], index: int, boundary: int) -> int | None:\n"
        '    """Элемент по индексу."""\n'
        "    value = elements[index]\n"
        "    return value if value > boundary else None\n\n\n"
        "result = (\n"
        "    get_squares([-2, 0, 5]),\n"
        "    get_max_element_index([-2, 0, 5, 2, 3, 4, 3]),\n"
        "    get_by_index([-2, 0, 5, 2, 3, 4, 3], 5, 3),\n"
        ")",
    ),
    # 1041: результат отсортирован по EventID — порядок лога потерян.
    (
        1041,
        "def get_events_by_device_type(records: list[dict], device_type: str) -> list[dict]:\n"
        '    """Отсортировано по EventID."""\n'
        '    filtered = [r for r in records if r["DeviceType"] == device_type]\n'
        '    return sorted(filtered, key=lambda r: r["EventID"])\n\n\n'
        'result = get_events_by_device_type(records, "Safari")',
    ),
    # 1041: устройство ищется по вхождению подстроки — Safari Mobile попадает в Safari.
    (
        1041,
        "def get_events_by_device_type(records: list[dict], device_type: str) -> list[dict]:\n"
        '    """Устройство по вхождению подстроки."""\n'
        '    return [r for r in records if device_type in r["DeviceType"]]\n\n\n'
        'result = get_events_by_device_type(records, "Safari")',
    ),
    # 1051: пути верные, но результат отсортирован — порядок словаря потерян.
    (
        1051,
        "def traverse_dictionary_immutable(dct: dict, prefix: str = '') -> list[tuple[str, int]]:\n"
        '    """Пути верные, но результат отсортирован."""\n'
        "    result = []\n"
        "    for key, value in dct.items():\n"
        '        path = f"{prefix}.{key}" if prefix else key\n'
        "        if isinstance(value, dict):\n"
        "            result.extend(traverse_dictionary_immutable(value, path))\n"
        "        else:\n"
        "            result.append((path, value))\n"
        "    return sorted(result)\n\n\n"
        'result = traverse_dictionary_immutable({"a": 1, "b": {"c": 2, "d": {"e": 4}}})',
    ),
    # 1061: `groupby` убирает только соседние повторы — для «без повторов» этого мало.
    (
        1061,
        "import itertools\n"
        "from collections.abc import Iterator\n\n\n"
        "def uniq(sequence: list[int]) -> Iterator[int]:\n"
        '    """Убирает только соседние повторы."""\n'
        "    for key, _ in itertools.groupby(sequence):\n"
        "        yield key\n\n\n"
        "result = (list(uniq([1, 2, 3, 3, 1, 7])), list(uniq([])))",
    ),
    # 1063: `zip(*matrix)` даёт кортежи — задание требует строки-списки.
    (
        1063,
        "def transpose(matrix: list[list[int]]) -> list[list[int]]:\n"
        '    """Столбцы собираются в кортежи, а не в списки."""\n'
        "    return list(zip(*matrix))\n\n\n"
        "result = (transpose([[1, 2], [3, 4], [5, 6]]), transpose([]))",
    ),
    # 1065: объединение словарей «на месте» меняет первый аргумент вызывающего.
    (
        1065,
        "from collections.abc import Iterator\n\n\n"
        "def uniq(sequence: list[int]) -> Iterator[int]:\n"
        '    """Элементы без повторов, в порядке входа."""\n'
        "    seen: set[int] = set()\n"
        "    for value in sequence:\n"
        "        if value not in seen:\n"
        "            seen.add(value)\n"
        "            yield value\n\n\n"
        "def dict_merge(*dicts: dict[int, int]) -> dict[int, int]:\n"
        '    """Объединение плоских словарей дописыванием в первый аргумент."""\n'
        "    merged: dict[int, int] = dicts[0] if dicts else {}\n"
        "    for mapping in dicts[1:]:\n"
        "        merged.update(mapping)\n"
        "    return merged\n\n\n"
        "def product(lhs: list[int], rhs: list[int]) -> int:\n"
        '    """Скалярное произведение."""\n'
        "    return sum(left * right for left, right in zip(lhs, rhs))\n\n\n"
        "result = (\n"
        "    list(uniq([2, 1, 2, 3])),\n"
        "    dict_merge({1: 2}, {2: 2}, {1: 1}),\n"
        "    product([1, 2, 3], [4, 5, 6]),\n"
        ")",
    ),
    # 1071: `cost >= 0` пропускает бесплатный товар — граница инварианта разъехалась.
    (
        1071,
        "from dataclasses import dataclass\n\n\n"
        "@dataclass(frozen=True)\n"
        "class Item:\n"
        '    """Товар: цена проверяется нестрого."""\n\n'
        "    title: str\n"
        "    cost: int\n\n"
        "    def __post_init__(self) -> None:\n"
        '        """Проверяет инварианты товара."""\n'
        '        assert self.title, "У товара должно быть название."\n'
        '        assert self.cost >= 0, "Цена товара должна быть положительной."\n\n\n'
        'result = (Item("Ложка", 25) == Item("Ложка", 25), Item("Ложка", 25).cost)',
    ),
    # 1073: `compare=False` не на том поле — цена выпала из сравнения.
    (
        1073,
        "from dataclasses import dataclass, field\n\n\n"
        "@dataclass(order=True)\n"
        "class Item:\n"
        '    """Сравнение по названию и item_id: цена забыта."""\n\n'
        "    title: str\n"
        "    cost: int = field(compare=False)\n"
        "    item_id: int = 0\n\n\n"
        "items = [\n"
        '    Item("Thing", 44, 9),\n'
        '    Item("Note", 8, 0),\n'
        '    Item("Things", 100, 1),\n'
        '    Item("Unity", 5, 8),\n'
        '    Item("Things", 44, 11),\n'
        '    Item("Note", 64, 15),\n'
        "]\n\n"
        "result = [item.item_id for item in sorted(items)]",
    ),
    # 1075: `__add__` меняет левый операнд — вектор перестаёт быть значением.
    (
        1075,
        "class Vector:\n"
        '    """Контейнер чисел одного типа."""\n\n'
        "    def __init__(self, elements: list[int]) -> None:\n"
        '        """Запоминает элементы вектора."""\n'
        "        self._elements = list(elements)\n\n"
        "    def __len__(self) -> int:\n"
        '        """Число элементов."""\n'
        "        return len(self._elements)\n\n"
        "    def __getitem__(self, index: int) -> int:\n"
        '        """Элемент по индексу."""\n'
        "        return self._elements[index]\n\n"
        "    def __iter__(self):\n"
        '        """Идёт по элементам по порядку."""\n'
        "        return iter(self._elements)\n\n"
        "    def __str__(self) -> str:\n"
        '        """Читаемое представление."""\n'
        '        return f"Vector({self._elements})"\n\n'
        "    def __repr__(self) -> str:\n"
        '        """Представление для отладки."""\n'
        '        return f"Vector({self._elements!r})"\n\n'
        '    def __add__(self, other: "Vector") -> "Vector":\n'
        '        """Сумма дописывается в левый операнд."""\n'
        "        for index, value in enumerate(other._elements):\n"
        "            self._elements[index] += value\n"
        "        return self\n\n"
        '    def __sub__(self, other: "Vector") -> "Vector":\n'
        '        """Поэлементная разность."""\n'
        "        return Vector([left - right for left, right in zip(self._elements, other._elements)])\n\n"
        '    def __mul__(self, scalar: int) -> "Vector":\n'
        '        """Умножение на скаляр."""\n'
        "        return Vector([element * scalar for element in self._elements])\n\n"
        '    def __rmul__(self, scalar: int) -> "Vector":\n'
        '        """Умножение скаляра на вектор."""\n'
        "        return self * scalar\n\n"
        '    def dot(self, other: "Vector") -> int:\n'
        '        """Скалярное произведение."""\n'
        "        return sum(left * right for left, right in zip(self._elements, other._elements))\n\n\n"
        "one = Vector([1, 2, 3])\n"
        "two = Vector([4, 5, 6])\n\n"
        "result = (list(one + two), list(two - one), list(one * 2), list(3 * two), one.dot(two))",
    ),
    # 1077: вектор кэширует один итератор — контейнер становится одноразовым.
    (
        1077,
        "class VectorIterator:\n"
        '    """Итератор вектора: хранит данные и текущую позицию."""\n\n'
        "    def __init__(self, elements: list[int]) -> None:\n"
        '        """Запоминает элементы и начинает с нулевой позиции."""\n'
        "        self._elements = elements\n"
        "        self._index = 0\n\n"
        '    def __iter__(self) -> "VectorIterator":\n'
        '        """Итератор возвращает сам себя."""\n'
        "        return self\n\n"
        "    def __next__(self) -> int:\n"
        '        """Следующий элемент или StopIteration."""\n'
        "        if self._index >= len(self._elements):\n"
        "            raise StopIteration\n"
        "        value = self._elements[self._index]\n"
        "        self._index += 1\n"
        "        return value\n\n\n"
        "class Vector:\n"
        '    """Контейнер, отдающий один и тот же итератор."""\n\n'
        "    def __init__(self, elements: list[int]) -> None:\n"
        "        self._elements = list(elements)\n"
        "        self._iterator = VectorIterator(self._elements)\n\n"
        "    def __iter__(self) -> VectorIterator:\n"
        "        return self._iterator\n\n\n"
        "vector = Vector([1, 2, 3])\n"
        "first = iter(vector)\n"
        "second = iter(vector)\n\n"
        "result = (next(first), next(first), next(second), list(vector))",
    ),
    # 1079: `__add__` склеивает векторы вместо поэлементной суммы.
    (
        1079,
        "from dataclasses import dataclass, field\n\n\n"
        "@dataclass(frozen=True, order=True)\n"
        "class Item:\n"
        '    """Товар: неизменяемый, сравнивается по названию и цене."""\n\n'
        "    title: str\n"
        "    cost: int\n"
        "    item_id: int = field(compare=False, default=0)\n\n"
        "    def __post_init__(self) -> None:\n"
        '        """Проверяет инварианты товара."""\n'
        '        assert self.title, "У товара должно быть название."\n'
        '        assert self.cost > 0, "Цена товара должна быть положительной."\n\n\n'
        "class Vector:\n"
        '    """Контейнер чисел одного типа."""\n\n'
        "    def __init__(self, elements: list[int]) -> None:\n"
        '        """Запоминает элементы."""\n'
        "        self._elements = list(elements)\n\n"
        "    def __len__(self) -> int:\n"
        '        """Число элементов."""\n'
        "        return len(self._elements)\n\n"
        "    def __getitem__(self, index: int) -> int:\n"
        '        """Элемент по индексу."""\n'
        "        return self._elements[index]\n\n"
        "    def __iter__(self):\n"
        '        """Идёт по элементам по порядку."""\n'
        "        return iter(self._elements)\n\n"
        '    def __add__(self, other: "Vector") -> "Vector":\n'
        '        """Склейка векторов вместо поэлементной суммы."""\n'
        "        return Vector(self._elements + other._elements)\n\n"
        '    def __mul__(self, scalar: int) -> "Vector":\n'
        '        """Умножение на скаляр."""\n'
        "        return Vector([element * scalar for element in self._elements])\n\n"
        '    def dot(self, other: "Vector") -> int:\n'
        '        """Скалярное произведение."""\n'
        "        return sum(left * right for left, right in zip(self._elements, other._elements))\n\n\n"
        "items = [\n"
        '    Item("Thing", 44, 9),\n'
        '    Item("Note", 8, 0),\n'
        '    Item("Unity", 5, 8),\n'
        "]\n\n"
        "result = (\n"
        "    sorted(items)[0].item_id,\n"
        "    list(Vector([1, 2, 3]) + Vector([4, 5, 6])),\n"
        "    Vector([1, 2, 3]).dot(Vector([4, 5, 6])),\n"
        ")",
    ),
    # 1081: в `elapsed` легла отметка времени, а не длительность блока.
    (
        1081,
        "import time\n\n\n"
        "class Timer:\n"
        '    """В elapsed попадает абсолютное время, а не длительность."""\n\n'
        "    def __init__(self) -> None:\n"
        '        """Готовит пустой замер."""\n'
        "        self.elapsed = 0.0\n\n"
        '    def __enter__(self) -> "Timer":\n'
        '        """Засекает время и отдаёт менеджер в `as`."""\n'
        "        self.elapsed = time.perf_counter()\n"
        "        return self\n\n"
        "    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:\n"
        '        """Ничего не досчитывает; исключение не подавляет."""\n'
        "        return False\n\n\n"
        "with Timer() as timer:\n"
        "    for _ in range(1000):\n"
        "        pass\n\n"
        "result = (isinstance(timer, Timer), timer.elapsed >= 0.0)",
    ),
    # 1083: файл закрывается только на «успешном» выходе из блока.
    (
        1083,
        "class FileManager:\n"
        '    """Закрывает файл, только если блок прошёл без исключения."""\n\n'
        '    def __init__(self, filename: str, mode: str = "r") -> None:\n'
        '        """Запоминает имя файла и режим."""\n'
        "        self.filename = filename\n"
        "        self.mode = mode\n"
        "        self.file = None\n\n"
        "    def __enter__(self):\n"
        '        """Открывает файл и отдаёт его в `as`."""\n'
        "        self.file = open(self.filename, self.mode)\n"
        "        return self.file\n\n"
        "    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:\n"
        '        """Закрывает файл, если исключения не было."""\n'
        "        if exc_type is None:\n"
        "            self.file.close()\n"
        "        return False\n\n\n"
        'with FileManager("isit_demo.txt", "w") as stream:\n'
        '    stream.write("строка\\n")\n\n'
        'with FileManager("isit_demo.txt") as reader:\n'
        "    content = reader.read()\n\n"
        "result = (stream.closed, content)",
    ),
    # 1085: обёртка молчит — трассировка вызовов пропала.
    (
        1085,
        "import functools\n\n\n"
        "def traced(function):\n"
        '    """Возвращает результат функции, ничего не печатая."""\n\n'
        "    @functools.wraps(function)\n"
        "    def wrapper(*args, **kwargs):\n"
        "        return function(*args, **kwargs)\n\n"
        "    return wrapper\n\n\n"
        "@traced\n"
        "def add(a: int, b: int = 1) -> int:\n"
        '    """Складывает два числа."""\n'
        "    return a + b\n\n\n"
        "result = (add(2, 3), add(2, b=5), add.__name__, add.__doc__)",
    ),
    # 1085: печатается литерал «вызов: add» вместо имени обёрнутой функции —
    # на второй подопытной функции это видно (аудит среза 4).
    (
        1085,
        "import functools\n\n\n"
        "def traced(function):\n"
        '    """Печатает заученную строку."""\n\n'
        "    @functools.wraps(function)\n"
        "    def wrapper(*args, **kwargs):\n"
        '        print("вызов: add")\n'
        "        return function(*args, **kwargs)\n\n"
        "    return wrapper\n\n\n"
        "@traced\n"
        "def add(a: int, b: int = 1) -> int:\n"
        '    """Складывает два числа."""\n'
        "    return a + b\n\n\n"
        "result = (add(2, 3), add(2, b=5), add.__name__, add.__doc__)",
    ),
    # 1087: ключом кэша стали только позиционные аргументы — `scale=1` и `scale=2`
    # неразличимы.
    (
        1087,
        "import functools\n"
        "from collections import OrderedDict\n\n\n"
        "def cache(max_size: int):\n"
        '    """Кэширует по позиционным аргументам."""\n\n'
        "    def decorator(function):\n"
        "        cached: OrderedDict[tuple, object] = OrderedDict()\n\n"
        "        @functools.wraps(function)\n"
        "        def wrapper(*args, **kwargs):\n"
        "            key = args\n"
        "            if key in cached:\n"
        "                cached.move_to_end(key)\n"
        "                return cached[key]\n"
        "            value = function(*args, **kwargs)\n"
        "            if max_size > 0:\n"
        "                cached[key] = value\n"
        "                if len(cached) > max_size:\n"
        "                    cached.popitem(last=False)\n"
        "            return value\n\n"
        "        return wrapper\n\n"
        "    return decorator\n\n\n"
        "calls: list[int] = []\n\n\n"
        "@cache(2)\n"
        "def identity(value: int) -> int:\n"
        '    """Считает настоящие вычисления."""\n'
        "    calls.append(value)\n"
        "    return value\n\n\n"
        "first = identity(1)\n"
        "second = identity(2)\n"
        "again = identity(1)\n"
        "third = identity(3)\n"
        "evicted = identity(2)\n\n"
        "result = (first, second, again, third, evicted, len(calls))",
    ),
    # 1087: `popitem()` без `last=False` вытесняет самую свежую запись.
    (
        1087,
        "import functools\n"
        "import inspect\n"
        "from collections import OrderedDict\n\n\n"
        "def cache(max_size: int):\n"
        '    """Вытесняет не ту запись: popitem берёт последнюю."""\n\n'
        "    def decorator(function):\n"
        "        cached: OrderedDict[tuple, object] = OrderedDict()\n"
        "        signature = inspect.signature(function)\n\n"
        "        @functools.wraps(function)\n"
        "        def wrapper(*args, **kwargs):\n"
        "            bound = signature.bind(*args, **kwargs)\n"
        "            bound.apply_defaults()\n"
        "            key = tuple(bound.arguments.items())\n"
        "            if key in cached:\n"
        "                cached.move_to_end(key)\n"
        "                return cached[key]\n"
        "            value = function(*args, **kwargs)\n"
        "            if max_size > 0:\n"
        "                cached[key] = value\n"
        "                if len(cached) > max_size:\n"
        "                    cached.popitem()\n"
        "            return value\n\n"
        "        return wrapper\n\n"
        "    return decorator\n\n\n"
        "calls: list[int] = []\n\n\n"
        "@cache(2)\n"
        "def identity(value: int) -> int:\n"
        '    """Считает настоящие вычисления."""\n'
        "    calls.append(value)\n"
        "    return value\n\n\n"
        "first = identity(1)\n"
        "second = identity(2)\n"
        "again = identity(1)\n"
        "third = identity(3)\n"
        "evicted = identity(2)\n\n"
        "result = (first, second, again, third, evicted, len(calls))",
    ),
    # 1089: перехватывается только stdout — stderr уходит мимо захвата.
    (
        1089,
        "import functools\n"
        "import io\n"
        "import sys\n\n\n"
        "class OutputCapture:\n"
        '    """Перехватывает только stdout."""\n\n'
        "    def __init__(self) -> None:\n"
        '        """Готовит пустые места под перехваченный вывод."""\n'
        '        self.stdout = ""\n'
        '        self.stderr = ""\n'
        "        self._old_stdout = None\n\n"
        '    def __enter__(self) -> "OutputCapture":\n'
        '        """Подменяет stdout и отдаёт менеджер в `as`."""\n'
        "        self._old_stdout = sys.stdout\n"
        "        sys.stdout = io.StringIO()\n"
        "        return self\n\n"
        "    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:\n"
        '        """Возвращает на место stdout; stderr не тронут."""\n'
        "        self.stdout = sys.stdout.getvalue()\n"
        "        sys.stdout = self._old_stdout\n"
        "        return False\n\n\n"
        "def count_calls(function):\n"
        '    """Считает вызовы функции в атрибуте `calls`."""\n\n'
        "    @functools.wraps(function)\n"
        "    def wrapper(*args, **kwargs):\n"
        "        wrapper.calls += 1\n"
        "        return function(*args, **kwargs)\n\n"
        "    wrapper.calls = 0\n"
        "    return wrapper\n\n\n"
        "original = sys.stdout\n"
        "with OutputCapture() as captured:\n"
        '    print("отчёт")\n'
        '    print("ошибка", file=sys.stderr)\n\n\n'
        "@count_calls\n"
        "def doubled(value: int) -> int:\n"
        '    """Удваивает значение."""\n'
        "    return value * 2\n\n\n"
        "doubled(1)\n"
        "doubled(2)\n\n"
        "result = (captured.stdout, captured.stderr, sys.stdout is original, doubled.calls)",
    ),
]

# Рекурсия под именем итеративного обхода: пути верные, но стек вызовов растёт —
# на дереве в 2000 уровней такой «итеративный» обход падает с RecursionError.
_ITERATIVE_AS_RECURSION = (
    "def traverse_dictionary_iterative(dct: dict, prefix: str = '') -> list[tuple[str, int]]:\n"
    '    """Рекурсия под именем итеративного обхода."""\n'
    "    result = []\n"
    "    for key, value in dct.items():\n"
    '        path = f"{prefix}.{key}" if prefix else key\n'
    "        if isinstance(value, dict):\n"
    "            result.extend(traverse_dictionary_iterative(value, path))\n"
    "        else:\n"
    "            result.append((path, value))\n"
    "    return result\n\n\n"
)

# Ловушки, которые падают не `AssertionError`, а ошибкой Python: ученик видит
# трассировку. Тип ошибки тут — часть контракта задания: без проверки на пустоту
# `min([])` даёт `ValueError`, а рекурсивный обход глубокого дерева —
# `RecursionError`; поэтому проверка тестов обязана ловить и их.
CRASHES = [
    (
        1033,
        ValueError,
        "def get_min_max(elements: list[int], default: int | None):\n"
        '    """Без проверки на пустоту."""\n'
        "    return min(elements), max(elements)\n\n\n"
        "result = get_min_max([3, 0, 5, 2, 1, 4, 6], 0)",
    ),
    (
        1053,
        RecursionError,
        _ITERATIVE_AS_RECURSION
        + 'result = sorted(traverse_dictionary_iterative({"a": 1, "b": {"c": 2, "d": {"e": 4}}}))',
    ),
    (
        1055,
        RecursionError,
        "def traverse_dictionary_mutable(dct: dict, result: list[tuple[str, int]], prefix: str = '') -> None:\n"
        '    """Рекурсивный обход: пары дописываются в result."""\n'
        "    for key, value in dct.items():\n"
        '        path = f"{prefix}.{key}" if prefix else key\n'
        "        if isinstance(value, dict):\n"
        "            traverse_dictionary_mutable(value, result, path)\n"
        "        else:\n"
        "            result.append((path, value))\n\n\n"
        + _ITERATIVE_AS_RECURSION
        + 'dct = {"a": 1, "b": {"c": 2, "d": {"e": 4}}}\n'
        "collected = []\n"
        "traverse_dictionary_mutable(dct, collected)\n"
        "result = (sorted(collected), sorted(traverse_dictionary_iterative(dct)))",
    ),
    # 1061: список вместо генератора — источник вычитывается целиком и раньше срока.
    (
        1061,
        RuntimeError,
        "from collections.abc import Iterator\n\n\n"
        "def uniq(sequence: list[int]) -> Iterator[int]:\n"
        '    """Собирает список целиком, хотя задание просит поток."""\n'
        "    seen: set[int] = set()\n"
        "    values = []\n"
        "    for value in sequence:\n"
        "        if value not in seen:\n"
        "            seen.add(value)\n"
        "            values.append(value)\n"
        "    return values\n\n\n"
        "result = (list(uniq([1, 2, 3, 3, 1, 7])), list(uniq([])))",
    ),
    # 1063: на пустой матрице нет нулевой строки — `matrix[0]` роняет проверку длины.
    (
        1063,
        IndexError,
        "def transpose(matrix: list[list[int]]) -> list[list[int]]:\n"
        '    """Длина столбца берётся из нулевой строки."""\n'
        "    return [\n"
        "        [matrix[row][column] for row in range(len(matrix))]\n"
        "        for column in range(len(matrix[0]))\n"
        "    ]\n\n\n"
        "result = (transpose([[1, 2], [3, 4], [5, 6]]), transpose([]))",
    ),
    # 1075: без `__rmul__` скаляр слева не умножается на вектор.
    (
        1075,
        TypeError,
        "class Vector:\n"
        '    """Контейнер чисел одного типа без `__rmul__`."""\n\n'
        "    def __init__(self, elements: list[int]) -> None:\n"
        '        """Запоминает элементы вектора."""\n'
        "        self._elements = list(elements)\n\n"
        "    def __iter__(self):\n"
        '        """Идёт по элементам по порядку."""\n'
        "        return iter(self._elements)\n\n"
        '    def __mul__(self, scalar: int) -> "Vector":\n'
        '        """Умножение на скаляр."""\n'
        "        return Vector([element * scalar for element in self._elements])\n\n\n"
        "one = Vector([1, 2, 3])\n"
        "two = Vector([4, 5, 6])\n\n"
        "result = (list(one * 2), list(3 * two))",
    ),
    # 1077: `__next__` без проверки границы даёт IndexError вместо StopIteration.
    (
        1077,
        IndexError,
        "class VectorIterator:\n"
        '    """Итератор вектора без проверки границы."""\n\n'
        "    def __init__(self, elements: list[int]) -> None:\n"
        '        """Запоминает элементы и начинает с нулевой позиции."""\n'
        "        self._elements = elements\n"
        "        self._index = 0\n\n"
        '    def __iter__(self) -> "VectorIterator":\n'
        '        """Итератор возвращает сам себя."""\n'
        "        return self\n\n"
        "    def __next__(self) -> int:\n"
        '        """Следующий элемент; конец обхода не обработан."""\n'
        "        value = self._elements[self._index]\n"
        "        self._index += 1\n"
        "        return value\n\n\n"
        "class Vector:\n"
        '    """Контейнер, по которому можно пройти несколько раз."""\n\n'
        "    def __init__(self, elements: list[int]) -> None:\n"
        "        self._elements = list(elements)\n\n"
        "    def __iter__(self) -> VectorIterator:\n"
        "        return VectorIterator(self._elements)\n\n\n"
        "vector = Vector([1, 2, 3])\n"
        "first = iter(vector)\n"
        "second = iter(vector)\n\n"
        "result = (next(first), next(first), next(second), list(vector))",
    ),
    # 1085: обёртка без `**kwargs` не принимает именованные аргументы.
    (
        1085,
        TypeError,
        "import functools\n\n\n"
        "def traced(function):\n"
        '    """Обёртка принимает только позиционные аргументы."""\n\n'
        "    @functools.wraps(function)\n"
        "    def wrapper(*args):\n"
        '        print(f"вызов: {function.__name__}")\n'
        "        return function(*args)\n\n"
        "    return wrapper\n\n\n"
        "@traced\n"
        "def add(a: int, b: int = 1) -> int:\n"
        '    """Складывает два числа."""\n'
        "    return a + b\n\n\n"
        "result = (add(2, 3), add(2, b=5), add.__name__, add.__doc__)",
    ),
    # 1089: счётчик вызовов остался локальной переменной — атрибута `calls` нет.
    (
        1089,
        AttributeError,
        "import functools\n"
        "import io\n"
        "import sys\n\n\n"
        "class OutputCapture:\n"
        '    """Перехватывает stdout и stderr внутри блока `with`."""\n\n'
        "    def __init__(self) -> None:\n"
        '        """Готовит пустые места под перехваченный вывод."""\n'
        '        self.stdout = ""\n'
        '        self.stderr = ""\n'
        "        self._old_stdout = None\n"
        "        self._old_stderr = None\n\n"
        '    def __enter__(self) -> "OutputCapture":\n'
        '        """Подменяет потоки и отдаёт менеджер в `as`."""\n'
        "        self._old_stdout = sys.stdout\n"
        "        self._old_stderr = sys.stderr\n"
        "        sys.stdout = io.StringIO()\n"
        "        sys.stderr = io.StringIO()\n"
        "        return self\n\n"
        "    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:\n"
        '        """Возвращает потоки на место; исключение не подавляет."""\n'
        "        self.stdout = sys.stdout.getvalue()\n"
        "        self.stderr = sys.stderr.getvalue()\n"
        "        sys.stdout = self._old_stdout\n"
        "        sys.stderr = self._old_stderr\n"
        "        return False\n\n\n"
        "def count_calls(function):\n"
        '    """Считает вызовы в локальной переменной."""\n\n'
        "    @functools.wraps(function)\n"
        "    def wrapper(*args, **kwargs):\n"
        "        calls = 0\n"
        "        calls += 1\n"
        "        return function(*args, **kwargs)\n\n"
        "    return wrapper\n\n\n"
        "original = sys.stdout\n"
        "with OutputCapture() as captured:\n"
        '    print("отчёт")\n'
        '    print("ошибка", file=sys.stderr)\n\n\n'
        "@count_calls\n"
        "def doubled(value: int) -> int:\n"
        '    """Удваивает значение."""\n'
        "    return value * 2\n\n\n"
        "doubled(1)\n"
        "doubled(2)\n\n"
        "result = (captured.stdout, captured.stderr, sys.stdout is original, doubled.calls)",
    ),
]

# Модули 2 и дальше живут своими файлами (`isit_items_topic02.py`,
# `isit_items_topic03.py`, `isit_items_topic04.py`): задания занятия — это
# десятки строк кода, и общий файл рос бы на сотни строк на каждое занятие.
# Пересечение id запрещено: у словарей `update` молча затёр бы эталон чужого
# модуля, поэтому совпадение ловит проверка ниже.
for _module in (isit_items_topic02, isit_items_topic03, isit_items_topic04):
    _overlap = (set(SOLUTIONS) & set(_module.SOLUTIONS)) | (set(WRONG) & set(_module.WRONG))
    assert not _overlap, f"id заданий повторяются у двух модулей: {sorted(_overlap)}"
    SOLUTIONS.update(_module.SOLUTIONS)
    ALTERNATIVES.extend(_module.ALTERNATIVES)
    WRONG.update(_module.WRONG)
    MORE_WRONG.extend(_module.MORE_WRONG)
    CRASHES.extend(_module.CRASHES)

ITEMS = {
    item.id: item
    for item in course_data(get_course(ISIT)).items
    if item.answer_type == "code"
}


def _run(item, code: str) -> None:
    namespace: dict = {"__name__": "__task__"}
    exec(compile(item.setup or "", "setup", "exec"), namespace)  # noqa: S102
    exec(compile(code, "solution", "exec"), namespace)  # noqa: S102
    exec(compile(item.tests or "", "tests", "exec"), namespace)  # noqa: S102


def test_every_code_item_has_a_reference_a_rival_and_a_trap() -> None:
    """У каждого задания с кодом есть эталон, верная альтернатива и ловушка.

    Без этой сверки задание легко завести в seed и не заметить, что проверки к
    нему не написаны: остальные тесты параметризуются по таблицам, и задание без
    строки в таблице не проверяется вовсе — прогон при этом выглядит обычным.
    """
    traps = (
        set(WRONG)
        | {item_id for item_id, _ in MORE_WRONG}
        | {item_id for item_id, _, _ in CRASHES}
    )

    assert set(SOLUTIONS) == set(ITEMS), f"нет эталона: {sorted(set(ITEMS) - set(SOLUTIONS))}"
    assert {item_id for item_id, _ in ALTERNATIVES} == set(ITEMS), (
        f"нет верной альтернативы: {sorted(set(ITEMS) - {i for i, _ in ALTERNATIVES})}"
    )
    assert not set(ITEMS) - traps, f"нет ни одной ловушки: {sorted(set(ITEMS) - traps)}"


@pytest.mark.parametrize("item_id", sorted(SOLUTIONS))
def test_solution_passes(item_id: int) -> None:
    _run(ITEMS[item_id], SOLUTIONS[item_id])


@pytest.mark.parametrize(
    ("item_id", "code"), [*WRONG.items(), *MORE_WRONG], ids=lambda value: str(value)[:12]
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


def _crash_id(value: object) -> str:
    """Имя для строки прогона: у класса ошибки — её имя, у кода — его начало."""
    return getattr(value, "__name__", str(value))[:14]


@pytest.mark.parametrize(("item_id", "error", "code"), CRASHES, ids=_crash_id)
def test_wrong_solution_crashes(item_id: int, error: type[BaseException], code: str) -> None:
    """Ошибка вместо `AssertionError` — тоже провал: задание обязано её показать."""
    with pytest.raises(error):
        _run(ITEMS[item_id], code)


def test_checks_leave_no_files_behind(tmp_path, monkeypatch) -> None:
    """Проверки заданий не оставляют файлов в рабочем каталоге (аудит среза 4).

    Задание 1083 пишет файл по относительному пути, поэтому уборка в его проверке
    обязана срабатывать и на неверных решениях: иначе частичный прогон (скажем,
    `pytest -x`) оставляет `isit_demo.txt` в корне репозитория.
    """
    monkeypatch.chdir(tmp_path)

    attempts = [*WRONG.items(), *MORE_WRONG, *ALTERNATIVES, (1083, SOLUTIONS[1083])]
    for item_id, code in attempts:
        with contextlib.suppress(AssertionError):
            _run(ITEMS[item_id], code)

    assert list(tmp_path.iterdir()) == [], "проверка задания оставила файл в рабочем каталоге"
