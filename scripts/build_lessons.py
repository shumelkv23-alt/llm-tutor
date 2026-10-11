"""Сборка 33 занятий из 103 тем (срез 56, план dense-lessons §2).

Одноразовый скрипт, лежит в git для ревью. На входе — замороженные seed в
старом формате (``tests/fixtures/legacy_v1``), на выходе — ``data/seed_topicNN.json``
и временные конспекты модуля 1 (склейка старых под заголовками разделов).

Что делает:

* узлы-занятия с разделами по таблице ``LAYOUT``;
* рёбра: «тема A → тема B» становится «занятие(A) → занятие(B)»; рёбра внутри
  занятия и к убранным темам исчезают, дубли схлопываются (жёсткое, если
  жёстким было хоть одно, вес — наибольший). Остаются только рёбра «вперёд»
  по порядку занятий модуля и «назад» между модулями; прочее печатается.
  Между соседними занятиями модуля, если прямого ребра нет, — мягкое ребро:
  так топологический порядок совпадает с таблицей;
* задания: веса тем переходят на их занятия, раздел — раздел главной темы
  задания; задание кладётся в файл модуля своего главного занятия;
* ``legacy``: все 103 старые темы (запись — в файле модуля занятия-цели,
  убранная тема — в файле модуля, где жила);
* анкеты: блоки те же, занятие — в блоке, где больше его старых тем.

Запуск: ``uv run python -X utf8 scripts/build_lessons.py``.

**Повторно не запускать:** после сборки seed правятся руками (задания модуля 1
в срезе 56, конспекты и задания в срезах 57–61) — повторный запуск их затрёт.
"""

import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tests" / "fixtures" / "legacy_v1"
TARGET = ROOT / "data"

# Темы, убранные без замены: курс рассчитан на базовый Python (план §1.2).
REMOVED = ("python_basics", "numpy_basics")
# Мягкое ребро порядка между соседними занятиями.
CHAIN_WEIGHT = 0.3

