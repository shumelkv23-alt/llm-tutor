# Оценка и подбор параметров

Качество модели и её гиперпараметры нельзя проверять на тех же данных, на
которых она обучалась. Занятие — о трёх инструментах честной оценки:
отложенной выборке, кросс-валидации и автоматическом переборе параметров
`GridSearchCV`.

## Зачем

После занятия вы сможете:

- отложить часть данных и объяснить, чем отложенная выборка полезна и в чём её
  ограничение;
- оценить модель кросс-валидацией и понять, сколько раз она обучится;
- подобрать гиперпараметры `GridSearchCV` и прочитать его результат;
- не допустить утечки данных при масштабировании внутри кросс-валидации.

## Отложенная выборка

### Главное

**Отложенная выборка** (hold-out) — часть данных, которая не участвует в
обучении и нужна только для проверки. Модель обучается на оставшейся части, а
качество на отложенной — честная оценка того, как она поведёт себя на новых
данных.

В scikit-learn: `train_test_split(X, y, test_size=0.3, random_state=17,
stratify=y)`. Здесь 30% строк уходит в отложенную выборку. Стратификация
(`stratify=y`) сохраняет доли классов в обеих частях.

Недостатки метода. Оценка зависит от конкретного разбиения: другое разбиение —
другое число. Кроме того, отложенные данные не участвуют в обучении, что
невыгодно, когда данных мало. А если подбирать гиперпараметры по той же
отложенной выборке много раз, она перестаёт быть независимой: итоговую
оценку делают на третьей, ни разу не использованной части.

### Пример

```python
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.tree import DecisionTreeClassifier

rng = np.random.default_rng(12)
X = rng.normal(size=(300, 4))
y = (X[:, 0] + rng.normal(0, 1.0, 300) > 0).astype(int)

for seed in (1, 2, 3):
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.3, random_state=seed, stratify=y
    )
    model = DecisionTreeClassifier(max_depth=3, random_state=0).fit(X_train, y_train)
    print("разбиение", seed, "-> точность", round(model.score(X_test, y_test), 3))
print(len(X_test) / len(X))   # доля отложенной выборки
```

### Частые ошибки

- **Оценивать модель на обучающей части.** Нужна именно отложенная.
- **Верить единственному разбиению.** Числа на разных разбиениях различаются;
  при малых данных разброс заметен.
- **Подбирать параметры и итог на одной отложенной выборке.** Итоговая оценка
  завышена: параметры подогнаны под неё.

## Кросс-валидация

### Главное

**K-fold кросс-валидация** устроена так: данные делят на `K` примерно равных
частей (фолдов). Затем `K` раз повторяют: обучают модель на `K − 1` фолдах, а
оставшийся используют для проверки. Итог — `K` оценок качества; их среднее —
более устойчивая оценка, а разброс показывает, насколько ей можно доверять.
При 5-кратной кросс-валидации модель обучается 5 раз.

Недостаток один: **стоимость** — модель обучается `K` раз. На больших данных
это дорого, и тогда довольствуются отложенной выборкой. Достоинство: в
обучении и в проверке побывали все данные.

В scikit-learn: `cross_val_score(model, X, y, cv=5)` возвращает массив из `K`
оценок. Для классификации по умолчанию используется стратифицированное
разбиение. Если в модель входит масштабирование, его кладут в `Pipeline`, чтобы
оно пересчитывалось на каждом обучающем фолде и тестовые данные не «утекали»
в расчёт среднего.

### Пример

```python
import numpy as np
from sklearn.model_selection import cross_val_score
from sklearn.neighbors import KNeighborsClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeClassifier

rng = np.random.default_rng(13)
X = np.column_stack([rng.normal(180, 50, 300), rng.integers(0, 8, 300)])
y = (X[:, 1] + rng.normal(0, 1.2, 300) > 4).astype(int)

tree_scores = cross_val_score(DecisionTreeClassifier(max_depth=3, random_state=0), X, y, cv=5)
print(len(tree_scores), "оценок:", tree_scores.round(3))
print("среднее", tree_scores.mean().round(3), "разброс", tree_scores.std().round(3))

knn = make_pipeline(StandardScaler(), KNeighborsClassifier(n_neighbors=5))
print("kNN с масштабом:", cross_val_score(knn, X, y, cv=5).mean().round(3))
```

