# Свои типы: dataclass и Generic

Заказ интернет-магазина — это набор позиций, у каждой позиции товар, а у товара
название и цена. Описывать это словарями (`{"cost": 25, "title": "Ложка"}`)
можно ровно до первой опечатки в ключе. Занятие о своих типах: `dataclass`
вместо рукописного класса, порядок сравнения, контейнер с арифметикой и свой
итератор.

## Зачем

После занятия вы сможете:

- описать тип одной строкой `@dataclass` и объяснить, что он сгенерировал;
- сделать объект неизменяемым и проверить инварианты при создании;
- задать порядок сравнения полями, а не руками;
- написать контейнер с `__len__`, `__getitem__` и арифметикой;
- ограничить тип элементов в обобщённом классе;
- различить iterable и iterator и реализовать протокол итератора.

## Неизменяемый dataclass

### Главное

`@dataclass` — это генератор кода: по списку аннотированных полей он пишет
`__init__`, `__repr__` и `__eq__`. Рукописный `__init__` в таком классе не
нужен и мешает: тесты лаборатории прямо проверяют, что его нет.

`frozen=True` добавляет к этому `__setattr__`, который бросает
`FrozenInstanceError` (наследник `AttributeError`) на любую попытку
присваивания. Неизменяемый объект можно положить в `set` или сделать ключом
словаря, и никто не поменяет поле у вас под руками — для товара это и есть
нужное поведение.

Проверки инвариантов живут в `__post_init__`: `dataclass` вызывает его в конце
сгенерированного `__init__`, когда все поля уже присвоены. Там и проверяют
«название непустое, цена положительная». В задании лаборатории просят именно
`assert`: договор о том, чего не бывает. Помните о цене: под `python -O` все
`assert` исчезают, и проверки вместе с ними.

Порядок полей — часть договора: он задаёт порядок аргументов `__init__` и
порядок сравнения (следующий раздел). Меняя порядок полей, вы меняете
интерфейс класса.

### Пример

```python
from dataclasses import dataclass, FrozenInstanceError


@dataclass(frozen=True)
class Item:
    """Товар: цена положительная, название непустое, поля не меняются."""

    title: str
    cost: int

    def __post_init__(self) -> None:
        assert self.title, "Название не может быть пустым"
        assert self.cost > 0, "Цена должна быть положительной"


spoon = Item(title="Ложка", cost=25)
print(spoon)                                     # Item(title='Ложка', cost=25)
print(spoon == Item(title="Ложка", cost=25))     # True — __eq__ сравнивает поля
print(len({spoon, Item(title="Ложка", cost=25)}))  # 1 — объект хешируется

try:
    spoon.cost = 30
except FrozenInstanceError as error:
    print(type(error).__name__, error)           # FrozenInstanceError ...

for title, cost in [("", 25), ("Ручка", 0), ("Ручка", -25)]:
    try:
        Item(title=title, cost=cost)
    except AssertionError as error:
        print("AssertionError:", error)
```

### Частые ошибки

- **`@dataclass` забыт.** Класс с одними аннотациями полей выглядит похоже, но
  `__init__` из них не появляется: `Item(title="Ложка", cost=25)` падает
  `TypeError`.
- **Проверки не в `__post_init__`.** Проверка отдельным методом, который надо
  не забыть вызвать, — это не инвариант: создать плохой объект всё ещё можно.
- **`frozen=True` забыт.** Присваивание проходит молча, и правило «поля не
  меняются» держится только на честном слове.
- **Рукописный `__init__`.** Он переопределяет сгенерированный, и `frozen`
  обходится через `object.__setattr__`; в задании это отдельно запрещено.
- **Проверка на `-O`.** Под `python -O` ни один `assert` не выполнится; если
  проверка должна работать всегда, это `raise ValueError(...)`.

## Сравнение и сортировка

### Главное

`@dataclass(order=True)` генерирует `__lt__`, `__le__`, `__gt__`, `__ge__` —
и сравнивает объекты как кортежи полей **в порядке объявления**. Товары в
задании сравниваются «по названию, а при равных названиях по цене»; значит
`title` объявляется раньше `cost`, и ничего больше писать не надо.

