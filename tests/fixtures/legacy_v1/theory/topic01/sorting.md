# Сортировка

## Зачем

Сортировка отвечает на вопросы «кто больше всех…» и «какие крайние значения». Она помогает найти выбросы, увидеть порядок и подготовить таблицу к выводу. В pandas две операции: по значениям столбцов и по индексу.

## Главное

- **`df.sort_values("col")`** сортирует строки по значениям столбца, по умолчанию по возрастанию.
- **`ascending=False`** — по убыванию.
- **Несколько столбцов:** `sort_values(["a", "b"], ascending=[True, False])`. Сначала по `a`, а при равных `a` — по `b`.
- **`df.sort_index()`** сортирует по индексу строк. Например, вернёт исходный порядок после сортировки по значениям.
- **`nlargest(n, "col")`** и **`nsmallest(n, "col")`** — короткий способ взять верхние и нижние `n` строк.
- Сортировка **возвращает новую таблицу**, исходная не меняется. Индекс строк переезжает вместе со строками.
- Пропуски по умолчанию уходят в конец (`na_position="last"`).

## Пример

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

## Частые ошибки

- **Вызвать `sort_values` и не сохранить результат.** Таблица останется в прежнем порядке.
- **Ждать нумерации `0, 1, 2…` после сортировки.** Индекс остаётся прежним; чтобы пронумеровать заново, нужен `reset_index(drop=True)`.
- **Передавать одно `ascending=False` при сортировке по нескольким столбцам** и ждать разных направлений. Нужен список `[True, False]`.

## Источник

Сортировка таблицы в [теме 1 mlcourse.ai](https://mlcourse.ai/book/topic01/topic01_pandas_data_analysis.html).
