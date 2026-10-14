# Признаки разных типов вместе

В таблице оттока числа и категории перемешаны: минуты разговоров, тарифный
план, штат, признак ухода. Корреляция Пирсона и диаграмма рассеяния работают
только для пар чисел. Это занятие — о том, как связать количественный признак
с категориальным, два категориальных между собой и как уместить связь трёх
признаков в одну тепловую карту.

## Зачем

После занятия вы сможете:

- сравнить распределение числового признака в группах (ящики, скрипки,
  `catplot`);
- показать связь двух категориальных признаков (`countplot` с `hue`,
  `crosstab`) и отличить доли от чисел;
- построить сводную таблицу `pivot_table` и нарисовать её тепловой картой;
- понимать, когда малая группа делает картинку обманчивой.

## Количественный против категориального

### Главное

Вопрос звучит так: «отличается ли числовой признак в разных группах?». Пример:
правда ли, что у клиентов с международным тарифом разговоры длиннее?

Универсальный подход — нарисовать распределение числа **отдельно для каждой
категории** и сравнить: ящики (`df.boxplot(column=…, by=…)` или
`sns.boxplot`) показывают медианы и разброс, скрипки (`sns.violinplot`) —
ещё и форму. Если категорий две, скрипку делят пополам: `hue` с `split=True`
рисует по половинке скрипки на каждый класс. А ещё есть сводка числами:
`df.groupby("International plan")["Total intl minutes"].describe()`.

`sns.catplot` — одна функция для всех «категориальных» видов графика: параметр
`kind` выбирает вид (`"box"`, `"violin"`, `"strip"`, `"bar"`), а `col=` и `row=`
раскладывают данные по панелям. Отличие от остальных функций seaborn:
`catplot` создаёт собственную фигуру и не принимает `ax=`.

### Пример

```python
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

rng = np.random.default_rng(9)
n = 600
plan = rng.random(n) < 0.12
churn = rng.random(n) < np.where(plan, 0.4, 0.1)
df = pd.DataFrame(
    {
        "International plan": np.where(plan, "Yes", "No"),
        "Churn": churn,
        "Total intl minutes": rng.normal(np.where(plan, 12, 10), 2.5).clip(0),
    }
)

print(df.groupby("International plan")["Total intl minutes"].describe().round(1))

fig, axes = plt.subplots(1, 2, figsize=(10, 3.5))
df.boxplot(column="Total intl minutes", by="International plan", ax=axes[0])
sns.violinplot(data=df, x="International plan", y="Total intl minutes",
               hue="Churn", split=True, cut=0, ax=axes[1])

grid = sns.catplot(data=df, x="International plan", y="Total intl minutes",
                   kind="violin", cut=0, hue="International plan", legend=False)
print(type(grid).__name__)   # catplot возвращает FacetGrid и рисует на своей фигуре
```

### Частые ошибки

- **Сравнивать группы по среднему без картинки.** Среднее одинаково у узкого
  и у очень широкого распределения; ящик покажет разницу.
- **Передать `ax=` в `catplot`.** Эта функция создаёт свою фигуру; для
  собственных осей берут `sns.boxplot` / `sns.violinplot`.
- **Игнорировать размер групп.** Скрипка из двадцати наблюдений выглядит так
  же убедительно, как из двух тысяч.

## Категориальный против категориального

### Главное

Для двух категориальных признаков считают **таблицу сопряжённости**
(`pd.crosstab`) — сколько наблюдений в каждой комбинации значений — и рисуют её
столбцами. В seaborn это `sns.countplot(data=df, x="Customer service calls",
hue="Churn")`: на каждое значение по группе столбцов, по одному на класс.

Здесь главная ловушка — абсолютные числа. Если клиентов без тарифа в десять
раз больше, столбец «без тарифа, ушёл» окажется выше, даже если доля ушедших у
них ниже. Чтобы сравнить группы честно, считают **доли внутри категории**:
`pd.crosstab(a, b, normalize="index")` делит каждую строку на её сумму. Доли и
рисуют: `.plot(kind="bar", stacked=True)` или `kind="bar"` рядом.

### Пример

```python
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

rng = np.random.default_rng(10)
n = 800
calls = rng.poisson(1.6, n).clip(0, 6)
df = pd.DataFrame(
    {
        "International plan": np.where(rng.random(n) < 0.1, "Yes", "No"),
        "Customer service calls": calls,
    }
)
df["Churn"] = rng.random(n) < (0.07 + 0.07 * calls) + (df["International plan"] == "Yes") * 0.2

counts = pd.crosstab(df["International plan"], df["Churn"])
shares = pd.crosstab(df["International plan"], df["Churn"], normalize="index")
print(counts)
print(shares.round(2))

fig, axes = plt.subplots(1, 2, figsize=(11, 3.5))
sns.countplot(data=df, x="Customer service calls", hue="Churn", ax=axes[0])
shares.plot(kind="bar", stacked=True, ax=axes[1])
axes[1].set_ylabel("доля внутри категории")
```

