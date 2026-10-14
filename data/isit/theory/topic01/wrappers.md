# Обёртки: with и декораторы

Обёртка — это код, который выполняется вокруг чужого кода: до, после и вместо
него. `with` оборачивает блок, декоратор — функцию. Занятие о том, как такие
обёртки пишутся: контекстные менеджеры с уборкой за собой и декоратор кэша с
вытеснением.

## Зачем

После занятия вы сможете:

- реализовать контекстный менеджер методами `__enter__` и `__exit__`;
- гарантировать уборку даже при исключении внутри блока;
- объяснить, что значит возврат `__exit__` и когда исключение подавляется;
- написать декоратор и декоратор с параметром, сохранив имя и докстринг;
- сделать LRU-кэш на `OrderedDict` и назвать его ловушки.

## Контекстный менеджер

### Главное

`with` — это синтаксис над протоколом из двух методов. Конструкция
`with Timer() as timer: ...` делает примерно следующее: создаёт объект,
вызывает у него `__enter__` и
присваивает результат имени после `as`, выполняет тело, а затем — **обязательно** —
вызывает `__exit__(exc_type, exc_val, exc_tb)`. Три аргумента описывают
исключение, из-за которого блок покинули; если блок завершился нормально, все
три равны `None`.

Правила, которые стоит помнить:

- `__enter__` возвращает то, что попадёт после `as`; чаще всего это `self`, и
  тогда внутри блока доступны атрибуты менеджера (`timer.elapsed`);
- `__exit__` вызывается и при исключении — в этом весь смысл `with`;
- возврат `__exit__` решает судьбу исключения: `False` (или `None`) —
  исключение идёт дальше, `True` — подавляется. Молчаливое `True` прячет
  ошибку от вызывающего кода, поэтому в учебных менеджерах возвращают `False`;
- исключение из тела не «теряется»: до `__exit__` оно доходит в аргументах, а
  после — продолжает путь наверх.

Альтернатива классу — декоратор `contextlib.contextmanager`, который
превращает генератор в менеджер: код до `yield` — это `__enter__`, после —
`__exit__`. В задании лаборатории менеджеры пишут классом: так видно механику.

### Пример

```python
import time


class Timer:
    """Замеряет время выполнения блока `with`; итог — в `elapsed`."""

    def __init__(self) -> None:
        self._start = 0.0
        self.elapsed = 0.0

    def __enter__(self) -> "Timer":
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.elapsed = time.perf_counter() - self._start
        return False


with Timer() as timer:
    total = sum(range(1000))

print(timer.elapsed >= 0.0, total)      # True 499500
print(isinstance(timer, Timer))         # True — __enter__ вернул self

same = Timer()
with same:
    pass
print(same.elapsed >= 0.0)              # True — имя после `as` не обязательно

try:
    with Timer() as failed:
        raise ValueError("сломалось внутри блока")
except ValueError as error:
    print("исключение ушло наверх:", error)
print(failed.elapsed >= 0.0)            # True — __exit__ отработал и при исключении
```

### Частые ошибки

- **Неверная сигнатура `__exit__`.** Без трёх аргументов исключения Python
  вызовет метод с четырьмя позициями и получит `TypeError` ровно в тот момент,
  когда ошибка уже случилась.
- **`__enter__` возвращает `None`.** `with FileManager(...) as f` даст `f =
  None`, и первое же `f.write(...)` упадёт.
- **`__exit__` возвращает `True`.** Исключение внутри блока исчезает: блок
  выглядит выполненным, хотя сломался на середине.
- **Замер по `time.time()`.** Эти часы могут «прыгнуть» при смене системного
  времени; для интервалов есть монотонный `time.perf_counter()`.
- **Поля не заполнены при исключении.** Если `elapsed` присваивается только в
  нормальном пути, после сбоя атрибут останется нулём, и причина будет
  выглядеть как «мгновенно».

## Уборка за собой

### Главное

Второй аргумент за `with` — гарантия: код в `__exit__` выполнится и при
исключении, и при `return` из функции, и при `break` из цикла. Под капотом это
`try/finally`, но записанный один раз — в менеджере, а не в каждом месте
вызова.

Отсюда два типовых сценария:

