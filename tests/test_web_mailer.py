"""Почта: письмо, порядок команд SMTP, ошибки без секретов (Срез 71)."""

import logging
import smtplib
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import parseaddr

import pytest

from llm_tutor.config import Settings
from llm_tutor.web import mailer

LETTER = {
    "to": "ann@example.com",
    "subject": "Восстановление пароля в llm·tutor",
    "text": "Здравствуйте!\n\nСсылка: http://127.0.0.1:8000/reset-password?token=секрет\n",
}


class FakeSmtp:
    """Подставной SMTP-сервер: запоминает команды, никуда не ходит.

    ``__init__`` падает, если ``fail_connect``; ``login`` — если пароль
    совпал с ``bad_password``.
    """

    calls: list[str] = []
    message: EmailMessage | None = None
    bad_password: str | None = None
    fail_connect = False

    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        if FakeSmtp.fail_connect:
            raise OSError("нет связи с почтовым сервером")
        self.host = host
        self.port = port
        FakeSmtp.calls.append(f"connect {host}:{port} timeout={timeout}")

    def __enter__(self) -> "FakeSmtp":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def ehlo(self) -> None:
        FakeSmtp.calls.append("ehlo")

    def starttls(self) -> None:
        FakeSmtp.calls.append("starttls")

    def login(self, user: str, password: str) -> None:
        FakeSmtp.calls.append(f"login {user}")
        if password == FakeSmtp.bad_password:
            raise smtplib.SMTPAuthenticationError(
                535, b"5.7.8 Username and Password not accepted"
            )

    def send_message(self, message: EmailMessage) -> None:
        FakeSmtp.calls.append("send_message")
        FakeSmtp.message = message


@pytest.fixture
def fake_smtp(monkeypatch: pytest.MonkeyPatch) -> type[FakeSmtp]:
    """SMTP-сервер подменён: письма наружу не уходят."""
    FakeSmtp.calls = []
    FakeSmtp.message = None
    FakeSmtp.bad_password = None
    FakeSmtp.fail_connect = False
    monkeypatch.setattr(smtplib, "SMTP", FakeSmtp)
    return FakeSmtp


def _smtp_mailer(**overrides) -> mailer.SmtpMailer:
    params = {
        "host": "smtp.example.com",
        "port": 587,
        "user": "tutor@example.com",
        "password": "пароль-приложения",
        "starttls": True,
        "sender": "tutor@example.com",
        "sender_name": "llm·tutor",
        "timeout": 10.0,
    }
    params.update(overrides)
    return mailer.SmtpMailer(**params)


async def test_smtp_mailer_sends_utf8_letter(fake_smtp) -> None:
    await _smtp_mailer().send(**LETTER)

    assert fake_smtp.calls == [
        "connect smtp.example.com:587 timeout=10.0",
        "ehlo",
        "starttls",
        "ehlo",
        "login tutor@example.com",
        "send_message",
    ]
    message = fake_smtp.message
    assert parseaddr(str(message["From"]))[1] == "tutor@example.com"
    assert str(make_header(decode_header(str(message["To"])))) == "ann@example.com"
    # Кириллица в теме и теле — не «=?...?=» и не «????».
    assert str(make_header(decode_header(str(message["Subject"])))) == LETTER["subject"]
    assert message.get_content_type() == "text/plain"
    assert message.get_content_charset() == "utf-8"
    assert LETTER["text"].strip() in message.get_content().strip()


async def test_smtp_mailer_skips_starttls_when_disabled(fake_smtp) -> None:
    await _smtp_mailer(starttls=False).send(**LETTER)

    assert "starttls" not in fake_smtp.calls


async def test_smtp_auth_failure_is_logged_without_secrets(fake_smtp, caplog) -> None:
    fake_smtp.bad_password = "пароль-приложения"

    with caplog.at_level(logging.ERROR):
        await _smtp_mailer().send(**LETTER)

    # Отправка не удалась, но ошибка не полетела наружу: письмо уходит фоном.
    assert "535" in caplog.text
    assert "SMTP_PASSWORD" in caplog.text
    assert "пароль-приложения" not in caplog.text
    assert "секрет" not in caplog.text


async def test_smtp_connection_error_is_logged(fake_smtp, caplog) -> None:
    fake_smtp.fail_connect = True

    with caplog.at_level(logging.ERROR):
        await _smtp_mailer().send(**LETTER)

    assert "OSError" in caplog.text
    assert "секрет" not in caplog.text


async def test_log_mailer_writes_the_whole_letter(caplog) -> None:
    """Почта не настроена — письмо (вместе со ссылкой) видно в журнале."""
    with caplog.at_level(logging.WARNING):
        await mailer.LogMailer().send(**LETTER)

    assert LETTER["to"] in caplog.text
    assert "reset-password?token=секрет" in caplog.text


def _settings(**overrides) -> Settings:
    defaults = {"openrouter_api_key": "sk-test", "_env_file": None}
    defaults.update(overrides)
    return Settings(**defaults)


def test_unconfigured_smtp_gives_the_log_mailer() -> None:
    """Без SMTP_USER/SMTP_PASSWORD приложение поднимается в режиме показа."""
    assert isinstance(mailer.build_mailer(_settings()), mailer.LogMailer)


def test_configured_smtp_gives_the_real_mailer() -> None:
    settings = _settings(smtp_user="tutor@example.com", smtp_password="пароль")

    built = mailer.build_mailer(settings)

    assert isinstance(built, mailer.SmtpMailer)
    assert built.sender == "tutor@example.com"
    assert built.sender_name == "llm·tutor"


def test_letter_from_falls_back_to_the_smtp_user() -> None:
    settings = _settings(smtp_user="tutor@example.com", smtp_password="пароль")

    assert mailer.build_mailer(settings).sender == "tutor@example.com"
    with_from = _settings(
        smtp_user="tutor@example.com", smtp_password="пароль", mail_from="tutor@llm-tutor.ru"
    )
    assert mailer.build_mailer(with_from).sender == "tutor@llm-tutor.ru"
