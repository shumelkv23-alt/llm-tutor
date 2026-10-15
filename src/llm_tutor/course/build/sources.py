"""Источники курса: файлы преподавателя, ссылка на GitHub, ссылка на страницу.

Сырьё складывается в ``<каталог курса>/sources/`` вместе с манифестом
(``manifest.json``): перегенерация модуля не требует повторной загрузки, а
преподаватель видит, из чего собран курс. Оттуда же наполняется БД материалов
курса (``materials.sqlite3``) — у каждого курса свой RAG-индекс, иначе ученик
нового курса цитировал бы mlcourse.

Сеть здесь синхронная: веб зовёт эти функции через ``asyncio.to_thread``, чтобы
не блокировать цикл событий, в котором идут ходы учеников.
"""

import hashlib
import ipaddress
import json
import socket
import time
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse, urlunparse

import httpx
from pydantic import BaseModel

from llm_tutor.course import store
from llm_tutor.course.ingest import to_markdown
from llm_tutor.db.connection import get_conn, migrate

SOURCES_DIR = "sources"
MANIFEST_NAME = "manifest.json"

# Что принимаем как текст: конспект, статья, блокнот. PDF и docx — не текст,
# их разбор тянет за собой зависимости и в браузере не проверяется.
TEXT_SUFFIXES = (".md", ".markdown", ".txt", ".html", ".htm")
NOTEBOOK_SUFFIX = ".ipynb"
ALLOWED_SUFFIXES = (*TEXT_SUFFIXES, NOTEBOOK_SUFFIX)
# В репозитории берём только документацию и блокноты: код и картинки не текст.
GITHUB_SUFFIXES = (".md", ".markdown", ".txt", ".ipynb")
GITHUB_SKIP_DIRS = ("node_modules", ".git", ".github", "venv", ".venv", "__pycache__")

GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})
GITHUB_API = "https://api.github.com"
GITHUB_RAW = "https://raw.githubusercontent.com"
# Каждый переход по редиректу проверяем заново: иначе страница уведёт на
# внутренний адрес и обойдёт проверку хоста.
MAX_REDIRECTS = 5
# Источники нумеруются в имени файла: NNNN_<слаг>.md — порядок загрузки виден.
_ID_WIDTH = 4


class SourceError(ValueError):
    """Источник не принят. Текст ошибки читает преподаватель, а не лог."""


class SourceRecord(BaseModel):
    """Запись манифеста: что загружено и откуда."""

    id: int
    kind: Literal["file", "url", "github"]
    # Откуда взялось: имя файла, адрес страницы или путь внутри репозитория.
    origin: str
    title: str
    # Имя файла внутри sources/ (нормализованный markdown).
    path: str
    bytes: int
    sha256: str
    fetched_at: float


# --- манифест ---


def sources_dir(root: str | Path) -> Path:
    """Папка источников курса."""
    return Path(root) / SOURCES_DIR


def read_manifest(root: str | Path) -> list[SourceRecord]:
    """Манифест источников; нет файла — пустой список."""
    path = sources_dir(root) / MANIFEST_NAME
    if not path.is_file():
        return []
    try:
        payload = store.read_json(path)
    except store.StoreError as error:
        raise SourceError(f"Манифест источников не читается: {error}") from error
    records = payload.get("sources", [])
    if not isinstance(records, list):
        raise SourceError("Манифест источников испорчен: ожидался список sources")
    return [SourceRecord.model_validate(item) for item in records]


def _write_manifest(root: str | Path, records: list[SourceRecord]) -> None:
    store.write_json(
        sources_dir(root) / MANIFEST_NAME,
        {"sources": [record.model_dump() for record in records]},
    )


def source_text(root: str | Path, record: SourceRecord) -> str:
    """Текст источника из его файла (для генерации и переиндексации)."""
    path = sources_dir(root) / record.path
    if not path.is_file():
        raise SourceError(f"Файл источника пропал: {record.path}")
    return path.read_text(encoding="utf-8")


