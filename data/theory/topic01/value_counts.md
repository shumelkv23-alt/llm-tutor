# Частоты: value_counts

## Зачем

Для категориальных признаков — штата, тарифа, факта ухода — первый вопрос: «сколько каких значений?». `value_counts` отвечает одной строкой. Так видно, сбалансированы ли классы, есть ли редкие категории и сколько уникальных значений.

## Главное

- **`s.value_counts()`** возвращает Series «значение → сколько раз», отсортированную по убыванию частоты.
- **`normalize=True`** даёт доли вместо количеств (в сумме 1).
- **`dropna=False`** считает и пропуски как отдельное значение (по умолчанию они не считаются).
- **`sort=False`** сохраняет порядок первого появления, а `.sort_index()` упорядочит по самим значениям.
- **`s.nunique()`** — число уникальных значений, **`s.unique()`** — сами значения.
- У DataFrame тоже есть `value_counts()`: он считает уникальные **сочетания** значений столбцов.

## Пример

```python
import pandas as pd

churn = pd.Series([False, False, True, False, True, False, False, False], name="Churn")
print(churn.value_counts())                 # False 6, True 2
print(churn.value_counts(normalize=True))   # False 0.75, True 0.25

plan = pd.Series(["No", "No", "Yes", None, "No", "Yes"], name="International plan")
print(plan.value_counts(dropna=False))      # No 3, Yes 2, None 1
print(plan.nunique())                       # 2 — пропуск не считается

df = pd.DataFrame({"plan": ["No", "No", "Yes", "No"], "churn": [False, True, True, False]})
print(df.value_counts())                    # частоты пар (plan, churn)
```

## Частые ошибки

- **Не проверять баланс классов.** Если ушло 15% клиентов, модель «никто не уходит» угадывает 85% случаев. С этим числом и нужно сравнивать.
- **Терять пропуски:** без `dropna=False` их не видно, хотя пропуск — тоже информация.
- **Сравнивать количества групп разного размера.** Для сравнения долей удобнее `normalize=True`.

## Источник

Распределение признака Churn в [теме 1 mlcourse.ai](https://mlcourse.ai/book/topic01/topic01_pandas_data_analysis.html).
