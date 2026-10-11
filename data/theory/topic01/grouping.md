# Группировка и сводные таблицы

«Чем ушедшие клиенты отличаются от оставшихся?», «В каких штатах отток
выше?» — это вопросы о группах. Группировка и сводные таблицы отвечают на них
одной строкой кода и дают основу для всех выводов в анализе данных.

## Зачем

После занятия вы сможете:

- объяснить схему split → apply → combine и записать её через `groupby`;
- посчитать несколько статистик по группам сразу через `agg`;
- построить таблицу сопряжённости `crosstab` и сводную таблицу `pivot_table`;
- читать доли в сводных таблицах и делать по ним выводы.

## split → apply → combine

### Главное

`df.groupby("ключ")` работает в три шага:

1. **split** — строки делятся на группы по значению ключа (все клиенты с
   международным тарифом — одна группа, без него — другая);
2. **apply** — к каждой группе применяется функция: среднее, сумма, размер;
3. **combine** — результаты собираются в новую Series или таблицу, где
   **индекс — значения ключа**.

Сам `df.groupby(...)` ничего не считает: он возвращает объект «группировка».
Расчёт происходит, когда к нему применяют агрегат:

- `df.groupby("key")["col"].mean()` — один столбец, результат — Series;
- `df.groupby("key")[["a", "b"]].mean()` — несколько столбцов, результат —
  DataFrame;
- `df.groupby("key").size()` — размер каждой группы.

Ключей может быть несколько: `groupby(["State", "Churn"])` — тогда индекс
результата составной (MultiIndex). Вернуть ключи в обычные столбцы помогает
`reset_index()` или параметр `as_index=False`.

### Пример

```python
import pandas as pd

df = pd.DataFrame(
    {
        "International plan": ["No", "No", "No", "Yes", "Yes", "Yes", "No", "No"],
        "Total day minutes": [265.1, 161.6, 243.4, 299.4, 166.7, 223.4, 218.2, 157.0],
        "Customer service calls": [1, 4, 0, 2, 3, 0, 4, 5],
        "Churn": [False, False, False, False, True, False, True, True],
    }
)

# Доля ушедших в каждой группе: среднее по логическому столбцу.
print(df.groupby("International plan")["Churn"].mean())

print(df.groupby("Churn")[["Total day minutes", "Customer service calls"]].mean().round(1))
print(df.groupby("International plan").size())
print(df.groupby("Churn", as_index=False)["Customer service calls"].median())
```

### Частые ошибки

- **Ждать результат от самого `groupby`.** Без агрегата получится объект
  `DataFrameGroupBy`, а не таблица.
- **Агрегат по нечисловым столбцам.** `df.groupby("key").mean()` по всей
  таблице упадёт, если в ней есть строковые столбцы. Выберите числовые
  столбцы явно.
- **Потерять ключ.** После группировки ключ — это индекс, а не столбец.
  `result["International plan"]` не найдёт его; нужен `result.index` или
  `reset_index()`.

## Агрегаты и agg

### Главное

Готовые агрегаты: `mean`, `median`, `sum`, `min`, `max`, `std`, `count`
(непустые), `size` (все строки), `nunique`. Когда нужно несколько статистик
сразу, используют `agg`:

- `agg(["mean", "max"])` — список функций для выбранных столбцов;
- `agg({"col1": "mean", "col2": ["min", "max"]})` — свои функции каждому
  столбцу;
- именованные агрегаты: `agg(avg_calls=("Customer service calls", "mean"))` —
  сразу задают имя столбца результата.

Функцию можно передать и как свою: `agg(lambda s: s.max() - s.min())`, но
встроенные имена в кавычках считаются быстрее.

`count` и `size` различаются пропусками: `size` считает все строки группы,
`count` — только непустые значения столбца.

### Пример

```python
import pandas as pd

df = pd.DataFrame(
    {
        "Churn": [False, False, False, False, True, False, True, True],
        "Total day minutes": [265.1, 161.6, 243.4, 299.4, 166.7, 223.4, 218.2, 157.0],
        "Customer service calls": [1, 4, 0, 2, 3, 0, 4, 5],
    }
)
grouped = df.groupby("Churn")

print(grouped["Total day minutes"].agg(["mean", "min", "max"]).round(1))
print(grouped.agg({"Total day minutes": "median", "Customer service calls": ["mean", "max"]}))
print(
    grouped.agg(
        clients=("Total day minutes", "size"),
        avg_calls=("Customer service calls", "mean"),
    )
)
```

