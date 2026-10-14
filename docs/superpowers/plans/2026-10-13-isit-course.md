# Второй курс «Машинное обучение для ИСИТ» — Implementation Plan

> **For agentic workers:** Steps use checkbox (`- [ ]`) syntax for tracking.
> Спека — `docs/superpowers/specs/2026-10-13-isit-course-design.md` (ссылки «§N»
> ведут туда). Каждый срез — TDD: сначала падающие тесты, потом код и
> содержание. После среза — адверсариальное ревью отдельным агентом (находки
> воспроизводятся запуском кода, ревьюер ничего не правит) → триаж → фиксы
> отдельным коммитом `Аудит среза N: …` с регрессионными тестами.
> Журнал исполнения — `.superpowers/sdd/2026-10-13-isit-course/progress.md`.

**Goal:** рядом с mlcourse в тьюторе появляется второй курс — «Машинное
обучение для ИСИТ»: 9 модулей, 39 занятий, 96 разделов, конспекты и банк
заданий, написанные заново по мотивам репозитория `Eldervald/isit_ml_labs`.

**Architecture:** содержание — только данные (`data/isit/seed_topicNN.json` +
`data/isit/theory/topicNN/<занятие>.md`), код приложения не меняется, кроме
регистрации курса (поле `Course.data_dir`, запись в `COURSES`) и флага
`--data-dir` у проверки seed. Конспекты и задания пишутся в том же формате, что
у mlcourse, и проверяются теми же валидаторами.

**Tech Stack:** прежний; ничего нового не добавляется.

---

## 1. Срезы

### Срез 1. Каркас второго курса

- [ ] `web/courses.py`: поле `data_dir: Path | None = None` у `Course`;
      `course_data` для курса с `data_dir` зовёт `course_paths(course.data_dir)`
      (glob — при первом обращении, а не на импорте); запись
      `Course(id="isit-ml", …, data_dir=DATA_DIR / "isit")`.
- [ ] `course/seed.py`: `main` получает `--data-dir PATH` (взаимоисключающе с
      `--seed`), проверка печатает выбранный каталог.
- [ ] Шаблоны: «Тьютор mlcourse.ai» → нейтральное «Тьютор по машинному
      обучению» (`login.html`, `register.html`, `forgot_password.html`,
      `reset_password.html`, `home.html`), текст «Пока доступен один курс» —
      про два курса.
- [ ] Тесты: `tests/test_web_courses.py` — каталог показывает оба курса,
      запись на `isit-ml` открывает свой файл БД, `course_data` не смешивает
      модули; `tests/test_isit_course.py` — каркас (наполняется по мере срезов).
- [ ] Пустой каталог `data/isit/` с первым seed-файлом не падает: `course_data`
      курса без файлов даёт понятную ошибку.

**Проверка:** `uv run pytest -q tests/test_web_courses.py tests/test_isit_course.py`.

### Срез 2. Модуль 1, занятия 1–3 (эталон стиля)

Модуль 1 — `lab1-python-intro`, 13 заданий, 9 занятий (спека §3). Первые три
задают стиль: `first_steps`, `branches`, `errors`.

- [ ] `data/isit/seed_topic01.json` (черновик): `course: "isit/topic01"`,
      `topic` с анкетой (блоки накрывают все 9 занятий модуля ровно один раз),
      `nodes` — все 9 занятий с разделами, `edges` — цепочка «вперёд» внутри
      модуля, `published: false`.
- [ ] Конспекты трёх занятий: `first_steps.md`, `branches.md`, `errors.md`
      (спека §4).
- [ ] Задания трёх занятий: 2+ на раздел, по одному итоговому; для `hello`,
      `middle`, `fizz_buzz`, `asserts`, `exceptions` — задания с кодом
      (`starter`/`setup`/`tests`, `rubric_id`).
- [ ] `tests/test_seed_code_items.py`: эталоны, `WRONG`/`MORE_WRONG` и верные
      альтернативы на каждое новое код-задание; тесты конспектов (исполняемые
      примеры).
- [ ] `tests/test_isit_course.py`: модуль 1 грузится, 9 занятий, 23 раздела,
      анкета накрывает занятия, у каждого раздела ≥2 задания.

**Проверка:** `uv run python -m llm_tutor.course.seed --data-dir data/isit` →
чисто; `uv run pytest -q tests/test_isit_course.py tests/test_seed_code_items.py`.

### Срез 3. Модуль 1, занятия 4–6

- [ ] Конспекты и задания `iteration` (3 раздела), `comprehensions`,
      `recursion`; код-задания по `f_iterate_me` (9 функций), `g_comprehensions`,
      `h_traverse_dictionary`.