### Частые ошибки

- **Масштабировать данные до кросс-валидации.** Тогда проверочные фолды
  влияют на масштаб, и оценка завышается. Масштабирование — внутри `Pipeline`.
- **Смотреть только на среднее.** Большой разброс между фолдами говорит о
  нестабильной модели или маленьких данных.
- **Применять кросс-валидацию без перемешивания к упорядоченным данным.**
  Если строки отсортированы по классам или времени, нужно перемешивание
  (`shuffle=True` в `KFold`) или специальная схема.

## GridSearchCV

### Главное

Настраивать гиперпараметры вручную долго. `GridSearchCV` перебирает все
комбинации значений из **сетки** параметров и для каждой считает качество
кросс-валидацией. Сетка из `max_depth` от 1 до 10 и `max_features` от 4 до 18
даёт 10 × 15 = 150 комбинаций; при 5 фолдах это 750 обучений, поэтому сетки
держат небольшими.

После `fit` результат лежит в атрибутах: `best_params_` — лучшая комбинация,
`best_score_` — её средняя оценка, `best_estimator_` — модель, переобученная
на всех данных с лучшими параметрами. Если параметр принадлежит шагу
`Pipeline`, в сетке его имя записывают как `имя_шага__параметр`, например
`kneighborsclassifier__n_neighbors`.

Итоговое качество проверяют на отложенной выборке, которая в переборе не
участвовала.

### Пример

```python
import numpy as np
from sklearn.model_selection import GridSearchCV, train_test_split
from sklearn.neighbors import KNeighborsClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeClassifier

rng = np.random.default_rng(14)
X = rng.normal(size=(400, 5))
y = (X[:, 0] + X[:, 1] ** 2 + rng.normal(0, 0.8, 400) > 1).astype(int)
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.3, random_state=17, stratify=y
)

grid = GridSearchCV(
    DecisionTreeClassifier(random_state=17),
    param_grid={"max_depth": range(1, 6), "min_samples_leaf": [1, 5, 20]},
    cv=5,
)
grid.fit(X_train, y_train)
print(grid.best_params_, round(grid.best_score_, 3))
print("на отложенной выборке:", round(grid.score(X_test, y_test), 3))

pipe = Pipeline([("scale", StandardScaler()), ("knn", KNeighborsClassifier())])
knn_grid = GridSearchCV(pipe, {"knn__n_neighbors": [1, 5, 15]}, cv=5).fit(X_train, y_train)
print(knn_grid.best_params_)
```

### Частые ошибки

- **Раздувать сетку.** Каждая лишняя комбинация умножает время на число
  фолдов.
- **Сообщать `best_score_` как итоговое качество.** Оно получено при выборе
  лучшего варианта и слегка оптимистично; итоговую проверку делают на
  отложенных данных.
- **Забыть префикс шага в `Pipeline`.** Параметр записывается как
  `имя_шага__параметр`, иначе `GridSearchCV` не найдёт его.

## Итоги занятия

| Что | Что делает |
|---|---|
| `train_test_split(…, test_size, stratify)` | отложенная выборка |
| `cross_val_score(model, X, y, cv=5)` | `K` оценок кросс-валидации |
| `GridSearchCV(model, param_grid, cv=5)` | перебор сетки параметров с кросс-валидацией |
| `best_params_`, `best_score_`, `best_estimator_` | результат перебора |
| `Pipeline`, `шаг__параметр` | масштабирование внутри модели и параметры шагов |

Порядок работы: отложить тест → подобрать параметры на кросс-валидации по
оставшейся части → один раз проверить на тесте.

## Источник

[mlcourse.ai, тема 3: деревья решений и метод ближайших соседей](https://mlcourse.ai/book/topic03/topic03_decision_trees_kNN.html)
и документация [scikit-learn: кросс-валидация](https://scikit-learn.org/stable/modules/cross_validation.html),
[GridSearchCV](https://scikit-learn.org/stable/modules/grid_search.html).
