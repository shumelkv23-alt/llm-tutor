# Опции полей и пост-инициализация

## Поля и значения по умолчанию

Изменяемое значение по умолчанию (`items: list = []`) — ошибка: все экземпляры получат один список. Для dataclass используйте `field(default_factory=list)`.

```python
@dataclass
class Cart:
    items: list[str] = field(default_factory=list)
```

Опции `field`: `init=False` (поле не входит в `__init__`), `repr=False`, `compare=False`, `default`, `default_factory`, `metadata`. `ClassVar` исключает атрибут из полей dataclass, а `InitVar` передаёт значение в `__init__` и `__post_init__`, не сохраняя его.

## __post_init__

`__post_init__` вызывается после сгенерированного `__init__` и удобен для вычисляемых полей:

```python
@dataclass
class Rectangle:
    width: float
    height: float
    area: float = field(init=False)

    def __post_init__(self):
        self.area = self.width * self.height
```

Если поле объявлено с `init=False` без значения по умолчанию, его нужно заполнить в `__post_init__`. Для `frozen=True` используйте `object.__setattr__` — обычное присваивание запрещено.

Пост-инициализация не заменяет валидацию: проверки можно делать и там, но ошибки должны быть явными (`ValueError`), а не молчаливыми.
