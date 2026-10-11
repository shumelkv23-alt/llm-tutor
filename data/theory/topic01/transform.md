# Сортировка и преобразование столбцов

Сортировка, apply/map/replace, новые и удалённые столбцы.

## Сортировка

### Сортировка

#### Зачем

Сортировка отвечает на вопросы «кто больше всех…» и «какие крайние значения». Она помогает найти выбросы, увидеть порядок и подготовить таблицу к выводу. В pandas две операции: по значениям столбцов и по индексу.

#### Главное

- **`df.sort_values("col")`** сортирует строки по значениям столбца, по умолчанию по возрастанию.
- **`ascending=False`** — по убыванию.
- **Несколько столбцов:** `sort_values(["a", "b"], ascending=[True, False])`. Сначала по `a`, а при равных `a` — по `b`.
- **`df.sort_index()`** сортирует по индексу строк. Например, вернёт исходный порядок после сортировки по значениям.
- **`nlargest(n, "col")`** и **`nsmallest(n, "col")`** — короткий способ взять верхние и нижние `n` строк.
- Сортировка **возвращает новую таблицу**, исходная не меняется. Индекс строк переезжает вместе со строками.
- Пропуски по умолчанию уходят в конец (`na_position="last"`).

#### Пример

```python
import pandas as pd

df = pd.DataFrame({
    "State": ["KS", "OH", "NJ", "OH", "OK"],
    "Total day charge": [45.07, 27.47, 41.38, 50.90, 28.34],
    "Customer service calls": [1, 1, 0, 2, 3],
})

print(df.sort_values("Total day charge", ascending=False).head(3))
print(df.sort_values(["State", "Total day charge"], ascending=[True, False]))
print(df.nlargest(2, "Customer service calls")["State"].tolist())  # ['OK', 'OH']

top = df.sort_values("Total day charge")
print(top.index.tolist())          # [1, 4, 2, 0, 3] — индекс уехал со строками
print(top.sort_index().index.tolist())  # [0, 1, 2, 3, 4]
```

#### Частые ошибки

- **Вызвать `sort_values` и не сохранить результат.** Таблица останется в прежнем порядке.
- **Ждать нумерации `0, 1, 2…` после сортировки.** Индекс остаётся прежним; чтобы пронумеровать заново, нужен `reset_index(drop=True)`.
- **Передавать одно `ascending=False` при сортировке по нескольким столбцам** и ждать разных направлений. Нужен список `[True, False]`.

#### Источник

