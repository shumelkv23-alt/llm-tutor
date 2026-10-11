# Группировка и сводные таблицы

groupby по схеме split → apply → combine, агрегаты и agg, crosstab и pivot_table.

## split → apply → combine

### Группировка: groupby

#### Зачем

Самые содержательные вопросы к данным — сравнения групп. Уходят ли чаще клиенты с международным тарифом? Сколько минут в среднем говорят в каждом штате? `groupby` разбивает таблицу на группы по значению ключа и считает по каждой группе одно и то же.

#### Главное

Группировка работает по схеме **split → apply → combine**:

1. **split** — строки делятся на группы по значению ключевого столбца (или нескольких);
2. **apply** — к каждой группе применяется функция, обычно агрегат: `mean`, `sum`, `count`, `size`;
3. **combine** — результаты собираются в новую таблицу или Series, где индекс — значения ключа.

- `df.groupby("key")["col"].mean()` — среднее `col` в каждой группе.
- Группировать можно по нескольким столбцам: `groupby(["a", "b"])` — тогда индекс результата составной.
- **`size()`** считает строки в группе, **`count()`** — непустые значения.
- `as_index=False` или `.reset_index()` вернут ключ из индекса в обычный столбец.
- Без агрегата `groupby` сам по себе ничего не считает: это «отложенное» разбиение.

#### Пример

```python
import pandas as pd

df = pd.DataFrame({
    "International plan": ["No", "No", "Yes", "No", "Yes", "No", "Yes", "No"],
    "Total day minutes": [265.1, 161.6, 243.4, 299.4, 166.7, 223.4, 218.2, 157.0],
    "Churn": [False, False, True, False, True, False, False, False],
})

print(df.groupby("International plan")["Churn"].mean())
# No     0.000000
# Yes    0.666667 — с международным тарифом уходят заметно чаще

print(df.groupby("International plan")["Total day minutes"].mean().round(1))
print(df.groupby("International plan").size())   # No 5, Yes 3

by_two = df.groupby(["International plan", "Churn"], as_index=False)["Total day minutes"].mean()
print(by_two)
```

#### Частые ошибки

- **Ждать результат без агрегата:** `df.groupby("a")` — это объект группировки, а не таблица.
- **Путать `size` и `count`:** `size` считает все строки, `count` пропускает `NaN` (и считается по каждому столбцу).
- **Делать выводы по маленьким группам.** Доля оттока 100% в группе из двух клиентов ничего не доказывает. Смотрите и размер группы.

#### Источник

Группировка в [теме 1 mlcourse.ai](https://mlcourse.ai/book/topic01/topic01_pandas_data_analysis.html).

## Агрегаты и agg

### Агрегации

#### Зачем

Одного среднего по группе часто мало: хочется сразу видеть и среднее, и медиану, и число объектов, причём для разных столбцов — разные функции. Метод `agg` собирает такую сводку за один вызов.

#### Главное

- Простые агрегаты вызываются прямо: `sum`, `mean`, `median`, `min`, `max`, `std`, `count`, `nunique`.
- **`agg(["mean", "max"])`** — несколько функций к выбранным столбцам: в результате по столбцу на каждую функцию.
- **`agg({"col1": "mean", "col2": ["min", "max"]})`** — словарь «столбец → функция(и)»: каждому столбцу своё.
- **Именованная агрегация** даёт понятные имена столбцов результата: `agg(avg_minutes=("Total day minutes", "mean"), clients=("Churn", "size"))`.
- Агрегат принимает и свою функцию: `agg(lambda s: s.max() - s.min())`.
- Без `groupby` метод `agg` сворачивает всю таблицу.

#### Пример

```python
import pandas as pd

df = pd.DataFrame({
    "State": ["KS", "OH", "KS", "OH", "OH", "KS"],
    "Total day minutes": [265.1, 161.6, 243.4, 299.4, 166.7, 223.4],
    "Customer service calls": [1, 1, 0, 2, 4, 0],
    "Churn": [False, False, True, False, True, False],
})

print(df.groupby("State")["Total day minutes"].agg(["mean", "max"]).round(1))

summary = df.groupby("State").agg({
    "Total day minutes": "median",
    "Customer service calls": ["min", "max"],
})
print(summary)

named = df.groupby("State").agg(
    clients=("Churn", "size"),
    churn_rate=("Churn", "mean"),
    calls_range=("Customer service calls", lambda s: s.max() - s.min()),
)
print(named)
```

#### Частые ошибки

- **Получить столбцы с двухуровневыми именами** после `agg` со списками и не понять, как к ним обратиться. Используйте именованную агрегацию — имена будут плоскими.
- **Путать `count` и `size`** в агрегации: `count` не считает пропуски.
- **Агрегировать текстовый столбец средним** — будет ошибка. Для текста подходят `nunique`, `first` и `size`.

#### Источник

Агрегации после группировки в [теме 1 mlcourse.ai](https://mlcourse.ai/book/topic01/topic01_pandas_data_analysis.html).

## crosstab и pivot_table

### Сводные таблицы: crosstab и pivot_table

#### Зачем

Когда интересует связь **двух** признаков сразу — тарифа и оттока, штата и числа звонков, — одномерной группировки мало. Нужна двумерная таблица: по строкам значения одного признака, по столбцам — другого. `crosstab` считает в ней частоты, `pivot_table` — любые агрегаты.

#### Главное

- **`pd.crosstab(df["a"], df["b"])`** — таблица сопряжённости: сколько строк с каждой парой значений.
- **`normalize`** превращает частоты в доли: `"index"` — доли внутри каждой строки, `"columns"` — внутри столбца, `True` — от всей таблицы.
- **`margins=True`** добавляет итоги по строкам и столбцам (строка и столбец `All`).
- **`df.pivot_table(values=..., index=..., columns=..., aggfunc=...)`** — сводка агрегата: например, среднее число минут для каждой пары «тариф × отток».
- По умолчанию `pivot_table` считает **среднее**. Другую функцию задают через `aggfunc` (`"sum"`, `"count"`, `"median"` или список).
- По сути `pivot_table` — это `groupby` по двум ключам, развёрнутый в таблицу.

#### Пример

```python
import pandas as pd

df = pd.DataFrame({
    "International plan": ["No", "No", "Yes", "No", "Yes", "No", "Yes", "No"],
    "Voice mail plan": ["Yes", "No", "No", "No", "Yes", "No", "No", "Yes"],
    "Total day minutes": [265.1, 161.6, 243.4, 299.4, 166.7, 223.4, 218.2, 157.0],
    "Churn": [False, False, True, False, True, False, False, False],
})

print(pd.crosstab(df["International plan"], df["Churn"]))
print(pd.crosstab(df["International plan"], df["Churn"], normalize="index").round(2))
print(pd.crosstab(df["International plan"], df["Churn"], margins=True))

table = df.pivot_table(
    values="Total day minutes",
    index="International plan",
    columns="Voice mail plan",
    aggfunc="mean",
)
print(table.round(1))
```

#### Частые ошибки

- **Сравнивать абсолютные частоты групп разного размера.** Для вывода «где чаще уходят» нужен `normalize="index"`.
- **Ждать от `pivot_table` суммы.** По умолчанию считается среднее, сумму нужно задать в `aggfunc="sum"`.
- **Путать `pivot_table` и `pivot`.** `pivot` только переставляет данные и падает на повторяющихся парах ключей, а `pivot_table` их агрегирует.

#### Источник

Сводные таблицы в [теме 1 mlcourse.ai](https://mlcourse.ai/book/topic01/topic01_pandas_data_analysis.html).
