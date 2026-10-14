# Comprehensions

Включение (comprehension) — это цикл, который собирает результат и при этом
остаётся выражением. В обработке записей лога почти каждая функция сводится к
одной строке включения: отобрать, заменить, собрать уникальные.

## Зачем

После занятия вы сможете:

- читать и писать списочные, множественные и словарные включения;
- различать фильтр (`if` после `for`) и выбор значения (`if`/`else` до `for`);
- понимать, чем множество отличается от списка в результате: уникальность
  против порядка;
- собирать словари включением и объяснять, почему идти надо по записи, а не по
  набору ключей;
- решать, когда включение читается лучше цикла, а когда — хуже.

## Списковые включения

### Главное

У включения три части, и порядок в них значим:

- **источник** — `for r in records`: по очереди даёт элементы;
- **фильтр** — `if r["EventTime"] > ts` **после** `for`: решает, попадёт ли
  элемент в результат;
- **выражение** — то, что стоит **до** `for`: из чего собирается результат.
  Здесь же живёт тернарный оператор:
  `r["RegionID"] if r["RegionID"] is not None else DEFAULT_REGION_ID`.

Отсюда главная путаница: `[r["RegionID"] for r in records if r["RegionID"] is
not None]` **выбрасывает** записи без региона, а `[r["RegionID"] if
r["RegionID"] is not None else DEFAULT_REGION_ID for r in records]` **оставляет
все**, подставляя значение по умолчанию. Одно и то же `if`, поставленное в
разные места, отвечает на разные вопросы — «кого взять» и «что вернуть».

Форма результата задаёт скобка: `[...]` — список (порядок сохраняется,
повторы остаются), `{...}` — множество (повторы исчезают, порядок не
обещается), `(...)` — генератор, который ничего не считает заранее и годится
для `sum()`, `max()`, `any()`. Два `for` подряд — это вложенные циклы в том же
порядке записи.

### Пример

```python
DEFAULT_REGION_ID = 100500

records = [
    {"EventID": 1, "EventTime": 100, "UserID": 10, "PageID": 5, "RegionID": None, "DeviceType": "Safari"},
    {"EventID": 2, "EventTime": 200, "UserID": 10, "PageID": 7, "RegionID": 3, "DeviceType": "Firefox"},
    {"EventID": 3, "EventTime": 300, "UserID": 11, "PageID": 5, "RegionID": 0, "DeviceType": "Safari"},
]


def get_unique_page_ids(records: list[dict]) -> set[int]:
    """Уникальные страницы, которые были в логе."""
    return {r["PageID"] for r in records}


def get_events_by_device_type(records: list[dict], device_type: str) -> list[dict]:
    """События с указанного устройства — порядок записей сохраняется."""
    return [r for r in records if r["DeviceType"] == device_type]


def get_region_ids_with_none_replaces_by_default(records: list[dict]) -> list[int]:
    """Регионы всех записей; у записей без региона — значение по умолчанию."""
    return [
        r["RegionID"] if r["RegionID"] is not None else DEFAULT_REGION_ID
        for r in records
    ]


print(sorted(get_unique_page_ids(records)))                  # [5, 7]
print([r["EventID"] for r in get_events_by_device_type(records, "Safari")])  # [1, 3]
print(get_region_ids_with_none_replaces_by_default(records))  # [100500, 3, 0]
print([r["PageID"] for r in records if r["EventTime"] > 100])  # [7, 5]
```

### Частые ошибки

- **Фильтр вместо тернарного оператора.** `if` после `for` выкидывает записи;
  если по условию запись должна остаться с другим значением, `if`/`else`
  пишут до `for`.
- **Множество там, где нужен порядок.** Результат-множество удобен для
  «уникальных страниц», но по нему нельзя сказать «в каком порядке это было»;
  там, где порядок важен (события устройства), результат — список.
- **Ожидание, что генератор можно перебрать дважды.** `(...)` исчерпывается
  после первого прохода; если результат нужен дважды, собирают список.
- **`map` и `filter` вместо включения.** Они читаются хуже и требуют `list(...)`
  вокруг; включение делает то же самое одной конструкцией.
- **Включение на пять условий.** Когда условий много, цикл читается лучше —
  длинное включение приходится разбирать по частям.

## Словарные включения

### Главное

Словарное включение собирает пары: `{key: value for key, value in r.items()}`.
Обход `r.items()` даёт и ключ, и значение сразу; обход самого словаря
(`for key in r`) даёт только ключи — `.keys()` писать не нужно.

