"""Конфигурация приложения через переменные окружения (pydantic-settings)."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Корень проекта (два уровня вверх от src/llm_tutor/config.py) — не зависит от cwd.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Дефолты сборки контекста (Срез 3) — единый источник для Settings и core.context.
DEFAULT_DIALOG_TAIL = 8
DEFAULT_RAG_TOP_K = 4
# Бюджет пакета контекста в токенах (Срез 5). При нехватке режется материал,
# затем хвост диалога; правила, профиль и состояние не режутся.
DEFAULT_CONTEXT_BUDGET_TOKENS = 8000


class Settings(BaseSettings):
    """Настройки, читаемые из `.env` и переменных окружения.

    Слой абстракции модели: смена модели = правка `.env`, не кода.
    """

    model_config = SettingsConfigDict(
        env_file=_PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        # «Запретить лишнее»: опечатка в имени переменной .env (напр. TUTOR_MDOEL)
        # упадёт, а не молча останется дефолтом.
        extra="forbid",
        # Не светить значения секретов в тексте ValidationError (утечка через stderr).
        hide_input_in_errors=True,
    )

    # --- OpenRouter / LLM ---
    openrouter_api_key: SecretStr
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    tutor_model: str = "anthropic/claude-sonnet-4.5"
    grader_model: str = "anthropic/claude-sonnet-4.5"
    extractor_model: str = "anthropic/claude-sonnet-4.5"

    llm_temperature: float = 0.4
    llm_max_retries: int = 1
    llm_max_tokens: int = 2048

    # --- Telegram ---
    telegram_bot_token: SecretStr

    # --- Хранилище ---
    db_path: str = "data/llm_tutor.sqlite3"

    # --- Модель ученика (Beta-счётчики), Срез 4 ---
    beta_prior_alpha: float = 1.0
    beta_prior_beta: float = 1.0
    beta_decay_lambda: float = 0.05
    beta_propagate_weight: float = 0.15

    # --- Планировщик: веса приоритета, Срез 4 ---
    priority_w1: float = 0.25
    priority_w2: float = 0.25
    priority_w3: float = 0.20
    priority_w4: float = 0.15
    priority_w5: float = 0.10
    priority_w6: float = 0.05

    # --- Пороги режимов узла, Срез 4 ---
    mastery_skip_threshold: float = 0.90
    mastery_verify_threshold: float = 0.75
    mastery_compressed_threshold: float = 0.50

    # --- Адаптивная диагностика, Срез 4.5 ---
    diagnostic_max_questions: int = 20
    diagnostic_uncertainty_threshold: float = 0.15
    # Длина одного захода: первый заход шире, последующие короткие.
    diagnostic_first_pass: int = 5
    diagnostic_followup: int = 2
    # Пауза перед повторным вопросом (дни). Она же — окно свежести
    # свидетельства: отвеченное недавно не предлагается, иначе серию чистых
    # ответов набирали бы переспрашиванием одного вопроса подряд.
    item_repeat_cooldown_days: float = 1.0

    # --- Анкета холодного старта, Срез 4.5 ---
    # Вес самооценки (source='self') — слабое свидетельство. Границы важны:
    # отрицательный вес сломал бы запись события прямо посреди хода.
    self_evidence_weight: float = Field(default=0.3, ge=0.0, le=1.0)

    # --- Рубричный грейдер, Срез 6 ---
    # Вердикт модели по открытому ответу — свидетельство ограниченного веса:
    # ни одно «высокоставочное» решение (например, пропуск темы) не должно
    # приниматься только по нему (§9.2 архитектуры).
    rubric_evidence_weight: float = Field(default=0.5, ge=0.0, le=1.0)

    # --- Маршрут и ведение, Срезы 9–10 ---
    # Сколько шагов должно измениться, чтобы сообщать о пересмотре маршрута
    # (§6.4: «если различия малы — оставить старый план»).
    route_min_significant_changes: int = Field(default=2, ge=1)
    # Сколько заданий подряд без подсказок закрывают узел (§6.1). В серию
    # идут только задания, отвеченные ВПЕРВЫЕ: повтор после показа ответа
    # независимым свидетельством не является.
    guide_success_streak: int = Field(default=1, ge=1)
    # Проход «закрой тему» (/close) — та же серия, но длиннее: ученик сам
    # просит засчитать тему, одного ответа мало (отложенное среза 28, D2).
    close_success_streak: int = Field(default=2, ge=1)
    # Вес повторного ответа на уже отвеченное задание: знания могло и не
    # прибавиться, поэтому свидетельство слабее.
    repeat_evidence_weight: float = Field(default=0.3, ge=0.0, le=1.0)

    # --- Сборка контекста, Срез 3 ---
    context_dialog_tail: int = DEFAULT_DIALOG_TAIL
    context_rag_top_k: int = DEFAULT_RAG_TOP_K
    context_budget_tokens: int = DEFAULT_CONTEXT_BUDGET_TOKENS

    @field_validator("openrouter_api_key", "telegram_bot_token")
    @classmethod
    def _secret_must_not_be_empty(cls, v: SecretStr, info: ValidationInfo) -> SecretStr:
        """Пустой секрет — сразу ясная ошибка, а не невнятный отказ провайдера."""
        if not v.get_secret_value().strip():
            raise ValueError(
                f"{info.field_name.upper()} пуст. Заполни его в .env (см. .env.example)."
            )
        return v


@lru_cache
def get_settings() -> Settings:
    """Синглтон настроек: чтение `.env` происходит один раз за процесс."""
    return Settings()