Поле, которое не должно участвовать в сравнении, помечают
`field(compare=False)`: у товара это `item_id` — он различает товары-близнецы,
но порядок задаёт не он. Заодно `field` умеет `default` — и тогда поле
обязательно должно идти после полей без значения по умолчанию.

Ручные `__lt__`/`__eq__` в этой задаче запрещены: их пишут руками там, где
`dataclass` их не даёт, а здесь это просто дублирование. Две тонкости, которые
следуют из `compare=False`:

- поле выпадает не только из `<`, но и из `==`: два товара с одинаковыми
  названием и ценой равны, даже если `item_id` у них разный;
- сортировка в Python **стабильна** — равные элементы остаются в исходном
  порядке, и именно на это полагаются тесты, когда `item_id` из сравнения
  исключён.

А ещё `eq` снимает `__hash__`: такой объект нельзя положить в `set`, пока тип не
станет `frozen`.

### Пример

```python
from dataclasses import dataclass, field


@dataclass(order=True)
class Item:
    """Сравнение — по названию, затем по цене; item_id в сравнении не участвует."""

    title: str
    cost: int
    item_id: int = field(compare=False, default=0)


items = [
    Item("Thing", 44, 9),
    Item("Note", 8, 0),
    Item("Things", 100, 1),
    Item("Unity", 5, 8),
    Item("Things", 44, 11),
    Item("Note", 64, 15),
]

print([item.item_id for item in sorted(items)])          # [0, 15, 9, 11, 1, 8]
print(Item("Pop-it", 200) < Item("Simple Dimple", 200))  # True — сравнили названия
print(Item("Jacket", 15000) > Item("Jacket", 400))       # True — названия равны, решила цена
print(Item("Note", 8, 0) < Item("Note", 8, 7))           # False — item_id не сравнивается
print(Item("Note", 8, 0) == Item("Note", 8, 7))          # True — и в __eq__ тоже

try:
    {Item("Note", 8, 0)}
except TypeError as error:
    print("TypeError:", error)   # eq снимает __hash__; класть в set можно только frozen

same = sorted([Item("Note", 8, 0), Item("Note", 8, 7)])
print([item.item_id for item in same])                   # [0, 7] — сортировка стабильна
```

### Частые ошибки

- **`compare=False` не поставлен.** `sorted` начнёт различать близнецов по
  `item_id`, и порядок перестанет совпадать с договором «название, потом цена».
- **Поля объявлены не в том порядке.** `cost` раньше `title` — и товары
  сортируются по цене, а название решает только при равных ценах.
- **Ручной `__lt__`.** Он молча переопределяет поведение `order=True` (и в
  задании запрещён): договор о сравнении разъезжается с тем, что видят тесты.
- **Сортировка по одному полю.** `sorted(items, key=lambda item: item.cost)`
  отвечает на другой вопрос — «по цене», а не «по названию, потом по цене».
- **`order=True` на несравнимых полях.** Если среди полей окажется словарь или
  список, сортировка упадёт: `dataclass` умеет сравнивать только сравнимые.

## Свой вектор

### Главное

Контейнер — это набор «дандеров», и у каждого своя роль: `__init__` принимает
данные и копирует их (`list(elements)` — чтобы переданный список не стал
внутренним состоянием), `__len__` отвечает `len(v)`, `__getitem__` — `v[i]`
(отрицательные индексы список обрабатывает сам), `__str__`/`__repr__` — печать.

Арифметика — тоже дандеры: `__add__`, `__sub__`, `__mul__`. Три правила:

- **операнды не меняются** — результат новый объект, `return Vector([...])`, а
  не `self._elements += ...`;
- **про длины надо подумать** — `zip` молча обрежет по короткому вектору, и
  несовпадение длин пройдёт незамеченным: в примере ниже векторы одной длины,
  поэтому сверки там нет, но в своём коде реши, что делать с несовпадением
  (чаще всего — понятная ошибка вместо молчаливого обреза);
- **`__rmul__` нужен отдельно** — для `3 * v` Python сначала спрашивает
  `int.__mul__`, получает `NotImplemented` и только потом зовёт `Vector.__rmul__`.

