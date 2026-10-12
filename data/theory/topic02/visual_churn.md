# Кейс: визуальный анализ оттока

Занятие собирает модуль в один рабочий процесс. Перед вами таблица клиентов
оператора связи, а вопрос — что отличает ушедших от оставшихся. Сначала мы
смотрим на каждый признак отдельно, потом сравниваем признаки с оттоком.
Результат — не модель, а список проверяемых гипотез.

## Зачем

После занятия вы сможете:

- обойти все признаки таблицы по плану и не пропустить странности: выбросы,
  перекосы, дисбаланс классов;
- сравнить признак с оттоком подходящим графиком — для числа, для категории и
  для пары признаков;
- превратить картинки в осторожные формулировки — «похоже, что…, проверим»;
- отличить сильный сигнал от шума малой группы.

## Признаки по одному

### Главное

Начинайте с признаков по одному и с целевого. План обхода короткий:

1. **Целевой признак.** Какая доля клиентов ушла? Если классы сильно
   несбалансированы, это стоит помнить на каждом следующем графике и при
   выборе метрики.
2. **Числовые признаки.** Гистограмма или KDE: форма, хвосты, горбы,
   подозрительные значения (нули там, где их быть не может, или «потолок»
   значений). Ящик — для выбросов.
3. **Категориальные признаки.** Столбцы частот: сколько категорий, нет ли
   редких, нет ли ошибок написания.
4. **Дубли смысла.** Пары признаков, дающие почти одно и то же (минуты и
   стоимость): одно из пары можно не рисовать дважды.

Результат этого шага — не вывод об оттоке, а **карта данных**: что можно
доверять, что чистить, на что смотреть дальше. Все признаки обходят циклом,
чтобы не писать один и тот же график десять раз.

### Пример

```python
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

rng = np.random.default_rng(15)
n = 800
df = pd.DataFrame(
    {
        "Total day minutes": rng.normal(180, 55, n).clip(0),
        "Customer service calls": rng.poisson(1.6, n),
        "Total intl calls": rng.poisson(4, n),
        "International plan": np.where(rng.random(n) < 0.1, "Yes", "No"),
        "Churn": rng.random(n) < 0.145,
    }
)

print(df["Churn"].value_counts(normalize=True).round(3))

numeric = ["Total day minutes", "Customer service calls", "Total intl calls"]
fig, axes = plt.subplots(1, len(numeric), figsize=(12, 3))
for ax, column in zip(axes, numeric):
    sns.histplot(df[column], bins=25, ax=ax)
    ax.set_title(column)

fig, axes = plt.subplots(1, 2, figsize=(7, 3))
sns.countplot(data=df, x="International plan", ax=axes[0])
sns.countplot(data=df, x="Churn", ax=axes[1])
print(df[numeric].describe().loc[["min", "50%", "max"]].round(1))
```

### Частые ошибки

- **Перескочить к оттоку, не посмотрев на признаки.** Выброс или ошибка в
  данных испортят и картинку против оттока, и модель.
- **Не фиксировать находки.** Через час вы забудете, что у `Total intl calls`
  длинный хвост. Записывайте замечания по ходу.
- **Чистить данные «на глаз» прямо в таблице.** Всё, что вы убираете или
  исправляете, делайте кодом: тогда процедуру можно повторить.

## Признаки против оттока

### Главное

Теперь сравниваем каждый признак с целевым. Вид сравнения зависит от типа
признака:

- **числовой против оттока** — плотность или ящик по группам «ушёл / остался»
  (`sns.kdeplot(hue="Churn", common_norm=False)`); если смещение между группами
  заметно, признак информативен;
- **категориальный против оттока** — доли ушедших внутри категории
  (`crosstab(..., normalize="index")` или `countplot` с `hue`), не абсолютные
  числа;
- **два признака против оттока** — сводная таблица и тепловая карта доли
  ушедших; она показывает, например, что риск растёт у клиентов с
  международным тарифом, часто звонящих в поддержку.

Дальше — формулировка. Картинка даёт **гипотезу**, а не вывод: «похоже, что
клиенты с четырьмя и более звонками в поддержку уходят чаще, проверим на
большей выборке и тестом». Остерегайтесь трёх вещей: малых групп (доля 60% на
пяти клиентах), дисбаланса (ушедших мало, любая группа шумит) и скрытых
третьих факторов (клиенты с тарифом одновременно дольше разговаривают).

### Пример

```python
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

rng = np.random.default_rng(16)
n = 1000
plan = rng.random(n) < 0.1
calls = rng.poisson(1.5, n)
risk = 0.06 + 0.05 * calls + plan * 0.3
df = pd.DataFrame(
    {
        "International plan": np.where(plan, "Yes", "No"),
        "Customer service calls": calls,
        "Total day minutes": rng.normal(180, 50, n).clip(0),
        "Churn": rng.random(n) < risk,
    }
)

fig, axes = plt.subplots(1, 3, figsize=(14, 3.5))
sns.kdeplot(data=df, x="Total day minutes", hue="Churn", common_norm=False,
            cut=0, ax=axes[0])
by_calls = df.groupby("Customer service calls")["Churn"].agg(["mean", "size"])
sns.barplot(x=by_calls.index, y=by_calls["mean"], color="C0", ax=axes[1])
axes[1].set_title("Доля ушедших по числу звонков")
pivot = df.pivot_table(values="Churn", index="International plan",
                       columns="Customer service calls", aggfunc="mean")
sns.heatmap(pivot, annot=True, fmt=".2f", cmap="Reds", ax=axes[2])
print(by_calls.round(2))   # size показывает, каким долям можно верить
```

Второй приём — численная проверка того, что показал график: сравнить доли
ушедших в двух группах и посмотреть, много ли в них наблюдений.

```python
import numpy as np
import pandas as pd

rng = np.random.default_rng(17)
n = 1000
calls = rng.poisson(1.5, n)
df = pd.DataFrame(
    {
        "Customer service calls": calls,
        "Churn": rng.random(n) < 0.06 + 0.05 * calls,
    }
)

df["many_calls"] = df["Customer service calls"] >= 4
summary = df.groupby("many_calls")["Churn"].agg(share="mean", clients="size")
print(summary.round(3))
```

### Частые ошибки

- **Выводить правило по группе из пяти клиентов.** Всегда печатайте размер
  группы рядом с долей.
- **Сравнивать абсолютные числа ушедших.** Нужны доли внутри групп.
- **Принять первую находку за ответ.** Картинка против оттока — гипотеза;
  проверка — на новых данных, статистикой или моделью.

## Итоги занятия

| Шаг | Чем делаем |
|---|---|
| Баланс классов | `value_counts(normalize=True)`, `countplot` |
| Числовые признаки по одному | `histplot`, `kdeplot`, `describe` |
| Категории по одной | `countplot`, `value_counts` |
| Числовой против оттока | `kdeplot(hue="Churn", common_norm=False)` |
| Категория против оттока | `crosstab(normalize="index")`, `groupby(...)["Churn"].mean()` |
| Два признака против оттока | `pivot_table` + `heatmap` |

Порядок работы: цель → признаки по одному → признаки против цели →
гипотезы с размерами групп.

## Источник

[mlcourse.ai, тема 2: визуальный анализ данных](https://mlcourse.ai/book/topic02/topic02_visual_data_analysis.html)
(раздел «Пример: данные оттока») и документация [seaborn](https://seaborn.pydata.org/).