Сортировка таблицы в [теме 1 mlcourse.ai](https://mlcourse.ai/book/topic01/topic01_pandas_data_analysis.html).

## apply, map и replace

### apply: функции к осям

#### Зачем

Иногда нужного преобразования нет среди готовых векторных операций: например, разобрать строку особого формата или посчитать что-то по нескольким столбцам сразу. Тогда функцию применяют к каждому элементу, столбцу или строке. Для этого есть `map` и `apply`. Это гибкий инструмент, но он медленнее векторных операций.

#### Главное

- **`s.map(func или словарь)`** — к каждому элементу Series. Словарь удобен для перекодировки: `{"Yes": 1, "No": 0}`.
- **`s.apply(func)`** — тоже поэлементно для Series. Функция может быть сложнее, чем `lambda`.
- **`df.apply(func)`** по умолчанию (`axis=0`) вызывает функцию **для каждого столбца**, а с `axis=1` — **для каждой строки**. Строка приходит в функцию как Series с именами столбцов в индексе.
- Если есть векторная альтернатива (`df["a"] * 2`, `np.where`, `.str`-методы), она почти всегда быстрее `apply`.
- Значение, которого нет в словаре `map`, превращается в `NaN`.

#### Пример

```python
import pandas as pd

df = pd.DataFrame({
    "State": ["KS", "OH", "NJ"],
    "International plan": ["No", "Yes", "No"],
    "Total day minutes": [265.1, 161.6, 243.4],
    "Total eve minutes": [197.4, 195.5, 121.2],
})

df["intl"] = df["International plan"].map({"Yes": 1, "No": 0})
print(df["intl"].tolist())                     # [0, 1, 0]

print(df[["Total day minutes", "Total eve minutes"]].apply(max))  # максимум по каждому столбцу

df["share_day"] = df.apply(
    lambda row: row["Total day minutes"] / (row["Total day minutes"] + row["Total eve minutes"]),
    axis=1,
)
print(df["share_day"].round(2).tolist())

# То же самое векторно — быстрее и короче:
fast = df["Total day minutes"] / (df["Total day minutes"] + df["Total eve minutes"])
print((fast == df["share_day"]).all())        # True
```

#### Частые ошибки

- **Забыть `axis=1`**, когда функция ждёт строку. Тогда в неё придёт столбец, и обращение `row["Total day minutes"]` упадёт.
- **Использовать `apply` для простой арифметики** — работает в десятки раз медленнее векторного выражения.
- **Не заметить `NaN` после `map`** со словарём, в котором есть не все значения.

#### Источник

Методы apply и map в [теме 1 mlcourse.ai](https://mlcourse.ai/book/topic01/topic01_pandas_data_analysis.html).

## Новые и удалённые столбцы

### Трансформации DataFrame

#### Зачем

Сырые столбцы редко отвечают на вопрос напрямую. Часто полезнее суммарные минуты, чем минуты днём, вечером и ночью по отдельности, или признак «звонил в поддержку больше трёх раз». Новые столбцы из старых — **вычисляемые признаки** — основа и анализа, и будущих моделей. Ненужные столбцы, наоборот, удаляют.

#### Главное

- **Присваивание** `df["new"] = выражение` добавляет столбец в конец таблицы или перезаписывает существующий.
- **`df.insert(позиция, "имя", значения)`** вставляет столбец в нужное место. Меняет таблицу на месте.
- **`df.assign(new=...)`** возвращает **новую** таблицу с добавленными столбцами. Удобно в цепочках вызовов.
- **`df.drop(columns=[...])`** удаляет столбцы, **`df.drop(index=[...])`** — строки. Возвращает новую таблицу.
- **`df.rename(columns={"старое": "новое"})`** переименовывает выбранные столбцы, а присваивание `df.columns = [...]` заменяет все имена сразу (список должен быть той же длины).
- **`pd.get_dummies(df["col"])`** превращает категориальный столбец в набор столбцов-флагов 0/1 — **one-hot кодирование**, которое понадобится моделям.
- Признак-флаг получают из условия: `df["many_calls"] = df["calls"] > 3`.

#### Пример

```python
import pandas as pd

df = pd.DataFrame({
    "Total day calls": [110, 123, 114],
    "Total eve calls": [99, 103, 110],
    "Total night calls": [91, 103, 104],
    "Customer service calls": [1, 4, 0],
    "Area code": [415, 415, 510],
})

total = df["Total day calls"] + df["Total eve calls"] + df["Total night calls"]
df.insert(loc=3, column="Total calls", value=total)
print(df.columns.tolist())

df["many_service_calls"] = df["Customer service calls"] > 3
df = df.drop(columns=["Area code"])
print(df[["Total calls", "many_service_calls"]])

renamed = df.assign(day_share=lambda d: d["Total day calls"] / d["Total calls"]).rename(
    columns={"Customer service calls": "service_calls"}
)
print(renamed[["service_calls", "day_share"]].round(2))
print("Area code" in df.columns)   # False
```

#### Частые ошибки

- **Ждать, что `drop`, `rename` и `assign` изменят таблицу.** Они возвращают новую таблицу — присвойте её.
- **Вызвать `insert` с занятым именем столбца.** Будет ошибка: `insert` не перезаписывает.
- **Удалить строки и забыть про индекс.** После `drop(index=...)` в индексе остаются «дыры», а `iloc` и `loc` начинают расходиться.

#### Источник

Добавление и удаление столбцов в [теме 1 mlcourse.ai](https://mlcourse.ai/book/topic01/topic01_pandas_data_analysis.html).
