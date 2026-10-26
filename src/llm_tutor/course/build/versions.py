"""Версии содержимого курса: снимок, список, откат.

Версия — папка ``<курс>/versions/<id>/`` с ``course.json``, seed-файлами и
конспектами. Источники, генерация и материалы курса в версию не входят: это
сырьё и рабочие артефакты, а не опубликованное содержание. Перед откатом
автоматически снимается версия текущего состояния — откат тоже обратим.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from llm_tutor.course import store

VERSIONS_DIR = "versions"
MANIFEST = "version.json"
MAX_VERSIONS = 20


class VersionError(RuntimeError):
    """Версию нельзя сохранить/восстановить."""


def _content_files(root: Path) -> list[Path]:
    """Файлы содержания курса; порядок — для манифеста."""
    files: list[Path] = []
    course_json = root / "course.json"
    if course_json.is_file():
        files.append(course_json)
    files.extend(sorted(root.glob("seed_topic*.json")))
    theory = root / "theory"
    if theory.is_dir():
        files.extend(sorted(path for path in theory.rglob("*") if path.is_file()))
    return files


def _write_manifest(version_dir: Path, *, version_id: str, label: str, created_at: float, files: list[str]) -> dict:
    manifest = {
        "id": version_id,
        "created_at": created_at,
        "label": label,
        "files": files,
    }
    (version_dir / MANIFEST).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def snapshot(
    root: str | Path,
    label: str = "",
    *,
    now: float | None = None,
    keep: str | None = None,
) -> dict:
    """Сохранить текущее содержание курса новой версией."""
    course_root = Path(root)
    if not (course_root / "course.json").is_file():
        raise VersionError("У курса нет course.json — сохранять нечего")
    versions = course_root / VERSIONS_DIR
    versions.mkdir(exist_ok=True)
    stamp = time.time() if now is None else now
    base = time.strftime("%Y%m%d-%H%M%S", time.gmtime(stamp))
    version_id = base
    counter = 2
    while (versions / version_id).exists():
        version_id = f"{base}-{counter}"
        counter += 1
    version_dir = versions / version_id
    version_dir.mkdir()
    files: list[str] = []
    for source in _content_files(course_root):
        relative = source.relative_to(course_root)
        target = version_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        files.append(relative.as_posix())
    _write_manifest(
        version_dir,
        version_id=version_id,
        label=label.strip()[:200],
        created_at=stamp,
        files=files,
    )
    _prune(versions, keep=keep)
    return {
        "id": version_id,
        "created_at": stamp,
        "label": label.strip()[:200],
        "files": len(files),
    }


def _prune(versions: Path, *, keep: str | None = None) -> None:
    entries = sorted(
        (entry for entry in versions.iterdir() if entry.is_dir()),
        key=lambda entry: entry.name,
        reverse=True,
    )
    for old in entries[MAX_VERSIONS:]:
        if keep is not None and old.name == keep:
            continue
        shutil.rmtree(old, ignore_errors=True)


def list_versions(root: str | Path) -> list[dict]:
    """Версии курса, новые первыми; битый манифест пропускается."""
    versions = Path(root) / VERSIONS_DIR
    if not versions.is_dir():
        return []
    found: list[dict] = []
    for entry in versions.iterdir():
        if not entry.is_dir():
            continue
        try:
            manifest = json.loads((entry / MANIFEST).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(manifest, dict):
            continue
        found.append(
            {
                "id": str(manifest.get("id") or entry.name),
                "created_at": float(manifest.get("created_at") or 0.0),
                "label": str(manifest.get("label") or ""),
                "files": len(manifest.get("files") or []),
            }
        )
    return sorted(found, key=lambda item: item["created_at"], reverse=True)


def _meta(root: Path) -> store.CourseMeta | None:
    """Метаданные курса или ``None``: откат не должен падать из-за старых файлов."""
    try:
        return store.read_meta(root)
    except store.StoreError:
        return None


def _merge_meta(
    current: store.CourseMeta, restored: store.CourseMeta, *, stamp: float
) -> store.CourseMeta:
    """Свести содержание версии с тем, что нельзя откатывать назад.

    Из версии берутся название, описание и прочее содержание. Публикация,
    происхождение и счётчики id остаются текущими: снятая с публикации
    версия не должна молча скрыть курс от учеников, а откат счётчиков выдал
    бы id, которые уже лежат в журнале событий учеников.
    """
    return restored.model_copy(
        update={
            "status": current.status,
            "origin": current.origin,
            "created_at": current.created_at or restored.created_at,
            "next_item_id": max(current.next_item_id, restored.next_item_id),
            "next_rubric_id": max(current.next_rubric_id, restored.next_rubric_id),
            "next_criterion_id": max(current.next_criterion_id, restored.next_criterion_id),
            "updated_at": stamp,
        }
    )


def restore(root: str | Path, version_id: str, *, now: float | None = None) -> dict:
    """Вернуть содержание версии; текущее состояние сначала сохраняется."""
    course_root = Path(root)
    if (
        not version_id
        or version_id in (".", "..")
        or Path(version_id).name != version_id
        or any(char in version_id for char in ("/", "\\", chr(0)))
    ):
        raise VersionError(f"Некорректная версия {version_id!r}")
    version_dir = course_root / VERSIONS_DIR / version_id
    if not version_dir.is_dir() or not (version_dir / MANIFEST).is_file():
        raise VersionError(f"Нет версии {version_id!r}")
    # Версия без читаемого course.json не должна затереть текущий курс.
    restored_meta = _meta(version_dir)
    if restored_meta is None:
        raise VersionError(f"В версии {version_id!r} нет читаемого course.json")
    current_meta = _meta(course_root)
    if current_meta is not None and current_meta.id != restored_meta.id:
        raise VersionError("Версия относится к другому курсу")
    stamp = time.time() if now is None else now
    # Страховка: откат тоже должен быть обратим.
    snapshot(course_root, label=f"перед откатом к {version_id}", now=stamp, keep=version_id)
    for source in version_dir.rglob("*"):
        if not source.is_file() or source.name == MANIFEST:
            continue
        relative = source.relative_to(version_dir)
        target = course_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    # Убираем файлы, которых в версии не было (модуль, добавленный позже).
    restored = {
        source.relative_to(version_dir).as_posix()
        for source in version_dir.rglob("*")
        if source.is_file() and source.name != MANIFEST
    }
    for relative in ("course.json", *[path.name for path in course_root.glob("seed_topic*.json")]):
        if relative not in restored:
            (course_root / relative).unlink(missing_ok=True)
    theory = course_root / "theory"
    if theory.is_dir():
        for path in sorted(theory.rglob("*"), reverse=True):
            if path.is_file() and path.relative_to(course_root).as_posix() not in restored:
                path.unlink(missing_ok=True)
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()
    if current_meta is not None:
        store.write_meta(course_root, _merge_meta(current_meta, restored_meta, stamp=stamp))
    try:
        manifest = json.loads((version_dir / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {}
    return {
        "id": version_id,
        "created_at": float(manifest.get("created_at") or 0.0),
        "label": str(manifest.get("label") or ""),
        "files": len(restored),
    }