### Частые ошибки

- **Сравнивать абсолютные числа в группах разного размера.** Переходите к
  долям: `normalize="index"` по строкам, `"columns"` по столбцам, `"all"` от
  общей суммы.
- **Перепутать, внутри чего считаются доли.** «Доля ушедших среди клиентов с
  тарифом» и «доля клиентов с тарифом среди ушедших» — разные числа и разные
  вопросы.
- **Кормить `crosstab` непрерывными признаками.** Минуты разговоров дают тысячи
  уникальных значений; сначала разбейте на интервалы (`pd.cut`).

## Тепловая карта сводной таблицы

### Главное

Если нужны три признака сразу — две категории и числовая характеристика —
строят **сводную таблицу** и рисуют её тепловой картой. `df.pivot_table(
values="Churn", index="International plan", columns="Customer service
calls", aggfunc="mean")` даёт сетку: строки и столбцы — категории, в клетках —
среднее значение. Для булева `Churn` среднее — **доля ушедших**.

`sns.heatmap(table, annot=True, fmt=".2f", cmap="Reds")` раскрашивает клетки.
В отличие от тепловой карты корреляций, шкала здесь односторонняя (от 0 до 1
или от минимума до максимума), поэтому подходит последовательная палитра
(`"Reds"`, `"Blues"`), а не расходящаяся.

Подвох сводных таблиц — **маленькие клетки**. Доля 1.0 в клетке с двумя
клиентами не значит ничего. Рядом с долями полезно считать размеры клеток
(`aggfunc="size"`) и не делать выводов по тем, где наблюдений мало. Пропуск
(`NaN`) в клетке означает, что такой комбинации в данных нет.

### Пример

```python
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

rng = np.random.default_rng(11)
n = 900
df = pd.DataFrame(
    {
        "International plan": np.where(rng.random(n) < 0.12, "Yes", "No"),
        "Customer service calls": rng.poisson(1.5, n).clip(0, 5),
    }
)
risk = 0.05 + 0.06 * df["Customer service calls"] + (df["International plan"] == "Yes") * 0.25
df["Churn"] = rng.random(n) < risk

table = df.pivot_table(values="Churn", index="International plan",
                       columns="Customer service calls", aggfunc="mean")
sizes = df.pivot_table(values="Churn", index="International plan",
                       columns="Customer service calls", aggfunc="size")
print(sizes)   # клеток с малым числом клиентов доверять нельзя

fig, ax = plt.subplots(figsize=(7, 2.5))
sns.heatmap(table, annot=True, fmt=".2f", cmap="Reds", vmin=0, vmax=1, ax=ax)
ax.set_title("Доля ушедших")
```

### Частые ошибки

- **Доверять клеткам с двумя-тремя наблюдениями.** Показывайте размеры клеток
  рядом или отсекайте маленькие группы.
- **Брать расходящуюся палитру для долей.** Красно-синяя шкала нужна там, где
  есть осмысленный ноль посередине (корреляции); для долей — последовательная.
- **Забыть, что `aggfunc` по умолчанию — среднее.** Для текстовых значений
  среднее не посчитать; используйте `"count"`, `"size"` или своё агрегирование.

## Итоги занятия

| Что | Что делает |
|---|---|
| `df.boxplot(column=…, by=…)`, `sns.violinplot(…, hue=…, split=True)` | числовой признак по группам |
| `sns.catplot(kind="box" / "violin" / "strip" / "bar")` | все категориальные графики одной функцией, свои фигуры |
| `pd.crosstab(a, b, normalize="index")` | доли внутри каждой категории |
| `sns.countplot(data=df, x=…, hue=…)` | столбцы частот по классам |
| `df.pivot_table(values, index, columns, aggfunc)` | сводная таблица |
| `sns.heatmap(table, annot=True, cmap="Reds")` | её тепловая карта |

Порядок работы: числовой против категории — ящики и скрипки; две категории
— доли внутри строки; три признака — сводная таблица и её размеры.

## Источник

[mlcourse.ai, тема 2: визуальный анализ данных](https://mlcourse.ai/book/topic02/topic02_visual_data_analysis.html)
и документация [seaborn](https://seaborn.pydata.org/tutorial/categorical.html),
[pandas `pivot_table`](https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.pivot_table.html).