Обобщённость даёт `Generic[T]` вместе с `TypeVar` **с ограничениями**:
`T = TypeVar("T", int, float)` разрешает только перечисленные типы, и
`Vector[str]` для mypy — ошибка. Это не то же самое, что `bound`: `bound`
разрешает любой подходящий тип, ограничения — точный список.

### Пример

```python
from collections.abc import Iterator
from typing import Generic, TypeVar

T = TypeVar("T", int, float)


class Vector(Generic[T]):
    """Контейнер чисел одного типа: длина, индексы, арифметика, скалярное произведение."""

    def __init__(self, elements: list[T]) -> None:
        self._elements: list[T] = list(elements)

    def __len__(self) -> int:
        return len(self._elements)

    def __getitem__(self, index: int) -> T:
        return self._elements[index]

    def append(self, element: T) -> None:
        self._elements.append(element)

    def __iter__(self) -> Iterator[T]:
        return iter(self._elements)

    def __str__(self) -> str:
        return "Vector(" + ", ".join(str(element) for element in self._elements) + ")"

    def __repr__(self) -> str:
        return f"Vector({self._elements!r})"

    def __add__(self, other: "Vector[T]") -> "Vector[T]":
        return Vector([left + right for left, right in zip(self._elements, other._elements)])

    def __sub__(self, other: "Vector[T]") -> "Vector[T]":
        return Vector([left - right for left, right in zip(self._elements, other._elements)])

    def __mul__(self, scalar: T) -> "Vector[T]":
        return Vector([element * scalar for element in self._elements])

    def __rmul__(self, scalar: T) -> "Vector[T]":
        return self * scalar

    def dot(self, other: "Vector[T]") -> T:
        return sum(left * right for left, right in zip(self._elements, other._elements))


first = Vector[int]([1, 2, 3])
second = Vector[int]([4, 5, 6])

print(first + second)                 # Vector([5, 7, 9])
print(second - first)                 # Vector([3, 3, 3])
print(first * 2, 3 * second)          # Vector([2, 4, 6]) Vector([12, 15, 18])
print(first.dot(second))              # 32
print(first)                          # вызывается __str__
print(repr(first))                    # вызывается __repr__
print(len(first), first[0], first[-1])  # 3 1 3
print(list((first + second) * 2))     # [10, 14, 18]
print(list(first), list(first))       # два прохода, вектор не израсходован
```

### Частые ошибки

- **`zip` без проверки длины.** `Vector([1, 2, 3]) + Vector([1])` вернёт вектор
  из одного элемента — вместо понятной ошибки о несовпадении размеров.
- **Мутация вместо нового объекта.** `self._elements[i] += other[i]` и `return
  self` меняют левый операнд: `(a + b)` испортит `a`, и следующая строка
  посчитает уже другое.
- **`__rmul__` забыт.** `2 * v` падает с `TypeError`, хотя `v * 2` работает.
- **`Any` в аннотациях.** Это требование исходной лаборатории, а не проверки
  тьютора: её тесты обходят `inspect.signature` и требуют аннотацию у каждого
  параметра (кроме `self`) и у возврата, причём `Any` запрещён.
- **`TypeVar` без ограничений.** `T = TypeVar("T")` пропускает `Vector[str]`, и
  проверка типов перестаёт что-либо запрещать.
- **`__str__` возвращает не строку.** Объект, а не текст, — `TypeError` при
  первой же печати; `str()` и `repr()` должны возвращать `str`.

## Свой итератор

### Главное

**Iterable** — то, по чему можно пройти: у него есть `__iter__`, и каждый
вызов возвращает **новый** итератор. **Iterator** — то, что идёт по элементам:
у него есть `__next__`, а `__iter__` возвращает **сам себя**. Контейнер —
iterable, `VectorIterator` — iterator, и вместе они дают `for`:

- `for` зовёт `iter(vector)`, получает итератор и дёргает `next` до
  `StopIteration`;
- позиция живёт в итераторе, поэтому два `iter(vector)` независимы, а второй
  `for` по тому же вектору снова видит все элементы;
- `StopIteration` — не ошибка, а сигнал «элементы кончились»; цикл `for`
  перехватывает её сам, а вот в `while True` её ловят руками.

Если контейнер вернёт из `__iter__` самого себя, он перестанет быть
многоразовым: первый проход заберёт позицию, второй ничего не получит. Если
итератор вернёт из `__iter__` новый итератор, позиция будет сбрасываться на
каждом шаге — `list(iterator)` не кончится никогда.