- **ресурс** — `FileManager` открывает файл в `__enter__` и закрывает в
  `__exit__`. Закрытие в конце блока «руками» не спасает: исключение уводит
  управление мимо этой строки;
- **состояние** — `OutputCapture` подменяет `sys.stdout` и `sys.stderr` на
  `io.StringIO`, а в `__exit__` возвращает прежние потоки на место. Вложенные
  менеджеры работают как матрёшка: внутренний сохраняет уже подменённые потоки
  и возвращает именно их. Восстановление состояния в `__exit__` — тот же
  `finally`, что и закрытие файла.

Порядок здесь принципиален: **сначала** забрать результат (`getvalue()`),
**потом** восстановить потоки. Если переставить, перехваченный текст уедет в
восстановленный поток.

### Пример

```python
import io
import os
import sys
import tempfile


class FileManager:
    """Открывает файл при входе в блок и закрывает его при выходе."""

    def __init__(self, filename: str, mode: str = "r") -> None:
        self.filename = filename
        self.mode = mode
        self.file = None

    def __enter__(self):
        self.file = open(self.filename, self.mode, encoding="utf-8")
        return self.file

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.file.close()
        return False


class OutputCapture:
    """Перехватывает stdout и stderr внутри блока, потоки возвращает на место."""

    def __init__(self) -> None:
        self._old_stdout = None
        self._old_stderr = None
        self.stdout = ""
        self.stderr = ""

    def __enter__(self) -> "OutputCapture":
        self._old_stdout, self._old_stderr = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.stdout = sys.stdout.getvalue()
        self.stderr = sys.stderr.getvalue()
        sys.stdout, sys.stderr = self._old_stdout, self._old_stderr
        return False


path = os.path.join(tempfile.mkdtemp(), "demo.txt")
try:
    with FileManager(path, "w") as stream:
        stream.write("строка\n")
        raise ValueError("сломалось после записи")
except ValueError as error:
    print("исключение ушло наверх:", error)
print("файл закрыт:", stream.closed)             # True — уборка была в __exit__

with FileManager(path) as stream:
    print("прочитано:", repr(stream.read()))

original = sys.stdout
with OutputCapture() as captured:
    print("в stdout")
    print("в stderr", file=sys.stderr)
print(repr(captured.stdout), repr(captured.stderr))   # 'в stdout\n' 'в stderr\n'
print(sys.stdout is original)                          # True — поток вернулся

with OutputCapture() as outer:
    print("внешний")
    with OutputCapture() as inner:
        print("внутренний")
    print("внутренний перехвачен:", repr(inner.stdout))
print("внешний перехвачен:", repr(outer.stdout))
```

### Частые ошибки

- **Уборка в конце блока, а не в `__exit__`.** Первое же исключение — и файл
  остаётся открытым; тесты лаборатории проверяют `f.closed` именно после
  исключения.
- **`close()` без проверки.** Если `__enter__` упал на `open()` (нет файла,
  нет прав), `self.file` останется `None`, и `__exit__` упадёт с
  `AttributeError` поверх исходной ошибки.
- **Потоки не восстановлены.** `sys.stdout` остаётся `StringIO`: весь
  дальнейший вывод уходит в никуда, а ошибка ищется совсем в другом месте.
- **Восстановление до чтения результата.** Поменяли местами `getvalue()` и
  присваивание `sys.stdout` — перехваченный текст исчез.
- **`True` из `__exit__`.** Подавленное исключение превращает сломанный блок в
  «выполненный успешно».

## Декораторы

### Главное

В Python функция — объект, поэтому её можно передать в другую функцию и
получить обратно. Декоратор — это именно такая функция: принимает функцию,
возвращает функцию, а `@имя` над `def` — сокращение для `f = имя(f)`.

Декоратор с параметром (`@cache(10)`) — на уровень глубже: `cache(10)`
возвращает **декоратор**, который уже применяется к функции. Отсюда три
вложенные функции: внешняя запоминает параметр, средняя получает функцию,
внутренняя (`wrapper`) вызывается вместо неё.

Что обязательно внутри `wrapper`:

- `*args, **kwargs` — иначе функция с именованными аргументами или их числом,
  отличным от ожидаемого, сломается при вызове;
- `functools.wraps(function)` — без него `wrapper.__name__` будет `"wrapper"`,
  а докстринг потеряется. Тесты лаборатории это проверяют;