# Модуль → занятия: (id, название, описание, [(раздел, заголовок, [старые темы])]).
LAYOUT: dict[int, list[tuple[str, str, str, list[tuple[str, str, list[str]]]]]] = {
    1: [
        ("pandas_structures", "Series и DataFrame",
         "Зачем pandas, Series с индексом и DataFrame — таблица из столбцов-Series.",
         [("why", "Зачем pandas", ["pandas_intro"]),
          ("series", "Series: значения и индекс", ["pandas_series"]),
          ("dataframe", "DataFrame: строки, столбцы, индекс", ["pandas_dataframe"])]),
        ("load_inspect", "Загрузка и первый осмотр",
         "read_csv и первый взгляд на таблицу: head, shape, info, пропуски и уникальные значения.",
         [("read", "Чтение таблиц", ["read_csv"]),
          ("inspect", "head, shape, info, dtypes", ["df_inspect"]),
          ("missing", "Пропуски и уникальные значения", [])]),
        ("types_stats", "Типы данных и описательные статистики",
         "Приведение типов, describe и ручные статистики, частоты значений.",
         [("dtypes", "Типы и приведение", ["dtype_conversion"]),
          ("describe", "describe и ручные статистики", ["describe_stats"]),
          ("counts", "Частоты значений", ["value_counts"])]),
        ("selection", "Выборка данных",
         "loc, iloc и срезы; булевы маски и составные условия.",
         [("loc", "loc, iloc и срезы", ["indexing_loc_iloc"]),
          ("masks", "Булевы маски и условия", ["boolean_indexing"])]),
        ("transform", "Сортировка и преобразование столбцов",
         "Сортировка, apply/map/replace, новые и удалённые столбцы.",
         [("sorting", "Сортировка", ["sorting"]),
          ("apply", "apply, map и replace", ["apply_functions"]),
          ("columns", "Новые и удалённые столбцы", ["df_transformations"])]),
        ("grouping", "Группировка и сводные таблицы",
         "groupby по схеме split → apply → combine, агрегаты и agg, crosstab и pivot_table.",
         [("groupby", "split → apply → combine", ["groupby"]),
          ("agg", "Агрегаты и agg", ["agg_functions"]),
          ("pivot", "crosstab и pivot_table", ["summary_tables"])]),
        ("eda_case", "EDA на практике: кейс оттока",
         "Гипотеза → срез → вывод на данных об оттоке и первое правило-прогноз.",
         [("workflow", "Гипотеза → срез → вывод", ["eda_workflow"]),
          ("churn", "Разбор оттока", ["churn_eda_case"]),
          ("first_model", "Первое правило-прогноз", [])]),
    ],
    2: [
        ("viz_intro", "Зачем графики и чем их строить",
         "Графики на этапах ML, типы признаков и инструменты: matplotlib, seaborn, df.plot.",
         [("purpose", "Графики на всех этапах ML", ["viz_purpose"]),
          ("feature_types", "Типы признаков", ["feature_types"]),
          ("libraries", "matplotlib, seaborn и df.plot",
           ["visualization_basics", "viz_libraries", "df_plot"])]),
        ("distributions", "Распределение одного признака",
         "Гистограмма, KDE, ящик с усами и скрипка, частоты категорий и баланс классов.",
         [("histogram", "Гистограмма", ["histogram"]),
          ("kde", "Плотность KDE", ["density_plot"]),
          ("box_violin", "Ящик с усами и скрипка", ["box_plot", "violin_plot"]),
          ("counts", "Частоты категорий и баланс классов", ["bar_count_plot", "class_imbalance"])]),
        ("numeric_relations", "Связи количественных признаков",
         "Корреляции и тепловая карта, диаграмма рассеяния и pairplot.",
         [("corr", "Корреляции и heatmap", ["correlation_heatmap"]),
          ("scatter", "Диаграмма рассеяния", ["scatter_plot"]),
          ("pairplot", "pairplot", ["pairplot"])]),
        ("mixed_relations", "Признаки разных типов вместе",
         "Количественный против категориального, категориальный против категориального, "
         "тепловая карта сводной таблицы.",
         [("num_cat", "Количественный против категориального", ["num_vs_cat", "catplot"]),
          ("cat_cat", "Категориальный против категориального", ["cat_vs_cat"]),
          ("pivot_heatmap", "Тепловая карта сводной таблицы", ["heatmap_pivot"])]),
        ("tsne_plotly", "Весь датасет на плоскости и интерактив",
         "Зачем снижать размерность, t-SNE и интерактивные графики plotly.",
         [("why_reduce", "Зачем снижать размерность", ["dim_reduction"]),
          ("tsne", "t-SNE", ["tsne"]),
          ("plotly", "Интерактивные графики plotly", ["plotly_interactive"])]),
        ("visual_churn", "Кейс: визуальный анализ оттока",
         "Признаки оттока по одному и против целевого признака.",
         [("one_by_one", "Признаки по одному", ["visual_eda_case"]),
          ("against_target", "Признаки против оттока", [])]),
    ],
    3: [
        ("ml_basics", "Задачи ML и обучение с учителем",
         "Типы задач машинного обучения, обучение с учителем и доля правильных ответов.",
         [("task_types", "Типы задач ML", ["ml_task_types"]),
          ("supervised", "Обучение с учителем", ["supervised_learning"]),
          ("accuracy", "Доля правильных ответов", ["accuracy_metric"])]),
        ("tree_criteria", "Дерево решений и критерии разбиения",
         "Дерево как цепочка правил, энтропия и прирост информации, критерий Джини.",
         [("idea", "Дерево как цепочка правил", ["decision_tree_idea"]),
          ("entropy", "Энтропия и прирост информации", ["entropy", "information_gain"]),
          ("gini", "Критерий Джини", ["gini_impurity"])]),
        ("tree_building", "Как строится дерево",
         "Жадное построение, пороги по количественному признаку, дерево для регрессии.",
         [("greedy", "Жадное построение", ["greedy_tree_building"]),
          ("thresholds", "Пороги по количественному признаку", ["numeric_splits"]),
          ("regression", "Дерево для регрессии", ["regression_tree"])]),
        ("tree_tuning", "Переобучение и параметры дерева",
         "Переобучение дерева, параметры DecisionTreeClassifier, плюсы и минусы деревьев.",
         [("overfitting", "Переобучение", ["tree_overfitting"]),
          ("params", "Параметры DecisionTreeClassifier", ["tree_hyperparams"]),
          ("pros_cons", "Плюсы и минусы деревьев", ["tree_pros_cons"])]),
        ("knn", "Метод ближайших соседей",
         "Идея kNN, параметры и масштабирование признаков, плюсы и минусы метода.",
         [("idea", "Идея kNN", ["knn_idea"]),
          ("params", "Параметры и масштабирование", ["knn_params"]),
          ("pros_cons", "Плюсы и минусы kNN", ["knn_pros_cons"])]),
        ("validation", "Оценка и подбор параметров",
         "Отложенная выборка, кросс-валидация и GridSearchCV.",
         [("holdout", "Отложенная выборка", ["holdout_validation"]),
          ("cv", "Кросс-валидация", ["cross_validation"]),
          ("grid", "GridSearchCV", ["grid_search"])]),
        ("trees_knn_practice", "Кейсы: деревья и kNN",
         "Отток и рукописные цифры: где сильнее дерево, а где kNN.",
         [("cases", "Отток и рукописные цифры", ["trees_knn_cases"]),
          ("when_which", "Где какой метод сильнее", [])]),
    ],
    4: [
        ("linreg", "Линейная регрессия и МНК",
         "Модель линейной регрессии, МНК и MSE, нормальное уравнение.",
         [("model", "Модель линейной регрессии", ["linear_regression"]),
          ("ols", "МНК и MSE", ["ols_mse"]),
          ("normal_eq", "Нормальное уравнение", ["normal_equation"])]),
        ("linreg_stats", "Почему МНК: статистический взгляд",
         "Теорема Гаусса–Маркова, правдоподобие и квадратичная ошибка, смещение и разброс.",
         [("gauss_markov", "Теорема Гаусса–Маркова", ["gauss_markov"]),
          ("likelihood", "Правдоподобие и квадратичная ошибка",
           ["maximum_likelihood", "mle_normal_mse"]),
          ("bias_variance", "Смещение и разброс", ["bias_variance"])]),
        ("ridge", "Мультиколлинеарность и гребневая регрессия",
         "Мультиколлинеарность и L2-регуляризация.",
         [("multicollinearity", "Мультиколлинеарность", ["multicollinearity"]),
          ("l2", "L2-регуляризация", ["ridge_regression"])]),
        ("logreg", "Логистическая регрессия",
         "Линейный классификатор, вероятность, шансы и логит, отступ и логистическая потеря.",
         [("linear_classifier", "Линейный классификатор", ["linear_classifier"]),
          ("logit", "Вероятность, шансы и логит", ["logistic_regression", "odds_logit"]),
          ("loss", "Отступ и логистическая потеря", ["margin", "logistic_loss"])]),
        ("nonlinear", "Нелинейные границы и параметр C",
         "Полиномиальные признаки, задача XOR и параметр регуляризации C.",
         [("poly", "Полиномиальные признаки", ["poly_features"]),
          ("xor", "Задача XOR", ["xor_problem"]),
          ("reg_c", "Параметр C", ["regularization_C"])]),
        ("text", "Линейные модели для текста",
         "Мешок слов и Pipeline для текста.",
         [("bow", "Мешок слов", ["bag_of_words"]),
          ("pipeline", "Pipeline для текста", ["text_pipeline"])]),
        ("curves", "Кривые валидации и обучения",
         "Кривая валидации и кривая обучения.",
         [("validation_curve", "Кривая валидации", ["validation_curve"]),
          ("learning_curve", "Кривая обучения", ["learning_curve"])]),
    ],
    5: [
        ("bagging_basics", "Ансамбли и бэггинг",
         "Мудрость толпы, бутстрэп, бэггинг и почему у него падает разброс.",
         [("crowd", "Мудрость толпы", ["ensembles"]),
          ("bootstrap", "Бутстрэп", ["bootstrapping"]),
          ("bagging", "Бэггинг", ["bagging"]),
          ("variance", "Почему падает разброс", ["bagging_variance"])]),
        ("forest", "Случайный лес",
         "Случайные подпространства, алгоритм случайного леса и оценка out-of-bag.",
         [("subspace", "Случайные подпространства", ["random_subspace"]),
          ("algorithm", "Алгоритм леса", ["random_forest"]),
          ("oob", "Оценка out-of-bag", ["oob_error"])]),
        ("rf_theory", "Почему лес работает",
         "Декорреляция деревьев, смещение леса и Extremely Randomized Trees.",
         [("decorrelation", "Декорреляция деревьев", ["rf_variance_decorrelation"]),
          ("bias", "Смещение леса", ["rf_bias"]),
          ("extra_trees", "Extremely Randomized Trees", ["extra_trees"])]),
        ("rf_tuning", "Настройка леса",
         "max_features, гиперпараметры и кривые подбора.",
         [("max_features", "max_features", ["rf_max_features"]),
          ("hyperparams", "Гиперпараметры", ["rf_hyperparams"]),
          ("curves", "Кривые подбора", ["rf_tuning_curves"])]),
        ("rf_properties", "Свойства и применения леса",
         "Лес и kNN, RandomTreesEmbedding, плюсы и минусы случайного леса.",
         [("vs_knn", "Лес и kNN", ["rf_vs_knn"]),
          ("embedding", "RandomTreesEmbedding", ["trees_embedding"]),
          ("pros_cons", "Плюсы и минусы леса", ["rf_pros_cons"])]),
        ("feature_importance", "Важность признаков",
         "Идея важности, перестановочная важность, важность по неоднородности и кейс.",
         [("idea", "Идея важности", ["feature_importance_idea"]),
          ("permutation", "Перестановочная важность", ["permutation_importance"]),
          ("impurity", "Важность по неоднородности", ["impurity_importance"]),
          ("case", "Кейс: отзывы о хостелах", ["feature_importance_case"])]),
    ],
}

