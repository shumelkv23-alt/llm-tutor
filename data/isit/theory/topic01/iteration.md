# Итерация по спискам

Список — самая частая структура в задачах на данные: значения из столбца,
предсказания модели, идентификаторы пользователей. Занятие про то, как пройти
по нему, не потеряв индекс, и как не сломать сам список по дороге.

## Зачем

После занятия вы сможете:

- выбирать способ итерации под задачу: по значениям, по индексам или сразу по
  обоим (`enumerate`);
- нумеровать элементы с единицы, когда этого требует условие;
- брать каждый второй элемент и понимать, почему `[::2]` и `[1::2]` — это
  разные списки;
- находить минимум, максимум и индекс максимума, не падая на пустом списке;
- искать элемент и отличать «не нашли» от «нашли на нулевой позиции»;
- не менять входной список, пока об этом не просят.

## Способы пройти список

### Главное

Способ итерации выбирают по тому, что нужно внутри цикла:

- **нужно значение** — `for value in elements`;
- **нужен индекс** — `for index in range(len(elements))`;
- **нужны оба** — `for index, value in enumerate(elements)`; нумерация с
  единицы — `enumerate(elements, start=1)`;
- **нужен обратный порядок или шаг** — `reversed(elements)` и срезы
  `elements[start:stop:step]`.

Срез — это копия, а не представление: `elements[1::2]` создаёт новый список из
элементов с индексами 1, 3, 5. «Каждый второй» — именно они, а `elements[::2]`
берёт первый, третий, пятый, то есть совсем другое.

Итерация по списку ничего не копирует и не защищает от изменений: если внутри
цикла список пополняется или из него удаляют, цикл начинает идти по
изменившимся данным. Поэтому входные данные, пока об этом не просят, не
трогают — и «испортил вход» становится ошибкой не вкуса, а поведения.

### Пример

```python
elements = [-2, 0, 5, 2, 3, 4, 3]

squares = []
for value in elements:
    squares.append(value * value)
print(squares)

indices_from_one = []
for index in range(len(elements)):
    indices_from_one.append(index + 1)
print(indices_from_one)

for number, value in enumerate(elements, start=1):
    print(number, value, sep=": ")

print(elements[1::2], elements[::2])
print(list(reversed(elements)))
```

### Частые ошибки

- **`elements[::2]` вместо `elements[1::2]`.** Задача «каждый второй элемент»
  — это элементы с индексами 1, 3, 5; срез с нуля берёт чётные позиции.
- **Индексация с нуля там, где условие просит с единицы.** В Python индексы
  начинаются с нуля, и «список индексов, начиная с 1» — это `index + 1`, а не
  `index`.
- **`for index in range(len(elements))` ради одного значения.**
  `value = elements[index]` внутри такого цикла — лишний шаг; когда индекс не
  нужен, итерируются по значениям.
- **Изменение списка внутри цикла по нему.** `append` внутри `for value in
  elements` удлиняет список, по которому идёт цикл.

## Минимум и максимум

### Главное

`min(elements)` и `max(elements)` — встроенные функции, и на непустом списке
они делают ровно то, что обещают. Ловушка одна: на **пустом** списке они
поднимают `ValueError: min() iterable argument is empty`. Пустой вход — не
редкость, поэтому в условии и появляется параметр `default`: пустой список
возвращает пару `(default, default)`.

Индекс максимального элемента — отдельная задача. `elements.index(max(elements))`
работает, но на пустом списке падает, а условие требует `None`. Надёжнее
считать индекс явно: `max(range(len(elements)), key=lambda index: elements[index])`.
При равных значениях `max` берёт **первое** максимальное — так же, как
`list.index`.

### Пример

```python
def get_min_max(elements: list[int], default: int | None) -> tuple[int | None, int | None]:
    """Минимум и максимум списка; на пустом списке — пара default."""
    if not elements:
        return default, default
    return min(elements), max(elements)


def get_max_element_index(elements: list[int]) -> int | None:
    """Индекс первого максимального элемента или None, если список пуст."""
    if not elements:
        return None
    return max(range(len(elements)), key=lambda index: elements[index])


print(get_min_max([3, 0, 5, 2, 1, 4, 6], 0))            # (0, 6)
print(get_min_max([], 0), get_min_max([], None))        # (0, 0) (None, None)
print(get_max_element_index([-2, 0, 5, 2, 3, 4, 3]))    # 2
print(get_max_element_index([]))                        # None

try:
    min([])
except ValueError as exc:
    print("без проверки на пустоту:", exc)
```

### Частые ошибки