- сам вызов `function(...)` — внутри `wrapper`, а не при декорировании:
  вызов снаружи означает «функция выполнилась один раз при импорте».

### Пример

```python
import functools
from typing import Callable


def traced(function: Callable) -> Callable:
    """Печатает имя вызываемой функции — декоратор без параметров."""

    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        print("вызов:", function.__name__)
        return function(*args, **kwargs)

    return wrapper


def repeat(times: int) -> Callable:
    """Декоратор с параметром: сколько раз повторить вызов."""

    def decorator(function: Callable) -> Callable:
        @functools.wraps(function)
        def wrapper(*args, **kwargs):
            return [function(*args, **kwargs) for _ in range(times)]

        return wrapper

    return decorator


@traced
def add(a: int, b: int = 1) -> int:
    """Складывает два числа."""
    return a + b


@repeat(3)
def greet(name: str) -> str:
    """Приветствие по имени."""
    return f"Привет, {name}!"


print(add(2, 3))                      # вызов: add, затем 5
print(add(2, b=5))                    # именованный аргумент доходит до функции
print(add.__name__, "|", add.__doc__)  # add | Складывает два числа.
print(greet("Аня"))                   # ['Привет, Аня!', 'Привет, Аня!', 'Привет, Аня!']
print(greet.__name__)                 # greet — wraps сохранил имя
```

### Частые ошибки

- **`wrapper` без `*args, **kwargs`.** Функция с другим числом аргументов
  падает `TypeError` — и виноват в этом декоратор, а не функция.
- **`functools.wraps` забыт.** Имя и докстринг подменяются на `wrapper`,
  справка и тесты видят чужое.
- **Вызов функции при декорировании.** `result = function()` вне `wrapper`
  выполняет функцию один раз на импорте, а потом возвращает застывший ответ.
- **`wrapper` не возвращён.** Имя функции становится `None`, и первый же вызов
  падает.
- **Перепутаны уровни.** Декоратор без параметра, применённый как `@cache(10)`,
  получит `10` вместо функции; наоборот — `@repeat` вместо `@repeat(3)` —
  получит функцию там, где ждёт число.

## Кэш и lru_cache

### Главное

Кэш — это словарь «аргументы → результат», который проверяется перед вызовом
функции: попали в словарь — не считаем, не попали — считаем и запоминаем.
Экономия огромная там, где функция вызывается с теми же аргументами (рекурсия
`fib` без кэша делает сотни тысяч вызовов там, где хватит пары десятков).

Размер кэша ограничен, поэтому нужно правило вытеснения. **LRU** (Least
Recently Used) выбрасывает запись, к которой дольше всего не обращались:

- хранилище — `OrderedDict`, он помнит порядок вставки;
- попадание в кэш: `move_to_end(key)` — запись становится «самой свежей»;
- переполнение: `popitem(last=False)` — уходит самый старый, тот, что в начале;
- `@functools.wraps` — по той же причине, что и в декораторе без кэша.

Отдельная забота — **ключ кэша**. Аргументы надо привести к сравнимому виду:
`f(1, 2)` и `f(1, b=2)` — это один и тот же вызов, а `f(1)` и `f(1, b=5)` — тоже
один, если `b` по умолчанию равен пяти. Поэтому ключ собирают не из `args` и
`kwargs` по отдельности, а через `inspect.signature(function).bind(*args,
**kwargs)` с `apply_defaults()`: получается отображение «имя параметра →
значение», одинаковое для всех форм записи одного вызова. Значения должны быть
хешируемыми — список в ключ не положить.

В стандартной библиотеке есть готовый `functools.lru_cache(maxsize=N)` — в
задании лаборатории им пользоваться нельзя, но знать о нём нужно.

### Пример

