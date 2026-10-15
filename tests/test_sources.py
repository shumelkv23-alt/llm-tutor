"""Источники курса: файлы, GitHub, страница в интернете (Срез D).

Без сети: HTTP подменяется ``httpx.MockTransport``, а проверка «хост публичный»
— подменой ``socket.getaddrinfo``. Лимиты и разбор форматов проверяются на
настоящих функциях.
"""

import json
import socket

import httpx
import pytest

from llm_tutor.course.build import sources as src

LIMITS = {"max_bytes": 4096, "max_count": 3}


def client_for(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def course_root(tmp_path):
    root = tmp_path / "course"
    root.mkdir()
    return root


@pytest.fixture
def public_hosts(monkeypatch):
    """Считает любой хост публичным: тесты скачивания не ходят в DNS.

    Настоящая проверка адресов проверяется отдельно — на литералах и на
    подменённом ``getaddrinfo``.
    """
    monkeypatch.setattr(src, "_public_host", lambda host: True)


# --- разбор загруженного ---

PAGE = "<!doctype html><html><body><h1>Заголовок</h1><p>Текст</p><script>x</script></body></html>"


def test_clean_text_converts_html_to_markdown():
    clean = src.clean_text("page.html", PAGE)
    assert "# Заголовок" in clean and "Текст" in clean and "<script>" not in clean


def test_clean_text_rejects_unknown_extension_and_binary():
    with pytest.raises(src.SourceError):
        src.clean_text("data.pdf", "текст")
    with pytest.raises(src.SourceError):
        src.clean_text("data.md", "текст\x00битый")


def test_notebook_becomes_markdown():
    notebook = json.dumps(
        {
            "cells": [
                {"cell_type": "markdown", "source": ["# Тема\n", "Рассказ"]},
                {"cell_type": "code", "source": ["df.head()"], "outputs": [{"x": 1}]},
            ]
        }
    )
    clean = src.clean_text("lesson.ipynb", notebook)
    assert "# Тема" in clean and "```python\ndf.head()\n```" in clean
    assert "outputs" not in clean


def test_broken_notebook_is_rejected():
    with pytest.raises(src.SourceError):
        src.clean_text("lesson.ipynb", "{не json")


# --- файлы преподавателя ---


def test_file_source_is_saved_and_listed(course_root):
    record = src.add_file_source(course_root, "Пандас.md", "# Пандас\n\nГруппировки.", **LIMITS)
    assert record.kind == "file" and record.id == 1
    assert record.path == "0001_pandas.md"
    assert (src.sources_dir(course_root) / record.path).read_text(encoding="utf-8").startswith("# Пандас")
    assert [item.id for item in src.read_manifest(course_root)] == [1]

    second = src.add_file_source(course_root, "Ещё.md", "# Ещё", **LIMITS)
    assert second.id == 2 and second.path == "0002_esche.md"
    assert [item.id for item in src.read_manifest(course_root)] == [1, 2]


def test_source_count_is_limited(course_root):
    for index in range(LIMITS["max_count"]):
        src.add_file_source(course_root, f"Файл {index}.md", "# Текст", **LIMITS)
    with pytest.raises(src.SourceError):
        src.add_file_source(course_root, "Лишний.md", "# Текст", **LIMITS)


def test_source_size_is_limited(course_root):
    with pytest.raises(src.SourceError):
        src.add_file_source(course_root, "Большой.md", "я" * 5000, **LIMITS)


def test_removed_source_leaves_manifest_and_disk(course_root):
    record = src.add_file_source(course_root, "Пандас.md", "# Пандас", **LIMITS)
    src.remove_source(course_root, record.id)
    assert src.read_manifest(course_root) == []
    assert not (src.sources_dir(course_root) / record.path).exists()
    with pytest.raises(src.SourceError):
        src.remove_source(course_root, record.id)


# --- проверка адресов (SSRF) ---


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "10.0.0.1", "192.168.1.10", "169.254.1.1", "::1", "0.0.0.0"],
)
def test_private_addresses_are_rejected(host):
    assert src._public_host(host) is False


