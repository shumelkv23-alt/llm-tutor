# apply: функции к осям

## Зачем

Иногда нужного преобразования нет среди готовых векторных операций: например, разобрать строку особого формата или посчитать что-то по нескольким столбцам сразу. Тогда функцию применяют к каждому элементу, столбцу или строке. Для этого есть `map` и `apply`. Это гибкий инструмент, но он медленнее векторных операций.

## Главное

- **`s.map(func или словарь)`** — к каждому элементу Series. Словарь удобен для перекодировки: `{"Yes": 1, "No": 0}`.
- **`s.apply(func)`** — тоже поэлементно для Series. Функция может быть сложнее, чем `lambda`.
- **`df.apply(func)`** по умолчанию (`axis=0`) вызывает функцию **для каждого столбца**, а с `axis=1` — **для каждой строки**. Строка приходит в функцию как Series с именами столбцов в индексе.
- Если есть векторная альтернатива (`df["a"] * 2`, `np.where`, `.str`-методы), она почти всегда быстрее `apply`.
- Значение, которого нет в словаре `map`, превращается в `NaN`.

## Пример

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

## Частые ошибки

- **Забыть `axis=1`**, когда функция ждёт строку. Тогда в неё придёт столбец, и обращение `row["Total day minutes"]` упадёт.
- **Использовать `apply` для простой арифметики** — работает в десятки раз медленнее векторного выражения.
- **Не заметить `NaN` после `map`** со словарём, в котором есть не все значения.

## Источник

Методы apply и map в [теме 1 mlcourse.ai](https://mlcourse.ai/book/topic01/topic01_pandas_data_analysis.html).
