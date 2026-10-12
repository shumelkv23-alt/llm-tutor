# Группировка: groupby

## Зачем

Самые содержательные вопросы к данным — сравнения групп. Уходят ли чаще клиенты с международным тарифом? Сколько минут в среднем говорят в каждом штате? `groupby` разбивает таблицу на группы по значению ключа и считает по каждой группе одно и то же.

## Главное

Группировка работает по схеме **split → apply → combine**:

1. **split** — строки делятся на группы по значению ключевого столбца (или нескольких);
2. **apply** — к каждой группе применяется функция, обычно агрегат: `mean`, `sum`, `count`, `size`;
3. **combine** — результаты собираются в новую таблицу или Series, где индекс — значения ключа.

- `df.groupby("key")["col"].mean()` — среднее `col` в каждой группе.
- Группировать можно по нескольким столбцам: `groupby(["a", "b"])` — тогда индекс результата составной.
- **`size()`** считает строки в группе, **`count()`** — непустые значения.
- `as_index=False` или `.reset_index()` вернут ключ из индекса в обычный столбец.
- Без агрегата `groupby` сам по себе ничего не считает: это «отложенное» разбиение.

## Пример

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

## Частые ошибки

- **Ждать результат без агрегата:** `df.groupby("a")` — это объект группировки, а не таблица.
- **Путать `size` и `count`:** `size` считает все строки, `count` пропускает `NaN` (и считается по каждому столбцу).
- **Делать выводы по маленьким группам.** Доля оттока 100% в группе из двух клиентов ничего не доказывает. Смотрите и размер группы.

## Источник

Группировка в [теме 1 mlcourse.ai](https://mlcourse.ai/book/topic01/topic01_pandas_data_analysis.html).
