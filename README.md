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
# заполни OPENROUTER_API_KEY в .env:
#   Windows (cmd/PowerShell): copy .env.example .env
#   macOS/Linux (bash):       cp .env.example .env
uv run python -m llm_tutor.bot.main   # запустить бота (после Среза 1)
```

## Тесты

```bash
uv run pytest
```

Подробнее — `llm-tutor-architecture.md` и `llm-tutor-implementation-plan.md`.
