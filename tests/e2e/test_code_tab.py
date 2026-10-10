"""Вкладка «Код» в браузере: запуск, ошибка, таймаут (Срез 46).

Нужен дистрибутив Pyodide. Из CDN в песочнице не скачать, поэтому тест
берёт локальную копию npm-пакета ``pyodide`` (ядро без pandas) из
``E2E_PYODIDE_DIR``; без неё тест пропускается.
"""

import functools
import http.server
import os
import threading

import pytest
from playwright.async_api import expect

from helpers import enroll, finish_survey_via_api, sign_up

pytestmark = pytest.mark.e2e

PYODIDE_DIR = os.environ.get("E2E_PYODIDE_DIR")


class _CorsHandler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map, ".wasm": "application/wasm"}

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def pyodide_url():
    if not PYODIDE_DIR:
        pytest.skip("E2E_PYODIDE_DIR не задан")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_CorsHandler, directory=PYODIDE_DIR))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()


@pytest.fixture
def pyodide_settings(pyodide_url, monkeypatch):
    monkeypatch.setenv("PYODIDE_INDEX_URL", pyodide_url)


async def _set(page, code: str) -> None:
    await page.evaluate("code => document.querySelector('.CodeMirror').CodeMirror.setValue(code)", code)


async def test_run_error_and_timeout(pyodide_settings, page, live_server) -> None:
    await sign_up(page, live_server)
    await enroll(page, live_server)
    await finish_survey_via_api(page)
    await page.click("#tab-btn-code")
    await page.wait_for_selector(".CodeMirror")
    console = page.locator(".code-console")

    await _set(page, "print(sum(i * i for i in range(5)))")
    await page.click("text=Запустить")
    await expect(console).to_contain_text("30", timeout=90000)

    await _set(page, "raise ValueError('плохо')")
    await page.click("text=Запустить")
    await expect(console).to_contain_text("ValueError: плохо")

    await _set(page, "while True:\n    pass")
    await page.click("text=Запустить")
    await expect(console).to_contain_text("дольше 10 секунд", timeout=30000)

    await _set(page, "print('снова')")
    await page.click("text=Запустить")
    await expect(console).to_contain_text("снова", timeout=90000)
    assert page.errors == []
