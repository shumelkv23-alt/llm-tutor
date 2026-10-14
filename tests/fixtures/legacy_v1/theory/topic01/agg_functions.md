# Агрегации

## Зачем

Одного среднего по группе часто мало: хочется сразу видеть и среднее, и медиану, и число объектов, причём для разных столбцов — разные функции. Метод `agg` собирает такую сводку за один вызов.

## Главное

- Простые агрегаты вызываются прямо: `sum`, `mean`, `median`, `min`, `max`, `std`, `count`, `nunique`.
- **`agg(["mean", "max"])`** — несколько функций к выбранным столбцам: в результате по столбцу на каждую функцию.
- **`agg({"col1": "mean", "col2": ["min", "max"]})`** — словарь «столбец → функция(и)»: каждому столбцу своё.
- **Именованная агрегация** даёт понятные имена столбцов результата: `agg(avg_minutes=("Total day minutes", "mean"), clients=("Churn", "size"))`.
- Агрегат принимает и свою функцию: `agg(lambda s: s.max() - s.min())`.
- Без `groupby` метод `agg` сворачивает всю таблицу.

## Пример

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

## Частые ошибки

- **Получить столбцы с двухуровневыми именами** после `agg` со списками и не понять, как к ним обратиться. Используйте именованную агрегацию — имена будут плоскими.
- **Путать `count` и `size`** в агрегации: `count` не считает пропуски.
- **Агрегировать текстовый столбец средним** — будет ошибка. Для текста подходят `nunique`, `first` и `size`.

## Источник

Агрегации после группировки в [теме 1 mlcourse.ai](https://mlcourse.ai/book/topic01/topic01_pandas_data_analysis.html).
