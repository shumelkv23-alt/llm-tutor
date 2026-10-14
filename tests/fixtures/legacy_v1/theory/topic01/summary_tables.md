# Сводные таблицы: crosstab и pivot_table

## Зачем

Когда интересует связь **двух** признаков сразу — тарифа и оттока, штата и числа звонков, — одномерной группировки мало. Нужна двумерная таблица: по строкам значения одного признака, по столбцам — другого. `crosstab` считает в ней частоты, `pivot_table` — любые агрегаты.

## Главное

- **`pd.crosstab(df["a"], df["b"])`** — таблица сопряжённости: сколько строк с каждой парой значений.
- **`normalize`** превращает частоты в доли: `"index"` — доли внутри каждой строки, `"columns"` — внутри столбца, `True` — от всей таблицы.
- **`margins=True`** добавляет итоги по строкам и столбцам (строка и столбец `All`).
- **`df.pivot_table(values=..., index=..., columns=..., aggfunc=...)`** — сводка агрегата: например, среднее число минут для каждой пары «тариф × отток».
- По умолчанию `pivot_table` считает **среднее**. Другую функцию задают через `aggfunc` (`"sum"`, `"count"`, `"median"` или список).
- По сути `pivot_table` — это `groupby` по двум ключам, развёрнутый в таблицу.

## Пример

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

## Частые ошибки

- **Сравнивать абсолютные частоты групп разного размера.** Для вывода «где чаще уходят» нужен `normalize="index"`.
- **Ждать от `pivot_table` суммы.** По умолчанию считается среднее, сумму нужно задать в `aggfunc="sum"`.
- **Путать `pivot_table` и `pivot`.** `pivot` только переставляет данные и падает на повторяющихся парах ключей, а `pivot_table` их агрегирует.

## Источник

Сводные таблицы в [теме 1 mlcourse.ai](https://mlcourse.ai/book/topic01/topic01_pandas_data_analysis.html).
