# Эмуляция числовых типов: вектор

## Арифметика вектора

Числовой тип узнаётся по операторам. Для двумерного вектора:

```python
from math import hypot

class Vector:
    def __init__(self, x=0, y=0):
        self.x, self.y = x, y

    def __repr__(self):
        return f"Vector({self.x!r}, {self.y!r})"

    def __abs__(self):
        return hypot(self.x, self.y)

    def __bool__(self):
        return bool(abs(self))

    def __add__(self, other):
        return Vector(self.x + other.x, self.y + other.y)

    def __mul__(self, scalar):
        return Vector(self.x * scalar, self.y * scalar)
```

`v + w` вызывает `Vector.__add__`; `v * 3` — `__mul__`. Для `3 * v` нужен `__rmul__`: Python сначала пробует `int.__mul__(3, v)`, получает `NotImplemented` и только потом зовёт правый операнд.

Если операция с чужим типом невозможна, возвращайте `NotImplemented`, а не `NotImplementedError`: интерпретатор попробует другой операнд и в конце сам поднимет `TypeError`.

## Сравнение и хеширование

`__eq__` задаёт смысл `==`. По умолчанию объекты сравниваются по идентичности. Определив `__eq__`, вы теряете унаследованный `__hash__` (Python ставит его в `None`): два равных объекта обязаны иметь одинаковый хеш, а автоматически это уже не гарантировано.

Чтобы вектор можно было класть в `set` или использовать как ключ `dict`, определите `__hash__` по неизменяемым полям, например `hash((self.x, self.y))`. Если объект изменяемый, хешировать его опасно: после изменения он «потеряется» в множестве.