- **`min(elements)` без проверки на пустоту.** Условие с `default` проверяет
  именно этот случай; на пустом списке без ветки функция падает.
- **`elements.index(max(elements))` без защиты.** На пустом списке это
  `ValueError` там, где условие требует `None`.
- **Возврат значения вместо индекса.** `max(elements)` даёт максимум, а не его
  позицию: для `[-2, 0, 5, 2, 3, 4, 3]` правильный ответ — `2`, а не `5`.
- **`default` вместо ответа на непустом списке.** Ветка «пусто» не должна
  срабатывать для списка, в котором есть значения.

## Поиск в списке

### Главное

`elements.index(3)` возвращает индекс **первого** вхождения и поднимает
`ValueError`, если элемента в списке нет: «мягкого» поиска, как у `dict.get`, у
списка нет. Значит, «не нашли» возвращает сама функция — через проверку
`if 3 in elements` или через цикл с `enumerate`, который дошёл до конца и
вернул `None`.

Последнее вхождение получают разворотом: `len(elements) - 1 - elements[::-1].index(3)`.
Здесь легко ошибиться в пересчёте индекса, поэтому такой однострочник стоит
проверять на двух-трёх примерах, включая случай, когда тройка стоит в начале.

Моржовый оператор `:=` пригодится в модельной задаче про обращение по индексу:
если обращение дорогое, `elements[index]` внутри условия и в возвращаемом
значении — это два обращения, а `value if (value := elements[index]) > boundary
else None` — одно. Тьютор проверяет поведение функции, а не наличие оператора:
задача про то, чтобы обращение действительно было однократным.

### Пример

```python
def get_first_three_index(elements: list[int]) -> int | None:
    """Индекс первой тройки или None, если её нет."""
    for index, value in enumerate(elements):
        if value == 3:
            return index
    return None


def get_last_three_index(elements: list[int]) -> int | None:
    """Индекс последней тройки или None, если её нет."""
    if 3 not in elements:
        return None
    return len(elements) - 1 - elements[::-1].index(3)


def get_by_index(elements: list[int], index: int, boundary: int) -> int | None:
    """Элемент по индексу, если он больше границы, иначе None."""
    return value if (value := elements[index]) > boundary else None


data = [-2, 0, 5, 2, 3, 4, 3]
print(get_first_three_index(data), get_last_three_index(data))    # 4 6
print(get_first_three_index([1, 2]), get_last_three_index([]))    # None None
print(get_last_three_index([3, 0, 5, 2, 1, 4, 6]))                # 0
print(get_by_index(data, 5, 3), get_by_index(data, 5, 4))         # 4 None
```

### Частые ошибки

- **`elements.index(3)` без проверки.** На списке без тройки — `ValueError`
  вместо `None`.
- **Достигли конца цикла и ничего не вернули.** Функция без `return` после
  цикла возвращает `None` — случайно, а не по замыслу; лучше написать это
  явно.
- **Ноль как признак «не нашли».** `[3, 0, 5]` даёт индекс `0` — законный
  ответ; путать его с «не нашли» нельзя, поэтому отсутствие обозначает именно
  `None`.
- **Обращение по индексу дважды.** `elements[index] > boundary` и
  `elements[index]` в возврате — два обращения; на модельной задаче это и есть
  ошибка.

## Итоги занятия

| Что | Когда нужно |
|---|---|
| `for value in elements` | нужны только значения |
| `for index in range(len(elements))` | нужен только индекс |
| `enumerate(elements, start=1)` | нужны оба, нумерация с единицы |
| `elements[1::2]` | каждый второй элемент |
| `min` / `max` | крайние значения; на пустом списке — `default` |
| `max(range(len(elements)), key=elements[index])` | индекс максимума |
| `elements.index(3)` | первое вхождение; без элемента — `ValueError` |
| `value if (value := elements[index]) > boundary else None` | одно обращение по индексу |

Правило занятия: сначала решите, что нужно внутри цикла — значение, индекс или
оба, — и выберите способ по этому ответу. Пустой список и «элемента нет» —
такие же входные данные, как и все остальные.

## Источник

Задание лаборатории [`lab1-python-intro`](https://github.com/Eldervald/isit_ml_labs/tree/0da67c6/lab1-python-intro):
[`f_iterate_me`](https://github.com/Eldervald/isit_ml_labs/tree/0da67c6/lab1-python-intro/f_iterate_me);
документация Python: [`enumerate`](https://docs.python.org/3/library/functions.html#enumerate),
[`min`/`max`](https://docs.python.org/3/library/functions.html#min),
[типы последовательностей](https://docs.python.org/3/library/stdtypes.html#sequence-types-list-tuple-range).