### Частые ошибки

- **Перепутать `count` и `size`.** При пропусках они дают разные числа. Число
  клиентов в группе — `size`.
- **Словарь с несуществующим столбцом.** `agg({"calls": "mean"})` упадёт с
  `KeyError`, если столбец называется `Customer service calls`.
- **Многоуровневые названия.** Список функций для столбца даёт двухуровневые
  названия столбцов результата. Если они мешают — используйте именованные
  агрегаты.

## crosstab и pivot_table

### Главное

**Таблица сопряжённости** `pd.crosstab(строки, столбцы)` считает, сколько
раз встречается каждая пара значений двух признаков. Параметры:

- `normalize="index"` — доли внутри каждой строки (строка в сумме даёт 1);
- `normalize="columns"` — доли внутри каждого столбца;
- `margins=True` — добавить итоги «All».

Например, `pd.crosstab(df["International plan"], df["Churn"],
normalize="index")` показывает долю ушедших среди клиентов с международным
тарифом и без него. В полном датасете mlcourse.ai с тарифом уходят около 42%
клиентов, без него — около 11%: тариф — сильный признак оттока.

**Сводная таблица** `df.pivot_table(values=…, index=…, columns=…,
aggfunc=…)` — обобщение: в ячейке не количество, а любая статистика столбца
`values` для пары (строка, столбец). По умолчанию `aggfunc="mean"`.

### Пример

```python
import pandas as pd

df = pd.DataFrame(
    {
        "International plan": ["No", "No", "No", "Yes", "Yes", "Yes", "No", "No"],
        "Voice mail plan": ["Yes", "No", "No", "No", "No", "Yes", "Yes", "No"],
        "Total day minutes": [265.1, 161.6, 243.4, 299.4, 166.7, 223.4, 218.2, 157.0],
        "Churn": [False, False, False, False, True, False, True, True],
    }
)

print(pd.crosstab(df["International plan"], df["Churn"]))
print(pd.crosstab(df["International plan"], df["Churn"], normalize="index").round(2))
print(pd.crosstab(df["International plan"], df["Churn"], margins=True))

print(
    df.pivot_table(
        values="Total day minutes",
        index="International plan",
        columns="Voice mail plan",
        aggfunc="mean",
    ).round(1)
)
```

### Частые ошибки

- **Не та нормировка.** «Какая доля клиентов с тарифом ушла» — это
  `normalize="index"`, если тариф — строки. `normalize="columns"` ответит на
  другой вопрос: «какая доля ушедших была с тарифом».
- **Пустые ячейки.** Если сочетания значений нет в данных, в `pivot_table`
  будет `NaN`. Это не ошибка, а отсутствие таких клиентов.
- **Выводы по маленьким группам.** Доля 100% в группе из двух клиентов ничего
  не значит. Смотрите и на `size` групп.

## Итоги занятия

| Что | Что делает |
|---|---|
| `df.groupby("key")["col"].mean()` | статистика по группам — Series |
| `df.groupby("key")[["a", "b"]].agg([...])` | несколько статистик по группам |
| `agg(name=("col", "func"))` | именованный агрегат |
| `size()` / `count()` | все строки группы / непустые значения |
| `pd.crosstab(a, b, normalize="index")` | частоты пар, доли по строкам |
| `df.pivot_table(values, index, columns, aggfunc)` | сводная таблица со статистикой |

Схема для любого вопроса о группах: выбрать ключ → выбрать столбцы → выбрать
статистику.

## Источник

[mlcourse.ai, тема 1: первичный анализ данных с Pandas](https://mlcourse.ai/book/topic01/topic01_pandas_data_analysis.html)
и документация pandas: [Group by: split-apply-combine](https://pandas.pydata.org/docs/user_guide/groupby.html),
[Reshaping and pivot tables](https://pandas.pydata.org/docs/user_guide/reshaping.html).
