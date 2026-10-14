"""Конфигурация приложения через переменные окружения (pydantic-settings)."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Корень проекта (два уровня вверх от src/llm_tutor/config.py) — не зависит от cwd.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Дефолты сборки контекста (Срез 3) — единый источник для Settings и core.context.
DEFAULT_DIALOG_TAIL = 8
DEFAULT_RAG_TOP_K = 4
# Бюджет пакета контекста в токенах (Срез 5). При нехватке режется материал,
# затем хвост диалога; правила, профиль и состояние не режутся.
DEFAULT_CONTEXT_BUDGET_TOKENS = 8000


# Переменные, которые приложение больше не читает (бот, его диагностика и
# его БД удалены в срезе 49). В старом .env они остались — при «запретить лишнее»
# веб не запустился бы, поэтому их молча пропускаем. Опечатки по-прежнему падают.
RETIRED_SETTINGS = frozenset(
    {
        "telegram_bot_token",
        "db_path",
        "diagnostic_max_questions",
        "diagnostic_uncertainty_threshold",
        "diagnostic_first_pass",
        "diagnostic_followup",
        "item_repeat_cooldown_days",
    }
)


def secret_value(secret: SecretStr | None) -> str:
    """Значение секрета строкой; ``None`` — пустая строка.

    ``model_copy(update=...)`` не прогоняет валидацию, и в поле секрета может
    оказаться обычная строка — так делают тесты. Падать на этом не должны ни
    ``Settings.smtp_configured``, ни сборка мейлера.
    """
    if secret is None:
        return ""
    return secret.get_secret_value() if isinstance(secret, SecretStr) else str(secret)


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
    llm_max_tokens: int = 3072

    # --- Веб-приложение, Срез 37 ---
    web_host: str = "127.0.0.1"
    web_port: int = Field(default=8000, ge=1, le=65535)
    # Аккаунты, веб-сессии и записи на курсы — общая БД; у каждого ученика
    # своя БД курса в users_dir (спека веба §5).
    accounts_db_path: str = "data/accounts.sqlite3"
    users_dir: str = "data/users"
    # Материалы RAG — общие для всех учеников, копируются в БД ученика.
    materials_db_path: str = "data/materials.sqlite3"
    registration_open: bool = True
    # За HTTPS (обратный прокси) — true: cookie сессии не уйдёт по HTTP.
    cookie_secure: bool = False
    # Pyodide (Python в браузере) для вкладки «Код»: дистрибутив с pandas.
    # Свой адрес — для зеркала или офлайн-копии; CSP разрешит именно его.
    pyodide_index_url: str = "https://cdn.jsdelivr.net/pyodide/v0.27.8/full/"

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
    # Проход «закрой тему» — та же серия, но длиннее: ученик сам
    # просит засчитать тему, одного ответа мало (отложенное среза 28, D2).
    close_success_streak: int = Field(default=2, ge=1)
    # Вес повторного ответа на уже отвеченное задание: знания могло и не
    # прибавиться, поэтому свидетельство слабее.
    repeat_evidence_weight: float = Field(default=0.3, ge=0.0, le=1.0)

    # --- Почта: восстановление пароля по ссылке, Срез 71 ---
    # У всех полей есть дефолт: старый .env (без почтовых строк) запускается
    # как раньше — extra="forbid" ловит опечатки в именах, а не отсутствие.
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_user: str | None = None
    # «Пароль приложения» Google (16 символов), а не пароль аккаунта: обычный
    # пароль сервер отвергает с 535. Секрет — как OPENROUTER_API_KEY.
    smtp_password: SecretStr | None = None
    smtp_starttls: bool = True
    smtp_timeout: float = Field(default=10.0, gt=0)
    # Отправитель письма; по умолчанию — SMTP_USER (Gmail всё равно подменит
    # адрес на владельца аккаунта). Имя — то, что видит ученик.
    mail_from: str | None = None
    mail_from_name: str = "llm·tutor"
    # Абсолютный адрес в письме — из настроек, а не из request.base_url: за
    # прокси заголовок Host подконтролен клиенту, и ученику ушла бы ссылка
    # на чужой хост.
    public_base_url: str = "http://127.0.0.1:8000"
    password_reset_ttl_minutes: int = Field(default=60, ge=5, le=1440)
    # Запросов письма на один адрес в окне лимитера (security.LoginLimiter).
    password_reset_limit: int = Field(default=3, ge=1)

    # --- Сборка контекста, Срез 3 ---
    context_dialog_tail: int = DEFAULT_DIALOG_TAIL
    context_rag_top_k: int = DEFAULT_RAG_TOP_K
    context_budget_tokens: int = DEFAULT_CONTEXT_BUDGET_TOKENS

    @field_validator("openrouter_api_key")
    @classmethod
    def _secret_must_not_be_empty(cls, v: SecretStr, info: ValidationInfo) -> SecretStr:
        """Пустой секрет — сразу ясная ошибка, а не невнятный отказ провайдера."""
        if not v.get_secret_value().strip():
            raise ValueError(
                f"{info.field_name.upper()} пуст. Заполни его в .env (см. .env.example)."
            )
        return v

    @model_validator(mode="before")
    @classmethod
    def _drop_retired(cls, data: object) -> object:
        """Убирает выведенные из употребления переменные (``RETIRED_SETTINGS``)."""
        if isinstance(data, dict):
            return {key: value for key, value in data.items() if str(key).lower() not in RETIRED_SETTINGS}
        return data

    @field_validator("smtp_host", "mail_from_name", "public_base_url")
    @classmethod
    def _strip_text(cls, v: str) -> str:
        """Пробелы по краям почтовых полей — след копипаста из .env (аудит среза 71).

        Незачищенный ``SMTP_HOST`` уводит соединение в ``gaierror`` уже на первом
        письме, а имя отправителя с переносом строки роняет сборку письма.
        """
        return v.strip()

    @field_validator("smtp_user", "mail_from", mode="after")
    @classmethod
    def _strip_optional_text(cls, v: str | None) -> str | None:
        """Пробельный адрес — это отсутствие адреса, а не адрес из пробелов.

        Иначе ``MAIL_FROM='   '`` (значение истинное!) оставлял в письме
        ``From: llm·tutor`` без адреса — такое письмо сервер не примет.
        """
        return (v or "").strip() or None

    @property
    def smtp_configured(self) -> bool:
        """Отправка возможна, только когда есть хост, адрес и непустой пароль.

        Пустой или пробельный пароль — та же неготовность, что и отсутствие:
        иначе приложение падало бы уже на первом письме.
        """
        if not self.smtp_host.strip():
            return False
        if not self.smtp_user:
            return False
        return bool(secret_value(self.smtp_password).strip())


@lru_cache
def get_settings() -> Settings:
    """Синглтон настроек: чтение `.env` происходит один раз за процесс."""
    return Settings()