Правило, из-за которого эта задача и попала в лабораторию: **идти надо по
записи, а не по набору ключей**. `{key: r[key] for key in keys}` выглядит
естественнее, но падает с `KeyError`, если в наборе есть ключ, которого в
записи нет, — а набор ключей приходит снаружи и никто не обещал, что он
согласован с записью. Правильная форма — `{key: value for key, value in
r.items() if key in keys}`: лишние ключи набора просто не встречаются.

Три задачи этого раздела — три разных вопроса:

- **отобрать** пары, чьи ключи в наборе: `{k: v for k, v in r.items() if k in keys}`;
- **оставить все ключи**, а чужие значения заменить на `None`:
  `{k: v if k in keys else None for k, v in r.items()}`;
- **вернуть только ключи** — это уже множество: `{k for k in r if k in keys}`.

### Пример

```python
record = {
    "EventID": 1,
    "EventTime": None,
    "UserID": 10,
    "PageID": 5,
    "RegionID": None,
    "DeviceType": "Safari",
}


def get_keys_where_value_is_not_none(r: dict) -> list[str]:
    """Ключи записи, у которых значение определено."""
    return [key for key, value in r.items() if value is not None]


def get_record_with_key_in_keys(r: dict, keys: set[str]) -> dict:
    """Только те пары записи, чьи ключи есть в наборе."""
    return {key: value for key, value in r.items() if key in keys}


def get_record_with_none_if_key_not_in_keys(r: dict, keys: set[str]) -> dict:
    """Все ключи записи; значения чужих ключей заменены на None."""
    return {key: value if key in keys else None for key, value in r.items()}


def get_keys_if_key_in_keys(r: dict, keys: set[str]) -> set[str]:
    """Ключи записи, которые есть в наборе."""
    return {key for key in r if key in keys}


print(get_keys_where_value_is_not_none(record))
print(get_record_with_key_in_keys(record, {"EventID", "UserID"}))
print(get_record_with_none_if_key_not_in_keys(record, {"EventID", "UserID"}))
print(get_keys_if_key_in_keys(record, {"EventID", "UserID", "SomeField"}))

try:
    {key: record[key] for key in {"EventID", "SomeField"}}
except KeyError as exc:
    print("обход набора ключей падает:", exc)
```

### Частые ошибки

- **Обход набора ключей.** `{key: r[key] for key in keys}` падает на ключе,
  которого в записи нет, и порядок пар берёт из набора, а не из записи.
- **Потеря ключей в `get_record_with_none_if_key_not_in_keys`.** Заменить
  значение на `None` — не то же самое, что выбросить пару: в результате
  остаются **все** ключи записи.
- **Список вместо множества.** `[key for key in r if key in keys]` вернёт
  список, а сравнение с множеством в тесте — это сравнение разных типов.
- **`r.keys()` там, где достаточно `r`.** Обход словаря уже идёт по ключам.
- **Проверка значения через `if value`.** Ноль и пустая строка — тоже
  значения; «не определено» — это именно `None`, и проверяют его через
  `is not None`.

## Итоги занятия

| Конструкция | Что делает |
|---|---|
| `[expr for r in records]` | список: порядок сохранён, повторы остаются |
| `[expr for r in records if cond]` | фильтр: часть записей не попадает |
| `[a if cond else b for r in records]` | выбор значения для каждой записи |
| `{r["PageID"] for r in records}` | множество: только уникальные |
| `{k: v for k, v in r.items() if k in keys}` | словарь: отобрать пары по ключам |
| `{k: v if k in keys else None for k, v in r.items()}` | словарь со всеми ключами |
| `sum(x for x in ...)` | генератор: считается на ходу, один проход |

Правило занятия: сначала решите, что означает условие — «кого взять» или «что
вернуть», — и только потом ставьте `if` на его место: после `for` или до него.
Итерируйте по тому, что есть, а не по тому, что пришло снаружи.

## Источник

Задание лаборатории [`lab1-python-intro`](https://github.com/Eldervald/isit_ml_labs/tree/0da67c6/lab1-python-intro):
[`g_comprehensions`](https://github.com/Eldervald/isit_ml_labs/tree/0da67c6/lab1-python-intro/g_comprehensions);
документация Python:
[включения](https://docs.python.org/3/tutorial/datastructures.html#list-comprehensions),
[словари](https://docs.python.org/3/tutorial/datastructures.html#dictionaries).