def test_public_address_is_allowed():
    assert src._public_host("8.8.8.8") is True


def test_name_resolving_to_private_address_is_rejected(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("10.1.2.3", 0))]
    )
    assert src._public_host("internal.example") is False


def test_unresolvable_name_is_rejected(monkeypatch):
    def boom(*args, **kwargs):
        raise socket.gaierror("нет такого имени")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    with pytest.raises(src.SourceError):
        src._public_host("no-such-host.example")


@pytest.mark.parametrize(
    "url",
    ["ftp://example.com/a", "https://user:pass@example.com/a", "https:///нет-хоста", "не адрес"],
)
def test_bad_urls_are_rejected(url):
    with pytest.raises(src.SourceError):
        src._checked_url(url)


def test_private_url_is_rejected(monkeypatch):
    monkeypatch.setattr(src, "_public_host", lambda host: False)
    with pytest.raises(src.SourceError):
        src._checked_url("http://127.0.0.1/secret")


# --- скачивание страницы ---


def test_page_is_downloaded_and_converted(course_root, public_hosts):
    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html"}, text=PAGE)

    record = src.add_url_source(
        course_root,
        "https://example.com/topic",
        max_bytes=4096,
        max_count=3,
        timeout=5.0,
        fetch_max_bytes=4096,
        client=client_for(handler),
    )
    assert record.kind == "url" and record.origin == "https://example.com/topic"
    assert "# Заголовок" in src.source_text(course_root, record)


def test_redirect_to_private_host_is_rejected(course_root):
    def handler(request):
        return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})

    with pytest.raises(src.SourceError):
        src.add_url_source(
            course_root,
            "https://example.com/start",
            max_bytes=4096,
            max_count=3,
            timeout=5.0,
            fetch_max_bytes=4096,
            client=client_for(handler),
        )


def test_redirect_inside_allowed_host_is_followed(course_root, public_hosts):
    def handler(request):
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "https://example.com/final"})
        return httpx.Response(200, headers={"content-type": "text/markdown"}, text="# Финал")

    record = src.add_url_source(
        course_root,
        "https://example.com/start",
        max_bytes=4096,
        max_count=3,
        timeout=5.0,
        fetch_max_bytes=4096,
        client=client_for(handler),
    )
    assert record.origin == "https://example.com/final"


def test_huge_page_is_rejected(course_root, public_hosts):
    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="я" * 10_000)

    with pytest.raises(src.SourceError):
        src.add_url_source(
            course_root,
            "https://example.com/huge",
            max_bytes=1024 * 1024,
            max_count=3,
            timeout=5.0,
            fetch_max_bytes=1024,
            client=client_for(handler),
        )


def test_non_text_page_is_rejected(course_root, public_hosts):
    def handler(request):
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF-1.4")

    with pytest.raises(src.SourceError):
        src.add_url_source(
            course_root,
            "https://example.com/file.pdf",
            max_bytes=4096,
            max_count=3,
            timeout=5.0,
            fetch_max_bytes=4096,
            client=client_for(handler),
        )


# --- GitHub ---


def test_github_url_is_parsed():
    assert src.parse_github("https://github.com/owner/repo") == ("owner", "repo", "", "")
    assert src.parse_github("https://github.com/owner/repo/tree/main/docs") == (
        "owner",
        "repo",
        "main",
        "docs",
    )
    assert src.parse_github("https://github.com/owner/repo/blob/main/a/b.md") == (
        "owner",
        "repo",
        "main",
        "a/b.md",
    )


@pytest.mark.parametrize(
    "url",
    ["https://gitlab.com/owner/repo", "https://github.com/owner", "https://github.com/"],
)
def test_foreign_repository_host_is_rejected(url):
    with pytest.raises(src.SourceError):
        src.parse_github(url)