# Анкета модуля 1 без Python и NumPy (план 56.2).
MODULE1_LEVEL_QUESTION = "Для начала — как у тебя с pandas?"


def _load_sources() -> dict[int, dict]:
    return {
        int(path.stem[-2:]): json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(SOURCE.glob("seed_topic0?.json"))
    }


def _mapping() -> tuple[dict[str, tuple[str, str]], dict[str, int], list[str]]:
    """Старая тема → (занятие, раздел); занятие → модуль; порядок занятий."""
    where: dict[str, tuple[str, str]] = {}
    module_of: dict[str, int] = {}
    order: list[str] = []
    for number, lessons in LAYOUT.items():
        for lesson_id, _, _, sections in lessons:
            module_of[lesson_id] = number
            order.append(lesson_id)
            for section_id, _, old_topics in sections:
                for old in old_topics:
                    assert old not in where, f"тема {old} в двух разделах"
                    where[old] = (lesson_id, section_id)
    return where, module_of, order


def _main_topic(weights: dict[str, float]) -> str:
    return max(weights, key=weights.__getitem__)


def build() -> dict:
    sources = _load_sources()
    where, module_of, order = _mapping()
    old_nodes = {n["id"]: (number, n) for number, seed in sources.items() for n in seed["nodes"]}
    missing = sorted(set(old_nodes) - set(where) - set(REMOVED))
    assert not missing, f"темы без раздела: {missing}"
    clash = sorted(set(old_nodes) & set(module_of))
    assert not clash, f"id занятия совпадает со старой темой: {clash}"
    report: list[str] = []

    seeds: dict[int, dict] = {}
    for number, seed in sources.items():
        seeds[number] = {
            "course": seed["course"],
            "comment": (
                f"Модуль {number} в формате занятий (срез 56, scripts/build_lessons.py): "
                "узел — занятие из 2–4 разделов."
            ),
            "topic": json.loads(json.dumps(seed["topic"])),
            "nodes": [],
            "edges": [],
            "items": [],
            "rubrics": seed.get("rubrics", []),
            "criteria": seed.get("criteria", []),
            "legacy": {},
        }

    # --- узлы ---
    for number, lessons in LAYOUT.items():
        for lesson_id, name, description, sections in lessons:
            olds = [old for _, _, topics in sections for old in topics]
            difficulty = round(sum(old_nodes[o][1]["difficulty"] for o in olds) / len(olds), 2)
            source_url = next(
                (old_nodes[o][1].get("source_url") for o in olds if old_nodes[o][1].get("source_url")),
                None,
            )
            seeds[number]["nodes"].append(
                {
                    "id": lesson_id,
                    "name": name,
                    "difficulty": difficulty,
                    "description": description,
                    "source_url": source_url,
                    "sections": [{"id": sid, "title": title} for sid, title, _ in sections],
                }
            )

    # --- legacy ---
    for old, (number, _) in old_nodes.items():
        if old in REMOVED:
            seeds[number]["legacy"][old] = None
            continue
        lesson_id, section_id = where[old]
        seeds[module_of[lesson_id]]["legacy"][old] = {"node": lesson_id, "section": section_id}

    # --- рёбра ---
    merged: dict[tuple[str, str, str], dict] = {}
    for seed in sources.values():
        for edge in seed["edges"]:
            if edge["from_id"] in REMOVED or edge["to_id"] in REMOVED:
                report.append(f"ребро к убранной теме: {edge['from_id']} -> {edge['to_id']}")
                continue
            src, dst = where[edge["from_id"]][0], where[edge["to_id"]][0]
            if src == dst:
                continue
            ms, md = module_of[src], module_of[dst]
            if ms == md and order.index(src) > order.index(dst):
                report.append(
                    f"против порядка занятий: {src} -> {dst} "
                    f"({edge['from_id']} -> {edge['to_id']})"
                )
                continue
            if ms > md:
                report.append(f"вперёд между модулями: {src} -> {dst}")
                continue
            key = (src, dst, edge.get("type", "requires"))
            if key in merged:
                merged[key]["hard"] = merged[key]["hard"] or edge.get("hard", True)
                merged[key]["weight"] = max(merged[key]["weight"], edge.get("weight", 1.0))
            else:
                merged[key] = {
                    "from_id": src,
                    "to_id": dst,
                    "type": key[2],
                    "hard": edge.get("hard", True),
                    "weight": edge.get("weight", 1.0),
                }
    for number, lessons in LAYOUT.items():
        ids = [lesson[0] for lesson in lessons]
        for src, dst in zip(ids, ids[1:]):
            if (src, dst, "requires") not in merged:
                merged[(src, dst, "requires")] = {
                    "from_id": src, "to_id": dst, "type": "requires",
                    "hard": False, "weight": CHAIN_WEIGHT,
                }
                report.append(f"мягкое ребро порядка: {src} -> {dst}")
    for edge in merged.values():
        seeds[module_of[edge["to_id"]]]["edges"].append(edge)
    for seed in seeds.values():
        seed["edges"].sort(key=lambda e: (order.index(e["to_id"]), order.index(e["from_id"])))

    # --- задания ---
    for seed in sources.values():
        for item in seed["items"]:
            weights = item["concept_weights"]
            main_old = _main_topic(weights)
            if main_old in REMOVED:
                report.append(f"задание {item['id']} убрано (главная тема {main_old})")
                continue
            main_lesson, section = where[main_old]
            new_weights: dict[str, float] = {main_lesson: weights[main_old]}
            for old, weight in weights.items():
                if old in REMOVED:
                    report.append(f"задание {item['id']}: вес на убранной теме {old} снят")
                    continue
                lesson = where[old][0]
                new_weights[lesson] = max(new_weights.get(lesson, 0.0), weight)
            # Главное занятие — с наибольшим весом и первым в словаре.
            top = max(new_weights.values())
            new_weights[main_lesson] = top
            ordered = {main_lesson: top, **{k: v for k, v in new_weights.items() if k != main_lesson}}
            new_item = {**item, "concept_weights": ordered, "section": section}
            seeds[module_of[main_lesson]]["items"].append(new_item)
    for seed in seeds.values():
        seed["items"].sort(key=lambda i: i["id"])

    # --- анкеты ---
    for number, seed in seeds.items():
        survey = seed["topic"]["survey"]
        block_of_old = {old: block["key"] for block in survey["blocks"] for old in block["concepts"]}
        lessons = LAYOUT[number]
        chosen: dict[str, list[str]] = {block["key"]: [] for block in survey["blocks"]}
        for lesson_id, _, _, sections in lessons:
            votes = Counter(
                block_of_old[old]
                for _, _, topics in sections
                for old in topics
                if old in block_of_old
            )
            if not votes:
                # Тема пришла из другого модуля (visualization_basics) — по соседям.
                raise AssertionError(f"занятие {lesson_id} без блока анкеты")
            key = max(votes, key=lambda k: (votes[k], -list(chosen).index(k)))
            chosen[key].append(lesson_id)
        blocks = []
        for block in survey["blocks"]:
            if not chosen[block["key"]]:
                report.append(f"модуль {number}: блок {block['key']} пуст и убран")
                continue
            blocks.append({**block, "concepts": chosen[block["key"]]})
        kept = {block["key"] for block in blocks}
        survey["blocks"] = blocks
        survey["assumed_by_confident"] = [k for k in survey.get("assumed_by_confident", []) if k in kept]
        survey["foundation"] = [k for k in survey.get("foundation", []) if k in kept]
        seed["topic"]["published"] = number == 1
    seeds[1]["topic"]["survey"]["level_question"] = MODULE1_LEVEL_QUESTION
    seeds[1]["topic"]["survey"]["level_options"][-1] = "Уверенно работаю с pandas"

    return {"seeds": seeds, "report": report}