### Пример

```python
from collections.abc import Iterator
from typing import Generic, TypeVar

T = TypeVar("T", int, float)


class VectorIterator(Generic[T]):
    """Итератор вектора: хранит ссылку на данные и текущую позицию."""

    def __init__(self, elements: list[T]) -> None:
        self._elements = elements
        self._index = 0

    def __iter__(self) -> "VectorIterator[T]":
        return self

    def __next__(self) -> T:
        if self._index >= len(self._elements):
            raise StopIteration
        element = self._elements[self._index]
        self._index += 1
        return element


class Vector(Generic[T]):
    """Контейнер: каждый `iter()` даёт свой независимый итератор."""

    def __init__(self, elements: list[T]) -> None:
        self._elements = list(elements)

    def __iter__(self) -> VectorIterator[T]:
        return VectorIterator(self._elements)


vector = Vector[int]([1, 2, 3])
first, second = iter(vector), iter(vector)
print(next(first), next(first), next(second))   # 1 2 1 — итераторы независимы
print(list(vector), list(vector))               # [1, 2, 3] [1, 2, 3] — вектор многоразовый

iterator = iter(vector)
print(isinstance(iterator, Iterator))           # True — протокол итератора соблюдён

try:
    while True:
        next(iterator)
except StopIteration:
    print("StopIteration после последнего элемента")

print([element for element in vector])          # [1, 2, 3]
print(next(iter(Vector[int]([])), "пусто"))     # пусто: пустой вектор сразу кончился
```

### Частые ошибки

- **Контейнер возвращает `self`.** Второй `for` по тому же вектору не увидит ни
  одного элемента, а `list(vector)` во второй раз даст `[]`.
- **`__next__` без `StopIteration`.** Вместо конца потока — вечный цикл с
  последним элементом или `IndexError` на выходе за границу.
- **`__iter__` итератора создаёт новый итератор.** Позиция сбрасывается, и
  проход не заканчивается: `for` по такому итератору зависает.
- **Позиция в классе, а не в экземпляре.** Атрибут-счётчик, объявленный на
  уровне класса, общий для всех итераторов: они начнут мешать друг другу.
- **`IndexError` вместо `StopIteration`.** Доступ к элементу без проверки
  границы ломает `for`, который ждёт именно `StopIteration`.

## Итоги занятия

| Что | Как | Зачем |
|---|---|---|
| Свой тип | `@dataclass` + аннотированные поля | `__init__` и `__repr__` даром |
| Инварианты | `__post_init__` + `assert` | плохой объект не создать |
| Неизменяемость | `frozen=True` | `FrozenInstanceError`, объект можно хешировать |
| Порядок сравнения | `order=True`, поля по важности | сравнение как у кортежа полей |
| Поле вне сравнения | `field(compare=False, default=…)` | `item_id` не решает порядок |
| Обобщённость | `Generic[T]`, `TypeVar("T", int, float)` | `Vector[str]` отклоняет mypy |
| Арифметика | `__add__`, `__sub__`, `__mul__`, `__rmul__` | новый объект, операнды целы |
| Протокол итератора | `__iter__` → новый итератор, `__next__` + `StopIteration` | несколько независимых обходов |

Правило занятия: свой тип — это договор. `dataclass` избавляет от рукописного
кода, но сам договор описываете вы: какие поля обязательны, что с чем
сравнивается, что можно менять и как по объекту идут.

## Источник

Задания лаборатории [`lab1-python-intro`](https://github.com/Eldervald/isit_ml_labs/tree/0da67c6/lab1-python-intro):
[`k_orders`](https://github.com/Eldervald/isit_ml_labs/tree/0da67c6/lab1-python-intro/k_orders),
[`l_typy_generic`](https://github.com/Eldervald/isit_ml_labs/tree/0da67c6/lab1-python-intro/l_typy_generic);
документация Python:
[`dataclasses`](https://docs.python.org/3/library/dataclasses.html),
[`typing.Generic`](https://docs.python.org/3/library/typing.html#typing.Generic),
[протокол итератора](https://docs.python.org/3/tutorial/classes.html#iterators).