def test_repository_files_are_taken(course_root):
    tree = {
        "tree": [
            {"type": "blob", "path": "README.md"},
            {"type": "blob", "path": "docs/topic.md"},
            {"type": "blob", "path": "docs/logo.png"},
            {"type": "blob", "path": "node_modules/dep/readme.md"},
        ]
    }

    def handler(request):
        if request.url.path == "/repos/owner/repo/git/trees/main":
            return httpx.Response(200, json=tree)
        if request.url.path.startswith("/owner/repo/main/"):
            return httpx.Response(200, text=f"# {request.url.path.rsplit('/', 1)[-1]}")
        return httpx.Response(404)

    added = src.add_github_source(
        course_root,
        "https://github.com/owner/repo/tree/main",
        max_bytes=4096,
        max_count=10,
        max_files=10,
        timeout=5.0,
        client=client_for(handler),
    )
    assert [record.title for record in added] == ["README", "topic"]
    assert all(record.kind == "github" for record in added)
    assert "logo.png" not in " ".join(record.origin for record in added)


def test_repository_without_text_files_is_rejected(course_root):
    def handler(request):
        return httpx.Response(200, json={"tree": [{"type": "blob", "path": "main.py"}]})

    with pytest.raises(src.SourceError):
        src.add_github_source(
            course_root,
            "https://github.com/owner/repo",
            max_bytes=4096,
            max_count=10,
            max_files=10,
            timeout=5.0,
            client=client_for(handler),
        )


def test_too_many_repository_files_is_rejected(course_root):
    tree = {"tree": [{"type": "blob", "path": f"doc{index}.md"} for index in range(5)]}

    def handler(request):
        return httpx.Response(200, json=tree)

    with pytest.raises(src.SourceError):
        src.add_github_source(
            course_root,
            "https://github.com/owner/repo",
            max_bytes=4096,
            max_count=10,
            max_files=2,
            timeout=5.0,
            client=client_for(handler),
        )


def test_github_sources_count_towards_the_course_limit(course_root):
    tree = {"tree": [{"type": "blob", "path": f"doc{index}.md"} for index in range(4)]}

    def handler(request):
        if request.url.path.startswith("/repos/"):
            return httpx.Response(200, json=tree)
        return httpx.Response(200, text="# Документ")

    with pytest.raises(src.SourceError):
        src.add_github_source(
            course_root,
            "https://github.com/owner/repo",
            max_bytes=4096,
            max_count=3,
            max_files=10,
            timeout=5.0,
            client=client_for(handler),
        )


# --- индекс материалов курса ---


def test_sources_become_course_materials(course_root, tmp_path):
    from llm_tutor.db.connection import get_conn

    src.add_file_source(course_root, "Пандас.md", "# Пандас\n\nГруппировки по городам.", **LIMITS)
    materials = tmp_path / "materials.sqlite3"
    assert src.ingest_sources(course_root, materials) == 1

    conn = get_conn(str(materials))
    try:
        rows = list(conn.execute("SELECT source_url, topic_id, content FROM chunks"))
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0][0] == "file:Пандас.md" and rows[0][1] == 1
    assert "Группировки" in rows[0][2]


def test_reingest_replaces_old_chunks(course_root, tmp_path):
    from llm_tutor.db.connection import get_conn

    materials = tmp_path / "materials.sqlite3"
    record = src.add_file_source(course_root, "Пандас.md", "# Пандас", **LIMITS)
    src.ingest_sources(course_root, materials)
    src.remove_source(course_root, record.id)
    src.add_file_source(course_root, "Другое.md", "# Другое\n\nТекст про другое.", **LIMITS)
    src.ingest_sources(course_root, materials)

    conn = get_conn(str(materials))
    try:
        assert [row[0] for row in conn.execute("SELECT source_url FROM chunks")] == ["file:Другое.md"]
    finally:
        conn.close()
