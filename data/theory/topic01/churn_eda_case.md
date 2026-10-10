# Кейс: EDA оттока телеком-клиентов

## Зачем

Этот кейс собирает всю тему в один сквозной разбор. В курсе mlcourse.ai используют датасет оттока клиентов телеком-оператора (около 3300 клиентов): по тарифу, минутам разговоров и звонкам в поддержку нужно понять, кто уходит. Ниже — тот же путь на маленькой синтетической выборке с теми же столбцами, чтобы код запускался без файла.

## Главное

План разбора:

1. **Загрузка и осмотр:** `read_csv`, `shape`, `info` — типы и пропуски.
2. **Приведение типов:** `Churn` в 0/1, тарифы в категории или флаги.
3. **Базовый уровень:** общая доля оттока. В исходных данных — около 14,5%.
4. **Гипотезы и срезы:**
   - международный тариф → `crosstab` с `normalize="index"`;
   - звонки в поддержку → `groupby` по числу звонков;
   - нагрузка днём → средние минуты у ушедших и оставшихся.
5. **Выводы и простое правило.** В курсе из наблюдений строят правило «международный тариф **или** 4+ звонка в поддержку → уйдёт». Оно угадывает около 85% клиентов — заметно лучше константы «никто не уходит».

## Пример

```python
import numpy as np
import pandas as pd

rng = np.random.default_rng(17)
n = 400
df = pd.DataFrame({
    "International plan": rng.choice(["No", "Yes"], size=n, p=[0.9, 0.1]),
    "Customer service calls": rng.poisson(1.5, size=n),
    "Total day minutes": rng.normal(180, 50, size=n).clip(0),
})
risk = (
    0.08
    + 0.35 * (df["International plan"] == "Yes")
    + 0.40 * (df["Customer service calls"] >= 4)
    + 0.10 * (df["Total day minutes"] > 260)
)
df["Churn"] = rng.random(n) < risk.clip(0, 0.95)

print(df.shape)
print(f"Базовая доля оттока: {df['Churn'].mean():.1%}")

print(pd.crosstab(df["International plan"], df["Churn"], normalize="index").round(2))
print(df.groupby("Customer service calls")["Churn"].agg(["mean", "size"]).round(2))
print(df.groupby("Churn")["Total day minutes"].mean().round(1))
```

Проверяем простое правило так же, как в курсе:

```python
predicted = (df["International plan"] == "Yes") | (df["Customer service calls"] >= 4)
accuracy = (predicted == df["Churn"]).mean()
constant = (~df["Churn"]).mean()          # «никто не уходит»
print(f"Правило: {accuracy:.0%}, константа: {constant:.0%}")
```

## Частые ошибки

- **Смотреть на точность без базы.** 85% звучит хорошо, но константа «никто не уходит» уже даёт около 85%. Правило ценно, если ловит именно ушедших.
- **Делать вывод о причине.** Частые звонки в поддержку — сигнал, а не доказанная причина ухода.
- **Забывать о размере групп** при чтении долей по числу звонков: на 6–9 звонках клиентов единицы.

## Источник

Сквозной разбор датасета оттока в [теме 1 mlcourse.ai: первичный анализ данных с Pandas](https://mlcourse.ai/book/topic01/topic01_pandas_data_analysis.html).
