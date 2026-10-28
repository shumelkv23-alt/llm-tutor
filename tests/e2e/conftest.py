"""Фикстуры браузерных тестов: живой сервер в потоке и Chromium.

Без Playwright или браузера тесты пропускаются (на Windows браузер ставится
``uv run playwright install chromium``).
"""

import asyncio
import os
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from llm_tutor.config import Settings
from llm_tutor.web.app import create_app
from web_fakes import RESET_LINK_RE

playwright_api = pytest.importorskip("playwright.async_api")

# В облачной песочнице Chromium лежит здесь, на машине разработчика — там,
# куда его поставил ``playwright install``.
_SANDBOX_CHROMIUM = Path("/opt/pw-browsers/chromium")


class EchoTutor:
    """Тьютор-эхо: отвечает Markdown-ом с кодом, чтобы видеть рендер."""

    def __init__(self) -> None:
        self.delay = 0.3

    async def chat_structured(self, messages, schema, *, model=None, temperature=None):
        await asyncio.sleep(self.delay)
        if "reply" not in schema.model_fields:
            raise RuntimeError("в e2e рубрики не проверяются")
        return schema(reply="**groupby** разбивает строки на группы.\n\n```python\ndf.groupby('city').size()\n```")

    async def chat(self, messages, *, model=None, temperature=None) -> str:
        return "ok"

    async def aclose(self) -> None:
        pass


class FileMailer:
    """Письма — файлами в каталог: у e2e сервер живёт в другом потоке.

    Запись атомарная (черновик + переименование): тест опрашивает каталог и не
    должен прочитать письмо наполовину.
    """

    def __init__(self, outbox: Path) -> None:
        self.outbox = outbox
        self.count = 0

    async def send(self, *, to: str, subject: str, text: str) -> None:
        self.count += 1
        target = self.outbox / f"letter-{self.count}.txt"
        draft = target.with_suffix(".part")
        draft.write_text(f"Кому: {to}\nТема: {subject}\n\n{text}", encoding="utf-8")
        draft.replace(target)

    async def aclose(self) -> None:
        """Ничего не держит."""

    def link(self, timeout: float = 10.0) -> str:
        """Ссылка восстановления из первого письма (ждёт, пока оно появится)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for letter in sorted(self.outbox.glob("letter-*.txt")):
                match = RESET_LINK_RE.search(letter.read_text(encoding="utf-8"))
                if match:
                    return match.group(0)
            time.sleep(0.05)
        raise AssertionError("Письмо со ссылкой восстановления не пришло")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def lessons_server(monkeypatch, tmp_path):
    """Живой сервер, у которого курс mlcourse — фикстура занятий (срез 55)."""
    from course_fixtures import use_lessons_course

    use_lessons_course(monkeypatch)
    yield from _serve(tmp_path)


@pytest.fixture
def live_server(monkeypatch, tmp_path):
    """URL приложения, запущенного uvicorn в отдельном потоке.

    Встроенный ``mlcourse`` показан в каталоге: сценарии записываются на него,
    а в бою он скрыт вместе с ``isit-ml``.
    """
    from course_fixtures import show_builtin_course

    show_builtin_course(monkeypatch)
    yield from _serve(tmp_path)


@pytest.fixture
def mail_server(tmp_path):
    """Сервер, письма которого ложатся файлами рядом с его БД (срез 74).

    Отдаёт пару «адрес, почта»: ссылку из письма иначе не достать — сервер
    живёт в отдельном потоке, а журнал uvicorn в e2e приглушён.
    """
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    post = FileMailer(outbox)
    for url in _serve(tmp_path, mailer=post):
        yield url, post


def _serve(tmp_path, mailer=None):
    port = _free_port()
    settings = Settings(
        _env_file=None,
        openrouter_api_key="test-key",
        accounts_db_path=str(tmp_path / "accounts.sqlite3"),
        users_dir=str(tmp_path / "users"),
        materials_db_path=str(tmp_path / "materials.sqlite3"),
        # Ссылка в письме — на этот же сервер: иначе PUBLIC_BASE_URL по
        # умолчанию (127.0.0.1:8000) увёл бы браузер на чужой порт.
        public_base_url=f"http://127.0.0.1:{port}",
        **({"pyodide_index_url": os.environ["PYODIDE_INDEX_URL"]} if os.environ.get("PYODIDE_INDEX_URL") else {}),
    )
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(settings, client=EchoTutor(), mailer=mailer),
            host="127.0.0.1",
            port=port,
            log_level="warning",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            pytest.fail("сервер не поднялся")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
async def browser():
    async with playwright_api.async_playwright() as p:
        options = {}
        if _SANDBOX_CHROMIUM.exists() and not os.environ.get("E2E_DEFAULT_BROWSER"):
            options["executable_path"] = str(_SANDBOX_CHROMIUM)
        try:
            instance = await p.chromium.launch(**options)
        except Exception as exc:  # noqa: BLE001 — браузера нет: тест не про это
            pytest.skip(f"Chromium недоступен: {exc}")
        yield instance
        await instance.close()


@pytest.fixture
async def page(browser):
    context = await browser.new_context(viewport={"width": 1440, "height": 900})
    instance = await context.new_page()
    errors: list[str] = []
    instance.on("pageerror", lambda error: errors.append(str(error)))
    instance.errors = errors
    yield instance
    await context.close()