# --- конспекты модуля 1: временная склейка старых (план 56.3) ---

_FENCE = re.compile(r"^\s*(```|~~~)")


def _demote(text: str) -> str:
    """Заголовки старого конспекта — на два уровня ниже (вне блоков кода).

    Заголовок раздела занятия — h2, поэтому h1/h2 старой темы не должны с ним
    спорить.
    """
    lines, fenced = [], False
    for line in text.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
        elif not fenced and re.match(r"^#{1,4} ", line):
            line = "##" + line
        lines.append(line)
    return "\n".join(lines).strip()


def module1_theory() -> dict[str, str]:
    folder = SOURCE / "theory" / "topic01"
    texts: dict[str, str] = {}
    for lesson_id, name, description, sections in LAYOUT[1]:
        parts = [f"# {name}", "", description, ""]
        for _, title, old_topics in sections:
            parts += [f"## {title}", ""]
            notes = [folder / f"{old}.md" for old in old_topics]
            notes = [path for path in notes if path.is_file()]
            if not notes:
                parts += ["Раздел готовится — тьютор объяснит его в чате.", ""]
            for path in notes:
                parts += [_demote(path.read_text(encoding="utf-8")), ""]
        texts[lesson_id] = "\n".join(parts).rstrip() + "\n"
    return texts


def main() -> None:
    result = build()
    for number, seed in result["seeds"].items():
        path = TARGET / f"seed_topic{number:02d}.json"
        path.write_text(json.dumps(seed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    folder = TARGET / "theory" / "topic01"
    for old in folder.glob("*.md"):
        old.unlink()
    for lesson_id, text in module1_theory().items():
        (folder / f"{lesson_id}.md").write_text(text, encoding="utf-8")
    for line in result["report"]:
        print(line)


if __name__ == "__main__":
    main()
