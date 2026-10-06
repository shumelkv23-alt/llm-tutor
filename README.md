# LLM-тьютор по курсу машинного обучения

Telegram-бот-тьютор на основе LLM, который ведёт ученика по курсу
[mlcourse.ai](https://mlcourse.ai) (пилот — topic01 «Pandas / Exploratory Data Analysis»).

## Стек

- Python 3.12, [uv](https://docs.astral.sh/uv/)
- aiogram 3.x (Telegram-бот)
- OpenRouter API (LLM, слой абстракции модели через конфиг)
- SQLite + FTS5 (RAG и хранилище состояния ученика)
- NetworkX (граф концептов курса)

## Быстрый старт

```bash
uv sync                     # установить зависимости
# заполни OPENROUTER_API_KEY и TELEGRAM_BOT_TOKEN в .env:
#   Windows (cmd/PowerShell): copy .env.example .env
#   macOS/Linux (bash):       cp .env.example .env

# загрузить материалы темы в RAG-индекс (один раз; можно URL или файл):
uv run python -m llm_tutor.course.ingest data/raw/topic01_pandas_data_analysis.md

uv run python -m llm_tutor.bot.main   # запустить бота
```

Граф темы и банк заданий (`data/seed_topic01.json`) грузятся в БД автоматически
при старте бота.

Команды бота:

- `/start` — знакомство: сначала короткая анкета (опыт, цель, время), потом тьютор
- `/diagnostic` — «подбор маршрута»: несколько коротких заданий с автопроверкой
- `/plan` — готовые узлы маршрута с режимом прохода

Задания (`choice`/`short`) проверяются кодом, без обращения к модели. Чтобы
дополнить банк — добавь объект в массив `items` в `data/seed_topic01.json`
(`answer` для `choice` — индекс верного варианта строкой); он подхватится при
следующем старте, а убранные вопросы из банка исчезнут.

## Тесты

```bash
uv run pytest
```

Подробнее — `llm-tutor-architecture.md` и `llm-tutor-implementation-plan.md`.
