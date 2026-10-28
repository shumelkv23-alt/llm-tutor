# Колода карт: последовательность без наследования

## Класс FrenchDeck

Колода — это список карт, но наследоваться от `list` необязательно. Достаточно хранить карты в атрибуте и реализовать `__len__` и `__getitem__`:

```python
import collections

Card = collections.namedtuple("Card", ["rank", "suit"])

class FrenchDeck:
    ranks = [str(n) for n in range(2, 11)] + list("JQKA")
    suits = "пики черви бубны трефы".split()

    def __init__(self):
        self._cards = [Card(rank, suit) for suit in self.suits for rank in self.ranks]

    def __len__(self):
        return len(self._cards)

    def __getitem__(self, position):
        return self._cards[position]
```

Такой класс уже ведёт себя как последовательность: работает `len(deck)`, `deck[0]`, `deck[-1]`, срез `deck[:3]`, перебор в `for` и `random.choice(deck)`.

## Итерация, срезы и перемешивание

Если `__iter__` нет, Python перебирает объект через `__getitem__` с индексами 0, 1, 2…, пока не получит `IndexError`. Поэтому `for card in deck` и `in` работают без отдельного метода.

Срез `deck[:3]` вернёт обычный список: `__getitem__` получает объект `slice`, а ваш код просто передаёт его списку карт. А вот `random.shuffle(deck)` требует `__setitem__` и упадёт с `TypeError`: колода только для чтения. Чтобы её перемешать, меняют местами элементы внутреннего списка или добавляют `__setitem__`.