def remove_source(root: str | Path, source_id: int) -> SourceRecord:
    """Убирает источник: сначала запись, потом файл (файл можно удалить и позже)."""
    records = read_manifest(root)
    found = next((record for record in records if record.id == source_id), None)
    if found is None:
        raise SourceError(f"Нет источника {source_id}")
    _write_manifest(root, [record for record in records if record.id != source_id])
    (sources_dir(root) / found.path).unlink(missing_ok=True)
    return found


# --- разбор загруженного ---


def clean_text(name: str, text: str) -> str:
    """Проверяет и нормализует текст источника (расширение, бинарь, разметка)."""
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        allowed = ", ".join(ALLOWED_SUFFIXES)
        raise SourceError(f"Формат {suffix or 'без расширения'} не поддержан: нужен один из {allowed}")
    if "\x00" in text:
        raise SourceError(f"{name}: похоже на двоичный файл, а нужен текст")
    if suffix == NOTEBOOK_SUFFIX:
        return notebook_to_markdown(text)
    return to_markdown(text)


def notebook_to_markdown(text: str) -> str:
    """Блокнот Jupyter → markdown: текст ячеек как есть, код — фенсами.

    Выход блокнота не берём: он шумный и часто огромный. Смысл блокнота для
    курса — рассказ и примеры, а не результаты прогона.
    """
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise SourceError(f"Блокнот не читается как JSON: {error}") from error
    cells = payload.get("cells") if isinstance(payload, dict) else None
    if not isinstance(cells, list):
        raise SourceError("В блокноте нет ячеек (cells)")
    parts: list[str] = []
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        source = cell.get("source")
        body = "".join(source) if isinstance(source, list) else (source or "")
        if not body.strip():
            continue
        if cell.get("cell_type") == "code":
            parts.append(f"```python\n{body.rstrip()}\n```")
        else:
            parts.append(body.rstrip())
    return "\n\n".join(parts)


def _slug(title: str) -> str:
    """Имя файла источника: транслит и дефисы (как id курса)."""
    return store.slugify(title) if title.strip() else "source"


def _too_many(records: list[SourceRecord], max_count: int) -> None:
    if len(records) >= max_count:
        raise SourceError(f"Источников уже {len(records)}: больше {max_count} не берём")


def _check_size(name: str, text: str, max_bytes: int) -> int:
    size = len(text.encode("utf-8"))
    if size > max_bytes:
        raise SourceError(f"{name}: {size // 1024} КБ больше допустимых {max_bytes // 1024} КБ")
    return size


def _store_source(
    root: str | Path,
    *,
    kind: str,
    origin: str,
    title: str,
    text: str,
    max_bytes: int,
    now: float | None = None,
) -> SourceRecord:
    """Кладёт нормализованный текст в sources/ и дописывает манифест."""
    records = read_manifest(root)
    size = _check_size(title, text, max_bytes)
    source_id = max((record.id for record in records), default=0) + 1
    folder = sources_dir(root)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{source_id:0{_ID_WIDTH}d}_{_slug(title)}.md"
    store.atomic_write_text(folder / name, text)
    record = SourceRecord(
        id=source_id,
        kind=kind,
        origin=origin,
        title=title,
        path=name,
        bytes=size,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        fetched_at=time.time() if now is None else now,
    )
    _write_manifest(root, [*records, record])
    return record


def add_file_source(
    root: str | Path, name: str, text: str, *, max_bytes: int, max_count: int
) -> SourceRecord:
    """Источник из файла преподавателя (текст приезжает из браузера)."""
    _too_many(read_manifest(root), max_count)
    clean = clean_text(name, text)
    return _store_source(
        root, kind="file", origin=name, title=Path(name).stem or "file",
        text=clean, max_bytes=max_bytes,
    )


# --- безопасная загрузка страницы ---