- [ ] Эталоны и неверные решения в `tests/test_seed_code_items.py`.

**Проверка:** `uv run pytest -q tests/test_isit_course.py tests/test_seed_code_items.py`.

### Срез 4. Модуль 1, занятия 7–9, публикация

- [ ] Конспекты и задания `toolbox`, `own_types`, `wrappers`; код-задания по
      `j_warm_up`, `k_orders`, `l_typy_generic`, `m_lru_cache`.
- [ ] `published: true` у модуля 1; `uv run python -m llm_tutor.course.seed
      --data-dir data/isit` не печатает «Разделы, где меньше 2 заданий»,
      «Занятия без итогового задания», «Без конспекта».
- [ ] Глазами в браузере: ученик записывается на курс, проходит анкету модуля 1,
      закрывает занятие.

**Проверка:** проверка seed + браузер.

### Срезы 5–12. Модули 2–9 (по срезу на модуль)

Порядок и состав — спека §3. В каждом срезе одинаковый набор шагов:

- [ ] **Срез 5.** Модуль 2 «NumPy, pandas и данные» (5 заданий lab2 без
      `e_validation`): `arrays`, `missing`, `titanic`, `imdb`; `titanic.csv`
      встраивается в `setup`, `imdb.csv` — выборкой.
- [ ] **Срез 6.** Модуль 3 «Линейная регрессия» (lab3): `metrics`, `ols`,
      `regularization`.
- [ ] **Срез 7.** Модуль 4 «Логистическая регрессия» (lab4): `sigmoid`,
      `gradient`, `boundary`.
- [ ] **Срез 8.** Модуль 5 «Деревья решений» (lab5): `impurity`, `cart`,
      `overfitting`, `interpretation`.
- [ ] **Срез 9.** Модуль 6 «Ансамбли: случайный лес» (lab6): `cross_validation`,
      `bagging`, `practice`, `anomalies`.
- [ ] **Срез 10.** Модуль 7 «k-NN, K-Means и размерность» (lab7): `knn`,
      `dim_reduction`, `kmeans`, `practice`.
- [ ] **Срез 11.** Модуль 8 «Градиентный бустинг» (lab8): `idea`,
      `implementation`, `hyperparameters`, `practice`.
- [ ] **Срез 12.** Модуль 9 «Дисбаланс классов и Pipeline» (lab9): `metrics`,
      `resampling`, `curves`, `pipelines`.

Шаги внутри каждого модульного среза: seed с `published: false` → конспекты →
банк заданий → код-задания с эталонами и неверными решениями → сверка эталонов
запуском (значения из ноутбука пересчитываются на данных из `setup`) →
`published: true` → проверка.

**Проверка:** `uv run python -m llm_tutor.course.seed --data-dir data/isit` и
`uv run pytest -q tests/test_isit_course.py tests/test_seed_code_items.py`.

### Срез 13. Сквозной сценарий и документы

- [ ] `tests/e2e/test_isit_course.py`: каталог с двумя курсами, запись на
      `isit-ml`, первое занятие, задание с кодом в Pyodide.
- [ ] `README.md`, `CLAUDE.md`, `problems.md`: второй курс, каталог данных,
      чем он отличается от mlcourse.
- [ ] Общее ревью содержания: расхождения с первоисточником, лимит 10 с,
      версия sklearn.

**Проверка:** `uv run pytest -q` и `uv run pytest -q -m e2e tests/e2e`.

## 2. Риски

- **10 секунд** на прогон кода в браузере — держать данные маленькими
  (спека §5).
- **API sklearn ≤ 1.6** в Pyodide 0.27.8.
- **Объём**: 39 занятий и ~230 заданий; каждый модуль — отдельный срез с ревью,
  работа растянется на много сессий.
- **Расхождение эталонов** с числами из ноутбуков: считаем заново на данных
  из `setup` и фиксируем в конспекте.
- **Анкета модуля** обязана накрывать все занятия модуля ровно один раз —
  при правке состава занятий анкету надо править вместе (`seed.py:_check_topics`).

## 3. Проверка вживую

```bash
uv run python -m llm_tutor.course.seed --data-dir data/isit
uv run pytest -q
uv run python -m llm_tutor.web
```

1. `/courses` — два курса; записаться на «Машинное обучение для ИСИТ».
2. Пройти анкету модуля 1, открыть занятие, ответить на вопрос раздела.
3. Вкладка «Код»: запустить заготовку → падает с понятным текстом; решить →
   «Проверить» → задание зачтено.
4. Дойти до итогового задания, закрыть занятие, увидеть следующее.