```python
import functools
import inspect
from collections import OrderedDict
from typing import Any, Callable, TypeVar

Function = TypeVar("Function", bound=Callable[..., Any])


def cache(max_size: int) -> Callable[[Function], Function]:
    """Декоратор: хранит результат для `max_size` самых свежих наборов аргументов."""

    def decorator(function: Function) -> Function:
        stored: "OrderedDict[tuple, Any]" = OrderedDict()
        signature = inspect.signature(function)

        def key_for(args: tuple, kwargs: dict) -> tuple:
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            return tuple(bound.arguments.items())

        @functools.wraps(function)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            key = key_for(args, kwargs)
            if key in stored:
                stored.move_to_end(key)          # обращались только что
                return stored[key]
            result = function(*args, **kwargs)
            stored[key] = result
            if len(stored) > max_size:
                stored.popitem(last=False)       # вытесняем самого старого
            return result

        return wrapper

    return decorator


computations: list[tuple] = []


@cache(100)
def fib(number: int) -> int:
    computations.append(number)
    if number < 2:
        return number
    return fib(number - 1) + fib(number - 2)


print(fib(25), len(computations))       # 75025 26 — посчитали по одному разу на аргумент
print(fib(25), len(computations))       # 75025 26 — попадание в кэш

calls: list[int] = []


@cache(2)
def identity(number: int) -> int:
    calls.append(number)
    return number


print(identity(1), identity(1), len(calls))     # 1 1 1
print(identity(2), identity(3), len(calls))     # 2 3 3 — кэш заполнен
print(identity(1), len(calls))                  # 1 4 — запись вытеснена: считаем снова
print(identity(3), len(calls))                  # 3 4 — а эта осталась
```

### Частые ошибки

- **Ключ только из `args`.** `f(1, b=2)` уходит в другую запись, чем `f(1, 2)`,
  и кэш перестаёт экономить; хуже — `kwargs` вообще теряются, если ключ собран
  как `args`.
- **Значения по умолчанию не учтены.** `f(1)` и `f(1, b=5)` становятся разными
  записями, хотя считают одно и то же; лечится `apply_defaults()`.
- **`move_to_end` при попадании забыт.** Кэш превращается в FIFO: вытесняется
  не тот, к кому давно не обращались, а вставленный раньше всех.
- **`popitem()` без `last=False`.** Выбрасывается самая свежая запись — ровно
  наоборот к LRU.
- **Проверка кэша после вызова.** Строка `result = function(...)` перед
  `if key in stored` делает всю затею бесполезной.
- **Нехешируемые аргументы.** Список или словарь в ключе — `TypeError:
  unhashable type`; такие аргументы кэшируют иначе (например, через кортеж).
- **`functools.wraps` забыт.** Обычная функция превращается в `wrapper` без
  имени и докстринга.

## Итоги занятия

| Что | Как | Ловушка |
|---|---|---|
| Вход в блок | `__enter__` возвращает то, что после `as` | вернуть `None` — и `as` бесполезен |
| Выход из блока | `__exit__(exc_type, exc_val, exc_tb)` | `True` подавляет исключение |
| Гарантия уборки | код уборки в `__exit__` | уборка «в конце блока» пропускается при исключении |
| Возврат состояния | сохранить в `__enter__`, вернуть в `__exit__` | перепутанный порядок теряет перехваченный вывод |
| Декоратор | функция → функция, `functools.wraps` | без `wraps` теряются имя и докстринг |
| Декоратор с параметром | три уровня вложенности | путаница «где параметр, где функция» |
| Ключ кэша | `signature.bind(...).apply_defaults()` | иначе одна и та же форма вызова считается дважды |
| Вытеснение LRU | `move_to_end` при попадании, `popitem(last=False)` | без `move_to_end` это FIFO |

Правило занятия: обёртка отвечает за то, что происходит **вокруг** чужого
кода. `with` гарантирует уборку, потому что `__exit__` вызывается всегда;
декоратор — обычная функция, и всё, что в нём забыто (`*args`, `wraps`,
`move_to_end`), тихо меняет поведение обёрнутого кода.

## Источник

Задания лаборатории [`lab1-python-intro`](https://github.com/Eldervald/isit_ml_labs/tree/0da67c6/lab1-python-intro):
[`i_context_manager`](https://github.com/Eldervald/isit_ml_labs/tree/0da67c6/lab1-python-intro/i_context_manager),
[`m_lru_cache`](https://github.com/Eldervald/isit_ml_labs/tree/0da67c6/lab1-python-intro/m_lru_cache);
документация Python:
[инструкция `with`](https://docs.python.org/3/reference/compound_stmts.html#the-with-statement),
[`functools.wraps`](https://docs.python.org/3/library/functools.html#functools.wraps),
[`OrderedDict`](https://docs.python.org/3/library/collections.html#collections.OrderedDict).