def _checked_url(url: str) -> str:
    """Проверяет адрес до запроса: схема, хост, отсутствие учётных данных."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise SourceError("Нужен адрес http(s)")
    if parsed.username or parsed.password:
        raise SourceError("В адресе не должно быть логина и пароля")
    if not parsed.hostname:
        raise SourceError("В адресе нет имени хоста")
    if not _public_host(parsed.hostname):
        raise SourceError(f"{parsed.hostname}: адрес ведёт во внутреннюю сеть — не берём")
    return urlunparse(parsed)


def _public_host(host: str) -> bool:
    """Хост — не локальный и не приватный адрес (ни прямо, ни после DNS)."""
    addresses: list[str] = []
    try:
        addresses.append(str(ipaddress.ip_address(host)))
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror:
            raise SourceError(f"{host}: имя не разрешается") from None
        addresses.extend(info[4][0] for info in infos)
    if not addresses:
        raise SourceError(f"{host}: имя не разрешается")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


def safe_fetch(
    url: str,
    *,
    max_bytes: int,
    timeout: float,
    client: httpx.Client | None = None,
) -> tuple[str, str]:
    """Скачивает текст страницы. Возвращает ``(текст, итоговый адрес)``.

    Редиректы проходим сами и проверяем каждый адрес заново: иначе страница
    уведёт на внутренний адрес и обойдёт проверку. Тело читаем потоком и
    обрываем на лимите — «страница» на гигабайт не должна съесть память.
    """
    owned = client is None
    http = client or httpx.Client(timeout=timeout)
    try:
        target = _checked_url(url)
        for _ in range(MAX_REDIRECTS):
            with http.stream("GET", target) as response:
                if response.is_redirect and response.headers.get("location"):
                    target = _checked_url(str(response.url.join(response.headers["location"])))
                    continue
                response.raise_for_status()
                ctype = response.headers.get("content-type", "")
                if ctype and not any(kind in ctype for kind in ("text/", "html", "xml", "json")):
                    raise SourceError(f"{target}: это {ctype}, а нужен текст страницы")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise SourceError(
                            f"{target}: страница больше {max_bytes // 1024} КБ — не берём"
                        )
                return bytes(body).decode(response.encoding or "utf-8", errors="replace"), str(
                    response.url
                )
        raise SourceError(f"{target}: слишком много перенаправлений")
    except httpx.HTTPError as error:
        raise SourceError(f"Не удалось скачать страницу: {error}") from error
    finally:
        if owned:
            http.close()


def add_url_source(
    root: str | Path,
    url: str,
    *,
    max_bytes: int,
    max_count: int,
    timeout: float,
    fetch_max_bytes: int,
    title: str = "",
    client: httpx.Client | None = None,
) -> SourceRecord:
    """Источник со страницы в интернете."""
    _too_many(read_manifest(root), max_count)
    text, final_url = safe_fetch(url, max_bytes=fetch_max_bytes, timeout=timeout, client=client)
    clean = to_markdown(text)
    name = title.strip() or urlparse(final_url).path.rstrip("/").rsplit("/", 1)[-1] or "page"
    return _store_source(
        root, kind="url", origin=final_url, title=name, text=clean, max_bytes=max_bytes
    )


# --- GitHub ---


def parse_github(url: str) -> tuple[str, str, str, str]:
    """Разбирает адрес репозитория: ``(owner, repo, ref, path)``.

    Принимаем ``github.com/{owner}/{repo}``, ``…/tree/{ref}[/{path}]`` и
    ``…/blob/{ref}/{path}``. Чужой хост — ошибка: туда мы не ходим.
    """
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or parsed.hostname not in GITHUB_HOSTS:
        raise SourceError("Нужна ссылка на репозиторий github.com")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        raise SourceError("В ссылке нет репозитория: нужен github.com/{автор}/{репозиторий}")
    owner, repo = parts[0], parts[1]
    ref, path = "", ""
    if len(parts) >= 4 and parts[2] in ("tree", "blob"):
        ref = parts[3]
        path = "/".join(parts[4:])
    return owner, repo, ref, path


def github_tree(
    owner: str, repo: str, ref: str, *, client: httpx.Client, max_files: int
) -> tuple[str, list[str]]:
    """Файлы репозитория: ``(ветка, пути подходящих файлов)``."""
    try:
        if not ref:
            info = client.get(f"{GITHUB_API}/repos/{owner}/{repo}")
            info.raise_for_status()
            ref = info.json().get("default_branch") or "main"
        tree = client.get(f"{GITHUB_API}/repos/{owner}/{repo}/git/trees/{ref}", params={"recursive": "1"})
        tree.raise_for_status()
        blobs = tree.json().get("tree", [])
    except httpx.HTTPError as error:
        raise SourceError(f"GitHub не отдал репозиторий {owner}/{repo}: {error}") from error
    paths = [
        entry.get("path", "")
        for entry in blobs
        if isinstance(entry, dict)
        and entry.get("type") == "blob"
        and entry.get("path", "").lower().endswith(GITHUB_SUFFIXES)
        and not any(part in GITHUB_SKIP_DIRS for part in entry.get("path", "").split("/"))
    ]
    if not paths:
        raise SourceError(f"В {owner}/{repo} нет текстовых файлов ({', '.join(GITHUB_SUFFIXES)})")
    if len(paths) > max_files:
        raise SourceError(
            f"В репозитории {len(paths)} подходящих файлов — больше {max_files} не берём. "
            "Укажите ссылку на папку с материалами"
        )
    return ref, sorted(paths)


def add_github_source(
    root: str | Path,
    url: str,
    *,
    max_bytes: int,
    max_count: int,
    max_files: int,
    timeout: float,
    client: httpx.Client | None = None,
) -> list[SourceRecord]:
    """Источники из репозитория GitHub (каждый файл — своя запись)."""
    records = read_manifest(root)
    owned = client is None
    http = client or httpx.Client(timeout=timeout, follow_redirects=True)
    try:
        owner, repo, ref, path = parse_github(url)
        branch, paths = github_tree(owner, repo, ref, client=http, max_files=max_files)
        if path:
            prefix = path.rstrip("/") + "/"
            chosen = [item for item in paths if item.startswith(prefix)]
            if not chosen:
                raise SourceError(f"В папке {path} нет текстовых файлов")
        else:
            chosen = paths
        _too_many(records, max_count)
        if len(records) + len(chosen) > max_count:
            raise SourceError(f"Столько источников не влезет: уже {len(records)}, берём максимум {max_count}")
        added = []
        for item in chosen:
            response = http.get(f"{GITHUB_RAW}/{owner}/{repo}/{branch}/{item}")
            response.raise_for_status()
            raw = response.text
            clean = clean_text(item, raw)
            added.append(
                _store_source(
                    root,
                    kind="github",
                    origin=f"{owner}/{repo}/{item}",
                    title=Path(item).stem,
                    text=clean,
                    max_bytes=max_bytes,
                )
            )
        return added
    except httpx.HTTPError as error:
        raise SourceError(f"GitHub не отдал файл: {error}") from error
    finally:
        if owned:
            http.close()


# --- индекс материалов курса ---


def ingest_sources(root: str | Path, materials_path: str | Path) -> int:
    """Собирает БД материалов курса из источников. Возвращает число источников.

    Все источники идут модулем 1 (``topic_id=1``): материал преподавателя — не
    материал конкретного модуля, и RAG (``topic_id <= текущий``) должен видеть
    его с первого занятия до последнего.
    """
    records = read_manifest(root)
    connection = get_conn(str(materials_path))
    try:
        migrate(connection)
        connection.execute("DELETE FROM chunks")
        for record in records:
            from llm_tutor.course.ingest import ingest_text

            ingest_text(
                connection,
                source_text(root, record),
                f"{record.kind}:{record.origin}",
                topic_id=1,
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return len(records)
