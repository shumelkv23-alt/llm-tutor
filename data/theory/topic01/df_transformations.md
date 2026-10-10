# Трансформации DataFrame

## Зачем

Сырые столбцы редко отвечают на вопрос напрямую. Часто полезнее суммарные минуты, чем минуты днём, вечером и ночью по отдельности, или признак «звонил в поддержку больше трёх раз». Новые столбцы из старых — **вычисляемые признаки** — основа и анализа, и будущих моделей. Ненужные столбцы, наоборот, удаляют.

## Главное

- **Присваивание** `df["new"] = выражение` добавляет столбец в конец таблицы или перезаписывает существующий.
- **`df.insert(позиция, "имя", значения)`** вставляет столбец в нужное место. Меняет таблицу на месте.
- **`df.assign(new=...)`** возвращает **новую** таблицу с добавленными столбцами. Удобно в цепочках вызовов.
- **`df.drop(columns=[...])`** удаляет столбцы, **`df.drop(index=[...])`** — строки. Возвращает новую таблицу.
- **`df.rename(columns={"старое": "новое"})`** переименовывает выбранные столбцы, а присваивание `df.columns = [...]` заменяет все имена сразу (список должен быть той же длины).
- **`pd.get_dummies(df["col"])`** превращает категориальный столбец в набор столбцов-флагов 0/1 — **one-hot кодирование**, которое понадобится моделям.
- Признак-флаг получают из условия: `df["many_calls"] = df["calls"] > 3`.

## Пример

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

## Частые ошибки

- **Ждать, что `drop`, `rename` и `assign` изменят таблицу.** Они возвращают новую таблицу — присвойте её.
- **Вызвать `insert` с занятым именем столбца.** Будет ошибка: `insert` не перезаписывает.
- **Удалить строки и забыть про индекс.** После `drop(index=...)` в индексе остаются «дыры», а `iloc` и `loc` начинают расходиться.

## Источник

Добавление и удаление столбцов в [теме 1 mlcourse.ai](https://mlcourse.ai/book/topic01/topic01_pandas_data_analysis.html).
