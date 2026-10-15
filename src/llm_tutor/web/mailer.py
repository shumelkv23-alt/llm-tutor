"""Почта: письмо со ссылкой восстановления пароля (Срез 71).

Отправка — через SMTP-сервер (по умолчанию Gmail); новых зависимостей нет:
``smtplib`` и ``email.message`` из стандартной библиотеки, вызов — в отдельном
потоке, чтобы сеть не задерживала цикл событий.

Пока почта не настроена (нет ``SMTP_USER``/``SMTP_PASSWORD``), приложение
работает в **режиме показа**: ``LogMailer`` печатает письмо целиком, и сценарий
восстановления проходится насквозь — токен берётся из журнала сервера.
"""

import asyncio
import logging
import smtplib
from email.message import EmailMessage
from email.utils import formataddr
from typing import Protocol

from llm_tutor.config import Settings, secret_value

logger = logging.getLogger(__name__)

# Подсказка про «пароль приложения»: Gmail отвечает 535 на пароль аккаунта,
# и без этой строки причина отказа неочевидна.
AUTH_HELP = (
    "Gmail принимает только «пароль приложения» из 16 символов "
    "(myaccount.google.com → Безопасность → Двухэтапная аутентификация → "
    "«Пароли приложений»), а не пароль аккаунта; впиши его в SMTP_PASSWORD."
)

MAIL_HELP = (
    "Отправка писем не настроена: письмо печатается здесь, ученику оно не уйдёт. "
    "Для настоящей отправки заполни в .env SMTP_USER и SMTP_PASSWORD. "
    + AUTH_HELP
)


class Mailer(Protocol):
    """Почта приложения: одно письмо простым текстом."""

    async def send(self, *, to: str, subject: str, text: str) -> None: ...

    async def aclose(self) -> None: ...


class SmtpMailer:
    """Письма через SMTP-сервер; соединение открывается на каждое письмо.

    Ошибку отправки наружу не отдаёт: письмо уходит фоновой задачей, и падение
    SMTP не должно ломать ответ ученику — пишем в журнал класс исключения и
    SMTP-код (535 у Gmail), но **не** пароль и не текст письма.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        user: str,
        password: str,
        starttls: bool = True,
        sender: str | None = None,
        sender_name: str = "llm·tutor",
        timeout: float = 10.0,
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.starttls = starttls
        # Gmail подменяет отправителя на владельца аккаунта, поэтому по
        # умолчанию пишем тот же адрес, с которого отправляем.
        self.sender = sender or user
        self.sender_name = sender_name
        self.timeout = timeout

    def letter(self, *, to: str, subject: str, text: str) -> EmailMessage:
        """Письмо целиком (одна часть, UTF-8): кириллица в теме и теле.

        Тело — ``base64``: письмо остаётся 7-битным (``us-ascii``) и переживает
        любой промежуточный сервер. С ``8bit`` в теле лежали бы сырые байты
        UTF-8 без объявления ``BODY=8BITMIME`` (аудит среза 71): Gmail такое
        съедает, релей без 8BITMIME — искалечил бы ссылку. Тема и так уезжает
        закодированным словом RFC 2047.
        """
        message = EmailMessage()
        message["From"] = formataddr((self.sender_name, self.sender))
        message["To"] = to
        message["Subject"] = subject
        message.set_content(text, charset="utf-8", cte="base64")
        return message

    def _send_sync(self, letter: EmailMessage) -> None:
        with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as smtp:
            smtp.ehlo()
            if self.starttls:
                # После STARTTLS возможности сервера другие — EHLO повторяем.
                smtp.starttls()
                smtp.ehlo()
            smtp.login(self.user, self.password)
            smtp.send_message(letter)

    async def send(self, *, to: str, subject: str, text: str) -> None:
        # Сборка письма — внутри try: перевод строки в заголовке (напр. перенос
        # в MAIL_FROM_NAME из .env) даёт ValueError, и он не должен пролететь
        # наружу через фоновую задачу (аудит среза 71).
        try:
            letter = self.letter(to=to, subject=subject, text=text)
            await asyncio.to_thread(self._send_sync, letter)
        except Exception as exc:  # noqa: BLE001 — письмо фоновое, наружу нельзя
            hint = f" {AUTH_HELP}" if isinstance(exc, smtplib.SMTPAuthenticationError) else ""
            logger.error(
                "Письмо для %s не отправлено (%s): %s.%s",
                to,
                type(exc).__name__,
                exc,
                hint,
            )

    async def aclose(self) -> None:
        """Соединение живёт одно письмо — закрывать нечего."""


class LogMailer:
    """Режим показа: письмо целиком в журнал (уровень WARNING).

    Единственное место, где токен восстановления попадает в журнал, и только
    тогда, когда почта не настроена.
    """

    async def send(self, *, to: str, subject: str, text: str) -> None:
        logger.warning("Письмо не отправлено (почта не настроена).\nКому: %s\nТема: %s\n%s", to, subject, text)

    async def aclose(self) -> None:
        """Ничего не держит."""


async def send_safely(mailer: Mailer, *, to: str, subject: str, text: str) -> None:
    """Отправка фоновой задачей: падение почты не должно всплыть наружу.

    Задача уходит в ``BackgroundTasks`` уже после ответа ученику, и её
    исключение никто не прочтёт — оно всплывёт как «необработанная ошибка»
    (или порвёт тестовый клиент). Поэтому последний рубеж здесь, а не в
    ``SmtpMailer``: любой мейлер имеет право ошибиться.
    """
    try:
        await mailer.send(to=to, subject=subject, text=text)
    except Exception:  # noqa: BLE001 — письмо фоновое, наружу нельзя
        logger.exception("Письмо для %s не отправлено", to)


def build_mailer(settings: Settings) -> Mailer:
    """Мейлер по настройкам: настроен SMTP — настоящий, иначе режим показа."""
    if not settings.smtp_configured:
        return LogMailer()
    assert settings.smtp_user is not None and settings.smtp_password is not None
    return SmtpMailer(
        host=settings.smtp_host,
        port=settings.smtp_port,
        user=settings.smtp_user,
        # Пробелы по краям — след копипаста «пароля приложения»; сам пароль
        # пробелов внутри не содержит (Gmail печатает его как 4 группы по 4).
        password=secret_value(settings.smtp_password).strip(),
        starttls=settings.smtp_starttls,
        sender=settings.mail_from,
        sender_name=settings.mail_from_name,
        timeout=settings.smtp_timeout,
    )
