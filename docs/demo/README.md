# Показ приложения: справочник докладчика

Наглядные диаграммы и живой пример собраны на странице-артефакте «Разбор llm_tutor».
Здесь — то, что нужно держать под рукой: подготовка, сценарий, запросы к базе,
маршрут чтения кода и ответы на неудобные вопросы.

## Подготовка за 5 минут

```bash
uv sync
# 1. Демо-ученики без ключа модели: Аня (3 занятия закрыты) и Борис (2, с ошибками)
uv run python scripts/demo_students.py data/demo

# 2. Приложение с настоящей моделью поверх демо-данных
ACCOUNTS_DB_PATH=data/demo/accounts.sqlite3 USERS_DIR=data/demo/users \
  MATERIALS_DB_PATH=data/demo/materials.sqlite3 uv run python -m llm_tutor.web

# 3. Админ для раздела /admin
ACCOUNTS_DB_PATH=data/demo/accounts.sqlite3 USERS_DIR=data/demo/users \
  uv run python -m llm_tutor.web.cli make-admin --email ann@example.com
```

Пароль обоих демо-учеников — `секретный-пароль`. Для чистого ученика зарегистрируйся
в браузере: живой ввод убедительнее готовых данных. Нужен ключ `OPENROUTER_API_KEY` в `.env`.

База ученика: `data/demo/users/<id>/mlcourse.sqlite3`. Узнать `<id>`:
`SELECT id, email FROM users;` в `data/demo/accounts.sqlite3`. В контейнере без
консоли `sqlite3` открывай базу так: `uv run python -m sqlite3 <файл>`.

## Сценарий на 12 минут

| Время | Что показать | Где |
|---|---|---|
| 0:00 | Тезис: «LLM — интерфейс, код — источник истины» | рис. 1 страницы |
| 1:00 | Карта знаний: заливка = оценка, контур = решение маршрута | `/profile` |
| 3:00 | Ответить неверно, затем верно; сравнить `events` и `mastery` до и после | `/lesson/mlcourse` + терминал |
| 6:00 | «Не понял» → `reinforce`, `hint_level` +1; выделить фрагмент → `messages.meta` | чат |
| 8:00 | «Закрыть занятие» — проход без подсказок | кнопка в уроке |
| 9:00 | Повторение: сдвинуть срок SQL, ответить верно и неверно | вкладка «Повторение» |
| 11:00 | Админка: воронка и карточка ученика | `/admin/students` |

## Запросы к базе ученика

```sql
-- последние свидетельства
SELECT id, datetime(ts,'unixepoch') t, concept_id, item_id, source, result, weight
  FROM events ORDER BY id DESC LIMIT 10;

-- оценка владения: среднее и неопределённость
SELECT concept_id, round(alpha,2) a, round(beta,2) b,
       round(alpha/(alpha+beta),2) mean,
       round(sqrt(alpha*beta/((alpha+beta)*(alpha+beta)*(alpha+beta+1))),3) sigma
  FROM mastery ORDER BY mean DESC;

-- расписание повторений
SELECT concept_id, step, datetime(due_at,'unixepoch') FROM review_schedule;

-- состояние занятия
SELECT json_extract(state,'$.current_node_id') node, json_extract(state,'$.mode') mode,
       json_extract(state,'$.hint_level') hint, json_extract(state,'$.passed_sections') passed
  FROM sessions;

-- диалог и метки цитат
SELECT role, substr(content,1,60), meta FROM messages ORDER BY id DESC LIMIT 6;

-- «машина времени» для повторения: срок наступил 3 дня назад
UPDATE review_schedule SET due_at = due_at - 3*86400;
```

Сдвиг срока работает без перезапуска: расписание читается из базы на каждый запрос.
Верный ответ повторения: ступень 0→1, срок +3 дня. Неверный: ступень −2 (не ниже 0), срок +1 день.

## Маршрут чтения кода (перед показом)

1. `CLAUDE.md` — карта слоёв и «подводные камни».
2. `core/turn.py`: `handle_turn` (развилка ответ / тьютор), `post_turn` (один коммит), `item_evidence` (веса источников).
3. `student/diagnostic.plan_evidence` и `student/beta.py` — как событие становится α/β, забывание, распространение.
4. `student/guide.py` — критерий закрытия занятия, правило двух попыток.
5. `student/planner.py`, `student/route.py` — режимы узла, маршрут, статусы.
6. `student/review.py` и `core/review.py` — лестница повторений, разминка, серия.
7. `core/context.py` и `llm/prompts.py` — что именно видит модель; `student/hints.py` — потолок подсказки.
8. `grader/rubric.py` — как модель оценивает открытый ответ, а код проверяет цитаты.
9. `web/api_lesson.py`, `web/userdb.py` — как всё это стоит за HTTP, замок ученика, файл базы на ученика.
10. `migrations/*.sql` — схема целиком.

## Чего в приложении нет (не обещать)

- **Режима «проект».** Есть итоговое задание занятия и кейсовые занятия («EDA на практике», «Кейсы: деревья и kNN»).
- **Свидетельств из диалога.** `events.source = 'dialogue'` ничем не создаётся.
- **Заблуждений.** Таблица `misconceptions` и настройка `extractor_model` не используются; резюме сессий (`sessions.summary`) тоже.
- **Адаптивной глубины урока по режиму планировщика.** Режимы `full`, `compressed`, `revisit` считаются и хранятся в снимке маршрута, но урок от них не меняется (глубина идёт из самооценки анкеты). Реально меняют ход `skip`, `verify` и `reinforce`.
- **Защиты от «перебора».** Только вес повторного ответа ×0.3 и признак «была помощь» в проверочном проходе; `events.hints_used` и `time_spent` не заполняются.
- **Калибровки порогов и лестницы** на реальных данных: все числа — стартовые эвристики из `config.py` и `student/review.py`.

## Вопросы, которые зададут

| Вопрос | Ответ |
|---|---|
| Почему занятие закрыто при оценке 0.68? | Закрытие — правило прохождения (разделы + итоговое), оценка — мера уверенности. Запасной путь: mean ≥ 0.90 при σ ≤ 0.10. |
| Модель может завысить оценку? | Нет: балл за открытый ответ считает код по критериям, цитата должна быть подстрокой ответа, вес источника 0.5; за выбор и код вердикт даёт код. |
| Что если модель недоступна? | Вопросы с вариантами, код и расписание работают; урок, чат и рубрика получат «не смог получить ответ». |
| Как проверяли качество грейдера? | `uv run python -m llm_tutor.eval.grader_eval` на золотом наборе `data/golden_set_topic01.json` (нужен ключ). Набор мал, разметка одним человеком. |
| Масштаб? | Один воркер uvicorn (замок в процессе); админка обходит базы учеников (~6 мс на ученика, кэш 60 с). |
