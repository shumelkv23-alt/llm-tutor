# Булева индексация

## Зачем

Многие вопросы к данным звучат как «а что у тех, кто…»: ушедших клиентов, звонивших в поддержку чаще трёх раз, жителей одного штата. Булева индексация выбирает такие строки: условие превращается в маску `True/False`, и в таблице остаются только строки с `True`.

## Главное

- Сравнение столбца даёт **маску** — булеву Series: `df["calls"] > 3`.
- **`df[маска]`** оставляет строки, где маска истинна. Тот же выбор через `loc`: `df.loc[маска, столбцы]`.
- Условия объединяют операторами **`&`** (и), **`|`** (или), **`~`** (не). **Каждое условие — в скобках**, потому что `&` и `|` связывают сильнее, чем `>` и `==`.
- **`isin([...])`** проверяет вхождение в список значений, **`between(a, b)`** — попадание в диапазон (границы входят).
- Сумма маски — число подходящих строк, среднее — их доля.
- Для строк есть `df["col"].str.contains("текст")`.

## Пример

```python
import pandas as pd

df = pd.DataFrame({
    "State": ["KS", "OH", "NJ", "OH", "OK", "AL", "OH"],
    "International plan": ["No", "No", "No", "Yes", "Yes", "No", "Yes"],
    "Customer service calls": [1, 1, 0, 2, 4, 5, 0],
    "Churn": [False, False, False, False, True, True, True],
})

many_calls = df["Customer service calls"] > 3
print(many_calls.sum())              # 2 клиента
print(df[many_calls])

churned_intl = df[(df["Churn"]) & (df["International plan"] == "Yes")]
print(len(churned_intl))             # 2

print(df.loc[df["State"].isin(["OH", "NJ"]), "Churn"].mean())  # 0.25
print(df[~df["Churn"]].shape[0])     # 4 оставшихся клиента
```

## Частые ошибки

- **Писать `and` и `or`** вместо `&` и `|`. Получится ошибка `The truth value of a Series is ambiguous`.
- **Забывать скобки:** `df["a"] > 1 & df["b"] < 5` разбирается как `df["a"] > (1 & df["b"]) < 5`. Нужно `(df["a"] > 1) & (df["b"] < 5)`.
- **Менять отфильтрованную копию:** `df[mask]["col"] = 0` не трогает исходную таблицу. Пишите `df.loc[mask, "col"] = 0`.

## Источник

Фильтрация клиентов по условиям в [теме 1 mlcourse.ai](https://mlcourse.ai/book/topic01/topic01_pandas_data_analysis.html).
